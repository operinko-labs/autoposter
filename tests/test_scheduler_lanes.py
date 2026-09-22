"""The scheduler's lanes (perf workstream C3).

Heavy jobs run one after another in one lane task; each light job runs as its
own task, never overlapping itself; shutdown cancels and awaits both. Every
wait here is on a recorded outcome or a counter under ``asyncio.timeout``,
never a fixed sleep, and every emptiness assertion is the postcondition of an
awaited shutdown -- the tiny-poll rule for ``poll_seconds=0.01`` schedulers.
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from autoposter.api.routes import SCHEDULED_JOB_NAMES
from autoposter.db.models import Run, ScheduledRun
from autoposter.scheduler import core
from autoposter.scheduler.core import Job, Scheduler
from autoposter.scheduler.run_history import open_run

# Registry-derived, never literals: the names the lanes classify by.
LIGHT_A, LIGHT_B = sorted(core.LIGHT_JOBS)[:2]


def _job(name, interval=3600, run=None):
    async def _noop(session):
        return "ok"

    return Job(name=name, interval_seconds=interval, run=run or _noop)


async def _until(predicate, seconds=30):
    """Wait on the behaviour, not the clock (roadmap row 119)."""
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


async def _shutdown(stop, task):
    """The lifespan's own sequence (app.py): set the stop event, cancel the
    scheduler task, gather it."""
    stop.set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_the_light_jobs_are_real_scheduled_job_names():
    """A typo in ``LIGHT_JOBS`` would quietly put that job in the heavy lane."""
    assert len(core.LIGHT_JOBS) == 3
    assert core.LIGHT_JOBS <= SCHEDULED_JOB_NAMES


async def test_a_blocking_heavy_job_does_not_delay_a_light_job(session_factory):
    heavy_started = asyncio.Event()
    release = asyncio.Event()
    light_runs: list[bool] = []

    async def heavy(session):
        heavy_started.set()
        await release.wait()
        return "heavy done"

    async def light(session):
        light_runs.append(True)
        return "light"

    stop = asyncio.Event()
    scheduler = Scheduler(
        session_factory,
        [_job("heavy", run=heavy), _job(LIGHT_A, interval=0, run=light)],
        poll_seconds=0.01,
    )
    task = asyncio.create_task(scheduler.run(stop))
    try:
        await _until(heavy_started.is_set)
        before = len(light_runs)
        await _until(lambda: len(light_runs) >= before + 2)
        assert not scheduler._heavy_lane.done(), "the heavy job was held open throughout"
    finally:
        release.set()
        await _shutdown(stop, task)

    assert len(light_runs) >= before + 2


async def test_heavy_jobs_never_overlap(session_factory):
    active = 0
    peak = 0
    runs = {"one": 0, "two": 0}

    def body(name):
        async def run(session):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            active -= 1
            runs[name] += 1
            return name
        return run

    stop = asyncio.Event()
    scheduler = Scheduler(
        session_factory,
        [_job("one", interval=0, run=body("one")), _job("two", interval=0, run=body("two"))],
        poll_seconds=0.01,
    )
    task = asyncio.create_task(scheduler.run(stop))
    try:
        await _until(lambda: runs["one"] >= 3 and runs["two"] >= 3)
    finally:
        await _shutdown(stop, task)

    assert peak == 1


async def test_a_light_job_never_overlaps_itself(session_factory):
    entries = 0
    gate = asyncio.Event()
    ticks: list[bool] = []

    async def light(session):
        nonlocal entries
        entries += 1
        await gate.wait()
        return "light"

    async def tick(session):
        ticks.append(True)
        return "tick"

    stop = asyncio.Event()
    scheduler = Scheduler(
        session_factory,
        [_job("tick", interval=0, run=tick), _job(LIGHT_A, interval=0, run=light)],
        poll_seconds=0.01,
    )
    task = asyncio.create_task(scheduler.run(stop))
    try:
        await _until(lambda: entries == 1)
        seen = len(ticks)
        # Three more ticks, each one dispatching every light job that is not
        # already running -- the running set is all that holds this one back.
        await _until(lambda: len(ticks) >= seen + 3)
        assert entries == 1
    finally:
        gate.set()
        await _shutdown(stop, task)


async def test_a_run_now_on_a_running_light_job_queues_one_more_run(session_factory):
    """``run_scheduled_job_now`` nulls ``last_started_at``. A light job that is
    still running is not started twice; it runs once more after it finishes."""
    entries = 0
    first = asyncio.Event()
    gate = asyncio.Event()
    ticks: list[bool] = []

    async def light(session):
        nonlocal entries
        entries += 1
        if entries == 1:
            first.set()
            await gate.wait()
        return "light"

    async def tick(session):
        ticks.append(True)
        return "tick"

    stop = asyncio.Event()
    scheduler = Scheduler(
        session_factory,
        [_job("tick", interval=0, run=tick), _job(LIGHT_B, interval=3600, run=light)],
        poll_seconds=0.01,
    )
    task = asyncio.create_task(scheduler.run(stop))
    try:
        await _until(first.is_set)
        async with session_factory() as session:
            # The route's own statement (api/routes.py run_scheduled_job_now).
            await session.execute(
                insert(ScheduledRun).values(name=LIGHT_B).on_conflict_do_update(
                    index_elements=["name"], set_={"last_started_at": None}
                )
            )
            await session.commit()
        seen = len(ticks)
        await _until(lambda: len(ticks) >= seen + 3)
        assert entries == 1, "a second copy started while the first was running"
        gate.set()
        await _until(lambda: entries == 2)
    finally:
        gate.set()
        await _shutdown(stop, task)


async def test_the_lag_monitor_name_covers_every_concurrently_running_job(
    session_factory,
):
    """With lanes, a heavy job and a light job can both be mid-run when the
    loop stalls; ``current_job`` (what loop_lag.py names) must name both, not
    whichever claimed last, and must drop each once its run is over."""
    heavy_started = asyncio.Event()
    light_started = asyncio.Event()
    release = asyncio.Event()

    async def heavy(session):
        heavy_started.set()
        await release.wait()
        return "heavy"

    async def light(session):
        light_started.set()
        await release.wait()
        return "light"

    stop = asyncio.Event()
    scheduler = Scheduler(
        session_factory,
        [_job("heavy", run=heavy), _job(LIGHT_A, run=light)],
        poll_seconds=0.01,
    )
    task = asyncio.create_task(scheduler.run(stop))
    try:
        await _until(lambda: heavy_started.is_set() and light_started.is_set())
        named = scheduler.current_job
    finally:
        release.set()
        await _shutdown(stop, task)

    assert named is not None and set(named.split(", ")) == {"heavy", LIGHT_A}
    # Postcondition of the awaited shutdown: nothing is still named.
    assert scheduler.current_job is None


async def test_shutdown_cancels_and_awaits_a_mid_run_job(session_factory):
    started = asyncio.Event()
    cancelled: list[bool] = []

    async def body(session):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job("demo", run=body)], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.wait_for(started.wait(), timeout=30)

    await _shutdown(stop, task)

    # Postconditions of the awaited shutdown, never observed mid-drain.
    assert cancelled == [True]
    assert scheduler._heavy_lane is not None and scheduler._heavy_lane.done()
    assert not scheduler._light_tasks
    assert not scheduler._running
    # The cancelled run is marked "interrupted" at the next open, as today.
    async with session_factory() as session:
        await open_run(session, kind="scheduled", name="demo")
        await session.commit()
        statuses = (
            await session.execute(
                select(Run.status).where(Run.name == "demo").order_by(Run.id)
            )
        ).scalars().all()
    assert statuses == ["interrupted", "running"]
