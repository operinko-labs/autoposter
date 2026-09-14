"""Names and sources; never a value (spec section 3).

The page has to say "TMDb: stored", "TVDB: environment", "Radarr: unset" and
offer Set / Replace / Clear. Everything below is about that being the whole of
what leaves this process.

The environment is cleared per test rather than trusted. Two of the claims
here are about a name resolving to `environment` or to `unset`, and both are
statements about THIS test's environment: a developer who exports
AUTOPOSTER_RADARR_APIKEY in their shell would otherwise turn the second into a
failure that says nothing about the code.
"""
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.api.secrets_api import (
    DATABASE_URL_CANNOT_BE_STORED,
    KEY_FILE_NOT_USABLE,
    NOT_A_SECRET_THIS_SERVICE_READS,
    VALUE_IS_NOT_STORABLE,
    WEBHOOK_SECRET_IS_GENERATED,
)
from autoposter.app import create_app
from autoposter.config import secret_store
from autoposter.config.loader import build_config
from autoposter.config.schema import (
    SECRET_NAMES,
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
    Secrets,
)
from autoposter.config.state import STATE_DIR_ENV

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    for name in (*SECRET_NAMES, STATE_FILE_NAMES_ENV, STORED_SECRET_NAMES_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def app(session_factory):
    config = build_config(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")))
    return create_app(
        config,
        session_factory,
        Secrets(
            database_url="postgresql+asyncpg://unused",
            plex_token="x", tmdb_token="x", tvdb_apikey="x",
            fanart_apikey="x", webhook_secret="x",
            admin_password_hash=hash_password(PASSWORD),
        ),
    )


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


async def test_the_listing_carries_names_and_sources_and_no_values(
    client, auth_headers, monkeypatch
):
    monkeypatch.setenv("AUTOPOSTER_TVDB_APIKEY", "from-env")
    response = await client.get("/api/secrets", headers=auth_headers)
    rows = {row["name"]: row for row in response.json()["secrets"]}
    assert rows["AUTOPOSTER_TVDB_APIKEY"]["source"] == "environment"
    assert rows["AUTOPOSTER_RADARR_APIKEY"]["source"] == "unset"
    assert rows["AUTOPOSTER_WEBHOOK_SECRET"]["generated"] is True
    assert rows["AUTOPOSTER_TMDB_TOKEN"]["generated"] is False
    assert [row["name"] for row in response.json()["secrets"]] == list(SECRET_NAMES)
    assert all(set(row) == {"name", "source", "generated"} for row in rows.values())
    assert "from-env" not in response.text


async def test_setting_a_secret_stores_it_and_reports_the_source(
    client, auth_headers, session_factory, app
):
    response = await client.put(
        "/api/secrets/AUTOPOSTER_TMDB_TOKEN",
        json={"value": "a-new-token"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == {"name": "AUTOPOSTER_TMDB_TOKEN", "source": "stored"}
    async with session_factory() as session:
        assert (await secret_store.load_stored_secrets(session))[
            "AUTOPOSTER_TMDB_TOKEN"
        ] == "a-new-token"
    assert app.state.secrets.tmdb_token == "a-new-token", "the running value is live"

    # The listing reads the LIVE table, not the marker this process booted
    # with -- a secret set from the page is `stored` before any restart.
    listing = await client.get("/api/secrets", headers=auth_headers)
    rows = {row["name"]: row for row in listing.json()["secrets"]}
    assert rows["AUTOPOSTER_TMDB_TOKEN"]["source"] == "stored"
    assert "a-new-token" not in listing.text


async def test_clearing_a_stored_secret_falls_through(
    client, auth_headers, monkeypatch, app
):
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", "from-env")
    await client.put(
        "/api/secrets/AUTOPOSTER_TMDB_TOKEN",
        json={"value": "stored"},
        headers=auth_headers,
    )
    response = await client.delete(
        "/api/secrets/AUTOPOSTER_TMDB_TOKEN", headers=auth_headers
    )
    assert response.json() == {"name": "AUTOPOSTER_TMDB_TOKEN", "source": "environment"}
    # The rebind follows the fall-through: the running value is what the next
    # source down answers, not the cleared one and not an empty string.
    assert app.state.secrets.tmdb_token == "from-env"


async def test_clearing_a_secret_no_source_answers_leaves_it_unset(
    client, auth_headers, app
):
    await client.put(
        "/api/secrets/AUTOPOSTER_MDBLIST_APIKEY",
        json={"value": "stored"},
        headers=auth_headers,
    )
    response = await client.delete(
        "/api/secrets/AUTOPOSTER_MDBLIST_APIKEY", headers=auth_headers
    )
    assert response.json() == {"name": "AUTOPOSTER_MDBLIST_APIKEY", "source": "unset"}
    assert app.state.secrets.mdblist_apikey == ""


async def test_an_unknown_name_is_a_404(client, auth_headers):
    response = await client.put(
        "/api/secrets/AUTOPOSTER_NOT_A_THING", json={"value": "x"}, headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == NOT_A_SECRET_THIS_SERVICE_READS
    cleared = await client.delete(
        "/api/secrets/AUTOPOSTER_NOT_A_THING", headers=auth_headers
    )
    assert cleared.status_code == 404
    assert cleared.json()["detail"] == NOT_A_SECRET_THIS_SERVICE_READS


async def test_the_generated_secret_is_refused(client, auth_headers):
    response = await client.put(
        "/api/secrets/AUTOPOSTER_WEBHOOK_SECRET",
        json={"value": "x"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == WEBHOOK_SECRET_IS_GENERATED


async def test_the_database_url_is_refused(client, auth_headers):
    response = await client.put(
        "/api/secrets/AUTOPOSTER_DATABASE_URL",
        json={"value": "postgresql+asyncpg://x"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == DATABASE_URL_CANNOT_BE_STORED
    assert "postgresql+asyncpg://x" not in response.text


async def test_a_value_the_store_cannot_hold_is_refused(
    client, auth_headers, session_factory
):
    """The store's own rule, asked at the route so the refusal is a 422 and
    not a 500 -- and asked about the empty string too, which `is_storable`
    allows and a ROW must not hold."""
    for value in ("row-one\nrow-two", "with\x00nul", "x" * 5000, ""):
        response = await client.put(
            "/api/secrets/AUTOPOSTER_TMDB_TOKEN",
            json={"value": value},
            headers=auth_headers,
        )
        assert response.status_code == 422, value[:8]
        assert response.json()["detail"] == VALUE_IS_NOT_STORABLE
    async with session_factory() as session:
        assert await secret_store.stored_secret_names(session) == []


async def test_an_unusable_key_file_names_the_file_and_never_the_value(
    client, auth_headers, state
):
    """A corrupt key is the operator's to fix and the sentence has to say
    which file. Nothing about the submitted value reaches the response."""
    (state / secret_store.secret_key_path().name).write_text("not-a-key\n")
    response = await client.put(
        "/api/secrets/AUTOPOSTER_TMDB_TOKEN",
        json={"value": "a-value-that-must-not-appear"},
        headers=auth_headers,
    )
    assert response.status_code == 503
    assert response.json()["detail"].startswith(KEY_FILE_NOT_USABLE)
    assert str(secret_store.secret_key_path()) in response.json()["detail"]
    assert "a-value-that-must-not-appear" not in response.text


async def test_an_unwritable_state_directory_answers_a_sentence(
    client, auth_headers, tmp_path, monkeypatch
):
    """Not a traceback: the volume did not mount, which is a deployment fault
    an operator reads off the page."""
    not_a_directory = tmp_path / "unmountable"
    not_a_directory.write_text("")
    monkeypatch.setenv(STATE_DIR_ENV, str(not_a_directory))
    response = await client.put(
        "/api/secrets/AUTOPOSTER_TMDB_TOKEN",
        json={"value": "a-value-that-must-not-appear"},
        headers=auth_headers,
    )
    assert response.status_code == 503
    assert response.json()["detail"].startswith(KEY_FILE_NOT_USABLE)
    assert "a-value-that-must-not-appear" not in response.text


async def test_every_route_needs_a_session(client):
    assert (await client.get("/api/secrets")).status_code == 401
    assert (
        await client.put("/api/secrets/AUTOPOSTER_TMDB_TOKEN", json={"value": "x"})
    ).status_code == 401
    assert (await client.delete("/api/secrets/AUTOPOSTER_TMDB_TOKEN")).status_code == 401
