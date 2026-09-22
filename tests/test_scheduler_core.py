"""Scheduler bookkeeping and claiming."""
import asyncio
import contextlib
import inspect
import json
import logging
import time
from pathlib import Path

import httpx
from sqlalchemy import func, select, text

from autoposter.actions import flags
from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import load_config
from autoposter.config.schema import NotificationsConfig
from autoposter.db.models import Render, Run, ScheduledRun
from autoposter.loop_lag import monitor_loop_lag

from conftest import seed_media_item
from autoposter.notify.dispatch import build_notifier
from autoposter.scheduler import core
from autoposter.scheduler.core import Job, Scheduler, claim_due
from autoposter.scheduler.jobs import make_drift_job
from autoposter.scheduler.run_history import FULL_PASS_NAME, open_run


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
    # Waits for the behaviour rather than for the clock: a fixed sleep budgets
    # ten poll intervals for one round trip to PostgreSQL, which is ample on an
    # idle machine and not ample under `-n auto` (roadmap row 119). The timeout
    # is swallowed so the assertion below reports the diagnosis rather than a
    # bare "5 seconds passed", and the `finally` keeps that honest by stopping
    # and awaiting the scheduler on both paths.
    try:
        async with asyncio.timeout(60):
            while not ran:
                await asyncio.sleep(0.01)
    except TimeoutError:
        pass
    finally:
        stop.set()
        await task

    assert ran
    async with session_factory() as session:
        row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_status == "ok"
    assert row.last_detail == "did the thing"
    assert row.last_finished_at is not None


async def test_a_claimed_job_logs_that_it_started(session_factory, caplog):
    """A multi-minute job was otherwise silent from claim to finish -- a
    healthy long run and a dead scheduler looked identical on the logs. One
    INFO line at claim time, naming only the job, fixes that."""
    async def body(session):
        return "did the thing"

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(name="prune", run=body)], poll_seconds=0.01)
    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(scheduler.run(stop))
        # As above: wait on the log line, not on the clock (roadmap row 119).
        try:
            async with asyncio.timeout(60):
                while not [r for r in caplog.records if "started" in r.message]:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pass
        finally:
            stop.set()
            await task

    started = [r for r in caplog.records if "started" in r.message]
    assert len(started) == 1
    assert started[0].message == "scheduler: prune started"
    assert started[0].levelno == logging.INFO


async def test_a_failing_job_is_recorded_and_the_scheduler_survives(session_factory):
    calls = []

    async def body(session):
        calls.append(True)
        raise RuntimeError("job exploded")

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(interval=0, run=body)], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    # Waits for the behaviour rather than for the clock. The assertion below is
    # that the scheduler came back round *after* the job raised, which costs two
    # full cycles -- claim, run, raise, record the failure, claim again -- each
    # one a round trip to PostgreSQL. A fixed `await asyncio.sleep(0.1)` was
    # enough for that on an idle machine and stopped being enough once CI ran
    # the suite under `-n auto`, where it failed as "scheduler stopped after the
    # first failure" having simply not been given the time.
    #
    # The timeout is swallowed rather than propagated so that the named
    # assertion below is what reports the failure: a bare TimeoutError out of
    # the `async with` says only "5 seconds passed", where the assertion says
    # "scheduler stopped after the first failure", which is the actual
    # diagnosis and the reason this test exists. The `finally` is what keeps
    # the swallowing honest -- the scheduler is stopped and awaited on both
    # paths, so a timed-out run cannot leave the task pending for
    # pytest-asyncio to report as unrelated teardown noise.
    try:
        async with asyncio.timeout(5):
            while len(calls) < 2:
                await asyncio.sleep(0.01)
    except TimeoutError:
        pass
    finally:
        stop.set()
        await task

    assert len(calls) > 1, "scheduler stopped after the first failure"
    async with session_factory() as session:
        row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_status == "failed"
    # Row 209: the served copy is the class name; "job exploded" now lives
    # only in the pod log.
    assert row.last_detail == "RuntimeError"


async def test_a_failing_job_records_the_class_name_only(session_factory, caplog):
    """Roadmap row 209 site (1): the failure branch wrote str(error) to
    last_detail, served by /api/snapshots and carried by the notification --
    and a plexapi or provider failure's message commonly carries the URL it
    failed on, in some shapes a token. Class name only on the served copy;
    the full message and traceback stay on the exc_info warning below -- the
    pod log, the trusted sink."""
    async def body(session):
        raise RuntimeError(
            "GET http://plex.internal:32400/library/all?X-Plex-Token=SECRETTOKEN failed"
        )

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(run=body)], poll_seconds=0.01)
    with caplog.at_level(logging.WARNING, logger="autoposter.scheduler.core"):
        task = asyncio.create_task(scheduler.run(stop))
        try:
            async with asyncio.timeout(5):
                while True:
                    async with session_factory() as check:
                        row = (
                            await check.execute(select(ScheduledRun))
                        ).scalar_one_or_none()
                    if row is not None and row.last_status == "failed":
                        break
                    await asyncio.sleep(0.01)
        finally:
            stop.set()
            await task

    assert row.last_detail == "RuntimeError"
    assert "SECRETTOKEN" not in row.last_detail
    assert "plex.internal" not in row.last_detail
    # The compensating control, pinned rather than assumed (the Sweep-1
    # pattern, test_scheduler_collections_job.py:267-286): narrowing the
    # served copy is only safe while the warning at core.py's failure branch
    # still writes the FULL message and traceback to the pod log. A downgrade
    # -- dropping exc_info, or deleting the line -- goes red here instead of
    # silently blinding the operator.
    pinned = [
        record for record in caplog.records
        if record.levelno == logging.WARNING and record.exc_info is not None
    ]
    assert [r.getMessage() for r in pinned] == ["scheduler: demo failed"]
    assert "SECRETTOKEN" in caplog.text
    assert "plex.internal" in caplog.text


class _ServedFailure(RuntimeError):
    served_detail = True


async def test_a_served_safe_failure_keeps_its_message(session_factory):
    """The marker half of row 209 site (1): CollectionsPassFailed and
    PruneRefused BUILD their messages for the served surfaces (per-library
    summaries whose error halves are already class-name-only, rows 136/188;
    prune's hand-built refusals). Narrowing those too would blank the
    dashboard -- test_builder_knobs.py pins the real one end to end. The
    marker is what lets the scheduler core stay ignorant of both modules."""
    async def body(session):
        raise _ServedFailure("Movies: failed (RuntimeError); TV Shows: 3 action(s)")

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(run=body)], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    try:
        async with asyncio.timeout(5):
            while True:
                async with session_factory() as check:
                    row = (
                        await check.execute(select(ScheduledRun))
                    ).scalar_one_or_none()
                if row is not None and row.last_status == "failed":
                    break
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        await task

    assert row.last_detail == "Movies: failed (RuntimeError); TV Shows: 3 action(s)"


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


async def test_the_wired_run_loop_closes_a_drained_full_pass(session_factory):
    """`close_drained_full_passes` was pinned only through the bare
    helper (`tests/test_run_history.py`) -- nothing exercised it through
    `Scheduler.run`'s own poll loop (`core.py:147`), the repository's own
    recorded defect class where a helper's tests are green and the wired
    path differs. A real `Scheduler` here, ticking over an open `full_pass`
    row with no outstanding job, so the row it closes is the one the wired
    loop found on its own."""
    async with session_factory() as session:
        run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
        await session.commit()

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))

    # As above: wait for the row to close, not for the clock (roadmap row 119).
    async def _closed():
        async with session_factory() as probe:
            row = (await probe.execute(select(Run).where(Run.id == run_id))).scalar_one()
            return row.finished_at is not None

    try:
        async with asyncio.timeout(60):
            while not await _closed():
                await asyncio.sleep(0.01)
    except TimeoutError:
        pass
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    async with session_factory() as session:
        row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "ok"
    assert row.finished_at is not None


class _RecordingNotifier:
    """Fake notifier: records calls, optionally reads the committed row.

    ``done`` is set at the end of ``send``, so a test awaiting it (with a
    timeout) proves the fire-and-forget task ran to completion rather than
    being dropped or garbage-collected mid-flight.
    """

    def __init__(self, session_factory=None, error=None):
        self._session_factory = session_factory
        self._error = error
        self.calls = []
        self.observed = []
        self.done = asyncio.Event()

    async def send(self, event: str, summary: str, detail: dict) -> bool:
        if self._session_factory is not None:
            async with self._session_factory() as session:
                row = (
                    await session.execute(select(ScheduledRun))
                ).scalar_one_or_none()
            self.observed.append(
                None
                if row is None
                else (row.last_status, row.last_finished_at is not None, row.last_detail)
            )
        self.calls.append((event, summary, detail))
        self.done.set()
        if self._error is not None:
            raise self._error
        return True


async def _await_calls(notifier: "_RecordingNotifier", count: int) -> None:
    """Wait until ``notifier.calls`` reaches ``count`` entries.

    ``notifier.done`` is set by the FIRST send, which is now the start event
    -- a test that needs a later send (the completion, or the additive
    failure event) to have landed must wait on the call count instead.
    """

    async def _reached():
        while len(notifier.calls) < count:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_reached(), timeout=5)


HOOK_HOST = "hooks.example.test"
HOOK_URL = f"http://{HOOK_HOST}/notify/tok-SECRET123"


def _refuse_db():
    raise AssertionError("a successful send must never touch the events log")


class _Catcher:
    """A webhook catcher: the real dispatcher POSTs into this.

    These tests assert the payload the operator's endpoint actually receives,
    not the arguments a fake notifier recorded -- the dispatch seam is where a
    wrong shape would reach n8n. ``seen`` lets a test await one fire-and-forget
    send instead of sleeping: the events it will wait for are named at
    construction, because a waiter created after the POST landed would wait
    forever.
    """

    def __init__(self, *events: str):
        self.bodies: list[dict] = []
        self._waiters = {
            f"autoposter: {event}": asyncio.Event() for event in events
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        waiter = self._waiters.get(body["title"])
        if waiter is not None:
            waiter.set()
        return httpx.Response(200)

    async def seen(self, event: str) -> dict:
        """Wait for one event and return its body."""
        await asyncio.wait_for(self._waiters[f"autoposter: {event}"].wait(), timeout=5)
        return self.body(event)

    def body(self, event: str) -> dict:
        (found,) = [b for b in self.bodies if b["title"] == f"autoposter: {event}"]
        return found

    @property
    def titles(self) -> list[str]:
        return [body["title"] for body in self.bodies]


async def test_a_completed_job_notifies_with_the_job_name_and_ok_status(session_factory):
    async def body(session):
        return "did the thing"

    notifier = _RecordingNotifier()
    stop = asyncio.Event()
    scheduler = Scheduler(
        session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
    )
    task = asyncio.create_task(scheduler.run(stop))
    await _await_calls(notifier, 2)
    stop.set()
    await task

    assert notifier.calls[0][0] == "scheduled_run_started"

    event, summary, detail = notifier.calls[1]
    assert event == "scheduled_run_completed"
    assert detail == {"job": "demo", "status": "ok", "detail": "did the thing"}
    assert "demo" in summary


async def test_a_failed_job_notifies_with_failed_status(session_factory):
    async def body(session):
        raise RuntimeError("job exploded")

    notifier = _RecordingNotifier()
    stop = asyncio.Event()
    scheduler = Scheduler(
        session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
    )
    task = asyncio.create_task(scheduler.run(stop))
    await _await_calls(notifier, 2)
    stop.set()
    await task

    assert notifier.calls[0][0] == "scheduled_run_started"

    event, _summary, detail = notifier.calls[1]
    assert event == "scheduled_run_completed"
    assert detail["job"] == "demo"
    assert detail["status"] == "failed"
    # Row 209: the notification payload is a served surface and carries the
    # same narrowed detail the scheduled_runs row records.
    assert detail["detail"] == "RuntimeError"


# --- roadmap row 19: the run_start and error events -------------------------


async def test_a_due_job_posts_a_run_start_payload_before_its_completion(
    session_factory,
):
    """Row 19's ``run_start``, asserted at the dispatch seam: this is the exact
    body the operator's endpoint receives. It fires after the claim commits --
    the row already says the run is in flight -- and before the completion
    payload, which is the ordering a consumer pairing the two depends on."""

    async def body(session):
        return "did the thing"

    catcher = _Catcher("scheduled_run_started", "scheduled_run_completed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        notifier = build_notifier(
            NotificationsConfig(enabled=True, url=HOOK_URL), http, _refuse_db
        )
        stop = asyncio.Event()
        scheduler = Scheduler(
            session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
        )
        task = asyncio.create_task(scheduler.run(stop))
        started = await catcher.seen("scheduled_run_started")
        await catcher.seen("scheduled_run_completed")
        stop.set()
        await task

    assert started == {
        "version": "1.0",
        "title": "autoposter: scheduled_run_started",
        "message": "scheduled run demo started",
        "attachments": [],
        # No status field, so an n8n gate on `body.type === "success"` passes
        # for a start too -- the shipped derivation (payload.py), unchanged.
        "type": "success",
    }
    assert catcher.titles.index("autoposter: scheduled_run_started") < (
        catcher.titles.index("autoposter: scheduled_run_completed")
    )


async def test_a_failed_job_posts_the_error_event_as_well_as_the_completion(
    session_factory,
):
    """Row 19's ``error``: one call site, the same boundary that writes the
    failed status. ADDITIVE -- ``scheduled_run_completed`` still fires, because
    removing a shipped event would break the n8n flow row 18 exists for. The
    message carries the class name only: an unmarked exception's str() is not
    a served surface (rows 209/213)."""

    async def body(session):
        raise RuntimeError("job exploded")

    catcher = _Catcher("scheduled_run_failed", "scheduled_run_completed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        notifier = build_notifier(
            NotificationsConfig(enabled=True, url=HOOK_URL), http, _refuse_db
        )
        stop = asyncio.Event()
        scheduler = Scheduler(
            session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
        )
        task = asyncio.create_task(scheduler.run(stop))
        failed = await catcher.seen("scheduled_run_failed")
        completed = await catcher.seen("scheduled_run_completed")
        stop.set()
        await task

    assert failed == {
        "version": "1.0",
        "title": "autoposter: scheduled_run_failed",
        "message": "scheduled run demo failed: RuntimeError",
        "attachments": [],
        "type": "failure",
    }
    assert completed["type"] == "failure", "the shipped event is unchanged"
    assert "job exploded" not in json.dumps(catcher.bodies), (
        "the exception's own message never reaches a served surface"
    )


async def test_a_successful_run_posts_no_error_event(session_factory):
    """The error event is a failure signal, not a run marker: a consumer
    routing on it must never be woken by a run that worked."""

    async def body(session):
        return "did the thing"

    catcher = _Catcher("scheduled_run_completed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        notifier = build_notifier(
            NotificationsConfig(enabled=True, url=HOOK_URL), http, _refuse_db
        )
        stop = asyncio.Event()
        scheduler = Scheduler(
            session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
        )
        task = asyncio.create_task(scheduler.run(stop))
        await catcher.seen("scheduled_run_completed")
        stop.set()
        await task

    assert "autoposter: scheduled_run_failed" not in catcher.titles


async def test_notification_failure_does_not_mark_the_run_failed(session_factory):
    """A notification describes work that already finished; its failure must
    never fail that work. A send that raises outright -- worse than the real
    Notifier ever behaves, since its contract is to return False -- leaves
    the recorded run untouched and the scheduler alive."""
    notifier = _RecordingNotifier(error=RuntimeError("webhook exploded"))
    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job()], poll_seconds=0.01, notifier=notifier)
    task = asyncio.create_task(scheduler.run(stop))
    # calls[0] is the start event; calls[1] is the completion send this test
    # needs to have landed (and raised) before checking the scheduler survived.
    await _await_calls(notifier, 2)
    assert not task.done(), "the scheduler died with the notification"
    stop.set()
    await task

    async with session_factory() as session:
        row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_status == "ok"
    assert row.last_finished_at is not None


async def test_the_notification_fires_after_the_run_is_committed(session_factory):
    """The notifier reads the database at send time: the row must already
    show the finished run, so a notification can never describe a run the
    database does not yet have."""

    async def body(session):
        return "did the thing"

    notifier = _RecordingNotifier(session_factory=session_factory)
    stop = asyncio.Event()
    scheduler = Scheduler(
        session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
    )
    task = asyncio.create_task(scheduler.run(stop))
    # calls[0] is the start event, which reads the row before the job even
    # runs -- the completion event (calls[1]) is the one this test pins.
    await _await_calls(notifier, 2)
    stop.set()
    await task

    assert notifier.observed[1] == ("ok", True, "did the thing")


async def test_the_scheduler_holds_the_notification_task_until_it_finishes(
    session_factory,
):
    """asyncio.create_task holds only a weak reference: a fire-and-forget
    task nothing else references can be garbage-collected mid-flight. The
    scheduler must keep a strong reference while the send is in flight and
    release it when the send completes."""
    release = asyncio.Event()

    class _ParkedNotifier:
        def __init__(self):
            self.done = asyncio.Event()

        async def send(self, event, summary, detail):
            await release.wait()
            self.done.set()
            return True

    notifier = _ParkedNotifier()
    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job()], poll_seconds=0.01, notifier=notifier)
    task = asyncio.create_task(scheduler.run(stop))

    async def parked():
        while not scheduler._notify_tasks:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(parked(), timeout=5)
    # A successful run now sends two events (start, then completion), both
    # parked on the same ``release`` -- there can be one or both in flight
    # by the time this observes the set, depending on scheduling.
    assert len(scheduler._notify_tasks) >= 1
    # stop before release: with poll_seconds=0.01, releasing first can let
    # the scheduler start ANOTHER run before ``run()`` notices ``stop`` --
    # that run's sends complete immediately (release is already set) and can
    # still be sitting in _notify_tasks, done-callback not yet ticked, when
    # this function's final check runs. Setting stop first closes off any
    # further run; the in-flight run keeps going (run()'s shutdown path
    # never touches _notify_tasks, so it is not cancelled), and awaiting
    # ``task`` below only waits for the poll loop to exit, not for the
    # notification tasks it started.
    stop.set()
    release.set()
    await task

    async def drained():
        while scheduler._notify_tasks:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(drained(), timeout=5)
    assert not scheduler._notify_tasks, "the done-callback must drop the reference"


EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


async def test_a_callable_cadence_is_resolved_on_every_claim(session):
    """``Job.interval_seconds`` may be a zero-argument callable, and
    ``claim_due`` must call it per poll rather than once. A cadence read at
    factory time is the whole of the bug this guards."""
    cadence = [3600.0]
    job = Job(name="live", interval_seconds=lambda: cadence[0], run=None)

    assert await claim_due(session, job) is True
    await session.execute(
        text("UPDATE scheduled_runs SET last_started_at = now() - interval '30 minutes'")
    )
    assert await claim_due(session, job) is False

    cadence[0] = 60.0
    assert await claim_due(session, job) is True


async def test_swapping_the_config_changes_when_a_scheduled_job_is_next_due(session):
    """The cadence edit an operator makes in the editor, end to end through
    the real factory: a drift sweep on the example config's 7-day cadence is
    not due 2 days after its last run, and becomes due the moment a swap makes
    it a daily job -- with nothing rebuilt and no restart.
    """
    config = load_config(EXAMPLE)
    assert config.scheduler.drift_days == 7, "precondition: the example cadence is weekly"
    holder = ConfigHolder(config)
    job = make_drift_job(holder)

    assert await claim_due(session, job) is True
    await session.execute(
        text("UPDATE scheduled_runs SET last_started_at = now() - interval '2 days'")
    )
    assert await claim_due(session, job) is False, (
        "precondition: 2 days into a 7-day cadence is not due"
    )

    holder.swap(
        config.model_copy(
            update={"scheduler": config.scheduler.model_copy(update={"drift_days": 1})}
        )
    )

    assert await claim_due(session, job) is True, (
        "the job kept the cadence it was built with: make_drift_job captured "
        "config.scheduler.drift_days instead of dereferencing the holder, so a "
        "cadence edit needs a restart"
    )


async def _runs(session):
    return (await session.execute(select(Run).order_by(Run.id))).scalars().all()


async def test_a_scheduled_pass_records_one_run_row_per_execution(session_factory, session):
    """The history `scheduled_runs` structurally cannot hold: its `name` is
    UNIQUE and every pass overwrites the same row, so it can produce exactly
    one duration per job. This is the second point, and the third."""
    scheduler = Scheduler(session_factory, [], poll_seconds=1)
    job = _job(name="demo", interval=3600)

    await scheduler._maybe_run(job)
    # Make it due again rather than waiting an hour.
    await session.execute(
        text("UPDATE scheduled_runs SET last_started_at = now() - interval '2 hours'")
    )
    await session.commit()
    await scheduler._maybe_run(job)

    rows = await _runs(session)
    assert len(rows) == 2
    assert [row.kind for row in rows] == ["scheduled", "scheduled"]
    assert [row.name for row in rows] == ["demo", "demo"]
    assert all(row.status == "ok" for row in rows)
    assert all(row.finished_at is not None for row in rows)


async def test_the_run_row_carries_the_bodys_summary_as_its_detail(session_factory, session):
    async def _run(session):
        return "reclaimed 3 stale job(s)"

    scheduler = Scheduler(session_factory, [], poll_seconds=1)
    await scheduler._maybe_run(_job(name="demo", run=_run))

    row = (await _runs(session))[0]
    assert row.detail == "reclaimed 3 stale job(s)"
    assert row.status == "ok"


async def test_a_failed_pass_records_the_class_name_only(session_factory, session):
    """Row 213, on the new surface: the run row's detail is a COPY of the
    string _maybe_run already narrowed, never a re-derivation. An ordinary
    exception's str() commonly embeds the URL it failed on -- in some shapes a
    token -- and this table is served by GET /api/stats/runs."""
    async def _boom(session):
        raise RuntimeError("https://plex.example/library?X-Plex-Token=secret")

    scheduler = Scheduler(session_factory, [], poll_seconds=1)
    await scheduler._maybe_run(_job(name="demo", run=_boom))

    row = (await _runs(session))[0]
    assert row.status == "failed"
    assert row.detail == "RuntimeError"
    assert "secret" not in (row.detail or "")
    assert "plex.example" not in (row.detail or "")


async def test_a_job_that_is_not_due_records_no_run_row(session_factory, session):
    """The row is opened after the claim, not before it: a poll that claims
    nothing is the common case (every job, most of the time), and a row per
    poll would be a history of the scheduler's heartbeat rather than of its
    work."""
    scheduler = Scheduler(session_factory, [], poll_seconds=1)
    job = _job(name="demo", interval=3600)

    await scheduler._maybe_run(job)
    await scheduler._maybe_run(job)  # not due

    assert len(await _runs(session)) == 1


async def test_stale_job_reclaim_writes_no_run_history_row(session_factory, session):
    """Critical fix: `stale_job_reclaim` is registered unconditionally
    (app.py:366) and runs every five minutes regardless of
    `scheduler.enabled`, while the retention trim only ever runs from inside
    the cleanup pass, which IS gated on that switch (app.py:383). Recording
    this job's passes would grow `runs` forever with nothing ever trimming
    it -- so it must write no row at all (`run_history.UNRECORDED`)."""
    scheduler = Scheduler(session_factory, [], poll_seconds=1)
    await scheduler._maybe_run(_job(name="stale_job_reclaim"))

    assert await _runs(session) == []


async def test_a_differently_named_pass_still_writes_a_run_history_row(
    session_factory, session
):
    """The other half of the same guarantee: only the one allowlisted name is
    excluded, not scheduled passes in general."""
    scheduler = Scheduler(session_factory, [], poll_seconds=1)
    await scheduler._maybe_run(_job(name="plex_prune"))

    rows = await _runs(session)
    assert len(rows) == 1
    assert rows[0].name == "plex_prune"


async def test_the_counts_stay_null_for_a_scheduled_run(session_factory, session):
    """Window attribution is honest only where the window IS the run's own
    work. A scheduled job's window overlaps whatever the worker pool happened
    to be doing, so its counts are not stamped -- NULL means "not attributed",
    the renders.size_bytes rule, rather than a zero that reads as a fact."""
    scheduler = Scheduler(session_factory, [], poll_seconds=1)
    await scheduler._maybe_run(_job(name="demo"))

    row = (await _runs(session))[0]
    assert row.processed is None
    assert row.failed is None
    assert row.deferred is None
    assert row.rendered_poster is None


async def _open_backdated_pass(session_factory) -> int:
    """An open full_pass row whose window already began an hour ago.

    The backdate is not cosmetic. This host's container clock steps BACKWARDS
    by a couple of seconds every half-minute, so a window built from two
    `now()` readings taken milliseconds apart is not reliably ordered, and a
    test that seeded a render "just after" the run opened would fail whenever
    the step landed in between. An hour of slack makes the window contain the
    seeded row no matter which way the clock moved. The `UPDATE ... interval`
    idiom is the one this file already uses at
    `test_a_job_is_claimable_once_its_interval_has_passed`.

    An hour is also comfortably inside FULL_PASS_CEILING_SECONDS (24 h), so the
    row still closes as `ok` and not as `timed_out`.
    """
    async with session_factory() as session:
        run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
        await session.execute(
            text("UPDATE runs SET started_at = now() - interval '1 hour' WHERE id = :id"),
            {"id": run_id},
        )
        await session.commit()
    return run_id


async def _scored_render(session_factory, *, source_mode="plex_generated"):
    """One media item and a render the database has just scored, so it lands
    inside the backdated window above."""
    async with session_factory() as session:
        item = await seed_media_item(
            session, f"rk-digest-{source_mode}", library="Movies",
            kind="movie", title="Dune",
        )
        session.add(
            Render(
                item_id=item.id, art_kind="poster", asset_path="/x.jpg",
                status="rendered", source_mode=source_mode,
                quality_scored_at=func.now(),
            )
        )
        await session.commit()


async def _run_until_closed(session_factory, scheduler, run_id):
    """Tick the real Scheduler until the full pass row closes, then stop it.

    The row, never the clock (roadmap row 119). Stopping the scheduler here
    only ends its polling loop -- `_start_notification` is fire-and-forget and
    can outlive it, so a positive test still awaits the notifier's call count
    afterwards; a negative test has no such wait and relies on the loop's own
    yields to have already run the notification task by the time it asserts.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.run(stop))

    async def _closed():
        async with session_factory() as probe:
            row = (await probe.execute(select(Run).where(Run.id == run_id))).scalar_one()
            return row.finished_at is not None

    try:
        async with asyncio.timeout(60):
            while not await _closed():
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


def _digest_config(enabled: bool):
    return ConfigHolder(
        load_config(EXAMPLE).model_copy(update={"actionable_digest_enabled": enabled})
    )


async def test_a_closed_full_pass_sends_one_digest_with_per_flag_counts(session_factory):
    """Roadmap row 236, through the wired loop rather than the helper: the
    repository's own recorded defect class is a green helper test beside a
    wired path that differs, so this drives a real Scheduler over a real open
    `full_pass` row and reads what the notifier was actually handed."""
    run_id = await _open_backdated_pass(session_factory)
    await _scored_render(session_factory)

    notifier = _RecordingNotifier()
    scheduler = Scheduler(
        session_factory, [], poll_seconds=0.01, notifier=notifier,
        config_holder=_digest_config(True),
    )
    await _run_until_closed(session_factory, scheduler, run_id)
    await _await_calls(notifier, 1)

    assert [event for event, _, _ in notifier.calls] == ["newly_actionable"]
    event, summary, detail = notifier.calls[0]
    assert summary == "1 newly actionable after a full pass"
    assert detail["plex_generated"] == 1


async def test_the_digest_detail_carries_flag_codes_and_counts_and_nothing_else(
    session_factory,
):
    """Row 213 over the whole payload, as an equality: an asset_path, a
    source_url, a stored detail string or a run id fails this by existing. The
    codes are the server's own vocabulary and the values are integers, which is
    the entire permitted alphabet for this event."""
    run_id = await _open_backdated_pass(session_factory)
    await _scored_render(session_factory)

    notifier = _RecordingNotifier()
    scheduler = Scheduler(
        session_factory, [], poll_seconds=0.01, notifier=notifier,
        config_holder=_digest_config(True),
    )
    await _run_until_closed(session_factory, scheduler, run_id)
    await _await_calls(notifier, 1)

    _, _, detail = notifier.calls[0]
    expected = {code: 0 for code in flags.FLAGS}
    expected["plex_generated"] = 1
    assert detail == expected
    assert list(detail) == list(flags.FLAGS), "registry order, so the chips read the same way"
    assert all(isinstance(value, int) for value in detail.values())


async def test_the_digest_is_not_sent_when_the_knob_is_off(session_factory):
    """Opt-in, and off by default. The same fixture as the sending test, one
    boolean apart -- so a gate that was never wired fails here and passes
    everywhere else."""
    run_id = await _open_backdated_pass(session_factory)
    await _scored_render(session_factory)

    notifier = _RecordingNotifier()
    scheduler = Scheduler(
        session_factory, [], poll_seconds=0.01, notifier=notifier,
        config_holder=_digest_config(False),
    )
    await _run_until_closed(session_factory, scheduler, run_id)

    assert notifier.calls == []


async def test_a_pass_that_produced_nothing_actionable_sends_no_digest(session_factory):
    """The suppression half of the dedupe rule: nothing is sent rather than an
    empty digest or a "nothing new" POST. A pass that scored a clean row -- and
    a pass that scored nothing at all -- is silent.

    `generate` is the ordinary source_mode (`db/models.py:88`'s own default),
    and a rendered row with no upload failure, no textless fallback, no logo
    fallback and no provider ladder recorded trips no default_on flag at all --
    which is what makes it the right negative here."""
    run_id = await _open_backdated_pass(session_factory)
    await _scored_render(session_factory, source_mode="generate")

    notifier = _RecordingNotifier()
    scheduler = Scheduler(
        session_factory, [], poll_seconds=0.01, notifier=notifier,
        config_holder=_digest_config(True),
    )
    await _run_until_closed(session_factory, scheduler, run_id)

    assert notifier.calls == []


async def test_a_scheduler_with_no_config_holder_still_closes_the_pass(session_factory):
    """The holder is optional for the reason the notifier is: every existing
    construction site and every test that builds a bare Scheduler must keep
    working, and the bookkeeping half of `_close_drained_runs` must never
    depend on the notification half."""
    async with session_factory() as session:
        run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
        await session.commit()

    notifier = _RecordingNotifier()
    scheduler = Scheduler(session_factory, [], poll_seconds=0.01, notifier=notifier)
    await _run_until_closed(session_factory, scheduler, run_id)

    async with session_factory() as session:
        row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "ok"
    assert notifier.calls == []


async def test_the_digest_fires_only_after_the_closers_own_commit(
    session_factory, monkeypatch
):
    """The post-commit ordering is not pinned
    by any existing test. This spies on ``actionable_window_counts`` --
    which ``_notify_newly_actionable`` awaits directly, not through the
    fire-and-forget notification seam, so its call is genuinely sequenced
    rather than racing a concurrently scheduled task -- and has the spy open
    its OWN fresh session to read the ``Run`` row at the exact moment
    counting starts. Postgres' own isolation, not timing, is what makes this
    deterministic: a fresh session cannot see another session's uncommitted
    write no matter how the event loop happens to schedule things.

    Moving ``await self._notify_newly_actionable(windows)`` above ``await
    session.commit()`` in ``_close_drained_runs`` must turn this red: the
    spy's fresh session would then read the row from OUTSIDE the closer's
    still-open transaction and see it not yet finished."""
    run_id = await _open_backdated_pass(session_factory)
    await _scored_render(session_factory)

    real_actionable_window_counts = core.actionable_window_counts
    observed: list[tuple[str, bool]] = []

    async def _spy(session, config, started_at, finished_at):
        async with session_factory() as probe:
            row = (
                await probe.execute(select(Run).where(Run.id == run_id))
            ).scalar_one()
        observed.append((row.status, row.finished_at is not None))
        return await real_actionable_window_counts(session, config, started_at, finished_at)

    monkeypatch.setattr(core, "actionable_window_counts", _spy)

    notifier = _RecordingNotifier()
    scheduler = Scheduler(
        session_factory, [], poll_seconds=0.01, notifier=notifier,
        config_holder=_digest_config(True),
    )
    await _run_until_closed(session_factory, scheduler, run_id)

    assert observed == [("ok", True)]


async def test_a_failing_digest_leaves_the_run_closed_and_the_scheduler_alive(
    session_factory, monkeypatch, caplog
):
    """The digest's own containment. Deleting
    the inner ``try``/``except`` in ``_notify_newly_actionable`` must turn
    this red -- an uncontained counting-query failure would propagate out of
    ``_close_drained_runs``, past its own already-exited ``try``, into
    ``Scheduler.run``, which has no handler of its own and would take the
    scheduler task down with it, breaking the method's own docstring promise
    ("bookkeeping must never take the scheduler down")."""
    run_id = await _open_backdated_pass(session_factory)
    await _scored_render(session_factory)

    async def _boom(session, config, started_at, finished_at):
        raise RuntimeError("counting query blew up")

    monkeypatch.setattr(core, "actionable_window_counts", _boom)

    notifier = _RecordingNotifier()
    scheduler = Scheduler(
        session_factory, [], poll_seconds=0.01, notifier=notifier,
        config_holder=_digest_config(True),
    )
    with caplog.at_level(logging.WARNING, logger="autoposter.scheduler.core"):
        await _run_until_closed(session_factory, scheduler, run_id)

    async with session_factory() as session:
        row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == "ok"
    assert row.finished_at is not None
    assert notifier.calls == []
    warnings = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
        and record.message == "scheduler: could not count newly actionable renders"
    ]
    assert warnings == ["scheduler: could not count newly actionable renders"]


async def _open_backdated_pass_hours(session_factory, hours_ago: int) -> int:
    """An open full_pass row backdated by a whole number of hours, for the
    two-rows-close-together test: hour-scale offsets so this host's clock
    (which steps backwards a couple of seconds every half-minute) can never
    reorder the two rows relative to each other or to the render stamps
    below."""
    async with session_factory() as session:
        run_id = await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
        await session.execute(
            text(
                "UPDATE runs SET started_at = now() - make_interval(hours => :h) "
                "WHERE id = :id"
            ),
            {"h": hours_ago, "id": run_id},
        )
        await session.commit()
    return run_id


async def _scored_render_minutes_ago(
    session_factory, *, rating_key: str, minutes_ago: int, source_mode="plex_generated"
):
    """A render stamped as scored a fixed number of minutes in the past,
    rather than at ``now()``, so it can be placed inside one closing row's
    window and outside another's."""
    async with session_factory() as session:
        item = await seed_media_item(
            session, rating_key, library="Movies", kind="movie", title="Dune",
        )
        render = Render(
            item_id=item.id, art_kind="poster", asset_path="/x.jpg",
            status="rendered", source_mode=source_mode,
        )
        session.add(render)
        await session.flush()
        await session.execute(
            text(
                "UPDATE renders SET quality_scored_at = "
                "now() - make_interval(mins => :m) WHERE id = :id"
            ),
            {"m": minutes_ago, "id": render.id},
        )
        await session.commit()


async def test_a_drain_that_closes_two_full_passes_sends_two_digests_each_over_its_own_window(
    session_factory,
):
    """The one behaviour the suite could not see until now -- a second
    ``POST /api/full-pass`` while the
    first pass is still draining opens a second ``full_pass`` row, and both
    close together on the same drain (``run_history.py``'s own docstring).
    Each closed row gets its own POST over its own window, so the
    younger row's window is a sub-interval of the older's and a render can be
    counted in both.

    The older row's window starts 3 hours ago; the younger's starts 1 hour
    ago; both end at the same close-time ``now()``. One render is scored 2
    hours ago -- inside the older window, before the younger one even opens
    -- and counts only for the older row. A second render is scored 10
    minutes ago -- inside both windows -- and counts for both. That makes
    the older digest's count (2) and the younger digest's count (1) differ,
    so a collector or emitter mutation that collapses the two windows into
    one (``windows[0]``-only, a stray ``break``, or appending the same pair
    twice) changes what this test observes instead of passing unnoticed."""
    older_id = await _open_backdated_pass_hours(session_factory, 3)
    younger_id = await _open_backdated_pass_hours(session_factory, 1)
    await _scored_render_minutes_ago(
        session_factory, rating_key="rk-digest-two-older", minutes_ago=120
    )
    await _scored_render_minutes_ago(
        session_factory, rating_key="rk-digest-two-both", minutes_ago=10
    )

    notifier = _RecordingNotifier()
    scheduler = Scheduler(
        session_factory, [], poll_seconds=0.01, notifier=notifier,
        config_holder=_digest_config(True),
    )

    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.run(stop))

    async def _both_closed():
        async with session_factory() as probe:
            rows = (
                await probe.execute(
                    select(Run).where(Run.id.in_([older_id, younger_id]))
                )
            ).scalars().all()
            return len(rows) == 2 and all(row.finished_at is not None for row in rows)

    try:
        async with asyncio.timeout(60):
            while not await _both_closed():
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    await _await_calls(notifier, 2)

    assert [event for event, _, _ in notifier.calls] == [
        "newly_actionable", "newly_actionable",
    ]
    counts = [detail["plex_generated"] for _, _, detail in notifier.calls]
    assert counts == [2, 1], (
        "the older row's window (3h) must count both renders and the "
        "younger row's window (1h) must count only the one inside it"
    )
    summaries = [summary for _, summary, _ in notifier.calls]
    assert summaries == [
        "2 newly actionable after a full pass",
        "1 newly actionable after a full pass",
    ]


# --- the running job's name, for the event-loop lag monitor (perf A9) ---


async def test_the_running_job_is_named_while_its_body_runs_and_cleared_after(
    session_factory,
):
    """``current_job`` is what loop_lag.py names in its WARNING. It must read
    the job's name for the whole run and ``None`` once the run is over -- a
    name left behind would pin every later stall on a job that finished."""
    seen = []
    scheduler = Scheduler(session_factory, [], poll_seconds=60)

    async def body(session):
        seen.append(scheduler.current_job)
        return "ok"

    assert scheduler.current_job is None
    await scheduler._maybe_run(_job(name="plex_prune", run=body))

    assert seen == ["plex_prune"]
    assert scheduler.current_job is None


async def test_a_failing_job_still_clears_the_running_name(session_factory):
    seen = []
    scheduler = Scheduler(session_factory, [], poll_seconds=60)

    async def body(session):
        seen.append(scheduler.current_job)
        raise RuntimeError("job exploded")

    await scheduler._maybe_run(_job(name="plex_merge", run=body))

    assert seen == ["plex_merge"]
    assert scheduler.current_job is None


async def test_a_job_that_is_not_due_is_never_named(session_factory):
    scheduler = Scheduler(session_factory, [], poll_seconds=60)
    job = _job(name="demo", interval=3600)
    await scheduler._maybe_run(job)

    ran = []

    async def body(session):
        ran.append(scheduler.current_job)
        return "ok"

    await scheduler._maybe_run(_job(name="demo", interval=3600, run=body))
    assert ran == []
    assert scheduler.current_job is None


async def test_the_lag_monitor_names_a_scheduled_job_that_blocks_the_loop(
    session_factory, caplog
):
    """The production path end to end: a real claim, a body that blocks the
    loop the way a synchronous plexapi call does, and the real monitor reading
    the real scheduler's attribute. The monitor wakes only after the stall --
    at the result write's first database await -- and the name has to still
    be set then, which is why ``_maybe_run`` clears it after the result write
    rather than straight after the body."""
    scheduler = Scheduler(session_factory, [], poll_seconds=60)

    async def body(session):
        time.sleep(0.6)
        return "blocked the loop"

    monitor = asyncio.create_task(monitor_loop_lag(lambda: scheduler.current_job))
    try:
        with caplog.at_level(logging.WARNING, logger="autoposter.loop_lag"):
            await asyncio.sleep(0)
            await scheduler._maybe_run(_job(name="collections_reconcile", run=body))
            async with asyncio.timeout(5):
                while not [r for r in caplog.records if r.name == "autoposter.loop_lag"]:
                    await asyncio.sleep(0.01)
    finally:
        monitor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor

    warnings = [
        r.getMessage() for r in caplog.records
        if r.name == "autoposter.loop_lag" and r.levelno == logging.WARNING
    ]
    assert any("scheduled job running: collections_reconcile" in m for m in warnings), warnings
