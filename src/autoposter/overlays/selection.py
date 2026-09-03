"""The overlay engine's SELECTION half: which definitions apply to this item.

Until this module, `badges/compose.py::_draw_definitions` drew EVERY
configured definition on EVERY badged item -- `OverlayDefinition` had no
condition, filter or applicability field at all. Every family roadmap row 100
names is a *conditional* overlay (Kometa expresses each as a `plex_search:`
or `filters:` block), so none of them was expressible. That is the gap this
module closes, and it closes it by REUSING a filter engine rather than
writing one.

**The grammar is `collections/filters.py`'s, imported, not re-implemented**
(adjudication A3). That module's parse/evaluate pair was proved against
Kometa 2.4.8's own filter code -- 120 items, member list against member list,
the `SETTLED-BY-ORACLE` verdicts on its rows -- and a second filter dialect
would be exactly the "same name, different filter" class its own docstring
rules out. `parse_condition` below is a thin narrowing in front of
`parse_filters`, not a parser.

**The VIEW is ours, and deliberately not `filter_values.PlexItemView`.** That
view is bound by one hard rule -- "reading a filter value never costs a Plex
request" -- because the collections engine walks the library once with
`section.all()` and holds PARTIAL plexapi objects, whose attribute access
silently reloads. The badge path is a richer per-item context: by the time
this view is built, `render/pipeline.py::apply_badges` has already called
`badges/values.py::media_info_from_plex`, which reloads the item and walks
`part.streams`, and it is handed `facts` and `plex_item` directly. So the
collections table's per-attribute source tiers and `kinds` restrictions are
LISTING constraints, and they do not bind here (the recon's adjudication
A3b): a show simply has no `media`, which the tag missing-value rule already
answers correctly, rather than needing a `kinds` column to forbid it.
`plex_item` is still very likely PARTIAL, though -- the badge path never
copies it -- so this view's own accessors read it the same load-bearing way
`filter_values.py`'s do (`object.__getattribute__`, imported directly rather
than re-implemented), even though the CLASS itself is not `PlexItemView`.

**The vocabulary is narrower than the collections block's, on purpose.**
`OVERLAY_ATTRIBUTES` is what THIS view can actually supply. An attribute that
parses as a collections filter but has no accessor here is refused at config
load naming what is available -- never answered as None, because None means
"this item has no value" and the table turns that into a defined match
result. A family that silently matches nothing is indistinguishable from a
family that is off.
"""
import json
from functools import lru_cache

from autoposter.collections.filter_values import _listing_value, _resolutions
from autoposter.collections.filters import (
    FilterGroup,
    evaluate,
    parse_filters,
    predicates,
)

__all__ = [
    "OVERLAY_ATTRIBUTES",
    "AttributeNotOnItem",
    "OverlayItemView",
    "compiled_condition",
    "parse_condition",
    "select",
]

# The attributes this view supplies, in `collections/filters.py`'s own
# spelling -- the key `evaluate` reads. Sub-phase C1's two:
#
# - `content_rating`: PLEX's certification string (adjudication A5), which is
#   what the six content-rating regionals match. Deliberately NOT
#   `ItemFacts.content_rating`, which is MDBList's Common Sense AGE rating --
#   a different value space. Kometa's alias buckets carry Plex agent
#   spellings (`gb/U`, `no/A`, `TV-Y`) Common Sense never emits, and the
#   `content_rating` row in `collections/filters.py` says exactly this.
# - `resolution`: every version's `videoResolution`, which is what
#   `direct_play` regexes.
#
# Later slices append; each addition owes a source note here and an agreement
# pin against `filter_values.PlexItemView` if that view supplies it too.
OVERLAY_ATTRIBUTES: tuple[str, ...] = ("content_rating", "resolution")


class AttributeNotOnItem(LookupError):
    """Asked for a value this view cannot supply.

    Raised, never returned as missing -- `parse_condition` refuses these at
    config load, so reaching this at render time means a condition got past
    that check.
    """


class OverlayItemView:
    """`filters.ItemView` over the badge pipeline's own per-item context.

    `media` is a `badges.values.MediaInfo`, `facts` an `ItemFacts`-shaped
    object (or None), `plex_item` the live plexapi item `apply_badges` was
    handed. All three are already in hand at the call site; this view makes
    no request of its own -- and reads `plex_item` the same load-bearing way
    `filter_values.py` does (see `get()` below) so that stays true even for
    an unrated movie whose `.media` is populated but whose `.contentRating`
    is unset.
    """

    def __init__(self, media, facts=None, plex_item=None) -> None:
        self._media = media
        self._facts = facts
        self._plex_item = plex_item

    def get(self, attribute: str, /) -> object | None:
        """Reads `plex_item` through `filter_values.py`'s own accessors, not
        a plain `getattr`, and that is load-bearing, not a style choice.

        `plex_item` is very likely a `PlexPartialObject`: `apply_badges`
        hands this view the SAME live item it uploads to, not a copy.
        `media_info_from_plex` reloading it beforehand only guarantees
        `.media` and its streams are populated -- it does nothing for
        `.contentRating`, and a plain `getattr` on an UNSET attribute of a
        partial object trips `PlexPartialObject.__getattribute__`'s reload
        branch: one synchronous blocking `requests` GET, inline, on the
        event loop (`select()` is called directly in `apply_badges`, not
        through `asyncio.to_thread`). `filter_values._listing_value` and
        `filter_values._resolutions` exist to read a partial object WITHOUT
        that risk (`object.__getattribute__`, which is exactly what
        `PlexPartialObject.__getattribute__` itself calls first, minus the
        reload branch that follows it) -- reusing them here closes that
        hazard and, for `resolution`, closes the duplication a straight
        `filter_values` import would otherwise leave (R2): one accessor,
        not two copies that can drift.
        """
        if attribute == "content_rating":
            return _listing_value(self._plex_item, "contentRating") or None
        if attribute == "resolution":
            return _resolutions(self._plex_item)
        raise AttributeNotOnItem(
            f"{attribute!r} is not an attribute an overlay condition can "
            "read on this service. Available: " + ", ".join(OVERLAY_ATTRIBUTES)
        )
