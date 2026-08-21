"""GET /api/status and GET /api/events."""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, Job, ScheduledRun

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def client(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def token(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return response.json()["token"]


@pytest_asyncio.fixture
async def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# --- /api/status ---


async def test_status_requires_a_session(client):
    response = await client.get("/api/status")
    assert response.status_code == 401


async def test_status_on_an_empty_database_returns_zeroed_counts(client, auth_headers):
    response = await client.get("/api/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["jobs_by_state"] == {
        "pending": 0, "running": 0, "done": 0, "failed": 0, "parked": 0,
    }
    assert body["processed_last_24h"] == 0
    assert body["scheduled_jobs"] == []


async def test_status_counts_queued_running_and_parked_jobs_separately(
    client, auth_headers, session
):
    session.add_all([
        Job(kind="render", payload={}, state="pending"),
        Job(kind="render", payload={}, state="pending"),
        Job(kind="render", payload={}, state="running"),
        Job(kind="render", payload={}, state="parked"),
        Job(kind="render", payload={}, state="parked"),
        Job(kind="render", payload={}, state="parked"),
    ])
    await session.commit()

    response = await client.get("/api/status", headers=auth_headers)
    body = response.json()
    assert body["jobs_by_state"]["pending"] == 2
    assert body["jobs_by_state"]["running"] == 1
    assert body["jobs_by_state"]["parked"] == 3
    assert body["jobs_by_state"]["done"] == 0
    assert body["jobs_by_state"]["failed"] == 0


async def test_status_processed_24h_window_excludes_older_rows(client, auth_headers, session):
    session.add_all([
        Job(kind="render", payload={}, state="done"),
        Job(kind="render", payload={}, state="done"),
    ])
    await session.commit()
    # Push one of the two "done" jobs outside the 24h window.
    await session.execute(
        text(
            "UPDATE jobs SET updated_at = now() - interval '25 hours' "
            "WHERE id = (SELECT id FROM jobs ORDER BY id LIMIT 1)"
        )
    )
    await session.commit()

    response = await client.get("/api/status", headers=auth_headers)
    assert response.json()["processed_last_24h"] == 1


async def test_status_reports_scheduled_job_last_run_and_status(client, auth_headers, session):
    session.add(
        ScheduledRun(
            name="drift", last_status="ok", last_detail="123 items checked",
        )
    )
    await session.commit()

    response = await client.get("/api/status", headers=auth_headers)
    scheduled = response.json()["scheduled_jobs"]
    assert len(scheduled) == 1
    assert scheduled[0]["name"] == "drift"
    assert scheduled[0]["last_status"] == "ok"
    assert scheduled[0]["last_detail"] == "123 items checked"


async def test_status_reports_worker_count(client, auth_headers):
    response = await client.get("/api/status", headers=auth_headers)
    assert response.json()["workers"] == 5  # config/autoposter.example.yaml


# --- /api/events ---


async def test_events_requires_a_session(client):
    response = await client.get("/api/events")
    assert response.status_code == 401


async def test_events_come_back_newest_first(client, auth_headers, session):
    session.add_all([
        EventLog(source="radarr", event_type="Download", payload={}, outcome="queued"),
        EventLog(source="sonarr", event_type="Download", payload={}, outcome="skipped"),
    ])
    await session.commit()
    await session.execute(
        text(
            "UPDATE events_log SET received_at = now() - interval '1 hour' "
            "WHERE source = 'radarr'"
        )
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers)
    events = response.json()["events"]
    assert [e["source"] for e in events] == ["sonarr", "radarr"]


async def test_events_limit_defaults_to_50(client, auth_headers, session):
    session.add_all(
        [EventLog(source="tautulli", event_type="x", payload={}) for _ in range(60)]
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers)
    assert len(response.json()["events"]) == 50


async def test_events_limit_is_respected(client, auth_headers, session):
    session.add_all(
        [EventLog(source="tautulli", event_type="x", payload={}) for _ in range(10)]
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers, params={"limit": 3})
    assert len(response.json()["events"]) == 3


async def test_events_limit_is_capped_above_its_maximum(client, auth_headers, session):
    session.add_all(
        [EventLog(source="tautulli", event_type="x", payload={}) for _ in range(250)]
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers, params={"limit": 10000})
    assert len(response.json()["events"]) == 200


async def test_events_never_include_the_payload(client, auth_headers, session):
    session.add(
        EventLog(
            source="manual", event_type="test", outcome="queued",
            payload={"secret_token": "should-never-leak"},
        )
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers)
    body = response.text
    assert "should-never-leak" not in body
    assert "payload" not in response.json()["events"][0]
