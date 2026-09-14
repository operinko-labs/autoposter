"""Stored secrets: encrypted with one key that never leaves the volume.

Three properties, each with its own test. The key is generated once, at 0600.
A stored value is unreadable from the database alone. And a row this key
cannot open is skipped rather than crashing the boot -- losing the volume
loses the key, which is the trade spec section 3 records, and a service that
cannot start is a worse expression of it than one that reports the name unset.
"""
import os
import stat

import pytest
from sqlalchemy import select

from autoposter.config import secret_store
from autoposter.config.state import STATE_DIR_ENV
from autoposter.db.models import StoredSecret


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    return tmp_path


def test_the_key_is_generated_once_at_0600(state):
    first = secret_store.load_or_create_key()
    path = secret_store.secret_key_path()
    assert path.is_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert secret_store.load_or_create_key() == first, "a second call must reuse it"


def test_a_value_is_unreadable_without_the_key(state, tmp_path, monkeypatch):
    token = secret_store.encrypt_secret("a-real-token")
    assert "a-real-token" not in token
    assert secret_store.decrypt_secret(token) == "a-real-token"

    other = tmp_path / "other-volume"
    other.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(other))
    with pytest.raises(secret_store.UndecryptableSecret):
        secret_store.decrypt_secret(token)


@pytest.mark.asyncio
async def test_store_read_and_clear_round_trip(state, session_factory):
    async with session_factory() as session:
        await secret_store.store_secret(session, "AUTOPOSTER_TMDB_TOKEN", "tok")
        await session.commit()
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {
            "AUTOPOSTER_TMDB_TOKEN": "tok"
        }
        assert await secret_store.stored_secret_names(session) == [
            "AUTOPOSTER_TMDB_TOKEN"
        ]
    async with session_factory() as session:
        await secret_store.store_secret(session, "AUTOPOSTER_TMDB_TOKEN", "tok2")
        await session.commit()
    async with session_factory() as session:
        assert (await secret_store.load_stored_secrets(session))[
            "AUTOPOSTER_TMDB_TOKEN"
        ] == "tok2", "a second set replaces rather than duplicating"
    async with session_factory() as session:
        assert await secret_store.clear_secret(session, "AUTOPOSTER_TMDB_TOKEN") is True
        await session.commit()
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {}
        assert await secret_store.clear_secret(session, "AUTOPOSTER_TMDB_TOKEN") is False


@pytest.mark.asyncio
async def test_the_database_alone_reveals_nothing(state, session_factory):
    async with session_factory() as session:
        await secret_store.store_secret(session, "AUTOPOSTER_TMDB_TOKEN", "tok")
        await session.commit()
    async with session_factory() as session:
        row = (await session.execute(select(StoredSecret))).scalar_one()
    assert "tok" not in row.ciphertext


@pytest.mark.asyncio
async def test_a_row_this_key_cannot_open_is_skipped_not_fatal(
    state, session_factory, caplog
):
    async with session_factory() as session:
        session.add(
            StoredSecret(name="AUTOPOSTER_TMDB_TOKEN", ciphertext="not-a-fernet-token")
        )
        await session.commit()
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {}
    assert "AUTOPOSTER_TMDB_TOKEN" in caplog.text
    assert "not-a-fernet-token" not in caplog.text


def test_a_value_too_long_to_store_is_refused(state):
    with pytest.raises(ValueError):
        secret_store.encrypt_secret("x" * 5000)


def test_a_value_the_environment_cannot_carry_is_refused(state):
    """A NUL byte is refused here, not at ``os.execv``.

    ``is_storable`` is the rule for every credential this deployment stores,
    and the boot that publishes a stored secret into ``os.environ`` raises
    ``ValueError: embedded null character`` on one -- in a process that is
    already committed to the exec and cannot be fixed from its own UI.
    """
    with pytest.raises(ValueError):
        secret_store.encrypt_secret("tok\x00en")
