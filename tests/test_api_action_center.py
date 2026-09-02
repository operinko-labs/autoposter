"""GET/POST /api/actions -- the Action Center's queue, counts and actions.

Two of these tests are the phase's load-bearing ones and are worth naming:
`test_editing_the_provider_order_changes_the_queue_with_no_row_write` proves
the judgement is derived rather than stored, through the real endpoint and the
real config holder; and
`test_a_dismissed_row_returns_once_its_facts_change` proves 11b's dismissal
identity, which is the risk that row names for itself.
"""
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ActionDismissal, EventLog, Job, MediaItem, Render
from autoposter.intake.arr import RenderIntent

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
ADMIN_PASSWORD_HASH = hash_password(PASSWORD)


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
    item = MediaItem(rating_key=rating_key, library=library, kind="movie", title=f"T{rating_key}")
    session.add(item)
    await session.flush()
    fields = {"status": "rendered", "asset_path": f"/assets/{rating_key}.jpg"}
    fields.update(render_fields)
    render = Render(item_id=item.id, art_kind=art_kind, **fields)
    session.add(render)
    await session.commit()
    return item, render


# --- GET /api/actions --------------------------------------------------------


async def test_the_queue_requires_a_session(client):
    assert (await client.get("/api/actions")).status_code == 401


async def test_a_flagged_row_is_listed_with_its_flag_and_its_factual_detail(
    client, auth_headers, session
):
    item, _ = await _seed(session, rating_key="1", status="no_art", detail="no poster art anywhere")
    await _seed(session, rating_key="2", status="rendered")

    body = (await client.get("/api/actions", headers=auth_headers)).json()

    assert body["total"] == 1
    row = body["items"][0]
    assert row["item_id"] == item.id
    assert row["art_kind"] == "poster"
    assert row["library"] == "Movies"
    assert row["flags"] == ["missing"]
    # The factual sentence, from the registry -- the page never restates a
    # fact of its own.
    assert row["details"] == ["no poster art anywhere"]
    assert row["dismissed"] is False
    assert len(row["evidence"]) == 64


async def test_an_unknown_flag_is_a_400_naming_the_flags_this_build_has(client, auth_headers):
    """Not a silently unfiltered queue: a typo in a saved URL would otherwise
    answer with the whole library and look like the filter working."""
    response = await client.get("/api/actions?flag=lanugage_miss", headers=auth_headers)

    assert response.status_code == 400
    assert "language_miss" in response.json()["detail"]


async def test_the_flag_filter_narrows_the_queue(client, auth_headers, session):
    missing, _ = await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="rendered", upload_status="failed")

    body = (await client.get("/api/actions?flag=missing", headers=auth_headers)).json()

    assert body["total"] == 1
    assert body["items"][0]["item_id"] == missing.id


async def test_the_library_and_art_kind_filters_narrow_the_queue(client, auth_headers, session):
    await _seed(session, rating_key="1", library="Movies", art_kind="poster", status="no_art")
    wanted, _ = await _seed(
        session, rating_key="2", library="Shows", art_kind="background", status="no_art"
    )

    body = (
        await client.get(
            "/api/actions?library=Shows&art_kind=background", headers=auth_headers
        )
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["item_id"] == wanted.id


async def test_paging_reports_a_total_and_never_re_serves_a_row(client, auth_headers, session):
    """`updated_at` alone is not a total order -- a full pass stamps thousands
    of rows inside one transaction timestamp, and page 2 would hand back rows
    from page 1. The id tiebreak is what makes the pager honest."""
    for n in range(5):
        await _seed(session, rating_key=str(n), status="no_art")

    first = (await client.get("/api/actions?limit=2&offset=0", headers=auth_headers)).json()
    second = (await client.get("/api/actions?limit=2&offset=2", headers=auth_headers)).json()

    assert first["total"] == 5
    assert first["limit"] == 2
    assert second["offset"] == 2
    seen = {row["item_id"] for row in first["items"]}
    assert seen.isdisjoint({row["item_id"] for row in second["items"]})


async def test_the_limit_is_clamped_to_the_endpoints_ceiling(client, auth_headers, session):
    await _seed(session, rating_key="1", status="no_art")

    body = (await client.get("/api/actions?limit=9999", headers=auth_headers)).json()

    assert body["limit"] == 200


# --- GET /api/actions/summary ------------------------------------------------


async def test_the_summary_counts_every_flag_and_totals_the_default_population(
    client, auth_headers, session
):
    await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="rendered", upload_status="failed")
    # Adopted is a flag with a count, and is NOT in the default total: on a
    # wholesale-adopted library it would be every row (A5).
    await _seed(session, rating_key="3", status="rendered", adopted=True)

    body = (await client.get("/api/actions/summary", headers=auth_headers)).json()
    counts = {entry["code"]: entry["count"] for entry in body["flags"]}

    assert counts["missing"] == 1
    assert counts["upload_failed"] == 1
    assert counts["unknown_provenance"] == 1
    assert body["total"] == 2
    labels = {entry["code"]: entry["label"] for entry in body["flags"]}
    assert labels["missing"]
    assert next(e for e in body["flags"] if e["code"] == "unknown_provenance")["default_on"] is False


# --- the derived-not-stored property, through the real endpoint --------------


async def test_editing_the_provider_order_changes_the_queue_with_no_row_write(
    app, client, auth_headers, session
):
    """The whole design in one test. The config holder is swapped exactly as a
    real config save swaps it, and the queue answers differently on the next
    request -- with the row's own columns untouched."""
    item, render = await _seed(session, rating_key="1", provider="TVDB", provider_rank=1)

    before = (
        await client.get("/api/actions?flag=provider_downgrade", headers=auth_headers)
    ).json()
    assert before["total"] == 1

    edited = load_config(EXAMPLE)
    edited.providers.order = ["TVDB", "TMDB", "Fanart"]
    app.state.config_holder.swap(edited)

    after = (
        await client.get("/api/actions?flag=provider_downgrade", headers=auth_headers)
    ).json()
    assert after["total"] == 0

    render_id = render.id  # read before expire_all -- see test_api_actions.py's precedent
    session.expire_all()
    reread = (await session.execute(select(Render).where(Render.id == render_id))).scalar_one()
    assert reread.provider == "TVDB"
    assert reread.provider_rank == 1


# --- dismissal ---------------------------------------------------------------


async def test_dismissing_a_row_takes_it_out_of_the_queue_and_the_counts(
    client, auth_headers, session
):
    item, _ = await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "missing"},
    )

    assert response.status_code == 200
    assert response.json()["dismissed"] is True
    assert len(response.json()["evidence"]) == 64
    assert (await client.get("/api/actions", headers=auth_headers)).json()["total"] == 0
    summary = (await client.get("/api/actions/summary", headers=auth_headers)).json()
    assert {e["code"]: e["count"] for e in summary["flags"]}["missing"] == 0


async def test_a_dismissed_row_returns_once_its_facts_change(client, auth_headers, session):
    """11b's own risk, answered by the mechanism rather than by a sweep: the
    dismissal holds an evidence hash, the queue recomputes that hash in SQL on
    every read, and a fact moving makes the join stop matching."""
    from datetime import datetime, timezone

    item, render = await _seed(session, rating_key="1", status="no_art")
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "missing"},
    )
    assert (await client.get("/api/actions", headers=auth_headers)).json()["total"] == 0

    # What a re-render that found art would do to this row -- including the
    # quality stamp, so this row does not also read as `unscored` and the
    # assertion below stays isolated to the one fact that actually moved.
    render.status = "rendered"
    render.upload_status = "failed"
    render.quality_scored_at = datetime.now(timezone.utc)
    await session.commit()

    body = (await client.get("/api/actions", headers=auth_headers)).json()
    assert body["total"] == 1
    assert body["items"][0]["flags"] == ["upload_failed"]
    assert body["items"][0]["dismissed"] is False


async def test_a_dismissed_row_returns_once_it_is_scored(client, auth_headers, session):
    """`unscored` rests on `quality_scored_at IS NULL`; the evidence hash must
    track scored-ness rather than the raw timestamp, or a re-render that
    finally scores this row could never resurrect a dismissal made while it
    was unscored."""
    from datetime import datetime, timezone

    item, render = await _seed(
        session, rating_key="1", status="rendered", upload_status="failed", quality_scored_at=None
    )
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "unscored"},
    )
    assert (await client.get("/api/actions", headers=auth_headers)).json()["total"] == 0

    # What the render that finally scores this row would do to it.
    render.quality_scored_at = datetime.now(timezone.utc)
    await session.commit()

    body = (await client.get("/api/actions", headers=auth_headers)).json()
    assert body["total"] == 1
    assert body["items"][0]["flags"] == ["upload_failed"]
    assert body["items"][0]["dismissed"] is False


async def test_include_dismissed_shows_the_row_and_marks_it(client, auth_headers, session):
    item, _ = await _seed(session, rating_key="1", status="no_art")
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "missing"},
    )

    body = (
        await client.get("/api/actions?include_dismissed=true", headers=auth_headers)
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["dismissed"] is True


async def test_undismissing_brings_the_row_back(client, auth_headers, session):
    item, _ = await _seed(session, rating_key="1", status="no_art")
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "missing"},
    )

    response = await client.post(
        "/api/actions/undismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster"},
    )

    assert response.status_code == 200
    assert response.json()["dismissed"] is False
    assert (await client.get("/api/actions", headers=auth_headers)).json()["total"] == 1
    assert (await session.execute(select(ActionDismissal))).scalars().all() == []


async def test_dismissing_twice_updates_the_evidence_rather_than_erroring(
    client, auth_headers, session
):
    """UNIQUE(item_id, art_kind) makes the second press a conflict; an
    operator pressing Dismiss on a row whose facts have moved must re-dismiss
    it, not see a 500."""
    item, render = await _seed(session, rating_key="1", status="no_art")
    payload = {"item_id": item.id, "art_kind": "poster", "flag": "missing"}
    first = (await client.post("/api/actions/dismiss", headers=auth_headers, json=payload)).json()

    render.status = "skipped"
    await session.commit()
    second = (await client.post("/api/actions/dismiss", headers=auth_headers, json=payload)).json()

    assert second["evidence"] != first["evidence"]
    rows = (await session.execute(select(ActionDismissal))).scalars().all()
    assert len(rows) == 1


async def test_dismissing_with_an_unknown_flag_is_a_422(client, auth_headers, session):
    """The registry check on the body mirrors the GET's 400 in content -- it
    names the codes this build has -- but answers 422, the pydantic-validated
    body's own refusal style, rather than the query-param endpoint's 400."""
    item, _ = await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "lanugage_miss"},
    )

    assert response.status_code == 422
    assert "language_miss" in response.text


async def test_dismissing_with_an_overlong_flag_is_a_422_not_a_500(client, auth_headers, session):
    """`flag` lands in a `String(32)` column; an unbounded field would let a
    33-character value reach the database and come back a 500 instead of a
    validation error."""
    item, _ = await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "x" * 33},
    )

    assert response.status_code == 422


async def test_dismissing_a_render_that_does_not_exist_is_a_404(client, auth_headers, session):
    item, _ = await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "title_card"},
    )

    assert response.status_code == 404


# --- the actions, which are enqueues -----------------------------------------


async def test_a_re_search_queues_one_process_item_job(client, auth_headers, session):
    item, _ = await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": item.id}
    )

    assert response.status_code == 200
    assert response.json()["queued"] is True
    jobs = (await session.execute(select(Job))).scalars().all()
    assert [job.kind for job in jobs] == ["process_item"]


async def test_asking_twice_while_the_first_is_pending_queues_nothing_the_second_time(
    client, auth_headers, session
):
    """`uq_jobs_pending_dedupe` is what makes a bulk press safe, so it is
    pinned here rather than assumed. A second queue is an answer, not an
    error: 200 with `queued: false`, exactly as /items/{id}/reprocess."""
    item, _ = await _seed(session, rating_key="1", status="no_art")

    first = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": item.id}
    )
    second = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": item.id}
    )

    assert first.json()["queued"] is True
    assert second.status_code == 200
    assert second.json() == {"queued": False, "job_id": None}
    assert len((await session.execute(select(Job))).scalars().all()) == 1


async def test_a_re_search_for_an_unknown_item_is_a_404(client, auth_headers):
    response = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": 999999}
    )
    assert response.status_code == 404


async def test_the_bulk_dry_run_counts_and_writes_nothing(client, auth_headers, session):
    await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="no_art")

    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": False}
        )
    ).json()

    assert body["status"] == "dry run"
    assert body["matched"] == 2
    assert body["items"] == 2
    assert body["enqueued"] == 0
    assert (await session.execute(select(Job))).scalars().all() == []
    assert (await session.execute(select(EventLog))).scalars().all() == []


async def test_the_bulk_apply_enqueues_and_records_one_event(client, auth_headers, session):
    await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="no_art")

    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": True}
        )
    ).json()

    assert body["status"] == "enqueued"
    assert body["enqueued"] == 2
    assert len((await session.execute(select(Job))).scalars().all()) == 2
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [event.event_type for event in events] == ["action_center_bulk_rerender"]


async def test_the_bulk_apply_collapses_two_flagged_kinds_of_one_item_into_one_job(
    client, auth_headers, session
):
    """11b's stated risk is a burst. The unit of the queue is a render row and
    the unit of the queue's work is an ITEM, so 400 flagged rows across 200
    items are 200 jobs, not 400 -- and the pending dedupe collapses the rest."""
    item = MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune")
    session.add(item)
    await session.flush()
    session.add_all([
        Render(item_id=item.id, art_kind="poster", status="no_art", asset_path="/a.jpg"),
        Render(item_id=item.id, art_kind="background", status="no_art", asset_path="/b.jpg"),
    ])
    await session.commit()

    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": True}
        )
    ).json()

    assert body["matched"] == 2
    assert body["items"] == 1
    assert body["enqueued"] == 1
    assert len((await session.execute(select(Job))).scalars().all()) == 1


async def test_the_bulk_apply_honours_the_flag_filter(client, auth_headers, session):
    wanted, _ = await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="rendered", upload_status="failed")

    body = (
        await client.post(
            "/api/actions/bulk/rerender",
            headers=auth_headers,
            json={"apply": True, "flag": "missing"},
        )
    ).json()

    assert body["matched"] == 1
    assert body["enqueued"] == 1


async def test_the_bulk_apply_reports_fewer_enqueued_than_items_when_one_is_already_pending(
    client, auth_headers, session
):
    """The operator's SECOND press: a re-search already queued one of the two
    flagged items, so its pending job dedupes the bulk press's attempt on that
    item. `enqueued` counts only jobs actually created, per the docstring's own
    contract (`action_center.py:405-407`) -- it must read `1`, not `2`, or the
    response would claim work it did not queue."""
    first, _ = await _seed(session, rating_key="1", status="no_art")
    second, _ = await _seed(session, rating_key="2", status="no_art")

    pending = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": first.id}
    )
    assert pending.json()["queued"] is True

    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": True}
        )
    ).json()

    assert body["matched"] == 2
    assert body["items"] == 2
    assert body["enqueued"] == 1
    jobs = (await session.execute(select(Job))).scalars().all()
    # The one job the single-item press already queued, plus exactly one more.
    assert len(jobs) == 2


async def test_the_bulk_apply_refuses_an_unknown_flag(client, auth_headers, session):
    """The bulk endpoint calls the same `_flag_predicate` as the GET, so an
    unknown code is refused rather than falling through to the default
    population -- worse here than on the GET, because `apply: true` writes
    jobs."""
    await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/bulk/rerender",
        headers=auth_headers,
        json={"apply": True, "flag": "lanugage_miss"},
    )

    assert response.status_code == 400
    assert "language_miss" in response.json()["detail"]
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_the_bulk_apply_over_an_empty_match_reports_complete(client, auth_headers):
    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": True}
        )
    ).json()

    assert body["status"] == "complete"
    assert body["enqueued"] == 0


# --- the quality backfill ----------------------------------------------------
#
# 11a's "backfill job scoring the existing library". It cannot invent a
# language: no column holds one on a pre-existing row, so the achieved rank of
# an already-rendered asset is genuinely unrecoverable without re-selecting.
# So the backfill re-selects, in operator-paced batches -- which is both the
# backfill and a demonstration of the bulk path.


async def test_the_backfill_status_reports_complete_on_an_empty_library(client, auth_headers):
    """Completion is derived first, and by the POST's own rule -- `done >=
    total` <=> nothing unscored is left <=> the trigger selects 0. Testing
    "has it started" first would let an empty library read `not_started`
    forever here while every POST answered `complete`: two endpoints
    disagreeing about the one state a disabled button keys off."""
    body = (await client.get("/api/actions/backfill", headers=auth_headers)).json()

    assert body == {
        "status": "complete", "done": 0, "total": 0,
        "queued_for_scoring": 0, "unscored_total": 0,
    }


async def test_the_backfill_status_counts_only_rows_that_can_be_scored(
    client, auth_headers, session
):
    """A no_art row never reaches the write-back that stamps
    quality_scored_at, so counting it in the population would make a backfill
    that can never finish."""
    from datetime import datetime, timezone

    await _seed(session, rating_key="1", status="rendered", quality_scored_at=None)
    await _seed(
        session, rating_key="2", status="rendered",
        quality_scored_at=datetime.now(timezone.utc),
    )
    await _seed(session, rating_key="3", status="no_art")

    body = (await client.get("/api/actions/backfill", headers=auth_headers)).json()

    assert body == {
        "status": "in_progress", "done": 1, "total": 2,
        "queued_for_scoring": 0, "unscored_total": 1,
    }


async def test_the_backfill_status_reports_how_many_unscored_assets_are_already_queued(
    client, auth_headers, session
):
    """`queued_for_scoring` counts, in ASSET units, how many of the still-
    unscored rendered rows already have an in-flight `process_item` job for
    their item -- `pending`, `running` or `deferred` alike, because a
    deferred job is still claimed for scoring, merely waiting on Plex
    visibility (queue/jobs.py's own reading of `deferred`). A third unscored
    row with no in-flight job at all must not be counted."""
    item0, _ = await _seed(session, rating_key="0", status="rendered", quality_scored_at=None)
    item1, _ = await _seed(session, rating_key="1", status="rendered", quality_scored_at=None)
    await _seed(session, rating_key="2", status="rendered", quality_scored_at=None)

    intent0 = RenderIntent(kind=item0.kind, title=item0.title)
    intent1 = RenderIntent(kind=item1.kind, title=item1.title)
    session.add_all([
        Job(kind="process_item", dedupe_key=intent0.dedupe_key, state="pending"),
        Job(kind="process_item", dedupe_key=intent1.dedupe_key, state="running"),
    ])
    await session.commit()

    body = (await client.get("/api/actions/backfill", headers=auth_headers)).json()

    assert body["queued_for_scoring"] == 2
    assert body["unscored_total"] == 3


async def test_a_deferred_jobs_asset_counts_as_queued_for_scoring(client, auth_headers, session):
    """`deferred` is a wait, not an absence -- the same reading
    `_select_backfill_batch` already gives it when deciding what NOT to
    re-select."""
    item, _ = await _seed(session, rating_key="0", status="rendered", quality_scored_at=None)
    intent = RenderIntent(kind=item.kind, title=item.title)
    session.add(Job(kind="process_item", dedupe_key=intent.dedupe_key, state="deferred"))
    await session.commit()

    body = (await client.get("/api/actions/backfill", headers=auth_headers)).json()

    assert body["queued_for_scoring"] == 1


async def test_a_done_or_dismissed_job_does_not_count_as_queued_for_scoring(
    client, auth_headers, session
):
    """A `done` job is not in flight any more -- if its render is still
    unscored, that render genuinely has nothing queued for it."""
    item, _ = await _seed(session, rating_key="0", status="rendered", quality_scored_at=None)
    intent = RenderIntent(kind=item.kind, title=item.title)
    session.add_all([
        Job(kind="process_item", dedupe_key=intent.dedupe_key, state="done"),
        Job(kind="process_item", dedupe_key=intent.dedupe_key, state="dismissed"),
    ])
    await session.commit()

    body = (await client.get("/api/actions/backfill", headers=auth_headers)).json()

    assert body["queued_for_scoring"] == 0


async def test_the_backfill_clears_the_fingerprint_of_every_row_it_enqueues(
    client, auth_headers, session
):
    """The crux, and the reason a plain enqueue is not enough. The pipeline's
    unchanged-fingerprint short-circuit returns ABOVE the write-back, so a
    re-processed row whose inputs have not changed reports "unchanged" and
    scores nothing -- the backfill would run forever and finish never.
    Clearing the fingerprint is what makes the next pass a real render, and it
    is the same move /items/{id}/renders/{kind}/clear-override already makes
    for the same reason."""
    _, render = await _seed(
        session, rating_key="1", status="rendered", quality_scored_at=None,
        fingerprint="deadbeef" * 8,
    )

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["status"] == "enqueued"
    assert body["selected"] == 1
    assert body["enqueued"] == 1
    render_id = render.id  # read before expire_all -- see test_api_actions.py's precedent
    session.expire_all()
    reread = (await session.execute(select(Render).where(Render.id == render_id))).scalar_one()
    assert reread.fingerprint is None


async def test_a_failed_batch_enqueue_leaves_no_fingerprint_cleared_and_no_jobs(
    client, auth_headers, session, monkeypatch
):
    """The fingerprint clear used to commit on its own, ahead of the enqueue
    loop -- so a failure partway through the old per-item loop could leave
    fingerprints cleared with nothing queued behind them. Now the clear stays
    a pending change until `enqueue_batch` runs, so the two share its one
    commit: if the batch insert itself fails, the whole transaction rolls
    back and nothing landed -- not the fingerprint, not a job, not an event."""
    import autoposter.api.action_center as action_center_module

    _, render = await _seed(
        session, rating_key="1", status="rendered", quality_scored_at=None,
        fingerprint="deadbeef" * 8,
    )

    async def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(action_center_module, "enqueue_batch", explode)

    with pytest.raises(RuntimeError):
        await client.post("/api/actions/backfill", headers=auth_headers)

    render_id = render.id  # read before expire_all -- see test_api_actions.py's precedent
    session.expire_all()
    reread = (await session.execute(select(Render).where(Render.id == render_id))).scalar_one()
    assert reread.fingerprint == "deadbeef" * 8
    assert (await session.execute(select(Job))).scalars().all() == []
    assert (await session.execute(select(EventLog))).scalars().all() == []


async def test_the_backfill_leaves_an_already_scored_rows_fingerprint_alone(
    client, auth_headers, session
):
    """The discriminating half: a backfill that cleared every fingerprint
    would pass the test above and force a full re-render of the whole library
    on the next pass."""
    from datetime import datetime, timezone

    _, scored = await _seed(
        session, rating_key="1", status="rendered",
        quality_scored_at=datetime.now(timezone.utc), fingerprint="cafebabe" * 8,
    )
    await _seed(session, rating_key="2", status="rendered", quality_scored_at=None)

    await client.post("/api/actions/backfill", headers=auth_headers)

    scored_id = scored.id  # read before expire_all -- see test_api_actions.py's precedent
    session.expire_all()
    reread = (await session.execute(select(Render).where(Render.id == scored_id))).scalar_one()
    assert reread.fingerprint == "cafebabe" * 8


async def test_the_backfill_queues_one_job_per_item_and_records_one_event(
    client, auth_headers, session
):
    item = MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune")
    session.add(item)
    await session.flush()
    session.add_all([
        Render(item_id=item.id, art_kind="poster", status="rendered", asset_path="/a.jpg"),
        Render(item_id=item.id, art_kind="background", status="rendered", asset_path="/b.jpg"),
    ])
    await session.commit()

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["selected"] == 2
    assert body["enqueued"] == 1
    assert len((await session.execute(select(Job))).scalars().all()) == 1
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [event.event_type for event in events] == ["action_center_quality_backfill"]


async def test_the_backfill_honours_the_configured_batch_size(
    app, client, auth_headers, session
):
    """One batch per press, not the whole library: the sweep's own reasoning --
    the worker pool and every provider budget. The operator pressing again is
    the pacing."""
    for n in range(3):
        await _seed(session, rating_key=str(n), status="rendered", fingerprint=f"{n}" * 64)

    edited = load_config(EXAMPLE)
    edited.scheduler.drift_batch_size = 1
    app.state.config_holder.swap(edited)

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["selected"] == 1
    cleared = (
        await session.execute(select(Render).where(Render.fingerprint.is_(None)))
    ).scalars().all()
    assert len(cleared) == 1


async def test_the_backfill_reports_completion_idempotently(client, auth_headers, session):
    """`complete` is an answer, not an error: a 200 with real counts, so the
    button can render it rather than styling it as a failure."""
    from datetime import datetime, timezone

    await _seed(
        session, rating_key="1", status="rendered",
        quality_scored_at=datetime.now(timezone.utc),
    )

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["status"] == "complete"
    assert body["selected"] == 0
    assert body["enqueued"] == 0
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_the_backfill_requires_a_session(client):
    assert (await client.get("/api/actions/backfill")).status_code == 401
    assert (await client.post("/api/actions/backfill")).status_code == 401


# --- pressing again while a batch is still in flight -------------------------
#
# Production, mid-backfill at 7681/17264: a press while the previous batch was
# still rendering re-selected the same unscored rows -- still unscored because
# their re-render had not landed yet -- and `enqueue_batch`'s own pending
# dedupe swallowed every one of them. The response read `enqueued: 0` with
# thousands of eligible rows still sitting elsewhere in the table: a dead
# button.


async def test_the_second_press_selects_rows_outside_the_pending_batch(
    app, client, auth_headers, session
):
    """The second press must not re-select item 0's still-unscored row -- its
    job is pending -- and must find item 1 instead, so `enqueued` stays
    honest rather than reporting 0 while item 1 is eligible."""
    for n in range(2):
        await _seed(session, rating_key=str(n), status="rendered")

    edited = load_config(EXAMPLE)
    edited.scheduler.drift_batch_size = 1
    app.state.config_holder.swap(edited)

    first = (await client.post("/api/actions/backfill", headers=auth_headers)).json()
    assert first["enqueued"] == 1

    second = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert second["selected"] == 1
    assert second["enqueued"] == 1


async def test_the_post_enqueue_queued_for_scoring_grows_cumulatively_across_presses(
    app, client, auth_headers, session
):
    """The operator mid-run at 8214/17264 (the live report this fixes) cannot
    see the queue's actual depth from one batch's own numbers. Each press's
    `queued_for_scoring` must read AFTER that press's own enqueue and must
    grow by the batch as further presses land on top of what is already in
    flight -- not restate one press's own batch size."""
    for n in range(3):
        await _seed(session, rating_key=str(n), status="rendered")

    edited = load_config(EXAMPLE)
    edited.scheduler.drift_batch_size = 1
    app.state.config_holder.swap(edited)

    first = (await client.post("/api/actions/backfill", headers=auth_headers)).json()
    assert first["queued_for_scoring"] == 1
    assert first["unscored_total"] == 3
    assert "1 of 3 unscored now queued for scoring" in first["detail"]

    second = (await client.post("/api/actions/backfill", headers=auth_headers)).json()
    assert second["queued_for_scoring"] == 2
    assert second["unscored_total"] == 3
    assert "2 of 3 unscored now queued for scoring" in second["detail"]


async def test_the_second_press_does_not_re_clear_a_pending_items_fingerprint(
    app, client, auth_headers, session
):
    """The discriminating half: a fix that simply widened the batch without
    excluding in-flight items would still touch item 0's row a second time."""
    _, render0 = await _seed(session, rating_key="0", status="rendered", fingerprint="a" * 64)
    await _seed(session, rating_key="1", status="rendered", fingerprint="b" * 64)

    edited = load_config(EXAMPLE)
    edited.scheduler.drift_batch_size = 1
    app.state.config_holder.swap(edited)

    await client.post("/api/actions/backfill", headers=auth_headers)  # item 0 goes pending

    # item 0's render has not been re-rendered yet -- still unscored -- and
    # something has left a fingerprint on it again. The second press must
    # leave it alone: item 0 already has a pending job.
    render0_id = render0.id
    session.expire_all()
    reread0 = (await session.execute(select(Render).where(Render.id == render0_id))).scalar_one()
    reread0.fingerprint = "sentinel" * 8
    await session.commit()

    await client.post("/api/actions/backfill", headers=auth_headers)  # should select item 1 only

    session.expire_all()
    reread0 = (await session.execute(select(Render).where(Render.id == render0_id))).scalar_one()
    assert reread0.fingerprint == "sentinel" * 8


async def test_a_deferred_jobs_item_is_not_selected_either(app, client, auth_headers, session):
    """`enqueue_batch` never wakes a deferred row (queue/jobs.py: "a scheduled
    pass is not that kind of signal"), so selecting a row whose item's job is
    merely deferred -- not pending -- clears its fingerprint for nothing: the
    same dead-button bug, reproduced for the deferred case."""
    item0, render0 = await _seed(
        session, rating_key="0", status="rendered", fingerprint="a" * 64
    )
    await _seed(session, rating_key="1", status="rendered", fingerprint="b" * 64)

    intent = RenderIntent(kind=item0.kind, title=item0.title)
    session.add(Job(kind="process_item", dedupe_key=intent.dedupe_key, state="deferred"))
    await session.commit()

    edited = load_config(EXAMPLE)
    edited.scheduler.drift_batch_size = 1
    app.state.config_holder.swap(edited)

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["selected"] == 1
    assert body["enqueued"] == 1
    render0_id = render0.id
    session.expire_all()
    reread0 = (await session.execute(select(Render).where(Render.id == render0_id))).scalar_one()
    assert reread0.fingerprint == "a" * 64
