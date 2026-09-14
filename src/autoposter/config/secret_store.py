"""Secrets in the database, encrypted with a key on the volume (spec section 3).

One table, one row per environment-variable NAME, one key. The key lives at
``$AUTOPOSTER_STATE_DIR/secret.key`` at 0600, is generated on the first WRITE
that needs it, and never enters the database, a response or a log line.

Reading never creates. ``load_key`` is the whole read path and it answers
``None`` for a volume that has no key; only the encryption step, which nothing
but ``store_secret`` and ``encrypt_secret`` reach, calls
``load_or_create_key``. That split is what keeps a
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
    prepare_state_directory,
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


def _claim_and_fill(path: Path) -> bytes | None:
    """Create ``path`` exclusively, fill it with a key, and return what landed.

    ``None`` when the file already existed and holds no key: either a claim
    another process is still filling, or one it left behind.

    The claim is the exclusive create, and it is what makes an existing key
    untouchable -- the read above this is only an optimisation, and a key that
    appears between that read and this call must not be written over.
    """
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _KEY_FILE_MODE))
    except FileExistsError:
        # Someone else holds it. Their key is the one every reader will agree
        # on, so take whatever is there rather than writing a second one.
        return load_key()
    try:
        write_state_file(path, Fernet.generate_key().decode("ascii") + "\n")
    except BaseException:
        # Including KeyboardInterrupt. An empty claim is a file no later call
        # can get past -- the exclusive create fails against it and there is no
        # key to read -- so a full or read-only volume would wedge key creation
        # on this deployment permanently. Unlinking is what keeps a transient
        # write failure transient.
        path.unlink(missing_ok=True)
        raise
    # The PATH, never the key.
    logger.info("generated the stored-secret encryption key at %s", path)
    return load_key()


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

    An empty file is a claim with no key in it. The second attempt below is
    what stops one becoming permanent: a writer killed between the claim and
    the write leaves a file that every later call would fail the exclusive
    create against and find nothing in, so no secret could ever be stored
    again without an operator deleting it by hand.

    Not cached in a module global. The state directory is an environment
    variable the suite repoints per test, and a cached key would leak one
    test's volume into the next. Reading a 44-byte file per call is nothing
    beside the work every caller is already doing.
    """
    path = secret_key_path()
    existing = load_key()
    if existing is not None:
        return existing
    # Through ``config/state.py`` rather than a bare ``mkdir``: the claim below
    # creates its own file, so without this the 0700 on a fresh volume would
    # wait for the first ``write_state_file`` and the key would be claimed in a
    # directory whose mode came from the umask.
    prepare_state_directory(path.parent)
    landed = _claim_and_fill(path)
    if landed is not None:
        return landed
    # A claim with no key in it. Take it for this process and fill it.
    path.unlink(missing_ok=True)
    landed = _claim_and_fill(path)
    if landed is not None:
        return landed
    # Still empty: another process holds the claim and is mid-write. Loud and
    # self-clearing -- by the time this is retried the winner's key is on disk
    # -- because the alternative is encrypting a credential under a key that is
    # about to be replaced.
    raise ValueError(f"{path} exists and holds no key; remove that file and try again")


def _unstorable_reason(value: str) -> str | None:
    """Why this deployment cannot store ``value``, or ``None`` when it can.

    A function rather than two raises, because ``store_secret`` has to know
    whether a ``ValueError`` from ``encrypt_secret`` was about the VALUE or
    about the KEY FILE, and the two are indistinguishable once raised.
    """
    if len(value) > MAXIMUM_SECRET_LENGTH:
        return f"a secret may be at most {MAXIMUM_SECRET_LENGTH} characters"
    if not is_storable(value):
        return "a secret cannot be stored as it stands"
    return None


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

    Raises ``ValueError`` for a value it refuses and, from
    ``load_or_create_key``, for a key file it cannot read or create.
    """
    reason = _unstorable_reason(value)
    if reason is not None:
        raise ValueError(reason)
    return _encrypt(value)


def _encrypt(value: str) -> str:
    """The encryption itself, for a value a caller has already checked.

    Split out of ``encrypt_secret`` above so that ``store_secret``, which asks
    ``_unstorable_reason`` for itself -- it has a NAME to put in the refusal
    and has to tell a bad value from a bad key file -- does not make the same
    call twice over.
    """
    return Fernet(load_or_create_key()).encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(key: bytes, token: str) -> str:
    try:
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        # No token text in the message: it is a credential's ciphertext and
        # this exception is rendered by callers that log.
        raise UndecryptableSecret("this key cannot open this stored secret") from exc


def decrypt_secret(token: str) -> str:
    """The plaintext behind ``token``.

    A caller has two failures to handle, and they are different faults:
    ``UndecryptableSecret`` when this volume's key cannot open the TOKEN -- a
    row written on another volume, a corrupt row, or a volume with no key at
    all -- and ``ValueError`` when the KEY FILE itself is unreadable or is not
    a key, which names the file and is the operator's to fix rather than the
    row's. A route that catches only the first answers 500 to the second.
    """
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
    the same thing once per credential, and the fault is not the rows'. Both of
    the exceptions a caller of ``decrypt_secret`` must handle --
    ``UndecryptableSecret`` for a token and ``ValueError`` for the key file --
    are answered here rather than raised, which is what "nothing raises out of
    here" means in practice.
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

    A refusal names the variable, because a caller setting several at once
    needs to know which. A ``ValueError`` about the KEY FILE travels on
    untouched: it is not this value's fault, its message names the file, and
    rewriting it would tell the operator their token is malformed when the
    fault is the volume's.
    """
    if not value:
        raise ValueError(f"{name} cannot be stored as an empty value")
    if _unstorable_reason(value) is not None:
        # The same sentence ``render_secrets_file`` uses, and like it the
        # value is never quoted back.
        raise ValueError(f"{name} cannot be stored as it stands")
    ciphertext = _encrypt(value)
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
