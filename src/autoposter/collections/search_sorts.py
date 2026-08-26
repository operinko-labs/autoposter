"""``search_sorts``: the sort matrices and the type keys, copied verbatim.

Phase 9b's second layer, and the smallest one. A Plex search URL carries a
``type=`` (which media type the query returns) and a ``sort=`` (which order,
and therefore -- when a ``limit`` is present -- WHICH items). Both are pure
transcription: Plex's sortable field names are underdocumented and several of
them are not the field the sort's NAME suggests, so every table below is
Kometa's own, copied **pre-encoded** from ``modules/plex.py`` at v2.4.8.

**Pre-encoded, never retyped.** The values below contain ``%3Adesc`` exactly
as Kometa stores them, and are joined with ``%2C`` (no value in either table
carries a comma of its own -- only the deferred season/episode/album/track
matrices do). Re-deriving them here -- taking
``rating:desc`` and quoting it at build time -- would be a second
implementation of something with exactly one correct answer, and every way of
getting it subtly wrong produces a URL Plex still answers, with a plausible and
differently-ordered set. Note that ``watchlist_sorts`` (plex.py:788-797) uses
the UNENCODED ``:desc`` for a different endpoint, which is precisely the
near-miss a hand-copy picks up.

**Three rows are worth reading before trusting the rest**, because each is a
name that does not mean what it says:

- ``resolution.asc``/``.desc`` sort by ``mediaHeight``, not by
  ``videoResolution``. Plex has no sortable resolution field, and the tag
  values it does have (``4k``, ``1080``, ``sd``) do not order lexically.
- ``random`` has no direction. It is the one key in either table with no
  ``.asc``/``.desc`` pair, and asking for ``random.desc`` is a refusal.
- ``unplayed`` in the SHOW table is ``unviewedLeafCount`` -- how many episodes
  are unwatched, a number -- while ``unplayed`` as a SEARCH ATTRIBUTE
  (``collections/filters.py``) is a movie-only boolean asking whether the item
  has been played at all. Same word, two meanings, in two tables that sit one
  import apart; they are not related and neither is derived from the other.

The season, episode, artist, album and track matrices (plex.py:668-778) are
deliberately absent: v1 searches movie and show libraries, and a table nothing
can reach is a table nobody checks. They arrive with the libtypes that need
them (the per-family tail, Task 6).

**``sort_by`` is not ``sort``.** Everything in this module serves
``plex_search``'s ``sort_by:`` key -- which order PLEX returns the QUERY in,
and therefore, when a ``limit`` is present, WHICH items the collection gets.
``CollectionDefinition.sort`` (``config/schema.py:289``, default ``custom``)
is a different setting under a confusingly similar name: it is the finished
collection's own display order in Plex, applied with ``collection.sortUpdate``
once the members exist (``collections/lists.py:275``). Kometa keeps them apart
the same way -- ``sort_by`` lives inside the search block (builder.py:4130) --
and the two must never be folded together, because one decides membership and
the other decides presentation.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

__all__ = [
    "KNOWN_SORT_NAMES",
    "MOVIE_SORTS",
    "SHOW_SORTS",
    "SORT_TYPES",
    "SortNotAvailable",
    "SortType",
    "require_sort_for_libtype",
    "sort_argument",
]

# modules/plex.py:604-636. Fifteen directional pairs plus ``random`` = 31.
#
# Read-only at the type level AND at runtime: ``Mapping`` is what
# ``SortType.sorts`` is annotated as, and ``MappingProxyType`` is what makes
# the annotation true. The repo's neighbouring transcription
# (``filters.FILTER_ATTRIBUTES``) is a tuple of frozen dataclasses for the same
# reason -- a table copied from someone else's source is data, and a caller
# that can edit it can make ours disagree with Kometa at runtime with nothing
# to show for it.
MOVIE_SORTS: Mapping[str, str] = MappingProxyType({
    "title.asc": "titleSort",
    "title.desc": "titleSort%3Adesc",
    "year.asc": "year",
    "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt",
    "originally_available.desc": "originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt",
    "release.desc": "originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating",
    "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating",
    "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",
    "user_rating.desc": "userRating%3Adesc",
    "content_rating.asc": "contentRating",
    "content_rating.desc": "contentRating%3Adesc",
    "duration.asc": "duration",
    "duration.desc": "duration%3Adesc",
    "progress.asc": "viewOffset",
    "progress.desc": "viewOffset%3Adesc",
    "plays.asc": "viewCount",
    "plays.desc": "viewCount%3Adesc",
    "added.asc": "addedAt",
    "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt",
    "viewed.desc": "lastViewedAt%3Adesc",
    # NOT videoResolution -- see the module docstring.
    "resolution.asc": "mediaHeight",
    "resolution.desc": "mediaHeight%3Adesc",
    "bitrate.asc": "mediaBitrate",
    "bitrate.desc": "mediaBitrate%3Adesc",
    # No direction, in either table.
    "random": "random",
})

# modules/plex.py:637-667. Fourteen directional pairs plus ``random`` = 29.
SHOW_SORTS: Mapping[str, str] = MappingProxyType({
    "title.asc": "titleSort",
    "title.desc": "titleSort%3Adesc",
    "year.asc": "year",
    "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt",
    "originally_available.desc": "originallyAvailableAt%3Adesc",
    "episode_originally_available.asc": "episode.originallyAvailableAt",
    "episode_originally_available.desc": "episode.originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt",
    "release.desc": "originallyAvailableAt%3Adesc",
    "episode_release.asc": "episode.originallyAvailableAt",
    "episode_release.desc": "episode.originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating",
    "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating",
    "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",
    "user_rating.desc": "userRating%3Adesc",
    "content_rating.asc": "contentRating",
    "content_rating.desc": "contentRating%3Adesc",
    # A COUNT of unwatched episodes -- not the boolean search attribute of the
    # same name. See the module docstring.
    "unplayed.asc": "unviewedLeafCount",
    "unplayed.desc": "unviewedLeafCount%3Adesc",
    "episode_added.asc": "episode.addedAt",
    "episode_added.desc": "episode.addedAt%3Adesc",
    "added.asc": "addedAt",
    "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt",
    "viewed.desc": "lastViewedAt%3Adesc",
    "random": "random",
})


@dataclass(frozen=True)
class SortType:
    """One library type's search identity.

    ``key`` is the ``type=`` query parameter (modules/plex.py:779-787), which
    is what makes ``/library/sections/N/all`` return movies rather than
    whatever the section's default is. ``default_sort`` is what Kometa applies
    when the config names none (builder.py:4140-4141) -- so an omitted
    ``sort_by`` is not an omitted parameter, it is ``title.asc``.
    """

    key: int
    default_sort: str
    sorts: Mapping[str, str]


SORT_TYPES: Mapping[str, SortType] = MappingProxyType({
    "movie": SortType(key=1, default_sort="title.asc", sorts=MOVIE_SORTS),
    "show": SortType(key=2, default_sort="title.asc", sorts=SHOW_SORTS),
})

# The union, for a LOAD-time check. A definition with no ``libraries:`` key
# runs against every library in the pass, so which table applies is not known
# until build time -- exactly the argument ``require_library_type`` makes
# (builders/base.py:246-269). Load time can still catch a typo that is in
# neither table, which is the common case by a wide margin.
KNOWN_SORT_NAMES: frozenset[str] = frozenset(MOVIE_SORTS) | frozenset(SHOW_SORTS)


class SortNotAvailable(Exception):
    """This sort is real, but not for the library type the pass is running on.

    Its own class rather than a ``ValueError`` so the engine's log line -- which
    carries the exception class name and nothing else -- says what kind of
    failure this was. The same reasoning, and the same shape, as
    ``LibraryTypeMismatch`` (builders/base.py:237-243).
    """


def sort_argument(libtype: str, sort_by: Sequence[str] = ()) -> str:
    """The ``sort=`` value for one query, already encoded.

    Several sorts join with ``%2C`` in the order written, which is Plex's
    tie-break order (builder.py:4289). Duplicates are kept rather than
    deduplicated: Kometa keeps them, and a second mention of the same key is a
    no-op on the server rather than an error, so removing it would be this
    module deciding something Plex already decided.
    """
    table = SORT_TYPES[libtype].sorts
    names = tuple(sort_by) or (SORT_TYPES[libtype].default_sort,)
    return "%2C".join(table[name] for name in names)


def require_sort_for_libtype(libtype: str, sort_by: Sequence[str]) -> None:
    """Refuse a sort this library type has no column for.

    A build-time check, for the reason ``require_library_type`` is one: a
    definition with no ``libraries:`` key applies to every library in the pass,
    so the type is only known here. The refusal matters because the
    alternative is invisible -- a ``KeyError`` deep in ``sort_argument`` would
    reach the engine as a dead source with no explanation, and falling back to
    the default sort would silently build a differently-ordered collection.
    """
    table = SORT_TYPES[libtype].sorts
    for name in sort_by:
        if name in table:
            continue
        others = sorted(
            other for other, spec in SORT_TYPES.items() if name in spec.sorts
        )
        if others:
            kinds = " or ".join(others)
            raise SortNotAvailable(
                f"sort_by {name!r} is a {kinds} sort, but this pass is running "
                f"against a {libtype} library, which has no such column. Narrow "
                f"the definition with `libraries:` so it only targets {kinds} "
                f"libraries, or pick a sort both have"
            )
        raise SortNotAvailable(
            f"sort_by {name!r} is not a sort for a {libtype} library. Options: "
            + ", ".join(sorted(table))
        )
