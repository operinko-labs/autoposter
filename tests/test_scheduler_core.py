"""Scheduler bookkeeping and claiming."""
import asyncio

from sqlalchemy import select, text

from autoposter.db.models import ScheduledRun
from autoposter.scheduler.core import Job, Scheduler, claim_due


def _job(name="demo", interval=3600, run=None):
    async def _noop(session):
        return "ok"

    return Job(name=name, interval_seconds=interval, run=run or _noop)


async def test_a_job_is_claimable_the_first_time(session):
    assert await claim_due(session, _job()) is True


async def test_a_job_is_not_claimable_again_inside_its_interval(session):
    job = _job(interval=3600)
    assert await claim_due(session, job) is True
    assert await claim_due(session, job) is False


async def test_a_job_is_claimable_once_its_interval_has_passed(session):
    job = _job(interval=3600)
    assert await claim_due(session, job) is True
    await session.execute(
        text("UPDATE scheduled_runs SET last_started_at = now() - interval '2 hours'")
    )
    assert await claim_due(session, job) is True


async def test_claiming_records_the_start_time_from_the_database_clock(session):
    await claim_due(session, _job())
    row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_started_at is not None


async def test_two_jobs_are_tracked_independently(session):
    assert await claim_due(session, _job(name="a")) is True
    assert await claim_due(session, _job(name="b")) is True
    rows = (await session.execute(select(ScheduledRun))).scalars().all()
    assert {r.name for r in rows} == {"a", "b"}


async def test_the_scheduler_runs_a_due_job_and_records_success(session_factory):
    ran = []

    async def body(session):
        ran.append(True)
        return "did the thing"

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(run=body)], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await task

    assert ran
    async with session_factory() as session:
        row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_status == "ok"
    assert row.last_detail == "did the thing"
    assert row.last_finished_at is not None


async def test_a_failing_job_is_recorded_and_the_scheduler_survives(session_factory):
    calls = []

    async def body(session):
        calls.append(True)
        raise RuntimeError("job exploded")

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(interval=0, run=body)], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await task

    assert len(calls) > 1, "scheduler stopped after the first failure"
    async with session_factory() as session:
        row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_status == "failed"
    assert "job exploded" in row.last_detail


async def test_the_scheduler_stops_promptly_on_the_stop_event(session_factory):
    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job()], poll_seconds=30)
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_an_empty_job_list_is_harmless(session_factory):
    stop = asyncio.Event()
    task = asyncio.create_task(Scheduler(session_factory, [], poll_seconds=0.01).run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
