"""GET /api/items, GET /api/items/{item_id} and GET /api/collections."""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ItemFacts, ManagedCollection, MediaItem, Render

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


def _item(rating_key, title, library="Movies", kind="movie"):
    return MediaItem(rating_key=rating_key, library=library, kind=kind, title=title)


# --- /api/items ---


async def test_items_requires_a_session(client):
    response = await client.get("/api/items")
    assert response.status_code == 401


async def test_items_pagination_returns_the_right_slice_and_total(client, auth_headers, session):
    session.add_all([_item(f"rk{i}", f"Movie {i}") for i in range(5)])
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers, params={"limit": 2, "offset": 1})
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


async def test_items_limit_is_capped_above_its_maximum(client, auth_headers, session):
    session.add_all([_item(f"rk{i}", f"Movie {i}") for i in range(250)])
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers, params={"limit": 10000})
    assert len(response.json()["items"]) == 200


async def test_items_filter_by_library(client, auth_headers, session):
    session.add_all([
        _item("rk1", "A", library="Movies"),
        _item("rk2", "B", library="TV Shows"),
    ])
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers, params={"library": "TV Shows"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "B"


async def test_items_filter_by_kind(client, auth_headers, session):
    session.add_all([
        _item("rk1", "A", kind="movie"),
        _item("rk2", "B", kind="show"),
    ])
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers, params={"kind": "show"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "B"


async def test_items_filter_by_render_status(client, auth_headers, session):
    session.add_all([_item("rk1", "A"), _item("rk2", "B")])
    await session.flush()
    items = (await session.execute(select(MediaItem))).scalars().all()
    ids = {row.rating_key: row.id for row in items}
    session.add_all([
        Render(item_id=ids["rk1"], art_kind="poster", status="rendered", asset_path="/x/a.jpg"),
        Render(item_id=ids["rk2"], art_kind="poster", status="failed", asset_path="/x/b.jpg"),
    ])
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers, params={"status": "failed"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "B"


async def test_items_filters_combine(client, auth_headers, session):
    session.add_all([
        _item("rk1", "A", library="Movies", kind="movie"),
        _item("rk2", "B", library="Movies", kind="show"),
        _item("rk3", "C", library="TV Shows", kind="movie"),
    ])
    await session.commit()

    response = await client.get(
        "/api/items", headers=auth_headers, params={"library": "Movies", "kind": "movie"}
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "A"


async def test_items_search_matches_a_substring_case_insensitively(client, auth_headers, session):
    session.add_all([_item("rk1", "The Matrix"), _item("rk2", "Inception")])
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers, params={"search": "matr"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "The Matrix"


async def test_items_search_treats_percent_as_a_literal_character(client, auth_headers, session):
    # "The 1000 Club" is the discriminating row: an unescaped "100%" becomes
    # ILIKE '%100%%', whose trailing wildcard matches it too. Without it the
    # test passes with or without the escaping.
    session.add_all([
        _item("rk1", "100% Wolf"), _item("rk2", "Inception"), _item("rk3", "The 1000 Club"),
    ])
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers, params={"search": "100%"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "100% Wolf"


async def test_items_search_treats_underscore_as_a_literal_character(client, auth_headers, session):
    session.add_all([_item("rk1", "Se7en_Special"), _item("rk2", "Inception")])
    await session.commit()

    # An underscore is a single-character wildcard in LIKE/ILIKE; "Se7enX"
    # must NOT match "Se7en_Special" if the escaping is genuine.
    response = await client.get("/api/items", headers=auth_headers, params={"search": "en_Sp"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Se7en_Special"

    response = await client.get("/api/items", headers=auth_headers, params={"search": "enXSp"})
    assert response.json()["total"] == 0


async def test_items_render_status_summary_is_included(client, auth_headers, session):
    session.add(_item("rk1", "A"))
    await session.flush()
    item_id = (await session.execute(select(MediaItem))).scalars().one().id
    session.add(Render(item_id=item_id, art_kind="poster", status="rendered", asset_path="/x/a.jpg"))
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers)
    row = response.json()["items"][0]
    assert row["render_status"] == {"poster": "rendered"}


async def test_items_render_status_keys_arrive_in_sorted_art_kind_order(client, auth_headers, session):
    # Insert in the opposite of sorted order ("poster" before "background")
    # so a query without an ORDER BY has no reason to return them sorted.
    session.add(_item("rk1", "A"))
    await session.flush()
    item_id = (await session.execute(select(MediaItem))).scalars().one().id
    session.add(Render(item_id=item_id, art_kind="poster", status="rendered", asset_path="/x/a.jpg"))
    session.add(Render(item_id=item_id, art_kind="background", status="rendered", asset_path="/x/b.jpg"))
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers)
    row = response.json()["items"][0]
    assert list(row["render_status"].keys()) == ["background", "poster"]


# --- /api/items/{item_id} ---


async def test_item_detail_requires_a_session(client, session):
    session.add(_item("rk1", "A"))
    await session.commit()
    response = await client.get("/api/items/1")
    assert response.status_code == 401


async def test_item_detail_includes_facts_and_renders(client, auth_headers, session):
    session.add(_item("rk1", "A"))
    await session.flush()
    item_id = (await session.execute(select(MediaItem))).scalars().one().id
    session.add(ItemFacts(item_id=item_id, critic_rating=8.5, studio="Studio X"))
    session.add(
        Render(
            item_id=item_id, art_kind="poster", status="rendered", asset_path="/x/a.jpg",
            fingerprint="abc123", badge_fingerprint="def456", upload_status="uploaded",
            adopted=True,
        )
    )
    await session.commit()

    response = await client.get(f"/api/items/{item_id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "A"
    assert body["facts"]["critic_rating"] == 8.5
    assert body["facts"]["studio"] == "Studio X"
    assert len(body["renders"]) == 1
    render = body["renders"][0]
    assert render["art_kind"] == "poster"
    assert render["status"] == "rendered"
    assert render["fingerprint"] == "abc123"
    assert render["badge_fingerprint"] == "def456"
    assert render["upload_status"] == "uploaded"
    assert render["adopted"] is True


async def test_item_detail_404s_for_an_unknown_id(client, auth_headers):
    response = await client.get("/api/items/999999", headers=auth_headers)
    assert response.status_code == 404


# --- /api/collections ---


async def test_collections_requires_a_session(client):
    response = await client.get("/api/collections")
    assert response.status_code == 401


async def test_collections_lists_what_is_managed(client, auth_headers, session):
    session.add(
        ManagedCollection(
            library="Movies", title="Best Picture Winners", kind="smart",
            definition_hash="somehash",
        )
    )
    await session.commit()

    response = await client.get("/api/collections", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["collections"]) == 1
    row = body["collections"][0]
    assert row["library"] == "Movies"
    assert row["title"] == "Best Picture Winners"
    assert row["kind"] == "smart"
    # No has_poster_hash: it reported bool(definition_hash), which is NOT NULL
    # with no default and so true for every row, and describes the hash of the
    # collection's filter and summary rather than any poster. A field that
    # always says yes is worse than an absent one.
    assert "has_poster_hash" not in row
