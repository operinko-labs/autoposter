"""Scheduler bookkeeping and claiming."""
import asyncio
import inspect
import json
import logging
from pathlib import Path

import httpx
from sqlalchemy import select, text

from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import load_config
from autoposter.config.schema import NotificationsConfig
from autoposter.db.models import Run, ScheduledRun
from autoposter.notify.dispatch import build_notifier
from autoposter.scheduler.core import Job, Scheduler, claim_due
from autoposter.scheduler.jobs import make_drift_job


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
        await asyncio.sleep(0.1)
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
