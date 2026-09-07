"""The Plex PIN flow, the owned-server filter, and the both-names ruling.

Two things here are unusual and both are deliberate. First, the PIN CODE and
the app.plex.tv auth URL ARE served -- a documented row 213 exception (facts
C6): they are minted by plex.tv, are public by design, and the flow is
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

import logging

import httpx
import pytest
import pytest_asyncio

from autoposter import boot
from autoposter.api import setup as setup_api
from autoposter.api import setup_plex

# The autouse env isolation, the hard-name tuple and the two token helpers are
# the wizard suite's, imported rather than copied. The three FIXTURES below are
# declared here instead of imported, following `test_api_setup_check.py`: a
# fixture imported into this namespace and then named as a test parameter is an
# F811 redefinition, and this repository imports constants across test modules
# and not fixtures.
from tests.test_api_setup import HARD, _authenticate, _headers, isolated_state  # noqa: F401

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

    servers = await setup_plex.owned_servers(ACCOUNT_TOKEN, transport=transport)

    assert [s["client_identifier"] for s in servers] == [OWNED_ID]


async def test_no_server_entry_carries_a_token():
    transport, _seen = _plex_transport(authorised=True)

    servers = await setup_plex.owned_servers(ACCOUNT_TOKEN, transport=transport)

    assert ACCOUNT_TOKEN not in repr(servers)
    assert all("accessToken" not in server for server in servers)


async def test_connections_are_ordered_local_first():
    """Probe 1: every entry has exactly one `local: true` https connection on
    32400, and that is the one a pod inside the cluster should use."""
    transport, _seen = _plex_transport(authorised=True)

    servers = await setup_plex.owned_servers(ACCOUNT_TOKEN, transport=transport)

    assert [c["local"] for c in servers[0]["connections"]] == [True, False]


async def test_library_sections_are_key_title_and_type_and_nothing_else():
    transport, _seen = _plex_transport(authorised=True)

    sections = await setup_plex.library_sections(PLEX_BASE, ACCOUNT_TOKEN, transport=transport)

    assert sections == [
        {"key": "1", "title": "Movies", "type": "movie"},
        {"key": "2", "title": "TV", "type": "show"},
        {"key": "3", "title": "Photos", "type": "photo"},
    ]


async def test_no_plex_call_logs_the_token_the_pin_or_the_identifier(caplog):
    """This module emits no log line at all, and that is the whole claim.

    `caplog.set_level(logging.DEBUG)` deliberately DEFEATS the clamp
    `boot.main` installs, so httpx's own INFO line -- one per request, carrying
    the FULL url, and the poll url carries the PIN id -- is visible from here.
    Measured: that line is the only place any of these four strings could
    appear, and only the PIN id ever reaches it. Asserting `str(PIN_ID) not in`
    the whole capture would therefore be asserting the clamp's effect in a test
    that has just switched the clamp off; the clamp is pinned below instead,
    and this test asserts what this module controls -- that it logs nothing.
    """
    caplog.set_level(logging.DEBUG)
    transport, _seen = _plex_transport(authorised=True)

    await setup_plex.mint_pin("row-121-client-id", transport=transport)
    await setup_plex.poll_pin(PIN_ID, "row-121-client-id", transport=transport)
    await setup_plex.owned_servers(ACCOUNT_TOKEN, transport=transport)

    ours = [record for record in caplog.records if not record.name.startswith("httpx")]
    assert ours == []
    # The token, the code and the identifier reach no line at all -- httpx's
    # included. None of the three is ever put in a url; all three travel as
    # headers, which httpx does not log.
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert ACCOUNT_TOKEN not in text
    assert PIN_CODE not in text
    assert "row-121-client-id" not in text


async def test_boot_clamps_httpx_before_this_flow_can_make_a_request(monkeypatch, tmp_path):
    """The one residual this row creates, pinned where it is actually fixed.

    httpx logs one INFO line per request with the FULL url, and the poll url is
    the first url in this service to carry a value that identifies a
    credential-bearing exchange -- the PIN id. C6's "nothing identifying the
    PIN is logged" is kept by `boot.main`'s clamp (boot.py:151) and by nothing
    in `setup_plex`, and until this row nothing pinned that clamp: deleting the
    line would have left every suite green.
    """
    logging.getLogger("httpx").setLevel(logging.INFO)
    for name in HARD:
        monkeypatch.setenv(name, "x")
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
    """Staged, never persisted: the identifier names this sign-in attempt, not
    this deployment, and the poll 404s without the same one."""
    _install(monkeypatch, authorised=False)
    token = await _authenticate(setup_client)

    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))

    assert setup_state.plex_client_identifier is not None
    assert setup_state.plex_pin_id == PIN_ID


async def test_a_second_sign_in_never_inherits_the_abandoned_identifier(
    setup_client, setup_state, monkeypatch
):
    """A fresh identifier per mint. Re-using one would let an abandoned PIN's
    approval land as this attempt's, and the identifier is the only thing
    binding a poll to the mint that made it."""
    _install(monkeypatch, authorised=False)
    token = await _authenticate(setup_client)
    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))
    first = setup_state.plex_client_identifier

    await setup_client.post("/api/setup/plex/pin", headers=_headers(token))

    assert setup_state.plex_client_identifier != first


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
    to complete rather than a credential to supply."""
    _install(monkeypatch, authorised=True)
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/plex/libraries", json={"base_url": PLEX_BASE}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NO_PLEX_ACCOUNT_TOKEN


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
    # The PIN id reaches httpx's own INFO line and nothing else; the clamp that
    # keeps that out of a deployment's log is pinned above, and this assertion
    # is about the four routes' own lines.
    ours = [record for record in caplog.records if not record.name.startswith("httpx")]
    assert str(PIN_ID) not in "\n".join(record.getMessage() for record in ours)
