"""Row 36 — Plex maintenance toggles. All three off by default.

The negative case is the test: with nothing switched on, the job must call
NOTHING on the Plex library. empty_trash especially -- Plex's own
auto-empty-trash is off on the production server, so mass item disappearance
currently implies deliberate action, and a default-on toggle would destroy
that signal.
"""
import asyncio
import logging
import threading
from pathlib import Path

import requests
from sqlalchemy import select

from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import build_config, load_config, read_config_document
from autoposter.db.models import ScheduledRun
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import make_maintenance_job

EXAMPLE = Path("config/autoposter.example.yaml")


class RecordingSection:
    def __init__(self, title: str, calls: list):
        self.title = title
        self._calls = calls

    def emptyTrash(self):
        self._calls.append(f"section:{self.title}")


class RecordingLibrary:
    def __init__(self):
        self.calls = []
        self.sections_asked = []

    def cleanBundles(self):
        self.calls.append("cleanBundles")

    def emptyTrash(self):
        self.calls.append("emptyTrash")

    def optimize(self):
        self.calls.append("optimize")

    def section(self, title):
        self.sections_asked.append(title)
        return RecordingSection(title, self.calls)


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


def _holder_with_libraries(libraries: dict, **maintenance):
    """A holder whose config carries a ``libraries:`` block.

    Built through ``build_config`` rather than ``model_copy``, because the
    block's validators are part of what makes the job's behaviour legal.
    """
    document = read_config_document(EXAMPLE)
    document["libraries"] = libraries
    if maintenance:
        document.setdefault("maintenance", {}).update(maintenance)
    return ConfigHolder(build_config(document))


async def test_empty_trash_stays_server_wide_when_no_library_overrides_it(session):
    """Gate off. A deployment that never opened the matrix must make the one
    call it made before this row, and must keep reaching sections
    ``collections.libraries`` does not name."""
    server = RecordingServer()
    job = make_maintenance_job(_holder_with_libraries({}, empty_trash=True), lambda: server)
    summary = await job.run(session)
    assert server.library.calls == ["emptyTrash"]
    assert server.library.sections_asked == []
    assert summary == "ran empty_trash"


async def test_empty_trash_runs_only_for_the_libraries_that_enable_it(session):
    """THE entry-point test for maintenance, through the job the
    scheduler registers.

    ``Movies`` states ``empty_trash: true``; ``TV Shows`` states nothing and
    the global is ``false``, so it inherits ``false``. Both halves in one
    assertion: the stated value wins, and the unstated one is not swept up
    with it.
    """
    server = RecordingServer()
    job = make_maintenance_job(
        _holder_with_libraries({"Movies": {"maintenance": {"empty_trash": True}}}),
        lambda: server,
    )
    summary = await job.run(session)
    assert server.library.calls == ["section:Movies"]
    # Only the libraries that want it: the flag is resolved off the validated
    # model BEFORE Plex is asked for a section, so a library that inherits a
    # false global costs no request at all.
    assert server.library.sections_asked == ["Movies"]
    assert "empty_trash" in summary


async def test_a_library_can_opt_out_of_a_global_empty_trash(session):
    """The other direction, which is the one an operator actually asks for:
    the global says yes, one library says no, and the rest still run."""
    server = RecordingServer()
    job = make_maintenance_job(
        _holder_with_libraries(
            {"Movies": {"maintenance": {"empty_trash": False}}}, empty_trash=True,
        ),
        lambda: server,
    )
    await job.run(session)
    assert server.library.calls == ["section:TV Shows"]


async def test_the_two_server_wide_operations_ignore_the_matrix(session):
    """``clean_bundles`` and ``optimize`` have no per-section form, so they
    are refused inside a library block at load (roadmap row 92) and the job
    goes on making one call each. Pinned here so the refusal and the job
    cannot drift apart into 'refused, and also quietly per-library'."""
    server = RecordingServer()
    job = make_maintenance_job(
        _holder_with_libraries(
            {"Movies": {"maintenance": {"empty_trash": True}}},
            clean_bundles=True, optimize=True,
        ),
        lambda: server,
    )
    await job.run(session)
    assert server.library.calls == ["cleanBundles", "section:Movies", "optimize"]


async def test_a_library_that_wants_nothing_costs_no_section_request(session):
    """The flag is resolved BEFORE Plex is asked for the section, so a
    library that has opted out costs no request at all.

    ``Movies`` states ``empty_trash: false``, and this double raises if its
    section is ever fetched -- an implementation that fetched first and
    checked after would fail here with that ``RuntimeError`` rather than
    quietly making a request per library per run.
    """

    class Refusing(RecordingLibrary):
        def section(self, title):
            self.sections_asked.append(title)
            if title == "Movies":
                raise RuntimeError("Movies opted out; this must not be fetched")
            return RecordingSection(title, self.calls)

    server = RecordingServer()
    server.library = Refusing()
    job = make_maintenance_job(
        _holder_with_libraries(
            {"Movies": {"maintenance": {"empty_trash": False}}}, empty_trash=True,
        ),
        lambda: server,
    )
    summary = await job.run(session)
    assert server.library.calls == ["section:TV Shows"]
    assert server.library.sections_asked == ["TV Shows"]
    assert summary == "ran empty_trash"


async def test_a_same_value_library_override_still_narrows_the_sweep(session):
    """The switch is on PRESENCE, not on value
    difference. ``Movies`` states ``empty_trash: true``, which is the SAME
    value the global already has, and ``TV Shows`` states nothing and
    inherits that same ``true``. The old, buggy switch (``value !=
    config.maintenance.empty_trash``) sees no difference anywhere and falls
    back to the ONE server-wide call -- which would also reach any Plex
    section outside ``collections.libraries``. Because a library has an
    opinion at all, the sweep must go section by section instead, even
    though every resolved value here happens to agree with the global.
    """
    server = RecordingServer()
    job = make_maintenance_job(
        _holder_with_libraries(
            {"Movies": {"maintenance": {"empty_trash": True}}}, empty_trash=True,
        ),
        lambda: server,
    )
    await job.run(session)
    assert server.library.calls == ["section:Movies", "section:TV Shows"]
    assert server.library.sections_asked == ["Movies", "TV Shows"]


async def test_the_server_library_is_read_off_the_event_loop(session):
    """perf C2: plexapi's ``PlexServer.library`` is a ``cached_data_property``
    whose first read is a request (``self.query('/library')``). Building the
    call list as ``server.library.emptyTrash`` or ``getattr(server.library,
    method)`` made that request on the loop, before the ``to_thread`` hop."""
    loop_thread = threading.get_ident()
    seen = []
    library = RecordingLibrary()

    class _Server:
        @property
        def library(self):
            seen.append(threading.get_ident())
            return library

    job = make_maintenance_job(
        _holder(clean_bundles=True, empty_trash=True, optimize=True), _Server
    )
    summary = await job.run(session)

    assert summary == "ran clean_bundles, empty_trash, optimize"
    assert seen and loop_thread not in seen


async def test_an_unreachable_server_fails_every_operation_by_class_name_only(session):
    """perf C2 moved the ``server.library`` read onto the thread, so a Plex
    that cannot be reached now fails INSIDE each operation's call rather than
    before the loop: every enabled operation is reported failed, none ran,
    and the served summary still carries the class name only -- never the URL
    a requests failure's str() embeds (row 213's rule)."""
    url = "http://plex:32400/library"

    class _Unreachable:
        @property
        def library(self):
            raise requests.exceptions.ConnectionError(url)

    job = make_maintenance_job(
        _holder(clean_bundles=True, empty_trash=True, optimize=True), _Unreachable
    )
    summary = await job.run(session)

    # The order is the job body's own ``wanted`` list.
    assert summary == (
        "ran nothing; clean_bundles failed (ConnectionError); "
        "empty_trash failed (ConnectionError); optimize failed (ConnectionError)"
    )
    assert "plex:32400" not in summary
    assert url not in summary
