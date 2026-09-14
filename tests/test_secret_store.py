"""Stored secrets: encrypted with one key that never leaves the volume.

Three properties, each with its own test. The key is generated once, at 0600.
A stored value is unreadable from the database alone. And a row this key
cannot open is skipped rather than crashing the boot -- losing the volume
loses the key, which is the trade spec section 3 records, and a service that
cannot start is a worse expression of it than one that reports the name unset.

The same rule applies one level up: a volume with no key at all, or with a key
file that is not a key, is a deployment that still starts. Reads therefore
never create a key -- only a write does -- because a read that created one
would answer a volume that failed to mount by inventing a key, and would then
be the key in place when the real volume came back.
"""
import logging
import os
import stat
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update

from autoposter.config import secret_store
from autoposter.config.state import STATE_DIR_ENV
from autoposter.db.models import StoredSecret

NAME = "AUTOPOSTER_TMDB_TOKEN"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "state"))
    return tmp_path / "state"


def warnings_in(caplog):
    return [record for record in caplog.records if record.levelno == logging.WARNING]


def test_the_key_is_generated_once_at_0600(state):
    first = secret_store.load_or_create_key()
    path = secret_store.secret_key_path()
    assert path.is_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert secret_store.load_or_create_key() == first, "a second call must reuse it"


def test_the_key_returned_is_the_key_on_disk(state, monkeypatch):
    """A race to create the key ends with one key, and every caller has it.

    ``write_state_file`` is atomic against a reader and last-writer-wins
    against a second writer, so the stub below stands in for another process
    landing its key after this call had generated one. Returning the generated
    key there would encrypt the next stored secret under a key that is nowhere
    on disk, and nothing would truncate or raise -- that secret would simply be
    unreadable forever.
    """
    winner = Fernet.generate_key()

    def another_process_wrote_first(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(winner + b"\n")

    monkeypatch.setattr(secret_store, "write_state_file", another_process_wrote_first)
    assert secret_store.load_or_create_key() == winner
    assert secret_store.secret_key_path().read_bytes().strip() == winner


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
        await secret_store.store_secret(session, NAME, "tok")
        await session.commit()
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {NAME: "tok"}
        assert await secret_store.stored_secret_names(session) == [NAME]
    async with session_factory() as session:
        await secret_store.store_secret(session, NAME, "tok2")
        await session.commit()
    async with session_factory() as session:
        assert (await secret_store.load_stored_secrets(session))[
            NAME
        ] == "tok2", "a second set replaces rather than duplicating"
    async with session_factory() as session:
        assert await secret_store.clear_secret(session, NAME) is True
        await session.commit()
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {}
        assert await secret_store.clear_secret(session, NAME) is False


@pytest.mark.asyncio
async def test_a_replaced_secret_moves_its_timestamp(state, session_factory):
    """``updated_at`` follows the value it describes.

    The row is back-dated rather than compared against the clock: what is
    under test is that the column's ``onupdate`` -- which does not fire for an
    upsert's SET clause -- is supplied explicitly, and a container clock that
    steps backwards is not.
    """
    backdated = datetime(2020, 1, 1, tzinfo=UTC)
    async with session_factory() as session:
        await secret_store.store_secret(session, NAME, "tok")
        await session.execute(
            update(StoredSecret).where(StoredSecret.name == NAME).values(updated_at=backdated)
        )
        await session.commit()
    async with session_factory() as session:
        await secret_store.store_secret(session, NAME, "tok2")
        await session.commit()
    async with session_factory() as session:
        row = (await session.execute(select(StoredSecret))).scalar_one()
    assert row.updated_at > backdated


@pytest.mark.asyncio
async def test_the_database_alone_reveals_nothing(state, session_factory):
    async with session_factory() as session:
        await secret_store.store_secret(session, NAME, "tok")
        await session.commit()
    async with session_factory() as session:
        row = (await session.execute(select(StoredSecret))).scalar_one()
    assert "tok" not in row.ciphertext


@pytest.mark.asyncio
async def test_a_row_this_key_cannot_open_is_skipped_not_fatal(state, session_factory, caplog):
    async with session_factory() as session:
        session.add(StoredSecret(name=NAME, ciphertext="not-a-fernet-token"))
        await session.commit()
    # A key that exists, so the row is the fault rather than the volume.
    secret_store.load_or_create_key()
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {}
    assert NAME in caplog.text
    assert "not-a-fernet-token" not in caplog.text


@pytest.mark.asyncio
async def test_a_volume_with_no_key_reads_as_unset_and_stays_keyless(
    state, session_factory, caplog
):
    """A read never creates a key, and never raises out of a boot.

    The shape is a state volume that failed to mount against a database that
    still has its rows. Generating a key here would make every row unreadable
    against a key that vanishes with the container -- and would then be the key
    in place when the real volume came back.
    """
    async with session_factory() as session:
        session.add(StoredSecret(name=NAME, ciphertext="whatever-this-was"))
        await session.commit()
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {}
    assert not secret_store.secret_key_path().exists()
    assert len(warnings_in(caplog)) == 1, "one warning for the table, not one per row"
    assert str(secret_store.secret_key_path()) in caplog.text


@pytest.mark.asyncio
async def test_a_key_file_that_is_not_a_key_names_the_file(state, session_factory, caplog):
    """A corrupt key file is told apart from a lost volume, and by name."""
    state.mkdir(parents=True, exist_ok=True)
    secret_store.secret_key_path().write_text("this-is-not-a-key\n", encoding="utf-8")
    async with session_factory() as session:
        session.add(StoredSecret(name=NAME, ciphertext="whatever-this-was"))
        await session.commit()
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {}
    assert len(warnings_in(caplog)) == 1
    assert str(secret_store.secret_key_path()) in caplog.text
    assert "this-is-not-a-key" not in caplog.text, "the contents of that file are a key"
    with pytest.raises(ValueError):
        secret_store.load_key()


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


@pytest.mark.asyncio
async def test_an_empty_value_is_refused_by_name(state, session_factory):
    """A row states that the name is set; emptying one is what ``clear`` is for."""
    async with session_factory() as session:
        with pytest.raises(ValueError, match=NAME):
            await secret_store.store_secret(session, NAME, "")
        assert await secret_store.stored_secret_names(session) == []


@pytest.mark.asyncio
async def test_a_refused_value_is_reported_against_its_name(state, session_factory):
    async with session_factory() as session:
        with pytest.raises(ValueError, match=NAME) as refused:
            await secret_store.store_secret(session, NAME, "x" * 5000)
    assert "xxx" not in str(refused.value), "the name, never the value"
