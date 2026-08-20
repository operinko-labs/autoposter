import asyncio
import functools
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from autoposter.config.schema import Config, Secrets
from autoposter.intake.routes import router
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
        by_name = {
            "TMDB": TMDBClient(secrets.tmdb_token, config.artwork.poster.language_order, http),
            "TVDB": TVDBClient(secrets.tvdb_apikey, http),
            "Fanart": FanartClient(secrets.fanart_apikey, http),
        }
        app.state.providers = [by_name[name] for name in config.providers.order if name in by_name]

        async with session_factory() as session:
            reclaimed = await reclaim_stale(session)
        if reclaimed:
            logger.info("reclaimed %d stale job(s)", reclaimed)

        handler = functools.partial(
            _handle_intent, config=config, http=http,
            plex=app.state.plex, providers=app.state.providers,
        )
        task = asyncio.create_task(
            run_workers(config.workers, session_factory, handler, stop_event)
        )
        logger.info("started %d workers", config.workers)
        try:
            yield
        finally:
            stop_event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
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


async def _handle_intent(session, intent, *, config, http, plex, providers):
    await process_item(session, config, http, plex, providers, intent)
