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


async def test_the_two_status_columns_round_trip(session):
    """Roadmap row 100 sub-phase C2c. The stored status is Kometa's TOKEN --
    `facts/tmdb_facts.py::TMDB_SHOW_STATUS` maps TMDb's string to it at the
    edge -- so this column and a `tmdb_status:` filter value are the same
    value space, which is the condition row 156 set for a facts-backed
    filter name."""
    item = await _item(session, "status")
    session.add(ItemFacts(
        item_id=item.id,
        tmdb_status="returning",
        last_episode_aired=date(2026, 8, 20),
    ))
    await session.commit()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_status == "returning"
    assert row.last_episode_aired == date(2026, 8, 20)


async def test_the_two_status_columns_are_null_until_a_gather_fills_them(session):
    """NULL means "TMDb has not told us", which is a DIFFERENT statement from
    any value -- the rule `alembic/versions/80f7d7e25a0c_tmdb_facts_widening.py`
    already records for `tmdb_original_language`, and the reason neither
    column takes a server-side default. It is also the state EVERY existing
    row is in on upgrade (adjudication A-4: no backfill job; the columns fill
    on the next facts refresh, and `api/facts_backfill.py` is the operator's
    fast path), so a show badges no status until its row is re-gathered --
    which is a disclosure, not a defect."""
    item = await _item(session, "status-null")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_status is None
    assert row.last_episode_aired is None


async def test_the_three_row_156_columns_round_trip(session):
    """Roadmap row 156's value-space pin, on `test_the_two_status_columns_
    round_trip`'s model. These three columns are what `common_sense_rating`,
    `imdb_rating` and `tmdb_rating` filter on, and each is a DIFFERENT value
    from the Plex attrib the Kometa-named filter of the neighbouring name
    reads -- which is why the three filters carry coined names."""
    item = await _item(session, "row156")
    session.add(ItemFacts(
        item_id=item.id,
        content_rating="13",
        critic_rating=7.8,
        audience_rating=6.9,
        sources={"content_rating": "mdb_commonsense", "critic_rating": "imdb"},
    ))
    await session.commit()
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.content_rating == "13"
    assert row.critic_rating == pytest.approx(7.8)
    assert row.audience_rating == pytest.approx(6.9)
    assert row.sources["content_rating"] == "mdb_commonsense"


def test_the_common_sense_value_space_is_mdblists_age_rating_not_a_certification():
    """What an operator writes on the right of `common_sense_rating:`. The
    column is `parse_content_rating`'s output and nothing else writes it: a
    bare age number as a STRING, gated on `commonsense` being truthy. A Plex
    certification spelling (`PG-13`) can never appear in this column, so a
    filter written with one matches nothing -- the disjointness the row's note
    declares, pinned at the parser that creates the value space."""
    from autoposter.facts.mdblist import parse_content_rating

    assert parse_content_rating({"commonsense": True, "age_rating": 13}) == "13"
    assert parse_content_rating({"commonsense": True, "age_rating": "8"}) == "8"
    assert parse_content_rating({"commonsense": False, "age_rating": 13}) is None
    assert parse_content_rating({"age_rating": "PG-13"}) is None
    assert parse_content_rating({"commonsense": True, "age_rating": ""}) is None
