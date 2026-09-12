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

from autoposter.collections import groups
from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    PlexSectionAccess,
    SmartContext,
    SourceClients,
)
from autoposter.collections.builders.plex_search import (
    LibraryTagResolver,
    PlexSearchUnavailable,
)
from autoposter.collections.enrichment import ensure_tags
from autoposter.collections.facts_read import ensure_facts
from autoposter.collections.filter_values import PlexItemView
from autoposter.collections.filters import (
    FilterPredicate,
    batched_attributes,
    evaluate,
    facts_attributes,
    parse_filters,
    tag_predicates,
    without_values,
)
from autoposter.collections import member_sort
from autoposter.collections.lists import member_diff, reconcile_list_collection
from autoposter.collections.reconcile import (
    LIBTYPES,
    has_label,
    load_labels,
    protected_label,
    reconcile_separator,
    shape_conflict,
    would_proceed,
)
from autoposter.collections.arr_overrides import restricted_members, tag_members
from autoposter.collections.mdblist_sync import sync_membership
from autoposter.collections.posters import LOCAL_ASSET_KIND, apply_local_posters_to_unmanaged
from autoposter.collections.resolve import build_owned_index, resolve_external
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import EventLog, ManagedCollection
from autoposter.providers.cache import ProviderCache

logger = logging.getLogger(__name__)

# What the sweep reports under when it has nothing to report *about* -- the
# refusal past the cap belongs to the library, not to any one collection, and
# a real collection title here would read as that collection being the problem.
SWEEP_TITLE = "(delete sweep)"

# Row 37's per-action results are library-wide, one call covering every
# unmanaged collection with a local poster -- not any one definition's -- so
# they are reported under this placeholder title the same way the sweep's are.
LOCAL_ASSETS_RESULT_TITLE = "(unmanaged local posters)"


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
    # What the pass ACTUALLY applied, as opposed to ``adding``/``removing``
    # above, which are preview counts and stay zero on a real pass. Filled
    # from ``reconcile_list_collection``'s ``deltas`` out-param, and read by
    # row 19's per-collection ``changes`` webhook -- which must report what
    # happened, not what a preview would have said.
    added: int = 0
    removed: int = 0
    failed: bool = False
    skipped: bool = False
    actions: list[str] = field(default_factory=list)


@dataclass
class CollectionNotification:
    """One outbound webhook this pass owes, collected but not yet sent.

    The engine decides WHAT to announce and WHERE; ``service.reconcile_libraries``
    decides WHEN, which is after its per-library commit -- the same
    "never describe work the database does not yet show" rule both shipped
    call sites follow. Collecting rather than sending also keeps the notifier
    out of ``run_library``'s signature, which every preview and CLI caller
    would otherwise have to learn about.

    ``url`` empty means the globally configured target (a swept delete of a
    collection no definition owns any more has no per-collection webhook to
    route to).
    """

    event: str
    summary: str
    detail: dict
    url: str = ""


@dataclass
class LibraryRun:
    """One library's pass: every action string, how each definition fared, and
    the per-collection webhooks the pass owes."""

    actions: list[str]
    definitions: list[DefinitionResult]
    notifications: list[CollectionNotification] = field(default_factory=list)
    # Row 269: the ``process_item`` entries -- ``(payload, dedupe_key)``, the
    # shape ``queue.jobs.enqueue_batch`` takes -- for members whose sort
    # position was recorded, changed or released this pass. Collected here
    # and enqueued by ``service.reconcile_libraries`` BELOW its per-library
    # commit, for the notifications' reason: a job must never describe a
    # row the database does not yet show.
    reprocess: list[tuple[dict, str]] = field(default_factory=list)

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
#
# ``changes_webhook`` rides along for the same reason, one step further out:
# the placeholder is the only definition an operator writes for the family, so
# a webhook on it is a webhook for every collection in it.
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
    "changes_webhook",
    # Row 269: how a family's members are SORTED, and whether each unit
    # creates a collection at all, are properties of the family, written once
    # on the placeholder -- exactly like ``item_label``.
    "member_sort",
    "create_collection",
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
    run_cache_seed: dict | None = None,
) -> list[str]:
    """``run_library``'s action strings, for callers that want only those.

    ``run_cache_seed`` pre-populates the pass's scratch. It exists for the
    delete sweep's tests, which need a dynamic family's generated-titles record
    without a real enumeration behind it; production never passes one, and a
    real pass overwrites any key a builder owns.
    """
    run = await run_library(
        session, section, library, library_type, definitions, config,
        http=http, label=label, dry_run=dry_run, run_index=run_index, now=now,
        summaries=summaries, sources=sources, cache=cache,
        run_cache_seed=run_cache_seed,
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
    run_cache_seed: dict | None = None,
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
    notifications: list[CollectionNotification] = []
    run_cache: dict = dict(run_cache_seed or {})
    # One owned index per MEMBER LEVEL (roadmap row 143), each built at most
    # once. A dict rather than a single slot because an episode-level
    # definition and an item-level one in the same pass are two different
    # traversals of the same library, and neither may pay for the other's.
    indexes: dict[str, dict] = {}
    existing: dict | None = None

    def owned_index(level: str = "item"):
        if level not in indexes:
            indexes[level] = build_owned_index(section, level)
        return indexes[level]

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

    # The group index and the tab order, resolved ONCE for the library rather
    # than per definition: ``preset_groups`` walks the catalog's active presets
    # and expanding it forty times would be forty identical scans. Pure -- no
    # Plex, no database -- so it costs nothing a dry run does not also pay.
    group_order = groups.effective_order(config)
    group_index = groups.preset_groups(config, library_type)

    # The titles the REST of this config already manages, resolved once per
    # library beside the group index above and for the same reason. A family
    # builder that ENUMERATES its collections checks its units against this,
    # which is what stops two presets rebuilding one Plex collection from two
    # membership rules every pass (``builders/facts_family.py``).
    #
    # The empty ``collections`` argument is deliberate: that argument feeds only
    # the ``TITLE_PATTERN`` branch, which matches against Plex's own listing, and
    # calling ``listing()`` here would buy the section listing before the first
    # definition ran -- on every pass, including the narrow callers that build one
    # definition. No pattern-titled builder is in the contest class.
    managed_titles = frozenset(
        definition_titles_for(definitions, [], library, library_type, config)
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
            session=session,
            definition=definition,
            managed_titles=managed_titles,
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
            if definition.member_sort:
                # Row 269: a gated placeholder cannot name its units, so no
                # row in this library may be released on its account.
                member_sort.hold_releases(
                    run_cache, library, "%r is outside its schedule" % definition.title,
                )
            continue

        builder = REGISTRY[definition.builder]

        if getattr(builder, "smart", False):
            # Not wrapped -- and no longer because "a smart builder does not
            # fetch", which stopped being true for ``dynamic``: it performs two
            # reads, the enumeration wrapped class-name-only inside
            # ``LibraryTagResolver`` and RETURNED as a refusal action, and the
            # shared listing through ``ctx.listing()`` unwrapped like every
            # other engine listing call. So what is left to escape ``apply`` is
            # a Plex write or that shared listing failing, and both belong to
            # the caller's per-library rollback rather than being swallowed here
            # as a dead source.
            #
            # One more thing can escape, in principle: a builder's own internal
            # params re-validation (``smart_url.search_url``'s
            # ``SmartUrlParams.model_validate``, ``dynamic``'s own
            # ``DynamicParams.model_validate``) raises ``pydantic.ValidationError``,
            # which every builder's ``REFUSALS`` tuple deliberately excludes
            # (``dynamic.py:220-222``). It cannot happen for a definition that
            # already loaded and validated, so an escape here is a defect
            # signal, not an operator refusal, and aborting the whole pass is
            # the right failure mode for it.
            #
            # Row 186: the tmdb_summary pull happens HERE, where ``summaries``
            # lives, through the same ``_summary_for`` every list definition
            # uses -- for every smart builder that refuses ``tmdb_summary`` at
            # config load this returns ``definition.summary`` untouched. The
            # empty BuilderResult stands in for "no builder-derived summary",
            # which is what a smart builder has.
            smart_summary, summary_note = await _summary_for(
                definition, BuilderResult(ids=[]), summaries
            )
            smart_actions = await builder.apply(
                SmartContext(
                    session=session, section=section, library=library,
                    library_type=library_type, label=label, config=config,
                    http=http, dry_run=dry_run, definition=definition,
                    summary=smart_summary,
                    # A note means the pull could not be RESOLVED, and the
                    # summary above is then ``_summary_for``'s fallback (None
                    # here: a smart builder's ``BuilderResult`` is empty), not
                    # the definition asserting it has no summary. Only the
                    # latter may reach the reconciler's clear.
                    summary_asserted=summary_note is None,
                    sort_prefix=groups.sort_prefix_for(
                        definition, group_index, group_order
                    ),
                    run_cache=run_cache, listing=listing,
                )
            )
            if summary_note:
                smart_actions = [*smart_actions, summary_note]
            actions += smart_actions
            # One result for the family, under the definition's own title: a
            # smart builder owns several collections and Plex evaluates each
            # one's membership itself, so there is no per-collection count to
            # report and nothing here would be true of only one of them.
            results.append(DefinitionResult(
                title=definition.title, library=library, actions=list(smart_actions)
            ))
            continue

        # A family that REFUSES is the one definition shape a pass could report
        # nothing at all about: the loop below appends one result per unit
        # returned, so an empty enumeration, an all-excluded family or an
        # over-cap fan-out leaves no row -- not that it ran, not why. The
        # builder writes its reasons into the pass's scratch instead, and this
        # is where they join the other "nothing was done, and here is why"
        # strings (``_run_one``'s filter-emptied and nothing-owned branches).
        # Asked off the registry entry rather than imported, the protocol
        # ``_family_state`` states. The slice is what keeps one family's notes
        # out of the next one's place: the list belongs to the pass, and every
        # family in it appends. A raise needs none of this -- the handler below
        # reports a failed result under the definition's own title, and a note
        # written before that raise is dropped with it: that visible failed
        # result is the compensation, where a ``finally`` would emit a refused
        # family's reasons for a pass that never finished expanding it.
        reporter = getattr(builder, "notes", None)
        reported = len(reporter(run_cache)) if reporter is not None else 0

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
            if definition.member_sort:
                member_sort.hold_releases(
                    run_cache, library,
                    "%r could not be expanded, so its units cannot be named"
                    % definition.title,
                )
            continue

        if reporter is not None:
            actions += reporter(run_cache)[reported:]

        # The placeholder's group, resolved once per definition: the
        # fall-through an expanded unit defers to when its own title and
        # builder place it nowhere (``groups.group_for``'s ``parent``). This
        # is what moves a facts_family member into the block whose divider
        # ``_separators`` -- which resolves from this same placeholder --
        # actually creates, closing the disagreement the !100_ misroute was.
        placeholder_group = groups.group_for(definition, group_index)

        for unit in units:
            result = await _run_one(
                session, section, library, unit, REGISTRY[unit.builder], context(unit),
                label=label, dry_run=dry_run, http=http, config=config,
                owned_index=owned_index, listing=listing, preview=preview,
                summaries=summaries,
                # ``unit``, not ``definition`` -- the whole of the C3 expansion
                # trap. An expanded unit carries the ceremony's own builder
                # (``imdb_award_years``), its own title ("Oscars Winners 2026")
                # and its own year, so it resolves its own group and its own
                # ordering key; resolving from the placeholder would hand all
                # five ceremony years one string. The placeholder decides only
                # the FALL-THROUGH, above.
                sort_prefix=groups.sort_prefix_for(
                    unit, group_index, group_order, parent=placeholder_group
                ),
                sort_order=groups.definition_order(unit, library_type),
            )
            actions += result.actions
            results.append(result)
            if not dry_run and unit.changes_webhook and (result.added or result.removed):
                # Row 19's ``changes``: opt-in per definition and never a
                # fallback to the global target -- a POST per changed
                # collection per pass would be an unbounded volume change to
                # the shipped integration. A dry run announces nothing because
                # it changed nothing.
                notifications.append(CollectionNotification(
                    event="collection_changed",
                    summary="%s: %r changed: +%d -%d" % (
                        library, unit.title, result.added, result.removed
                    ),
                    detail={
                        "library": library,
                        "collection": unit.title,
                        "added": result.added,
                        "removed": result.removed,
                    },
                    url=unit.changes_webhook,
                ))

    # Deliberately NOT wrapped in a ``try``, unlike the sweep below. A separator
    # write failing is a Plex write failing, which belongs to
    # ``reconcile_libraries``' per-library rollback exactly like every other
    # write in the pass; the sweep's wrapper exists because it runs READS below
    # writes this pass already committed, and this does not.
    for result in await _separators(
        session, section, library, library_type, definitions, config,
        label=label, dry_run=dry_run, listing=listing, http=http,
    ):
        actions += result.actions
        results.append(result)

    # Row 269. After every definition and before the sweep, inside the
    # per-library transaction ``reconcile_libraries`` commits -- and, like
    # the separators above, deliberately NOT wrapped: a failure here is a
    # database write failing and belongs to that same rollback.
    commit_actions, reprocess = await member_sort.commit_assignment(
        session, run_cache, library, dry_run
    )
    actions += commit_actions

    if sweep:
        try:
            swept = await _sweep(
                session, section, library, library_type, definitions, config,
                label=label, dry_run=dry_run, listing=listing,
                run_cache=run_cache, notifications=notifications,
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

        # Row 37. Gated by ``sweep``, the same as the delete sweep above and
        # for the same reason: ``sweep`` is the flag that says "this is the
        # whole-library pass" (``reconcile_libraries`` always passes it),
        # while narrower callers exercising one definition at a time do not
        # set it and must not pay for a pass over every OTHER collection in
        # the library. Placed after the delete sweep's try/except rather than
        # inside it: this never deletes anything, so a failure in the delete
        # sweep above must not skip it.
        #
        # ``rows`` (the plan's name for this) is ``_sweep``'s own local,
        # queried fresh inside that function's call -- not something this
        # scope already has -- so "not managed" is answered here with the
        # same query rather than reaching into that function's scope.
        owned_titles = {
            row.title
            for row in (
                await session.execute(
                    select(ManagedCollection).where(
                        ManagedCollection.library == library,
                        ManagedCollection.kind != LOCAL_ASSET_KIND,
                    )
                )
            ).scalars()
        }
        for action in await apply_local_posters_to_unmanaged(
            session, config, http, library,
            {t: c for t, c in listing().items() if t not in owned_titles},
            dry_run=dry_run,
        ):
            actions.append(action)
            results.append(DefinitionResult(
                title=LOCAL_ASSETS_RESULT_TITLE, library=library, actions=[action],
            ))

    return LibraryRun(
        actions=actions, definitions=results, notifications=notifications,
        reprocess=reprocess,
    )


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
    sort_prefix: str | None = None,
    sort_order: str | None = None,
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

    # Roadmap rows 143 + 88: what this definition's members ARE. The builder's
    # own answer (``result.level``) is what a builder that KNOWS says --
    # a library-walking episode builder; the definition's ``builder_level`` is
    # what an operator says for a builder that produces plain ids and cannot
    # know. Either may be non-default; both being non-default and DIFFERENT is
    # two answers to one question, and picking one silently resolves against an
    # index the other half never meant.
    declared = getattr(definition, "builder_level", "item")
    if declared != "item" and result.level != "item" and declared != result.level:
        outcome.failed = True
        outcome.skipped = True
        outcome.actions.append(
            "%r: builder_level is %r but %r builds %s-level members; nothing "
            "was applied. Remove builder_level, or point the definition at a "
            "builder that produces %s ids"
            % (definition.title, declared, definition.builder, result.level, declared)
        )
        return outcome
    level = declared if declared != "item" else result.level
    if level != "item" and ctx.library_type != "Show":
        outcome.failed = True
        outcome.skipped = True
        outcome.actions.append(
            "%r: %s-level members exist only in a Show library, and this pass "
            "is running against a %s library, where the search would match "
            "nothing at all. Narrow the definition with `libraries:` so it only "
            "targets Show libraries"
            % (definition.title, level, ctx.library_type)
        )
        return outcome
    # Belt-and-braces on the EFFECTIVE level, not the declared one. The two
    # schema validators above (`_arr_overrides_need_a_list_builder_at_item_level`,
    # `_builder_level_needs_a_builder_that_reads_it`) run at config load and can only
    # ever see `definition.builder_level` -- so a builder that self-declares a
    # non-item `result.level` while `builder_level` stays "item" (the default)
    # satisfies every one of them and would otherwise reach `restricted_members`
    # / `tag_members` / `sync_membership` below with season/episode members. An
    # episode's TVDB id and a series' TVDB id share one integer namespace, so a
    # numeric collision there is a live write to the wrong series. Since
    # search-tail E-2 `plex_search` sets `result.level` from the definition's
    # own `builder_level`, this is now a reachable refusal and not only a
    # standing one.
    if level != "item" and (
        definition.radarr_restrict
        or definition.sonarr_restrict
        or definition.item_radarr_tag
        or definition.item_sonarr_tag
        or getattr(definition, "sync_to_mdb_list", None)
    ):
        outcome.failed = True
        outcome.skipped = True
        outcome.actions.append(
            "%r: %s-level members cannot be restricted, tagged, or synced to "
            "Radarr/Sonarr/MDBList -- those services only know movies and "
            "shows; nothing was applied" % (definition.title, level)
        )
        return outcome
    index = owned_index(level)
    resolved = resolve_external(index, result.ids)
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
        # The tier-2 pre-step (roadmap row 197). Whether this definition needs
        # it is computed from the TABLE ROW -- ``batched_attributes`` -- and
        # never from config: there is no knob declaring "this one reads tier
        # 2", so a row moved between tiers changes this behaviour with no
        # config edit anywhere, and a filter naming only listing rows pays
        # nothing.
        tags = None
        facts = None
        needed: tuple[str, ...] = ()
        facts_needed: tuple[str, ...] = ()
        # Parsed ONCE for this stage now (roadmap row 158 needs the tree too,
        # and re-parsing for each consumer would compile the same regexes
        # three times). ``_passing`` still parses for itself when this hands
        # it nothing, so every other caller of that function is unchanged.
        parsed = None
        try:
            parsed = parse_filters(definition.filters)
            needed = batched_attributes(parsed)
            facts_needed = facts_attributes(parsed)
        except Exception:
            # A filter that does not parse is _passing's own report; the
            # enrichment pre-step stays out of its way.
            parsed = None
            needed = ()
            facts_needed = ()
        if needed:
            # ``ratingKey`` off a resolved item is reload-safe: it is never
            # None/[] on a real item, and ``resolve.build_owned_index`` already
            # reads it in this same pass.
            keys = [str(getattr(item, "ratingKey", "")) for item in items]
            try:
                # ``ctx.run_cache`` -- the PASS's dict (run_library's, built
                # once at the top of the library loop), not a fresh one per
                # definition. That is what makes two definitions over
                # overlapping sets cost one fetch for the union rather than
                # two for the parts.
                fetched = await ensure_tags(section, ctx.run_cache, keys)
            except Exception:
                logger.exception(
                    "%s: could not enrich %r for its tier-2 filter "
                    "attributes (%s); nothing was applied to it this pass",
                    library, definition.title, ", ".join(needed),
                )
                fetched = None
            if fetched is None or any(key not in fetched for key in keys):
                # The refusal law (facts C3): an item the batch did not
                # answer for must never evaluate as "has no tags" -- that
                # is a full, plausible, wrong membership. Same containment
                # as a filter that could not run.
                outcome.failed = True
                filter_failed = True
                items = []
                outcome.actions.append(
                    "%r: could not read %s for every resolved item (the "
                    "batched Plex metadata read failed or skipped items), "
                    "so the filter was not evaluated and nothing was "
                    "changed this pass"
                    % (definition.title, ", ".join(needed))
                )
            else:
                tags = fetched
        # The facts pre-step (roadmap row 156), phase 2's sibling and
        # deliberately after it: the tier-2 refusal is a HARD failure, and
        # querying our own database for a definition that is not going to
        # evaluate is work nobody reads.
        #
        # It is NOT the same law as phase 2 above. That one refuses the
        # definition when the batch did not answer for every item, because a
        # key Plex skipped is UNKNOWN and evaluating it as "has no tags" would
        # build a full, plausible, wrong collection. Here the database is
        # authoritative about its own rows: an item with no facts row is an
        # ANSWER, excluded by ruling C3's missing rule, and refusing the whole
        # definition over it would refuse every definition on a cold library
        # -- roughly thirty-two weeks of them at the drift sweep's defaults.
        # Only a failed READ refuses.
        #
        # Sitting before row 158's pruning below is free, not incidental: a
        # facts row carries ``search_kinds=()``, so ``_known_tag_values``
        # skips it, and pruning never edits ``items`` -- so nothing 158 does
        # can change which keys this read needs.
        if facts_needed and not filter_failed:
            facts_keys = [str(getattr(item, "ratingKey", "")) for item in items]
            try:
                # ``ctx.run_cache`` -- the PASS's dict, as phase 2 uses it, so
                # two definitions over overlapping sets cost one query for the
                # union rather than two for the parts.
                facts = await ensure_facts(
                    ctx.session, ctx.run_cache, library, facts_keys
                )
            except Exception as error:
                # Class name only. A SQLAlchemy error's message can carry the
                # statement and the DSN (roadmap row 213); the traceback goes
                # to the log and nothing derived from it reaches Plex.
                logger.exception(
                    "%s: could not read %s from this service's own database "
                    "for %r; nothing was applied to it this pass",
                    library, ", ".join(facts_needed), definition.title,
                )
                outcome.failed = True
                filter_failed = True
                items = []
                facts = None
                outcome.actions.append(
                    "%r: could not read %s from this service's own database "
                    "(%s), so the filter was not evaluated and nothing was "
                    "changed this pass"
                    % (definition.title, ", ".join(facts_needed),
                       type(error).__name__)
                )
        # Roadmap row 158, after the enrichment and before the evaluation. In
        # that order deliberately: the enrichment's own refusal is a HARD
        # failure (a definition whose batched read skipped items must not
        # evaluate at all), and warning an operator about the spelling of a
        # filter that is not going to run would be noise about a definition
        # nothing is applying.
        if not filter_failed and parsed is not None:
            parsed, vocabulary_actions = _known_tag_values(
                parsed, ctx, section, library, definition
            )
            outcome.actions += vocabulary_actions
        if not filter_failed:
            kept = _passing(
                definition, items, library, tags=tags, parsed=parsed, facts=facts
            )
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

    # Roadmap row 89(a). After the filter and before the cap, for the reason
    # the filter is before the cap (row 96): ``limit`` counts collection
    # MEMBERS, and capping before a stage that can still remove one would leave
    # a short collection. Read-only -- see ``arr_overrides``.
    restriction_stopped = False
    if not filter_failed and (
        definition.radarr_restrict or definition.sonarr_restrict
    ):
        had_items = bool(items)
        kept, restrict_actions = await restricted_members(
            definition, items,
            library_type=ctx.library_type,
            radarr=ctx.sources.radarr, sonarr=ctx.sources.sonarr,
            run_cache=ctx.run_cache,
        )
        outcome.actions += restrict_actions
        if kept is None:
            outcome.failed = True
            items = []
            restriction_stopped = True
        else:
            items = kept
            # ``restricted_members`` has already appended the sentence naming
            # what happened, so the reconcile call below must be skipped rather
            # than allowed to report "source returned no items" on top of it.
            restriction_stopped = had_items and not items

    if definition.limit is not None:
        # After resolution -- and, since row 96, after the filter -- so a limit
        # counts collection members rather than candidate ids: capping before
        # either would leave a short collection whenever the library was
        # missing one of the first few, or the filter excluded one of them.
        items = items[: definition.limit]

    # Row 269. At the one point that holds the definition's ordered,
    # filtered, capped members. An empty ``items`` marks the definition
    # UNSETTLED (its existing rows are left alone, see member_sort.py), and a
    # failed build is empty by construction (``BuilderResult(ids=[])``).
    if definition.member_sort:
        member_sort.record(ctx.run_cache, library, definition.title, items)

    outcome.skipped = not items
    if preview and definition.create_collection:
        collection = listing().get(definition.title)
        if collection is None:
            outcome.adding = len(items)
        elif items:
            # Row 142(b): the same ownership rule the pass applies, read-only.
            # A collision-blocked collection must not preview a write the pass
            # will refuse. And its fix round: nor may the reverse happen --
            # gating on ``resolve_collision``'s ``ok`` previewed 0/0 for an
            # eligible adoption, which is a write the pass DOES perform, so the
            # question asked here is ``would_proceed``'s "will the pass
            # reconcile this?" and not "is it already ours?". No message is
            # taken: ``reconcile_list_collection`` below runs the rule again
            # under the real dry_run and reports the conflict or the "would
            # adopt", and appending it here would double the action. Costs one
            # ``collection.reload()`` GET per previewed title that exists in
            # Plex -- the per-title price the real pass already pays in
            # ``lists.py``.
            # Row 215 completed the rule with its SHAPE half: ``shape_conflict``
            # first, mirroring ``lists.py``'s own ordering (shape before
            # ownership), because the owned smart-collection-under-a-list-
            # definition case passes the ownership gate and is refused only at
            # the shape gate -- so it previewed counts for a write the pass
            # refuses. No message here either, for the identical reason: the
            # reconcile step reports the conflict once.
            if shape_conflict(
                collection, definition.title, want_smart=False
            ) is None and would_proceed(
                collection, label,
                config.collections.adopt, config.collections.adopt_from,
                config.collections.protect_labels,
            ):
                adding, removing = member_diff(collection, items, definition.sync_mode)
                outcome.adding, outcome.removing = len(adding), len(removing)

    summary, summary_action = await _summary_for(definition, result, summaries)
    if summary_action:
        outcome.actions.append(summary_action)

    if restriction_stopped:
        # ``restricted_members`` said which service and why; calling the
        # reconciler with an empty list would add "source returned no items",
        # which is false -- the source returned items and the restriction is
        # what removed them.
        pass
    elif filter_emptied_a_non_empty_set:
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
    elif not definition.create_collection:
        # Row 269's sort-only mode: the positions recorded above are the
        # whole effect. No ``managed_collections`` row is written, so the
        # orphan sweep has nothing to delete; the title still counts as
        # managed (``definition_titles_for``), so a collection left behind
        # under it is protected rather than swept.
        outcome.actions.append(
            "%r: sort-only; recorded %d member position(s), no collection created"
            % (definition.title, len(items))
        )
    else:
        deltas: dict = {}
        outcome.actions += await reconcile_list_collection(
            session, section, library, definition.title, items, label,
            summary=summary,
            # As on the smart path above: an action string from ``_summary_for``
            # means the effective summary is unresolved this pass, so an absent
            # one asserts nothing and the reconciler must not clear on it.
            summary_asserted=summary_action is None,
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
            sort_prefix=sort_prefix,
            sort_order=sort_order,
            deltas=deltas,
        )
        # What the pass applied, for row 19's per-collection webhook. Absent
        # keys mean a dry run or an unchanged membership: nothing to announce.
        outcome.added = deltas.get("added", 0)
        outcome.removed = deltas.get("removed", 0)
    # Row 31, after everything that decides the membership -- resolution, the
    # filter and the limit -- so what is pushed is what the collection actually
    # holds. Outside the if/elif/else above on purpose: a definition whose
    # filter emptied it still has a truthful (empty) membership to report, and
    # ``sync_membership`` reports rather than pushing in that case.
    if getattr(definition, "sync_to_mdb_list", None):
        outcome.actions += await sync_membership(
            definition, items, index, result.ids,
            is_movie=ctx.library_type == "Movie",
            client=ctx.sources.mdblist,
            apply=config.collections.mdblist_sync_apply and not dry_run and not preview,
        )
    # Roadmap row 89(b), in the same place and for the same reason as row 31's
    # push: after everything that decides the membership, so what is tagged is
    # what the collection actually holds.
    if definition.item_radarr_tag or definition.item_sonarr_tag:
        outcome.actions += await tag_members(
            definition, items,
            library_type=ctx.library_type,
            radarr=ctx.sources.radarr, sonarr=ctx.sources.sonarr,
            run_cache=ctx.run_cache,
            apply=config.collections.arr_tag_apply and not dry_run and not preview,
        )
    return outcome


def _known_tag_values(parsed, ctx, section, library, definition):
    """Roadmap row 158: the definition's tag values, against this library's own.

    Returns ``(tree, actions)`` -- the parsed filter with every unknown value
    pruned out, and the lines to report. Never raises: the check is ADVISORY,
    and every outcome below leaves the membership either identical to what the
    pre-158 evaluation would have produced or narrower.

    **Why here and not in ``parse_filters``.** ``OverlayDefinition.condition``
    shares that parser (``overlays/schema.py:76-83``) and the badge pass holds
    one item, not a library -- there is no section and no
    ``listFilterChoices`` to call. So the vocabulary lives at the one stage
    that has a library in hand.

    **Why here and not at config LOAD.** A definition with no ``libraries:``
    key runs against EVERY library in the pass, so which vocabulary applies is
    not known until now -- the same argument ``require_library_type`` makes.

    **Why warn-and-drop rather than Kometa's refusal** (facts C2). Kometa
    raises (``Plex Error: {attribute}: {value} not found``,
    builder.py:4433-4437) and can afford to, because its own regional defaults
    ENUMERATE the library's vocabulary rather than naming values
    (``both_content_rating_uk.yml:20-22``). This catalog names them: 50 of the
    106 shipped preset collections carry a ``filters:`` block and seven of the
    eight presets involved are regional content-rating lists, switched on one
    region at a time by design. A faithful refusal would turn all of them red
    on the normal case. So this is Kometa's own ``validate: false``
    degradation (:4437-4438) applied unconditionally -- and it is NOT an
    operator-facing ``validate:`` knob, because row 90's cell records that 9c
    refused exactly that error-downgrade class.

    **Three outcomes, and the third is the one to read twice.**

    1. the value resolves -- nothing happens, at the cost of one memoised
       ``listFilterChoices`` per ``(library, libtype-scope, field)`` per PASS;
    2. the value does not resolve -- one warning naming the attribute and the
       value (Global Constraint 7: an operator's own written value is not a
       third-party exception message and echoing it is the whole diagnostic),
       and the value is dropped. A predicate that loses all of them becomes
       ``filters.MATCHES_NOTHING``, warned once more;
    3. the vocabulary cannot be READ -- Plex would not answer, or has no such
       filter for this library. That is ``PlexSearchUnavailable``, the
       resolver's own wrap, and it is MEMOISED there like a success. Nothing is
       dropped and nothing is refused: the definition evaluates exactly as it
       did before this row, which is a correct membership for every
       correctly-spelled value. The alternative -- failing the definition --
       would let one Plex hiccup take 50 preset collections down over a check
       that changes no membership when it succeeds. Reported once per
       attribute, and the resolver's message is already class-name-only.

    A FOURTH outcome is caught separately, and deliberately does not share
    outcome 3's wording. The resolver lets anything outside its five wrapped
    plexapi classes propagate raw and un-memoised, so an exception reaching
    that clause is a fault in THIS service rather than a fact about the
    library. It is logged with its traceback and reported as "could not be
    CHECKED", never as "Plex would not answer" -- same containment, no
    misattribution.
    """
    written = tag_predicates(parsed)
    if not written:
        return parsed, []
    libtype = ctx.library_type.lower()
    resolve = LibraryTagResolver(ctx, section, libtype)
    actions: list[str] = []
    unknown: set[tuple[str, str]] = set()
    unavailable: set[str] = set()
    checkable: list[FilterPredicate] = []

    for predicate in written:
        row = predicate.attribute
        # A row this libtype has no Plex search field for cannot be resolved at
        # all -- ``LibraryTagResolver._field_and_scope`` calls ``field_for``,
        # which raises rather than guessing. Skipped explicitly here so it
        # never reaches the blanket ``except Exception`` below, which would
        # otherwise fold it into a "could not be checked" action instead of
        # the real bug it is (Task 2 review, Minor 5). No shipped row reaches
        # this today: all seven evaluable tag rows are searchable on both
        # libtypes, and the other seven refuse at config load.
        if not row.searchable or libtype not in row.search_kinds:
            continue
        checkable.append(predicate)
        for value in predicate.values:
            pair = (row.name, str(value))
            if row.name in unavailable or pair in unknown:
                continue
            try:
                known = resolve.known(row.name, str(value))
            except PlexSearchUnavailable as error:
                # The resolver's OWN wrap, and the only failure it MEMOISES:
                # ``NotFound``/``BadRequest`` ("Plex has no such filter for
                # this library") and the blanket ``PlexApiException`` /
                # ``RequestException`` / ``ParseError`` clause ("Plex would not
                # answer at all") -- ``plex_search.py:455-500``. Both are
                # already class-name-only inside it, so this message carries no
                # Plex URL and is safe to log in full. Because the resolver
                # memoises it, a second definition naming this attribute costs
                # no second round trip either.
                unavailable.add(row.name)
                logger.warning(
                    "%s: %r: could not read this library's %s vocabulary (%s), "
                    "so its filter values were used as written",
                    library, definition.title, row.name, error,
                )
                actions.append(
                    "%r: this library's %s vocabulary could not be read, so the "
                    "filter's values were used as written and not checked this "
                    "pass" % (definition.title, row.name)
                )
                continue
            except Exception:
                # A DIFFERENT thing, and calling it the same would send an
                # operator to look at Plex for a bug in this process. The
                # resolver deliberately lets anything outside its five wrapped
                # plexapi classes propagate raw and UN-memoised, so an
                # exception reaching here is this service, not the library. The
                # containment still holds -- one definition's check is skipped,
                # never the pass -- but the traceback goes to the log and the
                # action string says something else. Nothing derived from the
                # exception reaches that string, which is the rule every other
                # catch in this module keeps.
                unavailable.add(row.name)
                logger.exception(
                    "%s: %r: checking the %s filter values raised",
                    library, definition.title, row.name,
                )
                actions.append(
                    "%r: the %s filter values could not be checked this pass, "
                    "so they were used as written"
                    % (definition.title, row.name)
                )
                continue
            if known:
                continue
            unknown.add(pair)
            logger.warning(
                "%s: %r: %s: %r is not one of the %s values this library uses; "
                "it was dropped from the filter",
                library, definition.title, predicate.field, value, row.name,
            )
            actions.append(
                "%r: %s: %r is not one of the %s values this library uses, so "
                "it was dropped from the filter. Check the spelling against "
                "the library's own list"
                % (definition.title, predicate.field, value, row.name)
            )

    if not unknown:
        return parsed, actions

    # Deduped by (attribute, its written values) -- not by predicate identity
    # -- so the same values named twice (``any: [{content_rating: X}, {content_
    # rating: X}]``) get one line rather than two substantively identical ones
    # differing only in which ``filters.any[i]`` wrote them (Task 2 review,
    # Minor 1).
    emptied: set[tuple[str, tuple[str, ...]]] = set()
    for predicate in checkable:
        name = predicate.attribute.name
        key = (name, tuple(str(value) for value in predicate.values))
        if key in emptied or not all((name, value) in unknown for value in key[1]):
            continue
        emptied.add(key)
        logger.warning(
            "%s: %r: every %s value in %s is unknown to this library",
            library, definition.title, name, predicate.field,
        )
        actions.append(
            "%r: every %s value in %s is unknown to this library, so it "
            "matches nothing and no members were kept"
            % (definition.title, name, predicate.field)
        )

    pruned = without_values(
        parsed,
        lambda predicate, value: (predicate.attribute.name, str(value)) in unknown,
    )
    return pruned, actions


def _passing(
    definition: CollectionDefinition,
    items: list,
    library: str,
    tags: dict | None = None,
    parsed=None,
    facts: dict | None = None,
) -> list | None:
    """The items this definition's ``filters:`` keeps, or None if it could not
    be evaluated at all.

    The stage sits between resolution and the cap and is deliberately dumb:
    order is preserved (the builder's order is the collection's), and nothing
    here reaches Plex. For the listing rows that is because ``PlexItemView``
    reads only values the section listing already carried; for the tier-2 rows
    it is because ``_run_one`` fetched the enrichment BEFORE this stage, once
    for the whole resolved set, and ``tags`` is that dict -- so reading a genre
    here is a dictionary lookup. Either way a filter never costs one request
    per item (``filter_values``).

    ``tags`` is ``{rating_key: ItemTags}`` or None for a tier-1-only filter.
    None is not "no tags found": ``_run_one`` refuses the definition outright
    before reaching here if any resolved item was missing from a batch it did
    need, so a None here means no batched attribute was named at all.

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
    because the model is copied by expansion, and a compiled regex would not
    survive the copy; it is microseconds against a pass that has just walked
    the library.

    ``parsed`` is the already-parsed, already-pruned tree when the engine has
    one -- row 158's vocabulary stage builds it, and handing it here is what
    makes a dropped value actually reach the evaluation. None means "parse it
    yourself", which is what every caller outside ``_run_one`` does and what
    ``_run_one`` itself falls back to when the filter did not parse (so the
    report below is still this function's).

    ``facts`` is ``{rating_key: facts_read.ItemFactsValues}`` or None for a
    filter naming no facts row. Unlike ``tags``, a key MISSING from a non-None
    ``facts`` cannot happen: ``ensure_facts`` answers for every key it was
    asked about, because this service's own database is authoritative about
    its own rows.
    """
    try:
        if parsed is None:
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
            if evaluate(
                parsed,
                PlexItemView(
                    item,
                    tags=None if tags is None
                    else tags.get(str(getattr(item, "ratingKey", ""))),
                    facts=None if facts is None
                    else facts.get(str(getattr(item, "ratingKey", ""))),
                ),
                now=now,
            )
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

    Three sources, in order: the one written in the config, the one the builder
    derived, and the one TMDB holds for the collection the definition names
    (roadmap row 30). The static summary wins because it is an explicit choice;
    a pull that silently overrode it would be a setting that reads as applied
    and is not.

    The builder's own summary comes SECOND because that is Kometa's order --
    ``update_details`` runs ``summary`` -> ``translation`` -> ... ->
    ``tmdb_summary`` (``modules/builder.py:5273-5284``), and what our builders
    derive is upstream's translation string
    (``docs/research/kometa-collections.md`` §5). So a chart definition that
    also sets ``tmdb_summary:`` keeps the transcribed chart string and the
    setting does nothing (roadmap row 251). The elided middle rungs of
    upstream's ladder name builders this service does not have, so this is a
    two-element swap and not a re-implementation of that ladder.

    The pull is contained here rather than by the caller's builder wrapper: a
    summary is cosmetic, and a TMDB outage must leave the collection's
    membership reconciled, not the whole definition failed. So a failure means
    the summary falls back to the builder's (usually None) and the pass says
    so -- and nothing derived from the exception reaches the action string,
    because a provider error carries the URL it failed on.
    """
    if (
        definition.summary is not None
        or result.summary is not None
        or definition.tmdb_summary is None
    ):
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


def _family_state(
    definitions: list[CollectionDefinition], library: str, run_cache: dict
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """``({label: definition title}, {label: what it built this pass})``.

    Pure -- it reads the registry, the definitions and the pass's scratch, never
    Plex -- and library-scoped for ``definition_titles_for``'s reason: a family
    aimed at another library must not protect, or sweep, this one's collections.

    The builder is asked rather than the module imported, so the engine keeps
    knowing only the protocol: a builder that manages a family whose titles it
    cannot enumerate offline says so by having a ``family_label``, and says what
    it actually built by having a ``generated_titles``.

    A family ABSENT from the second mapping is the fail-closed state: it did not
    run this pass (outside its schedule) or it refused before deriving anything,
    and the sweep must not consider any of its collections.
    """
    labels: dict[str, str] = {}
    generated: dict[str, set[str]] = {}
    for definition in definitions:
        if not _targets(definition, library):
            continue
        builder = REGISTRY[definition.builder]
        namer = getattr(builder, "family_label", None)
        if namer is None:
            continue
        label = namer(definition)
        labels[label] = definition.title
        reader = getattr(builder, "generated_titles", None)
        built = reader(run_cache, definition) if reader is not None else None
        if built is not None:
            generated[label] = built
    return labels, generated


def _why(family_title: str | None) -> str:
    """Why one candidate is being swept, as the fragment the three delete
    messages share. A family member and an ordinary orphan are the same kind of
    thing to every guard below and a different thing to the operator reading the
    report -- the collection is gone either way, but only one of them is
    something they can put back by widening ``include:``."""
    return (
        "the %r family no longer builds it" % family_title
        if family_title else "no definition builds it"
    )


def _webhook_for(definitions: list[CollectionDefinition], title: str | None) -> str:
    """The per-collection webhook of the definition that owns ``title``.

    Empty when no definition names it -- which is the ordinary case for a
    swept collection: it is being deleted precisely because nothing builds it
    any more, so there is no per-collection target and the delete goes to the
    global one.
    """
    if title is None:
        return ""
    for definition in definitions:
        if definition.title == title:
            return definition.changes_webhook
    return ""


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
    run_cache: dict,
    notifications: list | None = None,
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
      Operator note, and it is new with the family sweep below: a dynamic
      family that NARROWS reaches this cap without anybody editing a config --
      a tightened ``include:``, or the library simply ceasing to hold six of
      the values it held last pass, is six candidates. And reaching it
      suspends the sweep for the WHOLE library, not just for that family, so
      unrelated orphans stop being deleted too until the operator looks; the
      refusal names both numbers, and one refusal covers the whole sweep,
      which is why it cannot itself name the family that caused it.
    - a **dynamic family's members** are enumerated by the family LABEL, which
      is Kometa's own handle for the same job (``append_label``, meta.py:1421,
      and the ``sync:`` sweep at :1300, :1456-1461). The ones the family
      REBUILT this pass are managed and are not candidates; the ones it did not
      are candidates like any other, through every guard above. A family that
      did not build anything this pass -- outside its schedule, or refused --
      protects all of its collections and says so once, for the family rather
      than once per member: the alternative turns one failed Plex read into a
      deleted family.

    The enumeration is library-scoped (``definition_titles_for``) because a
    delete decision cannot use the leftovers report's deliberately
    over-inclusive set: that one counts a definition aimed at another library
    as managing this one's titles, which is the safe direction for a report
    and the wrong one for a sweep.
    """
    managed = definition_titles_for(
        definitions, listing().values(), library, library_type, config
    )
    families, generated = _family_state(definitions, library, run_cache)
    rows = {
        row.title: row
        for row in (
            await session.execute(
                select(ManagedCollection).where(ManagedCollection.library == library)
            )
        ).scalars()
    }

    results: list[DefinitionResult] = []
    candidates: list[tuple[str, object, ManagedCollection, str | None]] = []
    for family, family_title in families.items():
        if family in generated:
            continue
        # One line for the FAMILY, not one per member. A healthy fifty-member
        # family that happened to be outside its schedule would otherwise emit
        # fifty identical lines saying nothing was deleted.
        results.append(_swept(family_title, library, (
            "the %r family did not build anything this pass -- it was "
            "outside its schedule, or it refused -- so none of its collections "
            "were considered for deletion" % family_title
        )))

    for title, collection in listing().items():
        if title in managed or title not in rows:
            continue
        if rows[title].kind in ("operator", LOCAL_ASSET_KIND):
            # An operator created this directly (``ops/blank``) -- no
            # definition enumerates its title, so it always lands here, and
            # it must never be swept just because nothing builds it. Reported
            # rather than silently skipped, and regardless of
            # ``delete_unconfigured``: that setting decides what an
            # unattended pass may delete, and this was never such a
            # candidate in the first place.
            # ...and a LOCAL_ASSET_KIND row is a poster-hash ledger for a
            # collection this service never owned (row 37) -- deleting it
            # would delete somebody else's collection over a bookkeeping row.
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
        matched = [one for one in families if has_label(collection, one)]
        family_title: str | None = None
        if matched:
            # A collection can carry more than one family's label (both
            # reconciled it additively in the same or an earlier pass), so
            # every matching family must protect it, not just the first one
            # iteration happens to reach: a candidate only when EVERY family
            # that labels it ran and NONE of them built it this pass.
            if any(
                one not in generated or title in generated[one]
                for one in matched
            ):
                continue
            family_title = families[matched[0]]
        if not has_label(collection, label):
            logger.info(
                "%s: %r has a managed row but not the %r label; not ours to delete",
                library, title, label,
            )
            continue
        candidates.append((title, collection, rows[title], family_title))

    if not config.collections.delete_unconfigured:
        for title, _, _, family_title in candidates:
            results.append(_swept(title, library, (
                "%r: %s; set collections.delete_unconfigured to delete it"
                % (title, _why(family_title))
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

    for title, collection, row, family_title in candidates:
        why = _why(family_title)
        if dry_run:
            results.append(_swept(
                title, library, "would delete %r: %s" % (title, why), 1
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
        plex_rating_key = str(getattr(collection, "ratingKey", "") or "")
        # {} rather than {"plex": ""} when Plex gave no ratingKey: an empty
        # string is not a native id, and a reader trusting "refs" at face
        # value must not be handed a fake one.
        refs = {"plex": plex_rating_key} if plex_rating_key else {}
        session.add(EventLog(
            source="collections",
            event_type="collection_deleted",
            # Identity only. The row's stats are gone with it and the
            # collection's members were never ours to record.
            payload={
                "library": library,
                "title": title,
                # Legacy key, kept for any reader still watching for it
                # (spec §4.5); "refs" is the full per-server picture.
                "rating_key": plex_rating_key,
                "refs": refs,
            },
            outcome="deleted; %s" % why,
        ))
        # Flushed now, rather than left for the caller's eventual commit, so
        # this delete's audit trail is durable in the transaction before the
        # next candidate's ``collection.delete()`` -- the one Plex-side call
        # in this loop that can raise -- gets a chance to.
        await session.flush()
        if notifications is not None:
            # Facts adjudication 4: the EventLog row above is the ready hook,
            # so this is the same fact reaching a second sink under the same
            # event name -- not a second delete detection. Routed to the
            # family's webhook when a definition still owns the title, and to
            # the global target otherwise: a delete is destructive, rare and
            # capped by max_deletes, so "nowhere" is the wrong answer for it.
            notifications.append(CollectionNotification(
                event="collection_deleted",
                summary="deleted collection %r in %s" % (title, library),
                detail={
                    "library": library,
                    "collection": title,
                    "rating_key": plex_rating_key,
                    "refs": refs,
                    "reason": why,
                },
                url=_webhook_for(definitions, family_title),
            ))
        results.append(_swept(
            title, library, "deleted %r: %s" % (title, why), 1
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


async def _separators(
    session: AsyncSession,
    section,
    library: str,
    library_type: str,
    definitions: list[CollectionDefinition],
    config,
    label: str,
    dry_run: bool,
    listing,
    http: httpx.AsyncClient | None,
) -> list[DefinitionResult]:
    """One blank divider per group these definitions put collections in.

    After the definitions rather than before, for one reason: the set of active
    groups is derived FROM them, so running first would mean deciding what the
    pass built before it had built it. Before the sweep, though ordering
    against it does not matter for correctness: ``listing()`` is memoised
    once per library on its first call, well before this function ever runs,
    so a divider created here is invisible to whichever of the two reads it
    -- the sweep cannot delete it in the same pass, in either order.

    One result per separator, under its own title. The Common Sense divider used
    to fold its actions into the family's single result; a heading is now its own
    thing in every library, and a preview that hid three of them inside one
    family's row would be reporting the shape this phase replaced.

    ``http`` and ``config`` are threaded through because a separator carries a
    poster like any other collection this service manages -- the three groups
    with measured ``Default-Images`` artwork, at least. Without them
    ``posters_enabled`` reads False and every divider would silently lose the
    poster the Common Sense one has shipped with (``golden_port.json`` records
    it), which is what makes ``SeparatorSpec.poster_key`` mean anything.
    """
    specs = groups.separator_specs(
        [d for d in definitions if _targets(d, library)], library_type, config,
    )
    if not specs:
        return []

    stored = {
        row.title: row
        for row in (
            await session.execute(
                select(ManagedCollection).where(ManagedCollection.library == library)
            )
        ).scalars()
    }
    collections = config.collections
    results: list[DefinitionResult] = []
    for spec in specs:
        actions = await reconcile_separator(
            session, section, library, LIBTYPES[library_type], label, spec,
            listing(), stored,
            collections.adopt, collections.adopt_from or [],
            collections.adopt_removes_prior_label, dry_run,
            collections.protect_labels or [], http, config,
        )
        results.append(DefinitionResult(
            title=spec.title, library=library, actions=list(actions),
            # ``skipped`` covers every reason nothing was applied
            # (``DefinitionResult``'s own docstring) -- a divider this pass
            # created or updated is not that, so it is only true when the
            # reconcile had no actions to report (already current).
            skipped=not actions,
        ))
    return results


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

    Plus one term that is not a definition's title at all: every active group's
    separator (roadmap row 49). It is folded in at the end, from the same
    ``groups`` derivation ``_separators`` reconciles through.
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
    # Every active group's blank divider. Folded in here rather than by the
    # callers, so the delete sweep, the leftovers report and the config-load
    # collision check all see the same set -- a title one of them missed is a
    # heading the sweep reads as an orphan or the report reads as a prior
    # tool's. ``config`` is read only through ``config.collections``, which is
    # what lets ``CollectionsConfig._titles_must_not_collide`` call this with a
    # SimpleNamespace shim of that one section.
    titles |= groups.separator_titles(definitions, library_type, config)
    return titles
