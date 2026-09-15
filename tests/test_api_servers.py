"""The Servers tab's backend (spec section 5).

The listing, the live probe and the live library read. The one security rule
this module inherits from the wizard has its own test: a credential this
deployment holds never travels to an address a request named.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api import secrets_api
from autoposter.api import servers as servers_api
from autoposter.api.auth import hash_password
from autoposter.api.setup import PUBLIC_URL_NOT_AN_ADDRESS
from autoposter.app import create_app
from autoposter.config import secret_store
from autoposter.config.loader import build_config
from autoposter.config.overrides import (
    load_overrides_document, load_store, seed_store, store_meta, write_store,
)
from autoposter.config.schema import (
    ENVIRONMENT_SECRET_NAMES_ENV,
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
    Secrets,
)
from autoposter.db.models import EventLog
from autoposter.config.state import STATE_DIR_ENV
from autoposter.jellyfin.health import JellyfinHealth
from autoposter.plex.health import PlexHealth

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    monkeypatch.delenv("AUTOPOSTER_JELLYFIN_APIKEY", raising=False)
    monkeypatch.setenv("AUTOPOSTER_PLEX_TOKEN", "plex-token")
    return tmp_path


def _plex_only_document() -> dict:
    """The shipped example, with one media server configured and one not.

    The example's own ``plex.url`` is the placeholder an operator replaces
    (``https://<plex-host>``), so a real address is written over it here: these
    tests assert on the address a probe is sent to, and a placeholder host
    would make "the stored address was used" unreadable.
    """
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document.pop("jellyfin", None)
    document["plex"]["url"] = "http://plex:32400"
    return document


@pytest_asyncio.fixture
async def app(session_factory):
    document = _plex_only_document()
    application = create_app(
        build_config(document),
        session_factory,
        Secrets(
            database_url="postgresql+asyncpg://unused",
            plex_token="plex-token", tmdb_token="x", tvdb_apikey="x",
            fanart_apikey="x", webhook_secret="x",
            admin_password_hash=hash_password(PASSWORD),
        ),
    )
    async with session_factory() as session:
        await seed_store(session, document)
        await session.commit()
    return application


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def test_the_listing_names_every_server_the_schema_knows(client, auth_headers):
    body = (await client.get("/api/servers", headers=auth_headers)).json()
    rows = {row["name"]: row for row in body["servers"]}
    assert set(rows) == {"plex", "jellyfin"}
    assert rows["plex"]["configured"] is True
    assert rows["plex"]["url"] == "http://plex:32400"
    assert rows["plex"]["excluded_libraries"] == ["Muskarit", "Photos"]
    assert rows["plex"]["credential_source"] == "environment"
    assert rows["jellyfin"]["configured"] is False
    assert rows["jellyfin"]["url"] is None
    assert rows["jellyfin"]["excluded_libraries"] == []
    assert rows["jellyfin"]["credential_source"] == "unset"


async def test_the_listing_carries_no_credential(client, auth_headers):
    response = await client.get("/api/servers", headers=auth_headers)
    assert "plex-token" not in response.text


async def test_a_server_with_no_poller_is_not_reported_as_down(client, auth_headers):
    """A test application and a replica that has not run its lifespan both have
    no poller; the card must render "not checked yet", not "unhealthy"."""
    body = (await client.get("/api/servers", headers=auth_headers)).json()
    rows = {row["name"]: row for row in body["servers"]}
    assert rows["plex"]["health"] == {"ok": None, "detail": None, "checked_at": None}


async def test_a_healthy_poller_answers_its_own_last_success(app, client, auth_headers):
    checked = datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
    app.state.server_health = {
        "plex": SimpleNamespace(healthy=True, last_error=None, last_success=checked)
    }
    body = (await client.get("/api/servers", headers=auth_headers)).json()
    rows = {row["name"]: row for row in body["servers"]}
    assert rows["plex"]["health"] == {
        "ok": True, "detail": None, "checked_at": checked.isoformat()
    }


async def test_an_unhealthy_poller_answers_its_marker_and_no_stale_timestamp(
    app, client, auth_headers
):
    """``last_success`` is not when the poller last LOOKED, so during an outage
    the card is told nothing rather than a minute that belongs to another event.
    """
    app.state.server_health = {
        "plex": SimpleNamespace(
            healthy=False,
            last_error="ConnectError",
            last_success=datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc),
        )
    }
    body = (await client.get("/api/servers", headers=auth_headers)).json()
    rows = {row["name"]: row for row in body["servers"]}
    assert rows["plex"]["health"] == {
        "ok": False, "detail": "ConnectError", "checked_at": None
    }


async def test_the_health_shape_is_read_off_the_real_pollers(app, client, auth_headers):
    """The three keys against ``PlexHealth`` and ``JellyfinHealth`` themselves,
    not a stand-in: every field is read through ``getattr`` with a default, so a
    renamed attribute would leave every card reading "not checked yet" forever
    with the rest of this file still green. Each poller is driven through its
    own ``check_liveness``, so the attributes are set by the class under test.
    """
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as http:
        healthy = PlexHealth("http://plex:32400", "plex-token", http)
        await healthy.check_liveness()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as http:
        unhealthy = JellyfinHealth("http://jf:8096", "jf-key", http, version="test")
        await unhealthy.check_liveness()
    app.state.server_health = {"plex": healthy, "jellyfin": unhealthy}

    rows = {
        row["name"]: row
        for row in (await client.get("/api/servers", headers=auth_headers)).json()["servers"]
    }
    assert rows["plex"]["health"] == {
        "ok": True, "detail": None, "checked_at": healthy.last_success.isoformat()
    }
    assert rows["jellyfin"]["health"] == {
        "ok": False, "detail": "status 503", "checked_at": None
    }


async def test_a_check_with_a_typed_address_and_a_typed_credential_runs(
    client, auth_headers, monkeypatch
):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=json.dumps({"MediaContainer": {}}).encode())

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    response = await client.post(
        "/api/servers/plex/check",
        json={"url": "http://elsewhere:32400", "credential_value": "typed"},
        headers=auth_headers,
    )
    assert response.json()["ok"] is True
    assert seen[0].headers["X-Plex-Token"] == "typed"


async def test_a_typed_address_without_a_typed_credential_is_refused(
    client, auth_headers
):
    """The wizard's rule, restated: a credential this deployment holds never
    travels to an address a request named."""
    response = await client.post(
        "/api/servers/plex/check",
        json={"url": "http://attacker.example", "credential_value": None},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == servers_api.TYPED_ADDRESS_NEEDS_A_TYPED_CREDENTIAL


async def test_a_typed_address_that_is_not_an_address_is_refused(client, auth_headers):
    """The shared guard's own sentence, not a second spelling of it."""
    response = await client.post(
        "/api/servers/plex/check",
        json={"url": "http://user:pass@plex:32400", "credential_value": "typed"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "username or password" in response.json()["detail"]


async def test_a_check_with_neither_uses_what_is_stored(
    client, auth_headers, monkeypatch
):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=json.dumps({"MediaContainer": {}}).encode())

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    response = await client.post(
        "/api/servers/plex/check", json={}, headers=auth_headers
    )
    assert response.json()["ok"] is True
    assert str(seen[0].url).startswith("http://plex:32400")
    assert seen[0].headers["X-Plex-Token"] == "plex-token"


async def test_a_typed_credential_alone_is_checked_against_the_stored_address(
    client, auth_headers, monkeypatch
):
    """Replacing a credential on a card: the address is this deployment's own,
    so there is nowhere for the typed value to leak to."""
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=json.dumps({"MediaContainer": {}}).encode())

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    response = await client.post(
        "/api/servers/plex/check",
        json={"credential_value": "a new token"},
        headers=auth_headers,
    )
    assert response.json()["ok"] is True
    assert str(seen[0].url).startswith("http://plex:32400")
    assert seen[0].headers["X-Plex-Token"] == "a new token"


async def test_an_answering_server_is_reported_with_the_answering_sentence(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(
        servers_api,
        "_transport",
        lambda: httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=json.dumps({"MediaContainer": {}}).encode()
            )
        ),
    )
    body = (
        await client.post("/api/servers/plex/check", json={}, headers=auth_headers)
    ).json()
    assert body == {
        "ok": True,
        "refused": False,
        "failure": None,
        "version": None,
        "detail": "Plex answered.",
    }


async def test_a_check_serves_the_version_each_server_states(
    client, auth_headers, monkeypatch
):
    """Spec section 5's pill reads "connected, with the version". Both servers,
    because they state it on different paths and under different keys -- Plex
    on ``/identity`` and Jellyfin in the same ``/System/Info`` the probe already
    asks."""

    def handler(request):
        if request.url.path == "/identity":
            return httpx.Response(
                200,
                content=json.dumps(
                    {"MediaContainer": {"version": "1.41.2.9200-abcdef"}}
                ).encode(),
            )
        if request.url.path == "/System/Info":
            return httpx.Response(
                200, content=json.dumps({"Version": "10.11.0"}).encode()
            )
        return httpx.Response(
            200, content=json.dumps({"MediaContainer": {}}).encode()
        )

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    plex = (
        await client.post("/api/servers/plex/check", json={}, headers=auth_headers)
    ).json()
    assert plex["ok"] is True
    assert plex["version"] == "1.41.2.9200-abcdef"

    jellyfin = (
        await client.post(
            "/api/servers/jellyfin/check",
            json={"url": "http://jf:8096", "credential_value": "jf-key"},
            headers=auth_headers,
        )
    ).json()
    assert jellyfin["ok"] is True
    assert jellyfin["version"] == "10.11.0"


async def test_a_version_that_cannot_be_read_leaves_the_server_connected(
    client, auth_headers, monkeypatch
):
    """The version is a second question asked of a server that already
    answered, so nothing it finds may change what the check said: a card that
    said "connected" does not stop saying it because a version is missing."""

    def handler(request):
        if request.url.path == "/identity":
            return httpx.Response(500, content=b"secret internal detail")
        return httpx.Response(
            200, content=json.dumps({"MediaContainer": {}}).encode()
        )

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    response = await client.post(
        "/api/servers/plex/check", json={}, headers=auth_headers
    )
    assert response.json()["ok"] is True
    assert response.json()["version"] is None
    assert "secret internal detail" not in response.text


async def test_a_check_response_carries_neither_the_credential_nor_the_address(
    client, auth_headers, monkeypatch
):
    """The property the module leads with, asserted at the real entry point on
    the one body that had both to hand."""
    monkeypatch.setattr(
        servers_api,
        "_transport",
        lambda: httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=json.dumps({"MediaContainer": {}}).encode()
            )
        ),
    )
    response = await client.post(
        "/api/servers/plex/check",
        json={"url": "http://elsewhere:32400", "credential_value": "typed-secret"},
        headers=auth_headers,
    )
    assert "typed-secret" not in response.text
    assert "plex-token" not in response.text
    assert "elsewhere" not in response.text


async def test_a_failing_status_is_reported_as_a_number_not_the_servers_text(
    client, auth_headers, monkeypatch
):
    """The third shape of ``failure``: a status marker rather than an exception
    class, and the server's own body nowhere in it."""
    monkeypatch.setattr(
        servers_api,
        "_transport",
        lambda: httpx.MockTransport(
            lambda request: httpx.Response(500, content=b"secret internal detail")
        ),
    )
    body = (
        await client.post("/api/servers/plex/check", json={}, headers=auth_headers)
    ).json()
    assert body["ok"] is False and body["refused"] is False
    assert body["failure"] == "HTTPStatus500"
    assert body["detail"] == "Plex could not be reached (HTTPStatus500)."


async def test_a_refused_credential_is_reported_as_refused(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(
        servers_api,
        "_transport",
        lambda: httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    body = (
        await client.post("/api/servers/plex/check", json={}, headers=auth_headers)
    ).json()
    assert body["ok"] is False and body["refused"] is True
    assert body["detail"] == "Plex refused the credential."


async def test_an_unreachable_server_names_a_class_and_no_address(
    client, auth_headers, monkeypatch
):
    def boom(request):
        raise httpx.ConnectError("connection refused to http://plex:32400/library")

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(boom)
    )
    body = (
        await client.post("/api/servers/plex/check", json={}, headers=auth_headers)
    ).json()
    assert body["ok"] is False and body["refused"] is False
    assert body["failure"] == "ConnectError"
    assert "http://" not in body["detail"]


async def test_a_saved_address_does_not_redirect_the_held_credential(
    client, auth_headers, monkeypatch
):
    """A save hot-swaps the running config without a restart, so "configured"
    and "where this credential already goes" are two different strings between
    a save and the restart that applies it. The held credential follows the
    second one; the card shows the first.
    """
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=json.dumps({"MediaContainer": {}}).encode())

    document = _plex_only_document()
    document["plex"]["url"] = "http://elsewhere.example"
    saved = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": document}
    )
    assert saved.status_code == 200

    rows = {
        row["name"]: row
        for row in (await client.get("/api/servers", headers=auth_headers)).json()["servers"]
    }
    assert rows["plex"]["url"] == "http://elsewhere.example", (
        "the card shows what is saved"
    )

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    response = await client.post(
        "/api/servers/plex/check", json={}, headers=auth_headers
    )
    assert response.json()["ok"] is True
    assert str(seen[0].url).startswith("http://plex:32400"), (
        "the held token goes to the address this deployment booted on, not to "
        "one a save named a moment ago"
    )
    assert "elsewhere.example" not in str(seen[0].url)


async def test_a_saved_address_is_still_checkable_by_typing_its_credential(
    client, auth_headers, monkeypatch
):
    """The other half: an operator who has just typed a new address checks it
    the way anyone naming an address does -- with the credential to use."""
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=json.dumps({"MediaContainer": {}}).encode())

    document = _plex_only_document()
    document["plex"]["url"] = "http://elsewhere.example"
    await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": document}
    )
    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    response = await client.post(
        "/api/servers/plex/check",
        json={"url": "http://elsewhere.example", "credential_value": "typed"},
        headers=auth_headers,
    )
    assert response.json()["ok"] is True
    assert str(seen[0].url).startswith("http://elsewhere.example")
    assert seen[0].headers["X-Plex-Token"] == "typed"


async def test_a_stored_address_is_guarded_like_a_typed_one(
    app, client, auth_headers, monkeypatch
):
    """``PlexConfig.url`` is a bare ``str`` with no validator, so a document can
    carry what the typed path refuses. It is refused by the same guard, with
    its own sentence: the typed one is about a value in this request, and an
    operator who typed nothing would read it as being about their own input.
    Neither names the value.
    """
    sent: list[httpx.Request] = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, content=json.dumps({"MediaContainer": {}}).encode())

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    app.state.booted_config = app.state.booted_config.model_copy(
        update={
            "plex": app.state.booted_config.plex.model_copy(
                update={"url": "http://user:pass@plex:32400"}
            )
        }
    )

    response = await client.post(
        "/api/servers/plex/check", json={}, headers=auth_headers
    )
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.STORED_ADDRESS_NOT_USABLE
    assert PUBLIC_URL_NOT_AN_ADDRESS not in response.text, "not the typed sentence"
    assert "user:pass" not in response.text
    assert sent == [], "nothing is sent to an address that did not pass the guard"


async def test_an_unconfigured_server_with_no_address_is_refused(client, auth_headers):
    response = await client.post(
        "/api/servers/jellyfin/check", json={}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"] == servers_api.NEEDS_AN_ADDRESS


async def test_a_stored_address_with_no_credential_is_refused(
    app, client, auth_headers
):
    app.state.secrets = app.state.secrets.model_copy(update={"plex_token": ""})
    response = await client.post(
        "/api/servers/plex/check", json={}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"] == servers_api.NEEDS_A_CREDENTIAL


async def test_libraries_come_back_in_the_shared_shape(
    client, auth_headers, monkeypatch
):
    body = json.dumps(
        {"MediaContainer": {"Directory": [{"key": "1", "title": "Movies", "type": "movie"}]}}
    ).encode()
    monkeypatch.setattr(
        servers_api,
        "_transport",
        lambda: httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    response = await client.post(
        "/api/servers/plex/libraries", json={}, headers=auth_headers
    )
    assert response.json() == {
        "libraries": [{"id": "1", "name": "Movies", "kind": "movie"}]
    }


async def test_adding_a_server_probes_and_lists_what_the_operator_typed(
    client, auth_headers, monkeypatch
):
    """The flow the tab exists for: a server this deployment has NOT configured,
    an address and a credential typed into the card, a check and a tick-list --
    all without a stored value to fall back on.
    """
    seen: list[httpx.Request] = []
    folders = json.dumps(
        [{"ItemId": "abc", "Name": "Movies", "CollectionType": "movies"}]
    ).encode()

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=folders)

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(handler)
    )
    typed = {"url": "http://jf:8096", "credential_value": "typed-key"}

    checked = await client.post(
        "/api/servers/jellyfin/check", json=typed, headers=auth_headers
    )
    assert checked.json() == {
        "ok": True,
        "refused": False,
        "failure": None,
        # The folder listing this handler answers everything with carries no
        # ``Version``, and a version that is not there is null, not a guess.
        "version": None,
        "detail": "Jellyfin answered.",
    }

    listed = await client.post(
        "/api/servers/jellyfin/libraries", json=typed, headers=auth_headers
    )
    assert listed.json() == {
        "libraries": [{"id": "abc", "name": "Movies", "kind": "movies"}]
    }
    assert all(str(request.url).startswith("http://jf:8096") for request in seen)
    assert "typed-key" not in listed.text


async def test_adding_a_server_without_a_typed_credential_is_refused(
    client, auth_headers
):
    """The same unconfigured server, address typed and credential not: there is
    no stored value for this one either, and the rule does not soften because
    there is nothing to protect -- the answer is one sentence, not two."""
    response = await client.post(
        "/api/servers/jellyfin/check",
        json={"url": "http://jf:8096"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == servers_api.TYPED_ADDRESS_NEEDS_A_TYPED_CREDENTIAL


async def test_libraries_refused_by_the_server_say_refused_not_unreachable(
    client, auth_headers, monkeypatch
):
    """Both buttons classify one status the same way, so "Check connection" and
    "Reload libraries" cannot tell an operator two different things about one
    server."""
    monkeypatch.setattr(
        servers_api,
        "_transport",
        lambda: httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    response = await client.post(
        "/api/servers/plex/libraries", json={}, headers=auth_headers
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "Plex refused the credential."


async def test_libraries_answered_with_a_failing_status_name_the_number(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(
        servers_api,
        "_transport",
        lambda: httpx.MockTransport(
            lambda request: httpx.Response(500, content=b"secret internal detail")
        ),
    )
    response = await client.post(
        "/api/servers/plex/libraries", json={}, headers=auth_headers
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "Plex could not be reached (HTTPStatus500)."
    assert "secret internal detail" not in response.text


async def test_a_libraries_response_carries_no_credential(
    client, auth_headers, monkeypatch
):
    body = json.dumps(
        {"MediaContainer": {"Directory": [{"key": "1", "title": "Movies", "type": "movie"}]}}
    ).encode()
    monkeypatch.setattr(
        servers_api,
        "_transport",
        lambda: httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    response = await client.post(
        "/api/servers/plex/libraries", json={}, headers=auth_headers
    )
    assert "plex-token" not in response.text


async def test_libraries_from_an_unreachable_server_are_a_502(
    client, auth_headers, monkeypatch
):
    def boom(request):
        raise httpx.ConnectError("connection refused to http://plex:32400/library")

    monkeypatch.setattr(
        servers_api, "_transport", lambda: httpx.MockTransport(boom)
    )
    response = await client.post(
        "/api/servers/plex/libraries", json={}, headers=auth_headers
    )
    assert response.status_code == 502
    assert "http://" not in response.json()["detail"]


async def test_libraries_obey_the_typed_address_rule_too(client, auth_headers):
    response = await client.post(
        "/api/servers/plex/libraries",
        json={"url": "http://attacker.example"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == servers_api.TYPED_ADDRESS_NEEDS_A_TYPED_CREDENTIAL


async def test_an_unknown_server_is_a_404(client, auth_headers):
    response = await client.post(
        "/api/servers/emby/check", json={}, headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == servers_api.NOT_A_SERVER


async def test_every_route_needs_a_session(client):
    assert (await client.get("/api/servers")).status_code == 401
    assert (await client.post("/api/servers/plex/check", json={})).status_code == 401
    assert (await client.post("/api/servers/plex/libraries", json={})).status_code == 401


# --- The writes: save a server, remove one, set or clear its credential ----


async def _revision(client, auth_headers) -> str:
    """The store's current revision. Every server write must carry one."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    return body["overrides_revision"]


async def _credential(client, auth_headers, name="jellyfin", value="jf-key"):
    """Store this server's credential. A save is refused without one."""
    return await client.put(
        f"/api/servers/{name}/credential", json={"value": value}, headers=auth_headers
    )


async def _save(client, auth_headers, name="jellyfin", credential="jf-key", **fields):
    """A card's save, with the revision filled in unless a test overrides it.

    The credential goes FIRST, which is the order spec section 5's add-a-server
    flow puts the two calls in and the order the save now requires: a
    configured server whose credential resolves to nothing sends the next boot
    into the first-start wizard. ``credential=None`` is the other order, for
    the tests that are about that refusal.
    """
    if credential is not None:
        assert (
            await _credential(client, auth_headers, name, credential)
        ).status_code == 200
    body = {
        "url": "http://jellyfin:8096",
        "excluded_libraries": [],
        "switches": {},
        "expected_revision": await _revision(client, auth_headers),
    }
    body.update(fields)
    return await client.put(f"/api/servers/{name}", json=body, headers=auth_headers)


async def _rows(client, auth_headers) -> dict:
    body = (await client.get("/api/servers", headers=auth_headers)).json()
    return {row["name"]: row for row in body["servers"]}


async def test_adding_a_server_writes_its_block_and_lists_it_for_restart(
    client, auth_headers, session_factory
):
    """Spec section 5: the PUT writes the block into the stored document; the
    section is frozen, so it lands on the restart list."""
    response = await _save(
        client,
        auth_headers,
        excluded_libraries=["Home Videos"],
        switches={
            "badges.upload_to_jellyfin": True,
            "operations.write_to_jellyfin": True,
            "jellyfin.replace_thumb_with_backdrop": True,
        },
    )
    assert response.status_code == 200, response.text
    assert "jellyfin" in response.json()["restart_required"]

    async with session_factory() as session:
        document, meta = await load_store(session)
    assert document["jellyfin"]["url"] == "http://jellyfin:8096"
    assert document["jellyfin"]["excluded_libraries"] == ["Home Videos"]
    assert document["jellyfin"]["replace_thumb_with_backdrop"] is True
    assert document["badges"]["upload_to_jellyfin"] is True
    assert document["operations"]["write_to_jellyfin"] is True
    assert "jellyfin" in meta["restart_paths"]


async def test_a_save_is_visible_in_the_settings_page_own_view(client, auth_headers):
    """The card and the Settings page read one configuration, so a save made
    from one is a save the other reports -- including on the restart banner."""
    assert (await _save(client, auth_headers)).status_code == 200
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["jellyfin"]["url"] == "http://jellyfin:8096"
    assert "jellyfin" in body["restart_paths"]


async def test_a_save_keeps_the_block_keys_no_card_sends(
    client, auth_headers, session_factory
):
    """``library_map`` is a Jellyfin setting with no field on the card, so a
    save that defaulted it away would silently unmap every renamed library."""
    assert (await _save(client, auth_headers)).status_code == 200
    async with session_factory() as session:
        document = await load_overrides_document(session)
        document["jellyfin"]["library_map"] = {"Films": "Movies"}
        await write_store(session, document, store_meta())
        await session.commit()

    assert (
        await _save(client, auth_headers, url="http://jellyfin:8097")
    ).status_code == 200
    async with session_factory() as session:
        after = await load_overrides_document(session)
    assert after["jellyfin"]["library_map"] == {"Films": "Movies"}
    assert after["jellyfin"]["url"] == "http://jellyfin:8097"


async def test_a_save_on_a_delta_era_store_merges_over_the_file(
    app, client, auth_headers, session_factory, tmp_path
):
    """The store's shape is not this route's to choose: a row still holding a
    delta is merged over the mounted file, exactly as a settings save of the
    same deployment would be, and stays a delta afterwards."""
    app.state.config_path = tmp_path / "autoposter.yaml"
    app.state.config_path.write_text(
        yaml.safe_dump(_plex_only_document()), encoding="utf-8"
    )
    async with session_factory() as session:
        await write_store(session, {"workers": 5}, store_meta(1))
        await session.commit()

    assert (await _save(client, auth_headers)).status_code == 200

    async with session_factory() as session:
        document, meta = await load_store(session)
    assert meta["format"] == 1, "the row is still a delta"
    assert "plex" not in document, "the file's own values were not promoted"
    assert document["workers"] == 5
    assert document["jellyfin"]["url"] == "http://jellyfin:8096"

    rows = await _rows(client, auth_headers)
    assert rows["plex"]["configured"] is True, "the file's server survived the merge"
    assert rows["jellyfin"]["configured"] is True


async def test_a_save_without_a_credential_is_refused_and_writes_nothing(
    client, auth_headers, session_factory
):
    """The hazard the whole write path is under: ``missing_server_setup``
    reports EVERY configured server whose credential does not resolve, and boot
    turns any one of those into setup mode. So a 200 here would be a running
    deployment that comes back from the restart this response asks for as the
    first-start wizard, with the page that could undo it behind a setup token.
    """
    response = await _save(client, auth_headers, credential=None)
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.SERVER_NEEDS_A_CREDENTIAL_FIRST
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "jellyfin" not in document


async def test_a_save_with_the_credential_set_first_is_the_add_a_server_flow(
    client, auth_headers, session_factory
):
    """The other order, which is the one spec section 5 already specifies: the
    credential route, then the card's first save."""
    assert (
        await _credential(client, auth_headers, "jellyfin", "jf-key")
    ).status_code == 200
    saved = await _save(client, auth_headers, credential=None)
    assert saved.status_code == 200, saved.text
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert document["jellyfin"]["url"] == "http://jellyfin:8096"


async def test_a_credential_the_environment_supplies_is_enough_to_save(
    client, auth_headers
):
    """All three layers count, because all three answer the next start too.
    Plex's token is this deployment's environment variable and nothing is
    stored for it, and that is a server the next boot reads as complete."""
    response = await _save(
        client,
        auth_headers,
        name="plex",
        credential=None,
        url="http://plex:32400",
        excluded_libraries=["Muskarit"],
    )
    assert response.status_code == 200, response.text
    rows = await _rows(client, auth_headers)
    assert rows["plex"]["credential_source"] == "environment"


async def test_a_save_that_omits_the_exclusions_is_refused(
    client, auth_headers, session_factory
):
    """``excluded_libraries`` is written as a REPLACEMENT, so an omitted list
    would empty that server's exclusions with a 200 and no mention of it."""
    await _credential(client, auth_headers, "jellyfin", "jf-key")
    response = await client.put(
        "/api/servers/jellyfin",
        json={
            "url": "http://jellyfin:8096",
            "switches": {},
            "expected_revision": await _revision(client, auth_headers),
        },
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "excluded_libraries" in response.text
    async with session_factory() as session:
        assert "jellyfin" not in await load_overrides_document(session)


async def test_a_switch_that_is_not_this_servers_is_refused(client, auth_headers):
    response = await _save(
        client, auth_headers, switches={"badges.upload_to_plex": False}
    )
    assert response.status_code == 422
    assert servers_api.NOT_THIS_SERVERS_SWITCH in response.text


async def test_a_switch_outside_the_table_altogether_is_refused(
    client, auth_headers, session_factory
):
    """The table is the whole of what a card may reach: a key that belongs to
    no server is refused by the same walk, and nothing is written."""
    response = await _save(
        client, auth_headers, switches={"scheduler.enabled": False}
    )
    assert response.status_code == 422
    assert servers_api.NOT_THIS_SERVERS_SWITCH in response.text
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "jellyfin" not in document


async def test_a_refused_switch_writes_nothing(client, auth_headers, session_factory):
    """The refusal is reached before the document is touched, so the address
    that came with it is not stored either."""
    await _save(client, auth_headers, switches={"operations.write_to_plex": True})
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "jellyfin" not in document


async def test_an_address_that_is_not_an_address_is_refused_on_a_save(
    client, auth_headers, session_factory
):
    """The same shared guard the probe uses, with its own sentence."""
    response = await _save(client, auth_headers, url="http://user:pass@jellyfin:8096")
    assert response.status_code == 400
    assert response.json()["detail"] == PUBLIC_URL_NOT_AN_ADDRESS
    async with session_factory() as session:
        assert "jellyfin" not in await load_overrides_document(session)


async def test_a_stale_revision_is_refused(client, auth_headers, session_factory):
    """A settings save landing while the card was open: the card's document is
    the one from before it, so writing it back would revert that save."""
    response = await _save(
        client, auth_headers, expected_revision="not-the-current-one"
    )
    assert response.status_code == 409
    assert "changed somewhere else" in response.text
    async with session_factory() as session:
        assert "jellyfin" not in await load_overrides_document(session)


async def test_a_write_without_a_revision_is_refused(
    client, auth_headers, session_factory
):
    """Required, not optional: the document is read outside the row lock, so a
    caller that cannot say what it read is the caller this refuses."""
    saved = await client.put(
        "/api/servers/jellyfin",
        json={"url": "http://jellyfin:8096", "excluded_libraries": [], "switches": {}},
        headers=auth_headers,
    )
    assert saved.status_code == 422
    assert "expected_revision" in saved.text

    removed = await client.request(
        "DELETE", "/api/servers/plex", json={}, headers=auth_headers
    )
    assert removed.status_code == 422
    assert "expected_revision" in removed.text

    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "jellyfin" not in document
    assert document["plex"]["url"] == "http://plex:32400"


async def test_a_saved_address_is_reported_as_pending_a_restart(client, auth_headers):
    """The swapped generation carries the new address; the booted one does not,
    so the clients and the probe are still pointed at the old one."""
    rows = await _rows(client, auth_headers)
    assert rows["plex"]["restart_pending"] is False
    assert rows["jellyfin"]["restart_pending"] is False

    assert (await _save(client, auth_headers)).status_code == 200
    rows = await _rows(client, auth_headers)
    assert rows["jellyfin"]["configured"] is True
    assert rows["jellyfin"]["url"] == "http://jellyfin:8096"
    assert rows["jellyfin"]["restart_pending"] is True
    assert rows["plex"]["restart_pending"] is False


async def test_a_save_that_changes_no_address_is_pending_a_restart_too(
    client, auth_headers
):
    """The whole section is frozen, so the exclusions are as unapplied as an
    address would be -- and the card's pill must not contradict the banner the
    same save raised."""
    response = await _save(
        client,
        auth_headers,
        name="plex",
        url="http://plex:32400",
        excluded_libraries=["Muskarit"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["restart_required"] == ["plex.excluded_libraries"]

    rows = await _rows(client, auth_headers)
    assert rows["plex"]["url"] == "http://plex:32400", "the address did not move"
    assert rows["plex"]["restart_pending"] is True


async def test_a_saved_address_is_not_an_address_this_deployment_booted_with(
    client, auth_headers
):
    """The probe reads the BOOTED generation, so a save alone does not give it
    somewhere to send the held credential -- and the refusal says which
    generation it means."""
    assert (await _save(client, auth_headers)).status_code == 200
    response = await client.post(
        "/api/servers/jellyfin/check", json={}, headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"] == servers_api.NEEDS_AN_ADDRESS


async def test_a_removed_server_is_reported_as_pending_a_restart(
    client, auth_headers
):
    """The other direction of the same pill: the block is gone from the swapped
    generation, but the process still holds the client the booted one built, so
    the row is unconfigured AND pending until the restart."""
    assert (await _save(client, auth_headers)).status_code == 200
    assert (
        await _remove(client, auth_headers, name="plex", confirm=True)
    ).status_code == 200

    rows = await _rows(client, auth_headers)
    assert rows["plex"]["configured"] is False
    assert rows["plex"]["url"] is None
    assert rows["plex"]["restart_pending"] is True


async def _remove(client, auth_headers, name="jellyfin", **fields):
    body = {"expected_revision": await _revision(client, auth_headers)}
    body.update(fields)
    return await client.request(
        "DELETE", f"/api/servers/{name}", json=body, headers=auth_headers
    )


async def test_removing_the_only_server_is_refused(
    client, auth_headers, session_factory
):
    response = await _remove(client, auth_headers, name="plex")
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.LAST_SERVER
    async with session_factory() as session:
        assert (await load_overrides_document(session))["plex"]["url"]


async def test_removing_a_server_this_deployment_has_no_block_for_is_refused(
    client, auth_headers, session_factory
):
    """The seeded example carries ``write_to_jellyfin``, so a removal of a
    server that was never added used to be a 200 that flipped two real document
    keys, wrote a snapshot and a "remove" audit row for a removal that removed
    nothing -- and cleared the very credential spec section 5's add-a-server
    flow stores BEFORE the card's first save."""
    assert (
        await _credential(client, auth_headers, "jellyfin", "jf-key")
    ).status_code == 200
    async with session_factory() as session:
        before = await load_overrides_document(session)

    response = await _remove(client, auth_headers, confirm=True)
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.SERVER_NOT_CONFIGURED

    async with session_factory() as session:
        after = await load_overrides_document(session)
        stored = await secret_store.load_stored_secrets(session)
        rows = (
            (
                await session.execute(
                    select(EventLog).where(EventLog.event_type == "overrides_updated")
                )
            )
            .scalars()
            .all()
        )
    assert after == before, "nothing was written"
    assert "AUTOPOSTER_JELLYFIN_APIKEY" in stored, "the operator's own value"
    assert rows == [], "and no removal was recorded"


async def test_removing_a_server_drops_its_block_and_its_credential(
    client, auth_headers, session_factory
):
    assert (
        await _save(
            client,
            auth_headers,
            switches={
                "badges.upload_to_jellyfin": True,
                "operations.write_to_jellyfin": True,
            },
        )
    ).status_code == 200
    response = await _remove(client, auth_headers, confirm=True)
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        document, _meta = await load_store(session)
        stored = await secret_store.load_stored_secrets(session)
    assert "jellyfin" not in document
    assert document["badges"]["upload_to_jellyfin"] is False
    assert document["operations"]["write_to_jellyfin"] is False
    assert "AUTOPOSTER_JELLYFIN_APIKEY" not in stored
    assert response.json()["credential_cleared"] is True


async def test_a_removal_turns_off_the_per_library_overrides_of_its_switches(
    client, auth_headers, session_factory
):
    """A library's own ``true`` beats the global ``false`` a removal writes, so
    one library would keep delivering to a server that is gone."""
    assert (await _save(client, auth_headers)).status_code == 200
    settings = (await client.get("/api/config", headers=auth_headers)).json()
    # A library key has to name a configured library, so the two are read off
    # the deployment rather than invented.
    delivering, untouched = settings["collections"]["libraries"][:2]
    async with session_factory() as session:
        document = await load_overrides_document(session)
        document["libraries"] = {
            delivering: {
                "badges": {"upload_to_jellyfin": True, "upload_to_plex": True},
                "operations": {"write_to_jellyfin": True},
            },
            untouched: {"badges": {"enabled": True}},
        }
        await write_store(session, document, store_meta())
        await session.commit()

    response = await _remove(client, auth_headers, confirm=True)
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        after = await load_overrides_document(session)
    libraries = after["libraries"]
    assert libraries[delivering]["badges"]["upload_to_jellyfin"] is False
    assert libraries[delivering]["operations"]["write_to_jellyfin"] is False
    assert libraries[delivering]["badges"]["upload_to_plex"] is True, "not this one"
    assert libraries[untouched] == {"badges": {"enabled": True}}, "nothing was added"


async def test_a_removal_that_crosses_the_drop_cap_needs_confirm(
    client, auth_headers, session_factory
):
    """The editor's own cap, reached through this route: four dropped leaves is
    over it, and the refusal names them."""
    assert (
        await _save(
            client,
            auth_headers,
            switches={"jellyfin.replace_thumb_with_backdrop": True},
        )
    ).status_code == 200
    async with session_factory() as session:
        document = await load_overrides_document(session)
        document["jellyfin"]["library_map"] = {"Films": "Movies"}
        await write_store(session, document, store_meta())
        await session.commit()

    refused = await _remove(client, auth_headers)
    assert refused.status_code == 422
    assert "confirm" in refused.text
    async with session_factory() as session:
        assert "jellyfin" in await load_overrides_document(session)

    assert (await _remove(client, auth_headers, confirm=True)).status_code == 200
    async with session_factory() as session:
        assert "jellyfin" not in await load_overrides_document(session)


async def test_a_removal_is_recorded_as_a_removal(
    client, auth_headers, session_factory
):
    """The audit row and the pre-write snapshot are the one durable record that
    a server was removed; labelled "save" they say nothing of the kind."""
    assert (await _save(client, auth_headers)).status_code == 200
    assert (await _remove(client, auth_headers, confirm=True)).status_code == 200

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(EventLog)
                    .where(EventLog.event_type == "overrides_updated")
                    .order_by(EventLog.id)
                )
            )
            .scalars()
            .all()
        )
    assert [row.payload["reason"] for row in rows] == ["save", "remove"]


async def test_a_failing_credential_clear_is_reported_rather_than_swallowed(
    client, auth_headers, session_factory, monkeypatch, caplog
):
    """The removal has committed, so there is no failure left to report -- only
    a fact to act on: the row survived, the response says so, and the log says
    which server and what went wrong."""

    async def boom(session, name):
        raise OSError("the key volume went away")

    assert (await _save(client, auth_headers)).status_code == 200
    monkeypatch.setattr(secret_store, "clear_secret", boom)

    with caplog.at_level(logging.ERROR, logger="autoposter.api.servers"):
        response = await _remove(client, auth_headers, confirm=True)
    assert response.status_code == 200, response.text
    assert response.json()["credential_cleared"] is False

    logged = [record.getMessage() for record in caplog.records]
    assert any(
        "could not be cleared" in line and "jellyfin" in line and "OSError" in line
        for line in logged
    ), logged
    assert "jf-key" not in caplog.text

    async with session_factory() as session:
        document = await load_overrides_document(session)
        stored = await secret_store.load_stored_secrets(session)
    assert "jellyfin" not in document, "the removal itself stands"
    assert "AUTOPOSTER_JELLYFIN_APIKEY" in stored, "the row the log names"


async def test_a_refused_removal_leaves_the_credential_where_it_was(
    client, auth_headers, session_factory
):
    """A stale revision is decided under the row lock, after everything this
    route can check itself. Clearing before that would have a REFUSED removal
    take a still-configured server's credential with it."""
    assert (await _save(client, auth_headers)).status_code == 200

    response = await _remove(
        client, auth_headers, expected_revision="not-the-current-one", confirm=True
    )
    assert response.status_code == 409

    async with session_factory() as session:
        document = await load_overrides_document(session)
        stored = await secret_store.load_stored_secrets(session)
    assert document["jellyfin"]["url"] == "http://jellyfin:8096"
    assert "AUTOPOSTER_JELLYFIN_APIKEY" in stored


async def test_a_removal_from_a_delta_era_store_is_refused(
    client, auth_headers, session_factory
):
    """Dropping a key from a delta stops overriding the mounted file's block;
    it does not remove it. The card would report a removal that changed
    nothing, so it is refused until the next start converts the store."""
    assert (await _save(client, auth_headers)).status_code == 200
    async with session_factory() as session:
        document = await load_overrides_document(session)
        await write_store(session, document, store_meta(1))
        await session.commit()

    response = await _remove(client, auth_headers, confirm=True)
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.DELTA_STORE_CANNOT_REMOVE
    async with session_factory() as session:
        assert "jellyfin" in await load_overrides_document(session)


async def test_the_credential_routes_report_the_source_and_no_value(
    client, auth_headers
):
    response = await client.put(
        "/api/servers/jellyfin/credential",
        json={"value": "jf-key"},
        headers=auth_headers,
    )
    assert response.json() == {"name": "jellyfin", "credential_source": "stored"}
    assert "jf-key" not in response.text

    rows = await _rows(client, auth_headers)
    assert rows["jellyfin"]["credential_source"] == "stored"

    cleared = await client.delete(
        "/api/servers/jellyfin/credential", headers=auth_headers
    )
    assert cleared.json() == {
        "name": "jellyfin",
        "credential_source": "unset",
        "restart_required": False,
    }


async def test_clearing_the_only_credential_of_a_configured_server_is_refused(
    client, auth_headers, session_factory
):
    """The same end state as a save without a credential, reached from the
    other side. ``secrets_api``'s own guard does not cover it: that one keys on
    the names a deployment can never start without, and a server credential is
    required only WHEN its server is configured. The row stays where it was."""
    assert (await _save(client, auth_headers)).status_code == 200

    response = await client.delete(
        "/api/servers/jellyfin/credential", headers=auth_headers
    )
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.CONFIGURED_SERVER_KEEPS_A_CREDENTIAL
    async with session_factory() as session:
        stored = await secret_store.load_stored_secrets(session)
    assert "AUTOPOSTER_JELLYFIN_APIKEY" in stored


async def test_clearing_the_credential_of_a_server_that_is_not_configured_is_fine(
    client, auth_headers
):
    """The refusal is about a CONFIGURED server. Nothing reads a credential for
    a server this deployment does not have, so there is no boot to protect."""
    assert (
        await _credential(client, auth_headers, "jellyfin", "jf-key")
    ).status_code == 200
    cleared = await client.delete(
        "/api/servers/jellyfin/credential", headers=auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["credential_source"] == "unset"


async def test_a_cleared_credential_answers_the_layer_that_takes_over(
    client, auth_headers
):
    """Not a fixed word: the environment still supplies Plex's token, so the
    card must say so rather than claim the server has no credential -- and no
    restart is needed, because this process can still read that value. A
    configured server whose clear another layer answers is not refused: what
    the refusal above is about is the layer taking over being nothing at all.
    """
    await client.put(
        "/api/servers/plex/credential",
        json={"value": "a stored token"},
        headers=auth_headers,
    )
    cleared = await client.delete("/api/servers/plex/credential", headers=auth_headers)
    assert cleared.json() == {
        "name": "plex",
        "credential_source": "environment",
        "restart_required": False,
    }


async def test_a_clear_whose_replacement_is_unreadable_asks_for_a_restart(
    client, auth_headers, monkeypatch
):
    """``boot._export`` overwrote this name's environment entry with the row
    being removed, so the deployment's own value comes back at the next start
    and not before. The card is the only surface this fact has."""
    await client.put(
        "/api/servers/plex/credential",
        json={"value": "a stored token"},
        headers=auth_headers,
    )
    monkeypatch.setenv(STORED_SECRET_NAMES_ENV, "AUTOPOSTER_PLEX_TOKEN")
    monkeypatch.setenv(STATE_FILE_NAMES_ENV, "")
    monkeypatch.setenv(ENVIRONMENT_SECRET_NAMES_ENV, "AUTOPOSTER_PLEX_TOKEN")
    monkeypatch.setenv("AUTOPOSTER_PLEX_TOKEN", "a stored token")

    cleared = await client.delete("/api/servers/plex/credential", headers=auth_headers)
    assert cleared.json() == {
        "name": "plex",
        "credential_source": "environment",
        "restart_required": True,
    }
    assert "a stored token" not in cleared.text


async def test_the_credential_delegates_refusal_comes_back_unchanged(
    client, auth_headers
):
    """The store's own value rule, in the store's own words -- there is no
    second copy of it here to disagree with."""
    response = await client.put(
        "/api/servers/jellyfin/credential",
        json={"value": "two\nlines"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == secrets_api.VALUE_IS_NOT_STORABLE


async def test_an_unknown_server_is_a_404_on_every_write(client, auth_headers):
    revision = await _revision(client, auth_headers)
    assert (
        await client.put(
            "/api/servers/emby",
            json={
                "url": "http://emby:8096",
                "excluded_libraries": [],
                "switches": {},
                "expected_revision": revision,
            },
            headers=auth_headers,
        )
    ).status_code == 404
    assert (
        await client.request(
            "DELETE",
            "/api/servers/emby",
            json={"expected_revision": revision},
            headers=auth_headers,
        )
    ).status_code == 404
    assert (
        await client.put(
            "/api/servers/emby/credential", json={"value": "x"}, headers=auth_headers
        )
    ).status_code == 404
    assert (
        await client.delete("/api/servers/emby/credential", headers=auth_headers)
    ).status_code == 404


async def test_every_write_route_needs_a_session(client):
    body = {
        "url": "http://jellyfin:8096",
        "excluded_libraries": [],
        "switches": {},
        "expected_revision": "whatever",
    }
    assert (await client.put("/api/servers/jellyfin", json=body)).status_code == 401
    assert (
        await client.request(
            "DELETE", "/api/servers/jellyfin", json={"expected_revision": "whatever"}
        )
    ).status_code == 401
    assert (
        await client.put("/api/servers/jellyfin/credential", json={"value": "x"})
    ).status_code == 401
    assert (await client.delete("/api/servers/jellyfin/credential")).status_code == 401


# --- The Jellyfin library map, validated against both servers' own lists ----


def _both_servers(
    monkeypatch,
    plex_names,
    jellyfin_names,
    jellyfin_status=200,
    plex_kind="movie",
    jellyfin_kind="movies",
):
    """A transport that answers Plex's sections and Jellyfin's folders by path.

    The kinds are settable because they are validated: a folder of a kind this
    service never walks is refused even though the server does list it.
    """

    def handler(request):
        if "/library/sections" in request.url.path:
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "MediaContainer": {
                            "Directory": [
                                {"key": str(i), "title": name, "type": plex_kind}
                                for i, name in enumerate(plex_names, start=1)
                            ]
                        }
                    }
                ).encode(),
            )
        return httpx.Response(
            jellyfin_status,
            content=json.dumps(
                [
                    {"ItemId": f"jf{i}", "Name": name, "CollectionType": jellyfin_kind}
                    for i, name in enumerate(jellyfin_names, start=1)
                ]
            ).encode(),
        )

    monkeypatch.setattr(servers_api, "_transport", lambda: httpx.MockTransport(handler))


async def _boot_both_servers(app, client, auth_headers):
    """Configure Jellyfin, give it a credential, and restart onto both.

    The map's two reads go to the BOOTED generation's addresses with the
    credentials this deployment holds, so a save alone is not enough: the
    restart is what makes a saved address one this deployment has, and the
    two reassignments here are that restart. ``object_config`` is the
    generation the application OBJECT was built from and a real restart builds
    a new one, so leaving it behind would model a process that cannot exist.

    ``_save`` stores the credential itself, because the save is refused
    without one.
    """
    assert (await _save(client, auth_headers)).status_code == 200
    app.state.booted_config = app.state.config
    app.state.object_config = app.state.config


async def _map(client, auth_headers, pairs, **fields):
    body = {"pairs": pairs, "expected_revision": await _revision(client, auth_headers)}
    body.update(fields)
    return await client.put(
        "/api/servers/jellyfin/library-map", json=body, headers=auth_headers
    )


async def test_the_library_map_needs_both_servers(client, auth_headers):
    """A map pairs two servers, so a deployment with one has nothing to pair --
    and the refusal is about the map rather than the probe's sentence about an
    address, because neither server is the one at fault."""
    response = await _map(client, auth_headers, {"Movies": "Films"})
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.LIBRARY_MAP_NEEDS_BOTH_SERVERS


async def test_a_saved_but_unbooted_server_cannot_be_mapped_yet(
    app, client, auth_headers
):
    """The BOOTED generation, for the module header's reason: a saved address
    is not one this deployment sends its held credential to."""
    assert (await _save(client, auth_headers)).status_code == 200
    response = await _map(client, auth_headers, {"Movies": "Films"})
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.LIBRARY_MAP_NEEDS_BOTH_SERVERS


async def test_a_pair_the_server_does_not_list_is_refused(
    app, client, auth_headers, monkeypatch, session_factory
):
    """Spec section 6: a name typed by hand is not accepted. That is what makes
    a library's map partner decidable when the sibling design asks whether an
    item is missing from Jellyfin."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    response = await _map(client, auth_headers, {"Movies": "Elokuvat"})
    assert response.status_code == 422
    assert "Jellyfin" in response.text
    assert "Plex" not in response.text, "the half that does list its name"
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert document["jellyfin"].get("library_map", {}) == {}


async def test_a_plex_name_the_server_does_not_list_is_refused(
    app, client, auth_headers, monkeypatch
):
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    response = await _map(client, auth_headers, {"Elokuvat": "Films"})
    assert response.status_code == 422
    assert "Plex" in response.text


async def test_the_refusal_names_the_side_and_not_the_name_that_was_sent(
    app, client, auth_headers, monkeypatch
):
    """One fixed sentence with this service's own label for the server; the
    name the caller sent is the path beside it, never inside the sentence."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    response = await _map(client, auth_headers, {"Movies": "Elokuvat"})
    assert response.json()["detail"] == [
        {
            "path": servers_api.LIBRARY_MAP_PATH,
            "library": "Movies",
            "message": servers_api.NOT_A_LIBRARY_THIS_SERVER_LISTS.format(
                side="Jellyfin"
            ),
        }
    ]


async def test_only_pairs_that_differ_are_stored(
    app, client, auth_headers, monkeypatch, session_factory
):
    """A library named the same on both servers pairs itself, so an editor that
    sends every row it shows stores only what is not already implied."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies", "TV Shows"], ["Films", "TV Shows"])
    response = await _map(
        client, auth_headers, {"Movies": "Films", "TV Shows": "TV Shows"}
    )
    assert response.status_code == 200, response.text
    async with session_factory() as session:
        document, _meta = await load_store(session)
    assert document["jellyfin"]["library_map"] == {"Movies": "Films"}
    assert document["jellyfin"]["url"] == "http://jellyfin:8096", "the block survived"


async def test_a_stored_map_is_visible_in_the_settings_page_own_view(
    app, client, auth_headers, monkeypatch
):
    """The card and the Settings page read one configuration, and the frozen
    section puts the map on the restart banner like every other Jellyfin key --
    so a map saved here is a map the Settings page reports as unapplied."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    assert (await _map(client, auth_headers, {"Movies": "Films"})).status_code == 200

    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["jellyfin"]["library_map"] == {"Movies": "Films"}
    assert body["restart_paths"] == ["jellyfin.library_map.Movies"]


async def test_a_map_response_carries_no_credential(
    app, client, auth_headers, monkeypatch
):
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    response = await _map(client, auth_headers, {"Movies": "Films"})
    assert "jf-key" not in response.text
    assert "plex-token" not in response.text


async def test_a_stale_revision_is_refused_on_the_map_too(
    app, client, auth_headers, monkeypatch, session_factory
):
    """This write reads the stored document outside the row lock as well, so a
    settings save landing in that window is a 409 and not a silent revert."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    response = await _map(
        client, auth_headers, {"Movies": "Films"}, expected_revision="not-the-one"
    )
    assert response.status_code == 409
    assert "changed somewhere else" in response.text
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert document["jellyfin"].get("library_map", {}) == {}


async def test_a_map_without_a_revision_is_refused(
    app, client, auth_headers, monkeypatch
):
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    response = await client.put(
        "/api/servers/jellyfin/library-map",
        json={"pairs": {"Movies": "Films"}},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "expected_revision" in response.text


async def test_a_library_read_that_fails_answers_the_probes_own_sentence(
    app, client, auth_headers, monkeypatch, session_factory
):
    """One outage, one vocabulary: the map's reads and the card's library
    reload cannot tell an operator two different things about one server."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"], jellyfin_status=500)
    response = await _map(client, auth_headers, {"Movies": "Films"})
    assert response.status_code == 502
    assert response.json()["detail"] == servers_api.probe.UNREACHABLE.format(
        system="Jellyfin", failure="HTTPStatus500"
    )
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert document["jellyfin"].get("library_map", {}) == {}


async def test_the_map_route_needs_a_session(client):
    response = await client.put(
        "/api/servers/jellyfin/library-map",
        json={"pairs": {}, "expected_revision": "whatever"},
    )
    assert response.status_code == 401


async def test_a_server_removed_but_not_restarted_away_from_cannot_be_mapped(
    app, client, auth_headers, monkeypatch
):
    """The booted generation still carries the server, so the first gate lets it
    through; the stored document has no block to write the map into, and the
    answer is the route's sentence rather than pydantic's missing-field."""
    await _boot_both_servers(app, client, auth_headers)
    assert (
        await _remove(client, auth_headers, confirm=True)
    ).status_code == 200
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    response = await _map(client, auth_headers, {"Movies": "Films"})
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.LIBRARY_MAP_NEEDS_BOTH_SERVERS


async def test_a_library_of_a_kind_this_service_never_walks_is_refused(
    app, client, auth_headers, monkeypatch, session_factory
):
    """A music folder is listed by the server and dropped by the index, so a
    pair naming one maps a Plex library onto nothing: the Plex-facing name
    never appears, and every item in it would stay unresolved forever."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Music"], jellyfin_kind="music")
    response = await _map(client, auth_headers, {"Movies": "Music"})
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "path": servers_api.LIBRARY_MAP_PATH,
            "library": "Movies",
            "message": servers_api.NOT_A_LIBRARY_THIS_SERVICE_INDEXES.format(
                side="Jellyfin"
            ),
        }
    ]
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "library_map" not in document["jellyfin"]


async def test_a_kind_this_service_never_walks_is_not_reported_as_a_typo(
    app, client, auth_headers, monkeypatch
):
    """The two refusals send an operator looking for different mistakes, so a
    folder they can see in their own server must not be called unknown."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Music"], jellyfin_kind="music")
    response = await _map(client, auth_headers, {"Movies": "Music"})
    assert servers_api.NOT_A_LIBRARY_THIS_SERVER_LISTS.format(
        side="Jellyfin"
    ) not in response.text


async def test_clearing_the_map_leaves_no_key_behind(
    app, client, auth_headers, monkeypatch, session_factory
):
    """Absent is how "no map" is spelled in a configuration document, and the
    editor's per-row clear control produces exactly this body."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    assert (await _map(client, auth_headers, {"Movies": "Films"})).status_code == 200

    assert (await _map(client, auth_headers, {})).status_code == 200
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "library_map" not in document["jellyfin"]
    assert document["jellyfin"]["url"] == "http://jellyfin:8096", "the block survived"

    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["jellyfin"]["library_map"] == {}, "the schema's own default"


async def test_a_map_of_nothing_but_self_pairs_leaves_no_key_behind(
    app, client, auth_headers, monkeypatch, session_factory
):
    """Every row cleared, sent as the whole map: the pairs that survive the
    differ-filter are none, and none is an absent key, not an empty one."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["TV Shows"], ["TV Shows"])
    response = await _map(client, auth_headers, {"TV Shows": "TV Shows"})
    assert response.status_code == 200, response.text
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "library_map" not in document["jellyfin"]


async def test_a_map_on_a_delta_era_store_is_refused(
    app, client, auth_headers, session_factory, tmp_path
):
    """A delta is MERGED over the mounted file, so a map written into one is
    added to the file's pairs rather than replacing them: a cleared row comes
    back and an emptied map changes nothing. Refused before anything else,
    including the two live reads."""
    app.state.config_path = tmp_path / "autoposter.yaml"
    app.state.config_path.write_text(
        yaml.safe_dump(_plex_only_document()), encoding="utf-8"
    )
    async with session_factory() as session:
        await write_store(session, {"workers": 5}, store_meta(1))
        await session.commit()

    response = await _map(client, auth_headers, {})
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.DELTA_STORE_CANNOT_MAP


async def test_a_map_that_crosses_the_drop_cap_needs_confirm(
    app, client, auth_headers, monkeypatch, session_factory
):
    """This is the one route whose ordinary payload drops many leaves at once:
    each pair is a leaf, so an operator clearing four rows meets the editor's
    own cap and has to say they meant it."""
    await _boot_both_servers(app, client, auth_headers)
    async with session_factory() as session:
        document = await load_overrides_document(session)
        document["jellyfin"]["library_map"] = {
            "A": "a", "B": "b", "C": "c", "D": "d", "Movies": "Films",
        }
        await write_store(session, document, store_meta())
        await session.commit()
    _both_servers(monkeypatch, ["Movies"], ["Films"])

    refused = await _map(client, auth_headers, {"Movies": "Films"})
    assert refused.status_code == 422
    assert "confirm" in refused.text
    async with session_factory() as session:
        after = await load_overrides_document(session)
    assert len(after["jellyfin"]["library_map"]) == 5, "nothing was dropped"

    allowed = await _map(client, auth_headers, {"Movies": "Films"}, confirm=True)
    assert allowed.status_code == 200, allowed.text
    async with session_factory() as session:
        after = await load_overrides_document(session)
    assert after["jellyfin"]["library_map"] == {"Movies": "Films"}


async def test_a_self_paired_row_this_service_never_walks_is_not_refused(
    app, client, auth_headers, monkeypatch, session_factory
):
    """An editor submits every row it shows, and a music library is named the
    same on both servers as often as any other. The row is discarded before it
    is checked, because a pair this route will not store is not a statement
    about the two servers that it could be wrong about."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(
        monkeypatch, ["Music"], ["Music"], plex_kind="artist", jellyfin_kind="music"
    )
    response = await _map(client, auth_headers, {"Music": "Music"})
    assert response.status_code == 200, response.text
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "library_map" not in document["jellyfin"]


async def test_a_differing_pair_naming_the_same_folder_is_still_refused(
    app, client, auth_headers, monkeypatch
):
    """The filter softens nothing about the pairs that ARE stored: the same
    music folder on the far side of a real pair is the refusal it was."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Music"], jellyfin_kind="music")
    response = await _map(client, auth_headers, {"Movies": "Music"})
    assert response.status_code == 422
    assert servers_api.NOT_A_LIBRARY_THIS_SERVICE_INDEXES.format(
        side="Jellyfin"
    ) in response.text


async def test_a_map_with_one_credential_missing_says_so_about_the_map(
    app, client, auth_headers, monkeypatch, session_factory
):
    """Both servers are configured and booted, and one credential has gone out
    from under the running process. The probe's own sentence tells an operator
    to "set one on its card before checking it" -- a check button, on a write
    route, about a server they were not editing. This one is about the map."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    app.state.secrets = app.state.secrets.model_copy(
        update={"jellyfin_api_key": ""}
    )

    response = await _map(client, auth_headers, {"Movies": "Films"})
    assert response.status_code == 409
    assert response.json()["detail"] == servers_api.LIBRARY_MAP_NEEDS_BOTH_CREDENTIALS
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "library_map" not in document["jellyfin"]


async def test_two_plex_libraries_cannot_share_one_jellyfin_folder(
    app, client, auth_headers, monkeypatch, session_factory
):
    """Both halves of a many-to-one validate as listed-and-indexable, so
    nothing else refuses it -- and what it stores is a map the index resolves
    twice onto one folder. The rows are walked in sorted order, so the first
    Plex name keeps the folder and the later one is the row reported."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Elokuvat", "Movies"], ["Films"])

    response = await _map(
        client, auth_headers, {"Movies": "Films", "Elokuvat": "Films"}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "path": servers_api.LIBRARY_MAP_PATH,
            "library": "Movies",
            "message": servers_api.LIBRARY_MAP_PAIRS_ONCE.format(jellyfin="Films"),
        }
    ]
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "library_map" not in document["jellyfin"]


async def test_a_row_neither_server_carries_is_reported_once_and_claims_nothing(
    app, client, auth_headers, monkeypatch
):
    """Two Plex libraries pointed at one name Jellyfin does not list. A row that
    has already failed claims no folder: claiming one would report the second
    row twice, the second sentence telling the operator that the folder they
    mistyped is already paired with another Plex library."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Elokuvat", "Movies"], ["Films"])

    response = await _map(
        client, auth_headers, {"Movies": "Sarjat", "Elokuvat": "Sarjat"}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "path": servers_api.LIBRARY_MAP_PATH,
            "library": library,
            "message": servers_api.NOT_A_LIBRARY_THIS_SERVER_LISTS.format(
                side="Jellyfin"
            ),
        }
        for library in ("Elokuvat", "Movies")
    ], "one sentence per row, and neither of them about a second pairing"


async def test_removing_plex_drops_the_library_map_its_keys_name(
    app, client, auth_headers, monkeypatch, session_factory
):
    """The map's KEYS are Plex library names. With Plex gone nothing carries
    them, so every row names a library this deployment has no server for -- and
    the map route would later measure those rows against a live read of a
    server that is not there."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    assert (await _map(client, auth_headers, {"Movies": "Films"})).status_code == 200

    assert (
        await _remove(client, auth_headers, name="plex", confirm=True)
    ).status_code == 200
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "plex" not in document
    assert "library_map" not in document["jellyfin"]
    assert document["jellyfin"]["url"] == "http://jellyfin:8096", "the block survived"


async def test_a_real_pair_is_still_refused_beside_a_discarded_row(
    app, client, auth_headers, monkeypatch, session_factory
):
    """One wholesale submit carrying both shapes: the self-paired photo row is
    discarded, the typo in the real pair is not."""
    await _boot_both_servers(app, client, auth_headers)
    _both_servers(monkeypatch, ["Movies"], ["Films"])
    response = await _map(
        client, auth_headers, {"Photos": "Photos", "Movies": "Elokuvat"}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "path": servers_api.LIBRARY_MAP_PATH,
            "library": "Movies",
            "message": servers_api.NOT_A_LIBRARY_THIS_SERVER_LISTS.format(
                side="Jellyfin"
            ),
        }
    ]
    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "library_map" not in document["jellyfin"]
