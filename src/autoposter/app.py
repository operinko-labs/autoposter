import asyncio
import functools
import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import requests
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from plexapi.server import PlexServer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select
from starlette.responses import Response

from autoposter.api.auth import LoginRateLimiter
from autoposter.artwork_modes.base import WorkerPause
from autoposter.api.dashboard_stream import StatusBroadcaster
from autoposter.api.errors import validation_error_without_input
from autoposter.api.logs import LogBuffer
from autoposter.api.routes import router as api_router
from autoposter.api.version import ReleasePoller
from autoposter.config.holder import ConfigHolder
from autoposter.config.live import swap_config
from autoposter.config.loader import DEFAULT_CONFIG_PATH
from autoposter.config.overrides import load_effective_config
from autoposter.config.schema import STATE_FILE_NAMES_ENV, Config, Secrets
from autoposter.facts import imdb as imdb_module
from autoposter.facts.imdb import ImdbAutoRefresh
from autoposter.facts.mdblist import MDBListClient, NullMDBListClient
from autoposter.facts.tmdb_budget import TmdbRateBudget
from autoposter.facts.tmdb_facts import TMDBFactsClient
from autoposter.intake.arr import RenderIntent
from autoposter.intake.routes import router
from autoposter.notify.dispatch import NullNotifier, build_notifier
from autoposter.plex.client import PlexClient
from autoposter.plex.health import PlexHealth
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fanart import FanartClient
from autoposter.providers.imdb_parental_guide import IMDbParentalGuideClient
from autoposter.providers.tmdb import TMDBClient
from autoposter.providers.tvdb import TVDBClient
from autoposter.queue.jobs import reclaim_stale
from autoposter.queue.worker import run_workers
from autoposter.render.pipeline import SourceRefused, fetch_plex_generated_base, process_item
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import (
    make_arr_sync_job,
    make_asset_stats_job,
    make_cleanup_job,
    make_collections_job,
    make_credits_job,
    make_drift_job,
    make_maintenance_job,
    make_stale_reclaim_job,
)
from autoposter.scheduler.merge import make_merge_job
from autoposter.scheduler.prune import make_prune_job

logger = logging.getLogger(__name__)


def create_app(
    config: Config, session_factory, secrets: Secrets, run_background: bool = False,
    plex_factory: Callable[[Config], PlexClient] | None = None,
    engine=None,
) -> FastAPI:
    """The application, built from ``config`` -- the *file* generation.

    Note what ``config`` is and is not under ``run_background=True``. It is
    the generation the application object itself is shaped by (the docs
    routes) and the one the holder starts on, which is what every request
    arriving before startup finishes would see -- there are none. It is *not*
    what the background services boot on: the lifespan loads the effective
    config (this file plus the database overrides) for itself and swaps it in
    before building any of them, so mutating this argument to configure a
    background application does nothing. Configure one the way a deployment
    does, through the file at ``app.state.config_path`` or the overrides row.

    ``plex_factory`` is a callable taking the effective ``Config`` and
    returning the client to publish as ``app.state.plex``. Only
    ``main.build()`` passes one, exactly as only ``main.build()`` passes
    ``run_background=True``: both are production wiring the lifespan performs
    on its behalf, because both need something ``build()`` cannot have -- a
    running event loop, and the config generation that loop reads out of the
    database. Every test application passes neither and keeps
    ``app.state.plex`` as ``None``.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not run_background:
            yield
            return
        # The effective config -- the mounted file with the database overrides
        # merged over it -- is loaded HERE, first, and nowhere earlier.
        #
        # Not in main.build(), because `uvicorn --factory` calls build() from
        # inside the server's own event loop: an await is impossible there and
        # an asyncio.run bridge raises outright (see main.build's docstring).
        # This is the first point in the boot that is both on the loop and
        # holding the session factory.
        #
        # First, because everything below -- the provider clients, the
        # notifier, the Plex client, the liveness probe, the scheduler's job
        # set, the worker pool -- is constructed from `config`, and `config`
        # is rebound here to the swapped generation. Note what that rebinding
        # buys beyond correctness: `config` is now a local of this function,
        # so a config read accidentally added *above* this line is an
        # UnboundLocalError at boot rather than a silent read of the
        # un-overridden file.
        #
        # The overrides table is assumed to exist: every entry point runs
        # behind `alembic upgrade head` (docker-compose.yml's `api` command,
        # the deployment's init step), which is what the rest of this
        # lifespan already assumes of the schema.
        async with session_factory() as session:
            swap_config(app, await load_effective_config(app.state.config_path, session))
            # The single-clock fix for /api/status's derived job status (see
            # api/snapshots.py._run_status): last_started_at is stamped by
            # Postgres's own now() (scheduler/core.py's claim_due), so the
            # boot instant it is compared against has to come from the same
            # clock, not the app process's -- a deployment where the two
            # hosts' clocks disagree would otherwise mislabel the first run
            # after every restart as "interrupted" for its whole duration.
            # Read here, in the same session as the config swap, replacing
            # create_app's Python-clock placeholder.
            app.state.started_at = (
                await session.execute(select(func.now()))
            ).scalar_one()
            # Permanent visibility into the real skew the comment above
            # reasons about in the abstract: logged once, at boot, rather
            # than re-measured periodically -- the single-clock rule doesn't
            # need a live skew value to hold, only this one number on record
            # for whoever is diagnosing a mislabeled run later.
            delta_ms = round(
                (app.state.started_at - datetime.now(UTC)).total_seconds() * 1000
            )
            logger.info(
                "boot: database clock is %+dms relative to the process clock",
                delta_ms,
            )
        config = app.state.config_holder.current
        # create_app built the broadcaster from the placeholder above --
        # construction happens before this lifespan runs, and create_app has
        # no event loop to await the database read with. Rebuilt here, before
        # the app can have any subscribers, so the live stream's derived
        # status agrees with /api/status instead of comparing every run's
        # start against a stale Python-clock boot instant forever. The
        # session factory, config holder and scheduler-intervals mapping are
        # the same objects create_app handed the original broadcaster.
        app.state.dashboard_broadcaster = StatusBroadcaster(
            session_factory, app.state.config_holder, app.state.scheduler_intervals,
            started_at=app.state.started_at,
        )
        if plex_factory is not None:
            app.state.plex = plex_factory(config)
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
        # Published because the collections preview endpoint builds that
        # pass's source clients per request, the way the scheduled job builds
        # them per run -- see collections.service.build_source_clients.
        app.state.provider_cache = cache
        app.state.providers = _build_providers(config, secrets, http, cache)
        app.state.tmdb_facts = TMDBFactsClient(
            secrets.tmdb_token, http, cache=cache,
            cache_ttl_seconds=config.providers.cache_ttl_seconds,
            # The shared 429 window. Built here rather than inside the client
            # because it needs a session factory and the client holds none --
            # the same reason ProviderCache is constructed here.
            budget=TmdbRateBudget(
                session_factory, config.operations.tmdb_backoff_seconds
            ),
        )
        # A stand-in (never None) when no key is configured: only the
        # content-rating field MDBList would have supplied is affected, so
        # every other metadata operation still runs. See _build_mdblist.
        app.state.mdblist = _build_mdblist(
            secrets, http, cache=cache, cache_ttl_seconds=config.providers.cache_ttl_seconds
        )
        # Row 85. No API key needed -- the endpoint is public and
        # unauthenticated, same as charts.py/imdb_graphql.py -- so this is
        # always built, unlike TVDb/MDBList's credential-gated stand-ins.
        # Whether it is ever asked anything is entirely
        # operations.parental_labels_enabled's call, made per item in
        # render.pipeline.apply_metadata.
        app.state.imdb_parental = IMDbParentalGuideClient(
            http, cache=cache, cache_ttl_seconds=config.providers.cache_ttl_seconds
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

        # Replaces create_app's http=None placeholder with one that can
        # actually poll, now that `http` exists. See api/version.py's
        # ReleasePoller and its module docstring for why this is a
        # background poll rather than a request-path fetch, and why a build
        # that is not a release never polls at all.
        app.state.version_poller = ReleasePoller(http=http)

        async with session_factory() as session:
            reclaimed = await reclaim_stale(session)
        if reclaimed:
            logger.info("reclaimed %d stale job(s)", reclaimed)

        # Reads our own EXIF provenance back off whatever artwork Plex is
        # currently serving, so the badge stage can tell that the correct image
        # is already there and skip the upload -- see pipeline._already_in_plex.
        artwork_probe = functools.partial(_artwork_provenance_probe, app.state.plex)
        # The plex-preview fallback (roadmap row 241): when no provider has
        # a title_card, ask Plex for the frame it derived from the media
        # file itself (posters(), the media://-prefixed entry -- never our
        # own upload:// or an agent guess, see
        # plex/artwork.generated_title_card_url and the probe banked at
        # docs/research/2026-09-03-plex-episode-posters-probe.md). Built
        # here, once, so render_artifact never holds the token -- the same
        # shape as artwork_probe just above.
        plex_generated_base = functools.partial(
            fetch_plex_generated_base, http, app.state.plex,
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
            artwork_probe=artwork_probe, imdb_parental=app.state.imdb_parental,
            plex_generated_base=plex_generated_base,
        )

        # The dispatch map the worker pool runs. process_item is registered
        # like any other kind (Phase 7b): the entry decodes the RenderIntent
        # payload the render pipeline expects, so run_once stays kind-agnostic.
        # The mode kinds (backup/restore/reset/revert/logo) register their own
        # entries here as they land -- this is the one place that both holds the
        # per-process dependencies a handler needs and can reach app.state.
        async def process_item_handler(session, job):
            await handler(session, RenderIntent(**job.payload))

        handlers = {"process_item": process_item_handler}
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
        # Same shape again: a fixed-cadence background poll with no other
        # trigger, so it gets its own task rather than joining the scheduler.
        version_task = asyncio.create_task(app.state.version_poller.run(stop_event))

        # The job *set* is decided once, here, from the boot config: a swap
        # cannot register or drop a job, which is what puts scheduler.enabled
        # and the two per-job enabled flags in FROZEN_SECTIONS. Everything
        # inside a registered job -- its cadence included -- comes off the
        # holder per use and is live.
        holder = app.state.config_holder
        # How anything that needs a *raw* PlexServer gets one: the collections
        # engine walks library sections and their collections, which is
        # plexapi surface PlexClient (app.state.plex) deliberately does not
        # expose. Published rather than kept local to the scheduler branch
        # below because the collections preview endpoint needs the same thing
        # on a replica running with the scheduler off. Connecting blocks, so
        # every caller runs it through asyncio.to_thread.
        server_factory = functools.partial(PlexServer, config.plex.url, secrets.plex_token)
        app.state.plex_server_factory = server_factory
        # Registered unconditionally, unlike everything below: scheduler.enabled
        # is the master switch for the five optional maintenance passes
        # (config/schema.py's SchedulerConfig docstring), not for queue
        # correctness. This job closes the gap the boot-time reclaim_stale()
        # call above cannot -- a claim going stale while the process stays up,
        # or a second restart's boot reclaim missing what the first restart
        # just orphaned -- and disabling the maintenance passes must not also
        # disable that.
        scheduler_jobs = [make_stale_reclaim_job()]
        if config.scheduler.enabled:
            # The playlists pass (roadmap row 98a) rides this job, and it is
            # gated on its OWN switch inside the job body -- so the job has to
            # exist whenever EITHER half is wanted. Registering on
            # collections.enabled alone would make playlists.enabled a setting
            # that reads as configured and silently is not, which is the failure
            # this phase's refusal table exists to prevent, one level up from
            # the config.
            if config.collections.enabled or config.playlists.enabled:
                scheduler_jobs.append(make_collections_job(
                    holder, server_factory, http, summaries=app.state.tmdb_facts,
                    secrets=secrets, cache=cache, notifier=notifier,
                ))
            scheduler_jobs.append(make_drift_job(holder))
            scheduler_jobs.append(make_credits_job(holder, server_factory))
            scheduler_jobs.append(make_maintenance_job(holder, server_factory))
            scheduler_jobs.append(make_cleanup_job(holder))
            # Roadmap row 52's backfill, beside the cleanup sweep because they
            # are the two passes that read the asset tree. It needs nothing but
            # the holder: no Plex, no HTTP, no provider clients -- it stats the
            # paths renders rows already name.
            scheduler_jobs.append(make_asset_stats_job(holder))
            # The prune sweep needs a PlexClient rather than a raw PlexServer:
            # "gone" here means "the pipeline cannot resolve it", which is
            # PlexClient's section-constrained search and its library
            # exclusions, not a bare fetchItem. Built inside the factory so
            # that both the connect and the client construction happen on the
            # thread the job offloads to, and read off the holder -- so an
            # edited exclusion list reaches this job on its next run, even
            # before the restart the rest of the `plex` section waits for
            # (it is a FROZEN_SECTIONS entry; the settings editor tells the
            # operator so). The server connection itself still comes from
            # server_factory, built once at startup like the rest of `plex`.
            # `health.healthy` is passed as a deref for the same reason the
            # worker pool takes one: an unhealthy Plex must be seen at the
            # moment the pass starts, and for THIS job it means refuse, not
            # wait.
            scheduler_jobs.append(make_prune_job(
                holder,
                lambda: PlexClient(
                    server_factory(), holder.current.plex.excluded_libraries
                ),
                lambda: health.healthy,
            ))
            # The twin merge takes the same PlexClient the prune does, and for
            # the same reason: it asks whether a stored rating key is still
            # the item's own key, which is a section-constrained question that
            # honours the library exclusions. Built inside the factory so the
            # connect and the client construction both happen on the thread
            # the job offloads to, and read off the holder so an edited
            # exclusion list reaches it on its next run.
            #
            # `health.healthy` gates only this job's APPLY, not its scan: the
            # dry run asks Plex nothing at all, so an outage must not cost the
            # operator the report (see make_merge_job's docstring).
            scheduler_jobs.append(make_merge_job(
                holder,
                lambda: PlexClient(
                    server_factory(), holder.current.plex.excluded_libraries
                ),
                lambda: health.healthy,
            ))
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
            config_holder=app.state.config_holder,
        )
        scheduler_task = asyncio.create_task(scheduler.run(stop_event))

        task = asyncio.create_task(
            run_workers(
                config.workers, session_factory, handlers, stop_event,
                is_healthy=lambda: health.healthy, pause=app.state.worker_pause,
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
            version_task.cancel()
            scheduler_task.cancel()
            await asyncio.gather(
                task, health_task, imdb_task, version_task, scheduler_task,
                return_exceptions=True,
            )
            imdb_module.configure_miss_refresh(http, 0)
            await http.aclose()
            if app.state.engine is not None:
                # Last, and deliberately after the gather: dispose() closes
                # every pooled connection, and a task still being cancelled
                # has not yet returned its session to the pool. Without this
                # the process simply exited and Postgres saw a socket close
                # rather than a terminate -- "unexpected EOF on client
                # connection with an open transaction", all 5 pooled
                # connections, at every deploy. None for every application but
                # main.build()'s, whose engine this is.
                try:
                    await app.state.engine.dispose()
                except Exception as exc:  # noqa: BLE001 - shutdown must not fail on cleanup
                    logger.warning("engine dispose failed during shutdown: %s", type(exc).__name__)
            logging.getLogger().removeHandler(app.state.log_buffer)

    # The interactive docs enumerate every endpoint and its request shape to
    # anyone who can reach the port, and FastAPI serves them outside the
    # router, so they cannot carry require_session. Off by default; passing
    # openapi_url=None is what actually removes /docs and /redoc too.
    #
    # Read from the file generation, and only ever from it: this decision is
    # baked into the FastAPI object itself, which exists before the lifespan
    # runs and therefore before any override is known. A database override on
    # api_docs_enabled consequently does nothing, restart or not -- the one
    # setting in the schema that is genuinely file-only. FROZEN_SECTIONS says
    # so in the reason the editor renders, and deploy/README.md says so in
    # the overrides section.
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
    # A placeholder boot instant -- Python-clock, because create_app is
    # synchronous and cannot await the database read that fixes it. /api/status
    # compares a scheduled job's last_started_at (a Postgres-stamped column,
    # see scheduler/core.py's claim_due) against this value, and that
    # comparison is only sound against the SAME clock the column was stamped
    # from -- claim_due's own docstring is explicit that a due check must run
    # "against the database clock, never datetime.now()". The lifespan below
    # overwrites this with a `SELECT now()` read, single-clock end to end,
    # before any request can be served; this placeholder only survives for an
    # app whose lifespan never runs (every test app that does not opt into
    # run_background). Set unconditionally anyway, like the other state above,
    # so such an app still has one.
    app.state.started_at = datetime.now(UTC)
    # The *file* the config was loaded from, which a loaded Config cannot tell
    # anyone: an override is a delta over that document, so both reverting one
    # (the config editor) and applying one (the lifespan's merge above) mean
    # re-reading it. Published here so every application has it; main.build()
    # rebinds it to the path it actually read, and so does a test whose app was
    # built from a different file.
    app.state.config_path = DEFAULT_CONFIG_PATH
    app.state.session_factory = session_factory
    # The engine behind that factory, so the lifespan can dispose() it on the
    # way out -- see the `finally` above. None for every application but
    # main.build()'s, exactly like plex_factory: a test app's engine belongs
    # to the fixture that made it and must not be disposed here.
    app.state.engine = engine
    app.state.secrets = secrets
    # WHICH of this deployment's secrets came from the state file, as a
    # per-name boolean -- read with `.get(name, False)`, so a name the marker
    # does not carry is "not from the file". `boot` publishes the NAMES across
    # its exec (see `state_file_secret_names`), and this is the only reader.
    #
    # Read from os.environ directly at this construction path, the way the
    # version stamp the poller below reads is and for the same reason: it is
    # not a credential, it is a deployment fact, and routing it through Secrets
    # would make every test app fake a value for it.
    #
    # FAIL CLOSED. An application that never went through `boot` -- every test
    # app, and an operator running `python -m autoposter.main` -- has no
    # marker and answers False for every name, which is the C5 refusal. That
    # is deliberate: the refusal is the path a test gets for free and the
    # permission is the one a test has to opt into.
    app.state.secret_from_state_file = {
        name: True for name in os.environ.get(STATE_FILE_NAMES_ENV, "").split(",") if name
    }
    # http=None here -- create_app has no http client yet, only the lifespan
    # builds one -- so this placeholder never actually polls; GET /api/version
    # still has something to read from every test app that never runs the
    # background branch. The lifespan below replaces this with a poller that
    # can, the same app.state.plex / app.state.http precedent.
    #
    # The poller reads its own build stamp from the environment (see
    # api/version.py's `_running_version`): it is not a credential, it is a
    # deployment fact baked into the image, so routing it through Secrets would
    # make every test app fake a value for it.
    app.state.version_poller = ReleasePoller(http=None)
    # The worker-pause fence (Phase 7b). Created here so every application --
    # the deployed one and every test's -- has one for a mode trigger endpoint
    # to reach; the background lifespan hands this exact object to the worker
    # pool. A no-lifespan app holds one that simply nothing awaits.
    app.state.worker_pause = WorkerPause()
    # One applied artwork mode at a time in this process (Phase 7b). Two
    # concurrent applied runs would each raise the fence above and the first to
    # finish would drop it under the second -- and both would be writing to Plex
    # at once. Created here for the same reason the fence is: every application
    # must have one for the trigger endpoint to reach.
    app.state.mode_lock = asyncio.Lock()
    # Per process, so every worker pod limits its own callers -- see
    # LoginRateLimiter.
    app.state.login_rate_limiter = LoginRateLimiter()
    # One rotation at a time in this process. `merge_secrets_file` is a
    # read-modify-write over one file, and two concurrent rotations would
    # additionally mint two secrets, leave the *arrs holding one and this
    # application the other, and show both to the operator as if each had
    # worked. Created here for the reason `mode_lock` above is: every
    # application must have one for the route to reach.
    app.state.secret_rotation_lock = asyncio.Lock()
    # Its own limiter rather than the login table, so a rotation cannot spend
    # an operator's login budget and a login flood cannot lock the rotation
    # out. Five a minute: the action writes a file and makes two 10-second
    # outbound calls, and an operator performs it a handful of times in a
    # deployment's life.
    app.state.rotation_rate_limiter = LoginRateLimiter(
        max_attempts=5, window_seconds=60.0
    )
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
    # Built by the lifespan from plex_factory, once it holds the effective
    # config -- see the top of the lifespan and main.build's plex_client.
    app.state.plex = None
    # A zero-argument callable returning a connected PlexServer, set by the
    # lifespan's background branch. None here for the same reason plex is: an
    # app without that branch -- every test app -- has no Plex connection, and
    # a handler that needs one answers 503 rather than making its own.
    app.state.plex_server_factory = None
    # Set by the lifespan too. Handlers that need to talk to Plex check for
    # None rather than making a client of their own, so there stays exactly
    # one AsyncClient to close on shutdown.
    app.state.http = None
    app.state.providers = []
    app.state.tmdb_facts = None
    app.state.mdblist = None
    app.state.imdb_parental = None
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
        session_factory, app.state.config_holder, app.state.scheduler_intervals,
        started_at=app.state.started_at,
    )
    # Before the routers, though order does not matter to starlette: this is a
    # property of the application, not of any one endpoint. See the handler's
    # own docstring for what it drops and why.
    app.add_exception_handler(RequestValidationError, validation_error_without_input)

    # Roadmap row 121. The SPA probes this before it holds any credential, to
    # decide whether to render the login form or the first-start wizard, so it
    # must answer without one -- and it answers the same shape the setup
    # application answers, with the one bit reversed. `password_set` is
    # deliberately absent here: whether this deployment has an admin hash is
    # not something an unauthenticated caller may ask.
    #
    # Registered on the app rather than on api_router, and out of the schema,
    # on purpose. tests/test_api_login.py's structural sweep enumerates the
    # DOCUMENTED /api surface and requires every path on it to 401 without a
    # session; that sweep is the guard that a route added later cannot ship
    # open, and exempting a path from it is how such a sweep stops being
    # structural. This one route is pinned instead by tests/test_api_setup.py,
    # which asserts it is the ONLY /api path on this application registered
    # outside the schema.
    @app.get("/api/setup/state", include_in_schema=False)
    async def setup_state() -> dict:
        return {"setup": False}

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


async def _artwork_provenance_probe(plex, ref, art_kind):
    """``artwork_probe``'s callable shape, built over ``server.artwork_provenance``.

    ``plex`` is captured as a plain argument, like ``plex_generated_base``'s own
    partial captures it, rather than read off ``app.state`` at partial-construction
    time -- a test's ``plex_factory`` can hand back a bare stand-in that has no
    such method, and this must not touch it until the probe is actually called.
    """
    return await plex.artwork_provenance(ref, art_kind)


async def _handle_intent(
    session, intent, *, config_holder, http, plex, providers, tmdb_facts=None, mdblist=None,
    artwork_probe=None, imdb_parental=None, plex_generated_base=None,
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
            imdb_parental=imdb_parental, plex_generated_base=plex_generated_base,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        # PlexHealth (see plex/health.py) gating run_worker's claiming is now
        # the primary defence against a Plex outage burning through retry
        # attempts — an unhealthy server means jobs are never claimed in the
        # first place. This is the fallback for an outage that begins between
        # health checks: a Plex that is unreachable entirely (a connection or
        # timeout error surfacing from _LazyPlexServer's connect attempt, see
        # main.py) gets its own, configurable attempt budget instead of the
        # generic retry limit, threaded through to run_once via the exception
        # itself so the queue worker's own signature stays untouched.
        #
        # ItemNotFound is deliberately NOT tagged here any more: a Plex that
        # simply has not scanned the file yet is not on a budget at all, it is
        # deferred on an unbounded horizon (queue/worker.py, queue/jobs.py's
        # fail()). A budget threaded onto it would be read by nothing.
        exc.max_attempts = config.plex.resolve_max_attempts
        raise
    except SourceRefused as exc:
        # A validation refusal (render/pipeline.py's own docstring) is
        # deterministic: retrying re-downloads the exact same corrupt bytes,
        # so the generic 5-attempt budget just burns four attempts on a
        # source that cannot change between them. Parked on the first
        # attempt instead -- the PlexPathMismatch precedent (queue/worker.py:
        # "it needs a human") -- threaded through the exception itself like
        # resolve_max_attempts above, so run_once's own signature stays
        # untouched. A retry from Failures still re-runs this job in full: if
        # the source is fixed upstream by then, the next attempt renders
        # normally.
        exc.max_attempts = 1
        raise
