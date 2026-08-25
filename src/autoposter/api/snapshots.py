"""The database reads behind ``GET /api/status`` and ``GET /api/events``.

Factored out of the request handlers because the dashboard stream's
``StatusBroadcaster`` (api/dashboard_stream.py) has to build the same two
payloads on every poll. One implementation rather than two, so a change to
either shape reaches the REST response and the stream together -- and so the
events shape's deliberate omission of ``payload`` (it holds whole webhook
bodies and can carry tokens from the sending service) cannot be widened in
one place while the other stays narrow.

Neither helper opens a session or caps a limit: both are the caller's, so the
REST handlers keep their own query-parameter validation and the broadcaster
keeps its single session per poll.
"""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from autoposter.db.models import EventLog, Job, ScheduledRun

# jobs.state values (see db/models.py's Job docstring); always reported even
# when zero, so an empty database returns zeroed counts rather than an
# incomplete dict.
JOB_STATES = ("pending", "running", "done", "failed", "parked", "dismissed")


def _run_status(row: ScheduledRun, started_at: datetime) -> str | None:
    """The dashboard's derived ``status`` for one scheduled-job row.

    A run is *shaped like* in-progress when it has started and either never
    finished or its last finish is older than its last start -- the same
    "still going" test the dashboard could not previously make, since a
    multi-minute run logs nothing between claim and finish and the row's
    ``last_status``/``last_finished_at`` still show the *previous* run's
    outcome the whole time it is going.

    That shape alone cannot tell a live run from one whose process died
    mid-run -- a killed pod never gets to write ``last_finished_at``, so the
    row looks eternally "still going". ``started_at`` (the *current*
    process's boot instant) resolves it: a start at or after boot is this
    process's own claim, still running; a start before boot cannot belong to
    this process, so whatever claimed it is gone and the run died with it --
    a proof from the boot instant, not a guess from how long it has been.

    Anything not shaped like in-progress reports the recorded
    ``last_status`` as-is -- ``"ok"``, ``"failed"``, or ``None`` for a row
    that has never run, which the frontend already renders as such.

    ``started >= started_at`` only proves what it claims to when both sides
    come from the same clock: ``started`` (``row.last_started_at``) is
    stamped by Postgres's own ``now()`` (``scheduler/core.py``'s
    ``claim_due``), so ``started_at`` must be a database-clock reading too,
    never ``datetime.now()`` -- see ``app.py``'s lifespan, which reads it via
    ``SELECT now()`` for exactly this reason. A Python-clock ``started_at``
    compared against a Postgres-clock column would mislabel a healthy run as
    ``"interrupted"`` for however long the two hosts' clocks disagree.
    """
    started = row.last_started_at
    finished = row.last_finished_at
    if started is not None and (finished is None or finished < started):
        return "running" if started >= started_at else "interrupted"
    return row.last_status


async def status_snapshot(
    session, config, scheduler_intervals: dict, started_at: datetime
) -> dict:
    """The body of ``GET /api/status``: queue counts, worker count and the
    scheduled-job table.

    ``started_at`` is this process's boot instant (``app.state.started_at``),
    used only to derive each scheduled job's ``status`` -- see ``_run_status``.
    It must be a database-clock reading, not a Python-clock one -- see
    ``_run_status``'s docstring for why.
    """
    # GROUP BY in SQL rather than fetching every job row and counting in
    # Python -- the jobs table is the hot one at this library's size.
    state_counts = (
        await session.execute(select(Job.state, func.count()).group_by(Job.state))
    ).all()
    jobs_by_state = dict.fromkeys(JOB_STATES, 0)
    for state, count in state_counts:
        jobs_by_state[state] = count

    processed_last_24h = (
        await session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.state == "done",
                Job.updated_at >= func.now() - timedelta(hours=24),
            )
        )
    ).scalar_one()

    scheduled_rows = (
        (await session.execute(select(ScheduledRun).order_by(ScheduledRun.name)))
        .scalars()
        .all()
    )

    # Cadence is not in the database -- it lives on the in-memory scheduler
    # Job dataclass, published by create_app (empty) and filled by the
    # lifespan with whatever it actually registered. Merging by name here
    # means a row left behind by a job the current configuration no longer
    # registers reports a null interval rather than a stale one. The frontend
    # computes the next run from this plus last_started_at.
    return {
        "jobs_by_state": jobs_by_state,
        "workers": config.workers,
        "processed_last_24h": processed_last_24h,
        "scheduled_jobs": [
            {
                "name": row.name,
                "last_started_at": row.last_started_at,
                "last_finished_at": row.last_finished_at,
                "last_status": row.last_status,
                "last_detail": row.last_detail,
                "interval_seconds": scheduler_intervals.get(row.name),
                "status": _run_status(row, started_at),
            }
            for row in scheduled_rows
        ],
    }


async def events_snapshot(session, limit: int) -> list[dict]:
    """The ``events`` list of ``GET /api/events``, newest first.

    ``limit`` is used as given: the REST handler caps it against
    MAX_EVENTS_LIMIT before calling, the broadcaster passes its own constant.
    """
    # payload is never selected -- it holds whole webhook bodies and can
    # carry tokens from the sending service.
    rows = (
        await session.execute(
            select(
                EventLog.source, EventLog.event_type, EventLog.outcome, EventLog.received_at
            )
            .order_by(EventLog.received_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "source": row.source,
            "event_type": row.event_type,
            "outcome": row.outcome,
            "received_at": row.received_at,
        }
        for row in rows
    ]
