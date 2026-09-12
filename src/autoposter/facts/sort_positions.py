"""Roadmap row 269 -- the pipeline's read and delete of ``item_sort_positions``.

Pure: two statements, no config, no Plex. The gate is at the call sites
(``facts/gather.py`` reads only under ``operations.sort_title_source:
collections``; ``render/pipeline.py`` deletes only after a release was acted
on), so a deployment that never turns this on pays no query at all.
"""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemSortPosition, MediaItem


async def load_sort_position(
    session: AsyncSession, rating_key: str
) -> ItemSortPosition | None:
    """This item's row, held or released.

    Joined through ``media_items`` because the gather holds a
    ``ResolvedItem`` (a rating key) and not an id -- the ``facts_read.py``
    join shape, one row instead of many.
    """
    return (
        await session.execute(
            select(ItemSortPosition)
            .join(MediaItem, MediaItem.id == ItemSortPosition.item_id)
            .where(MediaItem.rating_key == str(rating_key))
        )
    ).scalar_one_or_none()


async def delete_sort_position(session: AsyncSession, item_id: int) -> None:
    """Drop the tombstone once its release has been acted on."""
    await session.execute(
        delete(ItemSortPosition).where(ItemSortPosition.item_id == item_id)
    )
