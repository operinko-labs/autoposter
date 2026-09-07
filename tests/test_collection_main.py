"""The per-library commit boundary in ``autoposter.collections.service``,
exercised the way ``python -m autoposter.collections`` uses it.

Moving the commit inside the loop (rather than once after every configured
library) matters because a failure partway through must not roll back a
library that already succeeded and already wrote to Plex -- without this,
the hash gate never gets to see that library again and it is rewritten in
full on every subsequent run. Containing the failure to its own library,
rather than letting it end the pass, matters because the scheduler runs this
same function and one bad library must not stop the rest of the configured
libraries from being tried.
"""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from plexapi.exceptions import NotFound
from sqlalchemy import select

from autoposter.collections.builders import SourceClients
from autoposter.collections.service import (
    LibraryOutcome,
    ReconcileResult,
    reconcile_libraries,
)
from autoposter.db.models import ManagedCollection
from plex_doubles import FakeSection as PlexSection

LABEL = "autoposter"


class FakeCollection:
    def __init__(self, title, rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self.summary = None
        self.titleSort = None
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

    def editSortTitle(self, sortTitle, locked=True):
        # Row 49: every managed collection derives its group's sort-title
        # prefix now, so this route is reached on every apply.
        self.titleSort = sortTitle

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


class FakeSection(PlexSection):
    collection_factory = FakeCollection


class BreaksOnTheLeftoversScan(FakeSection):
    """The reconcile itself succeeds and its Plex writes land; only the
    diagnostic leftovers scan afterwards fails -- a collection deleted
    mid-pass, a timeout, a 500. The scan is the only caller that passes a
    ``label`` filter, so failing on that alone isolates it."""

    def collections(self, label=None, **kw):
        if label is not None:
            raise RuntimeError("simulated failure listing collections by label")
        return super().collections(**kw)


class BreaksOnSecondLibrary:
    """``server.library.section()`` returns a section for known names and
    raises for anything else -- simulating a Plex read failure partway
    through the configured libraries."""

    class _Library:
        def __init__(self, sections):
            self._sections = sections

        def section(self, name):
            if name in self._sections:
                return self._sections[name]
            raise RuntimeError("simulated failure fetching %r" % name)

    def __init__(self, sections):
        self.library = self._Library(sections)


def _config(libraries):
    return SimpleNamespace(
        collections=SimpleNamespace(
            libraries=libraries, ownership_label=LABEL, apply_to_plex=True,
            # Charts/awards are exercised in test_collection_sources.py; keeping
            # them off here keeps this test scoped to the commit-boundary
            # behaviour and out of real network calls. Separators are
            # exercised in test_collection_separator.py, for the same
            # reason -- and off here since these fakes have no _server.
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
            # The delete sweep runs on every reconcile_libraries pass; off is
            # the default and what this file's fakes are written for -- the
            # sweep itself is tests/test_builder_knobs.py's.
            delete_unconfigured=False, max_deletes=5,
        )
    )


async def test_a_failure_on_the_second_library_does_not_roll_back_the_first(
    session, session_factory
):
    server = BreaksOnSecondLibrary({"Movies": FakeSection(ratings={"R", "17"})})
    config = _config(["Movies", "TV Shows"])

    async with httpx.AsyncClient() as http:
        summary = (await reconcile_libraries(session, server, config, http)).summary

    assert "TV Shows" in summary and "failed" in summary.lower(), (
        "a failure reconciling one library must be recorded, not left to "
        "abort the pass silently"
    )

    async with session_factory() as verify:
        rows = (await verify.execute(select(ManagedCollection))).scalars().all()
        assert any(r.title == "Age 17+ Movies" and r.library == "Movies" for r in rows), (
            "the first library's row must already be committed -- not rolled back "
            "by a failure on the second -- or a bare commit() at the end of the "
            "loop would never run at all"
        )


async def test_a_failed_leftovers_scan_does_not_discard_the_librarys_rows(
    session, session_factory
):
    """The leftovers report is diagnostic and runs after the library's Plex
    writes have already landed. If its failure reached the per-library
    handler, the rollback there would throw away every ``ManagedCollection``
    row for that library -- and the next pass, seeing no rows, would rewrite
    the whole library. That is precisely what the per-library commit
    boundary exists to prevent."""
    server = BreaksOnSecondLibrary({"Movies": BreaksOnTheLeftoversScan(ratings={"R", "17"})})

    async with httpx.AsyncClient() as http:
        result = await reconcile_libraries(session, server, _config(["Movies"]), http)

    assert result.failed is False, (
        "a failed diagnostic scan must not be reported as a failed library"
    )

    async with session_factory() as verify:
        rows = (await verify.execute(select(ManagedCollection))).scalars().all()
        assert any(r.title == "Age 17+ Movies" and r.library == "Movies" for r in rows), (
            "the library's rows must survive a read failure in the leftovers scan"
        )


async def test_a_failed_library_is_visible_to_a_caller_watching_the_exit_code(session):
    """``reconcile_libraries`` contains a failure rather than raising, so its
    result is the only place the outcome survives. The one-shot CLI turns that
    into an exit code -- without it ``python -m autoposter.collections`` exits
    0 after a library failed and a cron wrapper watching the exit code never
    sees it."""
    server = BreaksOnSecondLibrary({"Movies": FakeSection(ratings={"R", "17"})})

    async with httpx.AsyncClient() as http:
        failed = await reconcile_libraries(session, server, _config(["Movies", "TV Shows"]), http)
        clean = await reconcile_libraries(session, server, _config(["Movies"]), http)

    assert failed.failed is True
    assert clean.failed is False


async def test_the_cli_exits_non_zero_when_a_library_failed(monkeypatch):
    """The exit code itself, not just the predicate behind it."""
    import autoposter.collections.__main__ as cli

    async def fake_reconcile(session, server, config, http, summaries=None, **kwargs):
        return ReconcileResult(libraries=[
            LibraryOutcome(library="Movies", actions=["a", "b", "c"]),
            LibraryOutcome(library="TV Shows", error="simulated"),
        ])

    _stub_cli_dependencies(monkeypatch, cli, fake_reconcile)

    with pytest.raises(SystemExit) as exit_info:
        await cli.main()
    assert exit_info.value.code == 1


async def test_the_cli_exits_zero_when_every_library_succeeded(monkeypatch):
    import autoposter.collections.__main__ as cli

    async def fake_reconcile(session, server, config, http, summaries=None, **kwargs):
        return ReconcileResult(libraries=[
            LibraryOutcome(library="Movies", actions=["a", "b", "c"]),
            LibraryOutcome(library="TV Shows"),
        ])

    _stub_cli_dependencies(monkeypatch, cli, fake_reconcile)

    await cli.main()  # must not raise SystemExit


async def test_the_cli_silences_httpxs_url_bearing_request_log(monkeypatch):
    """``main()`` builds the same ``SourceClients`` bundle ``main.py`` does --
    Tracearr's base_url included -- so its httpx logger must be silenced the
    same way (see ``main.py:75-81``), or httpx's own INFO line prints the
    full request URL once per request."""
    import logging

    import autoposter.collections.__main__ as cli

    async def fake_reconcile(session, server, config, http, summaries=None, **kwargs):
        return ReconcileResult(libraries=[LibraryOutcome(library="Movies")])

    _stub_cli_dependencies(monkeypatch, cli, fake_reconcile)
    logging.getLogger("httpx").setLevel(logging.NOTSET)

    await cli.main()

    assert logging.getLogger("httpx").level == logging.WARNING


async def test_the_cli_threads_a_real_source_bundle_to_the_reconcile(monkeypatch):
    """Fix round F3: ``BuilderContext.sources`` defaults via
    ``default_factory``, so if ``main()`` ever stopped building and passing
    the bundle, every source-backed builder would just silently see "not
    configured" -- no error, nothing at config load or runtime. Pin that the
    CLI actually threads a REAL bundle carrying this run's secret, not the
    empty default ``build_source_clients`` is never even called to produce."""
    import autoposter.collections.__main__ as cli

    captured = {}

    async def fake_reconcile(session, server, config, http, summaries=None, **kwargs):
        captured.update(kwargs)
        return ReconcileResult(libraries=[LibraryOutcome(library="Movies")])

    _stub_cli_dependencies(monkeypatch, cli, fake_reconcile, mdblist_apikey="the-real-key")

    await cli.main()

    sources = captured["sources"]
    assert sources is not None and sources != SourceClients(), (
        "the CLI must pass the real bundle, not the empty default"
    )
    assert sources.mdblist is not None and sources.mdblist._apikey == "the-real-key", (
        "the bundle must carry this run's secrets, not someone else's"
    )


def _stub_cli_dependencies(monkeypatch, cli, fake_reconcile, mdblist_apikey=""):
    """Replace everything ``main()`` touches outside its own logic: config
    loading, the Plex connection, the engine and the reconcile itself. What
    is under test here is only what ``main()`` does with the summary."""

    class _NullSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _NullEngine:
        async def dispose(self):
            pass

    async def fake_load_effective_config(path, session):
        return SimpleNamespace(
            collections=SimpleNamespace(enabled=True),
            # `main()` now guards on both switches, and runs the playlists
            # half when this one is on. Off here for the reason charts and
            # awards are off in the job's own suite: this test is about what
            # `main()` does with the summary, and turning it on would drag a
            # second pass and a second set of fakes into it.
            playlists=SimpleNamespace(enabled=False),
            plex=SimpleNamespace(url="http://plex.invalid"),
            # The CLI builds its own cache-fronted TMDB facts client, for the
            # ``tmdb_summary:`` definitions a pass may carry -- so the stub
            # config needs the section that decides whether to cache.
            providers=SimpleNamespace(cache_ttl_seconds=0),
            # ...and its own SourceClients bundle, which reads whether either
            # arr service is configured. Both off: this test is about what
            # ``main()`` does with the summary, not about the clients.
            radarr=SimpleNamespace(enabled=False, base_url=""),
            sonarr=SimpleNamespace(enabled=False, base_url=""),
            tracearr=SimpleNamespace(enabled=False, base_url=""),
            # ...and the manual assets mount, which the bundle carries for
            # ``text_file``. A path, not a client: the value is irrelevant
            # here, its presence is what a real ``Config`` guarantees.
            manual_assets_root="/manual",
        )

    monkeypatch.setattr(cli, "load_effective_config", fake_load_effective_config)
    monkeypatch.setattr(
        cli, "Secrets",
        SimpleNamespace(
            from_env=lambda: SimpleNamespace(
                plex_token="t", tmdb_token="t", database_url="postgresql://x",
                # The soft secrets the bundle reads. Empty is the real default
                # for every one of them, and means "not configured".
                tvdb_apikey="t", mdblist_apikey=mdblist_apikey, radarr_apikey="",
                sonarr_apikey="", plex_account_token="", tracearr_apikey="",
            )
        ),
    )
    monkeypatch.setattr(cli, "PlexServer", lambda url, token: object())
    monkeypatch.setattr(cli, "make_engine", lambda url: _NullEngine())
    monkeypatch.setattr(cli, "make_session_factory", lambda engine: _NullSession)
    monkeypatch.setattr(cli, "reconcile_libraries", fake_reconcile)
