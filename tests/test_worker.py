import asyncio
from dataclasses import asdict

import pytest
from sqlalchemy import func, select, text

from autoposter.db.models import Job
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound
from autoposter.queue.jobs import MAX_ATTEMPTS, enqueue
from autoposter.queue.worker import run_once


async def test_run_once_processes_a_due_job(session):
    handled = []

    async def handler(session_, intent):
        handled.append(intent)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    assert await run_once(session, "worker-1", handler) is True
    assert handled[0].tmdb_id == 1
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "done"


async def test_run_once_returns_false_when_nothing_is_due(session):
    async def handler(session_, intent):
        raise AssertionError("should not be called")

    assert await run_once(session, "worker-1", handler) is False


async def test_item_not_found_reschedules_rather_than_failing(session):
    async def handler(session_, intent):
        raise ItemNotFound("plex has not scanned yet")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await run_once(session, "worker-1", handler)
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "pending"
    assert "scanned" in job.last_error


async def test_unexpected_errors_also_reschedule(session):
    async def handler(session_, intent):
        raise RuntimeError("provider exploded")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=2)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await run_once(session, "worker-1", handler)
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "pending"
    assert "exploded" in job.last_error


async def test_unknown_job_kinds_are_parked_not_retried(session):
    async def handler(session_, intent):
        raise AssertionError("should not be called")

    await enqueue(session, "not_a_real_kind", {})
    await run_once(session, "worker-1", handler)
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "parked"


async def test_cancelled_handler_releases_job_without_consuming_an_attempt(session):
    # A cancelled task (graceful shutdown) must not be treated like a job failure:
    # the job goes straight back to pending, keeps its original attempt count, and
    # the CancelledError must keep propagating so the worker task actually stops.
    async def handler(session_, intent):
        raise asyncio.CancelledError()

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=3)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)

    with pytest.raises(asyncio.CancelledError):
        await run_once(session, "worker-1", handler)

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "pending"
    assert job.attempts == 0
    assert job.claimed_by is None
    assert job.claimed_at is None


async def _make_due_now(session, job_id: int) -> None:
    """Reset a job to pending and due, using the database clock."""
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.state = "pending"
    job.run_after = (await session.execute(select(func.now()))).scalar_one()
    await session.commit()


async def test_item_not_found_survives_more_attempts_than_a_generic_failure(session):
    # Finding 2: config.plex.resolve_max_attempts gives waiting-on-Plex its own,
    # larger attempt budget. It is threaded onto the exception (mirroring what
    # app.py's _handle_intent does), not passed to run_once directly.
    async def not_found_handler(session_, intent):
        exc = ItemNotFound("plex has not scanned yet")
        exc.max_attempts = MAX_ATTEMPTS + 3
        raise exc

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=10)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    for _ in range(MAX_ATTEMPTS):
        await _make_due_now(session, job_id)
        await run_once(session, "worker-1", not_found_handler)

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    # A generic failure would have parked by now (see test_job_parks_after_max_attempts
    # in test_queue.py); the larger budget keeps this one retrying.
    assert job.state == "pending"

    async def generic_handler(session_, intent):
        raise RuntimeError("provider exploded")

    intent2 = RenderIntent(kind="movie", title="Dune", tmdb_id=11)
    job_id2 = await enqueue(
        session, "process_item", asdict(intent2), dedupe_key=intent2.dedupe_key
    )
    for _ in range(MAX_ATTEMPTS):
        await _make_due_now(session, job_id2)
        await run_once(session, "worker-1", generic_handler)

    job2 = (await session.execute(select(Job).where(Job.id == job_id2))).scalar_one()
    await session.refresh(job2)
    assert job2.state == "parked"


async def test_db_error_in_handler_reschedules_instead_of_stranding_at_running(session):
    # Finding 3: a handler that fails with a database error (not a plain Python
    # exception) leaves the session in a failed transaction. fail() issues a
    # SELECT, which raises PendingRollbackError on such a session unless it is
    # rolled back first — leaving the job stuck at 'running' until the 900s
    # reclaim sweep instead of being rescheduled.
    async def handler(session_, intent):
        await session_.execute(text("SELECT 1/0"))

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=12)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)

    await run_once(session, "worker-1", handler)

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "pending"
    assert job.claimed_by is None
    assert "division by zero" in job.last_error.lower()
