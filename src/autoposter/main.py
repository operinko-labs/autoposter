import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from plexapi.server import PlexServer

from autoposter.api.spa import mount_spa, spa_dist
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.base import make_engine, make_session_factory
from autoposter.plex.client import PlexClient

CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))

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


def build() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config(CONFIG_PATH)
    secrets = Secrets.from_env()

    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
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
