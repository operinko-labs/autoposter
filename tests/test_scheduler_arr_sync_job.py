"""``make_arr_sync_job``: the scheduled wrapper around ``arr.sync``.

Two independent things run per Plex library: the Radarr/Sonarr registration
(only for a service configured ``enabled``) and the safety-net enqueue
(unconditional, whenever ``arr_sync.enabled``). Both services being
unconfigured must be a clean no-op for the registration half while the
safety net still runs -- and a quality profile that cannot be resolved for
one service must not take down the other service's sync or the safety net.
"""
import json
import threading

import httpx
from sqlalchemy import select

from autoposter.config.schema import ArrSyncConfig, PlexConfig, RadarrConfig, SonarrConfig
from autoposter.db.models import Job as QueuedJob
from autoposter.scheduler.jobs import make_arr_sync_job

RADARR_SETTINGS = RadarrConfig(
    enabled=True,
    base_url="https://radarr.example",
    add_existing=True,
    quality_profile="HD Bluray + WEB",
)
SONARR_SETTINGS = SonarrConfig(
    enabled=True,
    base_url="https://sonarr.example",
    add_existing=True,
    quality_profile="WEB-1080p",
)


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, rating_key, title, guids, locations):
        self.ratingKey = rating_key
        self.title = title
        self.guids = [FakeGuid(g) for g in guids]
        self.locations = locations
        self.year = 2021


class FakeSection:
    def __init__(self, title, section_type, items):
        self.title = title
        self.type = section_type
        self._items = items

    def all(self):
        return self._items


class FakeLibrary:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


class FakeServer:
    def __init__(self, sections):
        self.library = FakeLibrary(sections)


class Unreachable(httpx.AsyncBaseTransport):
    """A transport that fails any test that makes it -- for asserting an
    unconfigured service is never contacted at all."""

    async def handle_async_request(self, request):  # pragma: no cover - only on failure
        raise AssertionError(f"unexpected request to a service that must not be called: {request.url}")


def _service_handler(profiles_json, existing_json=None):
    async def handler(request):
        if request.method == "GET" and request.url.path.endswith("/qualityprofile"):
            return httpx.Response(200, json=profiles_json)
        if request.method == "GET":
            return httpx.Response(200, json=existing_json or [])
        if request.method == "POST":
            return httpx.Response(201, json=json.loads(request.content))
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    return handler


def _config(
    radarr=None, sonarr=None, arr_sync=None, excluded_libraries=()
):
    class _Config:
        def __init__(self):
            self.radarr = radarr if radarr is not None else RadarrConfig()
            self.sonarr = sonarr if sonarr is not None else SonarrConfig()
            self.arr_sync = arr_sync if arr_sync is not None else ArrSyncConfig()
            self.plex = PlexConfig(url="https://plex.example", excluded_libraries=list(
                excluded_libraries
            ))

    return _Config()


async def _pending_jobs(session):
    return (
        (await session.execute(select(QueuedJob).where(QueuedJob.state == "pending")))
        .scalars()
        .all()
    )


class MultiplexTransport(httpx.AsyncBaseTransport):
    """Routes to a per-host handler, so one AsyncClient can stand in for
    both the Radarr and the Sonarr endpoint in the same test."""

    def __init__(self, by_host):
        self._by_host = by_host

    async def handle_async_request(self, request):
        handler = self._by_host[request.url.host]
        return await handler(request)


async def test_the_job_is_skipped_entirely_when_arr_sync_is_disabled(session):
    connected = []

    def server_factory():
        connected.append(True)
        return FakeServer([])

    config = _config(arr_sync=ArrSyncConfig(enabled=False))
    async with httpx.AsyncClient(transport=Unreachable()) as http:
        job = make_arr_sync_job(config, server_factory, http, _secrets())
        await job.run(session)

    assert not connected, "a disabled arr_sync pass must never connect to Plex"


async def test_both_services_unconfigured_is_a_clean_noop_but_the_safety_net_still_runs(session):
    """Neither radarr nor sonarr is enabled -- no request may reach either
    service -- but the safety net still enqueues unknown items."""
    movie = FakeItem("1", "Dune", ["tmdb://1"], ["/mnt/Media/Movies/Dune/Dune.mkv"])
    show = FakeItem("2", "Severance", ["tvdb://2"], ["/mnt/Media/TV/Severance"])
    server = FakeServer([
        FakeSection("Movies", "movie", [movie]),
        FakeSection("TV Shows", "show", [show]),
    ])
    config = _config(radarr=RadarrConfig(enabled=False), sonarr=SonarrConfig(enabled=False))

    async with httpx.AsyncClient(transport=Unreachable()) as http:
        job = make_arr_sync_job(config, lambda: server, http, _secrets())
        summary = await job.run(session)

    assert "Movies: enqueued 1 unknown item(s)" in summary
    assert "TV Shows: enqueued 1 unknown item(s)" in summary
    assert len(await _pending_jobs(session)) == 2


async def test_a_missing_quality_profile_for_one_service_does_not_prevent_the_other_or_the_safety_net(
    session,
):
    """``sync_section`` raises ValueError when the profile is unresolved --
    that must be caught and reported, not left to abort the whole job."""
    movie = FakeItem("10", "Dune", ["tmdb://10"], ["/mnt/Media/Movies/Dune/Dune.mkv"])
    show = FakeItem("11", "Severance", ["tvdb://11"], ["/mnt/Media/TV/Severance"])
    server = FakeServer([
        FakeSection("Movies", "movie", [movie]),
        FakeSection("TV Shows", "show", [show]),
    ])
    config = _config(radarr=RADARR_SETTINGS, sonarr=SONARR_SETTINGS)

    transport = MultiplexTransport({
        "radarr.example": _service_handler(profiles_json=[]),  # no matching profile
        "sonarr.example": _service_handler(
            profiles_json=[{"id": 7, "name": "WEB-1080p"}], existing_json=[]
        ),
    })
    async with httpx.AsyncClient(transport=transport) as http:
        job = make_arr_sync_job(config, lambda: server, http, _secrets())
        summary = await job.run(session)

    assert "radarr: failed" in summary
    assert "sonarr: checked 1, missing 1, added 1, failed 0" in summary
    # The safety net still ran for both libraries regardless of the failure.
    assert "Movies: enqueued 1 unknown item(s)" in summary
    assert "TV Shows: enqueued 1 unknown item(s)" in summary
    assert len(await _pending_jobs(session)) == 2


async def test_an_excluded_library_is_skipped_entirely(session):
    movie = FakeItem("20", "Dune", ["tmdb://20"], ["/mnt/Media/Movies/Dune/Dune.mkv"])
    server = FakeServer([FakeSection("Movies", "movie", [movie])])
    config = _config(
        radarr=RadarrConfig(enabled=False), sonarr=SonarrConfig(enabled=False),
        excluded_libraries=["Movies"],
    )

    async with httpx.AsyncClient(transport=Unreachable()) as http:
        job = make_arr_sync_job(config, lambda: server, http, _secrets())
        summary = await job.run(session)

    assert summary == "no libraries to sync"
    assert await _pending_jobs(session) == []


async def test_the_plex_connection_runs_off_the_event_loop(session):
    main_thread = threading.current_thread()
    connect_thread = {}

    def server_factory():
        connect_thread["thread"] = threading.current_thread()
        return FakeServer([])

    config = _config(radarr=RadarrConfig(enabled=False), sonarr=SonarrConfig(enabled=False))
    async with httpx.AsyncClient(transport=Unreachable()) as http:
        job = make_arr_sync_job(config, server_factory, http, _secrets())
        await job.run(session)

    assert connect_thread["thread"] is not None
    assert connect_thread["thread"] is not main_thread, (
        "server_factory must run via asyncio.to_thread, not directly on the "
        "event loop thread"
    )


def _secrets():
    from autoposter.config.schema import Secrets

    return Secrets(
        database_url="postgresql://x", plex_token="t", tmdb_token="t", tvdb_apikey="t",
        fanart_apikey="t", webhook_secret="t", radarr_apikey="radarr-key",
        sonarr_apikey="sonarr-key",
    )
