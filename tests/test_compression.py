"""Response compression (perf spec A1): on for ordinary responses, never for
the two NDJSON streams or the artwork bytes.

The streams are why this is a path filter rather than a bare
``GZipMiddleware``. Whether a gzipped NDJSON stream still reaches the browser
line by line is up to the installed Starlette: the release this project runs
today (1.6) flushes the compressor after every streamed chunk, so each line
does go out at once, but that is that release's behaviour rather than a
contract, and a compressed stream is no longer the bytes the endpoint yielded.
The spec keeps both streams off the compressor by path, so what the dashboard
and the Logs page read is exactly what was written, whatever version is
installed.

The streaming tests here speak raw ASGI. httpx's ``ASGITransport`` runs an app
to completion before it returns a response, so a stream that never ends hangs
it (tests/test_api_logs.py records what that cost). These stop as soon as one
whole line has arrived, and assert the stream was still open at that point:
"delivered before the generator finished" is the property.
"""
import asyncio
import inspect
import json
import logging
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from starlette.responses import StreamingResponse

from autoposter.api.auth import hash_password
from autoposter.api.compression import MINIMUM_SIZE, bypasses_compression, install_compression
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Render

from conftest import seed_media_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def assets_root(tmp_path) -> Path:
    root = tmp_path / "assets"
    root.mkdir()
    return root


async def _settle_other_tasks() -> None:
    """Let every task a test started finish unwinding before the engine is
    disposed: a dashboard poll cancelled mid-query otherwise leaves its
    session idle in transaction, and the next test's TRUNCATE blocks on it
    (tests/test_api_dashboard_stream.py's ``stop`` has the history)."""
    pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=10)


@pytest_asyncio.fixture
async def app(session_factory, assets_root):
    config = load_config(EXAMPLE).model_copy(update={"assets_root": assets_root})
    made = create_app(config, session_factory, _secrets())
    install_compression(made)
    yield made
    await _settle_other_tasks()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _first_line(app, path: str, headers: dict[str, str]):
    """GET ``path`` over raw ASGI, accepting gzip, until one whole NDJSON line
    has been sent. Returns ``(response headers, body bytes so far, whether the
    app was still streaming when the line arrived)``, then disconnects the
    way a closed browser tab does and waits for the app to wind down.

    A response whose headers already say it is compressed counts as arrived:
    its body is gzip, where a newline byte means nothing, and the caller's
    ``content-encoding`` assertion is the failure worth reporting -- not a
    ten-second timeout waiting for a ``\\n`` that may never appear."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"accept-encoding", b"gzip"),
            *[(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()],
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    request_sent = False
    disconnected = asyncio.Event()

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    response_headers: dict[bytes, bytes] = {}
    body = bytearray()
    line_arrived = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.start":
            response_headers.update(dict(message["headers"]))
            if b"content-encoding" in response_headers:
                line_arrived.set()
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))
            if b"\n" in body:
                line_arrived.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        async with asyncio.timeout(10):
            await line_arrived.wait()
        still_streaming = not task.done()
    finally:
        disconnected.set()
        try:
            async with asyncio.timeout(10):
                await task
        except TimeoutError:
            task.cancel()
    return response_headers, bytes(body), still_streaming


async def test_a_large_json_response_is_gzipped_for_a_client_that_accepts_it(
    client, auth_headers
):
    response = await client.get(
        "/api/config", headers={**auth_headers, "Accept-Encoding": "gzip"}
    )

    assert response.status_code == 200
    assert len(response.content) > MINIMUM_SIZE, "precondition: the body is worth compressing"
    assert response.headers["content-encoding"] == "gzip"
    # httpx decoded it; the JSON is intact.
    assert isinstance(response.json(), dict)


async def test_a_client_that_does_not_accept_gzip_gets_the_plain_body(client, auth_headers):
    response = await client.get(
        "/api/config", headers={**auth_headers, "Accept-Encoding": "identity"}
    )

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


async def test_a_small_response_is_left_alone(client):
    response = await client.get("/healthz", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


async def test_artwork_bytes_are_never_compressed(client, auth_headers, session, assets_root):
    """Highly compressible on purpose, and stored as ``.bin`` on purpose: the
    endpoint serves an unrecognised suffix as ``application/octet-stream``,
    which Starlette's gzip does not exclude by content type the way it does
    ``image/jpeg``. Gzip would take this body if the bypass let it, so the
    absent header is the bypass -- not the size threshold, and not
    Starlette's own content-type list."""
    payload = b"\xff\xd8\xff\xe0" + b"compressible " * 1000
    asset = assets_root / "poster.bin"
    asset.write_bytes(payload)
    item = await seed_media_item(session, "rk1", library="Movies", kind="movie", title="A")
    session.add(
        Render(
            item_id=item.id, art_kind="poster", status="rendered",
            asset_path=str(asset), base_sha256="9" * 64,
        )
    )
    await session.commit()

    response = await client.get(
        f"/api/items/{item.id}/artwork/poster",
        headers={**auth_headers, "Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream", (
        "precondition: a content type gzip would compress"
    )
    assert "content-encoding" not in response.headers
    assert response.content == payload


def _api_routes(routes, prefix: str = ""):
    """Every ``APIRoute`` the app serves, with its full path.

    FastAPI 0.141 stopped flattening included routers onto ``app.routes``: it
    leaves an opaque ``_IncludedRouter`` there instead, so a bare walk finds
    none of the API's routes (tests/test_spa_serving.py's
    ``_registered_routes`` has the history). An inclusion's routes are read
    back off its ``original_router`` with the include-time prefix applied;
    the caller's non-empty assertions prove the walk found something."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route, prefix + route.path
            continue
        included = getattr(route, "original_router", None)
        if included is None:
            continue
        context = getattr(route, "include_context", None)
        nested = getattr(context, "prefix", "") or ""
        yield from _api_routes(included.routes, prefix + nested)


async def test_every_streaming_and_artwork_route_bypasses_compression(app):
    """Derived from the route table, not listed: a streaming endpoint added
    later is caught here the day it is registered without a bypass."""
    streams, artwork = [], []
    for route, path in _api_routes(app.routes):
        concrete = path.replace("{item_id}", "7").replace("{art_kind}", "poster")
        returns = inspect.signature(route.endpoint).return_annotation
        if returns in (StreamingResponse, "StreamingResponse"):
            streams.append(concrete)
        elif path.startswith("/api/items/{item_id}/artwork"):
            artwork.append(concrete)

    assert streams, "no streaming route found; the derivation is broken"
    assert artwork, "no artwork route found; the derivation is broken"
    assert [path for path in streams + artwork if not bypasses_compression(path)] == []


@pytest.mark.parametrize("path", ["/api/config", "/api/items/7", "/assets/index-abc.js", "/"])
def test_ordinary_paths_are_compressible(path):
    assert not bypasses_compression(path)


async def test_the_log_stream_delivers_its_first_line_unbuffered_and_uncompressed(
    app, auth_headers
):
    app.state.log_buffer.emit(
        logging.LogRecord(
            "autoposter.probe", logging.INFO, __file__, 1, "compression probe", None, None
        )
    )

    headers, body, still_streaming = await _first_line(app, "/api/logs/stream", auth_headers)

    assert b"content-encoding" not in headers
    assert still_streaming, "the line only arrived once the stream had ended"
    assert json.loads(body.split(b"\n", 1)[0])["message"] == "compression probe"


async def test_the_dashboard_stream_delivers_its_first_snapshot_unbuffered_and_uncompressed(
    app, auth_headers
):
    headers, body, still_streaming = await _first_line(
        app, "/api/dashboard/stream", auth_headers
    )

    assert b"content-encoding" not in headers
    assert still_streaming, "the snapshot only arrived once the stream had ended"
    assert set(json.loads(body.split(b"\n", 1)[0])) == {"status", "events"}
