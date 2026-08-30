"""``GET /api/collections/definitions`` -- row 137's listing.

The listing answers without touching anything: no Plex, no engine run, no
database read beyond the one stored-overrides lookup that decides provenance.
Provenance is the load-bearing field: "file" rows belong to the mounted YAML
and the panel must never copy them into the overrides document (the freezing
hazard ``frontend/src/api/overrides.ts`` opens with); "override" rows are the
stored document's own and are the only ones the panel may rewrite. The value
is uniform per state by construction -- the overrides layer replaces a list
WHOLESALE (``config/overrides.py``) -- and both states are pinned here.
"""
import pathlib

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.overrides import OVERRIDES_ROW_ID
from autoposter.config.schema import CollectionDefinition, Secrets
from autoposter.db.models import ConfigOverride

EXAMPLE = pathlib.Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

# One definition, written the sparse way an operator (or the panel) would.
A_DEFINITION = {
    "title": "Star Wars",
    "builder": "tmdb_collection",
    "params": {"id": 10},
    "libraries": ["Movies"],
}


@pytest_asyncio.fixture
async def app(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    return create_app(load_config(EXAMPLE), session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": "Bearer %s" % response.json()["token"]}


def _swap_definitions(app, entries) -> None:
    """The same live swap the overrides API performs, narrowed to definitions."""
    config = app.state.config_holder.current
    app.state.config_holder.swap(config.model_copy(
        update={
            "collections": config.collections.model_copy(
                update={"definitions": [
                    CollectionDefinition.model_validate(entry) for entry in entries
                ]}
            )
        }
    ))


async def test_the_definitions_listing_requires_a_session(client):
    assert (await client.get("/api/collections/definitions")).status_code == 401


async def test_the_listing_answers_without_plex_and_serves_the_library_names(
    client, app, auth_headers
):
    """Config-only, the catalog's replica argument: ``plex_server_factory`` is
    None here, the state every Plex-touching endpoint answers 503 from. The
    served ``libraries`` are the create form's scope checkboxes -- names, not
    a type enum, because a definition's scope IS names."""
    assert getattr(app.state, "plex_server_factory", None) is None

    response = await client.get("/api/collections/definitions", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["libraries"] == ["Movies", "TV Shows"]
    assert body["definitions"] == []


async def test_the_listing_reports_file_provenance_when_no_override_is_stored(
    client, app, auth_headers
):
    """No ``collections.definitions`` key in the stored overrides document
    means the mounted file supplies the whole effective list -- rows the panel
    renders WITHOUT a remove control and never writes anywhere."""
    _swap_definitions(app, [A_DEFINITION])

    body = (
        await client.get("/api/collections/definitions", headers=auth_headers)
    ).json()

    assert body["definitions"] == [{
        "title": "Star Wars",
        "builder": "tmdb_collection",
        "params": {"id": 10},
        "libraries": ["Movies"],
        "sort": "custom",
        "sync_mode": "sync",
        "provenance": "file",
    }]


async def test_a_collections_override_without_the_definitions_key_is_still_file(
    client, app, auth_headers, session_factory
):
    """The discriminating branch: a stored ``collections`` section that does
    NOT carry ``definitions``.

    An operator who has ever saved any ``collections.*`` setting through the
    settings editor has one of these. Reading provenance as ``"collections" in
    stored`` would call every such deployment's file-defined rows "override" --
    which is not a cosmetic wrong label, it is the panel being told those rows
    are removable and rewritable, straight into the freezing hazard the field
    exists to prevent.
    """
    async with session_factory() as session:
        session.add(ConfigOverride(
            id=OVERRIDES_ROW_ID,
            document={"collections": {"apply_to_plex": True}},
        ))
        await session.commit()
    _swap_definitions(app, [A_DEFINITION])

    body = (
        await client.get("/api/collections/definitions", headers=auth_headers)
    ).json()

    assert [entry["provenance"] for entry in body["definitions"]] == ["file"]


async def test_the_listing_reports_override_provenance_when_the_document_carries_the_list(
    client, app, auth_headers, session_factory
):
    """With the key present in the stored document, every effective entry came
    through it (the wholesale replace), so every row is the panel's to
    rewrite -- the other half of the provenance split."""
    async with session_factory() as session:
        session.add(ConfigOverride(
            id=OVERRIDES_ROW_ID,
            document={"collections": {"definitions": [dict(A_DEFINITION)]}},
        ))
        await session.commit()
    _swap_definitions(app, [A_DEFINITION])

    body = (
        await client.get("/api/collections/definitions", headers=auth_headers)
    ).json()

    assert [entry["provenance"] for entry in body["definitions"]] == ["override"]
