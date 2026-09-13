"""``make_pending_deliveries_job``: the scheduled pending-deliveries retry
pass (``deliveries.retry_pending_deliveries``, spec Sec 5.3).

Registered unconditionally (see ``app.py``), so unlike every other job in
``scheduler/jobs.py`` this one takes no ``scheduler.enabled`` gate of its
own -- these tests only exercise the job's name, its cadence and that it
wraps the retry pass, the same shape ``test_scheduler_drift_job.py`` and
friends use for their own job factories.
"""
from types import SimpleNamespace

from autoposter.config.holder import ConfigHolder
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
