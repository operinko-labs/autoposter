"""Roadmap row 99 -- the per-item metadata override store.

The table, its cascade, its parser and its one pure loader. Nothing here
writes to ``item_facts`` and nothing here reads a provider: an override row
holds only what an operator typed, which is the freezing hazard's own rule
(``frontend/src/api/overrides.ts:13-21``) read onto this row.

Keyed on ``media_items.id`` and never on ``rating_key``: the 2026-09-03
identity re-key MUTATES ``rating_key`` in place (``render/pipeline.py``'s
``_rekey_by_identity``) precisely so that children keyed on ``id`` survive the
move, and a rating-key-keyed table would silently detach on every re-key.
"""
from datetime import datetime

import pytest
from sqlalchemy import select

from autoposter.db.models import ItemMetadataOverride, MediaItem


async def _item(session, rating_key: str = "1", kind: str = "movie") -> MediaItem:
    item = MediaItem(
        rating_key=rating_key, library="Movies", kind=kind, title="Heat",
        year=1995, tmdb_id=949,
    )
    session.add(item)
    await session.flush()
    return item


async def test_an_override_row_round_trips(session):
    item = await _item(session)
    session.add(ItemMetadataOverride(
        item_id=item.id, field="tagline", value="A Los Angeles crime saga",
    ))
    await session.commit()

    row = (await session.execute(select(ItemMetadataOverride))).scalar_one()
    assert row.item_id == item.id
    assert row.field == "tagline"
    assert row.value == "A Los Angeles crime saga"
    assert isinstance(row.created_at, datetime)
    assert isinstance(row.updated_at, datetime)


async def test_one_row_per_item_and_field(session):
    """UNIQUE(item_id, field) is what makes a PUT an upsert rather than an
    append: without it, "the override for this field" would stop being a
    single answerable question the moment anyone pressed save twice."""
    from sqlalchemy.exc import IntegrityError

    item = await _item(session)
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    await session.commit()
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="MGM"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_two_fields_on_one_item_coexist(session):
    item = await _item(session)
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    session.add(ItemMetadataOverride(item_id=item.id, field="tagline", value="x"))
    await session.commit()

    rows = (await session.execute(select(ItemMetadataOverride))).scalars().all()
    assert sorted(row.field for row in rows) == ["studio", "tagline"]


async def test_deleting_the_item_cascades_the_overrides(session):
    """``scheduler/prune.py`` hard-deletes ``media_items`` rows, and every
    other per-item child of that table already cascades -- renders, facts,
    credits, dismissals and ``parent_id`` itself. An override that outlived
    its item would be a row pointing at nothing, invisible to every listing
    and impossible to delete from the panel."""
    item = await _item(session)
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    await session.commit()

    await session.delete(item)
    await session.commit()

    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() == []


async def test_an_override_survives_a_re_key(session):
    """The whole reason the FK is ``media_items.id``. A re-key mutates
    ``rating_key`` on the SAME row and leaves ``id`` alone, so the override
    stays attached with no work at all. A ``rating_key``-keyed table would
    have detached here, silently."""
    item = await _item(session, rating_key="16201")
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    await session.commit()

    item.rating_key = "165269"
    await session.commit()
    session.expire_all()

    row = (await session.execute(select(ItemMetadataOverride))).scalar_one()
    reloaded = (
        await session.execute(select(MediaItem).where(MediaItem.id == row.item_id))
    ).scalar_one()
    assert reloaded.rating_key == "165269"
