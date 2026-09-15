"""The Servers tab's backend (spec section 5).

The listing, the live probe and the live library read. The one security rule
this module inherits from the wizard has its own test: a credential this
deployment holds never travels to an address a request named.
"""
import json
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
    assert body == {"ok": True, "refused": False, "failure": None, "detail": "Plex answered."}


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
    carry what the typed path refuses. It is refused with the same sentence,
    which names the field and never the value.
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
    assert response.status_code == 400
    assert response.json()["detail"] == PUBLIC_URL_NOT_AN_ADDRESS
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
        "ok": True, "refused": False, "failure": None, "detail": "Jellyfin answered."
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


async def _save(client, auth_headers, name="jellyfin", **fields):
    """A card's save, with the revision filled in unless a test overrides it."""
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
    await client.put(
        "/api/servers/jellyfin/credential",
        json={"value": "jf-key"},
        headers=auth_headers,
    )
    response = await _remove(client, auth_headers, confirm=True)
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        document, _meta = await load_store(session)
        stored = await secret_store.load_stored_secrets(session)
    assert "jellyfin" not in document
    assert document["badges"]["upload_to_jellyfin"] is False
    assert document["operations"]["write_to_jellyfin"] is False
    assert "AUTOPOSTER_JELLYFIN_APIKEY" not in stored


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


async def test_a_failing_credential_clear_leaves_the_server_configured(
    app, client, auth_headers, session_factory, monkeypatch
):
    """The clear is part of the write, not a step beside it: a clear that fails
    writes no document, so a retry has a server to remove rather than an
    orphaned credential row nobody can see."""

    async def boom(session, name):
        raise OSError("the key volume went away")

    assert (await _save(client, auth_headers)).status_code == 200
    monkeypatch.setattr(secret_store, "clear_secret", boom)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as failing:
        revision = await _revision(failing, auth_headers)
        response = await failing.request(
            "DELETE",
            "/api/servers/jellyfin",
            json={"expected_revision": revision, "confirm": True},
            headers=auth_headers,
        )
    assert response.status_code >= 500

    async with session_factory() as session:
        document = await load_overrides_document(session)
    assert "jellyfin" in document, "no document was written"


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


async def test_a_cleared_credential_answers_the_layer_that_takes_over(
    client, auth_headers
):
    """Not a fixed word: the environment still supplies Plex's token, so the
    card must say so rather than claim the server has no credential -- and no
    restart is needed, because this process can still read that value."""
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
