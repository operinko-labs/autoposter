"""GET /api/logs and GET /api/logs/stream, and the LogBuffer behind them."""
import asyncio
import json
import logging
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.api.logs import LogBuffer
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
    """The dashboard polls the API every few seconds; with access lines
    included, the log view would be mostly a mirror of the viewer's own
    requests."""
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


async def test_stream_requires_a_session(client):
    response = await client.get("/api/logs/stream")
    assert response.status_code == 401


async def test_stream_yields_the_backlog_then_lines_logged_while_connected(
    app, client, auth_headers
):
    app.state.log_buffer.emit(record("from the backlog"))

    async with client.stream("GET", "/api/logs/stream", headers=auth_headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        lines = response.aiter_lines()

        first = json.loads(await asyncio.wait_for(anext(lines), timeout=5))
        assert first["message"] == "from the backlog"

        app.state.log_buffer.emit(record("logged live"))
        second = json.loads(await asyncio.wait_for(anext(lines), timeout=5))
        assert second["message"] == "logged live"


async def test_stream_unsubscribes_when_the_client_disconnects(app, client, auth_headers):
    """A closed connection must not leave its queue subscribed forever --
    every line ever logged after it would pile up against the dead reader's
    cap and every fanout would keep paying for it."""
    app.state.log_buffer.emit(record("one line"))

    async with client.stream("GET", "/api/logs/stream", headers=auth_headers) as response:
        await asyncio.wait_for(anext(response.aiter_lines()), timeout=5)
        assert len(app.state.log_buffer._subscribers) == 1

    # The generator's finally runs on close; give the loop a tick to run it.
    for _ in range(10):
        if not app.state.log_buffer._subscribers:
            break
        await asyncio.sleep(0.05)
    assert len(app.state.log_buffer._subscribers) == 0
