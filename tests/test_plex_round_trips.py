"""Perf workstream B3: fewer Plex round trips from ``PlexClient``.

Two mechanisms. ``library.sections()`` -- one GET that every resolve made
afresh -- is cached for ``SECTIONS_TTL_SECONDS`` and re-read on a miss. And
inside one worker job, ``fetch_item`` reuses the plexapi object already
fetched for that rating key, until a write through this client evicts it.
"""
from dataclasses import asdict

import pytest

from autoposter.intake.arr import RenderIntent
from autoposter.plex import client as client_module
from autoposter.plex import writer as writer_module
from autoposter.plex.client import ItemNotFound, PlexClient, PlexPathMismatch
from autoposter.queue import job_memo
from autoposter.queue.worker import run_once
from autoposter.servers.base import ServerItemRef
from queue_support import enqueue_due
from test_plex import FakeItem, FakeSection, FakeServer

REF = ServerItemRef("plex", "1", "Movies", "movie")


class _CountingServer(FakeServer):
    """``test_plex.FakeServer``, counting section listings and item fetches."""

    def __init__(self, sections, items_by_key=None):
        super().__init__(sections, items_by_key)
        self.section_reads = 0
        self.fetches: list[int] = []

    def sections(self):
        self.section_reads += 1
        return list(self._sections)

    def fetchItem(self, ekey):
        self.fetches.append(ekey)
        return super().fetchItem(ekey)


# --- the sections cache ------------------------------------------------------


async def test_sections_are_listed_once_per_ttl(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(client_module, "_monotonic", lambda: clock[0])
    server = _CountingServer([FakeSection("Movies", "/mnt/Media/Movies", [])])
    client = PlexClient(server=server, excluded_libraries=[])

    await client.library_names()
    await client.library_names()
    assert server.section_reads == 1

    clock[0] += client_module.SECTIONS_TTL_SECONDS - 1
    await client.library_names()
    assert server.section_reads == 1

    clock[0] += 2
    await client.library_names()
    assert server.section_reads == 2


async def test_two_resolves_inside_the_ttl_list_sections_once():
    movie = FakeItem(
        "12345", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv", ["tmdb://693134"],
    )
    server = _CountingServer([FakeSection("Movies", "/mnt/Media/Movies", [movie])])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)

    await client.resolve(intent)
    await client.resolve(intent)

    assert server.section_reads == 1


async def test_a_library_added_inside_the_ttl_is_found_by_the_miss_re_read():
    old = FakeSection("Movies", "/mnt/Media/Movies", [])
    movie = FakeItem(
        "777", "New", 2026, "/mnt/Media/New Movies/New (2026)/new.mkv", ["tmdb://4242"],
    )
    new = FakeSection("New Movies", "/mnt/Media/New Movies", [movie])
    server = _CountingServer([old])
    client = PlexClient(server=server, excluded_libraries=[])
    assert await client.library_names() == {"Movies"}

    server._sections = [old, new]
    resolved = await client.resolve(RenderIntent(kind="movie", title="New", tmdb_id=4242))

    assert resolved.library == "New Movies"
    assert server.section_reads == 2


async def test_a_genuine_miss_re_reads_once_and_walks_the_guids_once():
    movies = FakeSection("Movies", "/mnt/Media/Movies", [])
    server = _CountingServer([movies])
    client = PlexClient(server=server, excluded_libraries=[])

    with pytest.raises(ItemNotFound):
        await client.resolve(RenderIntent(kind="movie", title="Nope", tmdb_id=999))

    assert movies.getguid_calls == ["tmdb://999"]
    assert server.section_reads == 2


class _HookedSection(FakeSection):
    """A section whose first ``getGuid`` runs ``hook`` before answering: another
    worker thread refreshing the shared section cache in the middle of this
    thread's walk."""

    def __init__(self, *args, hook=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._hook = hook

    def getGuid(self, guid):
        hook, self._hook = self._hook, None
        if hook is not None:
            hook()
        return super().getGuid(guid)


async def _client_whose_cache_moves_mid_walk():
    """The walk runs over [Movies]; while it runs, another thread stores
    [Movies, New Movies]. The re-read must be compared with the list the walk
    used, not with whatever the cache holds by then."""
    movie = FakeItem(
        "777", "New", 2026, "/mnt/Media/New Movies/New (2026)/new.mkv", ["tmdb://4242"],
    )
    new = FakeSection("New Movies", "/mnt/Media/New Movies", [movie])
    server = _CountingServer([])
    client = PlexClient(server=server, excluded_libraries=[])

    def another_thread_refreshes():
        server._sections = [old, new]
        client._library_sections(refresh=True)

    old = _HookedSection("Movies", "/mnt/Media/Movies", [], hook=another_thread_refreshes)
    server._sections = [old]
    assert await client.library_names() == {"Movies"}
    return client, RenderIntent(kind="movie", title="New", tmdb_id=4242)


async def test_a_cache_refreshed_by_another_thread_mid_walk_still_re_walks():
    client, intent = await _client_whose_cache_moves_mid_walk()

    resolved = await client.resolve(intent)

    assert resolved.library == "New Movies"


async def test_the_pruner_never_reads_a_mid_walk_refresh_as_absence():
    client, intent = await _client_whose_cache_moves_mid_walk()

    assert await client.exists_many([intent]) == [True]


async def test_a_root_folder_added_inside_the_ttl_is_found_by_the_mismatch_re_read():
    movie = FakeItem(
        "888", "Rooted", 2026, "/mnt/Media/Movies 2/Rooted (2026)/rooted.mkv",
        ["tmdb://5151"],
    )
    # The cached section object's lookups are live GETs, so it finds the item
    # Plex just scanned into the new root -- but its `locations` were read
    # before the operator added that root.
    cached = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    server = _CountingServer([cached])
    client = PlexClient(server=server, excluded_libraries=[])
    assert await client.library_names() == {"Movies"}

    fresh = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    fresh.locations.append("/mnt/Media/Movies 2")
    server._sections = [fresh]
    resolved = await client.resolve(RenderIntent(kind="movie", title="Rooted", tmdb_id=5151))

    assert resolved.native_id == "888"
    assert resolved.library == "Movies"
    assert server.section_reads == 2


async def test_a_genuine_path_mismatch_re_reads_once_and_still_raises():
    movie = FakeItem(
        "889", "Stray", 2026, "/elsewhere/Stray (2026)/stray.mkv", ["tmdb://5152"],
    )
    server = _CountingServer([FakeSection("Movies", "/mnt/Media/Movies", [movie])])
    client = PlexClient(server=server, excluded_libraries=[])

    with pytest.raises(PlexPathMismatch):
        await client.resolve(RenderIntent(kind="movie", title="Stray", tmdb_id=5152))

    assert server.section_reads == 2


# --- the per-job item memo ---------------------------------------------------


async def test_outside_a_job_every_fetch_goes_to_plex():
    server = _CountingServer([], items_by_key={1: object()})
    client = PlexClient(server=server, excluded_libraries=[])

    await client.fetch_item("1")
    await client.fetch_item("1")

    assert server.fetches == [1, 1]


async def test_inside_a_job_the_second_fetch_is_the_same_object():
    marker = object()
    server = _CountingServer([], items_by_key={1: marker})
    client = PlexClient(server=server, excluded_libraries=[])

    with job_memo.scope():
        first = await client.fetch_item("1")
        second = await client.fetch_item(1)

    assert first is second is marker
    assert server.fetches == [1]
    assert job_memo.current() is None


@pytest.mark.parametrize(
    "method, attribute, args",
    [
        ("upload_artwork", "upload_artwork", (b"x", "poster", True)),
        ("upload_logo", "upload_logo", (b"x",)),
        ("clear_logo", "clear_logo", ()),
        ("reset_artwork_to_agent_default", "reset_artwork_to_agent_default", ("poster",)),
    ],
)
async def test_every_artwork_write_evicts_the_item(monkeypatch, method, attribute, args):
    monkeypatch.setattr(client_module.plex_artwork, attribute, lambda *a, **k: None)
    server = _CountingServer([], items_by_key={1: object()})
    client = PlexClient(server=server, excluded_libraries=[])

    with job_memo.scope():
        await client.fetch_item("1")
        await getattr(client, method)(REF, *args)
        await client.fetch_item("1")

    assert server.fetches == [1, 1], "the write reused the memo, then evicted it"


@pytest.mark.parametrize("edits, fetches", [({"title.value": "X"}, [1, 1]), ({}, [1])])
async def test_apply_facts_evicts_only_when_it_wrote(monkeypatch, edits, fetches):
    async def fake_apply_facts(item, facts, operations=None, parental_categories=None,
                               overrides=None):
        return edits

    monkeypatch.setattr(writer_module, "apply_facts", fake_apply_facts)
    server = _CountingServer([], items_by_key={1: object()})
    client = PlexClient(server=server, excluded_libraries=[])

    with job_memo.scope():
        await client.apply_facts(REF, facts=None)
        await client.fetch_item("1")

    assert server.fetches == fetches


async def test_a_failed_write_evicts_too(monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("upload refused mid-write")

    monkeypatch.setattr(client_module.plex_artwork, "upload_artwork", refuse)
    server = _CountingServer([], items_by_key={1: object()})
    client = PlexClient(server=server, excluded_libraries=[])

    with job_memo.scope():
        with pytest.raises(RuntimeError):
            await client.upload_artwork(REF, b"x", "poster", True)
        await client.fetch_item("1")

    assert server.fetches == [1, 1]


async def test_the_worker_gives_each_job_its_own_memo(session):
    server = _CountingServer([], items_by_key={1: object()})
    client = PlexClient(server=server, excluded_libraries=[])

    async def handler(session_, job):
        await client.fetch_item("1")
        await client.fetch_item("1")

    for tmdb_id in (4301, 4302):
        intent = RenderIntent(kind="movie", title=f"T{tmdb_id}", tmdb_id=tmdb_id)
        await enqueue_due(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
        assert await run_once(session, "worker-1", {"process_item": handler}) is True

    assert server.fetches == [1, 1], "one fetch per job: shared inside, empty across"
    assert job_memo.current() is None
