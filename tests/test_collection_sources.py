"""The collection inventory and its per-source failure containment."""
from pathlib import Path
from types import SimpleNamespace

import httpx

from autoposter.collections.sources import CHART_COLLECTIONS, build_all

AWARD_FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")
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
    def __init__(self, title):
        self.title = title
        self.ratingKey = "c-" + title
        self._items = []
        self._labels = []

    def reload(self):
        pass

    @property
    def labels(self):
        return self._labels

    def items(self):
        return list(self._items)

    def addItems(self, items):
        self._items.extend(items)

    def removeItems(self, items):
        removed = {i.ratingKey for i in items}
        self._items = [i for i in self._items if i.ratingKey not in removed]

    def moveItem(self, item, after=None):
        pass

    def sortUpdate(self, sort=None):
        pass

    def editSummary(self, summary, locked=True):
        self.summary_set = summary

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    def __init__(self, items):
        self._items = items
        self._existing: dict[str, FakeCollection] = {}
        self.all_calls = 0

    def all(self):
        self.all_calls += 1
        return self._items

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        collection = FakeCollection(title)
        collection._items = list(items or [])
        self._existing[title] = collection
        return collection


def _config(*, charts=True, awards=True, apply_to_plex=True):
    return SimpleNamespace(
        collections=SimpleNamespace(charts=charts, awards=awards, apply_to_plex=apply_to_plex)
    )


def _transport(handler):
    return httpx.MockTransport(handler)


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


async def test_chart_collections_get_no_invented_summary(session):
    """Only the year award collections have known summary text; a chart
    title is not a summary."""

    def handler(request):
        return httpx.Response(200, text=CHART_FIXTURE)

    section = FakeSection([FakeItem("a", ["imdb://tt0111161"])])
    config = _config(charts=True, awards=False)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "Movies", "Movie", "autoposter", config)

    collection = section._existing["IMDb Top 250"]
    assert getattr(collection, "summary_set", None) is None


async def test_year_collections_get_the_templated_summary(session):
    def handler(request):
        return httpx.Response(200, text=AWARD_FIXTURE)

    section = FakeSection([FakeItem("a", ["imdb://tt31193180"])])
    config = _config(charts=False, awards=True)

    async with httpx.AsyncClient(transport=_transport(handler)) as http:
        await build_all(http, session, section, "Movies", "Movie", "autoposter", config)

    collection = section._existing["Oscars Winners 2026"]
    assert collection.summary_set == "The winners of the 2026 Academy Awards."


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
