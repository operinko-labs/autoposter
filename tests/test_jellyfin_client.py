"""JellyfinApi against the captured spec (docs/reference/2026-09-jellyfin-openapi-12.md).
Every path and parameter here is quoted from that document; nothing is recalled."""
import base64

import httpx
import pytest

from autoposter.intake.arr import RenderIntent
from autoposter.jellyfin.client import IMAGE_SLOT, JELLYFIN_CAPABILITIES, JellyfinApi, JellyfinClient
from autoposter.servers.base import CAP_LOCK_ARTWORK, ServerItemRef

BASE = "https://jf.example"


def _api(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JellyfinApi(http, BASE, api_key="k3y", version="v9.9.9"), http


async def test_every_request_carries_the_mediabrowser_header_and_no_query_key():
    seen = []
    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"Version": "12.0.0"})
    api, http = _api(handler)
    async with http:
        await api.system_info()
    auth = seen[0].headers["Authorization"]
    assert auth.startswith('MediaBrowser Token="k3y"') and 'Client="autoposter"' in auth and 'Version="v9.9.9"' in auth
    assert "k3y" not in str(seen[0].url)


async def test_virtual_folders_is_the_libraries_call():
    # capture → paths → "/Library/VirtualFolders" → get
    async def handler(request):
        assert request.url.path == "/Library/VirtualFolders"
        return httpx.Response(200, json=[{"Name": "Movies", "CollectionType": "movies", "Locations": ["/media/Movies"], "ItemId": "lib1"}])
    api, http = _api(handler)
    async with http:
        folders = await api.virtual_folders()
    assert folders[0]["Locations"] == ["/media/Movies"]


async def test_items_asks_recursively_with_provider_ids_and_paths():
    # capture → paths → "/Items" → get: parentId, recursive, includeItemTypes, fields
    async def handler(request):
        q = dict(request.url.params)
        assert q == {"parentId": "lib1", "recursive": "true", "includeItemTypes": "Movie", "fields": "ProviderIds,Path"}
        return httpx.Response(200, json={"Items": [{"Id": "m1", "Name": "Title", "Type": "Movie", "ProviderIds": {"Tmdb": "1"}, "Path": "/media/Movies/Title/t.mkv"}], "TotalRecordCount": 1})
    api, http = _api(handler)
    async with http:
        items = await api.items(parentId="lib1", recursive="true", includeItemTypes="Movie", fields="ProviderIds,Path")
    assert items[0]["Id"] == "m1"


async def test_items_returns_empty_list_when_items_key_is_absent():
    # capture → paths → "/Items" → get: BaseItemDtoQueryResult.Items is optional
    async def handler(request):
        return httpx.Response(200, json={"TotalRecordCount": 0})
    api, http = _api(handler)
    async with http:
        items = await api.items(parentId="lib1")
    assert items == []


async def test_set_image_posts_a_base64_body_with_the_image_content_type():
    # capture → paths → "/Items/{itemId}/Images/{imageType}" → post, body image/*
    # (documented). V3 (design doc §11), resolved live 2026-09-13 against
    # Jellyfin 12.0.0: raw bytes get a 500; the body must be base64-encoded,
    # Content-Type stays the real image mime.
    seen = {}
    async def handler(request):
        seen["path"], seen["ct"], seen["body"] = request.url.path, request.headers["Content-Type"], request.content
        return httpx.Response(204)
    api, http = _api(handler)
    async with http:
        await api.set_image("m1", "Primary", b"\x89PNG", "image/png")
    assert seen == {"path": "/Items/m1/Images/Primary", "ct": "image/png", "body": base64.b64encode(b"\x89PNG")}


async def test_update_item_posts_the_whole_dto():
    # capture → paths → "/Items/{itemId}" → post (UpdateItem replaces)
    seen = {}
    async def handler(request):
        seen["path"], seen["json"] = request.url.path, request.read()
        return httpx.Response(204)
    api, http = _api(handler)
    async with http:
        await api.update_item("m1", {"Id": "m1", "Name": "New", "LockedFields": ["Name"]})
    assert seen["path"] == "/Items/m1" and b'"LockedFields":["Name"]' in seen["json"]


def test_capabilities_and_slots():
    assert CAP_LOCK_ARTWORK not in JELLYFIN_CAPABILITIES
    assert IMAGE_SLOT == {"poster": "Primary", "season_poster": "Primary", "title_card": "Primary", "background": "Backdrop"}


async def test_item_fetches_a_single_item_by_id():
    # capture → paths → "/Items/{itemId}" → get
    async def handler(request):
        assert request.url.path == "/Items/m1"
        return httpx.Response(200, json={"Id": "m1", "Name": "Title"})
    api, http = _api(handler)
    async with http:
        item = await api.item("m1")
    assert item["Id"] == "m1"


async def test_seasons_asks_the_series_for_provider_ids_and_paths():
    # capture → paths → "/Shows/{seriesId}/Seasons" → get
    async def handler(request):
        assert request.url.path == "/Shows/s1/Seasons"
        assert dict(request.url.params) == {"fields": "ProviderIds,Path"}
        return httpx.Response(200, json={"Items": [{"Id": "se1"}]})
    api, http = _api(handler)
    async with http:
        seasons = await api.seasons("s1")
    assert seasons == [{"Id": "se1"}]


async def test_episodes_asks_the_series_for_provider_ids_and_paths():
    # capture → paths → "/Shows/{seriesId}/Episodes" → get
    async def handler(request):
        assert request.url.path == "/Shows/s1/Episodes"
        assert dict(request.url.params) == {"fields": "ProviderIds,Path"}
        return httpx.Response(200, json={"Items": [{"Id": "ep1"}]})
    api, http = _api(handler)
    async with http:
        episodes = await api.episodes("s1")
    assert episodes == [{"Id": "ep1"}]


async def test_image_returns_bytes_on_200():
    # capture → paths → "/Items/{itemId}/Images/{imageType}" → get: no security block, a public image read
    async def handler(request):
        assert request.url.path == "/Items/m1/Images/Primary"
        return httpx.Response(200, content=b"\x89PNG")
    api, http = _api(handler)
    async with http:
        data = await api.image("m1", "Primary")
    assert data == b"\x89PNG"


async def test_image_returns_none_on_404():
    # capture → paths → "/Items/{itemId}/Images/{imageType}" → get
    async def handler(request):
        return httpx.Response(404)
    api, http = _api(handler)
    async with http:
        data = await api.image("m1", "Primary")
    assert data is None


async def test_image_raises_on_500():
    # capture → paths → "/Items/{itemId}/Images/{imageType}" → get
    async def handler(request):
        return httpx.Response(500)
    api, http = _api(handler)
    async with http:
        with pytest.raises(httpx.HTTPStatusError):
            await api.image("m1", "Primary")


async def test_delete_image_tolerates_404():
    # capture → paths → "/Items/{itemId}/Images/{imageType}" → delete
    async def handler(request):
        assert request.url.path == "/Items/m1/Images/Primary"
        return httpx.Response(404)
    api, http = _api(handler)
    async with http:
        await api.delete_image("m1", "Primary")  # must not raise


async def test_delete_image_raises_on_500():
    # capture → paths → "/Items/{itemId}/Images/{imageType}" → delete
    async def handler(request):
        return httpx.Response(500)
    api, http = _api(handler)
    async with http:
        with pytest.raises(httpx.HTTPStatusError):
            await api.delete_image("m1", "Primary")


async def test_image_infos_is_the_per_item_image_listing():
    # capture → paths → "/Items/{itemId}/Images" → get
    async def handler(request):
        assert request.url.path == "/Items/m1/Images"
        return httpx.Response(200, json=[{"ImageType": "Primary", "Width": 300}])
    api, http = _api(handler)
    async with http:
        infos = await api.image_infos("m1")
    assert infos == [{"ImageType": "Primary", "Width": 300}]


async def test_upload_artwork_routes_kinds_to_slots_and_thumb_when_asked():
    posted = []
    async def handler(request):
        posted.append(request.url.path)
        return httpx.Response(204)
    api, http = _api(handler)
    client = JellyfinClient(api, excluded_libraries=[], library_map={}, replace_thumb_with_backdrop=True)
    ref = ServerItemRef("jellyfin", "m1", "Movies", "movie")
    async with http:
        await client.upload_artwork(ref, b"jpg", "background", lock=False)
        await client.upload_artwork(ref, b"jpg", "poster", lock=False)
    assert posted == ["/Items/m1/Images/Backdrop", "/Items/m1/Images/Thumb", "/Items/m1/Images/Primary"]


async def test_lock_true_is_refused_not_ignored():
    from autoposter.servers.base import UnsupportedOnServer
    api, http = _api(lambda r: httpx.Response(204))
    client = JellyfinClient(api, [], {}, False)
    async with http:
        with pytest.raises(UnsupportedOnServer):
            await client.upload_artwork(ServerItemRef("jellyfin", "m1", "Movies", "movie"), b"", "poster", lock=True)


async def test_no_thumb_post_when_the_setting_is_off():
    posted = []
    async def handler(request):
        posted.append(request.url.path)
        return httpx.Response(204)
    api, http = _api(handler)
    client = JellyfinClient(api, excluded_libraries=[], library_map={}, replace_thumb_with_backdrop=False)
    ref = ServerItemRef("jellyfin", "m1", "Movies", "movie")
    async with http:
        await client.upload_artwork(ref, b"jpg", "background", lock=False)
    assert posted == ["/Items/m1/Images/Backdrop"]


async def test_keys_resolve_answers_only_for_the_stored_key():
    """The twin merge's survivor election (scheduler/merge.py), which
    ``exists_many`` cannot make -- see plex/client.py's own
    ``test_keys_resolve_answers_only_for_the_stored_key`` (tests/test_plex.py)
    for the shared reasoning. ``keys_resolve`` asks only whether THIS row's
    stored native id still names the item it thinks it does, and must never
    fall through to a search on a miss -- that is the very fallback it exists
    to bypass."""
    async def handler(request):
        params = dict(request.url.params)
        assert "searchTerm" not in params, "keys_resolve must never search"
        path = request.url.path
        if path == "/Library/VirtualFolders":
            # `_key_matches` also checks the item's library -- served
            # once, for the coordinate-matching "m1" case only.
            return httpx.Response(200, json=[
                {"Name": "Movies", "CollectionType": "movies", "Locations": ["/media/Movies"], "ItemId": "lib1"},
            ])
        if path == "/Items/m1":
            return httpx.Response(200, json={
                "Id": "m1", "Type": "Movie", "ProviderIds": {"Tmdb": "693134"},
                "Path": "/media/Movies/Dune Part Two (2024)/dune.mkv",
            })
        if path == "/Items/m2":
            # A real item, but a different movie -- the stale key's story.
            return httpx.Response(200, json={
                "Id": "m2", "Type": "Movie", "ProviderIds": {"Tmdb": "1"},
                "Path": "/media/Movies/Other (2020)/other.mkv",
            })
        if path == "/Items":
            # The index's own build walk (`_key_matches`'s library check
            # rebuilds it); no movie needs to be indexed for this test.
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(404)
    api, http = _api(handler)
    client = JellyfinClient(api, [], {}, False)

    live = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134, refs={"jellyfin": "m1"})
    stale = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134, refs={"jellyfin": "m2"})
    gone = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134, refs={"jellyfin": "missing"})
    async with http:
        assert await client.keys_resolve([live, stale, gone]) == [True, False, False]


async def test_keys_resolve_refuses_an_intent_with_no_stored_key():
    async def handler(request):
        raise AssertionError("no stored key means no request at all")
    api, http = _api(handler)
    client = JellyfinClient(api, [], {}, False)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
    async with http:
        assert await client.keys_resolve([intent]) == [False]
        assert await client.keys_resolve([]) == []


async def test_keys_resolve_refuses_a_key_whose_item_moved_to_an_excluded_library():
    """plex/client.py:411-419's own check, mirrored: a stored key can still
    name a real, right-typed, coordinate-matching item that Jellyfin has
    since moved into a library this deployment excludes -- accepting it
    would elect a survivor this service is not supposed to touch."""
    async def handler(request):
        path = request.url.path
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=[
                {"Name": "Movies", "CollectionType": "movies", "Locations": ["/media/Movies"], "ItemId": "lib1"},
                {"Name": "Kids", "CollectionType": "movies", "Locations": ["/media/Kids"], "ItemId": "lib2"},
            ])
        if path == "/Items/m1":
            return httpx.Response(200, json={
                "Id": "m1", "Type": "Movie", "ProviderIds": {"Tmdb": "693134"},
                "Path": "/media/Kids/Dune Part Two (2024)/dune.mkv",
            })
        if path == "/Items":
            # "Kids" is excluded, so the build walk never asks for it -- only
            # the "Movies" folder's (empty) listing is fetched.
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(404)
    api, http = _api(handler)
    client = JellyfinClient(api, excluded_libraries=["Kids"], library_map={}, replace_thumb_with_backdrop=False)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134, refs={"jellyfin": "m1"})
    async with http:
        assert await client.keys_resolve([intent]) == [False]
