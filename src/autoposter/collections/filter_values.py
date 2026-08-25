"""``filter_values``: the item view -- Task 1's predicate model over real items.

``filters.evaluate`` reads an ``ItemView``, a mapping-like accessor keyed on the
table's attribute names. This module is the one implementation of that protocol
that touches ``plexapi``: it turns a resolved, LIVE plexapi item into the values
the table declares, and it does so under one hard rule --

    **reading a filter value never costs a Plex request.**

The engine walks the library exactly once, in ``resolve.build_owned_index``'s
single ``section.all()``, and the items it hands downstream are *partial*
objects. plexapi's ``PlexPartialObject.__getattribute__`` (base.py:650-668)
reloads such an object -- one synchronous HTTP GET -- whenever an attribute
reads back as ``None`` or ``[]``, because it cannot tell "absent" from "not
fetched yet". An accessor written the obvious way (``item.year``) is therefore
fine on every item that HAS a year and one request per item on every item that
does not. That is the silent N+1 the plan forbids, and it hides perfectly: the
suite's hand-written fakes have no such behaviour, and a real library shows it
only as a run that got slow.

Two things make the rule structural rather than a convention:

- ``_listing_value`` reads through ``object.__getattribute__``, which bypasses
  ``PlexPartialObject.__getattribute__`` entirely. Every value below comes from
  the listing XML plexapi already parsed into the object (``_loadData``
  attributes and ``cached_data_property`` children, both computed from
  ``self._data``); no path through this module can reach ``_reload``.
- Only the families THE PROBE found in the listing get an accessor at all. The
  rest refuse -- see ``AttributeNotInListing``.

**The probe** (read-only, production server ``ShadowPlex``, Plex
1.43.4.10903-e5521bd8c, 1955 movies / 284 shows; full log in
``.superpowers/sdd/task-2-report.md``) resolved the table's seven ``probe``
cells. One shipped and six deferred, which is close to the reverse of what the
table expected:

- ``resolution`` SHIPS. ``<Media videoResolution=...>`` is on all 1955 movies
  -- 2009 Media elements, every one carrying the attrib, and the listing's
  Media set matched ``/library/metadata`` with zero disagreements on 25 items
  sampled, including multi-version items in the sample.
- ``genre`` DEFERS, and this is the finding worth reading twice. The listing
  *does* carry ``<Genre>`` -- but TRUNCATED to at most two per item. Never
  three, in either section. The metadata endpoint for the same items returns
  three and four. A listing-backed genre accessor would not fail; it would
  quietly answer "Action, Crime" for a film whose third genre is Thriller,
  and ``genre: Thriller`` would build a full, plausible, wrong collection.
  Twenty-two listing parameters were tried; none changed it.
- ``label`` and ``collection`` DEFER for the same class of reason (labels are
  stripped from the listing outright, collections appear on 257 of 1955 movies
  and disagree with the metadata endpoint on 6 of 20 sampled).
- ``audio_language``/``subtitle_language`` DEFER as the table predicted: zero
  ``<Stream>`` elements across 200 listing movies.
- ``network`` DEFERS and is *stranded*: Plex 1.43.4 emits no ``network``
  attrib at all, in the listing OR the metadata endpoint, so no amount of
  reloading would produce a value.

A deferred family therefore has no accessor, and asking for one raises rather
than answering ``None``. That distinction is the whole point: ``None`` means
"this item has no value", which the table turns into a defined match result, so
returning it for an attribute we simply cannot read would be indistinguishable
from a real answer -- exactly the wrong-but-plausible outcome deferring exists
to prevent.
"""
from autoposter.collections.filters import FILTER_ATTRIBUTES

__all__ = [
    "DEFERRED_ATTRIBUTES",
    "SHIPPED_ATTRIBUTES",
    "AttributeNotInListing",
    "PlexItemView",
]


class AttributeNotInListing(LookupError):
    """Asked for a value tier 1 deliberately cannot read.

    Raised, never returned as missing. Task 3 refuses these at config load, so
    reaching this exception at run time means a filter got past that check.
    """


# The table's own name for each shipped attribute -> the plexapi attribute the
# value comes from. Plain ``_loadData`` attributes, all of them: plexapi has
# already cast them (``year`` to int, the ratings to float, ``addedAt`` and
# ``originallyAvailableAt`` to datetime), so there is nothing to parse here and
# nothing that could disagree with what the rest of the application sees.
_LISTING_ATTRIBS: dict[str, str] = {
    "year": "year",
    "audience_rating": "audienceRating",
    "critic_rating": "rating",
    "content_rating": "contentRating",
    "added": "addedAt",
    "release": "originallyAvailableAt",
    "studio": "studio",
}


def _listing_value(item: object, name: str) -> object | None:
    """One already-parsed value off the item, with no chance of a reload.

    ``object.__getattribute__`` is the load-bearing part: it is exactly what
    ``PlexPartialObject.__getattribute__`` calls first, minus the reload branch
    that follows. ``AttributeError`` is missing rather than an error because
    the accessors are total across item kinds -- a ``Show`` has no ``media``,
    and "shows have no resolution" is an answer, not a bug.
    """
    try:
        return object.__getattribute__(item, name)
    except AttributeError:
        return None


def _duration_minutes(item: object) -> int | None:
    """Plex's milliseconds as Kometa's minutes.

    Rounded here, once, because the table pins the view's duration to ``int``:
    ``ms / 60000`` lands on a whole number for almost no real runtime, and
    handing the float onwards would leave ``duration.eq`` comparing floats for
    exact equality across a division this layer owns.
    """
    milliseconds = _listing_value(item, "duration")
    if milliseconds is None:
        return None
    return round(milliseconds / 60000)


def _resolutions(item: object) -> tuple[str, ...] | None:
    """Every version's ``videoResolution``, as a tag list.

    A list rather than one value because 50 of the production library's movies
    carry more than one ``<Media>``, and a 4k re-encode next to a 1080p
    original must answer for both -- the tag comparison is any-of already.
    ``Media`` is a plain ``PlexObject``, not a partial one, so reading
    ``videoResolution`` off it carries no reload guard of its own.
    """
    media = _listing_value(item, "media")
    if not media:
        return None
    found = tuple(
        value
        for value in (getattr(one, "videoResolution", None) for one in media)
        if value
    )
    return found or None


def _passthrough(attrib: str):
    """The accessor for a row that is just a listing attrib under another name."""
    return lambda item: _listing_value(item, attrib)


_ACCESSORS = {name: _passthrough(attrib) for name, attrib in _LISTING_ATTRIBS.items()}
_ACCESSORS["duration"] = _duration_minutes
_ACCESSORS["resolution"] = _resolutions

# Derived from the table, not restated: a row whose source tier the probe moved
# changes these, and ``tests/test_collection_filter_values.py`` fails until the
# accessors match. ``SHIPPED_ATTRIBUTES`` is in table order so it reads as a
# subset of the table rather than an independent list.
SHIPPED_ATTRIBUTES: tuple[str, ...] = tuple(
    row.name for row in FILTER_ATTRIBUTES if row.source == "listing"
)
DEFERRED_ATTRIBUTES: tuple[str, ...] = tuple(
    row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-deferred"
)


class PlexItemView:
    """``ItemView`` over one resolved plexapi item."""

    def __init__(self, item: object) -> None:
        self._item = item

    def get(self, attribute: str, /) -> object | None:
        accessor = _ACCESSORS.get(attribute)
        if accessor is None:
            raise AttributeNotInListing(
                f"{attribute!r} has no tier-1 accessor: the Plex section listing does not "
                "carry it completely enough to filter on (source tier 'tier2-deferred' -- "
                "see the row's note for the probe data), and reading it per item would cost "
                "one Plex request per item. Filterable now: " + ", ".join(SHIPPED_ATTRIBUTES)
            )
        return accessor(self._item)
