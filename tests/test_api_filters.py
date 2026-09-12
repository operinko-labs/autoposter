"""GET /api/items/filters."""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Render

from conftest import seed_media_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def client(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def token(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return response.json()["token"]


@pytest_asyncio.fixture
async def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


async def _item(session, rating_key, title, library="Movies", kind="movie"):
    return await seed_media_item(session, rating_key, library=library, kind=kind, title=title)


async def test_filters_requires_a_session(client):
    response = await client.get("/api/items/filters")
    assert response.status_code == 401


async def test_filters_returns_distinct_sorted_values(client, auth_headers, session):
    item1 = await _item(session, "rk1", "A", library="TV Shows", kind="show")
    item2 = await _item(session, "rk2", "B", library="Movies", kind="movie")
    item3 = await _item(session, "rk3", "C", library="Movies", kind="movie")
    ids = {"rk1": item1.id, "rk2": item2.id, "rk3": item3.id}
    session.add_all([
        Render(item_id=ids["rk1"], art_kind="poster", status="rendered", asset_path="/x/a.jpg"),
        Render(item_id=ids["rk2"], art_kind="poster", status="failed", asset_path="/x/b.jpg"),
        Render(item_id=ids["rk3"], art_kind="poster", status="rendered", asset_path="/x/c.jpg"),
    ])
    await session.commit()

    response = await client.get("/api/items/filters", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    # Duplicates (two "Movies"/"movie"/"rendered" rows above) must collapse
    # to one entry each, and the result must come back sorted.
    assert body == {
        "libraries": ["Movies", "TV Shows"],
        "kinds": ["movie", "show"],
        "statuses": ["failed", "rendered"],
    }


async def test_filters_route_is_not_shadowed_by_item_detail(client, auth_headers, session):
    # This is the ordering trap: /items/filters must be registered before
    # /items/{item_id}, or FastAPI parses "filters" as an item_id path
    # parameter and this request 422s instead of returning the payload.
    await _item(session, "rk1", "A")

    response = await client.get("/api/items/filters", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"libraries", "kinds", "statuses"}
