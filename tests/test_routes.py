import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, Job

FIXTURES = Path(__file__).parent / "fixtures" / "webhooks"
EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
TOKEN = "test-secret"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def secrets():
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret=TOKEN,
    )


@pytest_asyncio.fixture
async def client(session_factory, secrets):
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz_is_open(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_missing_token_is_rejected(client):
    response = await client.post("/webhook/radarr", json=load("radarr_download.json"))
    assert response.status_code == 401


async def test_wrong_token_is_rejected(client):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": "nope"},
    )
    assert response.status_code == 401


async def test_radarr_download_enqueues_one_job(client, session):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 1
    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].kind == "process_item"
    assert jobs[0].payload["tmdb_id"] == 693134


async def test_sonarr_episode_enqueues_three_jobs(client, session):
    response = await client.post(
        "/webhook/sonarr",
        json=load("sonarr_download_single.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.json()["queued"] == 3
    jobs = (await session.execute(select(Job))).scalars().all()
    assert {j.payload["kind"] for j in jobs} == {"show", "season", "episode"}


async def test_repeated_delivery_is_coalesced(client, session):
    payload = load("sonarr_download_single.json")
    headers = {"X-Autoposter-Token": TOKEN}
    first = await client.post("/webhook/sonarr", json=payload, headers=headers)
    second = await client.post("/webhook/sonarr", json=payload, headers=headers)
    assert first.json()["queued"] == 3
    assert second.json()["queued"] == 0
    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 3


async def test_test_payload_is_acknowledged_without_queueing(client, session):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_test.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 0
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_every_delivery_is_logged(client, session):
    await client.post(
        "/webhook/radarr",
        json=load("radarr_test.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert events[0].event_type == "Test"


async def test_jobs_are_delayed_by_the_settle_window(client, session):
    await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    from datetime import timedelta

    job = (await session.execute(select(Job))).scalar_one()
    # Compare against the database clock, never this process's clock: the two
    # can drift (they measurably do on this machine), and run_after is
    # computed by Postgres via func.now() + settle_seconds.
    db_now = (await session.execute(select(func.now()))).scalar_one()
    assert job.run_after > db_now + timedelta(seconds=20)


async def test_non_ascii_token_is_rejected_not_500(client):
    # httpx requires header values to be ASCII-safe str or raw bytes; send the
    # UTF-8 bytes directly to get a genuinely non-ASCII header on the wire.
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": "tökén-é".encode("utf-8")},
    )
    assert response.status_code == 401


async def test_invalid_json_body_returns_400_and_is_logged(client, session):
    response = await client.post(
        "/webhook/radarr",
        content=b"{not valid json",
        headers={
            "X-Autoposter-Token": TOKEN,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert "_raw" in events[0].payload


async def test_non_object_json_body_returns_400_and_is_logged(client, session):
    response = await client.post(
        "/webhook/radarr",
        content=b"[1, 2, 3]",
        headers={
            "X-Autoposter-Token": TOKEN,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert "_raw" in events[0].payload


async def test_parser_bug_returns_500_and_logs_structured_payload(session_factory, secrets, session):
    # A well-formed JSON object whose shape our parser mishandles is our bug,
    # not the sender's: it must surface as a 500, and the EventLog row must
    # keep the structured payload (not a "_raw" text blob) as evidence.
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as broken_client:
        response = await broken_client.post(
            "/webhook/radarr",
            json={"eventType": "Download", "movie": "not-an-object"},
            headers={"X-Autoposter-Token": TOKEN},
        )
    assert response.status_code == 500

    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert "eventType" in events[0].payload
    assert "_raw" not in events[0].payload
    assert "parser error" in events[0].outcome
