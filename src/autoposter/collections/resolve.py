"""Map namespaced external ids onto the Plex items this library actually owns.

``section.all()`` returns every item with its guids already populated, so the
whole index costs one request -- measured at 1,954 movies in 2.8 seconds
against the production server. Reloading items individually to read their
guids would turn that into thousands of requests. That budget is why the index
is built once per library and covers every namespace at once: a second pass
per namespace would multiply the only expensive call in the engine.

Two rules the engine above depends on:

- **Unowned ids are dropped, never guessed.** There is deliberately no
  title-based fallback: a wrong match puts the wrong film in a collection,
  which is worse than a missing one. They are counted instead, so a definition
  can report ``unresolved: N`` rather than quietly shrinking.
- **Deduplication is by item, not by id.** One Plex item commonly carries an
  imdb *and* a tmdb guid, and a list may name both; the item belongs in the
  collection once, at the position of whichever id came first.
"""
import logging
from typing import NamedTuple

from autoposter.collections.ids import (
    LIBTYPE_FOR_LEVEL,
    NAMESPACES,
    ExternalId,
    MemberLevel,
)

logger = logging.getLogger(__name__)

# Every namespace except "plex" is read off an item's guids, where it appears
# as "<namespace>://<value>". "plex" is the rating key, which is the item's
# identity already. Derived from NAMESPACES so the two cannot drift.
GUID_PREFIXES: dict[str, str] = {ns: ns + "://" for ns in sorted(NAMESPACES - {"plex"})}

# ``{namespace: {value: plex_item}}``.
OwnedIndex = dict[str, dict[str, object]]


class ResolvedList(NamedTuple):
    """What a list of external ids turned into.

    ``unresolved`` counts the *distinct* ids that matched nothing -- see
    ``resolve_external``.
    """

    items: list[object]
    unresolved: int


def _identity(item: object) -> object:
    """What makes two references the same owned item.

    The rating key, because that is Plex's identity for it. Falling back to the
    object address keeps a guid-only fake usable and never merges two items.
    """
    rating_key = getattr(item, "ratingKey", None)
    return str(rating_key) if rating_key is not None else id(item)


def build_owned_index(section, level: MemberLevel = "item") -> OwnedIndex:
    """``{namespace: {value: plex_item}}`` for everything in the library.

    One ``section.all()`` pass. Within an item, the first guid of a namespace
    claims it -- the IMDb-only index this replaces stopped at an item's first
    ``imdb://`` guid, and reproducing that exactly is what lets the ported
    sources keep their output. Across items, the first item to claim a value
    keeps it; a duplicate guid on a second item does not steal the mapping.

    ``level`` (roadmap row 143) is what the index's entries ARE. ``"item"`` is
    the library's own granularity and still costs exactly one ``section.all()``.
    ``"season"``/``"episode"`` walk the same library again through
    ``section.search(libtype=...)``, because a Show's own guids say nothing
    about its episodes -- which is precisely why no builder could hand an
    episode back before this. The two indexes are kept SEPARATE rather than
    merged: a tvdb series id and a tvdb episode id are different id spaces, and
    merging them would let a series id resolve to an episode.

    An unknown level raises ``KeyError`` rather than falling back to the item
    walk. The fallback would be a full, plausible, wrong membership, which is
    the one outcome every rule in this package exists to prevent.
    """
    libtype = LIBTYPE_FOR_LEVEL[level]
    entries = section.all() if libtype is None else section.search(libtype=libtype)
    index: OwnedIndex = {namespace: {} for namespace in NAMESPACES}
    for item in entries:
        rating_key = getattr(item, "ratingKey", None)
        if rating_key is not None:
            index["plex"].setdefault(str(rating_key), item)
        claimed: set[str] = set()
        for guid in getattr(item, "guids", None) or []:
            value = getattr(guid, "id", "") or ""
            for namespace, prefix in GUID_PREFIXES.items():
                if namespace not in claimed and value.startswith(prefix):
                    index[namespace].setdefault(value[len(prefix):], item)
                    claimed.add(namespace)
                    break
    logger.debug(
        "indexed %d %s(s): %s",
        len(index["plex"]),
        level,
        ", ".join("%d by %s" % (len(index[ns]), ns) for ns in GUID_PREFIXES),
    )
    return index


def resolve_external(index: OwnedIndex, ids: list[ExternalId]) -> ResolvedList:
    """The owned items for these ids, in the order given, each item once.

    An id repeated in the input is looked up once, so a missing title asked for
    twice counts as one unresolved title: ``unresolved`` answers "how many of
    this list's titles is the library missing", which is the number a dry-run
    preview shows. An id in a namespace the index does not carry resolves to
    nothing and is counted like any other miss -- an unknown namespace is a
    definition that will never match, not a crash mid-run.
    """
    items: list[object] = []
    seen_ids: set[ExternalId] = set()
    seen_items: set[object] = set()
    unresolved = 0
    for external in ids:
        if external in seen_ids:
            continue
        seen_ids.add(external)
        namespace, value = external
        item = index.get(namespace, {}).get(value)
        if item is None:
            unresolved += 1
            continue
        identity = _identity(item)
        if identity in seen_items:
            continue
        seen_items.add(identity)
        items.append(item)
    return ResolvedList(items, unresolved)
