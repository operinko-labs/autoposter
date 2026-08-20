import logging
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from plexapi.server import PlexServer

from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.base import make_engine, make_session_factory
from autoposter.plex.client import PlexClient
from autoposter.providers.fanart import FanartClient
from autoposter.providers.tmdb import TMDBClient
from autoposter.providers.tvdb import TVDBClient

CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))


def build() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = load_config(CONFIG_PATH)
    secrets = Secrets.from_env()

    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
    app = create_app(config, session_factory, secrets, run_background=True)

    http = httpx.AsyncClient(timeout=30.0)
    by_name = {
        "TMDB": TMDBClient(secrets.tmdb_token, config.artwork.poster.language_order, http),
        "TVDB": TVDBClient(secrets.tvdb_apikey, http),
        "Fanart": FanartClient(secrets.fanart_apikey, http),
    }
    app.state.providers = [by_name[name] for name in config.providers.order if name in by_name]
    app.state.plex = PlexClient(
        server=PlexServer(config.plex.url, secrets.plex_token),
        excluded_libraries=config.plex.excluded_libraries,
    )
    return app


def main() -> None:
    uvicorn.run(build(), host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
