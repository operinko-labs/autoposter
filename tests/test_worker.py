import asyncio
from dataclasses import asdict

import pytest
import requests
from sqlalchemy import func, select, text

from autoposter.db.models import Job
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound, PlexPathMismatch
from autoposter.artwork_modes.base import WorkerPause
from autoposter.queue.jobs import MAX_ATTEMPTS, enqueue
from autoposter.queue.worker import run_once, run_worker


def _only_process_item(handler):
    """Adapt an intent-taking test handler into the worker's ``{kind: handler}`` map.

    The dispatch handler receives the ORM ``Job``; the ``process_item`` entry
    decodes its ``RenderIntent`` payload, exactly as app.py's real registration
    does. This keeps every existing test's handler body (which takes an intent)
    unchanged while exercising the new dispatch-map signature.
    """

    async def _run(session_, job):
        await handler(session_, RenderIntent(**job.payload))

    return {"process_item": _run}


async def test_run_once_processes_a_due_job(session):
    handled = []

    async def handler(session_, intent):
        handled.append(intent)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    assert await run_once(session, "worker-1", _only_process_item(handler)) is True
    assert handled[0].tmdb_id == 1
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "done"


async def test_run_once_returns_false_when_nothing_is_due(session):
    async def handler(session_, intent):
        raise AssertionError("should not be called")

    assert await run_once(session, "worker-1", _only_process_item(handler)) is False


async def test_item_not_found_defers_rather_than_failing(session):
    async def handler(session_, intent):
        raise ItemNotFound("plex has not scanned yet")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await run_once(session, "worker-1", _only_process_item(handler))
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "deferred"
    # The reason is kept on the row: it is what the Jobs page shows as the
    # thing being waited for, in place of the message-prefix guess it used to
    # make about a job that had already parked.
    assert "scanned" in job.last_error


async def test_path_mismatch_parks_instead_of_deferring_forever(session):
    # Site :488's raise (PlexPathMismatch) is a permanent path-mapping
    # misconfiguration, not a "Plex hasn't scanned yet" wait -- the item
    # resolved fine, but its file does not map under any of the library's
    # roots, and no amount of retrying fixes that. Unlike the other two
    # ItemNotFound raise sites, it must reach Failures like any other genuine
    # error rather than defer on the six-hour horizon forever.
    async def handler(session_, intent):
        raise PlexPathMismatch(
            "Plex item 123 ('/data/movies/Dune (2021)') is not inside any of "
            "the library roots ['/media/movies'] for library 'Movies'"
        )

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=13)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    for _ in range(MAX_ATTEMPTS):
        await _make_due_now(session, job_id)
        await run_once(session, "worker-1", _only_process_item(handler))

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "parked"


async def test_unexpected_errors_also_reschedule(session):
    async def handler(session_, intent):
        raise RuntimeError("provider exploded")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=2)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await run_once(session, "worker-1", _only_process_item(handler))
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "pending"
    assert "exploded" in job.last_error


async def test_unknown_job_kinds_are_parked_not_retried(session):
    async def handler(session_, intent):
        raise AssertionError("should not be called")

    await enqueue(session, "not_a_real_kind", {})
    await run_once(session, "worker-1", _only_process_item(handler))
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
        await run_once(session, "worker-1", _only_process_item(handler))

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
        await run_once(session, "worker-1", _only_process_item(handler))

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


async def _bring_horizon_forward(session, job_id: int) -> None:
    """Make a job due without touching its state, using the database clock.

    ``_make_due_now`` above flips the row back to ``pending``, which would hide
    the very thing the deferral tests are about: that ``claim()`` picks a
    ``deferred`` row up by itself once ``run_after`` passes.
    """
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.run_after = (await session.execute(select(func.now()))).scalar_one()
    await session.commit()


async def test_item_not_found_defers_forever_instead_of_parking(session):
    # The production shape this exists for: a movie added to Radarr before it
    # is released enqueues a job Plex cannot resolve for weeks. Retried against
    # any finite budget it parks and reads as a permanent failure. It must wait
    # on the long horizon instead, unbounded.
    async def not_found_handler(session_, intent):
        raise ItemNotFound("no Plex item for movie 'Dog Stars' (tmdb=12, tvdb=None)")

    intent = RenderIntent(kind="movie", title="Dog Stars", tmdb_id=12)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    for _ in range(MAX_ATTEMPTS + 5):
        await _bring_horizon_forward(session, job_id)
        await run_once(session, "worker-1", _only_process_item(not_found_handler))

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    # A generic failure would have parked long ago (test_job_parks_after_max_attempts
    # in test_queue.py); this one is still waiting, and always will be.
    assert job.state == "deferred"
    # A wait is not a failed attempt. The budget a real error would spend is
    # left untouched, so a job that waited a dozen times still gets its full
    # retry count the day Plex answers and something else goes wrong.
    assert job.attempts == 0


async def test_a_generic_failure_still_parks(session):
    # The contrast the test above is only meaningful against: nothing here
    # widened the ordinary failure path into an unbounded one.
    async def generic_handler(session_, intent):
        raise RuntimeError("provider exploded")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=11)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    for _ in range(MAX_ATTEMPTS):
        await _make_due_now(session, job_id)
        await run_once(session, "worker-1", _only_process_item(generic_handler))

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "parked"


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
        await run_once(session, "worker-1", _only_process_item(connection_error_handler))

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

    await run_once(session, "worker-1", _only_process_item(handler))

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
            "worker-1",
            session_factory,
            _only_process_item(handler),
            stop_event,
            is_healthy=lambda: healthy["value"],
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


async def test_a_registered_new_job_kind_dispatches_to_its_handler(session):
    # The dispatch map opens the worker to mode jobs (Phase 7b): a kind with a
    # registered handler runs it and completes, rather than parking. The mode
    # handler takes the ORM Job (its payload is not a RenderIntent), so the
    # process_item entry must not be reached for it.
    seen = []

    async def process(session_, intent):
        raise AssertionError("the process_item handler must not run for a mode job")

    async def backup(session_, job):
        seen.append(job.kind)

    handlers = {**_only_process_item(process), "backup": backup}
    await enqueue(session, "backup", {"library": "Movies"})

    assert await run_once(session, "worker-1", handlers) is True
    assert seen == ["backup"]
    job = (await session.execute(select(Job))).scalar_one()
    assert job.state == "done"


async def test_paused_pool_claims_nothing_then_resumes_on_clear(session_factory):
    # The worker-pause fence (Phase 7b restore): while paused, the pool claims
    # nothing -- the job stays pending with no attempt burned -- and the very
    # same job is claimed and run as soon as the fence clears. Mutation proof:
    # drop the pause check in run_worker and the paused assertions below red.
    handled = []

    async def handler(session_, intent):
        handled.append(intent)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=40)
    async with session_factory() as setup_session:
        job_id = await enqueue(
            setup_session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key
        )

    pause = WorkerPause()
    pause.pause()
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        run_worker(
            "worker-1",
            session_factory,
            _only_process_item(handler),
            stop_event,
            is_healthy=lambda: True,
            pause=pause,
        )
    )
    try:
        await asyncio.sleep(0.2)
        async with session_factory() as session:
            job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
            assert job.state == "pending"
            assert job.attempts == 0
            assert job.claimed_by is None
        assert handled == []

        pause.resume()

        async def is_done():
            async with session_factory() as session:
                job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
                return job.state == "done"

        await _wait_until(is_done)
        assert handled[0].tmdb_id == 40
    finally:
        stop_event.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_job_claimed_as_the_fence_rises_is_handed_back_uncharged(session):
    # The other half of the fence. run_worker's gate and the claim inside
    # run_once are several awaits apart, so a pause raised in that window would
    # otherwise let one more job run in full -- after the mode's drain had
    # already concluded there was nothing to wait for. run_once re-checks after
    # the claim and releases the job. Mutation proof: drop that re-check and the
    # handler's AssertionError fires.
    async def handler(session_, intent):
        raise AssertionError("the fence was up; this job must not have run")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=41)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)

    pause = WorkerPause()
    pause.pause()
    assert (
        await run_once(session, "worker-1", _only_process_item(handler), pause) is False
    )

    job = (await session.execute(select(Job))).scalar_one()
    # Pending, unclaimed, and the attempt claim() charged handed back: the job
    # is exactly as claimable as it was, so the fence costs it no retry budget.
    assert job.state == "pending"
    assert job.attempts == 0
    assert job.claimed_by is None


async def test_a_running_job_is_counted_active_for_the_drain(session):
    # What ``drain()`` reads: while the handler is inside run_once the pause
    # reports an active job, and it is given back afterwards.
    pause = WorkerPause()
    seen = []

    async def handler(session_, intent):
        seen.append(pause.active_jobs)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=42)
    await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)

    await run_once(session, "worker-1", _only_process_item(handler), pause)

    assert seen == [1]
    assert pause.active_jobs == 0


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
        run_worker(
            "worker-1", session_factory, _only_process_item(handler), stop_event, is_healthy=lambda: True
        )
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


# --- cancellation requested while the job was running -------------------------


async def _cancel_requested(session, job_id: int) -> None:
    """What POST /api/jobs/{id}/cancel leaves on a job it found running."""
    await session.execute(
        text("UPDATE jobs SET cancel_requested = true WHERE id = :id"), {"id": job_id}
    )
    await session.commit()


async def test_a_cancelled_job_is_dismissed_rather_than_rescheduled_on_failure(session):
    # The operator cancelled this job mid-attempt. A finished render cannot be
    # un-rendered, so the attempt is allowed to end -- but once it has ended in
    # failure the cancellation is honoured: dismissed, not queued for another
    # try, and regardless of how much retry budget is left.
    async def handler(session_, intent):
        raise RuntimeError("provider exploded")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=50)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await _cancel_requested(session, job_id)

    await run_once(session, "worker-1", _only_process_item(handler))

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "dismissed"
    assert job.attempts < MAX_ATTEMPTS, "the budget was not what stopped it"


async def test_a_cancelled_job_waiting_for_plex_is_also_dismissed(session):
    # The ItemNotFound path has its own, much larger attempt budget, so a
    # cancellation that only beat the generic cap would leave these retrying
    # for another hour.
    async def handler(session_, intent):
        raise ItemNotFound("no Plex item for movie 'Dune'")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=51)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await _cancel_requested(session, job_id)

    await run_once(session, "worker-1", _only_process_item(handler))

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "dismissed"


async def test_a_cancelled_job_that_succeeds_still_completes(session):
    # A render that already happened cannot be taken back, and the upload with
    # it. Reporting it as dismissed would be a false record of what the service
    # did to the library.
    handled = []

    async def handler(session_, intent):
        handled.append(intent)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=52)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await _cancel_requested(session, job_id)

    await run_once(session, "worker-1", _only_process_item(handler))

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "done"
    assert len(handled) == 1


async def test_an_uncancelled_job_still_reschedules_after_a_failure(session):
    # The other side of the guard: nothing about the retry path changes for a
    # job nobody cancelled.
    async def handler(session_, intent):
        raise RuntimeError("provider exploded")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=53)
    job_id = await enqueue(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)

    await run_once(session, "worker-1", _only_process_item(handler))

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "pending"
