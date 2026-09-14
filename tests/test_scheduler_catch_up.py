"""``make_catch_up_drain_job``: the catch-up drain's scheduled pass (spec §3).

Two jobs in one pass: it turns the automatic triggers' queued server NAMES
into runs, and then takes one batch from every open run whose cadence has
elapsed.
"""
import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from autoposter.config.holder import ConfigHolder
from autoposter.db.models import Run, ScheduledRun
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import make_catch_up_drain_job
from autoposter.servers.registry import Servers

from media_server_doubles import JELLYFIN_CAPS, FakeMediaServer


def _config(poll: int = 60) -> SimpleNamespace:
    return SimpleNamespace(
        scheduler=SimpleNamespace(
            catch_up_poll_seconds=poll,
            pending_deliveries_minutes=15,
            delivery_attempts=8,
        )
    )


def _job(holder, servers=None, requests=None, health=None):
    return make_catch_up_drain_job(
        holder,
        lambda: Servers({}) if servers is None else servers,
        lambda: [] if requests is None else requests,
        lambda: {} if health is None else health,
        http=None,
        mdblist=None,
    )


def test_the_job_is_named_and_reads_its_cadence_live():
    holder = ConfigHolder(_config(poll=30))
    job = _job(holder)

    assert job.name == "catch_up_drain"
    # Floored at a minute, like every other cadence in this module.
    assert job.current_interval() == 60


def test_the_cadence_is_the_operators_when_it_clears_the_floor():
    holder = ConfigHolder(_config(poll=300))

    assert _job(holder).current_interval() == 300


async def test_the_job_drains_and_reports(session):
    holder = ConfigHolder(_config())

    assert await _job(holder).run(session) == "catch-up: nothing in flight"


async def test_a_queued_request_starts_a_catch_up_before_the_drain(session, config_factory):
    holder = ConfigHolder(config_factory())
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS, libraries={"Movies"})
    requests = ["jellyfin"]
    job = _job(holder, servers=Servers({"jellyfin": jf}), requests=requests)

    detail = await job.run(session)

    assert detail.startswith("started jellyfin; ")
    assert requests == []
    run = (await session.execute(select(Run).where(Run.kind == "catch_up"))).scalar_one()
    assert run.server == "jellyfin"


async def test_a_refused_request_is_dropped_rather_than_retried_forever(
    session, config_factory
):
    """Both refusals this can hit -- "already in flight" and "the server is
    down" -- are answered by dropping the name: the work is either already
    happening or the next trigger raises it again. Re-queueing would turn a
    down server into a growing list nothing ever clears."""
    holder = ConfigHolder(config_factory())
    requests = ["emby"]
    job = _job(holder, requests=requests)

    assert await job.run(session) == "catch-up: nothing in flight"
    assert requests == []


async def test_a_refused_request_leaves_the_session_usable_for_the_next_one(
    session, config_factory
):
    """The refusal rolls back before the loop continues: `start_catch_up`
    takes an advisory lock and reads before it refuses, so a queued name after
    a refused one would otherwise run inside a transaction the refusal left
    behind."""
    holder = ConfigHolder(config_factory())
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS, libraries={"Movies"})
    requests = ["emby", "jellyfin"]
    job = _job(holder, servers=Servers({"jellyfin": jf}), requests=requests)

    detail = await job.run(session)

    assert detail.startswith("started jellyfin; ")
    assert requests == []


async def test_the_job_records_a_scheduled_run_through_the_real_scheduler(session_factory):
    """Proves the pass's runs ARE recorded, not merely that the job exists:
    the real ``Scheduler`` over the real job, with the ``scheduled_runs`` row
    read back. The wait is on the behaviour rather than on the clock, the
    shape ``tests/test_scheduler_core.py`` uses (roadmap row 119)."""
    job = _job(ConfigHolder(_config()))

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
    assert row.name == "catch_up_drain"
    assert row.last_status == "ok"
    assert row.last_detail == "catch-up: nothing in flight"
