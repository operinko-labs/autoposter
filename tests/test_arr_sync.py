"""``sync_section``: registering Plex items Radarr/Sonarr do not know about.

The single most dangerous flag in this phase is the search flag inside
``addOptions`` -- left on, or left out, a POST tells the service to go grab
a release. ``test_radarr_add_options_search_flag_is_false`` and
``test_sonarr_add_options_search_flag_is_false`` fail if it flips or
disappears, for both kinds.
"""
import json

import httpx

from autoposter.arr.client import RADARR, SONARR, ArrClient
from autoposter.arr.sync import ArrSyncSettings, sync_section

RADARR_SETTINGS = ArrSyncSettings(
    plex_root="/mnt/Media/Movies", arr_root="/mnt/media/Movies", quality_profile="HD Bluray + WEB",
)
SONARR_SETTINGS = ArrSyncSettings(
    plex_root="/mnt/Media/TV", arr_root="/mnt/media/TV", quality_profile="WEB-1080p",
)

RADARR_PROFILES = [{"id": 7, "name": "HD Bluray + WEB"}]
SONARR_PROFILES = [{"id": 7, "name": "WEB-1080p"}]


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, title, guids, locations):
        self.title = title
        self.ratingKey = title
        self.guids = [FakeGuid(g) for g in guids]
        self.locations = locations


class FakeSection:
    def __init__(self, items):
        self._items = items
        self.all_calls = 0

    def all(self):
        self.all_calls += 1
        return self._items


def _fake_http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _handler(existing_json, profiles_json, on_post=None):
    async def handler(request):
        if request.method == "GET" and request.url.path.endswith("/qualityprofile"):
            return httpx.Response(200, json=profiles_json)
        if request.method == "GET":
            return httpx.Response(200, json=existing_json)
        if request.method == "POST":
            if on_post is not None:
                return await on_post(request)
            return httpx.Response(201, json={"id": 1})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    return handler


async def test_an_item_already_known_is_not_added():
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    section = FakeSection([dune])
    existing = [{"tmdbId": 438631}]

    async def on_post(request):  # pragma: no cover - must never be called
        raise AssertionError("an already-known item must not be posted")

    async with _fake_http(_handler(existing, RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, section, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.checked == 1
    assert report.missing == 0
    assert report.added == 0
    assert report.titles == []


async def test_a_missing_movie_is_added_with_mapped_path_profile_and_monitor():
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    section = FakeSection([dune])
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 99})

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, section, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.added == 1
    assert report.missing == 1
    assert report.titles == ["Dune"]
    body = seen["body"]
    assert body["tmdbId"] == 438631
    assert body["path"] == "/mnt/media/Movies/Dune (2021)"
    assert body["rootFolderPath"] == "/mnt/media/Movies"
    assert body["qualityProfileId"] == 7
    assert body["monitored"] == RADARR_SETTINGS.monitored


async def test_a_missing_series_is_added_with_mapped_path_profile_and_monitor():
    show = FakeItem("Severance", ["tvdb://371980"], ["/mnt/Media/TV/Severance"])
    section = FakeSection([show])
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 12})

    async with _fake_http(_handler([], SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, section, SONARR, SONARR_SETTINGS, dry_run=False)

    assert report.added == 1
    body = seen["body"]
    assert body["tvdbId"] == 371980
    assert body["path"] == "/mnt/media/TV/Severance"
    assert body["rootFolderPath"] == "/mnt/media/TV"
    assert body["qualityProfileId"] == 7
    assert body["monitored"] == SONARR_SETTINGS.monitored


async def test_radarr_add_options_search_flag_is_false():
    """The single most dangerous flag in this phase. Must fail if it flips
    or disappears."""
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    section = FakeSection([dune])
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 99})

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await sync_section(client, section, RADARR, RADARR_SETTINGS, dry_run=False)

    assert seen["body"]["addOptions"]["searchForMovie"] is False


async def test_sonarr_add_options_search_flag_is_false():
    """The single most dangerous flag in this phase. Must fail if it flips
    or disappears."""
    show = FakeItem("Severance", ["tvdb://371980"], ["/mnt/Media/TV/Severance"])
    section = FakeSection([show])
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 12})

    async with _fake_http(_handler([], SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        await sync_section(client, section, SONARR, SONARR_SETTINGS, dry_run=False)

    assert seen["body"]["addOptions"]["searchForMissingEpisodes"] is False


async def test_an_item_with_no_external_id_is_skipped_and_counted():
    no_guid = FakeItem("The Moomins", [], ["/mnt/Media/TV/The Moomins"])
    section = FakeSection([no_guid])

    async def on_post(request):  # pragma: no cover
        raise AssertionError("an item with no external id must not be posted")

    async with _fake_http(_handler([], SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, section, SONARR, SONARR_SETTINGS, dry_run=False)

    assert report.checked == 1
    assert report.skipped_no_id == 1
    assert report.missing == 0
    assert report.added == 0
    assert report.titles == []


async def test_an_item_whose_path_will_not_map_is_skipped_and_counted():
    elsewhere = FakeItem(
        "Somewhere Else", ["tmdb://999"], ["/mnt/OtherMount/Somewhere Else/movie.mkv"]
    )
    section = FakeSection([elsewhere])

    async def on_post(request):  # pragma: no cover
        raise AssertionError("an item whose path does not map must not be posted")

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, section, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.checked == 1
    assert report.missing == 1
    assert report.skipped_no_path == 1
    assert report.added == 0
    assert report.titles == []


async def test_dry_run_posts_nothing():
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    section = FakeSection([dune])

    async def on_post(request):  # pragma: no cover
        raise AssertionError("dry run must never POST")

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, section, RADARR, RADARR_SETTINGS, dry_run=True)

    assert report.added == 0
    assert report.missing == 1
    assert report.titles == ["Dune"]


async def test_one_failing_add_does_not_prevent_the_others():
    bad = FakeItem("Bad", ["tmdb://1"], ["/mnt/Media/Movies/Bad/Bad.mkv"])
    good = FakeItem("Good", ["tmdb://2"], ["/mnt/Media/Movies/Good/Good.mkv"])
    section = FakeSection([bad, good])

    async def on_post(request):
        body = json.loads(request.content)
        if body["tmdbId"] == 1:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(201, json={"id": 2})

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, section, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.failed == 1
    assert report.added == 1
    assert report.titles == ["Bad", "Good"]


async def test_the_report_counts_add_up():
    known = FakeItem("Known", ["tmdb://1"], ["/mnt/Media/Movies/Known/Known.mkv"])
    no_id = FakeItem("NoId", [], ["/mnt/Media/Movies/NoId/NoId.mkv"])
    no_path = FakeItem("NoPath", ["tmdb://2"], ["/mnt/Other/NoPath/NoPath.mkv"])
    ok = FakeItem("Ok", ["tmdb://3"], ["/mnt/Media/Movies/Ok/Ok.mkv"])
    bad = FakeItem("Bad", ["tmdb://4"], ["/mnt/Media/Movies/Bad/Bad.mkv"])
    section = FakeSection([known, no_id, no_path, ok, bad])
    existing = [{"tmdbId": 1}]

    async def on_post(request):
        body = json.loads(request.content)
        if body["tmdbId"] == 4:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(201, json={"id": body["tmdbId"]})

    async with _fake_http(_handler(existing, RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, section, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.checked == 5
    assert report.skipped_no_id == 1
    assert report.missing == report.skipped_no_path + len(report.titles)
    assert len(report.titles) == report.added + report.failed
    assert report.missing == 3  # NoPath, Ok, Bad -- Known is not missing
    assert report.skipped_no_path == 1
    assert report.added == 1
    assert report.failed == 1


async def test_section_all_is_called_exactly_once():
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    section = FakeSection([dune])

    async with _fake_http(_handler([], RADARR_PROFILES)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await sync_section(client, section, RADARR, RADARR_SETTINGS, dry_run=True)

    assert section.all_calls == 1
