"""Secrets in the database, encrypted with a key on the volume (spec section 3).

One table, one row per environment-variable NAME, one key. The key lives at
``$AUTOPOSTER_STATE_DIR/secret.key`` at 0600, is generated on the first boot
that needs it, and never enters the database, a response or a log line.

Losing the volume loses the key and therefore every stored secret. That is the
operator's chosen trade: the volume already holds the admin password, and
re-entering keys is the cost of a lost volume rather than of a lost database
dump. This module makes that trade SURVIVABLE rather than fatal -- a row this
key cannot open is skipped, with the NAME logged, so the service still starts
and the Settings page reports the name as unset.

The vocabulary is the environment variable name (``AUTOPOSTER_TMDB_TOKEN``),
because that is the one spelling the resolver, the boot export, the wizard and
the UI already share.
"""
import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.state import (
    MAXIMUM_SECRET_LENGTH,
    SECRET_KEY_FILE_NAME,
    is_storable,
    state_dir,
    write_state_file,
)
from autoposter.db.models import StoredSecret

logger = logging.getLogger(__name__)


class UndecryptableSecret(Exception):
    """This key cannot open this token: a different volume, or a corrupt row."""


def secret_key_path() -> Path:
    return state_dir() / SECRET_KEY_FILE_NAME


def load_or_create_key() -> bytes:
    """The Fernet key, generated on the first call that needs one.

    Written through ``write_state_file``, which is atomic, 0600 and fsynced --
    the same guarantees the secrets file already has, and for a stronger
    reason: a half-written key makes every stored secret unreadable at once.

    Not cached in a module global. The state directory is an environment
    variable the suite repoints per test, and a cached key would leak one
    test's volume into the next. Reading a 44-byte file per call is nothing
    beside the work every caller is already doing.
    """
    path = secret_key_path()
    try:
        existing = path.read_bytes().strip()
    except (FileNotFoundError, NotADirectoryError):
        existing = b""
    if existing:
        return existing
    generated = Fernet.generate_key()
    write_state_file(path, generated.decode("ascii") + "\n")
    # The PATH, never the key.
    logger.info("generated the stored-secret encryption key at %s", path)
    return generated


def encrypt_secret(value: str) -> str:
    """``value`` as a Fernet token.

    The bounds are ``config/state.py``'s and are checked HERE rather than only
    at the route, because every writer of this table goes through this
    function. Both refusals end in the same unrecoverable place: a stored
    secret is published into the process environment and then carried across
    ``os.execv``, where a value past ``MAXIMUM_SECRET_LENGTH`` fails E2BIG and
    a value carrying a NUL byte raises ``ValueError: embedded null character``
    -- in a process already committed to the exec, at a boot that therefore
    serves no wizard from which it could be fixed. ``is_storable`` is the one
    rule for both, so it answers for this table as it does for ``secrets.env``;
    the length is asked first only so that the common refusal says what the
    bound is.
    """
    if len(value) > MAXIMUM_SECRET_LENGTH:
        raise ValueError(f"a secret may be at most {MAXIMUM_SECRET_LENGTH} characters")
    if not is_storable(value):
        raise ValueError("a secret cannot be stored as it stands")
    return Fernet(load_or_create_key()).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return Fernet(load_or_create_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        # No token text in the message: it is a credential's ciphertext and
        # this exception is rendered by callers that log.
        raise UndecryptableSecret("this key cannot open this stored secret") from exc


async def load_stored_secrets(session: AsyncSession) -> dict[str, str]:
    """Every stored secret this key can open, by name.

    A row it cannot open is SKIPPED with one warning naming the name. The
    alternative -- raising -- turns a restored database without its volume
    into a service that will not start and cannot be fixed from its own UI,
    which is exactly the shape ``boot`` exists to avoid.
    """
    rows = (await session.execute(select(StoredSecret))).scalars().all()
    values: dict[str, str] = {}
    for row in rows:
        try:
            values[row.name] = decrypt_secret(row.ciphertext)
        except UndecryptableSecret:
            # The NAME, never the value and never the token.
            logger.warning(
                "the stored secret %s cannot be decrypted with this deployment's "
                "key; it is ignored, and the next source down answers instead",
                row.name,
            )
    return values


async def store_secret(session: AsyncSession, name: str, value: str) -> None:
    """Set or replace one secret. The caller commits."""
    ciphertext = encrypt_secret(value)
    statement = insert(StoredSecret).values(name=name, ciphertext=ciphertext)
    # ``updated_at`` is set explicitly. The column's ``onupdate`` is applied to
    # a Core UPDATE, and the SET clause of an upsert is not one -- without this
    # a replaced secret would keep the timestamp of the value it replaced.
    statement = statement.on_conflict_do_update(
        index_elements=["name"], set_={"ciphertext": ciphertext, "updated_at": func.now()}
    )
    await session.execute(statement)


async def clear_secret(session: AsyncSession, name: str) -> bool:
    """Remove one stored secret; True when there was one. The caller commits."""
    result = await session.execute(delete(StoredSecret).where(StoredSecret.name == name))
    return bool(result.rowcount)


async def stored_secret_names(session: AsyncSession) -> list[str]:
    """The names the store answers, sorted. Names, never values."""
    rows = (await session.execute(select(StoredSecret.name))).scalars().all()
    return sorted(rows)
