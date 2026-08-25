"""``mdblist_list``: one MDBList list, and the daily budget that shapes it.

Three things are being pinned here, and only one of them is transport.

- **Both addressing forms reach MDBList's two list endpoints.** ``list:
  user/slug`` and ``list: 14`` are the same path with a different reference,
  which is why the client takes one string and the params model is what tells
  the two apart.
- **Which id a member becomes depends on the library.** MDBList hands over
  several ids per title and a ``mediatype``; the namespace picked has to be one
  the library's items actually carry, and an entry of the *other* media type is
  dropped rather than emitted -- TMDb's movie and show ids share one namespace,
  so a show's tmdb id in a Movie library can match an unrelated film.
- **The budget short-circuit.** MDBList answers 200 with an error body when the
  daily allowance is spent. The first one memoises the exception on the pass's
  ``run_cache``; every later MDBList definition in that pass re-raises it
  without a request. The call-counted test below is the one that matters: three
  definitions, one request.
"""
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.mdblist import MdblistBuilderRefused
from autoposter.collections.service import build_source_clients
from autoposter.config.schema import Secrets
from autoposter.facts.mdblist import MDBListClient, MDBListLimitReached, MDBListRefused

FIXTURES = Path(__file__).parent / "fixtures" / "collections"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _routed(routes: dict, seen: list | None = None):
    """Serve ``{path: payload}``; anything else is a 404, as MDBList's is."""

    def handler(request):
        if seen is not None:
            seen.append(request)
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _sources(http):
    return SourceClients(mdblist=MDBListClient("an-api-key", http))


def _ctx(
    sources: SourceClients,
    library_type: str = "Movie",
    run_cache: dict | None = None,
    **params,
) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library,
        library_type=library_type,
        config=params,
        sources=sources,
        run_cache={} if run_cache is None else run_cache,
    )


# --- addressing forms --------------------------------------------------------


async def test_a_user_and_slug_list_reaches_the_two_segment_endpoint():
    seen: list = []
    routes = {"/lists/linaspurinis/top-watched/items": load("mdblist_list_items.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["mdblist_list"].build(
            _ctx(_sources(http), list="linaspurinis/top-watched")
        )

    assert seen[0].url.path == "/lists/linaspurinis/top-watched/items"
    assert result.ids == [("tmdb", "550"), ("imdb", "tt6751668")]


async def test_a_numeric_list_id_reaches_the_single_segment_endpoint():
    seen: list = []
    routes = {"/lists/14/items": load("mdblist_list_items.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))

    assert seen[0].url.path == "/lists/14/items"
    assert result.ids == [("tmdb", "550"), ("imdb", "tt6751668")]


async def test_the_api_key_is_sent_as_a_query_parameter():
    """MDBList authenticates by query parameter, which ``build_cache_key``
    strips (``_CREDENTIAL_PARAMS``) -- so the key travels but is never
    persisted. Pinned in ``test_provider_cache.py``; pinned here as "it is
    actually sent", which is what makes the stripping matter."""
    seen: list = []
    routes = {"/lists/14/items": load("mdblist_list_items.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))

    assert seen[0].url.params["apikey"] == "an-api-key"


async def test_sort_and_order_are_threaded_when_set():
    seen: list = []
    routes = {"/lists/14/items": load("mdblist_list_items.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["mdblist_list"].build(
            _ctx(_sources(http), list=14, sort="imdbrating", order="desc")
        )

    assert seen[0].url.params["sort"] == "imdbrating"
    assert seen[0].url.params["order"] == "desc"


async def test_sort_and_order_are_omitted_when_unset():
    """Not sent empty: an unconfigured list keeps the request -- and therefore
    the cache key -- it has always had."""
    seen: list = []
    routes = {"/lists/14/items": load("mdblist_list_items.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))

    assert "sort" not in seen[0].url.params
    assert "order" not in seen[0].url.params


@pytest.mark.parametrize(
    "reference",
    [
        "https://mdblist.com/lists/linaspurinis/top-watched",
        "linaspurinis/top-watched/extra",
        "/top-watched",
        "linaspurinis/",
        "",
    ],
)
async def test_a_list_reference_that_is_neither_form_is_refused(reference):
    with pytest.raises(ValidationError):
        await REGISTRY["mdblist_list"].build(_ctx(SourceClients(), list=reference))


async def test_an_unknown_order_is_refused():
    with pytest.raises(ValidationError):
        await REGISTRY["mdblist_list"].build(
            _ctx(SourceClients(), list=14, order="descending")
        )


async def test_params_it_does_not_understand_are_refused():
    with pytest.raises(ValidationError):
        await REGISTRY["mdblist_list"].build(_ctx(SourceClients(), list=14, limit=10))


async def test_a_missing_list_param_is_refused():
    with pytest.raises(ValidationError):
        await REGISTRY["mdblist_list"].build(_ctx(SourceClients()))


# --- namespace mapping -------------------------------------------------------


async def test_a_movie_library_prefers_tmdb_then_imdb_and_skips_the_shows():
    """Fight Club has a tmdb id, Parasite only an imdb id, and the entry with
    neither is dropped -- with the three shows filtered out by mediatype."""
    routes = {"/lists/14/items": load("mdblist_list_items.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))

    assert result.ids == [("tmdb", "550"), ("imdb", "tt6751668")]


async def test_a_show_library_prefers_tvdb_then_tmdb_then_imdb():
    """Breaking Bad has a tvdb id, Chernobyl only tmdb, the third only imdb --
    and the movies are filtered out."""
    routes = {"/lists/14/items": load("mdblist_list_items.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["mdblist_list"].build(
            _ctx(_sources(http), library_type="Show", list=14)
        )

    assert result.ids == [("tvdb", "81189"), ("tmdb", "87108"), ("imdb", "tt0417299")]


async def test_an_entry_with_no_usable_id_is_skipped_and_logged(caplog):
    """Skipped rather than raised: an unresolvable title is the resolver's
    ordinary business, and one member MDBList has no ids for must not take the
    whole collection down. Logged so it is not invisible."""
    routes = {"/lists/14/items": load("mdblist_list_items.json")}
    with caplog.at_level(logging.DEBUG, logger="autoposter.collections.builders.mdblist"):
        async with httpx.AsyncClient(transport=_routed(routes)) as http:
            result = await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))

    assert len(result.ids) == 2
    assert "A Title MDBList Has No Ids For" in caplog.text


async def test_the_flat_array_shape_is_read_too():
    """MDBList has served both a ``{movies, shows}`` object and one flat array
    with ``mediatype`` on each entry. The entries are identical either way, so
    the parser reads the media type off the entry and uses the container only
    to iterate."""
    routes = {"/lists/14/items": load("mdblist_list_items_flat.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))

    assert result.ids == [("tmdb", "550"), ("imdb", "tt6751668")]


async def test_a_dropped_entry_does_not_reorder_the_ones_around_it():
    """Source order is the collection's custom order. Both fixtures put a
    dropped entry *between* two kept ones -- the untyped title in the object
    shape, a show between two movies in the flat one -- so a parser that
    collected the survivors in any other order would be caught here."""
    for fixture in ("mdblist_list_items.json", "mdblist_list_items_flat.json"):
        routes = {"/lists/14/items": load(fixture)}
        async with httpx.AsyncClient(transport=_routed(routes)) as http:
            result = await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))

        assert result.ids == [("tmdb", "550"), ("imdb", "tt6751668")], fixture


async def test_a_library_type_mdblist_cannot_be_mapped_for_is_refused():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["mdblist_list"].build(
                _ctx(_sources(http), library_type="Music", list=14)
            )


async def test_a_response_in_no_recognised_shape_raises_rather_than_emptying():
    """A shape change would otherwise read as "this list is now empty", which
    one layer down means "remove every member"."""
    routes = {"/lists/14/items": {"items": [{"imdb_id": "tt0137523"}]}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(MDBListRefused):
            await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))


async def test_an_entry_with_no_mediatype_raises():
    """Not skipped: nothing places it, and if the field has gone away every
    entry lacks it -- which would silently empty the collection."""
    routes = {"/lists/14/items": [{"title": "Untyped", "tmdb_id": 550}]}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(MDBListRefused):
            await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))


async def test_a_list_that_really_is_empty_is_data_not_a_failure():
    routes = {"/lists/14/items": {"movies": [], "shows": []}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))

    assert result.ids == []


# --- failure ------------------------------------------------------------------


async def test_a_missing_list_raises_naming_the_reference():
    """``fetch_json`` turns a 404 into ``None``, and ``None`` must not become an
    empty collection -- in sync mode that removes every member."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(MDBListRefused) as caught:
            await REGISTRY["mdblist_list"].build(
                _ctx(_sources(http), list="linaspurinis/top-watched")
            )

    assert "linaspurinis/top-watched" in str(caught.value)


async def test_an_error_body_that_is_not_the_budget_raises():
    routes = {"/lists/14/items": load("mdblist_error.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(MDBListRefused):
            await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))


async def test_the_builder_raises_when_mdblist_is_not_configured():
    """Absent means None and None means raise. ``NullMDBListClient`` is not
    what a list builder gets: that stand-in exists so one metadata field can go
    missing quietly."""
    with pytest.raises(MdblistBuilderRefused, match="MDBList"):
        await REGISTRY["mdblist_list"].build(_ctx(SourceClients(), list=14))


# --- the budget short-circuit -------------------------------------------------


async def test_the_budget_error_becomes_its_own_exception():
    routes = {"/lists/14/items": load("mdblist_limit_reached.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(MDBListLimitReached):
            await REGISTRY["mdblist_list"].build(_ctx(_sources(http), list=14))


async def test_one_budget_hit_short_circuits_every_later_list_in_the_pass():
    """The centerpiece. Three definitions, the transport answering "budget
    spent" on the first: exactly ONE request leaves the process and all three
    definitions fail. Without the memo each definition would spend another call
    against an allowance that is already gone -- and MDBList answers 200, so
    nothing below would even slow down."""
    seen: list = []
    routes = {
        "/lists/1/items": load("mdblist_limit_reached.json"),
        "/lists/2/items": load("mdblist_list_items.json"),
        "/lists/3/items": load("mdblist_list_items.json"),
    }
    run_cache: dict = {}
    failed = 0
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        sources = _sources(http)
        for reference in (1, 2, 3):
            try:
                await REGISTRY["mdblist_list"].build(
                    _ctx(sources, run_cache=run_cache, list=reference)
                )
            except MDBListLimitReached:
                failed += 1

    assert failed == 3
    assert len(seen) == 1
    assert seen[0].url.path == "/lists/1/items"


async def test_the_short_circuit_re_raises_the_same_exception():
    """The memo holds the exception, not a flag, so every contained definition
    reports the failure MDBList actually gave -- the ``imdb_award._event``
    precedent, which memoises its failure for the same reason."""
    routes = {"/lists/1/items": load("mdblist_limit_reached.json")}
    run_cache: dict = {}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        sources = _sources(http)
        with pytest.raises(MDBListLimitReached) as first:
            await REGISTRY["mdblist_list"].build(_ctx(sources, run_cache=run_cache, list=1))
        with pytest.raises(MDBListLimitReached) as second:
            await REGISTRY["mdblist_list"].build(_ctx(sources, run_cache=run_cache, list=2))

    assert second.value is first.value


async def test_a_fresh_pass_asks_again():
    """The memo is the pass's ``run_cache``, so a later pass -- after the daily
    allowance rolls over -- is not permanently poisoned by one bad afternoon."""
    seen: list = []
    routes = {"/lists/1/items": load("mdblist_limit_reached.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        sources = _sources(http)
        for _ in range(2):
            with pytest.raises(MDBListLimitReached):
                await REGISTRY["mdblist_list"].build(_ctx(sources, list=1))

    assert len(seen) == 2


async def test_an_ordinary_failure_does_not_short_circuit_the_pass():
    """Only the budget is a reason to stop asking. A list that 404s is one dead
    definition, not a dead source -- the next definition must still be built."""
    seen: list = []
    routes = {"/lists/2/items": load("mdblist_list_items.json")}
    run_cache: dict = {}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        sources = _sources(http)
        with pytest.raises(MDBListRefused):
            await REGISTRY["mdblist_list"].build(_ctx(sources, run_cache=run_cache, list=1))
        result = await REGISTRY["mdblist_list"].build(
            _ctx(sources, run_cache=run_cache, list=2)
        )

    assert result.ids == [("tmdb", "550"), ("imdb", "tt6751668")]
    assert len(seen) == 2


# --- registration and wiring --------------------------------------------------


def test_the_builder_is_registered_under_its_own_name():
    assert REGISTRY["mdblist_list"].type_name == "mdblist_list"


def _bundle_config():
    """Only the sections ``build_source_clients`` reads."""
    return SimpleNamespace(
        providers=SimpleNamespace(cache_ttl_seconds=3600),
        radarr=SimpleNamespace(enabled=False, base_url=""),
        sonarr=SimpleNamespace(enabled=False, base_url=""),
        tracearr=SimpleNamespace(enabled=False, base_url=""),
        manual_assets_root="/manual",
    )


def _secrets(mdblist_apikey="an-api-key"):
    return Secrets(
        database_url="postgresql+asyncpg://unused", plex_token="x",
        tmdb_token="x", tvdb_apikey="x", fanart_apikey="x", webhook_secret="x",
        mdblist_apikey=mdblist_apikey,
    )


async def test_the_pass_bundle_carries_an_mdblist_client_built_from_the_key():
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(_bundle_config(), _secrets(), http)

    assert isinstance(sources.mdblist, MDBListClient)


async def test_the_bundle_has_no_mdblist_client_when_the_key_is_unset():
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(_bundle_config(), _secrets(mdblist_apikey=""), http)

    assert sources.mdblist is None
