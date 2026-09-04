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
same as ``audience_rating: 8`` would.

ONE exception, copied from Kometa in sweep 2 (row 159): ``year``'s bare and
``.not`` forms route through Kometa's tag branch (``check_filter`` opens
``filter_attr != "year"``), so a missing year takes the tag half of the rule
-- ``year.not: 2000`` KEEPS an item with no year where ``year.gte: 2000``
drops it. Emergent upstream, matched here for parity, applied in ``_matches``.

This was originally shipped uniform
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

This table covers **33** of the 55 search names and **29** of the 70 filter
names. Both halves of the residue are real work, and they are different work:
the 45 unfiltered names are roadmap row 96's remainder (9a left 55 of them;
``plays``, ``last_played``, 10a's ``country``, phase B's four people rows and
search-tails-1's ``title``/``edition`` came in here as ``unprobed``, which is
a source tier and not an accessor, so the row-96 arithmetic has moved by nine
in total -- and then phase B's own probe gave ``plays`` and ``last_played``
real listing accessors and appended ``user_rating`` with one, so those three
are the first names counted here that an operator can actually filter on
rather than merely find in the table), while the 22 unsearched names are 9b's
own tail, filed per family for T6 -- search-tails-1 took seven of them (rows
170 + 172: the text pair, which also moved the filter-covered count by two,
and the five media booleans, which are search-only and move no filter number).
The two must not be reported as one number, which is what row 96's original
"~45" did.
"""
import datetime as dt
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from autoposter.lang import base_language_code

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
    "base_language_code",
    "batched_attributes",
    "evaluate",
    "parse_filters",
    "predicates",
    "resolve_search_values",
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
#
# Sub-phase C2c (roadmap row 100) added the fourth, and it is the first that
# describes no Plex read at all:
#
# - ``facts``: the value lives in this service's own ``item_facts`` row,
#   gathered from a provider. **REFUSAL-ONLY on the COLLECTIONS side** --
#   ``config/schema.py``'s ``filters:`` gate has its own ``why`` branch for
#   it, naming roadmap row 156 -- because making a facts-backed value
#   collection-filterable needs the sparsity story row 156 owns (an
#   ``item_facts`` column is NULL both for "the provider has nothing" and
#   for "this item has not been gathered yet", and a collection built on one
#   would silently shrink to whatever the facts layer has caught up with).
#   This tier NAMES that fence rather than opening it.
#
#   It is deliberately NOT ``unprobed``: that tier means "9a never probed
#   whether the Plex section listing carries it", and there is no Plex
#   listing that could ever carry a TMDb field, so claiming a missing probe
#   verdict would be a false statement rather than a cautious one. And not
#   ``tier2-deferred`` either: nothing is deferred, the value is held.
#
#   The OVERLAY side supplies these rows through
#   ``overlays/selection.py::OVERLAY_ATTRIBUTES``, which is a wholly separate
#   mechanism over a different view -- one that is handed the ``ItemFacts``
#   row directly. ``filter_values``'s ``SHIPPED_ATTRIBUTES`` /
#   ``BATCHED_ATTRIBUTES`` / ``DEFERRED_ATTRIBUTES`` are all tier-DERIVED, so
#   a ``facts`` row lands in none of them and needs no collections accessor;
#   ``PlexItemView.get`` falls through to its generic branch and refuses by
#   naming the tier, which is already the right sentence.
SOURCE_TIERS = (
    "listing", "probe", "tier2-batched", "tier2-deferred", "unprobed",
    "search-only", "facts",
)

# The operator vocabulary, per value type. These are internal names; the YAML
# spelling of each is ``.<name>`` except for the type's default, which is
# written as a bare key and has no modifier spelling at all -- see
# ``DEFAULT_OPERATOR``. Refusing ``genre.eq`` is deliberate: Kometa has no such
# modifier, and accepting a second spelling for the default is how a config
# ends up with two vocabularies.
OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    # `.count_gt/.count_gte/.count_lt/.count_lte` are Kometa's own modifier
    # class for tag attributes (`builder.py:4350`) -- "how many tags does
    # this item have", as opposed to "which". Added by roadmap row 100
    # sub-phase C2b, whose `language_count` family selects on
    # `audio_language.count_gte: 2`.
    #
    # ADJUDICATION A-1, and it REVERSES the phase-C recon's own A8
    # recommendation, which was two derived int attributes named
    # `audio_language_count`/`subtitle_language_count`. Those names are not
    # in Kometa's filter vocabulary at all, and `FilterAttribute`'s docstring
    # below makes `name` a promise: it is Kometa's own name and what an
    # operator writes in YAML. Inventing two would have made the 27-of-70
    # arithmetic in this module's docstring untrue and handed an operator a
    # spelling Kometa refuses. The cost of the exact-parity form is that
    # these four are legal on EVERY tag row, which is Kometa's own scoping;
    # the recon's objection to it ("collections would parse it and then have
    # no accessor") does not hold on this tree -- `audio_language` and
    # `subtitle_language` are `tier2-batched` with real accessors, and
    # `overlays/selection.py::OverlayItemView` supplies both from the
    # `MediaInfo` the badge pass already built.
    #
    # SEARCH-side: deliberately absent from `SEARCH_OPERATORS_BY_TYPE` below.
    # Kometa's count modifiers are a client-side filter mechanism; Plex is
    # never asked "how many audio languages", so a `plex_search` naming one
    # is refused by `_split_key` naming what the search half does take.
    "tag": ("eq", "not", "regex", "count_gt", "count_gte", "count_lt", "count_lte"),
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
    # ``.regex`` (roadmap row 178): a client-side vocabulary expansion, not a
    # regex Plex ever sees -- Kometa's own ``validate_attribute`` tests it
    # against the TAG list first (builder.py:4301), then the STRING list
    # (builder.py:4326), never against int/float/date/duration/bool. The
    # render layer (``search_url._arguments``) is what makes this safe to
    # ship under the SAME spelling ``filters:`` uses for a different
    # mechanism -- see its docstring for the divergence, documented
    # prominently there rather than merely in this comment.
    "tag": ("eq", "not", "regex"),
    "str": ("contains", "not", "is", "isnot", "begins", "ends", "regex"),
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
    # between us and Kometa for ``decade``. It is absent here (roadmap row
    # 178, now that ``.regex`` is a real search mechanism and not a global
    # refusal) because ``decade`` is transcribed as this table's ``int``
    # row, and ``SEARCH_OPERATORS_BY_TYPE`` only adds ``.regex`` to ``tag``
    # and ``str`` -- the same reason ``year`` still refuses it. Not a
    # per-row subtraction, so it needs no entry here either.
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
    # ``.regex`` renders as bare, positive key terms -- it expands to zero
    # modifier prefix regardless of type, because the expansion always
    # produces exact resolved KEYS (see search_url._arguments), never a
    # negatable server-side predicate. Kometa's own search regex has no
    # ``.not`` counterpart either.
    ("tag", "regex"): "",
    ("str", "contains"): "",
    ("str", "not"): "!",
    ("str", "is"): "%3D",
    ("str", "isnot"): "!%3D",
    ("str", "begins"): "%3C",
    ("str", "ends"): "%3E",
    ("str", "regex"): "",
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
    # A count comparison has no plexapi key: that table is per-VALUE
    # comparisons ("is this tag equal to X"), and "how many tags are there"
    # is not expressible in it. `None`, explicitly, keeps this table total
    # over OPERATORS_BY_TYPE -- which its own coverage test asserts -- rather
    # than leaving four holes a 9b translator would discover at run time.
    ("tag", "count_gt"): None,
    ("tag", "count_gte"): None,
    ("tag", "count_lt"): None,
    ("tag", "count_lte"): None,
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

# The `.count_*` modifiers' comparisons, keyed by operator name. A separate
# table rather than an if-chain inside `_matches` so that the operator list
# in OPERATORS_BY_TYPE["tag"] and the comparisons here cannot drift: a fifth
# spelling added to one and not the other is a KeyError on the first item
# rather than a silently-never-matching filter.
#
# None of the four is in `_NEGATES`: a count comparison is positive and has
# no negated spelling upstream, so `_matches` runs it directly. Kometa's own
# four are `builder.py:419`'s `tag_modifiers`, and its comparison is
# `util.is_number_filter` (`util.py:623-632`) after the list has been
# reduced to a length -- the same four relations, spelled as rejections.
_COUNT_COMPARISONS = {
    "count_gt": lambda have, want: have > want,
    "count_gte": lambda have, want: have >= want,
    "count_lt": lambda have, want: have < want,
    "count_lte": lambda have, want: have <= want,
}

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
# Thirty-seven rows: 9a's fifteen in the order the roadmap names them
# (roadmap.md:538-551), then 9b's four, 10a's two, phase B's five and
# search-tails-1's seven appended rather than interleaved so the first
# fifteen still read against the roadmap line they came from, plus C2a's
# ``versions`` (A14), C2b's ``aspect`` (A11) and C2c's ``tmdb_status`` and
# ``last_episode_aired`` (A-1/A-2) appended last. Column totals are asserted
# in tests/test_collection_filters.py as the transcription's checksum:
# 14 tag / 3 str / 4 int / 4 float / 4 date / 1 duration / 7 bool;
# 14 listing / 5 tier2-batched / 1 tier2-deferred / 7 unprobed /
# 8 search-only / 2 facts;
# 22 both-kinds / 12 movie-only / 3 show-only for ``kinds``, and
# 24 / 8 / 1 / 4 for ``search_kinds`` (the fourth bucket, ``()``, is the
# filterable-but-not-searchable one: ``versions``, ``aspect``,
# ``tmdb_status`` and ``last_episode_aired``, which land in neither kind),
# which is a different split and that is the point of the second column.
# NOTE, recorded rather than silently fixed: this sentence read "Thirty-four
# rows" and "24 / 8 / 1 / 1" until C2c, both of which stopped being true when
# C2b appended ``aspect`` -- the TEST beside it already asserted ``(): 2``.
# C2c corrects it while appending, and the arithmetic above is now the
# post-C2c truth.
#
# Phase B appended FIVE: the four PEOPLE rows, which move the ``tag`` and
# ``unprobed`` totals by four together -- ``actor`` on both kinds, and
# ``director``/``writer``/``producer`` movie-only in BOTH kind columns, which
# is why the two splits moved by different amounts -- and ``user_rating``
# (roadmap row 175), the third ``float`` and both-kinds in both columns.
#
# The source split is phase B's arithmetic, not 9a's, and phase B moved rows
# in two directions:
#
# - Task 2's probe left 9 listing / 6 tier2-deferred, and phase B moved five
#   of that six onto ``tier2-batched`` when the batched
#   ``/library/metadata/{k1,k2,...}`` read was measured returning the families
#   the listing had truncated or stripped. Only ``network`` stayed, and it
#   stayed for a reason no read can change -- see its row.
# - ``plays`` and ``last_played`` left ``unprobed`` for ``listing``, and
#   ``user_rating`` arrived there, when the phase-B probe (d) walked both
#   section listings and measured all three present-when-set. 9 + 3 = 12
#   listing; 7 - 2 = 5 unprobed. Three rows on one tier for three different
#   reasons -- absent-is-zero for ``plays``, absent-excludes for the other
#   two -- each argued on its own row rather than by the tier they share.
#
# Search-tails-1 appended SEVEN: rows 170 and 172. ``title`` and ``edition``
# are the second and third ``str`` rows, dual-vocabulary on ``unprobed`` (the
# ``country`` shape), moving ``str`` by two, ``unprobed`` by two and the
# filter-covered count by two. The five media booleans -- ``hdr``, ``dovi``,
# ``trash``, ``duplicate``, ``unmatched`` -- are ``search-only`` like
# ``decade`` (Kometa has no filter of any of these names), moving ``bool`` by
# five, ``search-only`` by five and no filter number at all. Every search
# cell is cited to tests/oracle/9b/kometa_build_filter.py by line in its own
# row's note, and oracle configs 18/19 pin every field-by-libtype render.
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
        "and `current_year-N`) are supported (roadmap row 171): a "
        "`_CurrentYear` sentinel, parsed at load and resolved against the "
        "run's own moment at match time -- the same deferred-resolution "
        "pattern `_Today` already has for dates. Subtraction, per Kometa's own "
        "transcription (tests/oracle/9b/kometa_build_filter.py:768-788): "
        "`current_year-5` means five years ago. "
        "Bare/`.not` missing-value routing follows Kometa's tag branch -- see "
        "_matches and roadmap row 159.",
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
        "display title (`English`). LOCATION-NAMES PHASE: the comparison "
        "folds BOTH sides through `base_language_code` (row 204's offline "
        "fold), so `audio_language: en`, `eng`, and a written base code "
        "against a regional stream tag all answer -- and a regional WRITTEN "
        "value matches at its base here, where `plex_search` targets an exact "
        "library value only, disclosed on purpose. `English` still matches "
        "nothing: the display-title seam is row 204's open half, and "
        "`iso_names.LANGUAGE_NAMES` is the table it awaits.",
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
        "-- folded at the base code exactly like `audio_language` above (row "
        "204), so `fi` and `fin` both answer; `Finnish` still matches nothing "
        "until row 204's display-title half.",
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
        "something added at 09:15 that day. ADJUDICATED sweep-2 (row 154, "
        "9b's D6 one module over): the runner-dependence is documented, not "
        "converted -- since the comparison moved to the moment (9a Task 4), a "
        "runner-clock offset shifts EVERY `added` comparison by that offset, "
        "not only ones near midnight; the Plex server's own zone (what an "
        "operator means) is not in the listing, and any fixed zone would make "
        "membership depend on where the pass ran. Prefer the relative forms "
        "(`added: 30`), day-granular and insensitive to any offset short of a "
        "day -- the same recommendation plex_search.py documents for search "
        "dates.",
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
        # Every non-regex operator this table ships takes the STRING branch,
        # and the value goes to Plex ``quote()``d and unresolved. ``.regex``
        # (roadmap row 178) is the exception -- it takes neither branch, but
        # its own vocabulary-expansion one in ``search_url._arguments``,
        # ahead of both -- because a pattern needs the library's enumerable
        # choices (``listFilterChoices``, plex.py:568) rather than either
        # branch's ordinary rendering. ``studio`` is this mechanism's own
        # worked example.
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
        "plays", "int", _BOTH, "listing",
        "Plex's `viewCount` -- how many times the item has been played by the "
        "account the token belongs to. In BOTH of Kometa's vocabularies: a "
        "search (plex.py:80, :547) and a filter (builder.py:280-293). Note "
        "that `viewCount` is PER-ACCOUNT: the answer depends "
        "on whose token the pass runs with, which is a property no other row "
        "in this table has. As a SEARCH it takes the four range modifiers and "
        "nothing else -- it is a number_attribute and not a year_attribute "
        "(plex.py:547, :599) -- so SEARCH_OPERATORS_EXCLUDED subtracts the "
        "bare form and `.not` that its `int` type otherwise offers. "
        "PHASE B: `unprobed` until the phase-B probe asked the question 9a "
        "never did, and now `listing`. Probe (d) walked both section listings "
        "(docs/research/plex-batch-probe/README.md): the attrib is present on "
        "79 of 1962 movies and 55 of 286 shows, and NEVER as `0` -- Plex omits "
        "it for an unwatched item rather than writing a zero. Sparse presence "
        "is safe here for a reason specific to this row: plexapi casts the "
        "attrib WITH A DEFAULT (`utils.cast(int, data.attrib.get('viewCount', "
        "0))`, video.py:64), so an absent attrib reads as 0 plays and never as "
        "missing -- which is the same value Kometa's own read produces, since "
        "Kometa reads it through the same plexapi. Absent-is-zero, not "
        "absent-is-missing; `plays.lt: 1` therefore MATCHES an unwatched item, "
        "here and upstream. Still per-account.",
        search_field="viewCount", show_search_field="show.viewCount",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "last_played", "date", _BOTH, "listing",
        "Plex's `lastViewedAt`. In both vocabularies, like `plays`. "
        "Per-account, like `plays`. As a "
        "search its bare and `.not` forms are RELATIVE WINDOWS -- "
        "`last_played.not: 6o` is \"not played in the last six months\", the "
        "shape a stale-media collection wants -- and roadmap row 154's "
        "timezone divergence applies to it in the opposite direction from "
        "`added`: a server-side date predicate evaluates in the PLEX SERVER's "
        "clock, not the runner's. "
        "PHASE B: `unprobed` until probe (d) measured it -- present on 98 of "
        "1962 movies and 64 of 286 shows "
        "(docs/research/plex-batch-probe/README.md), present-when-set, and "
        "present MORE often than `viewCount` because an in-progress item "
        "carries a last-viewed stamp before it ever completes a play (so "
        "neither attrib may be inferred from the other). Unlike `plays`, "
        "plexapi casts this one with NO default (`utils.toDatetime("
        "data.attrib.get('lastViewedAt'))`, video.py:50), so a never-played "
        "item reads MISSING -- and the `date` missing rule excludes a missing "
        "value under every operator, `.not` included. THAT IS UPSTREAM'S OWN "
        "BEHAVIOUR, which is why this ships rather than being re-filed: "
        "`last_played` is one of Kometa's three `date_filters` "
        "(builder.py:377-447), its `check_filter` branch is "
        "`if is_date_filter(getattr(item, 'lastViewedAt'), ...): return "
        "False`, and `is_date_filter` opens `if value is None: return True` "
        "(util.py:598-600) BEFORE it looks at the modifier. Kometa drops the "
        "never-played item from `last_played.not: 30` too. The verbatim "
        "transcription is in `.superpowers/oracle/9a/kometa_oracle.py`, and "
        "`tests/test_collection_filter_values.py` pins the behaviour. "
        "What an operator should take from it: this attribute means \"played, "
        "and when\", never \"never played\" -- for that, write `plays.lt: 1`, "
        "which the row above answers with a real zero.",
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
    # Roadmap row 175, filed long before this phase and cheap enough to ride
    # along with it. Appended last, after the people rows, for the reason
    # every phase has appended rather than interleaved.
    FilterAttribute(
        "user_rating", "float", _BOTH, "listing",
        "Plex's `userRating` -- the star rating belonging to WHOSE TOKEN THE "
        "PASS AUTHENTICATED AS, not the library's aggregate (roadmap row 175: "
        "the row's note has to say whose rating it is, or an operator reads "
        "'user rating' as the library's and gets their own). It is the third "
        "per-account row in this table, beside `plays` and `last_played`, and "
        "the only one of the three whose NAME invites the confusion -- "
        "`audience_rating` is the library's aggregate and lives one row up. "
        "Mechanically the `audience_rating` row again: a float with the five "
        "float modifiers, and in Kometa's `number_filters` "
        "(builder.py:377-447). "
        "PHASE B probe (d) verdict: present on 2 of 1962 movies and 0 of 286 "
        "shows (docs/research/plex-batch-probe/README.md) -- effectively "
        "ABSENT as a signal on that library, though present-when-set like the "
        "other two, so the accessor is sound and a filter naming it will "
        "simply find almost nothing there. Sparse presence ships honestly "
        "here because missing-excludes IS the right answer for an unrated "
        "item, and it is upstream's: Kometa's number branch reads `if "
        "test_number is None or is_number_filter(...): return False`, so an "
        "item the account has not rated fails the filter under every "
        "modifier, `.not` included -- exactly what the `float` missing rule "
        "does here.",
        search_field="userRating", show_search_field="show.userRating",
        search_kinds=_BOTH, filterable=True,
    ),
    # --- rows search-tails-1 added -------------------------------------------
    #
    # Rows 170 (``title``, ``edition``) and 172 (the five media booleans),
    # appended rather than interleaved like every batch before them. Every
    # SEARCH cell is cited to the repo's own verbatim transcription of
    # Kometa's tables -- tests/oracle/9b/kometa_build_filter.py, the fetched
    # artifact -- by LINE, and oracle configs 18/19 pin every field-by-libtype
    # render against the driver itself.
    FilterAttribute(
        "title", "str", _BOTH, "unprobed",
        "The item's own title -- a string_attribute upstream "
        "(kometa_build_filter.py:355, transcribing plex.py:507), so it takes "
        "the six string modifiers exactly as `studio` does: a bare "
        "`title: Dune` is a case-insensitive SUBSTRING match, not an exact "
        "one. No search_translation entry, so the movie field is the bare "
        "`title`; re-scoped to `show.title` on a show library by "
        "show_translation (kometa_build_filter.py:166). Dual-vocabulary like "
        "`country`: Kometa FILTERS on `title` too (`string_filters` -- the 9a "
        "oracle's verbatim transcription of builder.py:377-447 opens with "
        "it), so `filterable` is True -- and `unprobed` for the reason "
        "`country` carries that tier: 9a's probe never asked about a listing "
        "accessor for it, so a `filters:` block parses the key and refuses "
        "at the accessor by naming this tier. Kometa's separate `show_title` "
        "FILTER (season/episode level, one of the 44 filter-only names) is a "
        "different attribute and is NOT this row.",
        search_field="title", show_search_field="show.title",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "edition", "str", ("movie",), "unprobed",
        "Plex's edition title. The search field is `editionTitle` via "
        "search_translation (kometa_build_filter.py:88), then "
        "`show.editionTitle` on a show library via show_translation's entry "
        "for the TRANSLATED name (kometa_build_filter.py:182) -- the one row "
        "in the table that composes both tables, which is why oracle config "
        "19 pins the show render. Dual-LISTED in `searches` exactly like "
        "`studio` -- a string_attribute (:355) AND a tag_attribute (:421) -- "
        "and for `studio`'s own reason every non-regex operator this table "
        "ships takes the STRING branch: the value goes to Plex quoted and "
        "unresolved. `.regex` (roadmap row 178) takes neither branch, but "
        "the vocabulary-expansion one in `search_url._arguments`, checked "
        "ahead of both. "
        "Dual-VOCABULARY like `country` (`edition` is in Kometa's "
        "`string_filters`, the 9a oracle's transcription of "
        "builder.py:377-447), so `filterable` is True on `unprobed`. `kinds` "
        "is movie-only: an edition is a movie concept and upstream scopes "
        "the FILTER to movie libraries -- stated as INFERRED, not "
        "transcribed, because `builder.filters_by_type` still has no in-repo "
        "transcription (phase B review D11, unchanged here); the consequence "
        "either way is a refusal, since no accessor ships at this tier. The "
        "SEARCH column is the transcribed one and answers both library "
        "types.",
        search_field="editionTitle", show_search_field="show.editionTitle",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "hdr", "bool", _BOTH, "search-only",
        "High dynamic range, a server-side boolean: `hdr: true` emits "
        "`hdr=1`, `false` emits `hdr!=1` -- the `unplayed` row documents the "
        "bool rendering once for the whole type. In boolean_attributes "
        "(kometa_build_filter.py:359); no search_translation entry, so the "
        "movie field is the bare `hdr`; re-scoped to the EPISODE libtype on "
        "a show library (`episode.hdr`, kometa_build_filter.py:184) -- the "
        "same re-scoping `resolution` already does, because HDR is a "
        "property of the FILE and a show's files are its episodes'. "
        "`search-only`: Kometa has no `hdr` FILTER (row 96's 29-name "
        "search-only list), so a `filters:` block refuses it by naming the "
        "block it does belong to.",
        search_field="hdr", show_search_field="episode.hdr",
        search_kinds=_BOTH, filterable=False,
    ),
    FilterAttribute(
        "dovi", "bool", _BOTH, "search-only",
        "Dolby Vision. The one boolean with a search_translation entry, and "
        "it is the IDENTITY (kometa_build_filter.py:110: `dovi` -> `dovi`) -- "
        "transcribed as such rather than skipped; re-scoped to "
        "`episode.dovi` on a show library (kometa_build_filter.py:185). In "
        "boolean_attributes at :358. `search-only`: Kometa's client-side "
        "vocabulary has `has_dolby_vision` -- a DIFFERENT name, one of the "
        "44 filter-only ones -- and no `dovi` filter, so `filters:` refuses "
        "this spelling by pointing at the search block.",
        search_field="dovi", show_search_field="episode.dovi",
        search_kinds=_BOTH, filterable=False,
    ),
    FilterAttribute(
        "trash", "bool", _BOTH, "search-only",
        "Plex's trash flag: an item whose file has gone missing but has not "
        "yet been emptied from the library. In boolean_attributes "
        "(kometa_build_filter.py:364); no search_translation entry, so the "
        "movie field is the bare `trash`; re-scoped to `episode.trash` on a "
        "show library (kometa_build_filter.py:188) -- file-level, like "
        "`hdr`/`dovi`. `search-only`: Kometa has no filter of this name.",
        search_field="trash", show_search_field="episode.trash",
        search_kinds=_BOTH, filterable=False,
    ),
    FilterAttribute(
        "duplicate", "bool", ("movie",), "search-only",
        "Items carrying more than one media version. MOVIE-ONLY -- it is in "
        "movie_only_searches (kometa_build_filter.py:282), like `unplayed` "
        "-- so a show library refuses it BY NAME rather than being sent a "
        "query Plex answers with the wrong set; the show-side spelling is "
        "`episode_duplicate`, one of family E's twenty (roadmap row 173), "
        "deliberately not aliased. In boolean_attributes at :361. "
        "`search-only`: Kometa has no filter of this name (its `versions` "
        "filter counts media items and is one of the 44 filter-only "
        "names).",
        search_field="duplicate", show_search_field=None,
        search_kinds=("movie",), filterable=False,
    ),
    FilterAttribute(
        "unmatched", "bool", _BOTH, "search-only",
        "Items with no agent match. In boolean_attributes "
        "(kometa_build_filter.py:360); no search_translation entry, so the "
        "movie field is the bare `unmatched`; re-scoped to `show.unmatched` "
        "on a show library (kometa_build_filter.py:175) -- the SHOW level, "
        "not the episode's, unlike `hdr`/`dovi`/`trash`: a match belongs to "
        "the ITEM, not the file. `show_unmatched` and `episode_unmatched` "
        "remain family E's separate names (roadmap row 173), not aliases of "
        "this row. `search-only`: Kometa has no filter of this name.",
        search_field="unmatched", show_search_field="show.unmatched",
        search_kinds=_BOTH, filterable=False,
    ),
    FilterAttribute(
        "versions", "int", _BOTH, "listing",
        "How many `<Media>` versions the item carries -- Kometa's `versions` "
        "filter counts media items (one of the 44 filter names with no Plex "
        "search field; the SEARCH-side spelling is the separate `duplicate` "
        "row above, movie-only and unfilterable). `listing`: the section "
        "listing carries `<Media>` in full and un-truncated (9a's probe "
        "measured the per-item histogram {1: 1905, 2: 46, 3: 4}, recorded on "
        "the `resolution` row above), which is the same read that row "
        "already relies on. Adjudication A14: raised by C1's plan (the "
        "`versions` overlay family cannot select on `duplicate`, which is "
        "search-only), ruled here by sub-phase C2a -- the one row this "
        "sub-phase's own Global Constraint 2 permits, additive-only.",
        search_field=None, show_search_field=None,
        search_kinds=(), filterable=True,
    ),
    FilterAttribute(
        "aspect", "float", _BOTH, "listing",
        "The `<Media aspectRatio=...>` float (plexapi casts it, `Media."
        "_loadData`), which Kometa reads through the same attribute "
        "(`plex.py:200`) and puts in `float_attributes` (`builder.py:474`, "
        "`plex.float_attributes + ['aspect', 'tmdb_vote_average']`) -- so it "
        "is a CLIENT-SIDE `filters:` comparison and never a Plex search: the "
        "second of the 44 filter-only names to arrive under a table row, "
        "after `versions`. ADJUDICATION A11 (raised in the phase-C recon, "
        "ruled by sub-phase C2b). `listing`: the section listing carries "
        "`<Media>` in full and un-truncated (9a's probe, recorded on the "
        "`resolution` row above), which is the same read that row and "
        "`versions` already rely on. KINDS: both, and that is Kometa's own "
        "scoping rather than an inference -- `aspect` sits in "
        "`builder.py:278-308`'s `filters_by_type[\"movie_show_season_"
        "episode\"]`, and `filters` (`builder.py:350-356`) is built by "
        "substring test, so both `filters[\"movie\"]` and `filters[\"show\"]` "
        "carry it. A show has no `<Media>` and is excluded by the float "
        "missing-value rule anyway, so the column costs nothing either way; "
        "it is set to what upstream sets. MULTI-VERSION RULE (adjudication A-2): "
        "a `float` cannot answer a tuple the way `resolution`'s `tag` type "
        "does, so `filter_values._aspect` walks every `<Media>` child in "
        "listing order -- the SAME selection `_resolutions` makes -- and "
        "answers the FIRST that carries the attrib; the production "
        "histogram on the `resolution` row ({1: 1905, 2: 46, 3: 4}) is why "
        "that is the single-version answer for almost every item. CAVEAT an "
        "operator needs: Kometa's own aspect bands are open at BOTH ends "
        "(`.gt`/`.lt`, never `.gte`/`.lte`), so a 1.90 aspect matches none "
        "of them -- transcribed, not corrected. An item Plex has not "
        "analysed carries no `aspectRatio` at all and the `float` "
        "missing-value rule excludes it under every operator, `.not` "
        "included, which is what keeps an unanalysed file un-badged rather "
        "than wrongly badged.",
        search_field=None, show_search_field=None,
        search_kinds=(), filterable=True,
    ),
    FilterAttribute(
        "tmdb_status", "tag", ("show",), "facts",
        "TMDb's own `status` for the show, as KOMETA'S TOKEN rather than as "
        "TMDb's string: `discover_status` maps the six TMDb spellings onto "
        "`returning`/`planned`/`production`/`ended`/`canceled`/`pilot` "
        "(`/modules/tmdb.py:108` in the pinned v2.4.8 image), Kometa "
        "validates a written value against exactly those six as a "
        "`commalist` (`/modules/builder.py:4369`) and then tests membership "
        "of the written set outright -- `(modifier == '' and check_value not "
        "in filter_data)` (`/modules/tmdb.py:685-693`). EXACT-SET "
        "MEMBERSHIP, never substring, which is why this is a `tag` row and "
        "not a `str` one: `str` defaults to CONTAINS, and that would make "
        "`tmdb_status: end` match every ended show. `content_rating` is the "
        "same shape one attribute along -- a `tag` filter over a single "
        "string, read as a one-element list by `_as_tags`. SHOW-ONLY: "
        "`filters_by_type[\"show\"]` carries it (`/modules/builder.py:334-345`) "
        "and no other libtype does. TMDB, NEVER TVDB -- it is in "
        "`tmdb_filters` (`builder.py:371`) and `tvdb_status` is a SEPARATE "
        "name in `tvdb_filters` (`:375`) this service does not ship; that is "
        "roadmap row 100's A12 correction, re-confirmed here by direct read. "
        "`facts`: the value is `ItemFacts.tmdb_status`, written by "
        "`facts/tmdb_facts.py::parse_show_facts` off the same `/tv/{id}` "
        "payload the facts gather already fetches (zero new HTTP), and "
        "MAPPED TO THE TOKEN AT THE PARSER so this column and a written "
        "filter value share one value space -- the condition row 156 set. A "
        "collection naming this row is refused at load; an overlay "
        "`condition:` reads it through `overlays/selection.py::"
        "OverlayItemView`. Row 100 sub-phase C2c, adjudication A-2. A row "
        "whose column is still NULL -- every row in the library until its "
        "next facts refresh, adjudication A-4 -- is excluded by the tag "
        "missing-value rule under every positive operator, which matches "
        "Kometa excluding an item it has no TMDb object for "
        "(`/modules/builder.py:4700-4714`). GAP, DECLARED: the same rule "
        "negates under `.not`, so `tmdb_status.not: ended` matches every "
        "row whose column is still NULL -- the whole library until its "
        "next facts refresh -- where Kometa excludes that item "
        "regardless of modifier. DIVERGENCE, DECLARED: a status "
        "TMDb spells outside the six is a bare `KeyError` upstream "
        "(`/modules/tmdb.py:685`); here it is stored verbatim and matches "
        "nothing, which is the same drawn outcome without the crash. THREE "
        "OF THE SIX TOKENS DRAW NOTHING upstream and nothing here: "
        "`status.yml` ships bands for `returning`, `canceled` and `ended` "
        "only.",
        search_field=None, show_search_field=None,
        search_kinds=(), filterable=True,
    ),
    FilterAttribute(
        "last_episode_aired", "date", ("show",), "facts",
        "TMDb's `last_air_date` -- when the show's most recent episode aired "
        "(`/modules/tmdb.py:699`, `tmdb_date = item.last_air_date`, compared "
        "through `util.is_date_filter` at `:705`). DELIBERATELY NOT the "
        "`release`/`originally_available` value, which for a show is TMDb's "
        "`first_air_date`: the two are opposite ends of the same show, and "
        "conflating them would badge a long-ended series as AIRING for the "
        "fortnight after its premiere anniversary. SHOW-ONLY "
        "(`/modules/builder.py:334-345`), TMDb (`tmdb_filters`, `:369`). THE "
        "BARE FORM IS A WINDOW IN DAYS: `last_episode_aired: 14` means "
        "'aired in the last 14 days', confirmed BOTH ways in the pinned "
        "image and closing the datasources probe's open item 6 -- "
        "`util.is_date_filter`'s blank-modifier branch is `value < "
        "current_time - timedelta(days=data)`, rejecting what is older "
        "(`/modules/util.py:601-604`), and the search half renders the same "
        "window as `>>=-14d`, 'is in the last' "
        "(`/modules/builder.py:4222-4233`). `_as_days` already reads it "
        "identically. A show with no `last_air_date` is EXCLUDED under every "
        "operator: `is_date_filter(None, ...)` returns True, which is a "
        "rejection (`/modules/util.py:598-600`), and this table's own "
        "`_MISSING_ALWAYS_EXCLUDES` reaches the same verdict for a `date` "
        "row -- which is also the state of every row in the library on the "
        "upgrade that ships this. `facts`: `ItemFacts.last_episode_aired`; "
        "see the `tmdb_status` row above for what that tier means and why a "
        "collection is refused. A DECLARED DIVERGENCE FROM UPSTREAM'S "
        "`airing` BAND (adjudication A-1): `status.yml:61-63` selects AIRING "
        "with `plex_search: {any: {episode_air_date: 14}}` -- a server-side "
        "search over the LIBRARY's episode rows -- and `episode_air_date` is "
        "absent from `filters_by_type` entirely "
        "(`/modules/builder.py:278-350`), so no `filters:` block in EITHER "
        "system can name it; this is `duplicate`/`versions` (A14) verbatim. "
        "This row asks the question Kometa's own filter vocabulary does "
        "supply -- 'did the SHOW air an episode in the last N days, per "
        "TMDb' -- where upstream's search asks 'does the LIBRARY hold one'. "
        "They differ when the library lags broadcast (upstream draws "
        "nothing, this draws AIRING) or when Plex's own episode dates are "
        "wrong. `last_episode_aired_or_never`, whose null-KEEPS-the-item "
        "semantic (`/modules/tmdb.py:700-702`) is the interesting half, is a "
        "SEPARATE Kometa name and is deliberately not shipped.",
        search_field=None, show_search_field=None,
        search_kinds=(), filterable=True,
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
class _CurrentYear:
    """Kometa's ``current_year``/``current_year-N``, resolved against the
    run's MOMENT rather than the parse's -- the same deferred-resolution
    shape ``_Today`` already has for dates. Kometa's own transcription
    (``tests/oracle/9b/kometa_build_filter.py:768-788``, vendored for the 9b
    oracle rather than cited from Kometa's source tree, which is not in this
    repo) computes ``datetime.now().year - int(offset)`` -- subtraction, so
    ``current_year-5`` means five years ago, never five years from now.
    """

    offset: int = 0


_CURRENT_YEAR = re.compile(r"^current_year(?:-(\d+))?$")


def _as_current_year(value: object, field: str) -> "_CurrentYear | None":
    """``current_year`` or ``current_year-N``, or None for any value that is
    not this spelling -- the caller falls through to ``_as_int`` for an
    ordinary year number.

    Case-insensitive, like ``today`` (``_as_date``, above) -- a deliberate
    divergence from Kometa's own case-sensitive ``str(value).
    startswith("current_year")``, in the direction this module already chose
    for its one other sentinel word.

    No whitespace tolerance around the dash (``current_year - 5`` refuses,
    where Kometa's own parser would accept it via a ``.strip()`` on the
    split-off suffix) -- a deliberate narrowing: this module normalises the
    whole written string once, as every other string-form value here does,
    and no other grammar in this file tolerates internal whitespace either.
    A refusal here falls through to ``_as_int``'s "not a whole number"
    message, which is accurate: what is left is not a plain int and not this
    sentinel's spelling either.
    """
    if not isinstance(value, str):
        return None
    match = _CURRENT_YEAR.match(value.strip().lower())
    if not match:
        return None
    suffix = match.group(1)
    return _CurrentYear(offset=int(suffix) if suffix else 0)


def _resolve_search_value(value: object, now: dt.datetime) -> object:
    if isinstance(value, _CurrentYear):
        return now.year - value.offset
    if isinstance(value, _Today):
        return now.date()
    return value


def _resolve_predicate(predicate: FilterPredicate, now: dt.datetime) -> FilterPredicate:
    resolved = tuple(_resolve_search_value(value, now) for value in predicate.values)
    return predicate if resolved == predicate.values else replace(predicate, values=resolved)


def resolve_search_values(group: FilterGroup, *, now: dt.datetime) -> FilterGroup:
    """``_CurrentYear``/``_Today``, resolved against ``now`` -- the same
    deferred-resolution shape both sentinels already have for ``filters:``
    (``evaluate``/``_matches_one`` resolve them at compare time), applied
    here for a ``plex_search``, which has no compare step: it has to send
    Plex a concrete value.

    ``search_url.build_search_url`` is documented PURE -- no clock -- because
    that purity is what lets ``tests/test_collection_search_oracle.py``
    compare its output byte for byte against Kometa's own ``build_filter``
    with no dependence on when the suite runs. Resolving the sentinels HERE,
    before that call, is what keeps both true at once: an operator can still
    write ``year: current_year`` or ``release.after: today`` in a
    ``plex_search:`` block, and ``build_search_url`` itself never reads a
    clock. Without this step, a ``_CurrentYear``/``_Today`` value would reach
    ``search_url._arguments``' plain ``str(value)`` (int/float) or
    ``value.isoformat()`` (date) branches unresolved -- a query string
    carrying the sentinel's own ``repr()``, or an ``AttributeError``, rather
    than the year or date an operator meant. Neither sentinel has a case in
    ``search_url.py`` itself, on purpose: the resolution belongs where the
    two classes are defined, once, not duplicated at every render branch that
    might see one.

    Returns a new tree only where something actually changed -- most groups
    carry no relative value at all, and reusing the input avoids rebuilding a
    frozen-dataclass tree for nothing.
    """
    children = tuple(
        resolve_search_values(child, now=now) if isinstance(child, FilterGroup)
        else _resolve_predicate(child, now)
        for child in group.children
    )
    return group if children == group.children else replace(group, children=children)


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
    if operator in _COUNT_COMPARISONS:
        # ``.count_*`` asks HOW MANY tags the item has, so the written value
        # is a number even though every row carrying these operators is a
        # ``tag``. Same shape as ``.rated`` above: the operator decides the
        # value's type, not the row.
        return _as_int(value, field)
    if attribute.type == "bool":
        return _as_bool(value, field)
    if attribute.type in ("tag", "str"):
        return _as_text(value, field)
    if attribute.type == "int":
        if attribute.name == "year":
            current = _as_current_year(value, field)
            if current is not None:
                return current
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
    # The FIRST of the two branches is reachable now, for real: ``versions``
    # (C2a, A14) is filterable but has no Plex search field, the first of the
    # 44 filter-only attributes (``height``, ``summary``, ``filepath``, ... --
    # roadmap row 96's residue) to arrive under a table row rather than a bare
    # KeyError. It was written and tested with a synthetic row since
    # search-tails-1, before any real row reached it; ``versions`` is now that
    # real row, and ``aspect`` (C2b, A11) has since become the second.
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
            # A ``bool`` row as a SEARCH is what reaches this branch since
            # roadmap row 178 added ``.regex`` to every ``tag``/``str`` row's
            # operator set: ``resolution`` (tag) used to be the row this
            # existed for, but it now takes ``.regex`` too and is no longer
            # bare-only. A ``bool`` row's whole operator set is still just the
            # bare form, so the list of writable modifiers is empty and
            # "it takes " would render as a dangling phrase followed by a
            # parenthesis.
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
    pre-existing runner-dependence (roadmap row 154). Row 154 closed on exactly
    that posture (sweep 2): documented on the `added` row and here, per 9b's D6
    -- document the divergence, do not reconcile.
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


# The two stream-language attributes compare at the BASE ISO 639-1 code --
# roadmap row 204's offline fold. Both sides go through
# ``base_language_code``, so `audio_language: eng` meets a stream tagged
# `en`, and a written base code (`pt`) meets a regional stream tag
# (`pt-BR`) -- the row's two spellings -- with zero Plex reads. One
# judgement, disclosed on both rows' notes and pinned by test: a REGIONAL
# written value (`es-419`) also matches at its base here, where
# `plex_search` targets an exact library value only. A display TITLE
# (`English`) still matches nothing: langcodes cannot reduce it, the
# fallback returns it unchanged, and the name->code seam is row 204's
# still-open half (`iso_names.LANGUAGE_NAMES` is the table it awaits).
_LANGUAGE_FOLD_ATTRIBUTES = frozenset({"audio_language", "subtitle_language"})


# ``base_language_code`` moved to ``autoposter/lang.py`` and is imported at the
# top of this module. It is re-exported here (it is in ``__all__``) so every
# existing importer is unchanged; it moved because ``badges/values.py``'s flag
# badge needs the same reduction and ``badges/`` must not import this module.


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
        if attribute.name in _LANGUAGE_FOLD_ATTRIBUTES:
            wanted = base_language_code(want.casefold())
            return any(
                base_language_code(tag.casefold()) == wanted for tag in tags
            )
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
    target = now.year - want.offset if isinstance(want, _CurrentYear) else want
    if operator == "eq":
        return number == target
    if operator == "gt":
        return number > target
    if operator == "gte":
        return number >= target
    if operator == "lt":
        return number < target
    return number <= target


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
    if predicate.operator in _COUNT_COMPARISONS:
        # ``.count_*`` asks HOW MANY, and Kometa answers it with a NUMBER
        # derived before any missing-value test runs
        # (``modules/plex.py:2931-2932`` at the pinned digest):
        #
        #     test_number = len(test_number) if test_number else 0
        #     modifier = f".{modifier[7:]}"
        #
        # after which ``.count_lt: 3`` is an ordinary ``.lt`` over that
        # number (``plex.py:2934`` -> ``util.is_number_filter``,
        # ``util.py:623-632``). So a stream-less item counts as ZERO and is
        # KEPT by ``audio_language.count_lt: 3`` rather than dropped by the
        # tag missing-value rule -- which is why this branch is HERE, above
        # ``_is_missing``, and not in ``_matches_one``, which is only ever
        # reached after that rule has already had its say. Transcribed, not
        # invented; the pin in tests/test_collection_filters.py carries the
        # citation.
        #
        # ``len(_as_tags(...))`` counts the ENTRIES the view answered,
        # duplicates included -- Kometa's own value is an ``extend``-ed list
        # of every stream across every ``<Media>`` (``plex.py:2915-2922``)
        # with no dedupe anywhere in the path. A bare string counts as one,
        # via ``_as_tags``, no special case.
        #
        # ``if have else 0`` mirrors upstream's own truthiness test exactly:
        # ``None``, ``()`` and ``""`` are all zero, and nothing else is.
        count = len(_as_tags(have, attribute.name)) if have else 0
        return any(
            _COUNT_COMPARISONS[predicate.operator](count, want)
            for want in predicate.values
        )
    if _is_missing(have, attribute.type):
        # ``year``'s bare/``.not`` forms take the TAG half of the rule
        # (SETTLED against the fetched transcription, roadmap row 159):
        # Kometa's ``check_filter`` routes them through its set-intersection
        # branch -- the condition opens ``filter_attr != "year"``
        # (.superpowers/oracle/9a/kometa_oracle.py:190, transcribing
        # modules/plex.py:2895) -- so a missing year is dropped by ``year:``
        # and KEPT by ``year.not:``, while the four range modifiers stay on
        # the number branch and its unconditional exclusion. The split is
        # emergent (it exists so ``year: [1990, 1991]`` works as membership),
        # which is why 9a did not copy it; it is copied now because parity on
        # what ships beats a tidier rule nobody upstream applies.
        if attribute.name == "year" and predicate.operator in ("eq", "not"):
            return negative
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
