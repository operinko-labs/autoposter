from pathlib import Path

import httpx
import pytest
import requests
from httpx import ASGITransport, AsyncClient

from autoposter.app import _build_mdblist, _build_providers, _handle_intent, create_app
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


async def _get(app, path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_the_api_docs_are_off_by_default(secrets):
    """/docs, /redoc and /openapi.json enumerate every endpoint to anyone who
    can reach the port, and FastAPI serves them outside the router, so they
    cannot carry require_session."""
    config = load_config(EXAMPLE)
    assert config.api_docs_enabled is False
    app = create_app(config, session_factory=None, secrets=secrets)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await _get(app, path)).status_code == 404, path


async def test_the_api_docs_can_be_switched_on_deliberately(secrets):
    config = load_config(EXAMPLE)
    config.api_docs_enabled = True
    app = create_app(config, session_factory=None, secrets=secrets)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await _get(app, path)).status_code == 200, path


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


async def test_handle_intent_passes_the_artwork_probe_through_to_process_item():
    """The badge stage's provenance read is only useful if it is actually
    wired: app.py builds the partial, _handle_intent has to carry it."""
    seen = {}

    async def capture(*args, **kwargs):
        seen.update(kwargs)

    probe = object()
    config = load_config(EXAMPLE)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("autoposter.app.process_item", capture)
        await _handle_intent(
            None, RenderIntent(kind="movie", title="Dune", tmdb_id=1),
            config=config, http=None, plex=None, providers=[], artwork_probe=probe,
        )

    assert seen["artwork_probe"] is probe


async def test_the_wired_artwork_probe_reads_provenance_for_one_plex_item(monkeypatch):
    """The shape app.py builds -- functools.partial(artwork_provenance, http,
    base_url=..., headers=...) -- must be callable with just the plexapi object."""
    import functools

    from autoposter.plex.artwork import artwork_provenance
    from autoposter.plex.exif import format_provenance

    calls = []

    async def fake_probe_exif(http, url, headers):
        calls.append((url, headers))
        return {0x010E: format_provenance("fp-xyz")}

    monkeypatch.setattr("autoposter.plex.artwork.probe_exif", fake_probe_exif)
    probe = functools.partial(
        artwork_provenance, None,
        base_url="http://plex.local/", headers={"X-Plex-Token": "tok"},
    )

    result = await probe(type("Item", (), {"thumb": "/library/metadata/1/thumb/1"})())

    assert result == "fp-xyz"
    assert calls == [
        ("http://plex.local/library/metadata/1/thumb/1", {"X-Plex-Token": "tok"})
    ]
