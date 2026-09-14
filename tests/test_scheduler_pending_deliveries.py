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
    return SimpleNamespace(scheduler=SimpleNamespace(
        pending_deliveries_minutes=minutes, delivery_attempts=8,
    ))


async def test_the_job_is_named_and_reads_its_cadence_live(session):
    holder = ConfigHolder(_config(minutes=7))
    job = make_pending_deliveries_job(holder, lambda: {}, http=None, mdblist=None)

    assert job.name == "pending_deliveries"
    assert job.current_interval() == 7 * 60
    assert (await job.run(session)).startswith("pending deliveries: 0 due")


def test_the_cadence_floors_at_sixty_seconds():
    """A pending-deliveries cadence of under a minute is still one poll a
    minute, not sub-minute hammering -- the same floor other cadences are held to."""
    holder = ConfigHolder(_config(minutes=0))
    job = make_pending_deliveries_job(holder, lambda: {}, http=None, mdblist=None)

    assert job.current_interval() == 60


async def test_the_job_records_a_scheduled_run_through_the_real_scheduler(session_factory):
    """Proves the job's runs ARE recorded, not just that it is registered.

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


async def test_the_pass_summary_carries_the_per_server_sentence(session, config_factory):
    """spec §2: the run history keeps the sentence so the dashboard can show
    it. The two tests above only check the ``"pending deliveries: 0 due"``
    prefix, so this pins the per-server clause too -- ``scheduler/core.py``
    already stores whatever ``retry_pending_deliveries`` returns, and a
    future refactor could silently trim the sentence off without reddening
    either of them."""
    from datetime import datetime, timezone

    from autoposter import deliveries
    from autoposter.render import pipeline
    from autoposter.servers.registry import Servers
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

    config = config_factory()
    config.badges.upload_to_jellyfin = True
    item = await pipeline._upsert_media_item(
        session, resolved("jellyfin", "j1", file_path="/m.mkv")
    )
    render = await pipeline._get_or_create_render(session, item, "poster", "/a/p.jpg")
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()

    summary = await deliveries.retry_pending_deliveries(
        session,
        Servers({"jellyfin": FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)}),
        config, now=datetime.now(timezone.utc),
    )

    assert summary.endswith("jellyfin: 1 due, 0 uploaded, 0 written, 1 pending, 0 failed, 0 skipped")


async def test_the_per_server_sentence_lands_in_the_stored_run_row(session_factory):
    """Review test gap 6: spec §2's binding constraint is that the per-server
    sentence reaches the run history, and nothing proved it end to end -- the
    scheduler test above asserts only the `0 due` prefix of a zero-due pass,
    and the sentence test asserts the RETURNED string. `scheduler/core.py`
    stores whatever the job returns, and the length of what it stores is a
    `Text` column nobody had exercised with a real clause."""
    from autoposter import deliveries
    from autoposter.render import pipeline
    from media_server_doubles import resolved

    holder = ConfigHolder(_config(minutes=7))
    # An empty registry, so the one due row takes the `config: server
    # removed` branch: this test is about the sentence reaching the row, and
    # that branch needs no server double and no badge config.
    job = make_pending_deliveries_job(holder, lambda: {}, http=None, mdblist=None)

    async with session_factory() as seed:
        item = await pipeline._upsert_media_item(
            seed, resolved("jellyfin", "j1", file_path="/m.mkv")
        )
        render = await pipeline._get_or_create_render(seed, item, "poster", "/a/p.jpg")
        await deliveries.record(seed, render.id, "jellyfin", "pending", retry_in=0)
        await deliveries.record_metadata(seed, item.id, "jellyfin", "pending", retry_in=0)
        await seed.commit()

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
    assert row.last_status == "ok"
    assert row.last_detail == (
        "pending deliveries: 2 due, 0 done, 0 still pending, 2 failed, 0 skipped; "
        "jellyfin: 2 due, 0 uploaded, 0 written, 0 pending, 2 failed, 0 skipped"
    )
