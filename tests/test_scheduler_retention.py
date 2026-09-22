"""Perf spec D2: ``jobs``, ``events_log`` and ``provider_cache`` stop growing.

Every age here is seeded in SQL (``now() - make_interval(...)``) and every
cutoff is the database's ``now()``, never this process's clock -- the dev
Docker clock steps backwards, and the margins below (days) are what keep the
assertions independent of it.
"""
import logging

from sqlalchemy import select, text

from autoposter.api.action_center import _parked_by_latest_job
from autoposter.api.snapshots import JOB_STATES
from autoposter.db.models import EventLog, Job, ProviderCache
from autoposter.scheduler import retention
from autoposter.scheduler.retention import (
    EVENT_RETENTION_DAYS,
    JOB_RETENTION_DAYS,
    PROVIDER_CACHE_GRACE,
    PRUNABLE_JOB_STATES,
    PRUNED_EVENT_SOURCES,
    apply_retention,
    describe_retention,
)

# The operator and audit sources the spec keeps forever. The prune audit row
# is the only record of a deleted item (scheduler/prune.py).
AUDIT_SOURCES = ("prune", "merge", "config", "manual", "picker", "files", "facts")

OLD = JOB_RETENTION_DAYS + 10
RECENT = JOB_RETENTION_DAYS - 10


async def seed_job(session, *, state, key=None, age_days=0, kind="process_item") -> int:
    job_id = (
        await session.execute(
            text(
                "INSERT INTO jobs (kind, payload, dedupe_key, state, attempts, "
                "run_after, updated_at) VALUES (:kind, CAST('{}' AS jsonb), :key, "
                ":state, 0, now(), now() - make_interval(days => :age)) RETURNING id"
            ),
            {"kind": kind, "key": key, "state": state, "age": age_days},
        )
    ).scalar_one()
    await session.commit()
    return job_id


async def seed_event(session, *, source, age_days) -> int:
    event_id = (
        await session.execute(
            text(
                "INSERT INTO events_log (source, event_type, payload, received_at) "
                "VALUES (:source, 'seed', CAST('{}' AS jsonb), "
                "now() - make_interval(days => :age)) RETURNING id"
            ),
            {"source": source, "age": age_days},
        )
    ).scalar_one()
    await session.commit()
    return event_id


async def _seed_cache(session, key, *, offset_hours) -> None:
    await session.execute(
        text(
            "INSERT INTO provider_cache (key, value, expires_at) VALUES "
            "(:key, CAST('{}' AS jsonb), now() + make_interval(hours => :offset))"
        ),
        {"key": key, "offset": offset_hours},
    )
    await session.commit()


async def _ids(session, column) -> set:
    return set((await session.execute(select(column))).scalars().all())


def test_the_constants_are_the_spec_s():
    assert JOB_RETENTION_DAYS == 30
    assert EVENT_RETENTION_DAYS == 90
    assert PROVIDER_CACHE_GRACE.days == 1
    assert retention.BATCH == 5000
    assert set(PRUNED_EVENT_SOURCES) == {
        "radarr", "sonarr", "notifier", "actions", "collections", "playlists",
    }
    assert not set(PRUNED_EVENT_SOURCES) & set(AUDIT_SOURCES)
    # Every state retention may touch is a real one, and the live and
    # blocked states are never among them.
    assert set(PRUNABLE_JOB_STATES) <= set(JOB_STATES)
    assert set(JOB_STATES) - set(PRUNABLE_JOB_STATES) == {
        "pending", "running", "deferred", "parked", "failed",
    }


async def test_an_older_parked_row_under_a_newer_done_past_the_window_stays_unblocked(session):
    """The spec's named scenario. A newer `done` does not retire an older
    `parked` sibling, so if retention deleted the newest `done` the parked
    row would become "latest" and Action Center would report the item blocked.
    """
    parked = await seed_job(session, state="parked", key="movie:tmdb:1", age_days=OLD + 20)
    done = await seed_job(session, state="done", key="movie:tmdb:1", age_days=OLD)

    before = await _parked_by_latest_job(session)
    await apply_retention(session)
    after = await _parked_by_latest_job(session)

    assert "movie:tmdb:1" not in before
    assert after == before
    assert {parked, done} <= await _ids(session, Job.id)


async def test_retention_never_changes_which_items_action_center_reports_blocked(session):
    # Key A: the spec's scenario -- older parked, newer done, both old.
    a_parked = await seed_job(session, state="parked", key="A", age_days=OLD + 20)
    a_done = await seed_job(session, state="done", key="A", age_days=OLD)
    # Key B: an old done under a newer parked -- the done goes, B stays blocked.
    b_done = await seed_job(session, state="done", key="B", age_days=OLD + 5)
    b_parked = await seed_job(session, state="parked", key="B", age_days=1)
    # Key C: three old finished rows -- only the newest survives.
    c_oldest = await seed_job(session, state="dismissed", key="C", age_days=OLD + 30)
    c_middle = await seed_job(session, state="done_with_warnings", key="C", age_days=OLD + 20)
    c_newest = await seed_job(session, state="done", key="C", age_days=OLD)

    before = await _parked_by_latest_job(session)
    counts = await apply_retention(session)
    after = await _parked_by_latest_job(session)

    assert before == {"B"}
    assert after == before
    survivors = await _ids(session, Job.id)
    assert {a_parked, a_done, b_parked, c_newest} <= survivors
    assert not {b_done, c_oldest, c_middle} & survivors
    assert counts["jobs"] == 3


async def test_null_key_rows_go_past_the_window_and_stay_inside_it(session):
    old = await seed_job(session, state="done", age_days=OLD)
    recent = await seed_job(session, state="done", age_days=RECENT)

    await apply_retention(session)

    survivors = await _ids(session, Job.id)
    assert old not in survivors
    assert recent in survivors


async def test_live_blocked_and_failed_rows_are_never_touched(session):
    protected = [state for state in JOB_STATES if state not in PRUNABLE_JOB_STATES]
    ids = [await seed_job(session, state=state, age_days=400) for state in protected]

    counts = await apply_retention(session)

    assert set(ids) <= await _ids(session, Job.id)
    assert counts["jobs"] == 0


async def test_a_keyed_row_of_another_kind_is_left_alone(session):
    """Only process_item rows carry a dedupe_key today. A keyed row of any
    other kind is not guessed about -- it is kept."""
    older = await seed_job(session, state="done", key="X", age_days=OLD + 5, kind="other")
    newer = await seed_job(session, state="done", key="X", age_days=OLD, kind="other")

    await apply_retention(session)

    assert {older, newer} <= await _ids(session, Job.id)


async def test_only_allowlisted_event_sources_expire(session):
    expired = [
        await seed_event(session, source=source, age_days=EVENT_RETENTION_DAYS + 10)
        for source in PRUNED_EVENT_SOURCES
    ]
    inside_window = [
        await seed_event(session, source=source, age_days=EVENT_RETENTION_DAYS - 10)
        for source in PRUNED_EVENT_SOURCES
    ]
    audit = [await seed_event(session, source=source, age_days=1000) for source in AUDIT_SOURCES]

    counts = await apply_retention(session)

    survivors = await _ids(session, EventLog.id)
    assert not set(expired) & survivors
    assert set(inside_window) <= survivors
    assert set(audit) <= survivors, "an audit or operator source was deleted"
    assert counts["events_log"] == len(PRUNED_EVENT_SOURCES)


async def test_the_provider_cache_grace_is_respected(session):
    grace_hours = int(PROVIDER_CACHE_GRACE.total_seconds() // 3600)
    await _seed_cache(session, "past-grace", offset_hours=-(grace_hours + 6))
    await _seed_cache(session, "inside-grace", offset_hours=-(grace_hours - 6))
    await _seed_cache(session, "live", offset_hours=5)

    counts = await apply_retention(session)

    assert await _ids(session, ProviderCache.key) == {"inside-grace", "live"}
    assert counts["provider_cache"] == 1


async def test_batching_loops_until_nothing_is_left(session, monkeypatch):
    """With a batch of 2, a single LIMITed DELETE would remove 2 of 5 rows;
    removing all 5 is the loop running to completion."""
    monkeypatch.setattr(retention, "BATCH", 2)
    for _ in range(5):
        await seed_job(session, state="done", age_days=OLD)
        await seed_event(session, source=PRUNED_EVENT_SOURCES[0], age_days=EVENT_RETENTION_DAYS + 10)
    grace_hours = int(PROVIDER_CACHE_GRACE.total_seconds() // 3600)
    for n in range(5):
        await _seed_cache(session, f"k{n}", offset_hours=-(grace_hours + 6))

    counts = await apply_retention(session)

    assert counts == {"jobs": 5, "events_log": 5, "provider_cache": 5}
    assert await _ids(session, Job.id) == set()
    assert await _ids(session, EventLog.id) == set()
    assert await _ids(session, ProviderCache.key) == set()


async def test_a_failing_table_is_rolled_back_and_the_others_still_run(
    session, session_factory, monkeypatch, caplog
):
    old_job = await seed_job(session, state="done", age_days=OLD)
    old_event = await seed_event(
        session, source=PRUNED_EVENT_SOURCES[0], age_days=EVENT_RETENTION_DAYS + 10
    )
    grace_hours = int(PROVIDER_CACHE_GRACE.total_seconds() // 3600)
    await _seed_cache(session, "stale", offset_hours=-(grace_hours + 6))
    monkeypatch.setattr(retention, "_EVENTS_SQL", text("DELETE FROM no_such_table"))

    with caplog.at_level(logging.ERROR, logger="autoposter.scheduler.retention"):
        counts = await apply_retention(session)

    assert counts == {"jobs": 1, "events_log": None, "provider_cache": 1}
    assert any("events_log" in record.getMessage() for record in caplog.records)
    async with session_factory() as fresh:
        assert old_job not in await _ids(fresh, Job.id), "the jobs delete was not committed"
        assert old_event in await _ids(fresh, EventLog.id)
        assert await _ids(fresh, ProviderCache.key) == set()


def test_describe_retention():
    assert describe_retention({"jobs": 0, "events_log": 0, "provider_cache": 0}) == ""
    assert (
        describe_retention({"jobs": 3, "events_log": 0, "provider_cache": 2})
        == "; retention removed 3 jobs, 2 provider_cache row(s)"
    )
    assert (
        describe_retention({"jobs": 1, "events_log": None, "provider_cache": 0})
        == "; retention removed 1 jobs row(s); retention failed for events_log (see the log)"
    )
