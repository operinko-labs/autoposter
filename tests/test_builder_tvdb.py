"""The TVDb builders: one list, and the two explicit-id forms.

The transport is ``providers/tvdb.py``'s, which already knows how to log in and
how to recover from a 401. The new endpoints go through the *same*
``_authenticated_get``, so the retry is inherited rather than reimplemented --
and inheriting it is only worth anything if something proves it still happens,
which is what ``test_a_401_on_the_list_endpoint_still_re_logs_in_once`` is for.

What the builders add on top:

- **Which id a list entry means.** A v4 list entity is ``{"order": n,
  "seriesId": …, "movieId": …}`` -- one or the other, never both -- so a TVDb
  list is inherently mixed and cannot be typed as a whole. The library decides
  which half of it is being asked for, and the other half is dropped: series
  ids and movie ids are different id spaces sharing the ``tvdb`` namespace, so
  emitting a movie id into a Show library could match an unrelated series.
- **A slug needs a lookup.** ``/lists/slug/{slug}`` answers the *base* record,
  which has no entities, so the slug form resolves to an id and then asks
  ``/lists/{id}/extended``. Two requests, both cached.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.tvdb import TvdbBuilderRefused
from autoposter.collections.service import build_source_clients
from autoposter.config.schema import Secrets
from autoposter.providers.tvdb import TVDBClient, TVDBListRefused

FIXTURES = Path(__file__).parent / "fixtures" / "collections"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _routed(routes: dict, seen: list | None = None):
    """Serve ``{path: payload}`` behind a working login; anything else 404s."""

    def handler(request):
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "a-token"}})
        if seen is not None:
            seen.append(request)
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, json={"status": "failure"})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _sources(http):
    return SourceClients(tvdb=TVDBClient("an-apikey", http))


def _ctx(sources: SourceClients, library_type: str = "Show", **params) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library, library_type=library_type, config=params, sources=sources
    )


# --- tvdb_list ----------------------------------------------------------------


async def test_a_list_on_a_show_library_becomes_its_series_entries():
    seen: list = []
    routes = {"/v4/lists/8194/extended": load("tvdb_list_extended.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tvdb_list"].build(_ctx(_sources(http), id=8194))

    assert result.ids == [("tvdb", "81189"), ("tvdb", "362133")]
    assert seen[0].url.path == "/v4/lists/8194/extended"


async def test_the_same_list_on_a_movie_library_becomes_its_movie_entries():
    """The list is mixed and the library decides which half it meant. Passing
    the series ids through as well would put ids from a different TVDb id space
    into the ``tvdb`` namespace, where a collision resolves to the wrong
    title."""
    routes = {"/v4/lists/8194/extended": load("tvdb_list_extended.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["tvdb_list"].build(
            _ctx(_sources(http), library_type="Movie", id=8194)
        )

    assert result.ids == [("tvdb", "15678"), ("tvdb", "9982")]


async def test_an_entity_that_names_neither_kind_is_skipped():
    """The fixture's last entity has ``seriesId`` and ``movieId`` both null --
    dropping it is the same judgement ``mdblist_list`` makes about a member
    with no ids: unresolvable is the resolver's ordinary business."""
    routes = {"/v4/lists/8194/extended": load("tvdb_list_extended.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["tvdb_list"].build(_ctx(_sources(http), id=8194))

    assert len(load("tvdb_list_extended.json")["data"]["entities"]) == 5
    assert len(result.ids) == 2


async def test_a_slug_is_resolved_to_an_id_and_then_asked_for_its_entities():
    """``/lists/slug/{slug}`` answers a base record with no ``entities``, so
    the slug form is two requests. It matters for parity: a TVDb list URL
    carries the slug, not the id, so that is what an operator has to hand."""
    seen: list = []
    routes = {
        "/v4/lists/slug/a-mixed-tvdb-list": load("tvdb_list_slug.json"),
        "/v4/lists/8194/extended": load("tvdb_list_extended.json"),
    }
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tvdb_list"].build(
            _ctx(_sources(http), slug="a-mixed-tvdb-list")
        )

    assert [request.url.path for request in seen] == [
        "/v4/lists/slug/a-mixed-tvdb-list",
        "/v4/lists/8194/extended",
    ]
    assert result.ids == [("tvdb", "81189"), ("tvdb", "362133")]


async def test_a_slug_that_does_not_exist_raises_naming_it():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TVDBListRefused) as caught:
            await REGISTRY["tvdb_list"].build(_ctx(_sources(http), slug="no-such-list"))

    assert "no-such-list" in str(caught.value)


async def test_a_missing_list_raises_rather_than_building_nothing():
    """A 404 is ``None`` out of ``fetch_json``, and an empty membership one
    layer down means "remove every member"."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TVDBListRefused) as caught:
            await REGISTRY["tvdb_list"].build(_ctx(_sources(http), id=8194))

    assert "8194" in str(caught.value)


async def test_a_response_with_no_entities_array_raises():
    routes = {"/v4/lists/8194/extended": load("tvdb_list_slug.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TVDBListRefused):
            await REGISTRY["tvdb_list"].build(_ctx(_sources(http), id=8194))


async def test_a_list_that_really_is_empty_is_data_not_a_failure():
    routes = {"/v4/lists/8194/extended": {"data": {"id": 8194, "entities": []}}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["tvdb_list"].build(_ctx(_sources(http), id=8194))

    assert result.ids == []


async def test_a_library_type_a_tvdb_list_has_no_entity_kind_for_is_refused():
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["tvdb_list"].build(
                _ctx(_sources(http), library_type="Music", id=8194)
            )

    assert seen == []


async def test_a_list_needs_exactly_one_of_id_and_slug():
    with pytest.raises(ValidationError):
        await REGISTRY["tvdb_list"].build(_ctx(SourceClients()))
    with pytest.raises(ValidationError):
        await REGISTRY["tvdb_list"].build(
            _ctx(SourceClients(), id=8194, slug="a-mixed-tvdb-list")
        )


@pytest.mark.parametrize(
    "slug",
    [
        "a/b",
        "https://thetvdb.com/lists/a-mixed-tvdb-list",
        "?a",
        "a#b",
    ],
)
async def test_a_slug_that_is_not_a_bare_slug_is_refused(slug):
    """Fix round, finding 3: ``slug`` was interpolated into the path
    unvalidated, so a pasted list URL or a value containing '/', '?', '#'
    changed the request rather than 404ing cleanly. Same one-line pattern as
    MDBList's ``_LIST_REFERENCE``."""
    with pytest.raises(ValidationError):
        await REGISTRY["tvdb_list"].build(_ctx(SourceClients(), slug=slug))


async def test_a_list_refuses_params_it_does_not_understand():
    with pytest.raises(ValidationError):
        await REGISTRY["tvdb_list"].build(_ctx(SourceClients(), id=8194, limit=10))


async def test_a_list_id_of_zero_is_refused():
    with pytest.raises(ValidationError):
        await REGISTRY["tvdb_list"].build(_ctx(SourceClients(), id=0))


# --- the inherited login dance ------------------------------------------------


async def test_a_401_on_the_list_endpoint_still_re_logs_in_once():
    """The new endpoints go through ``_authenticated_get``, so TVDb's month-old
    token expiring mid-pass costs one re-login and not a failed definition. It
    is inherited, not reimplemented -- which is exactly why it needs a test of
    its own on this path."""
    calls = {"login": 0, "list": 0}

    async def handler(request):
        if request.url.path == "/v4/login":
            calls["login"] += 1
            return httpx.Response(200, json={"data": {"token": f"tok-{calls['login']}"}})
        calls["list"] += 1
        if request.headers.get("Authorization") == "Bearer tok-1":
            return httpx.Response(401)
        return httpx.Response(200, json=load("tvdb_list_extended.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await REGISTRY["tvdb_list"].build(_ctx(_sources(http), id=8194))

    assert result.ids == [("tvdb", "81189"), ("tvdb", "362133")]
    assert calls["login"] == 2  # the cold login, then exactly one re-login
    assert calls["list"] == 2  # the original request, then one retry


async def test_the_list_endpoint_sends_the_bearer_token():
    seen: list = []
    routes = {"/v4/lists/8194/extended": load("tvdb_list_extended.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["tvdb_list"].build(_ctx(_sources(http), id=8194))

    assert seen[0].headers["Authorization"] == "Bearer a-token"


# --- tvdb_movie / tvdb_show ---------------------------------------------------


async def test_explicit_show_ids_need_no_network():
    result = await REGISTRY["tvdb_show"].build(
        _ctx(SourceClients(), ids=[81189, "362133"])
    )

    assert result.ids == [("tvdb", "81189"), ("tvdb", "362133")]


async def test_explicit_movie_ids_need_no_network():
    result = await REGISTRY["tvdb_movie"].build(
        _ctx(SourceClients(), library_type="Movie", ids=[15678])
    )

    assert result.ids == [("tvdb", "15678")]


async def test_tvdb_show_is_refused_on_a_movie_library():
    """TVDb's series and movie ids are separate spaces in one namespace, so
    nothing about the ids themselves could catch the mix-up -- it would resolve
    to nothing, or to the wrong title, and be reported as a count."""
    with pytest.raises(LibraryTypeMismatch):
        await REGISTRY["tvdb_show"].build(
            _ctx(SourceClients(), library_type="Movie", ids=[81189])
        )


async def test_tvdb_movie_is_refused_on_a_show_library():
    with pytest.raises(LibraryTypeMismatch):
        await REGISTRY["tvdb_movie"].build(_ctx(SourceClients(), ids=[15678]))


@pytest.mark.parametrize("builder,library_type", [("tvdb_show", "Show"), ("tvdb_movie", "Movie")])
async def test_an_imdb_id_under_a_tvdb_builder_is_refused(builder, library_type):
    with pytest.raises(ValidationError, match="TVDb"):
        await REGISTRY[builder].build(
            _ctx(SourceClients(), library_type=library_type, ids=["tt0903747"])
        )


@pytest.mark.parametrize("builder,library_type", [("tvdb_show", "Show"), ("tvdb_movie", "Movie")])
async def test_an_empty_id_list_is_refused(builder, library_type):
    with pytest.raises(ValidationError):
        await REGISTRY[builder].build(
            _ctx(SourceClients(), library_type=library_type, ids=[])
        )


@pytest.mark.parametrize("builder,library_type", [("tvdb_show", "Show"), ("tvdb_movie", "Movie")])
async def test_the_explicit_builders_refuse_params_they_do_not_understand(builder, library_type):
    with pytest.raises(ValidationError):
        await REGISTRY[builder].build(
            _ctx(SourceClients(), library_type=library_type, id=81189)
        )


# --- the absent client ---------------------------------------------------------


async def test_the_list_builder_raises_when_tvdb_is_not_configured():
    with pytest.raises(TvdbBuilderRefused, match="TVDb"):
        await REGISTRY["tvdb_list"].build(_ctx(SourceClients(), id=8194))


# --- registration and wiring ---------------------------------------------------


def test_every_tvdb_builder_is_registered_under_its_own_name():
    for name in ("tvdb_list", "tvdb_movie", "tvdb_show"):
        assert REGISTRY[name].type_name == name


def _bundle_config():
    """Only the sections ``build_source_clients`` reads."""
    return SimpleNamespace(
        providers=SimpleNamespace(cache_ttl_seconds=3600),
        radarr=SimpleNamespace(enabled=False, base_url=""),
        sonarr=SimpleNamespace(enabled=False, base_url=""),
        manual_assets_root="/manual",
    )


def _secrets():
    return Secrets(
        database_url="postgresql+asyncpg://unused", plex_token="x",
        tmdb_token="x", tvdb_apikey="a-tvdb-apikey", fanart_apikey="x",
        webhook_secret="x",
    )


async def test_the_pass_bundle_carries_a_tvdb_client_built_from_the_apikey():
    """``SourceClients.tvdb`` defaults to None, so a bundle that stopped being
    built with one would leave every TVDb definition reporting "not configured"
    with nothing at load or run time to say otherwise."""
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(_bundle_config(), _secrets(), http)

    assert isinstance(sources.tvdb, TVDBClient)
    assert sources.tvdb._cache_ttl_seconds == 3600
