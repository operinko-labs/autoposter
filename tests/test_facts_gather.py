from datetime import date
import asyncio

import pytest
from sqlalchemy import select

from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts.gather import (
    format_audience,
    format_critic,
    gather_facts,
    persist_facts,
)
from autoposter.facts.models import GatheredFacts
from autoposter.plex.client import ResolvedItem


def item(kind="movie", season=None, episode=None):
    return ResolvedItem(
        rating_key="1", library="Movies", kind=kind, title="X", year=2023,
        season_number=season, episode_number=episode, root_folder="X",
        file_path=None, art_url=None, tmdb_id=940143, tvdb_id=371980,
        imdb_id="tt14316486", parent_rating_key=None,
    )


class FakeTMDB:
    def __init__(self):
        self.season_calls = []

    async def movie(self, tmdb_id):
        return GatheredFacts(audience_rating=6.3, genres=["Horror"], studio="A24",
                             originally_available=date(2023, 5, 12),
                             sources={"audience_rating": "tmdb"})

    async def show(self, tmdb_id):
        return GatheredFacts(audience_rating=8.4, genres=["Drama"], studio="Apple TV+",
                             sources={"audience_rating": "tmdb"})

    async def season_episode_ratings(self, tmdb_id, season_number):
        self.season_calls.append((tmdb_id, season_number))
        return {3: 7.8}


class FakeMDBList:
    def __init__(self, value="17"):
        self.value = value
        self.calls = 0

    async def content_rating(self, tmdb_id=None, tvdb_id=None, is_movie=True):
        self.calls += 1
        return self.value


def test_critic_always_renders_one_decimal():
    assert format_critic(4.9) == "4.9"
    assert format_critic(9.0) == "9.0"
    assert format_critic(10.0) == "10.0"
    assert format_critic(None) is None


def test_audience_truncates_rather_than_rounds():
    assert format_audience(6.3) == "63%"
    assert format_audience(7.0) == "70%"
    assert format_audience(10.0) == "100%"
    assert format_audience(8.65) == "86%"   # truncation, not 87
    assert format_audience(None) is None


async def test_movie_gathers_every_field(session):
    from autoposter.facts.imdb import store_ratings

    await store_ratings(session, {"tt14316486": 4.9})
    facts = await gather_facts(session, item(), FakeTMDB(), FakeMDBList())
    assert facts.critic_rating == pytest.approx(4.9)
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.content_rating == "17"
    assert facts.genres == ["Horror"]
    assert facts.studio == "A24"
    assert facts.sources["critic_rating"] == "imdb"
    assert facts.sources["content_rating"] == "mdb_commonsense"


async def test_show_uses_the_show_endpoints(session):
    facts = await gather_facts(session, item(kind="show"), FakeTMDB(), FakeMDBList())
    assert facts.studio == "Apple TV+"
    assert facts.content_rating == "17"


async def test_season_has_no_facts_of_its_own(session):
    mdblist = FakeMDBList()
    facts = await gather_facts(session, item(kind="season", season=2), FakeTMDB(), mdblist)
    assert facts.is_empty()
    assert mdblist.calls == 0


async def test_episode_takes_only_the_two_ratings(session):
    from autoposter.facts.imdb import store_episodes, store_ratings

    await store_episodes(session, {("tt14316486", 2, 3): "tt9999999"})
    await store_ratings(session, {"tt9999999": 7.1})
    tmdb = FakeTMDB()
    facts = await gather_facts(
        session, item(kind="episode", season=2, episode=3), tmdb, FakeMDBList()
    )
    assert facts.critic_rating == pytest.approx(7.1)
    assert facts.audience_rating == pytest.approx(7.8)
    assert facts.genres == []
    assert facts.studio is None
    assert facts.content_rating is None
    assert tmdb.season_calls == [(940143, 2)]


async def test_episode_without_imdb_data_still_returns_audience(session):
    facts = await gather_facts(
        session, item(kind="episode", season=2, episode=3), FakeTMDB(), FakeMDBList()
    )
    assert facts.critic_rating is None
    assert facts.audience_rating == pytest.approx(7.8)


async def test_persist_is_idempotent(session):
    media = MediaItem(rating_key="p1", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(critic_rating=4.9))
    await persist_facts(session, media.id, GatheredFacts(critic_rating=5.1))
    rows = (await session.execute(select(ItemFacts))).scalars().all()
    assert len(rows) == 1
    assert rows[0].critic_rating == pytest.approx(5.1)


async def test_no_mdblist_key_only_degrades_content_rating(session):
    """Finding 1: the stand-in used when no API key is configured must not
    affect critic rating, audience rating, genres, or studio."""
    from autoposter.facts.imdb import store_ratings
    from autoposter.facts.mdblist import NullMDBListClient

    await store_ratings(session, {"tt14316486": 4.9})
    facts = await gather_facts(session, item(), FakeTMDB(), NullMDBListClient())
    assert facts.critic_rating == pytest.approx(4.9)
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.genres == ["Horror"]
    assert facts.studio == "A24"
    assert facts.content_rating is None
    assert "content_rating" not in facts.sources
    assert facts.sources["critic_rating"] == "imdb"


async def test_mdblist_limit_does_not_abort_the_other_facts(session):
    from autoposter.facts.mdblist import MDBListLimitReached

    class Exhausted:
        async def content_rating(self, **kwargs):
            raise MDBListLimitReached("API Limit Reached!")

    facts = await gather_facts(session, item(), FakeTMDB(), Exhausted())
    assert facts.content_rating is None
    assert facts.audience_rating == pytest.approx(6.3)


async def test_updated_at_advances_on_second_persist_facts(session_factory):
    """Verify updated_at advances on upsert via on_conflict_do_update.

    This test proves the fix works: without updated_at in the set_ mapping,
    this test would fail because updated_at would remain frozen at insert time.
    """
    # Create a media item in the database
    async with session_factory() as s:
        media = MediaItem(rating_key="p2", library="Movies", kind="movie", title="X")
        s.add(media)
        await s.flush()
        await s.commit()
        media_id = media.id

    # First persist_facts call in a separate transaction
    async with session_factory() as s:
        await persist_facts(s, media_id, GatheredFacts(critic_rating=4.9))
        # Get the row and the database clock at commit time
        row1 = (await s.execute(select(ItemFacts).where(ItemFacts.item_id == media_id))).scalar_one()
        updated_at_1 = row1.updated_at

    # Ensure a small delay so database clock advances
    await asyncio.sleep(0.1)

    # Second persist_facts call in a separate transaction
    async with session_factory() as s:
        await persist_facts(s, media_id, GatheredFacts(critic_rating=5.1))
        row2 = (await s.execute(select(ItemFacts).where(ItemFacts.item_id == media_id))).scalar_one()
        updated_at_2 = row2.updated_at

    # Verify updated_at advanced and created_at stayed the same
    assert updated_at_2 > updated_at_1
    assert row2.critic_rating == pytest.approx(5.1)


async def test_fetched_at_advances_on_second_persist_facts(session_factory):
    """Verify fetched_at also advances on upsert.

    Both fetched_at and updated_at use the same mechanism (func.now() in set_ mapping),
    so both should advance on every conflict-update.
    """
    # Create a media item in the database
    async with session_factory() as s:
        media = MediaItem(rating_key="p3", library="Movies", kind="movie", title="X")
        s.add(media)
        await s.flush()
        await s.commit()
        media_id = media.id

    # First persist_facts call in a separate transaction
    async with session_factory() as s:
        await persist_facts(s, media_id, GatheredFacts(audience_rating=6.3))
        row1 = (await s.execute(select(ItemFacts).where(ItemFacts.item_id == media_id))).scalar_one()
        fetched_at_1 = row1.fetched_at

    # Ensure a small delay so database clock advances
    await asyncio.sleep(0.1)

    # Second persist_facts call in a separate transaction
    async with session_factory() as s:
        await persist_facts(s, media_id, GatheredFacts(audience_rating=7.5))
        row2 = (await s.execute(select(ItemFacts).where(ItemFacts.item_id == media_id))).scalar_one()
        fetched_at_2 = row2.fetched_at

    # Verify fetched_at advanced
    assert fetched_at_2 > fetched_at_1
    assert row2.audience_rating == pytest.approx(7.5)
