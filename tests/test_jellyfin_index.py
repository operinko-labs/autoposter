"""Spec §4.4: build, lookup by provider then basename, one narrow search on a
miss, ItemNotFound on the second miss, PathMismatch outside every root.

Controller ruling (Phase 2 identity corrections, 2026-09-12) amends this
brief: a season/episode ResolvedItem carries the SERIES' provider ids (never
the episode's own), parent_native_id is the series' id for a season and the
SEASON's id for an episode, root_folder is the series folder's basename, and
file_path is None for both seasons and episodes -- so a Jellyfin-resolved
item computes the same identity_key as its Plex twin. See the two
identity-parity tests below and the amended parent_native_id assertion in
test_an_episode_resolves_through_its_series_and_coordinates.
"""
import httpx
import pytest

from autoposter.intake.arr import RenderIntent
from autoposter.jellyfin.client import JellyfinApi
from autoposter.jellyfin.index import LibraryIndex
from autoposter.servers.base import ItemNotFound, PathMismatch
from autoposter.servers.identity import identity_key_for
from media_server_doubles import resolved

FOLDERS = [{"Name": "Movies", "CollectionType": "movies", "Locations": ["/media/Movies"], "ItemId": "lib-m"},
           {"Name": "TV", "CollectionType": "tvshows", "Locations": ["/media/TV"], "ItemId": "lib-t"}]
MATRIX = {"Id": "m1", "Name": "The Matrix", "Type": "Movie", "ProductionYear": 1999,
          "ProviderIds": {"Tmdb": "603", "Imdb": "tt0133093"}, "Path": "/media/Movies/The Matrix (1999)/m.mkv"}
SIMPSONS = {"Id": "s1", "Name": "The Simpsons", "Type": "Series", "ProviderIds": {"Tvdb": "71663"}, "Path": "/media/TV/The Simpsons"}
S2 = {"Id": "se2", "Name": "Season 2", "Type": "Season", "IndexNumber": 2, "SeriesId": "s1", "Path": "/media/TV/The Simpsons/Season 02"}
E3 = {"Id": "ep3", "Name": "Ep", "Type": "Episode", "IndexNumber": 3, "ParentIndexNumber": 2, "SeriesId": "s1",
      "ProviderIds": {"Tvdb": "55"}, "Path": "/media/TV/The Simpsons/Season 02/s02e03.mkv"}


def _api(extra_items=(), search_hits=()):
    calls = []
    async def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        p = request.url.path
        if p == "/Library/VirtualFolders":
            return httpx.Response(200, json=FOLDERS)
        if p == "/Items" and "searchTerm" in request.url.params:
            return httpx.Response(200, json={"Items": list(search_hits)})
        if p == "/Items":
            lib = request.url.params["parentId"]
            items = [MATRIX, *extra_items] if lib == "lib-m" else [SIMPSONS]
            return httpx.Response(200, json={"Items": items})
        if p == "/Shows/s1/Seasons":
            return httpx.Response(200, json={"Items": [S2]})
        if p == "/Shows/s1/Episodes":
            return httpx.Response(200, json={"Items": [E3]})
        return httpx.Response(404)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JellyfinApi(http, "https://jf", "k", "v1"), http, calls


async def test_a_movie_resolves_by_tmdb_with_root_folder_from_the_library_location():
    api, http, _ = _api()
    async with http:
        item = await LibraryIndex(api, excluded=set()).resolve(RenderIntent(kind="movie", title="The Matrix", tmdb_id=603))
    assert item.server == "jellyfin" and item.native_id == "m1" and item.library == "Movies"
    assert item.root_folder == "The Matrix (1999)" and item.file_path == MATRIX["Path"]
    assert item.tmdb_id == 603 and item.imdb_id == "tt0133093"


async def test_an_episode_resolves_through_its_series_and_coordinates():
    api, http, _ = _api()
    async with http:
        item = await LibraryIndex(api, excluded=set()).resolve(
            RenderIntent(kind="episode", title="The Simpsons", tvdb_id=71663, season_number=2, episode_number=3))
    assert item.native_id == "ep3" and item.parent_tvdb_id == 71663
    assert item.season_number == 2 and item.episode_number == 3
    # Controller ruling: parent_native_id is the two-hop model's immediate
    # parent -- the SEASON's id for an episode, not the series' -- so this
    # assertion changes from the brief's "s1" to "se2".
    assert item.parent_native_id == "se2"
    # Controller ruling: the episode's own provider ids are the SERIES' ids
    # (never the episode's own Tvdb=55), file_path is None (episodes key
    # without a file, like Plex), and root_folder is the series folder's
    # basename.
    assert item.tvdb_id == 71663
    assert item.file_path is None
    assert item.root_folder == "The Simpsons"


async def test_a_miss_makes_exactly_one_search_then_item_not_found():
    api, http, calls = _api()
    async with http:
        with pytest.raises(ItemNotFound):
            await LibraryIndex(api, excluded=set()).resolve(RenderIntent(kind="movie", title="Nope", tmdb_id=1, year=2001))
    searches = [c for c in calls if c[0] == "/Items" and "searchTerm" in c[1]]
    assert len(searches) == 1 and searches[0][1]["searchTerm"] == "Nope" and searches[0][1]["years"] == "2001"


async def test_a_search_hit_is_verified_by_provider_id_and_indexed():
    hit = {**MATRIX, "Id": "m9", "Name": "Late", "ProviderIds": {"Tmdb": "9"}, "Path": "/media/Movies/Late/l.mkv"}
    api, http, calls = _api(search_hits=[hit])
    index = LibraryIndex(api, excluded=set())
    async with http:
        first = await index.resolve(RenderIntent(kind="movie", title="Late", tmdb_id=9))
        second = await index.resolve(RenderIntent(kind="movie", title="Late", tmdb_id=9))
    assert first.native_id == second.native_id == "m9"
    assert sum(1 for c in calls if "searchTerm" in c[1]) == 1, "the second resolve came from the index"


async def test_a_wrong_search_hit_is_not_taken():
    hit = {**MATRIX, "Id": "wrong", "Name": "Late", "ProviderIds": {"Tmdb": "999"}}
    api, http, _ = _api(search_hits=[hit])
    async with http:
        with pytest.raises(ItemNotFound):
            await LibraryIndex(api, excluded=set()).resolve(RenderIntent(kind="movie", title="Late", tmdb_id=9))


async def test_a_path_outside_every_library_root_is_a_path_mismatch():
    stray = {**MATRIX, "Id": "x", "ProviderIds": {"Tmdb": "77"}, "Path": "/elsewhere/x.mkv"}
    api, http, _ = _api(extra_items=[stray])
    async with http:
        with pytest.raises(PathMismatch):
            await LibraryIndex(api, excluded=set()).resolve(RenderIntent(kind="movie", title="x", tmdb_id=77))


async def test_excluded_libraries_are_never_listed():
    api, http, calls = _api()
    async with http:
        await LibraryIndex(api, excluded={"TV"}).rebuild()
    assert all(c[1].get("parentId") != "lib-t" for c in calls if c[0] == "/Items")


async def test_a_failed_build_leaves_no_partial_index():
    async def handler(request): return httpx.Response(500)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    index = LibraryIndex(JellyfinApi(http, "https://jf", "k", "v1"), excluded=set())
    async with http:
        with pytest.raises(httpx.HTTPStatusError):
            await index.rebuild()
        assert index.built is False


async def test_a_jellyfin_episode_has_the_same_identity_key_as_its_plex_twin():
    """Controller ruling (identity parity): the series' provider ids, the
    two-hop parent model, and the None file_path together mean a Jellyfin
    episode and a Plex episode at the same coordinates key identically."""
    api, http, _ = _api()
    async with http:
        jf_item = await LibraryIndex(api, excluded=set()).resolve(
            RenderIntent(kind="episode", title="The Simpsons", tvdb_id=71663, season_number=2, episode_number=3))
    plex_item = resolved("plex", "9", kind="episode", tmdb_id=None, tvdb_id=71663, imdb_id=None,
                         season_number=2, episode_number=3, file_path=None, root_folder="The Simpsons")
    assert identity_key_for(jf_item) == identity_key_for(plex_item)


async def test_a_jellyfin_movie_has_the_same_identity_key_as_its_plex_twin():
    api, http, _ = _api()
    async with http:
        jf_item = await LibraryIndex(api, excluded=set()).resolve(RenderIntent(kind="movie", title="The Matrix", tmdb_id=603))
    # A different mount/root than Jellyfin's, same file basename: the tmdb
    # branch of identity_key ignores root_folder and keys on the basename
    # alone, same as test_the_same_file_on_two_mounts_is_one_item.
    plex_item = resolved("plex", "9", kind="movie", tmdb_id=603, imdb_id=None,
                         file_path="/plex-mount/Movies/The Matrix (1999)/m.mkv", root_folder="The Matrix (1999)")
    assert identity_key_for(jf_item) == identity_key_for(plex_item)
