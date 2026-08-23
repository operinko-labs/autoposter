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
from datetime import timedelta

from sqlalchemy import func, select

from autoposter.db.models import EventLog, Job, ScheduledRun

# jobs.state values (see db/models.py's Job docstring); always reported even
# when zero, so an empty database returns zeroed counts rather than an
# incomplete dict.
JOB_STATES = ("pending", "running", "done", "failed", "parked", "dismissed")


async def status_snapshot(session, config, scheduler_intervals: dict) -> dict:
    """The body of ``GET /api/status``: queue counts, worker count and the
    scheduled-job table."""
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
