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
"""
import datetime as dt
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "BY_NAME",
    "DEFAULT_OPERATOR",
    "FILTER_ATTRIBUTES",
    "ITEM_KINDS",
    "OPERATORS_BY_TYPE",
    "PLEXAPI_EQUIVALENT",
    "SOURCE_TIERS",
    "VALUE_TYPES",
    "FilterAttribute",
    "FilterGroup",
    "FilterPredicate",
    "ItemView",
    "evaluate",
    "parse_filters",
    "predicates",
]

# The four categorical columns, as closed sets. A row outside them would parse,
# load, and match nothing.
VALUE_TYPES = ("tag", "str", "int", "float", "date", "duration")
ITEM_KINDS = ("movie", "show")

# Where a row's data comes from. ``probe`` was Task 2's question, not a shrug:
# Plex's listing endpoint carries some child elements and not others depending
# on the server and the request, so a row marked ``probe`` becomes ``listing``
# (ships scan-free) or ``tier2-deferred`` (drops out of tier 1 rather than
# shipping a silent request-per-item) once the read-only probe answers. Task 2's
# probe (2026-08-25) answered all seven of tier 1's, so no row carries ``probe``
# today; the tier stays in the vocabulary for the tier-2 rows 9b will add.
SOURCE_TIERS = ("listing", "probe", "tier2-deferred")

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
# REVIEW against Kometa's number/date filter, which excludes a ``None`` value
# unconditionally. ``tag``/``str`` are deliberately absent -- their rule still
# depends on the operator (positive excludes, negative includes). See the
# module docstring.
_MISSING_ALWAYS_EXCLUDES = ("int", "float", "date", "duration")


@dataclass(frozen=True)
class FilterAttribute:
    """One row of the table.

    ``name`` is Kometa's own name for the attribute and is what an operator
    writes in YAML; where it differs from Plex's wire name (``release`` for
    ``originallyAvailableAt``, ``critic_rating`` for ``rating``) the row's note
    says so, because that difference is the reason the row exists rather than a
    passthrough. ``kinds`` is which library the attribute means anything for.
    ``source`` is one of ``SOURCE_TIERS``. ``note`` is required by the table's
    own test: a row with nothing to say about itself is a row nobody checked.
    """

    name: str
    type: str
    kinds: tuple[str, ...]
    source: str
    note: str

    @property
    def operators(self) -> tuple[str, ...]:
        """Derived, not stored. The operators are a property of the value type,
        so storing them per row would be a duplicate of ``OPERATORS_BY_TYPE``
        and the duplicate is the thing that rots."""
        return OPERATORS_BY_TYPE[self.type]

    @property
    def default_operator(self) -> str:
        return DEFAULT_OPERATOR[self.type]


_BOTH = ("movie", "show")

# --- THE TABLE ---------------------------------------------------------------
#
# Fifteen tier-1 rows, in the order the roadmap names them (roadmap.md:538-551),
# transcribed from Kometa's documented filter semantics. Column totals are
# asserted in tests/test_collection_filters.py as the transcription's checksum:
# 8 tag / 1 str / 1 int / 2 float / 2 date / 1 duration; 9 listing /
# 6 tier2-deferred (Task 2's probe moved the seven ``probe`` rows: resolution
# in, the other six out); 11 both-kinds / 3 movie-only / 1 show-only.
#
# THE PROBE, in one paragraph, because six of these rows are now a refusal and
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
        "genre", "tag", _BOTH, "tier2-deferred",
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
        "rather than ship a filter that silently drops a third of its matches.",
    ),
    FilterAttribute(
        "year", "int", _BOTH, "listing",
        "The listing attrib `year`. Kometa's special year words (`current_year` "
        "and its offsets) are NOT tier 1, so `year: current_year` refuses at "
        "load naming the field rather than parsing as something else.",
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
    ),
    FilterAttribute(
        "audience_rating", "float", _BOTH, "listing",
        "The listing attrib `audienceRating` (video.py:390), Plex's 0-10 "
        "audience score -- NOT `userRating`, which is the logged-in account's "
        "own star rating and is a separate Kometa filter (`user_rating`) left "
        "out of tier 1.",
    ),
    FilterAttribute(
        "critic_rating", "float", _BOTH, "listing",
        "The listing attrib `rating` (video.py:401). Kometa calls Plex's "
        "unqualified `rating` field the CRITIC rating, which is why this row "
        "exists rather than a passthrough: an operator writing `rating:` would "
        "be writing a Kometa attribute that does not exist.",
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
    ),
    FilterAttribute(
        "audio_language", "tag", ("movie",), "tier2-deferred",
        "Stream-level: the languages of the item's audio streams, under "
        "`<Media><Part><Stream>`. PROBE VERDICT: DEFERRED, exactly as this note "
        "predicted -- streams are a level below the Media children the listing "
        "carries, and it stops at Part. Zero `<Stream>` elements across 200 "
        "listing movies; the same movie via /library/metadata has four. Note "
        "that plexapi's `Movie`/`Show` also expose an `audioLanguage` ATTRIB, "
        "which is the item's preferred-audio SETTING and not the languages its "
        "files contain -- reading that would be a same-name-different-filter "
        "bug of the kind the item_facts adjudication already ruled out.",
    ),
    FilterAttribute(
        "subtitle_language", "tag", ("movie",), "tier2-deferred",
        "Stream-level, like `audio_language`, and DEFERRED with it on the same "
        "probe data (no `<Stream>` element reaches the listing at all). The "
        "`subtitleLanguage` attrib is the same trap as `audioLanguage`.",
    ),
    FilterAttribute(
        "label", "tag", _BOTH, "tier2-deferred",
        "Plex's `<Label>` child element -> the `labels` cached property "
        "(video.py:441). PROBE VERDICT: DEFERRED -- the listing strips the "
        "family outright. Zero `<Label>` children across all 1955 movies and all "
        "284 shows, while the metadata endpoint carried a label for all 30 "
        "sampled movies and for 19 of 20 sampled shows (`Overlay`, this "
        "server's Kometa-era marker). This settles what reconcile.py:70-83's "
        "deliberate per-collection reload was already evidence for.",
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
    ),
    FilterAttribute(
        "release", "date", _BOTH, "listing",
        "Kometa's name for Plex's `originallyAvailableAt` (video.py:398). The "
        "row's name is Kometa's, not Plex's, because the config is Kometa-"
        "shaped; the plan calls the attribute `release (originally_available)` "
        "for the same reason.",
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
    ),
    FilterAttribute(
        "studio", "str", _BOTH, "listing",
        "The listing attrib `studio` (video.py:405). The ONE tier-1 string "
        "attribute, and the distinction matters: a bare `studio: Warner` is a "
        "case-insensitive SUBSTRING match, where a bare `genre: Horror` is an "
        "exact tag match. SETTLED-IN-FAVOR (fix-round review) -- this "
        "transcription files `studio` under Kometa's string filters (which "
        "carry .is/.isnot/.begins/.ends) rather than its tag filters, and that "
        "is the single most consequential tag-vs-string call in the table: had "
        "Kometa treated it as a tag, `studio: Warner` would match nothing at "
        "all instead of everything Warner.",
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
        "Filed for tier 2 -- see the Task 2 report's recommendation.",
    ),
    FilterAttribute(
        "collection", "tag", _BOTH, "tier2-deferred",
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
        "the result set, changing what the listing returns.",
    ),
)

BY_NAME: dict[str, FilterAttribute] = {row.name: row for row in FILTER_ATTRIBUTES}


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
    """``all`` (every child must match) or ``any`` (one child must match)."""

    op: str
    children: tuple["FilterGroup | FilterPredicate", ...]
    field: str


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


def _parse_value(attribute: FilterAttribute, operator: str, value: object, field: str) -> object:
    if operator == "regex":
        return _as_regex(value, field)
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
        return _as_days(value, field)
    return _as_date(value, field)


def _split_key(key: str, field: str) -> tuple[FilterAttribute, str]:
    name, _, modifier = key.partition(".")
    attribute = BY_NAME.get(name)
    if attribute is None:
        raise ValueError(
            f"unknown filter attribute {name!r} at {field}: tier-1 attributes are "
            + ", ".join(sorted(BY_NAME))
        )
    if not modifier:
        return attribute, attribute.default_operator
    writable = [f".{op}" for op in attribute.operators if op != attribute.default_operator]
    if modifier not in attribute.operators or modifier == attribute.default_operator:
        # The bare form's meaning is not literally its "default operator" name
        # for a date -- ``added: 30`` is a window in days, not "added eq 30" --
        # so saying "which means eq" here would teach the wrong thing about
        # what a bare key does.
        bare_meaning = "within-the-last-N-days" if attribute.type == "date" else attribute.default_operator
        message = (
            f"{field}: .{modifier} does not apply to {name!r}, a {attribute.type} attribute "
            "-- it takes " + ", ".join(writable)
            + f" (or no modifier at all, which means {bare_meaning})"
        )
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
        raise ValueError(message)
    return attribute, modifier


def _parse_predicate(key: str, raw: object, field: str) -> FilterPredicate:
    attribute, operator = _split_key(key, field)
    written = raw if isinstance(raw, (list, tuple)) else [raw]
    if not written:
        raise ValueError(f"{field}: an empty list matches nothing -- remove the key instead")
    values = tuple(_parse_value(attribute, operator, one, field) for one in written)
    return FilterPredicate(attribute=attribute, operator=operator, values=values, field=field)


def _parse_block(raw: object, op: str, field: str) -> FilterGroup:
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
            children.append(_parse_nested(value, key, f"{field}.{key}"))
        else:
            children.append(_parse_predicate(key, value, f"{field}.{key}"))
    return FilterGroup(op=op, children=tuple(children), field=field)


def _parse_nested(raw: object, op: str, field: str) -> FilterGroup:
    """An ``any:``/``all:`` value, in either accepted shape.

    A **mapping** makes each of its keys one alternative -- ``any: {genre:
    Horror, label: keep}`` is "horror or labelled keep". A **list** makes each
    element a block whose own keys are ANDed, which is what an alternative
    needs when it is more than one attribute wide.
    """
    if isinstance(raw, Mapping):
        return _parse_block(raw, op, field)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        if not raw:
            raise ValueError(f"{field} is empty -- remove it, or give it a block to filter on")
        blocks = tuple(
            _parse_block(one, "all", f"{field}[{index}]") for index, one in enumerate(raw)
        )
        return FilterGroup(op=op, children=blocks, field=field)
    raise ValueError(f"{field}: expects a mapping of attributes or a list of them, not {raw!r}")


def parse_filters(raw: object, *, field: str = "filters") -> FilterGroup:
    """Parse a ``filters:`` block, or refuse naming the field that is wrong.

    Every refusal is a ``ValueError`` whose message starts with (or contains)
    the dotted path of the offending key, because the config layer wraps it
    with the definition's title and an operator with twenty definitions needs
    both halves to fix one. The top-level block is an ``all``: several keys in
    one mapping must all match, which is Kometa's rule.
    """
    return _parse_block(raw, "all", field)


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
