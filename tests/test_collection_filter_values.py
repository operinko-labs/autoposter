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

Phase B added a second tier to the same module and the same rule covers it:
the five ``tier2-batched`` families are read from the ``ItemTags`` the
engine's enrichment pass fetched once for the definition's resolved set, so a
batched accessor must not touch the plexapi item at all -- see
``test_batched_accessor_reads_the_enrichment_never_the_item``, whose fixture
raises on every attribute read rather than counting requests.
"""
import datetime as dt
from xml.etree import ElementTree as ET

import pytest
from plexapi.video import Movie, Show

from autoposter.collections.filter_values import (
    BATCHED_ATTRIBUTES,
    DEFERRED_ATTRIBUTES,
    SHIPPED_ATTRIBUTES,
    AttributeNotInListing,
    EnrichmentNotLoaded,
    PlexItemView,
    _ACCESSORS,
    _BATCHED_FIELDS,
)
from autoposter.collections.filters import FILTER_ATTRIBUTES, evaluate, parse_filters
from autoposter.plex.client import ItemTags

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

# The same movie after two plays and a rating -- phase B's three rows, which
# ``MOVIE_XML`` lacks for the same reason most real listing rows lack them
# (probe d: ``viewCount`` on 79 of 1962 movies, ``lastViewedAt`` on 98,
# ``userRating`` on 2). All three are ATTRIBS on the row, not children.
PLAYED_MOVIE_XML = MOVIE_XML.replace(
    ' studio="Universal Pictures">',
    ' studio="Universal Pictures" viewCount="2" lastViewedAt="1740000000"'
    ' userRating="9.0">',
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
    from it: a row that ships has an accessor, a row that deferred has none,
    and a row phase B moved to the batched tier reads its enrichment instead.
    Moving a row between tiers without doing the matching work fails here."""
    listing = tuple(row.name for row in FILTER_ATTRIBUTES if row.source == "listing")
    batched = tuple(row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-batched")
    deferred = tuple(row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-deferred")

    assert SHIPPED_ATTRIBUTES == listing
    assert BATCHED_ATTRIBUTES == batched
    assert DEFERRED_ATTRIBUTES == deferred
    # Three disjoint tiers, and disjointness is what makes ``get``'s dispatch
    # unambiguous: an attribute in two of them would have two answers.
    assert set(SHIPPED_ATTRIBUTES) & set(DEFERRED_ATTRIBUTES) == set()
    assert set(SHIPPED_ATTRIBUTES) & set(BATCHED_ATTRIBUTES) == set()
    assert set(BATCHED_ATTRIBUTES) & set(DEFERRED_ATTRIBUTES) == set()
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


def test_the_runtime_batched_field_map_matches_the_batched_rows():
    """The same discipline one tier along. ``_BATCHED_FIELDS`` is the dict
    ``get`` dispatches a tier-2 read through, and it maps the table's attribute
    name to the ``ItemTags`` field the enrichment carries it as -- so a row
    moved onto ``tier2-batched`` without an entry here would fall through to
    the ``_ACCESSORS`` branch and refuse as if it had no accessor at all, and a
    field renamed on ``ItemTags`` would raise ``AttributeError`` mid-pass."""
    batched = {row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-batched"}

    assert set(_BATCHED_FIELDS) == batched
    for field in _BATCHED_FIELDS.values():
        assert hasattr(ItemTags((), (), (), (), ()), field), field


def test_the_twelve_shipped_families_are_named():
    """Spelled out rather than derived, so that the set of attributes an
    operator can actually filter on is reviewable in one line -- and so that
    Task 2's probe verdicts cannot drift silently into or out of tier 1.

    The last three are phase B's: probe (d) walked both section listings and
    found ``viewCount``/``lastViewedAt``/``userRating`` present-when-set, which
    is the verdict ``unprobed`` was waiting for."""
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
        "plays",
        "last_played",
        "user_rating",
    )
    assert BATCHED_ATTRIBUTES == (
        "genre",
        "audio_language",
        "subtitle_language",
        "label",
        "collection",
    )
    # One row left, and it is the stranded one: phase B's batched read moved
    # the other five, and cannot move this one because Plex 1.43.4 emits
    # ``network`` in neither the listing nor /library/metadata.
    assert DEFERRED_ATTRIBUTES == ("network",)


# --- the values ---------------------------------------------------------------


# ``added`` is not written as a datetime literal, and that is a finding rather
# than test convenience: Plex sends ``addedAt`` as a unix epoch and plexapi
# converts it with ``datetime.fromtimestamp``, which uses the RUNNER's local
# timezone. The same item therefore reads as a different wall-clock time -- and,
# within a couple of hours of midnight, a different calendar DATE -- depending
# on where the pass runs. Pinning the literal here would have pinned this
# machine's offset (it did, in this test's first run: +2 against the container's
# UTC). See the Task 2 report; the moment comparison in ``filters._as_moment``
# is where it would show -- and since Task 4 made that comparison keep the time
# of day, it now shows at any hour, not only near midnight.
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


def test_duration_is_the_exact_quotient_in_minutes_not_raw_milliseconds():
    """The table pins this: Plex's wire value is milliseconds, Kometa's filter
    is minutes, and the view divides -- WITHOUT rounding. 6_353_000ms is
    105.8833... minutes and stays that.

    It was rounded until Task 4's oracle (see
    ``tests/test_collection_filter_oracle.py``). Rounding kept ``duration.eq``
    off float equality, but it moved every range comparison for a runtime
    within half a minute of the threshold: Kometa's conversion is
    ``test_number /= 60000`` with nothing after it, so a 149.7-minute film
    passes ``duration.lt: 150`` there and failed here. The oracle's library
    carries exactly that film.
    """
    value = PlexItemView(a_movie()).get("duration")

    assert value == pytest.approx(105.88333333)
    assert isinstance(value, float)


def test_a_rounded_duration_would_answer_a_boundary_range_wrongly():
    """The reason the line above is not just a type assertion, stated as the
    behaviour it protects. 8_982_000ms is 149.7 minutes; rounded it is 150,
    and ``duration.lt: 150`` then drops a film Kometa keeps."""
    xml = MOVIE_XML.replace('duration="6353000"', 'duration="8982000"')
    view = PlexItemView(a_movie(xml))

    assert view.get("duration") == pytest.approx(149.7)
    assert evaluate(parse_filters({"duration.lt": 150}), view) is True
    assert round(view.get("duration")) == 150


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


@pytest.mark.parametrize(
    "attribute", [name for name in SHIPPED_ATTRIBUTES if name != "plays"]
)
def test_every_accessor_is_total_over_an_item_that_has_nothing(attribute):
    """The listing attrib coverage is not 100% on the real server (5 movies
    have no studio, 14 no contentRating, 274 shows no critic rating), so
    "absent" is a real case, not a hypothetical. Every accessor answers None
    for it -- and answering is the point: the alternative is plexapi's reload.

    ``plays`` is excluded by name rather than quietly: it is the ONE shipped
    row whose absent value is not None, and the test below is its own."""
    assert PlexItemView(a_movie(BARE_MOVIE_XML)).get(attribute) is None


def test_the_per_account_families_read_their_listing_values():
    """Phase B's three rows, from a listing row that has all three. They are
    absent from ``MOVIE_XML`` for the same reason they are absent from most
    real rows -- probe (d) found ``viewCount`` on 79 of 1962 movies,
    ``lastViewedAt`` on 98 and ``userRating`` on 2."""
    view = PlexItemView(a_movie(PLAYED_MOVIE_XML))

    assert view.get("plays") == 2
    assert view.get("last_played") == dt.datetime.fromtimestamp(1740000000)
    assert view.get("user_rating") == 9.0


def test_an_unplayed_item_reads_zero_plays_where_the_other_two_read_missing():
    """The three split on what "absent" means, and the split is plexapi's
    rather than this module's: ``viewCount`` is cast with a DEFAULT of 0
    (``Video._loadData``, ``utils.cast(int, data.attrib.get('viewCount', 0))``)
    so an unwatched item reads zero plays and never missing, while
    ``lastViewedAt`` and ``userRating`` are cast with no default and read None.

    That is why the probe's "never `0` in the listing" finding is safe rather
    than alarming: Plex omits the attrib for an unwatched item and plexapi
    writes the zero back. Kometa reads the same three attributes through the
    same plexapi, so an operator gets upstream's own answer here."""
    view = PlexItemView(a_movie(BARE_MOVIE_XML))

    assert view.get("plays") == 0
    assert view.get("last_played") is None
    assert view.get("user_rating") is None


def test_a_never_played_item_is_excluded_the_way_kometa_excludes_it():
    """The verdict phase B's task 7 had to take, pinned as behaviour.

    ``last_played`` is one of Kometa's three ``date_filters``
    (``builder.py:377-447``), and its ``check_filter`` branch is
    ``if is_date_filter(getattr(item, 'lastViewedAt'), ...): return False`` --
    where ``is_date_filter`` opens ``if value is None: return True``
    (``util.py:598-600``), BEFORE it reads the modifier. So a never-played item
    fails a ``last_played`` filter upstream under every modifier, ``.not``
    included, and this table's ``date`` missing rule
    (``_MISSING_ALWAYS_EXCLUDES``) is that same behaviour. The two are pinned
    against Kometa's own transcribed code in
    ``.superpowers/oracle/9a/kometa_oracle.py``.

    ``user_rating`` is the same story one type along: it is in Kometa's
    ``number_filters``, whose branch reads ``if test_number is None or
    is_number_filter(...): return False``. An unrated item is excluded, which
    is what makes 2-of-1962 presence ship honestly rather than wrongly.

    ``plays`` is the one that does NOT go missing, and the contrast is the
    point of putting all three in one test."""
    view = PlexItemView(a_movie(BARE_MOVIE_XML))

    assert evaluate(parse_filters({"last_played.after": dt.date(2020, 1, 1)}), view) is False
    assert evaluate(parse_filters({"last_played.not": 30}), view) is False
    assert evaluate(parse_filters({"user_rating.gte": 8}), view) is False
    assert evaluate(parse_filters({"user_rating.not": 8}), view) is False

    assert evaluate(parse_filters({"plays.lt": 1}), view) is True
    assert evaluate(parse_filters({"plays.gte": 1}), view) is False


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
    view returned None for ``network``, a ``network: E4`` filter would quietly
    match nothing and a ``network.not: E4`` filter would quietly match
    everything -- a full, plausible, wrong collection. It refuses, loudly, and
    the config layer turns that into a load-time refusal so no run ever
    reaches here.

    The copy is phase B's, and it says something narrower than the tier's
    original wording did: this is no longer "the listing does not carry it
    completely enough", which the batched read would now answer, but "Plex
    emits it NOWHERE", which no read at any tier can."""
    with pytest.raises(AttributeNotInListing) as raised:
        PlexItemView(a_movie()).get(attribute)

    assert attribute in str(raised.value)
    assert "emits it nowhere" in str(raised.value)
    assert "different attribute under a distinct name" in str(raised.value), (
        "the TVDb/TMDb route is a separate row, not a silent substitution"
    )


def test_the_refusal_for_genre_is_not_that_the_item_lacks_genres():
    """The fixture's XML carries two <Genre> children and the view refuses
    anyway: the accessor reads the ENRICHMENT, and this view was built without
    one. That the listing happens to carry a truncated pair here is exactly why
    it must not be the answer -- the probe measured the cap at two against a
    metadata endpoint returning three and four."""
    item = a_movie()
    assert len(ET.fromstring(MOVIE_XML).findall("Genre")) == 2

    with pytest.raises(EnrichmentNotLoaded):
        PlexItemView(item).get("genre")


def test_an_attribute_outside_the_table_refuses_too():
    with pytest.raises(AttributeNotInListing):
        PlexItemView(a_movie()).get("director")


# --- the batched tier (phase B) -----------------------------------------------
#
# Five families the listing could not answer, read from the enrichment the
# engine fetched once for the definition's resolved set. Two properties are
# load-bearing, and the second is the refusal law: the accessor must never
# touch the ITEM (that is the N+1 the whole module exists to avoid), and it
# must never answer "missing" for an item whose enrichment was not loaded.


def test_batched_attributes_match_the_tier2_batched_rows():
    assert BATCHED_ATTRIBUTES == tuple(
        row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-batched"
    )


def test_batched_accessor_reads_the_enrichment_never_the_item():
    """THE TRIPWIRE for the batched tier. The item raises on ANY attribute
    read, so a single reach for ``item.genres`` -- the obvious implementation,
    and one that would silently reload every item plexapi handed back partial
    -- fails here rather than as a run that got slow.

    Language values are ISO 639-1 CODES (``en``), not display titles: the
    enrichment reads the stream's ``languageTag`` (probe decision D6), because
    that is what the tree's only language normaliser already emits."""

    class Explodes:
        def __getattr__(self, name):  # any read of the ITEM here is the N+1 trap
            raise AssertionError("a batched accessor must not touch the item")

    tags = ItemTags(("Horror", "Thriller"), ("Overlay",), (), ("en",), ())
    view = PlexItemView(Explodes(), tags=tags)

    assert view.get("genre") == ("Horror", "Thriller")
    assert view.get("label") == ("Overlay",)
    assert view.get("collection") is None, "an empty family is a missing value"
    assert view.get("audio_language") == ("en",)
    assert view.get("subtitle_language") is None


def test_an_empty_batched_family_feeds_the_tables_missing_value_rule():
    """The half of the line above that matters to an operator. An empty family
    is a real answer -- "Plex holds no labels for this item" -- and the table's
    missing-value rule is what turns it into a membership: a positive tag
    filter drops the item and a negated one keeps it. That is the same
    membership an unlabelled film gets from the tier-1 accessors, which is the
    point.

    Note what the view's ``or None`` does NOT do: ``filters._is_missing``
    already reads an empty sequence as missing for a ``tag`` attribute, so
    ``()`` would evaluate identically here. It buys one SHAPE for "no value"
    across both tiers -- every tier-1 accessor answers None -- so a direct
    reader of ``get`` has one thing to test for rather than two."""
    view = PlexItemView(object(), tags=ItemTags((), (), (), (), ()))

    assert view.get("label") is None
    assert evaluate(parse_filters({"label": "Overlay"}), view) is False
    assert evaluate(parse_filters({"label.not": "Overlay"}), view) is True


def test_batched_attribute_without_tags_raises_enrichment_not_loaded():
    """Raised, never answered ``None``. ``None`` means "this item has no
    value", which the table turns into a defined match result -- so answering
    it for an item the enrichment never covered is the wrong-but-plausible
    collection the tier system exists to prevent."""
    with pytest.raises(EnrichmentNotLoaded) as raised:
        PlexItemView(object()).get("genre")

    assert "genre" in str(raised.value)
    assert "ensure_tags" in str(raised.value), "the message names the way in"


def test_enrichment_not_loaded_is_a_lookup_error_like_the_tier_one_refusal():
    """Both refusals are ``LookupError``s, so the engine's containment catches
    either without knowing which tier failed -- and neither is an
    ``AttributeError``, which plexapi's own reload path raises and which a
    caller might reasonably swallow."""
    assert issubclass(EnrichmentNotLoaded, LookupError)
    assert issubclass(AttributeNotInListing, LookupError)


def test_tier_one_reads_still_work_with_tags_present():
    """The tags argument is additive: a view carrying an enrichment answers the
    listing rows exactly as it did without one."""
    view = PlexItemView(a_movie(), tags=ItemTags((), (), (), (), ()))

    assert view.get("year") == 2003
    assert view.get("studio") == "Universal Pictures"
    assert view.get("resolution") == ("1080",)


def test_a_deferred_row_still_refuses_even_with_an_enrichment_loaded():
    """``network`` is not in ``_BATCHED_FIELDS``, so a loaded enrichment does
    not smuggle it in: the batch cannot conjure an attrib Plex emits nowhere,
    and the refusal must say so rather than answering from an empty family."""
    view = PlexItemView(a_show(), tags=ItemTags((), (), (), (), ()))

    with pytest.raises(AttributeNotInListing):
        view.get("network")


def test_a_filter_over_the_batched_tier_evaluates_from_the_enrichment():
    """End to end through the predicate model, because that is what the engine
    actually does: parse, then evaluate over a view carrying the item's tags.
    The genre kept here (``Thriller``) is precisely the one the listing
    truncated away -- the probe's own example."""
    tags = ItemTags(("Action", "Crime", "Thriller"), (), (), (), ())
    view = PlexItemView(a_movie(), tags=tags)

    assert evaluate(parse_filters({"genre": "Thriller"}), view) is True
    assert evaluate(parse_filters({"genre": "Romance"}), view) is False


def test_reading_the_batched_tier_costs_no_plex_request():
    """The module's one hard rule, extended to the new tier. The enrichment was
    already fetched -- once, for the whole resolved set -- so reading it back
    here is a dict lookup, and the reload-guarded item is never touched."""
    server = NoRequestsServer()
    view = PlexItemView(
        a_movie(BARE_MOVIE_XML, server),
        tags=ItemTags(("Horror",), ("Overlay",), ("Franchise",), ("en",), ("fi",)),
    )

    for attribute in BATCHED_ATTRIBUTES:
        assert view.get(attribute) is not None

    assert server.calls == []


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
    tell from "not loaded". Zero requests.

    ``plays`` reads 0 there rather than None (see the split above), and 0 is
    falsy, so it is exactly as much of a reload risk as the rest -- the request
    count is what this test is about, not the value."""
    server = NoRequestsServer()
    view = PlexItemView(a_movie(BARE_MOVIE_XML, server))

    for attribute in SHIPPED_ATTRIBUTES:
        assert view.get(attribute) in (None, 0)

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
