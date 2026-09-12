import asyncio
import gzip
import threading
import types
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from autoposter.db.models import ImdbRating, MediaItem
from autoposter.facts import imdb as imdb_module
from autoposter.facts.imdb import (
    EPISODES_URL,
    RATINGS_URL,
    ImdbAutoRefresh,
    ImdbMissRefresh,
    _wanted_ids,
    download_tsv,
    get_episode_rating,
    get_rating,
    parse_episodes,
    parse_ratings,
    refresh,
    store_episodes,
    store_ratings,
)

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def _lines(name):
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def test_parse_ratings_skips_the_header():
    ratings = parse_ratings(_lines("title.ratings.sample.tsv"))
    assert "tconst" not in ratings
    assert ratings["tt0111161"] == pytest.approx(9.3)


def test_parse_ratings_filters_to_the_wanted_set():
    ratings = parse_ratings(_lines("title.ratings.sample.tsv"), wanted={"tt0111161"})
    assert ratings == {"tt0111161": pytest.approx(9.3)}


def test_parse_ratings_without_a_filter_keeps_everything():
    assert len(parse_ratings(_lines("title.ratings.sample.tsv"))) == 4


def test_parse_episodes_keys_by_show_season_episode():
    episodes = parse_episodes(_lines("title.episode.sample.tsv"), parents={"tt11280740"})
    assert episodes[("tt11280740", 2, 3)] == "tt9999999"
    assert episodes[("tt11280740", 2, 4)] == "tt9999998"


def test_parse_episodes_skips_null_season_and_episode():
    """IMDb writes \\N for unknown values; those rows must not crash or appear."""
    episodes = parse_episodes(_lines("title.episode.sample.tsv"), parents={"tt32857063"})
    assert episodes == {}


def test_parse_episodes_ignores_other_shows():
    episodes = parse_episodes(_lines("title.episode.sample.tsv"), parents={"tt11280740"})
    assert all(key[0] == "tt11280740" for key in episodes)


async def test_store_and_read_back_a_rating(session):
    await store_ratings(session, {"tt15239678": 8.5})
    assert await get_rating(session, "tt15239678") == pytest.approx(8.5)
    assert await get_rating(session, "tt00000000") is None


async def test_storing_twice_updates_rather_than_duplicating(session):
    await store_ratings(session, {"tt15239678": 8.5})
    await store_ratings(session, {"tt15239678": 8.6})
    rows = (await session.execute(select(ImdbRating))).scalars().all()
    assert len(rows) == 1
    assert rows[0].rating == pytest.approx(8.6)


async def test_episode_rating_joins_episode_to_rating(session):
    await store_episodes(session, {("tt11280740", 2, 3): "tt9999999"})
    await store_ratings(session, {"tt9999999": 7.0})
    assert await get_episode_rating(session, "tt11280740", 2, 3) == pytest.approx(7.0)


async def test_episode_rating_is_none_when_the_episode_is_unrated(session):
    await store_episodes(session, {("tt11280740", 2, 9): "tt7777777"})
    assert await get_episode_rating(session, "tt11280740", 2, 9) is None


async def test_episode_rating_is_none_for_an_unknown_episode(session):
    assert await get_episode_rating(session, "tt11280740", 9, 9) is None


async def test_download_tsv_streams_instead_of_materialising_the_whole_file():
    """Proves download_tsv doesn't build one big in-memory object.

    Builds a gzip stream of many thousands of lines (enough that buffering the
    decompressed text and a full list of lines would be obviously wasteful),
    feeds it through the same MockTransport code path a real download uses,
    and checks that (a) the function hands back a lazy generator rather than
    a list, and (b) iterating it still yields every line correctly.
    """
    header = "tconst\taverageRating\tnumVotes"
    row_count = 20_000
    rows = [header] + [f"tt{i:07d}\t{5 + (i % 5)}.0\t100" for i in range(row_count)]
    payload = gzip.compress("\n".join(rows).encode("utf-8"))

    def handler(request):
        return httpx.Response(200, content=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        status, _headers, result = await download_tsv(
            http, "https://example.test/title.ratings.tsv.gz"
        )
        assert status == 200
        assert isinstance(result, types.GeneratorType)
        lines = [line.rstrip("\n") for line in result]

    assert len(lines) == row_count + 1
    assert lines[0] == header
    assert lines[1] == "tt0000000\t5.0\t100"
    assert lines[-1] == f"tt{row_count - 1:07d}\t{5 + ((row_count - 1) % 5)}.0\t100"


async def test_download_tsv_returns_no_lines_on_a_304():
    """A 304 has no body: the caller must never try to read a temp file that
    was never created."""

    def handler(request):
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        status, _headers, result = await download_tsv(
            http,
            "https://example.test/title.ratings.tsv.gz",
            headers={"If-Modified-Since": "Wed, 21 Aug 2026 00:38:00 GMT"},
        )

    assert status == 304
    assert result is None


def _gzip_fixture(name: str) -> bytes:
    return gzip.compress((FIXTURES / name).read_bytes())


def _dataset_handler(request):
    url = str(request.url)
    if url == EPISODES_URL:
        return httpx.Response(200, content=_gzip_fixture("title.episode.sample.tsv"))
    if url == RATINGS_URL:
        return httpx.Response(200, content=_gzip_fixture("title.ratings.sample.tsv"))
    raise AssertionError(f"unexpected URL {url}")


async def test_refresh_stores_movie_and_show_episode_ratings_end_to_end(session):
    """Finding 1/8: refresh() end-to-end against both real dataset shapes."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(_dataset_handler)) as http:
        count = await refresh(session, http, {"tt0111161"}, {"tt11280740"})

    # tt0111161 (movie) + tt9999999 (tt11280740's S02E03, via the episode map).
    assert count == 2
    assert await get_rating(session, "tt0111161") == pytest.approx(9.3)
    assert await get_episode_rating(session, "tt11280740", 2, 3) == pytest.approx(7.0)


async def test_refresh_parses_off_the_event_loop(session, monkeypatch):
    """Finding 8: both parse_ratings and parse_episodes must run via
    asyncio.to_thread -- 9.8M rows of gzip+csv on the event loop would stall
    every worker and the health probe."""
    seen_threads = []
    real_parse_ratings = imdb_module.parse_ratings
    real_parse_episodes = imdb_module.parse_episodes

    def spy_ratings(lines, wanted=None):
        seen_threads.append(threading.current_thread())
        return real_parse_ratings(lines, wanted)

    def spy_episodes(lines, parents):
        seen_threads.append(threading.current_thread())
        return real_parse_episodes(lines, parents)

    monkeypatch.setattr(imdb_module, "parse_ratings", spy_ratings)
    monkeypatch.setattr(imdb_module, "parse_episodes", spy_episodes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_dataset_handler)) as http:
        await imdb_module.refresh(session, http, {"tt0111161"}, {"tt11280740"})

    assert len(seen_threads) == 2
    assert all(t is not threading.main_thread() for t in seen_threads)


async def test_wanted_ids_splits_movies_from_shows_via_episodes(session):
    """Finding 1: an episode's imdb_id is the SHOW's tconst, and a show only
    has its own row if a SeriesAdd webhook happened to arrive -- so the show
    set must be drawn from kind IN ('show', 'episode'), not just 'show'."""
    session.add_all([
        MediaItem(identity_key="movie:legacy:plex:m1", library="Movies", kind="movie", title="M",
                  imdb_id="tt0111161"),
        MediaItem(identity_key="show:legacy:plex:s1", library="Shows", kind="show", title="S",
                  imdb_id="tt11280740"),
        MediaItem(identity_key="episode:legacy:plex:e1", library="Shows", kind="episode", title="E",
                  imdb_id="tt22222222", season_number=1, episode_number=1),
        MediaItem(identity_key="episode:legacy:plex:e2", library="Shows", kind="episode", title="E2",
                  imdb_id=None, season_number=1, episode_number=2),
    ])
    await session.commit()

    movie_ids, show_ids = await _wanted_ids(session)

    assert movie_ids == {"tt0111161"}
    assert show_ids == {"tt11280740", "tt22222222"}


# --- ImdbAutoRefresh -------------------------------------------------------
#
# IMDb ratings change continuously and refresh() only sees ids present in
# media_items at the moment it runs, so a one-shot load goes stale (or
# misses newly-imported titles entirely) unless something re-runs it
# periodically. These tests cover the staleness check and the background
# loop built on it (see facts/imdb.py's ImdbAutoRefresh, started/stopped in
# app.py's lifespan the same way as PlexHealth).


async def test_is_stale_true_when_the_table_is_empty(session):
    assert await imdb_module._is_stale(session, interval_hours=24) is True


async def test_is_stale_false_when_data_is_within_the_interval(session):
    await store_ratings(session, {"tt0111161": 9.3})
    assert await imdb_module._is_stale(session, interval_hours=24) is False


async def test_is_stale_true_when_data_is_older_than_the_interval(session):
    await store_ratings(session, {"tt0111161": 9.3})
    # Backdate updated_at using the database's own clock (never this process's),
    # matching how the rest of the codebase treats server-side timestamps.
    await session.execute(text("UPDATE imdb_ratings SET updated_at = now() - interval '48 hours'"))
    await session.commit()
    assert await imdb_module._is_stale(session, interval_hours=24) is True


async def _seed_movie(session_factory, tconst: str = "tt0111161") -> None:
    async with session_factory() as seed:
        seed.add(MediaItem(
            identity_key="movie:legacy:plex:m1", library="Movies", kind="movie", title="M",
            imdb_id=tconst,
        ))
        await seed.commit()


async def test_auto_refresh_runs_on_startup_when_the_table_is_empty(session_factory):
    await _seed_movie(session_factory)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_dataset_handler)) as http:
        refresher = ImdbAutoRefresh(session_factory, http, interval_hours=24)
        await refresher._maybe_refresh()

    async with session_factory() as check:
        assert await get_rating(check, "tt0111161") == pytest.approx(9.3)


async def test_auto_refresh_skips_when_data_is_fresh_no_transport_call(session_factory):
    async with session_factory() as seed:
        await store_ratings(seed, {"tt0111161": 9.3})

    called = {"n": 0}

    def handler(request):
        called["n"] += 1
        raise AssertionError("transport must not be called when data is fresh")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbAutoRefresh(session_factory, http, interval_hours=24)
        await refresher._maybe_refresh()

    assert called["n"] == 0


async def test_auto_refresh_runs_when_data_is_older_than_the_interval(session_factory):
    async with session_factory() as seed:
        seed.add(MediaItem(
            identity_key="movie:legacy:plex:m1", library="Movies", kind="movie", title="M",
            imdb_id="tt0111161",
        ))
        await store_ratings(seed, {"tt0111161": 9.0})
        await seed.execute(text("UPDATE imdb_ratings SET updated_at = now() - interval '48 hours'"))
        await seed.commit()

    async with httpx.AsyncClient(transport=httpx.MockTransport(_dataset_handler)) as http:
        refresher = ImdbAutoRefresh(session_factory, http, interval_hours=24)
        await refresher._maybe_refresh()

    async with session_factory() as check:
        assert await get_rating(check, "tt0111161") == pytest.approx(9.3)


async def test_no_transaction_is_open_when_the_dataset_download_runs(session_factory):
    """The 2026-09-03 05:20:12Z boot-time transaction: 178 s, below the 300 s
    alert threshold, which is exactly why it produced a Postgres log line and
    no alert while the 20:55 prune produced an alert.

    ``_maybe_refresh`` opened a transaction at ``_is_stale`` and held it
    across two gzipped downloads (54 MB + 8.6 MB) and a 9.8M-row parse. The
    read must close before the download; and closing it in ``_maybe_refresh``
    alone is not enough, because ``_fetch_dataset`` reads the per-dataset poll
    state and reopens one immediately before ``download_tsv``.

    Seeded with a movie only, so ``show_ids`` is empty, the episodes fetch is
    skipped and the ratings download is the first and only HTTP call."""
    await _seed_movie(session_factory)

    sessions = []

    def recording_factory():
        # async_sessionmaker.__call__ returns the AsyncSession itself, which
        # is its own async context manager -- so capturing it here captures
        # the very object _maybe_refresh enters.
        made = session_factory()
        sessions.append(made)
        return made

    seen = []

    def handler(request):
        seen.append(sessions[-1].in_transaction())
        return _dataset_handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbAutoRefresh(recording_factory, http, interval_hours=24)
        await refresher._maybe_refresh()

    # Collected and asserted out here, not inside the handler: _maybe_refresh
    # deliberately swallows every Exception, so an assert in there would be
    # eaten and the test would pass on a broken build.
    assert seen == [False]
    async with session_factory() as check:
        assert await get_rating(check, "tt0111161") == pytest.approx(9.3)


async def test_auto_refresh_survives_a_failed_attempt_and_retries_next_interval(
    session_factory, monkeypatch, caplog
):
    """A network failure must be logged, not kill the loop: run() must still
    make a second attempt (here, effectively immediately, since
    interval_hours=0 keeps the test from waiting for real)."""
    await _seed_movie(session_factory)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=_gzip_fixture("title.ratings.sample.tsv"))

    stop_event = asyncio.Event()
    real_maybe_refresh = ImdbAutoRefresh._maybe_refresh

    async def stopping_maybe_refresh(self):
        await real_maybe_refresh(self)
        if calls["n"] >= 2:
            stop_event.set()

    monkeypatch.setattr(ImdbAutoRefresh, "_maybe_refresh", stopping_maybe_refresh)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbAutoRefresh(session_factory, http, interval_hours=0)
        with caplog.at_level("WARNING"):
            await asyncio.wait_for(refresher.run(stop_event), timeout=5)

    assert calls["n"] == 2
    assert any("automatic refresh failed" in r.message for r in caplog.records)
    async with session_factory() as check:
        assert await get_rating(check, "tt0111161") == pytest.approx(9.3)


async def test_auto_refresh_does_not_run_when_disabled(session_factory):
    await _seed_movie(session_factory)
    called = {"n": 0}

    def handler(request):
        called["n"] += 1
        return httpx.Response(200, content=_gzip_fixture("title.ratings.sample.tsv"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbAutoRefresh(session_factory, http, interval_hours=24, enabled=False)
        await asyncio.wait_for(refresher.run(asyncio.Event()), timeout=5)

    assert called["n"] == 0


# --- ImdbMissRefresh --------------------------------------------------------
#
# ImdbAutoRefresh above only catches a newly-imported title on its next
# scheduled pass, up to imdb_refresh_hours later. ImdbMissRefresh closes that
# gap: whenever facts/gather.py's _critic_rating finds no rating, it calls
# note_miss(), which attempts one refresh scoped to just that id -- but a
# genuinely unrated title (a same-day release, an unaired episode) is the
# normal case, so this must rate-limit itself hard: a season-pack import
# must trigger at most one download, not one per episode.


async def test_miss_refresh_triggers_when_no_attempt_is_recorded(session):
    async with httpx.AsyncClient(transport=httpx.MockTransport(_dataset_handler)) as http:
        refresher = ImdbMissRefresh(http, cooldown_minutes=60)
        await refresher.note_miss(session, "tt0111161", is_episode=False)

    assert await get_rating(session, "tt0111161") == pytest.approx(9.3)


async def test_second_miss_inside_the_cooldown_window_skips_the_network(session):
    """The season-pack case: a 10-episode import must not become 10 downloads."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=_gzip_fixture("title.ratings.sample.tsv"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbMissRefresh(http, cooldown_minutes=60)
        await refresher.note_miss(session, "tt0111161", is_episode=False)
        assert calls["n"] == 1

        await refresher.note_miss(session, "tt15239678", is_episode=False)
        assert calls["n"] == 1


async def test_miss_after_the_cooldown_window_expires_triggers_again(session):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=_gzip_fixture("title.ratings.sample.tsv"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbMissRefresh(http, cooldown_minutes=60)
        await refresher.note_miss(session, "tt0111161", is_episode=False)
        assert calls["n"] == 1

        # Backdate using the database's own clock, matching how the rest of
        # this module treats server-side timestamps (see _is_stale's tests).
        await session.execute(
            text(
                "UPDATE imdb_miss_refresh_state SET attempted_at = now() - interval '61 minutes'"
            )
        )
        await session.commit()

        await refresher.note_miss(session, "tt0111161", is_episode=False)
        assert calls["n"] == 2


async def test_movie_miss_does_not_request_the_episode_dataset(session):
    """8.6 MB, not 54 MB: a movie/show tconst needs only the ratings file."""

    def handler(request):
        url = str(request.url)
        if url == EPISODES_URL:
            raise AssertionError("a movie/show miss must not touch the episode dataset")
        assert url == RATINGS_URL
        return httpx.Response(200, content=_gzip_fixture("title.ratings.sample.tsv"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbMissRefresh(http, cooldown_minutes=60)
        await refresher.note_miss(session, "tt0111161", is_episode=False)

    assert await get_rating(session, "tt0111161") == pytest.approx(9.3)


async def test_episode_miss_also_requests_the_episode_dataset(session):
    """Only an episode miss needs the episode map, to learn the new episode's
    own tconst before its rating can be looked up."""
    requested_urls = set()

    def handler(request):
        url = str(request.url)
        requested_urls.add(url)
        return _dataset_handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbMissRefresh(http, cooldown_minutes=60)
        await refresher.note_miss(session, "tt11280740", is_episode=True)

    assert EPISODES_URL in requested_urls
    assert RATINGS_URL in requested_urls
    assert await get_episode_rating(session, "tt11280740", 2, 3) == pytest.approx(7.0)


async def test_miss_refresh_failure_is_swallowed_and_cooldown_is_recorded(session, caplog):
    """A provider outage must not raise, and must still record the attempt --
    otherwise a failing IMDb becomes a retry storm instead of one attempt per
    cooldown window."""

    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbMissRefresh(http, cooldown_minutes=60)
        with caplog.at_level("WARNING"):
            await refresher.note_miss(session, "tt0111161", is_episode=False)  # must not raise

    assert any("miss-triggered refresh failed" in r.message for r in caplog.records)
    assert await get_rating(session, "tt0111161") is None
    assert await imdb_module._miss_refresh_due(session, 60) is False


async def test_miss_refresh_disabled_when_cooldown_is_zero(session):
    def handler(request):
        raise AssertionError("must not touch the network when disabled")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        refresher = ImdbMissRefresh(http, cooldown_minutes=0)
        await refresher.note_miss(session, "tt0111161", is_episode=False)

    assert await get_rating(session, "tt0111161") is None


async def test_configure_miss_refresh_zero_installs_nothing(monkeypatch):
    monkeypatch.setattr(imdb_module, "_miss_refresh", None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_dataset_handler)) as http:
        imdb_module.configure_miss_refresh(http, 0)
        assert imdb_module._miss_refresh is None

        imdb_module.configure_miss_refresh(http, 60)
        assert imdb_module._miss_refresh is not None


# --- Conditional polling ----------------------------------------------------
#
# Polling every imdb_refresh_hours (now 6, not 24) must cost nothing on most
# polls: datasets.imdbws.com rebuilds once a day, so five out of six 6-hourly
# polls will hit an unchanged file. A matching If-Modified-Since gets a 304
# with no body. But refresh() discards every row not in its wanted-id set, so
# "the file is unchanged" alone must never justify skipping the parse -- a
# title imported since the last refresh would silently stay unrateable
# forever. Skipping is only safe when the wanted-id set ALSO still matches
# the last successful refresh (see refresh()'s docstring and _fetch_dataset).


class _ConditionalServer:
    """A MockTransport handler that mimics datasets.imdbws.com's conditional
    behaviour: a matching If-Modified-Since returns 304 with no body; anything
    else (missing, stale, or a different id-set forcing an unconditional
    request) returns 200 with the full fixture body and the current
    Last-Modified."""

    def __init__(self):
        self.last_modified = {
            RATINGS_URL: "Wed, 21 Aug 2026 00:38:00 GMT",
            EPISODES_URL: "Wed, 21 Aug 2026 00:39:00 GMT",
        }
        self.requests: list[httpx.Request] = []
        # (url, status_code) for every response returned, in order.
        self.responses: list[tuple[str, int]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        current = self.last_modified[url]
        if request.headers.get("if-modified-since") == current:
            self.responses.append((url, 304))
            return httpx.Response(304)
        fixture = "title.ratings.sample.tsv" if url == RATINGS_URL else "title.episode.sample.tsv"
        self.responses.append((url, 200))
        return httpx.Response(200, content=_gzip_fixture(fixture), headers={"Last-Modified": current})


async def test_first_refresh_sends_no_conditional_header_and_stores_last_modified(session):
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        await refresh(session, http, {"tt0111161"}, set())

    assert len(server.requests) == 1
    assert "if-modified-since" not in server.requests[0].headers

    state = await imdb_module._get_dataset_state(session, "ratings")
    assert state.last_modified == server.last_modified[RATINGS_URL]


async def test_second_refresh_sends_the_stored_last_modified(session):
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        await refresh(session, http, {"tt0111161"}, set())
        await refresh(session, http, {"tt0111161"}, set())

    assert len(server.requests) == 2
    assert server.requests[1].headers["if-modified-since"] == server.last_modified[RATINGS_URL]


async def test_304_with_unchanged_id_set_skips_the_parse_and_stores_nothing_new(session, caplog):
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        await refresh(session, http, {"tt0111161"}, set())
        with caplog.at_level("INFO"):
            count = await refresh(session, http, {"tt0111161"}, set())

    assert count == 0
    assert server.responses[-1] == (RATINGS_URL, 304)
    assert any(
        "ratings skipped (unchanged file, unchanged id set)" in r.message for r in caplog.records
    )
    rows = (await session.execute(select(ImdbRating))).scalars().all()
    assert {row.tconst for row in rows} == {"tt0111161"}


async def test_second_refresh_survives_an_expire_on_commit_session(engine):
    """``_fetch_dataset``'s commit (imdb.py) sits between loading the stored
    dataset state and dereferencing its last_modified/etag/wanted_hash on the
    second refresh. Every caller in this codebase happens to use
    expire_on_commit=False (db/base.py's factory; this file's own ``session``
    fixture), which is the only reason that ordering has never mattered here.
    A session built the ordinary SQLAlchemy way -- expire_on_commit=True is
    the library's own default -- must not hit MissingGreenlet on that read."""
    expiring = async_sessionmaker(engine, expire_on_commit=True)
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http, expiring() as session:
        await refresh(session, http, {"tt0111161"}, set())
        await refresh(session, http, {"tt0111161"}, set())

    assert len(server.requests) == 2
    assert server.requests[1].headers["if-modified-since"] == server.last_modified[RATINGS_URL]


async def test_304_with_a_changed_id_set_still_downloads_and_parses(session):
    """The trap: a newly imported title must not stay unrateable just because
    the dataset file itself hasn't changed since the last poll."""
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        await refresh(session, http, {"tt0111161"}, set())
        # The file is still unchanged (would 304 for the old id set), but the
        # library gained a title -- this must download and re-parse anyway.
        count = await refresh(session, http, {"tt0111161", "tt15239678"}, set())

    assert count == 2
    assert server.responses[-1] == (RATINGS_URL, 200)
    assert await get_rating(session, "tt15239678") == pytest.approx(8.5)


async def test_200_with_a_new_last_modified_replaces_the_stored_value(session):
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        await refresh(session, http, {"tt0111161"}, set())
        server.last_modified[RATINGS_URL] = "Thu, 22 Aug 2026 00:38:00 GMT"
        await refresh(session, http, {"tt0111161"}, set())

    assert server.responses[-1] == (RATINGS_URL, 200)
    state = await imdb_module._get_dataset_state(session, "ratings")
    assert state.last_modified == "Thu, 22 Aug 2026 00:38:00 GMT"


async def test_ratings_and_episodes_track_conditional_state_independently(session, caplog):
    """A skip on one dataset must never skip the other -- their wanted-id
    sets differ (movies+episode-tconsts vs shows)."""
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        await refresh(session, http, {"tt0111161"}, {"tt11280740"})
        # Only the ratings file changes on the second poll; episodes stays put.
        server.last_modified[RATINGS_URL] = "Thu, 22 Aug 2026 00:38:00 GMT"
        with caplog.at_level("INFO"):
            count = await refresh(session, http, {"tt0111161"}, {"tt11280740"})

    assert count == 2  # tt0111161 + tt9999999 (tt11280740's S02E03), unchanged
    episode_statuses = [status for url, status in server.responses if url == EPISODES_URL]
    ratings_statuses = [status for url, status in server.responses if url == RATINGS_URL]
    assert episode_statuses == [200, 304]
    assert ratings_statuses == [200, 200]
    assert any(
        "episodes skipped (unchanged file, unchanged id set)" in r.message for r in caplog.records
    )
    assert any("ratings downloaded, stored 2 rating(s)" in r.message for r in caplog.records)


def test_default_imdb_refresh_hours_is_six():
    from autoposter.config.schema import OperationsConfig

    assert OperationsConfig().imdb_refresh_hours == 6


async def test_untracked_refresh_leaves_the_poll_state_alone(session):
    """The miss-triggered refresh asks about one title, not the library.

    Writing its one-element wanted-id hash into the shared poll state would
    guarantee a mismatch on the next scheduled poll, forcing a full
    unconditional re-download of both datasets and defeating the conditional
    request entirely.
    """
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        await refresh(session, http, {"tt0111161"}, set())
        before = await imdb_module._get_dataset_state(session, "ratings")
        stored_hash = before.wanted_hash

        # A miss-triggered refresh for a single unrelated title.
        await refresh(session, http, {"tt15239678"}, set(), track_state=False)
        after = await imdb_module._get_dataset_state(session, "ratings")
        assert after.wanted_hash == stored_hash, "miss refresh must not rewrite poll state"

        # ...so the next scheduled poll still gets its cheap 304.
        count = await refresh(session, http, {"tt0111161"}, set())

    assert count == 0
    assert server.responses[-1] == (RATINGS_URL, 304)


async def test_untracked_refresh_sends_no_conditional_header(session):
    server = _ConditionalServer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        await refresh(session, http, {"tt0111161"}, set())
        await refresh(session, http, {"tt0111161"}, set(), track_state=False)

    assert "if-modified-since" not in server.requests[-1].headers
    assert server.responses[-1] == (RATINGS_URL, 200)
    assert await get_rating(session, "tt0111161") == pytest.approx(9.3)
