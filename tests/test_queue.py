import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from autoposter.db.models import Job
from autoposter.queue.jobs import MAX_ATTEMPTS, claim, complete, enqueue, fail


async def test_enqueue_returns_a_job_id(session):
    job_id = await enqueue(session, "process_item", {"rating_key": "1"})
    assert isinstance(job_id, int)


async def test_duplicate_pending_key_is_coalesced(session):
    first = await enqueue(session, "process_item", {"rating_key": "1"}, dedupe_key="k1")
    second = await enqueue(session, "process_item", {"rating_key": "1"}, dedupe_key="k1")
    assert first is not None
    assert second is None


async def test_key_is_reusable_once_the_job_finished(session):
    first = await enqueue(session, "process_item", {}, dedupe_key="k2")
    await complete(session, first)
    second = await enqueue(session, "process_item", {}, dedupe_key="k2")
    assert second is not None


async def test_claim_marks_running_and_counts_the_attempt(session):
    await enqueue(session, "process_item", {"rating_key": "7"})
    job = await claim(session, "worker-a")
    assert job is not None
    assert job.state == "running"
    assert job.claimed_by == "worker-a"
    assert job.attempts == 1
    assert job.payload["rating_key"] == "7"


async def test_claim_ignores_jobs_that_are_not_due(session):
    await enqueue(session, "process_item", {}, delay_seconds=3600)
    assert await claim(session, "worker-a") is None


async def test_claim_returns_none_when_queue_is_empty(session):
    assert await claim(session, "worker-a") is None


async def test_two_workers_never_claim_the_same_job(session_factory):
    # A single job, claimed concurrently by two independent sessions, exercises the
    # FOR UPDATE SKIP LOCKED guarantee: exactly one claim succeeds, the other skips
    # the locked row rather than double-claiming it.
    async with session_factory() as setup:
        job_id = await enqueue(setup, "process_item", {"n": 1})
    async with session_factory() as s1, session_factory() as s2:
        first, second = await asyncio.gather(claim(s1, "worker-1"), claim(s2, "worker-2"))
    claimed = [job for job in (first, second) if job is not None]
    assert len(claimed) == 1
    assert claimed[0].id == job_id
    assert claimed[0].attempts == 1


async def test_two_workers_claim_distinct_jobs_independently(session_factory):
    async with session_factory() as setup:
        await enqueue(setup, "process_item", {"n": 1}, dedupe_key="a")
        await enqueue(setup, "process_item", {"n": 2}, dedupe_key="b")
    async with session_factory() as s1, session_factory() as s2:
        first = await claim(s1, "worker-1")
        second = await claim(s2, "worker-2")
    assert first is not None and second is not None
    assert first.id != second.id


async def test_failure_reschedules_with_backoff(session):
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    state = await fail(session, job_id, "boom")
    assert state == "pending"
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.last_error == "boom"
    # Compare against the database clock, never this process's clock: the two can
    # drift, and the queue is defined entirely in terms of the database's now().
    db_now = (await session.execute(select(func.now()))).scalar_one()
    assert job.run_after > db_now + timedelta(seconds=5)


async def test_a_job_enqueued_without_delay_is_immediately_claimable(session):
    # Regression guard for app/database clock skew: with a client-side timestamp
    # and a database clock running behind, this job would not be due yet.
    await enqueue(session, "process_item", {"rating_key": "now"})
    assert await claim(session, "worker-a") is not None


async def test_created_at_uses_the_database_clock(session):
    # Regression guard for finding 1: created_at must be a server-side default
    # (func.now()), not one computed in this process, because the app clock and
    # the database clock can drift by several seconds on this machine.
    job_id = await enqueue(session, "process_item", {})
    db_now = (await session.execute(select(func.now()))).scalar_one()
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert abs((job.created_at - db_now).total_seconds()) < 1


async def test_fail_clears_claim_metadata(session):
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await fail(session, job_id, "boom")
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.claimed_by is None
    assert job.claimed_at is None


async def _make_due_now(session, job_id: int) -> None:
    """Reset a job to pending and due, using the database clock."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.state = "pending"
    job.run_after = (await session.execute(select(func.now()))).scalar_one()
    await session.commit()


async def test_job_parks_after_max_attempts(session):
    job_id = await enqueue(session, "process_item", {})
    for _ in range(MAX_ATTEMPTS - 1):
        await _make_due_now(session, job_id)
        await claim(session, "worker-a")
        assert await fail(session, job_id, "boom") == "pending"
    await _make_due_now(session, job_id)
    await claim(session, "worker-a")
    assert await fail(session, job_id, "boom") == "parked"


async def test_complete_marks_done(session):
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await complete(session, job_id)
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.state == "done"
