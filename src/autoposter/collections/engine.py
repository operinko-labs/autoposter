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

Two things sit on top of that loop. Every definition's outcome is recorded
(``DefinitionResult``), because "6 action(s)" is not an answer to "did the
pass work" -- a dead source produces no actions and used to be reported as a
clean run (roadmap row 115). And, when the caller asks for it, the delete
sweep (``_sweep``) -- the only code in this service that deletes a collection,
off by default, capped, and refused outright past the cap.
"""
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    SmartContext,
)
from autoposter.collections.lists import member_diff, reconcile_list_collection
from autoposter.collections.reconcile import has_label, load_labels, protected_label
from autoposter.collections.resolve import build_owned_index, resolve_external
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import EventLog, ManagedCollection

logger = logging.getLogger(__name__)

# What the sweep reports under when it has nothing to report *about* -- the
# refusal past the cap belongs to the library, not to any one collection, and
# a real collection title here would read as that collection being the problem.
SWEEP_TITLE = "(delete sweep)"


@dataclass
class DefinitionResult:
    """What one pass did, or would do, to one collection.

    Counts are a *preview* concern and are filled only when the caller asked
    for them (``run_library(preview=True)``): learning them costs a read of the
    collection's members per definition, which a scheduled pass has no use for
    -- it reports through the action strings, and the deltas it actually
    applied are stamped on the ``managed_collections`` row.

    ``skipped`` covers every reason nothing was applied -- outside its
    schedule, or nothing left to apply after a failure or an empty resolve --
    so a failed definition is skipped too, with ``failed`` saying which of the
    two it was.
    """

    title: str
    library: str
    adding: int = 0
    removing: int = 0
    deleting: int = 0
    unresolved: int = 0
    failed: bool = False
    skipped: bool = False
    actions: list[str] = field(default_factory=list)


@dataclass
class LibraryRun:
    """One library's pass: every action string, and how each definition fared."""

    actions: list[str]
    definitions: list[DefinitionResult]

    @property
    def failures(self) -> list[str]:
        """The titles whose builder failed. A pass with any of these is not a
        success, whatever the action count says (roadmap row 115)."""
        return [result.title for result in self.definitions if result.failed]


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
    summaries=None,
) -> list[str]:
    """``run_library``'s action strings, for callers that want only those."""
    run = await run_library(
        session, section, library, library_type, definitions, config,
        http=http, label=label, dry_run=dry_run, run_index=run_index, now=now,
        summaries=summaries,
    )
    return run.actions


async def run_library(
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
    sweep: bool = False,
    preview: bool = False,
    summaries=None,
) -> LibraryRun:
    """Reconcile every definition that applies to this library, in order.

    ``run_index`` and ``now`` drive schedule gating and are injected rather than
    read here, so a gate is testable without waiting a month.

    ``sweep`` runs the delete sweep afterwards (see ``_sweep``). It is off by
    default and switched on only by ``service.reconcile_libraries``, which is
    the one caller that passes the *whole* definition list for the library: a
    sweep run against a subset would find every definition the subset left out
    unaccounted for.

    ``preview`` fills the per-definition counts, at the cost of a member read
    per collection -- see ``DefinitionResult``.

    ``summaries`` is the TMDB facts client a ``tmdb_summary:`` definition
    borrows its summary through (roadmap row 30). Optional, and its absence is
    reported rather than raised: every other part of a pass works without it.
    """
    label = config.collections.ownership_label if label is None else label
    if dry_run is None:
        dry_run = not config.collections.apply_to_plex
    now = now or datetime.now(UTC)

    actions: list[str] = []
    results: list[DefinitionResult] = []
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
            results.append(
                DefinitionResult(title=definition.title, library=library, skipped=True)
            )
            continue

        builder = REGISTRY[definition.builder]

        if getattr(builder, "smart", False):
            # Not wrapped: a smart builder does not fetch, so anything it raises
            # is a Plex write failing, which belongs to the caller's per-library
            # rollback rather than being swallowed as a dead source.
            smart_actions = await builder.apply(
                SmartContext(
                    session=session, section=section, library=library,
                    library_type=library_type, label=label, config=config,
                    http=http, dry_run=dry_run, definition=definition,
                )
            )
            actions += smart_actions
            # One result for the family, under the definition's own title: a
            # smart builder owns several collections and Plex evaluates each
            # one's membership itself, so there is no per-collection count to
            # report and nothing here would be true of only one of them.
            results.append(DefinitionResult(
                title=definition.title, library=library, actions=list(smart_actions)
            ))
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
            results.append(DefinitionResult(
                title=definition.title, library=library, failed=True, skipped=True
            ))
            continue

        for unit in units:
            result = await _run_one(
                session, section, library, unit, REGISTRY[unit.builder], context(unit),
                label=label, dry_run=dry_run, http=http, config=config,
                owned_index=owned_index, listing=listing, preview=preview,
                summaries=summaries,
            )
            actions += result.actions
            results.append(result)

    if sweep:
        try:
            swept = await _sweep(
                session, section, library, library_type, definitions, config,
                label=label, dry_run=dry_run, listing=listing,
            )
        except Exception:
            # Mirrors ``service.unmanaged_prior_collections``'s containment:
            # the sweep's candidate scan does a Plex reload per orphan
            # (``load_labels``) below the reconcile's own writes, so a
            # transient read failure here must not reach
            # ``reconcile_libraries``'s per-library handler and roll back
            # rows this pass already committed to Plex.
            logger.exception("%s: delete sweep failed", library)
            swept = [_swept(
                SWEEP_TITLE, library,
                "delete sweep failed; nothing was deleted this pass -- see logs",
            )]
        for result in swept:
            actions += result.actions
            results.append(result)

    return LibraryRun(actions=actions, definitions=results)


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
    preview: bool = False,
    summaries=None,
) -> DefinitionResult:
    """One collection: build, resolve, cap, apply."""
    outcome = DefinitionResult(title=definition.title, library=library)

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
        outcome.failed = True
        # An empty result, and empty is what ``lists.py`` reads as "make no
        # changes". The other fields being left at their defaults is safe only
        # because that early return (lists.py, the ``if not items`` branch)
        # happens before anything reads the summary or the poster fields --
        # were it ever moved below them, this would start interpolating None
        # into a poster URL. The pairing is deliberate, not incidental.
        result = BuilderResult(ids=[])

    resolved = resolve_external(owned_index(), result.ids)
    outcome.unresolved = resolved.unresolved
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

    outcome.skipped = not items
    if preview:
        collection = listing().get(definition.title)
        if collection is None:
            outcome.adding = len(items)
        elif items:
            adding, removing = member_diff(collection, items, definition.sync_mode)
            outcome.adding, outcome.removing = len(adding), len(removing)

    summary, summary_action = await _summary_for(definition, result, summaries)
    if summary_action:
        outcome.actions.append(summary_action)

    outcome.actions += await reconcile_list_collection(
        session, section, library, definition.title, items, label,
        summary=summary,
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
        sync_mode=definition.sync_mode,
        settings=definition,
    )
    return outcome


async def _summary_for(
    definition: CollectionDefinition, result: BuilderResult, summaries
) -> tuple[str | None, str | None]:
    """The summary this collection should carry, and anything to report.

    Three sources, in order: the one written in the config, the one TMDB holds
    for the collection the definition names (roadmap row 30), and the one the
    builder derived. The static summary wins because it is an explicit choice;
    a pull that silently overrode it would be a setting that reads as applied
    and is not.

    The pull is contained here rather than by the caller's builder wrapper: a
    summary is cosmetic, and a TMDB outage must leave the collection's
    membership reconciled, not the whole definition failed. So a failure means
    the summary falls back to the builder's (usually None) and the pass says
    so -- and nothing derived from the exception reaches the action string,
    because a provider error carries the URL it failed on.
    """
    if definition.summary is not None or definition.tmdb_summary is None:
        return definition.summary or result.summary, None

    if summaries is None:
        # No TMDB client on this instance -- the preview endpoint on a replica
        # without the background lifespan, and the direct callers in tests.
        return result.summary, (
            "%r asked for its summary from TMDB, but this instance has no TMDB "
            "client; the summary is unchanged" % definition.title
        )

    try:
        pulled = await summaries.collection_summary(definition.tmdb_summary)
    except Exception:
        logger.exception(
            "could not read the TMDB summary for %r (collection %d)",
            definition.title, definition.tmdb_summary,
        )
        return result.summary, (
            "could not read the TMDB summary for %r; the summary is unchanged"
            % definition.title
        )

    if not pulled:
        return result.summary, (
            "TMDB has no summary for the collection %r names"
            % definition.title
        )
    return pulled, None


async def _sweep(
    session: AsyncSession,
    section,
    library: str,
    library_type: str,
    definitions: list[CollectionDefinition],
    config,
    label: str,
    dry_run: bool,
    listing,
) -> list[DefinitionResult]:
    """Collections this service owns that no definition builds any more.

    The only place anything is deleted, and every guard is here rather than
    spread over the callers:

    - a candidate must carry the **ownership label** *and* have a
      ``managed_collections`` row. Either alone is a collection that is not
      ours to delete: the row alone can name a collection somebody else
      recreated under that title, and the label alone is a collection an
      operator labelled by hand.
    - a **protected label wins**, as it does everywhere else -- checked before
      ownership, so a Maintainerr collection that also carries our label is
      reported and left alone.
    - ``delete_unconfigured`` is off by default, and off means *reported*: the
      posture ``unmanaged_prior_collections`` takes towards a prior tool's
      leftovers, turned on our own. The two reports overlap by design on an
      adopted collection, which carries both labels.
    - past ``max_deletes`` the sweep refuses **entirely**, with the numbers.
      Deleting "the first five" of a hundred would be the same accident,
      spread over twenty passes (the ``cleanup.max_orphans`` precedent).

    The enumeration is library-scoped (``definition_titles_for``) because a
    delete decision cannot use the leftovers report's deliberately
    over-inclusive set: that one counts a definition aimed at another library
    as managing this one's titles, which is the safe direction for a report
    and the wrong one for a sweep.
    """
    managed = definition_titles_for(
        definitions, listing().values(), library, library_type, config
    )
    rows = {
        row.title: row
        for row in (
            await session.execute(
                select(ManagedCollection).where(ManagedCollection.library == library)
            )
        ).scalars()
    }

    results: list[DefinitionResult] = []
    candidates: list[tuple[str, object, ManagedCollection]] = []
    for title, collection in listing().items():
        if title in managed or title not in rows:
            continue
        if rows[title].kind == "operator":
            # An operator created this directly (``ops/blank``) -- no
            # definition enumerates its title, so it always lands here, and
            # it must never be swept just because nothing builds it. Reported
            # rather than silently skipped, and regardless of
            # ``delete_unconfigured``: that setting decides what an
            # unattended pass may delete, and this was never such a
            # candidate in the first place.
            results.append(_swept(title, library, (
                "%r was created by an operator, not any definition; "
                "the sweep never deletes it" % title
            )))
            continue
        load_labels(collection)
        protecting = protected_label(collection, config.collections.protect_labels or [])
        if protecting is not None:
            results.append(_swept(title, library, (
                "protected: %r carries %r; leaving it untouched" % (title, protecting)
            )))
            continue
        if not has_label(collection, label):
            logger.info(
                "%s: %r has a managed row but not the %r label; not ours to delete",
                library, title, label,
            )
            continue
        candidates.append((title, collection, rows[title]))

    if not config.collections.delete_unconfigured:
        for title, _, _ in candidates:
            results.append(_swept(title, library, (
                "%r is no longer built by any definition; "
                "set collections.delete_unconfigured to delete it" % title
            )))
        return results

    cap = config.collections.max_deletes
    if len(candidates) > cap:
        # One string for the whole sweep, naming both numbers: the operator
        # needs to know it was not a near miss before raising the cap.
        results.append(_swept(SWEEP_TITLE, library, (
            "refusing to delete %d unconfigured collection(s) in %r: more than "
            "the max_deletes cap of %d; nothing was deleted and everything else "
            "was reconciled" % (len(candidates), library, cap)
        )))
        return results

    for title, collection, row in candidates:
        if dry_run:
            results.append(_swept(
                title, library, "would delete %r: no definition builds it" % title, 1
            ))
            continue
        try:
            collection.delete()
        except Exception:
            # A later candidate's Plex delete is not this candidate's
            # problem: it must not stop the remaining candidates from being
            # tried, and it must not cost the audit trail of a delete that
            # already happened -- which is why that audit is flushed below
            # before this loop moves on to the next candidate's delete().
            logger.exception("%s: could not delete %r", library, title)
            results.append(_swept(
                title, library, "failed to delete %r: see logs for detail" % title
            ))
            continue
        await session.delete(row)
        session.add(EventLog(
            source="collections",
            event_type="collection_deleted",
            # Identity only. The row's stats are gone with it and the
            # collection's members were never ours to record.
            payload={
                "library": library,
                "title": title,
                "rating_key": str(getattr(collection, "ratingKey", "") or ""),
            },
            outcome="deleted; no definition builds it",
        ))
        # Flushed now, rather than left for the caller's eventual commit, so
        # this delete's audit trail is durable in the transaction before the
        # next candidate's ``collection.delete()`` -- the one Plex-side call
        # in this loop that can raise -- gets a chance to.
        await session.flush()
        results.append(_swept(
            title, library, "deleted %r: no definition builds it" % title, 1
        ))
    return results


def _swept(title: str, library: str, action: str, deleting: int = 0) -> DefinitionResult:
    """One sweep outcome, in the same shape a definition reports.

    A would-be-deleted collection is still a collection with a title and a
    pending action, so the preview renders it as another row rather than a
    second kind of thing.
    """
    return DefinitionResult(
        title=title, library=library, deleting=deleting, skipped=True, actions=[action]
    )


def definition_titles_for(
    definitions: list[CollectionDefinition],
    collections,
    library: str,
    library_type: str,
    config,
) -> set[str]:
    """``definition_titles``, narrowed to what this library actually builds.

    The narrowing is the whole point: ``definition_titles`` counts every
    definition's title whatever library it targets, which keeps the leftovers
    report from inviting an operator to delete a sibling library's collection.
    A sweep reading that set would never delete an orphan whose title some
    other library's definition happens to use.
    """
    return definition_titles(
        [d for d in definitions if _targets(d, library)],
        collections, library_type, config,
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
