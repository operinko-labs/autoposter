"""The mounted file's new job: a drift report.

The file is no longer a source of truth, so the one thing it can still say is
"the configuration in git is not the configuration that is running". The
notice offers Import and Export, both of which already exist.

The round trip at the bottom is the other half of the same change: with
`overridden_paths` gone, the page seeds its pending document from the whole
served configuration and sends the whole thing back. That is only safe if what
comes back is what was there, so this file proves it against a real store
rather than against the page's own idea of one.
"""
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import build_config
from autoposter.config.overrides import (
    STORE_FORMAT,
    document_paths,
    load_effective_config,
    load_store,
    seed_store,
)
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def config_file(tmp_path) -> Path:
    path = tmp_path / "autoposter.yaml"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


@pytest_asyncio.fixture
async def app(session_factory, config_file):
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    application = create_app(build_config(document), session_factory, _secrets())
    application.state.config_path = config_file
    async with session_factory() as session:
        await seed_store(session, document)
        await session.commit()
    return application


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.mark.asyncio
async def test_a_seeded_store_matching_its_file_reports_no_drift(client, auth_headers):
    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["file_present"] is True
    assert body["differs"] is False
    assert body["paths"] == []


@pytest.mark.asyncio
async def test_an_edited_file_reports_the_paths_that_differ(
    client, auth_headers, config_file
):
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    document["workers"] = document["workers"] + 1
    config_file.write_text(yaml.safe_dump(document), encoding="utf-8")

    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["differs"] is True
    assert body["paths"] == ["workers"]


@pytest.mark.asyncio
async def test_the_drift_report_names_the_file_it_read(client, auth_headers, config_file):
    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["path"] == str(config_file)


@pytest.mark.asyncio
async def test_no_file_is_not_drift(client, auth_headers, config_file):
    """A deployment whose store is seeded needs no file. Reporting a removed
    ConfigMap as drift would turn the intended end state into a permanent
    warning."""
    config_file.unlink()
    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["file_present"] is False
    assert body["differs"] is False
    assert body["paths"] == []


@pytest.mark.asyncio
async def test_the_drift_route_needs_a_session(client):
    assert (await client.get("/api/config/drift")).status_code == 401


@pytest.mark.asyncio
async def test_the_served_config_no_longer_carries_overridden_paths(
    client, auth_headers
):
    """The provenance loses `overridden_paths` and the page shows no pill. The
    question a settings row answers is "what is this set to", and the answer is
    the field beside it."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert "overridden_paths" not in body
    assert body["restart_paths"] == []


# The keys `GET /api/config` adds that are not configuration, exactly as
# `frontend/src/api/overrides.ts` spells them. Written out here rather than
# imported from anywhere, because what is being proved is that the PAGE's rule
# for building a document is lossless -- a shared constant would prove only
# that this file and that file agree.
PROVENANCE_KEYS = frozenset(
    {
        "frozen_paths",
        "redacted_paths",
        "keep_sentinel",
        "field_descriptions",
        "computed_paths",
        "live_paths",
        "overrides_revision",
        "restart_paths",
        "secrets",
    }
)

_ABSENT = object()


def _read(document: dict, path: str) -> object:
    current: object = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _ABSENT
        current = current[part]
    return current


def _write(document: dict, path: str, value: object) -> None:
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def _document_from_config(served: dict) -> dict:
    """What `documentFromConfig` builds in the browser, in Python.

    The whole served configuration minus the provenance keys and `secrets`,
    with the keep sentinel at every redacted path the response actually
    carries.
    """
    document = {
        key: value for key, value in served.items() if key not in PROVENANCE_KEYS
    }
    for path in served["redacted_paths"]:
        if _read(document, path) is _ABSENT:
            continue
        _write(document, path, served["keep_sentinel"])
    return document


@pytest.mark.asyncio
async def test_the_page_round_trips_the_whole_store_without_truncating_it(
    client, auth_headers, config_file, session_factory
):
    """Seed, serve, save the served document back, and the store is unharmed.

    This is the proof the page cannot truncate the store. The pending document
    is no longer a delta rebuilt from a provenance list -- it is the whole
    configuration -- so a save that lost anything on the way out and back would
    quietly delete settings nobody touched, and would do it on the very first
    save after a deployment upgraded.

    Two claims, and the second is the one that matters at boot: every leaf the
    seed put in the store is still there with the same value (the redacted one
    included, which survives as the keep sentinel and is resolved back
    server-side), and the configuration a restart would build from the store is
    the configuration that was running before the save.
    """
    seeded = yaml.safe_load(config_file.read_text(encoding="utf-8"))

    served = (await client.get("/api/config", headers=auth_headers)).json()
    document = _document_from_config(served)
    # The page's own rule for the one redacted path, as a precondition: the
    # test would pass vacuously if the sentinel never got written.
    assert _read(document, "notifications.url") == served["keep_sentinel"]

    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": document, "expected_revision": served["overrides_revision"]},
    )
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        stored, meta = await load_store(session)
    assert meta["format"] == STORE_FORMAT
    lost = {
        path: _read(seeded, path)
        for path in document_paths(seeded)
        if _read(stored, path) != _read(seeded, path)
    }
    assert lost == {}

    async with session_factory() as session:
        rebuilt = await load_effective_config(config_file, session)
    assert rebuilt.model_dump(mode="json") == build_config(seeded).model_dump(mode="json")
