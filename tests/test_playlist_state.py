"""``managed_playlists`` -- the table that IS the ownership predicate.

For a collection, the row is half of ownership and the Plex label is the other
half; ``engine._sweep``'s docstring spells out why neither alone is enough. A
playlist has no label, so the row has to carry Plex's own identity for the
object instead: ``plex_rating_key``, not null, indexed, and the thing every
ownership test in this phase compares against.

Model-versus-migration drift is deliberately NOT tested here.
``tests/test_migrations.py`` already owns that question and owns it properly:
``test_alembic_head_matches_models`` runs ``alembic upgrade head`` against a
scratch database and then ``alembic check``, and ``test_alembic_has_a_single_head``
asserts the head. A test in THIS file could not do it -- ``tests/conftest.py``'s
``engine`` fixture builds the suite's schema with ``Base.metadata.create_all``,
so a table read back from that connection is a table built from
``ManagedPlaylist.__table__``, and comparing the two is a tautology that passes
with the migration file empty.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoposter.db.models import ManagedPlaylist


def test_the_table_has_no_library_column():
    """A playlist belongs to no library -- it spans however many its members
    came from -- so ``ManagedCollection``'s ``(library, title)`` key cannot be
    copied and neither can its column."""
    columns = {c.name for c in ManagedPlaylist.__table__.columns}
    assert "library" not in columns
    assert "libraries" in columns, (
        "the definition's SCOPE is still recorded, for the report -- it is just "
        "not part of the key"
    )


def test_the_rating_key_is_not_nullable_because_it_is_the_predicate():
    column = ManagedPlaylist.__table__.columns["plex_rating_key"]
    assert column.nullable is False
    assert column.index is True


def test_the_title_is_unique_on_its_own():
    constraints = {
        c.name: sorted(col.name for col in c.columns)
        for c in ManagedPlaylist.__table__.constraints
        if c.name == "uq_managed_playlist_title"
    }
    assert constraints == {"uq_managed_playlist_title": ["title"]}


async def test_two_rows_may_not_share_a_title(session):
    session.add(ManagedPlaylist(
        title="Marvel", plex_rating_key="7001", definition_hash="a", libraries=["Movies"],
    ))
    await session.flush()
    session.add(ManagedPlaylist(
        title="Marvel", plex_rating_key="7002", definition_hash="b", libraries=["Movies"],
    ))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_row_round_trips_its_libraries_as_a_list(session):
    session.add(ManagedPlaylist(
        title="Star Wars", plex_rating_key="7003", definition_hash="c",
        libraries=["Movies", "TV Shows"],
    ))
    await session.flush()
    session.expunge_all()

    row = (await session.execute(
        select(ManagedPlaylist).where(ManagedPlaylist.title == "Star Wars")
    )).scalar_one()

    assert row.libraries == ["Movies", "TV Shows"]
    assert row.member_count is None
    assert row.last_reconciled_at is None
