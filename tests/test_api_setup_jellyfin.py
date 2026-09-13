"""The wizard's media-server step, from the Jellyfin side.

Three surfaces and one walk. The surfaces are the check table's tenth entry
(``/System/Info`` behind the ``MediaBrowser`` header), the library read the
tick-list is built from (``/Library/VirtualFolders``), and the config step now
that a Plex address is no longer unconditional. The walk is the one this task
exists for: a deployment that has no Plex at all finishing the wizard through
the REAL routes, because every one of those three can be correct on its own and
still leave the step gate, the presence map and ``boot.is_configured``
disagreeing about what "configured" means.

Endpoint citations: docs/reference/2026-09-jellyfin-openapi-12.md.
"""

import httpx
import pytest
import pytest_asyncio

from autoposter import boot
from autoposter.api import setup as setup_api
from autoposter.api import setup_checks, setup_jellyfin
from autoposter.config import state as state_module

# The neighbouring suite's env isolation and token helpers, imported rather
# than copied for tests/test_api_setup_check.py's reason: the list of names
# `isolated_state` clears is the part that drifts. The two fixtures below are
# declared here, because a fixture imported into this namespace and then named
# as a test parameter is an F811 redefinition.
from test_api_setup import (  # noqa: F401
    EXAMPLE,
    FAKE_DB_URL,
    PLEX_URL,
    PUBLIC_URL,
    _answering,
    _authenticate,
    _headers,
    isolated_state,
)

JELLYFIN_URL = "https://jf.example"
JELLYFIN_KEY = "row-267-jellyfin-key-4a1e"


@pytest.fixture
def setup_app():
    return setup_api.build_setup_app()


@pytest_asyncio.fixture
async def setup_client(setup_app):
    transport = httpx.ASGITransport(app=setup_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# --- the check table ---------------------------------------------------------


def test_the_check_table_has_jellyfin_as_a_typed_address_system():
    check = setup_checks.CHECK_SYSTEMS["jellyfin"]

    assert check.host is None and check.path == "/System/Info"
    assert check.auth == "mediabrowser"
    assert check.credential == "AUTOPOSTER_JELLYFIN_APIKEY"


async def test_the_probe_sends_the_mediabrowser_header_and_reads_the_status():
    """The header form is the one thing about this subject that cannot be
    guessed: `X-Emby-Token` is refused with a 401 on 12.0, and the value
    format is `MediaBrowser Token="..."`. It is built by jellyfin/client.py so
    that this probe and the running application spell it once."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"Version": "12.0.0"})

    outcome = await setup_checks.run_check(
        "jellyfin",
        JELLYFIN_URL,
        {"AUTOPOSTER_JELLYFIN_APIKEY": JELLYFIN_KEY},
        transport=httpx.MockTransport(handler),
    )

    assert outcome.ok is True and outcome.failure is None
    assert seen["path"] == "/System/Info"
    assert seen["auth"].startswith('MediaBrowser Token="%s"' % JELLYFIN_KEY)


async def test_a_401_is_refused_not_unreachable():
    """The distinction the pane renders differently: the address answered and
    the key was rejected, which is not "Jellyfin could not be reached"."""
    outcome = await setup_checks.run_check(
        "jellyfin",
        JELLYFIN_URL,
        {"AUTOPOSTER_JELLYFIN_APIKEY": "wrong"},
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )

    assert outcome.ok is False and outcome.refused is True and outcome.failure is None


# --- the library read --------------------------------------------------------


async def test_library_list_names_ids_and_types_only():
    """`Locations` is the server's own filesystem paths, and the tick-list has
    no use for them. Built field by field, so a folder entry that grows a field
    on a later Jellyfin cannot grow one in what this wizard serves."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/Library/VirtualFolders"
        return httpx.Response(
            200,
            json=[
                {
                    "Name": "Movies",
                    "CollectionType": "movies",
                    "ItemId": "lib1",
                    "Locations": ["/mnt/movies"],
                }
            ],
        )

    libraries = await setup_jellyfin.library_list(
        JELLYFIN_URL, JELLYFIN_KEY, transport=httpx.MockTransport(handler)
    )

    assert libraries == [{"id": "lib1", "name": "Movies", "type": "movies"}]


async def test_the_libraries_route_reads_with_a_typed_key_and_refuses_without_one(
    setup_client, monkeypatch
):
    """Review I1's rule on the second server: the caller named the host, so the
    key that goes to it is the caller's own -- typed into this request, or one
    this wizard staged. Never the resolver's."""
    token = await _authenticate(setup_client)

    async def one_library(base_url, api_key, transport=None):
        assert base_url == JELLYFIN_URL and api_key == JELLYFIN_KEY
        return [{"id": "lib1", "name": "Movies", "type": "movies"}]

    monkeypatch.setattr(setup_jellyfin, "library_list", one_library)

    refused = await setup_client.post(
        "/api/setup/jellyfin/libraries",
        json={"base_url": JELLYFIN_URL},
        headers=_headers(token),
    )
    answered = await setup_client.post(
        "/api/setup/jellyfin/libraries",
        json={"base_url": JELLYFIN_URL, "credential_value": JELLYFIN_KEY},
        headers=_headers(token),
    )

    assert refused.status_code == 400
    assert refused.json()["detail"] == setup_api.CHECK_NEEDS_A_TYPED_CREDENTIAL
    assert answered.status_code == 200, answered.text
    assert answered.json() == {"libraries": [{"id": "lib1", "name": "Movies", "type": "movies"}]}
    assert JELLYFIN_KEY not in answered.text


# --- the config step, with no Plex address --------------------------------


async def test_the_config_step_refuses_a_submit_that_names_no_server(setup_client):
    """The step gate said as a REFUSAL rather than only as a progress line: a
    document with neither server is one `boot.is_configured` rejects, and the
    wizard would then be finishable into a pod that exits non-zero."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config", json={}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.STEP_SERVERS


async def test_a_jellyfin_only_document_does_not_inherit_the_example_plex_block(
    setup_client, setup_app
):
    """The example ships a `plex:` block with a placeholder address. Left in,
    `missing_server_setup` reads the deployment as a Plex one missing its
    token -- forever, because no wizard is served for that shape."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config",
        json={"jellyfin_url": JELLYFIN_URL, "jellyfin_excluded_libraries": ["Photos"]},
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    document = setup_app.state.setup.config_document
    assert document["jellyfin"] == {"url": JELLYFIN_URL, "excluded_libraries": ["Photos"]}
    assert "plex" not in document


async def test_a_malformed_jellyfin_address_is_refused_with_the_jellyfin_sentence(
    setup_client,
):
    """The page renders `detail` beside the field that was refused, so the
    sentence has to be about the address the operator actually typed. The
    shared guard refuses userinfo -- the Plex field's own pinned case -- and
    this is the other server's wording of the same refusal."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config",
        json={"jellyfin_url": "http://user:pass@jf.example"},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.JELLYFIN_URL_NOT_AN_ADDRESS
    assert "Plex" not in response.text


async def test_one_submit_can_configure_both_servers(setup_client, setup_app):
    """The third shape spec 8 allows, and the one the pane saves when both
    cards are filled in before Save."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/config",
        json={
            "plex_url": PLEX_URL,
            "excluded_libraries": ["Photos"],
            "jellyfin_url": JELLYFIN_URL,
            "jellyfin_excluded_libraries": ["Home Videos"],
        },
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    document = setup_app.state.setup.config_document
    assert document["plex"]["url"] == PLEX_URL
    assert document["plex"]["excluded_libraries"] == ["Photos"]
    assert document["jellyfin"] == {
        "url": JELLYFIN_URL, "excluded_libraries": ["Home Videos"],
    }


@pytest.mark.parametrize("jellyfin_first", [False, True])
async def test_saving_one_server_card_never_destroys_the_other(
    setup_client, setup_app, jellyfin_first
):
    """The two cards on the servers step may be saved one at a time, in either
    order, and each save rebuilds the document FROM THE EXAMPLE -- so a server
    this body does not name has to be restored from what this session already
    staged for it. Dropped instead, the operator's first card vanished with a
    200 and no sentence, which is the shape spec 8 describes for the pane.
    """
    token = await _authenticate(setup_client)
    plex = {"plex_url": PLEX_URL, "excluded_libraries": ["Photos"]}
    jellyfin = {
        "jellyfin_url": JELLYFIN_URL, "jellyfin_excluded_libraries": ["Home Videos"],
    }
    first, second = (jellyfin, plex) if jellyfin_first else (plex, jellyfin)

    assert (
        await setup_client.post("/api/setup/config", json=first, headers=_headers(token))
    ).status_code == 200
    response = await setup_client.post(
        "/api/setup/config", json=second, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    document = setup_app.state.setup.config_document
    assert document["plex"]["url"] == PLEX_URL
    assert document["plex"]["excluded_libraries"] == ["Photos"]
    assert document["jellyfin"] == {
        "url": JELLYFIN_URL, "excluded_libraries": ["Home Videos"],
    }


async def test_a_library_read_that_fails_is_a_class_name_and_never_the_error_text(
    setup_client, monkeypatch
):
    """`/plex/libraries`' arm on the second server: httpx embeds the whole url
    -- and an operator address -- in its own messages, so the failure reaches
    the page as the exception CLASS and a fixed sentence."""
    token = await _authenticate(setup_client)

    async def explode(base_url, api_key, transport=None):
        raise httpx.ConnectError("connection refused to " + JELLYFIN_URL)

    monkeypatch.setattr(setup_jellyfin, "library_list", explode)

    response = await setup_client.post(
        "/api/setup/jellyfin/libraries",
        json={"base_url": JELLYFIN_URL, "credential_value": JELLYFIN_KEY},
        headers=_headers(token),
    )

    assert response.status_code == 502
    assert response.json()["detail"] == setup_api.CHECK_UNREACHABLE.format(
        system="Jellyfin", failure="ConnectError"
    )
    assert JELLYFIN_URL not in response.text and JELLYFIN_KEY not in response.text


async def test_the_servers_line_reads_a_document_the_deployment_already_has(
    setup_client, monkeypatch, tmp_path
):
    """The other branch of `_document_for_boot`, and a real deployment shape:
    a mounted document names Plex, its token does not resolve, and the pod is
    in setup mode over some other credential. `configured` has to come off that
    document -- the wizard staged nothing -- or the servers step would report
    itself unstarted and the finish step would refuse it."""
    mounted = tmp_path / "config" / "autoposter.yaml"
    mounted.parent.mkdir(parents=True, exist_ok=True)
    mounted.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(mounted))
    token = await _authenticate(setup_client)

    progress = (await setup_client.get("/api/setup/progress", headers=_headers(token))).json()

    assert progress["servers"]["plex"] == {
        "configured": True, "credential": False, "checked": False,
    }
    assert progress["servers"]["jellyfin"]["configured"] is False
    # The address itself is never in the body, on either branch.
    assert "plex-host" not in (await setup_client.get(
        "/api/setup/progress", headers=_headers(token)
    )).text


# --- the walk ----------------------------------------------------------------


async def test_progress_carries_servers_and_the_wizard_finishes_jellyfin_only(
    setup_app, setup_client, monkeypatch
):
    """A Jellyfin-only walk through the REAL routes (spec 8).

    The property is the one no unit test above can hold on its own: the step
    gate, the presence map and `boot.is_configured` agree that a deployment
    with one media server and no Plex token at all is complete.
    """
    token = await _authenticate(setup_client)
    headers = _headers(token)
    monkeypatch.setattr(setup_api, "database_answers", _answering(True))
    assert (
        await setup_client.post(
            "/api/setup/public-url", json={"url": PUBLIC_URL}, headers=headers
        )
    ).status_code == 200
    assert (
        await setup_client.post(
            "/api/setup/database", json={"url": FAKE_DB_URL}, headers=headers
        )
    ).status_code == 200

    # The provider step: every provider name, and the Jellyfin key -- which is
    # NOT one of them, and is staged by the same submit.
    values = {
        name: "value"
        for name in setup_api._PROVIDER_ENV
        if name != setup_api._GENERATED_SECRET
    }
    values["AUTOPOSTER_JELLYFIN_APIKEY"] = JELLYFIN_KEY
    assert (
        await setup_client.post(
            "/api/setup/providers", json={"values": values}, headers=headers
        )
    ).status_code == 200

    async def fake_check(system, base_url, credentials, transport=None):
        assert system == "jellyfin" and base_url == JELLYFIN_URL
        # The typed-address rule: one name in the map, and it is the one the
        # operator just typed into this wizard.
        assert credentials == {"AUTOPOSTER_JELLYFIN_APIKEY": JELLYFIN_KEY}
        return setup_checks.CheckOutcome(ok=True, refused=False, failure=None)

    monkeypatch.setattr(setup_checks, "run_check", fake_check)
    checked = await setup_client.post(
        "/api/setup/check",
        json={"system": "jellyfin", "base_url": JELLYFIN_URL},
        headers=headers,
    )
    assert checked.json()["ok"] is True

    assert (
        await setup_client.post(
            "/api/setup/config",
            json={"jellyfin_url": JELLYFIN_URL, "jellyfin_excluded_libraries": ["Photos"]},
            headers=headers,
        )
    ).status_code == 200

    progress = (await setup_client.get("/api/setup/progress", headers=headers)).json()
    assert set(progress) == {
        "password", "database", "database_source", "providers", "required", "config",
        "config_source", "public_url", "checked_systems", "servers",
    }
    assert progress["servers"]["jellyfin"] == {
        "configured": True, "credential": True, "checked": True,
    }
    assert progress["servers"]["plex"] == {
        "configured": False, "credential": False, "checked": False,
    }
    assert progress["required"] == []
    # The server credentials are reported by `servers` and by nothing else.
    assert "AUTOPOSTER_PLEX_TOKEN" not in progress["providers"]
    assert "AUTOPOSTER_JELLYFIN_APIKEY" not in progress["providers"]

    monkeypatch.setattr(setup_api.os, "execv", lambda path, argv: None)
    response = await setup_client.post("/api/setup/finish", headers=headers)

    assert response.status_code == 200, response.text
    written = state_module.read_secrets_file(state_module.secrets_file_path())
    assert written["AUTOPOSTER_JELLYFIN_APIKEY"] == JELLYFIN_KEY
    assert "AUTOPOSTER_PLEX_TOKEN" not in written
    document = setup_api.read_config_document(state_module.state_config_path())
    assert document["jellyfin"] == {"url": JELLYFIN_URL, "excluded_libraries": ["Photos"]}
    assert "plex" not in document
    # The gate the next boot will run, over what was actually persisted.
    assert boot.is_configured(setup_api.resolve_secret_values()) is True
    assert JELLYFIN_KEY not in response.text
    assert setup_app.state.setup.token is not None
