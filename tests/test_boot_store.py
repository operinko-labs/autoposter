"""Boot reads the store before it decides anything, and hands it on.

Three claims, and they fail independently.

A stored secret reaches the boot resolver at all -- otherwise the store is a
table the running service can write and nothing ever reads.

A store that cannot be read is an empty map rather than a refusal: no database,
no table yet, no key, an unreachable host, a host that accepts the connection
and then stops answering. The layers beneath the store are how every deployment
that predates it is configured, so a boot that exited over an empty table would
break all of them at once, with no UI left from which it could be fixed -- and
"never blocks a boot" has to be a bound rather than an intention.

And the FIRST-START WIZARD sees what boot read. The wizard has no database
session of its own; the admin password hash may live in the store; and a wizard
that could not see it would treat a deployment that has a password as one that
has never had one and hand a setup token to whoever asked first.
"""
import asyncio
import os
import time

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from autoposter import boot
from autoposter.api.auth import hash_password
from autoposter.config import secret_store
from autoposter.config import state as state_module
from autoposter.config.schema import (
    ENVIRONMENT_SECRET_NAMES_ENV,
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
)
from autoposter.config.state import STATE_DIR_ENV
from autoposter.db import base as db_base

HARD = (
    "AUTOPOSTER_DATABASE_URL",
    "AUTOPOSTER_TMDB_TOKEN",
    "AUTOPOSTER_TVDB_APIKEY",
    "AUTOPOSTER_FANART_APIKEY",
    "AUTOPOSTER_WEBHOOK_SECRET",
)

SOFT = (
    "AUTOPOSTER_ADMIN_PASSWORD_HASH",
    "AUTOPOSTER_API_KEY",
    "AUTOPOSTER_MDBLIST_APIKEY",
    "AUTOPOSTER_PLEX_TOKEN",
    "AUTOPOSTER_JELLYFIN_APIKEY",
)

MASTER_PASSWORD = "a-real-master-password"


@pytest.fixture(autouse=True)
def clean_secret_environment(monkeypatch, tmp_path):
    """No inherited credentials, no inherited markers, and a state directory
    nobody else shares.

    Saved and restored by hand rather than through `monkeypatch.delenv` alone,
    for `tests/test_boot.py`'s reason: `boot.main` assigns into `os.environ`
    directly -- the three markers ahead of `_export`, then every resolved
    value -- and monkeypatch records no undo entry for a name that was absent
    when the test began, so a boot test would otherwise leak a credential into
    every test after it.
    """
    names = (
        *HARD,
        *SOFT,
        "AUTOPOSTER_CONFIG",
        STATE_FILE_NAMES_ENV,
        STORED_SECRET_NAMES_ENV,
        ENVIRONMENT_SECRET_NAMES_ENV,
    )
    saved = {name: os.environ[name] for name in names if name in os.environ}
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "state"))
    yield
    for name in names:
        os.environ.pop(name, None)
    os.environ.update(saved)


@pytest.fixture
def database_url():
    """The database this pytest process owns.

    ``tests/conftest.py`` exposes no fixture for the URL itself, but it writes
    the per-worker URL it derived back into this variable -- so reading the
    variable here names exactly the database ``session_factory`` writes into,
    under xdist as well as serially.
    """
    return os.environ["AUTOPOSTER_TEST_DATABASE_URL"]


@pytest.fixture
def tableless_database_url():
    """A database that answers but holds no ``stored_secrets`` table: the
    ordinary first boot, before ``alembic upgrade head`` has ever run.

    The compose file's maintenance database, which the suite already requires
    and which this project's migrations never touch.
    """
    url = os.environ["AUTOPOSTER_MAINTENANCE_DATABASE_URL"]
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _in_a_thread(url: str):
    """`stored_secrets_for_boot`, called the way `boot.main` calls it.

    It owns an `asyncio.run`, which is correct for the frame it is written for
    -- `main` is synchronous and runs before uvicorn -- and raises outright
    from inside a running loop like an async test's. Calling it directly there
    would land in its own catch-all and answer `{}`, which is
    indistinguishable from a store that held nothing: the test would pass for
    the wrong reason the day the read broke.
    """
    return asyncio.to_thread(boot.stored_secrets_for_boot, url)


@pytest.mark.asyncio
async def test_boot_reads_the_stored_secrets(session_factory, database_url):
    async with session_factory() as session:
        await secret_store.store_secret(session, "AUTOPOSTER_TMDB_TOKEN", "stored-tok")
        await session.commit()

    assert await _in_a_thread(database_url) == {"AUTOPOSTER_TMDB_TOKEN": "stored-tok"}


def test_boot_answers_empty_when_the_database_is_unreachable():
    """The resolver then falls through to the state file and the environment,
    which is exactly how every deployment that predates this behaved."""
    assert (
        boot.stored_secrets_for_boot(
            "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nothing"
        )
        == {}
    )


def test_boot_answers_empty_when_there_is_no_database_url_at_all():
    """The first-start shape: the wizard has not been through yet, so nothing
    names a database and there is nothing to connect to. Answered without an
    engine rather than by failing to build one."""
    assert boot.stored_secrets_for_boot("") == {}


@pytest.mark.asyncio
async def test_boot_answers_empty_when_the_table_does_not_exist_yet(tableless_database_url):
    """The ordinary first boot, and the one failure this function's own
    `except` is uniquely responsible for: the database answers perfectly and
    the migration that creates the table has not run, because it runs AFTER
    this read. A refusal here would mean no deployment could ever migrate.
    """
    assert await _in_a_thread(tableless_database_url) == {}


@pytest.mark.asyncio
async def test_a_database_that_never_answers_does_not_block_the_boot(
    monkeypatch, session_factory, database_url
):
    """A host that accepts the connection and then stops answering -- a paused
    VM, a failing-over pgbouncer, a DROP rule applied after accept -- is the
    case that hangs forever without a bound. Nothing bounds it underneath:
    only asyncpg has an implicit connect bound and no dialect bounds the
    QUERY. The boot decision, the migration and the exec all sit behind this
    read.
    """

    async def never_answers(session):
        await asyncio.sleep(30)
        raise AssertionError("the read must have been abandoned, not waited out")

    monkeypatch.setattr(secret_store, "load_stored_secrets", never_answers)
    monkeypatch.setattr(db_base, "PROBE_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    stored = await _in_a_thread(database_url)
    elapsed = time.monotonic() - started

    assert stored == {}
    assert elapsed < 10, "the read is abandoned on a bound, not waited out"


# --- what the wizard sees ---------------------------------------------------


def _write_state_secrets(values: dict[str, str]) -> None:
    path = state_module.secrets_file_path()
    state_module.write_state_file(path, state_module.render_secrets_file(values))


def _serve_the_wizard(monkeypatch):
    """Run a boot that lands in setup mode and hand back the application it
    would have served.

    Through `boot.main` rather than through `build_setup_app` directly, because
    the claim is about the ORDER inside `main`: the export has to happen before
    the branch, and a test that built the wizard itself would pass with the
    export on the wrong side of that line.
    """
    served: list[object] = []
    monkeypatch.setattr(boot, "_migrate", lambda: None)
    monkeypatch.setattr(boot.os, "execv", lambda path, argv: None)
    monkeypatch.setattr(boot.uvicorn, "run", lambda app, **kwargs: served.append(app))
    boot.main([])
    assert len(served) == 1, "this boot was meant to land in setup mode"
    return served[0]


@pytest.mark.asyncio
async def test_the_first_start_wizard_sees_a_stored_admin_password_hash(
    monkeypatch, session_factory, database_url
):
    """The store holds this deployment's admin hash, and the wizard must find
    it. It has no session of its own, so `boot` publishes what it read before
    it decides anything -- and a wizard that missed the hash would read "no
    password has ever been set" from the empty string and hand a setup token
    to whoever posted first.
    """
    from autoposter.api import setup as setup_api

    hashed = await asyncio.to_thread(hash_password, MASTER_PASSWORD)
    async with session_factory() as session:
        await secret_store.store_secret(session, "AUTOPOSTER_ADMIN_PASSWORD_HASH", hashed)
        await session.commit()
    # Configured but for one hard name, which is what puts this boot in setup
    # mode. The admin hash has nothing to do with that decision, which is
    # exactly why the wizard can be reached on a deployment that has one.
    _write_state_secrets(
        {
            "AUTOPOSTER_DATABASE_URL": database_url,
            "AUTOPOSTER_TMDB_TOKEN": "x",
            "AUTOPOSTER_TVDB_APIKEY": "x",
            "AUTOPOSTER_WEBHOOK_SECRET": "x",
        }
    )
    state_module.write_state_file(
        state_module.state_config_path(),
        yaml.safe_dump({"workers": 2, "plex": {"url": "https://plex.example"}}),
    )

    wizard = await asyncio.to_thread(_serve_the_wizard, monkeypatch)

    assert setup_api._persisted_admin_hash() == hashed
    assert os.environ[STORED_SECRET_NAMES_ENV] == "AUTOPOSTER_ADMIN_PASSWORD_HASH"

    transport = ASGITransport(app=wizard)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        refused = await client.post("/api/setup/password", json={"password": "not-the-one"})
        accepted = await client.post("/api/setup/password", json={"password": MASTER_PASSWORD})

    assert refused.status_code == 401, "a wrong password must not mint a setup token"
    assert "token" not in refused.json()
    assert accepted.status_code == 200, "the real password still gets in"
