"""Serving the built SPA without shadowing the API.

The whole risk in mounting a single-page application behind an API is
ordering. A catch-all that answers every unmatched path has to answer `/items`
with the SPA's HTML -- that is what makes a client-side route survive a page
reload -- while still letting `/api/does-not-exist` return its JSON 404 and
`/healthz` return its JSON body. Get that wrong and every API client's error
handling starts parsing HTML, and Kubernetes' probe starts passing against a
page that says nothing about whether the service works.

These tests pin the boundary from both sides.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.spa import mount_spa
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
INDEX_MARKER = "<!-- test index -->"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash="",
    )


@pytest.fixture
def dist(tmp_path) -> Path:
    """A stand-in for `npm run build` output.

    Building the real bundle would make this suite need a Node toolchain; the
    server has no opinion about what is inside index.html, only about which
    paths reach it.
    """
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        f"<!doctype html><html><body>{INDEX_MARKER}</body></html>", encoding="utf-8"
    )
    (root / "assets" / "app.js").write_text("export default 1;\n", encoding="utf-8")
    return root


@pytest_asyncio.fixture
async def client_with_spa(session_factory, dist):
    app = create_app(load_config(EXAMPLE), session_factory, _secrets())
    mount_spa(app, dist)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_root_serves_the_index(client_with_spa):
    response = await client_with_spa.get("/")
    assert response.status_code == 200
    assert INDEX_MARKER in response.text


async def test_a_client_route_returns_the_index(client_with_spa):
    """A deep link the SPA routes internally. The server has no such route,
    and must answer with the shell rather than a 404 -- otherwise reloading
    the page on /failures breaks."""
    response = await client_with_spa.get("/failures")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert INDEX_MARKER in response.text


async def test_built_assets_are_served(client_with_spa):
    response = await client_with_spa.get("/assets/app.js")
    assert response.status_code == 200
    assert "export default 1;" in response.text


async def test_api_404_stays_json_and_is_not_the_spa(client_with_spa):
    """The regression this file exists for."""
    response = await client_with_spa.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert INDEX_MARKER not in response.text


async def test_an_unauthenticated_api_call_still_401s_rather_than_serving_html(
    client_with_spa,
):
    """A real API path behind the session dependency. If the catch-all took
    precedence this would be a 200 of HTML, and the SPA would never learn it
    needs to log in."""
    response = await client_with_spa.get("/api/status")
    assert response.status_code == 401
    assert INDEX_MARKER not in response.text


async def test_healthz_is_not_shadowed(client_with_spa):
    response = await client_with_spa.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_metrics_is_not_shadowed(client_with_spa):
    response = await client_with_spa.get("/metrics")
    assert response.status_code == 200
    assert INDEX_MARKER not in response.text


async def test_a_missing_dist_directory_is_not_an_error(session_factory):
    """A developer checkout with no `npm run build`, and the Python test suite
    itself, must not need a Node toolchain to start the app."""
    app = create_app(load_config(EXAMPLE), session_factory, _secrets())
    mount_spa(app, Path("does/not/exist"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/healthz")).status_code == 200
        # No SPA to serve, so the catch-all must not exist at all rather than
        # answering with a broken page.
        assert (await c.get("/failures")).status_code == 404


async def test_a_dotdot_path_cannot_escape_the_dist_directory(client_with_spa, dist):
    """Path traversal. The secret it would reach here is a test fixture, but
    the container's dist sits next to the application's own files."""
    outside = dist.parent / "outside.txt"
    outside.write_text("do not serve me", encoding="utf-8")

    response = await client_with_spa.get("/assets/../../outside.txt")
    assert "do not serve me" not in response.text
