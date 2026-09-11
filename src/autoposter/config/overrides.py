"""The database overrides layer: config deltas deep-merged over the YAML.

The mounted ``autoposter.yaml`` is delivered by Flux from git and is read-only
in the pod, so the UI's edits cannot go back into it. They live in the
single-row ``config_overrides`` table instead (``db/models.py``) and are merged
over the file every time a ``Config`` is built. The file keeps owning the
defaults; the database owns the deltas.
"""
import hashlib
import json
import logging
from pathlib import Path
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.loader import build_config, read_config_document
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


async def load_overrides_document(
    session: AsyncSession, *, for_update: bool = False
) -> dict:
    """The stored overrides document, or ``{}`` when there is no row.

    An empty document is the pre-edit state of every deployment, and merging
    ``{}`` is the identity, so "no overrides yet" costs nothing beyond one
    primary-key lookup and behaves exactly like the file alone.

    Migrated sections are stripped here rather than at the merge, because this
    is the single seam every reader of the stored document comes through: the
    boot-time merge (``load_effective_config``) that would otherwise refuse to
    validate, and ``GET /api/config``'s ``overridden_paths``, which would
    otherwise mark a setting the editor no longer renders as overridden.

    ``for_update`` takes a row lock, and only ``_persist_and_swap`` passes it:
    a reader that locked would serialise ``GET /api/config`` behind every save
    for no benefit.

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
    lock is taken only on a store that has never been written.

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
        # first-ever save on the key instead, then look again: whoever gets
        # here second is now looking at whoever got here first.
        await session.execute(select(func.pg_advisory_xact_lock(OVERRIDES_INSERT_LOCK_KEY)))
        row = await session.scalar(statement)
    if row is None or not row.document:
        return {}
    if not isinstance(row.document, dict):
        # Only ``PUT /api/config/overrides`` ever writes this column and it
        # only ever writes an object, so this is unreachable through the
        # application -- but JSONB will hold a list or a bare scalar quite
        # happily if someone edits the row by hand, and the merge would then
        # fail with an AttributeError from three frames down. Say what is
        # actually wrong instead.
        raise ValueError(
            "the config_overrides document must be a JSON object, not "
            f"{type(row.document).__name__}"
        )
    return without_migrated_sections(row.document)


async def load_effective_config(path: Path, session: AsyncSession) -> Config:
    """The YAML config with the stored overrides merged over it.

    Validated whole and versioned through the same ``build_config`` as
    ``load_config``, so an overridden artwork setting moves ``config.version``
    exactly as editing the file would, and an overridden scheduler setting
    leaves it alone.
    """
    base = read_config_document(path)
    overrides = await load_overrides_document(session)
    if not overrides:
        return build_config(base)
    return build_config(merge_overrides(base, overrides))
