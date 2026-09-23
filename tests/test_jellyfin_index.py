"""Spec §4.4: build, lookup by provider then basename, one narrow search on a
miss, ItemNotFound on the second miss, PathMismatch outside every root.

A 2026-09-12 identity correction amends the description above:
a season/episode ResolvedItem carries the SERIES' provider ids (never
the episode's own), parent_native_id is the series' id for a season and the
SEASON's id for an episode, root_folder is the series folder's basename, and
file_path is None for both seasons and episodes -- so a Jellyfin-resolved
item computes the same identity_key as its Plex twin. See the two
identity-parity tests below and the amended parent_native_id assertion in
test_an_episode_resolves_through_its_series_and_coordinates.
"""
import asyncio

import httpx
import pytest

from autoposter.intake.arr import RenderIntent
from autoposter.jellyfin import index as jellyfin_index
from autoposter.jellyfin.client import JellyfinApi
from autoposter.jellyfin.index import LibraryIndex
from autoposter.servers.base import ItemNotFound, PathMismatch
from autoposter.servers.identity import identity_key_for
from media_server_doubles import resolved

FOLDERS = [{"Name": "Movies", "CollectionType": "movies", "Locations": ["/media/Movies"], "ItemId": "lib-m"},
           {"Name": "TV", "CollectionType": "tvshows", "Locations": ["/media/TV"], "ItemId": "lib-t"}]
MATRIX = {"Id": "m1", "Name": "The Matrix", "Type": "Movie", "ProductionYear": 1999,
          "ProviderIds": {"Tmdb": "603", "Imdb": "tt0133093"}, "Path": "/media/Movies/The Matrix (1999)/m.mkv"}
SIMPSONS = {"Id": "s1", "Name": "The Simpsons", "Type": "Series", "ProductionYear": 1989,
            "ProviderIds": {"Tvdb": "71663"}, "Path": "/media/TV/The Simpsons"}
S2 = {"Id": "se2", "Name": "Season 2", "Type": "Season", "IndexNumber": 2, "SeriesId": "s1", "Path": "/media/TV/The Simpsons/Season 02"}
E3 = {"Id": "ep3", "Name": "Ep", "Type": "Episode", "IndexNumber": 3, "ParentIndexNumber": 2, "SeriesId": "s1",
      # ProductionYear here is the episode's own AIR year, deliberately unlike
      # the series' 1989 -- a test reading item.year == 1989 proves it came
      # from the series (ids_source), not from the episode's own dto.
      "ProductionYear": 2004,
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
    # parent_native_id is the two-hop model's immediate
    # parent -- the SEASON's id for an episode, not the series' -- so this
    # assertion changes from "s1" to "se2".
    assert item.parent_native_id == "se2"
    # The episode's own provider ids are the SERIES' ids
    # (never the episode's own Tvdb=55), file_path is None (episodes key
    # without a file, like Plex), and root_folder is the series folder's
    # basename.
    assert item.tvdb_id == 71663
    assert item.file_path is None
    assert item.root_folder == "The Simpsons"
    # Minor: year for a season/episode comes from the SERIES (ids_source),
    # mirroring the Plex resolver's container.year -- not from the episode's
    # own ProductionYear (2004, a deliberately different decoy above).
    assert item.year == 1989


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
    item_calls = [c for c in calls if c[0] == "/Items" and "parentId" in c[1]]
    assert all(c[1].get("parentId") != "lib-t" for c in item_calls)
    # Minor: every build request is recursive and asks for ProviderIds+Path.
    assert all(c[1].get("recursive") == "true" and c[1].get("fields") == "ProviderIds,Path" for c in item_calls)


async def test_a_failed_build_leaves_no_partial_index():
    async def handler(request): return httpx.Response(500)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    index = LibraryIndex(JellyfinApi(http, "https://jf", "k", "v1"), excluded=set())
    async with http:
        with pytest.raises(httpx.HTTPStatusError):
            await index.rebuild()
        assert index.built is False


async def test_a_jellyfin_episode_has_the_same_identity_key_as_its_plex_twin():
    """The series' provider ids, the
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


async def test_library_map_translates_the_jellyfin_folder_name_to_the_plex_name():
    """config/schema.py's library_map is Plex name -> Jellyfin name. The
    Jellyfin folder here is really named "Films"; library_map={"Movies":
    "Films"} must make the resolved item report the PLEX-facing name
    "Movies", and root_folder must still come from the "Films" folder's own
    Locations (not be silently unresolvable because of the name swap)."""
    films_folder = [{"Name": "Films", "CollectionType": "movies", "Locations": ["/media/Films"], "ItemId": "lib-f"}]
    movie = {"Id": "m1", "Name": "The Matrix", "Type": "Movie", "ProductionYear": 1999,
             "ProviderIds": {"Tmdb": "603"}, "Path": "/media/Films/The Matrix (1999)/m.mkv"}
    async def handler(request):
        p = request.url.path
        if p == "/Library/VirtualFolders":
            return httpx.Response(200, json=films_folder)
        if p == "/Items":
            return httpx.Response(200, json={"Items": [movie]})
        return httpx.Response(404)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    index = LibraryIndex(JellyfinApi(http, "https://jf", "k", "v1"), excluded=set(), library_map={"Movies": "Films"})
    async with http:
        item = await index.resolve(RenderIntent(kind="movie", title="The Matrix", tmdb_id=603))
    assert item.library == "Movies"
    assert item.root_folder == "The Matrix (1999)"
    # library_names() must report the same translated name, not the raw
    # Jellyfin folder name (spec §1's absent rule reads this set).
    assert index.library_names() == {"Movies"}


async def test_library_names_excludes_the_excluded_and_non_movie_show_folders():
    """The behavioural half of servers/presence.py's absent rule (spec §1):
    an excluded folder and a non-movie/show CollectionType must both be
    absent from library_names() -- conformance's isinstance check alone
    cannot prove this, since it never calls the method."""
    folders = [
        {"Name": "Films", "CollectionType": "movies", "Locations": ["/media/Films"], "ItemId": "lib-f"},
        {"Name": "TV", "CollectionType": "tvshows", "Locations": ["/media/TV"], "ItemId": "lib-t"},
        {"Name": "Music", "CollectionType": "music", "Locations": ["/media/Music"], "ItemId": "lib-mu"},
    ]
    async def handler(request):
        p = request.url.path
        if p == "/Library/VirtualFolders":
            return httpx.Response(200, json=folders)
        if p == "/Items":
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(404)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    index = LibraryIndex(JellyfinApi(http, "https://jf", "k", "v1"), excluded={"TV"}, library_map={"Movies": "Films"})
    async with http:
        await index.rebuild()
    assert index.library_names() == {"Movies"}


async def test_concurrent_first_resolves_rebuild_the_index_only_once():
    """Two resolves racing on a fresh index must not each pay for their
    own /Library/VirtualFolders + /Items build -- the second's rebuild() call
    finds the lock already held and the first's build already complete."""
    api, http, calls = _api()
    index = LibraryIndex(api, excluded=set())
    async with http:
        movie_intent = RenderIntent(kind="movie", title="The Matrix", tmdb_id=603)
        episode_intent = RenderIntent(kind="episode", title="The Simpsons", tvdb_id=71663,
                                      season_number=2, episode_number=3)
        results = await asyncio.gather(index.resolve(movie_intent), index.resolve(episode_intent))
    assert {r.native_id for r in results} == {"m1", "ep3"}
    vf_calls = [c for c in calls if c[0] == "/Library/VirtualFolders"]
    assert len(vf_calls) == 1


async def test_a_rebuild_after_success_is_a_noop_and_the_old_index_keeps_resolving():
    """Minor: once built, a second rebuild() call (with no invalidate()
    first) must be a no-op -- built stays True and the old
    lookup keeps answering, even though the server would now fail every
    request."""
    state = {"fail": False}
    async def handler(request):
        if state["fail"]:
            return httpx.Response(500)
        p = request.url.path
        if p == "/Library/VirtualFolders":
            return httpx.Response(200, json=FOLDERS)
        if p == "/Items":
            lib = request.url.params["parentId"]
            items = [MATRIX] if lib == "lib-m" else [SIMPSONS]
            return httpx.Response(200, json={"Items": items})
        return httpx.Response(404)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    index = LibraryIndex(JellyfinApi(http, "https://jf", "k", "v1"), excluded=set())
    async with http:
        await index.rebuild()
        assert index.built is True
        state["fail"] = True
        await index.rebuild()  # must not raise: a no-op, the lock's builtin guard
        assert index.built is True
        item = await index.resolve(RenderIntent(kind="movie", title="The Matrix", tmdb_id=603))
    assert item.native_id == "m1"


async def test_kind_of_is_empty_for_an_unknown_type_and_it_is_never_indexed():
    """Minor: kind_of("") for an unrecognised Type (e.g. BoxSet), and such a
    dto must never be filed under any kind -- even when, as here, it shares a
    real movie's tmdb id and is returned to the index BEFORE that movie."""
    boxset = {"Id": "box1", "Name": "Marvel Collection", "Type": "BoxSet", "ProviderIds": {"Tmdb": "603"}}
    async def handler(request):
        p = request.url.path
        if p == "/Library/VirtualFolders":
            return httpx.Response(200, json=[FOLDERS[0]])
        if p == "/Items":
            return httpx.Response(200, json={"Items": [boxset, MATRIX]})
        return httpx.Response(404)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    index = LibraryIndex(JellyfinApi(http, "https://jf", "k", "v1"), excluded=set())
    async with http:
        assert index.kind_of(boxset) == ""
        item = await index.resolve(RenderIntent(kind="movie", title="The Matrix", tmdb_id=603))
    assert item.native_id == "m1"


async def test_the_index_rebuilds_after_max_age_seconds(monkeypatch):
    """spec §4.4 step 6: "rebuilt at the start of each full pass and on
    a fixed interval otherwise". A fresh index answers repeated resolves with
    no further build call; once the clock has moved past ``max_age_seconds``,
    the next resolve pays for exactly one fresh build."""
    clock = [1000.0]
    monkeypatch.setattr(jellyfin_index, "_monotonic", lambda: clock[0])
    api, http, calls = _api()
    index = LibraryIndex(api, excluded=set(), max_age_seconds=60)
    intent = RenderIntent(kind="movie", title="The Matrix", tmdb_id=603)
    async with http:
        await index.resolve(intent)
        assert len([c for c in calls if c[0] == "/Library/VirtualFolders"]) == 1

        await index.resolve(intent)
        assert len([c for c in calls if c[0] == "/Library/VirtualFolders"]) == 1, (
            "still fresh: no second build"
        )

        clock[0] += 61  # past max_age_seconds
        await index.resolve(intent)
        assert len([c for c in calls if c[0] == "/Library/VirtualFolders"]) == 2, (
            "stale: the next lookup must rebuild"
        )


# --- perf C4: the fast path and per-series listings ---------------------------

FUTURAMA = {"Id": "s2", "Name": "Futurama", "Type": "Series", "ProductionYear": 1999,
            "ProviderIds": {"Tvdb": "73871"}, "Path": "/media/TV/Futurama"}


class _GatedApi:
    """A ``JellyfinApi`` stand-in whose series listings wait on a per-series
    gate, so a test can hold one series' fetch open and watch what else
    proceeds meanwhile."""

    def __init__(self, series, *, open_gates=False, fail_first=False):
        self._series = list(series)
        self.gates = {s["Id"]: asyncio.Event() for s in self._series}
        self.asked = {s["Id"]: asyncio.Event() for s in self._series}
        if open_gates:
            for gate in self.gates.values():
                gate.set()
        self._fail_first = fail_first
        self.season_calls: list[str] = []

    async def virtual_folders(self):
        return FOLDERS

    async def items(self, **query):
        if "searchTerm" in query:
            return []
        return [MATRIX] if query["parentId"] == "lib-m" else list(self._series)

    async def seasons(self, series_id):
        self.season_calls.append(series_id)
        self.asked[series_id].set()
        await self.gates[series_id].wait()
        if self._fail_first:
            self._fail_first = False
            raise RuntimeError("Jellyfin dropped the connection")
        return [{**S2, "Id": "se2-" + series_id, "SeriesId": series_id}]

    async def episodes(self, series_id):
        return [{**E3, "Id": "ep3-" + series_id, "SeriesId": series_id}]


def _season(tvdb_id, title):
    return RenderIntent(kind="season", title=title, tvdb_id=tvdb_id, season_number=2)


async def test_two_concurrent_resolves_of_one_series_share_one_listing():
    api = _GatedApi([SIMPSONS])
    index = LibraryIndex(api, excluded=set())
    first = asyncio.create_task(index.resolve(_season(71663, "The Simpsons")))
    second = asyncio.create_task(index.resolve(_season(71663, "The Simpsons")))
    await asyncio.wait_for(api.asked["s1"].wait(), timeout=5)
    for _ in range(10):
        await asyncio.sleep(0)  # let the second resolve reach the listing

    api.gates["s1"].set()
    a, b = await asyncio.gather(first, second)

    assert a.native_id == b.native_id == "se2-s1"
    assert api.season_calls == ["s1"]


async def test_cancelling_one_waiter_does_not_cancel_the_shared_listing():
    """The ``asyncio.shield`` in ``_children_of``: one resolve's job timing
    out must not cancel the fetch another resolve of the same series is
    waiting on."""
    api = _GatedApi([SIMPSONS])
    index = LibraryIndex(api, excluded=set())
    first = asyncio.create_task(index.resolve(_season(71663, "The Simpsons")))
    second = asyncio.create_task(index.resolve(_season(71663, "The Simpsons")))
    await asyncio.wait_for(api.asked["s1"].wait(), timeout=5)
    for _ in range(10):
        await asyncio.sleep(0)  # let the second resolve reach the listing

    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    api.gates["s1"].set()
    item = await asyncio.wait_for(second, timeout=5)

    assert item.native_id == "se2-s1"
    assert api.season_calls == ["s1"]
    assert first.cancelled()
    assert index._listing == {}


async def test_a_slow_series_listing_does_not_hold_up_another_series():
    api = _GatedApi([SIMPSONS, FUTURAMA])
    api.gates["s2"].set()
    index = LibraryIndex(api, excluded=set())
    simpsons = asyncio.create_task(index.resolve(_season(71663, "The Simpsons")))
    await asyncio.wait_for(api.asked["s1"].wait(), timeout=5)

    futurama = await asyncio.wait_for(index.resolve(_season(73871, "Futurama")), timeout=5)

    assert futurama.native_id == "se2-s2"
    assert not simpsons.done(), "the Simpsons listing is still held open"
    api.gates["s1"].set()
    assert (await simpsons).native_id == "se2-s1"


async def test_a_fresh_index_answers_without_waiting_for_the_lock():
    api = _GatedApi([SIMPSONS], open_gates=True)
    index = LibraryIndex(api, excluded=set())
    await index.rebuild()

    async with index._lock:  # a refresh_folders or a rebuild in progress
        await asyncio.wait_for(index.rebuild(), timeout=5)


async def test_a_failed_series_listing_is_retried_by_the_next_resolve():
    api = _GatedApi([SIMPSONS], open_gates=True, fail_first=True)
    index = LibraryIndex(api, excluded=set())

    with pytest.raises(RuntimeError):
        await index.resolve(_season(71663, "The Simpsons"))
    item = await index.resolve(_season(71663, "The Simpsons"))

    assert item.native_id == "se2-s1"
    assert api.season_calls == ["s1", "s1"]
