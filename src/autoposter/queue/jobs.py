from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import Job

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30

_CLAIM_SQL = text(
    """
    UPDATE jobs
       SET state = 'running',
           claimed_by = :worker,
           claimed_at = now(),
           attempts = attempts + 1,
           updated_at = now()
     WHERE id = (
           SELECT id
             FROM jobs
            WHERE state = 'pending'
              AND run_after <= now()
            ORDER BY run_after, id
              FOR UPDATE SKIP LOCKED
            LIMIT 1
     )
    RETURNING id
    """
)


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict,
    dedupe_key: str | None = None,
    delay_seconds: int = 0,
) -> int | None:
    """Add a job to the queue.

    When ``dedupe_key`` matches a job that is already pending, nothing is inserted
    and ``None`` is returned — that is the debounce for webhook bursts. A burst of
    events therefore produces a single pass whose delay is measured from the first
    event, which bounds latency instead of postponing work indefinitely.
    """
    # run_after is computed by Postgres, not by this process. claim() compares it
    # against the database's now(), and an app clock that drifts from the database
    # clock would otherwise make jobs run early or late by the size of the drift.
    run_after = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay_seconds)
    stmt = insert(Job).values(
        kind=kind, payload=payload, dedupe_key=dedupe_key, run_after=run_after
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["dedupe_key"], index_where=text("state = 'pending'")
        )
    result = await session.execute(stmt.returning(Job.id))
    job_id = result.scalar_one_or_none()
    await session.commit()
    return job_id


async def claim(session: AsyncSession, worker_id: str) -> Job | None:
    """Atomically take the next due job. Concurrent callers never collide."""
    result = await session.execute(_CLAIM_SQL, {"worker": worker_id})
    job_id = result.scalar_one_or_none()
    await session.commit()
    if job_id is None:
        return None
    return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def complete(session: AsyncSession, job_id: int) -> None:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.state = "done"
    job.last_error = None
    await session.commit()


async def fail(session: AsyncSession, job_id: int, error: str) -> str:
    """Reschedule with exponential backoff, or park once attempts are exhausted."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.last_error = error
    job.claimed_by = None
    job.claimed_at = None
    if job.attempts >= MAX_ATTEMPTS:
        job.state = "parked"
    else:
        job.state = "pending"
        backoff = BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1))
        # Database clock again, for the same reason as enqueue().
        job.run_after = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, backoff)
    await session.commit()
    return job.state
