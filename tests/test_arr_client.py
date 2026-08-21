"""The Radarr/Sonarr API client.

Authentication is the X-Api-Key header on every request -- verified live,
both services also accept an ``apikey`` query parameter, but the header is
what this client must use, and the key must never leak into a URL or a log
line.
"""
import httpx
import pytest

from autoposter.arr.client import RADARR, SONARR, ArrClient

RADARR_MOVIES = [
    {"id": 1, "title": "Dune", "tmdbId": 438631},
    {"id": 2, "title": "No External Id", "tmdbId": 0},
    {"id": 3, "title": "Missing Field"},
]

SONARR_SERIES = [
    {"id": 1, "title": "Severance", "tvdbId": 371980},
    {"id": 2, "title": "No External Id", "tvdbId": 0},
    {"id": 3, "title": "Missing Field"},
]

RADARR_PROFILES = [
    {"id": 4, "name": "SD"},
    {"id": 7, "name": "HD Bluray + WEB"},
]

SONARR_PROFILES = [
    {"id": 4, "name": "SD"},
    {"id": 7, "name": "WEB-1080p"},
]

RADARR_ROOT_FOLDERS = [{"id": 1, "path": "/mnt/media/Movies", "accessible": True}]
SONARR_ROOT_FOLDERS = [{"id": 1, "path": "/mnt/media/TV", "accessible": True}]


def _fake_http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_api_key_is_sent_as_a_header_and_never_in_the_url():
    seen = {}

    async def handler(request):
        seen["headers"] = request.headers
        seen["url"] = str(request.url)
        return httpx.Response(200, json=RADARR_MOVIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "secret-key", RADARR)
        await client.existing_ids()

    assert seen["headers"]["X-Api-Key"] == "secret-key"
    assert "secret-key" not in seen["url"]


async def test_existing_ids_returns_string_ids_for_radarr():
    async def handler(request):
        return httpx.Response(200, json=RADARR_MOVIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        ids = await client.existing_ids()

    assert ids == {"438631"}


async def test_existing_ids_returns_string_ids_for_sonarr():
    async def handler(request):
        return httpx.Response(200, json=SONARR_SERIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        ids = await client.existing_ids()

    assert ids == {"371980"}


async def test_existing_ids_skips_entries_with_missing_or_zero_id():
    async def handler(request):
        return httpx.Response(200, json=RADARR_MOVIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        ids = await client.existing_ids()

    # Only the id-1 movie (tmdbId 438631) is present; the zero and missing
    # id-field entries must not contribute any id, e.g. not "0" or "None".
    assert ids == {"438631"}
    assert "0" not in ids
    assert "None" not in ids


async def test_existing_ids_raises_on_non_2xx_instead_of_an_empty_set():
    """An empty existing_ids means "add the whole library" under add_existing.

    A failed list request must therefore raise, never be swallowed into an
    empty set.
    """
    async def handler(request):
        return httpx.Response(500)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(httpx.HTTPStatusError):
            await client.existing_ids()


async def test_existing_paths_maps_normalised_path_to_title_and_id():
    movies = [
        {"id": 1, "title": "Sam Bai Mai Thao (2014)", "tvdbId": 415381,
         "path": "/mnt/media/TV/Muumien maailma (2014)"},
    ]

    async def handler(request):
        return httpx.Response(200, json=movies)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        paths = await client.existing_paths()

    assert paths == {
        "/mnt/media/TV/Muumien maailma (2014)": {
            "title": "Sam Bai Mai Thao (2014)", "tvdbId": 415381,
        },
    }


async def test_existing_paths_strips_trailing_slash():
    movies = [{"id": 1, "title": "Dune", "tmdbId": 438631, "path": "/mnt/media/Movies/Dune (2021)/"}]

    async def handler(request):
        return httpx.Response(200, json=movies)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        paths = await client.existing_paths()

    assert "/mnt/media/Movies/Dune (2021)" in paths
    assert "/mnt/media/Movies/Dune (2021)/" not in paths


async def test_existing_paths_skips_entries_with_no_path():
    movies = [{"id": 1, "title": "No Path", "tmdbId": 1}]

    async def handler(request):
        return httpx.Response(200, json=movies)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        paths = await client.existing_paths()

    assert paths == {}


async def test_quality_profile_id_resolves_exact_name():
    async def handler(request):
        return httpx.Response(200, json=RADARR_PROFILES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        profile_id = await client.quality_profile_id("HD Bluray + WEB")

    assert profile_id == 7


async def test_quality_profile_id_resolves_exact_name_for_sonarr():
    async def handler(request):
        return httpx.Response(200, json=SONARR_PROFILES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        profile_id = await client.quality_profile_id("WEB-1080p")

    assert profile_id == 7


async def test_quality_profile_id_returns_none_for_an_absent_profile():
    async def handler(request):
        return httpx.Response(200, json=RADARR_PROFILES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        profile_id = await client.quality_profile_id("Does Not Exist")

    assert profile_id is None


async def test_quality_profile_id_does_not_fall_back_to_another_profile():
    """A missing profile must be reported, never silently replaced."""
    async def handler(request):
        return httpx.Response(200, json=[{"id": 4, "name": "SD"}])

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        profile_id = await client.quality_profile_id("HD Bluray + WEB")

    assert profile_id is None


async def test_root_folders_returns_configured_paths():
    async def handler(request):
        return httpx.Response(200, json=RADARR_ROOT_FOLDERS)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        folders = await client.root_folders()

    assert folders == ["/mnt/media/Movies"]


async def test_root_folders_returns_configured_paths_for_sonarr():
    async def handler(request):
        return httpx.Response(200, json=SONARR_ROOT_FOLDERS)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        folders = await client.root_folders()

    assert folders == ["/mnt/media/TV"]


async def test_add_posts_to_the_movie_resource_path_and_returns_the_body():
    seen = {}
    payload = {
        "title": "Dune",
        "tmdbId": 438631,
        "qualityProfileId": 7,
        "rootFolderPath": "/mnt/media/Movies",
        "path": "/mnt/media/Movies/Dune (2021)",
        "monitored": True,
        "minimumAvailability": "announced",
        "addOptions": {"searchForMovie": False},
    }

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(201, json={**payload, "id": 99})

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        result = await client.add(payload)

    assert seen["method"] == "POST"
    assert seen["url"] == "https://radarr.example/api/v3/movie"
    assert result["id"] == 99


async def test_add_posts_to_the_series_resource_path():
    async def handler(request):
        return httpx.Response(201, json={"id": 12})

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        result = await client.add({"title": "Severance", "tvdbId": 371980})

    assert result["id"] == 12


async def test_add_raises_on_non_2xx():
    async def handler(request):
        return httpx.Response(400, json={"message": "bad request"})

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(httpx.HTTPStatusError):
            await client.add({"title": "Dune"})


async def test_trailing_slash_on_base_url_does_not_double_the_path():
    seen = {}

    async def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=RADARR_MOVIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example/", "key", RADARR)
        await client.existing_ids()

    assert seen["url"] == "https://radarr.example/api/v3/movie"
    assert "//api" not in seen["url"]
