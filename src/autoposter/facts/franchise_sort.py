"""Roadmap row 268 -- the sort title a franchise position becomes.

Plex sorts a library by ``titleSort``, a string, and generates it from the
title by dropping a leading article -- so "The Lord of the Rings: The Two
Towers" files under L, and the three films land in alphabetical rather than
franchise order. The sort title this module derives is ``"<name> <NN>"``: the
franchise name and the film's one-based, zero-padded position in it, as the
source lists it. String order is then the source's order, and the franchise
stays together at its own letter.

Pure: a function of the collection's order and the item's id, no config and
no I/O, so it is tested by table.
"""
from autoposter.facts.tmdb_facts import CollectionOrder

# TMDb names every franchise collection "<name> Collection". Dropped so the
# sort title reads as the franchise and not as its container.
_SUFFIX = " collection"

# Plex's own rule for deriving ``titleSort`` from a title, applied here to the
# franchise name for the same reason Plex applies it: a locked sort title is
# taken verbatim, so an article left on the front would move the whole
# franchise from L to T.
_ARTICLES = ("the ", "a ", "an ")


def franchise_sort_title(order: CollectionOrder | None, tmdb_id: int | None) -> str | None:
    """``"Alien 02"`` for the second film of the Alien franchise, or ``None``.

    ``None`` when there is no order to place the film in, when the film is
    absent from its own collection's parts (TMDb's two records disagreeing --
    a real state, and one this service must not paper over with a guess), or
    when the collection has no usable name.
    """
    if order is None or tmdb_id is None or tmdb_id not in order.parts:
        return None
    name = order.name.strip()
    if name.lower().endswith(_SUFFIX) and len(name) > len(_SUFFIX):
        name = name[: -len(_SUFFIX)].rstrip()
    lowered = name.lower()
    for article in _ARTICLES:
        if lowered.startswith(article) and len(name) > len(article):
            name = name[len(article):].lstrip()
            break
    if not name:
        return None
    width = max(2, len(str(len(order.parts))))
    return f"{name} {order.parts.index(tmdb_id) + 1:0{width}d}"
