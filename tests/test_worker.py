import asyncio
from dataclasses import asdict

import pytest
from sqlalchemy import select

from autoposter.db.models import Job
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound
from autoposter.queue.jobs import enqueue
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
