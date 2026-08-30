"""TMDb's list surface: the transport under every TMDb collection builder.

Never touches the real API -- MockTransport only.

Three properties carry the weight here, and each one is a way a *silent* wrong
answer could reach a sync-mode collection:

- **paging is followed, in order, and capped.** A chart is 20 items per page
  and a collection's custom order is the builder's output order, so a page
  loop that reordered or stopped early would build a plausible-looking, wrong
  collection. The cap is what keeps a list nobody meant to be enormous (or a
  ``total_pages`` this client misread) from becoming hundreds of requests.
- **a 404 raises rather than returning nothing.** ``fetch_json`` turns 404
  into ``None`` -- the right answer for artwork, where "no images" is a normal
  outcome -- and an empty membership one layer down means "remove every
  member" (``lists.reconcile_list_collection``).
- **the bearer token never enters the cache key.** It lives in a header,
  which ``build_cache_key`` never reads; this pins that it stays that way.
"""
import json
from pathlib import Path

import httpx
import pytest

from conftest import session_factory_for
from autoposter.providers.tmdb_lists import (
    CHART_ENDPOINTS,
    MAX_PAGES,
    TmdbListClient,
    TmdbListRefused,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _path(request) -> str:
    """The TMDb path, with the ``/3`` API-version prefix taken back off.

    Routing on the bare path keeps the test's route table readable as TMDb's
    own endpoint names; that the requests really carry ``/3`` is pinned by
    ``test_the_base_url_is_tmdbs_v3_api``.
    """
    return request.url.path.removeprefix("/3")


def _routed(routes: dict, seen: list | None = None, default=None):
    """A transport answering by path, recording every request it served.

    ``routes`` maps a path to either one payload or a ``{page: payload}`` dict,
    so a two-page endpoint is expressed as the two responses it really sends.
    """

    def handler(request):
        if seen is not None:
            seen.append(request)
        payload = routes.get(_path(request))
        if payload is None:
            return default or httpx.Response(404, json=load("tmdb_not_found.json"))
        if isinstance(payload, dict) and "__pages__" in payload:
            page = int(request.url.params.get("page", 1))
            # A page past the last recorded one answers the way TMDb really
            # does -- an empty array, not a 404 (``test_an_empty_page_ends_the
            # _loop`` pins that shape). Without this a caller that legitimately
            # walks one page further than the fixture set would see a KeyError
            # from the test's own scaffolding rather than the client's answer.
            empty = {"results": [], "items": []}
            return httpx.Response(200, json=payload["__pages__"].get(page, empty))
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _pages(*payloads):
    return {"__pages__": {n: payload for n, payload in enumerate(payloads, start=1)}}


POPULAR = _pages(
    load("tmdb_chart_movie_popular_p1.json"), load("tmdb_chart_movie_popular_p2.json")
)
LIST = _pages(load("tmdb_list_p1.json"), load("tmdb_list_p2.json"))


def _client(http, **kwargs):
    return TmdbListClient("a-read-access-token", http, **kwargs)


# --- the endpoint table ------------------------------------------------------


def test_the_chart_table_names_an_endpoint_per_library_type():
    """Every chart key maps to the endpoints that can answer it, and nothing
    else: a chart with no entry for a library type is a chart that library
    type cannot build, which is what the builders' mismatch guard reads."""
    assert set(CHART_ENDPOINTS) == {
        "popular", "top_rated", "now_playing", "upcoming",
        "airing_today", "on_the_air", "trending_day", "trending_week",
    }
    assert CHART_ENDPOINTS["popular"] == {"Movie": "/movie/popular", "Show": "/tv/popular"}
    # Theatrical release windows are a movie idea and TMDb has no TV form.
    assert CHART_ENDPOINTS["now_playing"] == {"Movie": "/movie/now_playing"}
    assert CHART_ENDPOINTS["upcoming"] == {"Movie": "/movie/upcoming"}
    # ...and the mirror: airing is a TV idea with no movie form.
    assert CHART_ENDPOINTS["airing_today"] == {"Show": "/tv/airing_today"}
    assert CHART_ENDPOINTS["on_the_air"] == {"Show": "/tv/on_the_air"}
    assert CHART_ENDPOINTS["trending_day"]["Movie"] == "/trending/movie/day"
    assert CHART_ENDPOINTS["trending_week"]["Show"] == "/trending/tv/week"


# --- paging ------------------------------------------------------------------


async def test_a_chart_is_read_across_every_page_in_order():
    """The order *is* the chart: page 2's items rank below page 1's, and the
    collection's custom order is exactly this list."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({"/movie/popular": POPULAR}, seen)) as http:
        ids = await _client(http).chart("/movie/popular")

    assert ids == ["438631", "693134", "238", "278"]
    assert [int(r.url.params["page"]) for r in seen] == [1, 2]


async def test_paging_stops_at_total_pages_rather_than_asking_for_one_more():
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({"/movie/popular": POPULAR}, seen)) as http:
        await _client(http).chart("/movie/popular")

    assert len(seen) == 2, "total_pages is 2; a third request is a page TMDb does not have"


async def test_a_single_page_chart_asks_once():
    seen: list = []
    routes = {"/tv/top_rated": load("tmdb_chart_tv_top_rated.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        ids = await _client(http).chart("/tv/top_rated")

    assert ids == ["95396", "1396"]
    assert len(seen) == 1


async def test_paging_is_capped_however_many_pages_tmdb_claims():
    """A runaway upstream -- a list with thousands of pages, or a
    ``total_pages`` this client misread -- must cost a bounded number of
    requests. The cap is the client's, not the definition's: ``limit`` trims
    after resolution and cannot stop a fetch that already happened."""
    seen: list = []

    def handler(request):
        seen.append(request)
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json={
            "page": page,
            "results": [{"id": page * 2 - 1}, {"id": page * 2}],
            "total_pages": 500,
            "total_results": 1000,
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        ids = await _client(http).chart("/movie/popular")

    assert len(seen) == MAX_PAGES == 10
    assert len(ids) == 20
    assert ids[:2] == ["1", "2"] and ids[-2:] == ["19", "20"]


async def test_the_cap_is_configurable_for_a_caller_that_wants_less():
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({"/movie/popular": POPULAR}, seen)) as http:
        ids = await _client(http, max_pages=1).chart("/movie/popular")

    assert ids == ["438631", "693134"]
    assert len(seen) == 1


async def test_an_empty_page_ends_the_loop():
    """TMDb answers a page past the end with an empty array rather than a 404,
    and a chart that really is empty is data, not a failure -- unlike a 404,
    which is the id being wrong."""
    seen: list = []
    routes = {"/movie/popular": {"page": 1, "results": [], "total_pages": 9}}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        ids = await _client(http).chart("/movie/popular")

    assert ids == []
    assert len(seen) == 1


# --- the v3 list endpoint ----------------------------------------------------


async def test_a_list_is_read_across_pages_in_order():
    """A v3 list details response holds ``items`` (not ``results``) and counts
    its members in ``item_count`` rather than announcing ``total_pages``."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({"/list/7096": LIST}, seen)) as http:
        ids = await _client(http).list_items(7096)

    assert ids == ["11", "1891", "1892", "95396"]
    assert [int(r.url.params["page"]) for r in seen] == [1, 2]


async def test_a_list_stops_once_item_count_is_satisfied():
    """The stop condition that matters if TMDb ever ignores ``page`` on this
    endpoint: four members collected out of four ends the loop, so the same
    payload cannot be collected ten times over."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({"/list/7096": LIST}, seen)) as http:
        await _client(http).list_items(7096)

    assert len(seen) == 2


async def test_a_list_that_repeats_its_only_page_is_still_read_once():
    seen: list = []
    only = load("tmdb_list_p1.json") | {"item_count": 2}
    async with httpx.AsyncClient(transport=_routed({"/list/7096": only}, seen)) as http:
        ids = await _client(http).list_items(7096)

    assert ids == ["11", "1891"]
    assert len(seen) == 1


async def test_a_filtered_list_stops_at_item_count_counting_entries_seen():
    """Roadmap row 144. ``item_count`` counts the list's MEMBERS, both media
    types together, so comparing it against ids KEPT could never be satisfied
    once the media-type filter dropped anything -- and ``tmdb_list`` now
    always filters. One page holds the whole 2-member list; the old
    comparison (1 kept < 2 listed) sent the client to fetch a second page it
    had no reason to ask for."""
    seen: list = []
    page = {
        "items": [
            {"id": 1, "media_type": "movie"},
            {"id": 2, "media_type": "tv"},
        ],
        "item_count": 2,
    }
    transport = _routed({"/list/1": _pages(page)}, seen)
    async with httpx.AsyncClient(transport=transport) as http:
        ids = await _client(http).list_items(1, media_type="movie")

    assert ids == ["1"]
    assert len(seen) == 1


async def test_a_list_endpoint_ignoring_page_does_not_duplicate_ids():
    """The pathological case the ``item_count`` break exists for: TMDb
    answering the same page whatever ``page`` says. With the break comparing
    ids KEPT (see above), the survivor was re-collected page after page until
    the duplicates themselves added up to ``item_count`` -- twice over here,
    and up to ``max_pages`` times for a list whose kept share is smaller.
    Bounded either way, but wasteful and wrong: the same member listed more
    than once."""

    def handler(request):
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": 1, "media_type": "movie"},
                    {"id": 2, "media_type": "tv"},
                ],
                "item_count": 2,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        ids = await _client(http).list_items(1, media_type="movie")

    assert ids == ["1"]


async def test_a_list_keeps_only_the_media_type_the_caller_asked_for():
    """A v3 list is the one endpoint here that answers with both kinds at
    once -- ``tmdb_list_p2.json`` carries the show 95396 beside a film -- and
    TMDb's movie and show ids are different id spaces sharing one namespace,
    so that id offered to a Movie library can resolve to an unrelated *film*.
    Dropping it here is what ``builders/mdblist.py`` and ``builders/tvdb.py``
    already do for the same collision."""
    async with httpx.AsyncClient(transport=_routed({"/list/7096": LIST})) as http:
        ids = await _client(http).list_items(7096, media_type="movie")

    assert ids == ["11", "1891", "1892"]
    assert "95396" not in ids


async def test_a_list_can_be_read_for_the_shows_instead():
    async with httpx.AsyncClient(transport=_routed({"/list/7096": LIST})) as http:
        ids = await _client(http).list_items(7096, media_type="tv")

    assert ids == ["95396"]


async def test_an_entry_with_no_media_type_raises_when_one_was_asked_for():
    """``_ids``' rule, one field over: a guess adds an unrelated title and a
    silent skip removes a real one, and neither leaves anything to notice."""
    routes = {
        "/list/7096": {
            "items": [{"id": 11, "media_type": "movie"}, {"id": 12}],
            "item_count": 2,
        }
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TmdbListRefused, match="media_type"):
            await _client(http).list_items(7096, media_type="movie")


async def test_an_unfiltered_read_does_not_demand_a_media_type():
    """The chart and discover endpoints answer for one media type already and
    do not tag their entries; only ``/list/{id}`` is mixed."""
    routes = {"/list/7096": {"items": [{"id": 11}, {"id": 12}], "item_count": 2}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        assert await _client(http).list_items(7096) == ["11", "12"]


async def test_a_missing_list_raises_and_names_the_id():
    """404 is ``None`` out of ``fetch_json``, and returning nothing here would
    empty a live collection on the next sync."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TmdbListRefused) as caught:
            await _client(http).list_items(7096)

    assert "7096" in str(caught.value)


async def test_a_server_error_raises_rather_than_returning_nothing():
    default = httpx.Response(500, text="upstream is having a day")
    async with httpx.AsyncClient(transport=_routed({}, default=default)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await _client(http).list_items(7096)


async def test_a_response_without_the_expected_array_raises():
    routes = {"/list/7096": {"id": "7096", "name": "Saga"}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TmdbListRefused, match="items"):
            await _client(http).list_items(7096)


async def test_an_entry_without_an_id_raises_rather_than_being_dropped():
    """A silently skipped member is a smaller collection every pass and no
    way to notice; the text_file builder refuses an unparseable line for the
    same reason."""
    routes = {"/list/7096": {"items": [{"id": 11}, {"title": "no id here"}], "item_count": 2}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TmdbListRefused):
            await _client(http).list_items(7096)


# --- collections and discover ------------------------------------------------


async def test_a_collections_parts_are_returned_in_order():
    """``/collection/{id}`` is not paged: TMDb returns every part at once."""
    seen: list = []
    routes = {"/collection/10": load("tmdb_collection.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        ids = await _client(http).collection_parts(10)

    assert ids == ["11", "1891", "1892"]
    assert len(seen) == 1
    assert "page" not in seen[0].url.params


async def test_a_missing_collection_raises_and_names_the_id():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TmdbListRefused, match="10"):
            await _client(http).collection_parts(10)


async def test_a_collection_name_is_read_from_the_same_response_as_its_parts(session):
    """One ``/collection/{id}`` read serves both halves of a franchise family:
    the name it titles the collection with, and the parts its membership is.
    Same path and same (empty) params, so ``build_cache_key`` agrees and the
    second call is served from the ``provider_cache`` row the first wrote."""
    from autoposter.providers.cache import ProviderCache

    seen: list = []
    cache = ProviderCache(session_factory_for(session))
    routes = {"/collection/10": load("tmdb_collection.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        client = _client(http, cache=cache, cache_ttl_seconds=3600)
        name = await client.collection_name(10)
        parts = await client.collection_parts(10)

    assert name == "Star Wars Collection"
    assert parts == ["11", "1891", "1892"]
    assert len(seen) == 1, "titling the family should cost no extra request"


async def test_a_collection_tmdb_does_not_know_has_no_name():
    """A dead id is one collection a family drops and reports, not a dead pass
    -- the one place here that degrades a 404 rather than raising it, and the
    method docstring says why."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        assert await _client(http).collection_name(999) is None


async def test_a_collection_with_no_name_at_all_has_none():
    """A 200 carrying no usable ``name`` is the same answer as a 404 here: a
    family cannot title a collection from it either way."""
    routes = {"/collection/10": {"id": 10, "name": "", "parts": []}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        assert await _client(http).collection_name(10) is None


async def test_discover_sends_the_filter_and_reads_results():
    seen: list = []
    routes = {"/discover/movie": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        ids = await _client(http).discover("movie", {"with_companies": 420})

    assert ids == ["11", "1891"]
    assert seen[0].url.params["with_companies"] == "420"


async def test_discover_reaches_the_tv_endpoint_for_shows():
    seen: list = []
    routes = {"/discover/tv": load("tmdb_discover_tv.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        ids = await _client(http).discover("tv", {"with_networks": 213})

    assert ids == ["95396", "1416"]
    assert _path(seen[0]) == "/discover/tv"


# --- request shape -----------------------------------------------------------


async def test_the_bearer_token_is_sent_on_every_page():
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({"/movie/popular": POPULAR}, seen)) as http:
        await _client(http).chart("/movie/popular")

    assert len(seen) == 2
    for request in seen:
        assert request.headers["Authorization"] == "Bearer a-read-access-token"
        assert request.headers["accept"] == "application/json"


async def test_region_and_language_are_sent_only_when_configured():
    """``region`` is the one that changes *membership*: TMDb resolves
    ``now_playing`` and ``upcoming`` against a country's release dates."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({"/movie/upcoming": POPULAR}, seen)) as http:
        await _client(http).chart("/movie/upcoming", region="FI", language="fi-FI")

    assert seen[0].url.params["region"] == "FI"
    assert seen[0].url.params["language"] == "fi-FI"

    seen.clear()
    async with httpx.AsyncClient(transport=_routed({"/movie/upcoming": POPULAR}, seen)) as http:
        await _client(http).chart("/movie/upcoming")

    assert "region" not in seen[0].url.params
    assert "language" not in seen[0].url.params


async def test_the_base_url_is_tmdbs_v3_api():
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({"/movie/popular": POPULAR}, seen)) as http:
        await _client(http).chart("/movie/popular")

    assert str(seen[0].url).startswith("https://api.themoviedb.org/3/movie/popular")


# --- caching -----------------------------------------------------------------


async def test_a_second_read_of_the_same_page_is_served_from_the_cache(session):
    from autoposter.providers.cache import ProviderCache

    seen: list = []
    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=_routed({"/movie/popular": POPULAR}, seen)) as http:
        client = _client(http, cache=cache, cache_ttl_seconds=3600)
        first = await client.chart("/movie/popular")
        second = await client.chart("/movie/popular")

    assert first == second == ["438631", "693134", "238", "278"]
    assert len(seen) == 2, "the second read should have cost no requests"


async def test_the_token_is_not_part_of_the_cache_key(session):
    """It travels in a header, and ``build_cache_key`` reads only method, URL
    and params -- so rotating the token cannot orphan every cached page, and
    the cache table can never hold a credential."""
    from autoposter.providers.cache import ProviderCache

    seen: list = []
    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=_routed({"/movie/popular": POPULAR}, seen)) as http:
        await TmdbListClient(
            "the-old-token", http, cache=cache, cache_ttl_seconds=3600
        ).chart("/movie/popular")
        ids = await TmdbListClient(
            "a-rotated-token", http, cache=cache, cache_ttl_seconds=3600
        ).chart("/movie/popular")

    assert ids == ["438631", "693134", "238", "278"]
    assert len(seen) == 2, "the rotated token read the same cache entries"
