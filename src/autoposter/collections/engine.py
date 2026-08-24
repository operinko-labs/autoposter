"""Definitions in, action strings out: the loop every collection is built by.

The engine owns everything that is the same whatever produced a collection's
membership -- library targeting, schedule gating, failure containment,
resolution to owned items, the member cap -- and hands the result to the
unchanged apply layer (``lists.reconcile_list_collection``). Builders fetch and
translate; the engine decides what happens to what they return.

Three rules it exists to keep:

- **A dead source leaves its collection alone.** ``build`` raises (that is the
  builder contract); the engine catches, and the empty result it substitutes is
  what ``reconcile_list_collection`` already treats as "make no changes", never
  "remove everything". One dead chart must not take the pass down, and must not
  empty a live collection either. Only the *builder* is wrapped: a failure
  writing to Plex still reaches the caller, which is what makes
  ``reconcile_libraries`` roll that library back and report it.
- **The expensive calls happen once.** The owned-item index costs a full
  ``section.all()`` and the collection listing returns all 305 collections on
  the production Movies section, so both are built lazily and shared across
  every definition -- and a library with nothing to build pays for neither.
- **Gating is skipping, not failing.** A definition outside its schedule
  contributes no actions and its collection is not touched; it is still a
  managed title, so the leftovers report does not suddenly call it abandoned.
"""
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    SmartContext,
)
from autoposter.collections.lists import reconcile_list_collection
from autoposter.collections.resolve import build_owned_index, resolve_external
from autoposter.config.schema import CollectionDefinition

logger = logging.getLogger(__name__)


def _targets(definition: CollectionDefinition, library: str) -> bool:
    """Whether this definition applies to this library.

    ``libraries: None`` means every library in ``collections.libraries``; an
    explicit list narrows it.
    """
    return definition.libraries is None or library in definition.libraries


def _due(definition: CollectionDefinition, run_index: int, now: datetime) -> bool:
    """Whether this definition's schedule lets it run on this pass."""
    schedule = definition.schedule
    if schedule is None:
        return True
    if run_index % schedule.every_n_runs:
        return False
    return not (schedule.months and now.month not in schedule.months)


async def _expand(
    builder, definition: CollectionDefinition, ctx: BuilderContext
) -> list[CollectionDefinition]:
    """The concrete collections one definition stands for.

    Almost always itself. A builder whose collections are named at run time --
    the Oscars years -- implements ``expand`` instead, and its own definition is
    a placeholder that never becomes a collection.
    """
    if not hasattr(builder, "expand"):
        return [definition]
    return await builder.expand(ctx)


async def run_definitions(
    session: AsyncSession,
    section,
    library: str,
    library_type: str,
    definitions: list[CollectionDefinition],
    config,
    http: httpx.AsyncClient | None = None,
    label: str | None = None,
    dry_run: bool | None = None,
    run_index: int = 0,
    now: datetime | None = None,
) -> list[str]:
    """Reconcile every definition that applies to this library, in order.

    ``run_index`` and ``now`` drive schedule gating and are injected rather than
    read here, so a gate is testable without waiting a month.
    """
    label = config.collections.ownership_label if label is None else label
    if dry_run is None:
        dry_run = not config.collections.apply_to_plex
    now = now or datetime.now(UTC)

    actions: list[str] = []
    run_cache: dict = {}
    index = None
    existing: dict | None = None

    def owned_index():
        nonlocal index
        if index is None:
            index = build_owned_index(section)
        return index

    def listing() -> dict:
        nonlocal existing
        if existing is None:
            existing = {c.title: c for c in section.collections()}
        return existing

    def context(definition: CollectionDefinition) -> BuilderContext:
        return BuilderContext(
            library=library,
            library_type=library_type,
            http=http,
            config=definition.params,
            run_cache=run_cache,
        )

    for definition in definitions:
        if not _targets(definition, library):
            continue
        if not _due(definition, run_index, now):
            logger.debug(
                "%s: %r is outside its schedule this pass", library, definition.title
            )
            continue

        builder = REGISTRY[definition.builder]

        if getattr(builder, "smart", False):
            # Not wrapped: a smart builder does not fetch, so anything it raises
            # is a Plex write failing, which belongs to the caller's per-library
            # rollback rather than being swallowed as a dead source.
            actions += await builder.apply(
                SmartContext(
                    session=session, section=section, library=library,
                    library_type=library_type, label=label, config=config,
                    http=http, dry_run=dry_run,
                )
            )
            continue

        try:
            units = await _expand(builder, definition, context(definition))
        except Exception:
            # An expanding definition has no collection of its own, so there is
            # no title to report against and nothing was touched. Logged, and
            # the pass continues -- the shape ``_award_event`` had.
            logger.exception(
                "%s: could not expand %r (%s)", library, definition.title, definition.builder
            )
            continue

        for unit in units:
            actions += await _run_one(
                session, section, library, unit, REGISTRY[unit.builder], context(unit),
                label=label, dry_run=dry_run, http=http, config=config,
                owned_index=owned_index, listing=listing,
            )

    return actions


async def _run_one(
    session: AsyncSession,
    section,
    library: str,
    definition: CollectionDefinition,
    builder,
    ctx: BuilderContext,
    label: str,
    dry_run: bool,
    http: httpx.AsyncClient | None,
    config,
    owned_index,
    listing,
) -> list[str]:
    """One collection: build, resolve, cap, apply."""
    if definition.sync_mode == "append":
        # Reported rather than treated as sync: sync would remove members an
        # append definition exists to keep, and silently doing nothing would
        # look like a collection that had simply stopped updating.
        return [
            "%r: skipped; sync_mode 'append' is not implemented yet" % definition.title
        ]

    try:
        result = await builder.build(ctx)
    except Exception:
        # The containment invariant. The class name goes in the log with the
        # traceback; nothing derived from the exception reaches an action string
        # or Plex, because a provider error commonly carries the URL it failed
        # on and those carry credentials.
        logger.exception(
            "%s: source failed for %r (%s)", library, definition.title, definition.builder
        )
        result = BuilderResult(ids=[])

    resolved = resolve_external(owned_index(), result.ids)
    if resolved.unresolved:
        logger.info(
            "%s: %r has %d id(s) this library does not own",
            library, definition.title, resolved.unresolved,
        )

    items = resolved.items
    if definition.limit is not None:
        # After resolution, so a limit counts collection members rather than
        # candidate ids: capping before would leave a short collection whenever
        # the library was missing one of the first few.
        items = items[: definition.limit]

    return await reconcile_list_collection(
        session, section, library, definition.title, items, label,
        summary=definition.summary or result.summary,
        sort=definition.sort,
        dry_run=dry_run,
        existing=listing(),
        adopt=config.collections.adopt,
        adopt_from=config.collections.adopt_from,
        adopt_removes_prior_label=config.collections.adopt_removes_prior_label,
        protect_labels=config.collections.protect_labels,
        kind=result.poster_kind,
        key=result.poster_key,
        http=http,
        config=config,
    )


def definition_titles(
    definitions: list[CollectionDefinition], collections, library_type: str, config
) -> set[str]:
    """Every collection title these definitions manage.

    Three cases, because three kinds of definition name their collections
    differently: a smart builder owns a family of titles and lists them itself;
    an expanding builder's titles are dynamic, so they are recognised in
    ``collections`` by the builder's pattern (re-fetching the source here to
    learn this pass's titles would be a second request for a report); everything
    else is its own title.

    Gated-off definitions are included deliberately -- a collection skipped this
    pass is still managed, and reporting it as a prior tool's leftover would
    invite an operator to delete it.
    """
    titles: set[str] = set()
    for definition in definitions:
        builder = REGISTRY[definition.builder]
        if getattr(builder, "smart", False):
            titles |= builder.titles(library_type, config)
            continue
        pattern = getattr(builder, "TITLE_PATTERN", None)
        if pattern is not None:
            titles |= {
                collection.title for collection in collections
                if pattern.match(collection.title)
            }
            continue
        titles.add(definition.title)
    return titles
