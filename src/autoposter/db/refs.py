"""Read-only lookups over ``MediaItemServerRef`` (spec §4.1).

The one place every reader outside ``render/pipeline.py`` goes to translate
between a ``media_items`` row and the per-server native id it has on each
server it is known to. ``pipeline.upsert_server_ref`` is the only writer.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import MediaItemServerRef


async def refs_for(session: AsyncSession, item_id: int) -> dict[str, str]:
    rows = await session.execute(
        select(MediaItemServerRef.server, MediaItemServerRef.native_id)
        .where(MediaItemServerRef.item_id == item_id).order_by(MediaItemServerRef.id)
    )
    return {server: native_id for server, native_id in rows.all()}


async def item_id_for(session: AsyncSession, server: str, native_id: str) -> int | None:
    return (await session.execute(
        select(MediaItemServerRef.item_id)
        .where(MediaItemServerRef.server == server, MediaItemServerRef.native_id == native_id)
    )).scalar_one_or_none()


async def native_ids(session: AsyncSession, item_ids: list[int], server: str) -> dict[int, str]:
    if not item_ids:
        return {}
    rows = await session.execute(
        select(MediaItemServerRef.item_id, MediaItemServerRef.native_id)
        .where(MediaItemServerRef.item_id.in_(item_ids), MediaItemServerRef.server == server)
    )
    return dict(rows.all())
