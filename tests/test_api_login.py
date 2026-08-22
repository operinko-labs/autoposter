"""The login endpoint and the /api router."""
import re
import threading
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from autoposter.api import routes as routes_module
from autoposter.api.auth import LoginRateLimiter, hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Session as SessionModel

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
async def app(session_factory):
    return create_app(load_config(EXAMPLE), session_factory, _secrets(hash_password(PASSWORD)))


@pytest_asyncio.fixture
async def client(app):
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


async def test_the_password_check_never_runs_on_the_event_loop(client, monkeypatch):
    """bcrypt at cost 12 is 250-300 ms of pure CPU, and this endpoint needs no
    credentials to reach. Running it inline would freeze the loop that also
    carries the worker pool, the scheduler and the Plex probe, at a few
    requests a second."""
    threads = []
    real = routes_module.verify_password

    def recording(plain, hashed):
        threads.append(threading.current_thread())
        return real(plain, hashed)

    monkeypatch.setattr(routes_module, "verify_password", recording)

    response = await client.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 200
    assert threads, "verify_password was never called"
    assert all(thread is not threading.main_thread() for thread in threads)


async def test_login_attempts_are_rate_limited_per_client(app, client):
    """Defence in depth per pod: without it, anyone who can reach the port can
    spend the whole process's CPU on bcrypt."""
    app.state.login_rate_limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)

    first = await client.post("/api/login", json={"password": "nope"})
    second = await client.post("/api/login", json={"password": "nope"})
    third = await client.post("/api/login", json={"password": PASSWORD})

    assert [first.status_code, second.status_code] == [401, 401]
    assert third.status_code == 429
    assert "token" not in third.json()


async def test_the_missing_admin_hash_warning_is_logged_once_not_per_attempt(
    session_factory, caplog
):
    """An unauthenticated caller must not be able to flood the log by
    repeatedly posting to /api/login."""
    with caplog.at_level("WARNING"):
        app = create_app(load_config(EXAMPLE), session_factory, _secrets(""))
        at_startup = [
            r for r in caplog.records if "AUTOPOSTER_ADMIN_PASSWORD_HASH" in r.getMessage()
        ]
        assert len(at_startup) == 1

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            for _ in range(3):
                assert (await c.post("/api/login", json={"password": "x"})).status_code == 401

    after = [r for r in caplog.records if "AUTOPOSTER_ADMIN_PASSWORD_HASH" in r.getMessage()]
    assert len(after) == 1


async def test_login_prunes_expired_session_rows(client, session):
    """Nothing else deletes them, so the table would grow with every login for
    the life of the deployment."""
    await client.post("/api/login", json={"password": PASSWORD})
    await session.execute(text("UPDATE sessions SET expires_at = now() - interval '1 hour'"))
    await session.commit()

    await client.post("/api/login", json={"password": PASSWORD})

    rows = (await session.execute(select(SessionModel))).scalars().all()
    assert len(rows) == 1


# Enumerated from the OpenAPI schema rather than by walking `app.routes`
# and matching `isinstance(route, APIRoute)`. That walk silently found zero
# routes on FastAPI 0.141, which stopped flattening included routers onto
# `app.routes` -- it appends one opaque `_IncludedRouter` instead, exposing
# neither `.routes` nor the APIRoute objects nested inside it. Zero routes
# makes this test vacuous, which is precisely the failure it exists to
# prevent, so it is enumerated from the documented surface instead. That is
# stable across both versions and unaffected by `docs_url=None`.
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _api_routes(app):
    for path, operations in app.openapi().get("paths", {}).items():
        if not path.startswith("/api"):
            continue
        for method in sorted(m.upper() for m in operations):
            if method in METHODS:
                yield method, path


async def test_every_api_route_except_login_requires_a_session(app, client):
    """Structural, not per-route: every route does carry
    Depends(require_session) today, but that is a habit and so is each
    hand-written 401 test. A route added later without it would ship open with
    a green suite -- this enumerates whatever the router actually mounts."""
    checked = []
    for method, path in _api_routes(app):
        if path == "/api/login":
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        response = await client.request(method, url)
        assert response.status_code == 401, "%s %s answered %d without a token" % (
            method, path, response.status_code,
        )
        checked.append((method, path))
    # Sanity: the loop above must actually have found the router, not an
    # empty app.
    assert len(checked) >= 16


async def test_healthz_is_reachable_without_a_token(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_metrics_is_reachable_without_a_token(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
