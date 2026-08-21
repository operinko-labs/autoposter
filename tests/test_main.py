"""Proves ``autoposter.main.build()`` -- the actual production entrypoint --
wires the SPA in.

Every other test that mounts the SPA does so against a fresh ``FastAPI()``
(the image job's own check in ``.forgejo/workflows/ci.yml``) or against
``create_app()`` directly (``tests/test_spa_serving.py``). None of them import
``build()``, so none of them would notice if the
``mount_spa(app, spa_dist())`` call at the end of ``build()`` -- deliberately
kept out of ``create_app()`` so the test suite does not pick up a stale
``frontend/dist`` -- were ever deleted. The app it returns would still boot,
still pass every other check, and just answer ``/`` with a 404.

This test needs no real database, no real Plex server and no
``npm run build``: everything ``build()`` touches besides ``mount_spa`` and
``spa_dist`` is stubbed out, so the only thing exercised is the wiring itself.
"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import autoposter.main as main_module
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
INDEX_MARKER = "<!-- build() test index -->"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash="",
    )


class _FakeSecrets:
    """Stands in for the real ``Secrets`` class in ``main_module``'s
    namespace only, so ``Secrets.from_env()`` needs no environment
    variables. The real ``Secrets`` model is untouched."""

    @staticmethod
    def from_env() -> Secrets:
        return _secrets()


@pytest.fixture
def dist(tmp_path) -> Path:
    """A stand-in for `npm run build` output. `mount_spa` returns silently
    when there is no dist, so this has to be a real directory with a real
    `index.html` -- a missing one would prove nothing."""
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text(
        f"<!doctype html><html><body>{INDEX_MARKER}</body></html>", encoding="utf-8"
    )
    return root


@pytest.fixture(autouse=True)
def _stub_build_dependencies(monkeypatch, dist):
    """Replace everything `build()` needs besides the SPA wiring under test.

    `load_config` reads a real file, `Secrets.from_env` reads real
    environment variables, `make_engine` opens a real database connection
    pool, and `PlexClient` wraps a (lazily-connecting) real Plex server --
    none of that is what this test is about, so all four are stubbed with
    lightweight stand-ins.
    """
    monkeypatch.setattr(main_module, "load_config", lambda path: load_config(EXAMPLE))
    monkeypatch.setattr(main_module, "Secrets", _FakeSecrets)
    monkeypatch.setattr(main_module, "make_engine", lambda url: object())
    monkeypatch.setattr(
        main_module, "PlexClient", lambda server, excluded_libraries: object()
    )
    # spa_dist() itself is not under test; only whether build() calls
    # mount_spa with its result.
    monkeypatch.setattr(main_module, "spa_dist", lambda: dist)


async def test_build_wires_the_spa_into_the_returned_app():
    """The whole point: exercise `autoposter.main.build()`, not `create_app()`
    directly, and prove the SPA the production entrypoint actually returns is
    reachable. Deleting `mount_spa(app, spa_dist())` from `build()` must turn
    this red."""
    app = main_module.build()

    assert any(getattr(route, "path", None) == "/{full_path:path}" for route in app.routes), (
        "build() did not register the SPA catch-all route"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert INDEX_MARKER in response.text
