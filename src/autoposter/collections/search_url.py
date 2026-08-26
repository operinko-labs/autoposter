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
Kometa it is because 9a or 9b refused something (``.regex``, ``validate:``,
the implicit base, a bare ``duration:``), never because a branch was
simplified.

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
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import quote

from autoposter.collections.filters import (
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
    """

    def __call__(self, attribute: str, value: str, /) -> tuple[str, ...]: ...


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
    """This attribute is real, but Plex will not answer it for this library
    type. Its own class so the engine's class-name-only log line says so; the
    same shape as ``LibraryTypeMismatch`` (builders/base.py:237-243)."""


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
    """
    body = _render_group(group, libtype=libtype, resolve_tag=resolve_tag)
    if not body:
        raise SearchProducedNothing(
            "this search built no query terms at all, which Plex would answer "
            "with the entire library"
        )
    tail = body[:-1] if group.op == "all" else f"push=1&{body}pop=1"
    head = f"?type={SORT_TYPES[libtype].key}&"
    # ``if limit``, not ``if limit is not None`` -- Kometa's own test
    # (builder.py:4289). A zero would otherwise emit ``limit=0&``, a byte Kometa
    # never sends and which Plex would answer with nothing at all. Kometa
    # refuses ``< 1`` a layer up (:4152) and so does ``PlexSearchParams``
    # (``Field(ge=1)``), so this is the second of two gates rather than the
    # only one -- but this module is public and pure, and a caller that skipped
    # the params model should not be able to build a query no server answers.
    if limit:
        head += f"limit={limit}&"
    # The gate, immediately ahead of the lookup it protects.
    # ``sort_argument`` indexes the libtype's table directly, so a sort that is
    # real for the OTHER libtype -- ``episode_added.desc`` against a movie
    # library -- reaches it as a bare ``KeyError``, which the engine reports as
    # a dead source with no explanation. Here rather than only in the builder
    # because this function is public and pure: the oracle drives it, the unit
    # tests drive it, and a caller that skipped the params model must not be
    # able to reach the unexplained failure either. ``PlexSearchBuilder`` calls
    # the same gate before it starts resolving tag values, which is about the
    # round-trips it saves rather than about the message.
    require_sort_for_libtype(libtype, sort_by)
    head += f"sort={sort_argument(libtype, sort_by)}&"
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
        return [(modifier, value.isoformat()) for value in predicate.values]

    # A tag: the library's KEY, never the written word, and possibly several
    # per value. builder.py:4400-4440, :4245-4248.
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
            out.extend((modifier, key) for key in keys)
        return out

    # A string: quoted, unresolved. builder.py:4246.
    if row.type == "str":
        return [(modifier, quote(str(value))) for value in predicate.values]

    # int and float, as they stand -- a float keeps its ``.0``.
    return [(modifier, str(value)) for value in predicate.values]
