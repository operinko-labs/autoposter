"""GET /api/logs and GET /api/logs/stream, and the LogBuffer behind them."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.api.logs import LogBuffer, ndjson_lines, stream_logs
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def app(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    return create_app(load_config(EXAMPLE), session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def record(message: str, *, name: str = "autoposter.test", level: int = logging.INFO):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )


# --- LogBuffer ---


def test_buffer_keeps_only_the_most_recent_capacity_lines():
    buffer = LogBuffer(capacity=3)
    for i in range(5):
        buffer.emit(record(f"line {i}"))
    assert [entry["message"] for entry in buffer.lines()] == [
        "line 2", "line 3", "line 4",
    ]


def test_buffer_entries_carry_level_logger_and_timestamp():
    buffer = LogBuffer()
    buffer.emit(record("something happened", level=logging.WARNING))
    (entry,) = buffer.lines()
    assert entry["level"] == "WARNING"
    assert entry["logger"] == "autoposter.test"
    assert entry["message"] == "something happened"
    # ISO 8601 with an explicit offset, like every other timestamp the API
    # hands the SPA.
    assert entry["ts"].endswith("+00:00")


def test_buffer_excludes_uvicorn_access_lines():
    """With access lines included, the log view would be mostly a mirror of
    the viewer's own requests rather than a record of what the service did."""
    buffer = LogBuffer()
    buffer.emit(record('GET /api/status 200', name="uvicorn.access"))
    buffer.emit(record("a real line"))
    assert [entry["message"] for entry in buffer.lines()] == ["a real line"]


def test_buffer_includes_the_traceback_when_a_record_carries_one():
    buffer = LogBuffer()
    try:
        raise ValueError("the underlying failure")
    except ValueError:
        import sys

        buffer.emit(
            logging.LogRecord(
                name="autoposter.test", level=logging.ERROR, pathname=__file__,
                lineno=1, msg="job failed", args=(), exc_info=sys.exc_info(),
            )
        )
    (entry,) = buffer.lines()
    assert "job failed" in entry["message"]
    assert "ValueError: the underlying failure" in entry["message"]


def test_credential_query_params_are_scrubbed_from_the_served_message():
    """Roadmap row 117: Fanart's api_key rides the query string, httpx embeds
    the full URL in an HTTPStatusError's message, and ladder.py:81 (and the
    candidates fan-out) log provider failures with exc_info -- the traceback's
    last line landed in this buffer and was served by /api/logs and the
    stream. The scrub is here, at the one point the served surface's text is
    assembled: stdout keeps the full traceback under the repo's host-only
    rule."""
    buffer = LogBuffer()
    try:
        raise ValueError(
            "Client error '401 Unauthorized' for url "
            "'https://webservice.fanart.tv/v3/movies/603?api_key=SECRETKEY'"
        )
    except ValueError:
        import sys

        buffer.emit(
            logging.LogRecord(
                name="autoposter.providers.ladder", level=logging.WARNING,
                pathname=__file__, lineno=1, msg="provider Fanart failed, continuing",
                args=(), exc_info=sys.exc_info(),
            )
        )
    (entry,) = buffer.lines()
    assert "SECRETKEY" not in entry["message"]
    assert "api_key=REDACTED" in entry["message"]
    # The traceback itself survives -- only the credential goes.
    assert "ValueError" in entry["message"]
    assert "provider Fanart failed" in entry["message"]


def test_a_token_param_in_a_plain_message_is_scrubbed_too():
    """The same law for a message that was never an exception: a URL with
    X-Plex-Token pasted into an ordinary log line must not be served intact."""
    buffer = LogBuffer()
    buffer.emit(record(
        "GET http://plex.internal:32400/library/all?X-Plex-Token=PLEXSECRET failed"
    ))
    (entry,) = buffer.lines()
    assert "PLEXSECRET" not in entry["message"]
    assert "X-Plex-Token=REDACTED" in entry["message"]


async def test_a_full_subscriber_queue_drops_lines_rather_than_blocking():
    """One slow stream reader must not cost the process memory without bound
    or stall the emitting thread."""
    buffer = LogBuffer()
    backlog, queue = buffer.subscribe()
    assert backlog == []
    for i in range(queue.maxsize + 50):
        buffer.emit(record(f"line {i}"))
    # emit hands fanout to the loop via call_soon_threadsafe; let it run.
    await asyncio.sleep(0)
    assert queue.qsize() == queue.maxsize
    buffer.unsubscribe(queue)


# --- /api/logs ---


async def test_logs_require_a_session(client):
    response = await client.get("/api/logs")
    assert response.status_code == 401


async def test_logs_return_buffered_lines_oldest_first(app, client, auth_headers):
    app.state.log_buffer.emit(record("first"))
    app.state.log_buffer.emit(record("second"))

    response = await client.get("/api/logs", headers=auth_headers)
    assert response.status_code == 200
    assert [line["message"] for line in response.json()["lines"]] == ["first", "second"]


async def test_logs_limit_returns_the_most_recent_lines(app, client, auth_headers):
    for i in range(10):
        app.state.log_buffer.emit(record(f"line {i}"))

    response = await client.get("/api/logs", headers=auth_headers, params={"limit": 3})
    assert [line["message"] for line in response.json()["lines"]] == [
        "line 7", "line 8", "line 9",
    ]


# --- /api/logs/stream ---
#
# The tail never ends, and httpx's ASGITransport runs the app to completion
# before returning a response -- an endpoint-level test of it does not fail,
# it hangs until the runner is killed (this suite proved that: three
# containers sat blocked for ten hours). So the endpoint tests below cover
# only what terminates, and the streaming behaviour is driven against
# `ndjson_lines` directly, with bounded iteration and an explicit aclose().


async def test_stream_requires_a_session(client):
    response = await client.get("/api/logs/stream")
    assert response.status_code == 401


async def test_stream_response_is_unbuffered_ndjson(app):
    """The frame the SPA parses, and the header that stops a reverse proxy
    from holding the tail back until the (never-arriving) end of the body."""
    request = SimpleNamespace(app=app)
    response = await stream_logs(request, _=None)

    assert response.media_type == "application/x-ndjson"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    await response.body_iterator.aclose()


async def test_stream_yields_the_backlog_then_lines_logged_while_connected(app):
    buffer = app.state.log_buffer
    buffer.emit(record("from the backlog"))

    stream = ndjson_lines(buffer)
    try:
        first = json.loads(await asyncio.wait_for(anext(stream), timeout=5))
        assert first["message"] == "from the backlog"

        buffer.emit(record("logged live"))
        second = json.loads(await asyncio.wait_for(anext(stream), timeout=5))
        assert second["message"] == "logged live"
    finally:
        await stream.aclose()


async def test_stream_sends_a_heartbeat_when_nothing_is_logged(app, monkeypatch):
    """Silence must not be indistinguishable from a dead connection: the
    keepalive is what makes a broken socket fail a write rather than hold the
    subscription open."""
    monkeypatch.setattr("autoposter.api.logs.HEARTBEAT_SECONDS", 0.05)
    stream = ndjson_lines(app.state.log_buffer)
    try:
        assert json.loads(await asyncio.wait_for(anext(stream), timeout=5)) == {
            "heartbeat": True
        }
    finally:
        await stream.aclose()


async def test_stream_unsubscribes_when_the_client_disconnects(app):
    """A closed connection must not leave its queue subscribed forever --
    every line ever logged after it would pile up against the dead reader's
    cap and every fanout would keep paying for it. Starlette closes the body
    generator when the client goes away, which is what aclose() models here."""
    buffer = app.state.log_buffer
    buffer.emit(record("one line"))

    stream = ndjson_lines(buffer)
    await asyncio.wait_for(anext(stream), timeout=5)
    assert len(buffer._subscribers) == 1

    await stream.aclose()
    assert len(buffer._subscribers) == 0
