"""The poster hash column on ``managed_collections``.

``poster_sha256`` is nullable with no server default: ``NULL`` means we have
never set this collection's poster, which is correct for every existing row
-- adopted collections keep the poster they already have.
"""
from sqlalchemy import select

from autoposter.db.models import ManagedCollection


async def test_a_new_row_has_no_poster_hash(session):
    session.add(ManagedCollection(
        library="Movies", title="IMDb Top 250", kind="manual",
        definition_hash="abc",
    ))
    await session.flush()

    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None


async def test_the_poster_hash_round_trips(session):
    session.add(ManagedCollection(
        library="Movies", title="IMDb Top 250", kind="manual",
        definition_hash="abc", poster_sha256="a" * 64,
    ))
    await session.commit()

    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 == "a" * 64


async def test_two_libraries_keep_independent_poster_hashes(session):
    """The ``(library, title)`` uniqueness already lets the same title exist
    in two libraries; the poster hash must stay independent per row."""
    session.add(ManagedCollection(
        library="Movies", title="IMDb Top 250", kind="manual",
        definition_hash="abc", poster_sha256="a" * 64,
    ))
    session.add(ManagedCollection(
        library="Kids Movies", title="IMDb Top 250", kind="manual",
        definition_hash="abc", poster_sha256="b" * 64,
    ))
    await session.commit()

    rows = (await session.execute(
        select(ManagedCollection).order_by(ManagedCollection.library)
    )).scalars().all()
    assert [r.poster_sha256 for r in rows] == ["b" * 64, "a" * 64]
