from pathlib import Path

import httpx
import pytest
import requests

from autoposter.app import _build_mdblist, _build_providers, _handle_intent
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.facts.mdblist import MDBListClient, NullMDBListClient
from autoposter.intake.arr import RenderIntent
from autoposter.providers.fanart import FanartClient
from autoposter.providers.tmdb import TMDBClient
from autoposter.queue.jobs import MAX_ATTEMPTS

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


async def test_handle_intent_tags_plex_connection_errors_with_resolve_max_attempts(monkeypatch):
    # Finding 1: a Plex outage surfaces as a requests connection/timeout error
    # (see _LazyPlexServer._connect in main.py), not ItemNotFound, so
    # _handle_intent must classify it the same way — otherwise run_once falls
    # back to the generic MAX_ATTEMPTS cap and the job parks after ~450s.
    config = load_config(EXAMPLE)
    assert config.plex.resolve_max_attempts != MAX_ATTEMPTS  # sanity: distinct budgets

    async def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Plex unreachable")

    monkeypatch.setattr("autoposter.app.process_item", boom)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        await _handle_intent(
            None, intent, config=config, http=None, plex=None, providers=[],
        )

    assert exc_info.value.max_attempts == config.plex.resolve_max_attempts


async def test_missing_mdblist_key_warns_and_returns_a_stand_in(secrets, caplog):
    # Finding 1: an unconfigured MDBList key must degrade only content
    # ratings, and the operator must be told why via a startup warning
    # naming the environment variable, not left to discover it by omission.
    assert secrets.mdblist_apikey == ""

    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            mdblist = _build_mdblist(secrets, http)

    assert isinstance(mdblist, NullMDBListClient)
    assert await mdblist.content_rating(tmdb_id=1, is_movie=True) is None
    assert any(
        "AUTOPOSTER_MDBLIST_APIKEY" in record.message for record in caplog.records
    )


async def test_configured_mdblist_key_builds_the_real_client_without_warning(secrets, caplog):
    secrets.mdblist_apikey = "KEY"

    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            mdblist = _build_mdblist(secrets, http)

    assert isinstance(mdblist, MDBListClient)
    assert not any(
        "AUTOPOSTER_MDBLIST_APIKEY" in record.message for record in caplog.records
    )
