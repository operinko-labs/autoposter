import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from plexapi.server import PlexServer

from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.base import make_engine, make_session_factory
from autoposter.plex.client import PlexClient

CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))


def build() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config(CONFIG_PATH)
    secrets = Secrets.from_env()

    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
    app = create_app(config, session_factory, secrets, run_background=True)

    app.state.plex = PlexClient(
        server=PlexServer(config.plex.url, secrets.plex_token),
        excluded_libraries=config.plex.excluded_libraries,
    )
    return app


def main() -> None:
    uvicorn.run(build(), host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
