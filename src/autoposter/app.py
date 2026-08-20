import asyncio
import functools
import logging
from contextlib import asynccontextmanager

import httpx
import requests
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from autoposter.config.schema import Config, Secrets
from autoposter.intake.routes import router
from autoposter.plex.client import ItemNotFound
from autoposter.plex.health import PlexHealth
from autoposter.providers.fanart import FanartClient
from autoposter.providers.tmdb import TMDBClient
from autoposter.providers.tvdb import TVDBClient
from autoposter.queue.jobs import reclaim_stale
from autoposter.queue.worker import run_workers
from autoposter.render.pipeline import process_item

logger = logging.getLogger(__name__)


def create_app(
    config: Config, session_factory, secrets: Secrets, run_background: bool = False
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not run_background:
            yield
            return
        stop_event = asyncio.Event()
        # Single client for the process: provider clients borrow it rather than
        # each owning one, so there is exactly one AsyncClient to close on shutdown.
        http = httpx.AsyncClient(timeout=30.0)
        app.state.providers = _build_providers(config, secrets, http)

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

        handler = functools.partial(
            _handle_intent, config=config, http=http,
            plex=app.state.plex, providers=app.state.providers,
        )
        health_task = asyncio.create_task(health.run(stop_event))
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
            await asyncio.gather(task, health_task, return_exceptions=True)
            await http.aclose()

    app = FastAPI(title="autoposter", lifespan=lifespan)
    app.state.config = config
    app.state.session_factory = session_factory
    app.state.secrets = secrets
    app.state.plex = None
    app.state.providers = []
    app.include_router(router)

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def _build_providers(config: Config, secrets: Secrets, http: httpx.AsyncClient) -> list:
    by_name = {
        "TMDB": TMDBClient(secrets.tmdb_token, config.artwork.poster.language_order, http),
        "TVDB": TVDBClient(secrets.tvdb_apikey, http),
        "Fanart": FanartClient(secrets.fanart_apikey, http),
    }
    providers = []
    for name in config.providers.order:
        if name not in by_name:
            logger.warning("configured provider %r has no implementation; skipping", name)
            continue
        providers.append(by_name[name])
    return providers


async def _handle_intent(session, intent, *, config, http, plex, providers):
    try:
        await process_item(session, config, http, plex, providers, intent)
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
