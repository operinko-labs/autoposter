"""The only module that writes `runs` (roadmap row 53).

Against a real database rather than a mock session: every statement here is
SQL -- a window function for the retention clause, the database clock for both
timestamps -- and a mocked session would verify the Python around SQL that was
never executed.
"""

from datetime import timedelta

from sqlalchemy import func, select, text

from autoposter.db.models import Job, Render, Run

from conftest import seed_media_item
from autoposter.scheduler.run_history import (
    FULL_PASS_CEILING_SECONDS,
    FULL_PASS_NAME,
    RUN_HISTORY_KEEP,
    close_drained_full_passes,
    close_run,
    open_run,
    trim_run_history,
    window_counts,
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


async def test_two_open_full_passes_coexist_and_a_scheduled_open_does_not_touch_them(
    session,
):
    """The orphan-close in open_run is scoped to scheduled runs. Full passes
    all share the name `full_pass` and Task 2 opens one per button press, so
    without `Run.kind == "scheduled"` in the WHERE, a second press would
    orphan the first as `interrupted` before its counts are ever stamped."""
    first = await open_run(session, kind="full_pass", name="full_pass")
    second = await open_run(session, kind="full_pass", name="full_pass")
    await session.commit()

    await open_run(session, kind="scheduled", name="plex_prune")
    await session.commit()

    rows = {row.id: row for row in await _rows(session, name="full_pass")}
    assert rows[first].status == "running"
    assert rows[second].status == "running"


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


async def _item(session, rating_key="1"):
    return await seed_media_item(session, rating_key, library="Movies", kind="movie", title="Dune")


async def _now(session):
    return (await session.execute(select(func.now()))).scalar_one()


async def test_window_counts_group_renders_by_art_kind_over_rendered_at(session):
    """C3's first number: artifacts RE-COMPOSITED, from renders.rendered_at,
    which render/pipeline.py stamps only at the write-back -- the fingerprint
    short-circuit returns before it, so this is composites and not visits.
    That is exactly why `processed` is a separate column."""
    # renders.uq_render_item_kind is one row per (item_id, art_kind), so two
    # "rendered_poster" hits in the same window come from two different
    # items, not one item rendered twice.
    items = [await _item(session, rating_key=str(n)) for n in range(1, 4)]
    start = await _now(session)
    for item, kind in zip(items, ("poster", "poster", "background")):
        session.add(
            Render(item_id=item.id, art_kind=kind, asset_path="/x.jpg",
                   status="rendered", rendered_at=func.now())
        )
    await session.commit()
    end = await _now(session)

    counts = await window_counts(session, start, end)

    assert counts["rendered_poster"] == 2
    assert counts["rendered_background"] == 1
    # Always all four keys, zero-filled -- api/stats.py's rule, so a chart
    # never points at a field that vanished because nothing of that kind was
    # composited.
    assert counts["rendered_season_poster"] == 0
    assert counts["rendered_title_card"] == 0
    assert all(isinstance(value, int) for value in counts.values())


async def test_a_render_outside_the_window_is_not_counted(session):
    item = await _item(session)
    session.add(
        Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg",
               status="rendered", rendered_at=func.now())
    )
    await session.commit()
    start = await _now(session)
    end = start + timedelta(hours=1)

    assert (await window_counts(session, start, end))["rendered_poster"] == 0


async def test_window_counts_take_jobs_by_state_and_never_parked(session):
    """C3's second number, and its deliberate omission: `parked` is not a
    served count. A parked job is an operator matter the Action Center owns,
    and folding it into a run's rollup would put it on a surface with no way
    to act on it."""
    start = await _now(session)
    for state in ("done", "done", "failed", "deferred", "parked", "pending"):
        session.add(Job(kind="process_item", payload={}, state=state))
    await session.commit()
    end = await _now(session)

    counts = await window_counts(session, start, end)

    assert counts["processed"] == 2
    assert counts["failed"] == 1
    assert counts["deferred"] == 1
    assert "parked" not in counts


async def test_only_process_item_jobs_are_counted(session):
    """`process_item` is the only kind the worker pool dispatches today
    (app.py's `handlers` map), so this changes no number now -- and it is what
    keeps the count meaning the same thing on the day a second kind lands,
    rather than silently absorbing it."""
    start = await _now(session)
    session.add(Job(kind="process_item", payload={}, state="done"))
    session.add(Job(kind="something_else", payload={}, state="done"))
    await session.commit()
    end = await _now(session)

    assert (await window_counts(session, start, end))["processed"] == 1


async def test_a_drained_full_pass_is_closed_with_its_counts(session):
    run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    session.add(Job(kind="process_item", payload={}, state="done"))
    session.add(Job(kind="process_item", payload={}, state="failed"))
    await session.commit()

    assert await close_drained_full_passes(session) == 1
    await session.commit()

    row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "ok"
    assert row.finished_at is not None
    assert (row.processed, row.failed, row.deferred) == (1, 1, 0)
    assert row.rendered_poster == 0
    # Row 213: counts only. Never a job's last_error on this surface.
    assert row.detail == "drained: 1 processed, 1 failed, 0 deferred"


async def test_a_pending_job_holds_the_run_open_and_a_deferred_one_does_not(session):
    """C2, both halves. A pending or running job created inside the window is
    the pass still draining. A DEFERRED one is a wait, not work in flight --
    queue/jobs.py's DEFER_INTERVAL_SECONDS is six hours with no attempt cap,
    so letting one hold the row open would mean a run that never closes."""
    run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    pending = Job(kind="process_item", payload={}, state="pending")
    session.add(pending)
    await session.commit()

    assert await close_drained_full_passes(session) == 0
    await session.commit()
    row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "running"

    pending.state = "deferred"
    await session.commit()

    assert await close_drained_full_passes(session) == 1
    await session.commit()
    await session.refresh(row)
    assert row.status == "ok"
    assert row.deferred == 1


async def test_a_job_created_before_the_run_started_does_not_hold_it_open(session):
    """Window attribution's stated edge, pinned rather than left implicit: a
    job already pending when the pass began belongs to whatever created it.
    enqueue_batch's ON CONFLICT DO NOTHING skips such an item entirely, so
    waiting on it would be waiting on work this run never queued."""
    session.add(Job(kind="process_item", payload={}, state="pending"))
    await session.commit()
    # The run opens AFTER that job exists.
    await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    await session.commit()

    assert await close_drained_full_passes(session) == 1


async def test_a_pass_past_the_ceiling_is_closed_as_timed_out(session):
    """C2's hard ceiling. Without it a single stuck pending job -- a worker
    killed mid-claim past the reclaim window, a kind nothing handles -- leaves
    a row `running` forever and the chart shows a pass that never ends."""
    run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    session.add(Job(kind="process_item", payload={}, state="pending"))
    await session.commit()
    await session.execute(
        text(
            "UPDATE runs SET started_at = now() - make_interval(secs => :secs)"
        ),
        {"secs": FULL_PASS_CEILING_SECONDS + 60},
    )
    await session.commit()

    assert await close_drained_full_passes(session) == 1
    await session.commit()

    row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "timed_out"
    assert row.processed is not None
    # M-1: a timed-out row's served sentence must not say it drained -- that
    # is the one case the word is false.
    assert row.detail.startswith("timed out: ")
    assert not row.detail.startswith("drained:")


async def test_a_scheduled_run_is_never_closed_by_the_drain_watcher(session):
    """The watcher's WHERE clause is `kind = 'full_pass'`. A scheduled run has
    its own close at its own boundary, and a second closer would race it."""
    run_id = await open_run(session, kind="scheduled", name="demo")
    await session.commit()

    assert await close_drained_full_passes(session) == 0
    await session.commit()

    row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "running"


async def test_an_older_still_draining_full_pass_blocks_a_younger_one_from_closing(
    session,
):
    """I-1: `open_runs` is oldest-first and the loop `break`s at the first row
    still draining rather than `continue`ing past it, so a younger row with
    nothing outstanding of its own does not close ahead of an older row that
    is still in flight. Only the older row has an outstanding job here --
    that is what makes `continue` and `break` diverge: the pre-fix `continue`
    would skip the older row and let this younger one close on this very
    tick. They close together instead, on the same drain, which is what
    `api/routes.py`'s docstring promises a second press does."""
    older_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    older_job = Job(kind="process_item", payload={}, state="pending")
    session.add(older_job)
    await session.commit()

    younger_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    await session.commit()
    # No job created after younger's started_at -- only the older row has
    # outstanding work.

    assert await close_drained_full_passes(session) == 0
    await session.commit()

    rows = {row.id: row for row in await _rows(session, name=FULL_PASS_NAME)}
    assert rows[older_id].status == "running"
    assert rows[younger_id].status == "running"

    older_job.state = "done"
    await session.commit()

    assert await close_drained_full_passes(session) == 2
    await session.commit()

    rows = {row.id: row for row in await _rows(session, name=FULL_PASS_NAME)}
    assert rows[older_id].status == "ok"
    assert rows[younger_id].status == "ok"
    # Same tick, same window semantics: both closed against the same `now`.
    assert rows[older_id].finished_at == rows[younger_id].finished_at


async def test_a_zero_queue_second_press_does_not_close_before_the_first(session):
    """I-1's concrete failure mode: a second press while the first pass is
    still draining can enqueue NOTHING at all (`enqueue_batch`'s ON CONFLICT
    DO NOTHING against every item the first pass already claimed). Evaluated
    alone that younger row looks drained on its first tick -- no
    `process_item` job was created at or after ITS `started_at` -- and must
    not close while the real, older pass is still open beside it."""
    older_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    older_job = Job(kind="process_item", payload={}, state="pending")
    session.add(older_job)
    await session.commit()

    younger_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    await session.commit()
    # No job created after younger's started_at -- the zero-queue press.

    assert await close_drained_full_passes(session) == 0
    await session.commit()

    rows = {row.id: row for row in await _rows(session, name=FULL_PASS_NAME)}
    assert rows[older_id].status == "running"
    assert rows[younger_id].status == "running"

    older_job.state = "done"
    await session.commit()

    assert await close_drained_full_passes(session) == 2
    await session.commit()

    rows = {row.id: row for row in await _rows(session, name=FULL_PASS_NAME)}
    assert rows[older_id].status == "ok"
    assert rows[younger_id].status == "ok"


async def test_closing_full_passes_trims_full_pass_history_to_the_keep_bound(session):
    """I-2: the watcher's poll loop runs regardless of `scheduler.enabled`,
    and so does the writer it must bound (`POST /api/full-pass`), so the
    close itself -- not the gated cleanup pass -- is what keeps this bound."""
    for _ in range(RUN_HISTORY_KEEP + 1):
        run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
        await close_run(session, run_id, status="ok", detail="x")
    await session.commit()
    assert len(await _rows(session, name=FULL_PASS_NAME)) == RUN_HISTORY_KEEP + 1

    await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    await session.commit()

    assert await close_drained_full_passes(session) == 1
    await session.commit()

    assert len(await _rows(session, name=FULL_PASS_NAME)) == RUN_HISTORY_KEEP


async def test_the_closed_windows_collector_matches_what_the_call_closed(session):
    """The out-parameter's whole contract, asserted rather than assumed: one
    (started_at, finished_at) pair per row THIS call closed, so
    len(closed_windows) is the return value and roadmap row 236's digest counts
    the same interval the seven count columns were taken over. A collector that
    appended a row it did not close, or missed one it did, would send a digest
    describing a window the `runs` row does not."""
    run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
    session.add(Job(kind="process_item", payload={}, state="done"))
    await session.commit()

    windows: list = []
    assert await close_drained_full_passes(session, closed_windows=windows) == 1
    await session.commit()

    row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert len(windows) == 1
    assert windows[0] == (row.started_at, row.finished_at)


async def test_closing_nothing_appends_nothing(session):
    windows: list = []
    assert await close_drained_full_passes(session, closed_windows=windows) == 0
    assert windows == []
