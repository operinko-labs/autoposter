"""The login endpoint and the /api router."""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


def _secrets(admin_password_hash: str = "") -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=admin_password_hash,
    )


@pytest_asyncio.fixture
async def client(session_factory):
    secrets = _secrets(hash_password(PASSWORD))
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def client_no_admin_password(session_factory):
    secrets = _secrets("")
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_correct_password_returns_a_token(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["expires_at"]


async def test_wrong_password_returns_401_with_no_token(client):
    response = await client.post("/api/login", json={"password": "nope"})
    assert response.status_code == 401
    assert "token" not in response.json()


async def test_unset_admin_hash_rejects_every_login_attempt(client_no_admin_password):
    response = await client_no_admin_password.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 401


async def test_unset_admin_hash_rejects_even_an_empty_password(client_no_admin_password):
    response = await client_no_admin_password.post("/api/login", json={"password": ""})
    assert response.status_code == 401


async def test_me_requires_a_token(client):
    response = await client.get("/api/me")
    assert response.status_code == 401


async def test_me_rejects_a_malformed_token(client):
    response = await client.get("/api/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


async def test_me_rejects_a_missing_bearer_scheme(client):
    login = await client.post("/api/login", json={"password": PASSWORD})
    token = login.json()["token"]
    response = await client.get("/api/me", headers={"Authorization": token})
    assert response.status_code == 401


async def test_me_rejects_an_expired_token(client, session):
    login = await client.post("/api/login", json={"password": PASSWORD})
    token = login.json()["token"]
    await session.execute(text("UPDATE sessions SET expires_at = now() - interval '1 hour'"))
    await session.commit()
    response = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_me_accepts_a_valid_token(client):
    login = await client.post("/api/login", json={"password": PASSWORD})
    token = login.json()["token"]
    response = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


async def test_logout_revokes_the_token(client):
    login = await client.post("/api/login", json={"password": PASSWORD})
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    logout = await client.post("/api/logout", headers=headers)
    assert logout.status_code == 200
    response = await client.get("/api/me", headers=headers)
    assert response.status_code == 401


async def test_logout_requires_a_token(client):
    response = await client.post("/api/logout")
    assert response.status_code == 401


async def test_healthz_is_reachable_without_a_token(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_metrics_is_reachable_without_a_token(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
