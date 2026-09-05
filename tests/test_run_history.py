"""The only module that writes `runs` (roadmap row 53).

Against a real database rather than a mock session: every statement here is
SQL -- a window function for the retention clause, the database clock for both
timestamps -- and a mocked session would verify the Python around SQL that was
never executed.
"""

from sqlalchemy import select, text

from autoposter.db.models import Run
from autoposter.scheduler.run_history import (
    RUN_HISTORY_KEEP,
    close_run,
    open_run,
    trim_run_history,
)


async def _rows(session, name=None):
    statement = select(Run).order_by(Run.id)
    if name is not None:
        statement = statement.where(Run.name == name)
    return (await session.execute(statement)).scalars().all()


async def test_opening_a_run_records_it_as_running_on_the_database_clock(session):
    run_id = await open_run(session, kind="scheduled", name="plex_prune")
    await session.commit()

    row = (await _rows(session))[0]
    assert row.id == run_id
    assert (row.kind, row.name, row.status) == ("scheduled", "plex_prune", "running")
    assert row.started_at is not None
    assert row.finished_at is None
    # Not attributed until something attributes it (Task 2). NULL, never 0.
    assert row.processed is None
    assert row.rendered_poster is None


async def test_closing_a_run_stamps_the_finish_the_status_and_the_detail(session):
    run_id = await open_run(session, kind="scheduled", name="plex_prune")
    await close_run(session, run_id, status="ok", detail="nothing to do")
    await session.commit()

    row = (await _rows(session))[0]
    assert row.status == "ok"
    assert row.detail == "nothing to do"
    assert row.finished_at is not None
    assert row.finished_at >= row.started_at


async def test_a_long_detail_is_truncated_to_the_stored_width(session):
    """scheduled_runs.last_detail's own rule, applied to the copy: the string
    is already narrowed to a class name by scheduler/core.py before it gets
    here, but a `served_detail` exception's reviewed message has no length
    bound, and an unbounded write into a served column is how a row becomes
    unrenderable."""
    run_id = await open_run(session, kind="scheduled", name="plex_prune")
    await close_run(session, run_id, status="failed", detail="x" * 5000)
    await session.commit()

    assert len((await _rows(session))[0].detail) == 2000


async def test_retention_keeps_the_newest_rows_per_name(session):
    """C6: the table is bounded by a clause in the existing cleanup pass, not
    by a fifth scheduler job. Per NAME, because the cadences differ by three
    orders of magnitude -- stale_job_reclaim runs every five minutes and
    plex_prune every seven days, and a global cap would evict a week of the
    latter to make room for an afternoon of the former."""
    for index in range(RUN_HISTORY_KEEP + 3):
        run_id = await open_run(session, kind="scheduled", name="stale_job_reclaim")
        await close_run(session, run_id, status="ok", detail=str(index))
    await session.commit()

    deleted = await trim_run_history(session)
    await session.commit()

    assert deleted == 3
    kept = await _rows(session, name="stale_job_reclaim")
    assert len(kept) == RUN_HISTORY_KEEP
    # The three OLDEST went; the newest survive.
    assert [row.detail for row in kept[:3]] == ["3", "4", "5"]


async def test_retention_counts_each_name_independently(session):
    """A cheap job's flood must not evict an expensive job's history."""
    for _ in range(5):
        await open_run(session, kind="scheduled", name="noisy")
    quiet_id = await open_run(session, kind="scheduled", name="quiet")
    await session.commit()

    deleted = await trim_run_history(session, keep=2)
    await session.commit()

    assert deleted == 3
    assert len(await _rows(session, name="noisy")) == 2
    assert [row.id for row in await _rows(session, name="quiet")] == [quiet_id]


async def test_opening_a_run_closes_its_orphaned_predecessor_as_interrupted(session):
    """Important 2: a pod SIGKILL or crash between a prior open and its own
    close leaves that row `running` forever with nothing else to reconcile
    it. The next pass of the SAME name is the first thing to notice, and
    closes it as `interrupted` -- in the same transaction as its own open."""
    first_id = await open_run(session, kind="scheduled", name="plex_prune")
    await session.commit()
    # No close_run call for first_id -- as if the process died mid-run.

    second_id = await open_run(session, kind="scheduled", name="plex_prune")
    await session.commit()

    rows = {row.id: row for row in await _rows(session, name="plex_prune")}
    assert rows[first_id].status == "interrupted"
    assert rows[first_id].finished_at is not None
    assert rows[second_id].status == "running"
    assert rows[second_id].finished_at is None


async def test_retention_deletes_nothing_below_the_threshold(session):
    """The pass runs weekly on a table that is usually well inside the cap;
    the statement must be a no-op then, not a rewrite of every row."""
    await open_run(session, kind="full_pass", name="full_pass")
    await session.commit()

    assert await trim_run_history(session) == 0
    await session.commit()
    assert len(await _rows(session)) == 1
    # And the guard on the guard: the fixture's table really is empty of
    # anything else, so the assertion above is about the clause, not luck.
    assert (await session.execute(text("SELECT count(*) FROM runs"))).scalar_one() == 1
