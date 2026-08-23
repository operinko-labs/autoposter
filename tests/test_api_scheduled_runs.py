"""POST /api/scheduled-runs/{name}/run -- the dashboard's "run now" button."""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ScheduledRun

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


async def test_run_now_requires_a_session(client):
    response = await client.post("/api/scheduled-runs/arr_sync/run")
    assert response.status_code == 401


async def test_an_unknown_name_without_a_token_is_still_a_401(client):
    """Auth before the allowlist: the name check must not tell an
    unauthenticated caller which job names exist."""
    response = await client.post("/api/scheduled-runs/not_a_job/run")
    assert response.status_code == 401


async def test_an_unknown_job_name_is_a_404(client, auth_headers, session):
    response = await client.post(
        "/api/scheduled-runs/rm_minus_rf/run", headers=auth_headers
    )
    assert response.status_code == 404
    # And nothing was written: an unrecognised name must not be able to seed
    # the table with rows the scheduler will never run.
    rows = (await session.execute(select(ScheduledRun))).scalars().all()
    assert rows == []


async def test_requesting_a_job_with_no_row_yet_inserts_one_that_is_due(
    client, auth_headers, session
):
    """``claim_due`` treats ``last_started_at IS NULL`` as due unconditionally
    (scheduler/core.py), so an inserted row -- which has a NULL
    ``last_started_at`` by construction -- is picked up on the next poll."""
    response = await client.post(
        "/api/scheduled-runs/collections_reconcile/run", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == {"status": "requested", "poll_seconds": 60}

    row = (await session.execute(select(ScheduledRun))).scalars().one()
    assert row.name == "collections_reconcile"
    assert row.last_started_at is None


async def test_requesting_a_job_that_has_already_run_clears_its_last_started_at(
    client, auth_headers, session
):
    """The row exists and is not yet due; nulling ``last_started_at`` is what
    makes it due. The rest of the row is history and must survive."""
    session.add(
        ScheduledRun(
            name="ratings_drift_sweep",
            last_status="ok",
            last_detail="123 items checked",
        )
    )
    await session.execute(
        update(ScheduledRun)
        .where(ScheduledRun.name == "ratings_drift_sweep")
        .values(last_started_at=func.now())
    )
    await session.commit()
    session.expire_all()
    before = (await session.execute(select(ScheduledRun))).scalars().one()
    assert before.last_started_at is not None, "precondition: the row is not due"

    response = await client.post(
        "/api/scheduled-runs/ratings_drift_sweep/run", headers=auth_headers
    )
    assert response.status_code == 200

    session.expire_all()
    row = (await session.execute(select(ScheduledRun))).scalars().one()
    assert row.last_started_at is None
    assert row.last_status == "ok"
    assert row.last_detail == "123 items checked"


async def test_asking_twice_leaves_exactly_one_row(client, auth_headers, session):
    """The ON CONFLICT arm, exercised from the endpoint rather than from a
    hand-seeded row: a second request must update, never insert a duplicate."""
    for _ in range(2):
        response = await client.post(
            "/api/scheduled-runs/asset_cleanup/run", headers=auth_headers
        )
        assert response.status_code == 200

    rows = (await session.execute(select(ScheduledRun))).scalars().all()
    assert len(rows) == 1
    assert rows[0].last_started_at is None
