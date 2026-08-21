"""The record of which collections we own."""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoposter.db.models import ManagedCollection


async def test_a_managed_collection_round_trips(session):
    row = ManagedCollection(
        library="Movies", title="Age 17+ Movies", kind="smart",
        plex_rating_key="123", definition_hash="a" * 64,
    )
    session.add(row)
    await session.flush()
    loaded = (
        await session.execute(select(ManagedCollection).where(ManagedCollection.id == row.id))
    ).scalar_one()
    assert loaded.title == "Age 17+ Movies"
    assert loaded.kind == "smart"


async def test_the_same_title_cannot_be_managed_twice_in_one_library(session):
    session.add(ManagedCollection(library="Movies", title="Age 17+ Movies",
                                  kind="smart", definition_hash="a"))
    await session.flush()
    session.add(ManagedCollection(library="Movies", title="Age 17+ Movies",
                                  kind="smart", definition_hash="b"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_the_same_title_in_two_libraries_is_fine(session):
    """'IMDb Top 250' legitimately exists in both libraries."""
    session.add(ManagedCollection(library="Movies", title="IMDb Top 250",
                                  kind="manual", definition_hash="a"))
    session.add(ManagedCollection(library="TV Shows", title="IMDb Top 250",
                                  kind="manual", definition_hash="a"))
    await session.flush()
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert len(rows) == 2
