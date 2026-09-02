"""GET/POST /api/actions -- the Action Center's queue, counts and actions.

Two of these tests are the phase's load-bearing ones and are worth naming:
`test_editing_the_provider_order_changes_the_queue_with_no_row_write` proves
the judgement is derived rather than stored, through the real endpoint and the
real config holder; and
`test_a_dismissed_row_returns_once_its_facts_change` proves 11b's dismissal
identity, which is the risk that row names for itself.
"""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ActionDismissal, EventLog, Job, MediaItem, Render

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
