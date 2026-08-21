"""Scheduler bookkeeping and claiming."""
import asyncio
import inspect

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


async def test_only_one_of_two_concurrent_replicas_claims_the_same_job(session_factory):
    """The branch's headline safety property, asserted rather than reasoned
    about: two replicas racing for one due job, and exactly one wins.

    An ``asyncio.Barrier`` rather than sleeps, and each replica warms its
    connection *before* waiting on it: a session acquires its connection
    lazily, so without the warm-up the second replica's first statement can
    land after the first has already committed and the two never actually
    overlap. Which replica wins is not asserted -- that is a race, and should
    be -- only that exactly one does.

    A claim that reads the row, decides in Python and then writes passes
    every other test in this file and fails this one with two ``True``s: two
    replicas running the same pass, double-writing to Plex.
    """
    job = _job(name="contended", interval=3600)

    # Create the row and leave it due, so both replicas race on the claim
    # itself rather than on the first-time insert.
    async with session_factory() as setup:
        await claim_due(setup, job)
        await setup.execute(text("UPDATE scheduled_runs SET last_started_at = NULL"))
        await setup.commit()

    both_ready = asyncio.Barrier(2)

    async def replica() -> bool:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            await both_ready.wait()
            claimed = await claim_due(session, job)
            await session.commit()
            return claimed

    results = await asyncio.gather(replica(), replica())

    assert sorted(results) == [False, True], (
        "exactly one replica must claim a due job; got %r" % (results,)
    )
    async with session_factory() as session:
        row = (
            await session.execute(
                select(ScheduledRun).where(ScheduledRun.name == "contended")
            )
        ).scalar_one()
    assert row.last_started_at is not None


async def test_the_claim_is_documented_as_not_being_a_lease():
    """A job whose runtime exceeds its own interval can be claimed again by a
    second replica: ``last_started_at`` is committed immediately and the row
    lock goes with it. Harmless at 24-hour and 7-day cadences, so the
    limitation is recorded rather than fixed -- but it must stay recorded, or
    the next person to add a sub-hour cadence will not know."""
    doc = inspect.getdoc(claim_due) or ""
    assert "not a lease" in doc.lower(), (
        "claim_due must document that the claim is not a lease and what that "
        "costs a job that outruns its own interval"
    )


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
