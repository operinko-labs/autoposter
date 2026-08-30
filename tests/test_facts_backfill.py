"""Row 206's one-shot catch-up: ``backfill_facts`` and its trigger endpoint.

The drift sweep visits 500 items a week and only ones older than
``drift_max_age_days``, so the two facts columns the location/language packs
read (written [] / NULL by the pre-widening backfill) converge in ~5 weeks.
This is the same walk with the age predicate dropped and a persisted cursor
in its place: each trigger takes the next ``drift_batch_size`` parents by id,
stamps them the way the sweep stamps its picks, and enqueues the same
``process_item`` job -- the pipeline, not a new one, does the provider work.

The park is the one behaviour that is not the sweep's: a batch enqueued into
an open TMDb 429 window would be gathered with TMDb skipped, ``fetched_at``
stamped anyway, and the new columns lost until the item ages back into the
drift sweep -- so a blocked budget refuses the TRIGGER, cursor unmoved.
"""
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, FactsBackfillState, ItemFacts, Job, MediaItem
from autoposter.facts.tmdb_budget import TmdbRateBudget
from autoposter.scheduler.jobs import backfill_facts

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

_next_rating_key = iter(str(n) for n in range(1, 1_000_000))


async def _make_item(session, *, kind="movie", title="Item", tmdb_id=None, parent_id=None):
    item = MediaItem(
        rating_key=next(_next_rating_key),
        library="Movies" if kind == "movie" else "TV Shows",
        kind=kind,
        title=title,
        tmdb_id=tmdb_id,
        parent_id=parent_id,
    )
    session.add(item)
    await session.commit()
    return item


async def _make_fresh_facts(session, item_id: int) -> None:
    """A facts row fetched JUST NOW -- the drift sweep would skip this item
    for ``drift_max_age_days``; the backfill must not."""
    facts = ItemFacts(item_id=item_id)
    session.add(facts)
    await session.commit()
    await session.execute(
        text("UPDATE item_facts SET fetched_at = now() WHERE item_id = :item_id"),
        {"item_id": item_id},
    )
    await session.commit()


async def _titles(session) -> list[str]:
    jobs = (await session.execute(select(Job).order_by(Job.id))).scalars().all()
    return [job.payload["title"] for job in jobs]


async def _cursor(session) -> int | None:
    state = (
        await session.execute(
            select(FactsBackfillState).where(FactsBackfillState.id == 1)
        )
    ).scalar_one_or_none()
    return state.cursor_item_id if state is not None else None


# --- the function ------------------------------------------------------------


async def test_the_first_batch_takes_the_lowest_ids_and_advances_the_cursor(session):
    first = await _make_item(session, title="A", tmdb_id=1)
    second = await _make_item(session, title="B", tmdb_id=2)
    await _make_item(session, title="C", tmdb_id=3)

    batch = await backfill_facts(session, batch_size=2)
    await session.commit()

    assert (batch.selected, batch.enqueued) == (2, 2)
    assert await _titles(session) == ["A", "B"]
    assert await _cursor(session) == second.id
    assert first.id < second.id


async def test_a_second_trigger_resumes_past_the_cursor(session):
    await _make_item(session, title="A", tmdb_id=1)
    await _make_item(session, title="B", tmdb_id=2)
    third = await _make_item(session, title="C", tmdb_id=3)

    await backfill_facts(session, batch_size=2)
    await session.commit()
    batch = await backfill_facts(session, batch_size=2)
    await session.commit()

    assert (batch.selected, batch.enqueued) == (1, 1)
    assert await _titles(session) == ["A", "B", "C"]
    assert await _cursor(session) == third.id


async def test_fresh_facts_do_not_exempt_an_item(session):
    """THE delta against the sweep: the age predicate is gone. An item whose
    facts were fetched a minute ago is exactly the population this exists for
    -- every row's ``tmdb_origin_country`` was written [] before the widening,
    and freshness says nothing about the missing columns."""
    item = await _make_item(session, title="Fresh", tmdb_id=1)
    await _make_fresh_facts(session, item.id)

    batch = await backfill_facts(session, batch_size=500)
    await session.commit()

    assert batch.selected == 1
    assert await _titles(session) == ["Fresh"]


async def test_episodes_and_seasons_are_left_out(session):
    """The sweep's population rule, inherited verbatim: seasons and episodes
    are covered by their parent's pass."""
    show = await _make_item(session, kind="show", title="A Show")
    await _make_item(session, kind="season", title="S1", parent_id=show.id)
    await _make_item(session, kind="episode", title="E1", parent_id=show.id)

    batch = await backfill_facts(session, batch_size=500)
    await session.commit()

    assert batch.selected == 1
    assert await _titles(session) == ["A Show"]


async def test_selected_items_are_stamped_attempted(session):
    """The sweep's stamp, inherited: without it the drift sweep's next tick
    would re-select (and re-enqueue) exactly what the backfill just queued."""
    item = await _make_item(session, title="A", tmdb_id=1)

    await backfill_facts(session, batch_size=500)
    await session.commit()
    await session.refresh(item)

    assert item.facts_attempted_at is not None


async def test_a_drained_population_reports_complete_idempotently(session):
    await _make_item(session, title="A", tmdb_id=1)
    await backfill_facts(session, batch_size=500)
    await session.commit()
    cursor_after_walk = await _cursor(session)

    batch = await backfill_facts(session, batch_size=500)
    await session.commit()

    assert (batch.selected, batch.enqueued) == (0, 0)
    assert await _cursor(session) == cursor_after_walk
    assert await _titles(session) == ["A"]


# --- the endpoint ------------------------------------------------------------


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


def _app(session_factory, batch_size=500):
    config = load_config(EXAMPLE)
    config.scheduler.drift_batch_size = batch_size
    config.operations.tmdb_backoff_seconds = 60
    return create_app(config, session_factory, _secrets())


async def _client_and_headers(app):
    asgi = ASGITransport(app=app)
    client = AsyncClient(transport=asgi, base_url="http://test")
    response = await client.post("/api/login", json={"password": PASSWORD})
    return client, {"Authorization": f"Bearer {response.json()['token']}"}


async def test_the_endpoint_requires_a_session(session_factory):
    app = _app(session_factory)
    asgi = ASGITransport(app=app)
    async with AsyncClient(transport=asgi, base_url="http://test") as client:
        assert (await client.post("/api/facts/backfill")).status_code == 401
        assert (await client.get("/api/facts/backfill")).status_code == 401


async def test_post_enqueues_a_batch_and_reports_progress_from_the_live_count(
    session, session_factory
):
    for title in ("A", "B", "C"):
        await _make_item(session, title=title, tmdb_id=ord(title))

    app = _app(session_factory, batch_size=2)
    client, headers = await _client_and_headers(app)
    try:
        response = await client.post("/api/facts/backfill", headers=headers)
    finally:
        await client.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "enqueued"
    assert body["enqueued"] == 2
    # Live count of THIS database's movie/show parents -- never the row's
    # measured 2252 (facts C2).
    assert (body["done"], body["total"]) == (2, 3)
    assert await _titles(session) == ["A", "B"]
    audit = (await session.execute(select(EventLog))).scalars().all()
    assert [row.event_type for row in audit] == ["facts_backfill"]
    assert audit[0].source == "facts"
    assert audit[0].payload["enqueued"] == 2


async def test_a_completed_walk_answers_complete_and_enqueues_nothing(
    session, session_factory
):
    await _make_item(session, title="A", tmdb_id=1)
    app = _app(session_factory)
    client, headers = await _client_and_headers(app)
    try:
        first = await client.post("/api/facts/backfill", headers=headers)
        second = await client.post("/api/facts/backfill", headers=headers)
    finally:
        await client.aclose()

    assert first.json()["status"] == "enqueued"
    assert second.json()["status"] == "complete"
    assert second.json()["enqueued"] == 0
    assert await _titles(session) == ["A"]


async def test_a_blocked_tmdb_budget_parks_the_trigger(session, session_factory):
    """C2's parks-not-loses: an open 429 window means the batch would gather
    with TMDb skipped and stamp ``fetched_at`` anyway -- the columns this
    sweep exists to fill would be silently lost until the drift sweep aged
    the items back in. So the trigger refuses and the cursor does not move."""
    await _make_item(session, title="A", tmdb_id=1)
    await TmdbRateBudget(session_factory, 60).note_refusal(60)

    app = _app(session_factory)
    client, headers = await _client_and_headers(app)
    try:
        response = await client.post("/api/facts/backfill", headers=headers)
    finally:
        await client.aclose()

    body = response.json()
    assert body["status"] == "parked"
    assert body["enqueued"] == 0
    assert await _titles(session) == []
    assert await _cursor(session) is None


async def test_get_reports_progress_without_enqueuing(session, session_factory):
    await _make_item(session, title="A", tmdb_id=1)
    await _make_item(session, title="B", tmdb_id=2)

    app = _app(session_factory, batch_size=1)
    client, headers = await _client_and_headers(app)
    try:
        before = await client.get("/api/facts/backfill", headers=headers)
        await client.post("/api/facts/backfill", headers=headers)
        during = await client.get("/api/facts/backfill", headers=headers)
        await client.post("/api/facts/backfill", headers=headers)
        after = await client.get("/api/facts/backfill", headers=headers)
    finally:
        await client.aclose()

    assert before.json() == {"status": "not_started", "done": 0, "total": 2}
    assert during.json() == {"status": "in_progress", "done": 1, "total": 2}
    assert after.json() == {"status": "complete", "done": 2, "total": 2}
    assert len(await _titles(session)) == 2
