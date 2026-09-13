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
from autoposter.collections.playlist_presets import playlist_definitions
from autoposter.config.schema import PlaylistsConfig, Secrets
from autoposter.db.models import ConfigOverride, ManagedPlaylist, ManagedPlaylistUser

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
    assert body["preset_conflicts"] == []


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
        "summary": None,
        "limit": None,
        "schedule": None,
        "sync_mode": "sync",
        "builder_level": "item",
        "provenance": "file",
        "preset_key": None,
    }]
    assert body["preset_conflicts"] == []


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


async def test_the_listing_serves_summary_limit_and_schedule(
    client, app, auth_headers
):
    """L-7 from the 98a branch review: without these three the editor cannot
    show a definition, let alone round-trip one. Parity with the collections
    listing was the reason they were missing and it is not a good enough one.
    """
    _swap_playlists(app, definitions=[{
        **A_DEFINITION,
        "summary": "The films, in release order.",
        "limit": 40,
        "schedule": {"every_n_runs": 3},
    }])

    body = (
        await client.get("/api/playlists/definitions", headers=auth_headers)
    ).json()

    row = body["definitions"][0]
    assert row["summary"] == "The films, in release order."
    assert row["limit"] == 40
    # The FULL model, both fields, defaults included. There is no
    # exclude-anything precedent to copy: the collections listing
    # (`api/collections_builders.py::collections_definitions`) does not serve
    # `schedule` at all, and the one place this codebase serialises a nested
    # config model onto a response is `GET /api/config`, which does a plain
    # `config.model_dump(mode="json")` (`api/routes.py:1452`). So `months`
    # comes with it, and asserting the whole dict is what makes the served
    # shape a contract rather than a subset nobody pinned.
    assert row["schedule"] == {"every_n_runs": 3, "months": None}


async def test_a_switched_on_preset_is_listed_as_a_preset_row(
    client, app, auth_headers
):
    """A third provenance value, and the key beside it. The panel offers no
    Edit and no Remove on these: a preset is switched off by name in
    ``playlists.presets``, not by editing a definition that is not stored
    anywhere."""
    _swap_playlists(app, presets=["star_wars_timeline"])

    body = (
        await client.get("/api/playlists/definitions", headers=auth_headers)
    ).json()

    assert [(r["title"], r["provenance"], r["preset_key"]) for r in body["definitions"]] == [
        ("Star Wars (Timeline Order)", "preset", "star_wars_timeline")
    ]


async def test_a_shadowed_preset_is_served_as_a_conflict_not_as_a_row(
    client, app, auth_headers
):
    """A6's report, on the surface the panel reads. The operator's definition
    is the only row; the displaced preset is named separately so the panel can
    say which key stopped building."""
    _swap_playlists(
        app,
        presets=["star_wars_timeline"],
        definitions=[{
            "title": "Star Wars (Timeline Order)",
            "builder": "imdb_list",
            "params": {"list": "ls055350410"},
        }],
    )

    body = (
        await client.get("/api/playlists/definitions", headers=auth_headers)
    ).json()

    assert [r["provenance"] for r in body["definitions"]] == ["file"]
    assert body["preset_conflicts"] == [
        {"key": "star_wars_timeline", "title": "Star Wars (Timeline Order)"}
    ]


async def test_the_listing_is_the_same_composition_the_pass_runs(
    client, app, auth_headers
):
    """One composition, three readers — the constraint, pinned on the third.

    The pass and the delete sweep both enumerate
    ``playlist_presets.playlist_definitions``, and this listing is
    the third caller. Nothing stops it re-expanding the presets and appending
    the operator's own by hand, and that would agree with the function today
    and drift silently later — while the panel's ``overrideOrdinal`` counts on
    the two orders being identical to map a listing row back to its stored
    entry. So the listing goes through the function too, and this says so in a
    way a rewrite cannot pass by accident.
    """
    _swap_playlists(
        app,
        presets=["star_wars_timeline", "mcu_timeline"],
        definitions=[A_DEFINITION],
    )

    body = (
        await client.get("/api/playlists/definitions", headers=auth_headers)
    ).json()

    config = app.state.config_holder.current
    assert [row["title"] for row in body["definitions"]] == [
        definition.title for definition in playlist_definitions(config)
    ]
    # Not a tautology over an empty list: two presets ahead of one operator
    # definition, which is also the ordering `overrideOrdinal` depends on.
    assert [row["provenance"] for row in body["definitions"]] == [
        "preset", "preset", "file",
    ]


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
    from autoposter.redact import redact_urls

    assert redact_urls("failed to read https://x.example/l?apikey=SECRET now") == (
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


# --- the preview's per-user rows ---------------------------------------------


async def test_the_preview_serves_a_users_list_on_every_playlist(
    client, app, auth_headers
):
    """Present and empty rather than absent, so a reader never has to tell "no
    users" apart from "an older server". Every definition here names none."""
    from test_playlists import FakeSection, FakeServer, MOVIE_A

    app.state.plex_server_factory = lambda: FakeServer({
        "Movies": FakeSection([MOVIE_A]),
        "TV Shows": FakeSection([], section_type="show"),
    })
    _swap_playlists(app, definitions=[{
        **A_DEFINITION, "builder": "tmdb_movie", "params": {"ids": ["1"]},
    }])

    body = (
        await client.post("/api/playlists/preview", json={}, headers=auth_headers)
    ).json()

    assert body["playlists"][0]["users"] == []


async def test_a_definition_naming_a_user_without_an_account_token_says_so(
    client, app, auth_headers
):
    """Never silently inert. The deployment asked for a fan-out it holds no
    credential for, and the endpoint is where an operator finds out -- naming
    the environment variable, because "authentication failed" names nothing
    anybody can act on."""
    from test_playlists import FakeSection, FakeServer, MOVIE_A

    app.state.plex_server_factory = lambda: FakeServer({
        "Movies": FakeSection([MOVIE_A]),
        "TV Shows": FakeSection([], section_type="show"),
    })
    _swap_playlists(app, sync_to_users_apply=True, definitions=[{
        **A_DEFINITION, "builder": "tmdb_movie", "params": {"ids": ["1"]},
        "sync_to_users": ["alice"],
    }])

    body = (
        await client.post("/api/playlists/preview", json={}, headers=auth_headers)
    ).json()

    assert body["actions"][-1] == (
        "the per-user playlist sync is configured but no plex.tv account "
        "token is: set AUTOPOSTER_PLEX_ACCOUNT_TOKEN, which must be this "
        "server's OWNER's account token. Nothing was written to any user"
    )
    assert body["playlists"][0]["users"] == []


def test_the_served_row_carries_every_per_user_field():
    """The projection, pinned field for field rather than through a pass: the
    endpoint runs a real reconcile, and driving one that reaches seventeen
    fake accounts to check a dict's keys would test the pass again and the
    projection barely at all.

    The third row is a SWEPT copy, and it is here so ``deleting`` is asserted
    at a value something writes rather than only at its default. The sweep's
    user branch (``_user_swept``) is the one place that sets it -- 1 for a copy
    this pass deleted or would delete under the authorisation it has -- and
    ``tests/test_playlists.py`` pins the writing end through the real pass.
    Global Constraint 16 forbids a field added "ready for" a later slice;
    this one is written, served and asserted in this phase, at both ends."""
    from autoposter.api.playlists import _playlist_row
    from autoposter.collections.playlist_users import PlaylistUserResult
    from autoposter.collections.playlists import PlaylistResult

    row = _playlist_row(PlaylistResult(
        title="Timeline",
        users=[
            PlaylistUserResult(
                title="alice", user_id=7, creating=3, added=3,
            ),
            PlaylistUserResult(
                title="Kids", skipped=True,
                reason="PIN-protected; this service never holds a Plex PIN",
            ),
            PlaylistUserResult(
                title="bob", user_id=2, deleting=1, skipped=True,
            ),
        ],
    ))

    assert row["users"] == [
        {
            "title": "alice", "user_id": 7, "creating": 3, "adding": 0,
            "removing": 0, "deleting": 0, "added": 3, "removed": 0,
            "skipped": False, "failed": False, "reason": None,
        },
        {
            "title": "Kids", "user_id": None, "creating": 0, "adding": 0,
            "removing": 0, "deleting": 0, "added": 0, "removed": 0,
            "skipped": True, "failed": False,
            "reason": "PIN-protected; this service never holds a Plex PIN",
        },
        {
            "title": "bob", "user_id": 2, "creating": 0, "adding": 0,
            "removing": 0, "deleting": 1, "added": 0, "removed": 0,
            "skipped": True, "failed": False, "reason": None,
        },
    ]


def test_a_served_user_reason_is_redacted_like_every_other_string():
    """Every reason this service builds is a sentence from a closed set or an
    exception's class name, so nothing here can carry a URL today. It goes
    through ``redact_urls`` anyway -- twice, in fact:
    ``PlaylistUserResult.__post_init__`` runs it when the result is built and
    ``_playlist_row`` runs it again on the way out. The helper is idempotent
    and the rule on this surface is that nothing reaches a response unfiltered;
    a rule with an exemption is a rule somebody will widen.

    ``URL_PATTERN`` is ``https?://\\S+``, so the whole URL -- query string,
    token and all -- becomes ``<url>`` in one substitution."""
    from autoposter.api.playlists import _playlist_row
    from autoposter.collections.playlist_users import PlaylistUserResult
    from autoposter.collections.playlists import PlaylistResult

    row = _playlist_row(PlaylistResult(
        title="Timeline",
        users=[PlaylistUserResult(
            title="alice", failed=True,
            reason="broke on https://plex.example/x?X-Plex-Token=SECRET",
        )],
    ))

    assert row["users"][0]["reason"] == "broke on <url>"
    assert "SECRET" not in row["users"][0]["reason"]


# --- the delete leaves the copies, and says so -------------------------------


async def test_the_delete_leaves_the_user_copies_and_says_so(
    client, app, auth_headers, session_factory
):
    """There are exactly three removal events and this endpoint is not one of
    them: a user copy is removed by the sweep, through one gate and one cap.
    Deleting them here would mean plex.tv plus one blocking session per user
    inside an HTTP handler with no cap, and RETIRING the rows without deleting
    the objects would strand the only handle this service holds on a live
    playlist in somebody else's account. So the rows stay, and the response
    says how many and what removes them."""
    from test_playlists import FakeServer

    async with session_factory() as session:
        session.add(ManagedPlaylist(
            title="Marvel Cinematic Universe", plex_rating_key="8001",
            definition_hash="abc", libraries=["Movies"],
        ))
        session.add_all([
            ManagedPlaylistUser(
                definition_key="Marvel Cinematic Universe", plex_user_id=1,
                plex_user_title="alice", plex_rating_key="9001",
                definition_hash="abc",
            ),
            ManagedPlaylistUser(
                definition_key="Marvel Cinematic Universe", plex_user_id=2,
                plex_user_title="bob", plex_rating_key="9002",
                definition_hash="abc",
            ),
        ])
        await session.commit()

    from test_playlists import FakePlaylist

    app.state.plex_server_factory = lambda: FakeServer(
        {}, playlists=[FakePlaylist(8001, "Marvel Cinematic Universe")]
    )

    body = (await client.post(
        "/api/playlists/ops/delete",
        json={"title": "Marvel Cinematic Universe", "confirm": True},
        headers=auth_headers,
    )).json()

    assert body["user_copies"] == 2
    assert body["actions"][-1] == (
        "2 user copies of it remain and are not deleted here; the next pass "
        "reports them, and deletes them when playlists.delete_unconfigured "
        "and playlists.sync_to_users_apply are both on"
    )
    async with session_factory() as session:
        rows = (
            await session.execute(select(ManagedPlaylistUser))
        ).scalars().all()
    assert len(rows) == 2


async def test_the_delete_says_nothing_about_copies_when_there_are_none(
    client, app, auth_headers, session_factory
):
    """The line is worth reading only when there is something to read about."""
    from test_playlists import FakePlaylist, FakeServer

    async with session_factory() as session:
        session.add(ManagedPlaylist(
            title="Marvel Cinematic Universe", plex_rating_key="8001",
            definition_hash="abc", libraries=["Movies"],
        ))
        await session.commit()

    app.state.plex_server_factory = lambda: FakeServer(
        {}, playlists=[FakePlaylist(8001, "Marvel Cinematic Universe")]
    )

    body = (await client.post(
        "/api/playlists/ops/delete",
        json={"title": "Marvel Cinematic Universe", "confirm": True},
        headers=auth_headers,
    )).json()

    assert body["user_copies"] == 0
    assert body["actions"] == ["deleted the playlist 'Marvel Cinematic Universe'"]
