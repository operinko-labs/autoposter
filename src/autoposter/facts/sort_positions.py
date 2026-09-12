"""Roadmap row 269 -- the pipeline's side of ``item_sort_positions``.

Pure: two statements and one dataclass overlay, no config, no server. The
gate is at the call sites (``render/pipeline.py::apply_metadata`` reads only
under ``operations.sort_title_source: collections`` and deletes only after a
release was acted on), so a deployment that never turns this on pays no
query at all.

Keyed on ``media_items.id`` and NOTHING else, on purpose: the pipeline holds
the id by the time it gathers, and the Jellyfin design
(``docs/design/2026-09-12-jellyfin-media-server-design.md`` §4.6-4.7) drops
``media_items.rating_key`` for an identity key plus per-server refs. A read
that joined on the Plex key would have to move with it; this one does not.
"""
from dataclasses import replace

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemSortPosition
from autoposter.facts.franchise_sort import format_position
from autoposter.facts.models import GatheredFacts


async def load_sort_position(session: AsyncSession, item_id: int) -> ItemSortPosition | None:
    """This item's row, held or released, or ``None``."""
    return (
        await session.execute(
            select(ItemSortPosition).where(ItemSortPosition.item_id == item_id)
        )
    ).scalar_one_or_none()


def with_sort_position(facts: GatheredFacts, row: ItemSortPosition | None) -> GatheredFacts:
    """``facts`` with the row's sort title laid on, or ``facts`` itself.

    A held row yields ``"<base> <NN>"``; a RELEASED row (spec §8) yields
    ``""`` -- CLEAR -- which the writer turns into one blank-and-unlock edit
    and ``apply_metadata`` then drops the row. Returned by identity when
    there is no row, so gate-off and no-row are indistinguishable downstream.
    """
    if row is None:
        return facts
    value = "" if row.released_at is not None else format_position(
        row.base, row.position, row.total
    )
    return replace(
        facts, sort_title=value, sources={**facts.sources, "sort_title": "collections"}
    )


async def delete_sort_position(session: AsyncSession, item_id: int) -> None:
    """Drop the tombstone once its release has been acted on."""
    await session.execute(
        delete(ItemSortPosition).where(ItemSortPosition.item_id == item_id)
    )
