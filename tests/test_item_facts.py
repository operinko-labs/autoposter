from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts.models import GatheredFacts


async def _item(session, rating_key="1"):
    item = MediaItem(rating_key=rating_key, library="Movies", kind="movie", title="X")
    session.add(item)
    await session.flush()
    return item


async def test_facts_roundtrip(session):
    item = await _item(session)
    session.add(ItemFacts(
        item_id=item.id, critic_rating=4.9, audience_rating=6.3,
        content_rating="17", genres=["Horror", "Drama"], studio="A24",
        originally_available=date(2023, 5, 12),
        sources={"critic_rating": "imdb", "audience_rating": "tmdb"},
    ))
    await session.commit()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating == pytest.approx(4.9)
    assert row.genres == ["Horror", "Drama"]
    assert row.sources["critic_rating"] == "imdb"


async def test_one_facts_row_per_item(session):
    item = await _item(session, "dup")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()
    session.add(ItemFacts(item_id=item.id))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_all_facts_are_optional(session):
    """A brand new item has no facts yet; nothing may be NOT NULL."""
    item = await _item(session, "empty")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating is None
    assert row.genres == []


async def test_fetched_at_comes_from_the_database_clock(session):
    from sqlalchemy import func

    item = await _item(session, "clock")
    session.add(ItemFacts(item_id=item.id))
    await session.flush()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    # Asserted as EXACT equality inside one transaction, not as proximity.
    # func.now() is transaction_timestamp(), constant for the whole transaction,
    # and the server_default that stamped fetched_at ran inside this same one --
    # so a database-clock value matches to the microsecond, while anything
    # computed in this process cannot. The previous shape compared two readings
    # taken at different instants with a `< 1` second tolerance; on this dev VM
    # the wall clock steps backwards ~2.7 s every ~30 s
    # (docs/research/dev-clock-step/), which is larger than that tolerance, so a
    # step landing between the write and the read failed the test spuriously.
    db_now = (await session.execute(select(func.now()))).scalar_one()
    assert row.fetched_at == db_now


def test_gathered_facts_is_frozen_and_defaults_empty():
    import dataclasses

    facts = GatheredFacts()
    assert facts.genres == []
    assert facts.sources == {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.studio = "nope"


def test_is_empty_false_for_zero_rating():
    """A rating of 0.0 is a real, present value, not a missing one."""
    assert GatheredFacts(critic_rating=0.0).is_empty() is False


def test_is_empty_false_for_zero_content_rating():
    """A content rating of '0' is a present string, not a missing one."""
    assert GatheredFacts(content_rating="0").is_empty() is False


def test_is_empty_true_for_empty_genre_list():
    assert GatheredFacts(genres=[]).is_empty() is True


def test_is_empty_true_for_default_instance():
    assert GatheredFacts().is_empty() is True


def test_is_empty_true_when_only_sources_populated():
    """sources is provenance, not a fact, so it must not count toward emptiness."""
    assert GatheredFacts(sources={"critic_rating": "imdb"}).is_empty() is True
