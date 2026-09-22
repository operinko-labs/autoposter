"""``dynamic``: one smart collection per distinct value the library holds.

The generic engine ``collections/buckets.py`` is one hardcoded instance of, and
the third smart builder. Its siblings mark the two shapes it sits between:
``cs_bucket`` manages a FAMILY whose titles come from a static table, and
``smart_filter`` manages exactly ONE collection whose query the operator wrote.
This one manages a family whose titles and queries are both derived from what
the LIBRARY turns out to hold.

**One write path, one grammar.** Upstream's twelve tag/scan
dynamic types generate ``smart_filter`` collections created by a raw POST with
the ``build_filter`` query (meta.py:950, builder.py:1476-1478, plex.py:1592-1600)
-- the grammar 9b's oracle transcribes and 9c's reconciler already writes. So
every collection here goes through ``smart.reconcile_smart_collection`` with a
``build_search_url`` string: no plexapi ``filters=``, no second translation
layer, no second drift hash. Routing the generated block through
``parse_filters(..., searching=True)`` rather than assembling a query by hand is
part of the same rule -- it is what makes ``decade``'s bare-form-only emission
(``filters.SEARCH_OPERATORS_EXCLUDED``) hold for this emitter too, without a
second gate to keep in step.

**Four pieces, three of them pure.** ``dynamic_types`` says what to enumerate
and how to title it; ``LibraryTagResolver.choices`` does the one Plex read;
``dynamic_keys`` decides which keys become collections and what each asks for;
``dynamic_titles`` names them. This module is the seam between them and the
reconciler, and it holds exactly three decisions of its own: the fan-out cap,
the family label and the per-key minimum.

**The fan-out cap.** ``studio`` on the production movie library enumerates to
824 values, which the phase's own probe measured; ``year`` on a seventy-year
library is seventy collections. Upstream has no cap at all. So a family whose
enumeration exceeds ``max_collections`` (default 50) refuses with both numbers,
the type that fanned out and the two ways forward, and creates nothing -- rather
than creating eight hundred collections an operator then has to delete one at a
time.

**The family label.** Every collection this builder creates carries
``FAMILY_LABEL_PREFIX + definition.title`` beside the ownership label. That is
Kometa's own handle for the same job -- it labels each generated collection with
the map name (``append_label``, meta.py:1421) and its ``sync:`` sweep deletes
labelled collections no key regenerated (meta.py:1300, :1456-1461) -- and it is
what ``engine._sweep`` enumerates, through this service's own delete guards.
This module's half of that is ``generated_titles``: the pass's record of every
title the family derived, written before anything is created, so a Plex write
that failed cannot read as an operator narrowing their family. No record at all
means the family did not decide, and the sweep then considers none of its
collections.

**The per-key minimum.** Whether a key's collection is built at all, once its
match count against Plex is known, is this module's own call rather than one
delegated to the four pure modules above -- ``minimum_items``'s field comment
carries the rationale and the divergence from upstream.

**No ``titles()``, deliberately.** ``engine.definition_titles`` offers a smart
builder that hook, and ``cs_bucket`` uses it because its titles come from a
static table. A dynamic family's titles are the library's: offline enumeration
is impossible, and online enumeration would put a Plex call inside
``_titles_must_not_collide``, which runs on every config write
(``config/schema.py``). So this builder declares none, the engine falls through
to ``{definition.title}``, and two things follow -- the
placeholder's title is reserved although no collection is created under it, and
the family's real titles are invisible to the leftovers report and the sweep.
That is roadmap rows 135/162's gap widening per dynamic type, recorded here and
answered at run time by the reconciler's own ownership check: a collision with
somebody else's collection is refused by ``resolve_collision`` rather than
silently adopted.

**Every refusal RETURNS.** ``engine.py``'s smart dispatch does not wrap a smart
builder's ``apply``, on the grounds that anything escaping it is a Plex WRITE
failing. So a library type this type cannot serve, a dead filter lookup, an
empty enumeration, an over-cap fan-out, a duplicate title, a bucket whose values
the type's own search grammar refuses and a key whose query Plex cannot answer
are all caught here and returned as action strings. A failing label write still
reaches the engine's per-library rollback, unchanged.

**A family-level refusal freezes its existing members, deliberately** (roadmap
row 223, answered 2026-09-08: the coupling is KEPT). Every branch above returns
BEFORE the sweep's record is seeded, before the listing is fetched and before
the per-key ``reconcile_smart_collection`` call -- so a refused family's
collections keep whatever sort prefix they had and take no poster updates until
the operator acts. That is half a loss and half a protection, and the protective
half is why it stays: ``engine.py:1418-1421`` reads an ABSENT record as the
fail-closed state, so the same return that freezes the family also guarantees
the sweep will not delete any of it. Reconciling half a family under a record
the delete sweep also reads is how collections get deleted; splitting the two is
filed rather than built, and its named prerequisite is that the record be seeded
from the LIVE listing, never from ``titled``. The refusal is logged as well as
reported (``_refused``) so the freeze is at least audible.
"""
import datetime as dt
import logging
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoposter.collections.builders.base import (
    LibraryTypeMismatch,
    SmartContext,
    require_library_type,
)
from autoposter.collections.builders.plex_search import (
    LibraryTagResolver,
    PlexSearchUnavailable,
)
from autoposter.collections.dynamic_keys import DerivedKeys, derive_keys
from autoposter.collections.dynamic_titles import (
    ABSENT_KEY,
    OTHER_KEY,
    DuplicateFamilyTitle,
    family_titles,
    title_format_names_the_key,
)
from autoposter.collections.dynamic_types import DYNAMIC_TYPE_NAMES, DYNAMIC_TYPES
from autoposter.collections.filters import _as_current_year, parse_filters
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
    count_matches,
    reconcile_smart_collection,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FAMILY_LABEL_PREFIX", "DynamicBuilder", "DynamicParams", "YearWindow",
    "family_label", "generated_titles", "poster_for_unit",
]

# The label every collection in one family carries, beside the ownership label.
# Prefixed rather than the bare definition title (which is what Kometa uses,
# meta.py:1421): a family called "Genres" would otherwise claim a plain
# ``Genres`` label an operator may already use for something else, and this
# label is a delete handle -- 10a-2's sweep reads it.
FAMILY_LABEL_PREFIX = "autoposter-dynamic: "

# The one dynamic type whose `data:` this service reads. A constant rather
# than the literal in three places, because the refusal table below, the
# params validator and the enumeration filter all have to agree about it.
WINDOW_TYPE = "year"

# Kometa's keys this builder refuses, each with the reason. Held as data so the
# refusals cannot drift apart in wording, and so adding one is a row rather than
# a branch -- ``plex_search._REFUSED_KEYS`` is the shape.
_REFUSED_KEYS: dict[str, str] = {
    "test": (
        "`test: true` configures NOTHING on a collection upstream -- it is in "
        "`ignored_details` (modules/builder.py:137) and the only thing that "
        "reads it is Kometa's own `--run-tests` CLI flag, which skips every "
        "collection WITHOUT the marker (kometa.py:1233-1248). Accepting it "
        "here would be a knob that does nothing. Preview a definition with the "
        "collections preview instead, which reports what a pass would do "
        "without writing"
    ),
    "data": (
        "`data:` is read here for exactly ONE dynamic type, `year`, where "
        "Kometa's `number` type takes `starting`/`ending` and enumerates the "
        "years between them (meta.py:1112-1143) -- which is how "
        "`defaults/both/year.yml` says 'the last ten years'. For every other "
        "type upstream never parses it either (meta.py:23 makes it a valid "
        "key, and nothing reads it unless the type is one of the people, "
        "award, number, custom or list types), so accepting it on this type "
        "would be a block an operator writes and nothing reads"
    ),
    "sync": (
        "`sync: true` upstream is a DELETE sweep -- it labels each generated "
        "collection and deletes labelled collections no key regenerated "
        "(meta.py:1300, :1456-1461) -- not a sync mode. This service does that "
        "sweep already, for every builder rather than for this one, and gates "
        "it on `collections.delete_unconfigured` and `collections.max_deletes` "
        "instead of on a per-definition boolean -- so a config mistake cannot "
        "cascade into a wiped library one definition at a time"
    ),
    "other_template": (
        "`other_template:` names another Kometa TEMPLATE for the leftovers "
        "collection (meta.py:1311-1317), and this service has no template "
        "system -- the leftovers collection is built by the same type query "
        "over the keys no `include:` entry named. Write `other_name:` for its "
        "title; there is nothing else about it to point elsewhere"
    ),
    "template": (
        "Kometa's dynamic engine emits nothing but variable bindings and puts "
        "the whole query in a `template:` (meta.py:1287-1289). Here the query "
        "IS the type, so there is no template to name"
    ),
    "template_variables": (
        "the same: this engine has no templates, so there are no variables to "
        "bind. The per-key knobs are `key_name_override`, `title_override` and "
        "`title_format`"
    ),
}

# The one refusal above that is CONDITIONAL, and the only type it is lifted
# for. Held beside the table rather than as a branch inside the validator so
# the table stays the whole story: a key is refused unless the definition's
# type is the one named here. `year` is on this list because Kometa's own
# `number` type reads `data:` (meta.py:1112-1143) and `defaults/both/year.yml`
# is written with it; nothing else upstream reads it for a library type this
# service ships.
_REFUSED_UNLESS_TYPE: dict[str, str] = {"data": WINDOW_TYPE}

# Anything written as ``<<...>>``. Non-greedy on the inside so two tokens on one
# line are two matches rather than one span from the first to the last.
_TOKEN = re.compile(r"<<[^<>]*>>")

# What is left after every well-formed token is removed. A half-written
# ``<<key_name>`` matches ``_TOKEN`` not at all, so without this it passed every
# validator and shipped literally into a live collection's name -- the exact
# outcome the token check exists to prevent, reached by typing one bracket
# fewer.
_UNBALANCED = re.compile(r"<<|>>")

# What ``dynamic_titles.render_title`` actually substitutes, and therefore the
# whole set a ``title_format`` may name. Upstream resolves two more families
# that have nowhere to come from here -- a template's ``default:`` values
# (meta.py:1406-1410) and the library-level ``<<limit>>`` (:1272-1273) -- and an
# unresolved token is not a no-op: it ships literally into a live collection's
# name.
#
# ``<<{auto_type}>>`` (``<<genre>>``, ``<<year>>``, ...) is deliberately NOT
# here. It is resolvable in principle -- ``render_title`` takes an ``auto_type``
# -- but upstream substitutes the value LIST for it, ``str()``-ed, so it renders
# a Python repr (``Top ['Horror'] movies``) in a real collection name. Refusing
# is the better answer than widening the set to include a token whose only
# rendering is that one.
_TITLE_FORMAT_TOKENS = frozenset({
    "<<library_type>>", "<<library_typeU>>", "<<title>>", "<<key_name>>",
    "<<value>>", "<<key>>",
})

# ``_other_title`` substitutes the library type and NOTHING else (meta.py:
# 1306-1310) -- the leftovers bucket's key is the literal ``"other"``, which is
# not a name anybody wants in a title, so no key-name pass runs over it.
_OTHER_NAME_TOKENS = frozenset({"<<library_type>>", "<<library_typeU>>"})

# A ``title_override`` is the finished title, verbatim (meta.py:1382-1383): no
# substitution pass runs over it at all, so every token in one is unresolvable.
_TITLE_OVERRIDE_TOKENS: frozenset[str] = frozenset()

# The oldest bound worth believing. Not a Kometa number -- upstream range
# checks `starting` at `minimum=0` (meta.py:1126) -- but `year: 0` is a typo
# every time, and a family whose window starts before cinema builds nothing
# and says nothing about why.
_EARLIEST_YEAR = 1800

# Every window refusal, as a FIXED sentence. None of them interpolates what
# the operator wrote: an operator's value in a served string is what this
# project refuses, and the four cases are distinguishable without it.
WINDOW_BOUND_REFUSAL = (
    "each bound of `data:` is either a whole year (1800 or later, and no "
    "later than next year) or Kometa's own relative spelling `current_year` "
    "/ `current_year-N`, which is resolved against the run's own moment and "
    "subtracts (meta.py:1119-1134). Written any other way it is neither, so "
    "there is no window to build"
)
WINDOW_ORDER_REFUSAL = (
    "`data.ending` resolves to a year before `data.starting`, so the window "
    "is empty and this definition could never build anything. Kometa refuses "
    "the same pair the same way (meta.py:1136-1137)"
)
WINDOW_TOO_WIDE_REFUSAL = (
    "this `data:` window spans more years than `max_collections` allows, so "
    "the family would refuse on every pass once it met a library. Narrow the "
    "window, or raise `max_collections` on purpose"
)
WINDOW_HOLDS_NOTHING = (
    "this library reported no year inside this definition's `data:` window, "
    "so it builds nothing. The window narrows the years the library itself "
    "reports -- it never invents one -- so a window over years this library "
    "has nothing from is a family with no members. Widen the window, or aim "
    "the definition at a library that covers those years"
)


def _now() -> dt.datetime:
    """The pass's own moment, in ONE place.

    Every relative bound in a family resolves against a single captured
    moment, which is the shape ``filters.resolve_search_values`` established
    for a ``plex_search`` (``builders/plex_search.py``, ``builders/
    smart_filter.py``: one ``dt.datetime.now()`` per build, never one per
    value). A function rather than an inline call so the load-time validator
    and ``apply`` read the same clock and a test can advance it by a year --
    which is the only way to see the window slide without waiting for
    January.
    """
    return dt.datetime.now()


def _resolve_bound(written: str, now: dt.datetime) -> int:
    """One written bound as a year, against ``now``.

    ``filters._as_current_year`` is row 171's parser and this reuses it
    rather than growing a second one: two parsers for one grammar is two
    things to keep in step, and the divergences that parser already chose
    deliberately -- case-insensitive, no whitespace around the dash -- are
    ones an operator should meet in the same shape here.
    """
    sentinel = _as_current_year(written, "data")
    if sentinel is None:
        return int(written.strip())
    return now.year - sentinel.offset


class YearWindow(BaseModel):
    """Kometa's ``number`` window (``data: {starting, ending}``), for
    ``type: year`` only.

    The two bounds are held AS WRITTEN and resolved on demand, never at
    parse time: ``current_year`` means the run's year, and a service that
    resolved it when the config loaded would pin January's answer until the
    next config write. That is the same deferred-resolution property
    ``_CurrentYear`` and ``_Today`` already have on the ``filters:`` side.

    ``extra="forbid"`` because upstream's ``increment`` is deliberately not
    read here -- every year in the window gets a collection, and a family
    that skipped every other year is not a pack anybody asked for -- and a
    silently-ignored ``increment:`` would be the block an operator writes and
    nothing reads.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    starting: str
    ending: str

    @field_validator("starting", "ending", mode="before")
    @classmethod
    def _a_bound_is_a_year_or_the_sentinel(cls, value: object) -> str:
        # ``mode="before"``, deliberately: a ``bool``, ``None`` or a list is a
        # shape pydantic's own ``str`` coercion refuses on its own terms
        # ("Input should be a valid string"), which never reaches this
        # validator in "after" mode and tells an operator porting `year.yml`
        # nothing about `data:`'s own grammar. ``bool`` is excluded from the
        # numeric branch by name because it is a Python ``int`` subclass and
        # ``coerce_numbers_to_str`` would otherwise wave ``True``/``False``
        # through as "True"/"False".
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError(WINDOW_BOUND_REFUSAL)
        value = str(value)
        if _as_current_year(value, "data") is not None:
            return value
        try:
            year = int(value.strip())
        except ValueError:
            raise ValueError(WINDOW_BOUND_REFUSAL) from None
        if not _EARLIEST_YEAR <= year <= _now().year + 1:
            raise ValueError(WINDOW_BOUND_REFUSAL)
        return value

    def resolve(self, now: dt.datetime) -> tuple[int, int]:
        """Both bounds against ONE moment, inclusive at both ends."""
        return _resolve_bound(self.starting, now), _resolve_bound(self.ending, now)

# Every EXCEPTION CLASS of its own an operator's configuration can cause once it
# meets a real library. A tuple so the per-key path has one catch rather than
# six, and named exhaustively rather than as ``Exception`` so a genuine bug in
# this module still reaches the engine as a failure instead of being reported to
# the operator as something about their family. ``parse_filters`` raises a bare
# ``ValueError`` and is therefore NOT here -- it is caught around its own call
# instead, because ``pydantic.ValidationError`` subclasses ``ValueError`` and an
# entry here would swallow ``DynamicParams.model_validate`` too. See ``apply``.
REFUSALS = (
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
    """The pass's run-cache key for one family's generated titles.

    Keyed on the family LABEL rather than the definition title, because the
    label is what the sweep has in hand when it finds a member: it matched the
    collection on that label a line earlier.
    """
    return "dynamic:generated:%s" % label


def generated_titles(run_cache: dict, definition) -> set[str] | None:
    """What ``definition``'s family built this pass, or ``None``.

    ``None`` is the FAIL-CLOSED answer and it means "this family did not get as
    far as deciding": the definition was outside its schedule, or it refused at
    the family level -- a library type its type cannot serve, an empty
    enumeration, an all-excluded family, an over-cap fan-out, a duplicate title,
    a dead filter lookup. A sweep must not delete a family's collections on a
    pass that never enumerated the library: one transient ``listFilterChoices``
    failure would otherwise read as "the operator narrowed the family" and take
    every collection in it.

    A set (not ``None``) is the family's own answer, and it is the set of every
    title the family DERIVED -- written before a single collection is created,
    so a key whose Plex write refused is still a key this family builds.
    """
    return run_cache.get(_generated_key(family_label(definition)))


def poster_for_unit(row, unit) -> tuple[str | None, str | None]:
    """This unit's default-poster family and key, or ``(None, None)``.

    The key is the unit's OWN key, never its title: the two differ on every row
    whose ``key_from`` is ``"key"`` (``1980`` against "the 1980s", ``4k``
    against "4K"), and it is the key half that upstream named its files by.

    The leftovers bucket is excluded by name. ``other`` is a bucket THIS
    SERVICE invents for the keys no ``include:`` entry claimed; upstream never
    drew it, and a family directory that happens to hold an ``other.jpg`` for
    its own reasons (``aspect/`` does) would answer with artwork belonging to
    a different question entirely.
    """
    if row.poster_kind is None or unit.key == OTHER_KEY:
        return None, None
    return row.poster_kind, unit.key


class DynamicParams(BaseModel):
    """A dynamic family: which values to enumerate, and how to name them.

    ``type`` is the only required key. Everything else narrows the family
    (``include``/``exclude``/``addons``/``custom_keys``), names it
    (``title_format``, ``key_name_override``, ``title_override``,
    ``remove_prefix``/``remove_suffix``, ``other_name``) or bounds it
    (``sort_by``, ``limit``, ``max_collections``, ``minimum_items``).

    ``extra="forbid"`` so ``includes:`` for ``include:`` is an error rather than
    a silently-ignored key, and ``coerce_numbers_to_str`` because certification
    tables are written with integer YAML keys upstream and an operator porting
    one writes ``include: [12, 16]``.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    type: str
    # Kometa's `number` window, and the only key this service reads out of
    # `data:` (meta.py:1112-1143). Accepted for `type: year` alone -- the
    # refusal table above is what enforces that, before this field is ever
    # built -- and it NARROWS the enumeration rather than replacing it: the
    # keys are still the years the library reports, which is the deliberate
    # divergence from upstream's unconditional `while current <= ending`
    # (meta.py:1138-1143).
    data: YearWindow | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    # Typed as a mapping rather than left to ``dynamic_keys._dictliststr``:
    # that function raises a bare ``TypeError`` on anything else, which would
    # reach the engine as a crash in this module rather than as something about
    # the operator's config. The type is what makes that branch unreachable
    # from YAML.
    addons: dict[str, list[str]] = Field(default_factory=dict)
    custom_keys: bool = True
    key_name_override: dict[str, str] = Field(default_factory=dict)
    title_override: dict[str, str] = Field(default_factory=dict)
    remove_prefix: list[str] = Field(default_factory=list)
    remove_suffix: list[str] = Field(default_factory=list)
    title_format: str | None = None
    other_name: str | None = None
    sort_by: list[str] | None = None
    # ``0`` is the NO-LIMIT sentinel and the one value ``None`` cannot mean:
    # unset takes the type row's own default (50), so before this there was no
    # way to say "ask Plex for the whole match set". Kometa's shipped packs say
    # exactly that -- ``template: [smart_filter, shared]`` makes ``limit`` an
    # OPTIONAL variable and a pack that supplies none emits a search carrying no
    # ``limit=`` at all (`defaults/templates.yml:238-255`; only `decade.yml`
    # pins one, at 100). Zero rather than a second field or a string because it
    # is already what the emitter treats as no limit (`search_url.py:146-154`,
    # tracking Kometa's own `if limit` at builder.py:4289), so the sentinel is
    # read off the query builder instead of invented above it. Below zero still
    # refuses: that is a typo, not an intent.
    limit: int | None = Field(default=None, ge=0)
    # The refuse-over-surprise floor. 50 is chosen against the production
    # library: content ratings enumerate to ~7, decades to ~12, genres to ~25,
    # countries to 63, networks to 91, subtitle languages to 115 and studios to
    # 824 -- so the families that would create more collections than an operator
    # can hold in their head are exactly the ones that refuse until the cap is
    # raised on purpose.
    max_collections: int = Field(default=50, ge=1)
    # Roadmap decomposition step 4's other half, and a deliberate DIVERGENCE:
    # upstream has no per-key minimum for any of the thirteen library dynamic
    # types (the nearest thing is the people types' `data: minimum`, which is a
    # credit threshold on the ENUMERATION and not on the result). So the default
    # is None and not a number -- a shipped floor an operator has to discover
    # and switch off is the surprise this project refuses -- and switching it on
    # costs one Plex read per key per pass, which is why it is opt-in twice
    # over: by existing, and by being priced in its own docstring.
    #
    # It also changes what a ZERO-match key MEANS, which is worth saying out
    # loud rather than leaving to be discovered. Off, a key nothing matches
    # refuses at the reconciler (`SmartFilterMatchedNothing`) and stays in the
    # pass's record, so an existing collection under that title is protected.
    # On -- at ANY value, since zero is below every threshold -- the key leaves
    # the record and that collection becomes an ordinary sweep candidate,
    # through the usual guards. The feature working as designed, but a
    # protection posture that flips on one opt-in.
    minimum_items: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _the_refused_keys(cls, data: object) -> object:
        """Everything that has to beat ``extra="forbid"``.

        A ``mode="before"`` validator for ``plex_search``'s own reason: these
        are keys Kometa accepts, so an operator porting a config writes them,
        and pydantic's generic "Extra inputs are not permitted" would tell them
        nothing about WHY a key that works upstream does not work here. Matched
        exactly, never case-insensitively, so this validator's verdict and
        pydantic's field lookup agree on every input.
        """
        if not isinstance(data, dict):
            return data
        keys = {str(key) for key in data}
        for refused, why in _REFUSED_KEYS.items():
            if refused not in keys:
                continue
            # ``is not None`` and not a truthiness test: a definition with no
            # ``type`` at all must still be refused here, and ``data.get`` on
            # such a mapping answers ``None``, which would otherwise compare
            # equal to a missing exemption and wave the key through.
            exempt = _REFUSED_UNLESS_TYPE.get(refused)
            if exempt is not None and data.get("type") == exempt:
                continue
            raise ValueError("%r is not accepted here. %s" % (refused, why))
        return data

    @model_validator(mode="after")
    def _the_type_must_be_one_this_service_enumerates(self) -> "DynamicParams":
        if self.type not in DYNAMIC_TYPES:
            raise ValueError(
                "%r is not a dynamic type this service enumerates. Options: %s. "
                "Kometa has more, and each absence is a decision with a reason "
                "-- see `collections/dynamic_types.py`'s module docstring"
                % (self.type, ", ".join(DYNAMIC_TYPE_NAMES))
            )
        return self

    @model_validator(mode="after")
    def _a_window_must_be_one_a_family_could_build(self) -> "DynamicParams":
        """The three window refusals that need more than one bound to see.

        Checked at LOAD, against the load's own moment, rather than at build:
        both bounds of a relative window move together, so the verdict is the
        same in any year, and a mixed window (one literal, one relative) is
        judged the way it reads today -- which is when the operator is here to
        read the answer.
        """
        if self.data is None:
            return self
        now = _now()
        first, last = self.data.resolve(now)
        # The believable-range floor and ceiling apply here too, not only to
        # a literal bound: `_a_bound_is_a_year_or_the_sentinel` checks a
        # literal's own value, but a sentinel (`current_year-N`) is held AS
        # WRITTEN and only becomes a year once resolved against a moment --
        # so a window whose sentinel resolves outside 1800..next-year would
        # otherwise reach the family unrefused. Same fixed sentence as the
        # literal path; never the resolved value.
        if not _EARLIEST_YEAR <= first <= now.year + 1 or not _EARLIEST_YEAR <= last <= now.year + 1:
            raise ValueError(WINDOW_BOUND_REFUSAL)
        if last < first:
            raise ValueError(WINDOW_ORDER_REFUSAL)
        if last - first + 1 > self.max_collections:
            raise ValueError(WINDOW_TOO_WIDE_REFUSAL)
        return self

    @model_validator(mode="after")
    def _a_title_format_must_name_the_key(self) -> "DynamicParams":
        """meta.py:1234-1236 reverts such a format to the type's default and
        logs. Reverting builds the whole family under names the operator did not
        write -- and then skips all but the first as duplicates."""
        if self.title_format is not None and not title_format_names_the_key(
            self.title_format
        ):
            raise ValueError(
                "`title_format` has to carry `<<key_name>>` (or `<<title>>`, "
                "which means the same value): without one, every collection in "
                "the family would be given the same name. Kometa silently "
                "reverts to its own default here (modules/meta.py:1234-1236)"
            )
        return self

    @model_validator(mode="after")
    def _every_token_must_be_one_something_resolves(self) -> "DynamicParams":
        """A ``<<…>>`` this service cannot substitute is refused by name.

        Not a style rule: nothing downstream fails on an unknown token, so the
        literal ``<<limit>>`` would be POSTed as part of a live collection's
        title and stay there. Each of the three places a token can be written
        has its own resolved set, because each goes through a different amount
        of ``dynamic_titles`` -- the constants above say which and why.
        """
        written: list[tuple[str, str, frozenset[str]]] = []
        if self.title_format is not None:
            written.append(("title_format", self.title_format, _TITLE_FORMAT_TOKENS))
        if self.other_name is not None:
            written.append(("other_name", self.other_name, _OTHER_NAME_TOKENS))
        written += [
            ("title_override[%r]" % key, value, _TITLE_OVERRIDE_TOKENS)
            for key, value in self.title_override.items()
        ]
        for where, text, allowed in written:
            for token in _TOKEN.findall(text):
                if token in allowed:
                    continue
                raise ValueError(
                    "`%s` names %s, which nothing here resolves -- it would be "
                    "written into the collection's name exactly as it stands. "
                    "%s. (Kometa resolves more tokens than this because it "
                    "resolves them out of a template's variables, "
                    "modules/meta.py:1272-1273 and :1406-1410, and this "
                    "service has no template system.)"
                    % (
                        where, token,
                        "Nothing is substituted into a `title_override`: it is "
                        "the finished title, verbatim" if not allowed
                        else "This one takes " + ", ".join(sorted(allowed)),
                    )
                )
            if _UNBALANCED.search(_TOKEN.sub("", text)):
                raise ValueError(
                    "`%s` has unbalanced token delimiters: %r. A token is "
                    "written `<<name>>` with two brackets on each side, and a "
                    "half-written one matches nothing here -- it would be "
                    "written into the collection's name exactly as it stands, "
                    "which is what this check exists to prevent" % (where, text)
                )
        return self

    @model_validator(mode="after")
    def _a_title_override_must_name_something(self) -> "DynamicParams":
        """``family_titles`` honours an override by MEMBERSHIP rather than
        truthiness (meta.py:1382-1383, and its own comment says why), so an
        empty string is faithfully "this collection has no name". Upstream would
        create it; refused here, at the moment of the edit."""
        for key, value in self.title_override.items():
            if not value.strip():
                raise ValueError(
                    "`title_override` gives %r an empty title. An override is "
                    "used exactly as written -- there is no fallback to "
                    "`title_format` for a blank one -- so this would create a "
                    "collection with no name. Drop the entry to keep the "
                    "generated title, or `exclude` the key" % key
                )
        return self

    @model_validator(mode="after")
    def _key_name_overrides_must_be_unique(self) -> "DynamicParams":
        """meta.py:1251-1257 pops a key mid-iteration when two values match --
        a ``RuntimeError`` in CPython 3, not the graceful skip it reads as. And
        two keys renamed to one name would title one collection twice."""
        seen: dict[str, str] = {}
        for key, name in self.key_name_override.items():
            if name in seen:
                raise ValueError(
                    "`key_name_override` renames both %r and %r to %r, which "
                    "would give one collection two keys. Rename one of them, or "
                    "merge them with `addons` so they are one key to begin with"
                    % (seen[name], key, name)
                )
            seen[name] = key
        return self

    @model_validator(mode="after")
    def _sort_names_must_exist_somewhere(self) -> "DynamicParams":
        for name in self.sort_by or []:
            if name not in KNOWN_SORT_NAMES:
                raise ValueError(
                    "sort_by %r is not a Plex sort. Options: %s"
                    % (name, ", ".join(sorted(KNOWN_SORT_NAMES)))
                )
        return self


class DynamicBuilder:
    """A family of Plex-native smart collections, one per value in the library."""

    type_name = "dynamic"
    # The engine's marker for "this one applies itself" -- see SmartContext.
    smart = True
    params_model = DynamicParams

    # The same seven ``cs_bucket`` refuses and for the same reasons: this
    # definition names a FAMILY, and Plex evaluates each member's membership
    # live. ``sort_title``, ``collection_mode`` and the ``visible_*`` flags are
    # deliberately absent -- they are properties of the collection OBJECT rather
    # than of its membership, they apply to every member of the family, and the
    # create path applies them (roadmap row 104).
    refused_definition_fields = {
        "summary": (
            "this definition names a FAMILY of collections, one per value the "
            "library holds, so a single summary could not be the summary of any "
            "particular one of them. Kometa's dynamic engine writes no summary "
            "either -- it emits variable bindings and lets the template decide"
        ),
        "sort": (
            "Plex evaluates each collection's membership live, so there is no "
            "resolved order to set. The order the search returns is "
            "`params.sort_by`, and the family's own ordering is what "
            "`sort_title` is for"
        ),
        "limit": (
            "Plex evaluates each collection's membership from a filter, so "
            "there is no resolved list to cap. Cap the SEARCH instead, with "
            "`params.limit` -- which, with `params.sort_by`, is what 'the 50 "
            "highest-rated in each genre' means. To cap how many COLLECTIONS "
            "the family builds, use `params.max_collections`"
        ),
        "sync_mode": (
            "Plex owns these collections' membership; there is nothing for this "
            "service to sync or append to"
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
            "a `filters:` block narrows a membership this service resolved, and "
            "these are never resolved here -- the items are chosen inside Plex, "
            "by each key's own filter. Narrow the family with `include:`, "
            "`exclude:` or `params.limit` instead"
        ),
    }

    def family_label(self, definition) -> str:
        """The label this definition's collections carry.

        A method as well as a module function so the engine can ask the
        REGISTRY entry -- ``_sweep`` has a builder and a definition, and no
        reason to import this module by name.
        """
        return family_label(definition)

    def generated_titles(self, run_cache: dict, definition) -> set[str] | None:
        """What this definition's family built this pass -- see the module
        function of the same name for what ``None`` means.

        A method as well as a module function for ``family_label``'s reason:
        ``engine._sweep`` has a builder and a definition and no reason to import
        this module by name. Growing these two methods is the whole protocol a
        future family builder needs to join the sweep.
        """
        return generated_titles(run_cache, definition)

    async def apply(self, ctx: SmartContext) -> list[str]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'dynamic' builder builds the family its definition names, "
                "so it cannot run without one"
            )
        params = DynamicParams.model_validate(definition.params)
        row = DYNAMIC_TYPES[params.type]
        libtype = ctx.library_type.lower()
        collections = ctx.config.collections

        try:
            # Above the enumeration, deliberately: a show-only type against a
            # movie library must cost zero Plex round trips.
            require_library_type(
                "the 'dynamic' builder's %r type" % params.type,
                ctx.library_type, row.kinds,
            )
            resolver = LibraryTagResolver(ctx, ctx.section, libtype)
            enumerated = [
                (choice_key if row.key_from == "key" else choice_title, choice_title)
                for choice_key, choice_title in resolver.choices(row.attribute)
            ]
        except REFUSALS as refusal:
            return self._refused(ctx, definition.title, refusal)

        if not enumerated:
            return self._refused(ctx, definition.title, (
                "%r reports no %r values at all, so this definition would build "
                "no collections. A family with nothing to enumerate is not an "
                "empty family -- it is a library that cannot answer the "
                "question, which is a real state for a section Plex types as "
                "%s and an operator fills with something else"
                % (ctx.library, params.type, libtype)
            ))

        derived = derive_keys(
            enumerated,
            include=params.include,
            exclude=params.exclude,
            addons=params.addons,
            custom_keys=params.custom_keys,
        )

        if params.data is not None:
            # One captured moment for the whole family, per pass. Both bounds
            # resolve against it, so a pass that straddles midnight on New
            # Year's Eve builds one window rather than two.
            #
            # Applied to the DERIVED keys, after ``addons:`` has had its say --
            # not to ``enumerated`` before ``derive_keys`` runs. An addon can
            # introduce a key the library never enumerated at all
            # (``dynamic_keys.derive_keys``'s ``add_key not in present``
            # branch only requires that the addon's MEMBERS be present, not
            # the synthetic key itself), so a window that filtered only the
            # library's raw enumeration would let such a key through
            # regardless of its bounds.
            first, last = params.data.resolve(_now())
            derived = DerivedKeys(
                keys=tuple(
                    unit for unit in derived.keys
                    if unit.key.isdigit() and first <= int(unit.key) <= last
                ),
                other_keys=derived.other_keys,
            )
            # ``isdigit`` also drops Plex's ABSENT_KEY ("None") here -- a year
            # window has no bucket for "no year" -- the same verdict
            # ``family_titles`` reaches for it a few lines below for every
            # other dynamic family.
            if not derived.keys:
                return self._refused(ctx, definition.title, WINDOW_HOLDS_NOTHING)

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
                # nothing is left over, so an ``other_name`` would name a
                # collection that could never have a member.
                other_name=params.other_name if params.include else None,
            )
        except DuplicateFamilyTitle as refusal:
            return self._refused(ctx, definition.title, refusal)

        if not titled:
            return self._refused(ctx, definition.title, (
                "every %r value %r holds was excluded, so this definition builds "
                "nothing. Widen `include:`, or remove the definition"
                % (params.type, ctx.library)
            ))
        if len(titled) > params.max_collections:
            return self._refused(ctx, definition.title, (
                "this would create %d collections in %r -- %r reports %d "
                "value(s) there, which `include:`, `exclude:` and `addons:` "
                "narrow to that many buckets -- and `max_collections` is %d, so "
                "nothing was created. Narrow the family with `include:` or "
                "`exclude:`, or raise `max_collections` past %d if that is "
                "really what you want"
                % (len(titled), ctx.library, params.type, len(enumerated),
                   params.max_collections, len(titled))
            ))

        # Declared here rather than beside the emission loop because the two
        # reports below are about the FAMILY -- what the library reported and
        # what the operator's narrowing did with it -- and both are decided
        # before any key is built.
        actions: list[str] = []

        # ``OTHER_KEY`` is the leftovers bucket's literal key (meta.py:
        # 1306-1310). If the library also holds a real value spelled "other"
        # that the operator explicitly `include`d, it gets its own titled entry
        # with that same key -- one key would then mean two things: the
        # emitter's leftovers hint would be printed for the real value's
        # bucket, and an operator's `exclude: [other]` would be ambiguous
        # between them. Refused for that one bucket rather than guessed.
        # Upstream shares the collision and does not notice it.
        #
        # ``family_titles`` claims the leftovers bucket LAST
        # (dynamic_titles.py:331-335), so it is always the LAST entry here with
        # ``key == OTHER_KEY``. The collision only arises when an EARLIER entry
        # also carries that key -- a real value spelled "other" that was itself
        # `include`d into its own bucket. A real "other" value that was never
        # `include`d simply lands among the leftovers bucket's own ``values``
        # instead, which is one bucket, not two, and is not ambiguous.
        #
        # Unlike every other per-bucket refusal in this module, this drop
        # happens BEFORE the record seed below -- so an existing collection
        # under the real value's title becomes an ordinary sweep candidate,
        # not a protected one. That is narrowing, not write-failure: this
        # family genuinely does not build that title this pass.
        other_positions = [
            index for index, unit in enumerate(titled) if unit.key == OTHER_KEY
        ]
        if len(other_positions) > 1:
            drop = other_positions[0]
            real = titled[drop]
            titled = tuple(
                unit for index, unit in enumerate(titled) if index != drop
            )
            actions.append(
                "refused %r: %r holds a %r value spelled %r, which is also "
                "the leftovers bucket's own key, so one key would mean two "
                "things here -- `exclude:` or `key_name_override:` could not "
                "tell them apart either. Drop `other_name:` to build the "
                "real value's collection, or `exclude: [%s]` to build only "
                "the leftovers"
                % (real.title, ctx.library, params.type, OTHER_KEY, OTHER_KEY)
            )

        # ``family_titles`` drops an ``ABSENT_KEY`` key outright and is right
        # to: it is Plex's "these items have no value for this field", not a
        # value, and a collection called "Top None movies" is nobody's ask. But
        # dropping it silently leaves an operator whose library has forty
        # unrated films with a family that has no bucket for them and no reason
        # why. Reported, and honestly: at this layer a real tag named literally
        # "None" is the same string, so the report says so instead of claiming
        # to know which one this was.
        if any(key == ABSENT_KEY for key, _ in enumerated):
            actions.append(
                "%r reports a %r value of %r, which is how Plex spells 'these "
                "items have no value for this field'. No collection is built "
                "for it -- the query would be `%s=None`, which is a real search "
                "and a useless collection. If this library genuinely has a %s "
                "tag named %r, the two are the same string here and there is no "
                "way to tell them apart: rename the tag in Plex"
                % (ctx.library, params.type, ABSENT_KEY, row.search_key,
                   params.type, ABSENT_KEY)
            )

        # Two present sets, because the five narrowing sources do not all
        # match the same way. ``include``, ``key_name_override``,
        # ``title_override`` and addon MEMBERS are all matched against the KEY
        # ONLY (dynamic_keys.py:144/:156/:163, dynamic_titles.py:158/:289), so
        # ``by_key`` is what they're checked against -- it also carries the
        # synthetic ``addons`` keys, which are names the library never
        # reported and ``include:`` may correctly name. ``exclude`` alone is
        # matched against key OR display value (dynamic_keys.py:137) -- a
        # correct ``exclude: [1930s]`` reported as inert would be worse than
        # not reporting at all -- so it gets the wider ``by_key_or_value``.
        # Reusing the wider set for the key-only sources would hide a real
        # typo on any type keyed on ``choice.key`` (``decade``, ``resolution``):
        # ``include: [1980s]`` would read as effective when it is inert.
        by_key = {key for key, _ in enumerated} | set(params.addons)
        by_key_or_value = by_key | {value for _, value in enumerated}
        inert = self._inert(ctx, params, by_key, by_key_or_value)
        if inert is not None:
            actions.append(inert)

        # The sweep's input, written HERE -- after the family-level refusals,
        # before a single collection is created. Seeded with every title the
        # family derived, so a key whose write refuses below is still a key this
        # family builds and its collection survives the sweep: a Plex write that
        # failed is not an operator narrowing their family. The set is
        # deliberately MUTABLE -- but only ever NARROWED, with ``discard``, by a
        # per-key decision below: nothing may add a title back once removed, and
        # nothing may replace ``ctx.run_cache``'s entry with a different object,
        # because the sweep reads this exact one.
        # ``titled`` is non-empty here -- every refusal above that could leave it
        # empty or unreached already returned -- and that matters because the
        # engine reads absence and emptiness as opposites: no record protects the
        # whole family, an EMPTY record makes every member a candidate. Writing
        # ``set()`` here would silently delete the family.
        generated: set[str] = {unit.title for unit in titled}
        ctx.run_cache[_generated_key(family_label(definition))] = generated

        # The definition's own settings, plus the family label. ``model_copy``
        # rather than a re-validated construction, for ``engine._completed``'s
        # reason: both halves are already validated, and re-running the
        # definition validators here would hold this copy to rules that were
        # checked at config load.
        settings = definition.model_copy(
            update={"labels": [*definition.labels, family_label(definition)]}
        )
        # The pass's one listing, fetched here rather than at context
        # construction so a definition that refuses above costs nothing and a
        # pass with no dynamic definition never fetches it at all.
        listing = await ctx.listing() if ctx.listing is not None else None

        for unit in titled:
            # ``ABSENT_KEY`` is Plex's "these items have no value for this
            # field", not a value. ``family_titles`` drops such a KEY outright;
            # the leftovers bucket is the one place it can still reach a query,
            # because its values are the leftover keys themselves -- and
            # ``genre=None`` is a term this library would answer.
            values = [value for value in unit.values if value != ABSENT_KEY]
            if not values:
                actions.append(
                    "refused %r: it resolved to no values, and a smart filter "
                    "with no terms matches the entire library" % unit.title
                )
                continue
            try:
                parsed = parse_filters(
                    {row.search_key: values},
                    field="params", searching=True, base="any",
                )
            except ValueError as refusal:
                # Caught HERE, around this one call, and deliberately NOT added
                # to ``REFUSALS``: ``pydantic.ValidationError`` subclasses
                # ``ValueError``, so a module-level entry would also swallow
                # ``DynamicParams.model_validate`` above and report a broken
                # params model as something about one of the operator's buckets.
                #
                # Reachable, and from a config that validates. The leftovers
                # bucket's values are the leftover KEYS themselves: a surviving
                # key no ``include:`` entry named goes to ``other_keys``
                # (dynamic_keys.py:154-159) and ``family_titles`` passes those
                # through verbatim as that bucket's values
                # (dynamic_titles.py:330-334). A synthetic ``addons`` key is
                # such a key -- and it is a bucket NAME, not a value the library
                # holds, so ``year: 'Eighties'`` is not a whole number and
                # ``genre: ''`` is an empty tag. Uncaught, that escapes the
                # engine's unwrapped smart dispatch (engine.py:341-352) and
                # costs the whole library its reconcile, which is the one thing
                # this module's docstring promises an operator's configuration
                # cannot do.
                logger.warning(
                    "%s: %r was not built: %s", ctx.library, unit.title, refusal
                )
                # ``parse_filters`` prefixes every refusal with the dotted
                # config path it was handed, which is this module's own
                # argument (``field="params"``) and not anything the operator
                # wrote. The prefix is deterministic, so removing exactly it is
                # safe and falling back to the whole message is honest.
                reason = str(refusal).removeprefix(
                    "params.%s: " % row.search_key
                )
                actions.append(
                    "refused %r in the %r family: it asks this library for %s "
                    "as %r values, and %s.%s"
                    % (
                        unit.title, definition.title,
                        ", ".join(repr(one) for one in values), params.type,
                        reason,
                        " The leftovers bucket's values are the keys no "
                        "`include:` entry named, so an `addons` key the library "
                        "does not itself hold arrives here as if it were one of "
                        "its values. Name that key in `include:` to build it as "
                        "its own collection, or `exclude:` it."
                        if unit.key == OTHER_KEY else "",
                    )
                )
                continue
            try:
                url = build_search_url(
                    parsed,
                    libtype=libtype,
                    sort_by=params.sort_by or row.sort_by,
                    # ``or None`` is where the no-limit sentinel is spent: a
                    # pinned ``limit: 0`` arrives here as 0 and leaves as
                    # ``None``, which is how ``build_search_url`` spells "emit
                    # no limit at all". Only the sentinel can be zero -- no
                    # type row pins 0 and the params model refuses anything
                    # below it -- so nothing else is caught by the falsiness.
                    limit=(row.limit if params.limit is None else params.limit) or None,
                    resolve_tag=resolver,
                )
                logger.debug("dynamic: %s -> %s", unit.title, url)
                if params.minimum_items is not None:
                    matched = count_matches(ctx.section, url)
                    if matched < params.minimum_items:
                        # Out of the pass's record, which is the WHOLE of
                        # delete-below-minimum: this key is not one the family
                        # builds this pass, so an existing collection under this
                        # title is an ordinary sweep candidate and goes through
                        # the same guards every other one does -- the ownership
                        # label and the managed row, a protecting label,
                        # `delete_unconfigured` (off by default, and off means
                        # reported) and `max_deletes`, past which the whole
                        # library's sweep refuses. Nothing is deleted here.
                        #
                        # The discard is AFTER a count that succeeded, and that
                        # ordering is the fail-closed half: a count Plex would
                        # not answer raises out of here into `except REFUSALS`
                        # below with the title still in the record, so one dead
                        # read protects the collection instead of narrowing the
                        # family that never measured it.
                        generated.discard(unit.title)
                        actions.append(
                            "did not build %r: it matches %d item(s) and "
                            "`minimum_items` is %d. If a collection already "
                            "exists under that title, the delete sweep decides "
                            "its fate through `delete_unconfigured` and "
                            "`max_deletes` like any other"
                            % (unit.title, matched, params.minimum_items)
                        )
                        continue
                actions += await reconcile_smart_collection(
                    ctx.session,
                    ctx.section,
                    ctx.library,
                    ctx.library_type,
                    unit.title,
                    url,
                    ctx.label,
                    # No summary: upstream's dynamic engine writes none either
                    # (its generated config is a template call and a label), and
                    # a family has no single summary to write. Deliberately
                    # WITHOUT ``summary_asserted``: this definition refuses both
                    # ``summary:`` and ``tmdb_summary:`` at config load, so it
                    # could never have had one to drop, and the only summary a
                    # generated collection can carry is Plex's own or an
                    # operator's -- never this service's to clear.
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
                    # One call per generated key, and the reconciler wraps by
                    # that key's OWN title -- so each generated collection gets
                    # ``!<NNN>_<its own title>`` without this engine having to
                    # know the scheme.
                    sort_prefix=ctx.sort_prefix,
                    # The family's own default artwork
                    # (``collections/default_images.py``). One call per
                    # generated key, keyed by that key -- which is the same
                    # string upstream named the file by, because both come from
                    # Kometa's own grouping vocabulary.
                    poster_kind=poster_for_unit(row, unit)[0],
                    poster_key=poster_for_unit(row, unit)[1],
                )
            except REFUSALS as refusal:
                # Contained to ONE key: an unresolvable value or a filter that
                # matches nothing is that key's problem, and eleven working
                # collections must not stop being managed because a twelfth
                # cannot be built.
                logger.warning(
                    "%s: %r was not built: %s", ctx.library, unit.title, refusal
                )
                actions.append("refused %r: %s" % (unit.title, refusal))
        return actions

    def _inert(
        self, ctx: SmartContext, params: DynamicParams,
        by_key: set[str], by_key_or_value: set[str],
    ) -> str | None:
        """Narrowing and override entries naming a key the library never
        reported, as one line or none.

        Silently inert is upstream's behaviour and is not a correctness hole --
        an all-excluded family does refuse. It is a TYPO an operator cannot
        see: ``include: [Horor]`` builds a family with one fewer collection and
        says nothing at all. One line rather than one per entry, because a
        config ported from another library can name a dozen at once.

        Two present sets, not one: ``exclude`` alone is matched against key OR
        display value (dynamic_keys.py:137); ``include``, ``key_name_override``,
        ``title_override`` and addon MEMBERS are all matched against the KEY
        ONLY (dynamic_keys.py:144/:156/:163, dynamic_titles.py:158/:289). On a
        type keyed on ``choice.key`` (``decade``, ``resolution``, e.g. key
        ``1980`` and title ``1980s``), reusing ``exclude``'s wider set for the
        other four would report a genuinely inert ``include: [1980s]`` as if
        it named the key.

        ``addons`` is checked by its MEMBERS rather than by its keys, which is
        the one place this differs from the other four. An addon key the library
        never reported is upstream's synthetic bucket (dynamic_keys.py:141-150):
        it builds a real collection under that name, so calling it inert would
        be false, and a typo in it is visible in the family's own titles. An
        addon MEMBER the library never reported is dropped -- twice, at :144
        and at :163 -- and is the invisible one.
        """
        named: list[str] = []
        for where, keys, present in (
            ("include", params.include, by_key),
            ("exclude", params.exclude, by_key_or_value),
            ("addons", [one for many in params.addons.values() for one in many],
             by_key),
            ("key_name_override", list(params.key_name_override), by_key),
            ("title_override", list(params.title_override), by_key),
        ):
            named += [
                "%s: %r" % (where, one)
                for one in keys if str(one) not in present
            ]
        # Every individual absent member above is
        # already named, but a bucket whose members are ALL absent builds no
        # collection at all (dynamic_keys.py:144-146) -- a fact the per-member
        # lines do not say, so it is named here too.
        named += [
            "addons: %r (every member is absent, so it builds nothing)" % key
            for key, members in params.addons.items()
            if members and all(str(one) not in by_key for one in members)
        ]
        if not named:
            return None
        return (
            "%s names no value %r holds and does nothing: %s. Every entry is "
            "matched against the key exactly -- for a type keyed on `choice.key`"
            " that is the bare form (`1980`, not `1980s`) -- so a near miss is "
            "silently inert rather than an error"
            % ("One entry" if len(named) == 1 else "%d entries" % len(named),
               ctx.library, ", ".join(named))
        )

    def _refused(self, ctx: SmartContext, title: str, why: object) -> list[str]:
        """One definition-level refusal, logged and returned.

        Logged as well as reported because the action string reaches a run
        report an operator may not read, and the logs page is where they look
        when a family stops updating.
        """
        logger.warning("%s: %r was not built: %s", ctx.library, title, why)
        return ["refused %r: %s" % (title, why)]
