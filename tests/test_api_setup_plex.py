"""The Plex PIN flow, the owned-server filter, and the both-names ruling.

Two things here are unusual and both are deliberate. First, the PIN CODE and
the app.plex.tv auth URL ARE served -- a documented row 213 exception:
they are minted by plex.tv, are public by design, and the flow is
impossible without showing them. Second, the account token is stored under
BOTH `AUTOPOSTER_PLEX_TOKEN` and `AUTOPOSTER_PLEX_ACCOUNT_TOKEN` with no
exchange, because probe 1 established that the OWNED server's `accessToken`
equals the account token. Servers shared TO the account are out of scope, and
the servers list is filtered `owned == true` server-side rather than merely
defaulted to it -- a shared server would need its own per-resource token, which
is exactly the scope the ruling excludes.

The fixture below follows what the implementation probe MEASURED against
plex.tv on 2026-09-07, not what the plan documented: a `strong=true` mint
answers 201 with twelve fields (`authToken`, `clientIdentifier`, `code`,
`createdAt`, `expiresAt`, `expiresIn`, `id`, `location`, `newRegistration`,
`product`, `qr`, `trusted`), and the `code` a STRONG pin carries is a long
opaque string -- not the four characters the plan expected. Nothing in the
client reads the code's length, so the drift changes no code; it changes what
the page may honestly say about it, and `PIN_CODE` here is long for that
reason.
"""

import asyncio
import logging
import os

import httpx
import pytest
import pytest_asyncio

from autoposter import boot
from autoposter.api import setup as setup_api
from autoposter.api import setup_plex
from autoposter.config.schema import (
    ENVIRONMENT_SECRET_NAMES_ENV,
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
)

# The autouse env isolation, the hard-name tuple, the two token helpers and the
# three the finish test below needs are the wizard suite's, imported rather than
# copied. The three FIXTURES below are declared here instead of imported,
# following `test_api_setup_check.py`: a fixture imported into this namespace
# and then named as a test parameter is an F811 redefinition, and this
# repository imports constants across test modules and not fixtures.
from test_api_setup import (  # noqa: F401
    FAKE_DB_URL,
    HARD,
    _answering,
    _authenticate,
    _headers,
    _NOT_PASTED,
    isolated_state,
)

@pytest.fixture
def database_url():
    """The database this pytest process owns.

    The finish step CONNECTS now -- it writes the staged credentials into the
    secrets table -- so a walk that goes through it has to name a database that
    answers, where before a well-formed unreachable one was enough.
    """
    return os.environ["AUTOPOSTER_TEST_DATABASE_URL"]


BOOT_MARKERS = (
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
    ENVIRONMENT_SECRET_NAMES_ENV,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def setup_app():
    return setup_api.build_setup_app()


@pytest_asyncio.fixture
async def setup_client(setup_app):
    transport = httpx.ASGITransport(app=setup_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def setup_state(setup_app):
    """The in-memory ``SetupState`` behind ``setup_client``. Staged values --
    the identifier, the PIN id, the account token, the document -- are never
    served back, so the assertions about them have to read the object."""
    return setup_app.state.setup


def _must_not_run(*args, **kwargs):
    raise AssertionError("boot went further than the clamp this test is about")


# Obviously fake. The account token is the one string every no-echo assertion
# in this file searches for.
ACCOUNT_TOKEN = "row-121-plex-account-token-8d2e"
# As long as a measured strong PIN's, and obviously fake with it.
PIN_CODE = "row-121-strong-pin-code-abcdefghij"
PIN_ID = 4242
EXPIRES_AT = "2099-01-01T00:00:00Z"
OWNED_ID = "row-121-owned-server"
SHARED_ID = "row-121-shared-server"
PLEX_BASE = "https://plex.invalid:32400"

RESOURCES = [
    {
        "clientIdentifier": OWNED_ID,
        "name": "Owned",
        "product": "Plex Media Server",
        "platform": "Linux",
        "provides": "server",
        "owned": True,
        "accessToken": ACCOUNT_TOKEN,
        "connections": [
            {
                "uri": "https://remote.invalid:32400",
                "local": False,
                "protocol": "https",
                "port": 32400,
                "relay": False,
            },
            {
                "uri": "https://plex.invalid:32400",
                "local": True,
                "protocol": "https",
                "port": 32400,
                "relay": False,
            },
        ],
    },
    {
        "clientIdentifier": SHARED_ID,
        "name": "Shared",
        "product": "Plex Media Server",
        "platform": "Windows",
        "provides": "server",
        "owned": False,
        "accessToken": "row-121-someone-elses-token",
        "connections": [
            {
                "uri": "https://other.invalid:32400",
                "local": True,
                "protocol": "https",
                "port": 32400,
                "relay": False,
            },
        ],
    },
]

SECTIONS = {
    "MediaContainer": {
        "Directory": [
            {"key": "1", "title": "Movies", "type": "movie"},
            {"key": "2", "title": "TV", "type": "show"},
            {"key": "3", "title": "Photos", "type": "photo"},
        ]
    }
}


def _plex_transport(*, authorised: bool):
    """One handler for the whole flow, recording every request it saw.

    The two PIN bodies carry every field the live probe saw, so a client that
    started reading `expiresIn` or `location` would be exercised against the
    measured shape rather than against a convenient subset.
    """
    seen: list[httpx.Request] = []

    def _pin(*, with_token: bool) -> dict:
        return {
            "id": PIN_ID,
            "code": PIN_CODE,
            "authToken": ACCOUNT_TOKEN if with_token else None,
            "expiresAt": EXPIRES_AT,
            "expiresIn": 1800,
            "createdAt": "2026-09-07T00:00:00Z",
            "clientIdentifier": "ignored",
            "location": {"code": "XX"},
            "newRegistration": None,
            "product": setup_plex.PRODUCT,
            "qr": "https://plex.tv/api/v2/pins/qr/ignored",
            "trusted": False,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/api/v2/pins" and request.method == "POST":
            return httpx.Response(201, json=_pin(with_token=False))
        if path == f"/api/v2/pins/{PIN_ID}":
            return httpx.Response(200, json=_pin(with_token=authorised))
        if path == "/api/v2/resources":
            return httpx.Response(200, json=RESOURCES)
        if path == "/library/sections":
            return httpx.Response(200, json=SECTIONS)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler), seen


async def test_minting_a_pin_answers_a_code_and_an_expiry_and_no_token():
    transport, _seen = _plex_transport(authorised=False)

    minted = await setup_plex.mint_pin("row-121-client-id", transport=transport)

    assert minted["code"] == PIN_CODE
    assert minted["id"] == PIN_ID
    assert minted["expires_at"] == EXPIRES_AT
    assert "authToken" not in minted


async def test_every_call_carries_the_same_client_identifier():
    """The load-bearing header: plex.tv 404s a poll whose identifier differs
    from the mint's, which the implementation probe confirmed."""
    transport, seen = _plex_transport(authorised=True)

    await setup_plex.mint_pin("row-121-client-id", transport=transport)
    await setup_plex.poll_pin(PIN_ID, "row-121-client-id", transport=transport)

    assert [r.headers["X-Plex-Client-Identifier"] for r in seen] == [
        "row-121-client-id",
        "row-121-client-id",
    ]


async def test_the_mint_asks_for_a_strong_pin():
    """Measured: `?strong=true` is what makes the code unguessable, and it is
    the reason the code is long rather than four characters. A mint that lost
    the query string would answer a four-character PIN an attacker with the
    client identifier could enumerate."""
    transport, seen = _plex_transport(authorised=False)

    await setup_plex.mint_pin("row-121-client-id", transport=transport)

    assert seen[0].url.params["strong"] == "true"


async def test_the_poll_answers_none_until_the_operator_approves():
    transport, _seen = _plex_transport(authorised=False)

    assert await setup_plex.poll_pin(PIN_ID, "row-121-client-id", transport=transport) is None


async def test_the_poll_answers_the_account_token_once_approved():
    transport, _seen = _plex_transport(authorised=True)

    token = await setup_plex.poll_pin(PIN_ID, "row-121-client-id", transport=transport)

    assert token == ACCOUNT_TOKEN


async def test_the_auth_url_carries_the_code_and_the_identifier():
    """Served deliberately: the operator cannot complete the flow otherwise.
    The row 213 exception, and the whole of it."""
    url = setup_plex.auth_url("row-121-client-id", PIN_CODE)

    assert url.startswith("https://app.plex.tv/auth#?")
    assert f"code={PIN_CODE}" in url
    assert "clientID=row-121-client-id" in url


async def test_only_owned_servers_are_listed():
    """Probe 1: the four shared servers each carry a DIFFERENT accessToken, so
    a shared server would need an exchange -- the scope the ruling excludes.
    Filtered server-side, not defaulted to."""
    transport, _seen = _plex_transport(authorised=True)

    servers = await setup_plex.owned_servers(
        ACCOUNT_TOKEN, "row-121-client-id", transport=transport
    )

    assert [s["client_identifier"] for s in servers] == [OWNED_ID]


async def test_no_server_entry_carries_a_token():
    transport, _seen = _plex_transport(authorised=True)

    servers = await setup_plex.owned_servers(
        ACCOUNT_TOKEN, "row-121-client-id", transport=transport
    )

    assert ACCOUNT_TOKEN not in repr(servers)
    assert all("accessToken" not in server for server in servers)


async def test_connections_are_ordered_local_first():
    """Probe 1: every entry has exactly one `local: true` https connection on
    32400, and that is the one a pod inside the cluster should use."""
    transport, _seen = _plex_transport(authorised=True)

    servers = await setup_plex.owned_servers(
        ACCOUNT_TOKEN, "row-121-client-id", transport=transport
    )

    assert [c["local"] for c in servers[0]["connections"]] == [True, False]


async def test_the_resources_call_carries_the_client_identifier():
    """The load-bearing header this row exists for: the implementation probe
    measured plex.tv answering 400 `X-Plex-Client-Identifier is missing` on
    `/api/v2/resources` without it, and 200 with it -- the same identifier the
    mint and poll send, or the account looks like a different device."""
    transport, seen = _plex_transport(authorised=True)
    identifier = "row-121-client-id"
    await setup_plex.mint_pin(identifier, transport=transport)

    await setup_plex.owned_servers(ACCOUNT_TOKEN, identifier, transport=transport)

    request = next(r for r in seen if r.url.path == "/api/v2/resources")
    assert "X-Plex-Client-Identifier" in request.headers
    assert "X-Plex-Product" in request.headers
    assert "X-Plex-Token" in request.headers
    assert request.headers["X-Plex-Client-Identifier"] == identifier


async def test_library_sections_are_key_title_and_type_and_nothing_else():
    transport, _seen = _plex_transport(authorised=True)

    sections = await setup_plex.library_sections(
        PLEX_BASE, ACCOUNT_TOKEN, "row-121-client-id", transport=transport
    )

    assert sections == [
        {"key": "1", "title": "Movies", "type": "movie"},
        {"key": "2", "title": "TV", "type": "show"},
        {"key": "3", "title": "Photos", "type": "photo"},
    ]


async def test_the_sections_call_carries_the_client_identifier():
    """Same header, same reason, on the second of the two calls the live probe
    found broken: a picked server's `/library/sections` read."""
    transport, seen = _plex_transport(authorised=True)
    identifier = "row-121-client-id"
    await setup_plex.mint_pin(identifier, transport=transport)

    await setup_plex.library_sections(PLEX_BASE, ACCOUNT_TOKEN, identifier, transport=transport)

    request = next(r for r in seen if r.url.path == "/library/sections")
    assert "X-Plex-Client-Identifier" in request.headers
    assert "X-Plex-Product" in request.headers
    assert "X-Plex-Token" in request.headers
    assert request.headers["X-Plex-Client-Identifier"] == identifier


async def test_a_library_read_stops_at_the_body_cap():
    """The timeout bounds TIME and not SIZE.

    Unlike the check probes -- which read a status and abandon the body --
    these four calls have to READ their answers, and one of them reads from the
    address the operator picked. An endless body there would be an endless read
    into a pod with a memory limit. `setup_checks`' idiom, with a cap sized for
    a body that is used rather than thrown away.
    """
    offered = 0
    # Four megabytes, offered a chunk at a time and COUNTED. Large but finite
    # on purpose: an endless generator would prove the cap by hanging forever
    # when it regressed, and a test that hangs is not a test that reports.
    chunk_count = 1024

    async def oversized():
        nonlocal offered
        for _ in range(chunk_count):
            offered += 1
            yield b"x" * 4096

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized())

    # A megabyte of `x` is not JSON, which is the honest answer: the route
    # above turns it into a class name like every other failure.
    with pytest.raises(Exception):
        await setup_plex.library_sections(
            PLEX_BASE, ACCOUNT_TOKEN, "row-121-client-id", transport=httpx.MockTransport(handler)
        )

    within_the_cap = setup_plex.PLEX_BODY_LIMIT_BYTES // 4096 + 1
    assert within_the_cap < chunk_count, "the fixture must be bigger than the cap to prove one"
    assert offered <= within_the_cap


async def test_no_plex_call_logs_the_token_the_pin_or_the_identifier(caplog):
    """Nothing reaches a log record, and `caplog` proves it at DEBUG.

    `caplog.set_level(logging.DEBUG)` deliberately DEFEATS `boot.main`'s
    process-wide clamp, which is the point: httpx logs one INFO line per
    request with the FULL url and the poll url carries the PIN id, so if that
    exception rested on that clamp alone the last assertion here would fail. It passes
    because every call runs inside `setup_checks.no_httpx_request_log` -- the
    property is this module's own, not another file's setting.
    """
    caplog.set_level(logging.DEBUG)
    transport, _seen = _plex_transport(authorised=True)

    await setup_plex.mint_pin("row-121-client-id", transport=transport)
    await setup_plex.poll_pin(PIN_ID, "row-121-client-id", transport=transport)
    await setup_plex.owned_servers(ACCOUNT_TOKEN, "row-121-client-id", transport=transport)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert ACCOUNT_TOKEN not in text
    assert PIN_CODE not in text
    assert "row-121-client-id" not in text
    assert str(PIN_ID) not in text


async def test_boot_clamps_httpx_before_this_flow_can_make_a_request(monkeypatch, tmp_path):
    """The second line of defence, pinned because nothing pinned it before.

    `setup_plex`'s own filter is the first, and the test above proves it. This
    is `boot.main`'s process-wide clamp (boot.py:151), which covers every other
    httpx caller in the process -- and which no test asserted until this row
    put a PIN id in a url: deleting the line would have left every suite green.
    """
    logging.getLogger("httpx").setLevel(logging.INFO)
    for name in HARD:
        monkeypatch.setenv(name, "x")
    # `boot.main` assigns its three name markers into `os.environ` itself, and
    # monkeypatch records no undo for a name this test never touched -- so
    # without these they would outlive it and decide a source label in every
    # later test in the same worker. Setting each to what it already holds is
    # what registers that undo.
    for marker in BOOT_MARKERS:
        monkeypatch.setenv(marker, os.environ.get(marker, ""))
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(tmp_path / "absent.yaml"))
    monkeypatch.setattr(boot, "_migrate", _must_not_run)
    monkeypatch.setattr(boot.uvicorn, "run", _must_not_run)
    monkeypatch.setattr(boot.os, "execv", _must_not_run)

    # Every hard secret resolves and no document does, which boot answers with
    # a non-zero exit -- the cheapest shape that runs main's opening lines.
    with pytest.raises(SystemExit):
        boot.main([])

    assert logging.getLogger("httpx").level == logging.WARNING


# --- the routes --------------------------------------------------------------


def _install(monkeypatch, *, authorised: bool):
    """Point the routes at the fake transport, and record what they called."""
    transport, seen = _plex_transport(authorised=authorised)
    for name in ("mint_pin", "poll_pin", "owned_servers", "library_sections"):
        original = getattr(setup_plex, name)

        def bound(*args, _original=original, **kwargs):
            return _original(*args, transport=transport, **kwargs)

        monkeypatch.setattr(setup_api.setup_plex, name, bound)
    return seen


async def test_the_mint_route_serves_the_code_and_the_link_and_never_a_token(
    setup_client, monkeypatch
):
    """The documented row 213 exception, and its exact extent: a code, a link
    and a number of seconds."""
    _install(monkeypatch, authorised=False)
    token = await _authenticate(setup_client)

    response = await setup_client.post("/api/setup/plex/pin", headers=_headers(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"code", "auth_url", "expires_in"}
    assert body["code"] == PIN_CODE
    assert body["auth_url"].startswith("https://app.plex.tv/auth#?")
    assert isinstance(body["expires_in"], int)


async def test_the_mint_route_stages_the_identifier_and_the_pin_id(
    setup_client, setup_state, monkeypatch
):
    """Staged, never persisted: the poll 404s without the same identifier the
    mint used, so both have to outlive the request that made them -- and
    nothing after the wizard needs either, so neither is written down."""
    _install(monkeypatch, authorised=False)
    token = await _authenticate(setup_client)

    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))

    assert setup_state.plex_client_identifier is not None
    assert setup_state.plex_pin_id == PIN_ID


async def test_a_second_sign_in_reuses_the_deployments_client_identifier(
    setup_client, setup_state, monkeypatch
):
    """One identifier per deployment, not one per attempt.

    It names this deployment as a DEVICE on the operator's plex.tv account, so
    a fresh one per attempt would leave a dead device entry behind for every
    sign-in they restarted. What must not be inherited is the abandoned PIN,
    and the pin id is overwritten on every mint -- which is what makes reusing
    the identifier safe."""
    _install(monkeypatch, authorised=False)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))
    first = setup_state.plex_client_identifier

    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))

    assert setup_state.plex_client_identifier == first
    assert setup_state.plex_pin_id == PIN_ID


async def test_the_poll_answers_a_boolean_and_the_token_is_in_no_response_body(
    setup_client, monkeypatch
):
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))

    response = await setup_client.get("/api/setup/plex/pin", headers=_headers(token))

    assert response.json() == {"authorised": True}
    assert ACCOUNT_TOKEN not in response.text


async def test_an_unapproved_poll_answers_false(setup_client, monkeypatch):
    _install(monkeypatch, authorised=False)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))

    response = await setup_client.get("/api/setup/plex/pin", headers=_headers(token))

    assert response.json() == {"authorised": False}


async def test_an_approved_poll_stages_the_account_token_under_both_names(
    setup_client, setup_state, monkeypatch
):
    """The ruling, and probe 1 is its precondition: the OWNED server's
    accessToken EQUALS the account token on this deployment, so there is
    nothing to exchange and both names get the same value."""
    seen = _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))

    await setup_client.get("/api/setup/plex/pin", headers=_headers(token))

    staged = setup_state.staged
    assert staged["AUTOPOSTER_PLEX_TOKEN"] == ACCOUNT_TOKEN
    assert staged["AUTOPOSTER_PLEX_ACCOUNT_TOKEN"] == ACCOUNT_TOKEN
    # And no exchange: the only plex.tv paths touched are the mint and the poll.
    assert sorted({request.url.path for request in seen}) == [
        "/api/v2/pins",
        f"/api/v2/pins/{PIN_ID}",
    ]


async def test_polling_before_a_pin_is_minted_is_one_fixed_sentence(setup_client):
    token = await _authenticate(setup_client)

    response = await setup_client.get("/api/setup/plex/pin", headers=_headers(token))

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NO_PLEX_SIGN_IN_IN_PROGRESS


async def test_the_servers_route_lists_only_owned_entries(setup_client, monkeypatch):
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))
    await setup_client.get("/api/setup/plex/pin", headers=_headers(token))

    response = await setup_client.get("/api/setup/plex/servers", headers=_headers(token))

    assert [s["client_identifier"] for s in response.json()["servers"]] == [OWNED_ID]
    assert ACCOUNT_TOKEN not in response.text


async def test_the_servers_route_needs_a_signed_in_account(setup_client):
    """No account token staged and none resolving means there is nothing to
    ask plex.tv with -- said as a step, never as a credential."""
    token = await _authenticate(setup_client)

    response = await setup_client.get("/api/setup/plex/servers", headers=_headers(token))

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NO_PLEX_ACCOUNT_TOKEN


async def test_the_libraries_route_lists_key_title_and_type(setup_client, monkeypatch):
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))
    await setup_client.get("/api/setup/plex/pin", headers=_headers(token))

    response = await setup_client.post(
        "/api/setup/plex/libraries", json={"base_url": PLEX_BASE}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    assert [library["title"] for library in response.json()["libraries"]] == [
        "Movies",
        "TV",
        "Photos",
    ]


@pytest.mark.parametrize(
    "value", ["file:///etc/passwd", "plex.invalid", "http://operator:row-121-secret@plex.invalid"]
)
async def test_the_libraries_route_applies_the_shared_address_guard(
    setup_client, monkeypatch, value
):
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))
    await setup_client.get("/api/setup/plex/pin", headers=_headers(token))

    response = await setup_client.post(
        "/api/setup/plex/libraries", json={"base_url": value}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.PUBLIC_URL_NOT_AN_ADDRESS
    assert "row-121-secret" not in response.text


async def test_the_libraries_route_needs_a_signed_in_account(setup_client, monkeypatch):
    """The address guard runs first and this second: an operator who reached a
    pick-list has a token, so this is the direct-POST shape, and it is a step
    to complete rather than a credential to supply. The
    sentence is the typed-address one -- neither typed nor staged is the only
    way to arrive here with nothing to read with."""
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/plex/libraries", json={"base_url": PLEX_BASE}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.CHECK_NEEDS_A_TYPED_CREDENTIAL


async def test_the_plex_selection_stages_the_url_and_the_excluded_libraries(
    setup_client, setup_state, monkeypatch
):
    """The config step, driven from the accordion instead of a bare text box.
    The ticks are stored as their COMPLEMENT -- `plex.excluded_libraries` is
    what the schema has, and the example's own two entries are one deployment's
    and must not survive onto another's."""
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config",
        json={"plex_url": PLEX_BASE, "excluded_libraries": ["Photos"]},
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    document = setup_state.config_document
    assert document["plex"]["url"] == PLEX_BASE
    assert document["plex"]["excluded_libraries"] == ["Photos"]


async def test_every_library_ticked_stages_an_empty_exclusion_list(
    setup_client, setup_state, monkeypatch
):
    """`[]` and absent are different answers, and this is the one that says so.
    An operator who ticked every library HAS answered -- the example's two
    exclusions are another deployment's and must not survive that answer -- so
    an empty list is applied and not read as "unchanged"."""
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)

    await setup_client.post(
        "/api/setup/config",
        json={"plex_url": PLEX_BASE, "excluded_libraries": []},
        headers=_headers(token),
    )

    assert setup_state.config_document["plex"]["excluded_libraries"] == []


async def test_the_selection_without_a_library_list_leaves_the_examples_alone(
    setup_client, setup_state, monkeypatch
):
    """`excluded_libraries` is optional and absent means "unchanged" -- the
    same "empty means keep" rule the provider step has, for the same reason:
    an operator who did not reach the tick-list has not asked for anything."""
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)

    await setup_client.post(
        "/api/setup/config", json={"plex_url": PLEX_BASE}, headers=_headers(token)
    )

    assert setup_state.config_document["plex"]["excluded_libraries"] == ["Muskarit", "Photos"]


async def test_no_route_logs_the_token_the_code_or_the_identifier(
    setup_client, setup_state, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))
    await setup_client.get("/api/setup/plex/pin", headers=_headers(token))
    await setup_client.get("/api/setup/plex/servers", headers=_headers(token))

    text = "\n".join(record.getMessage() for record in caplog.records)
    identifier = setup_state.plex_client_identifier
    assert ACCOUNT_TOKEN not in text
    assert PIN_CODE not in text
    assert identifier not in text
    assert str(PIN_ID) not in text


async def test_a_dripping_answer_is_cut_off_by_the_total_time_bound(monkeypatch):
    """The size cap is not a bound on TIME, as the timeout is not a bound on size.

    ``timeout=`` is httpx's PER-OPERATION timeout: a server emitting one byte
    inside every window gets a fresh ten seconds for each, so it holds the
    handler and the connection until the megabyte cap is reached -- hours rather
    than seconds -- and the reachable case is a library read against the address
    the operator supplied. ``setup_checks.run_check`` wraps its whole probe in
    ``asyncio.wait_for`` and lists that bound separately from its cap; this is
    the same bound, here.

    The drip is finite on purpose, for the body cap test's reason: a test that
    proves a bound by hanging forever once the bound regresses is not a test
    that reports.
    """
    offered = 0
    chunks = 200
    monkeypatch.setattr(setup_plex, "PLEX_TIMEOUT_SECONDS", 0.1)

    async def dripping():
        nonlocal offered
        for _ in range(chunks):
            offered += 1
            await asyncio.sleep(0.02)
            yield b"x"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=dripping())

    # Two hundred bytes over four seconds: far under the megabyte cap, and every
    # single read far under one timeout window. Only a bound on the WHOLE call
    # can stop this one.
    with pytest.raises(TimeoutError):
        await setup_plex.library_sections(
            PLEX_BASE, ACCOUNT_TOKEN, "row-121-client-id", transport=httpx.MockTransport(handler)
        )

    assert offered < chunks, "the whole call must be bounded, not merely each read"


async def test_the_libraries_route_reads_with_the_token_typed_beside_the_address(
    setup_client, setup_state, monkeypatch
):
    """The MANUAL arrival at the tick-list, and why it exists.

    The pick-list is not the only way a real deployment gets here: plex.tv can
    be unreachable from the pod, the server can be unlinked from any plex.tv
    account, the picked connection can be one the pod cannot route to, and the
    operator can simply already hold the token. All four leave the accordion's
    typed address and a pasted token as the only inputs there are, and the
    wizard cannot be finished without a configuration document -- so this route
    reads the token the same way ``/check`` does.

    ``credential_value``'s semantics are that endpoint's exactly: used for THIS
    read and staged nowhere, absent or empty meaning "keep", so an untouched
    field reads with the token the deployment holds.
    """
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/plex/libraries",
        json={"base_url": PLEX_BASE, "credential_value": ACCOUNT_TOKEN},
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    assert [library["title"] for library in response.json()["libraries"]] == [
        "Movies",
        "TV",
        "Photos",
    ]
    assert ACCOUNT_TOKEN not in response.text
    # Staged nowhere: what the deployment WILL hold is Save's answer, never a
    # question's -- the rule `/check` states for the same field.
    assert "AUTOPOSTER_PLEX_TOKEN" not in setup_state.staged


async def test_the_manual_path_reaches_the_document_and_then_finish(
    setup_client, setup_state, monkeypatch, database_url
):
    """The other half: arriving manually reaches the SAME configuration submit,
    and the wizard can then be finished.

    Before this, the document had exactly one writer and it sat behind a
    COMPLETED plex.tv sign-in, so a deployment that could not complete one was
    stopped forever: ``config_source`` stayed null, which is what
    ``_unmet_step`` answers ``STEP_CONFIG`` for and what the page disables
    Continue on. Nothing about the submit itself is new -- one submit path, two
    ways to arrive at it.
    """
    _install(monkeypatch, authorised=True)
    monkeypatch.setattr(setup_api, "database_answers", _answering(True))
    monkeypatch.setattr(setup_api.os, "execv", lambda path, argv: None)
    token = await _authenticate(setup_client)

    # No PIN minted and nothing staged: the sign-in never happened.
    libraries = await setup_client.post(
        "/api/setup/plex/libraries",
        json={"base_url": PLEX_BASE, "credential_value": ACCOUNT_TOKEN},
        headers=_headers(token),
    )
    assert libraries.status_code == 200, libraries.text
    assert setup_state.plex_pin_id is None

    await setup_client.post(
        "/api/setup/config",
        json={"plex_url": PLEX_BASE, "excluded_libraries": ["Photos"]},
        headers=_headers(token),
    )
    progress = await setup_client.get("/api/setup/progress", headers=_headers(token))
    assert progress.json()["config_source"] == "staged"

    await setup_client.post(
        "/api/setup/database", json={"url": database_url}, headers=_headers(token)
    )
    await setup_client.post(
        "/api/setup/providers",
        json={
            "values": {
                name: "value"
                for name in (*HARD, "AUTOPOSTER_PLEX_TOKEN")
                if name not in _NOT_PASTED
            }
        },
        headers=_headers(token),
    )

    response = await setup_client.post("/api/setup/finish", headers=_headers(token))

    assert response.status_code == 200, response.text
    assert response.json() == {"restarting": True}


# --- the identifier through the routes, and the token a typed address may use


async def test_the_client_identifier_is_one_string_across_every_route_that_calls_plex(
    setup_client, monkeypatch
):
    """Through the REAL routes, and not the module beneath them.

    plex.tv 404s a poll whose ``X-Plex-Client-Identifier`` differs from its
    mint's, so a route that minted a fresh one per call is a sign-in that can
    never complete -- and this branch shipped exactly that wiring broken
    twice. The module-level test above passes one literal in and reads the same
    literal out, which cannot see a route that forgot to persist it; this walks
    mint, poll, servers and libraries the way the page does and asserts the
    four requests agreed.

    The identifier itself is never asserted against a literal and never
    printed: it names THIS DEPLOYMENT as a device on the operator's account.
    What is asserted is that there is exactly one of it.
    """
    seen = _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)

    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))
    await setup_client.get("/api/setup/plex/pin", headers=_headers(token))
    servers = await setup_client.get("/api/setup/plex/servers", headers=_headers(token))
    libraries = await setup_client.post(
        "/api/setup/plex/libraries",
        json={"base_url": PLEX_BASE, "credential_value": ACCOUNT_TOKEN},
        headers=_headers(token),
    )

    assert servers.status_code == 200, servers.text
    assert libraries.status_code == 200, libraries.text
    assert [request.url.path for request in seen] == [
        "/api/v2/pins",
        f"/api/v2/pins/{PIN_ID}",
        "/api/v2/resources",
        "/library/sections",
    ]
    identifiers = {request.headers.get("X-Plex-Client-Identifier") for request in seen}
    assert None not in identifiers
    assert len(identifiers) == 1


async def test_the_libraries_route_never_reads_with_a_token_the_resolver_supplied(
    setup_client, monkeypatch
):
    """The address is the caller's, so the token must be the caller's too.

    Setup mode is entered when ONE hard secret fails to resolve, so a pod in it
    still resolves ``AUTOPOSTER_PLEX_TOKEN`` from its environment -- and
    reading a caller-typed address with THAT value sends the deployment's live
    Plex token to a host the request named. Typed or staged, or the fixed
    sentence and no call at all.
    """
    monkeypatch.setenv("AUTOPOSTER_PLEX_TOKEN", ACCOUNT_TOKEN)
    seen = _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/plex/libraries", json={"base_url": PLEX_BASE}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.CHECK_NEEDS_A_TYPED_CREDENTIAL
    assert seen == []
    assert ACCOUNT_TOKEN not in response.text


async def test_the_libraries_route_reads_with_the_token_the_sign_in_staged(
    setup_client, monkeypatch
):
    """The pick-list arrival, which the rule above must not break: the poll
    stages the account token under ``AUTOPOSTER_PLEX_TOKEN``, and a value this
    wizard was handed is the caller's own."""
    seen = _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))
    await setup_client.get("/api/setup/plex/pin", headers=_headers(token))

    response = await setup_client.post(
        "/api/setup/plex/libraries", json={"base_url": PLEX_BASE}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    assert [request.url.path for request in seen][-1] == "/library/sections"
