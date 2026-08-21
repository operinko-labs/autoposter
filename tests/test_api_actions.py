"""GET /api/jobs/parked, retry/dismiss, POST /api/items/{id}/reprocess and
GET /api/config."""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Job, MediaItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

# A recognisable secret value used by the redaction test -- chosen to be
# unlikely to appear anywhere else in the response by accident.
SECRET_TMDB_TOKEN = "sekrit-tmdb-token-should-never-leak-9f31c2"


@pytest_asyncio.fixture
async def client(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token=SECRET_TMDB_TOKEN, tvdb_apikey="x",
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


def _parked_job(**overrides):
    fields = {
        "kind": "process_item",
        "payload": {"kind": "movie", "title": "A"},
        "state": "parked",
        "attempts": 5,
        "last_error": "Plex never scanned the file",
    }
    fields.update(overrides)
    return Job(**fields)


# --- GET /api/jobs/parked ---


async def test_parked_jobs_requires_a_session(client):
    response = await client.get("/api/jobs/parked")
    assert response.status_code == 401


async def test_parked_jobs_lists_them_with_their_reasons(client, auth_headers, session):
    session.add(_parked_job(last_error="Plex never scanned the file"))
    await session.commit()

    response = await client.get("/api/jobs/parked", headers=auth_headers)
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["reason"] == "Plex never scanned the file"
    assert jobs[0]["attempts"] == 5


async def test_parked_jobs_excludes_other_states(client, auth_headers, session):
    session.add_all([
        Job(kind="process_item", payload={}, state="pending"),
        Job(kind="process_item", payload={}, state="done"),
        _parked_job(),
    ])
    await session.commit()

    response = await client.get("/api/jobs/parked", headers=auth_headers)
    assert len(response.json()["jobs"]) == 1


# --- POST /api/jobs/{job_id}/retry ---


async def test_retry_requires_a_session(client, session):
    session.add(_parked_job())
    await session.commit()
    response = await client.post("/api/jobs/1/retry")
    assert response.status_code == 401


async def test_retry_makes_a_parked_job_claimable_again(client, auth_headers, session):
    session.add(_parked_job(attempts=5))
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id

    response = await client.post(f"/api/jobs/{job_id}/retry", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["state"] == "pending"

    row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.state == "pending"
    assert row.attempts == 0


async def test_retrying_an_unknown_job_is_404(client, auth_headers):
    response = await client.post("/api/jobs/999999/retry", headers=auth_headers)
    assert response.status_code == 404


async def test_retrying_a_job_that_is_not_parked_is_404_not_500(client, auth_headers, session):
    session.add(Job(kind="process_item", payload={}, state="pending"))
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id

    response = await client.post(f"/api/jobs/{job_id}/retry", headers=auth_headers)
    assert response.status_code == 404


async def test_retrying_an_already_dismissed_job_is_404(client, auth_headers, session):
    session.add(_parked_job())
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id
    await client.post(f"/api/jobs/{job_id}/dismiss", headers=auth_headers)

    response = await client.post(f"/api/jobs/{job_id}/retry", headers=auth_headers)
    assert response.status_code == 404


# --- POST /api/jobs/{job_id}/dismiss ---


async def test_dismiss_requires_a_session(client, session):
    session.add(_parked_job())
    await session.commit()
    response = await client.post("/api/jobs/1/dismiss")
    assert response.status_code == 401


async def test_dismiss_marks_without_deleting_the_row(client, auth_headers, session):
    session.add(_parked_job(last_error="disk full"))
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id

    response = await client.post(f"/api/jobs/{job_id}/dismiss", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["state"] == "dismissed"

    row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    assert row is not None
    assert row.state == "dismissed"
    assert row.last_error == "disk full"


async def test_dismissing_an_unknown_job_is_404(client, auth_headers):
    response = await client.post("/api/jobs/999999/dismiss", headers=auth_headers)
    assert response.status_code == 404


async def test_dismissing_an_already_dismissed_job_is_404_not_500(client, auth_headers, session):
    session.add(_parked_job())
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id
    first = await client.post(f"/api/jobs/{job_id}/dismiss", headers=auth_headers)
    assert first.status_code == 200

    second = await client.post(f"/api/jobs/{job_id}/dismiss", headers=auth_headers)
    assert second.status_code == 404

    row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.state == "dismissed"


# --- POST /api/items/{item_id}/reprocess ---


async def test_reprocess_requires_a_session(client, session):
    session.add(MediaItem(rating_key="rk1", library="Movies", kind="movie", title="A"))
    await session.commit()
    response = await client.post("/api/items/1/reprocess")
    assert response.status_code == 401


async def test_reprocess_enqueues_a_job(client, auth_headers, session):
    session.add(MediaItem(rating_key="rk1", library="Movies", kind="movie", title="A"))
    await session.commit()
    item_id = (await session.execute(select(MediaItem))).scalars().one().id

    response = await client.post(f"/api/items/{item_id}/reprocess", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["queued"] is True
    assert body["job_id"] is not None

    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].kind == "process_item"
    assert jobs[0].state == "pending"


async def test_reprocessing_twice_still_queues_once(client, auth_headers, session):
    session.add(MediaItem(rating_key="rk1", library="Movies", kind="movie", title="A"))
    await session.commit()
    item_id = (await session.execute(select(MediaItem))).scalars().one().id

    first = await client.post(f"/api/items/{item_id}/reprocess", headers=auth_headers)
    second = await client.post(f"/api/items/{item_id}/reprocess", headers=auth_headers)

    assert first.json()["queued"] is True
    assert second.json()["queued"] is False
    assert second.json()["job_id"] is None

    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1


async def test_reprocessing_an_unknown_item_is_404(client, auth_headers):
    response = await client.post("/api/items/999999/reprocess", headers=auth_headers)
    assert response.status_code == 404


# --- GET /api/config ---


async def test_config_requires_a_session(client):
    response = await client.get("/api/config")
    assert response.status_code == 401


async def test_config_returns_the_configuration_shape(client, auth_headers):
    response = await client.get("/api/config", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    # From config/autoposter.example.yaml -- proves this is the real config,
    # not an empty stub.
    assert body["workers"] == 5
    assert "plex" in body
    assert "artwork" in body


async def test_config_never_leaks_a_secret_value(client, auth_headers):
    """Seeds a recognisable secret and asserts it appears nowhere in the
    serialised response body, rather than checking specific fields -- a
    field-by-field assertion would silently miss a key added later."""
    response = await client.get("/api/config", headers=auth_headers)
    assert response.status_code == 200
    assert SECRET_TMDB_TOKEN not in response.text
    # Also nowhere in the login password's hash, in case a future change
    # starts threading the admin hash through some other field.
    assert hash_password(PASSWORD) not in response.text
