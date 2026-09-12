"""Roadmap row 269 through ``apply_metadata`` -- the real seam.

The gated-feature trio for the held row (gate-off byte-identical, gate-on
fires, second pass steady), and the same trio for the RELEASE: a tombstone
writes one blank-and-unlock edit and is deleted, apply-off keeps it, an
exempt item drops it without a write.
"""
import logging
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from autoposter.config.loader import load_config
from autoposter.config.schema import OperationsConfig
from autoposter.db.models import ItemMetadataOverride, ItemSortPosition, MediaItem
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import plan_edits
from autoposter.render.pipeline import apply_metadata

from test_mass_ops_fields import FakeItem, FakeTMDB, _item
from test_mass_ops_verbs import FakeField, LockableItem, RecordingPlexItem, RecordingServer

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


@pytest_asyncio.fixture
async def media_item_id(session):
    media = MediaItem(rating_key="1", library="Movies", kind="movie", title="Aliens")
    session.add(media)
    await session.flush()
    return media.id


async def _hold(session, item_id, base="Alien", position=2, total=3):
    session.add(ItemSortPosition(
        item_id=item_id, library="Movies", definition_title="Alien Collection",
        base=base, position=position, total=total,
    ))
    await session.flush()


async def _release(session, item_id):
    await session.execute(
        update(ItemSortPosition)
        .where(ItemSortPosition.item_id == item_id)
        .values(released_at=func.now())
    )
    await session.flush()


async def _row(session, item_id):
    return (
        await session.execute(
            select(ItemSortPosition).where(ItemSortPosition.item_id == item_id)
        )
    ).scalar_one_or_none()


def _apply(session, config, media_item_id, plex_item):
    # ``RecordingServer``: since PR #236 the pipeline writes through the
    # MediaServer protocol by ref, never a plexapi object directly.
    return apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts()), NullMDBListClient(),
    )


# --- the writer's clear ------------------------------------------------------


def test_a_clear_counts_as_a_fact():
    """"" means CLEAR and must reach ``apply_facts``; a falsy check would
    drop it at ``apply_metadata``'s is_empty gate."""
    assert not GatheredFacts(sort_title="").is_empty()


def test_the_writer_clears_a_locked_sort_title():
    item = LockableItem(titleSort="Alien 02", locks=[("titleSort", True)])
    edits = plan_edits(item, GatheredFacts(sort_title=""), OperationsConfig(sort_title_apply=True))
    assert edits == {"titleSort.value": "", "titleSort.locked": 0}


def test_the_writer_leaves_an_unlocked_sort_title_alone():
    """Steady state after the clear: Plex reports the field unlocked, so
    there is nothing left to hand back."""
    item = LockableItem(titleSort="Alien", locks=[("titleSort", False)])
    edits = plan_edits(item, GatheredFacts(sort_title=""), OperationsConfig(sort_title_apply=True))
    assert edits == {}


def test_the_writer_reports_a_clear_under_apply_off(caplog):
    item = LockableItem(
        titleSort="Alien 02", locks=[("titleSort", True)], title="Aliens", year=1986,
    )
    with caplog.at_level(logging.INFO, logger="autoposter.plex.writer"):
        edits = plan_edits(item, GatheredFacts(sort_title=""), OperationsConfig())
    assert edits == {}
    assert (
        "plex: would clear sort_title on movie 'Aliens' (1986) "
        "(operations.sort_title_apply is off)"
    ) in [r.getMessage() for r in caplog.records]


def test_a_show_can_carry_a_sort_title():
    """A list may hold shows; the per-source kind rule lives at the gather."""
    item = FakeItem(kind="show", titleSort="Dark")
    edits = plan_edits(
        item, GatheredFacts(sort_title="Dark 01"), OperationsConfig(sort_title_apply=True)
    )
    assert edits == {"titleSort.value": "Dark 01", "titleSort.locked": 1}


# --- the held row, through the real seam -------------------------------------


@pytest.mark.asyncio
async def test_gate_off_reads_no_row(session, media_item_id, config):
    await _hold(session, media_item_id)
    plex_item = RecordingPlexItem(titleSort="Aliens", locks=[])
    await _apply(session, config, media_item_id, plex_item)
    assert plex_item.edits == []


@pytest.mark.asyncio
async def test_gate_on_writes_the_position_and_the_second_pass_is_steady(
    session, media_item_id, config
):
    config.operations.sort_title_source = "collections"
    config.operations.sort_title_apply = True
    await _hold(session, media_item_id)
    plex_item = RecordingPlexItem(titleSort="Aliens", locks=[])

    await _apply(session, config, media_item_id, plex_item)
    assert plex_item.edits == [{"titleSort.value": "Alien 02", "titleSort.locked": 1}]

    plex_item.titleSort = "Alien 02"
    plex_item.fields = [FakeField("titleSort", True)]
    await _apply(session, config, media_item_id, plex_item)
    assert len(plex_item.edits) == 1


@pytest.mark.asyncio
async def test_a_row_99_override_wins_over_the_position(session, media_item_id, config):
    config.operations.sort_title_source = "collections"
    config.operations.sort_title_apply = True
    config.operations.item_overrides_enabled = True
    await _hold(session, media_item_id)
    session.add(ItemMetadataOverride(item_id=media_item_id, field="sort_title", value="Mine"))
    await session.flush()
    plex_item = RecordingPlexItem(titleSort="Aliens", locks=[])

    await _apply(session, config, media_item_id, plex_item)
    assert plex_item.edits == [{"titleSort.value": "Mine", "titleSort.locked": 1}]


# --- the release, through the real seam --------------------------------------


@pytest.mark.asyncio
async def test_a_released_row_clears_once_and_is_then_gone(session, media_item_id, config):
    config.operations.sort_title_source = "collections"
    config.operations.sort_title_apply = True
    await _hold(session, media_item_id)
    await _release(session, media_item_id)
    plex_item = RecordingPlexItem(titleSort="Alien 02", locks=[("titleSort", True)])

    await _apply(session, config, media_item_id, plex_item)
    assert plex_item.edits == [{"titleSort.value": "", "titleSort.locked": 0}]
    assert await _row(session, media_item_id) is None, "the tombstone is deleted after the clear"

    plex_item.titleSort = "Aliens"
    plex_item.fields = [FakeField("titleSort", False)]
    await _apply(session, config, media_item_id, plex_item)
    assert len(plex_item.edits) == 1


@pytest.mark.asyncio
async def test_apply_off_keeps_the_tombstone(session, media_item_id, config):
    config.operations.sort_title_source = "collections"
    await _hold(session, media_item_id)
    await _release(session, media_item_id)
    plex_item = RecordingPlexItem(titleSort="Alien 02", locks=[("titleSort", True)])

    await _apply(session, config, media_item_id, plex_item)
    assert plex_item.edits == []
    assert await _row(session, media_item_id) is not None


@pytest.mark.asyncio
async def test_an_exempt_item_drops_the_tombstone_without_a_write(
    session, media_item_id, config
):
    """Row 99's own ruling for its DELETE endpoint: the row goes, the write
    does not, and the response (here: the log) says so rather than claiming
    a clear that never happened."""
    config.operations.sort_title_source = "collections"
    config.operations.sort_title_apply = True
    config.operations.ignore_ids = ["1"]
    await _hold(session, media_item_id)
    await _release(session, media_item_id)
    plex_item = RecordingPlexItem(titleSort="Alien 02", locks=[("titleSort", True)])

    await _apply(session, config, media_item_id, plex_item)
    assert plex_item.edits == []
    assert await _row(session, media_item_id) is None
