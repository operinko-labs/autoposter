"""IMDb ratings via the bulk non-commercial datasets.

IMDb publishes these files daily and forbids scraping the site for the same
data. Use is personal and non-commercial only, and requires the attribution
carried in README.md.
"""

import csv
import gzip
import logging
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
    """Stream a gzipped TSV and yield decoded lines.

    The files are tens of megabytes; they are decompressed in memory rather
    than written to disk, and never cached — IMDb refreshes them daily.
    """
    response = await http.get(url, follow_redirects=True, timeout=300)
    response.raise_for_status()
    text = gzip.decompress(response.content).decode("utf-8")
    return iter(text.splitlines())


async def store_ratings(session: AsyncSession, ratings: dict[str, float]) -> None:
    for tconst, rating in ratings.items():
        await session.execute(
            insert(ImdbRating)
            .values(tconst=tconst, rating=rating)
            .on_conflict_do_update(index_elements=["tconst"], set_={"rating": rating})
        )
    await session.commit()


async def store_episodes(
    session: AsyncSession, episodes: dict[tuple[str, int, int], str]
) -> None:
    for (parent, season, episode), tconst in episodes.items():
        await session.execute(
            insert(ImdbEpisode)
            .values(
                parent_tconst=parent,
                season_number=season,
                episode_number=episode,
                tconst=tconst,
            )
            .on_conflict_do_update(
                index_elements=["parent_tconst", "season_number", "episode_number"],
                set_={"tconst": tconst},
            )
        )
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
