"""GET /api/dashboard/stream, and the StatusBroadcaster behind it.

The stream never ends, and httpx's ASGITransport runs the app to completion
before returning a response -- an endpoint-level test of it does not fail, it
hangs until the runner is killed (see tests/test_api_logs.py:145-152, where
that cost this suite three ten-hour containers). So the endpoint tests here
cover only what terminates, and the streaming behaviour is driven against
``ndjson_snapshots`` and the broadcaster directly, with bounded iteration and
an explicit aclose().
"""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.api.dashboard_stream import (
    StatusBroadcaster,
    ndjson_snapshots,
    stream_dashboard,
)
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, Job, ScheduledRun

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

# Fast enough that a test does not wait on the production 2s cadence, slow
# enough that several polls still fit in a sleep a test can afford.
FAST_POLL = 0.02


@pytest.fixture
def config():
    return load_config(EXAMPLE)


async def stop(broadcaster: StatusBroadcaster) -> None:
    """Unsubscribe everything, then let every cancelled poll task finish
    unwinding before the test's engine is disposed.

    Not optional bookkeeping: a poll cancelled mid-query leaves its session
    checked out and idle in transaction until the task is actually run again,
    and the next test's TRUNCATE then blocks on it forever.
    """
    for queue in list(broadcaster._subscribers):
        broadcaster.unsubscribe(queue)
    pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=10)


@pytest_asyncio.fixture
async def app(session_factory, config):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    made = create_app(config, session_factory, secrets)
    yield made
    await stop(made.state.dashboard_broadcaster)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest_asyncio.fixture
async def broadcaster(session_factory, config):
    """A broadcaster of this test's own, polling fast, always stopped."""
    made = StatusBroadcaster(session_factory, config, {}, interval_seconds=FAST_POLL)
    yield made
    await stop(made)


def an_event(source: str = "radarr") -> EventLog:
    return EventLog(source=source, event_type="Download", payload={}, outcome="queued")


# --- the snapshot the broadcaster builds ---


async def test_the_snapshot_matches_the_rest_endpoints_exactly(
    app, client, auth_headers, session
):
    """The SPA renders the stream's payload with the same code that renders
    the REST responses, so the two must be the same bytes -- including the
    datetime rendering, where ``str(datetime)`` would emit a space in place of
    the ISO ``T``."""
    session.add_all([
        Job(kind="render", payload={}, state="pending"),
        Job(kind="render", payload={}, state="done"),
        ScheduledRun(name="ratings_drift_sweep", last_status="ok", last_detail="12 checked"),
        an_event("sonarr"),
    ])
    await session.commit()
    # In place, the way the lifespan fills it -- the broadcaster was handed
    # this dict at construction and must see the change.
    app.state.scheduler_intervals["ratings_drift_sweep"] = 86400

    status = (await client.get("/api/status", headers=auth_headers)).json()
    events = (
        await client.get("/api/events", headers=auth_headers, params={"limit": 25})
    ).json()["events"]
    assert status["scheduled_jobs"][0]["interval_seconds"] == 86400, (
        "precondition: the REST endpoint reports the interval"
    )

    stream = ndjson_snapshots(app.state.dashboard_broadcaster)
    try:
        snapshot = json.loads(await asyncio.wait_for(anext(stream), timeout=10))
    finally:
        await stream.aclose()

    assert snapshot == {"status": status, "events": events}


async def test_the_snapshot_never_carries_the_event_payload(app, session):
    """/api/events omits ``payload`` because it holds whole webhook bodies and
    can carry tokens from the sending service; the stream reuses that shape
    and must not widen it."""
    session.add(
        EventLog(
            source="manual", event_type="test", outcome="queued",
            payload={"secret_token": "should-never-leak"},
        )
    )
    await session.commit()

    stream = ndjson_snapshots(app.state.dashboard_broadcaster)
    try:
        line = await asyncio.wait_for(anext(stream), timeout=10)
    finally:
        await stream.aclose()

    assert "should-never-leak" not in line
    assert "payload" not in json.loads(line)["events"][0]


# --- fan-out only on change ---


async def test_an_unchanged_snapshot_is_not_fanned_out(broadcaster):
    """The whole point of polling server-side: viewers are pushed to when
    something happens, not every tick. Without the comparison this is a 2s
    poll relayed to every open tab."""
    _, queue = broadcaster.subscribe()
    await asyncio.wait_for(queue.get(), timeout=10)

    # Many more polls over a database nothing has touched.
    await asyncio.sleep(FAST_POLL * 10)
    assert queue.qsize() == 0

    broadcaster.unsubscribe(queue)


async def test_a_change_is_fanned_out_exactly_once(broadcaster, session):
    _, queue = broadcaster.subscribe()
    first = await asyncio.wait_for(queue.get(), timeout=10)
    assert first["events"] == []

    session.add(an_event())
    await session.commit()

    changed = await asyncio.wait_for(queue.get(), timeout=10)
    assert [event["source"] for event in changed["events"]] == ["radarr"]

    # The polls that follow see the same database and must stay silent.
    await asyncio.sleep(FAST_POLL * 10)
    assert queue.qsize() == 0

    broadcaster.unsubscribe(queue)


async def test_a_full_subscriber_queue_drops_snapshots_rather_than_blocking(broadcaster):
    """A stalled reader must not cost the process memory without bound; it
    misses the intermediate snapshots and gets the next changed one."""
    _, queue = broadcaster.subscribe()
    for i in range(queue.maxsize + 10):
        broadcaster._publish({"status": {"processed_last_24h": i}, "events": []})

    assert queue.qsize() == queue.maxsize

    broadcaster.unsubscribe(queue)


# --- the subscriber-driven poll loop ---


async def test_the_poll_loop_runs_only_while_somebody_is_subscribed(broadcaster, session):
    """No background database traffic with zero viewers -- and a viewer
    arriving afterwards gets a loop again, not silence."""
    assert broadcaster._task is None, "precondition: construction starts nothing"

    _, queue = broadcaster.subscribe()
    first_task = broadcaster._task
    assert first_task is not None
    await asyncio.wait_for(queue.get(), timeout=10)

    broadcaster.unsubscribe(queue)
    assert broadcaster._task is None
    await asyncio.wait([first_task], timeout=10)
    assert first_task.done()

    # A new viewer restarts it, on a task of its own.
    latest, second = broadcaster.subscribe()
    assert latest is not None, "the snapshot survives the gap; the loop does not"
    assert broadcaster._task is not None and broadcaster._task is not first_task

    session.add(an_event())
    await session.commit()
    changed = await asyncio.wait_for(second.get(), timeout=10)
    assert [event["source"] for event in changed["events"]] == ["radarr"]

    broadcaster.unsubscribe(second)


async def test_unsubscribing_the_same_queue_twice_is_harmless(broadcaster):
    """A generator closed twice, or a finally reached after an explicit
    unsubscribe, must not disturb the loop the other viewers are on."""
    _, first = broadcaster.subscribe()
    _, second = broadcaster.subscribe()
    task = broadcaster._task

    broadcaster.unsubscribe(first)
    broadcaster.unsubscribe(first)
    assert broadcaster._task is task
    assert not task.done()

    broadcaster.unsubscribe(second)
    assert broadcaster._task is None


async def test_a_failed_poll_does_not_end_the_loop(session_factory, config, caplog):
    """A database blip must cost one snapshot, not everyone's stream."""
    attempts = []

    def flaky_factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("connection refused")
        return session_factory()

    broadcaster = StatusBroadcaster(
        flaky_factory, config, {}, interval_seconds=FAST_POLL
    )
    _, queue = broadcaster.subscribe()
    with caplog.at_level(logging.WARNING):
        snapshot = await asyncio.wait_for(queue.get(), timeout=10)

    assert len(attempts) >= 2, "the loop tried again after the failure"
    assert "status" in snapshot and "events" in snapshot
    assert "dashboard status poll failed" in caplog.text

    await stop(broadcaster)


async def test_the_broadcaster_reads_scheduler_intervals_filled_after_construction(
    session_factory, config, session
):
    """create_app builds the broadcaster before the lifespan registers any
    scheduler job, so it holds the mapping itself rather than its contents --
    see app.py, which fills that dict in place for this reason."""
    intervals: dict = {}
    session.add(ScheduledRun(name="asset_cleanup"))
    await session.commit()
    broadcaster = StatusBroadcaster(
        session_factory, config, intervals, interval_seconds=FAST_POLL
    )
    _, queue = broadcaster.subscribe()
    first = await asyncio.wait_for(queue.get(), timeout=10)
    assert first["status"]["scheduled_jobs"][0]["interval_seconds"] is None

    intervals["asset_cleanup"] = 3600
    changed = await asyncio.wait_for(queue.get(), timeout=10)
    assert changed["status"]["scheduled_jobs"][0]["interval_seconds"] == 3600

    await stop(broadcaster)


# --- the NDJSON generator ---


async def test_the_stream_yields_the_current_snapshot_then_changes_then_a_heartbeat(
    broadcaster, session, monkeypatch
):
    # Prime the broadcaster so a snapshot already exists when the reader
    # connects -- the case a real viewer hits on any but the first connection.
    _, primer = broadcaster.subscribe()
    await asyncio.wait_for(primer.get(), timeout=10)
    broadcaster.unsubscribe(primer)

    stream = ndjson_snapshots(broadcaster)
    try:
        first = json.loads(await asyncio.wait_for(anext(stream), timeout=10))
        assert first["events"] == []

        session.add(an_event())
        await session.commit()
        second = json.loads(await asyncio.wait_for(anext(stream), timeout=10))
        assert [event["source"] for event in second["events"]] == ["radarr"]

        # Only now, so neither line above can race the keepalive: silence must
        # not be indistinguishable from a dead connection.
        monkeypatch.setattr("autoposter.api.dashboard_stream.HEARTBEAT_SECONDS", 0.05)
        third = json.loads(await asyncio.wait_for(anext(stream), timeout=10))
        assert third == {"heartbeat": True}
    finally:
        await stream.aclose()


async def test_the_stream_waits_for_the_first_snapshot_rather_than_yielding_a_null(
    broadcaster,
):
    """Nothing has been polled yet on a cold process; the reader gets the
    first real snapshot, not an empty line it would have to special-case."""
    stream = ndjson_snapshots(broadcaster)
    try:
        first = json.loads(await asyncio.wait_for(anext(stream), timeout=10))
        assert set(first) == {"status", "events"}
    finally:
        await stream.aclose()


async def test_the_stream_unsubscribes_and_stops_polling_on_disconnect(broadcaster):
    """A closed connection must not leave its queue in the fanout set -- and
    because the loop lives exactly as long as its subscribers, a leak here
    means the process polls the database forever with nobody watching.
    Starlette closes the body generator when the client goes away, which is
    what aclose() models."""
    stream = ndjson_snapshots(broadcaster)
    await asyncio.wait_for(anext(stream), timeout=10)
    assert len(broadcaster._subscribers) == 1
    task = broadcaster._task

    await stream.aclose()

    assert len(broadcaster._subscribers) == 0
    assert broadcaster._task is None
    await asyncio.wait([task], timeout=10)
    assert task.done()


# --- the endpoint ---


async def test_the_stream_requires_a_session(client):
    response = await client.get("/api/dashboard/stream")
    assert response.status_code == 401


async def test_the_stream_response_is_unbuffered_ndjson(app):
    """The frame the SPA parses, and the header that stops a reverse proxy
    from holding the stream back until the (never-arriving) end of the body."""
    request = SimpleNamespace(app=app)
    response = await stream_dashboard(request, _=None)

    assert response.media_type == "application/x-ndjson"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    await response.body_iterator.aclose()


async def test_create_app_publishes_a_broadcaster_without_starting_one(app):
    """The logs buffer precedent: always there for the endpoint to reach, but
    create_app has no running loop, so nothing may be scheduled here."""
    assert isinstance(app.state.dashboard_broadcaster, StatusBroadcaster)
    assert app.state.dashboard_broadcaster._task is None
