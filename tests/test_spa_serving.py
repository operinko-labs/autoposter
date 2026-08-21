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
SECRET = "do not serve me"


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


def _app_with_spa(session_factory, dist):
    app = create_app(load_config(EXAMPLE), session_factory, _secrets())
    mount_spa(app, dist)
    return app


async def _raw_asgi_get(app, raw_path: str) -> tuple[int, dict[bytes, bytes], bytes]:
    """GET `raw_path` verbatim, with no client-side normalisation.

    httpx canonicalises dot segments as it builds the URL --
    ``httpx.URL("http://test/assets/../../x").raw_path`` is ``b"/x"`` -- so
    anything asserting on traversal has to bypass it or it is only testing
    httpx. This speaks ASGI directly instead: the scope carries the path
    exactly as an attacker's client would put it on the wire.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    status = 500
    headers: dict[bytes, bytes] = {}
    body = bytearray()

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            headers.update(dict(message["headers"]))
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await app(scope, receive, send)
    return status, headers, bytes(body)


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


@pytest.mark.parametrize(
    "raw_path",
    [
        "/assets/../../outside.txt",
        "/assets/../outside.txt",
        "/assets/subdir/../../../outside.txt",
    ],
)
async def test_traversal_through_the_assets_mount_is_refused(
    session_factory, dist, raw_path
):
    """Path traversal into the directory dist sits in.

    The secret here is a fixture file; in the container dist sits beside the
    application's own files and its config mount.

    Driven straight at the ASGI app rather than through AsyncClient: httpx
    resolves dot segments while building the URL, so
    `client.get("/assets/../../outside.txt")` puts `/outside.txt` on the wire
    and the server never sees a `..` to reject. A test written that way passes
    against an implementation that would have served the file, which is what
    the previous version of this test did. A hostile client does not normalise,
    so neither does this one.
    """
    outside = dist.parent / "outside.txt"
    outside.write_text(SECRET, encoding="utf-8")

    status, _headers, body = await _raw_asgi_get(_app_with_spa(session_factory, dist), raw_path)

    assert status == 404
    assert SECRET not in body.decode("utf-8", "replace")


@pytest.mark.parametrize(
    "raw_path",
    ["/../outside.txt", "/failures/../../outside.txt", "/./../outside.txt"],
)
async def test_traversal_through_the_catch_all_serves_the_shell_not_the_file(
    session_factory, dist, raw_path
):
    """The other half of the same surface.

    The catch-all is handed whatever the /assets mount did not claim, so a
    `..` arrives there too. It must keep treating the path as an opaque
    client-side route and answer with index.html -- never resolve it against
    the filesystem.
    """
    outside = dist.parent / "outside.txt"
    outside.write_text(SECRET, encoding="utf-8")

    status, headers, body = await _raw_asgi_get(_app_with_spa(session_factory, dist), raw_path)

    assert status == 200
    assert headers[b"content-type"].startswith(b"text/html")
    text = body.decode("utf-8", "replace")
    assert INDEX_MARKER in text
    assert SECRET not in text
