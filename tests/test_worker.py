import asyncio
from dataclasses import asdict

import pytest
import requests
from sqlalchemy import func, select, text

from autoposter.db.models import Job
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound
from autoposter.queue.jobs import MAX_ATTEMPTS, enqueue
from autoposter.queue.worker import run_once, run_worker


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


async def test_cancelled_mid_db_operation_still_releases_and_propagates(session):
    # Finding 2: if cancellation lands mid-database-operation, the session is
    # left in a failed transaction. release()'s SELECT would raise
    # PendingRollbackError instead of releasing the job unless the
    # CancelledError branch rolls back first, exactly like its ItemNotFound
    # and generic-exception siblings.
    async def handler(session_, intent):
        try:
            await session_.execute(text("SELECT 1/0"))
        except Exception:
            pass  # the session is now in a failed transaction, same as a real DB error
        raise asyncio.CancelledError()

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=21)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)

    with pytest.raises(asyncio.CancelledError):
        await run_once(session, "worker-1", handler)

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "pending"
    assert job.claimed_by is None


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


async def test_plex_connection_error_survives_more_attempts_than_a_generic_failure(session):
    # Finding 1: a Plex connectivity failure (surfacing from _LazyPlexServer's
    # connect attempt as a requests.exceptions.ConnectionError/Timeout, tagged
    # by app.py's _handle_intent) must get the same larger, configurable
    # attempt budget as ItemNotFound — not the generic MAX_ATTEMPTS cap that
    # parks a job after ~450 seconds of backoff.
    async def connection_error_handler(session_, intent):
        exc = requests.exceptions.ConnectionError("Plex unreachable")
        exc.max_attempts = MAX_ATTEMPTS + 3
        raise exc

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=20)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    for _ in range(MAX_ATTEMPTS):
        await _make_due_now(session, job_id)
        await run_once(session, "worker-1", connection_error_handler)

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    # A generic failure would have parked by now; the larger budget keeps
    # this one retrying instead of silently and permanently dropping the job.
    assert job.state == "pending"


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


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    async def poll():
        while not await predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout=timeout)


async def test_run_worker_skips_claiming_while_unhealthy_and_resumes_on_recovery(
    session_factory,
):
    # PlexHealth's liveness gate: while the server is known unhealthy, a job
    # must stay pending with attempts untouched, then be claimed normally as
    # soon as health recovers.
    handled = []

    async def handler(session_, intent):
        handled.append(intent)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=30)
    async with session_factory() as setup_session:
        job_id = await enqueue(
            setup_session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key
        )

    healthy = {"value": False}
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        run_worker(
            "worker-1", session_factory, handler, stop_event, is_healthy=lambda: healthy["value"]
        )
    )
    try:
        await asyncio.sleep(0.2)
        async with session_factory() as session:
            job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
            assert job.state == "pending"
            assert job.attempts == 0
        assert handled == []

        healthy["value"] = True

        async def is_done():
            async with session_factory() as session:
                job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
                return job.state == "done"

        await _wait_until(is_done)
        assert handled[0].tmdb_id == 30
    finally:
        stop_event.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_run_worker_processes_jobs_normally_when_healthy(session_factory):
    handled = []

    async def handler(session_, intent):
        handled.append(intent)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=31)
    async with session_factory() as setup_session:
        job_id = await enqueue(
            setup_session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key
        )

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        run_worker("worker-1", session_factory, handler, stop_event, is_healthy=lambda: True)
    )
    try:

        async def is_done():
            async with session_factory() as session:
                job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
                return job.state == "done"

        await _wait_until(is_done)
        assert handled[0].tmdb_id == 31
    finally:
        stop_event.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
