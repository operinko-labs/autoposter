"""Boot reads the store before it decides anything.

Two claims, and they fail independently. A stored secret reaches the boot
resolver at all -- otherwise the store is a table the running service can
write and nothing ever reads. And a store that cannot be read is an empty map
rather than a refusal: no database, no table yet, no key, an unreachable host.
The layers beneath the store are how every deployment that predates it is
configured, so a boot that exited over an empty table would break all of them
at once, with no UI left from which it could be fixed.
"""
import asyncio
import os

import pytest

from autoposter import boot
from autoposter.config import secret_store
from autoposter.config.state import STATE_DIR_ENV


@pytest.fixture
def database_url():
    """The database this pytest process owns.

    ``tests/conftest.py`` exposes no fixture for the URL itself, but it writes
    the per-worker URL it derived back into this variable -- so reading the
    variable here names exactly the database ``session_factory`` writes into,
    under xdist as well as serially.
    """
    return os.environ["AUTOPOSTER_TEST_DATABASE_URL"]


@pytest.mark.asyncio
async def test_boot_reads_the_stored_secrets(
    tmp_path, monkeypatch, session_factory, database_url
):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    async with session_factory() as session:
        await secret_store.store_secret(session, "AUTOPOSTER_TMDB_TOKEN", "stored-tok")
        await session.commit()

    # In a THREAD. `stored_secrets_for_boot` owns an `asyncio.run`, which is
    # correct for the frame it is written for -- `boot.main` is synchronous and
    # runs before uvicorn -- and raises outright from inside a running loop
    # like this test's. Calling it directly here would land in its own
    # catch-all and answer {}, which is indistinguishable from a store that
    # held nothing: the test would pass for the wrong reason the day the read
    # broke.
    stored = await asyncio.to_thread(boot.stored_secrets_for_boot, database_url)

    assert stored == {"AUTOPOSTER_TMDB_TOKEN": "stored-tok"}


def test_boot_answers_empty_when_the_database_is_unreachable(tmp_path, monkeypatch):
    """No database, no table, no key: none of them may stop a boot. The
    resolver then falls through to the state file and the environment, which
    is exactly how every deployment that predates this behaved."""
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))

    assert (
        boot.stored_secrets_for_boot(
            "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nothing"
        )
        == {}
    )


def test_boot_answers_empty_when_there_is_no_database_url_at_all(tmp_path, monkeypatch):
    """The first-start shape: the wizard has not been through yet, so nothing
    names a database and there is nothing to connect to. Answered without an
    engine rather than by failing to build one."""
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))

    assert boot.stored_secrets_for_boot("") == {}
