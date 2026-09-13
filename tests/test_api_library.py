"""GET /api/items, GET /api/items/{item_id} and GET /api/collections."""
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ItemFacts, ManagedCollection, Render, RenderDelivery

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


async def _item(session, rating_key, title, library="Movies", kind="movie", **extra):
    return await seed_media_item(
        session, rating_key, library=library, kind=kind, title=title, **extra
    )


# --- /api/items ---


async def test_items_requires_a_session(client):
    response = await client.get("/api/items")
    assert response.status_code == 401


async def test_items_pagination_returns_the_right_slice_and_total(client, auth_headers, session):
    for i in range(5):
        await _item(session, f"rk{i}", f"Movie {i}")

    response = await client.get("/api/items", headers=auth_headers, params={"limit": 2, "offset": 1})
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


async def test_items_limit_is_capped_above_its_maximum(client, auth_headers, session):
    for i in range(250):
        await _item(session, f"rk{i}", f"Movie {i}")

    response = await client.get("/api/items", headers=auth_headers, params={"limit": 10000})
    assert len(response.json()["items"]) == 200


async def test_items_filter_by_library(client, auth_headers, session):
    await _item(session, "rk1", "A", library="Movies")
    await _item(session, "rk2", "B", library="TV Shows")

    response = await client.get("/api/items", headers=auth_headers, params={"library": "TV Shows"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "B"


async def test_items_filter_by_kind(client, auth_headers, session):
    await _item(session, "rk1", "A", kind="movie")
    await _item(session, "rk2", "B", kind="show")

    response = await client.get("/api/items", headers=auth_headers, params={"kind": "show"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "B"


async def test_items_filter_by_render_status(client, auth_headers, session):
    item1 = await _item(session, "rk1", "A")
    item2 = await _item(session, "rk2", "B")
    ids = {"rk1": item1.id, "rk2": item2.id}
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
    await _item(session, "rk1", "A", library="Movies", kind="movie")
    await _item(session, "rk2", "B", library="Movies", kind="show")
    await _item(session, "rk3", "C", library="TV Shows", kind="movie")

    response = await client.get(
        "/api/items", headers=auth_headers, params={"library": "Movies", "kind": "movie"}
    )
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "A"


async def test_items_search_matches_a_substring_case_insensitively(client, auth_headers, session):
    await _item(session, "rk1", "The Matrix")
    await _item(session, "rk2", "Inception")

    response = await client.get("/api/items", headers=auth_headers, params={"search": "matr"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "The Matrix"


async def test_items_search_treats_percent_as_a_literal_character(client, auth_headers, session):
    # "The 1000 Club" is the discriminating row: an unescaped "100%" becomes
    # ILIKE '%100%%', whose trailing wildcard matches it too. Without it the
    # test passes with or without the escaping.
    await _item(session, "rk1", "100% Wolf")
    await _item(session, "rk2", "Inception")
    await _item(session, "rk3", "The 1000 Club")

    response = await client.get("/api/items", headers=auth_headers, params={"search": "100%"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "100% Wolf"


async def test_items_search_treats_underscore_as_a_literal_character(client, auth_headers, session):
    await _item(session, "rk1", "Se7en_Special")
    await _item(session, "rk2", "Inception")

    # An underscore is a single-character wildcard in LIKE/ILIKE; "Se7enX"
    # must NOT match "Se7en_Special" if the escaping is genuine.
    response = await client.get("/api/items", headers=auth_headers, params={"search": "en_Sp"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Se7en_Special"

    response = await client.get("/api/items", headers=auth_headers, params={"search": "enXSp"})
    assert response.json()["total"] == 0


async def test_items_render_status_summary_is_included(client, auth_headers, session):
    item = await _item(session, "rk1", "A")
    session.add(Render(item_id=item.id, art_kind="poster", status="rendered", asset_path="/x/a.jpg"))
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers)
    row = response.json()["items"][0]
    assert row["render_status"] == {"poster": "rendered"}
    assert row["refs"] == {"plex": "rk1"}
    assert "rating_key" not in row


async def test_items_carry_their_own_refs_not_swapped(client, auth_headers, session):
    """``refs_by_item.get(item.id, {})`` has to key off the right item, not
    just answer with the page's first (or only) row's refs for every item."""
    await _item(session, "rk1", "A")
    await _item(session, "rk2", "B")

    response = await client.get("/api/items", headers=auth_headers)
    by_title = {row["title"]: row["refs"] for row in response.json()["items"]}
    assert by_title == {"A": {"plex": "rk1"}, "B": {"plex": "rk2"}}


async def test_items_render_status_keys_arrive_in_sorted_art_kind_order(client, auth_headers, session):
    # Insert in the opposite of sorted order ("poster" before "background")
    # so a query without an ORDER BY has no reason to return them sorted.
    item = await _item(session, "rk1", "A")
    session.add(Render(item_id=item.id, art_kind="poster", status="rendered", asset_path="/x/a.jpg"))
    session.add(Render(item_id=item.id, art_kind="background", status="rendered", asset_path="/x/b.jpg"))
    await session.commit()

    response = await client.get("/api/items", headers=auth_headers)
    row = response.json()["items"][0]
    assert list(row["render_status"].keys()) == ["background", "poster"]


# --- /api/items/{item_id} ---


async def test_item_detail_requires_a_session(client, session):
    await _item(session, "rk1", "A")
    response = await client.get("/api/items/1")
    assert response.status_code == 401


async def test_item_detail_includes_facts_and_renders(client, auth_headers, session):
    item = await _item(session, "rk1", "A")
    item_id = item.id
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
    assert body["refs"] == {"plex": "rk1"}
    assert "rating_key" not in body
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


async def test_item_detail_nests_each_render_own_deliveries(client, auth_headers, session):
    """One ``render_deliveries`` row per server the render is owed to, nested
    under that render -- not a top-level list, and not one query per render
    (see the item_detail handler's single ``RenderDelivery`` SELECT)."""
    item = await _item(session, "rk1", "A")
    item_id = item.id
    render = Render(
        item_id=item_id, art_kind="poster", status="rendered", asset_path="/x/a.jpg",
    )
    session.add(render)
    await session.flush()
    session.add_all([
        RenderDelivery(
            render_id=render.id, server="plex", status="uploaded",
            uploaded_at=datetime(2026, 8, 2, tzinfo=UTC),
        ),
        RenderDelivery(
            render_id=render.id, server="jellyfin", status="pending",
            detail="ConnectError: connection refused",
        ),
    ])
    await session.commit()

    response = await client.get(f"/api/items/{item_id}", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()["renders"][0]["deliveries"]
    # Ordered by server, so the chips cannot reorder between page loads.
    # The two rows above are inserted plex-first, so insertion order would
    # read the other way round.
    assert [d["server"] for d in payload] == ["jellyfin", "plex"]
    deliveries = {d["server"]: d for d in payload}
    assert len(deliveries) == 2
    assert deliveries["plex"]["status"] == "uploaded"
    assert deliveries["plex"]["uploaded_at"] is not None
    assert deliveries["jellyfin"]["status"] == "pending"
    assert deliveries["jellyfin"]["detail"] == "ConnectError: connection refused"


async def test_item_detail_echoes_the_render_provider(client, auth_headers, session):
    """``provider`` is the only trace a manual override leaves in the database
    (render/pipeline.py stamps ``provider="manual"``), so the detail page needs
    it to know whether there is an override to offer clearing."""
    item = await _item(session, "rk1", "A")
    item_id = item.id
    session.add_all([
        Render(
            item_id=item_id, art_kind="poster", status="rendered",
            asset_path="/x/a.jpg", provider="manual",
        ),
        Render(
            item_id=item_id, art_kind="background", status="rendered",
            asset_path="/x/b.jpg", provider="TMDB",
        ),
    ])
    await session.commit()

    response = await client.get(f"/api/items/{item_id}", headers=auth_headers)
    by_kind = {r["art_kind"]: r for r in response.json()["renders"]}
    assert by_kind["poster"]["provider"] == "manual"
    assert by_kind["background"]["provider"] == "TMDB"


async def test_item_detail_exposes_the_source_url_and_textlessness(
    client, auth_headers, session
):
    """Both are on the model and neither was served. The candidate picker needs
    them to mark which of a provider's images is the one currently in use --
    ``provider`` alone cannot, since a provider offers many."""
    item = await _item(session, "rk1", "A")
    item_id = item.id
    session.add(
        Render(
            item_id=item_id, art_kind="poster", status="rendered", asset_path="/x/a.jpg",
            provider="TMDB", source_url="https://image.tmdb.org/t/p/original/in-use.jpg",
            textless=True,
        )
    )
    await session.commit()

    response = await client.get(f"/api/items/{item_id}", headers=auth_headers)

    render = response.json()["renders"][0]
    assert render["source_url"] == "https://image.tmdb.org/t/p/original/in-use.jpg"
    assert render["textless"] is True


async def test_item_detail_404s_for_an_unknown_id(client, auth_headers):
    response = await client.get("/api/items/999999", headers=auth_headers)
    assert response.status_code == 404


async def test_item_detail_names_the_show_for_an_episode(client, auth_headers, session):
    """An episode's parent_id points at its SEASON row, not the show directly
    (see render/pipeline.py's _upsert_media_item and adopt/walk.py's
    _resolved_episode) -- the endpoint has to climb two levels to reach the
    show. Without the show's name the page has nothing to tell apart the
    dozens of "Episode 26"s a real library holds."""
    show = await _item(session, "rk-show", "Firefly", library="TV Shows", kind="show")
    season = await _item(
        session, "rk-season", "Season 1", library="TV Shows", kind="season",
        parent=show, season_number=1,
    )
    episode = await _item(
        session, "rk-episode", "Episode 26", library="TV Shows", kind="episode",
        parent=season, season_number=1, episode_number=26,
    )

    response = await client.get(f"/api/items/{episode.id}", headers=auth_headers)
    body = response.json()
    assert body["season_number"] == 1
    assert body["episode_number"] == 26
    assert body["parent"] == {"id": show.id, "title": "Firefly"}


async def test_item_detail_names_the_show_for_a_season(client, auth_headers, session):
    """A season's parent_id points directly at the show."""
    show = await _item(session, "rk-show", "Firefly", library="TV Shows", kind="show")
    season = await _item(
        session, "rk-season", "Season 1", library="TV Shows", kind="season",
        parent=show, season_number=1,
    )

    response = await client.get(f"/api/items/{season.id}", headers=auth_headers)
    body = response.json()
    assert body["parent"] == {"id": show.id, "title": "Firefly"}


async def test_item_detail_degrades_honestly_when_the_parent_is_unresolved(
    client, auth_headers, session
):
    """The upsert leaves parent_id NULL when the parent has not been processed
    yet (render/pipeline.py's _upsert_media_item). The endpoint must not
    invent a show name for that case -- it reports no parent, same as an item
    that has none."""
    episode = await _item(
        session, "rk-episode", "Episode 26", library="TV Shows", kind="episode",
        season_number=1, episode_number=26,
    )

    response = await client.get(f"/api/items/{episode.id}", headers=auth_headers)
    body = response.json()
    assert body["parent"] is None
    assert body["season_number"] == 1
    assert body["episode_number"] == 26


async def test_item_detail_movie_has_no_parent(client, auth_headers, session):
    item = await _item(session, "rk1", "A")

    response = await client.get(f"/api/items/{item.id}", headers=auth_headers)
    body = response.json()
    assert body["parent"] is None
    assert body["season_number"] is None
    assert body["episode_number"] is None


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


async def test_collections_echo_the_reconcile_stats(client, auth_headers, session):
    """The four columns the reconcile pass stamps, passed straight through.
    A zero delta is a real observation -- a pass that confirmed membership was
    already correct -- so it must arrive as 0, not be flattened into null."""
    session.add(
        ManagedCollection(
            library="Movies", title="IMDb Top 250", kind="manual",
            definition_hash="somehash",
            member_count=250, last_added=0, last_removed=0,
            last_reconciled_at=datetime(2026, 8, 23, 9, 30, tzinfo=UTC),
        )
    )
    await session.commit()

    response = await client.get("/api/collections", headers=auth_headers)
    row = response.json()["collections"][0]
    assert row["member_count"] == 250
    assert row["last_added"] == 0
    assert row["last_removed"] == 0
    assert row["last_reconciled_at"].startswith("2026-08-23T09:30")


async def test_a_collection_no_pass_has_stamped_reports_nulls(
    client, auth_headers, session
):
    """Every row predating the stats migration, and every smart row forever
    (Plex evaluates the filter live, so there is no member count to have),
    reads null. Never zero: "no members" and "never counted" are different
    claims and only one of them is true here."""
    session.add(
        ManagedCollection(
            library="Movies", title="Age 17+ Movies", kind="smart",
            definition_hash="somehash",
        )
    )
    await session.commit()

    response = await client.get("/api/collections", headers=auth_headers)
    row = response.json()["collections"][0]
    assert row["member_count"] is None
    assert row["last_added"] is None
    assert row["last_removed"] is None
    assert row["last_reconciled_at"] is None
