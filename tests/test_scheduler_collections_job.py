"""``make_collections_job``: the scheduled wrapper around
``autoposter.collections.service.reconcile_libraries``.

The fakes mirror the ones in ``tests/test_collection_main.py`` and
``tests/test_collection_reconcile.py`` -- this job is a thin shell around
the same function those tests exercise directly, so the same plexapi
surface is all that is needed here too.
"""
import threading
from types import SimpleNamespace

import httpx

from autoposter.scheduler.jobs import make_collections_job

LABEL = "autoposter"


class FakeChoice:
    def __init__(self, title):
        self.title = title


class FakeCollection:
    def __init__(self, title, rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self._labels = []

    @property
    def labels(self):
        return self._labels

    def reload(self, **kw):
        pass

    def updateFilters(self, **kw):
        pass

    def editSummary(self, summary, locked=True):
        pass

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    def __init__(self, ratings, section_type="movie"):
        self._ratings = list(ratings)
        self._existing = {}
        self.type = section_type

    def listFilterChoices(self, field, libtype=None):
        return [FakeChoice(r) for r in self._ratings]

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, limit=None,
                          libtype=None, sort=None, filters=None, **kw):
        collection = FakeCollection(title, rating_key=str(len(self._existing) + 1))
        self._existing[title] = collection
        return collection


class FakeLibrary:
    def __init__(self, sections):
        self._sections = sections

    def section(self, name):
        return self._sections[name]


class FakeServer:
    """A ``PlexServer`` stand-in with just enough surface for
    ``reconcile_libraries``: ``server.library.section(name)``."""

    def __init__(self, sections):
        self.library = FakeLibrary(sections)


class BreaksOnSecondLibrary:
    """Like ``FakeServer``, but raises fetching any section not in
    ``sections`` -- simulating a Plex read failure partway through the
    configured libraries."""

    class _Library:
        def __init__(self, sections):
            self._sections = sections

        def section(self, name):
            if name in self._sections:
                return self._sections[name]
            raise RuntimeError("simulated failure fetching %r" % name)

    def __init__(self, sections):
        self.library = self._Library(sections)


def _config(libraries=("Movies", "TV Shows"), enabled=True, apply_to_plex=True):
    return SimpleNamespace(
        collections=SimpleNamespace(
            enabled=enabled, libraries=list(libraries), ownership_label=LABEL,
            apply_to_plex=apply_to_plex,
            # Charts/awards are exercised in test_collection_sources.py; keeping
            # them off here keeps this test scoped to the job wiring and out of
            # real network calls. Separators are exercised in
            # test_collection_separator.py, for the same reason -- and off
            # here since these fakes have no _server.
            charts=False, awards=False, separators=False,
            adopt=False, adopt_from=["Kometa"], adopt_removes_prior_label=False,
            protect_labels=[],
            # Posters are exercised in test_collection_poster_wiring.py; off
            # here for the same reason charts/awards/separators are.
            posters=False,
        ),
        scheduler=SimpleNamespace(collections_hours=24),
    )


async def test_the_job_is_skipped_entirely_when_collections_are_disabled(session):
    connected = []

    def server_factory():
        connected.append(True)
        return FakeServer({})

    config = _config(enabled=False)
    async with httpx.AsyncClient() as http:
        job = make_collections_job(config, server_factory, http)
        summary = await job.run(session)

    assert not connected, "a disabled pass must never connect to Plex"
    assert "disabled" in summary.lower()


async def test_a_successful_pass_returns_a_summary_naming_each_library(session):
    server = FakeServer({
        "Movies": FakeSection({"R", "17"}),
        "TV Shows": FakeSection({"TV-14"}, section_type="show"),
    })
    config = _config(["Movies", "TV Shows"])

    async with httpx.AsyncClient() as http:
        job = make_collections_job(config, lambda: server, http)
        summary = await job.run(session)

    assert "Movies: 2 action(s)" in summary
    assert "TV Shows: 4 action(s)" in summary


async def test_a_failure_reconciling_one_library_does_not_prevent_the_other(session):
    server = BreaksOnSecondLibrary({"Movies": FakeSection({"R", "17"})})
    config = _config(["Movies", "TV Shows"])

    async with httpx.AsyncClient() as http:
        job = make_collections_job(config, lambda: server, http)
        summary = await job.run(session)

    assert "Movies: 2 action(s)" in summary
    assert "TV Shows" in summary and "failed" in summary.lower()


async def test_the_plex_connection_runs_off_the_event_loop(session):
    main_thread = threading.current_thread()
    connect_thread = {}

    def server_factory():
        connect_thread["thread"] = threading.current_thread()
        return FakeServer({"Movies": FakeSection({"R"})})

    config = _config(["Movies"])
    async with httpx.AsyncClient() as http:
        job = make_collections_job(config, server_factory, http)
        await job.run(session)

    assert connect_thread["thread"] is not None
    assert connect_thread["thread"] is not main_thread, (
        "server_factory must run via asyncio.to_thread, not directly on the "
        "event loop thread"
    )
