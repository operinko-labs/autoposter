"""The first-start setup application (roadmap row 121).

Two properties are proved structurally rather than route by route, because
route-by-route is a habit and a habit does not cover the next route somebody
adds:

* every /api path the real application serves answers 503 here, so a caller
  that reaches an unconfigured deployment gets one fixed sentence and never a
  half-built endpoint;
* every setup route past the master password refuses a request without the
  setup token, and the ONE open /api path on the real application is the
  probe -- checked against that application's own route table.
"""
import logging
import stat
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Aliased `setup_api`, not `setup_module`: a module-level name `setup_module`
# in a test file is pytest's xunit-style per-module setup hook, and pytest
# calls whatever carries that name -- a module object has no __code__, so
# every test in the file errors before it runs.
from autoposter.api import setup as setup_api
from autoposter.api.routes import _REDACTED
from autoposter.api.setup import build_setup_app
from autoposter.app import create_app
from autoposter.config import state as state_module
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

# Distinctive so the T6 grep gate can prove none of them entered src/ or the
# frontend bundle.
MASTER_PASSWORD = "row-121-master-passphrase-e41b"
FAKE_PLEX_TOKEN = "row-121-plex-token-9c2a"
FAKE_DB_URL = "postgresql+asyncpg://row121user:row-121-db-secret-4f1a@db.invalid:5432/ap"

HARD = (
    "AUTOPOSTER_DATABASE_URL",
    "AUTOPOSTER_PLEX_TOKEN",
    "AUTOPOSTER_TMDB_TOKEN",
    "AUTOPOSTER_TVDB_APIKEY",
    "AUTOPOSTER_FANART_APIKEY",
    "AUTOPOSTER_WEBHOOK_SECRET",
)


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    for name in (*HARD, "AUTOPOSTER_ADMIN_PASSWORD_HASH", "AUTOPOSTER_API_KEY",
                 "AUTOPOSTER_MDBLIST_APIKEY", "AUTOPOSTER_RADARR_APIKEY",
                 "AUTOPOSTER_SONARR_APIKEY", "AUTOPOSTER_HARBOR_TOKEN",
                 "AUTOPOSTER_PLEX_ACCOUNT_TOKEN", "AUTOPOSTER_TRACEARR_APIKEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(state_module.STATE_DIR_ENV, str(tmp_path / "state"))
    # spa_dist() would otherwise pick up whatever frontend/dist the developer's
    # checkout happens to hold; the SPA test below supplies its own.
    monkeypatch.setattr(setup_api, "spa_dist", lambda: None)


@pytest.fixture
def setup_app():
    return build_setup_app()


@pytest_asyncio.fixture
async def setup_client(setup_app):
    transport = ASGITransport(app=setup_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
    )


@pytest.fixture
def normal_app(session_factory):
    return create_app(load_config(EXAMPLE), session_factory, _secrets())


@pytest_asyncio.fixture
async def normal_client(normal_app):
    transport = ASGITransport(app=normal_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _authenticate(client) -> str:
    response = await client.post("/api/setup/password", json={"password": MASTER_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _headers(token: str) -> dict:
    return {setup_api.SETUP_TOKEN_HEADER: token}


# --- the probe --------------------------------------------------------------


async def test_the_setup_app_reports_that_setup_is_required(setup_client):
    response = await setup_client.get("/api/setup/state")

    assert response.status_code == 200
    assert response.json() == {"setup": True, "password_set": False}


async def test_the_normal_app_reports_that_it_is_not(normal_client):
    """C6: served by BOTH applications, so the SPA's one probe on load has an
    answer either way and never has to treat a 404 as data."""
    response = await normal_client.get("/api/setup/state")

    assert response.status_code == 200
    assert response.json() == {"setup": False}


def test_the_probe_is_the_only_open_api_path_on_the_normal_app(normal_app):
    """The compensating control for keeping it out of the OpenAPI schema.

    tests/test_api_login.py's structural sweep enumerates the DOCUMENTED /api
    surface and requires every path on it to 401 without a session. That sweep
    is what stops a route shipping open, so this route is registered outside
    the schema rather than exempted from it -- and this test is what stops a
    SECOND route being hidden the same way.
    """
    documented = set(normal_app.openapi().get("paths", {}))
    hidden = {
        route.path
        for route in normal_app.routes
        if getattr(route, "path", "").startswith("/api")
        and getattr(route, "path", "") not in documented
    }

    assert hidden == {"/api/setup/state"}


async def test_password_set_is_reported_only_by_the_setup_app(setup_client):
    await _authenticate(setup_client)

    assert (await setup_client.get("/api/setup/state")).json()["password_set"] is True


# --- the 503 sweep ----------------------------------------------------------


async def test_every_other_api_path_answers_503_with_one_fixed_sentence(
    normal_app, setup_client
):
    """Structural: enumerated from what the REAL application serves, so a
    router added later is covered without anybody remembering to add it."""
    checked = []
    for path, operations in normal_app.openapi().get("paths", {}).items():
        if not path.startswith("/api") or path.startswith("/api/setup"):
            continue
        for method in sorted(m.upper() for m in operations):
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            url = path.replace("{", "").replace("}", "")
            response = await setup_client.request(method, url)
            assert response.status_code == 503, "%s %s answered %d" % (
                method, path, response.status_code,
            )
            assert response.json()["detail"] == setup_api.NOT_CONFIGURED
            checked.append((method, path))

    # Sanity: the loop must have found the real router, not an empty app.
    assert len(checked) >= 16


async def test_the_setup_routes_are_not_swallowed_by_the_503(setup_client):
    assert (await setup_client.get("/api/setup/state")).status_code == 200


# --- the SPA and the 422 handler -------------------------------------------


async def test_the_setup_app_serves_the_spa_shell_for_a_client_route(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!-- setup shell -->", encoding="utf-8")
    monkeypatch.setattr(setup_api, "spa_dist", lambda: dist)

    transport = ASGITransport(app=build_setup_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/setup")

    assert response.status_code == 200
    assert "setup shell" in response.text


async def test_a_422_never_echoes_the_body_that_was_rejected(setup_client):
    """The #178 handler, which the setup application must install too -- here
    it matters more, not less: every body this application takes is a
    credential, and pydantic's `missing` arm puts the WHOLE body in `input`."""
    response = await setup_client.post(
        "/api/setup/password", json={"not_the_field": MASTER_PASSWORD}
    )

    assert response.status_code == 422
    assert MASTER_PASSWORD not in response.text
    assert all(set(entry) == {"type", "loc", "msg"} for entry in response.json()["detail"])


# --- step 1: the master password -------------------------------------------


async def test_the_first_password_is_persisted_as_a_bcrypt_hash(setup_client):
    await _authenticate(setup_client)

    held = state_module.read_secrets_file(state_module.secrets_file_path())
    stored = held["AUTOPOSTER_ADMIN_PASSWORD_HASH"]
    assert stored.startswith("$2b$")
    assert MASTER_PASSWORD not in stored


async def test_the_persisted_file_is_0600_inside_a_0700_directory(setup_client):
    await _authenticate(setup_client)
    path = state_module.secrets_file_path()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


async def test_the_password_is_never_echoed_or_logged(setup_client, caplog):
    with caplog.at_level(logging.DEBUG):
        response = await setup_client.post(
            "/api/setup/password", json={"password": MASTER_PASSWORD}
        )

    assert MASTER_PASSWORD not in response.text
    assert MASTER_PASSWORD not in "\n".join(r.getMessage() for r in caplog.records)


async def test_a_second_call_verifies_against_the_hash_it_already_persisted(setup_client):
    """A reloaded page, a second browser, a restarted wizard: the password is
    set once and proved thereafter."""
    first = await _authenticate(setup_client)
    second = await _authenticate(setup_client)

    assert second != first
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert held["AUTOPOSTER_ADMIN_PASSWORD_HASH"].startswith("$2b$")


async def test_a_wrong_password_is_refused_with_the_login_sentence(setup_client):
    await _authenticate(setup_client)

    response = await setup_client.post("/api/setup/password", json={"password": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == setup_api.INVALID_CREDENTIALS


async def test_the_password_route_is_rate_limited_before_bcrypt_runs(setup_app):
    """LoginRateLimiter exists to bound bcrypt CPU on a route that needs no
    credential to reach -- api/routes.py's login handler makes the same call in
    the same order, and this route is the only other one like it."""
    setup_app.state.setup_rate_limiter.max_attempts = 2
    transport = ASGITransport(app=setup_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(2):
            await client.post("/api/setup/password", json={"password": MASTER_PASSWORD})
        refused = await client.post("/api/setup/password", json={"password": MASTER_PASSWORD})

    assert refused.status_code == 429
    assert refused.json()["detail"] == setup_api.TOO_MANY_ATTEMPTS


async def test_a_too_short_password_is_refused_without_echoing_it(setup_client):
    response = await setup_client.post("/api/setup/password", json={"password": "short"})

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.PASSWORD_TOO_SHORT
    assert "short" not in response.json()["detail"]
    assert state_module.read_secrets_file(state_module.secrets_file_path()) == {}


# --- the setup token --------------------------------------------------------


async def test_every_route_past_the_password_requires_the_setup_token(setup_client):
    """The security proof of this row: the wizard's own equivalent of
    tests/test_api_login.py's structural sweep, which the separate application
    is what makes unnecessary over there."""
    token = await _authenticate(setup_client)
    checked = []
    for route in setup_api.router.routes:
        path = route.path
        if path == "/api/setup/state" or path == "/api/setup/password":
            continue
        for method in sorted(route.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}):
            response = await setup_client.request(method, path)
            assert response.status_code == 401, "%s %s answered %d without a token" % (
                method, path, response.status_code,
            )
            assert response.json()["detail"] == setup_api.NOT_AUTHENTICATED
            checked.append((method, path))

    assert len(checked) >= 1
    assert token  # the token itself is exercised by the tests that use it


async def test_a_wrong_setup_token_is_refused_with_the_same_sentence(setup_client):
    await _authenticate(setup_client)

    response = await setup_client.get("/api/setup/progress", headers=_headers("nope"))

    assert response.status_code == 401
    assert response.json()["detail"] == setup_api.NOT_AUTHENTICATED


def test_the_redaction_string_is_the_one_the_config_endpoint_serves():
    """Repeated in api/setup.py rather than imported, because importing
    api/routes.py would pull the whole application router into the setup
    process. This is the pin that keeps the two equal."""
    assert setup_api.REDACTED == _REDACTED
