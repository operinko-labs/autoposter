"""POST /api/config/webhook-secret/rotate.

Three claims carry this file and each has its own case, because they fail
independently.

1. **The refusal is structural.** An application that did not learn from
   `boot` that its webhook secret came from the state file refuses, and
   refuses BEFORE touching the filesystem -- the state file is byte-identical
   afterwards.
2. **The rebind is live at the real entry point.** Not "the attribute
   changed": a Radarr delivery carrying the new token is accepted and one
   carrying the old token is refused, both through `/webhook/radarr`.
3. **The value is served once and exists nowhere else.** Not in a log, not in
   the audit row, not in a second response.

The `_arr` seam is `test_api_setup_arr.py`'s: route-level cases monkeypatch
`setup_arr.register`, which is where the wizard's own route tests draw the
line too.
"""

import json

import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api import secret_rotation, setup_arr
from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config import state as state_module
from autoposter.config.loader import build_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog

# The wizard suite's autouse environment isolation, imported rather than
# copied -- `test_api_setup_arr.py:40-46` is the precedent. It delenvs every
# secret name and points STATE_DIR at a tmp_path, which is what makes the
# state-file assertions below about this test's own file.
from test_api_config_editor import EXAMPLE, PASSWORD  # noqa: F401
from test_api_setup import isolated_state  # noqa: F401

WEBHOOK_ENV = "AUTOPOSTER_WEBHOOK_SECRET"
OLD_SECRET = "row-255-old-webhook-secret-2a7c"
UNRELATED = "row-255-unrelated-soft-value-9d3f"
RADARR_BASE = "http://radarr.invalid:7878"
SONARR_BASE = "http://sonarr.invalid:8989"
PUBLIC_URL = "https://autoposter.example.test"


def _secrets(**overrides) -> Secrets:
    values = dict(
        database_url="postgresql+asyncpg://unused",
        plex_token="x",
        tmdb_token="x",
        tvdb_apikey="x",
        fanart_apikey="x",
        webhook_secret=OLD_SECRET,
        radarr_apikey="row-255-radarr-key",
        sonarr_apikey="row-255-sonarr-key",
        admin_password_hash=hash_password(PASSWORD),
    )
    values.update(overrides)
    return Secrets(**values)


def _document(**overrides) -> dict:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document["public_url"] = PUBLIC_URL
    document["radarr"] = {**document.get("radarr", {}), "base_url": RADARR_BASE}
    document["sonarr"] = {**document.get("sonarr", {}), "base_url": SONARR_BASE}
    document.update(overrides)
    return document


def _seed_state_file() -> None:
    """The file a wizard-configured deployment boots from. The unrelated soft
    name is here so every write below can be asserted to have left it alone."""
    state_module.merge_secrets_file(
        {WEBHOOK_ENV: OLD_SECRET, "AUTOPOSTER_MDBLIST_APIKEY": UNRELATED}
    )


def _build(session_factory, document, secrets, from_state=True):
    application = create_app(build_config(document), session_factory, secrets)
    # What `boot` would have published. Set on the object rather than through
    # the environment so a test that wants the refusal simply passes False --
    # `create_app`'s own read is pinned in tests/test_app.py.
    application.state.secret_from_state_file = {WEBHOOK_ENV: True} if from_state else {}
    return application


@pytest_asyncio.fixture
async def app(session_factory):
    _seed_state_file()
    return _build(session_factory, _document(), _secrets())


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _accepting(record: list) -> object:
    async def register(service, base_url, api_key, public_url, secret, transport=None):
        record.append(
            {
                "service": service,
                "base_url": base_url,
                "api_key": api_key,
                "public_url": public_url,
                "secret": secret,
            }
        )
        return "updated", None

    return register


async def _rotate(client, auth):
    return await client.post("/api/config/webhook-secret/rotate", headers=auth)


# --- the refusal --------------------------------------------------------------


async def test_an_env_configured_deployment_is_refused_by_a_fixed_sentence(
    session_factory, monkeypatch
):
    _seed_state_file()
    before = state_module.secrets_file_path().read_bytes()
    application = _build(session_factory, _document(), _secrets(), from_state=False)
    monkeypatch.setattr(setup_arr, "register", _accepting([]))
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = (await client.post("/api/login", json={"password": PASSWORD})).json()["token"]
        response = await _rotate(client, {"Authorization": f"Bearer {token}"})

    assert response.status_code == 400
    assert response.json()["detail"] == secret_rotation.ENV_CONFIGURED_REFUSAL
    # The point of the refusal is that NOTHING was written -- the bytes, not
    # just the status code.
    assert state_module.secrets_file_path().read_bytes() == before


def test_the_refusal_names_the_variable_and_nothing_else():
    """Row 213: a variable name is what an operator must act on; a path,
    a host or a value is not theirs to be told here."""
    sentence = secret_rotation.ENV_CONFIGURED_REFUSAL

    assert "AUTOPOSTER_WEBHOOK_SECRET" in sentence
    assert "secrets.env" not in sentence
    assert "/state" not in sentence


# --- the rebind -------------------------------------------------------------


async def test_the_rebind_is_a_new_object_that_carries_every_other_field(
    app, client, auth, monkeypatch
):
    monkeypatch.setattr(setup_arr, "register", _accepting([]))
    before = app.state.secrets

    response = await _rotate(client, auth)

    after = app.state.secrets
    assert response.status_code == 200
    assert after is not before
    assert after.webhook_secret == response.json()["webhook_secret"]
    assert after.webhook_secret != OLD_SECRET
    # The case that catches a copy which dropped a field: everything but the
    # webhook secret must be equal, admin_password_hash included -- an app
    # whose admin hash vanished mid-rotation locks the operator out.
    assert after.model_dump(exclude={"webhook_secret"}) == before.model_dump(
        exclude={"webhook_secret"}
    )


async def test_the_new_value_authenticates_at_the_intake_immediately(
    app, client, auth, monkeypatch
):
    """The gated-features rule: one test through the REAL entry point. The
    helper being right is not the claim -- `intake/routes.py` reading the
    rebound object per request is."""
    monkeypatch.setattr(setup_arr, "register", _accepting([]))
    minted = (await _rotate(client, auth)).json()["webhook_secret"]

    delivered = await client.post(
        "/webhook/radarr",
        headers={"X-Autoposter-Token": minted},
        json={"eventType": "Test"},
    )

    assert delivered.status_code == 200


async def test_the_old_value_stops_authenticating_immediately(app, client, auth, monkeypatch):
    """The other half, as its own case: the new one working and the old one
    having stopped are two claims, and a dual-accept regression would keep the
    first green."""
    monkeypatch.setattr(setup_arr, "register", _accepting([]))
    await _rotate(client, auth)

    delivered = await client.post(
        "/webhook/radarr",
        headers={"X-Autoposter-Token": OLD_SECRET},
        json={"eventType": "Test"},
    )

    assert delivered.status_code == 401


# --- once-only --------------------------------------------------------------


async def test_there_is_no_route_that_serves_the_value_a_second_time(app):
    """The wizard needed a GET because it minted at step 3 and displayed at
    step 5. This has no such gap: the POST response is the only serve, so
    there is nothing replayable to get wrong."""
    paths = app.openapi().get("paths", {})

    assert "/api/config/webhook-secret/rotate" in paths
    assert set(paths["/api/config/webhook-secret/rotate"]) == {"post"}
    # The ONLY webhook-secret path on this application, and it takes only POST.
    assert [path for path in paths if "webhook-secret" in path] == [
        "/api/config/webhook-secret/rotate"
    ]


async def test_a_second_rotation_answers_a_different_value(app, client, auth, monkeypatch):
    monkeypatch.setattr(setup_arr, "register", _accepting([]))

    first = (await _rotate(client, auth)).json()["webhook_secret"]
    second = (await _rotate(client, auth)).json()["webhook_secret"]

    assert first != second
    assert app.state.secrets.webhook_secret == second


async def test_the_value_reaches_the_response_and_nothing_else(
    app, client, auth, monkeypatch, caplog, session
):
    monkeypatch.setattr(setup_arr, "register", _accepting([]))
    with caplog.at_level("DEBUG"):
        response = await _rotate(client, auth)
    minted = response.json()["webhook_secret"]

    assert minted not in caplog.text
    assert minted not in "".join(
        json.dumps(entry, default=str) for entry in app.state.log_buffer.lines()
    )
    rows = (await session.execute(select(EventLog))).scalars().all()
    assert rows
    for row in rows:
        assert minted not in json.dumps(row.payload, default=str)
        assert minted not in (row.outcome or "")


# --- the registrations ------------------------------------------------------


async def test_register_is_called_with_the_configs_address_and_the_secrets_key(
    app, client, auth, monkeypatch
):
    """Nothing here is caller-supplied. The wizard bounds a resolved secret to
    a reconstructed address because there the address arrived in a REQUEST;
    here it comes from this deployment's own validated Config, so the body
    carries no address field at all and there is nothing to bound."""
    calls = []
    monkeypatch.setattr(setup_arr, "register", _accepting(calls))

    minted = (await _rotate(client, auth)).json()["webhook_secret"]

    assert {call["service"] for call in calls} == {"radarr", "sonarr"}
    radarr = next(call for call in calls if call["service"] == "radarr")
    assert radarr["base_url"] == RADARR_BASE
    assert radarr["api_key"] == "row-255-radarr-key"
    assert radarr["public_url"] == PUBLIC_URL
    assert radarr["secret"] == minted


async def test_a_disabled_service_is_still_re_registered(session_factory, monkeypatch):
    """`radarr.enabled` gates `arr_sync` -- registering unknown Plex items INTO
    Radarr -- and has nothing to do with the inbound webhook. A deployment can
    receive Radarr webhooks all day with `enabled: false`, and skipping it here
    would leave that deployment 401ing with every test green."""
    _seed_state_file()
    document = _document()
    document["radarr"]["enabled"] = False
    document["sonarr"]["enabled"] = False
    application = _build(session_factory, document, _secrets())
    calls = []
    monkeypatch.setattr(setup_arr, "register", _accepting(calls))
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = (await c.post("/api/login", json={"password": PASSWORD})).json()["token"]
        response = await _rotate(c, {"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert {call["service"] for call in calls} == {"radarr", "sonarr"}


async def test_a_service_with_no_address_or_no_key_is_reported_not_configured(
    session_factory, monkeypatch
):
    _seed_state_file()
    document = _document()
    document["radarr"]["base_url"] = ""
    application = _build(session_factory, document, _secrets(sonarr_apikey=""))
    calls = []
    monkeypatch.setattr(setup_arr, "register", _accepting(calls))
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = (await c.post("/api/login", json={"password": PASSWORD})).json()["token"]
        body = (await _rotate(c, {"Authorization": f"Bearer {token}"})).json()

    assert calls == []
    for service, label in (("radarr", "Radarr"), ("sonarr", "Sonarr")):
        assert body["registrations"][service]["ok"] is False
        assert body["registrations"][service]["action"] is None
        assert label in body["registrations"][service]["detail"]
        assert "not configured" in body["registrations"][service]["detail"].lower()


async def test_an_empty_public_url_rotates_and_names_public_url(session_factory, monkeypatch):
    """The finish page already promised the operator that `public_url` is what
    lets the Settings page re-register for them later. When it is empty the
    rotation still rotates -- the secret is independently useful -- and says
    which field to fill in."""
    _seed_state_file()
    document = _document(public_url="")
    application = _build(session_factory, document, _secrets())
    calls = []
    monkeypatch.setattr(setup_arr, "register", _accepting(calls))
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = (await c.post("/api/login", json={"password": PASSWORD})).json()["token"]
        response = await _rotate(c, {"Authorization": f"Bearer {token}"})

    body = response.json()
    assert response.status_code == 200
    assert body["webhook_secret"]
    assert calls == []
    assert "public_url" in body["registrations"]["radarr"]["detail"]
    assert "public_url" in body["registrations"]["sonarr"]["detail"]


async def test_a_refused_registration_is_a_200_with_the_secret_still_rotated(
    app, client, auth, monkeypatch
):
    """The rotation SUCCEEDED: the secret is rotated and this deployment
    now expects the new value. What failed is a courtesy the operator finishes
    with a paste, and a 500 or a rollback would trade that paste for a
    half-written state directory."""

    async def refusing(service, base_url, api_key, public_url, secret, transport=None):
        return None, "refused"

    monkeypatch.setattr(setup_arr, "register", refusing)

    response = await _rotate(client, auth)

    body = response.json()
    assert response.status_code == 200
    assert body["registrations"]["radarr"]["ok"] is False
    assert body["registrations"]["radarr"]["action"] is None
    assert "Radarr" in body["registrations"]["radarr"]["detail"]
    assert app.state.secrets.webhook_secret == body["webhook_secret"]
    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert held[WEBHOOK_ENV] == body["webhook_secret"]


async def test_a_registration_that_raises_is_reported_not_propagated(
    app, client, auth, monkeypatch, session
):
    """`setup_arr.register` never raises by contract, but a route that assumed
    so would turn one bad deployment into a 500 that says nothing -- AFTER the
    state file write and the rebind, so the deployment would have silently
    adopted a secret that was never served and no audit row would record it.
    The class name is what reaches the operator, never the exception's own
    text -- httpx embeds the request URL in its messages."""

    async def exploding(service, base_url, api_key, public_url, secret, transport=None):
        raise ConnectionError("x")

    monkeypatch.setattr(setup_arr, "register", exploding)

    response = await _rotate(client, auth)

    assert response.status_code == 200
    body = response.json()
    assert body["webhook_secret"]
    assert body["registrations"]["sonarr"]["ok"] is False
    assert "ConnectionError" in body["registrations"]["sonarr"]["detail"]

    row = (
        (
            await session.execute(
                select(EventLog).where(EventLog.event_type == "webhook_secret_rotated")
            )
        )
        .scalars()
        .one()
    )
    assert row.outcome == "rotated"


# --- the file, the audit row, and the guards --------------------------------


async def test_the_write_merges_and_leaves_every_other_name_alone(app, client, auth, monkeypatch):
    monkeypatch.setattr(setup_arr, "register", _accepting([]))

    minted = (await _rotate(client, auth)).json()["webhook_secret"]

    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert held[WEBHOOK_ENV] == minted
    assert held["AUTOPOSTER_MDBLIST_APIKEY"] == UNRELATED


async def test_the_audit_row_records_the_action_words_and_no_value(
    app, client, auth, monkeypatch, session
):
    monkeypatch.setattr(setup_arr, "register", _accepting([]))

    body = (await _rotate(client, auth)).json()

    row = (
        (
            await session.execute(
                select(EventLog).where(EventLog.event_type == "webhook_secret_rotated")
            )
        )
        .scalars()
        .one()
    )
    assert row.source == "config"
    assert row.outcome == "rotated"
    assert row.payload == {"radarr": "updated", "sonarr": "updated"}
    assert body["rotated_at"]


async def test_a_second_rotation_in_flight_is_refused_without_touching_an_arr(
    app, client, auth, monkeypatch
):
    """One `asyncio.Lock`, and a second caller refused rather than queued: two
    concurrent rotations would mint two secrets, leave the *arrs holding one
    and the app the other, and show both to the operator as if each had
    worked. `merge_secrets_file` is a read-modify-write over one file besides."""
    calls = []
    monkeypatch.setattr(setup_arr, "register", _accepting(calls))
    await app.state.secret_rotation_lock.acquire()
    try:
        response = await _rotate(client, auth)
    finally:
        app.state.secret_rotation_lock.release()

    assert response.status_code == 409
    assert calls == []
    assert app.state.secrets.webhook_secret == OLD_SECRET


async def test_the_rotation_is_rate_limited_the_way_login_is(app, client, auth, monkeypatch):
    monkeypatch.setattr(setup_arr, "register", _accepting([]))
    limit = app.state.rotation_rate_limiter.max_attempts

    statuses = [(await _rotate(client, auth)).status_code for _ in range(limit + 1)]

    assert statuses[-1] == 429
    assert set(statuses[:limit]) == {200}


async def test_an_unwritable_state_directory_is_a_fixed_503(app, client, auth, monkeypatch):
    """The reachable shape this branch exists for: a pod whose PVC is missing
    or misowned. The sentence carries the directory an operator sets, never a
    file name and never the errno text."""

    def refusing_write(values):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(secret_rotation, "merge_secrets_file", refusing_write)

    response = await _rotate(client, auth)

    assert response.status_code == 503
    assert secret_rotation.STATE_DIR_NOT_WRITABLE in response.json()["detail"]
    assert "PermissionError" in response.json()["detail"]
    assert app.state.secrets.webhook_secret == OLD_SECRET


async def test_the_route_is_on_the_documented_surface_and_needs_a_session(client):
    """`tests/test_api_login.py`'s structural sweep should pick this up for
    free -- asserted here rather than assumed, because a route registered
    outside the schema is exactly how such a sweep stops being structural."""
    response = await client.post("/api/config/webhook-secret/rotate")

    assert response.status_code == 401
