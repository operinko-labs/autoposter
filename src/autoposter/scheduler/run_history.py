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

from sqlalchemy import func, insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import Run

# How many rows per job name the cleanup pass keeps (facts C6). Chosen as a
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

# Job names whose passes must record NO run history (fix round 1, Critical).
# `stale_job_reclaim` is the one job `app.py` registers unconditionally
# (app.py:366) rather than behind `scheduler.enabled` -- it runs every
# STALE_RECLAIM_INTERVAL_SECONDS (five minutes) regardless of that switch,
# while `trim_run_history` only ever runs from inside the cleanup pass, which
# IS gated on `scheduler.enabled` (app.py:383). Recording stale_job_reclaim's
# passes would grow this table forever, untrimmed, in that first-class
# supported configuration -- exactly what C6 exists to prevent. It is also
# not an operator-visible pass: nothing serves its history the way the other
# jobs' rows are meant to be read. Consulted in `scheduler/core.py`'s
# `_maybe_run`, before `open_run` is even called, so an unrecorded name never
# gets a `close_run` call either.
UNRECORDED = frozenset({"stale_job_reclaim"})


async def open_run(session: AsyncSession, *, kind: str, name: str) -> int:
    """Record that a run has started; return its id.

    Any still-open row for the same ``name`` (``finished_at IS NULL``) is
    closed first, in this same transaction, as ``status='interrupted'`` with
    ``finished_at`` on the database clock (fix round 1, Important 2) -- a pod
    SIGKILL or crash between a prior open and its own close otherwise leaves
    that row `running` forever, since nothing else ever reconciles it. The
    next pass of the same name is the first thing to notice.

    Scoped to ``kind == "scheduled"``: full passes all share the name
    ``full_pass`` (Task 2 opens one per button press), so an unscoped WHERE
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
    session: AsyncSession, keep: int = RUN_HISTORY_KEEP
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
    """
    result = await session.execute(
        text(
            """
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
                          ) ranked
                    WHERE rank > :keep
                   )
            """
        ),
        {"keep": keep},
    )
    return result.rowcount
