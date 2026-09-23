"""Read-only lookups over ``MediaItemServerRef`` (spec §4.1).

The one place every reader outside ``render/pipeline.py`` goes to translate
between a ``media_items`` row and the per-server native id it has on each
server it is known to. ``pipeline.upsert_server_ref`` is the only writer.
"""
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import MediaItem, MediaItemServerRef

# asyncpg caps one statement at 32,767 bind parameters. A full-library caller
# (a run_full_pass over every media_items row, the artwork/metadata backup
# walks) can pass an id list well past that on a real library, so
# native_ids/refs_for_items chunk it internally rather than trusting every
# caller to page itself -- one IN(...) clause per chunk, dicts merged.
_IN_CHUNK = 1000


def _chunked(item_ids: list[int]) -> list[list[int]]:
    return [item_ids[i:i + _IN_CHUNK] for i in range(0, len(item_ids), _IN_CHUNK)]


# ONE ref per (item, server) is an invariant -- ``upsert_server_ref`` deletes
# the item's other refs for a server it just resolved, and the migration's
# merge drops a stale row's ref for a server the survivor already has (spec
# §4.1). So the three lookups below normally have nothing to choose between.
# They still read ``id DESC`` and keep the FIRST row seen for a key
# (``setdefault``, never a dict comprehension's last-one-wins), so that a
# database written by some other version of this code -- or a hand-edited
# row -- resolves to the NEWEST ref rather than to whichever one Postgres
# happened to return first. Arbitrary is the failure this replaces; every
# reader now agrees on the same answer.
async def refs_for(session: AsyncSession, item_id: int) -> dict[str, str]:
    rows = await session.execute(
        select(MediaItemServerRef.server, MediaItemServerRef.native_id)
        .where(MediaItemServerRef.item_id == item_id)
        .order_by(MediaItemServerRef.id.desc())
    )
    refs: dict[str, str] = {}
    for server, native_id in rows.all():
        refs.setdefault(server, native_id)
    return refs


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
            .order_by(MediaItemServerRef.id.desc())
        )
        for item_id, native_id in rows.all():
            result.setdefault(item_id, native_id)
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
            .order_by(MediaItemServerRef.id.desc())
        )
        for item_id, server, native_id in rows.all():
            result[item_id].setdefault(server, native_id)
    return result


async def native_id_by_external_ids(
    session: AsyncSession,
    server: str,
    *,
    kind: str,
    tmdb_id: int | None,
    tvdb_id: int | None,
    season_number: int | None,
    episode_number: int | None,
) -> str | None:
    """The stored ``server`` id of the ONE item these external ids name, or None.

    Perf workstream B3's webhook shortcut (``render/pipeline.process_item``).
    ``media_items.tmdb_id``/``tvdb_id`` are both indexed; the kind and the
    season/episode numbers narrow a show's id down to one season or episode.
    More than one matching item -- one movie filed in "Movies" and "4K Movies"
    -- answers None: which of them a webhook means is decided by the resolver's
    own section walk today, and a hint must not change that answer. Newest ref
    per item, the ``id DESC`` + ``setdefault`` rule the other readers here use.
    """
    id_matches = []
    if tmdb_id:
        id_matches.append(MediaItem.tmdb_id == tmdb_id)
    if tvdb_id:
        id_matches.append(MediaItem.tvdb_id == tvdb_id)
    if not id_matches:
        return None
    rows = await session.execute(
        select(MediaItemServerRef.item_id, MediaItemServerRef.native_id)
        .join(MediaItem, MediaItem.id == MediaItemServerRef.item_id)
        .where(
            MediaItemServerRef.server == server,
            MediaItem.kind == kind,
            or_(*id_matches),
            MediaItem.season_number.is_not_distinct_from(season_number),
            MediaItem.episode_number.is_not_distinct_from(episode_number),
        )
        .order_by(MediaItemServerRef.id.desc())
    )
    by_item: dict[int, str] = {}
    for item_id, native_id in rows.all():
        by_item.setdefault(item_id, native_id)
    if len(by_item) != 1:
        return None
    return next(iter(by_item.values()))
