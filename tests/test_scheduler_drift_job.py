"""``sweep_stale_facts`` / ``make_drift_job``: the periodic ratings-drift sweep.

Ratings change without any file event, so nothing else re-triggers an item.
The sweep re-enqueues the same ``process_item`` job the webhook intake path
uses so the existing pipeline notices the change; it does no provider work
itself. These tests use the real database session and the real ``enqueue``
(see ``tests/test_queue.py``) rather than a fake queue, so the debounce
behaviour they lean on is the one actually shipped.
"""
from types import SimpleNamespace

from sqlalchemy import select, text

from autoposter.db.models import ItemFacts, Job, MediaItem
from autoposter.scheduler.jobs import make_drift_job, sweep_stale_facts

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


async def _make_facts(session, item_id: int, *, age_days: float) -> None:
    """Attach an ``ItemFacts`` row to ``item_id`` whose ``fetched_at`` is
    ``age_days`` old, measured against the database clock."""
    facts = ItemFacts(item_id=item_id)
    session.add(facts)
    await session.commit()
    await session.execute(
        text(
            "UPDATE item_facts SET fetched_at = now() - make_interval(secs => :secs)"
            " WHERE item_id = :item_id"
        ),
        {"secs": age_days * 86400, "item_id": item_id},
    )
    await session.commit()


async def _titles(session) -> list[str]:
    jobs = (await session.execute(select(Job).order_by(Job.id))).scalars().all()
    return [job.payload["title"] for job in jobs]


async def test_items_with_fresh_facts_are_not_enqueued(session):
    item = await _make_item(session, title="Fresh Movie")
    await _make_facts(session, item.id, age_days=1)

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=500)

    assert count == 0
    assert await _titles(session) == []


async def test_items_with_stale_facts_are_enqueued(session):
    item = await _make_item(session, title="Stale Movie")
    await _make_facts(session, item.id, age_days=8)

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=500)

    assert count == 1
    assert await _titles(session) == ["Stale Movie"]


async def test_items_with_no_facts_row_at_all_are_enqueued(session):
    await _make_item(session, title="Never Processed")

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=500)

    assert count == 1
    assert await _titles(session) == ["Never Processed"]


async def test_batch_size_caps_how_many_are_enqueued(session):
    for i in range(5):
        item = await _make_item(session, title=f"Movie {i}")
        await _make_facts(session, item.id, age_days=10)

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=2)

    assert count == 2
    assert len(await _titles(session)) == 2


async def test_oldest_first_ordering(session):
    # A missing-facts row is the most stale of all and must sort ahead of
    # every item that merely has an old timestamp.
    await _make_item(session, title="Never Processed", tmdb_id=1)
    oldest = await _make_item(session, title="Oldest", tmdb_id=2)
    await _make_facts(session, oldest.id, age_days=30)
    middle = await _make_item(session, title="Middle", tmdb_id=3)
    await _make_facts(session, middle.id, age_days=15)

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=2)

    assert count == 2
    assert await _titles(session) == ["Never Processed", "Oldest"]


async def test_returned_count_matches_jobs_actually_enqueued(session):
    for i in range(3):
        item = await _make_item(session, title=f"Movie {i}", tmdb_id=100 + i)
        await _make_facts(session, item.id, age_days=10)

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=500)

    assert count == 3
    assert len(await _titles(session)) == 3


async def test_episodes_are_not_enqueued(session):
    show = await _make_item(session, kind="show", title="A Show")
    # Neither the show nor the episode has a facts row, so both would
    # qualify as stale on that rule alone -- only the show's kind is
    # eligible.
    await _make_item(session, kind="episode", title="An Episode", parent_id=show.id)

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=500)

    assert count == 1
    assert await _titles(session) == ["A Show"]


async def test_make_drift_job_wraps_sweep_stale_facts(session):
    item = await _make_item(session, title="Stale Movie")
    await _make_facts(session, item.id, age_days=10)

    job = make_drift_job(SimpleNamespace())
    summary = await job.run(session)

    assert job.name == "ratings_drift_sweep"
    assert "enqueued 1 item(s)" in summary
    assert await _titles(session) == ["Stale Movie"]
