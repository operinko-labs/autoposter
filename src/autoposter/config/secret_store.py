"""Secrets in the database, encrypted with a key on the volume (spec section 3).

One table, one row per environment-variable NAME, one key. The key lives at
``$AUTOPOSTER_STATE_DIR/secret.key`` at 0600, is generated on the first WRITE
that needs it, and never enters the database, a response or a log line.

Reading never creates. ``load_key`` is the whole read path and it answers
``None`` for a volume that has no key; only ``store_secret``, through
``encrypt_secret``, calls ``load_or_create_key``. That split is what keeps a
boot whose state volume failed to mount from writing a brand-new key into a
container filesystem, skipping every row against it, and taking the real key's
place the moment the volume comes back.

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
import os
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

# The claim below creates the file itself rather than through
# ``write_state_file``, so it names the mode that module would have given it.
_KEY_FILE_MODE = 0o600


class UndecryptableSecret(Exception):
    """This key cannot open this token: a different volume, or a corrupt row."""


def secret_key_path() -> Path:
    return state_dir() / SECRET_KEY_FILE_NAME


def load_key() -> bytes | None:
    """The key this volume holds, or ``None`` when it holds none.

    Never creates one, which is the whole point of it being separate from
    ``load_or_create_key``: every reader goes through here, and a read that
    created a key would answer a missing volume by inventing one that makes
    the real key's rows unreadable when it returns.

    A file that exists and is not a key raises ``ValueError`` naming the FILE.
    Degrading to "this key cannot open that row" instead would report a
    corrupted key file in exactly the words a lost volume uses, and those are
    the two faults an operator most needs told apart. The message names the
    path and never the contents -- the contents are the key.
    """
    path = secret_key_path()
    try:
        raw = path.read_bytes().strip()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise ValueError(f"{path} cannot be read") from exc
    if not raw:
        return None
    try:
        Fernet(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{path} does not hold an encryption key") from exc
    return raw


def load_or_create_key() -> bytes:
    """The Fernet key, generated on the first WRITE that needs one.

    Two processes can reach this at once on a fresh volume -- boot and the
    application it execs, or two workers -- so the file is CLAIMED with
    ``O_CREAT | O_EXCL`` before anything is written to it, and the key returned
    is always the one re-read from disk rather than the one this call
    generated. ``write_state_file`` is atomic against a reader but
    last-writer-wins against a second writer, so without both halves the loser
    of a race encrypts a row under a key that is no longer anywhere: nothing
    truncates, nothing raises, and that secret is unreadable forever.

    The content is still written through ``write_state_file`` -- atomic, 0600,
    fsynced, in a directory it tightens -- for a stronger reason than the
    secrets file has: a half-written key makes every stored secret unreadable
    at once.

    Not cached in a module global. The state directory is an environment
    variable the suite repoints per test, and a cached key would leak one
    test's volume into the next. Reading a 44-byte file per call is nothing
    beside the work every caller is already doing.
    """
    path = secret_key_path()
    existing = load_key()
    if existing is not None:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _KEY_FILE_MODE))
    except FileExistsError:
        # Another process claimed it between the read above and this call. Its
        # key is the one every reader will agree on, so fall through and take
        # whatever it wrote.
        pass
    else:
        write_state_file(path, Fernet.generate_key().decode("ascii") + "\n")
        # The PATH, never the key.
        logger.info("generated the stored-secret encryption key at %s", path)
    landed = load_key()
    if landed is None:
        # The claim exists but is still empty: whoever made it has not finished
        # writing. Loud, because the alternative is encrypting a credential
        # under a key that is about to be replaced.
        raise ValueError(f"{path} was claimed by another process but holds no key yet")
    return landed


def encrypt_secret(value: str) -> str:
    """``value`` as a Fernet token.

    The bounds are ``config/state.py``'s and are checked HERE rather than only
    at the route, because every writer of this table goes through this
    function. ``is_storable`` is asked in full so that one rule decides what
    every source of a credential may hold. Two of its three refusals bite in
    this path: a value past ``MAXIMUM_SECRET_LENGTH`` fails ``os.execv`` with
    E2BIG and a NUL byte raises ``ValueError: embedded null character``, both
    in a process already committed to the exec, at a boot that therefore serves
    no wizard from which it could be fixed. The third -- the line-break rule --
    is ``read_secrets_file``'s parser rather than this table's, and a line
    break would survive both the environment and the exec unharmed; keeping it
    anyway is what stops a value this store accepts being refused by
    ``secrets.env``, when the two are meant to be interchangeable sources for
    the same name. The length is asked first only so that the common refusal
    says what the bound is.
    """
    if len(value) > MAXIMUM_SECRET_LENGTH:
        raise ValueError(f"a secret may be at most {MAXIMUM_SECRET_LENGTH} characters")
    if not is_storable(value):
        raise ValueError("a secret cannot be stored as it stands")
    return Fernet(load_or_create_key()).encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(key: bytes, token: str) -> str:
    try:
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        # No token text in the message: it is a credential's ciphertext and
        # this exception is rendered by callers that log.
        raise UndecryptableSecret("this key cannot open this stored secret") from exc


def decrypt_secret(token: str) -> str:
    key = load_key()
    if key is None:
        raise UndecryptableSecret("this volume holds no stored-secret encryption key")
    return _decrypt(key, token)


async def load_stored_secrets(session: AsyncSession) -> dict[str, str]:
    """Every stored secret this key can open, by name.

    Nothing raises out of here. It is called during boot, and every fault it
    can meet -- no key, a key file that is not a key, a state directory it
    cannot read, a row this key cannot open -- describes a deployment that must
    still start and report those names as unset, not one that exits non-zero
    with no UI from which it could be fixed.

    A row this key cannot open is SKIPPED with one warning naming the name. A
    key that is missing or unusable is instead ONE warning for the whole table,
    naming the FILE and the NUMBER of rows: per-row warnings there would say
    the same thing once per credential, and the fault is not the rows'.
    """
    rows = (await session.execute(select(StoredSecret))).scalars().all()
    if not rows:
        return {}
    try:
        key = load_key()
    except ValueError as exc:
        # The exception names the file; the count is this function's to add.
        logger.warning(
            "%s, so the %d stored secret(s) cannot be read; the next source "
            "down answers for each instead",
            exc,
            len(rows),
        )
        return {}
    if key is None:
        logger.warning(
            "there is no stored-secret encryption key at %s, so the %d stored "
            "secret(s) cannot be read; the next source down answers for each instead",
            secret_key_path(),
            len(rows),
        )
        return {}
    values: dict[str, str] = {}
    for row in rows:
        try:
            values[row.name] = _decrypt(key, row.ciphertext)
        except UndecryptableSecret:
            # The NAME, never the value and never the token.
            logger.warning(
                "the stored secret %s cannot be decrypted with this deployment's "
                "key; it is ignored, and the next source down answers instead",
                row.name,
            )
    return values


async def store_secret(session: AsyncSession, name: str, value: str) -> None:
    """Set or replace one secret. The caller commits.

    The empty string is refused. ``is_storable`` allows it -- in
    ``secrets.env`` an empty line is simply not a value -- but a ROW is a
    positive statement that this name is set, and an empty one would have the
    Settings page report it as stored while the resolver treats it as absent.
    Clearing a name is ``clear_secret``.
    """
    if not value:
        raise ValueError(f"{name} cannot be stored as an empty value")
    try:
        ciphertext = encrypt_secret(value)
    except ValueError as exc:
        # The refusal is about this NAME's value, and a caller setting several
        # at once needs to know which. The same sentence ``render_secrets_file``
        # uses, and like it the value is never quoted back.
        raise ValueError(f"{name} cannot be stored as it stands") from exc
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
