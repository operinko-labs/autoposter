"""The item view: Task 1's predicate model over real, live ``plexapi`` items.

Every fixture here is a REAL ``plexapi.video.Movie``/``Show`` built from XML
shaped like the production listing (the shapes are copied from the Task 2 probe
dump in ``.superpowers/sdd/task-2-report.md``), attached to a server object
that raises if anything asks it for a request. That is deliberate: a
hand-written fake item would answer every attribute happily and prove nothing
about the one property this module exists to guarantee -- that evaluating a
filter over a library costs ZERO Plex requests beyond the single
``section.all()`` walk the engine already pays.

The guarantee is tested from both sides, because either half alone can rot into
a test that cannot fail:

- ``test_no_shipped_accessor_can_reach_a_reload`` and
  ``test_a_hundred_item_library_evaluates_with_zero_plex_requests`` assert our
  side: nothing we read triggers plexapi's partial-object reload.
- ``test_the_reload_trap_these_accessors_dodge_is_still_real`` is the positive
  control asserting plexapi's side: ordinary attribute access on a missing
  value DOES still fire a request. Without it, a future plexapi that stopped
  reloading would turn both tests above green-and-vacuous.
"""
import datetime as dt
from xml.etree import ElementTree as ET

import pytest
from plexapi.video import Movie, Show

from autoposter.collections.filter_values import (
    DEFERRED_ATTRIBUTES,
    SHIPPED_ATTRIBUTES,
    AttributeNotInListing,
    PlexItemView,
    _ACCESSORS,
)
from autoposter.collections.filters import FILTER_ATTRIBUTES, evaluate, parse_filters

# --- the fixtures ------------------------------------------------------------
#
# ``initpath`` is the section listing, not the item's own key: that is what
# makes ``isFullObject()`` false and the reload guard live, exactly as it is
# for an item that came out of ``build_owned_index``'s walk.
LISTING = "/library/sections/1/all"
SHOW_LISTING = "/library/sections/2/all"

# Copied from the probe's raw dump of `2 Fast 2 Furious`, trimmed to the
# elements the table cares about. Note the two <Genre> children: the real
# listing carries two of that movie's three genres, which is the truncation
# that deferred the genre row.
MOVIE_XML = (
    '<Video ratingKey="158244" key="/library/metadata/158244" type="movie"'
    ' title="2 Fast 2 Furious" year="2003" audienceRating="6.9" rating="3.6"'
    ' contentRating="PG-13" addedAt="1700000000" originallyAvailableAt="2003-06-05"'
    ' duration="6353000" studio="Universal Pictures">'
    '<Media id="9" videoResolution="1080" width="1920"><Part id="1" file="/m.mkv"/></Media>'
    '<Genre tag="Action"/><Genre tag="Crime"/>'
    '<Country tag="Germany"/><Director tag="John Singleton"/><Role tag="Paul Walker"/>'
    "</Video>"
)

# The item with nothing on it: no year, no ratings, no studio, no Media. Every
# one of those reads as None/[] , which is precisely what plexapi's reload guard
# cannot tell apart from "not loaded yet" -- so this fixture is the one that
# would reload under a careless accessor.
BARE_MOVIE_XML = (
    '<Video ratingKey="2" key="/library/metadata/2" type="movie" title="Nothing Known"/>'
)

# ``childCount`` is not decoration: plexapi's ``Show._loadData`` reads
# ``self.childCount`` while building ``seasonCount``, and on a partial show
# that read fires the reload guard during construction. The real listing
# carries it, so the fixture does too.
SHOW_XML = (
    '<Directory ratingKey="7" key="/library/metadata/7/children" type="show"'
    ' title="Black Mirror" year="2011" audienceRating="8.3" contentRating="TV-MA"'
    ' addedAt="1700000000" originallyAvailableAt="2011-12-04" duration="3600000"'
    ' studio="Netflix" childCount="7" leafCount="33">'
    '<Genre tag="Drama"/><Genre tag="Mystery"/>'
    "</Directory>"
)


class PlexRequestMade(Exception):
    """Raised instead of performing a request, so any reload is a loud failure
    at the exact line that caused it rather than a slow test."""


class NoRequestsServer:
    """Stands in for ``PlexServer``. Counts, then refuses."""

    def __init__(self):
        self.calls = []

    def query(self, key, *args, **kwargs):
        self.calls.append(key)
        raise PlexRequestMade(key)


def a_movie(xml=MOVIE_XML, server=None):
    return Movie(server or NoRequestsServer(), ET.fromstring(xml), initpath=LISTING)


def a_show(xml=SHOW_XML, server=None):
    return Show(server or NoRequestsServer(), ET.fromstring(xml), initpath=SHOW_LISTING)


# --- the table's verdict is the module's shape --------------------------------


def test_an_accessor_exists_for_exactly_the_listing_rows():
    """The probe's verdict lives in the table, and this module is generated
    from it: a row that ships has an accessor, a row that deferred has none.
    Moving a row between tiers without doing the matching work fails here."""
    listing = tuple(row.name for row in FILTER_ATTRIBUTES if row.source == "listing")
    deferred = tuple(row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-deferred")

    assert SHIPPED_ATTRIBUTES == listing
    assert DEFERRED_ATTRIBUTES == deferred
    assert set(SHIPPED_ATTRIBUTES) & set(DEFERRED_ATTRIBUTES) == set()
    # The probe answered every row: no cell is still waiting on it.
    assert [row.name for row in FILTER_ATTRIBUTES if row.source == "probe"] == []


def test_the_runtime_accessor_map_matches_the_listing_rows():
    """The module docstring claims an accessor exists for exactly the families
    the probe shipped. ``SHIPPED_ATTRIBUTES`` alone does not bind that claim --
    it is derived from the same table ``_ACCESSORS`` is, by a separate path, so
    the two could drift apart without either test above noticing. This checks
    the actual runtime dict ``PlexItemView.get`` dispatches through, derived
    fresh from the table so a future tier move is still caught."""
    listing = {row.name for row in FILTER_ATTRIBUTES if row.source == "listing"}

    assert set(_ACCESSORS) == listing


def test_the_nine_shipped_families_are_named():
    """Spelled out rather than derived, so that the set of attributes an
    operator can actually filter on is reviewable in one line -- and so that
    Task 2's probe verdicts cannot drift silently into or out of tier 1."""
    assert SHIPPED_ATTRIBUTES == (
        "year",
        "resolution",
        "audience_rating",
        "critic_rating",
        "content_rating",
        "added",
        "release",
        "duration",
        "studio",
    )
    assert DEFERRED_ATTRIBUTES == (
        "genre",
        "audio_language",
        "subtitle_language",
        "label",
        "network",
        "collection",
    )


# --- the values ---------------------------------------------------------------


# ``added`` is not written as a datetime literal, and that is a finding rather
# than test convenience: Plex sends ``addedAt`` as a unix epoch and plexapi
# converts it with ``datetime.fromtimestamp``, which uses the RUNNER's local
# timezone. The same item therefore reads as a different wall-clock time -- and,
# within a couple of hours of midnight, a different calendar DATE -- depending
# on where the pass runs. Pinning the literal here would have pinned this
# machine's offset (it did, in this test's first run: +2 against the container's
# UTC). See the Task 2 report; the date-granular comparison in
# ``filters._as_calendar_date`` is where it would show.
@pytest.mark.parametrize(
    "attribute,expected",
    [
        ("year", 2003),
        ("audience_rating", 6.9),
        ("critic_rating", 3.6),
        ("content_rating", "PG-13"),
        ("added", dt.datetime.fromtimestamp(1700000000)),
        ("release", dt.datetime(2003, 6, 5)),
        ("studio", "Universal Pictures"),
        ("resolution", ("1080",)),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_each_shipped_family_reads_its_listing_value(attribute, expected):
    assert PlexItemView(a_movie()).get(attribute) == expected


def test_duration_is_whole_minutes_and_an_int_not_raw_milliseconds():
    """The table pins this: Plex's wire value is milliseconds, Kometa's filter
    is minutes, and the view rounds once here so ``duration.eq`` never compares
    an unrounded float. 6_353_000ms is 105.883... minutes."""
    value = PlexItemView(a_movie()).get("duration")

    assert value == 106
    assert isinstance(value, int) and not isinstance(value, bool)


def test_resolution_reads_every_version_of_a_multi_version_movie():
    """50 of the production library's 1955 movies carry more than one <Media>.
    A filter on resolution must see all of them, or a 4k re-encode alongside a
    1080p original would answer only for whichever came first."""
    xml = MOVIE_XML.replace(
        '<Media id="9" videoResolution="1080" width="1920"><Part id="1" file="/m.mkv"/></Media>',
        '<Media id="9" videoResolution="1080"><Part id="1" file="/a.mkv"/></Media>'
        '<Media id="10" videoResolution="4k"><Part id="2" file="/b.mkv"/></Media>',
    )

    assert PlexItemView(a_movie(xml)).get("resolution") == ("1080", "4k")


def test_a_show_reads_its_own_listing_values_and_has_no_resolution():
    """``resolution`` is a movie-only row, and a show carries no <Media> at all
    (0 of 284 in the probe). The accessor is still total over a show: it
    answers "missing" rather than raising or reaching for episodes."""
    view = PlexItemView(a_show())

    assert view.get("year") == 2011
    assert view.get("studio") == "Netflix"
    assert view.get("content_rating") == "TV-MA"
    assert view.get("duration") == 60
    assert view.get("resolution") is None


# --- the missing-value rule ---------------------------------------------------


@pytest.mark.parametrize("attribute", SHIPPED_ATTRIBUTES)
def test_every_accessor_is_total_over_an_item_that_has_nothing(attribute):
    """The listing attrib coverage is not 100% on the real server (5 movies
    have no studio, 14 no contentRating, 274 shows no critic rating), so
    "absent" is a real case, not a hypothetical. Every accessor answers None
    for it -- and answering is the point: the alternative is plexapi's reload."""
    assert PlexItemView(a_movie(BARE_MOVIE_XML)).get(attribute) is None


def test_the_missing_rule_from_the_table_falls_out_of_a_None_answer():
    """Task 1 owns the rule; this pins that the view feeds it correctly. A tag
    attribute with no value is dropped by a positive filter and kept by a
    negative one; a numeric one is dropped by both."""
    view = PlexItemView(a_movie(BARE_MOVIE_XML))

    assert evaluate(parse_filters({"content_rating": "PG-13"}), view) is False
    assert evaluate(parse_filters({"content_rating.not": "PG-13"}), view) is True
    assert evaluate(parse_filters({"year.gte": 2000}), view) is False
    assert evaluate(parse_filters({"year.not": 2000}), view) is False


# --- the deferred families refuse ---------------------------------------------


@pytest.mark.parametrize("attribute", DEFERRED_ATTRIBUTES)
def test_a_deferred_family_refuses_instead_of_answering_missing(attribute):
    """The dangerous failure is not an exception, it is a wrong answer. If the
    view returned None for ``genre``, a ``genre: Horror`` filter would quietly
    match nothing and a ``genre.not: Horror`` filter would quietly match
    everything -- a full, plausible, wrong collection. It refuses, loudly, and
    Task 3 turns that into a load-time refusal so no run ever reaches here."""
    with pytest.raises(AttributeNotInListing) as raised:
        PlexItemView(a_movie()).get(attribute)

    assert attribute in str(raised.value)
    assert "tier2-deferred" in str(raised.value)


def test_the_refusal_for_genre_is_not_that_the_item_lacks_genres():
    """The fixture's XML carries two <Genre> children and the refusal happens
    anyway: the reason is the probe's truncation verdict, not this item."""
    item = a_movie()
    assert len(ET.fromstring(MOVIE_XML).findall("Genre")) == 2

    with pytest.raises(AttributeNotInListing):
        PlexItemView(item).get("genre")


def test_an_attribute_outside_the_table_refuses_too():
    with pytest.raises(AttributeNotInListing):
        PlexItemView(a_movie()).get("director")


# --- the reload budget, from both sides ---------------------------------------


def test_the_reload_trap_these_accessors_dodge_is_still_real():
    """POSITIVE CONTROL. Ordinary attribute access on an absent value fires one
    request per item -- the N+1 this whole module is arranged to avoid. If
    plexapi ever stopped doing this, the two tests below would still pass while
    proving nothing, so this one has to fail loudly on that day instead."""
    server = NoRequestsServer()
    item = a_movie(BARE_MOVIE_XML, server)

    with pytest.raises(PlexRequestMade):
        item.year

    assert len(server.calls) == 1
    assert server.calls[0].startswith("/library/metadata/2")


def test_no_shipped_accessor_can_reach_a_reload():
    """THE TRIPWIRE. Read every shipped family off the item that has none of
    them -- the worst case, where every value is the falsy one plexapi cannot
    tell from "not loaded". Zero requests."""
    server = NoRequestsServer()
    view = PlexItemView(a_movie(BARE_MOVIE_XML, server))

    for attribute in SHIPPED_ATTRIBUTES:
        assert view.get(attribute) is None

    assert server.calls == []


def test_a_hundred_item_library_evaluates_with_zero_plex_requests():
    """The plex_trivial.py:70-73 pattern, at library scale: a filter naming
    every shipped family, evaluated over 100 partial items, half of which carry
    nothing. The count that matters is the request count, and it is zero."""
    server = NoRequestsServer()
    items = []
    for index in range(100):
        xml = MOVIE_XML if index % 2 == 0 else BARE_MOVIE_XML
        source_key = "158244" if index % 2 == 0 else "2"
        items.append(
            a_movie(xml.replace('ratingKey="%s"' % source_key, 'ratingKey="%d"' % index), server)
        )

    group = parse_filters(
        {
            "year.gte": 2000,
            "resolution": "1080",
            "audience_rating.gt": 5,
            "critic_rating.lt": 9,
            "content_rating": "PG-13",
            "added.after": "2000-01-01",
            "release.before": "2020-01-01",
            "duration.gte": 90,
            "studio": "Universal",
        }
    )
    results = [evaluate(group, PlexItemView(item)) for item in items]

    assert server.calls == []
    assert results.count(True) == 50
    assert results.count(False) == 50
