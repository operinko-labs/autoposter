"""The `runs` history table's only writer (roadmap row 53).

One module rather than three call sites writing the same table, because the
three boundaries are in three packages -- ``scheduler/core.py`` (a scheduled
pass), ``api/routes.py`` (a full pass opening), and the scheduler's poll loop
(a full pass closing) -- and the rules about what may be stored are the same
at all three. It lives under ``scheduler/`` because the scheduler owns the
row's lifecycle; ``api/`` importing it is the direction
``api/facts_backfill.py`` already takes to ``scheduler/jobs.py``.

**Nothing here commits.** Every function takes the caller's session and leaves
the transaction to it -- which is what lets ``run_full_pass`` open a run row
and enqueue its jobs in ONE transaction, so both timestamps resolve to the
same ``transaction_timestamp()`` and every job it creates is inside its own
run's window by construction.

Row 213: a string reaches this module only as ``detail``, and only from a
caller that has already narrowed it. Nothing here reads ``jobs.last_error``,
formats an exception, or touches a path.
"""

from datetime import datetime

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import Job, Render, Run

# How many rows per job name the cleanup pass keeps. Chosen as a
# history depth rather than a byte budget: 500 passes is about five years of a
# weekly job, a month and a half of an hourly one, and a day and a half of the
# five-minutely stale_job_reclaim -- the cheapest job being the one that
# rolls, which is the right way round. A plain constant, not a config field,
# for STALE_RECLAIM_INTERVAL_SECONDS's reason: this bounds a table, it is not
# an operator's tuning decision.
RUN_HISTORY_KEEP = 500

# The single width every served detail string is cut to, matching
# scheduled_runs.last_detail's own truncation in scheduler/core.py.
_DETAIL_WIDTH = 2000

# Job names whose passes must record NO run history.
# `stale_job_reclaim` is the one job `app.py` registers unconditionally
# (app.py:366) rather than behind `scheduler.enabled` -- it runs every
# STALE_RECLAIM_INTERVAL_SECONDS (five minutes) regardless of that switch,
# while `trim_run_history`'s only *unconditional* caller is the cleanup pass,
# which IS gated on `scheduler.enabled` (app.py:383) -- the drain watcher's
# own call, scoped to `kind="full_pass"`, would never bound a
# stale_job_reclaim row anyway. Recording stale_job_reclaim's
# passes would grow this table forever, untrimmed, in that first-class
# supported configuration -- exactly what the keep bound exists to prevent.
# It is also
# not an operator-visible pass: nothing serves its history the way the other
# jobs' rows are meant to be read. Consulted in `scheduler/core.py`'s
# `_maybe_run`, before `open_run` is even called, so an unrecorded name never
# gets a `close_run` call either.
UNRECORDED = frozenset({"stale_job_reclaim"})


async def open_run(session: AsyncSession, *, kind: str, name: str) -> int:
    """Record that a run has started; return its id.

    Any still-open row for the same ``name`` (``finished_at IS NULL``) is
    closed first, in this same transaction, as ``status='interrupted'`` with
    ``finished_at`` on the database clock -- a pod
    SIGKILL or crash between a prior open and its own close otherwise leaves
    that row `running` forever, since nothing else ever reconciles it. The
    next pass of the same name is the first thing to notice.

    Scoped to ``kind == "scheduled"``: full passes all share the name
    ``full_pass`` (one row per button press), so an unscoped WHERE
    would orphan the previous open full pass as ``interrupted`` on a second
    press, before its counts are ever stamped. A full pass has its own closer
    (the drain-watcher) and must never be closed here.

    ``started_at`` and ``status`` come from the column defaults rather than
    from Python: the timestamp must be the database clock (every other
    timestamp in this schema is), and 'running' is the only correct status for
    a row that exists because a run is in flight.
    """
    await session.execute(
        update(Run)
        .where(Run.kind == "scheduled", Run.name == name, Run.finished_at.is_(None))
        .values(finished_at=func.now(), status="interrupted")
    )
    result = await session.execute(
        insert(Run).values(kind=kind, name=name).returning(Run.id)
    )
    return result.scalar_one()


async def close_run(
    session: AsyncSession, run_id: int, *, status: str, detail: str
) -> None:
    """Stamp a run's end, outcome and detail.

    ``detail`` is truncated here rather than at each call site, so there is
    one width and one place to change it. It is a COPY of a string the caller
    has already narrowed for a served surface (row 213) -- this module never
    derives one.
    """
    await session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(finished_at=func.now(), status=status, detail=detail[:_DETAIL_WIDTH])
    )


async def trim_run_history(
    session: AsyncSession,
    keep: int = RUN_HISTORY_KEEP,
    *,
    kind: str | None = None,
    name: str | None = None,
) -> int:
    """Delete all but the newest ``keep`` rows per ``name``; return the count.

    One statement, in SQL: a window function ranks each name's rows newest
    first and the delete takes everything past the cap. Reading the ids into
    Python first would be a second round trip and a race with whatever is
    writing runs at the same moment.

    ``ORDER BY started_at DESC, id DESC`` rather than ``started_at`` alone:
    two runs of the same job can share a timestamp to the microsecond on a
    fast pass, and a tie the ranking breaks arbitrarily makes this delete a
    different row on every call.

    ``kind``/``name`` scope the delete to one recorded name -- the
    drain-watcher's own call passes both so that trimming
    what it just closed never re-ranks every scheduled job's rows too.
    Omitted, as the cleanup pass omits them, the delete covers the whole
    table.
    """
    where_sql = ""
    params: dict = {"keep": keep}
    if kind is not None:
        where_sql += " WHERE kind = :kind"
        params["kind"] = kind
    if name is not None:
        where_sql += (" AND" if where_sql else " WHERE") + " name = :name"
        params["name"] = name

    result = await session.execute(
        text(
            f"""
            DELETE FROM runs
             WHERE id IN (
                   SELECT id
                     FROM (
                          SELECT id,
                                 row_number() OVER (
                                     PARTITION BY name
                                     ORDER BY started_at DESC, id DESC
                                 ) AS rank
                            FROM runs
                            {where_sql}
                          ) ranked
                    WHERE rank > :keep
                   )
            """
        ),
        params,
    )
    return result.rowcount


# The name every full-pass row carries. A literal rather than a job name,
# because a full pass is not a scheduled job and deliberately is not in
# api/routes.py's SCHEDULED_JOB_NAMES -- the dashboard's Run-now button must
# not offer it.
FULL_PASS_NAME = "full_pass"

# How long an open full pass may stay open before the watcher gives up on it.
# 24 hours because the operator's own measured pass is ~3.5 h for 16k items at
# zero composites, and a library four times that size on a bad day should not
# be declared timed out while it is genuinely working. What this bounds is the
# other case: one pending job that nothing will ever claim -- a worker killed
# outside the reclaim window, a payload no handler decodes -- which would
# otherwise leave the row `running` forever and put a bar of unbounded height
# on the duration chart.
FULL_PASS_CEILING_SECONDS = 24 * 60 * 60

# The four artifact kinds, in the order the counts report them. Kept in
# lockstep with api/stats.py's ART_KINDS: these two tuples must describe the
# same four columns or a chart legend and a storage widget disagree about what
# an art kind is.
_ART_KINDS = ("poster", "season_poster", "background", "title_card")

# The job states each count column reads. `parked` is deliberately absent:
# a parked job is an operator matter the Action Center owns, and a
# run rollup is not a surface anyone can act on it from.
#
# `processed` reads TWO states: `done_with_warnings` is a finished job whose
# item was processed exactly like a `done` one -- only a server it touched is
# still owed something -- so counting it anywhere else would have a pass read
# as having done less work than it did, or (worse) as having failed items it
# did not.
_JOB_STATE_COLUMNS = {
    "processed": ("done", "done_with_warnings"),
    "failed": ("failed",),
    "deferred": ("deferred",),
}


async def window_counts(
    session: AsyncSession, started_at: datetime, finished_at: datetime
) -> dict:
    """What the worker pool finished between two instants.

    **This is window attribution, not causation.** No run id threads through a
    ``process_item`` job (see this module's docstring and the recon), so what
    these numbers describe is a half-open interval ``[started_at,
    finished_at)`` and nothing more. For a full pass the interval IS the
    pass's drain, which is what makes it worth serving; for anything else it
    is whatever happened to be running, which is why nothing else stamps it.

    Two grouped round trips, the ``storage_snapshot`` shape: one scan of
    ``renders`` grouped by ``art_kind``, one of ``jobs`` grouped by ``state``,
    each with the window as its predicate. Every value is passed through
    ``int()`` as a defensive zero-fill guard for asyncpg's ``count()`` ->
    Python ``int`` mapping, and to keep the pattern consistent should a
    ``sum``/``avg`` column -- which asyncpg CAN decode as a ``Decimal`` -- ever
    join these two.

    All seven keys are always present and zero-filled (``jobs_by_state``'s
    rule): a mapping must never point at a field that vanished because this
    pass composited no title cards.
    """
    rendered = (
        await session.execute(
            select(Render.art_kind, func.count())
            .where(
                Render.rendered_at >= started_at,
                Render.rendered_at < finished_at,
            )
            .group_by(Render.art_kind)
        )
    ).all()

    by_state = (
        await session.execute(
            select(Job.state, func.count())
            .where(
                # Only the kind the worker pool actually dispatches today
                # (app.py's `handlers`). Naming it keeps the count meaning the
                # same thing on the day a second kind lands.
                Job.kind == "process_item",
                Job.updated_at >= started_at,
                Job.updated_at < finished_at,
            )
            .group_by(Job.state)
        )
    ).all()

    counts = {f"rendered_{kind}": 0 for kind in _ART_KINDS}
    counts.update({column: 0 for column in _JOB_STATE_COLUMNS})

    for art_kind, total in rendered:
        # An art_kind outside the four (nothing writes one) is simply not
        # counted rather than inventing a key from stored data -- api/stats.py
        # takes the same position, for the same reason.
        key = f"rendered_{art_kind}"
        if key in counts:
            counts[key] = int(total)

    seen = {state: int(total) for state, total in by_state}
    for column, states in _JOB_STATE_COLUMNS.items():
        counts[column] = sum(seen.get(state, 0) for state in states)

    return counts


async def close_drained_full_passes(
    session: AsyncSession,
    *,
    closed_windows: list[tuple[datetime, datetime]] | None = None,
) -> int:
    """Close every open full pass that has drained, or run out of time.

    The drain-watcher. "Drained" is: no ``process_item`` job created at or
    after the run's ``started_at`` is still ``pending`` or ``running``.
    ``deferred`` is excluded on purpose -- it is a wait, not work in flight
    (``queue/jobs.py``'s ``DEFER_INTERVAL_SECONDS`` is six hours with no
    attempt cap), so a deferred row is COUNTED as deferred and does not hold
    the run open.

    Two known and accepted imprecisions, stated rather than hidden:

    * a webhook arriving mid-drain creates a ``process_item`` job inside the
      window, so it extends the run and lands in its counts;
    * an item whose job was already pending when the pass began is skipped by
      ``enqueue_batch``'s ``ON CONFLICT DO NOTHING``, so the pass neither waits
      for it nor counts it as its own.

    Both follow from window attribution, which is the honest mechanism
    available; an id on the job is not, at any price this row can pay.

    ``open_runs`` is walked oldest-first and stops at the
    first row still draining: a second press while the first pass's jobs are
    still in flight can enqueue nothing at all (``enqueue_batch``'s ``ON
    CONFLICT DO NOTHING``), and without this ordering that jobless younger row
    would close on the very next tick as a one-minute "pass", stealing a slice
    of the older pass's still-in-progress counts. Stopping rather than
    skipping means every row younger than an open one waits for it, and they
    all close together on the same drain -- which is what
    ``api/routes.py``'s docstring promises a second press does. A row past
    its own ceiling still closes as ``timed_out`` even while older siblings
    are draining, since it is not waiting on anything at that point.

    ``closed_windows``, when given, is appended one ``(started_at,
    finished_at)`` pair per row THIS call closed, in the order it closed them,
    so ``len(closed_windows)`` always equals the return value. An
    out-parameter rather than a widened return type because the return value is
    a documented count with twelve assertions against it in
    ``tests/test_run_history.py`` and this function's contract is "how many did
    you close". Roadmap row 236's digest needs the windows so that it counts
    the SAME interval the seven attribution columns above were taken over;
    re-deriving them with a second SELECT would be guessing at which rows this
    call closed rather than being told.

    Returns how many rows it closed. Does not commit.
    """
    open_runs = (
        await session.execute(
            select(Run.id, Run.started_at)
            .where(Run.kind == "full_pass", Run.finished_at.is_(None))
            .order_by(Run.id)
        )
    ).all()
    if not open_runs:
        return 0

    now = (await session.execute(select(func.now()))).scalar_one()
    closed = 0
    for run_id, started_at in open_runs:
        outstanding = (
            await session.execute(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.kind == "process_item",
                    Job.state.in_(("pending", "running")),
                    Job.created_at >= started_at,
                )
            )
        ).scalar_one()
        expired = (now - started_at).total_seconds() >= FULL_PASS_CEILING_SECONDS
        if outstanding and not expired:
            # Still draining, and not yet timed out: stop here rather than
            # continue, so no younger row closes ahead of this one.
            break

        status = "timed_out" if outstanding else "ok"
        counts = await window_counts(session, started_at, now)
        await session.execute(
            update(Run)
            # `finished_at IS NULL` (M-3): a second replica's tick that raced
            # this one to the same row must not overwrite the first writer's
            # numbers with its own, later window's.
            .where(Run.id == run_id, Run.finished_at.is_(None))
            .values(
                finished_at=now,
                status=status,
                # Counts only, never a job's last_error (row 213). The same
                # three numbers the columns hold, so the served sentence and
                # the served fields can never disagree. "timed out" rather
                # than "drained" on that path (M-1): the ceiling fired
                # precisely because the pass had NOT drained.
                detail=(
                    ("timed out: " if status == "timed_out" else "drained: ")
                    + "{processed} processed, {failed} failed, {deferred} deferred"
                ).format(**counts)[:_DETAIL_WIDTH],
                **counts,
            )
        )
        closed += 1
        if closed_windows is not None:
            # The same two instants window_counts was just handed, so the
            # digest and the stored columns can never describe two different
            # intervals for one pass.
            closed_windows.append((started_at, now))

    if closed:
        # The bound holds for full-pass rows here rather than through the
        # (conditionally-registered) cleanup job: the
        # watcher runs regardless of `scheduler.enabled`, and so does the
        # writer it is bounding (`POST /api/full-pass`). Scoped to this
        # kind/name so trimming what was just closed never re-ranks every
        # scheduled job's rows too, and in this same transaction so the
        # bound is never left to a separate, possibly-skipped commit.
        await trim_run_history(session, kind="full_pass", name=FULL_PASS_NAME)
    return closed
