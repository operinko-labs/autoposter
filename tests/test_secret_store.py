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
from autoposter.config.schema import (
    SECRET_NAMES,
    STATE_FILE_NAMES_ENV,
    STORED_SECRET_NAMES_ENV,
    resolve_secret_values,
    secret_sources,
    state_file_secret_names,
)
from autoposter.config.state import STATE_DIR_ENV, merge_secrets_file
from autoposter.db.models import StoredSecret

NAME = "AUTOPOSTER_TMDB_TOKEN"


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A state directory nobody else shares, and neither boot marker set.

    The markers are what `secret_sources` falls back to with no session and no
    readable file, so a marker inherited from the container's environment (or
    left behind by a boot test in the same process) would decide a source
    label here instead of this test's own fixture.
    """
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "state"))
    monkeypatch.delenv(STATE_FILE_NAMES_ENV, raising=False)
    monkeypatch.delenv(STORED_SECRET_NAMES_ENV, raising=False)
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


def test_an_existing_key_is_never_written_over(state, monkeypatch):
    planted = Fernet.generate_key()
    state.mkdir(parents=True, exist_ok=True)
    secret_store.secret_key_path().write_bytes(planted + b"\n")

    def no_key_may_be_written(path, text):
        raise AssertionError("a volume that has a key must not be given a second one")

    monkeypatch.setattr(secret_store, "write_state_file", no_key_may_be_written)
    assert secret_store.load_or_create_key() == planted
    assert secret_store.secret_key_path().read_bytes() == planted + b"\n"


def test_a_key_that_arrives_before_the_create_is_not_written_over(state, monkeypatch):
    """The exclusive create, not the read above it, is what protects a key.

    The stub is the other half of the race the re-read covers: another process
    landed its key after this call looked and found nothing. Without
    ``O_EXCL`` the create would succeed and ``write_state_file`` would replace
    that key, leaving every row written under it unreadable.
    """
    planted = Fernet.generate_key()
    state.mkdir(parents=True, exist_ok=True)
    secret_store.secret_key_path().write_bytes(planted + b"\n")

    real_load_key = secret_store.load_key
    looks = []

    def blind_to_the_first_look():
        looks.append(None)
        return None if len(looks) == 1 else real_load_key()

    monkeypatch.setattr(secret_store, "load_key", blind_to_the_first_look)
    assert secret_store.load_or_create_key() == planted
    assert secret_store.secret_key_path().read_bytes() == planted + b"\n"


@pytest.mark.asyncio
async def test_an_empty_key_file_is_a_stale_claim_rather_than_a_wedge(state, session_factory):
    """A claim its writer never filled must not stop this deployment forever.

    The exclusive create fails against such a file and there is no key to read,
    so without taking it over no secret could ever be stored again without an
    operator deleting it by hand.
    """
    state.mkdir(parents=True, exist_ok=True)
    secret_store.secret_key_path().write_bytes(b"")
    async with session_factory() as session:
        await secret_store.store_secret(session, NAME, "tok")
        await session.commit()
    assert secret_store.load_key() is not None
    async with session_factory() as session:
        assert await secret_store.load_stored_secrets(session) == {NAME: "tok"}


def test_a_write_that_fails_leaves_no_claim_behind(state, monkeypatch):
    """A full or read-only volume is a transient fault, and must stay one."""
    real_write_state_file = secret_store.write_state_file

    def the_volume_is_full(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(secret_store, "write_state_file", the_volume_is_full)
    with pytest.raises(OSError):
        secret_store.load_or_create_key()
    assert not secret_store.secret_key_path().exists()

    monkeypatch.setattr(secret_store, "write_state_file", real_write_state_file)
    assert secret_store.load_or_create_key(), "the next attempt is not wedged"


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


@pytest.mark.asyncio
async def test_a_key_file_fault_is_not_reported_as_a_bad_value(state, session_factory):
    """The operator is told which of the two is wrong.

    Naming the variable for a fault that belongs to the key file would send
    someone to check a token that is perfectly good.
    """
    state.mkdir(parents=True, exist_ok=True)
    secret_store.secret_key_path().write_text("this-is-not-a-key\n", encoding="utf-8")
    async with session_factory() as session:
        with pytest.raises(ValueError) as refused:
            await secret_store.store_secret(session, NAME, "a-fine-token")
    assert str(secret_store.secret_key_path()) in str(refused.value)
    assert "cannot be stored as it stands" not in str(refused.value)
    assert "a-fine-token" not in str(refused.value)


# --- precedence: stored, then the state file, then the environment ----------


def test_a_stored_value_wins_over_the_environment_and_the_file(state, monkeypatch):
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", "from-env")
    merge_secrets_file({"AUTOPOSTER_TMDB_TOKEN": "from-file"})
    resolved = resolve_secret_values({"AUTOPOSTER_TMDB_TOKEN": "from-store"})
    assert resolved["AUTOPOSTER_TMDB_TOKEN"] == "from-store"


def test_clearing_a_stored_value_falls_through_to_the_next_source(state, monkeypatch):
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", "from-env")
    assert resolve_secret_values({})["AUTOPOSTER_TMDB_TOKEN"] == "from-env"


def test_the_state_file_wins_over_the_environment(state, monkeypatch):
    """spec section 3's order, which reverses the lower two layers.

    No existing deployment has a name with a value on both layers -- the
    wizard writes to the file only the names the environment did not resolve
    -- so this is a rule about what the UI and the wizard may do from now on,
    not a change to what any running deployment resolves to."""
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", "from-env")
    merge_secrets_file({"AUTOPOSTER_TMDB_TOKEN": "from-file"})
    assert resolve_secret_values()["AUTOPOSTER_TMDB_TOKEN"] == "from-file"


def test_a_name_on_both_layers_reports_the_state_file_as_its_source(state, monkeypatch):
    """The source label and the resolved value must never disagree: a page
    that said "environment" about a value the file supplied would send an
    operator to change the wrong thing."""
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", "from-env")
    merge_secrets_file({"AUTOPOSTER_TMDB_TOKEN": "from-file"})
    assert resolve_secret_values()["AUTOPOSTER_TMDB_TOKEN"] == "from-file"
    assert secret_sources()["AUTOPOSTER_TMDB_TOKEN"] == "state file"


def test_an_empty_stored_value_counts_as_absent(state, monkeypatch):
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", "from-env")
    assert (
        resolve_secret_values({"AUTOPOSTER_TMDB_TOKEN": ""})["AUTOPOSTER_TMDB_TOKEN"]
        == "from-env"
    )


def test_sources_name_every_secret_and_no_values(state, monkeypatch):
    monkeypatch.delenv("AUTOPOSTER_TMDB_TOKEN", raising=False)
    monkeypatch.delenv("AUTOPOSTER_RADARR_APIKEY", raising=False)
    monkeypatch.delenv("AUTOPOSTER_FANART_APIKEY", raising=False)
    monkeypatch.setenv("AUTOPOSTER_TVDB_APIKEY", "from-env")
    merge_secrets_file({"AUTOPOSTER_FANART_APIKEY": "from-file"})
    sources = secret_sources(["AUTOPOSTER_TMDB_TOKEN"])
    assert sources["AUTOPOSTER_TMDB_TOKEN"] == "stored"
    assert sources["AUTOPOSTER_TVDB_APIKEY"] == "environment"
    assert sources["AUTOPOSTER_FANART_APIKEY"] == "state file"
    assert sources["AUTOPOSTER_RADARR_APIKEY"] == "unset"
    assert set(sources) == set(SECRET_NAMES)
    assert set(sources.values()) <= {"stored", "state file", "environment", "unset"}


def test_state_file_secret_names_now_lists_a_name_the_environment_also_carries(
    state, monkeypatch
):
    """`state_file_secret_names` publishes which names the FILE answers, and
    the rotation path reads it. With the file above the environment, a name on
    both layers is answered by the file -- so it belongs on this list, where
    the old environment-first rule excluded it."""
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", "from-env")
    merge_secrets_file({"AUTOPOSTER_TMDB_TOKEN": "from-file"})
    assert "AUTOPOSTER_TMDB_TOKEN" in state_file_secret_names()
    assert "AUTOPOSTER_TMDB_TOKEN" not in state_file_secret_names(
        {"AUTOPOSTER_TMDB_TOKEN": "from-store"}
    )


# --- the sources survive boot's export --------------------------------------
#
# Every caller of `secret_sources` runs in a process `boot._export` has
# already been through, where every winning value is in `os.environ` and every
# name therefore looks like an environment name. These are the cases that
# distinguish a map which reports ORIGIN from one that reports where a value
# happens to sit now.

EXPORTED_HARD = (
    "AUTOPOSTER_DATABASE_URL",
    "AUTOPOSTER_TMDB_TOKEN",
    "AUTOPOSTER_TVDB_APIKEY",
    "AUTOPOSTER_FANART_APIKEY",
    "AUTOPOSTER_WEBHOOK_SECRET",
)


def _as_boot_left_it(monkeypatch, *names: str) -> None:
    """`os.environ` the way `_export` leaves it: every winning value present,
    whichever layer supplied it."""
    for name in (*EXPORTED_HARD, *names):
        monkeypatch.setenv(name, "published-by-the-export")


def test_a_file_supplied_name_is_still_the_state_file_after_the_export(state, monkeypatch):
    """The deployment this route exists for. A wizard-configured process has
    every hard name in its environment because boot put it there, and a map
    that asked `os.environ` first would label the whole deployment
    `environment` -- sending its operator to change a variable nothing reads,
    and refusing the one rotation the Settings page offers."""
    merge_secrets_file({"AUTOPOSTER_WEBHOOK_SECRET": "from-file"})
    _as_boot_left_it(monkeypatch, "AUTOPOSTER_API_KEY")

    sources = secret_sources([])

    assert sources["AUTOPOSTER_WEBHOOK_SECRET"] == "state file"
    assert sources["AUTOPOSTER_API_KEY"] == "environment"


def test_a_stored_name_is_still_stored_after_the_export(state, monkeypatch):
    _as_boot_left_it(monkeypatch)
    merge_secrets_file({"AUTOPOSTER_TMDB_TOKEN": "from-file"})

    sources = secret_sources(["AUTOPOSTER_TMDB_TOKEN"])

    assert sources["AUTOPOSTER_TMDB_TOKEN"] == "stored", "the store outranks the file"
    assert sources["AUTOPOSTER_TVDB_APIKEY"] == "environment"


def test_the_boot_markers_answer_a_caller_with_no_session(state, monkeypatch):
    """No session and no readable state file -- an env-configured deployment
    whose volume is not mounted, or any caller outside a route. The markers
    are what the boot that DID read those sources published, and they are the
    honest answer once neither can be consulted again."""
    _as_boot_left_it(monkeypatch)
    monkeypatch.setenv(STORED_SECRET_NAMES_ENV, "AUTOPOSTER_TMDB_TOKEN")
    monkeypatch.setenv(STATE_FILE_NAMES_ENV, "AUTOPOSTER_WEBHOOK_SECRET")

    sources = secret_sources()

    assert sources["AUTOPOSTER_TMDB_TOKEN"] == "stored"
    assert sources["AUTOPOSTER_WEBHOOK_SECRET"] == "state file"
    assert sources["AUTOPOSTER_TVDB_APIKEY"] == "environment"


def test_a_live_empty_store_overrides_the_stored_marker(state, monkeypatch):
    """A secret cleared from the Settings page since boot. An empty collection
    is not `None`: it means the table was read and holds nothing, and the page
    must say what is true now rather than what was true at the last restart."""
    _as_boot_left_it(monkeypatch)
    monkeypatch.setenv(STORED_SECRET_NAMES_ENV, "AUTOPOSTER_TMDB_TOKEN")

    assert secret_sources()["AUTOPOSTER_TMDB_TOKEN"] == "stored"
    assert secret_sources([])["AUTOPOSTER_TMDB_TOKEN"] == "environment"


def test_an_unreadable_state_file_falls_back_to_its_marker(state, monkeypatch):
    """`read_secrets_file` raises on a file that exists and cannot be read --
    right for the boot path, wrong for a page render. The marker carries the
    same answer boot reached from the same file."""
    merge_secrets_file({"AUTOPOSTER_WEBHOOK_SECRET": "from-file"})
    _as_boot_left_it(monkeypatch)
    monkeypatch.setenv(STATE_FILE_NAMES_ENV, "AUTOPOSTER_WEBHOOK_SECRET")

    def refused(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("autoposter.config.schema.read_secrets_file", refused)

    assert secret_sources([])["AUTOPOSTER_WEBHOOK_SECRET"] == "state file"
