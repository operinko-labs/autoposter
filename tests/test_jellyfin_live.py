"""Spec §11. Needs AUTOPOSTER_TEST_JELLYFIN_URL/_KEY; skipped otherwise, deselected in CI.

A failure here is a **finding**, not a regression: the fix belongs to the one
``JellyfinApi``/``LibraryIndex`` function the failing V-item names (spec §11),
not to this file.
"""
import httpx
import pytest

from autoposter.jellyfin.client import JellyfinApi, _content_type

pytestmark = pytest.mark.jellyfin


async def _lowest_named_movie(api: JellyfinApi) -> dict:
    """The one movie V3/V4 write to, picked deterministically: the lowest
    ``Name`` in the Movies library. Sorted client-side rather than via
    ``sortBy``/``limit`` -- capture -> "/Items" -> get does not document
    those parameters (task-17-addendum.md permits either)."""
    movies = next(f for f in await api.virtual_folders() if f["CollectionType"] == "movies")
    items = await api.items(parentId=movies["ItemId"], recursive="true", includeItemTypes="Movie", fields="Path")
    return min(items, key=lambda item: item["Name"])


def test_the_network_guard_exempts_this_suite(jellyfin_live):
    """Regression, not a V-item: conftest.py's autouse ``no_outbound_network``
    fixture blocked every real ``httpx`` call, this file's live calls
    included -- before this task's fix, V1-V6 could never reach the real
    instance even with credentials set (every one failed with
    ``RuntimeError: tests must not make real network calls``). Fixed by
    exempting tests marked ``jellyfin``; this guards that exemption against a
    future edit narrowing it back down."""
    assert httpx.AsyncHTTPTransport.handle_async_request.__name__ != "blocked"


async def test_v1_the_mediabrowser_header_is_accepted(jellyfin_live):
    """V1 (spec §11): capture -> "/System/Info" -> get."""
    url, key = jellyfin_live
    async with httpx.AsyncClient() as http:
        info = await JellyfinApi(http, url, key, "test").system_info()
    assert info["Version"].startswith("12."), info


async def test_v2_provider_id_keys_as_served_and_whether_seasons_carry_any(jellyfin_live):
    """V2 (spec §11): capture -> "/Items" -> get, "/Shows/{seriesId}/Seasons" -> get."""
    url, key = jellyfin_live
    async with httpx.AsyncClient() as http:
        api = JellyfinApi(http, url, key, "test")
        folders = await api.virtual_folders()
        tv = next(f for f in folders if f["CollectionType"] == "tvshows")
        series = (await api.items(
            parentId=tv["ItemId"], recursive="true", includeItemTypes="Series", fields="ProviderIds,Path",
        ))[0]
        seasons = await api.seasons(series["Id"])
    assert set(series["ProviderIds"]) & {"Tmdb", "Tvdb", "Imdb"}, series["ProviderIds"]  # casing
    print("V2 season ProviderIds:", [s.get("ProviderIds") for s in seasons])  # observation


async def test_v3_set_image_accepts_raw_bytes(jellyfin_live, jpeg_bytes):
    """V3 (spec §11): capture -> "/Items/{itemId}/Images/{imageType}" -> post.

    Writes to one deterministic movie (``_lowest_named_movie``); its original
    Primary image is captured before the write and restored (or deleted, if
    there was none) in a ``finally``, whatever the assertion below decides.
    """
    url, key = jellyfin_live
    # A generous timeout: the finally-block restore writes back a real,
    # possibly large poster, and this instance's concurrent library scan was
    # observed (while developing this test) to slow an image-write response
    # past httpx's 5s default, raising ReadTimeout mid-restore.
    async with httpx.AsyncClient(timeout=30) as http:
        api = JellyfinApi(http, url, key, "test")
        item = await _lowest_named_movie(api)
        print("V3 movie:", item["Name"], item["Id"])
        original = await api.image(item["Id"], "Primary")
        try:
            await api.set_image(item["Id"], "Primary", jpeg_bytes, "image/jpeg")
            back = await api.image(item["Id"], "Primary")
            assert back is not None and len(back) > 0
        finally:
            if original is None:
                await api.delete_image(item["Id"], "Primary")
            else:
                await api.set_image(item["Id"], "Primary", original, _content_type(original))


async def test_v4_a_metadata_refresh_without_replace_leaves_our_image(jellyfin_live, jpeg_bytes):
    """V4 (spec §11): the refresh call is cited from jellyfin-openapi-12.json
    -> paths -> "/Items/{itemId}/Refresh" -> post (docs/reference/2026-09-jellyfin-openapi-12.md
    gained a matching section in this task).

    Writes to one deterministic movie (``_lowest_named_movie``); its original
    Primary image is captured before the write and restored (or deleted, if
    there was none) in a ``finally``.
    """
    url, key = jellyfin_live
    async with httpx.AsyncClient(timeout=30) as http:  # see V3's timeout note above
        api = JellyfinApi(http, url, key, "test")
        item = await _lowest_named_movie(api)
        print("V4 movie:", item["Name"], item["Id"])
        original = await api.image(item["Id"], "Primary")
        try:
            await api.set_image(item["Id"], "Primary", jpeg_bytes, "image/jpeg")
            r = await http.post(
                f"{url}/Items/{item['Id']}/Refresh",
                params={"metadataRefreshMode": "Default", "imageRefreshMode": "Default", "replaceAllImages": "false"},
                headers=api.headers(),
            )
            r.raise_for_status()
            back = await api.image(item["Id"], "Primary")
            assert back == jpeg_bytes or (back is not None and len(back) == len(jpeg_bytes))
        finally:
            if original is None:
                await api.delete_image(item["Id"], "Primary")
            else:
                await api.set_image(item["Id"], "Primary", original, _content_type(original))


async def test_v5_paths_are_absolute_and_inside_a_location(jellyfin_live):
    """V5 (spec §11): capture -> "/Library/VirtualFolders" -> get, "/Items" -> get."""
    url, key = jellyfin_live
    count = 0
    async with httpx.AsyncClient() as http:
        api = JellyfinApi(http, url, key, "test")
        for f in await api.virtual_folders():
            for dto in await api.items(
                parentId=f["ItemId"], recursive="true", includeItemTypes="Movie,Series", fields="Path",
            ):
                count += 1
                assert any(dto["Path"].startswith(loc) for loc in f["Locations"]), (dto["Path"], f["Locations"])
    print("V5 item count:", count)


async def test_v6_search_with_years_returns_top_level_items(jellyfin_live):
    """V6 (spec §11): capture -> "/Items" -> get (searchTerm, years)."""
    url, key = jellyfin_live
    async with httpx.AsyncClient() as http:
        api = JellyfinApi(http, url, key, "test")
        hits = await api.items(
            searchTerm="a", years="2020", recursive="true", includeItemTypes="Movie", fields="ProviderIds",
        )
    assert all(h["Type"] == "Movie" for h in hits)
