import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from plexapi.server import PlexServer

from autoposter.api.spa import mount_spa, spa_dist
from autoposter.app import create_app
from autoposter.config.loader import DEFAULT_CONFIG_PATH
from autoposter.config.overrides import load_effective_config
from autoposter.config.schema import Config, Secrets
from autoposter.db.base import make_engine, make_session_factory
from autoposter.plex.client import PlexClient

# One definition, shared with create_app (which publishes it as
# app.state.config_path for the config editor). Kept under this name because
# the suite monkeypatches main.CONFIG_PATH to point at the example config.
CONFIG_PATH = DEFAULT_CONFIG_PATH

logger = logging.getLogger(__name__)


class _LazyPlexServer:
    """Defers connecting to Plex until the server is actually used.

    ``PlexServer(...)`` makes a blocking network call. Doing that eagerly at
    boot means a Plex outage crashloops the whole pod, taking webhook intake
    down with it. Connecting lazily lets the process start, serve /healthz,
    and queue webhooks while Plex is unreachable; jobs that need Plex get
    ``config.plex.resolve_max_attempts`` worth of backoff (see
    ``_handle_intent`` in app.py) instead of the generic retry cap, but an
    outage longer than that still parks them permanently — see "Recovering
    parked jobs" in deploy/README.md to requeue them by hand. Every attribute
    access (already happening inside a worker thread via ``PlexClient``)
    triggers a (re)connect attempt if the previous one failed or never ran.
    """

    def __init__(self, url: str, token: str):
        self._url = url
        self._token = token
        self._server = None

    def _connect(self):
        if self._server is None:
            try:
                self._server = PlexServer(self._url, self._token)
            except Exception:
                logger.error("failed to connect to Plex at %s", self._url, exc_info=True)
                raise
        return self._server

    def __getattr__(self, name):
        return getattr(self._connect(), name)


def effective_config(database_url: str) -> Config:
    """The YAML config with the database overrides merged over it.

    ``build()`` is called by uvicorn before the server's event loop exists, so
    the overrides read runs in its own ``asyncio.run`` -- and therefore on its
    own engine, disposed before that loop closes. Handing the application's
    engine to a loop that is about to be thrown away would leave a dead
    asyncpg connection in its pool for the first request to find.

    The table is assumed to exist: every entry point runs behind
    ``alembic upgrade head`` (see docker-compose.yml's ``api`` command and the
    deployment's init step), which is already what the rest of the code
    assumes.
    """

    async def read() -> Config:
        engine = make_engine(database_url)
        try:
            async with make_session_factory(engine)() as session:
                return await load_effective_config(CONFIG_PATH, session)
        finally:
            await engine.dispose()

    return asyncio.run(read())


def build() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs one INFO line per request carrying the FULL url, and two of
    # this process's URLs embed secrets: the notifications webhook may carry
    # a token in its path (Uptime-Kuma style -- the host-only guarantee in
    # config/schema.py's NotificationsConfig), and fanart.tv's key travels
    # as an api_key query parameter (providers/fanart.py). Neither may reach
    # the pod logs, so httpx speaks only at WARNING and above.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    secrets = Secrets.from_env()
    config = effective_config(secrets.database_url)

    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
    # create_app publishes app.state.config_holder from this config -- the
    # generation the process starts on. Building the holder there rather than
    # here is what gives every test's application one too.
    app = create_app(config, session_factory, secrets, run_background=True)
    app.state.plex = PlexClient(
        server=_LazyPlexServer(config.plex.url, secrets.plex_token),
        excluded_libraries=config.plex.excluded_libraries,
    )
    # Last, and here rather than in create_app(): the SPA's catch-all matches
    # whatever no router claimed, so anything mounted afterwards is
    # unreachable. Keeping it out of the factory also keeps it out of the test
    # suite, which would otherwise pick up a stale frontend/dist from the
    # developer's checkout and quietly serve it during every test.
    mount_spa(app, spa_dist())
    return app


def main() -> None:
    uvicorn.run(build(), host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
