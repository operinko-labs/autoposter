"""``make_pending_deliveries_job``: the scheduled pending-deliveries retry
pass (``deliveries.retry_pending_deliveries``, spec Sec 5.3).

Registered unconditionally (see ``app.py``), so unlike every other job in
``scheduler/jobs.py`` this one takes no ``scheduler.enabled`` gate of its
own -- these tests only exercise the job's name, its cadence and that it
wraps the retry pass, the same shape ``test_scheduler_drift_job.py`` and
friends use for their own job factories.
"""
import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from autoposter.config.holder import ConfigHolder
from autoposter.db.models import ScheduledRun
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import make_pending_deliveries_job


def _config(minutes: int) -> SimpleNamespace:
    return SimpleNamespace(scheduler=SimpleNamespace(pending_deliveries_minutes=minutes))


async def test_the_job_is_named_and_reads_its_cadence_live(session):
    holder = ConfigHolder(_config(minutes=7))
    job = make_pending_deliveries_job(holder, lambda: {}, http=None, mdblist=None)

    assert job.name == "pending_deliveries"
    assert job.current_interval() == 7 * 60
    assert (await job.run(session)).startswith("pending deliveries: 0 due")


def test_the_cadence_floors_at_sixty_seconds():
    """A pending-deliveries cadence of under a minute is still one poll a
    minute, not sub-minute hammering -- the same floor the brief rules on."""
    holder = ConfigHolder(_config(minutes=0))
    job = make_pending_deliveries_job(holder, lambda: {}, http=None, mdblist=None)

    assert job.current_interval() == 60


async def test_the_job_records_a_scheduled_run_through_the_real_scheduler(session_factory):
    """Gap 5 (fix round 3): the addendum's "its runs ARE recorded" half.

    ``tests/test_app.py`` proves the job is registered and the assertion
    ``"pending_deliveries" not in UNRECORDED`` is a naming check; this drives
    the real ``Scheduler`` over the real job and reads the ``scheduled_runs``
    row back. The wait is on the behaviour rather than on the clock, the
    shape ``tests/test_scheduler_core.py`` uses for the same reason (roadmap
    row 119), and the ``finally`` stops the scheduler on both paths."""
    holder = ConfigHolder(_config(minutes=7))
    job = make_pending_deliveries_job(holder, lambda: {}, http=None, mdblist=None)

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [job], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    row = None
    try:
        async with asyncio.timeout(60):
            while row is None or row.last_finished_at is None:
                await asyncio.sleep(0.01)
                async with session_factory() as check:
                    row = (await check.execute(select(ScheduledRun))).scalar_one_or_none()
    except TimeoutError:
        pass
    finally:
        stop.set()
        await task

    assert row is not None, "the registered job never recorded a run"
    assert row.name == "pending_deliveries"
    assert row.last_status == "ok"
    assert row.last_detail.startswith("pending deliveries: 0 due")
