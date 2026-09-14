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
import os
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.api.secrets_api import (
    DATABASE_URL_CANNOT_BE_STORED,
    HARD_SECRET_WOULD_BE_UNSET,
    KEY_FILE_NOT_USABLE,
    NOT_A_SECRET_THIS_SERVICE_READS,
    VALUE_IS_NOT_STORABLE,
    WEBHOOK_SECRET_IS_GENERATED,
)
from autoposter.app import create_app
from autoposter.config import secret_store
from autoposter.config.loader import build_config
from autoposter.config.schema import (
    ENVIRONMENT_SECRET_NAMES_ENV,
    SECRET_NAMES,
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
    Secrets,
    state_file_secret_names,
)
from autoposter.config.state import STATE_DIR_ENV, merge_secrets_file

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
TMDB = "AUTOPOSTER_TMDB_TOKEN"
MDBLIST = "AUTOPOSTER_MDBLIST_APIKEY"
MARKERS = (STORED_SECRET_NAMES_ENV, STATE_FILE_NAMES_ENV, ENVIRONMENT_SECRET_NAMES_ENV)


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    for name in (*SECRET_NAMES, *MARKERS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    return tmp_path


def booted_with(monkeypatch, *, stored=(), state_file=(), environment=(), exported=None):
    """The environment `boot` leaves behind for the application it execs.

    The three markers it publishes -- each set unconditionally, possibly empty
    -- and `_export`'s assignment of the WINNING value into `os.environ`, which
    is the whole reason the markers exist: for a name the store won, what sits
    in `os.environ` afterwards is a copy of the stored row, and whatever the
    deployment itself set for that name is gone.
    """
    monkeypatch.setenv(STORED_SECRET_NAMES_ENV, ",".join(stored))
    monkeypatch.setenv(STATE_FILE_NAMES_ENV, ",".join(state_file))
    monkeypatch.setenv(ENVIRONMENT_SECRET_NAMES_ENV, ",".join(environment))
    for name, value in (exported or {}).items():
        monkeypatch.setenv(name, value)


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

    # The WHOLE body, written out: every row, every key, the order, and
    # nowhere for a value to hide. A row added to `SECRET_NAMES` fails here
    # rather than passing a containment check unnoticed.
    assert response.json() == {
        "secrets": [
            {"name": "AUTOPOSTER_DATABASE_URL", "source": "unset", "generated": False},
            {"name": "AUTOPOSTER_TMDB_TOKEN", "source": "unset", "generated": False},
            {
                "name": "AUTOPOSTER_TVDB_APIKEY",
                "source": "environment",
                "generated": False,
            },
            {"name": "AUTOPOSTER_FANART_APIKEY", "source": "unset", "generated": False},
            {"name": "AUTOPOSTER_WEBHOOK_SECRET", "source": "unset", "generated": True},
            {"name": "AUTOPOSTER_PLEX_TOKEN", "source": "unset", "generated": False},
            {
                "name": "AUTOPOSTER_JELLYFIN_APIKEY",
                "source": "unset",
                "generated": False,
            },
            {"name": "AUTOPOSTER_MDBLIST_APIKEY", "source": "unset", "generated": False},
            {"name": "AUTOPOSTER_RADARR_APIKEY", "source": "unset", "generated": False},
            {"name": "AUTOPOSTER_SONARR_APIKEY", "source": "unset", "generated": False},
            {
                "name": "AUTOPOSTER_ADMIN_PASSWORD_HASH",
                "source": "unset",
                "generated": False,
            },
            {
                "name": "AUTOPOSTER_PLEX_ACCOUNT_TOKEN",
                "source": "unset",
                "generated": False,
            },
            {
                "name": "AUTOPOSTER_TRACEARR_APIKEY",
                "source": "unset",
                "generated": False,
            },
            {"name": "AUTOPOSTER_API_KEY", "source": "unset", "generated": False},
        ]
    }
    # The literal above is in `SECRET_NAMES`' order because the route is, and
    # this is the line that says so.
    assert [row["name"] for row in response.json()["secrets"]] == list(SECRET_NAMES)
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
    # No markers: nothing has exec'd this process, so the environment's value
    # is the environment's own and is reachable.
    assert response.json() == {
        "name": "AUTOPOSTER_TMDB_TOKEN",
        "source": "environment",
        "restart_required": False,
    }
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
    assert response.json() == {
        "name": "AUTOPOSTER_MDBLIST_APIKEY",
        "source": "unset",
        "restart_required": False,
    }
    assert app.state.secrets.mdblist_apikey == ""


# --- the clear, in the environment `boot` actually leaves behind -------------
#
# Three arms, one per source that can take over, each with the markers and the
# exported copy a real deployment carries. The copy is what makes these
# different from the two cases above: `os.environ` holds the value being
# cleared, so a route that read it would report the credential as coming from
# a variable nobody set and go on using it until the next restart.


async def test_a_clear_the_state_file_answers_takes_the_file_s_value(
    client, auth_headers, app, monkeypatch
):
    await client.put(
        f"/api/secrets/{TMDB}", json={"value": "the-stored-one"}, headers=auth_headers
    )
    merge_secrets_file({TMDB: "the-one-in-the-state-file"})
    # The file marker comes from `boot`'s own function rather than a literal,
    # and what it publishes here is EMPTY: that marker records what the file
    # WON, and at this boot the store outranked it. Naming the marker by hand
    # would describe a deployment `boot` cannot produce -- and would pass while
    # every real one of this shape answered `unset`.
    booted_with(
        monkeypatch,
        stored=[TMDB],
        state_file=state_file_secret_names({TMDB: "the-stored-one"}),
        exported={TMDB: "the-stored-one"},
    )
    assert os.environ[STATE_FILE_NAMES_ENV] == "", "the marker boot really emits"

    response = await client.delete(f"/api/secrets/{TMDB}", headers=auth_headers)

    assert response.json() == {
        "name": TMDB,
        "source": "state file",
        "restart_required": False,
    }
    assert app.state.secrets.tmdb_token == "the-one-in-the-state-file"


async def test_a_clear_the_environment_answers_needs_a_restart(
    client, auth_headers, app, monkeypatch
):
    """The deployment set this variable and `_export` overwrote it with the
    stored row, so the value that takes over cannot be read until the next
    start. The row is gone either way -- the response says the rest."""
    await client.put(
        f"/api/secrets/{TMDB}", json={"value": "the-stored-one"}, headers=auth_headers
    )
    booted_with(
        monkeypatch,
        stored=[TMDB],
        environment=[TMDB],
        exported={TMDB: "the-stored-one"},
    )

    response = await client.delete(f"/api/secrets/{TMDB}", headers=auth_headers)

    assert response.json() == {
        "name": TMDB,
        "source": "environment",
        "restart_required": True,
    }
    assert app.state.secrets.tmdb_token == "", "the cleared value is not in force"
    assert "the-stored-one" not in response.text


async def test_a_clear_nothing_answers_does_not_resurrect_the_exported_copy(
    client, auth_headers, app, monkeypatch
):
    """No layer below: the environment's entry for this name is `boot`'s echo
    of the row itself, and it must not be mistaken for a source."""
    await client.put(
        f"/api/secrets/{MDBLIST}", json={"value": "the-stored-one"}, headers=auth_headers
    )
    booted_with(monkeypatch, stored=[MDBLIST], exported={MDBLIST: "the-stored-one"})

    response = await client.delete(f"/api/secrets/{MDBLIST}", headers=auth_headers)

    assert response.json() == {
        "name": MDBLIST,
        "source": "unset",
        "restart_required": False,
    }
    assert app.state.secrets.mdblist_apikey == ""
    assert "the-stored-one" not in response.text


async def test_clearing_a_hard_secret_with_nothing_beneath_it_is_refused(
    client, auth_headers, app, monkeypatch, session_factory
):
    """The deployment would not start again. Refused while the row is still
    there, rather than reported after it is gone."""
    await client.put(
        f"/api/secrets/{TMDB}", json={"value": "the-stored-one"}, headers=auth_headers
    )
    booted_with(monkeypatch, stored=[TMDB], exported={TMDB: "the-stored-one"})

    response = await client.delete(f"/api/secrets/{TMDB}", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == HARD_SECRET_WOULD_BE_UNSET
    async with session_factory() as session:
        assert await secret_store.stored_secret_names(session) == [TMDB]
    assert app.state.secrets.tmdb_token == "the-stored-one"


async def test_a_row_this_key_cannot_open_is_not_reported_as_stored(
    client, auth_headers
):
    """The lost or swapped volume, which this store is built to survive: the
    resolver skips the row and the running value comes from the layer beneath,
    so the listing has to say the same."""
    await client.put(
        f"/api/secrets/{TMDB}", json={"value": "the-stored-one"}, headers=auth_headers
    )
    secret_store.secret_key_path().write_bytes(Fernet.generate_key() + b"\n")

    response = await client.get("/api/secrets", headers=auth_headers)

    rows = {row["name"]: row for row in response.json()["secrets"]}
    assert rows[TMDB]["source"] == "unset"


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
