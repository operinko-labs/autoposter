"""The read-only API key, through the real app (roadmap row 51).

A second credential for callers with no browser session -- a Homepage
``customapi`` widget, a script -- on exactly the GET routes in
``api.auth.ALLOWLIST``. Everything here goes through ``create_app`` and
``ASGITransport`` rather than the dependency alone, because the thing worth
pinning is the wired surface: which routes a key opens (two), which it does
not (every other one), where the key is read from (the header, never the
query string), what an unset key does (refuses everything), and that the
key's value never comes back out of the process through ``/api/config`` or
``/api/logs``.

The refusal tests are green before the feature exists -- every ``/api``
route already refuses a request without a session -- and stay in the file as
the pins that make the feature safe to widen later.
"""

import logging
import re
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api import auth as auth_module
from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
# The one fixed fake this file uses. Distinctive enough that its absence
# from a served body proves something; never a real key.
FAKE_KEY = "test-api-key-0123456789abcdef"
KEYED = {"X-API-Key": FAKE_KEY}
REFUSED = {"detail": "not authenticated"}
EXPECTED_ALLOWLIST = frozenset({"/api/status", "/api/version"})
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _secrets(api_key: str = FAKE_KEY) -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
        api_key=api_key,
    )


@pytest_asyncio.fixture
async def app(session_factory):
    return create_app(load_config(EXAMPLE), session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def client_no_key(session_factory):
    app = create_app(load_config(EXAMPLE), session_factory, _secrets(api_key=""))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _api_routes(app):
    """Every ``/api`` method+path the app mounts, from the OpenAPI document --
    the enumeration tests/test_api_login.py's structural sweep uses, for the
    reason it gives there (walking ``app.routes`` finds nothing on FastAPI
    0.141)."""
    for path, operations in app.openapi().get("paths", {}).items():
        if not path.startswith("/api"):
            continue
        for method in sorted(m.upper() for m in operations):
            if method in METHODS:
                yield method, path


# --- the secret ---


def test_the_key_is_a_soft_secret_read_from_the_environment_only(monkeypatch):
    """The ``mdblist_apikey`` tier: absent means the key path is off (and
    closed), not that the process refuses to start."""
    for name in (
        "AUTOPOSTER_DATABASE_URL", "AUTOPOSTER_PLEX_TOKEN", "AUTOPOSTER_TMDB_TOKEN",
        "AUTOPOSTER_TVDB_APIKEY", "AUTOPOSTER_FANART_APIKEY", "AUTOPOSTER_WEBHOOK_SECRET",
    ):
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("AUTOPOSTER_API_KEY", raising=False)

    assert Secrets.from_env().api_key == ""

    monkeypatch.setenv("AUTOPOSTER_API_KEY", FAKE_KEY)
    assert Secrets.from_env().api_key == FAKE_KEY


# --- what a key opens ---


async def test_a_key_reads_the_allowlisted_routes_without_a_session(client):
    status = await client.get("/api/status", headers=KEYED)
    assert status.status_code == 200
    assert set(status.json()) == {
        "jobs_by_state", "workers", "processed_last_24h", "scheduled_jobs",
    }

    version = await client.get("/api/version", headers=KEYED)
    assert version.status_code == 200
    assert set(version.json()) == {"version", "latest", "update_available"}


async def test_a_key_opens_exactly_the_allowlist_and_nothing_else(app, client):
    """Structural, like tests/test_api_login.py's sweep: every mounted /api
    route is requested WITH the key, and must answer 200 if and only if it is
    a GET in ALLOWLIST. A route given Depends(api_key_or_session) by mistake,
    or an allowlist entry that names no mounted route, fails here."""
    assert auth_module.ALLOWLIST == EXPECTED_ALLOWLIST

    opened, refused = [], []
    for method, path in _api_routes(app):
        if path == "/api/login":
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        response = await client.request(method, url, headers=KEYED)
        if method == "GET" and path in EXPECTED_ALLOWLIST:
            assert response.status_code == 200, "%s %s refused the key: %d" % (
                method, path, response.status_code,
            )
            opened.append(path)
        else:
            assert response.status_code == 401, "%s %s answered %d to a key" % (
                method, path, response.status_code,
            )
            assert response.json() == REFUSED
            refused.append((method, path))
    assert set(opened) == set(EXPECTED_ALLOWLIST)
    # Both halves of the refusal matrix were actually exercised: keyed GETs
    # off the allowlist, and keyed writes.
    assert any(method == "GET" for method, _ in refused)
    assert any(method == "POST" for method, _ in refused)


async def test_a_key_is_refused_off_the_allowlist(client):
    """The two named cases the sweep also covers, spelled out: the served
    config (paths, hosts) and a write."""
    config = await client.get("/api/config", headers=KEYED)
    assert config.status_code == 401
    assert config.json() == REFUSED

    full_pass = await client.post("/api/full-pass", headers=KEYED)
    assert full_pass.status_code == 401
    assert full_pass.json() == REFUSED


async def test_the_query_string_is_never_consulted(client):
    """Posterizarr accepts ``?api_key=`` and ``?secret=``; this deliberately
    does not, so a key can never land in an access log or a browser history.
    Not rejected -- simply never read -- so a correct key there is refused
    exactly like no key."""
    for param in ("api_key", "secret"):
        response = await client.get("/api/status", params={param: FAKE_KEY})
        assert response.status_code == 401, param
        assert response.json() == REFUSED


async def test_an_unset_key_fails_closed(client_no_key):
    """Unset means off and CLOSED (the admin-hash posture), not open: a
    correct-looking header, and an empty one -- which a naive compare against
    an empty configured value would call equal -- are both refused."""
    keyed = await client_no_key.get("/api/status", headers=KEYED)
    assert keyed.status_code == 401
    assert keyed.json() == REFUSED

    empty = await client_no_key.get("/api/status", headers={"X-API-Key": ""})
    assert empty.status_code == 401
    assert empty.json() == REFUSED


async def test_a_wrong_key_is_refused_with_the_same_body_as_no_key(client):
    """One fixed sentence for every refusal -- wrong, empty, absent, off the
    allowlist -- and no 403 anywhere: nothing in the response distinguishes
    "wrong key" from "no key" from "not a route a key may read"."""
    wrong = await client.get("/api/status", headers={"X-API-Key": "nope"})
    empty = await client.get("/api/status", headers={"X-API-Key": ""})
    absent = await client.get("/api/status")
    off_list = await client.get("/api/config", headers=KEYED)

    for response in (wrong, empty, absent, off_list):
        assert response.status_code == 401
        assert response.json() == REFUSED


async def test_a_session_still_works_on_the_allowlisted_routes(client, session_headers):
    """The key is additive. A browser session reads the same two routes as
    before, and a session beside a wrong key is still a session."""
    assert (await client.get("/api/status", headers=session_headers)).status_code == 200
    assert (await client.get("/api/version", headers=session_headers)).status_code == 200
    both = await client.get(
        "/api/status", headers={**session_headers, "X-API-Key": "nope"}
    )
    assert both.status_code == 200


# --- the key never comes back out ---


async def test_the_key_is_redacted_from_the_served_config(client, session_headers):
    response = await client.get("/api/config", headers=session_headers)
    assert response.status_code == 200
    assert response.json()["secrets"]["api_key"] == "***REDACTED***"
    assert FAKE_KEY not in response.text


async def test_the_key_never_reaches_the_log_buffer(app, client, session_headers):
    """Nothing logs request headers today; this pins that nothing starts to.
    The buffer is attached to the root logger for the duration of the test
    only (the lifespan does it in production; ASGITransport runs no
    lifespan) and detached in ``finally`` so no later test's lines land in
    this app's buffer."""
    root = logging.getLogger()
    root.addHandler(app.state.log_buffer)
    try:
        assert (await client.get("/api/status", headers=KEYED)).status_code == 200
        assert (await client.get("/api/config", headers=KEYED)).status_code == 401
        assert (
            await client.get("/api/status", params={"api_key": FAKE_KEY})
        ).status_code == 401
        # A line of our own -- WARNING, so the root logger's default level
        # cannot drop it -- so the assertion below is about a buffer that
        # demonstrably captured something and not about an empty one.
        logging.getLogger("autoposter.test").warning("marker line")
    finally:
        root.removeHandler(app.state.log_buffer)

    response = await client.get("/api/logs", headers=session_headers)
    assert response.status_code == 200
    assert any(line["message"] == "marker line" for line in response.json()["lines"])
    assert FAKE_KEY not in response.text


# --- the two places an operator looks ---


def test_the_secret_is_documented_in_both_places_an_operator_looks():
    """``.env.example`` is the compose path and ``deploy/README.md`` is the
    Kubernetes one (the tests/test_builder_tracearr.py rule). A soft secret
    documented in neither is a feature nobody can turn on -- and this one's
    README entry is also where the header-not-URL rule is written down."""
    env_example = Path(".env.example").read_text(encoding="utf-8")
    readme = Path("deploy/README.md").read_text(encoding="utf-8")

    assert "AUTOPOSTER_API_KEY" in env_example
    assert "AUTOPOSTER_API_KEY" in readme
    assert "X-API-Key" in readme
    assert "customapi" in readme
    assert (
        readme.count('X-API-Key: "{{HOMEPAGE_VAR_AUTOPOSTER_API_KEY}}"') == 1
    )
