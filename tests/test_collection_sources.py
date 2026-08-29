"""The collection inventory and its per-source failure containment."""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
from plexapi.exceptions import NotFound

from autoposter.collections.engine import run_definitions
from autoposter.collections.sources import CHART_COLLECTIONS, chart_and_award_definitions


async def build_all(http, session, section, library, library_type, label, config):
    """The list-collection half of a pass, through the engine.

    What ``sources.build_all`` was before ``reconcile_libraries`` moved onto
    ``run_definitions`` directly: the chart and award definitions without the
    smart Common Sense family, which this file is not about.
    """
    return await run_definitions(
        session, section, library, library_type,
        chart_and_award_definitions(config, library_type),
        config, http=http, label=label,
    )

AWARD_FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")
VALIDATION_FIXTURE = Path(
    "tests/fixtures/collections/event_validation.yml"
).read_text(encoding="utf-8")
CHART_FIXTURE = (Path("tests/fixtures/collections/imdb_chart.json")).read_text(encoding="utf-8")


def test_the_movie_inventory_matches_production():
    titles = [t for t, _ in CHART_COLLECTIONS["Movie"]]
    assert titles == ["IMDb Popular", "IMDb Top 250", "IMDb Lowest Rated"]


def test_shows_have_no_lowest_rated_chart():
    """IMDb has no lowest-rated TV chart; the default omits it deliberately."""
    titles = [t for t, _ in CHART_COLLECTIONS["Show"]]
    assert titles == ["IMDb Popular", "IMDb Top 250"]
    assert "IMDb Lowest Rated" not in titles


def test_show_charts_use_the_show_chart_keys():
    keys = dict(CHART_COLLECTIONS["Show"])
    assert keys["IMDb Top 250"] == "top_shows"
    assert keys["IMDb Popular"] == "popular_shows"


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, key, guids):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]


class FakeCollection:
    """Same caching model as tests/test_collection_lists.py: ``items()``
    returns a snapshot that only ``reload()`` refreshes."""

    def __init__(self, title):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = []
        self._cache = []
        self._labels = []
        self.summary = None
        self.sort_set = None
        self.titleSort = None
        self.sort_title_set = None
        self.summary_set = None
        self.summary_queries = []
        # Stands in for ``collection._server``: the summary is written with a
        # raw item-level PUT, not ``editSummary``.
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    def reload(self):
        self._cache = list(self._live)

    @property
    def labels(self):
        return self._labels

    def items(self):
        return list(self._cache)

    def addItems(self, items):
        self._live.extend(items)

    def removeItems(self, items):
        removed = {i.ratingKey for i in items}
        self._live = [i for i in self._live if i.ratingKey not in removed]

    def moveItem(self, item, after=None):
        self._live = [i for i in self._live if i.ratingKey != item.ratingKey]
        if after is None:
            self._live.insert(0, item)
        else:
            position = [i.ratingKey for i in self._live].index(after.ratingKey)
            self._live.insert(position + 1, item)

    def sortUpdate(self, sort=None):
        self.sort_set = sort

    def editSortTitle(self, sortTitle, locked=True):
        # Row 49: every managed collection derives its group's sort-title
        # prefix now, so this route is reached on every apply.
        self.sort_title_set = sortTitle
        self.titleSort = sortTitle

    def editSummary(self, summary, locked=True):
        """Raises the way the live server does -- the section route plexapi
        takes 404s for collection summaries. See
        ``reconcile._edit_collection_summary``."""
        raise NotFound("(404) not_found; /library/sections/42/all?type=18")

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Stands in for ``server.query`` -- the item-level summary PUT."""
        self.summary_queries.append({"key": key, "method": method})
        self.summary_set = parse_qs(urlsplit(key).query)["summary.value"][0]
        self.summary = self.summary_set

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    def __init__(self, items):
        self._items = items
        self._existing: dict[str, FakeCollection] = {}
        self.all_calls = 0
        self.collection_calls = 0

    def all(self):
        self.all_calls += 1
        return self._items

    def collections(self, **kw):
        self.collection_calls += 1
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        collection = FakeCollection(title)
        collection._live = list(items or [])
        collection._cache = list(items or [])
        self._existing[title] = collection
        return collection


def _config(*, charts=True, awards=True, apply_to_plex=True):
    return SimpleNamespace(
        collections=SimpleNamespace(
            charts=charts, awards=awards, apply_to_plex=apply_to_plex,
            adopt=False, adopt_from=["Kometa"], adopt_removes_prior_label=False,
            protect_labels=[],
            # Posters are exercised in test_collection_poster_wiring.py; off
            # here so this file stays scoped to source wiring, not artwork.
            posters=False,
        )
    )


def _transport(handler):
    """The per-test handler, with the award validation list answered for it.

    Every award build asks for ``event_validation.yml`` before the event file
    -- one extra route that says nothing about what any test here is checking,
    so it is served here rather than repeated in nine handlers.
    ``test_both_sources_disabled_touches_nothing`` still proves what it always
    did: with awards off there is no definition to build, so nothing asks for
    the validation list either and its refusing handler is never reached.
    """
    def handle(request):
        if "event_validation" in str(request.url):
            return httpx.Response(200, text=VALIDATION_FIXTURE)
        return handler(request)

    return httpx.MockTransport(handle)


async def test_a_failed_chart_does_not_prevent_the_award_collections(session):
    """A GraphQL outage must not stop the Oscars collections, which come
    from a different, healthy source."""

    def handler(request):
        if "graphql.imdb.com" in str(request.url):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([FakeItem("bp", ["imdb://tt31193180"])])
    config = _config()

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        actions = await build_all(
            http, session, section, "Movies", "Movie", "autoposter", config
        )

    assert any("Oscars Best Picture Winners" in a for a in actions)
    # The failed chart made no change -- it must not have emptied anything,
    # and its own "no items" message is one of the returned actions.
    assert any("IMDb Top 250" in a and "no items" in a.lower() for a in actions)


async def test_award_collections_are_movies_only(session):
    """Award collections must never appear for a Show library."""

    def handler(request):
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([FakeItem("bp", ["imdb://tt31193180"])])
    config = _config(charts=False, awards=True)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        actions = await build_all(
            http, session, section, "TV Shows", "Show", "autoposter", config
        )

    assert actions == []


async def test_a_library_with_nothing_to_build_pays_for_no_index(session):
    """``build_owned_index`` costs a full ``section.all()``. A Show library
    with only awards enabled builds no collection at all, so it must not pay
    for the index -- nor list the section's collections."""

    def handler(request):
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([FakeItem("bp", ["imdb://tt31193180"])])
    config = _config(charts=False, awards=True)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "TV Shows", "Show", "autoposter", config)

    assert section.all_calls == 0
    assert section.collection_calls == 0


async def test_both_sources_disabled_touches_nothing(session):
    def handler(request):
        raise AssertionError("no request should be made")

    section = FakeSection([FakeItem("bp", ["imdb://tt31193180"])])
    config = _config(charts=False, awards=False)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        assert await build_all(
            http, session, section, "Movies", "Movie", "autoposter", config
        ) == []

    assert section.all_calls == 0
    assert section.collection_calls == 0


async def test_the_section_is_listed_once_for_the_whole_library(session):
    """The production Movies section holds 305 collections; listing it once
    per collection would fetch all ten times."""

    def handler(request):
        if "graphql.imdb.com" in str(request.url):
            return httpx.Response(200, text=CHART_FIXTURE)
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([FakeItem("a", ["imdb://tt0111161"])])
    config = _config()

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "Movies", "Movie", "autoposter", config)

    assert section.collection_calls == 1


async def test_the_imdb_index_is_built_once_for_the_library(session):
    """One chart plus the whole award family must still be a single index
    build, not one per collection."""

    def handler(request):
        if "graphql.imdb.com" in str(request.url):
            return httpx.Response(200, text=CHART_FIXTURE)
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([FakeItem("a", ["imdb://tt0111161"])])
    config = _config()

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "Movies", "Movie", "autoposter", config)

    assert section.all_calls == 1


async def test_chart_summaries_are_the_verbatim_kometa_strings(session):
    """Summaries are transcribed from Kometa's translations file
    (kometa-collections.md §5), never worded here."""

    def handler(request):
        return httpx.Response(200, text=CHART_FIXTURE)

    section = FakeSection([FakeItem("a", ["imdb://tt0111161"])])
    config = _config(charts=True, awards=False)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "Movies", "Movie", "autoposter", config)

    assert section._existing["IMDb Popular"].summary_set == "List of IMDb Popular movies."
    assert section._existing["IMDb Top 250"].summary_set == "List of IMDb Top 250 movies."
    assert (
        section._existing["IMDb Lowest Rated"].summary_set
        == "List of IMDb Lowest Rated movies."
    )


async def test_show_chart_summaries_say_show_not_movie(session):
    def handler(request):
        return httpx.Response(200, text=CHART_FIXTURE)

    section = FakeSection([FakeItem("a", ["imdb://tt0111161"])])
    config = _config(charts=True, awards=False)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "TV Shows", "Show", "autoposter", config)

    assert section._existing["IMDb Popular"].summary_set == "List of IMDb Popular shows."
    assert section._existing["IMDb Top 250"].summary_set == "List of IMDb Top 250 shows."


async def test_static_award_summaries_are_the_verbatim_kometa_strings(session):
    def handler(request):
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([
        FakeItem("bp", ["imdb://tt31193180"]),
        FakeItem("bd", ["imdb://tt30144839"]),
    ])
    config = _config(charts=False, awards=True)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "Movies", "Movie", "autoposter", config)

    assert section._existing["Oscars Best Picture Winners"].summary_set == (
        "The Academy Award for Best Picture is one of the Academy Awards presented "
        "annually by the Academy of Motion Picture Arts and Sciences since the awards "
        "debuted in 1929."
    )
    assert section._existing["Oscars Best Director Winners"].summary_set == (
        "The Academy Award for Best Director is one of the Academy Awards presented "
        "annually by the Academy of Motion Picture Arts and Sciences since the awards "
        "debuted in 1929."
    )


async def test_year_collections_get_the_templated_summary(session):
    def handler(request):
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([FakeItem("a", ["imdb://tt31193180"])])
    config = _config(charts=False, awards=True)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "Movies", "Movie", "autoposter", config)

    collection = section._existing["Oscars Winners 2026"]
    assert collection.summary_set == "Academy Awards (Oscars) Winners for 2026."


async def test_the_year_collections_sort_by_release_and_the_rest_by_custom(session):
    """§2.4: the dynamic year collections override collection_order to
    release; the two static winner collections keep custom."""

    def handler(request):
        if "graphql.imdb.com" in str(request.url):
            return httpx.Response(200, text=CHART_FIXTURE)
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([
        FakeItem("a", ["imdb://tt0111161"]),
        FakeItem("bp", ["imdb://tt31193180"]),
    ])
    config = _config()

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "Movies", "Movie", "autoposter", config)

    assert section._existing["Oscars Winners 2026"].sort_set == "release"
    assert section._existing["Oscars Best Picture Winners"].sort_set == "custom"
    assert section._existing["IMDb Top 250"].sort_set == "custom"


async def test_dry_run_writes_nothing_and_still_reports(session):
    def handler(request):
        if "graphql.imdb.com" in str(request.url):
            return httpx.Response(200, text=CHART_FIXTURE)
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([FakeItem("a", ["imdb://tt0111161"])])
    config = _config(apply_to_plex=False)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        actions = await build_all(
            http, session, section, "Movies", "Movie", "autoposter", config
        )

    assert section._existing == {}
    assert actions
