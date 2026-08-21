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

# Recognisable secret values used by the redaction test -- chosen to be
# unlikely to appear anywhere else in the response by accident. Every one of
# them is distinct: seeding "x" everywhere left only tmdb_token genuinely
# asserted on, since "x" appears in any response by chance.
SECRET_TMDB_TOKEN = "sekrit-tmdb-token-should-never-leak-9f31c2"
SECRET_PLEX_TOKEN = "sekrit-plex-token-should-never-leak-4b7ade"
SECRET_WEBHOOK_SECRET = "sekrit-webhook-secret-should-never-leak-c05e18"
# The hash the app actually holds. bcrypt is salted, so hashing PASSWORD
# again in the assertion would produce a different string that could never
# appear in a response whether the endpoint leaked or not.
ADMIN_PASSWORD_HASH = hash_password(PASSWORD)


@pytest_asyncio.fixture
async def client(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token=SECRET_PLEX_TOKEN, tmdb_token=SECRET_TMDB_TOKEN, tvdb_apikey="x",
        fanart_apikey="x", webhook_secret=SECRET_WEBHOOK_SECRET,
        admin_password_hash=ADMIN_PASSWORD_HASH,
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


async def test_parked_jobs_pagination_reaches_past_the_first_page(client, auth_headers, session):
    """Without an offset, anything past the first ``limit`` parked jobs is
    unreachable -- and a backlog is exactly when this endpoint matters."""
    session.add_all([_parked_job(last_error=f"reason {i}") for i in range(3)])
    await session.commit()

    first_page = await client.get(
        "/api/jobs/parked", headers=auth_headers, params={"limit": 2, "offset": 0}
    )
    second_page = await client.get(
        "/api/jobs/parked", headers=auth_headers, params={"limit": 2, "offset": 2}
    )

    first_ids = [job["id"] for job in first_page.json()["jobs"]]
    second_ids = [job["id"] for job in second_page.json()["jobs"]]
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert not set(first_ids) & set(second_ids)


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


async def test_retrying_a_job_whose_item_is_already_queued_is_409_not_500(
    client, auth_headers, session
):
    """uq_jobs_pending_dedupe allows one pending job per dedupe key. An item
    parks after repeated failures, a webhook then queues a fresh pending job
    for the same item, and Retry collides with the index. Every production
    job carries a dedupe_key -- the other tests here leave it None, which is
    why this went unnoticed."""
    dedupe_key = "movie:tmdb:603"
    session.add_all([
        _parked_job(dedupe_key=dedupe_key),
        Job(kind="process_item", payload={}, state="pending", dedupe_key=dedupe_key),
    ])
    await session.commit()
    parked_id = (
        await session.execute(select(Job.id).where(Job.state == "parked"))
    ).scalar_one()

    response = await client.post(f"/api/jobs/{parked_id}/retry", headers=auth_headers)
    assert response.status_code == 409
    assert "already queued" in response.json()["detail"]

    session.expire_all()  # read the row back from the database, not the map
    row = (await session.execute(select(Job).where(Job.id == parked_id))).scalar_one()
    assert row.state == "parked"


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
    """Seeds recognisable secrets and asserts none appears anywhere in the
    serialised response body, rather than checking specific fields -- a
    field-by-field assertion would silently miss a key added later."""
    response = await client.get("/api/config", headers=auth_headers)
    assert response.status_code == 200
    assert SECRET_TMDB_TOKEN not in response.text
    assert SECRET_PLEX_TOKEN not in response.text
    assert SECRET_WEBHOOK_SECRET not in response.text
    # The admin hash the app is actually holding, not a fresh hash of the
    # same password: bcrypt is salted, so a fresh one could never appear in
    # any response and would assert nothing.
    assert ADMIN_PASSWORD_HASH not in response.text
