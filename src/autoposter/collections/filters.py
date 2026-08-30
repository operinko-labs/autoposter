"""``filters``: the tier-1 filter attribute table and the typed predicate model.

Phase 9a's foundation. A ``filters:`` block on a collection definition is
evaluated client-side against the items a builder resolved, and this module is
the whole of what that means: which attributes exist, what each one's values
are, which operators each type accepts, and what an operator's YAML turns into.
Nothing here touches Plex -- ``evaluate`` reads a *view*, a mapping-like
accessor whose real implementation (Task 2) sits over resolved plexapi items.
Keeping the model Plex-free is what lets the operator semantics be pinned as
data, which is the point: phase 9b translates this same vocabulary into a Plex
*search*, so a disagreement between the two would be a filter that means one
thing in a normal collection and another in a smart one.

**The table is the deliverable.** ``FILTER_ATTRIBUTES`` below is one row per
tier-1 attribute, carrying Kometa's own name for it, the value type, the item
kinds it applies to, where the data comes from, and -- the column that does the
work -- a note on every non-obvious cell. Everything else is generated from or
checked against it: the operator set of a row is its *type's* operator set
(``FilterAttribute.operators``), the parser's vocabulary is ``BY_NAME``, and
``PLEXAPI_EQUIVALENT`` states, per operator, the ``plexapi.base.OPERATORS`` key
9b will translate into -- or ``None``, explicitly, where plexapi has no
equivalent.

**Where the table came from.** Kometa publishes no machine-readable filter
schema, so the rows are *transcribed* from its documented filter semantics.
That transcription is fallible in a way a wrong TMDb parameter is not: a filter
with the wrong meaning still loads, still runs, and produces a full, plausible,
wrong collection. Two things are done about it. Every judgement call carries its
reasoning on the row, and the ones this transcription was **not** confident
about are marked in the note. A fresh call is ``UNVERIFIED-TRANSCRIPTION``. A
call this module's own fix round checked against named Kometa evidence reads
``SETTLED-BY-REVIEW`` (the code changed to match) or ``SETTLED-IN-FAVOR`` (the
original transcription was already right); a note with none of these is a claim
this module has stood behind since Task 1.

``SETTLED-BY-ORACLE`` outranks all of them. Task 4 ran a Kometa oracle: 120
listing-shaped items and two filter configs, evaluated by this module and by
**Kometa 2.4.8's own filter code**, fetched and transcribed standalone
(``modules/util.py``'s ``is_date_filter``/``is_number_filter``/
``is_string_filter``, ``modules/plex.py``'s ``check_filter``/``split``,
``modules/builder.py``'s ``check_filters``), member list against member list.
Its verdicts are the ones marked that way below, and they OVERRULE an earlier
``SETTLED-BY-REVIEW`` where the two disagree -- one such marker cited a
comparison that is not in Kometa's source at all, which is the failure mode a
recollection has and a fetched file does not. The procedure, the transcription
and every adjudication are in ``.superpowers/sdd/task-4-report.md``; the
comparison itself is ``tests/test_collection_filter_oracle.py``, which pins
Kometa's member lists as data.

**The missing-value rule splits by value-type family (SETTLED-BY-ORACLE).** For
``tag``/``str`` attributes: an item with no value for the attribute is EXCLUDED
by a positive filter and INCLUDED by a negative one (``.not``, ``.isnot``). So
``genre: Horror`` drops an item with no genres at all, and ``genre.not: Horror``
keeps it -- this is Kometa's rule for tags, where its implementation intersects
the item's tags with the filter's and the empty intersection falls out
correctly for both modifiers. For ``int``/``float``/``date``/``duration``
attributes the missing item is EXCLUDED under EVERY operator, including
``.not`` -- so ``audience_rating.not: 8`` on an unrated item still drops it,
same as ``audience_rating: 8`` would. This was originally shipped uniform
(a single rule, no type exception) and marked ``UNVERIFIED-TRANSCRIPTION``
because a per-type exception is a rule nobody remembers; a fix round then
settled it on a recollection, and the ORACLE confirmed the recollection against
the source. ``is_number_filter`` (util.py:623-632) opens the disjunction it
returns with a bare ``value is None``, and ``is_date_filter``
(util.py:598-600) opens with ``if value is None: return True`` -- both return
"filter this item out" *before* looking at the modifier, so the short-circuit
ignores the operator entirely for these four types. Only the tag/string paths
have no such short-circuit: ``is_string_filter`` (util.py:639-656) is driven by
a ``values`` list that is empty when the item has no value, and its final
expression then reads False (keep) for ``.not``/``.isnot`` and True (drop) for
the positive modifiers -- which is the tag half of the rule, falling out of
Kometa's own code rather than being asserted about it. This is the rule the
brief named as the oracle's first target, and it holds. Both halves are applied
in exactly one place, ``_matches`` below, so that every operator gets the rule
for its type and no operator can quietly opt out.

**What tier 1 deliberately does not have**, so that each is a refusal naming the
field rather than a silent gap: Kometa's tag ``.count_gt``/``.count_gte``/
``.count_lt``/``.count_lte`` modifiers, its ``.regex`` on *date* attributes,
its relative-date spellings beyond the bare ``today``, its special ``year``
words (``current_year`` and its offsets), and -- SETTLED-BY-ORACLE, see
``OPERATORS_BY_TYPE`` -- the four range modifiers on a date, which Kometa
accepts and silently rewrites to the strict ``.after``/``.before``. Each is a
tier-2 row, not a bug.

**The set arithmetic (phase 9b), because "the rest of row 96" is not one
number.** Kometa has TWO vocabularies and they are not nested, so a single
"residue" figure hides which half is being talked about. Counted against
Kometa v2.4.8, by enumerating the tables themselves rather than the docs:

- the FILTER vocabulary is ``builder.filters_by_type`` (builder.py:278-350):
  **70** distinct attribute names across all seven library types, **65** of
  them reachable on a movie or show library;
- the SEARCH vocabulary is ``plex.searches`` (plex.py:594-601): 338 distinct
  ``name.modifier`` entries, of which 183 are non-music, spelling **55**
  distinct non-music attribute names;
- the overlap is **26** names. So **44** filter names have no Plex search
  field at all (``aspect``, ``height``, ``width``, ``versions``, ``summary``,
  ``filepath``, ``imdb_keyword``, ``tmdb_*``, ``tvdb_*``, ...) and **29**
  search names have no filter (``unplayed``, ``progress``, ``hdr``,
  ``decade``, ``folder_location``, the whole ``episode_*`` family, ...).

This table covers **25** of the 55 search names and **22** of the 70 filter
names. Both halves of the residue are real work, and they are different work:
the 48 unfiltered names are roadmap row 96's remainder (9a left 55 of them;
``plays``, ``last_played``, 10a's ``country`` and phase B's four people rows
came in here as ``unprobed``, which is a source tier and not an accessor, so
the row-96 arithmetic moves by seven and no further), while the 30 unsearched
names are 9b's own tail, filed per family for T6. The two must not be reported
as one number, which is what row 96's original "~45" did.
"""
import datetime as dt
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "BY_NAME",
    "DEFAULT_OPERATOR",
    "FILTERABLE_ATTRIBUTES",
    "FILTER_ATTRIBUTES",
    "ITEM_KINDS",
    "OPERATORS_BY_TYPE",
    "PLEXAPI_EQUIVALENT",
    "RELATIVE_UNITS",
    "SEARCHABLE_ATTRIBUTES",
    "SEARCH_MODIFIERS",
    "SEARCH_ONLY_OPERATORS",
    "SEARCH_OPERATORS_BY_TYPE",
    "SEARCH_OPERATORS_EXCLUDED",
    "SOURCE_TIERS",
    "VALUE_TYPES",
    "FilterAttribute",
    "FilterGroup",
    "FilterPredicate",
    "ItemView",
    "RelativeWindow",
    "batched_attributes",
    "evaluate",
    "parse_filters",
    "predicates",
]

# The four categorical columns, as closed sets. A row outside them would parse,
# load, and match nothing.
#
# ``bool`` joined in 9b and is SEARCH-ONLY in practice: Kometa's boolean
# attributes (``unplayed``, ``progress``, ``hdr``, ...) are answered by the
# Plex server as ``field=1`` / ``field!=1`` (builder.py:4238-4241) and have no
# client-side counterpart in Kometa's filter vocabulary at all.
VALUE_TYPES = ("tag", "str", "int", "float", "date", "duration", "bool")
ITEM_KINDS = ("movie", "show")

# Where a row's data comes from, for the CLIENT-SIDE filter. ``probe`` was Task
# 2's question, not a shrug: Plex's listing endpoint carries some child elements
# and not others depending on the server and the request, so a row marked
# ``probe`` becomes ``listing`` (ships scan-free) or ``tier2-deferred`` (drops
# out of tier 1 rather than shipping a silent request-per-item) once the
# read-only probe answers. Task 2's probe (2026-08-25) answered all seven of
# tier 1's, so no row carries ``probe`` today. 9b added two tiers rather than
# reusing an existing one, because both would have been a lie:
#
# - ``unprobed``: the attribute IS in Kometa's filter vocabulary, but 9a never
#   probed whether the section listing carries it, so there is no verdict to
#   cite. Refusing it in ``filters:`` with the tier2-deferred copy would claim
#   a probe finding that does not exist. ``plays`` and ``last_played`` are the
#   two: Kometa filters on both (builder.py:280-293) and searches on both.
# - ``search-only``: the attribute is not in Kometa's filter vocabulary at all,
#   so there is nothing to probe. ``unplayed`` and ``progress``.
#
# Phase B added the third:
#
# - ``tier2-batched`` (phase B): the listing strands it but the batched
#   ``/library/metadata/{k1,k2,...}`` read carries it fully (9a probe F,
#   re-verified for the show library by the phase-B probe). Readable through
#   the engine's enrichment pass ONLY -- a definition whose filters name such
#   a row triggers one batched fetch for its resolved set, and an item the
#   batch did not answer for REFUSES the definition rather than evaluating
#   with a silently-missing value.
SOURCE_TIERS = (
    "listing", "probe", "tier2-batched", "tier2-deferred", "unprobed", "search-only",
)

# The operator vocabulary, per value type. These are internal names; the YAML
# spelling of each is ``.<name>`` except for the type's default, which is
# written as a bare key and has no modifier spelling at all -- see
# ``DEFAULT_OPERATOR``. Refusing ``genre.eq`` is deliberate: Kometa has no such
# modifier, and accepting a second spelling for the default is how a config
# ends up with two vocabularies.
OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "tag": ("eq", "not", "regex"),
    "str": ("contains", "not", "is", "isnot", "begins", "ends", "regex"),
    "int": ("eq", "not", "gt", "gte", "lt", "lte"),
    "float": ("eq", "not", "gt", "gte", "lt", "lte"),
    "duration": ("eq", "not", "gt", "gte", "lt", "lte"),
    # ``.before``/``.after`` are Kometa's spellings and are STRICT, and they
    # are the ONLY absolute date operators. ``.gt``/``.gte``/``.lt``/``.lte``
    # are all deliberately absent, and the reason is a correction
    # (SETTLED-BY-ORACLE): this module shipped ``.gte``/``.lte`` as inclusive-
    # boundary forms on the stated grounds that "Kometa has no spelling for
    # them". It has. ``Plex.split`` (plex.py:2735-2747) rewrites ``.gt`` AND
    # ``.gte`` to ``.after``, and ``.lt`` AND ``.lte`` to ``.before``, for
    # every date attribute -- so Kometa accepts all four and every one of them
    # means the STRICT comparison. Keeping our inclusive reading would have
    # made ``release.gte: 2000-01-01`` a different membership under the same
    # name in the two systems, which is the same-name-different-filter class
    # the plan's item_facts adjudication already ruled out. Refusing at load
    # says so; silently disagreeing would not. A tier-2 row is filed for a
    # real inclusive-boundary date operator under a name Kometa does not use.
    "date": ("eq", "not", "before", "after"),
    # Search-only in practice -- every ``bool`` row is ``filterable=False`` --
    # but the type has to have an entry or ``FilterAttribute.operators`` raises
    # a KeyError for a row nothing was ever going to evaluate.
    "bool": ("eq",),
}

# What a bare ``genre: Horror`` means. Note that ``str`` defaults to *contains*
# and ``tag`` to exact match -- that difference is Kometa's and it is the whole
# reason the two types are separate rows in ``VALUE_TYPES``.
DEFAULT_OPERATOR: dict[str, str] = {
    "tag": "eq",
    "str": "contains",
    "int": "eq",
    "float": "eq",
    "duration": "eq",
    "date": "eq",
    "bool": "eq",
}

# --- the SEARCH half of the vocabulary (phase 9b) ----------------------------
#
# Distinct from ``OPERATORS_BY_TYPE`` above and deliberately so: an attribute
# can be legal in one block and refused in the other, and one table with two
# operator sets is the only shape in which the two cannot drift apart. Every
# entry is transcribed from Kometa v2.4.8's ``searches`` comprehension
# (modules/plex.py:594-601), which is also the gate ``Builder._filter`` checks
# a written key against (builder.py:4194) -- a name.modifier absent from it is
# refused by Kometa outright, not merely undocumented. The differences from the
# client-side set are each argued below.
SEARCH_OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    # ``.regex`` is deliberately absent (see SEARCH_ONLY_OPERATORS' sibling
    # note below and ``_split_key``): Kometa's search-regex is not a regex sent
    # to Plex, it is a client-side expansion over the library's tag vocabulary
    # (builder.py:4301-4323), so shipping the spelling here would make one
    # config key mean two mechanisms.
    "tag": ("eq", "not"),
    "str": ("contains", "not", "is", "isnot", "begins", "ends"),
    # The bare form and ``.not`` are here for ``year`` alone, which reaches
    # them by being a ``year_attribute`` and therefore taking ``tag_modifiers``
    # as well as ``number_modifiers`` (plex.py:597, :599). The table's other
    # ``int`` row, ``plays``, is a ``number_attribute`` only, and
    # SEARCH_OPERATORS_EXCLUDED subtracts the two from it.
    "int": ("eq", "not", "gt", "gte", "lt", "lte"),
    # NOT ``eq``/``not``, and this is a TRANSCRIPTION CORRECTION made against a
    # live fetch of v2.4.8 rather than from the plan's text. Kometa's
    # ``float_attributes`` take ``float_modifiers`` and nothing else
    # (plex.py:549-550, :600), which is the four ranges plus ``.rated``: there
    # is no ``critic_rating:`` and no ``critic_rating.not:`` in ``searches`` at
    # all, so Kometa answers either with "attribute is not valid"
    # (builder.py:4194-4195). Offering them here would have shipped two keys
    # Plex is never asked.
    #
    # ``.rated`` is search-only: Plex answers "has any rating at all" as
    # ``field!=-1`` (builder.py:4236-4237), which no client-side comparison
    # spells.
    "float": ("gt", "gte", "lt", "lte", "rated"),
    # The four ranges only, for the same reason as ``float`` -- ``duration`` IS
    # a ``float_attribute`` (plex.py:549), with ``.rated`` subtracted from it
    # by name (plex.py:600). So a bare ``duration:`` is not a Kometa search
    # either; ``_split_key`` refuses it saying so. Where the two blocks DO
    # agree is the unit: Kometa multiplies a search duration by 60000
    # (builder.py:4234) exactly as the client-side view divides by it, so
    # ``duration.gt: 90`` is ninety minutes in both.
    "duration": ("gt", "gte", "lt", "lte"),
    "date": ("eq", "not", "before", "after"),
    "bool": ("eq",),
}

# Per-row subtractions from the type's search operator set. ``resolution`` is
# transcribed from ``no_not_mods`` (modules/plex.py:593) -- Plex has no negated
# resolution filter. ``plays`` is the ``int`` row that is not a year: see the
# ``int`` note above (plex.py:547-548, :599).
SEARCH_OPERATORS_EXCLUDED: dict[str, tuple[str, ...]] = {
    "resolution": ("not",),
    "plays": ("eq", "not"),
    # ``decade`` is in the same ``no_not_mods`` list as ``resolution``
    # (plex.py:593) and loses MORE than ``resolution`` does, because it is a
    # ``year_attribute`` and therefore reached the number modifiers too: that
    # comprehension is guarded by the same list (plex.py:599), so all four
    # ranges go with the ``.not``. The bare form is what is left.
    #
    # One nuance the sentence above glosses: upstream's ``.regex`` survives
    # ``no_not_mods`` (plex.py:597), so this list is not the whole difference
    # between us and Kometa for ``decade``. It is absent here because this
    # service refuses ``.regex`` in a SEARCH globally, for every row, and not
    # because ``decade`` loses it -- a subtraction that is already made
    # elsewhere does not need a row here.
    "decade": ("not", "gt", "gte", "lt", "lte"),
}

# Operators that exist ONLY in a search, so that writing one in a ``filters:``
# block is answered by name rather than by the generic "does not apply" line.
SEARCH_ONLY_OPERATORS = ("rated",)

# The modifier translation, keyed ``(value_type, operator)``.
#
# Kometa's own ``modifier_translation`` (modules/plex.py:195) is keyed on the
# modifier string alone, and it is NOT a bijection: four wire strings are each
# reached from two different modifiers, and every one of those collisions is
# between two DIFFERENT value types --
#
#     %3E    .gte (int/float/duration)  and  .ends   (str)
#     %3C    .lte (int/float/duration)  and  .begins (str)
#     %3E%3E .gt  (int/float/duration)  and  .after  (date)
#     %3C%3C .lt  (int/float/duration)  and  .before (date)
#
# -- so the pair key is not decoration, it is the only key under which the
# table is a function. ``test_the_modifier_table_is_not_invertible`` pins that
# structurally, so nobody "simplifies" this into a one-level dict.
#
# The two date entries below are the odd ones: a bare or ``.not`` date in a
# search is a relative window, and Kometa takes its modifier from
# ``last_mod`` (builder.py:4224) rather than from ``modifier_translation`` --
# ``%3E%3E`` for "in the last N", ``%3C%3C`` for "not in the last N". They are
# written here so the renderer has one lookup and not two, which is also why
# they are NOT part of the four collisions above: they do not come from
# ``modifier_translation`` at all.
#
# ``("float", "rated")`` and ``("bool", "eq")`` are the empty string because
# for those two the NEGATION rides on the argument, not on the modifier: a
# ``.rated`` term is ``field!=-1`` or ``field=-1``, and a boolean term is
# ``field=1`` or ``field!=1`` (builder.py:4236-4241). The renderer supplies the
# ``!``; this table must not, or it would be applied twice.
SEARCH_MODIFIERS: dict[tuple[str, str], str] = {
    ("tag", "eq"): "",
    ("tag", "not"): "!",
    ("str", "contains"): "",
    ("str", "not"): "!",
    ("str", "is"): "%3D",
    ("str", "isnot"): "!%3D",
    ("str", "begins"): "%3C",
    ("str", "ends"): "%3E",
    ("int", "eq"): "",
    ("int", "not"): "!",
    ("int", "gt"): "%3E%3E",
    ("int", "gte"): "%3E",
    ("int", "lt"): "%3C%3C",
    ("int", "lte"): "%3C",
    ("float", "gt"): "%3E%3E",
    ("float", "gte"): "%3E",
    ("float", "lt"): "%3C%3C",
    ("float", "lte"): "%3C",
    ("float", "rated"): "",
    ("duration", "gt"): "%3E%3E",
    ("duration", "gte"): "%3E",
    ("duration", "lt"): "%3C%3C",
    ("duration", "lte"): "%3C",
    ("date", "eq"): "%3E%3E",
    ("date", "not"): "%3C%3C",
    ("date", "before"): "%3C%3C",
    ("date", "after"): "%3E%3E",
    ("bool", "eq"): "",
}

# Kometa's ``date_sub_mods`` (modules/plex.py:307), which is both the legal
# unit set for a relative window and the name each unit reads as. ``o`` is
# MONTHS and ``m`` is MINUTES -- the least guessable pair in the vocabulary,
# and the reason ``_as_window``'s refusal spells the whole table out.
RELATIVE_UNITS: dict[str, str] = {
    "s": "Seconds", "m": "Minutes", "h": "Hours", "d": "Days",
    "w": "Weeks", "o": "Months", "y": "Years",
}

# The 9b translation argument, written down. Each of our operators names the
# ``plexapi.base.OPERATORS`` key it means, so 9b's search translation is a
# lookup rather than a reinvention. Negative operators name their POSITIVE
# counterpart's key -- ``.not`` on a tag is "no tag ``iexact``-matches any
# value", and the negation is ours, not plexapi's -- so a 9b translator that
# negated the key too would double-negate. This is a UNIFORM rule with no
# exceptions: ``test_every_negative_operators_plexapi_mapping_equals_its_positive_counterparts``
# in the test module pins it structurally. (SETTLED-BY-REVIEW: the three
# ``.not`` rows below for ``int``/``float``/``duration`` originally read
# ``"ne"`` -- plexapi's own negation key, which happens to exist, but is not
# the *positive counterpart's* key the stated convention promises. Fixed to
# ``"exact"``, matching each type's ``eq`` row, the same as every other
# negative operator in this table already did.) The two ``None`` entries are
# the deliberate gap: "in the last N days" is a relative window and every entry
# in plexapi's table is an absolute comparison.
PLEXAPI_EQUIVALENT: dict[tuple[str, str], str | None] = {
    ("tag", "eq"): "iexact",
    ("tag", "not"): "iexact",
    ("tag", "regex"): "iregex",
    ("str", "contains"): "icontains",
    ("str", "not"): "icontains",
    ("str", "is"): "iexact",
    ("str", "isnot"): "iexact",
    ("str", "begins"): "istartswith",
    ("str", "ends"): "iendswith",
    ("str", "regex"): "iregex",
    ("int", "eq"): "exact",
    ("int", "not"): "exact",
    ("int", "gt"): "gt",
    ("int", "gte"): "gte",
    ("int", "lt"): "lt",
    ("int", "lte"): "lte",
    ("float", "eq"): "exact",
    ("float", "not"): "exact",
    ("float", "gt"): "gt",
    ("float", "gte"): "gte",
    ("float", "lt"): "lt",
    ("float", "lte"): "lte",
    ("duration", "eq"): "exact",
    ("duration", "not"): "exact",
    ("duration", "gt"): "gt",
    ("duration", "gte"): "gte",
    ("duration", "lt"): "lt",
    ("duration", "lte"): "lte",
    # A boolean has no client-side operator set (it is search-only), so this
    # entry exists to keep the table total over OPERATORS_BY_TYPE rather than
    # because anything reads it. ``exact`` is the honest key: plexapi would
    # compare the value for equality.
    ("bool", "eq"): "exact",
    ("date", "eq"): None,
    ("date", "not"): None,
    ("date", "before"): "lt",
    ("date", "after"): "gt",
}

# The negative operators, and the positive one each negates. Every negative is
# evaluated by running its positive counterpart and inverting -- which is what
# makes ``.not`` mean "matches NONE of the given values" rather than "does not
# match the first one", and what makes the missing-value rule fall out of one
# branch instead of one per operator.
_NEGATES = {"not": None, "isnot": "is"}

# The value types whose missing-value rule ignores the operator: SETTLED-BY-
# ORACLE against Kometa's number/date filter, which excludes a ``None`` value
# unconditionally. ``tag``/``str`` are deliberately absent -- their rule still
# depends on the operator (positive excludes, negative includes). See the
# module docstring.
_MISSING_ALWAYS_EXCLUDES = ("int", "float", "date", "duration")


@dataclass(frozen=True)
class FilterAttribute:
    """One row of the table -- both vocabularies.

    ``name`` is Kometa's own name for the attribute and is what an operator
    writes in YAML; where it differs from Plex's wire name (``release`` for
    ``originallyAvailableAt``, ``critic_rating`` for ``rating``) the row's note
    says so, because that difference is the reason the row exists rather than a
    passthrough. ``note`` is required by the table's own test: a row with
    nothing to say about itself is a row nobody checked.

    **The client-side half.** ``kinds`` is which library the attribute means
    anything for as a ``filters:`` predicate; ``source`` is one of
    ``SOURCE_TIERS``; ``filterable`` is whether the attribute is in Kometa's
    FILTER vocabulary at all (modules/builder.py:278-350). The two are
    independent: ``plays`` is filterable and unprobed, ``unplayed`` is neither.

    **The search half.** ``search_field`` is the Plex query field for a MOVIE
    library, after ``search_translation`` (modules/plex.py:60-138);
    ``show_search_field`` is the same after ``show_translation``
    (modules/plex.py:168-193) re-scopes it for a SHOW library -- which for
    three media attributes means the EPISODE libtype, not the show's.
    ``search_kinds`` is which library types Plex will answer the search for,
    transcribed from ``movie_only_searches`` (:430-445) and
    ``show_only_searches`` (:446-506), and it is a SEPARATE column from
    ``kinds`` because they genuinely differ -- ``resolution`` is movie-only
    client-side and both-kinds server-side.
    """

    name: str
    type: str
    kinds: tuple[str, ...]
    source: str
    note: str
    search_field: str | None
    show_search_field: str | None
    search_kinds: tuple[str, ...]
    filterable: bool

    @property
    def operators(self) -> tuple[str, ...]:
        """Derived, not stored. The operators are a property of the value type,
        so storing them per row would be a duplicate of ``OPERATORS_BY_TYPE``
        and the duplicate is the thing that rots."""
        return OPERATORS_BY_TYPE[self.type]

    @property
    def default_operator(self) -> str:
        return DEFAULT_OPERATOR[self.type]

    @property
    def searchable(self) -> bool:
        """Derived from ``search_field``, not stored beside it -- a row with a
        field and ``searchable=False`` would be a contradiction the table could
        hold."""
        return self.search_field is not None

    @property
    def search_operators(self) -> tuple[str, ...]:
        """The type's search operators, minus this row's own subtractions."""
        excluded = SEARCH_OPERATORS_EXCLUDED.get(self.name, ())
        return tuple(
            op for op in SEARCH_OPERATORS_BY_TYPE[self.type] if op not in excluded
        )

    def field_for(self, libtype: str) -> str:
        """The Plex query field for one library type.

        Raises rather than falling back: a caller asking for a field on a
        libtype the row does not serve has already skipped the
        ``search_kinds`` check, and answering with the movie field would build
        a query Plex silently answers with the wrong set.
        """
        if self.search_field is None:
            raise ValueError(f"{self.name!r} has no Plex search field")
        if libtype not in self.search_kinds:
            raise ValueError(
                f"{self.name!r} is not searchable on a {libtype} library"
            )
        if libtype == "show" and self.show_search_field is not None:
            return self.show_search_field
        return self.search_field


_BOTH = ("movie", "show")

# --- THE TABLE ---------------------------------------------------------------
#
# Twenty-five rows: 9a's fifteen in the order the roadmap names them
# (roadmap.md:538-551), then 9b's four, 10a's two and phase B's four appended
# rather than interleaved so the first fifteen still read against the roadmap
# line they came from. Column totals are asserted in
# tests/test_collection_filters.py as the transcription's checksum:
# 13 tag / 1 str / 3 int / 2 float / 3 date / 1 duration / 2 bool;
# 9 listing / 5 tier2-batched / 1 tier2-deferred / 7 unprobed / 3 search-only;
# 14 both-kinds / 10 movie-only / 1 show-only for ``kinds``, and
# 17 / 7 / 1 for ``search_kinds``, which is a different split and that is the
# point of the second column.
#
# Phase B's four are the PEOPLE rows, and they move the ``tag`` and
# ``unprobed`` totals by four together -- ``actor`` on both kinds, and
# ``director``/``writer``/``producer`` movie-only in BOTH kind columns, which
# is why the two splits moved by different amounts.
#
# The source split is phase B's arithmetic, not 9a's: Task 2's probe left
# 9 listing / 6 tier2-deferred, and phase B moved five of that six onto
# ``tier2-batched`` when the batched ``/library/metadata/{k1,k2,...}`` read was
# measured returning the families the listing had truncated or stripped. Only
# ``network`` stayed, and it stayed for a reason no read can change -- see its
# row.
#
# THE PROBE, in one paragraph, because six of these rows were a refusal and
# a reader deserves the reason without leaving the file. Read-only, against the
# production server (ShadowPlex, Plex 1.43.4.10903-e5521bd8c, 1955 movies /
# 284 shows), 2026-08-25; the full log is in
# ``.superpowers/sdd/task-2-report.md``. The finding that decided most of it:
# Plex's section listing does not merely omit some child elements, it TRUNCATES
# the ones it does send. Genre comes back capped at two per item -- never three,
# anywhere in either section -- while the item's own metadata endpoint returns
# three and four. So the listing is not a smaller-but-correct answer for tags;
# it is a wrong one, and a filter built on it would produce full, plausible,
# wrong collections rather than visibly empty ones. Twenty-two listing
# parameters were tried against the tag counts; none changed anything.
FILTER_ATTRIBUTES: tuple[FilterAttribute, ...] = (
    FilterAttribute(
        "genre", "tag", _BOTH, "tier2-batched",
        "Plex's `<Genre>` child element, which plexapi exposes as the `genres` "
        "cached property (video.py:433). Exact tag match, case-insensitive: "
        "`genre: Hor` does NOT match `Horror`. PROBE VERDICT: DEFERRED, and the "
        "surprising one -- the listing DOES carry `<Genre>`, but capped at two "
        "per item. Per-item counts over the full movie listing were {1: 175, "
        "2: 1780} and over the shows {1: 43, 2: 241}: not one item anywhere with "
        "three, against a metadata endpoint that routinely returns three or four. "
        "Twenty sampled movies had 38 genres in the listing against 58 in "
        "metadata, agreeing exactly on 3 of 20. `2 Fast 2 Furious` lists as "
        "Action/Crime and is also a Thriller. A listing-backed accessor would "
        "therefore not fail, it would answer WRONG, so genre leaves tier 1 "
        "rather than ship a filter that silently drops a third of its matches. "
        "PHASE B: moved to `tier2-batched` -- the batched metadata read returns "
        "the family full and untruncated (9a probe F: 3 sampled items matched "
        "the single-key endpoint exactly), so `filters:` may name it; the "
        "engine pays one batched fetch for the definition's resolved set.",
        search_field="genre", show_search_field="show.genre",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "year", "int", _BOTH, "listing",
        "The listing attrib `year`. Kometa's special year words (`current_year` "
        "and its offsets) are NOT tier 1, so `year: current_year` refuses at "
        "load naming the field rather than parsing as something else.",
        search_field="year", show_search_field="show.year",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "resolution", "tag", ("movie",), "listing",
        "Plex carries it on `<Media videoResolution=...>`, which plexapi reaches "
        "through the `media` cached property. Values are Plex's own: 4k, 1080, "
        "720, 576, 480, sd. Movie-only because a show's resolution is a property "
        "of its episodes, and per-episode traversal is not a tier-1 read. PROBE "
        "VERDICT: SHIPS -- the one row that went the opposite way to this note's "
        "original expectation. All 1955 movies carry `<Media>` in the listing, "
        "all 2009 Media elements across them carry `videoResolution` (a "
        "200-item sample found 1080 on 189, sd on 7, 4k on 5, 480 on 3, 720 on "
        "2), and the listing's Media set matched the metadata endpoint's with "
        "zero disagreements on 25 items sampled, including multi-version items "
        "in the sample, which is why the view hands back a LIST of resolutions "
        "rather than one. Unlike the tag families, Media is not "
        "truncated: the per-item count histogram was {1: 1905, 2: 46, 3: 4}, "
        "which is the real distribution of file versions, not a cap.",
        # Movie-only as a client filter and BOTH as a search: Plex answers a
        # show library's resolution at the EPISODE libtype
        # (show_translation, plex.py:186), which is exactly the per-episode
        # traversal the client-side accessor refuses to pay for -- the server
        # does it for free.
        search_field="resolution", show_search_field="episode.resolution",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "audience_rating", "float", _BOTH, "listing",
        "The listing attrib `audienceRating` (video.py:390), Plex's 0-10 "
        "audience score -- NOT `userRating`, which is the logged-in account's "
        "own star rating and is a separate Kometa filter (`user_rating`) left "
        "out of tier 1.",
        search_field="audienceRating", show_search_field="show.audienceRating",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "critic_rating", "float", _BOTH, "listing",
        "The listing attrib `rating` (video.py:401). Kometa calls Plex's "
        "unqualified `rating` field the CRITIC rating, which is why this row "
        "exists rather than a passthrough: an operator writing `rating:` would "
        "be writing a Kometa attribute that does not exist.",
        search_field="rating", show_search_field="show.rating",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "content_rating", "tag", _BOTH, "listing",
        "The listing attrib `contentRating` (video.py:393) -- a single string "
        "like `PG-13`, but Kometa filters it as a TAG (exact match against a "
        "list of allowed values, not a substring), so the view may hand back a "
        "bare string and the tag comparison tolerates one. This is Plex's own "
        "certification and deliberately NOT `ItemFacts.content_rating`, which "
        "is a Common Sense age rating (gather.py:98-116): same name, different "
        "filter, per the plan's Plex-only adjudication.",
        search_field="contentRating", show_search_field="show.contentRating",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "audio_language", "tag", ("movie",), "tier2-batched",
        "Stream-level: the languages of the item's audio streams, under "
        "`<Media><Part><Stream>`. PROBE VERDICT: DEFERRED, exactly as this note "
        "predicted -- streams are a level below the Media children the listing "
        "carries, and it stops at Part. Zero `<Stream>` elements across 200 "
        "listing movies; the same movie via /library/metadata has four. Note "
        "that plexapi's `Movie`/`Show` also expose an `audioLanguage` ATTRIB, "
        "which is the item's preferred-audio SETTING and not the languages its "
        "files contain -- reading that would be a same-name-different-filter "
        "bug of the kind the item_facts adjudication already ruled out. "
        "PHASE B: moved to `tier2-batched` -- the batched metadata read carries "
        "`<Stream>` in full (9a probe F: the streams the listing omits entirely "
        "are present, 4 of 7 stream elements per sampled item being the audio "
        "and subtitle ones), so `filters:` may name it; the engine pays one "
        "batched fetch for the definition's resolved set. The VALUES are ISO "
        "639-1 CODES -- the stream's `languageTag` (`en`), not the `language` "
        "display title (`English`) -- because that is what the tree's only "
        "language normaliser (`plex_search._base_language_code`) emits, so "
        "write `audio_language: en`.",
        search_field="audioLanguage", show_search_field="episode.audioLanguage",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "subtitle_language", "tag", ("movie",), "tier2-batched",
        "Stream-level, like `audio_language`, and DEFERRED with it on the same "
        "probe data (no `<Stream>` element reaches the listing at all). The "
        "`subtitleLanguage` attrib is the same trap as `audioLanguage`. "
        "PHASE B: moved to `tier2-batched` with `audio_language`, on the same "
        "9a probe F evidence (the batched metadata read carries the `<Stream>` "
        "elements the listing omits) and with the same ISO 639-1 CODE values "
        "-- write `subtitle_language: fi`, not `Finnish`.",
        search_field="subtitleLanguage",
        show_search_field="episode.subtitleLanguage",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "label", "tag", _BOTH, "tier2-batched",
        "Plex's `<Label>` child element -> the `labels` cached property "
        "(video.py:441). PROBE VERDICT: DEFERRED -- the listing strips the "
        "family outright. Zero `<Label>` children across all 1955 movies and all "
        "284 shows, while the metadata endpoint carried a label for all 30 "
        "sampled movies and for 19 of 20 sampled shows (`Overlay`, this "
        "server's Kometa-era marker). This settles what reconcile.py:70-83's "
        "deliberate per-collection reload was already evidence for. "
        "PHASE B: moved to `tier2-batched` -- the batched metadata read carries "
        "the labels the listing strips outright (9a probe F: present on the "
        "sampled items, matching the single-key endpoint), so `filters:` may "
        "name it; the engine pays one batched fetch for the definition's "
        "resolved set.",
        search_field="label", show_search_field="show.label",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "added", "date", _BOTH, "listing",
        "The listing attrib `addedAt` (video.py:44). Plex sends a unix epoch, "
        "and plexapi's `toDatetime` (utils.py) converts it with "
        "`datetime.fromtimestamp(value)` -- no `tz` argument, because "
        "plexapi's own `DATETIME_TIMEZONE` is `None` -- so the result is a "
        "naive datetime in the RUNNER's local clock, not the Plex server's. "
        "Compared at the MOMENT, time of day included -- see the date "
        "convention in `_as_moment` -- so `added.after: 2026-06-01` keeps "
        "something added at 09:15 that day, and two runs of the same "
        "collection in different timezones can disagree about an item added "
        "near midnight (roadmap row 154).",
        search_field="addedAt", show_search_field="show.addedAt",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "release", "date", _BOTH, "listing",
        "Kometa's name for Plex's `originallyAvailableAt` (video.py:398). The "
        "row's name is Kometa's, not Plex's, because the config is Kometa-"
        "shaped; the plan calls the attribute `release (originally_available)` "
        "for the same reason.",
        search_field="originallyAvailableAt",
        show_search_field="show.originallyAvailableAt",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "duration", "duration", _BOTH, "listing",
        "The listing attrib `duration` (video.py:394) is MILLISECONDS; Kometa's "
        "filter is MINUTES. The view converts, and the config is minutes -- so "
        "`duration.gt: 90` is an hour and a half, not 90ms. Its own value type "
        "rather than `int` so the unit lives in the table instead of in a "
        "comment on one row. SETTLED-BY-ORACLE: the view hands back the EXACT "
        "quotient `ms / 60000`, a float, not a rounded int. Task 2 rounded it, "
        "to keep `.eq` off float equality; Kometa does not (`test_number /= "
        "60000`, plex.py:2923-2926), and rounding silently moved every RANGE "
        "comparison for runtimes within half a minute of the threshold -- the "
        "oracle's 149.7-minute film passed `duration.lt: 150` in Kometa and "
        "failed here. The cost of the correction is that `duration.eq: 90` is "
        "now a float-equality test almost nothing satisfies, which is exactly "
        "what it is in Kometa; write a range instead.",
        # MOVIE-ONLY as a search, and only for the four range modifiers:
        # ``duration.gt``/``.gte``/``.lt``/``.lte`` are in
        # movie_only_searches (plex.py:441-444). There is no bare ``duration``
        # search at all -- see SEARCH_OPERATORS_BY_TYPE's note -- and the
        # ranges that do exist are MINUTES on both sides, because Kometa
        # multiplies a search duration by 60000 (builder.py:4234).
        search_field="duration", show_search_field=None,
        search_kinds=("movie",), filterable=True,
    ),
    FilterAttribute(
        "studio", "str", _BOTH, "listing",
        "The listing attrib `studio` (video.py:405). The ONE tier-1 string "
        "attribute, and the distinction matters: a bare `studio: Warner` is a "
        "case-insensitive SUBSTRING match, where a bare `genre: Horror` is an "
        "exact tag match. SETTLED-BY-ORACLE (Kometa builder.py:377-447 puts "
        "`studio` in string_filters; both oracle configs exercise it) -- this "
        "transcription files `studio` under Kometa's string filters (which "
        "carry .is/.isnot/.begins/.ends) rather than its tag filters, and that "
        "is the single most consequential tag-vs-string call in the table: had "
        "Kometa treated it as a tag, `studio: Warner` would match nothing at "
        "all instead of everything Warner.",
        # ``studio`` is in BOTH of Kometa's search category lists -- it is a
        # string_attribute (plex.py:507) AND a tag_attribute (plex.py:568),
        # which is where two of the duplicate entries in ``searches`` come
        # from. Which branch wins depends on the modifier:
        # ``validate_attribute`` tests ``.regex`` against the TAG list first
        # (builder.py:4301) and only then the string list (builder.py:4326).
        # Since ``.regex`` is refused here (see SEARCH_OPERATORS_BY_TYPE),
        # every operator this table ships takes the STRING branch, and the
        # value goes to Plex ``quote()``d and unresolved.
        search_field="studio", show_search_field="show.studio",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "network", "tag", ("show",), "tier2-deferred",
        "Show-only. plexapi reads `Show.network` from a listing ATTRIB "
        "(video.py:618), not from a child element, so the expectation was that "
        "this one might well be listing-resident. PROBE VERDICT: DEFERRED, and "
        "STRANDED -- Plex 1.43.4 does not emit `network` at all. Zero of 284 "
        "shows carry the attrib in the listing, AND it is absent from "
        "/library/metadata for the shows checked, so the reload the naive "
        "accessor would pay per show returns None anyway. What those shows do "
        "carry is a `studio` naming the network (E4, Hulu, Paramount+), which is "
        "a DIFFERENT attribute with its own row and its own string semantics; "
        "conflating them is not a substitution this table will make silently. "
        "Filed for tier 2 -- see the Task 2 report's recommendation. "
        "PHASE B VERDICT, recorded rather than shipped false: the batch read "
        "cannot conjure an attrib Plex 1.43.4 emits nowhere (0/284 in the "
        "listing AND absent from /library/metadata), so `network` stays "
        "refused, alone on `tier2-deferred` now that the other five moved. "
        "Sourcing it from TVDb/TMDb under a DISTINCT name is a separate "
        "roadmap row if anyone wants it; conflating it with `studio` stays "
        "refused by name.",
        # Already show-scoped by search_translation (plex.py:63), so
        # show_translation never sees it and the two columns are equal rather
        # than the second being None. 9a proved the ITEM attribute absent on
        # Plex 1.43.4; whether the SEARCH field answers is Task 5's probe #1,
        # the single highest-value question this phase asks.
        search_field="show.network", show_search_field="show.network",
        search_kinds=("show",), filterable=True,
    ),
    FilterAttribute(
        "collection", "tag", _BOTH, "tier2-batched",
        "Plex's `<Collection>` child element -> the `collections` cached "
        "property (video.py:417): the collections the ITEM is already a member "
        "of on the server. Not this service's definitions, and not the "
        "collection being built -- filtering on it is how an operator says "
        "\"anything not already in X\". PROBE VERDICT: DEFERRED. The listing "
        "carries the element for only 257 of 1955 movies, and where it is "
        "missing that is not the truth: 6 of 20 sampled movies disagreed with "
        "the metadata endpoint (4 collection memberships in the listing against "
        "10 in metadata), `2 Fast 2 Furious` among them -- listed as being in no "
        "collection while actually in The Fast and the Furious Collection. Shows "
        "fared better (20 of 20 agreed) but a family that is right for one "
        "library kind and wrong for the other is not a tier-1 filter. Note also "
        "that `includeCollections=1`, the parameter whose name suggests it fixes "
        "this, does something else entirely: it MIXES Collection objects into "
        "the result set, changing what the listing returns. PHASE B: moved to "
        "`tier2-batched` -- the batched metadata read carries the memberships "
        "the listing disagreed about (9a probe F: present on the sampled items "
        "and matching the single-key endpoint), so `filters:` may name it; the "
        "engine pays one batched fetch for the definition's resolved set. Note "
        "what that makes writable which was not: `collection.not: X` -- "
        "\"anything not already in X\" -- is now answered from the truth "
        "rather than from the listing's 257-of-1955.",
        search_field="collection", show_search_field="show.collection",
        search_kinds=_BOTH, filterable=True,
    ),
    # --- rows 9b added ------------------------------------------------------
    #
    # None of the four is a client-side filter today, and the two REASONS are
    # different, which is why ``SOURCE_TIERS`` grew two values rather than one.
    FilterAttribute(
        "plays", "int", _BOTH, "unprobed",
        "Plex's `viewCount` -- how many times the item has been played by the "
        "account the token belongs to. In BOTH of Kometa's vocabularies: a "
        "search (plex.py:80, :547) and a filter (builder.py:280-293). Its "
        "source tier is `unprobed`, not `tier2-deferred`, and the distinction "
        "is deliberate: 9a's probe never asked whether `viewCount` reaches the "
        "section listing, so there is no verdict to cite and the "
        "tier2-deferred refusal copy -- which cites one -- would be a claim "
        "nobody checked. A `filters:` block naming it refuses saying exactly "
        "that. Note also that `viewCount` is PER-ACCOUNT: the answer depends "
        "on whose token the pass runs with, which is a property no other row "
        "in this table has. As a SEARCH it takes the four range modifiers and "
        "nothing else -- it is a number_attribute and not a year_attribute "
        "(plex.py:547, :599) -- so SEARCH_OPERATORS_EXCLUDED subtracts the "
        "bare form and `.not` that its `int` type otherwise offers.",
        search_field="viewCount", show_search_field="show.viewCount",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "last_played", "date", _BOTH, "unprobed",
        "Plex's `lastViewedAt`. In both vocabularies, like `plays`, and "
        "`unprobed` for the same reason. Per-account, like `plays`. As a "
        "search its bare and `.not` forms are RELATIVE WINDOWS -- "
        "`last_played.not: 6o` is \"not played in the last six months\", the "
        "shape a stale-media collection wants -- and roadmap row 154's "
        "timezone divergence applies to it in the opposite direction from "
        "`added`: a server-side date predicate evaluates in the PLEX SERVER's "
        "clock, not the runner's.",
        search_field="lastViewedAt", show_search_field="show.lastViewedAt",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "unplayed", "bool", ("movie",), "search-only",
        "Plex's `unwatched` (plex.py:84), a server-side boolean: `unplayed: "
        "true` emits `unwatched=1`, `false` emits `unwatched!=1` "
        "(builder.py:4238-4241). MOVIE-ONLY -- it is in movie_only_searches "
        "(plex.py:439). A show library's equivalent is `unplayed_episodes` "
        "(`show.unwatchedLeaves`), which is a different attribute with a "
        "different meaning (how many episodes, not whether the show) and is "
        "filed for the per-family tail rather than aliased silently. "
        "`search-only`: Kometa has no `unplayed` FILTER at all, so `filters:` "
        "refuses it by naming the block it does belong to. Per-account.",
        search_field="unwatched", show_search_field=None,
        search_kinds=("movie",), filterable=False,
    ),
    FilterAttribute(
        "progress", "bool", ("movie",), "search-only",
        "Plex's `inProgress` (plex.py:89): partially played. Movie-only "
        "(plex.py:440); the show equivalent is `episode_progress`, filed for "
        "the tail. `search-only` like `unplayed`, and per-account like it. "
        "Note that Kometa's `progress` SORT (`viewOffset`, plex.py:623-624) "
        "is a different thing under the same word -- a sort key, not a "
        "predicate -- and the sort table carries it independently.",
        search_field="inProgress", show_search_field=None,
        search_kinds=("movie",), filterable=False,
    ),
    # --- rows 10a added ------------------------------------------------------
    #
    # Both are named by roadmap row 102's own decomposition list, and neither
    # is a new mechanism: ``decade`` is roadmap row 171's single table row (the
    # ``current_year``/``current_year-N`` value grammar that row also carries
    # is NOT built here and stays with it), and ``country`` is roadmap row 174
    # in full. Phase 10a needs them because its acceptance criterion names
    # ``decade`` and because upstream's dynamic type table carries both.
    FilterAttribute(
        "decade", "int", ("movie",), "search-only",
        "Plex's own ``decade`` filter. MOVIE-ONLY -- it is in "
        "``movie_only_searches`` (plex.py:437), which is exactly why Kometa's "
        "show-decade dynamic type falls back to a full library scan "
        "(meta.py:881-898) and why this phase does not ship one. A YEAR "
        "attribute upstream (``year_attributes``, plex.py:546), so its values "
        "are sent as PLAIN NUMBERS -- ``decade=1980`` -- and are never resolved "
        "through the library's tag vocabulary, exactly like ``year``; the "
        "dynamic engine feeds it the enumerated ``choice.key`` (``1980``) and "
        "titles the collection from ``choice.title`` (``1980s``). Its operator "
        "set is NOT the ``int`` set: ``decade`` is in ``no_not_mods`` "
        "(plex.py:593), which subtracts ``.not`` from its tag modifiers AND "
        "removes it from the number-modifier comprehension entirely "
        "(plex.py:597-599), so the bare form is the only decade search Kometa "
        "builds -- ``SEARCH_OPERATORS_EXCLUDED`` subtracts the other five. "
        "``search-only``: Kometa has no ``decade`` FILTER at all (row 96's "
        "29-name search-only list names it first), so a ``filters:`` block "
        "refuses it by naming the block it does belong to.",
        search_field="decade", show_search_field=None,
        search_kinds=("movie",), filterable=False,
    ),
    FilterAttribute(
        "country", "tag", ("movie",), "unprobed",
        "Plex's ``<Country>`` child element -- a tag attribute whose values "
        "resolve through the same ``listFilterChoices`` path every shipped tag "
        "attribute uses, which is roadmap row 174 in full. Re-scoped to "
        "``show.country`` on a show library by ``show_translation`` "
        "(plex.py:168-193). Source tier ``unprobed`` rather than "
        "``tier2-deferred``, for the reason ``plays`` and ``last_played`` carry "
        "that tier: 9a's probe never asked whether ``<Country>`` reaches the "
        "section listing, so there is no verdict to cite and the "
        "tier2-deferred copy -- which cites one -- would be a claim nobody "
        "checked. It IS in both of Kometa's vocabularies (row 96's arithmetic "
        "puts it in the 26-name overlap: it appears in neither the 44 "
        "filter-only names nor the 29 search-only ones), so ``filterable`` is "
        "True. ``kinds`` is movie-only because upstream's FILTER scope for "
        "``country`` is movie-only too -- ``builder.py:328``'s only entry is "
        "``\"movie_artist\": [\"country\"]``, no show key carries it -- so a "
        "``filters:`` block on a show library refuses it by naming its tier. "
        "That is a different upstream table from phase 10a's dynamic "
        "``country`` TYPE, which is ALSO movie-only (meta.py:18-19); the "
        "SEARCH column is unaffected by either and answers for both library "
        "types via ``show_translation``.",
        search_field="country", show_search_field="show.country",
        search_kinds=_BOTH, filterable=True,
    ),
    # --- rows phase B added --------------------------------------------------
    #
    # The four PEOPLE rows. Their existence is the whole of roadmap row 194's
    # "blocker one": until the table carried them, a per-person query was not
    # WRITABLE at all -- ``parse_filters(..., searching=True)`` refuses an
    # attribute the table does not hold, which is exactly right and is what
    # made the four Top-* packs gated rather than merely unbuilt.
    #
    # All four are ``unprobed``, which is a SOURCE tier and not an accessor:
    # phase B ships their SEARCH half and no client-side read. Where a
    # client-side read would come from is a different question with a
    # different answer -- the credits CACHE (``collections/credits.py``), not
    # a listing accessor -- and it is a facts-tier decision roadmap row 156
    # owns rather than something this table may assume.
    FilterAttribute(
        "actor", "tag", _BOTH, "unprobed",
        "Plex's ``<Role>`` child element -- the library's own credit data, the "
        "same tags the phase-B credits cache scans. Row 169's first entry: the "
        "row's existence is what makes a per-person query WRITABLE at all "
        "(roadmap row 194 blocker one). ``unprobed`` client-side for the "
        "reason ``plays`` carries that tier: 9a never asked whether "
        "``<Role>`` reaches the section listing completely, so there is no "
        "verdict to cite. Re-scoped to ``show.actor`` on a show library by "
        "``show_translation`` (plex.py:168-193) -- an entry this task's brief "
        "said did not exist and the repo's own verbatim transcription of that "
        "table (``tests/oracle/9b/kometa_build_filter.py``) shows it does; "
        "``None`` here would have sent a show library the bare ``actor`` "
        "field, which is the silent-wrong-set failure ``field_for`` exists to "
        "prevent. Values resolve through ``listFilterChoices`` (the phase-B "
        "probe measured the field answering on both sections); Kometa's "
        "hubSearch fallback (plex.py:1273-1280) is deliberately NOT built "
        "until a real miss shows the choices listing insufficient.",
        search_field="actor", show_search_field="show.actor",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "director", "tag", ("movie",), "unprobed",
        "Plex's ``<Director>`` child element. MOVIE-ONLY as a search -- it is "
        "in Kometa's ``movie_only_searches`` (plex.py:430-436) -- so the show "
        "libtype refuses by name rather than sending a query Plex answers "
        "with the wrong set. Movie-only as a FILTER too, and independently: "
        "``builder.filters_by_type`` carries it under the movie/episode key "
        "and no show key. The phase-B probe measured the same thing from the "
        "other end (D7): a show section enumerates this field EMPTY rather "
        "than refusing it, which is the quietest possible wrong answer and is "
        "why the gate is by name here. Everything else is the ``actor`` "
        "row's note.",
        search_field="director", show_search_field=None,
        search_kinds=("movie",), filterable=True,
    ),
    FilterAttribute(
        "writer", "tag", ("movie",), "unprobed",
        "Plex's ``<Writer>`` child element. Movie-only per plex.py:430-436, "
        "like ``director``; otherwise the ``actor`` row's note.",
        search_field="writer", show_search_field=None,
        search_kinds=("movie",), filterable=True,
    ),
    FilterAttribute(
        "producer", "tag", ("movie",), "unprobed",
        "Plex's ``<Producer>`` child element. Movie-only per plex.py:430-436, "
        "like ``director``; otherwise the ``actor`` row's note.",
        search_field="producer", show_search_field=None,
        search_kinds=("movie",), filterable=True,
    ),
)

BY_NAME: dict[str, FilterAttribute] = {row.name: row for row in FILTER_ATTRIBUTES}

# Derived from the table, in table order, so each reads as a subset of it
# rather than as an independent list. ``filter_values.SHIPPED_ATTRIBUTES``
# already does the same thing one column along.
SEARCHABLE_ATTRIBUTES: tuple[str, ...] = tuple(
    row.name for row in FILTER_ATTRIBUTES if row.searchable
)
FILTERABLE_ATTRIBUTES: tuple[str, ...] = tuple(
    row.name for row in FILTER_ATTRIBUTES if row.filterable
)


class ItemView(Protocol):
    """What ``evaluate`` reads. A plain ``dict`` satisfies it.

    ``get`` is keyed on the TABLE's attribute name -- ``release``, not
    ``originallyAvailableAt`` -- and returns the value in the type the row
    declares: a sequence of strings (or one bare string) for ``tag``, a string
    for ``str``, a number for ``int``/``float``, a ``float`` of MINUTES for
    ``duration``, a ``date`` or ``datetime`` for ``date``. ``None`` means the
    item has no value, which is the missing-value rule's input.

    Duration is the exact quotient of Plex's milliseconds and 60000, NOT
    rounded (SETTLED-BY-ORACLE; this said ``int``, ROUNDED, until Task 4).
    Rounding does keep ``duration.eq`` off float equality, but it moves every
    range comparison for a runtime within half a minute of the threshold, and
    Kometa rounds nothing. The trap it was avoiding is real and is Kometa's
    too: see the ``duration`` row's note.

    A ``datetime`` for a ``date`` row keeps its time of day -- comparisons are
    made at the moment, not the calendar date (``_as_moment``).
    """

    def get(self, attribute: str, /) -> object | None: ...


@dataclass(frozen=True)
class FilterPredicate:
    """One ``attribute[.operator]: value`` line, parsed and typed.

    ``values`` is always a tuple even for a scalar, because a list in YAML
    means ANY-OF and treating the scalar as a one-element any-of is the same
    rule with no special case. ``field`` is the dotted path of the YAML key
    this came from (``filters.any[0].year.gte``), carried so a refusal and a
    future log line can name it.
    """

    attribute: FilterAttribute
    operator: str
    values: tuple[object, ...]
    field: str


@dataclass(frozen=True)
class FilterGroup:
    """``all`` (every child must match) or ``any`` (one child must match).

    ``inline`` is a RENDERING hint and nothing else: it says this group's
    children belong in the parent's stream rather than inside their own
    ``push``/``pop`` pair, which is how a ``plex_search`` list-shaped nesting
    reproduces Kometa byte for byte. It is always False for a ``filters:``
    tree, and ``evaluate`` ignores it -- an inline group's ``op`` equals its
    parent's, and both conjunctions are associative, so the boolean answer is
    the same either way. See ``_parse_nested``.
    """

    op: str
    children: tuple["FilterGroup | FilterPredicate", ...]
    field: str
    inline: bool = False


# --- parsing -----------------------------------------------------------------


def _boolean_is_not_a_value(value: object, field: str) -> None:
    """``True`` is an ``int`` in python and would silently compare as 1.

    Checked before every numeric coercion rather than inside one, because a
    YAML ``genre: yes`` is a bool too and has nothing to do with numbers.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field}: {value!r} is a true/false, which no filter takes")


def _as_text(value: object, field: str) -> str:
    """A tag or string value. Numbers are accepted because Plex's own tag
    vocabularies contain them -- `resolution: 1080` is an int in YAML and a
    string on the wire, and quoting it is not something an operator should have
    to know to do."""
    _boolean_is_not_a_value(value, field)
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        raise ValueError(f"{field}: {value!r} is not a word or phrase")
    if not text:
        raise ValueError(f"{field}: an empty value matches nothing -- remove the key instead")
    return text


def _as_regex(value: object, field: str) -> re.Pattern:
    """Compiled at load, so a broken pattern is a refusal naming the field
    rather than an exception per item, hours later, inside the run.

    CASE-SENSITIVE, unlike every other operator here (SETTLED-BY-ORACLE). This
    module first compiled with ``re.IGNORECASE``, on the assumption that a
    regex should behave like the substring and tag comparisons beside it. It
    should not: Kometa compiles a filter pattern with no flags at every one of
    the three places it touches one -- ``util.validate_regex``
    (util.py:310-323, plain ``re.compile(reg)``), the string comparison
    (``re.compile(check_value).search(value)``, util.py:650) and the tag
    comparison (``re.compile(reg).search(name)``, plex.py:2961) -- so
    ``studio.regex: pictures$`` matches nothing at all in Kometa while our
    version matched every Universal/Columbia/Paramount title in the library.
    The oracle caught exactly that (six members ours-only, Task 4's report).
    An operator who wants the old behaviour writes ``(?i)`` in the pattern,
    which means the same thing in both.
    """
    text = _as_text(value, field)
    try:
        return re.compile(text)
    except re.error as error:
        raise ValueError(
            f"{field}: {text!r} is not a valid regular expression -- {error}"
        ) from error


def _as_int(value: object, field: str) -> int:
    _boolean_is_not_a_value(value, field)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as error:
            raise ValueError(f"{field}: {value!r} is not a whole number") from error
    raise ValueError(f"{field}: {value!r} is not a whole number")


def _as_float(value: object, field: str) -> float:
    _boolean_is_not_a_value(value, field)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as error:
            raise ValueError(f"{field}: {value!r} is not a number") from error
    raise ValueError(f"{field}: {value!r} is not a number")


_CLOCK = re.compile(r"^(\d+):([0-5]\d)$")
_SPELLED = re.compile(r"^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?$", re.IGNORECASE)


def _as_minutes(value: object, field: str) -> float:
    """A duration, in minutes.

    A bare number is Kometa's own form and is minutes. The written forms
    (``1h30m``, ``90m``, ``1:30``) are this module's addition, normalised to
    minutes here so nothing downstream has to know they exist -- added because
    a runtime filter is the one place an operator naturally writes hours.
    """
    _boolean_is_not_a_value(value, field)
    if isinstance(value, (int, float)):
        minutes = float(value)
    elif isinstance(value, str):
        text = value.strip()
        clock = _CLOCK.match(text)
        spelled = _SPELLED.match(text)
        if clock:
            minutes = float(clock.group(1)) * 60 + float(clock.group(2))
        elif spelled and (spelled.group(1) or spelled.group(2)):
            minutes = float(spelled.group(1) or 0) * 60 + float(spelled.group(2) or 0)
        else:
            try:
                minutes = float(text)
            except ValueError as error:
                raise ValueError(
                    f"{field}: {value!r} is not a runtime -- write minutes (90), or 1h30m, or 1:30"
                ) from error
    else:
        raise ValueError(
            f"{field}: {value!r} is not a runtime -- write minutes (90), or 1h30m, or 1:30"
        )
    if minutes < 0:
        raise ValueError(f"{field}: {value!r} is a negative runtime")
    return minutes


class _Today:
    """The literal ``today``, resolved against the run's MOMENT rather than the
    parse's -- a config loaded once and run nightly must not freeze the day it
    was read on.

    The run's moment, not the run's midnight: Kometa resolves the same word as
    ``datetime.now() if data == "today"`` (builder.py:4443), so
    ``release.before: today`` keeps something released earlier today. Ours
    agrees since Task 4's oracle."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "today"


_TODAY = _Today()


@dataclass(frozen=True)
class RelativeWindow:
    """A bare or ``.not`` date in a **search**: "in the last N <unit>".

    The client-side spelling of the same idea is a plain int of days
    (``_as_days``), because ``filters:`` evaluates in python and there is
    nothing to hand a unit to. A search has a server, and Plex takes the unit
    natively -- Kometa sends ``f"{count}{unit}"`` (builder.py:4446-4452) with
    the unit taken from the value's last character. This is the narrowing of
    9a's two ``PLEXAPI_EQUIVALENT`` ``None``s that the roadmap's Notes-for-9b
    item 2 predicted: those two entries mean "plexapi's CLIENT-side table has
    no key", not "Plex cannot do this".

    ``unit`` is a key of ``RELATIVE_UNITS``. Note ``o`` is months and ``m`` is
    minutes; the renderer rewrites ``o`` to ``mon`` on the wire
    (builder.py:4226-4227), which is Plex's spelling and not Kometa's.
    """

    count: int
    unit: str


_WINDOW = re.compile(r"^(\d+)([smhdwoy])$")


def _as_window(value: object, field: str) -> RelativeWindow:
    """A relative window, in Kometa's own spelling.

    A bare int is days, which is both Kometa's default (``search_mod = "d"``,
    builder.py:4447) and the client-side meaning, so the two blocks agree on
    the one spelling an operator is most likely to write. A suffixed string
    names its own unit. Anything else refuses NAMING ALL SEVEN UNITS, because
    ``o`` for months next to ``m`` for minutes is the single least guessable
    thing in this vocabulary and a refusal that does not spell it out sends
    the operator to the source.
    """
    _boolean_is_not_a_value(value, field)
    if isinstance(value, int):
        count, unit = value, "d"
    elif isinstance(value, str):
        text = value.strip().lower()
        match = _WINDOW.match(text)
        if match:
            count, unit = int(match.group(1)), match.group(2)
        elif text.isdigit():
            count, unit = int(text), "d"
        else:
            count = -1
            unit = ""
    else:
        count, unit = -1, ""
    if not unit or count < 0:
        spelled = ", ".join(
            f"{key} = {name.lower()}" for key, name in RELATIVE_UNITS.items()
        )
        raise ValueError(
            f"{field}: {value!r} is not a window -- write a whole number of days "
            f"(30), or a number with a unit ({spelled}). Note that `o` is months "
            "and `m` is minutes"
        )
    return RelativeWindow(count=count, unit=unit)


def _as_bool(value: object, field: str) -> bool:
    """A true/false. The one place in this module where a bool is the value
    rather than the mistake -- see ``_boolean_is_not_a_value``, which every
    other coercion opens with.

    Strings are refused even though Kometa accepts ``t``/``yes``/``n``/``no``
    (util.py:985-995): YAML already turns every spelling an operator would
    naturally write into a real boolean, so accepting the strings would only
    add spellings nobody needs and one more thing for the two systems to
    disagree about.
    """
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field}: {value!r} is not true or false")


_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

_DATE_MESSAGE = "is not a date -- write it as 2024-01-31 or 01/31/2024, or `today`"


def _as_date(value: object, field: str) -> dt.date | _Today:
    """A date, in either of two unambiguous spellings.

    ISO (``2024-01-31``, dashes, 4-digit year leading) is this module's own
    form; ``MM/DD/YYYY`` (slashes) is how Kometa's own configs spell it, and
    an operator copying a value out of an existing Kometa config should not
    have to reformat it. The two are told apart by punctuation, not position,
    so there is no reading of a well-formed value that is ambiguous between
    them.
    """
    if isinstance(value, str) and value.strip().casefold() == "today":
        return _TODAY
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        text = value.strip()
        us = _US_DATE.match(text)
        if us:
            month, day, year = (int(group) for group in us.groups())
            try:
                return dt.date(year, month, day)
            except ValueError as error:
                raise ValueError(f"{field}: {value!r} {_DATE_MESSAGE}") from error
        try:
            return dt.date.fromisoformat(text)
        except ValueError as error:
            raise ValueError(f"{field}: {value!r} {_DATE_MESSAGE}") from error
    raise ValueError(f"{field}: {value!r} {_DATE_MESSAGE}")


def _as_days(value: object, field: str) -> int:
    """The bare date form's value: a window in days.

    ``added: 30`` is "added in the last 30 days", not "added on day 30". This
    is Kometa's documented meaning for a date filter with no modifier and it is
    the least guessable thing in the table, which is why a string here refuses
    rather than being coerced -- ``added: "2024-01-01"`` is an operator who
    meant ``added.after``, and answering it with a 2024-day window would be
    worse than saying so.

    SETTLED-BY-ORACLE: Kometa's blank-modifier date-filter branch is
    ``value < current_time - timedelta(days=data)`` (util.py:601-604) -- a
    day-window test, not an equality test, so this reading was right. The
    *upper* half of the recollection that appeared here as a
    ``SETTLED-IN-FAVOR`` note (``or value > current_time``) is not in Kometa's
    code and has been retracted; see ``_matches_one``.
    """
    _boolean_is_not_a_value(value, field)
    if not isinstance(value, int):
        raise ValueError(
            f"{field}: {value!r} is not a number of days -- a bare `{field.rsplit('.', 1)[-1]}:` "
            "is a window in days (30 means the last 30 days); for a fixed date use .before/.after"
        )
    if value < 0:
        raise ValueError(f"{field}: {value!r} is a negative number of days")
    return value


def _parse_value(
    attribute: FilterAttribute,
    operator: str,
    value: object,
    field: str,
    *,
    searching: bool,
) -> object:
    if operator == "regex":
        return _as_regex(value, field)
    if operator == "rated":
        # ``.rated`` is a yes/no question about a FLOAT attribute -- "does this
        # item have a critic rating at all" -- so the value's type has nothing
        # to do with the row's.
        return _as_bool(value, field)
    if attribute.type == "bool":
        return _as_bool(value, field)
    if attribute.type in ("tag", "str"):
        return _as_text(value, field)
    if attribute.type == "int":
        return _as_int(value, field)
    if attribute.type == "float":
        return _as_float(value, field)
    if attribute.type == "duration":
        return _as_minutes(value, field)
    # date
    if operator in ("eq", "not"):
        return _as_window(value, field) if searching else _as_days(value, field)
    return _as_date(value, field)


def _split_key(key: str, field: str, *, searching: bool) -> tuple[FilterAttribute, str]:
    name, _, modifier = key.partition(".")
    attribute = BY_NAME.get(name)
    vocabulary = SEARCHABLE_ATTRIBUTES if searching else FILTERABLE_ATTRIBUTES
    block = "plex_search" if searching else "filters:"
    if attribute is None:
        # The noun keeps 9a's wording for a ``filters:`` block rather than
        # generalising it away, and the block name carries the rest: the two
        # vocabularies are different lists, so offering the whole table here
        # would name attributes the block being parsed cannot take.
        noun = "search" if searching else "filter"
        raise ValueError(
            f"unknown {noun} attribute {name!r} at {field}: the {block} "
            "vocabulary is " + ", ".join(sorted(vocabulary))
        )

    # The cross-reference (D2c). The two vocabularies are DISTINCT and share
    # one table, so an attribute can be legal in one block and refused in the
    # other -- and when it is, the refusal says where it does live rather than
    # reading as a gap. Kometa's own two vocabularies are not nested either:
    # 44 of its filter names have no Plex search field, and 29 of its search
    # names have no filter.
    #
    # The FIRST of the two branches is unreachable with today's twenty-one rows:
    # every one of them is searchable, because the fifteen 9a shipped all have
    # Plex search fields. It is written now, and tested with a synthetic row,
    # because the first filter-only attribute (``aspect``, ``height``,
    # ``versions``, ``summary``, ... -- 44 of them, roadmap row 96's residue)
    # will arrive under a table row and must not arrive under a bare KeyError.
    if searching and not attribute.searchable:
        raise ValueError(
            f"{field}: {name!r} is a client-side filter attribute but Plex has "
            "no search field for it, so a plex_search cannot ask for it. Write "
            "it as a `filters:` block on the definition instead -- the search "
            "narrows server-side and the filter refines what comes back"
        )
    if not searching and not attribute.filterable:
        raise ValueError(
            f"{field}: {name!r} is a plex_search attribute (Plex answers it "
            "server-side) and not a client-side filter -- Kometa has no filter "
            "of that name either. Move it into the plex_search builder's "
            "`params`"
        )

    if modifier == "and":
        # Kometa's ``and_searches`` (plex.py:391-407) makes ``genre.and`` mean
        # "every one of these", while a bare ``genre:`` at the top level means
        # "any of these" -- a base conjunction chosen by a suffix on one key.
        # Refused, because the same key spelling would then mean two
        # memberships depending on a suffix three characters long.
        raise ValueError(
            f"{field}: .and is not a modifier here. Kometa uses it to make one "
            f"key ANDed inside an implicit base; write the base out instead -- "
            f"`all:` for every-one-of, `any:` for any-of -- so the config says "
            f"which it is"
        )
    if searching and modifier == "regex":
        raise ValueError(
            f"{field}: .regex is not a plex_search modifier. Kometa's search "
            "regex does not reach Plex at all -- it expands the pattern against "
            "the library's own tag vocabulary first and sends the matching tags "
            "(builder.py:4301-4323) -- so one spelling would mean two "
            "mechanisms. A `filters:` block on the same definition supports "
            ".regex client-side"
        )
    if not searching and modifier in SEARCH_ONLY_OPERATORS:
        # Both halves of the condition are load-bearing, and they are different
        # halves. The TYPE is why the advice can name a numeric comparison at
        # all (``genre.gt: 0`` is meaningless); the OPERATOR is what the rest of
        # the sentence describes -- "has any rating at all", the -1 sentinel.
        # ``SEARCH_ONLY_OPERATORS`` is a one-element tuple today, so testing
        # only the type would put this copy under any second entry that joins
        # it, describing a modifier that is not ``.rated``.
        if attribute.type == "float" and modifier == "rated":
            raise ValueError(
                f"{field}: .{modifier} is a plex_search modifier -- Plex answers "
                f"'has any rating at all' as a server-side comparison against -1 "
                f"and a client-side filter has no equivalent spelling. Write "
                f"`{name}.gt: 0` if that is what you mean"
            )
        raise ValueError(
            f"{field}: .{modifier} is a plex_search modifier -- it has no "
            f"client-side filter equivalent, for {name!r} or any attribute"
        )

    operators = attribute.search_operators if searching else attribute.operators
    default = attribute.default_operator

    if not modifier:
        if default in operators:
            return attribute, default
        # Reached by the four search rows whose search operator set has no
        # blank form: ``duration``, the two ratings and ``plays`` (``plays``'s
        # own TYPE has one, but the row subtracts it -- see the ``int`` note
        # above). The message names the reason rather than the rule, because
        # "not supported" would read as a gap in Plex and it is not one --
        # Kometa refuses the same key, from the same list.
        raise ValueError(
            f"{field}: a bare `{name}:` is not a plex_search. Kometa's search "
            f"vocabulary gives {name!r} its range modifiers and nothing else "
            "(plex.py:594-601), and it checks a written key against exactly "
            "that list (builder.py:4194-4195), so Plex is never asked a plain "
            "equality question about it. Write one of "
            + ", ".join(f"`{name}.{op}`" for op in operators)
        )

    if modifier not in operators or modifier == default:
        writable = [f".{op}" for op in operators if op != default]
        # The bare form's meaning is not literally its "default operator" name
        # for a date -- ``added: 30`` is a window in days, not "added eq 30" --
        # so saying "which means eq" here would teach the wrong thing about
        # what a bare key does.
        bare_meaning = (
            "within-the-last-N-days"
            if attribute.type == "date" and not searching
            else "in-the-last-N"
            if attribute.type == "date"
            else default
        )
        head = (
            f"{field}: .{modifier} does not apply to {name!r}, "
            f"{'an' if attribute.type[:1] in 'aeiou' else 'a'} "
            f"{attribute.type} attribute in a {block} block"
        )
        if not writable:
            # ``resolution`` as a SEARCH is the row this exists for: its whole
            # operator set is the bare form (Kometa's no_not_mods), so the list
            # of writable modifiers is empty and "it takes " would render as a
            # dangling phrase followed by a parenthesis.
            message = head + f" -- it takes no modifier at all, which means {bare_meaning}"
        else:
            message = head + " -- it takes " + ", ".join(writable)
            if default in operators:
                message += f" (or no modifier at all, which means {bare_meaning})"
        if attribute.type == "date" and modifier in ("gt", "gte", "lt", "lte"):
            # Kometa accepts all four on a date and rewrites every one of them
            # to the STRICT form (plex.py:2735-2747). Refusing without saying
            # so would look like a gap; the point is that the spelling means
            # something different from what it says, in Kometa as much as here.
            message += (
                f". Kometa accepts .{modifier} on a date but silently rewrites it to "
                ".after/.before, which are strict -- write the strict one you mean, so "
                "the config says what it does"
            )
        if name == "resolution" and modifier == "not":
            message += (
                ". Plex answers no negated resolution filter at all "
                "(Kometa's no_not_mods, plex.py:593)"
            )
        raise ValueError(message)
    return attribute, modifier


def _parse_predicate(key: str, raw: object, field: str, *, searching: bool) -> FilterPredicate:
    attribute, operator = _split_key(key, field, searching=searching)
    written = raw if isinstance(raw, (list, tuple)) else [raw]
    if not written:
        raise ValueError(f"{field}: an empty list matches nothing -- remove the key instead")
    values = tuple(
        _parse_value(attribute, operator, one, field, searching=searching)
        for one in written
    )
    return FilterPredicate(attribute=attribute, operator=operator, values=values, field=field)


def _parse_block(raw: object, op: str, field: str, *, searching: bool) -> FilterGroup:
    """One mapping of ``attribute[.operator]: value`` keys, plus any nested
    ``any:``/``all:`` blocks, combined with ``op``."""
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field}: a filter block is a mapping of attributes, not {raw!r}")
    if not raw:
        raise ValueError(f"{field} is empty -- remove it, or give it an attribute to filter on")
    children: list[FilterGroup | FilterPredicate] = []
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"{field}: {key!r} is not an attribute name")
        if key in ("any", "all"):
            children.append(
                _parse_nested(value, key, f"{field}.{key}", parent_op=op, searching=searching)
            )
        else:
            children.append(
                _parse_predicate(key, value, f"{field}.{key}", searching=searching)
            )
    return FilterGroup(op=op, children=tuple(children), field=field)


def _parse_nested(
    raw: object, op: str, field: str, *, parent_op: str, searching: bool
) -> FilterGroup:
    """An ``any:``/``all:`` value, in either accepted shape.

    A **mapping** makes each of its keys one alternative -- ``any: {studio:
    A24, year.gte: 2020}`` is "from A24 or from this decade". Identical in both
    modes: Kometa's ``util.get_list`` turns a mapping into a one-element list
    and renders it with the written key's conjunction, which is what this is.

    A **list** is where the two blocks diverge, and the divergence is Kometa's,
    not ours:

    - ``filters:`` (Builder.check_filters, builder.py:4674-4754) ANDs each
      element's keys and ORs the elements, so ``any: [{a, b}, {c}]`` is
      ``(a AND b) OR c``. That is 9a's behaviour and its oracle settled it.
    - ``plex_search`` (Builder._filter, builder.py:4207-4217) renders each
      element with the WRITTEN key's conjunction and joins the elements with
      the CONTAINING block's, so the same YAML is ``push(a OR b) <parent> c``.

    One grammar, two renderings, one parameter -- rather than a second parser,
    or a refusal that would cost a Kometa spelling. ``inline`` is how the URL
    builder tells them apart: an inline wrapper's children are spliced into the
    parent's stream instead of getting a ``push``/``pop`` of their own, which
    is what makes the byte-level output match. ``evaluate`` needs no change,
    because an inline wrapper's op equals its parent's and both AND and OR are
    associative.
    """
    if isinstance(raw, Mapping):
        return _parse_block(raw, op, field, searching=searching)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        if not raw:
            raise ValueError(f"{field} is empty -- remove it, or give it a block to filter on")
        element_op = op if searching else "all"
        blocks = tuple(
            _parse_block(one, element_op, f"{field}[{index}]", searching=searching)
            for index, one in enumerate(raw)
        )
        return FilterGroup(
            op=parent_op if searching else op,
            children=blocks,
            field=field,
            inline=searching,
        )
    raise ValueError(f"{field}: expects a mapping of attributes or a list of them, not {raw!r}")


def parse_filters(
    raw: object,
    *,
    field: str = "filters",
    searching: bool = False,
    base: str = "all",
) -> FilterGroup:
    """Parse a ``filters:`` block or a ``plex_search`` block, or refuse naming
    the field that is wrong.

    Every refusal is a ``ValueError`` whose message starts with (or contains)
    the dotted path of the offending key, because the config layer wraps it
    with the definition's title and an operator with twenty definitions needs
    both halves to fix one.

    ``base`` is the top-level conjunction, and it defaults to ``all`` because
    that is what several keys in one ``filters:`` mapping mean in Kometa. A
    ``plex_search`` writes its base out (``all:`` or ``any:``, D1 -- the
    implicit base is refused), and the builder passes the written one here
    rather than nesting the block one level deeper: Kometa's ``base_dict`` IS
    the inner mapping (builder.py:4278-4284), and wrapping it would add a
    ``push``/``pop`` pair Kometa does not emit.

    ``searching`` selects the SEARCH vocabulary and grammar -- see
    ``SEARCH_OPERATORS_BY_TYPE``, ``_as_window`` and ``_parse_nested``. Both are
    keyword-only, and both default to the 9a behaviour its oracle settled.
    """
    if base not in ("any", "all"):
        raise ValueError(f"{field}: {base!r} is not a base -- write `any` or `all`")
    return _parse_block(raw, base, field, searching=searching)


def predicates(node: "FilterGroup | FilterPredicate") -> Iterator[FilterPredicate]:
    """Every predicate in a parsed tree, depth-first.

    The config layer's source-tier check walks this: whether a filter names an
    attribute the item view can actually read is a property of the leaves, and
    parsing alone cannot answer it (this module's vocabulary is the whole
    table, accessors exist for a subset -- see ``filter_values``). Kept here so
    that the tree's shape stays this module's business.
    """
    if isinstance(node, FilterPredicate):
        yield node
        return
    for child in node.children:
        yield from predicates(child)


def batched_attributes(group: FilterGroup) -> tuple[str, ...]:
    """The distinct ``tier2-batched`` attribute names this parsed filter reads,
    in first-appearance order.

    What the engine uses to decide whether a definition needs the enrichment
    pass at all -- computed from the table row, never from config (there is no
    knob to declare it, and a knob would be a second place for the answer to
    be wrong). Distinct because two predicates on one attribute are still one
    fetch, and ordered because a refusal's action string names these back to
    the operator, who wrote them in this order.
    """
    seen: dict[str, None] = {}
    for predicate in predicates(group):
        if predicate.attribute.source == "tier2-batched":
            seen.setdefault(predicate.attribute.name, None)
    return tuple(seen)


# --- evaluation --------------------------------------------------------------


def _as_moment(value: object, attribute: str) -> dt.datetime:
    """The date convention, in one place.

    Every date comparison is made at the MOMENT, not the calendar date
    (SETTLED-BY-ORACLE). This module first compared date-granularly, dropping
    the time of day on both sides; Kometa does not. Its filter compares the
    plexapi value as it stands -- a full datetime for ``addedAt`` -- against a
    ``validate_date`` result, which is ``datetime.strptime`` and therefore
    midnight (util.py:299-307), and against ``current_time``, which is
    ``datetime.now()`` (builder.py:1165). So ``added.after: 2026-06-01`` keeps
    an item added at 09:15 THAT DAY in Kometa and dropped it here, and the
    oracle found three such items in a 120-item library (Task 4's report). A
    date value (``release``, which Plex sends as a bare date) reads as that
    day's midnight, which is what plexapi produces for it anyway, so nothing
    about the absolute operators on ``release`` changed.

    An aware datetime keeps the wall-clock reading it already has -- ``tzinfo``
    is dropped, never converted. Converting to UTC or to the runner's zone
    would make the same library filter differently depending on where the pass
    happened, which is not a property a collection should have.

    ``added`` does not reach this function timezone-neutral, though: Plex sends
    ``addedAt`` as a unix epoch, and plexapi's ``toDatetime`` converts it with
    ``datetime.fromtimestamp(value)`` -- no ``tz`` -- so it is already a naive
    datetime in the RUNNER's local clock by the time this function sees it, not
    the Plex server's. This function's zone-preserving policy is correct for a
    value that already carries the right zone; it does not undo ``added``'s
    pre-existing runner-dependence (roadmap row 154).
    """
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    raise TypeError(f"the view gave {value!r} for {attribute!r}, which is not a date")


def _as_number(value: object, attribute: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"the view gave {value!r} for {attribute!r}, which is not a number")
    return float(value)


def _as_tags(value: object, attribute: str) -> tuple[str, ...]:
    """A bare string counts as one tag: ``content_rating`` is a tag filter over
    a single Plex string, so the view handing back ``"PG-13"`` is correct."""
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(one) for one in value)
    raise TypeError(f"the view gave {value!r} for {attribute!r}, which is not a list of tags")


def _is_missing(value: object, value_type: str) -> bool:
    """The missing-value rule's input.

    ``None`` always, plus the two present-but-empty shapes Plex actually
    produces: an item with no tags at all, and an empty ``studio`` string. A
    value of the wrong *type* is deliberately NOT missing -- it falls through to
    the comparison, which raises. See ``evaluate``.
    """
    if value is None:
        return True
    if value_type == "tag":
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, Sequence) and not isinstance(value, bytes):
            return len(value) == 0
        return False
    if value_type == "str":
        return isinstance(value, str) and not value.strip()
    return False


def _matches_one(
    attribute: FilterAttribute, operator: str, have: object, want: object, now: dt.datetime
) -> bool:
    """One written value of a POSITIVE operator against the item's value.

    Only positive operators reach here: ``_matches`` turns a negative one into
    its counterpart and inverts the result, so there is no ``.not`` branch in
    any of the four families below.
    """
    kind = attribute.type

    if kind == "tag":
        tags = _as_tags(have, attribute.name)
        if operator == "regex":
            return any(want.search(tag) for tag in tags)
        return any(tag.casefold() == want.casefold() for tag in tags)

    if kind == "str":
        text = have if isinstance(have, str) else None
        if text is None:
            raise TypeError(
                f"the view gave {have!r} for {attribute.name!r}, which is not a word or phrase"
            )
        if operator == "regex":
            return bool(want.search(text))
        low, wanted = text.casefold(), want.casefold()
        if operator == "contains":
            return wanted in low
        if operator == "is":
            return low == wanted
        if operator == "begins":
            return low.startswith(wanted)
        return low.endswith(wanted)

    if kind == "date":
        when = _as_moment(have, attribute.name)
        if operator == "eq":
            # "in the last N days": at or after (now - N days), with NO upper
            # bound, so a value in the FUTURE passes (SETTLED-BY-ORACLE). The
            # lower edge alone shipped first; a fix round then added an upper
            # bound at today, citing Kometa's comparison as
            # ``value < data or value > current_time``. That citation was a
            # recollection and it is wrong. Kometa's blank-modifier branch is
            # ``value < current_time - timedelta(days=data)`` and nothing else
            # (util.py:601-604) -- one-sided. The oracle found two future-dated
            # releases that Kometa keeps and the upper bound dropped, so the
            # bound is gone and the recollection is retracted: ``release: 30``
            # over an item Plex dates next March keeps it, in both systems.
            return when >= now - dt.timedelta(days=want)
        moment = now if isinstance(want, _Today) else _as_moment(want, attribute.name)
        if operator == "before":
            return when < moment
        return when > moment

    number = _as_number(have, attribute.name)
    if operator == "eq":
        return number == want
    if operator == "gt":
        return number > want
    if operator == "gte":
        return number >= want
    if operator == "lt":
        return number < want
    return number <= want


def _matches(predicate: FilterPredicate, view: ItemView, now: dt.datetime) -> bool:
    """One predicate against one item.

    The two rules that apply to every operator live here and only here, which
    is what makes them invariants rather than conventions:

    - **missing value**: for ``tag``/``str`` attributes, an item with no value
      is excluded by a positive filter and included by a negative one; for
      ``int``/``float``/``date``/``duration`` attributes it is excluded by
      EVERY operator, including ``.not``. See the module docstring for the
      transcription note on this split;
    - **a list means any-of**: the predicate holds if ANY written value
      matches, and a negative operator is the negation of that -- so
      ``genre.not: [Horror, Comedy]`` means "neither", not "not Horror".
    """
    attribute = predicate.attribute
    negative = predicate.operator in _NEGATES
    have = view.get(attribute.name)
    if _is_missing(have, attribute.type):
        if attribute.type in _MISSING_ALWAYS_EXCLUDES:
            return False
        return negative

    operator = predicate.operator
    if negative:
        operator = _NEGATES[operator] or attribute.default_operator
    matched = any(
        _matches_one(attribute, operator, have, want, now) for want in predicate.values
    )
    return not matched if negative else matched


def evaluate(
    node: "FilterGroup | FilterPredicate",
    view: ItemView,
    *,
    now: dt.datetime | dt.date | None = None,
) -> bool:
    """Does this item pass the filter?

    ``now`` is the run's MOMENT, which the relative date operators and the
    literal ``today`` measure against; it defaults to the current time so a
    caller with no run moment still gets the documented behaviour. It is a
    keyword so the signature the plan fixed -- ``evaluate(group, view) ->
    bool`` -- is the one that stays. A ``date`` is accepted and read as that
    day's midnight, which is what a caller that only has a run date means.

    It was called ``today`` and typed ``date`` until Task 4's oracle showed
    that Kometa compares at the moment rather than the calendar date
    (``current_time = datetime.now()``, builder.py:1165; see ``_as_moment``).
    The name changed with the type so a caller cannot keep passing a date
    while believing the old semantics.

    A view whose value has the wrong *type* raises rather than counting as
    missing: that is a bug in the accessor, and swallowing it would hide it
    behind a full, plausible, wrong collection. The engine stage contains the
    exception (Task 3); this layer's job is to be loud.
    """
    if now is None:
        when = dt.datetime.now()
    elif isinstance(now, dt.datetime):
        when = now
    else:
        when = dt.datetime(now.year, now.month, now.day)
    if isinstance(node, FilterPredicate):
        return _matches(node, view, when)
    results = (evaluate(child, view, now=when) for child in node.children)
    return any(results) if node.op == "any" else all(results)
