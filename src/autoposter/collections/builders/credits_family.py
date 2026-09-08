"""``credits_family``: one smart collection per person with enough credits.

The fourth family shape, distinguished the way the others are: ``cs_bucket``
manages SMART collections from a static table, ``dynamic`` SMART collections
from a Plex enumeration, ``facts_family`` LIST collections from a database
enumeration -- and this manages SMART collections from a DATABASE enumeration,
because the question the pack asks ("everyone with at least `depth`
appearances, capped to the `limit` most-credited") needs COUNTS, which
``listFilterChoices`` can never answer (values only -- roadmap row 194), and
the counts live in this service's ``item_credits`` cache (the phase-B scan).
Each person's MEMBERSHIP is still Plex's: a smart collection whose filter is
the person's own tag, resolved through the same ``LibraryTagResolver`` every
tag search uses -- upstream's own semantic (the actor TAG, the library's credit
data), deliberately NOT a TMDb filmography, which is a different membership
under the same name (row 194's open question, answered and disclosed in the
pack descriptions).

**The counts are a floor, not a cast census.** The phase-B probe measured a
server-side cap of 200 ``Role`` children per item (D1), so ``enumerate_credits``
counts what Plex RETURNED. The bias is directional and worth stating in the one
place that turns these numbers into a ranking: a person whose every appearance
is in a >200-role cast can be ABSENT from the family entirely, not merely
ranked low. Nothing here may be described as "the top N actors" as a
completeness claim, and the packs built on it do not.

Coverage is honest, not hidden: the enumeration reflects only items the credits
scan has visited, every family-level refusal reports the attempted/total
numbers, and the weekly scan (``scheduler.credits_scan_days``) is what closes
the gap.

Joins the delete sweep through ``family_label``/``generated_titles``, the
protocol ``builders/dynamic.py`` names. The label prefix differs from both
sibling families' so the three sweeps cannot enumerate each other's members.

**A family-level refusal freezes its existing members, deliberately** (roadmap
row 223, answered 2026-09-08: the coupling is KEPT). The over-cap branch returns
before the record seed at ``ctx.run_cache[_generated_key(...)]``, so a refused
family's collections keep stale sort prefixes and take no poster updates -- and,
by the same return, are guaranteed not to be swept, because
``engine.py:1418-1421`` reads an absent record as the fail-closed state. The
refusal is logged as well as reported, ``builders/dynamic._refused``'s line
verbatim, so a family that stops updating says so on the logs page.

A single person's collection is the opposite case, not frozen but silently
dropped: see the row-224 comment at the record seed below for how a vocabulary
narrowing becomes a delete.
"""
import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoposter.collections.builders.base import (
    LibraryTypeMismatch,
    SmartContext,
    require_library_type,
)
from autoposter.collections.builders.plex_search import (
    LibraryTagResolver,
    PlexSearchUnavailable,
)
from autoposter.collections.credits import (
    CREDIT_KINDS,
    credits_coverage,
    enumerate_credits,
)
from autoposter.collections.dynamic_keys import derive_keys
from autoposter.collections.dynamic_titles import (
    DuplicateFamilyTitle,
    family_titles,
    title_format_names_the_key,
)
from autoposter.collections.filters import parse_filters
from autoposter.collections.search_sorts import KNOWN_SORT_NAMES, SortNotAvailable
from autoposter.collections.search_url import (
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    TagValueNotFound,
    build_search_url,
)
from autoposter.collections.smart import (
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    reconcile_smart_collection,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FAMILY_LABEL_PREFIX", "TITLE_FORMATS", "CreditsFamilyBuilder",
    "CreditsFamilyParams", "family_label", "generated_titles",
]

FAMILY_LABEL_PREFIX = "autoposter-credits: "

# Upstream's title shapes: the actor packs title bare names, the three movie
# crews carry the role suffix (the same "<name> (Director)" shape the 8c
# starter set already borrowed from defaults/movie/director.yml).
#
# Public because the CATALOG renders each pack's shape line out of it -- the
# same derived-not-restated property both sibling families are held to, so the
# sentence the picker shows cannot describe a title this builder will not
# produce.
TITLE_FORMATS = {
    "actor": "<<key_name>>",
    "director": "<<key_name>> (Director)",
    "writer": "<<key_name>> (Writer)",
    "producer": "<<key_name>> (Producer)",
}
# director/writer/producer are movie-only end to end: their SEARCH is in
# Kometa's movie_only_searches (plex.py:430-436), their FILTER scope is the
# movie/episode key, and their upstream packs are defaults/movie/*. The probe
# measured the third reason (D7): a show section enumerates all three EMPTY
# rather than refusing them, so a family that got as far as querying would
# report "nobody qualifies" for a library that can never answer. actor is both
# (defaults/both/actor.yml).
_KINDS_FOR_TYPE = {
    "actor": ("Movie", "Show"),
    "director": ("Movie",),
    "writer": ("Movie",),
    "producer": ("Movie",),
}

# ``builders/dynamic.REFUSALS``, transcribed rather than imported, and for that
# module's exact reasons: every exception class an operator's configuration can
# cause once it meets a real library, named exhaustively rather than as
# ``Exception`` so a genuine bug in THIS module still reaches the engine as a
# failure instead of being reported to the operator as something about their
# family. Copied rather than shared because the two families' per-key paths are
# free to diverge; the eight classes come from the same five modules
# ``dynamic.py`` imports them from. ``parse_filters`` raises a bare
# ``ValueError`` and is deliberately NOT here -- see ``apply``.
_REFUSALS = (
    LibraryTypeMismatch,
    PlexSearchUnavailable,
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    SortNotAvailable,
    TagValueNotFound,
)


def family_label(definition) -> str:
    """The label every collection in ``definition``'s family carries."""
    return "%s%s" % (FAMILY_LABEL_PREFIX, definition.title)


def _generated_key(label: str) -> str:
    """The pass's run-cache key for one family's generated titles, keyed on the
    family LABEL for ``dynamic._generated_key``'s reason: the label is what the
    sweep has in hand when it finds a member."""
    return "credits_family:generated:%s" % label


def generated_titles(run_cache: dict, definition) -> set[str] | None:
    """What ``definition``'s family built this pass, or ``None``.

    ``None`` is FAIL-CLOSED -- the family never got as far as deciding -- and a
    set is what it derived this pass; verbatim ``builders/dynamic.py``'s
    contract, because ``engine._family_state`` reads both the same way. A sweep
    must not delete a family's collections on a pass that refused: one
    transient failure would otherwise read as "the operator narrowed the
    family" and take every collection in it.
    """
    return run_cache.get(_generated_key(family_label(definition)))


class CreditsFamilyParams(BaseModel):
    """``data: {depth, limit}`` is upstream's own vocabulary for these packs
    (all four transcribed in .superpowers/sdd/p10c-upstream-person.md 2.1); the
    narrowing/titling knobs are the shared family vocabulary ``DynamicParams``/
    ``FactsFamilyParams`` already teach, so an operator who has learned one
    family's knobs has learned this one's.

    Absent on purpose: ``other_name`` (a leftovers bucket ORing every
    sub-threshold person into one query is a URL longer than the chunk cap the
    phase-B probe measured), and ``minimum_items`` (``depth`` IS this family's
    floor, counted in the database before a single Plex call).
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    type: str
    depth: int = Field(default=5, ge=1)
    limit: int = Field(default=25, ge=1)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    addons: dict[str, list[str]] = Field(default_factory=dict)
    custom_keys: bool = True
    key_name_override: dict[str, str] = Field(default_factory=dict)
    title_override: dict[str, str] = Field(default_factory=dict)
    remove_prefix: list[str] = Field(default_factory=list)
    remove_suffix: list[str] = Field(default_factory=list)
    title_format: str | None = None
    sort_by: list[str] = Field(default_factory=list)
    # The same refuse-over-surprise floor both siblings pin, and the same
    # number. ``limit`` already caps the family at 25 by default, so reaching
    # this means an operator raised it on purpose and then went past what they
    # can hold in their head.
    max_collections: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def _the_type_must_be_a_credit_kind(self) -> "CreditsFamilyParams":
        if self.type not in CREDIT_KINDS:
            raise ValueError(
                "%r is not a credit kind this service scans. Options: %s"
                % (self.type, ", ".join(CREDIT_KINDS))
            )
        return self

    @model_validator(mode="after")
    def _a_title_format_must_name_the_key(self) -> "CreditsFamilyParams":
        """Both siblings' rule, and this family needs it for their reason:
        without ``<<key_name>>`` every person in the family would be given the
        same name, and all but the first would then refuse as duplicates."""
        if self.title_format is not None and not title_format_names_the_key(
            self.title_format
        ):
            raise ValueError(
                "`title_format` has to carry `<<key_name>>` (or `<<title>>`, "
                "which means the same value): without one, every collection in "
                "the family would be given the same name"
            )
        return self

    @model_validator(mode="after")
    def _sort_names_must_exist_somewhere(self) -> "CreditsFamilyParams":
        """``DynamicParams``' own gate, and worth having on this family too: a
        sort Plex has no column for would otherwise refuse once per PERSON at
        run time -- twenty-five identical refusals for one typo."""
        for name in self.sort_by:
            if name not in KNOWN_SORT_NAMES:
                raise ValueError(
                    "sort_by %r is not a Plex sort. Options: %s"
                    % (name, ", ".join(sorted(KNOWN_SORT_NAMES)))
                )
        return self


class CreditsFamilyBuilder:
    """A family of smart collections, one per sufficiently-credited person."""

    type_name = "credits_family"
    # The engine's marker for "this one applies itself" -- see SmartContext.
    smart = True
    params_model = CreditsFamilyParams

    # ``DynamicBuilder``'s seven, verbatim in structure -- this definition names
    # a family of smart collections and every refusal holds for the same
    # reasons; only the narrowing advice differs. ``sort_title``,
    # ``collection_mode`` and the ``visible_*`` flags are deliberately absent:
    # they are properties of the collection OBJECT rather than of its
    # membership, and the create path applies them (roadmap row 104).
    refused_definition_fields = {
        "summary": (
            "this definition names a FAMILY of collections, one per person, so "
            "a single summary could not be the summary of any particular one "
            "of them"
        ),
        "sort": (
            "Plex evaluates each collection's membership live, so there is no "
            "resolved order to set. Use `params.sort_by` for the search's own "
            "order"
        ),
        "limit": (
            "Plex evaluates each collection's membership from a filter, so "
            "there is no resolved list to cap. `params.limit` caps how many "
            "PEOPLE the family builds; `params.depth` is its floor"
        ),
        "sync_mode": (
            "Plex owns these collections' membership; there is nothing for "
            "this service to sync or append to"
        ),
        "item_label": (
            "this service never resolves these collections' members -- Plex "
            "does -- so there is no list of items to label"
        ),
        "tmdb_summary": (
            "this definition names a family of collections, so one borrowed "
            "overview could not be the summary of any particular one of them"
        ),
        "filters": (
            "a `filters:` block narrows a membership this service resolved, "
            "and these are never resolved here -- the items are chosen inside "
            "Plex, by each person's own tag filter. Narrow the family with "
            "`depth:`, `limit:`, `include:` or `exclude:` instead"
        ),
    }

    def family_label(self, definition) -> str:
        """The label this definition's collections carry -- a method as well as
        a module function so ``engine._family_state`` can ask the REGISTRY
        entry rather than importing this module by name."""
        return family_label(definition)

    def generated_titles(self, run_cache: dict, definition) -> set[str] | None:
        """What this definition's family built this pass; see the module
        function of the same name for what ``None`` means."""
        return generated_titles(run_cache, definition)

    async def apply(self, ctx: SmartContext) -> list[str]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'credits_family' builder builds the family its "
                "definition names, so it cannot run without one"
            )
        params = CreditsFamilyParams.model_validate(definition.params)
        libtype = ctx.library_type.lower()
        actions: list[str] = []

        # Above every read, deliberately: a movie-only type on a show library
        # costs zero database work and zero Plex round trips.
        require_library_type(
            "the 'credits_family' builder's %r type" % params.type,
            ctx.library_type, _KINDS_FOR_TYPE[params.type],
        )
        if ctx.session is None:
            raise ValueError(
                "the 'credits_family' builder reads this service's own "
                "database and was given no session"
            )

        # A savepoint around the reads rather than the bare session, for
        # ``facts_family.expand``'s reason: the engine contains one definition's
        # failure and carries on with the next, and a failed query on the shared
        # session would poison the transaction instead -- turning one bad
        # definition into every later statement in the pass raising.
        async with ctx.session.begin_nested():
            counted = await enumerate_credits(
                ctx.session, params.type,
                library=ctx.library, library_type=ctx.library_type,
            )
            attempted, total = await credits_coverage(
                ctx.session, library=ctx.library, library_type=ctx.library_type,
            )

        eligible = [
            (person, count) for person, count in counted if count >= params.depth
        ]
        if not eligible:
            return [
                "%r built nothing: no %s has %d appearance(s) in %r yet. The "
                "credits scan has visited %d of %d item(s) there; the family "
                "fills in as the weekly scan works through the rest "
                "(scheduler.credits_scan_days). Lower `depth:` if the "
                "threshold is the problem"
                % (definition.title, params.type, params.depth, ctx.library,
                   attempted, total)
            ]

        # Roadmap row 224. The cap decides who this family BUILDS, so it has to
        # be applied to the people this library can actually be searched for --
        # not to the raw counts, which let a person whose tag the vocabulary
        # does not know take a slot and then refuse inside the loop below,
        # wasting it instead of backfilling from the next-most-credited person
        # who would have searched cleanly. Measured on the live deployment at
        # the ruling: 29 of 75 slots across three person packs produced no
        # collection, against 1,878 eligible people to backfill from.
        #
        # It costs no extra Plex round trip: ``LibraryTagResolver`` memoises one
        # ``listFilterChoices`` per (library, libtype-scope, field) per pass on
        # ``ctx.run_cache``, and this builder already pays for that exact call
        # inside the loop. The construction moved up here from below the cap for
        # the same reason -- it is the same object, asked earlier.
        resolver = LibraryTagResolver(ctx, ctx.section, libtype)
        try:
            searchable = [
                (person, count) for person, count in eligible
                if resolver.known(params.type, person)
            ]
        except PlexSearchUnavailable as refusal:
            # ``engine.py:1158``'s own clause, for its reason. This is the
            # resolver's ONLY memoised failure -- "Plex has no such filter for
            # this library", or "Plex would not answer at all" -- and both are
            # already class-name-only inside it, so the message carries no Plex
            # URL and is safe to log whole (row 213). Uncaught it would escape
            # the engine's unwrapped smart dispatch and cost the library its
            # whole reconcile, which is exactly what the per-person catches
            # below exist to prevent; so the ranking falls back to counts alone
            # and those catches stay the fallback they always were.
            logger.warning(
                "%s: %r: could not read this library's %s vocabulary (%s), so "
                "the ranking was capped on the counts alone",
                ctx.library, definition.title, params.type, refusal,
            )
            searchable = eligible
        if not searchable:
            # NOT the sentence above: that one says the SCAN has not found
            # anybody yet, and it would be false here -- people cleared the
            # floor and this library's own tag vocabulary knows none of them.
            # Numbers and the library, never a person: naming every one of them
            # is not a report.
            return [
                "%r built nothing: %d %s(s) have %d appearance(s) or more in "
                "%r, but the %s tag vocabulary of %r knows none of them, so "
                "none can be searched for. A Plex library scan is what "
                "reconciles the credits cache with the tags Plex will answer on"
                % (definition.title, len(eligible), params.type, params.depth,
                   ctx.library, params.type, ctx.library)
            ]
        capped = searchable[: params.limit]
        if len(searchable) > len(capped):
            actions.append(
                "%r: %d %s(s) meet depth %d; built the %d most-credited "
                "(`limit`)"
                % (definition.title, len(eligible), params.type, params.depth,
                   len(capped))
            )
        if len(searchable) < len(eligible):
            actions.append(
                "%r: %d of the %d %s(s) that met depth %d are not in the tag "
                "vocabulary of %r and were dropped before `limit:`, so the cap "
                "was filled from the next-most-credited people instead"
                % (definition.title, len(eligible) - len(searchable),
                   len(eligible), params.type, params.depth, ctx.library)
            )

        # The key IS the person's name here, unlike ``dynamic``'s Plex-keyed
        # types: the credits cache stores the Plex TAG NAME, which is both what
        # an operator writes in ``include:``/``exclude:`` and what the tag
        # resolver looks up. One string, no key/title split to get wrong.
        derived = derive_keys(
            [(person, person) for person, _count in capped],
            include=params.include, exclude=params.exclude,
            addons=params.addons, custom_keys=params.custom_keys,
        )
        try:
            titled = family_titles(
                derived,
                library_type=ctx.library_type,
                title_format=params.title_format or TITLE_FORMATS[params.type],
                key_name_override=params.key_name_override,
                title_override=params.title_override,
                remove_prefix=params.remove_prefix,
                remove_suffix=params.remove_suffix,
                # No leftovers bucket at all -- see ``CreditsFamilyParams``.
                other_name=None,
            )
        except DuplicateFamilyTitle as refusal:
            return actions + ["refused %r: %s" % (definition.title, refusal)]
        if not titled:
            return actions + [
                "%r built nothing: every eligible %s was excluded. Widen "
                "`include:`, raise `limit:` (a person ranked below it never "
                "reached the exclusions), or remove the definition"
                % (definition.title, params.type)
            ]
        if len(titled) > params.max_collections:
            # Roadmap row 223, this builder's half of the same line
            # ``builders/dynamic._refused`` has carried since it shipped: a
            # family-level refusal freezes every existing member out of its own
            # re-sort and poster updates until the operator acts, and an
            # operator who never opens the run report has no other way to learn
            # that happened. The reported string is unchanged.
            why = (
                "this would create %d collections in %r and `max_collections` "
                "is %d. Narrow with `depth:`/`limit:`/`exclude:`, or raise "
                "`max_collections` past %d if that is really what you want"
                % (len(titled), ctx.library, params.max_collections,
                   len(titled))
            )
            logger.warning(
                "%s: %r was not built: %s", ctx.library, definition.title, why
            )
            return actions + ["%r built nothing: %s" % (definition.title, why)]

        # The narrowing between the cap and the built family, reported rather
        # than left to be inferred from a short list (review F13).
        if len(titled) < len(capped):
            actions.append(
                "%r: %d of the %d most-credited %s(s) built no collection "
                "(`include:`/`exclude:`/`addons:`)"
                % (definition.title, len(capped) - len(titled), len(capped),
                   params.type)
            )

        # The sweep's record -- seeded with every derived title BEFORE any
        # reconcile, ``dynamic.py``'s law: a write failure is not narrowing.
        # Never ``set()``: the engine reads absence and emptiness as opposites,
        # and an empty record would silently delete the family. Every refusal
        # that could leave ``titled`` empty has already returned.
        #
        # Row 224 makes this record SHRINK as well as grow: a person whose tag
        # has left the library's vocabulary is absent from ``searchable`` and
        # therefore from ``titled`` and this record, even though their own
        # collection still exists in Plex -- so the sweep treats it as
        # unmanaged and deletes it (gated by ``collections.delete_unconfigured``,
        # capped by ``max_deletes``). The same semantics as the dynamic
        # families' vanished values, accepted for row 224 on 2026-09-08. A
        # narrowing of ``resolver.known()`` is therefore a delete-path change
        # and must be reviewed as one.
        generated: set[str] = {unit.title for unit in titled}
        ctx.run_cache[_generated_key(family_label(definition))] = generated

        # The definition's own settings plus the family label. ``model_copy``
        # rather than a re-validated construction, for ``engine._completed``'s
        # reason: both halves are already validated.
        settings = definition.model_copy(
            update={"labels": [*definition.labels, family_label(definition)]}
        )
        # The pass's one listing, fetched here rather than at context
        # construction so a definition that refuses above costs nothing.
        listing = ctx.listing() if ctx.listing is not None else None
        collections = ctx.config.collections

        for unit in titled:
            try:
                parsed = parse_filters(
                    {params.type: list(unit.values)},
                    field="params", searching=True, base="any",
                )
            except ValueError as refusal:
                # Caught HERE, around this one call, and deliberately NOT in
                # ``_REFUSALS``: ``pydantic.ValidationError`` subclasses
                # ``ValueError``, so a module-level entry would swallow a broken
                # params model too. Reachable from a clean config -- a Plex tag
                # can be any string, and a whitespace-only one is an empty tag
                # value here -- and uncaught it would escape the engine's
                # unwrapped smart dispatch and cost the whole library its
                # reconcile.
                logger.warning(
                    "%s: %r was not built: %s", ctx.library, unit.title, refusal
                )
                actions.append("refused %r: %s" % (unit.title, refusal))
                continue
            try:
                url = build_search_url(
                    parsed, libtype=libtype, sort_by=params.sort_by,
                    # No limit, deliberately: a person's collection is all of
                    # their films in this library, not the first fifty of them.
                    limit=None, resolve_tag=resolver,
                )
                logger.debug("credits_family: %s -> %s", unit.title, url)
                actions += await reconcile_smart_collection(
                    ctx.session,
                    ctx.section,
                    ctx.library,
                    ctx.library_type,
                    unit.title,
                    url,
                    ctx.label,
                    # No summary: a family has no single summary to write, and
                    # the definition's own is refused above. Deliberately
                    # WITHOUT ``summary_asserted``, exactly as ``dynamic`` does
                    # it: a definition that cannot carry a summary cannot assert
                    # the absence of one, so a person's collection keeps
                    # whatever Plex or an operator put there.
                    summary=None,
                    dry_run=ctx.dry_run,
                    existing=listing,
                    adopt=collections.adopt,
                    adopt_from=collections.adopt_from,
                    adopt_removes_prior_label=collections.adopt_removes_prior_label,
                    protect_labels=collections.protect_labels,
                    http=ctx.http,
                    config=ctx.config,
                    settings=settings,
                    sort_prefix=ctx.sort_prefix,
                )
            except _REFUSALS as refusal:
                # One person's problem stays one person's: an unresolvable tag
                # or a filter Plex refuses must not stop the other twenty-four
                # collections being managed.
                logger.warning(
                    "%s: %r was not built: %s", ctx.library, unit.title, refusal
                )
                actions.append("refused %r: %s" % (unit.title, refusal))
        return actions
