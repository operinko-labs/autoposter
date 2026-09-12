"""Read-only lookups over ``MediaItemServerRef`` (spec §4.1).

The one place every reader outside ``render/pipeline.py`` goes to translate
between a ``media_items`` row and the per-server native id it has on each
server it is known to. ``pipeline.upsert_server_ref`` is the only writer.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import MediaItemServerRef

# asyncpg caps one statement at 32,767 bind parameters. A full-library caller
# (a run_full_pass over every media_items row, the artwork/metadata backup
# walks) can pass an id list well past that on a real library, so
# native_ids/refs_for_items chunk it internally rather than trusting every
# caller to page itself -- one IN(...) clause per chunk, dicts merged.
_IN_CHUNK = 1000


def _chunked(item_ids: list[int]) -> list[list[int]]:
    return [item_ids[i:i + _IN_CHUNK] for i in range(0, len(item_ids), _IN_CHUNK)]


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
    result: dict[int, str] = {}
    for chunk in _chunked(item_ids):
        rows = await session.execute(
            select(MediaItemServerRef.item_id, MediaItemServerRef.native_id)
            .where(MediaItemServerRef.item_id.in_(chunk), MediaItemServerRef.server == server)
        )
        result.update(rows.all())
    return result


async def refs_for_items(session: AsyncSession, item_ids: list[int]) -> dict[int, dict[str, str]]:
    """``refs_for``, batched over a page of items in one query per chunk.

    Grouped by item rather than by server, unlike ``native_ids``: an API
    list/detail/mismatch/action-centre row wants every server an item is
    known to at once, not one server across many items.
    """
    if not item_ids:
        return {}
    result: dict[int, dict[str, str]] = {item_id: {} for item_id in item_ids}
    for chunk in _chunked(item_ids):
        rows = await session.execute(
            select(MediaItemServerRef.item_id, MediaItemServerRef.server, MediaItemServerRef.native_id)
            .where(MediaItemServerRef.item_id.in_(chunk))
            .order_by(MediaItemServerRef.id)
        )
        for item_id, server, native_id in rows.all():
            result[item_id][server] = native_id
    return result
