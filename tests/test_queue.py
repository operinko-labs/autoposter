import asyncio
from datetime import timedelta

from sqlalchemy import func, select, text

from autoposter.db.models import Job
from autoposter.queue.jobs import (
    DEFER_INTERVAL_SECONDS,
    MAX_ATTEMPTS,
    RECLAIM_STAGGER_SECONDS,
    claim,
    complete,
    enqueue,
    enqueue_batch,
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


async def test_reenqueuing_a_deferred_key_wakes_it_instead_of_duplicating(session):
    # The production incident this guards: uq_jobs_pending_dedupe used to
    # cover 'pending' only, so every webhook/sweep event for an item still
    # waiting on Plex minted another independent deferred row -- 11 of them
    # piled up for one show. The widened index now covers 'deferred' too, and
    # enqueue() wakes the existing row on conflict instead of failing to
    # insert a duplicate.
    job_id = await enqueue(session, "process_item", {"n": 1}, dedupe_key="k-wake")
    await claim(session, "worker-a")
    await fail(session, job_id, "no Plex item", defer_seconds=DEFER_INTERVAL_SECONDS)

    woken = await enqueue(session, "process_item", {"n": 2}, dedupe_key="k-wake")

    rows = (await session.execute(select(Job).where(Job.dedupe_key == "k-wake"))).scalars().all()
    assert len(rows) == 1, "a second row was inserted instead of waking the deferred one"
    assert woken == job_id

    job = rows[0]
    assert job.state == "pending"
    db_now = (await session.execute(select(func.now()))).scalar_one()
    assert job.run_after <= db_now
    # A woken deferred row starts fresh: fail() already reset attempts to 0
    # when it deferred, so waking must not touch it further.
    assert job.attempts == 0


async def test_reenqueuing_a_pending_key_still_debounces_with_no_wake(session):
    # The pre-existing pending-debounce contract must survive the widened
    # index untouched: a duplicate event for an already-pending job returns
    # None and leaves the row alone, it does not "wake" anything.
    job_id = await enqueue(session, "process_item", {}, dedupe_key="k-pending")
    job_before = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    run_after_before = job_before.run_after

    second = await enqueue(session, "process_item", {}, dedupe_key="k-pending", delay_seconds=999)

    assert second is None
    job_after = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job_after)
    assert job_after.state == "pending"
    assert job_after.run_after == run_after_before


async def test_enqueue_batch_skips_a_deferred_key_rather_than_waking_it(session):
    # enqueue_batch backs the full-pass/backfill sweep, not a targeted event,
    # so it makes the opposite choice from enqueue(): a deferred row is left
    # alone (ON CONFLICT DO NOTHING), not woken.
    job_id = await enqueue(session, "process_item", {}, dedupe_key="k-batch")
    await claim(session, "worker-a")
    await fail(session, job_id, "no Plex item", defer_seconds=DEFER_INTERVAL_SECONDS)

    inserted = await enqueue_batch(session, "process_item", [({"n": 2}, "k-batch")])

    assert inserted == 0
    rows = (await session.execute(select(Job).where(Job.dedupe_key == "k-batch"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == job_id
    assert rows[0].state == "deferred", "batch must not wake a deferred row"


async def test_complete_dismisses_a_deferred_sibling_created_while_the_first_ran(session):
    # The widened index makes it impossible for enqueue()/enqueue_batch() to
    # ever create a second live (pending or deferred) row for a key that
    # already has one -- except through the one gap the index cannot see:
    # a row in 'running' state is not covered by the partial index at all, so
    # a fresh event for the same key during that window still inserts an
    # independent row. If that second row later defers on its own and the
    # first later completes, the second is stranded exactly as
    # complete()'s sibling-dismissal sweep was written to handle.
    first = await enqueue(session, "process_item", {}, dedupe_key="k-race")
    await claim(session, "worker-a")  # first -> running, no longer index-covered

    second = await enqueue(session, "process_item", {}, dedupe_key="k-race")
    assert second is not None, "a fresh row must still be insertable while the first is running"

    await claim(session, "worker-b")
    await fail(session, second, "no Plex item", defer_seconds=DEFER_INTERVAL_SECONDS)

    await complete(session, first)

    rows = {
        job.id: job.state
        for job in (await session.execute(select(Job))).scalars().all()
    }
    assert rows[first] == "done"
    assert rows[second] == "dismissed"


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

    # reclaim_stale() commits, so run_after was stamped in one transaction and
    # any now() read here belongs to a later one. This comparison is inherently
    # cross-transaction and CANNOT be made immune the way test_item_facts.py's
    # same-transaction identity is -- and neither can claim() itself
    # (_CLAIM_SQL matches on `run_after <= now()`), which is production
    # semantics: a backwards clock step in this window breaks the application's
    # job scheduling, not merely this assertion. Computing the comparison
    # server-side is tidiness, not immunity. The residual is the commit plus a
    # round trip, 5.6 ms against this machine's ~30 s step cycle -- ≈0.019 %,
    # irreducible (docs/research/dev-clock-step/).
    run_after, db_now, is_due = (
        await session.execute(
            select(Job.run_after, func.now(), Job.run_after <= func.now()).where(
                Job.id == job_id
            )
        )
    ).one()
    assert is_due, (
        f"run_after {run_after} is not due against the database clock {db_now}: "
        "either reclaim_stale did not reset it, or the wall clock stepped "
        "backwards between the two transactions (docs/research/dev-clock-step/)"
    )
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


async def test_reclaim_stale_staggers_run_after_across_reclaimed_jobs(session):
    # Production incident: a periodic reclaim that set every reclaimed job's
    # run_after to the same now() released 5 jobs into the worker pool at
    # once -- the concurrent renders OOMKilled the pod within seconds. The
    # jobs died together (same restart); restarting them together is what
    # killed it, so reclaim must re-admit them one at a time instead.
    job_ids = [await enqueue(session, "process_item", {"n": i}) for i in range(3)]
    for job_id in job_ids:
        await claim(session, "worker-a")
    await session.execute(
        text("UPDATE jobs SET claimed_at = now() - interval '20 minutes' WHERE id = ANY(:ids)"),
        {"ids": job_ids},
    )
    await session.commit()

    count = await reclaim_stale(session, older_than_seconds=900)
    assert count == 3

    rows = (
        await session.execute(
            select(Job.id, Job.run_after).where(Job.id.in_(job_ids)).order_by(Job.run_after)
        )
    ).all()
    run_afters = [run_after for _id, run_after in rows]
    # Strictly increasing, and each apart by exactly the stagger interval --
    # all computed against the same server-side now() inside one UPDATE
    # statement, so this is not subject to the dev clock's backwards steps.
    for earlier, later in zip(run_afters, run_afters[1:]):
        assert (later - earlier) == timedelta(seconds=RECLAIM_STAGGER_SECONDS)
    # The very first reclaimed row still becomes immediately claimable, same
    # as the unstaggered behaviour this replaces (ordinal 0 -> now() + 0).
    db_now = (await session.execute(select(func.now()))).scalar_one()
    assert run_afters[0] <= db_now


async def test_reclaim_stale_preserves_cancel_requested(session):
    # Cancel is only honoured when an attempt ends (fail()) -- reclaim_stale
    # itself must never touch cancel_requested, or a cancelled-but-stuck job
    # would lose its cancel flag on the very sweep meant to unstick it, and
    # Cancel would hang forever again. Adding `cancel_requested = false` to
    # the reclaim SQL is a natural-looking "a reclaimed job starts fresh"
    # edit that would pass every other test in the suite.
    job_id = await enqueue(session, "process_item", {})
    await claim(session, "worker-a")
    await session.execute(
        text(
            "UPDATE jobs SET claimed_at = now() - interval '20 minutes',"
            " cancel_requested = true WHERE id = :id"
        ),
        {"id": job_id},
    )
    await session.commit()

    count = await reclaim_stale(session, older_than_seconds=900)
    assert count == 1

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "pending"
    assert job.cancel_requested is True


async def test_reclaim_stale_does_not_re_pend_a_row_already_reclaimed_and_re_claimed(
    session_factory,
):
    """M1 regression, reproduced without a timing race.

    Before the fix, the reclaim UPDATE's outer WHERE was qualified only by
    `jobs.id = stale.id` -- the `state = 'running'` and staleness checks lived
    in the ``stale`` CTE, which Postgres materializes once per statement and
    does not re-run. Under READ COMMITTED, a second reclaim blocked on this
    row (a concurrent sweep -- e.g. a new pod's boot reclaim racing the
    outgoing pod's periodic tick) re-checks only the outer qualification
    against the row's *current* version when it unblocks. `jobs.id =
    stale.id` alone survives no matter what happened to the row meanwhile, so
    a row already reclaimed and then re-claimed by a live worker got re-pended
    out from under it, wiping the live claim.

    Reproduced deterministically with two real sessions rather than a timing
    race: session A holds an uncommitted transaction that leaves the row
    exactly as "reclaimed, then re-claimed fresh" would -- state stays
    'running' throughout; only claimed_by/claimed_at change. Session B's
    reclaim starts concurrently; its CTE snapshot is taken before A's change
    is visible (still sees the row as stale, so it is included), and its
    UPDATE then blocks on A's lock. Only once B is confirmed blocked (polling
    pg_locks, not a sleep) do we commit A -- so B's post-unblock re-check runs
    against the fresh claim for certain, not on a guess about timing.
    """
    async with session_factory() as setup:
        job_id = await enqueue(setup, "process_item", {})
        await setup.execute(
            text(
                "UPDATE jobs SET state = 'running', claimed_by = 'worker-stale',"
                " claimed_at = now() - interval '20 minutes' WHERE id = :id"
            ),
            {"id": job_id},
        )
        await setup.commit()

    session_a = session_factory()
    await session_a.execute(text("SELECT 1"))  # warm the connection before it matters
    await session_a.execute(
        text(
            "UPDATE jobs SET claimed_by = 'worker-fresh', claimed_at = now()"
            " WHERE id = :id"
        ),
        {"id": job_id},
    )
    # session_a's transaction is now open, uncommitted, and holds the row lock.

    b_pid: int | None = None

    async def second_reclaim():
        nonlocal b_pid
        async with session_factory() as session_b:
            b_pid = (await session_b.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            return await reclaim_stale(session_b, older_than_seconds=900)

    task_b = asyncio.create_task(second_reclaim())

    try:
        # Confirmation, not a guess: wait for session_b's UPDATE to actually
        # be blocked on session_a's lock before releasing it. pg_locks is
        # cluster-wide, so under xdist another worker's own ungranted lock
        # could satisfy this poll early and make the test vacuously pass --
        # scope it to session_b's own backend pid (the row-lock wait shows up
        # as a transactionid lock, which carries no database column, so
        # scoping by database would never match).
        async with session_factory() as poll:
            async with asyncio.timeout(5):
                while b_pid is None:
                    await asyncio.sleep(0.01)
                while True:
                    blocked = (
                        await poll.execute(
                            text("SELECT 1 FROM pg_locks WHERE NOT granted AND pid = :pid"),
                            {"pid": b_pid},
                        )
                    ).first()
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
    finally:
        # If the timeout above fires, session_a must still be closed here --
        # otherwise it keeps holding the row lock and the next test's
        # TRUNCATE wedges behind it.
        await session_a.commit()
        await session_a.close()

    reclaimed_by_b = await task_b

    assert reclaimed_by_b == 0, "the second reclaim touched the row despite the fresh claim"

    async with session_factory() as check:
        row = (await check.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.state == "running", "the reclaimed-and-re-claimed row was re-pended"
    assert row.claimed_by == "worker-fresh"
    assert row.claimed_at is not None
