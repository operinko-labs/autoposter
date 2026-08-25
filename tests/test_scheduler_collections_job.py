"""``make_collections_job``: the scheduled wrapper around
``autoposter.collections.service.reconcile_libraries``.

The fakes mirror the ones in ``tests/test_collection_main.py`` and
``tests/test_collection_reconcile.py`` -- this job is a thin shell around
the same function those tests exercise directly, so the same plexapi
surface is all that is needed here too.
"""
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from plexapi.exceptions import NotFound

from autoposter.collections.builders import SourceClients
from autoposter.collections.service import CollectionsPassFailed, ReconcileResult
from autoposter.config.holder import ConfigHolder
from autoposter.config.schema import Secrets
from autoposter.scheduler.jobs import make_collections_job

LABEL = "autoposter"


class FakeChoice:
    def __init__(self, title):
        self.title = title


class FakeCollection:
    def __init__(self, title, rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self.summary = None
        self._labels = []
        # Stands in for ``collection._server``: the summary is written with a
        # raw item-level PUT, not ``editSummary``.
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    @property
    def labels(self):
        return self._labels

    def reload(self, **kw):
        pass

    def updateFilters(self, **kw):
        pass

    def editSummary(self, summary, locked=True):
        """Raises the way the live server does -- the section route plexapi
        takes 404s for collection summaries. See
        ``reconcile._edit_collection_summary``."""
        raise NotFound("(404) not_found; /library/sections/42/all?type=18")

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Stands in for ``server.query`` -- the item-level summary PUT."""
        self.summary = parse_qs(urlsplit(key).query)["summary.value"][0]

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
            # No operator-configured definitions and no catalog presets: this
            # test is about the shipped inventory, which is what two empty
            # lists leave.
            definitions=[], presets=[],
            # The delete sweep is off by default; it is exercised in
            # tests/test_builder_knobs.py.
            delete_unconfigured=False, max_deletes=5,
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
        job = make_collections_job(ConfigHolder(config), server_factory, http)
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
        job = make_collections_job(ConfigHolder(config), lambda: server, http)
        summary = await job.run(session)

    assert "Movies: 2 action(s)" in summary
    assert "TV Shows: 4 action(s)" in summary


async def test_a_failure_reconciling_one_library_does_not_prevent_the_other(session):
    """The containment is unchanged -- the healthy library is still
    reconciled and still named -- but the *job* now fails, which is the whole
    of roadmap row 115: the run this test used to assert was ``ok`` was a run
    where a configured library had not been reconciled at all."""
    server = BreaksOnSecondLibrary({"Movies": FakeSection({"R", "17"})})
    config = _config(["Movies", "TV Shows"])

    async with httpx.AsyncClient() as http:
        job = make_collections_job(ConfigHolder(config), lambda: server, http)
        with pytest.raises(CollectionsPassFailed) as failure:
            await job.run(session)

    detail = str(failure.value)
    assert "Movies: 2 action(s)" in detail
    assert "TV Shows" in detail and "failed" in detail.lower()


async def test_a_real_secrets_bundle_reaches_the_reconcile(session, monkeypatch):
    """Fix round F3: ``sources`` defaults via ``default_factory``
    (``BuilderContext.sources``), so a forgotten wiring here would silently
    degrade every source-backed builder to "not configured" -- no error, no
    failed collection, nothing. Pin that the job actually builds and threads
    a REAL bundle carrying this run's secret, not the empty default."""
    captured = {}

    async def fake_reconcile(session, server, config, http, **kwargs):
        captured.update(kwargs)
        return ReconcileResult()

    monkeypatch.setattr("autoposter.scheduler.jobs.reconcile_libraries", fake_reconcile)

    secrets = Secrets(
        database_url="postgresql+asyncpg://unused", plex_token="x", tmdb_token="x",
        tvdb_apikey="x", fanart_apikey="x", webhook_secret="x",
        mdblist_apikey="the-real-key",
    )
    config = _config(["Movies"])
    # build_source_clients reads these sections too; _config's shared shape
    # has no reason to carry them since every other test here passes no
    # secrets and never reaches that call.
    config.providers = SimpleNamespace(cache_ttl_seconds=0)
    config.radarr = SimpleNamespace(enabled=False, base_url="")
    config.sonarr = SimpleNamespace(enabled=False, base_url="")
    config.tracearr = SimpleNamespace(enabled=False, base_url="")
    config.manual_assets_root = "/manual"
    server = FakeServer({"Movies": FakeSection({"R"})})

    async with httpx.AsyncClient() as http:
        job = make_collections_job(ConfigHolder(config), lambda: server, http, secrets=secrets)
        await job.run(session)

    sources = captured["sources"]
    assert sources is not None and sources != SourceClients(), (
        "the job must pass the real bundle, not the empty default"
    )
    assert sources.mdblist is not None and sources.mdblist._apikey == "the-real-key", (
        "the bundle must carry this run's secrets, not someone else's"
    )


async def test_the_plex_connection_runs_off_the_event_loop(session):
    main_thread = threading.current_thread()
    connect_thread = {}

    def server_factory():
        connect_thread["thread"] = threading.current_thread()
        return FakeServer({"Movies": FakeSection({"R"})})

    config = _config(["Movies"])
    async with httpx.AsyncClient() as http:
        job = make_collections_job(ConfigHolder(config), server_factory, http)
        await job.run(session)

    assert connect_thread["thread"] is not None
    assert connect_thread["thread"] is not main_thread, (
        "server_factory must run via asyncio.to_thread, not directly on the "
        "event loop thread"
    )
