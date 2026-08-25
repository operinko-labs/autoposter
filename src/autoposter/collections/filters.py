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
reasoning on the row, and the ones this transcription is **not** confident about
are marked ``UNVERIFIED-TRANSCRIPTION`` in the note -- Task 4's Kometa oracle
adjudicates those against Kometa's own code. A note without that marker is a
claim this module is standing behind.

**The missing-value rule, which is an invariant and not a per-operator detail.**
An item that has no value for an attribute is EXCLUDED by a positive filter and
INCLUDED by a negative one (``.not``, ``.isnot``). So ``genre: Horror`` drops an
item with no genres at all, and ``genre.not: Horror`` keeps it. This is applied
in exactly one place, ``_matches`` below, so that every operator gets it and no
operator can quietly opt out. ``UNVERIFIED-TRANSCRIPTION``: the rule is
Kometa's for tag attributes, where its implementation intersects the item's tags
with the filter's and the empty intersection falls out correctly for both
modifiers. For *numbers and dates* Kometa may instead short-circuit a missing
value to "excluded" regardless of modifier, which would make
``year.not: 1999`` drop an item with no year rather than keep it. Uniformity is
chosen here because a rule with a per-type exception is a rule nobody can
remember; the oracle decides whether Kometa agrees.

**What tier 1 deliberately does not have**, so that each is a refusal naming the
field rather than a silent gap: Kometa's tag ``.count_gt``/``.count_gte``/
``.count_lt``/``.count_lte`` modifiers, its ``.regex`` on *date* attributes,
its relative-date spellings beyond the bare ``today``, and its special ``year``
words (``current_year`` and its offsets). Each is a tier-2 row, not a bug.
"""
import datetime as dt
import re
from collections.abc import Mapping, Sequence
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
]

# The four categorical columns, as closed sets. A row outside them would parse,
# load, and match nothing.
VALUE_TYPES = ("tag", "str", "int", "float", "date", "duration")
ITEM_KINDS = ("movie", "show")

# Where a row's data comes from. ``probe`` is Task 2's question, not a shrug:
# Plex's listing endpoint carries some child elements and not others depending
# on the server and the request, so a row marked ``probe`` becomes ``listing``
# (ships scan-free) or ``tier2-deferred`` (drops out of tier 1 rather than
# shipping a silent request-per-item) once the read-only probe answers.
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
    # ``.before``/``.after`` are Kometa's spellings and are STRICT. ``.gte``/
    # ``.lte`` are the inclusive-boundary forms Kometa has no spelling for,
    # added because a range like "released on or after 2000-01-01" is otherwise
    # unwritable. ``.gt``/``.lt`` are deliberately absent: they would be second
    # spellings of ``.after``/``.before``.
    "date": ("eq", "not", "before", "after", "gte", "lte"),
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
# value", and the negation is ours, not plexapi's. The two ``None`` entries are
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
    ("int", "not"): "ne",
    ("int", "gt"): "gt",
    ("int", "gte"): "gte",
    ("int", "lt"): "lt",
    ("int", "lte"): "lte",
    ("float", "eq"): "exact",
    ("float", "not"): "ne",
    ("float", "gt"): "gt",
    ("float", "gte"): "gte",
    ("float", "lt"): "lt",
    ("float", "lte"): "lte",
    ("duration", "eq"): "exact",
    ("duration", "not"): "ne",
    ("duration", "gt"): "gt",
    ("duration", "gte"): "gte",
    ("duration", "lt"): "lt",
    ("duration", "lte"): "lte",
    ("date", "eq"): None,
    ("date", "not"): None,
    ("date", "before"): "lt",
    ("date", "after"): "gt",
    ("date", "gte"): "gte",
    ("date", "lte"): "lte",
}

# The negative operators, and the positive one each negates. Every negative is
# evaluated by running its positive counterpart and inverting -- which is what
# makes ``.not`` mean "matches NONE of the given values" rather than "does not
# match the first one", and what makes the missing-value rule fall out of one
# branch instead of one per operator.
_NEGATES = {"not": None, "isnot": "is"}


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
# 8 tag / 1 str / 1 int / 2 float / 2 date / 1 duration; 8 listing / 7 probe;
# 11 both-kinds / 3 movie-only / 1 show-only.
FILTER_ATTRIBUTES: tuple[FilterAttribute, ...] = (
    FilterAttribute(
        "genre", "tag", _BOTH, "probe",
        "Plex's `<Genre>` child element, which plexapi exposes as the `genres` "
        "cached property (video.py:433) -- present on a listing item only if the "
        "listing XML carried the child, which is Task 2's probe question. Exact "
        "tag match, case-insensitive: `genre: Hor` does NOT match `Horror`.",
    ),
    FilterAttribute(
        "year", "int", _BOTH, "listing",
        "The listing attrib `year`. Kometa's special year words (`current_year` "
        "and its offsets) are NOT tier 1, so `year: current_year` refuses at "
        "load naming the field rather than parsing as something else.",
    ),
    FilterAttribute(
        "resolution", "tag", ("movie",), "probe",
        "Stream-level: Plex carries it on `<Media videoResolution=...>`, which "
        "plexapi reaches through the `media` cached property. EXPECT THIS TO "
        "DEFER to tier 2 -- the roadmap names it tier 1, but the plan's "
        "no-silent-N+1 rule outranks the naming, and a listing that carries "
        "Media children is the less likely probe outcome. Values are Plex's "
        "own: 4k, 1080, 720, 576, 480, sd. Movie-only here because a show's "
        "resolution is a property of its episodes, and per-episode traversal is "
        "not a tier-1 read.",
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
        "audio_language", "tag", ("movie",), "probe",
        "Stream-level, like `resolution`: the languages of the item's audio "
        "streams, under `<Media><Part><Stream>`. EXPECT THIS TO DEFER -- "
        "streams are a level below the Media children a listing might carry.",
    ),
    FilterAttribute(
        "subtitle_language", "tag", ("movie",), "probe",
        "Stream-level, like `audio_language`, and expected to defer with it.",
    ),
    FilterAttribute(
        "label", "tag", _BOTH, "probe",
        "Plex's `<Label>` child element -> the `labels` cached property "
        "(video.py:441). reconcile.py:70-83 already pays a deliberate reload "
        "for labels per collection, which is evidence the listing may not carry "
        "them; the probe decides.",
    ),
    FilterAttribute(
        "added", "date", _BOTH, "listing",
        "The listing attrib `addedAt` (video.py:44), a naive datetime in the "
        "Plex server's own clock. Compared date-granularly -- see the date "
        "convention in `_as_calendar_date`.",
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
        "`duration.gte: 90` is an hour and a half, not 90ms. Its own value type "
        "rather than `int` so the unit lives in the table instead of in a "
        "comment on one row.",
    ),
    FilterAttribute(
        "studio", "str", _BOTH, "listing",
        "The listing attrib `studio` (video.py:405). The ONE tier-1 string "
        "attribute, and the distinction matters: a bare `studio: Warner` is a "
        "case-insensitive SUBSTRING match, where a bare `genre: Horror` is an "
        "exact tag match. UNVERIFIED-TRANSCRIPTION -- this transcription files "
        "`studio` under Kometa's string filters (which carry .is/.isnot/"
        ".begins/.ends) rather than its tag filters, and that is the single "
        "most consequential tag-vs-string call in the table: if Kometa treats "
        "it as a tag, `studio: Warner` matches nothing at all instead of "
        "everything Warner. Task 4's oracle adjudicates.",
    ),
    FilterAttribute(
        "network", "tag", ("show",), "probe",
        "Show-only. plexapi reads `Show.network` from a listing ATTRIB "
        "(video.py:618), not from a child element, so unlike genre/label/"
        "collection this one may well be listing-resident -- but the 9a fact "
        "sheet audited `Movie._loadData` only, so the probe still has to "
        "confirm the show listing carries the attrib. Kometa filters `network` "
        "as a tag, so the bare form is an exact match even though the Plex "
        "value is one string.",
    ),
    FilterAttribute(
        "collection", "tag", _BOTH, "probe",
        "Plex's `<Collection>` child element -> the `collections` cached "
        "property (video.py:417): the collections the ITEM is already a member "
        "of on the server. Not this service's definitions, and not the "
        "collection being built -- filtering on it is how an operator says "
        "\"anything not already in X\".",
    ),
)

BY_NAME: dict[str, FilterAttribute] = {row.name: row for row in FILTER_ATTRIBUTES}


class ItemView(Protocol):
    """What ``evaluate`` reads. A plain ``dict`` satisfies it.

    ``get`` is keyed on the TABLE's attribute name -- ``release``, not
    ``originallyAvailableAt`` -- and returns the value in the type the row
    declares: a sequence of strings (or one bare string) for ``tag``, a string
    for ``str``, a number for ``int``/``float``, minutes for ``duration``, a
    ``date`` or ``datetime`` for ``date``. ``None`` means the item has no value,
    which is the missing-value rule's input.
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
    rather than an exception per item, hours later, inside the run."""
    text = _as_text(value, field)
    try:
        return re.compile(text, re.IGNORECASE)
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
    """The literal ``today``, resolved against the run's date rather than the
    parse's -- a config loaded once and run nightly must not freeze the day it
    was read on."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "today"


_TODAY = _Today()


def _as_date(value: object, field: str) -> dt.date | _Today:
    if isinstance(value, str) and value.strip().casefold() == "today":
        return _TODAY
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError as error:
            raise ValueError(
                f"{field}: {value!r} is not a date -- write it as 2024-01-31, or `today`"
            ) from error
    raise ValueError(f"{field}: {value!r} is not a date -- write it as 2024-01-31, or `today`")


def _as_days(value: object, field: str) -> int:
    """The bare date form's value: a window in days.

    ``added: 30`` is "added in the last 30 days", not "added on day 30". This
    is Kometa's documented meaning for a date filter with no modifier and it is
    the least guessable thing in the table, which is why a string here refuses
    rather than being coerced -- ``added: "2024-01-01"`` is an operator who
    meant ``added.after``, and answering it with a 2024-day window would be
    worse than saying so.
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
        raise ValueError(
            f"{field}: .{modifier} does not apply to {name!r}, a {attribute.type} attribute "
            "-- it takes " + ", ".join(writable)
            + f" (or no modifier at all, which means {attribute.default_operator})"
        )
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


# --- evaluation --------------------------------------------------------------


def _as_calendar_date(value: object, attribute: str) -> dt.date:
    """The date convention, in one place.

    Every date comparison is date-granular, and an aware datetime keeps the
    calendar date it already reads as -- ``tzinfo`` is dropped, never converted.
    Converting to UTC or to the runner's zone would make the same library
    filter differently depending on where the pass happened, which is not a
    property a collection should have. Plex writes ``addedAt`` naive in the
    server's own clock anyway, so dropping is also the faithful reading.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
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
    attribute: FilterAttribute, operator: str, have: object, want: object, today: dt.date
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
        when = _as_calendar_date(have, attribute.name)
        if operator == "eq":
            # "in the last N days", inclusive of the boundary day. A future
            # date passes: a window that starts N days ago has no upper edge,
            # and an item Plex says is released next week is still "released in
            # the last 30 days" by that reading. UNVERIFIED-TRANSCRIPTION --
            # whether Kometa bounds the window at today is exactly the kind of
            # corner the Task 4 oracle is for.
            return when >= today - dt.timedelta(days=want)
        moment = today if isinstance(want, _Today) else want
        if operator == "before":
            return when < moment
        if operator == "after":
            return when > moment
        if operator == "gte":
            return when >= moment
        return when <= moment

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


def _matches(predicate: FilterPredicate, view: ItemView, today: dt.date) -> bool:
    """One predicate against one item.

    The two rules that apply to every operator live here and only here, which
    is what makes them invariants rather than conventions:

    - **missing value**: an item with no value for the attribute is excluded by
      a positive filter and included by a negative one. See the module
      docstring for the transcription note on this;
    - **a list means any-of**: the predicate holds if ANY written value
      matches, and a negative operator is the negation of that -- so
      ``genre.not: [Horror, Comedy]`` means "neither", not "not Horror".
    """
    attribute = predicate.attribute
    negative = predicate.operator in _NEGATES
    have = view.get(attribute.name)
    if _is_missing(have, attribute.type):
        return negative

    operator = predicate.operator
    if negative:
        operator = _NEGATES[operator] or attribute.default_operator
    matched = any(
        _matches_one(attribute, operator, have, want, today) for want in predicate.values
    )
    return not matched if negative else matched


def evaluate(
    node: "FilterGroup | FilterPredicate",
    view: ItemView,
    *,
    today: dt.date | None = None,
) -> bool:
    """Does this item pass the filter?

    ``today`` is the run's date, which the relative date operators measure
    against; it defaults to the current date so a caller with no run date still
    gets the documented behaviour. It is a keyword so the signature the plan
    fixed -- ``evaluate(group, view) -> bool`` -- is the one that stays.

    A view whose value has the wrong *type* raises rather than counting as
    missing: that is a bug in the accessor, and swallowing it would hide it
    behind a full, plausible, wrong collection. The engine stage contains the
    exception (Task 3); this layer's job is to be loud.
    """
    when = today if today is not None else dt.date.today()
    if isinstance(node, FilterPredicate):
        return _matches(node, view, when)
    results = (evaluate(child, view, today=when) for child in node.children)
    return any(results) if node.op == "any" else all(results)
