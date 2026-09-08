"""``search_url``: a parsed predicate tree as a Plex search query string.

Phase 9b's third layer and the one the whole phase's risk sits on. The roadmap
names that risk by name: *silent wrongness -- a mistranslated attribute returns
a plausible-but-wrong item set*. A collection built from a wrong query is not
empty and does not error; it is full, and wrong, and looks exactly like a
correct collection of different titles. So two things are true of this module
and both are load-bearing:

**It is pure.** No plexapi, no HTTP, no clock, no config. Its inputs are a
``FilterGroup`` (Task 1's parser), a library type, a sort list, a limit, and a
``resolve_tag`` CALLABLE. That purity is what lets
``tests/test_collection_search_oracle.py`` compare its output, byte for byte,
against strings produced by Kometa's own ``build_filter`` running standalone --
which is the only test that can catch a translation this module and its unit
tests would agree on and Plex would not.

**Every branch is a transcription with a line reference.** The assembly is
``modules/builder.py:4169-4295`` at Kometa v2.4.8; the per-type special cases
are :4222-4250; the final wrap is :4287-4289. Where this module differs from
Kometa it is because 9a or 9b refused something (``validate:``, the implicit
base, a bare ``duration:``), never because a branch was simplified --
``.regex`` (roadmap row 178) is its own case: not a refusal, a DIFFERENT
mechanism under the same spelling, transcribed in its own branch below.

**What Kometa does here and this module deliberately does not:**

- ``validate: false`` (builder.py:4160-4167) downgrades every per-attribute
  error to a log line and CONTINUES, so a typo silently narrows nothing. This
  module has no such switch -- a value that does not resolve raises.
- Kometa skips a term whose validation came back empty (``continue``,
  :4220-4221), which is how a ``validate: false`` run ends up with a query
  missing a clause it was asked for. Nothing here can produce an empty term.
- ``includeCollections`` appears nowhere in Kometa's search path either, and
  ``test_no_built_url_ever_carries_includeCollections`` keeps it that way here:
  it does not enrich a listing, it MIXES Collection objects into the result set
  (roadmap Notes-for-9b item 1). plexapi agrees on what the parameter does --
  its only use is ``LibrarySection.totalViewSize(includeCollections=True)``
  (library.py:505-518), and ``totalSize`` asks for it as ``False`` (:463).
"""
import datetime as dt
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import quote

from autoposter.collections.filters import (
    DISCOVERED,
    MOMENT_DATE_ROWS,
    SEARCH_MODIFIERS,
    FilterGroup,
    FilterPredicate,
    RelativeWindow,
)
from autoposter.collections.search_sorts import (
    SORT_TYPES,
    require_sort_for_libtype,
    sort_argument,
)

__all__ = [
    "SearchAttributeNotAvailable",
    "SearchProducedNothing",
    "TagResolver",
    "TagValueNotFound",
    "build_search_url",
]


class TagResolver(Protocol):
    """The library's own tag vocabulary, as a callable.

    Returns the Plex KEYS one written value resolves to -- empty when the
    library has no such value, and SEVERAL when the value expands (a language
    code covering every locale variant the library carries). Injected rather
    than imported so this module stays pure; Task 4's builder passes the
    Plex-backed, run-cached one and the tests pass a dict.

    ``choices`` (roadmap row 178) is the enumeration half, used only for a
    ``.regex`` search predicate: every ``(key, title)`` the library reports
    for an attribute, so the pattern can be tested against each TITLE (the
    spelling an operator would otherwise write) rather than the opaque key.
    A ``resolve_tag`` whose config never writes ``.regex`` in a search never
    needs to implement it -- ``_arguments`` calls it only from the branch
    below.

    ``discover_field`` (roadmap row 176) is the FIELD half, and it is asked for
    exactly one row: ``folder_location``, whose ``search_field`` is the
    ``DISCOVERED`` sentinel because Kometa cannot hard-code the field either
    (``Library.get_search_key`` reads ``listFilters`` at run time,
    modules/plex.py:1286-1297). It answers the query field for one attribute on
    one library type, already re-scoped -- ``episode.<field>`` on a show library
    -- and a ``resolve_tag`` whose config never writes ``folder_location`` never
    needs to implement it, the same escape hatch ``choices`` has.
    """

    def __call__(self, attribute: str, value: str, /) -> tuple[str, ...]: ...
    def choices(self, attribute: str, /) -> tuple[tuple[str, str], ...]: ...
    def discover_field(self, attribute: str, libtype: str, /) -> str: ...


class TagValueNotFound(Exception):
    """A written tag value is not in the library's vocabulary.

    Kometa raises here too (``Plex Error: {attribute}: {value} not found``,
    builder.py:4433-4437) and this service did not, before 9b: it compared
    case-insensitively at evaluation time instead, so a typo produced an empty
    collection rather than a refusal (roadmap row 158). A search has to resolve
    the value -- Plex is sent a key, not a word -- so the refusal comes for
    free on this path and stays filed on the other.
    """


class SearchAttributeNotAvailable(Exception):
    """This attribute is real, but it will not be answered here.

    Two cases, both refusals of a real name rather than of a typo: Plex will not
    answer it for this LIBRARY TYPE (the original case, and the same shape as
    ``LibraryTypeMismatch``, builders/base.py:237-243); or this service will not
    answer it on this BUILDER -- ``folder_location`` under ``smart_filter``,
    whose stored query would otherwise carry a run-time-discovered field into a
    definition hash (roadmap row 176, ruling C5). Its own class so the engine's
    class-name-only log line says so."""


class SearchProducedNothing(Exception):
    """The tree rendered to no terms at all.

    Unreachable from a validated config (the parser refuses an empty block),
    and raised rather than returned because the alternative -- a query with a
    ``type`` and a ``sort`` and no predicate -- means THE WHOLE LIBRARY.
    Kometa's equivalent is ``FilterFailed`` (builder.py:4291).
    """


def build_search_url(
    group: FilterGroup,
    *,
    libtype: str,
    search_type: str | None = None,
    sort_by: Sequence[str] = (),
    limit: int | None = None,
    resolve_tag: TagResolver,
) -> str:
    """The query string for one search, from ``?`` onward.

    Appended by the caller to ``/library/sections/{key}/all``, which is what
    Kometa does with it (plex.py:958-959).

    Assembly order is Kometa's and is not arbitrary: ``type`` first, then
    ``limit`` if there is one, then ``sort`` (always -- an omitted ``sort_by``
    means the type's default, never no sort), then the body
    (builder.py:4287-4289). An ``all`` base has its trailing ``&`` stripped; an
    ``any`` base is wrapped in ``push=1&...pop=1`` instead, because a top-level
    OR needs a scope and the query string has no other way to give it one.

    ``libtype`` and ``search_type`` are two different questions and this
    function is where they part (search-tail E-2, roadmap rows 173/179).
    ``search_type`` decides the ``type=`` byte, which sort matrix a name must
    be in, and the encoded sort value; ``libtype`` decides which attributes
    Plex will answer and whether a field renders as ``show.genre`` or bare.
    They are the same for every definition that writes no ``builder_level``,
    which is why ``search_type`` defaults to ``libtype`` and why every URL
    built before E-2 is byte-identical after it. Kometa splits them the same
    way and for the same reason: ``sort_type = self.builder_level``
    (modules/builder.py:4093-4121) while ``show_translation`` applies
    ``if self.library.is_show`` (:4176-4181), so a ``type=4`` search on a show
    library still renders ``episode.title`` and ``show.genre``.
    """
    # The gate, as the FIRST statement -- ahead of ``_render_group`` and every
    # ``resolve_tag`` round-trip it makes. ``sort_argument`` indexes the
    # search type's table directly, so a sort that is real for another search
    # level -- ``episode_added.desc`` against a movie library -- would
    # otherwise reach it as a bare ``KeyError``, which the engine reports as a
    # dead source with no explanation. One site rather than two (Task 4
    # review, ruling on Minor 1): this function is public and pure, so it has
    # to hold for every caller, not only ``PlexSearchBuilder``; and hoisting it
    # above the body means a wrong-libtype sort refuses before this call
    # resolves a single tag value, which used to require a second, earlier
    # call at the builder's own call site.
    search_type = search_type or libtype
    require_sort_for_libtype(search_type, sort_by)
    body = _render_group(group, libtype=libtype, resolve_tag=resolve_tag)
    if not body:
        raise SearchProducedNothing(
            "this search built no query terms at all, which Plex would answer "
            "with the entire library"
        )
    tail = body[:-1] if group.op == "all" else f"push=1&{body}pop=1"
    head = f"?type={SORT_TYPES[search_type].key}&"
    # ``if limit``, not ``if limit is not None`` -- Kometa's own test
    # (builder.py:4289). A zero would otherwise emit ``limit=0&``, a byte Kometa
    # never sends and which Plex would answer with nothing at all. Kometa
    # refuses ``< 1`` a layer up (:4152) and so does ``PlexSearchParams``
    # (``Field(ge=1)``), so this is the second of two gates rather than the
    # only one -- but this module is public and pure, and a caller that skipped
    # the params model should not be able to build a query no server answers.
    if limit:
        head += f"limit={limit}&"
    head += f"sort={sort_argument(search_type, sort_by)}&"
    return head + tail


def _render_group(group: FilterGroup, *, libtype: str, resolve_tag: TagResolver) -> str:
    """One block, as terms joined by its own conjunction. Always ends in ``&``.

    ``conjunction`` goes BEFORE each term after the first, which is Kometa's
    shape (builder.py:4248, :4252) and not the more obvious "join with a
    separator" -- the difference shows up in the nested case, where the
    conjunction that joins two ``push``/``pop`` pairs belongs to the block
    containing them.
    """
    conjunction = "and=1&" if group.op == "all" else "or=1&"
    out = ""
    for child in group.children:
        if isinstance(child, FilterGroup):
            inner = _render_group(child, libtype=libtype, resolve_tag=resolve_tag)
            if not inner:
                continue
            # An INLINE wrapper is a list-shaped ``any:``/``all:`` in a
            # plex_search: its children each got their own push/pop already,
            # and Kometa joins them with the containing block's conjunction
            # rather than scoping them together (builder.py:4207-4217). Giving
            # it a pair of its own would add a nesting level Kometa never
            # emits. See ``filters._parse_nested``.
            piece = inner if child.inline else f"push=1&{inner}pop=1&"
        else:
            piece = _render_predicate(child, group.op, libtype=libtype, resolve_tag=resolve_tag)
        # UNREACHABLE, and a place this module does not do what Kometa does --
        # named here so the module docstring's "never because a branch was
        # simplified" claim stays true. Kometa reaches :4252 with an
        # empty ``results`` and appends the conjunction anyway, producing a
        # dangling ``and=1&`` with no term in front of it. Nothing here can
        # render an empty piece: the parser refuses an empty block, an empty
        # list and a nested block that produced no children, which is what
        # ``SearchProducedNothing``'s docstring says. Kept rather than deleted
        # because it is the last line of defence for the query shape -- a
        # dangling conjunction is a URL Plex still answers, with a different
        # set -- and a guard whose cost is one comparison is cheaper than the
        # class of bug it excludes.
        if not piece:
            continue
        out += (conjunction if out else "") + piece
    return out


def _render_predicate(
    predicate: FilterPredicate,
    block_op: str,
    *,
    libtype: str,
    resolve_tag: TagResolver,
) -> str:
    """One ``attribute[.operator]: value`` line, as one or more terms.

    More than one when the value is a list, or when a tag value expands -- and
    they are joined by the BLOCK's conjunction, not by a fixed OR
    (builder.py:4245-4248). Under an ``all:`` block that makes
    ``content_rating: [PG-13, R]`` an AND, which is the most surprising thing
    in this grammar and is Kometa's.
    """
    row = predicate.attribute
    if libtype not in row.search_kinds:
        kinds = " or ".join(sorted(row.search_kinds))
        raise SearchAttributeNotAvailable(
            f"{predicate.field}: Plex answers {row.name!r} only for {kinds} "
            f"libraries, and this pass is running against a {libtype} library, "
            f"where the query would match nothing at all. Narrow the definition "
            f"with `libraries:` so it only targets {kinds} libraries"
        )
    # THE one line in this module that asks for a field, and the one place a
    # run-time-discovered field can enter a URL (roadmap row 176). Every row but
    # ``folder_location`` answers from the table; that one holds the
    # ``DISCOVERED`` sentinel and ``field_for`` raises on it, so the branch is
    # not an optimisation -- it is the only path that produces a field at all.
    # Kometa's own shape, one layer up: ``arg_key = get_search_key(attr, ...) if
    # attr == "folder_location" else <the translation tables>``
    # (modules/builder.py:4179), the only attribute in its grammar whose field
    # is a function call.
    if row.search_field is DISCOVERED:
        field = resolve_tag.discover_field(row.name, libtype)
    else:
        field = row.field_for(libtype)
    conjunction = "and=1&" if block_op == "all" else "or=1&"
    args = _arguments(predicate, resolve_tag=resolve_tag)
    return "".join(
        (conjunction if index else "") + f"{field}{modifier}={value}&"
        for index, (modifier, value) in enumerate(args)
    )


def _arguments(
    predicate: FilterPredicate, *, resolve_tag: TagResolver
) -> list[tuple[str, str]]:
    """``(modifier, value)`` pairs for one predicate, in Kometa's branch order.

    The order matters: ``.rated`` is tested before the boolean branch because a
    ``.rated`` predicate's VALUE is a boolean and would otherwise take it, and
    the date branch is tested before everything because a relative window is a
    date value with a modifier the table's own lookup would get wrong.
    """
    row = predicate.attribute
    operator = predicate.operator
    modifier = SEARCH_MODIFIERS[(row.type, operator)]

    # A relative window: negative, unit-suffixed, and ``o`` becomes ``mon``.
    # builder.py:4222-4233.
    if row.type == "date" and operator in ("eq", "not"):
        out = []
        for value in predicate.values:
            # A real guard, not an ``assert``: an assert is stripped under
            # ``python -O``, and what it was guarding is not a theory about
            # this module's own arithmetic but the ONE way a caller can hand
            # this function a value it cannot render. A bare or ``.not`` date
            # parsed with ``searching=False`` is an ``int`` of days, not a
            # window, and the unguarded failure is ``'int' object has no
            # attribute 'unit'`` several frames down. ``RELATIVE_UNITS`` used
            # to be asserted here too, on the line AFTER ``value.unit`` had
            # already been read, so it could not fail usefully; the parser's
            # ``_as_window`` is the only producer of a ``RelativeWindow`` and
            # it refuses a unit outside the table, which is where that check
            # belongs.
            if not isinstance(value, RelativeWindow):
                raise TypeError(
                    f"{predicate.field}: a relative date window is required "
                    f"here, not {value!r}. This tree was parsed for a "
                    "`filters:` block -- call parse_filters(..., "
                    "searching=True) for a plex_search, which is what turns "
                    "`added: 30` into a window rather than a day count"
                )
            unit = "mon" if value.unit == "o" else value.unit
            out.append((modifier, f"-{value.count}{unit}"))
        return out

    # "Has any rating at all", as a comparison against Plex's sentinel.
    # builder.py:4236-4237. The negation is on the ARGUMENT's modifier slot,
    # which is why SEARCH_MODIFIERS holds "" for this pair.
    if operator == "rated":
        return [("!" if value else "", "-1") for value in predicate.values]

    # builder.py:4238-4241, and the same argument-side negation.
    if row.type == "bool":
        return [("" if value else "!", "1") for value in predicate.values]

    # MINUTES to MILLISECONDS. ``predicate.values`` are floats (9a's
    # ``_as_minutes``), and Kometa's are too (``float(str(value))``,
    # util.py:861), so the product renders with a trailing ``.0`` -- which is
    # what Kometa sends. builder.py:4234-4235.
    if row.type == "duration":
        return [(modifier, str(value * 60000)) for value in predicate.values]

    # An absolute date, ISO, whichever spelling it was written in.
    # builder.py:4441-4445.
    if row.type == "date":
        # ROADMAP ROW 157, and the ONE rendering in this module that is not a
        # straight table lookup. ``.to`` is inclusive at the calendar DAY. On a
        # row Plex stores as a bare date that is already what ``%3C=`` means
        # (measured: ``originallyAvailableAt%3C=D`` -> 1864 = the strict
        # ``%3C%3C=D`` at 1862 plus the two items dated D). On a
        # ``MOMENT_DATE_ROWS`` row it is NOT: Plex reads a bare date on
        # ``addedAt`` as that day's MIDNIGHT, and the probe measured
        # ``addedAt%3C=A`` and ``addedAt%3C%3C=A`` at the same 1862 -- both
        # forms drop everything added during day A, including the 09:15 item
        # ``filters._matches_one`` keeps. The form that means the same as the
        # client half is the STRICT one at the day after: ``%3C%3C=A+1`` is
        # "strictly before (A+1) 00:00", which is ``when < next_day`` exactly.
        # (The same day-after form was measured on the date field, where a
        # same-day count exists to check it against: ``%3C%3C=D+1day`` -> 1864
        # = ``%3C=D``.)
        #
        # It lives here rather than in ``SEARCH_MODIFIERS`` because that table
        # is keyed by ``(type, operator)`` and this is a per-ROW answer; and
        # the row-set is imported from ``filters`` rather than restated,
        # because the client half reads the same set and a second copy is the
        # thing that drifts. ``value`` is a ``dt.date`` here -- ``_as_date``
        # produces one and ``resolve_search_values`` has already turned any
        # ``today`` into one -- so the arithmetic is calendar arithmetic and
        # carries across month and year ends by construction.
        if operator == "to" and row.name in MOMENT_DATE_ROWS:
            return [
                ("%3C%3C", (value + dt.timedelta(days=1)).isoformat())
                for value in predicate.values
            ]
        return [(modifier, value.isoformat()) for value in predicate.values]

    # A tag: the library's KEY, never the written word, and possibly several
    # per value. builder.py:4400-4440, :4245-4248.
    #
    # QUOTED, like the ``str`` branch below and unlike Kometa, which quotes only
    # that one. A deliberate divergence from the byte-for-byte oracle's source
    # of truth, in the direction of correctness: for every tag family whose Plex
    # key is an opaque id ``quote`` is the identity and the two agree anyway,
    # but ``content_rating``'s key IS its title, so an unquoted ``+`` reaches
    # the matcher as a space and an unquoted ``&`` ends the parameter -- a
    # SILENT SUBSET rather than an error.
    # ``tests/test_collection_cs_equivalence.py`` is the proof;
    # ``docs/research/plex-dynamic-probe/README.md`` section 5 is the ``+``
    # measurement.
    # For whoever hits this from the other direction: if a future 9b oracle
    # config resolves a tag to a TITLE and that breaks byte parity against
    # Kometa, the failure IS this divergence surfacing -- keep the quote and
    # accept the byte mismatch; do not revert it to make the comparison green.
    # Roadmap row 178: Kometa's search ``.regex`` is a client-side expansion
    # over the library's own tag vocabulary, not a regex Plex ever evaluates
    # (builder.py:4301-4323) -- a DIFFERENT mechanism from ``filters:``'s
    # ``.regex``, which matches the item's own value client-side after
    # resolution. Same spelling, two mechanisms, on purpose (the project's
    # Kometa-parity naming doctrine) -- the divergence is documented here,
    # at the one place that actually renders it, rather than only in a
    # refusal message an operator who writes it correctly never reads.
    #
    # Renders as plain resolved-key terms regardless of the row's ``str``/
    # ``tag`` classification for ``filters:`` purposes -- ``studio`` is
    # ``str`` there (substring match) but its search vocabulary is still
    # enumerable via ``listFilterChoices``, which is what Kometa's own
    # ``validate_attribute`` falls back to for exactly this row (plex.py:568).
    if operator == "regex":
        out = []
        for pattern in predicate.values:
            keys = tuple(
                key for key, title in resolve_tag.choices(row.name)
                if pattern.search(title)
            )
            if not keys:
                raise TagValueNotFound(
                    f"{predicate.field}: {pattern.pattern!r} matched none of "
                    f"the {row.name} values this library uses"
                )
            out.extend((modifier, quote(str(key))) for key in keys)
        return out

    if row.type == "tag":
        out = []
        for value in predicate.values:
            keys = resolve_tag(row.name, value)
            if not keys:
                raise TagValueNotFound(
                    f"{predicate.field}: {value!r} is not one of the "
                    f"{row.name} values this library uses, so Plex has no key "
                    "to search for. Check the spelling against the library's "
                    "own list"
                )
            out.extend((modifier, quote(str(key))) for key in keys)
        return out

    # A string: quoted, unresolved. builder.py:4246.
    if row.type == "str":
        return [(modifier, quote(str(value))) for value in predicate.values]

    # int and float, as they stand -- a float keeps its ``.0``.
    return [(modifier, str(value)) for value in predicate.values]
