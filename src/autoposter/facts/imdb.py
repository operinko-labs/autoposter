"""IMDb ratings via the bulk non-commercial datasets.

IMDb publishes these files daily and forbids scraping the site for the same
data. Use is personal and non-commercial only, and requires the attribution
carried in README.md.
"""

import csv
import gzip
import itertools
import logging
import os
import tempfile
from collections.abc import Iterable, Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ImdbEpisode, ImdbRating

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


async def download_tsv(http: httpx.AsyncClient, url: str) -> Iterator[str]:
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
    """
    fd, path = tempfile.mkstemp(suffix=".tsv.gz")
    os.close(fd)
    try:
        async with http.stream("GET", url, follow_redirects=True, timeout=300) as response:
            response.raise_for_status()
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

    return _lines()


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
) -> int:
    """Reload both datasets, keeping only rows this library needs.

    Returns the number of ratings stored. Episodes are resolved first so their
    ids can be added to the ratings filter in a single pass.
    """
    episodes: dict[tuple[str, int, int], str] = {}
    if show_ids:
        episodes = parse_episodes(await download_tsv(http, EPISODES_URL), show_ids)
        await store_episodes(session, episodes)
        logger.info("imdb: kept %d episode rows for %d shows", len(episodes), len(show_ids))

    wanted = set(movie_ids) | set(episodes.values())
    ratings = parse_ratings(await download_tsv(http, RATINGS_URL), wanted) if wanted else {}
    await store_ratings(session, ratings)
    logger.info("imdb: stored %d ratings", len(ratings))
    return len(ratings)
