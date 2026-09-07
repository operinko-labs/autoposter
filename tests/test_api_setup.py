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
import os
import stat
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Aliased `setup_api`, not `setup_module`: a module-level name `setup_module`
# in a test file is pytest's xunit-style per-module setup hook, and pytest
# calls whatever carries that name -- a module object has no __code__, so
# every test in the file errors before it runs.
from autoposter import boot
from autoposter.api import setup as setup_api
from autoposter.api.routes import _REDACTED
from autoposter.api.setup import build_setup_app
from autoposter.app import create_app
from autoposter.config import state as state_module
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets, resolve_secret_values

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

# Distinctive so the T6 grep gate can prove none of them entered src/ or the
# frontend bundle.
MASTER_PASSWORD = "row-121-master-passphrase-e41b"
# A DSN whose password half is a string of its own, so the no-echo tests can
# look for the part that must never survive a refusal.
FAKE_DB_URL = "postgresql+asyncpg://autoposter:row-121-db-secret@db.invalid:5432/autoposter"
FAKE_PLEX_TOKEN = "row-121-plex-token-7f31"
PLEX_URL = "http://plex.example.test:32400"
# \x85 is a line separator to str.splitlines -- which read_secrets_file parses
# with -- and to nothing an eye would notice. The tail is a second NAME=value
# entry, which is the whole of the injection.
SPLIT_VALUE = "row-121-tmdb\x85AUTOPOSTER_PLEX_TOKEN=row-121-injected"
# A NUL survives the state file's round trip untouched and then makes
# `os.environ[name] = value` raise in boot._export -- at a boot where every
# hard secret resolves, so no wizard is served and the pod exits non-zero
# forever. The state directory would need shell access to repair.
NUL_VALUE = "row-121-nul\x00tail"
# The same unrecoverable place by the other door: past MAXIMUM_SECRET_LENGTH
# the exec that hands the deployment to the application is E2BIG.
OVERSIZED_VALUE = "row-121-long" + "x" * state_module.MAXIMUM_SECRET_LENGTH

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
    # The wizard now asks the loader's own resolver, which prefers a PRESENT
    # AUTOPOSTER_CONFIG -- so a developer's exported one (or the image's baked
    # one) would otherwise decide whether step 4 is offered. The three shipped
    # shapes are set explicitly by the test that pins them.
    monkeypatch.delenv("AUTOPOSTER_CONFIG", raising=False)
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


@pytest.fixture
def setup_state(setup_app):
    """The in-memory ``SetupState`` behind ``setup_client``, for the handful of
    tests that need to look at what was staged rather than only what a route
    answered. Not a route: staged values -- an address, a document -- are
    never served back."""
    return setup_app.state.setup


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

    # Sanity: the loop must have found the real router, not an empty app --
    # pinned NEAR the measured surface (73 documented /api operations outside
    # /api/setup at c1c19a9) rather than at a fifth of it, so that a change
    # which drops most of the documented paths out of create_app().openapi()
    # fails here instead of passing a floor it never approaches.
    assert len(checked) >= 70


# The one path the setup application answers outside /api/setup/* and the SPA's
# own files. An exact set, not a containment: a second exemption has to be
# argued for here, which is the whole value of the assertion.
NOT_SWEPT = {"/healthz"}


def test_the_probe_is_the_only_path_outside_the_wizard_and_the_spa(setup_app):
    """I-4's pin, in the shape test_the_probe_is_the_only_open_api_path_on_the
    _normal_app uses: the setup application is a credential form on an
    unauthenticated port, so what it serves BESIDES the wizard is enumerated
    rather than assumed. The `spa_dist` autouse stub means this app has no SPA
    routes at all, so everything left is either /api or the exemption.
    """
    outside = {
        route.path
        for route in setup_app.routes
        if not getattr(route, "path", "/api").startswith("/api")
    }

    assert outside == NOT_SWEPT


async def test_the_setup_app_answers_the_kubernetes_probe(setup_client):
    """The chart points liveness AND readiness at httpGet /healthz on 8080.
    Without this route readiness never passes, so the Service has no endpoint
    and the wizard is unreachable, and liveness restarts the pod at ~t+80 s
    into the same state."""
    response = await setup_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "setup"}


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


async def test_a_password_over_72_bytes_is_refused_before_bcrypt_sees_it(setup_client):
    """bcrypt >= 4.0 RAISES past 72 bytes rather than truncating, so without
    this check a 12-word diceware passphrase in the first field of the
    first-start wizard is a bare 500: no hash, no token, no sentence."""
    too_long = "x" * (setup_api.MAXIMUM_PASSWORD_BYTES + 1)

    response = await setup_client.post("/api/setup/password", json={"password": too_long})

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.PASSWORD_TOO_LONG
    assert too_long not in response.text
    assert state_module.read_secrets_file(state_module.secrets_file_path()) == {}


async def test_a_password_of_exactly_72_bytes_is_accepted(setup_client):
    """The boundary is a library's and not this row's, so both sides are
    pinned: one byte fewer must still reach bcrypt and persist."""
    response = await setup_client.post(
        "/api/setup/password", json={"password": "x" * setup_api.MAXIMUM_PASSWORD_BYTES}
    )

    assert response.status_code == 200, response.text
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert held["AUTOPOSTER_ADMIN_PASSWORD_HASH"].startswith("$2b$")


async def test_a_password_carrying_a_nul_byte_cannot_poison_the_state_file(
    setup_client,
):
    """Step 1 is the third place a value arrives, and the one that needs no
    check: what it persists is bcrypt's OUTPUT, not the operator's paste.
    bcrypt >= 4.2 hashes a NUL-bearing password rather than raising -- measured,
    not assumed -- and the hash is ASCII, so the state file cannot be given a
    value the environment could not carry by this route. Pinned here so a later
    change that persists anything derived from the raw password has to argue
    with a test rather than with a comment.
    """
    response = await setup_client.post(
        "/api/setup/password", json={"password": MASTER_PASSWORD + "\x00tail"}
    )

    assert response.status_code == 200, response.text
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    stored = held["AUTOPOSTER_ADMIN_PASSWORD_HASH"]
    assert stored.startswith("$2b$")
    assert state_module.is_storable(stored)
    assert MASTER_PASSWORD not in response.text


async def test_a_multibyte_password_is_measured_in_bytes_not_characters(setup_client):
    """40 characters passes a `len()` on the str -- and is 80 bytes encoded,
    which is what bcrypt counts and refuses."""
    response = await setup_client.post("/api/setup/password", json={"password": "é" * 40})

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.PASSWORD_TOO_LONG


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
    assert token


async def test_the_minted_token_is_accepted_by_the_route_it_guards(setup_client):
    """The positive half, without which every other token test passes against a
    `require_setup_token` that raises 401 unconditionally -- a wrong `alias` on
    the Header, a compare over the wrong pair, or the non-ASCII TypeError
    below. Task 3 builds every step on this dependency."""
    token = await _authenticate(setup_client)

    response = await setup_client.get("/api/setup/progress", headers=_headers(token))

    assert response.status_code == 200, response.text


async def test_a_wrong_setup_token_is_refused_with_the_same_sentence(setup_client):
    await _authenticate(setup_client)

    response = await setup_client.get("/api/setup/progress", headers=_headers("nope"))

    assert response.status_code == 401
    assert response.json()["detail"] == setup_api.NOT_AUTHENTICATED


async def test_a_missing_setup_token_is_refused_on_the_same_route(setup_client):
    """Absent, wrong and malformed take one path and one sentence -- all three
    through the same route, so "indistinguishably" is measured and not
    asserted in a docstring."""
    await _authenticate(setup_client)

    response = await setup_client.get("/api/setup/progress")

    assert response.status_code == 401
    assert response.json()["detail"] == setup_api.NOT_AUTHENTICATED


async def test_a_non_ascii_setup_token_is_a_401_and_never_a_500(setup_client):
    """Starlette decodes header values as latin-1, so this single byte arrives
    as a non-ASCII str -- and `compare_digest` on str raises TypeError there,
    which without the bytes compare is an unhandled 500 with a traceback in the
    pod log, from an unauthenticated caller, on every route the token guards.
    Sent as bytes because httpx will not encode a non-ASCII str header."""
    await _authenticate(setup_client)

    response = await setup_client.get(
        "/api/setup/progress", headers={setup_api.SETUP_TOKEN_HEADER: b"\xff"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == setup_api.NOT_AUTHENTICATED


# --- what /progress reports, and what it must never carry --------------------


def test_the_admin_hash_and_the_api_key_are_not_provider_credentials():
    """The admin hash is step 1's output and the API key is row 51's operator
    choice; neither is a third party's credential. If either stayed in
    _PROVIDER_ENV, Task 3's provider form -- specified to iterate this tuple --
    would accept a caller-chosen AUTOPOSTER_ADMIN_PASSWORD_HASH and let a
    token-holder re-key or permanently lock out the deployment's admin."""
    assert "AUTOPOSTER_ADMIN_PASSWORD_HASH" not in setup_api._PROVIDER_ENV
    assert "AUTOPOSTER_API_KEY" not in setup_api._PROVIDER_ENV
    assert "AUTOPOSTER_DATABASE_URL" not in setup_api._PROVIDER_ENV
    # ...and it is still the provider set, not an empty tuple.
    assert "AUTOPOSTER_PLEX_TOKEN" in setup_api._PROVIDER_ENV
    assert "AUTOPOSTER_MDBLIST_APIKEY" in setup_api._PROVIDER_ENV


async def test_progress_reports_presence_and_never_a_value(setup_client):
    """C8/C9 for this application's only reporting surface: booleans, NAMES,
    and ***REDACTED***/null -- nothing else, and no value of any kind."""
    token = await _authenticate(setup_client)
    stored = state_module.read_secrets_file(state_module.secrets_file_path())[
        "AUTOPOSTER_ADMIN_PASSWORD_HASH"
    ]

    response = await setup_client.get("/api/setup/progress", headers=_headers(token))
    body = response.json()

    assert response.status_code == 200, response.text
    assert set(body) == {
        "password", "database", "providers", "required", "config", "config_source",
        "public_url",
    }
    assert body["password"] is True
    assert body["database"] is False
    assert body["config"] is False
    # A word or null, and never a path: this response is a presence surface.
    assert body["config_source"] is None
    # NAMES, in _SECRET_ENV order -- the database URL is what is still missing.
    assert body["required"] == list(HARD)
    assert set(body["providers"]) == set(setup_api._PROVIDER_ENV)
    assert set(body["providers"].values()) == {None}
    # The master password is reported ONCE, as the boolean above.
    assert "AUTOPOSTER_ADMIN_PASSWORD_HASH" not in body["providers"]
    assert stored not in response.text
    assert MASTER_PASSWORD not in response.text


async def test_progress_redacts_a_provider_it_holds(monkeypatch, setup_client):
    """The other arm of the presence map: a set name is ***REDACTED***, which
    is a presence claim and not the credential."""
    monkeypatch.setenv("AUTOPOSTER_PLEX_TOKEN", "row-121-plex-token-9c2a")
    token = await _authenticate(setup_client)

    response = await setup_client.get("/api/setup/progress", headers=_headers(token))

    assert response.json()["providers"]["AUTOPOSTER_PLEX_TOKEN"] == setup_api.REDACTED
    assert "row-121-plex-token-9c2a" not in response.text


def test_the_redaction_string_is_the_one_the_config_endpoint_serves():
    """Repeated in api/setup.py rather than imported, because importing
    api/routes.py would pull the whole application router into the setup
    process. This is the pin that keeps the two equal."""
    assert setup_api.REDACTED == _REDACTED


# --- step 2: the database URL ----------------------------------------------


async def test_a_database_url_that_answers_is_staged_and_not_yet_persisted(
    setup_client, monkeypatch
):
    """Amendment 3: the hard secrets are STAGED in the token's memory and land
    on disk only at the finish step, so an abandoned wizard leaves a state
    directory the next boot reads as "not configured yet" rather than as a
    deployment whose credentials are half there."""
    token = await _authenticate(setup_client)
    monkeypatch.setattr(setup_api, "database_answers", _answering(True))

    response = await setup_client.post(
        "/api/setup/database", json={"url": FAKE_DB_URL}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert "AUTOPOSTER_DATABASE_URL" not in held
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["database"] is True
    assert FAKE_DB_URL not in progress.text


async def test_a_database_that_refuses_is_reported_by_class_name_only(
    setup_client, monkeypatch
):
    token = await _authenticate(setup_client)
    monkeypatch.setattr(setup_api, "database_answers", _answering(False, "OperationalError"))

    response = await setup_client.post(
        "/api/setup/database", json={"url": FAKE_DB_URL}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "the database did not answer (OperationalError)"


async def test_a_refused_database_url_is_not_staged(setup_client, monkeypatch):
    token = await _authenticate(setup_client)
    monkeypatch.setattr(setup_api, "database_answers", _answering(False, "OperationalError"))

    await setup_client.post(
        "/api/setup/database", json={"url": FAKE_DB_URL}, headers=_headers(token)
    )

    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["database"] is False


async def test_the_database_url_never_reaches_a_response_or_the_log(
    setup_client, monkeypatch, caplog
):
    """A connection error carries the DSN in its own text -- host, user and
    password. This is the densest credential string this service ever holds,
    and the wizard is the one place a human types it in."""
    token = await _authenticate(setup_client)
    monkeypatch.setattr(setup_api, "database_answers", _answering(False, "OperationalError"))

    with caplog.at_level(logging.DEBUG):
        response = await setup_client.post(
            "/api/setup/database", json={"url": FAKE_DB_URL}, headers=_headers(token)
        )

    assert "row-121-db-secret" not in response.text
    assert "row-121-db-secret" not in "\n".join(r.getMessage() for r in caplog.records)


async def test_a_real_database_url_is_accepted_through_the_real_probe(setup_client):
    """Not mocked: the step's contract is that the URL is validated by being
    USED, and a probe that is only ever faked proves the mock."""
    from conftest import TEST_DB_URL

    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/database", json={"url": TEST_DB_URL}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text


# --- step 3: the provider keys ---------------------------------------------


async def test_provider_keys_are_staged_and_served_only_as_a_presence_map(setup_client):
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_PLEX_TOKEN": FAKE_PLEX_TOKEN}},
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["providers"]["AUTOPOSTER_PLEX_TOKEN"] == setup_api.REDACTED
    assert body["providers"]["AUTOPOSTER_TMDB_TOKEN"] is None
    assert FAKE_PLEX_TOKEN not in response.text
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert "AUTOPOSTER_PLEX_TOKEN" not in held


async def test_an_omitted_provider_key_leaves_the_staged_one_alone(setup_client):
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_PLEX_TOKEN": FAKE_PLEX_TOKEN}},
        headers=_headers(token),
    )

    response = await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_TMDB_TOKEN": "row-121-tmdb-token-4b7e"}},
        headers=_headers(token),
    )

    providers = response.json()["providers"]
    assert providers["AUTOPOSTER_PLEX_TOKEN"] == setup_api.REDACTED
    assert providers["AUTOPOSTER_TMDB_TOKEN"] == setup_api.REDACTED
    assert FAKE_PLEX_TOKEN not in response.text
    assert "row-121-tmdb-token-4b7e" not in response.text


async def test_a_name_this_service_does_not_read_is_refused_without_echoing_it(
    setup_client,
):
    """The submitted KEYS are caller-chosen strings, so the refusal names none
    of them (facts Amendment 5). An operator who pastes a token into a name
    field would otherwise get it back in a response body that reaches a
    reverse-proxy log, a HAR export and the frontend's retained error text."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/providers",
        json={"values": {"row-121-a-token-in-the-name-field": "row-121-unknown-value"}},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NOT_A_CREDENTIAL_THIS_SERVICE_READS
    assert "row-121-a-token-in-the-name-field" not in response.text
    assert "row-121-unknown-value" not in response.text


async def test_a_provider_value_the_reader_would_split_is_refused_at_the_step(
    setup_client,
):
    """render_secrets_file refuses this value as well -- but that raise lands
    inside the FINISH step, after the config document has been written, as a
    500 that names no field with the wizard about to disappear. The check
    belongs at the step that accepts the value; the refusal names the NAME,
    which has already passed the allowlist, and never the value."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_TMDB_TOKEN": SPLIT_VALUE}},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        f"{setup_api.VALUE_IS_NOT_STORABLE} AUTOPOSTER_TMDB_TOKEN"
    )
    assert "row-121-injected" not in response.text
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["providers"]["AUTOPOSTER_TMDB_TOKEN"] is None


async def test_a_split_value_never_reaches_the_finish_step(
    setup_app, setup_client, monkeypatch
):
    """The other half: a wizard that was refused the value at step 3 finishes
    normally, and what lands is one entry per name -- the injected second
    entry is not in the file, and no credential was replaced by it."""
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_TMDB_TOKEN": SPLIT_VALUE}},
        headers=_headers(token),
    )
    await _complete_every_step(setup_client, token, monkeypatch)
    monkeypatch.setattr(setup_api.os, "execv", lambda path, argv: None)

    response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    assert response.status_code == 200, response.text
    assert all(
        state_module.is_storable(value)
        for value in setup_app.state.setup.staged.values()
    )
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert held["AUTOPOSTER_TMDB_TOKEN"] == "value"
    assert held["AUTOPOSTER_PLEX_TOKEN"] == FAKE_PLEX_TOKEN


def test_the_wizard_refuses_exactly_what_the_writer_refuses():
    """One rule, one function. A value the step accepts and the renderer
    refuses is a 500 at the last step of a wizard that is gone afterwards, so
    the two checks cannot be two expressions that happen to agree today."""
    for value in ("", "plain", "two\nlines", "u\x85nicode", "\u2028", "trailing\r",
                  NUL_VALUE, OVERSIZED_VALUE,
                  "x" * state_module.MAXIMUM_SECRET_LENGTH):
        try:
            state_module.render_secrets_file({"AUTOPOSTER_TMDB_TOKEN": value})
        except ValueError:
            assert not state_module.is_storable(value), value
        else:
            assert state_module.is_storable(value), value


def test_a_value_the_environment_cannot_carry_is_not_storable():
    """The rule is the environment's and not this module's taste, so it is
    proved against the environment: a value `os.environ` refuses is a value
    that reaches `boot._export` at a boot where every hard secret resolves --
    no wizard is served for that shape, and the pod exits non-zero until
    somebody edits the state volume from a shell. Length is the same failure
    one step later at `os.execv` (E2BIG), bounded rather than probed because
    probing it would depend on the runner's RLIMIT_STACK."""
    with pytest.raises(ValueError):
        os.environ["ROW_121_PROBE"] = NUL_VALUE

    assert not state_module.is_storable(NUL_VALUE)
    assert not state_module.is_storable(OVERSIZED_VALUE)
    assert not state_module.is_storable(SPLIT_VALUE)
    # The bound from both sides, and the empty string, which is the one value
    # that is not a line at all.
    assert state_module.is_storable("x" * state_module.MAXIMUM_SECRET_LENGTH)
    assert not state_module.is_storable("x" * (state_module.MAXIMUM_SECRET_LENGTH + 1))
    assert state_module.is_storable("")
    assert state_module.is_storable(FAKE_DB_URL)


async def test_a_provider_value_the_environment_cannot_carry_is_refused_at_the_step(
    setup_client,
):
    """The NUL byte and the oversized value take the split value's path, at
    the same step and for the same reason: the finish step is too late, and
    the boot that follows it has no wizard to fall back to."""
    token = await _authenticate(setup_client)

    for value in (NUL_VALUE, OVERSIZED_VALUE):
        response = await setup_client.post(
            "/api/setup/providers",
            json={"values": {"AUTOPOSTER_TMDB_TOKEN": value}},
            headers=_headers(token),
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == (
            f"{setup_api.VALUE_IS_NOT_STORABLE} AUTOPOSTER_TMDB_TOKEN"
        )
        assert "row-121-nul" not in response.text
        assert "row-121-long" not in response.text
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["providers"]["AUTOPOSTER_TMDB_TOKEN"] is None


async def test_a_database_url_the_environment_cannot_carry_is_refused_before_the_probe(
    setup_client, monkeypatch
):
    """Step 2's half of the same rule, and the probe must not run for it: the
    value can never be stored, so an outbound connection made on its behalf is
    one the deployment gains nothing from."""
    probed: list[str] = []

    async def answers(url: str) -> tuple[bool, str]:
        probed.append(url)
        return True, ""

    monkeypatch.setattr(setup_api, "database_answers", answers)
    token = await _authenticate(setup_client)

    for value in (FAKE_DB_URL + "\x00", FAKE_DB_URL + "x" * 4096):
        response = await setup_client.post(
            "/api/setup/database", json={"url": value}, headers=_headers(token)
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == (
            f"{setup_api.VALUE_IS_NOT_STORABLE} AUTOPOSTER_DATABASE_URL"
        )
        assert "row-121-db-secret" not in response.text
    assert probed == []
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["database"] is False


async def test_the_presence_map_is_readable_on_its_own(setup_client):
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_PLEX_TOKEN": FAKE_PLEX_TOKEN}},
        headers=_headers(token),
    )

    response = await setup_client.get("/api/setup/providers", headers=_headers(token))

    assert response.json()["providers"]["AUTOPOSTER_PLEX_TOKEN"] == setup_api.REDACTED
    # The generated value is served by the call that generated it and never
    # again -- a GET is not that call.
    assert response.json()["webhook_secret"] is None
    assert FAKE_PLEX_TOKEN not in response.text


async def test_the_webhook_secret_is_generated_and_served_exactly_once(
    setup_client, caplog
):
    """The one value this application ever puts in a response body, and the
    reason it may: it is not a credential the wizard was GIVEN. Sonarr and
    Radarr sign their webhooks with a secret this deployment chooses, so
    somebody has to choose it -- and an operator typing one is a weaker secret
    plus a credential on the wire that came from a form. It is generated here,
    shown on the response that generated it, and never served again.
    """
    token = await _authenticate(setup_client)

    with caplog.at_level(logging.DEBUG):
        first = await setup_client.post(
            "/api/setup/providers", json={"values": {}}, headers=_headers(token)
        )
    generated = first.json()["webhook_secret"]
    second = await setup_client.post(
        "/api/setup/providers", json={"values": {}}, headers=_headers(token)
    )
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))

    assert generated and len(generated) >= 32
    assert first.json()["providers"]["AUTOPOSTER_WEBHOOK_SECRET"] == setup_api.REDACTED
    assert second.json()["webhook_secret"] is None
    assert generated not in second.text
    assert generated not in progress.text
    assert generated not in "\n".join(r.getMessage() for r in caplog.records)


async def test_a_submitted_webhook_secret_is_refused_by_name(setup_client):
    """Refused rather than accepted, so the value this application serves is
    provably one it minted and never one a caller sent it."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_WEBHOOK_SECRET": "row-121-not-yours"}},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.WEBHOOK_SECRET_IS_GENERATED
    assert "row-121-not-yours" not in response.text


async def test_a_database_url_the_reader_would_split_is_refused_before_the_probe(
    setup_client, monkeypatch
):
    """Narrower than the provider step -- such a URL must first answer SELECT 1
    -- but the same failure at the same wrong step, and no reason to open an
    outbound connection for a value that can never be stored."""
    probed: list[str] = []

    async def answers(url: str) -> tuple[bool, str]:
        probed.append(url)
        return True, ""

    monkeypatch.setattr(setup_api, "database_answers", answers)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/database",
        json={"url": FAKE_DB_URL + "\x85AUTOPOSTER_PLEX_TOKEN=row-121-injected"},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        f"{setup_api.VALUE_IS_NOT_STORABLE} AUTOPOSTER_DATABASE_URL"
    )
    assert probed == []
    assert "row-121-db-secret" not in response.text
    assert "row-121-injected" not in response.text


# --- step 4: the config document -------------------------------------------


async def test_the_config_step_stages_the_document_and_writes_nothing_yet(setup_client):
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config", json={"plex_url": PLEX_URL}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["path"] == str(state_module.state_config_path())
    # Amendment 3: written at finish, in front of the secrets file.
    assert not state_module.state_config_path().exists()
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["config"] is True


async def test_the_config_step_is_refused_while_a_document_already_resolves(
    setup_app, setup_client, monkeypatch, tmp_path
):
    """Amendment 6: offering step 4 only when config_source is null is a
    server rule, not a client courtesy the SPA happens to observe -- a direct
    POST past it must be refused too, and must stage nothing."""
    mounted = tmp_path / "config" / "autoposter.yaml"
    mounted.parent.mkdir(parents=True, exist_ok=True)
    mounted.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(mounted))
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config", json={"plex_url": PLEX_URL}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.CONFIG_ALREADY_PROVIDED
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["config_source"] == "configured"
    # Nothing staged, not merely nothing written: config_document_path()
    # already resolving would make the disk assertion true either way.
    assert setup_app.state.setup.config_document is None


async def test_a_document_that_does_not_validate_is_refused_by_class_name_only(
    setup_client, monkeypatch, tmp_path
):
    """Validated BEFORE it is staged: a document that does not load would leave
    the next boot crashing inside load_config with the wizard already gone."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("workers: 5\n", encoding="utf-8")
    monkeypatch.setattr(setup_api, "example_config_path", lambda: broken)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config", json={"plex_url": PLEX_URL}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "the configuration document was rejected (ValidationError)"
    )
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["config"] is False


async def test_a_plex_url_that_is_not_an_address_is_refused_with_a_fixed_sentence(
    setup_client,
):
    """PlexConfig.url is a bare str, so an empty one VALIDATES -- and a
    deployment whose plex.url is blank reaches every job and fails there, with
    the wizard already gone. The one field an operator types into this step is
    checked here instead."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config", json={"plex_url": ""}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.PLEX_URL_NOT_AN_ADDRESS
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["config"] is False


async def test_the_wizard_sees_the_document_the_next_boot_will_read(
    setup_client, monkeypatch, tmp_path
):
    """All three shipped shapes SET AUTOPOSTER_CONFIG -- the image bakes
    /config/autoposter.yaml, the HelmRelease sets it beside a ConfigMap mount,
    docker-compose.yml points it at the bind-mounted example -- and setup mode
    is reachable on all three (a blanked or failed ExternalSecret; a .env
    missing one hard name). Asking only whether the STATE document exists made
    the wizard demand step 4 on a deployment that already had a document, write
    the operator's Plex URL to a file the next boot never opens, and answer
    with that file's name. It asks the loader's own resolver instead."""
    from autoposter.config import loader as loader_module

    token = await _authenticate(setup_client)

    # The image's baked variable with nothing mounted at it: no document
    # resolves, so step 4 is offered.
    baked = tmp_path / "config" / "autoposter.yaml"
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(baked))
    body = (
        await setup_client.get("/api/setup/progress", headers=_headers(token))
    ).json()
    assert loader_module.config_document_path() is None
    assert body["config"] is False
    assert body["config_source"] is None

    # A ConfigMap mounted at the path the variable names.
    baked.parent.mkdir(parents=True, exist_ok=True)
    baked.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    response = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert loader_module.config_document_path() == baked
    assert response.json()["config"] is True
    assert response.json()["config_source"] == "configured"
    # The SOURCE, never the path.
    assert str(baked) not in response.text

    # Compose: the variable points at the bind-mounted example.
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(EXAMPLE))
    response = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert loader_module.config_document_path() == EXAMPLE
    assert response.json()["config_source"] == "configured"
    assert str(EXAMPLE) not in response.text

    # Nothing configured anywhere, and a document the wizard itself wrote.
    monkeypatch.delenv("AUTOPOSTER_CONFIG", raising=False)
    state_module.state_config_path().parent.mkdir(parents=True, exist_ok=True)
    state_module.state_config_path().write_text(
        EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    response = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert loader_module.config_document_path() == state_module.state_config_path()
    assert response.json()["config_source"] == "state"
    assert str(state_module.state_config_path()) not in response.text


def test_the_image_ships_the_example_document_the_wizard_starts_from():
    """The runtime image has never shipped a config, because a deployment
    mounts one -- and the deployment this row exists for has nothing to mount.
    Without these two lines the wizard's config step is a 500 in production
    while every test here passes against the repository's own copy."""
    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY config ./config" in dockerfile
    assert "ENV AUTOPOSTER_EXAMPLE_CONFIG=/app/config/autoposter.example.yaml" in dockerfile


def test_the_example_document_path_follows_the_environment(monkeypatch, tmp_path):
    """The spa_dist() shape, for spa_dist()'s reason: the package is
    pip-installed into site-packages in the image, so nothing is findable
    relative to the module files there."""
    monkeypatch.delenv("AUTOPOSTER_EXAMPLE_CONFIG", raising=False)
    assert state_module.example_config_path() == EXAMPLE

    monkeypatch.setenv("AUTOPOSTER_EXAMPLE_CONFIG", str(tmp_path / "elsewhere.yaml"))
    assert state_module.example_config_path() == tmp_path / "elsewhere.yaml"


# --- the deployment's own URL (wizard v2 step 2) -----------------------------

PUBLIC_URL = "https://autoposter.example.test"


async def test_the_deployment_url_step_stages_an_address(setup_client):
    """Step 2 of the v2 flow. Staged, never persisted here: it lands in the
    config document at the finish step, or nowhere at all on a deployment whose
    document already resolves (facts C1)."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/public-url", json={"url": PUBLIC_URL}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["public_url"] is True


async def test_the_progress_surface_reports_the_url_as_presence_and_never_as_a_value(setup_client):
    """Row 213 holds for a non-credential too: /progress is a presence surface,
    so the address the operator typed is reported as a boolean."""
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/public-url", json={"url": PUBLIC_URL}, headers=_headers(token)
    )

    response = await setup_client.get("/api/setup/progress", headers=_headers(token))

    assert set(response.json()) == {
        "password", "database", "providers", "required",
        "config", "config_source", "public_url",
    }
    assert PUBLIC_URL not in response.text


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "gopher://autoposter.example.test",
        "autoposter.example.test",
        "https://",
        "",
    ],
)
async def test_the_deployment_url_step_refuses_a_scheme_that_is_not_http(setup_client, value):
    """The shared guard's first half. `http`/`https` and a host, or nothing."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/public-url", json={"url": value}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.PUBLIC_URL_NOT_AN_ADDRESS


async def test_the_deployment_url_step_refuses_userinfo_in_the_authority(setup_client):
    """The guard's second half, and the one that matters for the check endpoint
    Task 2 reuses it in: `http://user:pass@host` puts a credential in a string
    that ends up in an *arr's database, its UI and its logs."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/public-url",
        json={"url": "https://operator:row-121-secret@autoposter.example.test"},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.PUBLIC_URL_NOT_AN_ADDRESS


async def test_the_deployment_url_refusal_never_echoes_the_address(setup_client):
    """The refusal names the requirement, never the value that failed it --
    the module's rule for every refusal it makes."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/public-url",
        json={"url": "gopher://row-121-typo-host.invalid"},
        headers=_headers(token),
    )

    assert "row-121-typo-host" not in response.text


async def test_the_deployment_url_is_written_into_the_document_the_wizard_stages(
    setup_client, setup_state
):
    """On a deployment with no document, the URL is a config value and lands in
    the document the finish step writes."""
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/public-url", json={"url": PUBLIC_URL}, headers=_headers(token)
    )

    response = await setup_client.post(
        "/api/setup/config", json={"plex_url": PLEX_URL}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    assert setup_state.config_document["public_url"] == PUBLIC_URL


async def test_the_staged_document_is_restamped_with_a_url_staged_after_the_config_step(
    setup_client, setup_state
):
    """The ordering hazard, closed by construction: the operator may complete
    the Plex accordion (which stages the document) before or after the URL
    step, and BACK makes both orders reachable. The document is re-stamped
    immediately before it is written, so neither order loses the value."""
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/config", json={"plex_url": PLEX_URL}, headers=_headers(token)
    )
    await setup_client.post(
        "/api/setup/public-url", json={"url": PUBLIC_URL}, headers=_headers(token)
    )

    stamped = setup_api._apply_staged_urls(dict(setup_state.config_document), setup_state)

    assert stamped["public_url"] == PUBLIC_URL


async def test_a_document_that_already_resolves_never_receives_the_deployment_url(
    setup_client, setup_state, monkeypatch, tmp_path
):
    """Facts C1: on a deployment whose document is supplied (a mounted
    ConfigMap), the wizard stages the URL and USES it for registration but
    persists nothing -- POST /api/setup/config still refuses outright
    (Amendment 6), and that refusal is unchanged by v2."""
    mounted = tmp_path / "mounted.yaml"
    mounted.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(mounted))
    token = await _authenticate(setup_client)

    staged = await setup_client.post(
        "/api/setup/public-url", json={"url": PUBLIC_URL}, headers=_headers(token)
    )
    refused = await setup_client.post(
        "/api/setup/config", json={"plex_url": PLEX_URL}, headers=_headers(token)
    )

    assert staged.status_code == 200, staged.text
    assert refused.status_code == 400
    assert refused.json()["detail"] == setup_api.CONFIG_ALREADY_PROVIDED
    assert setup_state.config_document is None


async def test_an_unreadable_example_document_is_a_503_and_not_a_500(setup_client, monkeypatch):
    """Row 121 residue (b), closed here because v2 makes it load-bearing: the
    config document is now composed from more than one step, so an image whose
    example document is missing fails with less to say than v1's single step
    had. A 503 naming the class, never a bare 500."""

    def _unreadable(_path):
        raise FileNotFoundError("the example document")

    monkeypatch.setattr(setup_api, "read_config_document", _unreadable)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config", json={"plex_url": PLEX_URL}, headers=_headers(token)
    )

    assert response.status_code == 503
    assert response.json()["detail"].startswith(setup_api.EXAMPLE_CONFIG_UNREADABLE)
    assert "FileNotFoundError" in response.json()["detail"]
    assert "the example document" not in response.json()["detail"]


# --- step 5: the handover ---------------------------------------------------


# What the wizard supplies for itself: the database URL has its own step, and
# the webhook secret is generated by the provider step.
_NOT_PASTED = {"AUTOPOSTER_DATABASE_URL", "AUTOPOSTER_WEBHOOK_SECRET"}


async def _complete_every_step(client, token, monkeypatch) -> None:
    monkeypatch.setattr(setup_api, "database_answers", _answering(True))
    await client.post(
        "/api/setup/database", json={"url": FAKE_DB_URL}, headers=_headers(token)
    )
    await client.post(
        "/api/setup/providers",
        json={
            "values": {
                name: (FAKE_PLEX_TOKEN if name == "AUTOPOSTER_PLEX_TOKEN" else "value")
                for name in HARD
                if name not in _NOT_PASTED
            }
        },
        headers=_headers(token),
    )
    await client.post(
        "/api/setup/config", json={"plex_url": PLEX_URL}, headers=_headers(token)
    )


async def test_finish_writes_the_config_document_then_the_secrets_and_execs(
    setup_client, monkeypatch
):
    """os.execv rather than a flag: nothing in a running setup application can
    become the real one -- no migration has run, no engine exists, create_app
    was never called -- and the exec is what makes the exit atomic.

    The write ORDER is Amendment 3's. The other order has a window in which the
    hard secrets resolve and no document does, which boot treats as a
    configuration error and a non-zero exit -- a deployment that can no longer
    be fixed by the wizard, because no wizard is served for that shape.
    """
    token = await _authenticate(setup_client)
    await _complete_every_step(setup_client, token, monkeypatch)
    document_was_on_disk: list[bool] = []
    real_merge = setup_api.merge_secrets_file

    def recording_merge(values):
        document_was_on_disk.append(state_module.state_config_path().is_file())
        real_merge(values)

    monkeypatch.setattr(setup_api, "merge_secrets_file", recording_merge)
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_api.os, "execv", lambda path, argv: calls.append(argv))

    response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    assert response.status_code == 200, response.text
    assert response.json() == {"restarting": True}
    assert document_was_on_disk == [True]
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert held["AUTOPOSTER_DATABASE_URL"] == FAKE_DB_URL
    assert held["AUTOPOSTER_PLEX_TOKEN"] == FAKE_PLEX_TOKEN
    assert held["AUTOPOSTER_WEBHOOK_SECRET"]
    assert load_config(state_module.state_config_path()).plex.url == PLEX_URL
    assert calls == [[setup_api.sys.executable, "-m", "autoposter.boot"]]


async def test_the_written_config_document_is_the_one_the_loader_would_find(
    setup_client, monkeypatch
):
    from autoposter.config import loader as loader_module

    token = await _authenticate(setup_client)
    await _complete_every_step(setup_client, token, monkeypatch)
    monkeypatch.setattr(setup_api.os, "execv", lambda path, argv: None)
    await setup_client.post("/api/setup/finish", headers=_headers(token))

    assert loader_module._default_config_path() == state_module.state_config_path()


async def test_nothing_hard_is_persisted_until_finish(setup_client, monkeypatch):
    """Amendment 3's whole point, stated as the boot decision it protects: a
    wizard abandoned after every step but the last leaves a state directory
    that boots back into setup mode -- never into the credentials-without-a-
    document exit, for which no wizard is served."""
    token = await _authenticate(setup_client)
    await _complete_every_step(setup_client, token, monkeypatch)

    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert set(held) == {"AUTOPOSTER_ADMIN_PASSWORD_HASH"}
    assert not state_module.state_config_path().exists()
    assert boot.is_configured(resolve_secret_values()) is False


async def test_finish_names_the_unmet_step_with_a_fixed_sentence(setup_client, monkeypatch):
    token = await _authenticate(setup_client)
    monkeypatch.setattr(setup_api.os, "execv", _never_exec)

    response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.STEP_DATABASE


async def test_finish_names_the_config_step_when_only_that_is_missing(
    setup_client, monkeypatch
):
    token = await _authenticate(setup_client)
    monkeypatch.setattr(setup_api, "database_answers", _answering(True))
    await setup_client.post(
        "/api/setup/database", json={"url": FAKE_DB_URL}, headers=_headers(token)
    )
    await setup_client.post(
        "/api/setup/providers",
        json={"values": {name: "value" for name in HARD if name not in _NOT_PASTED}},
        headers=_headers(token),
    )
    monkeypatch.setattr(setup_api.os, "execv", _never_exec)

    response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    assert response.json()["detail"] == setup_api.STEP_CONFIG


async def test_finish_never_echoes_a_value_in_its_refusal(setup_client, monkeypatch):
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_PLEX_TOKEN": FAKE_PLEX_TOKEN}},
        headers=_headers(token),
    )
    monkeypatch.setattr(setup_api.os, "execv", _never_exec)

    response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    assert FAKE_PLEX_TOKEN not in response.text
    assert FAKE_DB_URL not in response.text


async def test_finish_writes_no_state_document_when_the_deployment_has_one(
    setup_client, monkeypatch, tmp_path
):
    """The other side of the same rule: with a document the next boot will
    read, step 4 is never offered, nothing is staged for it, and finish must
    not write a second document beside the mounted one."""
    mounted = tmp_path / "config" / "autoposter.yaml"
    mounted.parent.mkdir(parents=True, exist_ok=True)
    mounted.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(mounted))
    token = await _authenticate(setup_client)
    monkeypatch.setattr(setup_api, "database_answers", _answering(True))
    await setup_client.post(
        "/api/setup/database", json={"url": FAKE_DB_URL}, headers=_headers(token)
    )
    await setup_client.post(
        "/api/setup/providers",
        json={"values": {name: "value" for name in HARD if name not in _NOT_PASTED}},
        headers=_headers(token),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_api.os, "execv", lambda path, argv: calls.append(argv))

    response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    assert response.status_code == 200, response.text
    assert not state_module.state_config_path().exists()
    assert calls == [[setup_api.sys.executable, "-m", "autoposter.boot"]]


# --- an unwritable state directory ------------------------------------------


async def test_an_unwritable_state_directory_answers_a_fixed_503(setup_client):
    """The deployment shape T5 creates: a pod whose PVC is missing or misowned
    reaches setup mode, passes /healthz, is routed by the Ingress -- and its
    very first POST is an UNAUTHENTICATED route that writes. Unhandled that is
    a bare 500 with FastAPI's generic body, from which an operator cannot tell
    an unwritable volume from a broken build."""
    directory = state_module.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o500)
    try:
        probe = directory / ".writable"
        try:
            probe.touch()
        except OSError:
            pass
        else:
            probe.unlink()
            pytest.skip("this uid writes a 0500 directory; the finish-step pin covers it")
        response = await setup_client.post(
            "/api/setup/password", json={"password": MASTER_PASSWORD}
        )
    finally:
        directory.chmod(0o700)

    _assert_state_dir_503(response)
    assert MASTER_PASSWORD not in response.text
    assert state_module.read_secrets_file(state_module.secrets_file_path()) == {}


async def test_a_refused_secrets_write_at_finish_is_the_same_503(
    setup_client, monkeypatch, caplog
):
    """Amendment 3's ordering, reported rather than served as a 500: the
    document has already landed when the second write raises, and what that
    leaves -- a document and no hard secrets -- is the next boot back in the
    wizard rather than the exit-forever shape."""
    token = await _authenticate(setup_client)
    await _complete_every_step(setup_client, token, monkeypatch)

    monkeypatch.setattr(setup_api, "merge_secrets_file", _refusing_write)
    monkeypatch.setattr(setup_api.os, "execv", _never_exec)

    with caplog.at_level(logging.DEBUG):
        response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    _assert_state_dir_503(response)
    assert FAKE_PLEX_TOKEN not in response.text
    assert FAKE_DB_URL not in response.text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_PLEX_TOKEN not in logged
    assert FAKE_DB_URL not in logged
    # The document stayed; the hard secrets did not land.
    assert state_module.state_config_path().is_file()
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert set(held) == {"AUTOPOSTER_ADMIN_PASSWORD_HASH"}
    assert boot.is_configured(resolve_secret_values()) is False


def _refusing_write(*args):
    """What ``directory.mkdir``/``mkstemp`` raise on a missing or misowned
    PVC. The errno text carries a file name, which is why the response may
    not."""
    raise PermissionError(13, "Permission denied", "secrets.env")


def _assert_state_dir_503(response) -> None:
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    # The DIRECTORY an operator sets and the exception's CLASS, and nothing
    # else: no errno text, no file name, no value.
    assert detail.startswith(setup_api.STATE_DIR_NOT_WRITABLE)
    assert str(state_module.state_dir()) in detail
    assert "PermissionError" in detail
    assert "Permission denied" not in detail
    assert state_module.SECRETS_FILE_NAME not in detail
    assert state_module.CONFIG_FILE_NAME not in detail


async def test_a_refused_password_write_is_the_same_503_unauthenticated(
    setup_client, monkeypatch
):
    """The read-only-directory pin above skips wherever the suite runs as root,
    which is the container CI uses -- so the arm that matters most is pinned
    without it as well. Step 1 is the UNAUTHENTICATED route and the first write
    a misowned volume refuses, and unhandled it is a bare 500."""
    monkeypatch.setattr(setup_api, "merge_secrets_file", _refusing_write)

    response = await setup_client.post(
        "/api/setup/password", json={"password": MASTER_PASSWORD}
    )

    _assert_state_dir_503(response)
    assert MASTER_PASSWORD not in response.text
    # No token was minted: a wizard that cannot write cannot proceed.
    assert "token" not in response.json()


async def test_a_refused_document_write_at_finish_is_the_same_503(
    setup_client, monkeypatch
):
    """The first of finish's two writes. Nothing lands, so the deployment is
    exactly as the wizard found it."""
    token = await _authenticate(setup_client)
    await _complete_every_step(setup_client, token, monkeypatch)
    monkeypatch.setattr(setup_api, "write_state_file", _refusing_write)
    monkeypatch.setattr(setup_api.os, "execv", _never_exec)

    response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    _assert_state_dir_503(response)
    assert FAKE_PLEX_TOKEN not in response.text
    assert FAKE_DB_URL not in response.text
    assert not state_module.state_config_path().exists()
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert set(held) == {"AUTOPOSTER_ADMIN_PASSWORD_HASH"}


# --- progress ---------------------------------------------------------------


async def test_the_progress_map_reports_steps_and_names_but_never_values(
    setup_client, monkeypatch
):
    token = await _authenticate(setup_client)
    await _complete_every_step(setup_client, token, monkeypatch)

    response = await setup_client.get("/api/setup/progress", headers=_headers(token))

    body = response.json()
    assert body["password"] is True
    assert body["database"] is True
    assert body["config"] is True
    assert body["required"] == []
    assert set(body["providers"].values()) <= {setup_api.REDACTED, None}
    assert FAKE_PLEX_TOKEN not in response.text
    assert FAKE_DB_URL not in response.text


def _never_exec(*args, **kwargs):
    raise AssertionError("the process must not be replaced while a step is unmet")


def _answering(ok: bool, failure: str = ""):
    async def answers(url: str) -> tuple[bool, str]:
        return ok, failure

    return answers
