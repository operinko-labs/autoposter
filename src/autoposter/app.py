import asyncio
import functools
import logging
from contextlib import asynccontextmanager

import httpx
import requests
from fastapi import FastAPI
from plexapi.server import PlexServer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from autoposter.api.auth import LoginRateLimiter
from autoposter.api.dashboard_stream import StatusBroadcaster
from autoposter.api.logs import LogBuffer
from autoposter.api.routes import router as api_router
from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import DEFAULT_CONFIG_PATH
from autoposter.config.schema import Config, Secrets
from autoposter.facts import imdb as imdb_module
from autoposter.facts.imdb import ImdbAutoRefresh
from autoposter.facts.mdblist import MDBListClient, NullMDBListClient
from autoposter.facts.tmdb_facts import TMDBFactsClient
from autoposter.intake.routes import router
from autoposter.notify.dispatch import NullNotifier, build_notifier
from autoposter.plex.artwork import artwork_provenance
from autoposter.plex.client import ItemNotFound
from autoposter.plex.health import PlexHealth
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fanart import FanartClient
from autoposter.providers.tmdb import TMDBClient
from autoposter.providers.tvdb import TVDBClient
from autoposter.queue.jobs import reclaim_stale
from autoposter.queue.worker import run_workers
from autoposter.render.pipeline import process_item
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import (
    make_arr_sync_job,
    make_cleanup_job,
    make_collections_job,
    make_drift_job,
)

logger = logging.getLogger(__name__)


def create_app(
    config: Config, session_factory, secrets: Secrets, run_background: bool = False
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not run_background:
            yield
            return
        # Capture the process's own log stream for /api/logs. Attached here
        # rather than in create_app: the root logger is process-global, so
        # attaching per app instance would leave every test app's handler
        # stacked on it for the rest of the process.
        logging.getLogger().addHandler(app.state.log_buffer)
        stop_event = asyncio.Event()
        # Single client for the process: provider clients borrow it rather than
        # each owning one, so there is exactly one AsyncClient to close on shutdown.
        http = httpx.AsyncClient(timeout=30.0)
        # Published so request handlers can borrow it too -- the artwork
        # endpoints proxy Plex on behalf of the SPA. Closed in the `finally`
        # below with everything else that holds it.
        app.state.http = http
        # None (rather than a cache with ttl_seconds=0) when caching is disabled, so
        # _build_providers never issues a DB round trip for a config that opted out.
        cache = ProviderCache(session_factory) if config.providers.cache_ttl_seconds > 0 else None
        app.state.providers = _build_providers(config, secrets, http, cache)
        app.state.tmdb_facts = TMDBFactsClient(
            secrets.tmdb_token, http, cache=cache,
            cache_ttl_seconds=config.providers.cache_ttl_seconds,
        )
        # A stand-in (never None) when no key is configured: only the
        # content-rating field MDBList would have supplied is affected, so
        # every other metadata operation still runs. See _build_mdblist.
        app.state.mdblist = _build_mdblist(
            secrets, http, cache=cache, cache_ttl_seconds=config.providers.cache_ttl_seconds
        )
        # The real sender when the config can send, otherwise a NullNotifier
        # (never None) -- see notify/dispatch.build_notifier. Published for
        # the full-pass endpoint's fire-and-forget hook; also handed to the
        # scheduler below for its run-completed hook.
        notifier = build_notifier(config.notifications, http, session_factory)
        app.state.notifier = notifier

        health = PlexHealth(
            url=config.plex.url,
            token=secrets.plex_token,
            http=http,
            liveness_interval=config.plex.liveness_interval_seconds,
            refresh_interval=config.plex.token_refresh_interval_seconds,
            refresh_enabled=config.plex.token_refresh_enabled,
        )
        app.state.plex_health = health
        # Check once before workers start: a service booting during a Plex
        # outage should not immediately claim and fail a batch of jobs.
        await health.check_liveness()

        async with session_factory() as session:
            reclaimed = await reclaim_stale(session)
        if reclaimed:
            logger.info("reclaimed %d stale job(s)", reclaimed)

        # Reads our own EXIF provenance back off whatever artwork Plex is
        # currently serving, so the badge stage can tell that the correct image
        # is already there and skip the upload -- see pipeline._already_in_plex.
        artwork_probe = functools.partial(
            artwork_provenance, http,
            base_url=config.plex.url,
            headers={"X-Plex-Token": secrets.plex_token},
        )
        # config_holder, never the Config: this partial lives for the life of
        # the process, so a closured instance would pin every worker to the
        # generation that was current at boot. Handing it the holder makes
        # everything process_item reads per item -- badges.*, artwork.*, the
        # asset roots, operations.* -- live in one move. See _handle_intent.
        handler = functools.partial(
            _handle_intent, config_holder=app.state.config_holder, http=http,
            plex=app.state.plex, providers=app.state.providers,
            tmdb_facts=app.state.tmdb_facts, mdblist=app.state.mdblist,
            artwork_probe=artwork_probe,
        )
        imdb_refresh = ImdbAutoRefresh(
            session_factory, http,
            interval_hours=config.operations.imdb_refresh_hours,
            enabled=config.operations.imdb_refresh_enabled,
        )
        # Miss-triggered refresh (see facts/imdb.py's ImdbMissRefresh): installed
        # process-wide since gather_facts()'s signature carries no http client.
        imdb_module.configure_miss_refresh(http, config.operations.imdb_miss_refresh_minutes)
        health_task = asyncio.create_task(health.run(stop_event))
        # ImdbAutoRefresh deliberately keeps its own loop rather than joining
        # the scheduler below: its trigger is remote dataset staleness plus a
        # miss-triggered cooldown path (see facts/imdb.py), not a fixed
        # interval, so folding it in would mean losing that or bending the
        # scheduler around one job.
        imdb_task = asyncio.create_task(imdb_refresh.run(stop_event))

        # The job *set* is decided once, here, from the boot config: a swap
        # cannot register or drop a job, which is what puts scheduler.enabled
        # and the two per-job enabled flags in FROZEN_SECTIONS. Everything
        # inside a registered job -- its cadence included -- comes off the
        # holder per use and is live.
        holder = app.state.config_holder
        scheduler_jobs = []
        if config.scheduler.enabled:
            server_factory = functools.partial(PlexServer, config.plex.url, secrets.plex_token)
            if config.collections.enabled:
                scheduler_jobs.append(make_collections_job(holder, server_factory, http))
            scheduler_jobs.append(make_drift_job(holder))
            scheduler_jobs.append(make_cleanup_job(holder))
            if config.arr_sync.enabled:
                scheduler_jobs.append(make_arr_sync_job(holder, server_factory, http, secrets))
        # Published so config.live.swap_config can recompute the cadences
        # below without rebuilding the jobs -- it has no other way to reach
        # them, and rebuilding would silently change the job set.
        app.state.scheduler_jobs = scheduler_jobs
        # Published for GET /api/status, which has no other way to reach the
        # cadence: it is a field on the in-memory Job dataclass and is never
        # written to scheduled_runs. Built from the jobs actually registered
        # just above, so a job this configuration skipped is simply absent
        # and reports a null interval rather than a cadence nothing honours.
        #
        # Filled in place, never rebound: the dashboard broadcaster holds this
        # exact dict (create_app hands it the object, not a copy), so
        # replacing it here would leave the stream reporting null intervals
        # forever while /api/status reported the real ones.
        app.state.scheduler_intervals.update(
            {job.name: job.current_interval() for job in scheduler_jobs}
        )
        scheduler = Scheduler(
            session_factory, scheduler_jobs,
            poll_seconds=config.scheduler.poll_seconds, notifier=notifier,
        )
        scheduler_task = asyncio.create_task(scheduler.run(stop_event))

        task = asyncio.create_task(
            run_workers(
                config.workers, session_factory, handler, stop_event,
                is_healthy=lambda: health.healthy,
            )
        )
        logger.info("started %d workers", config.workers)
        try:
            yield
        finally:
            stop_event.set()
            task.cancel()
            health_task.cancel()
            imdb_task.cancel()
            scheduler_task.cancel()
            await asyncio.gather(
                task, health_task, imdb_task, scheduler_task, return_exceptions=True
            )
            imdb_module.configure_miss_refresh(http, 0)
            await http.aclose()
            logging.getLogger().removeHandler(app.state.log_buffer)

    # The interactive docs enumerate every endpoint and its request shape to
    # anyone who can reach the port, and FastAPI serves them outside the
    # router, so they cannot carry require_session. Off by default; passing
    # openapi_url=None is what actually removes /docs and /redoc too.
    docs = config.api_docs_enabled
    app = FastAPI(
        title="autoposter",
        lifespan=lifespan,
        openapi_url="/openapi.json" if docs else None,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
    )
    # The generation box, built here rather than in main.build() so that every
    # application -- the deployed one and every test's -- has one. Consumers
    # that want liveness are handed this; the per-request readers keep reading
    # app.state.config, which config.live.swap_config rebinds to the same
    # object the holder now holds. The two are never allowed to diverge.
    app.state.config_holder = ConfigHolder(config)
    app.state.config = config
    # The *file* the config was loaded from, which the config editor needs and
    # a loaded Config cannot tell it: an override is a delta over that
    # document, so reverting one means re-reading it. Published here so every
    # application has it; a test whose app was built from a different file
    # rebinds this to that file.
    app.state.config_path = DEFAULT_CONFIG_PATH
    app.state.session_factory = session_factory
    app.state.secrets = secrets
    # Per process, so every worker pod limits its own callers -- see
    # LoginRateLimiter.
    app.state.login_rate_limiter = LoginRateLimiter()
    # Created here so the /api/logs endpoints always have one to read, but
    # attached to the root logger only by the lifespan above (run_background
    # deployments) -- see the comment there.
    app.state.log_buffer = LogBuffer()
    if not secrets.admin_password_hash:
        # Once, here, rather than per attempt in the login handler: an
        # unauthenticated caller could otherwise flood the log at will.
        logger.warning(
            "AUTOPOSTER_ADMIN_PASSWORD_HASH is not set; this deployment has no "
            "admin password configured, so every login attempt will fail"
        )
    app.state.plex = None
    # Set by the lifespan, like app.state.plex is set by main.build(). Handlers
    # that need to talk to Plex check for None rather than making a client of
    # their own, so there stays exactly one AsyncClient to close on shutdown.
    app.state.http = None
    app.state.providers = []
    app.state.tmdb_facts = None
    app.state.mdblist = None
    # A NullNotifier, never None: the full-pass endpoint fires its hook
    # unconditionally, so test apps and no-lifespan instances must still hold
    # something with a send(). The lifespan replaces it with build_notifier's
    # result.
    app.state.notifier = NullNotifier()
    # {job name: interval_seconds} for the jobs the scheduler registered.
    # Empty here and unconditionally set, never left unbound: an app without
    # the background lifespan -- every test app, and any replica running with
    # the scheduler disabled -- still serves /api/status, and that handler
    # reads this. Filled by the lifespan's background branch.
    app.state.scheduler_intervals = {}
    # The Job objects behind that mapping, so a swap can recompute the
    # cadences. Empty and unconditionally set for the same reason as above:
    # swap_config must work on an app whose lifespan never ran.
    app.state.scheduler_jobs = []
    # Created here so /api/dashboard/stream always has one to subscribe to --
    # the log_buffer precedent above. No lifespan work: the poll loop is
    # subscriber-driven and its task is created from subscribe(), which runs
    # on the event loop create_app does not have. It is handed the interval
    # mapping itself so the lifespan's later fill (in place, see above) is
    # visible to it.
    app.state.dashboard_broadcaster = StatusBroadcaster(
        session_factory, app.state.config_holder, app.state.scheduler_intervals
    )
    app.include_router(router)
    app.include_router(api_router)

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def _build_providers(
    config: Config, secrets: Secrets, http: httpx.AsyncClient, cache: ProviderCache | None = None
) -> list:
    ttl = config.providers.cache_ttl_seconds
    by_name = {
        "TMDB": TMDBClient(
            secrets.tmdb_token, config.artwork.poster.language_order, http,
            cache=cache, cache_ttl_seconds=ttl,
        ),
        "TVDB": TVDBClient(secrets.tvdb_apikey, http, cache=cache, cache_ttl_seconds=ttl),
        "Fanart": FanartClient(secrets.fanart_apikey, http, cache=cache, cache_ttl_seconds=ttl),
    }
    providers = []
    for name in config.providers.order:
        if name not in by_name:
            logger.warning("configured provider %r has no implementation; skipping", name)
            continue
        providers.append(by_name[name])
    return providers


def _build_mdblist(
    secrets: Secrets,
    http: httpx.AsyncClient,
    cache: ProviderCache | None = None,
    cache_ttl_seconds: int = 24 * 3600,
):
    """The real client when an API key is configured, otherwise a stand-in.

    An operator who has not configured MDBList has no reason to suspect
    content ratings are the only thing affected unless told so explicitly
    (finding 1) — hence the warning naming the environment variable.
    """
    if secrets.mdblist_apikey:
        return MDBListClient(
            secrets.mdblist_apikey, http, cache=cache, cache_ttl_seconds=cache_ttl_seconds
        )
    logger.warning(
        "AUTOPOSTER_MDBLIST_APIKEY is not set; content ratings will be skipped "
        "while other metadata operations continue"
    )
    return NullMDBListClient()


async def _handle_intent(
    session, intent, *, config_holder, http, plex, providers, tmdb_facts=None, mdblist=None,
    artwork_probe=None,
):
    # Dereferenced once per job, at the top: process_item takes a config per
    # call already, so one read here is all it takes for a config swap to be
    # visible to the very next item a worker picks up. One read rather than
    # several also means a single job never straddles two generations.
    config = config_holder.current
    try:
        await process_item(
            session, config, http, plex, providers, intent,
            tmdb_facts=tmdb_facts, mdblist=mdblist, artwork_probe=artwork_probe,
        )
    except (ItemNotFound, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        # PlexHealth (see plex/health.py) gating run_worker's claiming is now
        # the primary defence against a Plex outage burning through retry
        # attempts — an unhealthy server means jobs are never claimed in the
        # first place. This is the fallback for an outage that begins between
        # health checks: threaded through to run_once via the exception
        # itself, so the queue worker's own signature stays untouched. Waiting
        # on Plex — whether it just hasn't scanned the file yet (ItemNotFound)
        # or is unreachable entirely (a connection/timeout error surfacing
        # from _LazyPlexServer's connect attempt, see main.py) — gets its own,
        # configurable attempt budget instead of the generic retry limit.
        exc.max_attempts = config.plex.resolve_max_attempts
        raise
