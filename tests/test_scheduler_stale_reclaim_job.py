"""``make_stale_reclaim_job``: the periodic sweep that returns orphaned
``running`` claims to ``pending``.

Production incident: ``reclaim_stale`` (see ``queue/jobs.py``) was only ever
called once, at process startup. Two Flux deploys minutes apart orphaned a
batch of ``running`` jobs on the first restart and the second restart's boot
reclaim skipped them (their claims were younger than the 900s threshold) --
nothing ever reclaimed them again, and Cancel hung forever because
``cancel_requested`` is only honoured when an attempt ends (``queue/jobs.py``
``fail()``). These tests pin the periodic sweep that closes that gap, wired
through the real ``Scheduler`` so what is pinned is the tick actually invoking
the reclaim, not just the reclaim function in isolation (that is already
covered directly in ``tests/test_queue.py``).
"""
import asyncio
import logging

from sqlalchemy import func, select, text

from autoposter.db.models import Job as JobRow
from autoposter.queue.jobs import claim, enqueue
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import make_stale_reclaim_job


async def test_scheduler_tick_reclaims_a_stale_running_claim(session_factory):
    async with session_factory() as setup:
        job_id = await enqueue(setup, "process_item", {})
        await claim(setup, "worker-a")
        # Older than reclaim_stale's 900s threshold, on the database clock --
        # a worker that died mid-job, not one still genuinely working it.
        await setup.execute(
            text("UPDATE jobs SET claimed_at = now() - interval '20 minutes' WHERE id = :id"),
            {"id": job_id},
        )
        await setup.commit()

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [make_stale_reclaim_job()], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    try:
        async with asyncio.timeout(5):
            while True:
                async with session_factory() as check:
                    row = (
                        await check.execute(select(JobRow).where(JobRow.id == job_id))
                    ).scalar_one()
                if row.state == "pending":
                    break
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        await task

    async with session_factory() as check:
        row = (
            await check.execute(select(JobRow).where(JobRow.id == job_id))
        ).scalar_one()
        assert row.state == "pending"
        assert row.claimed_by is None
        assert row.claimed_at is None
        # run_after must be due against the DATABASE's now(), not this
        # process's -- the dev clock steps backwards, so any client-side
        # comparison here would be flaky by construction.
        run_after, db_now, is_due = (
            await check.execute(
                select(JobRow.run_after, func.now(), JobRow.run_after <= func.now()).where(
                    JobRow.id == job_id
                )
            )
        ).one()
        assert is_due, f"run_after {run_after} is not due against db now {db_now}"


async def test_scheduler_tick_leaves_a_young_running_claim_alone(session_factory):
    async with session_factory() as setup:
        job_id = await enqueue(setup, "process_item", {})
        await claim(setup, "worker-a")

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [make_stale_reclaim_job()], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await task

    async with session_factory() as check:
        row = (
            await check.execute(select(JobRow).where(JobRow.id == job_id))
        ).scalar_one()
    assert row.state == "running"
    assert row.claimed_by == "worker-a"


async def test_stale_reclaim_job_logs_only_when_something_was_reclaimed(session, caplog):
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")

    job = make_stale_reclaim_job()
    assert job.name == "stale_job_reclaim"

    with caplog.at_level(logging.INFO, logger="autoposter.scheduler.jobs"):
        await job.run(session)
    assert not [r for r in caplog.records if "reclaimed" in r.message]

    await session.execute(
        text("UPDATE jobs SET claimed_at = now() - interval '20 minutes' WHERE id = :id"),
        {"id": job_id},
    )
    await session.commit()
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="autoposter.scheduler.jobs"):
        await job.run(session)
    reclaimed_logs = [r for r in caplog.records if "reclaimed" in r.message]
    assert len(reclaimed_logs) == 1
    assert reclaimed_logs[0].levelno == logging.INFO
