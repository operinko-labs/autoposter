"""``facts_family``: one LIST collection per value this service's facts hold.

The third family shape, and the sentence that distinguishes it from its two
siblings: ``cs_bucket`` manages a family of SMART collections from a static
table, ``dynamic`` manages a family of SMART collections from a Plex
enumeration, and this manages a family of LIST collections from a DATABASE
enumeration. The difference is forced rather than chosen -- Plex has no
``origin_country`` field, so there is no filter to store on a collection and
the membership has to be resolved and applied as a list.

**Four pieces, three of them phase 10a's.** ``facts_enumeration`` does the one
database read; ``dynamic_keys.derive_keys`` decides which keys become
collections and what each asks for; ``dynamic_titles.family_titles`` names
them; and this module is the seam between them and the engine's ordinary list
path. Nothing in the first three is modified, and their tests are the gate.

**How it reaches the engine.** Through ``expand`` (``engine._expand``), the
same hook ``imdb_award_years`` uses for the Oscars years -- the definition an
operator writes is a placeholder that never becomes a collection, and the units
it returns are ordinary list definitions the engine resolves and applies.

**How it joins the delete sweep.** Through ``family_label`` and
``generated_titles``, which is exactly the protocol ``builders/dynamic.py``
names ("Growing these two methods is the whole protocol a future family builder
needs to join the sweep"). ``engine._family_state`` reads them off the registry
entry with ``getattr`` and does not care that this one is a list builder. The
prefix differs from the dynamic families' so the two sweeps cannot enumerate
each other's members.

**Coverage, said out loud rather than discovered.** This family enumerates what
the facts pipeline has VISITED. On a library the pipeline has half worked
through, the family is half the size it will be -- correct, incomplete, and
converging as the drift sweep works the rest (``scheduler.drift_days``,
``scheduler.drift_batch_size``: 500 items a week by default). Every pack built
on this says so in its own description, and the builder reports the coverage
numbers into the run cache, which the pass reads back into its actions
(``engine.py``, above the unit loop).

**Every refusal RETURNS**, as a note in the pass's run cache the engine
surfaces: an empty enumeration, an all-excluded family, an over-cap fan-out, a
duplicate title, a franchise TMDb cannot name and a title another definition
in the config already manages are all reported rather than raised.
``expand`` returning ``[]`` is a family that built nothing, and
``generated_titles`` answering ``None`` is what stops the sweep treating that as
narrowing.
"""
from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.collections import iso_names
from autoposter.collections.dynamic_keys import derive_keys
from autoposter.collections.dynamic_titles import (
    DuplicateFamilyTitle,
    family_titles,
    title_format_names_the_key,
)
from autoposter.collections.facts_enumeration import coverage, enumerate_values
from autoposter.collections.facts_family import (
    FACTS_FAMILY_TYPE_NAMES,
    FACTS_FAMILY_TYPES,
)
from autoposter.config.schema import CollectionDefinition

__all__ = [
    "FAMILY_LABEL_PREFIX", "FactsFamilyBuilder", "FactsFamilyParams",
    "family_label", "generated_titles", "notes",
]

# The label every collection in one family carries, beside the ownership label.
# Distinct from ``builders/dynamic.FAMILY_LABEL_PREFIX`` so the two family
# sweeps cannot enumerate each other's members, and prefixed rather than the
# bare definition title for that module's reason: a family called "Countries"
# would otherwise claim a plain ``Countries`` label an operator may already use,
# and this label is a DELETE handle.
FAMILY_LABEL_PREFIX = "autoposter-facts: "

_NOTES_KEY = "facts_family:notes"


def family_label(definition) -> str:
    """The label every collection in ``definition``'s family carries."""
    return "%s%s" % (FAMILY_LABEL_PREFIX, definition.title)


# Written out as a constant because it is now READ as well as written: a family
# scans the pass's records for the titles the families BEFORE it claimed, which
# is the only place a second enumerated family's member titles exist.
_GENERATED_PREFIX = "facts_family:generated:"


def _generated_key(label: str) -> str:
    return _GENERATED_PREFIX + label


def notes(run_cache: dict) -> list[str]:
    """Everything the families in this pass reported, for the engine to surface.

    A list on the run cache rather than a return value, because ``expand`` has
    to answer with definitions and has nowhere else to put an operator-facing
    note: the engine appends one ``DefinitionResult`` per unit RETURNED, so a
    family that refuses produces no row of its own. ``run_library`` reads the
    slice each definition added and appends it to the pass's actions.
    """
    return run_cache.setdefault(_NOTES_KEY, [])


def generated_titles(run_cache: dict, definition) -> set[str] | None:
    """What ``definition``'s family built this pass, or ``None``.

    ``None`` is the FAIL-CLOSED answer, verbatim from ``builders/dynamic.py``'s
    of the same name and for the same reason: it means this family did not get
    as far as deciding -- outside its schedule, or refused at the family level
    (an empty enumeration, an all-excluded family, an over-cap fan-out, a
    duplicate title). A sweep must not delete a family's collections on a pass
    that never enumerated: one transient failure would otherwise read as "the
    operator narrowed the family" and take every collection in it.

    A set is the family's own answer, and it is every title the family DERIVED
    -- written before a single collection is created, so a key whose apply
    failed is still a key this family builds.
    """
    return run_cache.get(_generated_key(family_label(definition)))


class FactsFamilyParams(BaseModel):
    """A facts family: which stored field to enumerate, and how to name it.

    Deliberately the same vocabulary ``DynamicParams`` uses -- ``include``,
    ``exclude``, ``addons``, ``custom_keys``, ``key_name_override``,
    ``title_override``, ``remove_prefix``/``remove_suffix``, ``title_format``,
    ``other_name``, ``max_collections`` -- because the keys are consumed by the
    SAME two pure modules and an operator who has learned one family's knobs has
    learned this one's.

    Three of ``DynamicParams``' knobs are deliberately absent. ``sort_by`` and
    ``limit`` cap a SEARCH Plex evaluates and there is none here -- a list
    definition's own ``sort:`` and ``limit:`` fields do that job, and the engine
    already inherits both into every expanded unit
    (``engine._INHERITED_BY_EXPANSION``). ``minimum_items`` is absent because
    the thing it would count does not exist at this layer: a unit's membership
    is resolved by the unit's OWN builder one layer down, so a per-key minimum
    here would mean resolving every key's membership twice, and for
    ``tmdb_collection`` that second pass is a TMDb read per franchise taken
    before deciding whether to build it. Upstream DOES apply a floor here --
    ``franchise.yml``'s own templates block carries ``minimum_items: 2``, which
    this service therefore diverges from, visibly and on purpose (roadmap row
    199 carries the divergence and what closing it would cost). A shipped knob
    whose cost is a second full pass is not worth a line -- and a knob an
    operator sets that
    nothing reads is exactly what ``DynamicParams``' refusal of ``data:``
    exists to prevent.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    type: str
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    addons: dict[str, list[str]] = Field(default_factory=dict)
    custom_keys: bool = True
    key_name_override: dict[str, str] = Field(default_factory=dict)
    title_override: dict[str, str] = Field(default_factory=dict)
    remove_prefix: list[str] = Field(default_factory=list)
    remove_suffix: list[str] = Field(default_factory=list)
    title_format: str | None = None
    other_name: str | None = None
    # The same refuse-over-surprise floor ``DynamicParams`` pins, and the same
    # number: a family that would create more collections than an operator can
    # hold in their head refuses with both numbers and creates nothing.
    max_collections: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def _the_type_must_be_one_this_service_enumerates(self) -> "FactsFamilyParams":
        if self.type not in FACTS_FAMILY_TYPES:
            raise ValueError(
                "%r is not a facts family this service enumerates. Options: %s. "
                "A family built on a PLEX tag is `builder: dynamic` instead -- "
                "see `collections/facts_family.py`'s module docstring for why "
                "the two tables are separate"
                % (self.type, ", ".join(FACTS_FAMILY_TYPE_NAMES))
            )
        return self

    @model_validator(mode="after")
    def _a_title_format_must_name_the_key(self) -> "FactsFamilyParams":
        if self.title_format is not None and not title_format_names_the_key(
            self.title_format
        ):
            raise ValueError(
                "`title_format` has to carry `<<key_name>>` (or `<<title>>`, "
                "which means the same value): without one, every collection in "
                "the family would be given the same name"
            )
        return self


class FactsFamilyBuilder:
    """A family of list collections, one per value the stored facts hold."""

    type_name = "facts_family"
    params_model = FactsFamilyParams

    def family_label(self, definition) -> str:
        """The label this definition's collections carry -- a method as well as
        a module function so ``engine._family_state`` can ask the REGISTRY
        entry rather than importing this module by name."""
        return family_label(definition)

    def generated_titles(self, run_cache: dict, definition) -> set[str] | None:
        """What this definition's family built this pass; see the module
        function of the same name for what ``None`` means."""
        return generated_titles(run_cache, definition)

    def notes(self, run_cache: dict) -> list[str]:
        """Everything the families in this pass reported -- a method as well as
        a module function for ``family_label``'s reason: the engine reads it off
        the REGISTRY entry rather than importing this module by name."""
        return notes(run_cache)

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        """Never reached, and it raises rather than returning nothing.

        Unlike ``imdb_award_years``, whose expanded units name IT as their
        builder, every unit this family returns names a DIFFERENT one
        (``facts_value``, ``tmdb_collection``) -- so the engine's
        ``_run_one`` never dispatches back here and there is no membership for
        this builder to produce. It exists because a registry entry is either a
        ``Builder`` or a ``SmartBuilder`` and nothing may be in the registry as
        neither (``tests/test_builder_base.py``), and it raises because an
        empty membership is read one layer down as "make no changes": a caller
        who reached this by mistake must hear about it.
        """
        raise ValueError(
            "the 'facts_family' builder produces no membership of its own -- it "
            "expands into one definition per enumerated value, each built by "
            "its own builder. Call `expand`, or write the member builder "
            "(`facts_value`, `tmdb_collection`) directly for a single value"
        )

    async def expand(self, ctx: BuilderContext) -> list[CollectionDefinition]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'facts_family' builder expands the family its definition "
                "names, so it cannot run without one"
            )
        params = FactsFamilyParams.model_validate(ctx.config)
        row = FACTS_FAMILY_TYPES[params.type]
        report = notes(ctx.run_cache)

        # Above the enumeration deliberately: a movie-only family on a show
        # library must cost zero database work.
        require_library_type(
            "the 'facts_family' builder's %r type" % params.type,
            ctx.library_type, row.field.kinds,
        )
        if ctx.session is None:
            raise ValueError(
                "the 'facts_family' builder reads this service's own database "
                "and was given no session"
            )

        # A savepoint around the reads, not the bare session, for the reason
        # ``facts_value.build`` takes one: ``engine._expand``'s caller contains
        # an expansion failure and carries on with the next definition, on the
        # invariant that a dead source does not stop the pass. A failed query
        # on the shared session would poison the transaction instead, turning
        # one bad definition into every later statement in the pass raising.
        async with ctx.session.begin_nested():
            counted = await enumerate_values(
                ctx.session, row.field,
                library=ctx.library, library_type=ctx.library_type,
            )
            attempted, total = await coverage(
                ctx.session, library=ctx.library, library_type=ctx.library_type,
            )
        if not counted:
            report.append(
                "%r built nothing: no %s values are stored for %r yet. The "
                "facts pipeline has visited %d of %d item(s) there; this "
                "family fills in as the ratings-drift sweep works through the "
                "rest (scheduler.drift_days, scheduler.drift_batch_size)"
                % (definition.title, params.type, ctx.library, attempted, total)
            )
            return []

        # The franchise family is the one that needs a name before it can title
        # anything: the enumeration is ids. One `/collection/{id}` read each,
        # through the provider cache, and the SAME cache entry the membership
        # builder reads one layer down -- so the family pays once per franchise
        # per TTL, not twice.
        enumerated: list[tuple[str, str]] = []
        if row.key_from == "id":
            client = getattr(ctx.sources, "tmdb", None)
            if client is None:
                report.append(
                    "%r built nothing: a %r family names each collection from "
                    "TMDb and no TMDb read access token is configured"
                    % (definition.title, params.type)
                )
                return []
            for value, _count in counted:
                name = await client.collection_name(int(value))
                if not name:
                    report.append(
                        "%r: TMDb could not name collection %s, so no "
                        "collection was built for it. A collection titled from "
                        "an id is nobody's ask; `title_override: {%s: …}` "
                        "names it by hand"
                        % (definition.title, value, value)
                    )
                    continue
                enumerated.append((value, name))
        elif row.names == "keys":
            # Row 196's normalisation decision (facts C2): our codes map UP
            # into TMDb's display names, so upstream's name-keyed grouping
            # tables (region.yml/continent.yml -- 647 member strings, zero
            # codes) apply verbatim. The DATABASE enumeration stays ISO --
            # row 156's law -- and the fold back down happens where the
            # membership query is built, below. A code the vendored table
            # cannot name keys and titles as itself: never invented, and a
            # miss is visible in the collection list rather than hidden.
            for value, _count in counted:
                name = iso_names.country_name(value) or value
                enumerated.append((name, name))
        elif row.names == "titles":
            # Row 190: only the TITLE renders through the table; the KEY
            # stays the code, so include/exclude/addons and both override
            # tables keep speaking ISO -- and a code with no entry titles as
            # the code, upstream's own fallback branch. ``language_title``
            # rather than ``language_name`` because the key staying the code is
            # exactly what stops two codes sharing a name from collapsing the
            # way `Congo` does above: it appends the code where the vendored
            # table is ambiguous, so `nr`/`nd` build two collections instead of
            # refusing the whole family (`iso_names`' docstring carries it).
            enumerated = [
                (value, iso_names.language_title(value) or value)
                for value, _count in counted
            ]
        else:
            enumerated = [(value, value) for value, _count in counted]

        if not enumerated:
            report.append(
                "%r built nothing: none of the %d enumerated %s value(s) could "
                "be named" % (definition.title, len(counted), params.type)
            )
            return []

        derived = derive_keys(
            enumerated,
            include=params.include,
            exclude=params.exclude,
            addons=params.addons,
            custom_keys=params.custom_keys,
        )
        try:
            titled = family_titles(
                derived,
                library_type=ctx.library_type,
                title_format=params.title_format or row.title_format,
                key_name_override=params.key_name_override,
                title_override=params.title_override,
                remove_prefix=params.remove_prefix,
                remove_suffix=params.remove_suffix,
                # Upstream gates the leftovers collection on ``include``
                # (meta.py:1301-1311) and so does this: with no include list
                # nothing is left over.
                other_name=params.other_name if params.include else None,
            )
        except DuplicateFamilyTitle as refusal:
            report.append("%r built nothing: %s" % (definition.title, refusal))
            return []

        if not titled:
            report.append(
                "%r built nothing: every %s value %r holds was excluded. Widen "
                "`include:`, or remove the definition"
                % (definition.title, params.type, ctx.library)
            )
            return []
        if len(titled) > params.max_collections:
            report.append(
                "%r built nothing: this would create %d collections in %r -- "
                "%r reports %d value(s) there, which `include:`, `exclude:` and "
                "`addons:` narrow to that many buckets -- and `max_collections` "
                "is %d. Narrow the family, or raise `max_collections` past %d "
                "if that is really what you want"
                % (definition.title, len(titled), ctx.library, params.type,
                   len(enumerated), params.max_collections, len(titled))
            )
            return []

        # Curated wins. Two independent presets may name one real-world
        # collection -- ``content_universes``' hand-written "Fast & Furious"
        # and this family's TMDb enumeration of the same franchise -- with two
        # different membership rules, and before this filter whichever
        # definition ran last in ``config.collections.presets`` order
        # overwrote the other's Plex object every pass (and mis-banded it on
        # the way, since ``groups.group_for`` resolves a unit's title through
        # the OTHER preset's index entry). ``group_for``'s precedence is not
        # the defect and is not touched: the defect is that this family was
        # allowed to claim a title somebody else already manages.
        #
        # Two sources, because one is blind to the other. ``managed_titles``
        # is every CURATED definition's own title, which is the franchise
        # case. The pass's own generated records are the second: a family
        # contributes only its PLACEHOLDER title to ``definition_titles``
        # (``engine.py:1348-1355``), so "Regions" and "Continents" both
        # enumerating one country name is invisible until both have run.
        # Whichever ran first keeps it; ordering is preset order, which is
        # deterministic and is a strict improvement on the silent every-pass
        # fight it replaces.
        own_key = _generated_key(family_label(definition))
        contested = set(ctx.managed_titles)
        for key, claimed in ctx.run_cache.items():
            if key.startswith(_GENERATED_PREFIX) and key != own_key:
                contested |= claimed

        kept = []
        for unit in titled:
            if unit.title in contested:
                # Reported, not raised, and not a warning: with a curated pack
                # and this family both switched on, this is the expected
                # steady state rather than anything going wrong.
                report.append(
                    "%r: %r is already managed by another definition in this "
                    "config, so this family left it alone -- that collection "
                    "keeps its own membership. Expected when a curated pack "
                    "covers something the enumeration also finds; switch the "
                    "other definition off if you want this family to build it "
                    "instead" % (definition.title, unit.title)
                )
                continue
            kept.append(unit)
        titled = kept

        if not titled:
            # Every title went to somebody else. Returning here rather than
            # falling through is what leaves ``generated`` UNWRITTEN, so
            # ``generated_titles`` answers None -- the fail-closed value -- and
            # the delete sweep does not read this as "the operator narrowed the
            # family" and take collections another definition now owns.
            report.append(
                "%r built nothing: every %s value it enumerated is already "
                "managed by another definition in this config"
                % (definition.title, params.type)
            )
            return []

        # The sweep's input, written HERE -- after every family-level refusal,
        # before a single unit is returned. Seeded with every title the family
        # derived, so a unit whose apply fails is still a title this family
        # builds and its collection survives the sweep. Never ``set()``: the
        # engine reads absence and emptiness as opposites, and an empty record
        # would silently delete the family.
        generated = {unit.title for unit in titled}
        ctx.run_cache[_generated_key(family_label(definition))] = generated

        label = family_label(definition)
        units: list[CollectionDefinition] = []
        for unit in titled:
            if row.member_builder == "tmdb_collection":
                # One franchise per collection: the shipped builder takes a
                # single id, and an addon merge over franchises is upstream's
                # own way of saying "Prometheus belongs with Alien", which is
                # two ids and one collection. Not expressible through a builder
                # that takes one id, so a merged bucket takes its FIRST value
                # and the rest are reported -- honest, and visible.
                if len(unit.values) > 1:
                    report.append(
                        "%r: %r merges %d franchises and the `tmdb_collection` "
                        "builder takes one id, so only %s was built. Write the "
                        "extra franchises as their own definitions if you want "
                        "them: %s"
                        % (definition.title, unit.title, len(unit.values),
                           unit.values[0], ", ".join(unit.values[1:]))
                    )
                unit_params = {"id": int(unit.values[0])}
            else:
                values = list(unit.values)
                if row.names == "keys":
                    # The names fold back DOWN to the codes the database
                    # stores -- `items_with_values` queries `item_facts`,
                    # whose enumeration is ISO by law (row 156). A name that
                    # is several codes' expands to all of them; a passthrough
                    # key (a code the table could not name) folds to itself.
                    values = [
                        code
                        for name in values
                        for code in (iso_names.country_codes(name) or (name,))
                    ]
                unit_params = {"field": row.field.name, "values": values}
            units.append(CollectionDefinition(
                title=unit.title,
                builder=row.member_builder,
                params=unit_params,
                # Both halves, and it has to be both: ``engine._completed``
                # fills ``labels`` from the placeholder only when the expander
                # set none, so setting the family label alone would silently
                # drop the operator's own.
                labels=[*definition.labels, label],
            ))

        report.append(
            "%r enumerated %d %s value(s) in %r and built %d collection(s); the "
            "facts pipeline has visited %d of %d item(s) there"
            % (definition.title, len(enumerated), row.name, ctx.library,
               len(units), attempted, total)
        )
        return units
