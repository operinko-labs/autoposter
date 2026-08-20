"""IMDb ratings via the bulk non-commercial datasets.

IMDb publishes these files daily and forbids scraping the site for the same
data. Use is personal and non-commercial only, and requires the attribution
carried in README.md.
"""

import asyncio
import csv
import gzip
import hashlib
import itertools
import logging
import os
import sys
import tempfile
from collections.abc import Iterable, Iterator
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.base import make_engine, make_session_factory
from autoposter.db.models import (
    ImdbDatasetState,
    ImdbEpisode,
    ImdbMissRefreshState,
    ImdbRating,
    MediaItem,
)

logger = logging.getLogger(__name__)

RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
EPISODES_URL = "https://datasets.imdbws.com/title.episode.tsv.gz"

_NULL = "\\N"

# Rows per INSERT for the bulk upsert helpers below. Large enough to cut the
# round-trip count from "one per row" to a handful, small enough that a single
# statement's parameter list stays bounded.
_UPSERT_CHUNK_SIZE = 2000


def _chunked(rows: list[dict], size: int) -> Iterator[list[dict]]:
    it = iter(rows)
    while chunk := list(itertools.islice(it, size)):
        yield chunk


def parse_ratings(lines: Iterable[str], wanted: set[str] | None = None) -> dict[str, float]:
    """``{tconst: averageRating}``, optionally filtered to ``wanted``."""
    ratings: dict[str, float] = {}
    reader = csv.reader(lines, delimiter="\t")
    next(reader, None)  # header
    for row in reader:
        if len(row) < 2:
            continue
        tconst, average = row[0], row[1]
        if wanted is not None and tconst not in wanted:
            continue
        if average == _NULL:
            continue
        try:
            ratings[tconst] = float(average)
        except ValueError:
            continue
    return ratings


def parse_episodes(lines: Iterable[str], parents: set[str]) -> dict[tuple[str, int, int], str]:
    """``{(show_tconst, season, episode): episode_tconst}`` for the given shows."""
    episodes: dict[tuple[str, int, int], str] = {}
    reader = csv.reader(lines, delimiter="\t")
    next(reader, None)
    for row in reader:
        if len(row) < 4:
            continue
        tconst, parent, season, episode = row[0], row[1], row[2], row[3]
        if parent not in parents:
            continue
        if season == _NULL or episode == _NULL:
            continue
        try:
            episodes[(parent, int(season), int(episode))] = tconst
        except ValueError:
            continue
    return episodes


async def download_tsv(
    http: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None
) -> tuple[int, httpx.Headers, Iterator[str] | None]:
    """Stream a gzipped TSV to a temporary file, then yield decoded lines.

    ``title.episode.tsv.gz`` decompresses to roughly 500 MB, and this runs in
    containers with memory limits well below that. Buffering the response
    body, the decompressed text, and a list of every line simultaneously (the
    old approach) peaks around 1-1.5 GB and gets OOM-killed. Streaming lazily
    across the async HTTP boundary while a synchronous ``csv.reader`` consumes
    the result is awkward, so instead the response is streamed straight to a
    temp file on disk (bounded by the HTTP chunk size, not the file size),
    then read back through ``gzip.open`` which decompresses and decodes
    incrementally as each line is requested. The temp file is removed once
    every line has been yielded, or immediately if the download fails; disk
    is cheap and nothing here is cached across calls regardless.

    ``headers`` carries conditional-request headers (``If-Modified-Since``,
    ``If-None-Match``) when the caller wants to skip the transfer entirely if
    the file hasn't changed. Returns ``(status_code, response_headers,
    lines)``; on a 304 there is no body, so ``lines`` is ``None`` and no temp
    file is ever created.
    """
    async with http.stream(
        "GET", url, follow_redirects=True, timeout=300, headers=headers
    ) as response:
        status = response.status_code
        resp_headers = response.headers
        if status == 304:
            return status, resp_headers, None
        response.raise_for_status()

        fd, path = tempfile.mkstemp(suffix=".tsv.gz")
        os.close(fd)
        try:
            with open(path, "wb") as out:
                async for chunk in response.aiter_bytes():
                    out.write(chunk)
        except BaseException:
            os.remove(path)
            raise

    def _lines() -> Iterator[str]:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                yield from fh
        finally:
            os.remove(path)

    return status, resp_headers, _lines()


async def store_ratings(session: AsyncSession, ratings: dict[str, float]) -> None:
    stmt = insert(ImdbRating)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tconst"], set_={"rating": stmt.excluded.rating}
    )
    rows = [{"tconst": tconst, "rating": rating} for tconst, rating in ratings.items()]
    for chunk in _chunked(rows, _UPSERT_CHUNK_SIZE):
        await session.execute(stmt, chunk)
    await session.commit()


async def store_episodes(
    session: AsyncSession, episodes: dict[tuple[str, int, int], str]
) -> None:
    stmt = insert(ImdbEpisode)
    stmt = stmt.on_conflict_do_update(
        index_elements=["parent_tconst", "season_number", "episode_number"],
        set_={"tconst": stmt.excluded.tconst},
    )
    rows = [
        {
            "parent_tconst": parent,
            "season_number": season,
            "episode_number": episode,
            "tconst": tconst,
        }
        for (parent, season, episode), tconst in episodes.items()
    ]
    for chunk in _chunked(rows, _UPSERT_CHUNK_SIZE):
        await session.execute(stmt, chunk)
    await session.commit()


def _wanted_hash(ids: set[str]) -> str:
    """Stable hash of a wanted-id set, independent of query/iteration order."""
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


async def _get_dataset_state(session: AsyncSession, dataset: str) -> ImdbDatasetState | None:
    return (
        await session.execute(
            select(ImdbDatasetState).where(ImdbDatasetState.dataset == dataset)
        )
    ).scalar_one_or_none()


async def _save_dataset_state(
    session: AsyncSession,
    dataset: str,
    last_modified: str | None,
    etag: str | None,
    wanted_hash: str,
) -> None:
    stmt = insert(ImdbDatasetState).values(
        dataset=dataset, last_modified=last_modified, etag=etag, wanted_hash=wanted_hash
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["dataset"],
        set_={
            "last_modified": stmt.excluded.last_modified,
            "etag": stmt.excluded.etag,
            "wanted_hash": stmt.excluded.wanted_hash,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.commit()


async def _fetch_dataset(
    session: AsyncSession,
    http: httpx.AsyncClient,
    url: str,
    dataset: str,
    wanted_ids: set[str],
    track_state: bool = True,
) -> tuple[Iterator[str] | None, str]:
    """Conditionally download one dataset, tracking per-dataset poll state.

    Returns ``(lines, reason)``. ``lines`` is ``None`` when the download was
    skipped; ``reason`` is a human-readable explanation, used for both the
    "why did we skip" log line and (as an empty string) the "we downloaded"
    case.

    A 304 only counts as "nothing to do" when the *wanted id set* also
    matches the one used for the last successful refresh -- see ``refresh``'s
    docstring for why: the dataset file not changing does not mean this
    library's current row selection from it hasn't. When the id set changed,
    no ``If-Modified-Since`` header is sent at all, so the server always
    returns a full 200 body to re-parse.

    ``Last-Modified``/``ETag`` are stored and replayed verbatim, exactly as
    received -- never parsed or compared to local time.

    ``track_state=False`` fetches unconditionally and leaves the stored state
    untouched. That is for callers whose ``wanted_ids`` is not the library's
    id set -- the miss-triggered refresh asks about a single title, and
    writing its one-element hash here would guarantee a mismatch on the next
    scheduled poll, forcing a full 62MB re-download and defeating the
    conditional request.
    """
    wanted_hash = _wanted_hash(wanted_ids)
    state = await _get_dataset_state(session, dataset) if track_state else None

    headers: dict[str, str] = {}
    if state is not None and state.last_modified and state.wanted_hash == wanted_hash:
        headers["If-Modified-Since"] = state.last_modified
        if state.etag:
            headers["If-None-Match"] = state.etag

    status, resp_headers, lines = await download_tsv(http, url, headers=headers or None)

    if status == 304:
        return None, "unchanged file, unchanged id set"

    if track_state:
        await _save_dataset_state(
            session,
            dataset,
            resp_headers.get("Last-Modified"),
            resp_headers.get("ETag"),
            wanted_hash,
        )
    return lines, ""


async def get_rating(session: AsyncSession, imdb_id: str) -> float | None:
    return (
        await session.execute(select(ImdbRating.rating).where(ImdbRating.tconst == imdb_id))
    ).scalar_one_or_none()


async def get_episode_rating(
    session: AsyncSession, show_imdb_id: str, season: int, episode: int
) -> float | None:
    """Join the episode map to the ratings table.

    Returns ``None`` when the episode is unknown *or* known but unrated — both
    mean "no badge value", and the caller does not need to tell them apart.
    """
    tconst = (
        await session.execute(
            select(ImdbEpisode.tconst).where(
                ImdbEpisode.parent_tconst == show_imdb_id,
                ImdbEpisode.season_number == season,
                ImdbEpisode.episode_number == episode,
            )
        )
    ).scalar_one_or_none()
    if tconst is None:
        return None
    return await get_rating(session, tconst)


async def refresh(
    session: AsyncSession,
    http: httpx.AsyncClient,
    movie_ids: set[str],
    show_ids: set[str],
    track_state: bool = True,
) -> int:
    """Reload both datasets, keeping only rows this library needs.

    Returns the number of ratings stored. Episodes are resolved first so their
    ids can be added to the ratings filter in a single pass.

    Each dataset is fetched conditionally (see ``_fetch_dataset``): a poll
    that finds both the remote file and this library's wanted-id set
    unchanged since the last successful refresh downloads and parses
    nothing. Ratings and episodes are tracked independently -- a skip on one
    never skips the other, since their id sets differ (movies+episode-tconsts
    vs shows).

    The correctness trap this guards against: ``refresh()`` only ever stores
    rows for ids in ``wanted`` at call time, discarding everything else. So
    "the file is unchanged" does *not* imply "there's nothing to do" -- if a
    title was imported since the last refresh, its row was thrown away last
    time and must be re-extracted from the very same unchanged file. Hence
    the wanted-id-set hash is part of the skip condition, not just
    ``Last-Modified``.

    ``track_state=False`` bypasses both the conditional request and the state
    write; the miss-triggered refresh uses it because it asks about one title
    rather than the whole library.
    """
    episode_tconsts: set[str] = set()
    if show_ids:
        lines, skip_reason = await _fetch_dataset(
            session, http, EPISODES_URL, "episodes", show_ids, track_state
        )
        if lines is None:
            episode_tconsts = set(
                (
                    await session.execute(
                        select(ImdbEpisode.tconst).where(
                            ImdbEpisode.parent_tconst.in_(show_ids)
                        )
                    )
                ).scalars()
            )
            logger.info(
                "imdb: episodes skipped (%s); %d row(s) already stored for %d show(s)",
                skip_reason, len(episode_tconsts), len(show_ids),
            )
        else:
            # Finding 8: 9.8M rows of gzip + csv is slow enough to stall the
            # event loop (and the health probe with it) if run inline, so
            # it's offloaded to a thread same as every other blocking call in
            # this codebase.
            episodes = await asyncio.to_thread(parse_episodes, lines, show_ids)
            await store_episodes(session, episodes)
            episode_tconsts = set(episodes.values())
            logger.info(
                "imdb: episodes downloaded, kept %d row(s) for %d show(s)",
                len(episodes), len(show_ids),
            )
    else:
        logger.info("imdb: episodes skipped (no show ids to refresh)")

    wanted = set(movie_ids) | episode_tconsts
    if not wanted:
        logger.info("imdb: ratings skipped (no ids to refresh)")
        return 0

    lines, skip_reason = await _fetch_dataset(
        session, http, RATINGS_URL, "ratings", wanted, track_state
    )
    if lines is None:
        logger.info("imdb: ratings skipped (%s)", skip_reason)
        return 0

    ratings = await asyncio.to_thread(parse_ratings, lines, wanted)
    await store_ratings(session, ratings)
    logger.info("imdb: ratings downloaded, stored %d rating(s)", len(ratings))
    return len(ratings)


async def _wanted_ids(session: AsyncSession) -> tuple[set[str], set[str]]:
    """The movie/show tconst sets this library actually needs.

    An episode row's own ``imdb_id`` is IMDb's episode tconst, not its show's
    — the join to per-episode ratings goes through ``imdb_episodes`` instead
    (see ``get_episode_rating``). What ``refresh()`` needs for its
    ``show_ids`` argument is the *show*'s tconst, which both a show item and
    each of its episode items carry as ``imdb_id`` (set from the parent show
    at webhook time) — hence ``kind IN ('show', 'episode')`` rather than just
    ``'show'``: a show only has its own row if a ``SeriesAdd`` webhook
    happened to arrive, but every episode of it does.
    """
    movie_ids = set(
        (
            await session.execute(
                select(MediaItem.imdb_id)
                .where(MediaItem.kind == "movie", MediaItem.imdb_id.is_not(None))
                .distinct()
            )
        ).scalars()
    )
    show_ids = set(
        (
            await session.execute(
                select(MediaItem.imdb_id)
                .where(
                    MediaItem.kind.in_(("show", "episode")), MediaItem.imdb_id.is_not(None)
                )
                .distinct()
            )
        ).scalars()
    )
    return movie_ids, show_ids


async def _stored_episode_count(session: AsyncSession, show_ids: set[str]) -> int:
    if not show_ids:
        return 0
    return (
        await session.execute(
            select(func.count()).select_from(ImdbEpisode).where(
                ImdbEpisode.parent_tconst.in_(show_ids)
            )
        )
    ).scalar_one()


async def _is_stale(session: AsyncSession, interval_hours: float) -> bool:
    """Whether ``imdb_ratings`` is empty or its newest row predates the interval.

    Both sides of the comparison come from the database clock (``func.now()``
    and the column's server-set ``updated_at``), not the host's — the two can
    disagree, and this module never mixes them.
    """
    newest, now = (
        await session.execute(select(func.max(ImdbRating.updated_at), func.now()))
    ).one()
    if newest is None:
        return True
    return now - newest >= timedelta(hours=interval_hours)


_MISS_REFRESH_ROW_ID = 1


async def _miss_refresh_due(session: AsyncSession, cooldown_minutes: int) -> bool:
    """Whether the cooldown window has elapsed (or no attempt is recorded yet).

    Both sides of the comparison come from the database clock, same reasoning
    as ``_is_stale`` above.
    """
    row = (
        await session.execute(
            select(ImdbMissRefreshState.attempted_at, func.now()).where(
                ImdbMissRefreshState.id == _MISS_REFRESH_ROW_ID
            )
        )
    ).one_or_none()
    if row is None:
        return True
    attempted_at, now = row
    return now - attempted_at >= timedelta(minutes=cooldown_minutes)


async def _record_miss_refresh_attempt(session: AsyncSession) -> None:
    """Record *now* as the last miss-triggered attempt, success or failure.

    Written unconditionally -- including when the refresh below raises -- so
    a provider outage cannot turn into a retry storm: callers still get at
    most one attempt per cooldown window, just one that keeps failing until
    IMDb recovers.
    """
    stmt = insert(ImdbMissRefreshState).values(id=_MISS_REFRESH_ROW_ID)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"], set_={"attempted_at": func.now()}
    )
    await session.execute(stmt)
    await session.commit()


class ImdbMissRefresh:
    """Refreshes one IMDb id when a rating lookup during fact gathering finds
    nothing, rate-limited so a season-pack import cannot trigger one download
    per episode.

    A genuinely unrated title is the normal case -- a same-day release, or an
    unaired episode -- so this fires at most once per ``cooldown_minutes``;
    every miss after the first inside that window returns immediately
    without touching the network. The cooldown lives in
    ``imdb_miss_refresh_state`` (the database clock), not a process
    variable, so multiple pods behind one database share the same window.

    ``_lock`` only prevents *this process* from starting two downloads for
    two concurrent misses at once; the database check is re-tested once the
    lock is held, in case a concurrent miss already used up this window while
    this one was waiting on the lock.
    """

    def __init__(self, http: httpx.AsyncClient, cooldown_minutes: int):
        self._http = http
        self._cooldown_minutes = cooldown_minutes
        self._lock = asyncio.Lock()

    async def note_miss(self, session: AsyncSession, tconst: str, is_episode: bool) -> None:
        """Attempt one refresh for ``tconst``, subject to the cooldown.

        Never raises: a failed refresh just means the rating stays null this
        pass (the caller re-checks the lookup afterwards regardless), same as
        every other error-swallowing path in this module.

        A movie/show miss (``is_episode=False``) only needs the ratings
        dataset (8.6 MB); only an episode miss pulls the episode dataset too
        (54 MB), to learn the new episode's own tconst before it can be
        looked up in the ratings file.
        """
        if self._cooldown_minutes <= 0:
            return
        async with self._lock:
            if not await _miss_refresh_due(session, self._cooldown_minutes):
                return
            try:
                if is_episode:
                    await refresh(session, self._http, set(), {tconst}, track_state=False)
                else:
                    await refresh(session, self._http, {tconst}, set(), track_state=False)
            except Exception as exc:  # noqa: BLE001 - a missing rating must never fail the job
                logger.warning("imdb: miss-triggered refresh failed for %s: %s", tconst, exc)
            finally:
                await _record_miss_refresh_attempt(session)


# Process-wide handler, installed once at startup (see app.py's lifespan,
# alongside ImdbAutoRefresh) and consulted by facts/gather.py whenever a
# rating lookup misses. gather_facts()'s signature is frozen and carries no
# http client, so this is the seam that lets a fact-gathering pass reach the
# network without threading one through every call in between. ``None`` means
# either not configured yet or disabled via imdb_miss_refresh_minutes=0.
_miss_refresh: "ImdbMissRefresh | None" = None


def configure_miss_refresh(http: httpx.AsyncClient, cooldown_minutes: int) -> None:
    """Install (or disable) the process-wide miss-triggered refresh handler."""
    global _miss_refresh
    _miss_refresh = ImdbMissRefresh(http, cooldown_minutes) if cooldown_minutes > 0 else None


async def note_rating_miss(session: AsyncSession, tconst: str, *, is_episode: bool) -> None:
    """Called by facts/gather.py whenever a critic-rating lookup finds nothing."""
    if _miss_refresh is not None:
        await _miss_refresh.note_miss(session, tconst, is_episode)


class ImdbAutoRefresh:
    """Background refresh of the IMDb datasets.

    Same shape as ``plex.health.PlexHealth``: a single ``run(stop_event)``
    coroutine that the app lifespan starts as a task and cancels on shutdown,
    so this never blocks startup and never stalls the event loop (the actual
    parsing still runs via ``asyncio.to_thread`` inside ``refresh()``).

    IMDb ratings change continuously, and ``refresh()`` only knows about the
    ids present in ``media_items`` at the moment it runs — a title imported
    since the last refresh has no row at all until the next one — so this has
    to run periodically rather than once.
    """

    def __init__(
        self,
        session_factory,
        http: httpx.AsyncClient,
        interval_hours: float = 24,
        enabled: bool = True,
    ):
        self._session_factory = session_factory
        self._http = http
        self._interval_hours = interval_hours
        self._enabled = enabled

    async def run(self, stop_event: asyncio.Event) -> None:
        if not self._enabled:
            return
        while not stop_event.is_set():
            await self._maybe_refresh()
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._interval_hours * 3600
                )
            except asyncio.TimeoutError:
                pass
            else:
                return

    async def _maybe_refresh(self) -> None:
        """Refresh if stale. Never raises: a failed attempt just waits for the
        next interval, same as PlexHealth.refresh_token's error handling."""
        try:
            async with self._session_factory() as session:
                if not await _is_stale(session, self._interval_hours):
                    return
                movie_ids, show_ids = await _wanted_ids(session)
                rating_count = await refresh(session, self._http, movie_ids, show_ids)
                episode_count = await _stored_episode_count(session, show_ids)
        except Exception as exc:  # noqa: BLE001 - deliberately never propagates
            logger.warning("imdb: automatic refresh failed (will retry next interval): %s", exc)
            return
        logger.info(
            "imdb: automatic refresh stored %d rating(s) and %d episode row(s)",
            rating_count, episode_count,
        )


async def _run_cli() -> int:
    """One-shot loader: ``python -m autoposter.facts.imdb``.

    The app itself refreshes this dataset automatically in the background
    (see ``ImdbAutoRefresh``), on the schedule set by
    ``operations.imdb_refresh_hours``. This entry point remains useful for a
    first load before the app has run, or to force a refresh immediately
    rather than waiting for the next interval.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    database_url = os.environ.get("AUTOPOSTER_DATABASE_URL")
    if not database_url:
        print("AUTOPOSTER_DATABASE_URL is not set.", file=sys.stderr)
        return 1

    engine = make_engine(database_url)
    session_factory = make_session_factory(engine)
    try:
        async with session_factory() as session, httpx.AsyncClient() as http:
            movie_ids, show_ids = await _wanted_ids(session)
            rating_count = await refresh(session, http, movie_ids, show_ids)
            episode_count = await _stored_episode_count(session, show_ids)
    finally:
        await engine.dispose()

    print(f"Stored {rating_count} rating(s) and {episode_count} episode row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run_cli()))
