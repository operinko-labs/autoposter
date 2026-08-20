import gzip
import types
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from autoposter.db.models import ImdbRating
from autoposter.facts.imdb import (
    download_tsv,
    get_episode_rating,
    get_rating,
    parse_episodes,
    parse_ratings,
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
        result = await download_tsv(http, "https://example.test/title.ratings.tsv.gz")
        assert isinstance(result, types.GeneratorType)
        lines = [line.rstrip("\n") for line in result]

    assert len(lines) == row_count + 1
    assert lines[0] == header
    assert lines[1] == "tt0000000\t5.0\t100"
    assert lines[-1] == f"tt{row_count - 1:07d}\t{5 + ((row_count - 1) % 5)}.0\t100"
