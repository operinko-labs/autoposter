"""The configuration store: the single row that holds the configuration.

The mounted ``autoposter.yaml`` is delivered by Flux from git and is read-only
in the pod, so the UI's edits cannot go back into it. The configuration lives
in the single-row ``config_overrides`` table instead (``db/models.py``), and
what that row holds is what runs: the whole document, validated through the
same ``build_config`` as a file-only load.

The file seeds an empty store once and is not read at boot again, which is
what makes it removable -- a deployment whose store is seeded needs no file.
A row written before the store held whole documents holds a delta instead
(``STORE_FORMAT``), and is merged over the file exactly as it always was until
it is converted.
"""
import hashlib
import json
import logging
from pathlib import Path
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.loader import COMPUTED_PATHS, build_config, read_config_document
from autoposter.config.schema import Config
from autoposter.db.models import ConfigOverride

logger = logging.getLogger(__name__)

# The single-document table's only row.
OVERRIDES_ROW_ID = 1

#: The advisory-lock key the write path takes when there is no row to lock yet.
#:
#: Pinned by value rather than derived at import, for the reason
#: ``EMPTY_DOCUMENT_REVISION`` is: two pods running different keys would not
#: serialise against each other, so a change to it must be a deliberate act
#: with a red test in front of it. The value is the top 63 bits of
#: ``sha256(b"autoposter.config_overrides.insert")`` -- an arbitrary number,
#: but one derived from what it protects, so it cannot collide by accident
#: with an advisory lock some other part of this database picks by hand.
#: PostgreSQL's advisory locks share one cluster-wide namespace per database
#: and nothing else in this codebase takes one.
OVERRIDES_INSERT_LOCK_KEY = 4907594664404778877

# Sections that left the config schema behind and are dropped out of a stored
# overrides document instead of being refused with it.
#
# ``version_check`` was live-editable in the settings editor right up to the
# commit that took its three fields out of the config file, so any deployment
# whose operator ever saved that section has the key sitting in this table's
# one row. The update check takes no configuration at all now
# (``api/version.py``), so the key has nowhere to migrate to and is simply
# dropped. ``Config``'s
# ``_version_check_moved_to_an_env_var`` validator refuses the key in *any*
# document it validates, and the merged document goes through the same
# validator -- so leaving it in place would brick the pod at boot with a
# message telling the operator to edit a YAML file that does not carry the
# key, while the editor that could clear the row sits behind an app that will
# not start.
#
# The file half of that refusal stands: a mounted ``autoposter.yaml`` with a
# ``version_check:`` block is an operator-actionable error, and being told to
# delete it is the point. This half is not actionable, so it self-heals
# instead: the key is dropped with one WARNING, and because the editor can no
# longer produce it, the next save of the overrides document writes it out of
# existence for good.
MIGRATED_SECTIONS = ("version_check",)


def document_revision(document: dict) -> str:
    """A token identifying exactly this document's *content*.

    Sent to every editor with its seed and sent back with its save, so the
    write path can refuse a document composed against a store that has since
    moved (the 2026-09-01 lost update). The alternative token -- the row's own
    ``updated_at`` -- is wrong twice over. It moves on a no-op rewrite, so an
    unchanged document would invalidate every open page for nothing; and this
    project has a recorded deployment whose container clock steps ~2.7s
    backwards every ~27s, which would make a timestamp token travel backwards.
    A content hash has neither problem and has the right semantics besides:
    two writers that independently produced the same document are not in
    conflict, because there is nothing to lose.

    Canonical dump -- sorted keys, no whitespace -- so the token depends on
    what the document says and not on how it was serialised on the way in.
    """
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: What a fresh deployment serves. A real token rather than null, so an editor
#: seeding against an empty store carries the same shape as every other one.
EMPTY_DOCUMENT_REVISION = document_revision({})


def _reject_secrets(document: dict, path: str = "") -> None:
    """A ``secrets`` key anywhere in the overrides is a hard error.

    Secrets come from the environment (``Secrets.from_env``) and are never part
    of the config document. Storing one here would write a token into a
    database row and into every config API response that echoes the overrides,
    so this refuses rather than merging it and relying on a redactor further
    down the line to keep it out of sight.

    ``libraries`` (roadmap row 92) is keyed on Plex library NAMES, not config
    section names, so a library literally named ``secrets`` is a real library
    rather than an attempt to smuggle one into the document -- the check is
    exempt at that one level (``path == "libraries"``) and still walks that
    library's own settings normally.
    """
    for key, value in document.items():
        where = f"{path}.{key}" if path else str(key)
        if key == "secrets" and path != "libraries":
            raise ValueError(
                f"config overrides must not contain secrets (found at {where}); "
                "secrets come from the environment only"
            )
        if isinstance(value, dict):
            _reject_secrets(value, where)


def merge_overrides(base: dict, overrides: dict) -> dict:
    """Deep-merge ``overrides`` over ``base``, returning a new dict.

    Nested mappings merge key by key; everything else -- scalars and lists
    alike -- is replaced wholesale. Lists are replaced rather than concatenated
    because every list in this config is a complete statement of intent
    (``providers.order``, ``plex.excluded_libraries``,
    ``artwork.poster.language_order``): an operator removing a provider from
    the order must get a shorter list, not the same one back.

    Neither argument is mutated.
    """
    _reject_secrets(overrides)
    return _merge(base, overrides)


def _merge(base: dict, overrides: dict) -> dict:
    merged = dict(base)
    for key, value in overrides.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge(existing, value)
        else:
            merged[key] = value
    return merged


def _model_for(annotation) -> type[BaseModel] | None:
    """The pydantic model behind a field annotation, if there is one.

    Unwraps unions so ``text: TextStyle | None`` is recognised as a nested
    model rather than an opaque scalar -- ``artwork.poster.text.font_size``
    (which does not exist) has to be reportable at full depth, and a walk that
    stopped at optional sub-models would wave through every typo beneath one.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation):
        model = _model_for(arg)
        if model is not None:
            return model
    return None


def _mapping_model_for(annotation) -> type[BaseModel] | None:
    """The model behind a ``dict[str, Model]`` annotation, if there is one.

    ``_model_for`` above answers "is there a model named anywhere in here",
    which is the right question for ``text: TextStyle | None`` and the WRONG
    one for ``libraries: dict[str, LibraryOverride]``: it recurses through
    ``get_args`` and hands back ``LibraryOverride``, so the walk below would
    descend with a Plex LIBRARY NAME in a field name's place and report
    ``libraries.Movies`` as an unknown setting -- a 422 on every save that
    names a real library (roadmap row 92).

    So the mapping shape is recognised first, and separately. ``_model_for``
    is left exactly as it is: its contract is "the model this annotation
    names", and narrowing it here would be a silent behaviour change to a
    helper this walk is not the only judge of.
    """
    origin = get_origin(annotation)
    if origin is dict:
        args = get_args(annotation)
        return _model_for(args[1]) if len(args) == 2 else None
    if origin in (Union, UnionType):
        for arg in get_args(annotation):
            found = _mapping_model_for(arg)
            if found is not None:
                return found
    return None


def unknown_key_paths(document: dict, model: type[BaseModel] = Config, path: str = "") -> list[str]:
    """Dotted paths in ``document`` that no field of the config schema matches.

    Pydantic ignores unknown keys, so without this a typo'd override is stored,
    merged, validated and applied -- and does nothing, silently, with the
    default still in force. That is the exact failure
    ``tests/test_example_config_matches_schema.py`` exists to catch in the
    YAML file; this is the same check for the write path, at full depth rather
    than one level.

    The schema is *not* switched to ``extra="forbid"`` to get this. The file
    loader validates the same model, and a deployment whose mounted YAML has
    picked up a stray key would then fail to boot rather than ignoring it --
    a much worse failure than the one being fixed, and not this endpoint's
    call to make.

    A dict under a field that is not a nested model is left alone: that is a
    type error, and pydantic's own message for it is better than anything this
    walk could say.

    A dict under a field whose annotation is ``dict[str, Model]`` is walked
    key by key instead, with the key as a path segment: the keys there are
    data (Plex library names, roadmap row 92), so a key is never reported and
    a typo BENEATH one is reported at full depth.
    """
    unknown: list[str] = []
    for key, value in document.items():
        where = f"{path}.{key}" if path else str(key)
        field = model.model_fields.get(key)
        if field is None:
            unknown.append(where)
            continue
        mapping = _mapping_model_for(field.annotation)
        if mapping is not None:
            # Checked BEFORE `_model_for`, which would answer with this
            # mapping's value model and make the walk read every key as a
            # field name. Each key is DATA -- a Plex library name -- so it
            # becomes a path segment and is never itself reportable; what is
            # reportable is a typo inside one.
            if isinstance(value, dict):
                for name, entry in value.items():
                    if isinstance(entry, dict):
                        unknown.extend(
                            unknown_key_paths(entry, mapping, f"{where}.{name}")
                        )
            continue
        nested = _model_for(field.annotation)
        if nested is not None and isinstance(value, dict):
            unknown.extend(unknown_key_paths(value, nested, where))
    return unknown


def document_paths(document: dict, path: str = "") -> list[str]:
    """Every dotted leaf path the overrides document sets.

    What the editor renders as "overridden" beside a value. A nested mapping
    is walked rather than reported, because ``artwork`` being present says
    nothing; ``artwork.title_card.season_label`` is the claim an operator made.
    """
    paths: list[str] = []
    for key, value in document.items():
        where = f"{path}.{key}" if path else str(key)
        if isinstance(value, dict) and value:
            paths.extend(document_paths(value, where))
        else:
            paths.append(where)
    return paths


def empty_leaf_paths(document: dict, path: str = "") -> list[str]:
    """Every dotted path where the document sets an object to ``{}``.

    Hand-crafted only -- no caller in this codebase can produce one -- but
    ``document_paths`` cannot report it honestly: its own ``isinstance(value,
    dict) and value`` check is false for an empty dict, so it falls through to
    the leaf branch and reports the WHOLE section (``artwork``, not
    ``artwork.<setting>``) as a single override. Saving that seeds the editor
    into storing the section wholesale on the very next save. The write path
    refuses these outright rather than silently reinterpreting them -- see
    ``api/routes.py::_validated_generation``.
    """
    paths: list[str] = []
    for key, value in document.items():
        where = f"{path}.{key}" if path else str(key)
        if isinstance(value, dict):
            if value:
                paths.extend(empty_leaf_paths(value, where))
            else:
                paths.append(where)
    return paths


def without_migrated_sections(document: dict) -> dict:
    """``document`` minus any ``MIGRATED_SECTIONS`` key, warning once per key.

    Public because the stored document is no longer its only source: a restored
    snapshot is a raw row this function never ran over, and a snapshot taken
    before a section left the schema still holds it (config/snapshots.py).

    Top-level only, and deliberately so: these are whole sections that left the
    schema, not individual settings, and a walk looking for the name at depth
    would strip an operator's legitimately-named subkey somewhere else in the
    tree.

    Returns the same object when there is nothing to drop, so the ordinary
    deployment -- every one whose operator never touched the section -- pays a
    membership test and allocates nothing.
    """
    present = [key for key in MIGRATED_SECTIONS if key in document]
    if not present:
        return document
    for key in present:
        logger.warning(
            "dropping stale %s from stored overrides -- the update check "
            "takes no configuration now; this warning disappears once the "
            "stored overrides are next saved", key,
        )
    return {key: value for key, value in document.items() if key not in present}


#: The store's own version, kept in the row's ``meta`` and nowhere else.
#:
#: 1 -- which is what an absent key means -- is the DELTA the row held while
#: the mounted file owned the defaults: a partial document merged over it on
#: every load. 2 is the whole configuration document, which ``build_config``
#: validates directly and which is what runs.
STORE_FORMAT = 2


def store_meta(format_: int = STORE_FORMAT, restart_list: list[str] | None = None) -> dict:
    """The metadata column's value, built in one place.

    The restart list is omitted rather than written empty: an absent key and
    an empty list mean the same thing to every reader, and omitting it keeps
    the ordinary row to one key.

    The keyword is spelled differently from the ``restart_paths`` reader below
    on purpose, so that neither name hides the other inside this module.
    """
    meta: dict = {"format": format_}
    if restart_list:
        meta["restart_paths"] = list(restart_list)
    return meta


def restart_paths(meta: dict) -> list[str]:
    """The frozen paths waiting for a restart. Sorted, never ``None``.

    An absent key, an empty list and a row with no metadata at all all mean
    "nothing is waiting", so every reader gets one shape back and none of them
    has to know which of the three it is looking at.
    """
    return sorted(meta.get("restart_paths") or [])


def with_restart_paths(meta: dict, paths: list[str]) -> dict:
    """``meta`` carrying ``paths`` as its restart list, sorted and deduplicated.

    A REPLACEMENT rather than a union, and it has to be. The list is always
    computed against the generation this process BOOTED on, and every entry
    already on it was computed against that same generation -- the boot itself
    empties the list -- so the set handed in here is the whole truth about what
    a restart would change. A union could only ever keep a path whose value has
    just been put back to the one that is running, which is a notice asking for
    a restart that would do nothing. Two saves of two frozen sections still
    leave both paths, because the second save differs from the boot in both.

    The key is dropped rather than written empty when nothing is waiting,
    which is what ``store_meta`` writes for the same state.
    """
    waiting = sorted(set(paths))
    if not waiting:
        return {key: value for key, value in meta.items() if key != "restart_paths"}
    return {**meta, "restart_paths": waiting}


async def store_row(
    session: AsyncSession, *, for_update: bool
) -> ConfigOverride | None:
    """The store's single row, or ``None``, under the lock the caller asked for.

    Split out from ``load_store`` because two writers need the row itself:
    what a read strips out of the document is not what decides whether the
    store has ever been written, and "has it ever been written" is the
    question both the seed and the format stamp turn on. A row holding
    nothing but sections that left the schema reads as an empty document and
    is not an empty store.

    ``for_update`` takes a row lock, and only a write path passes it: a reader
    that locked would serialise ``GET /api/config`` behind every save for no
    benefit.

    When there is no row yet, ``SELECT ... FOR UPDATE`` has nothing to lock, so
    the row lock alone left one hole: two *simultaneous* first-ever saves on a
    fresh deployment both read ``{}``, both found ``EMPTY_DOCUMENT_REVISION``
    current, and the loser's entire first save was replaced wholesale by the
    winner's upsert -- after it had already been answered 200. That case is the
    *least* bounded of all, not the most: ``_drop_refusal`` returns ``None``
    the moment the stored document is empty ("nothing to destroy"), so the drop
    cap short-circuits for both writers and bounds nothing.

    So the no-row path takes a transaction-scoped advisory lock and reads
    again. The second read is what does the work: the first writer's row is
    committed by the time the second acquires the lock, so the second sees it,
    its ``EMPTY_DOCUMENT_REVISION`` no longer matches, and it gets the same 409
    every other stale writer gets. The row-present path is untouched -- the
    lock is taken only on a store that has never been written. ``seed_store``
    leans on the same property for the same reason: two pods booting against
    one fresh database must not both decide the store is empty.

    Two properties this leans on, named because a future change to either would
    reopen the hole silently. The re-read must see a row committed after this
    transaction began, which is READ COMMITTED's per-statement snapshot
    (``db/base.py`` sets no ``isolation_level``); under REPEATABLE READ it
    would come back empty. And the lock must be released by the commit that
    makes the row visible, which is what ``_xact_`` means -- a session-scoped
    advisory lock would leak on the 409 path.
    """
    statement = select(ConfigOverride).where(ConfigOverride.id == OVERRIDES_ROW_ID)
    if for_update:
        # The write path reads and compares and writes as one step. Without the
        # lock, two overlapping transactions can both read the same document,
        # both find their expected revision current, and both write -- which is
        # the lost update this whole check exists to stop, just narrower.
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if for_update and row is None:
        # Nothing was locked, because there was nothing to lock. Serialise the
        # first-ever write on the key instead, then look again: whoever gets
        # here second is now looking at whoever got here first.
        await session.execute(
            select(func.pg_advisory_xact_lock(OVERRIDES_INSERT_LOCK_KEY))
        )
        row = await session.scalar(statement)
    return row


def _row_document(row: ConfigOverride | None) -> dict:
    """A row's document, checked, and stripped of sections that left the schema.

    The strip lives here rather than at any later point, because this is the
    single seam every reader of the stored document comes through: the
    boot-time load that would otherwise refuse to validate, and
    ``GET /api/config``, which would otherwise serve a setting the editor no
    longer renders.
    """
    if row is None or not row.document:
        return {}
    if not isinstance(row.document, dict):
        # Only the config write path ever writes this column and it only ever
        # writes an object, so this is unreachable through the application --
        # but JSONB will hold a list or a bare scalar quite happily if someone
        # edits the row by hand, and validation would then fail with an
        # AttributeError from three frames down. Say what is actually wrong
        # instead.
        raise ValueError(
            "the config_overrides document must be a JSON object, not "
            f"{type(row.document).__name__}"
        )
    return without_migrated_sections(row.document)


def store_contents(row: ConfigOverride | None) -> tuple[dict, dict]:
    """One row's document and metadata, as every reader of the store sees them.

    Taken apart from the read so that a caller holding the row -- because it
    has to know whether there is one -- does not have to read it twice to
    learn what it says.

    An existing row keeps its metadata even when its document is empty. The
    two are not the same fact: the restart list lives in the metadata and
    outlives whatever the document happens to hold, so answering ``{}`` for it
    would invite a caller that found no document to throw the list away too.
    ``_row_document`` already answers ``{}`` for an empty document.
    """
    if row is None:
        return {}, {}
    meta = row.meta if isinstance(row.meta, dict) else {}
    return _row_document(row), meta


async def load_store(
    session: AsyncSession, *, for_update: bool = False
) -> tuple[dict, dict]:
    """The stored document and its metadata, or ``({}, {})`` when there is no row.

    An empty store is the pre-configuration state of every deployment, and it
    is the one state that sends the loader looking at the mounted file. A row
    whose every section has left the schema reads as an empty *document* here
    and cannot be told apart from no row at all -- a caller that needs the
    difference asks ``store_row`` for the row instead.
    """
    return store_contents(await store_row(session, for_update=for_update))


async def load_overrides_document(
    session: AsyncSession, *, for_update: bool = False
) -> dict:
    """The stored document alone, for the readers that want only that.

    ``GET /api/config``, the export, the collection and playlist builders and
    the write path's read-compare-write all ask this question and have no use
    for the row's metadata.
    """
    document, _meta = await load_store(session, for_update=for_update)
    return document


async def write_store(session: AsyncSession, document: dict, meta: dict) -> None:
    """The upsert. One row, ``id=1``, enforced by the table's CHECK.

    Not committed here: a caller writes the store as part of a larger
    transaction -- with a snapshot of what it replaced, or with the emptiness
    test that chose it -- and a commit in the middle of one of those would be
    exactly the half-applied write the snapshot exists to prevent.
    """
    statement = insert(ConfigOverride).values(
        id=OVERRIDES_ROW_ID, document=document, meta=meta
    )
    statement = statement.on_conflict_do_update(
        index_elements=["id"],
        set_={"document": document, "meta": meta, "updated_at": func.now()},
    )
    await session.execute(statement)


async def clear_restart_paths(session: AsyncSession) -> None:
    """Forget the restart list. Called by the boot, and by nothing else.

    Under the row lock, because the save path takes the same one and the two
    must not interleave: a save that added a path between an unlocked read
    here and the write would have its claim erased by a boot that never
    applied it.

    The METADATA column only, and the document is neither read nor written.
    Two reasons, and both are about a write nobody asked for: a read strips
    the sections that have left the schema, so writing the document back would
    persist that strip with no snapshot and no audit row behind it; and a
    hand-edited document that is not a JSON object refuses to be read at all,
    which would turn the last thing standing between a bad row and a bootable
    deployment into an error. The rest of the metadata -- the format above
    all, whose loss would relabel a whole document as a delta -- is kept.

    Not committed here, for ``write_store``'s reason: the caller owns the
    transaction. Nothing is written when the list is already empty, so the
    ordinary boot leaves the row untouched.
    """
    row = await store_row(session, for_update=True)
    if row is None:
        return
    meta = row.meta if isinstance(row.meta, dict) else {}
    if not restart_paths(meta):
        return
    row.meta = with_restart_paths(meta, [])


async def seed_store(session: AsyncSession, document: dict) -> dict:
    """Write ``document`` as the store, once, and answer what the store holds.

    The emptiness test is taken under the row lock, so two pods starting at the
    same instant against a fresh database cannot both seed -- the second reads
    the first's row and gets it back, and validates and runs that one.

    Emptiness is judged on the raw row -- no row at all, or a row holding
    nothing -- and not on the document a read hands back. A row whose every
    section has left the schema strips to ``{}`` but is still a store somebody
    wrote, and its metadata, which is where the restart list lives, is not
    this function's to replace. Such a row is returned as the ``{}`` it strips
    to, and the caller routes it the way it routes any other stored document.

    A row can exist and still hold nothing -- a document of literally ``{}``.
    The seed is right to fill that document in, and the format it writes is
    the one this seed validates against, but the restart list beside it is
    still not this function's to throw away.

    This is the one write in this module that takes no snapshot, because there
    is nothing to snapshot: the store was empty. That is also why it needs no
    reason string -- a snapshot records what a write displaced, and this one
    displaces nothing.
    """
    row = await store_row(session, for_update=True)
    if row is not None and row.document:
        return _row_document(row)
    _empty, meta = store_contents(row)
    await write_store(session, document, store_meta(restart_list=restart_paths(meta)))
    return document


#: The ``ConfigOverrideSnapshot.reason`` the one-time delta conversion writes.
MIGRATE_REASON = "migrate"

#: What a delta-era store with no readable file is told, in one place.
#:
#: Two callers refuse that state -- this module at boot and the config write
#: path -- and an operator who meets one and then the other must not be given
#: two different accounts of the same unrecoverable store.
DELTA_WITHOUT_FILE = (
    "the configuration store holds a delta from before the store became the "
    "document, and the configuration file it was a delta of cannot be read; "
    "restore the file and start again"
)


async def migrate_delta_to_document(session: AsyncSession, base: dict | None) -> dict:
    """Turn a delta-era store into a document, once, and answer what it holds.

    The merge is exactly what the effective configuration was computed to be
    at every load while the row held a delta: the mounted document with the
    delta over it. Nothing is recomputed and nothing is dropped, so this
    deployment runs the configuration it ran yesterday across the boot that
    converts it.

    The delta is read HERE, from the locked row, rather than taken from the
    caller that noticed the row was one. The caller's read is not under the
    lock, so a save committed between the two -- the pod this one is replacing
    is still serving the editor -- would otherwise be converted away: the
    merge would carry the delta the caller saw, and the save would be gone
    from the store with no trace but its own snapshot. What the lock holds is
    what is converted.

    The SNAPSHOT comes first and carries format 1, which is what makes the
    conversion undoable: restoring it re-runs the merge the delta described,
    so an operator who dislikes the result is one restore from the delta they
    had -- as the store reads it, which is to say stripped of any section that
    has left the schema, those being unrestorable into a document that
    validates. A delta with nothing left in it -- ``{}``, because every
    section it held has left the schema -- snapshots nothing, there being no
    earlier state to restore to, and converts to the file's own document.

    The row is taken under its LOCK before anything is read for the merge or
    written, and one that says format 2 by then is returned as it stands
    rather than converted: somebody else has already done this, and their
    document is the store. This is a read-modify-write over the row every save
    also writes, and ``seed_store`` closes the same window on the same row the
    same way.

    The merged document is validated BEFORE it is written rather than by the
    caller afterwards. This is the last load that reads the file, so a
    converted store the schema refuses could never be repaired -- the
    application would not start, and the editor that could clear the row sits
    behind the application. Refusing here leaves the delta and the file both
    standing, which is a state an operator can act on.

    ``base`` is ``None`` when there is no mounted file, and then the delta is
    refused rather than promoted. A delta is a statement ABOUT a document;
    without that document, writing it as the whole configuration would
    silently default every key the file used to carry -- a service that boots
    and does the wrong thing, which is worse than one that says why it will
    not.
    """
    # Imported here rather than at module scope: config/snapshots.py reads
    # this module's ``document_paths``, so a top-level import would close a
    # cycle that neither file can carry.
    from autoposter.config.snapshots import capture_snapshot

    if base is None:
        raise ValueError(DELTA_WITHOUT_FILE)
    row = await store_row(session, for_update=True)
    meta = row.meta if row is not None and isinstance(row.meta, dict) else {}
    if meta.get("format") == STORE_FORMAT:
        # Somebody else got here first. What they wrote is the store. Commit
        # so the row lock is released now rather than at the end of a
        # caller's session -- a CLI run holds one for hours.
        document = _row_document(row)
        await session.commit()
        return document
    delta = _row_document(row)
    merged = merge_overrides(base, delta)
    _validated(merged)
    await capture_snapshot(session, delta, MIGRATE_REASON, format=1)
    # What the row already said is carried through rather than replaced. Only
    # the format is this write's to set; the restart list is not its to throw
    # away.
    await write_store(session, merged, {**meta, **store_meta()})
    await session.commit()
    logger.warning(
        "the stored configuration was a delta and has been merged into a whole "
        "document; the previous delta is kept as a config snapshot"
    )
    return merged


def changed_paths(before: dict, after: dict, prefix: str = "") -> list[str]:
    """Dotted paths whose value differs between two documents, both directions.

    Both directions, because a key one document no longer carries has changed
    just as much as one whose value moved.

    Lifted here from ``api/routes.py::_changed_paths`` so the drift report and
    the stale-save 409 count the same things; that function now delegates and
    there is exactly one copy.
    """
    changed: list[str] = []
    for key in set(before) | set(after):
        where = f"{prefix}.{key}" if prefix else str(key)
        old, new = before.get(key), after.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            changed.extend(changed_paths(old, new, where))
        elif old != new:
            changed.append(where)
    return changed


def _comparable(document: dict) -> dict | None:
    """The configuration a document DESCRIBES, or ``None`` if it describes none.

    Two documents that mean the same thing are rarely spelled the same way. The
    mounted YAML is sparse -- it states what its author cared about and lets the
    schema default the rest -- while what the editor stores is a whole model
    dump, every key present. Comparing those two raw would report a difference
    at every setting the file simply does not mention, which is most of them.

    So both sides are built and dumped, and the comparison is between the two
    effective configurations. Then two kinds of key come back out:

    - ``COMPUTED_PATHS``: derived by this service from the rest of the document
      (``version``). Neither side owns it, so it cannot be a difference
      between them -- and a stored document carries it while the file never
      spells it, which would make it the one permanent difference.
    - ``secrets``: not a ``Config`` field, so a dump cannot carry one and this
      pop is belt and braces. It stays because this walk's output is a list of
      paths served to a page, and the day something named ``secrets`` does
      become a field is not the day to discover that.
    """
    try:
        dumped = build_config(document).model_dump(mode="json")
    except (ValueError, TypeError):
        # TypeError: a YAML file whose top-level key resolves to a non-string
        # (a bare ``on:``) fails in ``Config(**data)`` before pydantic sees it.
        # pydantic's ValidationError is a ValueError, and so is every refusal
        # `Config`'s own validators raise.
        return None
    dumped.pop("secrets", None)
    for path in COMPUTED_PATHS:
        parts = path.split(".")
        target = dumped
        for part in parts[:-1]:
            target = target.get(part) if isinstance(target, dict) else None
        if isinstance(target, dict):
            target.pop(parts[-1], None)
    return dumped


def drift_report(file_document: dict | None, stored: dict) -> dict:
    """Whether the file on disk still describes what the store describes.

    This is the mounted file's whole remaining job. It is not a source of
    truth any more -- it seeds an empty store once, and a delta-era row is a
    statement about it -- so the one thing it can still tell an operator is
    "the configuration in git is not the configuration that is running".

    Configurations, not documents: see ``_comparable``. A store the page has
    saved holds a whole model dump of the same configuration the sparse file
    seeded it with, and reporting that as drift would light the notice on every
    deployment forever after its first save.

    No file is NOT drift. Removing the ConfigMap once the store is seeded is
    the end state this design is working towards, and reporting it as a
    difference would leave a permanent notice on the System tab for having
    done the right thing.

    An empty store is not drift either, for the same shape of reason running
    the other way: it is what the file is about to seed, so there is nothing
    yet for it to disagree with.

    A file that no longer describes a configuration this service can build IS
    drift, and is reported with no paths. There is no second document to walk,
    so naming paths is not available -- but "the file and the store agree"
    would be a false sentence, and the operator wants to know.
    """
    if file_document is None:
        return {"file_present": False, "differs": False, "paths": []}
    if not stored:
        return {"file_present": True, "differs": False, "paths": []}
    file_config = _comparable(file_document)
    stored_config = _comparable(stored)
    if file_config is None or stored_config is None:
        return {"file_present": True, "differs": True, "paths": []}
    paths = sorted(changed_paths(file_config, stored_config))
    return {"file_present": True, "differs": bool(paths), "paths": paths}


def _read_file_document(path: Path | None) -> dict | None:
    """The mounted document, or ``None`` when there is no readable file.

    ``None`` and not an exception: a deployment configured from the UI has no
    mounted file at all, and the caller already has to handle that.

    The three callers are the whole of the file's life: the boot-time seed of
    an empty store, the delta paths that are statements about this file, and
    the drift report above. Nothing else in this service reads it.
    """
    if path is None:
        return None
    try:
        return read_config_document(Path(path))
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None


def _validated(document: dict) -> Config:
    """``build_config``, plus the secrets refusal the write path applies.

    The stored document is the configuration now rather than a delta layered
    over a file, which makes a ``secrets`` key in it something the config API
    would echo back. ``merge_overrides`` keeps the editor from ever producing
    one; this is what keeps a hand-edited row -- or a mounted file carrying the
    key into the seed -- from doing it instead.
    """
    _reject_secrets(document)
    return build_config(document)


async def load_effective_config(path: Path | None, session: AsyncSession) -> Config:
    """The stored configuration document, validated.

    The mounted file is read ONLY when the store is empty, and then only to
    seed it -- or when the store holds a delta, which is a statement about
    that file and means nothing without it. After that the file is a drift
    report and nothing else, which is what makes it removable: a deployment
    whose store is seeded needs no file.

    ``path`` may be ``None`` or point at nothing -- a deployment configured
    from the UI has no mounted file. An empty store AND no file is unreachable
    from a booted application (``boot.is_configured`` refuses it and serves the
    wizard instead), so it raises rather than inventing a document.

    On the seed path this COMMITS the caller's session, because the seed has
    to be durable before the configuration it holds is acted on; the one-time
    conversion of a delta commits for the same reason, and both happen at most
    once in the life of a deployment. Every other path reads and writes
    nothing.

    Validated whole and versioned through the same ``build_config`` as
    ``load_config``, so a stored artwork setting moves ``config.version``
    exactly as editing the file would, and a stored scheduler setting leaves it
    alone.
    """
    document, meta = await load_store(session)
    if document and meta.get("format") == STORE_FORMAT:
        return _validated(document)
    base = _read_file_document(path)
    if document:
        return _validated(await migrate_delta_to_document(session, base))
    if base is None:
        raise ValueError(
            "the configuration store is empty and no configuration file was "
            "found; this deployment has not been configured"
        )
    seeded = await seed_store(session, base)
    if not seeded:
        # The seed found a row it will not replace: one holding nothing but
        # sections that left the schema. That is not an empty store, it is a
        # delta with nothing left in it, so it keeps its metadata and takes
        # the path every other delta takes -- which merges nothing over the
        # file and leaves the file's document standing.
        return _validated(await migrate_delta_to_document(session, base))
    # Validated before the commit, deliberately: the seed is the last time this
    # file is read, so a store seeded with a document the schema refuses could
    # never be repaired -- the application would not start, and the editor that
    # could clear the row sits behind the application. Refusing here leaves the
    # store empty and the file editable, which is a state an operator can act
    # on.
    config = _validated(seeded)
    await session.commit()
    return config
