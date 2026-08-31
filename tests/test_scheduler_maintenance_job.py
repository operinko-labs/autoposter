"""Row 36 — Plex maintenance toggles. All three off by default.

The negative case is the test: with nothing switched on, the job must call
NOTHING on the Plex library. empty_trash especially -- Plex's own
auto-empty-trash is off on the production server, so mass item disappearance
currently implies deliberate action, and a default-on toggle would destroy
that signal.
"""
from pathlib import Path

from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import load_config
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
    assert summary == "ran clean_bundles; empty_trash failed: plex said no"


def test_the_cadence_comes_off_the_holder(session):
    holder = _holder()
    job = make_maintenance_job(holder, lambda: None)
    assert job.name == "plex_maintenance"
    assert job.current_interval() == holder.current.scheduler.maintenance_days * 24 * 3600
