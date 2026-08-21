"""``sync_section``: registering Plex items Radarr/Sonarr do not know about.

The single most dangerous flag in this phase is the search flag inside
``addOptions`` -- left on, or left out, a POST tells the service to go grab
a release. ``test_radarr_add_options_search_flag_is_false`` and
``test_sonarr_add_options_search_flag_is_false`` fail if it flips or
disappears, for both kinds.
"""
import json

import httpx
import pytest

from autoposter.arr.client import RADARR, SONARR, ArrClient
from autoposter.arr.sync import ArrSyncRefused, ArrSyncSettings, sync_section

RADARR_SETTINGS = ArrSyncSettings(
    plex_root="/mnt/Media/Movies", arr_root="/mnt/media/Movies", quality_profile="HD Bluray + WEB",
)
SONARR_SETTINGS = ArrSyncSettings(
    plex_root="/mnt/Media/TV", arr_root="/mnt/media/TV", quality_profile="WEB-1080p",
)

RADARR_PROFILES = [{"id": 7, "name": "HD Bluray + WEB"}]
SONARR_PROFILES = [{"id": 7, "name": "WEB-1080p"}]

# Verified live: one root folder each.
RADARR_ROOT_FOLDERS = [{"id": 1, "path": "/mnt/media/Movies", "accessible": True}]
SONARR_ROOT_FOLDERS = [{"id": 1, "path": "/mnt/media/TV", "accessible": True}]


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, title, guids, locations):
        self.title = title
        self.ratingKey = title
        self.guids = [FakeGuid(g) for g in guids]
        self.locations = locations


def _fake_http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _handler(existing_json, profiles_json, on_post=None, root_folders=None):
    async def handler(request):
        if request.method == "GET" and request.url.path.endswith("/qualityprofile"):
            return httpx.Response(200, json=profiles_json)
        if request.method == "GET" and request.url.path.endswith("/rootfolder"):
            if root_folders is None:
                root_folders_json = (
                    RADARR_ROOT_FOLDERS if "radarr" in request.url.host else SONARR_ROOT_FOLDERS
                )
            else:
                root_folders_json = root_folders
            return httpx.Response(200, json=root_folders_json)
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
    items = [dune]
    existing = [{"tmdbId": 438631}]

    async def on_post(request):  # pragma: no cover - must never be called
        raise AssertionError("an already-known item must not be posted")

    async with _fake_http(_handler(existing, RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.checked == 1
    assert report.missing == 0
    assert report.added == 0
    assert report.titles == []


async def test_a_missing_movie_is_added_with_mapped_path_profile_and_monitor():
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    items = [dune]
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 99})

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=False)

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
    items = [show]
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 12})

    async with _fake_http(_handler([], SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=False)

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
    items = [dune]
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 99})

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=False)

    assert seen["body"]["addOptions"]["searchForMovie"] is False


async def test_sonarr_add_options_search_flag_is_false():
    """The single most dangerous flag in this phase. Must fail if it flips
    or disappears."""
    show = FakeItem("Severance", ["tvdb://371980"], ["/mnt/Media/TV/Severance"])
    items = [show]
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 12})

    async with _fake_http(_handler([], SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=False)

    assert seen["body"]["addOptions"]["searchForMissingEpisodes"] is False


async def test_an_item_with_no_external_id_is_skipped_and_counted():
    no_guid = FakeItem("The Moomins", [], ["/mnt/Media/TV/The Moomins"])
    items = [no_guid]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("an item with no external id must not be posted")

    async with _fake_http(_handler([], SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=False)

    assert report.checked == 1
    assert report.skipped_no_id == 1
    assert report.present_by_path == 0
    assert report.missing == 0
    assert report.added == 0
    assert report.titles == []


async def test_an_item_with_no_external_id_but_a_registered_path_is_already_present():
    """The real observed case: Plex's Tractor Tom has no tvdb guid yet (its
    TVDB entry is an hour old and Plex's agent has not picked it up), but
    Sonarr already has the show registered at the mapped path. That is not
    a gap -- it must not be reported as skipped_no_id, and nothing is added.
    """
    tractor_tom = FakeItem("Tractor Tom", ["imdb://tt0442647", "tmdb://16613"], ["/mnt/Media/TV/Tractor Tom"])
    items = [tractor_tom]
    existing = [
        {"tvdbId": 481561, "title": "Tractor Tom", "path": "/mnt/media/TV/Tractor Tom"},
    ]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("an item already present by path must not be posted")

    async with _fake_http(_handler(existing, SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=False)

    assert report.checked == 1
    assert report.present_by_path == 1
    assert report.skipped_no_id == 0
    assert report.missing == 0
    assert report.added == 0
    assert report.titles == []
    assert report.misassignments == []


async def test_an_item_whose_path_will_not_map_is_skipped_and_counted():
    elsewhere = FakeItem(
        "Somewhere Else", ["tmdb://999"], ["/mnt/OtherMount/Somewhere Else/movie.mkv"]
    )
    items = [elsewhere]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("an item whose path does not map must not be posted")

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.checked == 1
    assert report.missing == 1
    assert report.skipped_no_path == 1
    assert report.added == 0
    assert report.titles == []


async def test_dry_run_posts_nothing():
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    items = [dune]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("dry run must never POST")

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=True)

    assert report.added == 0
    assert report.missing == 1
    assert report.titles == ["Dune"]


async def test_one_failing_add_does_not_prevent_the_others():
    bad = FakeItem("Bad", ["tmdb://1"], ["/mnt/Media/Movies/Bad/Bad.mkv"])
    good = FakeItem("Good", ["tmdb://2"], ["/mnt/Media/Movies/Good/Good.mkv"])
    items = [bad, good]

    async def on_post(request):
        body = json.loads(request.content)
        if body["tmdbId"] == 1:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(201, json={"id": 2})

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.failed == 1
    assert report.added == 1
    assert report.titles == ["Bad", "Good"]


async def test_the_report_counts_add_up():
    known = FakeItem("Known", ["tmdb://1"], ["/mnt/Media/Movies/Known/Known.mkv"])
    no_id = FakeItem("NoId", [], ["/mnt/Media/Movies/NoId/NoId.mkv"])
    no_path = FakeItem("NoPath", ["tmdb://2"], ["/mnt/Other/NoPath/NoPath.mkv"])
    ok = FakeItem("Ok", ["tmdb://3"], ["/mnt/Media/Movies/Ok/Ok.mkv"])
    bad = FakeItem("Bad", ["tmdb://4"], ["/mnt/Media/Movies/Bad/Bad.mkv"])
    items = [known, no_id, no_path, ok, bad]
    existing = [{"tmdbId": 1}]

    async def on_post(request):
        body = json.loads(request.content)
        if body["tmdbId"] == 4:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(201, json={"id": body["tmdbId"]})

    async with _fake_http(_handler(existing, RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.checked == 5
    assert report.skipped_no_id == 1
    assert report.missing == report.skipped_no_path + len(report.titles)
    assert len(report.titles) == report.added + report.failed
    assert report.missing == 3  # NoPath, Ok, Bad -- Known is not missing
    assert report.skipped_no_path == 1
    assert report.added == 1
    assert report.failed == 1


async def test_an_item_whose_mapped_path_is_already_registered_under_another_id_is_not_added():
    """The real observed case: Plex's The Moomins (tvdb 82850) maps to a
    folder Sonarr already holds -- under tvdb 415381, as a different show
    entirely. The guard must block the add and count it separately from an
    ordinary skip.
    """
    moomins = FakeItem("The Moomins", ["tvdb://82850"], ["/mnt/Media/TV/Muumien maailma (2014)"])
    items = [moomins]
    existing = [
        {"tvdbId": 415381, "title": "Sam Bai Mai Thao (2014)",
         "path": "/mnt/media/TV/Muumien maailma (2014)"},
    ]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("a path collision must never be posted")

    async with _fake_http(_handler(existing, SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=False)

    assert report.added == 0
    assert report.skipped_path_taken == 1
    assert report.titles == []
    assert len(report.misassignments) == 1
    message = report.misassignments[0]
    assert "The Moomins" in message
    assert "82850" in message
    assert "Sam Bai Mai Thao (2014)" in message
    assert "415381" in message


async def test_a_path_collision_is_still_reported_under_dry_run():
    moomins = FakeItem("The Moomins", ["tvdb://82850"], ["/mnt/Media/TV/Muumien maailma (2014)"])
    items = [moomins]
    existing = [
        {"tvdbId": 415381, "title": "Sam Bai Mai Thao (2014)",
         "path": "/mnt/media/TV/Muumien maailma (2014)"},
    ]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("dry run must never POST")

    async with _fake_http(_handler(existing, SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=True)

    assert report.added == 0
    assert report.skipped_path_taken == 1
    assert len(report.misassignments) == 1


async def test_path_collision_applies_to_radarr_too():
    movie = FakeItem("Real Movie", ["tmdb://999"], ["/mnt/Media/Movies/Real Movie (2020)/movie.mkv"])
    items = [movie]
    existing = [
        {"tmdbId": 111, "title": "Wrong Movie", "path": "/mnt/media/Movies/Real Movie (2020)"},
    ]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("a path collision must never be posted")

    async with _fake_http(_handler(existing, RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.added == 0
    assert report.skipped_path_taken == 1
    assert "Wrong Movie" in report.misassignments[0]
    assert "111" in report.misassignments[0]


async def test_an_item_whose_mapped_path_is_free_is_still_added_normally():
    """The guard must not block a legitimate addition just because the
    service has other, unrelated paths registered."""
    show = FakeItem("Severance", ["tvdb://371980"], ["/mnt/Media/TV/Severance"])
    items = [show]
    existing = [
        {"tvdbId": 1, "title": "Something Else", "path": "/mnt/media/TV/Something Else"},
    ]
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 12})

    async with _fake_http(_handler(existing, SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=False)

    assert report.added == 1
    assert report.skipped_path_taken == 0
    assert report.titles == ["Severance"]


async def test_collision_check_is_exact_not_a_prefix_match():
    """A service holding '/mnt/media/TV/Show Two' must not block adding
    '/mnt/media/TV/Show' -- exact, case-sensitive path equality only."""
    show = FakeItem("Show", ["tvdb://1"], ["/mnt/Media/TV/Show"])
    items = [show]
    existing = [
        {"tvdbId": 2, "title": "Show Two", "path": "/mnt/media/TV/Show Two"},
    ]
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1})

    async with _fake_http(_handler(existing, SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=False)

    assert report.skipped_path_taken == 0
    assert report.added == 1
    assert seen["body"]["path"] == "/mnt/media/TV/Show"


async def test_trailing_slash_on_the_registered_path_does_not_cause_a_false_negative():
    show = FakeItem("Severance", ["tvdb://371980"], ["/mnt/Media/TV/Severance"])
    items = [show]
    existing = [
        {"tvdbId": 1, "title": "Other Show", "path": "/mnt/media/TV/Severance/"},
    ]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("a path collision must never be posted")

    async with _fake_http(_handler(existing, SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, items, SONARR, SONARR_SETTINGS, dry_run=False)

    assert report.skipped_path_taken == 1
    assert report.added == 0


async def test_a_service_reporting_nothing_at_all_is_refused_rather_than_acted_on():
    """A 200-OK empty listing -- a wrong base_url, a fresh or restored
    instance, a proxy returning an empty body -- makes every Plex item look
    missing *and* leaves the collision guard nothing to compare against.
    Under ``add_existing`` that is a library-sized mass write, so the pass
    refuses instead.
    """
    items = [
        FakeItem(f"Movie {n}", [f"tmdb://{n}"], [f"/mnt/Media/Movies/Movie {n}/movie.mkv"])
        for n in range(1, 21)
    ]

    async def on_post(request):  # pragma: no cover
        raise AssertionError("an empty listing must never trigger a POST")

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(ArrSyncRefused) as raised:
            await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=False)

    message = str(raised.value)
    assert "20 item(s)" in message
    assert "radarr" in message


async def test_a_service_reporting_nothing_at_all_is_refused_under_dry_run_too():
    items = [
        FakeItem(f"Movie {n}", [f"tmdb://{n}"], [f"/mnt/Media/Movies/Movie {n}/movie.mkv"])
        for n in range(1, 21)
    ]

    async with _fake_http(_handler([], RADARR_PROFILES)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(ArrSyncRefused):
            await sync_section(client, items, RADARR, RADARR_SETTINGS, dry_run=True)


async def test_an_empty_listing_for_a_trivially_small_section_still_proceeds():
    """A genuinely tiny library must not be locked out by the guard."""
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])

    async with _fake_http(_handler([], RADARR_PROFILES)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, [dune], RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.added == 1


async def test_a_service_managing_a_different_tree_is_refused_before_anything_is_listed():
    """The wrong-instance case, caught directly: a Radarr whose root folders
    are somewhere else entirely is not the instance this sync is configured
    for. Nothing is listed and nothing is added.
    """
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    seen = []

    async def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/rootfolder"):
            return httpx.Response(200, json=[{"id": 1, "path": "/data/films"}])
        raise AssertionError(f"nothing else may be requested: {request.method} {request.url}")

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(ArrSyncRefused) as raised:
            await sync_section(client, [dune], RADARR, RADARR_SETTINGS, dry_run=False)

    assert "/data/films" in str(raised.value)
    assert seen == [("GET", "/api/v3/rootfolder")]


async def test_a_service_reporting_no_root_folders_at_all_is_refused():
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])

    async with _fake_http(_handler([], RADARR_PROFILES, root_folders=[])) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(ArrSyncRefused):
            await sync_section(client, [dune], RADARR, RADARR_SETTINGS, dry_run=False)


async def test_the_shipped_mount_wide_mapping_is_accepted_against_a_per_library_root_folder():
    """The shipped config maps the whole mount (``arr_path: /mnt/media``)
    while Radarr reports the per-library root ``/mnt/media/Movies``. Same
    tree -- the pass must proceed, not refuse.
    """
    settings = ArrSyncSettings(
        plex_root="/mnt/Media", arr_root="/mnt/media", quality_profile="HD Bluray + WEB",
    )
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    seen = {}

    async def on_post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 99})

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, [dune], RADARR, settings, dry_run=False)

    assert report.added == 1
    assert seen["body"]["path"] == "/mnt/media/Movies/Dune (2021)"


async def test_a_movie_loose_in_the_library_root_is_never_registered_as_the_root_itself():
    """A movie file with no folder of its own resolves to the root. Radarr
    would then believe that one movie owns the entire tree, and a later
    delete-with-files would target it.
    """
    loose = FakeItem("Loose Movie", ["tmdb://1"], ["/mnt/Media/Movies/Loose Movie.mkv"])

    async def on_post(request):  # pragma: no cover
        raise AssertionError("the root itself must never be registered as an item")

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, [loose], RADARR, RADARR_SETTINGS, dry_run=False)

    assert report.skipped_root_path == 1
    assert report.added == 0
    assert report.titles == []


async def test_a_mapped_path_equal_to_a_service_root_folder_is_never_registered():
    """Same hazard under the shipped mount-wide mapping: the mapped path is
    not ``arr_root`` there, it is Radarr's own root folder.
    """
    settings = ArrSyncSettings(
        plex_root="/mnt/Media", arr_root="/mnt/media", quality_profile="HD Bluray + WEB",
    )
    loose = FakeItem("Loose Movie", ["tmdb://1"], ["/mnt/Media/Movies/Loose Movie.mkv"])

    async def on_post(request):  # pragma: no cover
        raise AssertionError("a service root folder must never be registered as an item")

    async with _fake_http(_handler([], RADARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        report = await sync_section(client, [loose], RADARR, settings, dry_run=False)

    assert report.skipped_root_path == 1
    assert report.added == 0


async def test_a_malformed_external_id_does_not_end_the_pass():
    """``tvdb://12345/1/2`` parses to a non-numeric id. That is one item's
    failure, not the end of the section.
    """
    bad = FakeItem("Bad Guid", ["tvdb://12345/1/2"], ["/mnt/Media/TV/Bad Guid"])
    good = FakeItem("Severance", ["tvdb://371980"], ["/mnt/Media/TV/Severance"])
    posted = []

    async def on_post(request):
        posted.append(json.loads(request.content)["title"])
        return httpx.Response(201, json={"id": 1})

    async with _fake_http(_handler([], SONARR_PROFILES, on_post)) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        report = await sync_section(client, [bad, good], SONARR, SONARR_SETTINGS, dry_run=False)

    assert posted == ["Severance"]
    assert report.failed == 1
    assert report.added == 1
    assert report.checked == 2


async def test_the_service_listing_is_fetched_exactly_once_per_pass():
    """Ids and paths are two views of one listing, not two GETs that could
    disagree with each other.
    """
    dune = FakeItem("Dune", ["tmdb://438631"], ["/mnt/Media/Movies/Dune (2021)/Dune.mkv"])
    listings = []

    async def handler(request):
        if request.url.path.endswith("/rootfolder"):
            return httpx.Response(200, json=RADARR_ROOT_FOLDERS)
        if request.url.path.endswith("/qualityprofile"):
            return httpx.Response(200, json=RADARR_PROFILES)
        if request.method == "GET" and request.url.path.endswith("/movie"):
            listings.append(request.url.path)
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 1})

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await sync_section(client, [dune], RADARR, RADARR_SETTINGS, dry_run=False)

    assert listings == ["/api/v3/movie"]
