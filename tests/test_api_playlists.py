"""The four playlist routes.

Every test goes through ``create_app(...)`` + ``ASGITransport`` with a real
Postgres session factory and the real ``app.state.config_holder`` swapped --
never a monkeypatched predicate -- because the standing lesson here is two
same-branch defects where the helper tests passed and the wired path differed.
Every verb gets its own 401.
"""
import pathlib

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.overrides import OVERRIDES_ROW_ID
from autoposter.config.schema import PlaylistsConfig, Secrets
from autoposter.db.models import ConfigOverride, ManagedPlaylist

EXAMPLE = pathlib.Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

# ``list``, not ``list_id`` -- ``ImdbListParams`` is ``extra="forbid"`` with one
# required field named ``list``, and ``PlaylistDefinition`` validates ``params``
# through it. See the same note in tests/test_playlist_config.py.
A_DEFINITION = {
    "title": "Marvel Cinematic Universe",
    "builder": "imdb_list",
    "params": {"list": "ls539646485"},
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


def _swap_playlists(app, **section) -> None:
    config = app.state.config_holder.current
    app.state.config_holder.swap(config.model_copy(
        update={"playlists": PlaylistsConfig.model_validate(section)}
    ))


# --- every verb is behind a session ------------------------------------------


async def test_the_listing_requires_a_session(client):
    assert (await client.get("/api/playlists")).status_code == 401


async def test_the_definitions_listing_requires_a_session(client):
    assert (await client.get("/api/playlists/definitions")).status_code == 401


async def test_the_preview_requires_a_session(client):
    assert (await client.post("/api/playlists/preview", json={})).status_code == 401


async def test_the_delete_requires_a_session(client):
    response = await client.post(
        "/api/playlists/ops/delete", json={"title": "x", "confirm": True}
    )
    assert response.status_code == 401


# --- GET /api/playlists ------------------------------------------------------


async def test_the_listing_serves_the_managed_rows_nulls_included(
    client, auth_headers, session_factory
):
    """Straight through, NULLs included: NULL means no pass has stamped this
    row, and a zero delta means a pass that ran and found nothing to change.
    Rendering either as the other would be a false claim."""
    async with session_factory() as session:
        session.add(ManagedPlaylist(
            title="Marvel Cinematic Universe", plex_rating_key="8001",
            definition_hash="abc", libraries=["Movies"],
        ))
        await session.commit()

    body = (await client.get("/api/playlists", headers=auth_headers)).json()

    assert body["playlists"] == [{
        "id": body["playlists"][0]["id"],
        "title": "Marvel Cinematic Universe",
        "plex_rating_key": "8001",
        "libraries": ["Movies"],
        "member_count": None,
        "last_added": None,
        "last_removed": None,
        "last_reconciled_at": None,
    }]


# --- GET /api/playlists/definitions ------------------------------------------


async def test_the_definitions_listing_answers_without_plex(
    client, app, auth_headers
):
    """Config-only, the collections catalog's replica argument:
    ``plex_server_factory`` is None here, the state every Plex-touching endpoint
    answers 503 from."""
    assert getattr(app.state, "plex_server_factory", None) is None

    body = (
        await client.get("/api/playlists/definitions", headers=auth_headers)
    ).json()

    assert body["libraries"] == ["Movies", "TV Shows"]
    assert body["definitions"] == []


async def test_the_definitions_listing_reports_file_provenance_by_default(
    client, app, auth_headers
):
    _swap_playlists(app, definitions=[A_DEFINITION])

    body = (
        await client.get("/api/playlists/definitions", headers=auth_headers)
    ).json()

    assert body["definitions"] == [{
        "title": "Marvel Cinematic Universe",
        "builder": "imdb_list",
        "params": {"list": "ls539646485"},
        "libraries": ["Movies"],
        "sync_mode": "sync",
        "builder_level": "item",
        "provenance": "file",
    }]


async def test_a_stored_playlists_definitions_override_reports_override(
    client, app, auth_headers, session_factory
):
    """The provenance field the 98b panel's writes will depend on: "override"
    rows are the panel's to rewrite, "file" rows must never be copied into the
    stored document."""
    _swap_playlists(app, definitions=[A_DEFINITION])
    async with session_factory() as session:
        session.add(ConfigOverride(
            id=OVERRIDES_ROW_ID, document={"playlists": {"definitions": [A_DEFINITION]}}
        ))
        await session.commit()

    body = (
        await client.get("/api/playlists/definitions", headers=auth_headers)
    ).json()

    assert body["definitions"][0]["provenance"] == "override"


# --- POST /api/playlists/preview ---------------------------------------------


async def test_the_preview_answers_503_without_a_plex_connection(
    client, auth_headers
):
    response = await client.post(
        "/api/playlists/preview", json={}, headers=auth_headers
    )

    assert response.status_code == 503


async def test_the_preview_is_a_dry_run_even_with_apply_to_plex_on(
    client, app, auth_headers, session_factory
):
    """``dry_run=True`` is FORCED, not inherited: the preview's whole job is to
    report what a pass would do, and an operator pressing preview must never
    discover that it wrote."""
    from test_playlists import FakeSection, FakeServer, MOVIE_A, MOVIE_B

    server = FakeServer({
        "Movies": FakeSection([MOVIE_A, MOVIE_B]),
        "TV Shows": FakeSection([], section_type="show"),
    })
    app.state.plex_server_factory = lambda: server
    # ``tmdb_movie``, not ``simple_ids``. ``builders/simple_ids.py`` is a MODULE
    # name; the four builders it registers are ``imdb_id``, ``tmdb_movie``,
    # ``tmdb_show`` and ``plex_rating_key``, and
    # ``_must_be_a_registered_builder`` refuses everything else by name. The id
    # values are bare numbers for a related reason: ``TmdbIdParams`` refuses
    # anything non-numeric, so ``"tmdb://1"`` would fail even under the right
    # builder. ``tmdb_movie`` also calls ``require_library_type(...,
    # ("Movie",))``, which A_DEFINITION's ``libraries: ["Movies"]`` and this
    # fake section's ``type == "movie"`` satisfy.
    _swap_playlists(app, apply_to_plex=True, definitions=[{
        **A_DEFINITION, "builder": "tmdb_movie", "params": {"ids": ["1"]},
    }])

    body = (
        await client.post("/api/playlists/preview", json={}, headers=auth_headers)
    ).json()

    assert server.created == []
    assert body["playlists"][0]["title"] == "Marvel Cinematic Universe"
    assert body["playlists"][0]["adding"] == 1
    async with session_factory() as session:
        rows = (await session.execute(select(ManagedPlaylist))).scalars().all()
    assert rows == []


async def test_a_preview_action_never_carries_a_url(client, app, auth_headers):
    """The same redaction the collections preview applies: a poster or provider
    step reports the source it could not fetch, and provider URLs carry
    credentials often enough that none of them is echoed into a response."""
    from autoposter.api.playlists import _redact

    assert _redact("failed to read https://x.example/l?apikey=SECRET now") == (
        "failed to read <url> now"
    )


# --- POST /api/playlists/ops/delete ------------------------------------------


async def test_the_delete_needs_confirm_true(client, auth_headers):
    response = await client.post(
        "/api/playlists/ops/delete",
        json={"title": "Marvel Cinematic Universe"},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert "needs confirm: true" in response.json()["detail"]


async def test_the_delete_refuses_a_playlist_with_no_row(
    client, app, auth_headers
):
    """The ownership predicate, at the endpoint. The one guard NOT shared with
    the sweep is ``delete_unconfigured``: that setting authorises the unattended
    sweep to decide for itself, and this is an operator deciding, so
    ``confirm: true`` stands in its place."""
    from test_playlists import FakePlaylist, FakeSection, FakeServer, MOVIE_A

    stranger = FakePlaylist(8100, "Marvel Cinematic Universe", [MOVIE_A])
    app.state.plex_server_factory = lambda: FakeServer(
        {"Movies": FakeSection([MOVIE_A]), "TV Shows": FakeSection([], "show")},
        playlists=[stranger],
    )

    response = await client.post(
        "/api/playlists/ops/delete",
        json={"title": "Marvel Cinematic Universe", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert "no managed_playlists row" in response.json()["detail"]
    assert stranger.deleted is False


async def test_the_delete_retires_a_row_whose_playlist_is_gone(
    client, app, auth_headers, session_factory
):
    """The row's rating key names nothing on the server, so there is nothing to
    delete -- and the row is RETIRED rather than answered 409.

    Without this the row is unreachable forever: the sweep deliberately skips
    it (a candidate must name a live playlist, so an operator is never invited
    to authorise a deletion that cannot happen), and refusing here too would
    leave ``GET /api/playlists`` listing a playlist that does not exist with no
    route to clean it up. Retiring is not a Plex write and cannot be one -- the
    object is already gone -- so it is exactly what "delete this playlist"
    means in this state.

    ``retired_orphan`` says which of the two things happened, because a 200 that
    meant either would tell the operator nothing."""
    from test_playlists import FakeSection, FakeServer, MOVIE_A

    app.state.plex_server_factory = lambda: FakeServer(
        {"Movies": FakeSection([MOVIE_A]), "TV Shows": FakeSection([], "show")},
    )
    async with session_factory() as session:
        session.add(ManagedPlaylist(
            title="Marvel Cinematic Universe", plex_rating_key="8199",
            definition_hash="x", libraries=["Movies"],
        ))
        await session.commit()

    response = await client.post(
        "/api/playlists/ops/delete",
        json={"title": "Marvel Cinematic Universe", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["retired_orphan"] is True
    async with session_factory() as session:
        assert (await session.execute(select(ManagedPlaylist))).scalars().all() == []


async def test_the_delete_retires_the_row_and_never_touches_a_same_title_stranger(
    client, app, auth_headers, session_factory
):
    """The discriminating half of the retire path. A row exists under the
    title, and a playlist with that very title is on the server -- but it is a
    different object: ours was deleted and somebody recreated the name. Title
    match is never ownership, so the stranger is not deleted, not adopted and
    not re-pointed to; only our dead row goes."""
    from test_playlists import FakePlaylist, FakeSection, FakeServer, MOVIE_A

    stranger = FakePlaylist(8200, "Marvel Cinematic Universe", [MOVIE_A])
    app.state.plex_server_factory = lambda: FakeServer(
        {"Movies": FakeSection([MOVIE_A]), "TV Shows": FakeSection([], "show")},
        playlists=[stranger],
    )
    async with session_factory() as session:
        session.add(ManagedPlaylist(
            title="Marvel Cinematic Universe", plex_rating_key="8199",
            definition_hash="x", libraries=["Movies"],
        ))
        await session.commit()

    response = await client.post(
        "/api/playlists/ops/delete",
        json={"title": "Marvel Cinematic Universe", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["retired_orphan"] is True
    assert stranger.deleted is False
    assert [i.ratingKey for i in stranger.items()] == ["11"]
    async with session_factory() as session:
        assert (await session.execute(select(ManagedPlaylist))).scalars().all() == []


async def test_the_delete_removes_ours_and_leaves_an_audit_row(
    client, app, auth_headers, session_factory
):
    from autoposter.db.models import EventLog
    from test_playlists import FakePlaylist, FakeSection, FakeServer, MOVIE_A

    ours = FakePlaylist(8300, "Marvel Cinematic Universe", [MOVIE_A])
    app.state.plex_server_factory = lambda: FakeServer(
        {"Movies": FakeSection([MOVIE_A]), "TV Shows": FakeSection([], "show")},
        playlists=[ours],
    )
    async with session_factory() as session:
        session.add(ManagedPlaylist(
            title="Marvel Cinematic Universe", plex_rating_key="8300",
            definition_hash="x", libraries=["Movies"],
        ))
        await session.commit()

    response = await client.post(
        "/api/playlists/ops/delete",
        json={"title": "Marvel Cinematic Universe", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["retired_orphan"] is False, (
        "a real delete and a retired orphan both answer 200; the flag is what "
        "tells the operator which happened"
    )
    assert ours.deleted is True
    async with session_factory() as session:
        assert (await session.execute(select(ManagedPlaylist))).scalars().all() == []
        event = (await session.execute(
            select(EventLog).where(EventLog.event_type == "playlist_deleted")
        )).scalar_one()
    assert event.payload["rating_key"] == "8300"


async def test_the_endpoints_answer_503_when_playlists_are_disabled(
    client, app, auth_headers
):
    """``playlists.enabled`` is what an operator switches off to stop this
    service touching playlists at all; the ops endpoint would otherwise keep
    writing, which is exactly the guarantee that switch is supposed to give.
    The two read-only listings are NOT behind it, for the collections catalog's
    reason: reading config changes nothing, and an operator about to switch the
    section back on would otherwise be shown an empty page."""
    _swap_playlists(app, enabled=False)

    assert (await client.get(
        "/api/playlists/definitions", headers=auth_headers
    )).status_code == 200
    assert (await client.get("/api/playlists", headers=auth_headers)).status_code == 200
    assert (await client.post(
        "/api/playlists/preview", json={}, headers=auth_headers
    )).status_code == 503
    assert (await client.post(
        "/api/playlists/ops/delete",
        json={"title": "x", "confirm": True}, headers=auth_headers,
    )).status_code == 503
