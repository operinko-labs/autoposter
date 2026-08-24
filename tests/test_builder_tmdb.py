"""The TMDb collection builders: charts, and the five "by id" list sources.

The transport is pinned in ``test_tmdb_lists_client.py``; what these tests are
about is the three decisions the builders make on top of it:

- **which endpoint a chart means for this library.** ``chart: popular`` is
  ``/movie/popular`` on a Movie library and ``/tv/popular`` on a Show library,
  and a chart that has no form for this library type is refused rather than
  quietly built from the other one.
- **an absent client raises**, per ``SourceClients``: no TMDb token means the
  definition fails and is reported, not a collection that never fills.
- **a 404 raises**, all the way up from the client -- because an empty
  membership means "remove every member" one layer down.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.tmdb import TmdbBuilderRefused, TmdbRegionUnsupported
from autoposter.collections.service import build_source_clients
from autoposter.config.schema import Secrets
from autoposter.providers.tmdb_lists import TmdbListClient, TmdbListRefused

FIXTURES = Path(__file__).parent / "fixtures" / "collections"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _path(request) -> str:
    """The TMDb path, without the ``/3`` API-version prefix (see
    ``test_tmdb_lists_client.py``, which pins that the prefix is really sent)."""
    return request.url.path.removeprefix("/3")


def _routed(routes: dict, seen: list | None = None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        payload = routes.get(_path(request))
        if payload is None:
            return httpx.Response(404, json=load("tmdb_not_found.json"))
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _sources(http):
    return SourceClients(tmdb=TmdbListClient("a-read-access-token", http))


def _ctx(sources: SourceClients, library_type: str = "Movie", **params) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library, library_type=library_type, config=params, sources=sources
    )


# --- charts ------------------------------------------------------------------


async def test_a_chart_follows_the_library_type_to_the_movie_endpoint():
    seen: list = []
    routes = {"/movie/popular": load("tmdb_chart_movie_popular_p1.json") | {"total_pages": 1}}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_chart"].build(_ctx(_sources(http), chart="popular"))

    assert result.ids == [("tmdb", "438631"), ("tmdb", "693134")]
    assert _path(seen[0]) == "/movie/popular"


async def test_the_same_chart_key_reaches_the_tv_endpoint_on_a_show_library():
    """One definition, both libraries: ``chart: popular`` is what the operator
    writes and the library the pass runs against decides the media type."""
    seen: list = []
    routes = {"/tv/popular": load("tmdb_chart_tv_top_rated.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_chart"].build(
            _ctx(_sources(http), library_type="Show", chart="popular")
        )

    assert result.ids == [("tmdb", "95396"), ("tmdb", "1396")]
    assert _path(seen[0]) == "/tv/popular"


async def test_a_tv_only_chart_on_a_movie_library_raises_at_build():
    """The mismatch cannot be caught at config load: a definition with no
    ``libraries:`` key applies to every library in the pass, so which library
    type a build is running against is only known here. Building it from
    ``/movie/popular`` instead would be a full, plausible, wrong collection."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch) as caught:
            await REGISTRY["tmdb_chart"].build(
                _ctx(_sources(http), chart="airing_today")
            )

    message = str(caught.value)
    assert "airing_today" in message and "Show" in message and "Movie" in message


async def test_a_movie_only_chart_on_a_show_library_raises_at_build():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["tmdb_chart"].build(
                _ctx(_sources(http), library_type="Show", chart="now_playing")
            )


async def test_the_mismatch_is_refused_before_a_single_request():
    """A refusal that had already spent a round trip would be a refusal that
    can also fail with a network error, which is a different report."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["tmdb_chart"].build(_ctx(_sources(http), chart="on_the_air"))

    assert seen == []


async def test_a_chart_threads_region_and_language():
    seen: list = []
    routes = {"/movie/upcoming": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["tmdb_chart"].build(
            _ctx(_sources(http), chart="upcoming", region="FI", language="fi-FI")
        )

    assert seen[0].url.params["region"] == "FI"
    assert seen[0].url.params["language"] == "fi-FI"


async def test_a_lowercase_region_is_normalised_rather_than_sent_as_typed():
    """TMDb wants an uppercase ISO-3166-1 code and silently ignores anything
    else -- which would be an operator's region quietly not applied."""
    seen: list = []
    routes = {"/movie/upcoming": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["tmdb_chart"].build(
            _ctx(_sources(http), chart="upcoming", region="fi")
        )

    assert seen[0].url.params["region"] == "FI"


async def test_region_still_works_on_the_movie_popular_chart():
    """The four ``/movie/*`` list endpoints are where TMDb actually applies
    ``region`` -- the fix round's refusal must not catch the charts it is
    supposed to keep working."""
    seen: list = []
    routes = {"/movie/popular": load("tmdb_chart_movie_popular_p1.json") | {"total_pages": 1}}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_chart"].build(
            _ctx(_sources(http), chart="popular", region="FI")
        )

    assert result.ids == [("tmdb", "438631"), ("tmdb", "693134")]
    assert seen[0].url.params["region"] == "FI"


async def test_region_on_the_tv_popular_chart_is_refused():
    """``/tv/popular`` accepts ``region`` over HTTP and silently ignores it --
    the operator would get the global chart with no signal, plus a wasted
    distinct cache key. The builder must refuse before that request is sent."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(TmdbRegionUnsupported) as caught:
            await REGISTRY["tmdb_chart"].build(
                _ctx(_sources(http), library_type="Show", chart="popular", region="FI")
            )

    message = str(caught.value)
    assert "popular" in message and "top_rated" in message
    assert "now_playing" in message and "upcoming" in message
    assert seen == []


async def test_region_on_a_trending_chart_is_refused():
    """None of the ``/trending/*`` endpoints honour ``region`` at all."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(TmdbRegionUnsupported):
            await REGISTRY["tmdb_chart"].build(
                _ctx(_sources(http), chart="trending_day", region="FI")
            )

    assert seen == []


async def test_an_unknown_chart_is_refused():
    with pytest.raises(ValidationError, match="popular"):
        await REGISTRY["tmdb_chart"].build(_ctx(SourceClients(), chart="most_watched"))


async def test_a_chart_refuses_params_it_does_not_understand():
    with pytest.raises(ValidationError):
        await REGISTRY["tmdb_chart"].build(
            _ctx(SourceClients(), chart="popular", limit=10)
        )


async def test_a_malformed_region_is_refused():
    with pytest.raises(ValidationError):
        await REGISTRY["tmdb_chart"].build(
            _ctx(SourceClients(), chart="popular", region="Finland")
        )


async def test_a_malformed_language_is_refused():
    with pytest.raises(ValidationError):
        await REGISTRY["tmdb_chart"].build(
            _ctx(SourceClients(), chart="popular", language="finnish")
        )


async def test_a_chart_offers_no_summary_or_poster():
    """Unlike ``imdb_chart``, whose title and summary are Kometa translation
    strings transcribed in ``docs/research/kometa-collections.md`` §5. No such
    transcription exists for TMDb's charts, and a guessed poster key is a
    hosted URL that 404s and leaves the collection quietly without artwork."""
    routes = {"/tv/popular": load("tmdb_chart_tv_top_rated.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["tmdb_chart"].build(
            _ctx(_sources(http), library_type="Show", chart="popular")
        )

    assert result.summary is None
    assert (result.poster_kind, result.poster_key) == (None, None)


# --- list, collection, company, network, keyword -----------------------------


async def test_a_list_becomes_its_members_in_list_order():
    routes = {"/list/7096": load("tmdb_list_p1.json") | {"item_count": 2}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["tmdb_list"].build(_ctx(_sources(http), id=7096))

    assert result.ids == [("tmdb", "11"), ("tmdb", "1891")]


async def test_a_list_id_written_as_a_string_still_works():
    """YAML quoting is the operator's business, not the builder's."""
    routes = {"/list/7096": load("tmdb_list_p1.json") | {"item_count": 2}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["tmdb_list"].build(_ctx(_sources(http), id="7096"))

    assert result.ids == [("tmdb", "11"), ("tmdb", "1891")]


async def test_a_missing_list_raises_naming_the_id_rather_than_building_nothing():
    """The pin: ``fetch_json`` turns a 404 into ``None``, and ``None`` must not
    become an empty collection -- in sync mode that removes every member."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TmdbListRefused) as caught:
            await REGISTRY["tmdb_list"].build(_ctx(_sources(http), id=7096))

    assert "7096" in str(caught.value)


async def test_a_collection_becomes_its_parts():
    async with httpx.AsyncClient(
        transport=_routed({"/collection/10": load("tmdb_collection.json")})
    ) as http:
        result = await REGISTRY["tmdb_collection"].build(_ctx(_sources(http), id=10))

    assert result.ids == [("tmdb", "11"), ("tmdb", "1891"), ("tmdb", "1892")]


async def test_a_collection_is_refused_on_a_show_library():
    """A TMDb collection is a movie franchise: its ``parts`` are movies, so on
    a Show library it would resolve to nothing at all and report a count."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["tmdb_collection"].build(
                _ctx(_sources(http), library_type="Show", id=10)
            )


async def test_a_company_discovers_movies_on_a_movie_library():
    seen: list = []
    routes = {"/discover/movie": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_company"].build(_ctx(_sources(http), id=420))

    assert result.ids == [("tmdb", "11"), ("tmdb", "1891")]
    assert seen[0].url.params["with_companies"] == "420"


async def test_a_company_discovers_shows_on_a_show_library():
    seen: list = []
    routes = {"/discover/tv": load("tmdb_discover_tv.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_company"].build(
            _ctx(_sources(http), library_type="Show", id=420)
        )

    assert result.ids == [("tmdb", "95396"), ("tmdb", "1416")]
    assert seen[0].url.params["with_companies"] == "420"


async def test_a_keyword_follows_the_library_type_too():
    seen: list = []
    routes = {"/discover/tv": load("tmdb_discover_tv.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_keyword"].build(
            _ctx(_sources(http), library_type="Show", id=9715)
        )

    assert result.ids == [("tmdb", "95396"), ("tmdb", "1416")]
    assert seen[0].url.params["with_keywords"] == "9715"


async def test_a_network_discovers_shows():
    seen: list = []
    routes = {"/discover/tv": load("tmdb_discover_tv.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_network"].build(
            _ctx(_sources(http), library_type="Show", id=213)
        )

    assert result.ids == [("tmdb", "95396"), ("tmdb", "1416")]
    assert seen[0].url.params["with_networks"] == "213"


async def test_a_network_is_refused_on_a_movie_library():
    """TMDb has no movie form of a network at all -- there is no
    ``/discover/movie`` filter for one -- so this is a definition that can
    only ever have meant a Show library."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["tmdb_network"].build(_ctx(_sources(http), id=213))


@pytest.mark.parametrize(
    "builder", ["tmdb_list", "tmdb_collection", "tmdb_company", "tmdb_keyword"]
)
async def test_the_id_builders_refuse_params_they_do_not_understand(builder):
    with pytest.raises(ValidationError):
        await REGISTRY[builder].build(_ctx(SourceClients(), id=10, ids=[11]))


@pytest.mark.parametrize(
    "builder", ["tmdb_list", "tmdb_collection", "tmdb_company", "tmdb_keyword"]
)
async def test_the_id_builders_refuse_a_missing_id(builder):
    with pytest.raises(ValidationError):
        await REGISTRY[builder].build(_ctx(SourceClients()))


async def test_an_id_of_zero_is_refused():
    """``0`` is what a mis-read config or an empty template renders to, and
    TMDb answers it with a 404 the operator then has to go and read."""
    with pytest.raises(ValidationError):
        await REGISTRY["tmdb_collection"].build(_ctx(SourceClients(), id=0))


# --- the absent client -------------------------------------------------------


@pytest.mark.parametrize(
    "builder,params",
    [
        ("tmdb_chart", {"chart": "popular"}),
        ("tmdb_list", {"id": 7096}),
        ("tmdb_collection", {"id": 10}),
        ("tmdb_company", {"id": 420}),
        ("tmdb_keyword", {"id": 9715}),
    ],
)
async def test_every_tmdb_builder_raises_when_tmdb_is_not_configured(builder, params):
    """Absent means None and None means raise -- the engine contains it as one
    dead source, which reads as "this definition failed" rather than a
    collection that quietly never fills."""
    with pytest.raises(TmdbBuilderRefused, match="TMDb"):
        await REGISTRY[builder].build(_ctx(SourceClients(), **params))


async def test_the_network_builder_raises_when_tmdb_is_not_configured():
    with pytest.raises(TmdbBuilderRefused, match="TMDb"):
        await REGISTRY["tmdb_network"].build(
            _ctx(SourceClients(), library_type="Show", id=213)
        )


# --- registration ------------------------------------------------------------


def test_every_tmdb_builder_is_registered_under_its_own_name():
    for name in (
        "tmdb_chart", "tmdb_list", "tmdb_collection",
        "tmdb_company", "tmdb_network", "tmdb_keyword",
    ):
        assert REGISTRY[name].type_name == name


# --- wiring ------------------------------------------------------------------


def _bundle_config():
    """Only the sections ``build_source_clients`` reads."""
    return SimpleNamespace(
        providers=SimpleNamespace(cache_ttl_seconds=3600),
        radarr=SimpleNamespace(enabled=False, base_url=""),
        sonarr=SimpleNamespace(enabled=False, base_url=""),
        manual_assets_root="/manual",
    )


def _secrets(tmdb_token="a-read-access-token"):
    return Secrets(
        database_url="postgresql+asyncpg://unused", plex_token="x",
        tmdb_token=tmdb_token, tvdb_apikey="x", fanart_apikey="x",
        webhook_secret="x",
    )


async def test_the_pass_bundle_carries_a_tmdb_client_built_from_the_token():
    """``SourceClients.tmdb`` defaults to None, so a bundle that stopped being
    built with one would leave every TMDb definition reporting "not
    configured" with nothing at load or run time to say otherwise."""
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(_bundle_config(), _secrets(), http)

    assert isinstance(sources.tmdb, TmdbListClient)
    assert sources.tmdb._token == "a-read-access-token"
    assert sources.tmdb._cache_ttl_seconds == 3600


async def test_the_bundle_has_no_tmdb_client_when_the_token_is_blank():
    """A client holding "" would turn every TMDb definition into a 401 the
    operator has to read out of a log, rather than "TMDb is not configured"."""
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(_bundle_config(), _secrets(tmdb_token=""), http)

    assert sources.tmdb is None
