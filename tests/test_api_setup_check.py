"""One check-connection endpoint, ten subjects, and the bound on all of them.

The surface this file guards is the one the wizard could not avoid having: a
form behind the setup token that makes an outbound request to an address a
caller supplies. Five of the ten subjects take such an address (Plex, Jellyfin,
Radarr, Sonarr, Tracearr); five hit a compiled-in host. What is deliberately NOT here
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
from test_api_setup import _authenticate, _headers, isolated_state  # noqa: F401


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
JELLYFIN_BASE = "http://jellyfin.invalid:8096"
# What a provider's own error body would carry, and which must never reach the
# wizard's response.
PROVIDER_BODY = "row-121-provider-said-this"

CREDENTIALS = {
    "AUTOPOSTER_PLEX_TOKEN": FAKE_TOKEN,
    "AUTOPOSTER_JELLYFIN_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_PLEX_ACCOUNT_TOKEN": FAKE_TOKEN,
    "AUTOPOSTER_TMDB_TOKEN": FAKE_TOKEN,
    "AUTOPOSTER_TVDB_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_FANART_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_MDBLIST_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_RADARR_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_SONARR_APIKEY": FAKE_APIKEY,
    "AUTOPOSTER_TRACEARR_APIKEY": FAKE_APIKEY,
}

TYPED = ("plex", "jellyfin", "radarr", "sonarr", "tracearr")


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
    if system == "jellyfin":
        return JELLYFIN_BASE
    return SONARR_BASE if system in TYPED else None


def test_the_allowlist_is_exactly_the_ten_systems_the_wizard_shows():
    """An exact set, not a containment. The key is what the endpoint is allowed
    to build a request from, so a key added here is a new outbound target and
    has to be argued for in this assertion."""
    assert set(setup_checks.CHECK_SYSTEMS) == {
        "plex",
        "plex_account",
        "jellyfin",
        "tmdb",
        "tvdb",
        "fanart",
        "mdblist",
        "radarr",
        "sonarr",
        "tracearr",
    }


def test_no_check_lets_a_caller_choose_a_path_a_method_or_a_header():
    """The property that makes this a `check` endpoint rather than a proxy:
    every field except the base address of five subjects is compiled in."""
    for system, check in setup_checks.CHECK_SYSTEMS.items():
        assert check.method in {"GET", "POST"}, system
        assert (check.host is None) == (system in TYPED), system
        assert check.path.startswith("/"), system


@pytest.mark.parametrize(
    "system",
    [
        "plex",
        "plex_account",
        "jellyfin",
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
@pytest.mark.parametrize("system", ["plex", "jellyfin", "tmdb", "sonarr", "tracearr"])
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
        json={"system": "sonarr", "base_url": SONARR_BASE, "credential_value": FAKE_APIKEY},
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
        json={"system": "sonarr", "base_url": SONARR_BASE, "credential_value": FAKE_APIKEY},
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
        json={"system": "sonarr", "base_url": SONARR_BASE, "credential_value": FAKE_APIKEY},
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
            json={"system": system, "base_url": SONARR_BASE, "credential_value": FAKE_APIKEY},
            headers=_headers(token),
        )

    state = setup_client._transport.app.state.setup
    stamped = setup_api._apply_staged_urls({}, state)

    assert stamped["radarr"]["base_url"] == SONARR_BASE
    assert stamped["sonarr"]["base_url"] == SONARR_BASE
    assert stamped["tracearr"]["base_url"] == SONARR_BASE


# --- the composed url, and the bound on the body it brings back --------------

# `system`, the path the request must carry, the whole set of query NAMES it
# must carry, and the non-secret pairs whose values are asserted. The names of
# the two credential-bearing parameters are asserted and their values are not:
# an assertion message is a place a key must never appear, which is also why
# `CREDENTIALS` above is fake.
COMPOSED = [
    (
        "plex_account",
        "/api/v2/resources",
        {"includeHttps", "includeRelay"},
        {"includeHttps": "1", "includeRelay": "0"},
    ),
    ("tracearr", "/api/v2/public/history", {"limit"}, {"limit": "1"}),
    ("sonarr", "/api/v3/system/status", set(), {}),
    ("radarr", "/api/v3/system/status", set(), {}),
    ("plex", "/library/sections", set(), {}),
    ("tmdb", "/3/configuration", set(), {}),
    ("tvdb", "/v4/login", set(), {}),
    ("fanart", "/v3.2/movies/550", {"api_key"}, {}),
    ("mdblist", "/tmdb/movie/550/", {"apikey"}, {}),
]


def _recording_transport(seen: list[httpx.URL]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"ok": True}, headers={"x-ratelimit-limit": "100"})

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(("system", "path", "names", "pairs"), COMPOSED)
async def test_the_composed_url_is_the_path_and_query_the_table_compiled_in(
    system, path, names, pairs
):
    """The table's paths carry query strings -- `?limit=1`, `?page_size=1`,
    `?includeHttps=1&includeRelay=0` -- and httpx REPLACES a url's query with
    whatever `params=` says, an empty mapping included. Nothing else in this
    file looks at the url a request was actually built with, so the two bounds
    that exist to keep the probe cheap could be erased in silence."""
    seen: list[httpx.URL] = []

    await setup_checks.run_check(
        system, _address_for(system), CREDENTIALS, transport=_recording_transport(seen)
    )

    assert [url.path for url in seen] == [path]
    assert set(seen[0].params.keys()) == names
    for name, expected in pairs.items():
        assert seen[0].params.get(name) == expected, name


def _oversized_transport(chunks: list[int], chunk_size: int = 1024, count: int = 4096):
    """A response body far larger than anything this table reads, yielded a
    chunk at a time so the test can see how much of it was consumed."""

    async def body():
        for index in range(count):
            chunks.append(index)
            yield b"x" * chunk_size

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"x-ratelimit-limit": "100"})

    return httpx.MockTransport(handler)


async def test_a_probe_that_reads_no_body_never_pulls_one():
    """Eight of the nine subjects are answered by the status and one header. The
    body behind them is a third party's, on the private network these checks
    are aimed at, and five seconds of it is gigabytes."""
    chunks: list[int] = []

    outcome = await setup_checks.run_check(
        "sonarr", SONARR_BASE, CREDENTIALS, transport=_oversized_transport(chunks)
    )

    assert outcome.ok is True
    assert chunks == []


async def test_the_one_probe_that_reads_a_body_stops_at_the_cap():
    """MDBList's spent-budget shape is a shallow object, so the head of the
    body is all this reads and the rest is abandoned rather than buffered. An
    answer that does not fit in the cap is not that shape, and is reported as a
    failure by class name like every other one."""
    chunks: list[int] = []

    outcome = await setup_checks.run_check(
        "mdblist", None, CREDENTIALS, transport=_oversized_transport(chunks)
    )

    assert len(chunks) * 1024 <= setup_checks.CHECK_BODY_LIMIT_BYTES + 1024
    assert outcome.ok is False
    assert outcome.refused is False


# --- the credential the operator typed, not only the one the server holds ----


async def test_the_check_probes_the_credential_supplied_inline(setup_client, monkeypatch):
    """The two inputs in one accordion behaved oppositely: the address was live
    from the form and the credential was whatever was last SAVED, so a freshly
    pasted key was answered `refused` about a key that is correct."""
    seen: dict[str, str] = {}

    async def record(system, base_url, credentials, transport=None):
        seen.update(credentials)
        return setup_checks.CheckOutcome(ok=True, refused=False, failure=None)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", record)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": "sonarr", "base_url": SONARR_BASE, "credential_value": FAKE_APIKEY},
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    assert seen["AUTOPOSTER_SONARR_APIKEY"] == FAKE_APIKEY
    # Row 213: the answer is the fixed sentence and nothing the caller sent.
    assert FAKE_APIKEY not in response.text


async def test_an_inline_credential_is_used_for_the_probe_and_staged_nowhere(
    setup_client, monkeypatch
):
    """Staging stays with Save. A check is a question, and answering it must
    not become the deployment's answer to what its Sonarr key is."""

    async def answered(system, base_url, credentials, transport=None):
        return setup_checks.CheckOutcome(ok=True, refused=False, failure=None)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", answered)
    token = await _authenticate(setup_client)

    await setup_client.post(
        "/api/setup/check",
        json={"system": "sonarr", "base_url": SONARR_BASE, "credential_value": FAKE_APIKEY},
        headers=_headers(token),
    )

    assert setup_client._transport.app.state.setup.staged == {}


async def test_an_empty_credential_field_probes_the_one_the_deployment_holds(
    setup_client, monkeypatch
):
    """Empty means keep is the rule every one-field pane in this wizard has,
    and the check reads the same way: an operator who saved a key and then
    presses Check without retyping it checks the key that was saved."""
    held = "row-121-held-apikey-4d2e"
    seen: dict[str, str] = {}

    async def record(system, base_url, credentials, transport=None):
        seen.update(credentials)
        return setup_checks.CheckOutcome(ok=True, refused=False, failure=None)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", record)
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_SONARR_APIKEY": held}},
        headers=_headers(token),
    )

    await setup_client.post(
        "/api/setup/check",
        json={"system": "sonarr", "base_url": SONARR_BASE, "credential_value": None},
        headers=_headers(token),
    )

    assert seen["AUTOPOSTER_SONARR_APIKEY"] == held


# --- the checked Plex address, and the config step's own ---------------------


async def test_a_checked_plex_address_updates_a_document_but_never_adds_a_server(
    setup_client, monkeypatch
):
    """The `plex` arm of `_apply_staged_urls` -- the one whose config key is
    `url` rather than `base_url` -- which nothing asserted, and which the test
    below turns off.

    It UPDATES and does not ADD (Phase 6 review I2). A checked address
    correcting the server a document already names is the back navigation this
    map exists for; a checked address CREATING a `plex:` block would make
    "Check connection", the one control on that pane framed as a test, write a
    delivery target the operator never saved -- and `missing_server_setup`
    would then demand that server's credential to finish, with nothing able to
    remove it.
    """

    async def answered(system, base_url, credentials, transport=None):
        return setup_checks.CheckOutcome(ok=True, refused=False, failure=None)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", answered)
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/check",
        json={"system": "plex", "base_url": PLEX_BASE, "credential_value": FAKE_TOKEN},
        headers=_headers(token),
    )

    state = setup_client._transport.app.state.setup

    named = setup_api._apply_staged_urls({"plex": {"url": "http://stale:32400"}}, state)
    assert named["plex"]["url"] == PLEX_BASE
    assert "plex" not in setup_api._apply_staged_urls({}, state)


async def test_a_configuration_submit_wins_over_the_address_the_plex_check_staged(
    setup_client, monkeypatch
):
    """Both panes render on the SAME step, so this is one operator correcting
    one field: the address that answered is the NAT one, the address in-cluster
    traffic must use is the one typed into the configuration field second, and
    before this the endpoint answered 200 and wrote the first one anyway --
    at the config step AND again at finish, which applies the staged map a
    second time."""

    async def answered(system, base_url, credentials, transport=None):
        return setup_checks.CheckOutcome(ok=True, refused=False, failure=None)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", answered)
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/check",
        json={"system": "plex", "base_url": PLEX_BASE, "credential_value": FAKE_TOKEN},
        headers=_headers(token),
    )

    corrected = "http://plex:32400"
    response = await setup_client.post(
        "/api/setup/config", json={"plex_url": corrected}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    state = setup_client._transport.app.state.setup
    assert state.config_document["plex"]["url"] == corrected
    # And the second application, the one `finish` makes over the same map.
    assert setup_api._apply_staged_urls(state.config_document, state)["plex"]["url"] == corrected


# --- a typed address never carries a credential the resolver supplied --------


def _install(monkeypatch, status: int = 200):
    """Point the ROUTE's check at a recording transport, with the REAL
    ``run_check`` still in the middle of it.

    A route test that substitutes ``run_check`` proves what the endpoint
    decided and never what went on the wire, and the wire is the whole of the
    finding below: this seam makes "no outbound request was made" and "the
    probe carried THIS value and not that one" observable in the same test.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={}, headers={"x-ratelimit-limit": "100"})

    recorder = httpx.MockTransport(handler)
    original = setup_checks.run_check

    async def bound(system, base_url, credentials, transport=None):
        return await original(system, base_url, credentials, transport=recorder)

    monkeypatch.setattr(setup_api.setup_checks, "run_check", bound)
    return seen


@pytest.mark.parametrize("system", TYPED)
async def test_a_resolved_credential_never_reaches_an_address_the_caller_typed(
    setup_client, monkeypatch, system
):
    """Setup mode is entered when ONE hard secret fails to resolve, so a pod in
    it still holds every OTHER credential from its environment. Before this,
    an absent ``credential_value`` meant "probe with what the deployment
    holds" for the four systems whose HOST the caller supplies too -- so one
    request with the setup token sent the live Plex token, or an *arr key, to
    a host that request named.
    """
    check = setup_checks.CHECK_SYSTEMS[system]
    monkeypatch.setenv(check.credential, FAKE_APIKEY)
    seen = _install(monkeypatch)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": system, "base_url": _address_for(system)},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.CHECK_NEEDS_A_TYPED_CREDENTIAL
    # The whole of it: nothing was sent anywhere.
    assert seen == []
    assert FAKE_APIKEY not in response.text


async def test_a_staged_credential_may_go_to_an_address_the_caller_typed(setup_client, monkeypatch):
    """The other side of the rule. A value THIS WIZARD was handed came from the
    same caller as the address, so the probe runs and carries it -- which is
    what keeps "empty means keep" working for the operator who saved a key at
    the provider step and then pressed Check without retyping it."""
    seen = _install(monkeypatch)
    token = await _authenticate(setup_client)
    await setup_client.post(
        "/api/setup/providers",
        json={"values": {"AUTOPOSTER_SONARR_APIKEY": FAKE_APIKEY}},
        headers=_headers(token),
    )

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": "sonarr", "base_url": SONARR_BASE},
        headers=_headers(token),
    )

    assert response.json() == {"ok": True, "detail": "Sonarr answered."}
    assert [request.headers["X-Api-Key"] for request in seen] == [FAKE_APIKEY]


async def test_a_typed_credential_wins_over_the_one_the_resolver_holds(setup_client, monkeypatch):
    """The third arm, and the pre-existing rule this leaves intact: the value
    in the same request authenticates the probe."""
    monkeypatch.setenv("AUTOPOSTER_SONARR_APIKEY", "row-121-held-and-never-sent")
    seen = _install(monkeypatch)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check",
        json={"system": "sonarr", "base_url": SONARR_BASE, "credential_value": FAKE_APIKEY},
        headers=_headers(token),
    )

    assert response.json() == {"ok": True, "detail": "Sonarr answered."}
    assert [request.headers["X-Api-Key"] for request in seen] == [FAKE_APIKEY]
    assert "row-121-held-and-never-sent" not in str(seen[0].headers)


async def test_a_built_in_host_still_probes_the_credential_the_deployment_holds(
    setup_client, monkeypatch
):
    """The rule is about the ADDRESS and not about the credential. Six of the
    ten have a compiled-in host, so nothing the caller sent decides where the
    value goes, and "empty means keep" is unchanged for them."""
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", FAKE_TOKEN)
    seen = _install(monkeypatch)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/check", json={"system": "tmdb"}, headers=_headers(token)
    )

    assert response.json()["ok"] is True
    assert [request.url.host for request in seen] == ["api.themoviedb.org"]
