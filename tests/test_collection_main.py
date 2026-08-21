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

import httpx
import pytest
from sqlalchemy import select

from autoposter.collections.service import reconcile_libraries, summary_has_failure
from autoposter.db.models import ManagedCollection

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
        )
    )


async def test_a_failure_on_the_second_library_does_not_roll_back_the_first(
    session, session_factory
):
    server = BreaksOnSecondLibrary({"Movies": FakeSection({"R", "17"})})
    config = _config(["Movies", "TV Shows"])

    async with httpx.AsyncClient() as http:
        summary = await reconcile_libraries(session, server, config, http)

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
    server = BreaksOnSecondLibrary({"Movies": BreaksOnTheLeftoversScan({"R", "17"})})

    async with httpx.AsyncClient() as http:
        summary = await reconcile_libraries(session, server, _config(["Movies"]), http)

    assert summary_has_failure(summary) is False, (
        "a failed diagnostic scan must not be reported as a failed library"
    )

    async with session_factory() as verify:
        rows = (await verify.execute(select(ManagedCollection))).scalars().all()
        assert any(r.title == "Age 17+ Movies" and r.library == "Movies" for r in rows), (
            "the library's rows must survive a read failure in the leftovers scan"
        )


async def test_a_failed_library_is_visible_to_a_caller_watching_the_exit_code(session):
    """``reconcile_libraries`` contains a failure rather than raising, so the
    summary string is the only place the outcome survives. The one-shot CLI
    turns that into an exit code -- without it ``python -m
    autoposter.collections`` exits 0 after a library failed and a cron
    wrapper watching the exit code never sees it."""
    server = BreaksOnSecondLibrary({"Movies": FakeSection({"R", "17"})})

    async with httpx.AsyncClient() as http:
        failed = await reconcile_libraries(session, server, _config(["Movies", "TV Shows"]), http)
        clean = await reconcile_libraries(session, server, _config(["Movies"]), http)

    assert summary_has_failure(failed) is True
    assert summary_has_failure(clean) is False


async def test_the_cli_exits_non_zero_when_a_library_failed(monkeypatch):
    """The exit code itself, not just the predicate behind it."""
    import autoposter.collections.__main__ as cli

    async def fake_reconcile(session, server, config, http):
        return "Movies: 3 action(s); TV Shows: failed (simulated)"

    _stub_cli_dependencies(monkeypatch, cli, fake_reconcile)

    with pytest.raises(SystemExit) as exit_info:
        await cli.main()
    assert exit_info.value.code == 1


async def test_the_cli_exits_zero_when_every_library_succeeded(monkeypatch):
    import autoposter.collections.__main__ as cli

    async def fake_reconcile(session, server, config, http):
        return "Movies: 3 action(s); TV Shows: 0 action(s)"

    _stub_cli_dependencies(monkeypatch, cli, fake_reconcile)

    await cli.main()  # must not raise SystemExit


def _stub_cli_dependencies(monkeypatch, cli, fake_reconcile):
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

    monkeypatch.setattr(
        cli, "load_config",
        lambda path: SimpleNamespace(
            collections=SimpleNamespace(enabled=True),
            plex=SimpleNamespace(url="http://plex.invalid"),
        ),
    )
    monkeypatch.setattr(
        cli, "Secrets",
        SimpleNamespace(
            from_env=lambda: SimpleNamespace(plex_token="t", database_url="postgresql://x")
        ),
    )
    monkeypatch.setattr(cli, "PlexServer", lambda url, token: object())
    monkeypatch.setattr(cli, "make_engine", lambda url: _NullEngine())
    monkeypatch.setattr(cli, "make_session_factory", lambda engine: _NullSession)
    monkeypatch.setattr(cli, "reconcile_libraries", fake_reconcile)
