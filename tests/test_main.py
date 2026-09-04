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

import logging
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import autoposter.main as main_module
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.plex.client import PlexClient

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
INDEX_MARKER = "<!-- build() test index -->"

# A URL shaped like the two secret-bearing URLs this process actually
# requests: the notifications webhook (token in the path, Uptime-Kuma style)
# and fanart.tv (api_key in the query string).
URL_TOKEN = "tok-should-never-reach-the-log-83f5d1"
TOKEN_URL = f"http://hooks.example.test/notify/{URL_TOKEN}"


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

    `CONFIG_PATH` points at `/config/autoposter.yaml`, which does not exist
    outside a deployed pod, `Secrets.from_env` reads real environment
    variables, and `make_engine` opens a real database connection pool -- none
    of that is what these tests are about, so each gets a stand-in.

    Note what is deliberately *not* stubbed: the config load itself. It used
    to be, back when `build()` read the overrides row through a synchronous
    `asyncio.run` bridge -- and that stub is exactly why no test noticed the
    bridge exploding under `uvicorn --factory`. Pointing `CONFIG_PATH` at the
    example config keeps these tests off the network and off the database
    while still running the real load path.

    `create_app` is spied on rather than replaced -- every other test here
    needs the real one to actually build an app -- and the kwargs each call
    received are yielded, so a test can check what `build()` passed it without
    a stand-in that would let an argument silently vanish.
    """
    monkeypatch.setattr(main_module, "CONFIG_PATH", EXAMPLE)
    monkeypatch.setattr(main_module, "Secrets", _FakeSecrets)
    monkeypatch.setattr(main_module, "make_engine", lambda url: object())
    # spa_dist() itself is not under test; only whether build() calls
    # mount_spa with its result.
    monkeypatch.setattr(main_module, "spa_dist", lambda: dist)

    calls: list[dict] = []
    real_create_app = main_module.create_app

    def spying_create_app(*args, **kwargs):
        calls.append(kwargs)
        return real_create_app(*args, **kwargs)

    monkeypatch.setattr(main_module, "create_app", spying_create_app)
    yield calls


async def test_build_can_be_called_from_inside_a_running_event_loop():
    """``uvicorn autoposter.main:build --factory`` -- the dev-compose ``api``
    command, and the only form ``--reload`` accepts -- calls ``build()`` from
    inside the server's own event loop. ``build()`` may therefore not bridge
    an async read with ``asyncio.run``: the config editor's first cut did, and
    every hot-reload boot died on ``RuntimeError: asyncio.run() cannot be
    called from a running event loop`` before serving a single request.

    This test *is* that repro. pytest-asyncio already runs it on a loop, so a
    direct call here is precisely what uvicorn's factory path does; production
    (``python -m autoposter.main``, which builds before ``uvicorn.run``) never
    saw it, which is why it survived a merge.
    """
    app = main_module.build()

    assert isinstance(app, FastAPI)


async def test_build_passes_a_plex_factory_that_builds_a_real_plex_client(
    _stub_build_dependencies,
):
    """``build()`` must hand ``create_app`` a ``plex_factory`` -- deleting the
    keyword from `main.py`'s ``create_app(...)`` call leaves this suite green
    everywhere else (no other test here drives the lifespan, and
    `test_app.py`'s own plex_factory coverage, e.g.
    `test_the_lifespan_builds_the_plex_client_from_the_effective_config`
    around line 186, passes its own stand-in factory rather than exercising
    `main.build()`'s) while every real deployment starts with
    `app.state.plex is None`, turning the live-artwork endpoint into a
    permanent 503.
    """
    calls = _stub_build_dependencies
    main_module.build()

    assert len(calls) == 1
    plex_factory = calls[0].get("plex_factory")
    assert plex_factory is not None, "build() did not pass plex_factory to create_app"

    client = plex_factory(load_config(EXAMPLE))
    assert isinstance(client, PlexClient)


async def test_build_publishes_the_engine_for_the_lifespan_to_dispose(
    _stub_build_dependencies,
):
    """``build()`` owns the only reference to the engine (``main.py``'s
    ``make_engine`` call), and the lifespan is the only place that knows when
    the process is going away. Without this hand-off there is no
    ``dispose()`` anywhere in the served application, and every deploy leaves
    Postgres logging "unexpected EOF on client connection with an open
    transaction" for all 5 pooled connections instead of a clean terminate.

    Deleting the keyword from ``main.py``'s ``create_app(...)`` call leaves
    the rest of the suite green -- ``test_app.py``'s disposal test passes its
    own fake engine rather than exercising ``build()``."""
    calls = _stub_build_dependencies

    app = main_module.build()

    assert calls, "build() did not call create_app"
    assert calls[0].get("engine") is not None, (
        "build() did not pass its engine to create_app, so the lifespan has "
        "nothing to dispose"
    )
    assert app.state.engine is calls[0]["engine"]


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


async def test_build_keeps_httpx_request_urls_out_of_the_log(caplog):
    """httpx logs every request at INFO with the FULL url -- which in this
    process may carry the notifications webhook token in its path
    (config/schema.py's NotificationsConfig documents the host-only
    guarantee) or fanart.tv's api_key in its query string. ``build()``'s
    logging setup must therefore cap the ``httpx`` logger at WARNING;
    deleting that line from ``build()`` must turn this red."""
    main_module.build()

    # The property: build() itself capped the httpx logger. Asserted on the
    # logger's own level, not getEffectiveLevel(): under pytest the root
    # logger already has handlers, so build()'s basicConfig is a no-op and
    # the root sits at WARNING -- an effective-level assertion would pass
    # with the suppression line deleted.
    assert logging.getLogger("httpx").level >= logging.WARNING

    # The behaviour: a request made at the app's INFO default leaves no URL
    # in the log, success included -- httpx logs its per-request line on a
    # 200, not just on failures.
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(TOKEN_URL, json={})
    assert response.status_code == 200  # the request really happened
    assert URL_TOKEN not in caplog.text
    assert TOKEN_URL not in caplog.text
