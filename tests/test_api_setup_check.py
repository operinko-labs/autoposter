"""One check-connection endpoint, ten subjects, and the bound on all of them.

The surface this file guards is the one the wizard could not avoid having: a
form behind the setup token that makes an outbound request to an address a
caller supplies. Four of the ten subjects take such an address (Plex, Radarr,
Sonarr, Tracearr); six hit a compiled-in host. What is deliberately NOT here
is a private-IP denylist -- every correct answer on every shipped deployment IS
a private address, so a denylist would refuse `http://sonarr` and nothing else.
The bound is instead: an allowlisted system key, a fixed path per system, no
caller-chosen method or header, a scheme/userinfo guard, and a five-second
ceiling. What remains is a boolean port scan behind the setup token, and the
module docstring says so out loud.
"""

import logging

import httpx
import pytest
import pytest_asyncio

from autoposter.api import setup as setup_api
from autoposter.api import setup_checks

# The autouse env isolation and the two token helpers are the neighbouring
# suite's, imported rather than copied because the list of names it clears is
# the part that drifts. The two fixtures BELOW are declared here instead of
# imported: a fixture imported into this namespace and then named as a test
# parameter is an F811 redefinition, and this repository has no precedent for
# importing fixtures across test modules -- only constants.
from tests.test_api_setup import _authenticate, _headers, isolated_state  # noqa: F401


@pytest.fixture
def setup_app():
    return setup_api.build_setup_app()


@pytest_asyncio.fixture
async def setup_client(setup_app):
    transport = httpx.ASGITransport(app=setup_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# `asyncio_mode = "auto"` (pyproject.toml) marks the async tests here, the way
# it does for every other suite in this directory.

# Obviously fake, and distinctive enough for the no-echo sweeps to search for.
FAKE_TOKEN = "row-121-check-token-a19f"
FAKE_APIKEY = "row-121-check-apikey-b73c"
SONARR_BASE = "http://sonarr.invalid:8989"
PLEX_BASE = "http://plex.invalid:32400"
# What a provider's own error body would carry, and which must never reach the
# wizard's response.
PROVIDER_BODY = "row-121-provider-said-this"

CREDENTIALS = {
    "AUTOPOSTER_PLEX_TOKEN": FAKE_TOKEN,
    "AUTOPOSTER_PLEX_ACCOUNT_TOKEN": FAKE_TOKEN,
    "AUTOPOSTER_TMDB_TOKEN": FAKE_TOKEN,
    "AUTOPOSTER_TVDB_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_FANART_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_MDBLIST_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_RADARR_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_SONARR_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_HARBOR_TOKEN": FAKE_TOKEN,
    "AUTOPOSTER_TRACEARR_APIKEY": FAKE_APIKEY,
}

TYPED = ("plex", "radarr", "sonarr", "tracearr")


def _transport(status: int, body=None, headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json=body if body is not None else {},
            headers=headers if headers is not None else {"x-ratelimit-limit": "100"},
        )

    return httpx.MockTransport(handler)


def _address_for(system: str) -> str | None:
    if system == "plex":
        return PLEX_BASE
    return SONARR_BASE if system in TYPED else None


def test_the_allowlist_is_exactly_the_ten_systems_the_wizard_shows():
    """An exact set, not a containment. The key is what the endpoint is allowed
    to build a request from, so a key added here is a new outbound target and
    has to be argued for in this assertion."""
    assert set(setup_checks.CHECK_SYSTEMS) == {
        "plex",
        "plex_account",
        "tmdb",
        "tvdb",
        "fanart",
        "mdblist",
        "radarr",
        "sonarr",
        "harbor",
        "tracearr",
    }


def test_no_check_lets_a_caller_choose_a_path_a_method_or_a_header():
    """The property that makes this a `check` endpoint rather than a proxy:
    every field except the base address of four subjects is compiled in."""
    for system, check in setup_checks.CHECK_SYSTEMS.items():
        assert check.method in {"GET", "POST"}, system
        assert (check.host is None) == (system in TYPED), system
        if system != "harbor":
            # Harbor's path is derived from AUTOPOSTER_IMAGE_REF at call time.
            assert check.path.startswith("/"), system


@pytest.mark.parametrize(
    "system",
    [
        "plex",
        "plex_account",
        "tmdb",
        "tvdb",
        "fanart",
        "mdblist",
        "radarr",
        "sonarr",
        "tracearr",
    ],
)
async def test_a_two_hundred_is_the_answered_outcome(system):
    body = {"data": {"token": FAKE_TOKEN}} if system == "tvdb" else {"ok": True}
    outcome = await setup_checks.run_check(
        system, _address_for(system), CREDENTIALS, transport=_transport(200, body)
    )

    assert outcome.ok is True
    assert outcome.failure is None


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("system", ["plex", "tmdb", "sonarr", "tracearr"])
async def test_a_401_or_403_is_the_credential_refusal(system, status):
    outcome = await setup_checks.run_check(
        system,
        _address_for(system),
        CREDENTIALS,
        transport=_transport(status, {"message": PROVIDER_BODY}),
    )

    assert outcome.ok is False
    assert outcome.refused is True


async def test_a_transport_error_is_the_class_name_and_nothing_else():
    """The `database_answers` idiom (db/base.py): a connection error's own text
    carries the address, and this string is a response body."""

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to " + SONARR_BASE)

    outcome = await setup_checks.run_check(
        "sonarr", SONARR_BASE, CREDENTIALS, transport=httpx.MockTransport(explode)
    )

    assert outcome.ok is False
    assert outcome.refused is False
    assert outcome.failure == "ConnectError"


async def test_a_hung_target_is_bounded_and_reported_as_a_timeout(monkeypatch):
    """A host that accepts and then stops answering is the case an httpx
    connect timeout does not cover. `asyncio.wait_for` around the whole probe
    does -- the same bound `database_answers` chose, and the same value."""
    import asyncio

    async def never(*_args, **_kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(setup_checks, "CHECK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(setup_checks, "_probe", never)

    outcome = await setup_checks.run_check("sonarr", SONARR_BASE, CREDENTIALS)

    assert outcome.ok is False
    assert outcome.failure == "TimeoutError"


async def test_mdblist_answers_two_hundred_with_an_error_body_when_the_budget_is_spent():
    """facts/mdblist.py:12-14 -- MDBList answers 200 with an error body rather
    than a 429. Reading the status alone would call a spent key healthy."""
    outcome = await setup_checks.run_check(
        "mdblist",
        None,
        CREDENTIALS,
        transport=_transport(200, {"error": "API Limit Reached!"}),
    )

    assert outcome.ok is False
    assert outcome.refused is True


async def test_tracearr_two_hundred_without_its_rate_limit_header_never_reached_the_api():
    """providers/tracearr.py:175 -- a 200 with no `x-ratelimit-limit` was
    served by the SPA, not the API, so it proves nothing about the key."""
    outcome = await setup_checks.run_check(
        "tracearr",
        SONARR_BASE,
        CREDENTIALS,
        transport=_transport(200, {"ok": True}, headers={}),
    )

    assert outcome.ok is False
    assert outcome.failure == "TracearrDidNotAnswer"


async def test_harbor_with_no_image_reference_says_so_as_a_class_name(monkeypatch):
    """Harbor's address is DERIVED from AUTOPOSTER_IMAGE_REF, never typed --
    which is why it has no SSRF surface at all -- so an unset one is a
    deployment fact, not a credential failure."""
    monkeypatch.delenv("AUTOPOSTER_IMAGE_REF", raising=False)

    outcome = await setup_checks.run_check("harbor", None, CREDENTIALS)

    assert outcome.ok is False
    assert outcome.failure == "ImageReferenceUnset"


async def test_no_provider_body_text_survives_any_outcome():
    """The sweep. Ten subjects, one assertion: whatever the third party said,
    the wizard reports a boolean and a class name."""
    for system in setup_checks.CHECK_SYSTEMS:
        for status in (200, 401, 500):
            outcome = await setup_checks.run_check(
                system,
                _address_for(system),
                CREDENTIALS,
                transport=_transport(status, {"message": PROVIDER_BODY}),
            )
            assert PROVIDER_BODY not in f"{outcome.ok}{outcome.refused}{outcome.failure}", system


async def test_no_credential_reaches_a_log_record(caplog):
    """httpx logs one INFO line per request carrying the whole url, which is
    why the address arm is here beside the credential one -- and why Fanart is
    checked too: its key rides the QUERY STRING, so for that subject the url
    and the credential are the same string."""
    caplog.set_level(logging.DEBUG)

    await setup_checks.run_check(
        "sonarr", SONARR_BASE, CREDENTIALS, transport=_transport(401, {"m": PROVIDER_BODY})
    )
    await setup_checks.run_check(
        "fanart", None, CREDENTIALS, transport=_transport(200, {"m": PROVIDER_BODY})
    )

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_APIKEY not in text
    assert SONARR_BASE not in text
    assert PROVIDER_BODY not in text


# --- the endpoint ------------------------------------------------------------


async def test_an_unknown_system_key_is_one_fixed_sentence(setup_client):
    """The key is caller text, so the refusal cannot name it -- the
    NOT_A_CREDENTIAL_THIS_SERVICE_READS idiom, on a second vocabulary."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": "row-121-not-a-system", "base_url": None},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NOT_A_SYSTEM_THIS_WIZARD_CHECKS
    assert "row-121-not-a-system" not in response.text


async def test_a_built_in_system_refuses_a_supplied_address(setup_client):
    """TMDb's host is a constant. Accepting one here would turn the endpoint
    into the proxy the table exists to prevent."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": "tmdb", "base_url": SONARR_BASE},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.CHECK_TAKES_NO_ADDRESS


async def test_a_typed_system_without_an_address_is_refused(setup_client):
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check", json={"system": "sonarr", "base_url": None}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.CHECK_NEEDS_AN_ADDRESS


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "gopher://sonarr.invalid",
        "sonarr.invalid",
        "http://operator:row-121-secret@sonarr.invalid",
    ],
)
async def test_the_endpoint_refuses_a_scheme_or_userinfo_the_guard_rejects(setup_client, value):
    """Task 1's shared guard, reused verbatim -- one spelling for the URL step,
    the check and the registration."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check", json={"system": "sonarr", "base_url": value}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.PUBLIC_URL_NOT_AN_ADDRESS
    assert "row-121-secret" not in response.text


async def test_a_successful_check_stages_the_address_it_proved(setup_client, monkeypatch):
    """The address is a config value with no step of its own, so the check --
    the one moment it is known to work -- is what stages it. The registration
    reads it from there, and the config document is stamped with it."""

    async def answered(system, base_url, credentials, transport=None):
        return setup_checks.CheckOutcome(ok=True, refused=False, failure=None)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", answered)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": "sonarr", "base_url": SONARR_BASE},
        headers=_headers(token),
    )

    assert response.json() == {"ok": True, "detail": "Sonarr answered."}
    assert setup_client._transport.app.state.setup.base_urls["sonarr"] == SONARR_BASE


async def test_a_failed_check_stages_nothing(setup_client, monkeypatch):
    async def unreachable(system, base_url, credentials, transport=None):
        return setup_checks.CheckOutcome(ok=False, refused=False, failure="ConnectError")

    monkeypatch.setattr(setup_api.setup_checks, "run_check", unreachable)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": "sonarr", "base_url": SONARR_BASE},
        headers=_headers(token),
    )

    assert response.json() == {
        "ok": False,
        "detail": "Sonarr could not be reached (ConnectError).",
    }
    assert setup_client._transport.app.state.setup.base_urls == {}


async def test_a_refused_credential_is_the_second_sentence(setup_client, monkeypatch):
    async def refused(system, base_url, credentials, transport=None):
        return setup_checks.CheckOutcome(ok=False, refused=True, failure=None)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", refused)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": "sonarr", "base_url": SONARR_BASE},
        headers=_headers(token),
    )

    assert response.json() == {"ok": False, "detail": "Sonarr refused the credential."}


async def test_the_checked_addresses_are_stamped_onto_the_document_the_wizard_writes(
    setup_client, monkeypatch
):
    """A checked Radarr/Sonarr/Tracearr address is a config value, so on a
    deployment with no document it lands in the one the finish step writes."""

    async def answered(system, base_url, credentials, transport=None):
        return setup_checks.CheckOutcome(ok=True, refused=False, failure=None)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", answered)
    token = await _authenticate(setup_client)
    for system in ("radarr", "sonarr", "tracearr"):
        await setup_client.post(
            "/api/setup/check",
            json={"system": system, "base_url": SONARR_BASE},
            headers=_headers(token),
        )

    state = setup_client._transport.app.state.setup
    stamped = setup_api._apply_staged_urls({}, state)

    assert stamped["radarr"]["base_url"] == SONARR_BASE
    assert stamped["sonarr"]["base_url"] == SONARR_BASE
    assert stamped["tracearr"]["base_url"] == SONARR_BASE
