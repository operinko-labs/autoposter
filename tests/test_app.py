from pathlib import Path

import httpx
import pytest

from autoposter.app import _build_providers
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.providers.fanart import FanartClient
from autoposter.providers.tmdb import TMDBClient

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def secrets():
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
    )


async def test_unknown_provider_is_warned_about_and_skipped(secrets, caplog):
    # Regression guard for finding 5: a typo'd or stale provider name (e.g. the
    # "Plex" provider this project does not implement) must be dropped with a
    # clear warning, not silently produce an empty provider list.
    config = load_config(EXAMPLE)
    config.providers.order = ["TMDB", "Plex", "Fanart"]

    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            providers = _build_providers(config, secrets, http)

    assert [type(p) for p in providers] == [TMDBClient, FanartClient]
    assert any("Plex" in record.message for record in caplog.records)
