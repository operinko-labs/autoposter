"""The ``python -m autoposter.adopt`` entry point's Plex access.

``server.library`` is an HTTP-fetching property on a real ``PlexServer``, so
``asyncio.to_thread(server.library.section, name)`` evaluates the blocking part
*before* handing anything to the thread. On a two-library cutover that is only
two GETs, but it is the same class of defect the walk itself had, and the
event loop is shared with the Plex liveness probe and every worker.
"""
import logging
import threading
from types import SimpleNamespace

import pytest

from autoposter.adopt.__main__ import fetch_section


class _RecordingServer:
    """``library`` is a property, as on a real ``PlexServer`` -- reading it records
    the thread that did so."""

    def __init__(self, reads):
        self._reads = reads

    @property
    def library(self):
        self._reads.append(threading.current_thread())
        return self

    def section(self, name):
        self._reads.append(threading.current_thread())
        return f"section:{name}"


async def test_the_library_property_is_evaluated_inside_the_worker_thread():
    reads = []
    server = _RecordingServer(reads)

    result = await fetch_section(server, "Movies")

    assert result == "section:Movies"
    assert len(reads) == 2  # the `library` property and the `section` call
    assert all(t is not threading.current_thread() for t in reads)


async def test_the_cli_refuses_before_touching_plex_when_it_is_not_configured(
    monkeypatch, caplog
):
    """I1: ``config.plex`` is optional now (a Jellyfin-only deployment). This
    CLI builds a real ``PlexServer(config.plex.url, ...)`` with no
    lazy-connect wrapper, so a missing ``plex:`` block must be refused loudly
    before that read, not crash on ``None.url``."""
    import autoposter.adopt.__main__ as cli

    class _NullSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _NullEngine:
        async def dispose(self):
            pass

    async def fake_load_effective_config(path, session):
        return SimpleNamespace(plex=None)

    monkeypatch.setattr(cli, "load_effective_config", fake_load_effective_config)
    monkeypatch.setattr(
        cli, "Secrets",
        SimpleNamespace(
            from_env=lambda: SimpleNamespace(plex_token="t", database_url="postgresql://x")
        ),
    )
    monkeypatch.setattr(cli, "make_engine", lambda url: _NullEngine())
    monkeypatch.setattr(cli, "make_session_factory", lambda engine: _NullSession)

    def _must_not_connect(*args, **kwargs):
        raise AssertionError("must not build a PlexServer when Plex is not configured")

    monkeypatch.setattr(cli, "PlexServer", _must_not_connect)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as exit_info:
            await cli.main()

    assert exit_info.value.code == 2
    assert "no Plex server is configured" in caplog.text
