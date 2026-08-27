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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    PlexSectionAccess,
    SmartContext,
    SourceClients,
)
from autoposter.collections.filter_values import PlexItemView
from autoposter.collections.filters import evaluate, parse_filters
from autoposter.collections.lists import member_diff, reconcile_list_collection
from autoposter.collections.reconcile import has_label, load_labels, protected_label
from autoposter.collections.resolve import build_owned_index, resolve_external
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import EventLog, ManagedCollection
from autoposter.providers.cache import ProviderCache

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

    ``unresolved`` and ``filtered`` are filled on every pass rather than only
    for a preview: both are by-products of work the pass did anyway, and both
    answer "why is this collection smaller than the source" -- the first with
    "this library does not own them", the second with "``filters:`` excluded
    them".
    """

    title: str
    library: str
    adding: int = 0
    removing: int = 0
    deleting: int = 0
    unresolved: int = 0
    # How many resolved items this definition's ``filters:`` excluded. Zero
    # when it has none, and zero -- not the whole set -- when the stage could
    # not run: see ``_passing``.
    filtered: int = 0
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
        """The titles whose builder failed, or whose filter stage could not be
        evaluated. A pass with any of these is not a success, whatever the
        action count says (roadmap row 115)."""
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


# The fields an expanded definition inherits from the placeholder it came
# from (roadmap row 141). Every one of them describes the *collection* -- how
# it is labelled, sorted, capped, pinned -- rather than where its membership
# comes from, and an expanding definition is the only definition an operator
# gets to write for the family, so a setting on it is a setting for all of
# them. Deliberately not here: ``title``, ``builder``, ``params``, ``summary``,
# ``sort``, ``libraries`` and ``schedule``. The first five are what the
# expander exists to decide per unit; the last two were already honoured on
# the placeholder before expansion ever happened, and re-applying them would
# be a second, differently-scoped gate.
#
# ``filters`` rides along on the same argument as ``limit``, and the two are
# the list's only narrowing knobs (roadmap row 96). Neither says where a
# collection's membership comes from -- that is ``builder`` and ``params``,
# which the expander owns -- and both say which of it survives. "Oscar winners,
# capped at 25" and "Oscar winners, nothing before 2000" are the same kind of
# instruction, written once for a family an operator cannot enumerate; dropping
# the second silently would be row 141's defect again, one field along.
_INHERITED_BY_EXPANSION = (
    "labels",
    "label_sync",
    "item_label",
    "sort_title",
    "collection_mode",
    "visible_library",
    "visible_home",
    "visible_shared",
    "hub_priority",
    "limit",
    "filters",
    "sync_mode",
    "tmdb_summary",
)


async def _expand(
    builder, definition: CollectionDefinition, ctx: BuilderContext
) -> list[CollectionDefinition]:
    """The concrete collections one definition stands for.

    Almost always itself. A builder whose collections are named at run time --
    the Oscars years -- implements ``expand`` instead, and its own definition is
    a placeholder that never becomes a collection.

    What the expander returns is completed from the placeholder rather than
    taken as the whole truth: an expanding builder knows the title, the params
    and the summary of each unit, and knows nothing about how the operator
    wanted the family labelled or capped. Before this, those settings were
    silently dropped -- the definition loaded, the collections were built, and
    every per-collection setting on it did nothing (roadmap row 141).
    """
    if not hasattr(builder, "expand"):
        return [definition]
    return [_completed(definition, unit) for unit in await builder.expand(ctx)]


def _completed(
    placeholder: CollectionDefinition, unit: CollectionDefinition
) -> CollectionDefinition:
    """One expanded unit, with the placeholder's settings filled in.

    "Set by the expander" is ``model_fields_set``, not "differs from the
    default": a builder that deliberately expands to ``sync_mode: sync`` under
    a placeholder asking for ``append`` means it, and comparing against
    defaults could not tell that from silence.

    ``model_copy`` rather than a re-validated construction, because both
    halves are already-validated models and re-running the definition
    validators here would hold an expanded unit to rules that were checked on
    the placeholder at config load.
    """
    update = {
        name: getattr(placeholder, name)
        for name in _INHERITED_BY_EXPANSION
        if name not in unit.model_fields_set
    }
    return unit.model_copy(update=update) if update else unit


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
    sources: SourceClients | None = None,
    cache: ProviderCache | None = None,
) -> list[str]:
    """``run_library``'s action strings, for callers that want only those."""
    run = await run_library(
        session, section, library, library_type, definitions, config,
        http=http, label=label, dry_run=dry_run, run_index=run_index, now=now,
        summaries=summaries, sources=sources, cache=cache,
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
    sources: SourceClients | None = None,
    cache: ProviderCache | None = None,
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

    ``sources`` is the pass's ``SourceClients`` bundle and ``cache`` the
    process's provider cache, both threaded in the same way and for the same
    reason as ``summaries``: they are built where the config and the secrets
    are, which is not here. Omitting ``sources`` is a bundle with nothing
    configured, not a missing one -- see ``BuilderContext.sources``.
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

    # The pass's bundle, with this library's Plex accessor bound onto it. The
    # accessor closes over ``owned_index`` above rather than over ``section``
    # alone, which is the whole point: a builder listing what the library owns
    # reads the index the engine was going to build anyway, so it costs no
    # second ``section.all()``.
    bound_sources = replace(
        sources or SourceClients(),
        plex=PlexSectionAccess(section, owned_index),
    )

    def context(definition: CollectionDefinition) -> BuilderContext:
        return BuilderContext(
            library=library,
            library_type=library_type,
            http=http,
            config=definition.params,
            cache=cache,
            run_cache=run_cache,
            sources=bound_sources,
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
                    run_cache=run_cache, listing=listing,
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
    filter_failed = False
    filter_emptied_a_non_empty_set = False
    if definition.filters is not None:
        had_items = bool(items)
        kept = _passing(definition, items, library)
        if kept is None:
            outcome.failed = True
            filter_failed = True
            items = []
        else:
            outcome.filtered = len(items) - len(kept)
            items = kept
        # The source did return items here -- the filter is what emptied the
        # set (or could not run at all) -- so the action string below must
        # say that, not repeat the "source returned no items" wording, which
        # would misattribute the filter's outcome to the source.
        filter_emptied_a_non_empty_set = had_items and not items

    if definition.limit is not None:
        # After resolution -- and, since row 96, after the filter -- so a limit
        # counts collection members rather than candidate ids: capping before
        # either would leave a short collection whenever the library was
        # missing one of the first few, or the filter excluded one of them.
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

    if filter_emptied_a_non_empty_set:
        # ``reconcile_list_collection`` would report this as "source returned
        # no items", which is true of its own ``items`` argument but false of
        # what actually happened -- the source returned items, and the filter
        # is why none reached here. Report the filter instead of calling in.
        if filter_failed:
            outcome.actions.append(
                "%r: the filter could not be evaluated; leaving the collection "
                "untouched" % definition.title
            )
        else:
            outcome.actions.append(
                "%r: the filter excluded every member; leaving the collection "
                "untouched" % definition.title
            )
    elif resolved.unresolved and not items:
        # The same misattribution one step earlier. ``reconcile_list_collection``
        # would say "source returned no items" here too, and the source did
        # return items -- this library simply owns none of them, which is an
        # operator-actionable fact ("that list is not about my library") where
        # the source wording points at a dead provider instead.
        outcome.actions.append(
            "%r: the source returned %d id(s), none of which this library owns; "
            "leaving the collection untouched" % (definition.title, len(result.ids))
        )
    else:
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


def _passing(definition: CollectionDefinition, items: list, library: str) -> list | None:
    """The items this definition's ``filters:`` keeps, or None if it could not
    be evaluated at all.

    The stage sits between resolution and the cap and is deliberately dumb:
    order is preserved (the builder's order is the collection's), and nothing
    here reaches Plex -- ``PlexItemView`` reads only values the section listing
    already carried, which is what keeps a filter from costing one request per
    item (``filter_values``).

    **The containment is defence in depth, and it is on purpose.** Evaluation
    is total by construction: the accessors are total over the item, the
    predicate model defines a result for a missing value rather than raising,
    and a filter naming an attribute with no accessor is refused at config load
    (``schema.CollectionDefinition._filters_must_parse_and_be_readable``). So
    this ``except`` should be unreachable. It is here because the engine's rule
    is that no one definition's failure is the pass's, and a rule that holds
    only where somebody remembered to keep it is not a rule -- the guard that
    never fires costs nothing, and the one left out is the one that takes a
    pass down.

    Contained the way a dead source is, for a reason worth stating: the
    alternative -- keeping the unfiltered items -- would write the very members
    the operator's filter exists to exclude, a full and plausible and wrong
    collection. No items is what ``lists.py`` reads as "make no changes", so
    the collection is left exactly as it was and the definition is reported
    failed. The parse happens here rather than being carried on the definition
    because the model is dumped to JSON for ``definition_config_hash`` and
    copied by expansion, and neither would survive a compiled regex; it is
    microseconds against a pass that has just walked the library.
    """
    try:
        parsed = parse_filters(definition.filters)
        # One moment for the whole collection. ``evaluate`` would otherwise
        # read the clock per item, and a long pass would measure the first half
        # of a relative window (``added: 30``) against one instant and the
        # second half against a later one -- a membership no single instant
        # would have produced.
        #
        # The runner's LOCAL clock rather than this pass's ``now``, which is
        # UTC: plexapi hands back ``addedAt`` as a naive datetime in the
        # runner's clock (``filters._as_moment``), so the local clock is the
        # one that shares a basis with the values being compared. That whole
        # runner-dependence is roadmap row 154's open question; this line
        # deliberately does not pre-empt its answer.
        #
        # A datetime, not a date: Kometa's own ``current_time`` is
        # ``datetime.now()`` and its date filters compare against it with the
        # time of day intact (Task 4's oracle). Passing midnight here would
        # widen every relative window by up to a day against Kometa's.
        now = datetime.now()
        return [
            item for item in items
            if evaluate(parsed, PlexItemView(item), now=now)
        ]
    except Exception:
        # The class name and traceback go to the log; nothing derived from the
        # exception reaches an action string, the same rule the builder's
        # containment above keeps.
        logger.exception(
            "%s: could not evaluate the filter on %r; nothing was applied to it "
            "this pass", library, definition.title,
        )
        return None


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


def _family_labels(
    definitions: list[CollectionDefinition], library: str
) -> dict[str, str]:
    """``{label: definition title}`` for every dynamic family this library builds.

    Pure -- it reads the registry and the definitions, never Plex -- and
    library-scoped for ``definition_titles_for``'s reason: a family aimed at
    another library must not protect this one's collections.

    The builder is asked rather than the module imported, so the engine keeps
    knowing only the protocol: a builder that manages a family whose titles it
    cannot enumerate offline says so by having a ``family_label``.
    """
    labels: dict[str, str] = {}
    for definition in definitions:
        if not _targets(definition, library):
            continue
        namer = getattr(REGISTRY[definition.builder], "family_label", None)
        if namer is not None:
            labels[namer(definition)] = definition.title
    return labels


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
    - a **dynamic family's member** is reported and never deleted. Its titles
      are the library's, so ``definition_titles_for`` cannot contain them and
      every one of them would otherwise look like an orphan; the family's own
      sweep (phase 10a-2) is what decides its lifecycle, through these same
      guards.

    The enumeration is library-scoped (``definition_titles_for``) because a
    delete decision cannot use the leftovers report's deliberately
    over-inclusive set: that one counts a definition aimed at another library
    as managing this one's titles, which is the safe direction for a report
    and the wrong one for a sweep.
    """
    managed = definition_titles_for(
        definitions, listing().values(), library, library_type, config
    )
    families = _family_labels(definitions, library)
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
        family = next(
            (one for one in families if has_label(collection, one)), None
        )
        if family is not None:
            # A dynamic family's titles are the library's, so no definition
            # enumerates them and every member lands here on the first
            # sweep-enabled pass after it was created. The family owns its own
            # lifecycle -- phase 10a-2 gives it a sweep that runs through these
            # same guards -- so this one reports and never deletes. Reported
            # rather than skipped: an operator who narrowed the family has to
            # see what is left behind.
            results.append(_swept(title, library, (
                "%r belongs to the dynamic family %r, whose own delete sweep "
                "ships in phase 10a-2; nothing was deleted"
                % (title, families[family])
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

    Four cases, because four kinds of definition name their collections
    differently: a smart builder that owns a family of titles lists them itself;
    a smart builder that owns exactly the collection its definition names lists
    nothing and is recognised by its own title;
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
            # Two shapes of smart builder, and the difference is exactly this.
            # ``cs_bucket`` manages a FAMILY whose titles it derives itself, so
            # it lists them. ``smart_filter`` manages the one collection its
            # definition names, so there is nothing to derive -- and a
            # ``titles`` method that handed the definition's own title back to
            # the caller that already has it would be ceremony, not
            # information. Falling through is the smaller diff and keeps the
            # engine's smart dispatch the single seam (9c decision C6).
            lister = getattr(builder, "titles", None)
            titles |= lister(library_type, config) if lister else {definition.title}
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
