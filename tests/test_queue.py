import asyncio
from datetime import timedelta

from sqlalchemy import func, select, text

from autoposter.db.models import Job
from autoposter.queue.jobs import (
    DEFER_INTERVAL_SECONDS,
    MAX_ATTEMPTS,
    claim,
    complete,
    enqueue,
    fail,
    reclaim_stale,
)


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
    #
    # Anchored to run_after rather than to a wall-clock reading taken afterwards:
    # enqueue() computes run_after as func.now() + a zero interval in the INSERT
    # itself, so with no delay both columns are the same transaction_timestamp()
    # and are EXACTLY equal, while a client-side created_at would differ by the
    # app/DB offset. The previous shape compared created_at against a func.now()
    # read in a later transaction with a `< 1` second tolerance; this dev VM's
    # wall clock steps backwards ~2.7 s every ~30 s
    # (docs/research/dev-clock-step/), so that tolerance was smaller than the
    # step -- and no tolerance wide enough to survive the step would still be
    # narrow enough to catch the drift this test exists to guard.
    job_id = await enqueue(session, "process_item", {})
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.created_at == job.run_after


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


async def test_a_deferred_job_waits_the_long_horizon_and_never_parks(session):
    # ``deferred`` is not a failure with a bigger budget -- it has no budget.
    # Well past the attempt cap that parks an ordinary failure, this one is
    # still waiting, because nothing about it is wrong.
    job_id = await enqueue(session, "process_item", {})
    for _ in range(MAX_ATTEMPTS + 3):
        await _make_due_now(session, job_id)
        await claim(session, "worker-a")
        state = await fail(
            session, job_id, "no Plex item", defer_seconds=DEFER_INTERVAL_SECONDS
        )
        assert state == "deferred"

    db_now = (await session.execute(select(func.now()))).scalar_one()
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    # The horizon, not the backoff curve: this row is waiting for the library
    # to catch up, which happens on a scale of days.
    remaining = (job.run_after - db_now).total_seconds()
    assert DEFER_INTERVAL_SECONDS - 300 < remaining <= DEFER_INTERVAL_SECONDS
    assert job.attempts == 0


async def test_claim_takes_a_deferred_job_once_its_horizon_passes(session):
    # What makes the wait a wait rather than a grave: the same claim that
    # picks up pending work picks a deferred row up when it comes due, with
    # nothing else having to resurrect it.
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await fail(session, job_id, "no Plex item", defer_seconds=DEFER_INTERVAL_SECONDS)

    assert await claim(session, "worker-a") is None, "claimed six hours early"

    await session.execute(
        text("UPDATE jobs SET run_after = now() WHERE id = :id"), {"id": job_id}
    )
    await session.commit()

    job = await claim(session, "worker-a")
    assert job is not None
    assert job.id == job_id
    assert job.state == "running"


async def test_a_cancelled_job_is_dismissed_rather_than_deferred(session):
    # Cancellation outranks the wait. It has to: the horizon is unbounded, so
    # a deferral that ignored the cancel would be the operator's last word
    # ignored forever.
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await session.execute(
        text("UPDATE jobs SET cancel_requested = true WHERE id = :id"), {"id": job_id}
    )
    await session.commit()

    state = await fail(
        session, job_id, "no Plex item", defer_seconds=DEFER_INTERVAL_SECONDS
    )
    assert state == "dismissed"


async def test_completing_a_job_dismisses_the_deferred_row_for_the_same_item(session):
    # The Download webhook eventually queues a fresh job for the item whose
    # earlier add is still deferred. Once that one succeeds the wait has no
    # subject left, so it is dismissed rather than left to resolve, run and
    # redo finished work six hours later.
    waiting = await enqueue(session, "process_item", {}, dedupe_key="k-deferred")
    await claim(session, "worker-a")
    await fail(session, waiting, "no Plex item", defer_seconds=DEFER_INTERVAL_SECONDS)

    # The partial unique index covers pending rows only, so the deferred
    # sibling does not block the new job.
    fresh = await enqueue(session, "process_item", {}, dedupe_key="k-deferred")
    assert fresh is not None
    other = await enqueue(session, "process_item", {}, dedupe_key="k-other")
    await claim(session, "worker-a")
    await fail(session, other, "no Plex item", defer_seconds=DEFER_INTERVAL_SECONDS)

    await complete(session, fresh)

    rows = {
        job.id: job.state
        for job in (await session.execute(select(Job))).scalars().all()
    }
    assert rows[waiting] == "dismissed"
    assert rows[fresh] == "done"
    # Another item's wait is not this item's business.
    assert rows[other] == "deferred"


async def test_complete_marks_done(session):
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await complete(session, job_id)
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert job.state == "done"


async def test_reclaim_stale_resets_old_running_jobs(session):
    # A job whose claim is far older than the threshold means the process that
    # claimed it is gone (crash, OOM, SIGKILL) — it must become claimable again.
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await session.execute(
        text("UPDATE jobs SET claimed_at = now() - interval '20 minutes' WHERE id = :id"),
        {"id": job_id},
    )
    await session.commit()

    count = await reclaim_stale(session, older_than_seconds=900)
    assert count == 1

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "pending"
    assert job.claimed_by is None
    assert job.claimed_at is None
    assert await claim(session, "worker-b") is not None


async def test_reclaim_stale_resets_run_after_so_the_job_is_immediately_claimable(session):
    # A reclaimed job's old run_after describes a schedule that no longer means
    # anything once the worker holding it is dead — the work is overdue, not
    # pending a future slot. Even a job scheduled an hour out must become
    # immediately claimable once its claim is stale.
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await session.execute(
        text(
            "UPDATE jobs"
            " SET claimed_at = now() - interval '20 minutes',"
            "     run_after = now() + interval '1 hour'"
            " WHERE id = :id"
        ),
        {"id": job_id},
    )
    await session.commit()

    count = await reclaim_stale(session, older_than_seconds=900)
    assert count == 1

    # reclaim_stale() commits, so a wall-clock reading taken here would be a
    # later transaction than the row's run_after -- two readings, zero slack,
    # exposed to the dev VM's backwards clock step (docs/research/dev-clock-step/).
    # Pushing the comparison into the query itself keeps it server-side and
    # immune, the same fix as test_item_facts.py's same-transaction identity.
    row = (
        await session.execute(
            select(Job).where(Job.id == job_id, Job.run_after <= func.now())
        )
    ).scalar_one_or_none()
    assert row is not None
    assert await claim(session, "worker-b") is not None


async def test_reclaim_stale_leaves_recent_claims_alone(session):
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")

    count = await reclaim_stale(session, older_than_seconds=900)
    assert count == 0

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "running"
    assert job.claimed_by == "worker-a"
