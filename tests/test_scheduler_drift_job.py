"""``sweep_stale_facts`` / ``make_drift_job``: the periodic ratings-drift sweep.

Ratings change without any file event, so nothing else re-triggers an item.
The sweep re-enqueues the same ``process_item`` job the webhook intake path
uses so the existing pipeline notices the change; it does no provider work
itself. These tests use the real database session and the real ``enqueue``
(see ``tests/test_queue.py``) rather than a fake queue, so the debounce
behaviour they lean on is the one actually shipped.
"""
import threading
from types import SimpleNamespace

from sqlalchemy import select, text

from autoposter.config.holder import ConfigHolder
from autoposter.db.models import ItemFacts, Job, MediaItem
from autoposter.scheduler import jobs as scheduler_jobs
from autoposter.scheduler.jobs import make_credits_job, make_drift_job, sweep_stale_facts

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


async def test_a_permanently_failing_item_does_not_starve_the_others(session):
    """An item that can never be resolved -- no external ids, gone from Plex,
    a provider that keeps erroring -- never gets a ``fetched_at``, so ordering
    on ``fetched_at`` alone puts it at the front of every sweep for ever.
    ``drift_batch_size`` of those and nothing else is ever revisited again,
    which is exactly what the sweep exists to prevent.
    """
    await _make_item(session, title="Stuck", tmdb_id=1)
    other = await _make_item(session, title="Other", tmdb_id=2)
    await _make_facts(session, other.id, age_days=10)

    assert await sweep_stale_facts(session, max_age_days=7, batch_size=1) == 1
    assert await _titles(session) == ["Stuck"]

    # "Stuck" is still the most stale by fetched_at -- it still has none --
    # but it has just been attempted, so the next sweep must move on.
    assert await sweep_stale_facts(session, max_age_days=7, batch_size=1) == 1
    assert await _titles(session) == ["Stuck", "Other"]


async def test_an_attempted_item_comes_round_again_behind_the_others(session):
    """Deprioritised, not dropped. Once everything else has been attempted
    too, the failing item is retried like anything else -- the sweep still
    eventually revisits every item, which is the whole point of it."""
    stuck = await _make_item(session, title="Stuck", tmdb_id=1)
    other = await _make_item(session, title="Other", tmdb_id=2)
    await _make_facts(session, other.id, age_days=10)

    await sweep_stale_facts(session, max_age_days=7, batch_size=1)
    await sweep_stale_facts(session, max_age_days=7, batch_size=1)
    assert await _titles(session) == ["Stuck", "Other"]

    # Both have been attempted now; let the queue drain and age "Stuck"'s
    # attempt so it is once again the least-recently-touched of the two.
    await session.execute(text("UPDATE jobs SET state = 'done'"))
    await session.execute(
        text(
            "UPDATE media_items SET facts_attempted_at = now() - interval '30 days'"
            " WHERE id = :id"
        ),
        {"id": stuck.id},
    )
    await session.commit()

    assert await sweep_stale_facts(session, max_age_days=7, batch_size=1) == 1
    assert await _titles(session) == ["Stuck", "Other", "Stuck"]


async def test_make_drift_job_wraps_sweep_stale_facts(session):
    item = await _make_item(session, title="Stale Movie")
    await _make_facts(session, item.id, age_days=10)

    config = SimpleNamespace(
        scheduler=SimpleNamespace(drift_days=7, drift_max_age_days=7, drift_batch_size=500)
    )
    job = make_drift_job(ConfigHolder(config))
    summary = await job.run(session)

    assert job.name == "ratings_drift_sweep"
    assert "enqueued 1 item(s)" in summary
    assert await _titles(session) == ["Stale Movie"]


async def test_make_credits_job_scans_with_a_server_from_the_factory(session, monkeypatch):
    """The library-credits scan (roadmap rows 197/194). Connecting to Plex
    blocks, so ``server_factory`` runs through ``asyncio.to_thread`` -- the
    same contract ``make_collections_job`` takes, asserted here by the thread
    the factory actually ran on."""
    main_thread = threading.current_thread()
    ran_on = []

    def server_factory():
        ran_on.append(threading.current_thread())
        return "the-server"

    seen = {}

    async def fake_scan(scan_session, server, config):
        seen["session"] = scan_session
        seen["server"] = server
        seen["config"] = config
        return "Movies: 3 item(s) scanned, 7 credit(s)"

    monkeypatch.setattr(scheduler_jobs, "scan_credits", fake_scan)

    config = SimpleNamespace(scheduler=SimpleNamespace(credits_scan_days=7))
    holder = ConfigHolder(config)
    job = make_credits_job(holder, server_factory)

    summary = await job.run(session)

    assert job.name == "credits_scan"
    assert summary == "Movies: 3 item(s) scanned, 7 credit(s)"
    assert seen["session"] is session
    assert seen["server"] == "the-server"
    assert seen["config"] is config
    assert ran_on and ran_on[0] is not main_thread


def test_make_credits_job_reads_its_cadence_live():
    """The cadence is a deref of the holder, not a value captured at build
    time: an edited ``credits_scan_days`` takes effect on the next poll rather
    than at the next restart."""
    config = SimpleNamespace(scheduler=SimpleNamespace(credits_scan_days=7))
    holder = ConfigHolder(config)
    job = make_credits_job(holder, lambda: None)

    assert job.current_interval() == 7 * 24 * 3600

    holder.swap(SimpleNamespace(scheduler=SimpleNamespace(credits_scan_days=1)))

    assert job.current_interval() == 24 * 3600


async def test_the_enqueued_intent_carries_the_rows_rating_key(session):
    """The first of the two silent twin producers.

    ``_stamp_and_enqueue`` built its ``RenderIntent`` with no ``rating_key``
    field at all, so every drift job went straight to the GUID walk, resolved
    the LIVE key and upserted a second row -- silently, because the pipeline's
    fork warning only fires when the intent carried a key to disagree with.
    Carrying the key is what every other row-derived intent already does
    (``prune.intent_for``, ``routes._enqueue_reprocess``,
    ``action_center._reprocess_entries``).
    """
    item = await _make_item(session, title="Stale Movie", tmdb_id=42)
    await _make_facts(session, item.id, age_days=8)

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=500)

    assert count == 1
    (job,) = (await session.execute(select(Job).order_by(Job.id))).scalars().all()
    assert job.payload["rating_key"] == item.rating_key
