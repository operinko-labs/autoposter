"""``filter_values``: the item view -- the predicate model over real items.

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
does not. That is the silent N+1 this rule forbids, and it hides perfectly: the
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
1.43.4.10903-e5521bd8c, 1955 movies / 284 shows) resolved the table's seven
``probe`` cells. One shipped and six deferred, which is close to the reverse of what the
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

**Phase B moved five of those six**, and the history above is kept rather than
rewritten because it is still why they are not read from the listing. What
changed is that a second read exists: the batched
``/library/metadata/{k1,k2,...}`` endpoint returns ``<Genre>``, ``<Label>``,
``<Collection>`` and ``<Stream>`` in full, and one such request answers up to
200 items (``plex.client.fetch_tag_index``). So ``genre``, ``label``,
``collection``, ``audio_language`` and ``subtitle_language`` are
``tier2-batched``: read HERE, from the ``ItemTags`` the engine's enrichment
pass fetched once for the definition's resolved set, and never from the item.
The zero-requests rule above is untouched by that -- this module still makes
no request; the engine made one, before evaluation, for the whole set.

``network`` alone stays deferred -- five batched, one stranded -- and its reason
was never truncation: Plex 1.43.4 emits the attrib in neither the listing nor
``/library/metadata``, so there is no read at any tier for the batch to be a
better version of.

**Phase B also added three tier-1 rows, from a second probe.** The 9a probe
above never asked whether ``viewCount``/``lastViewedAt``/``userRating`` reach
the section listing, which is why ``plays`` and ``last_played`` sat on
``unprobed`` -- a tier that means "nobody measured", not "we tried". Phase B's
probe (d) walked both listings raw
(``docs/research/plex-batch-probe/README.md``) and measured all three
PRESENT-WHEN-SET: ``viewCount`` on 79 of 1962 movies and 55 of 286 shows,
``lastViewedAt`` on 98 and 64, ``userRating`` on 2 and 0. That is presence
only, not agreement with ``/library/metadata``: the ``listing`` call for these
three per-account scalars is an inference from 9a's finding that disagreements
were child elements, never scalar row attribs, not a fresh measurement. So
``plays``, ``last_played`` and ``user_rating`` are ``listing``, read here with
the same ``object.__getattribute__`` no-reload discipline as the nine before
them, and each row in ``filters.py`` argues its own case for why sparse
presence is safe for it -- they do not share one.

All three are PER-ACCOUNT: the value belongs to whichever account the pass
token authenticated as. That is a property no other row in this table has, and
it is on each row rather than only here.
"""
from autoposter.collections.filters import BY_NAME, FILTER_ATTRIBUTES

__all__ = [
    "BATCHED_ATTRIBUTES",
    "DEFERRED_ATTRIBUTES",
    "SHIPPED_ATTRIBUTES",
    "AttributeNotInListing",
    "EnrichmentNotLoaded",
    "PlexItemView",
]


class AttributeNotInListing(LookupError):
    """Asked for a value tier 1 deliberately cannot read.

    Raised, never returned as missing. Config load refuses these, so
    reaching this exception at run time means a filter got past that check.
    """


# The table's own name for each shipped attribute -> the plexapi attribute the
# value comes from. Plain ``_loadData`` attributes, all of them: plexapi has
# already cast them (``year`` to int, the ratings to float, ``addedAt`` and
# ``originallyAvailableAt`` to datetime), so there is nothing to parse here and
# nothing that could disagree with what the rest of the application sees.
#
# The last three are phase B's, and the CAST is the whole of why they are safe
# on a sparse attrib (``Video._loadData``, plexapi 4.18.2):
#
#     self.viewCount    = utils.cast(int, data.attrib.get('viewCount', 0))
#     self.lastViewedAt = utils.toDatetime(data.attrib.get('lastViewedAt'))
#     self.userRating   = utils.cast(float, data.attrib.get('userRating'))
#
# ``viewCount`` carries a DEFAULT, so an unwatched item reads 0 plays and never
# missing; the other two carry none, so an item with no value reads None and
# the table's int/float/date missing rule excludes it under every operator.
# Both behaviours are Kometa's own, because Kometa reads the same three
# attributes through the same plexapi -- see the rows in ``filters.py``.
_LISTING_ATTRIBS: dict[str, str] = {
    "year": "year",
    "audience_rating": "audienceRating",
    "critic_rating": "rating",
    "content_rating": "contentRating",
    "added": "addedAt",
    "release": "originallyAvailableAt",
    "studio": "studio",
    "plays": "viewCount",
    "last_played": "lastViewedAt",
    "user_rating": "userRating",
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


def _duration_minutes(item: object) -> float | None:
    """Plex's milliseconds as Kometa's minutes -- the exact quotient, NOT
    rounded (SETTLED-BY-ORACLE).

    This rounded at first, on the argument that ``ms / 60000`` lands on a whole
    number for almost no real runtime and that handing the float onwards leaves
    ``duration.eq`` comparing floats for exact equality. Both halves of that are
    true and it is still the wrong call, because rounding moves the RANGE
    operators: Kometa's own conversion is ``test_number /= 60000`` with nothing
    after it (plex.py:2923-2926), so a 149.7-minute film passes
    ``duration.lt: 150`` there and failed here. The oracle found exactly
    that item. Every runtime within half a minute of a threshold was answered
    wrongly -- roughly one item in a hundred and twenty for an integer
    threshold, always at the boundary, which is the least visible place to be
    wrong.

    So ``duration.eq: 90`` is now a float-equality test against a value almost
    no film has, and that is Kometa's behaviour too (``value != data`` over the
    same quotient, util.py:626). The table's row says so; an operator who wants
    "about 90 minutes" writes a range.
    """
    milliseconds = _listing_value(item, "duration")
    if milliseconds is None:
        return None
    return milliseconds / 60000


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


def _versions(item: object) -> int | None:
    """How many `<Media>` versions this item carries -- Kometa's `versions`
    filter. The same reload-free listing read `_resolutions` uses, counted
    rather than valued. Answers None (missing), not 0, for an item with no
    `<Media>` at all -- exactly as `_resolutions` does; `filters._is_missing`
    treats both the same regardless of the attribute's type."""
    media = _listing_value(item, "media")
    if not media:
        return None
    return len(media)


def _aspect(item: object) -> float | None:
    """The item's aspect ratio -- Kometa's `aspect` filter, which reads the
    same `<Media aspectRatio=...>` attrib (`plex.py:200`) and compares it
    client-side (`builder.py:474` puts `aspect` in `float_attributes`).

    ADJUDICATION A-2, the one question the C2b recon left open. A
    multi-version item has no single aspect ratio, and `aspect` is a `float`
    -- it cannot answer a tuple the way `resolution`'s `tag` type does. The
    rule is the SAME media-selection walk `_resolutions` above makes -- every
    `<Media>` child, in listing order, reload-free -- narrowed to a scalar by
    taking the FIRST version that carries the attrib. A single-version item
    (1905 of the production library's 1955 movies, per the histogram recorded
    on the `resolution` row) therefore answers its only version, and a scope
    original beside an open-matte re-encode answers the original, which is
    the version Plex lists first and the one an operator means by "the"
    version.

    No cast: plexapi's `Media._loadData` already runs `utils.cast(float, ...)`
    over this attrib, exactly as it does for the ratings and `year` in
    `_LISTING_ATTRIBS` above, so the value arrives typed and an absent attrib
    arrives as None.

    Answers None -- missing, never 0.0 -- for an item with no `<Media>` at
    all AND for one whose versions carry no `aspectRatio`, which is the
    common unanalysed-file case: Plex omits the attrib until it has analysed
    the file. `filters._is_missing` excludes a missing `float` under EVERY
    operator, `.not` included, so an unanalysed item draws no aspect badge
    rather than a wrong one.
    """
    media = _listing_value(item, "media")
    if not media:
        return None
    for one in media:
        value = getattr(one, "aspectRatio", None)
        if value is not None:
            return value
    return None


def _passthrough(attrib: str):
    """The accessor for a row that is just a listing attrib under another name."""
    return lambda item: _listing_value(item, attrib)


_ACCESSORS = {name: _passthrough(attrib) for name, attrib in _LISTING_ATTRIBS.items()}
_ACCESSORS["duration"] = _duration_minutes
_ACCESSORS["resolution"] = _resolutions
_ACCESSORS["versions"] = _versions
_ACCESSORS["aspect"] = _aspect

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
BATCHED_ATTRIBUTES: tuple[str, ...] = tuple(
    row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-batched"
)

# Table attribute name -> the ItemTags field the enrichment carries it as.
_BATCHED_FIELDS: dict[str, str] = {
    "genre": "genres",
    "label": "labels",
    "collection": "collections",
    "audio_language": "audio_languages",
    "subtitle_language": "subtitle_languages",
}

# Table attribute name -> the ``facts_read.ItemFactsValues`` field carrying it
# (roadmap row 156). The two are the same word by construction -- that
# dataclass is named for the TABLE, not for the ``item_facts`` columns -- so
# this is an identity map and exists as a membership TEST rather than as a
# translation: it is what tells ``get`` that an attribute belongs to the facts
# branch. The column translation lives in ``facts_read._COLUMNS``, once.
_FACTS_FIELDS: dict[str, str] = {
    "common_sense_rating": "common_sense_rating",
    "imdb_rating": "imdb_rating",
    "tmdb_rating": "tmdb_rating",
}


class EnrichmentNotLoaded(LookupError):
    """A tier-2 attribute was read on a view built without its enrichment.

    Unreachable when the engine is doing its job -- ``_run_one`` refuses the
    definition before evaluation if any resolved item lacks enrichment -- so
    reaching this means a caller built the view by hand. Raised, never
    answered with None: None means "this item has no value", which the table
    turns into a defined match result, and that is exactly the wrong-but-
    plausible answer the tier system exists to prevent.
    """


class PlexItemView:
    """``ItemView`` over one resolved plexapi item, plus (optionally) the
    item's batched enrichment for the tier-2 rows.

    ``tags`` is a ``plex.client.ItemTags`` or None. None is the tier-1-only
    view every caller before phase B built, and it stays legal: a definition
    filtering on listing rows alone needs no enrichment and must not pay for
    one.

    ``facts`` is a ``facts_read.ItemFactsValues`` or None, on exactly ``tags``'
    terms: None is the view every caller before roadmap row 156 built and it
    stays legal, because a definition naming no facts row must not pay for a
    database read. An item that genuinely has no facts is
    ``ItemFactsValues()`` -- a different object and a different answer.
    """

    def __init__(self, item: object, tags=None, facts=None) -> None:
        self._item = item
        self._tags = tags  # plex.client.ItemTags | None
        self._facts = facts  # facts_read.ItemFactsValues | None

    def get(self, attribute: str, /) -> object | None:
        field = _BATCHED_FIELDS.get(attribute)
        if field is not None:
            if self._tags is None:
                raise EnrichmentNotLoaded(
                    f"{attribute!r} is a tier-2 attribute and this view was "
                    "built without its enrichment -- the engine fetches one "
                    "batched read per resolved set; a direct caller passes "
                    "`tags=` (see collections/enrichment.ensure_tags)"
                )
            # An EMPTY family is this item having no value of that kind, which
            # is a different thing from the enrichment being absent -- the
            # branch above is that one. Both are answers here; neither reaches
            # the item.
            #
            # ``or None`` is the VIEW's contract rather than a change of
            # membership: ``filters._is_missing`` already reads an empty
            # sequence as missing for a ``tag`` attribute, so ``()`` and
            # ``None`` evaluate identically. What it buys is one shape for "no
            # value" across both tiers -- every tier-1 accessor answers None --
            # so a direct reader of ``get`` has one thing to test for.
            values = getattr(self._tags, field)
            return tuple(values) or None
        field = _FACTS_FIELDS.get(attribute)
        if field is not None:
            if self._facts is None:
                raise EnrichmentNotLoaded(
                    f"{attribute!r} is a facts-backed attribute and this view "
                    "was built without its values -- the engine reads them "
                    "once per pass for the resolved set; a direct caller "
                    "passes `facts=` (see collections/facts_read.ensure_facts)"
                )
            # None here is "this item has no value", which is the answer for a
            # NULL column, for an item with no facts row and for an item the
            # sync has not seen -- three states the missing rule treats identically.
            # The NOT-LOADED case is the branch above; the two must never
            # collapse.
            return getattr(self._facts, field)
        accessor = _ACCESSORS.get(attribute)
        if accessor is None:
            row = BY_NAME.get(attribute)
            if row is None:
                why = "it is not one of the table's attributes at all"
            elif row.source == "tier2-deferred":
                # Phase B narrowed this from "the listing is incomplete" -- the
                # batched read answers that now, for the five rows it moved --
                # to the one thing the batch cannot fix. ``network`` is the
                # only row on this tier and the copy describes it exactly.
                why = (
                    "Plex emits it nowhere on this server (0/284 shows in the "
                    "listing AND absent from /library/metadata -- 9a's probe), "
                    "so no read at any tier can answer it; a TVDb/TMDb-sourced "
                    "equivalent would be a different attribute under a distinct "
                    "name"
                )
            else:
                # The request-per-item cost belongs to the tier-2 branch and is
                # not true here: a ``search-only`` row has nothing to read per
                # item -- that is what the tier means -- and an ``unprobed`` one
                # has never been measured. Each names its own tier and stops.
                why = f"its source tier is {row.source!r}"
            raise AttributeNotInListing(
                f"{attribute!r} has no accessor at any tier: {why}. Filterable "
                "now: " + ", ".join(
                    SHIPPED_ATTRIBUTES + BATCHED_ATTRIBUTES + tuple(_FACTS_FIELDS)
                )
            )
        return accessor(self._item)
