"""POST /api/actions/rebuild -- roadmap row 233, ruled 2026-09-08.

"Delete" means REBUILD: clear the selected render(s)' fingerprints and queue
their items for another pass. Nothing is unlinked from the asset tree and
nothing is asked of Plex, so every test here asserts over `renders`, `jobs`
and `events_log` and none of them touches a file.

Its own module rather than more of `test_api_action_center.py` (1262 lines
already) because one endpoint with two body forms, a cap, an idempotence
rule and a row-213 shape rule is a suite's worth of questions about one
construct. It is in DEEP_SUITES (`tests/conftest.py`) like every other
`test_api_*.py`: it builds the ASGI application against a real database.
"""
import json
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ActionDismissal, EventLog, Job, MediaItem, Render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
ADMIN_PASSWORD_HASH = hash_password(PASSWORD)

#: Every key the endpoint is allowed to serve, and no other. Row 213: counts
#: and Plex rating keys only -- no asset path, no host, no free text.
RESPONSE_KEYS = {
    "status", "matched", "selected", "cleared", "items", "enqueued", "rating_keys",
}


@pytest_asyncio.fixture
async def app(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x", fanart_apikey="x",
        webhook_secret="x", admin_password_hash=ADMIN_PASSWORD_HASH,
    )
    return create_app(load_config(EXAMPLE), session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _seed(session, *, rating_key, library="Movies", art_kind="poster", **render_fields):
    """One media item and one render row, the shape test_api_action_center.py
    seeds. Fingerprints default to non-null so "was it cleared?" is a real
    question rather than a tautology."""
    item = MediaItem(rating_key=rating_key, library=library, kind="movie", title=f"T{rating_key}")
    session.add(item)
    await session.flush()
    fields = {
        "status": "no_art",
        "asset_path": f"/assets/{rating_key}.jpg",
        "fingerprint": rating_key * 64,
        "badge_fingerprint": rating_key * 64,
    }
    fields.update(render_fields)
    render = Render(item_id=item.id, art_kind=art_kind, **fields)
    session.add(render)
    await session.commit()
    return item, render


async def _fingerprints_of(session, render_id):
    """This row's two fingerprints, read straight from the database.

    A COLUMN select rather than an entity select, and never
    ``session.expire_all()``. The endpoint wrote through its OWN session;
    this one already holds the row it seeded, and ``expire_on_commit=False``
    (tests/conftest.py) means a plain ``select(Render)`` hands back the stale
    in-memory copy. Expiring it instead would make the next attribute access
    a lazy refresh, which under asyncio raises ``MissingGreenlet``. A scalar
    the ORM has nowhere to cache avoids both.
    """
    return (
        await session.execute(
            select(Render.fingerprint, Render.badge_fingerprint).where(Render.id == render_id)
        )
    ).one()


# --- auth --------------------------------------------------------------------


async def test_the_rebuild_requires_a_session(client):
    response = await client.post("/api/actions/rebuild", json={"apply": True})
    assert response.status_code == 401


# --- form A: one row ---------------------------------------------------------


async def test_one_row_rebuild_clears_that_rows_fingerprints_and_queues_its_item(
    client, auth_headers, session
):
    """C1's whole substance. BOTH fingerprints, because the pipeline's
    short-circuit is the fingerprint and the badge compositor has its own --
    /items/{id}/renders/{kind}/clear-override clears both for the same
    reason."""
    pressed, pressed_render = await _seed(session, rating_key="1")
    _, untouched = await _seed(session, rating_key="2")
    pressed_id, untouched_id = pressed_render.id, untouched.id

    body = (
        await client.post(
            "/api/actions/rebuild",
            headers=auth_headers,
            json={"row": {"item_id": pressed.id, "art_kind": "poster"}, "apply": True},
        )
    ).json()

    assert body["cleared"] == 1
    assert body["enqueued"] == 1
    assert body["rating_keys"] == ["1"]
    assert await _fingerprints_of(session, pressed_id) == (None, None)
    # And no others: the blast radius is exactly the row pressed.
    assert await _fingerprints_of(session, untouched_id) == ("2" * 64, "2" * 64)
    jobs = (await session.execute(select(Job))).scalars().all()
    assert [job.kind for job in jobs] == ["process_item"]


async def test_a_second_rebuild_of_the_same_row_clears_nothing_and_still_reports(
    client, auth_headers, session
):
    """C2's idempotence. The state the operator asked for is the state the row
    is in, so this is a 200 with `cleared: 0` -- never a 409, which would
    teach them to press again."""
    item, render = await _seed(session, rating_key="1")
    render_id = render.id
    press = {"row": {"item_id": item.id, "art_kind": "poster"}, "apply": True}

    first = (await client.post("/api/actions/rebuild", headers=auth_headers, json=press)).json()
    second = (await client.post("/api/actions/rebuild", headers=auth_headers, json=press)).json()

    assert first["cleared"] == 1
    assert second["status"] == "enqueued"
    assert second["matched"] == 1
    assert second["selected"] == 1
    assert second["cleared"] == 0
    # The pending dedupe swallowed the second job, which is the honest number
    # rather than a claim of work that was not queued.
    assert second["enqueued"] == 0
    assert len((await session.execute(select(Job))).scalars().all()) == 1
    assert await _fingerprints_of(session, render_id) == (None, None)


async def test_a_rebuild_of_a_row_that_is_gone_matches_nothing_and_says_complete(
    client, auth_headers
):
    """Deliberately NOT /actions/rerender's 404. One endpoint serves both
    forms, the bulk form must answer 200 for an empty filter, and a page that
    re-reads after every action shows the row gone either way -- so a stale
    row press is `complete` with zeroes, not an error to style."""
    response = await client.post(
        "/api/actions/rebuild",
        headers=auth_headers,
        json={"row": {"item_id": 999999, "art_kind": "poster"}, "apply": True},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    assert response.json()["matched"] == 0


# --- form B: the filter ------------------------------------------------------


async def test_the_bulk_dry_run_counts_and_writes_nothing(client, auth_headers, session):
    """The unguarded offer: it answers what a rebuild WOULD do -- `cleared`
    and `items` counted honestly, the same split `bulk_rerender_action`'s
    dry run keeps between `items` (honest) and `enqueued` (a write count) --
    while it clears nothing, queues nothing and logs nothing, so it needs no
    arm. One row already has no fingerprint to clear, so `cleared` (1) is
    distinguishable from `selected` (2) rather than a tautology."""
    _, already_clear = await _seed(
        session, rating_key="1", fingerprint=None, badge_fingerprint=None
    )
    _, still_set = await _seed(session, rating_key="2")
    already_clear_id, still_set_id = already_clear.id, still_set.id

    body = (
        await client.post("/api/actions/rebuild", headers=auth_headers, json={"apply": False})
    ).json()

    assert body["status"] == "dry run"
    assert body["matched"] == 2
    assert body["selected"] == 2
    assert body["cleared"] == 1
    assert body["items"] == 2
    assert body["enqueued"] == 0
    assert await _fingerprints_of(session, already_clear_id) == (None, None)
    assert await _fingerprints_of(session, still_set_id) == ("2" * 64, "2" * 64)
    assert (await session.execute(select(Job))).scalars().all() == []
    assert (await session.execute(select(EventLog))).scalars().all() == []


async def test_the_bulk_apply_reports_the_whole_shape_and_records_one_event(
    client, auth_headers, session
):
    """The whole-dict assertion C5 asks for. A key added later has to be added
    here too, which is the point: this response is a contract the page and
    row 213 both read."""
    await _seed(session, rating_key="7")

    body = (
        await client.post("/api/actions/rebuild", headers=auth_headers, json={"apply": True})
    ).json()

    assert body == {
        "status": "enqueued",
        "matched": 1,
        "selected": 1,
        "cleared": 1,
        "items": 1,
        "enqueued": 1,
        "rating_keys": ["7"],
    }
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [event.event_type for event in events] == ["action_center_rebuild"]


async def test_the_bulk_rebuild_honours_the_configured_batch_size(
    app, client, auth_headers, session
):
    """One batch per press, not the whole match -- the same cap
    /actions/bulk/rerender and /actions/backfill take, for the same reason:
    every press is a real re-render, and the operator pressing again is the
    pacing."""
    for n in range(3):
        await _seed(session, rating_key=str(n + 1))

    edited = load_config(EXAMPLE)
    edited.scheduler.drift_batch_size = 1
    app.state.config_holder.swap(edited)

    body = (
        await client.post("/api/actions/rebuild", headers=auth_headers, json={"apply": True})
    ).json()

    assert body["matched"] == 3
    assert body["selected"] == 1
    assert body["cleared"] == 1
    # Counted in SQL: the filter is evaluated against the database, so the
    # test session's own stale copies of these rows cannot answer it.
    still_set = (
        await session.execute(
            select(func.count()).select_from(Render).where(Render.fingerprint.isnot(None))
        )
    ).scalar_one()
    assert still_set == 2


async def test_the_bulk_rebuild_collapses_two_flagged_kinds_of_one_item_into_one_job(
    client, auth_headers, session
):
    """The queue's unit is a render row; the work's unit is an item. Two
    flagged kinds of one item clear two fingerprints and queue ONE job."""
    item = MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune")
    session.add(item)
    await session.flush()
    session.add_all([
        Render(item_id=item.id, art_kind="poster", status="no_art",
               asset_path="/a.jpg", fingerprint="a" * 64),
        Render(item_id=item.id, art_kind="background", status="no_art",
               asset_path="/b.jpg", fingerprint="b" * 64),
    ])
    await session.commit()

    body = (
        await client.post("/api/actions/rebuild", headers=auth_headers, json={"apply": True})
    ).json()

    assert body["matched"] == 2
    assert body["selected"] == 2
    assert body["cleared"] == 2
    assert body["items"] == 1
    assert body["enqueued"] == 1
    assert len((await session.execute(select(Job))).scalars().all()) == 1


# --- row 213 -----------------------------------------------------------------


async def test_the_response_and_the_event_carry_no_path_and_no_free_text(
    client, auth_headers, session
):
    """C2 under row 213. The seeded asset_path is `/assets/1.jpg`; if any
    served string carried it -- or any other absolute path -- this fails. The
    events_log `outcome` IS served (`/api/events`), so it is checked too;
    `payload` is not (api/snapshots.py never selects it) but is counts-only
    regardless."""
    await _seed(session, rating_key="1")

    body = (
        await client.post("/api/actions/rebuild", headers=auth_headers, json={"apply": True})
    ).json()

    assert set(body) == RESPONSE_KEYS
    assert "/" not in json.dumps(body)
    event = (await session.execute(select(EventLog))).scalars().one()
    assert "/" not in event.outcome
    assert set(event.payload) == {"matched", "selected", "cleared", "items", "enqueued"}


# --- the dismissal ruling ----------------------------------------------------


async def test_a_rebuild_leaves_a_dismissal_standing(client, auth_headers, session):
    """Decided for row 233: a rebuild does NOT touch action_dismissals.

    `fingerprint` is not in `flags._EVIDENCE_COLUMNS`, so the hiding rule
    survives the clear by construction -- and that is right: the day the
    rebuild LANDS and moves any fact, `_dismissal_join()` stops matching and
    the row returns on its own. Deleting the dismissal here would be a second
    invalidation path competing with the self-expiring one, and it would
    overrule an operator who said "leave this one" before the new artwork
    exists.
    """
    item, render = await _seed(session, rating_key="1")
    render_id = render.id
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster"},
    )

    await client.post(
        "/api/actions/rebuild",
        headers=auth_headers,
        json={"row": {"item_id": item.id, "art_kind": "poster"}, "apply": True},
    )

    dismissals = (await session.execute(select(ActionDismissal))).scalars().all()
    assert len(dismissals) == 1
    assert await _fingerprints_of(session, render_id) == (None, None)


async def test_the_bulk_rebuild_skips_a_dismissed_row_unless_it_is_asked_for(
    client, auth_headers, session
):
    """The bulk form reuses `_scope`, so "leave this one" holds against a
    filter press exactly as it holds against the listing -- and ticking
    "Show dismissed" is how the operator overrules it."""
    item, render = await _seed(session, rating_key="1")
    render_id = render.id
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster"},
    )

    default = (
        await client.post("/api/actions/rebuild", headers=auth_headers, json={"apply": True})
    ).json()

    assert default["matched"] == 0
    assert default["status"] == "complete"
    assert await _fingerprints_of(session, render_id) == ("1" * 64, "1" * 64)

    asked = (
        await client.post(
            "/api/actions/rebuild",
            headers=auth_headers,
            json={"apply": True, "include_dismissed": True},
        )
    ).json()

    assert asked["cleared"] == 1
    assert await _fingerprints_of(session, render_id) == (None, None)


# --- task-1 review I2: four mutants that survived the tests above ------------


async def test_a_row_press_in_an_excluded_library_matches_nothing_and_clears_nothing(
    client, auth_headers, session
):
    """I2#1: the row branch's own `flags.excluded_library_predicate(config)`
    term. Nothing above seeds an excluded library and presses the row form,
    so the guarantee that a row in an excluded library is not stranded
    cleared -- nothing can re-render it once cleared -- was unpinned. The
    example config excludes `Photos`.
    """
    item, render = await _seed(session, rating_key="1", library="Photos")
    render_id = render.id

    body = (
        await client.post(
            "/api/actions/rebuild",
            headers=auth_headers,
            json={"row": {"item_id": item.id, "art_kind": "poster"}, "apply": True},
        )
    ).json()

    assert body["matched"] == 0
    assert body["status"] == "complete"
    assert await _fingerprints_of(session, render_id) == ("1" * 64, "1" * 64)


async def test_the_cleared_count_counts_a_row_whose_badge_fingerprint_alone_is_set(
    client, auth_headers, session
):
    """I2#2: `cleared` is `fingerprint is not None or badge_fingerprint is
    not None`. Every seed above sets both columns together, so the two halves
    of that `or` are indistinguishable. `backfill_trigger` clears only
    `fingerprint`, so a row with `fingerprint IS NULL` and `badge_fingerprint`
    still set is reachable in production, and a rebuild over it really does
    clear a badge fingerprint -- it must count.
    """
    item, render = await _seed(session, rating_key="1", fingerprint=None)
    render_id = render.id
    assert await _fingerprints_of(session, render_id) == (None, "1" * 64)

    body = (
        await client.post(
            "/api/actions/rebuild",
            headers=auth_headers,
            json={"row": {"item_id": item.id, "art_kind": "poster"}, "apply": True},
        )
    ).json()

    assert body["cleared"] == 1
    assert await _fingerprints_of(session, render_id) == (None, None)


async def test_a_bulk_rebuild_with_no_matches_writes_no_event_log_row(
    client, auth_headers, session
):
    """I2#3: the `if batch:` guard on the `EventLog` insert. The dry-run test
    above returns before this line is ever reached, and the "row that is
    gone" test asserts nothing about `events_log` -- so writing an audit row
    for a zero-row `apply: True` press would go unnoticed. Nothing is seeded
    here, so nothing matches; only the guard stands between this press and a
    zero-row `EventLog` entry.
    """
    body = (
        await client.post("/api/actions/rebuild", headers=auth_headers, json={"apply": True})
    ).json()

    assert body["matched"] == 0
    assert body["status"] == "complete"
    assert (await session.execute(select(EventLog))).scalars().all() == []


async def test_rating_keys_are_sorted_lexicographically(client, auth_headers, session):
    """I2#4: `sorted(...)` on `rating_keys`. Every assertion above is over a
    one-element list, which cannot tell a sort from a pass-through. Seeding
    "9" before "2" gives them ascending `Render.id`s, so an unsorted
    (insertion-order) return would read `["9", "2"]`; the endpoint's own
    `sorted()` must produce `["2", "9"]`.
    """
    await _seed(session, rating_key="9")
    await _seed(session, rating_key="2")

    body = (
        await client.post("/api/actions/rebuild", headers=auth_headers, json={"apply": True})
    ).json()

    assert body["rating_keys"] == ["2", "9"]
