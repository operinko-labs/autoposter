"""The Radarr/Sonarr API client.

Authentication is the X-Api-Key header on every request -- verified live,
both services also accept an ``apikey`` query parameter, but the header is
what this client must use, and the key must never leak into a URL or a log
line.
"""
import json

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
        await client.listing()

    assert seen["headers"]["X-Api-Key"] == "secret-key"
    assert "secret-key" not in seen["url"]


async def test_ids_in_returns_string_ids_for_radarr():
    async def handler(request):
        return httpx.Response(200, json=RADARR_MOVIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        ids = client.ids_in(await client.listing())

    assert ids == {"438631"}


async def test_ids_in_returns_string_ids_for_sonarr():
    async def handler(request):
        return httpx.Response(200, json=SONARR_SERIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        ids = client.ids_in(await client.listing())

    assert ids == {"371980"}


async def test_ids_in_skips_entries_with_missing_or_zero_id():
    async def handler(request):
        return httpx.Response(200, json=RADARR_MOVIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        ids = client.ids_in(await client.listing())

    # Only the id-1 movie (tmdbId 438631) is present; the zero and missing
    # id-field entries must not contribute any id, e.g. not "0" or "None".
    assert ids == {"438631"}
    assert "0" not in ids
    assert "None" not in ids


async def test_listing_raises_on_non_2xx_instead_of_an_empty_set():
    """An empty listing means "add the whole library" under add_existing.

    A failed list request must therefore raise, never be swallowed into an
    empty set.
    """
    async def handler(request):
        return httpx.Response(500)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(httpx.HTTPStatusError):
            await client.listing()


async def test_paths_in_maps_normalised_path_to_title_and_id():
    movies = [
        {"id": 1, "title": "Sam Bai Mai Thao (2014)", "tvdbId": 415381,
         "path": "/mnt/media/TV/Muumien maailma (2014)"},
    ]

    async def handler(request):
        return httpx.Response(200, json=movies)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        paths = client.paths_in(await client.listing())

    assert paths == {
        "/mnt/media/TV/Muumien maailma (2014)": {
            "title": "Sam Bai Mai Thao (2014)", "tvdbId": 415381,
        },
    }


async def test_paths_in_strips_trailing_slash():
    movies = [{"id": 1, "title": "Dune", "tmdbId": 438631, "path": "/mnt/media/Movies/Dune (2021)/"}]

    async def handler(request):
        return httpx.Response(200, json=movies)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        paths = client.paths_in(await client.listing())

    assert "/mnt/media/Movies/Dune (2021)" in paths
    assert "/mnt/media/Movies/Dune (2021)/" not in paths


async def test_paths_in_skips_entries_with_no_path():
    movies = [{"id": 1, "title": "No Path", "tmdbId": 1}]

    async def handler(request):
        return httpx.Response(200, json=movies)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        paths = client.paths_in(await client.listing())

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
        await client.listing()

    assert seen["url"] == "https://radarr.example/api/v3/movie"
    assert "//api" not in seen["url"]


# ---------------------------------------------------------------------------
# The list-builder surface: ordered ids, and the tag vocabulary.
#
# ``ids_in`` answers "does the service hold this?", which is a set question and
# has no order. A collection built from the same listing *is* an order, so the
# builders read ``ordered_ids_in`` instead -- the same entries, the same skip
# rule, as a list. Both come from one implementation so the two views cannot
# start disagreeing about which entries count.

RADARR_TAGS = [{"id": 1, "label": "kids"}, {"id": 3, "label": "4K"}]


async def test_ordered_ids_in_preserves_the_listing_order():
    movies = [
        {"id": 1, "title": "Dune", "tmdbId": 438631},
        {"id": 2, "title": "The Godfather", "tmdbId": 238},
        {"id": 3, "title": "Paddington", "tmdbId": 116149},
    ]

    async def handler(request):
        return httpx.Response(200, json=movies)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        ids = client.ordered_ids_in(await client.listing())

    assert ids == ["438631", "238", "116149"]


async def test_ordered_ids_in_skips_the_same_entries_ids_in_does():
    """One skip rule, two views: an entry the service has not matched to
    anything has no external id, and must not appear in either."""
    async def handler(request):
        return httpx.Response(200, json=RADARR_MOVIES)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        entries = await client.listing()

        assert client.ordered_ids_in(entries) == ["438631"]
        assert set(client.ordered_ids_in(entries)) == client.ids_in(entries)


async def test_tag_ids_in_reads_the_tag_ids_off_one_entry():
    client = ArrClient(None, "https://radarr.example", "key", RADARR)

    assert client.tag_ids_in({"id": 1, "tags": [3, 1]}) == {3, 1}
    # Both shapes an untagged entry takes in the wild.
    assert client.tag_ids_in({"id": 2, "tags": []}) == set()
    assert client.tag_ids_in({"id": 3}) == set()


async def test_tags_maps_labels_to_ids_with_the_api_key_in_the_header():
    seen = {}

    async def handler(request):
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["url"] = str(request.url)
        return httpx.Response(200, json=RADARR_TAGS)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "secret-key", RADARR)
        tags = await client.tags()

    assert tags == {"kids": 1, "4K": 3}
    assert seen["path"] == "/api/v3/tag"
    assert seen["headers"]["X-Api-Key"] == "secret-key"
    assert "secret-key" not in seen["url"]


async def test_tags_is_the_same_endpoint_for_sonarr():
    """/api/v3/tag is not resource-scoped -- both services expose one flat tag
    vocabulary, so the path does not take the ``movie``/``series`` resource."""
    seen = {}

    async def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json=[{"id": 2, "label": "ongoing"}])

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        tags = await client.tags()

    assert tags == {"ongoing": 2}
    assert seen["path"] == "/api/v3/tag"


async def test_tags_skips_entries_with_no_label():
    async def handler(request):
        return httpx.Response(
            200, json=[{"id": 1, "label": "kids"}, {"id": 2}, {"label": "no id"}]
        )

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)

        assert await client.tags() == {"kids": 1}


async def test_tags_raises_on_non_2xx():
    """The same rule as ``listing``: an unreadable tag vocabulary would make
    every tag name look unknown, which is a refusal, not an empty answer."""
    async def handler(request):
        return httpx.Response(401)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(httpx.HTTPStatusError):
            await client.tags()


# --- roadmap row 89(b): the client's first WRITE ----------------------------
#
# Shapes are the services' own, banked verbatim in
# `.superpowers/sdd/p-arr-api-capture.md` -- `paths → "/api/v3/tag" → post`
# and `paths → "/api/v3/{movie,series}/editor" → put`. Nothing here is recalled
# from memory, and nothing here uses the full-body `PUT /api/v3/movie/{id}`:
# echoing a 49-property resource back is how a dropped field becomes a NULLed
# one on the operator's own Radarr.


async def test_entry_ids_by_external_id_maps_the_service_ids():
    """The Arr's INTERNAL id is what the editor endpoint takes; the external id
    is what a Plex item can be matched by. This is the join between them."""
    async with _fake_http(lambda request: httpx.Response(200, json=RADARR_MOVIES)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        mapping = client.entry_ids_by_external_id(await client.listing())

    assert mapping == {"438631": 1}


async def test_create_tag_posts_the_label_and_returns_the_new_id():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        seen["key"] = request.headers.get("X-Api-Key")
        return httpx.Response(200, json={"id": 9, "label": "autoposter"})

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "secret-key", RADARR)
        tag_id = await client.create_tag("autoposter")

    assert tag_id == 9
    assert seen["method"] == "POST"
    assert seen["url"] == "https://radarr.example/api/v3/tag"
    assert seen["body"] == {"label": "autoposter"}
    assert seen["key"] == "secret-key"


async def test_create_tag_raises_on_non_2xx():
    """An unknown tag id would be written onto items as a number meaning
    nothing, so a failed creation must never be swallowed."""
    async with _fake_http(lambda request: httpx.Response(400)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(httpx.HTTPStatusError):
            await client.create_tag("autoposter")


async def test_apply_tags_puts_the_editor_body_for_radarr():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await client.apply_tags([1, 2], [9])

    assert seen["method"] == "PUT", "DELETE takes the same body and deletes the movies"
    assert seen["url"] == "https://radarr.example/api/v3/movie/editor"
    assert seen["body"] == {"movieIds": [1, 2], "tags": [9], "applyTags": "add"}


async def test_apply_tags_puts_the_editor_body_for_sonarr():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        await client.apply_tags([4], [9])

    assert seen["method"] == "PUT"
    assert seen["url"] == "https://sonarr.example/api/v3/series/editor"
    assert seen["body"] == {"seriesIds": [4], "tags": [9], "applyTags": "add"}


async def test_apply_tags_is_additive_never_replace():
    """``applyTags`` is the enum ["add", "remove", "replace"] (banked). This
    service tags items it manages; it does not own an Arr's tag vocabulary, so
    it must never take a tag off something."""
    seen = {}

    async def handler(request):
        seen["body"] = json.loads(request.read())
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await client.apply_tags([1], [9])

    assert seen["body"]["applyTags"] == "add"


async def test_apply_tags_with_nothing_to_tag_makes_no_request():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await client.apply_tags([], [9])
        await client.apply_tags([1], [])

    assert requests == []


async def test_apply_tags_raises_on_non_2xx():
    async with _fake_http(lambda request: httpx.Response(500)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(httpx.HTTPStatusError):
            await client.apply_tags([1], [9])
