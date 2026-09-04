"""Row 36 — Plex maintenance toggles. All three off by default.

The negative case is the test: with nothing switched on, the job must call
NOTHING on the Plex library. empty_trash especially -- Plex's own
auto-empty-trash is off on the production server, so mass item disappearance
currently implies deliberate action, and a default-on toggle would destroy
that signal.
"""
import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import load_config
from autoposter.db.models import ScheduledRun
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import make_maintenance_job

EXAMPLE = Path("config/autoposter.example.yaml")


class RecordingLibrary:
    def __init__(self):
        self.calls = []

    def cleanBundles(self):
        self.calls.append("cleanBundles")

    def emptyTrash(self):
        self.calls.append("emptyTrash")

    def optimize(self):
        self.calls.append("optimize")


class RecordingServer:
    def __init__(self):
        self.library = RecordingLibrary()


def _holder(**maintenance):
    config = load_config(EXAMPLE)
    config = config.model_copy(
        update={"maintenance": config.maintenance.model_copy(update=maintenance)}
    )
    return ConfigHolder(config)


async def test_nothing_enabled_calls_nothing(session):
    server = RecordingServer()
    job = make_maintenance_job(_holder(), lambda: server)
    summary = await job.run(session)
    assert server.library.calls == []
    assert summary == "skipped: no maintenance operation is enabled"


async def test_only_the_enabled_operations_run(session):
    server = RecordingServer()
    job = make_maintenance_job(
        _holder(clean_bundles=True, optimize=True), lambda: server
    )
    summary = await job.run(session)
    assert server.library.calls == ["cleanBundles", "optimize"]
    assert summary == "ran clean_bundles, optimize"


async def test_a_failing_operation_is_reported_not_raised(session):
    class Broken(RecordingLibrary):
        def emptyTrash(self):
            raise RuntimeError("plex said no")

    server = RecordingServer()
    server.library = Broken()
    job = make_maintenance_job(
        _holder(clean_bundles=True, empty_trash=True), lambda: server
    )
    summary = await job.run(session)
    assert server.library.calls == ["cleanBundles"]
    # Class name only on the served summary (roadmap row 213's rule); the
    # message and traceback are on the exc_info warning -- the pod log.
    assert summary == "ran clean_bundles; empty_trash failed (RuntimeError)"


def test_the_cadence_comes_off_the_holder(session):
    holder = _holder()
    job = make_maintenance_job(holder, lambda: None)
    assert job.name == "plex_maintenance"
    assert job.current_interval() == holder.current.scheduler.maintenance_days * 24 * 3600


async def test_a_plex_failure_reaches_last_detail_as_a_class_name_only(
    session_factory, caplog
):
    """The audit's one CRITICAL: a plexapi/requests failure's str() carries
    the host, port and URL it failed on, and the maintenance summary is
    scheduled_runs.last_detail -- served by /api/snapshots, the dashboard
    stream and the notification payload. Driven through the real Scheduler
    so the column itself is what is asserted on, not the job's return
    value. The full message stays on the WARNING (the pod log, row 207's
    trusted sink), pinned here so a downgrade of that line goes red too."""

    class Unreachable(RecordingLibrary):
        def emptyTrash(self):
            raise ConnectionError(
                "HTTPConnectionPool(host='plex.internal', port=32400): Max retries "
                "exceeded with url: /library/clean/bundles?path=/mnt/media/Movies"
            )

    server = RecordingServer()
    server.library = Unreachable()
    job = make_maintenance_job(_holder(empty_trash=True, optimize=True), lambda: server)

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [job], poll_seconds=0.01)
    with caplog.at_level(logging.WARNING, logger="autoposter.scheduler.jobs"):
        task = asyncio.create_task(scheduler.run(stop))
        try:
            async with asyncio.timeout(5):
                while True:
                    async with session_factory() as check:
                        row = (
                            await check.execute(select(ScheduledRun))
                        ).scalar_one_or_none()
                    if row is not None and row.last_status == "ok":
                        break
                    await asyncio.sleep(0.01)
        finally:
            stop.set()
            await task

    assert server.library.calls == ["optimize"]
    assert row.last_detail == "ran optimize; empty_trash failed (ConnectionError)"
    for marker in ("plex.internal", "32400", "/library/clean/bundles", "/mnt/"):
        assert marker not in row.last_detail

    warned = [r for r in caplog.records if r.exc_info is not None]
    assert [r.getMessage() for r in warned] == ["maintenance: empty_trash failed"]
    for marker in ("plex.internal", "32400", "/library/clean/bundles", "/mnt/"):
        assert marker in caplog.text
