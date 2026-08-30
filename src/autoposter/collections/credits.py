"""The library-wide credit scan and its counted enumeration (rows 197/194).

The scan is whole-library per run, NOT drift-paced, and that is an argument
rather than an oversight: Plex is local and unmetered, the currency is round
trips (row 197's explicit non-inheritance from Phase A's rate budget), and
the batched read makes a full movie-library scan ceil(1962/200) = 10
requests (~17s, measured). What IS inherited from Phase A in shape: the
attempt stamped whether or not it found anything, and a refusal that never
enters the cache.

The enumeration is ``facts_enumeration.enumerate_values``' GROUP BY shape --
it returns (value, count), which is the depth/limit semantics row 194 needs
and which no ``listFilterChoices`` call can answer (values, never counts).

**No count here is a complete cast, and none may ever be read as one.** The
phase-B probe measured a
server-side cap of **200 ``Role`` children per item**
(docs/research/plex-batch-probe/README.md, D1) -- 54 of 200 sampled shows and
2 of 200 movies return exactly 200 and none more, and a single-key fetch of
the same item returns the same 200, so the truncation cannot be read around.
Every number this module produces is therefore over the credits Plex
RETURNED: a person left out of a truncated cast is undercounted, and is
indistinguishable from a person that item never credited. Shows are empty of
director/writer/producer for a different and complete reason -- series-level
Plex metadata carries none at all -- so the scan stores what exists and
synthesises nothing.
"""
import asyncio
import logging

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemCredit, MediaItem
from autoposter.plex.client import TAG_BATCH_CHUNK, fetch_credit_index

logger = logging.getLogger(__name__)

__all__ = [
    "CREDIT_KINDS",
    "credits_coverage",
    "enumerate_credits",
    "scan_credits",
    "scan_library_credits",
]

# credit kind -> the CreditTags field carrying it. ONE source of truth: the
# public tuple is derived from it rather than repeated beside it.
_FIELD_FOR_KIND = {
    "actor": "actors",
    "director": "directors",
    "writer": "writers",
    "producer": "producers",
}

CREDIT_KINDS = tuple(_FIELD_FOR_KIND)

# Library-type spelling -> media_items.kind rows, facts_enumeration's own map:
# a Show library's credits are the SHOWS' (episode credits are not scanned).
_KINDS = {"Movie": ("movie",), "Show": ("show",)}

# Plex section.type -> our library-type spelling (collections/service.py's
# LIBRARY_TYPES, restated here to keep this module import-light).
_LIBRARY_TYPES = {"movie": "Movie", "show": "Show"}


async def scan_library_credits(
    session: AsyncSession, section, library: str, library_type: str,
    chunk_size: int = TAG_BATCH_CHUNK,
) -> tuple[int, int]:
    """Scan one section. Returns (items stamped, credit rows written).

    Per answered item: its rows are REPLACED (delete + insert -- a person
    Plex no longer credits does not linger) and the attempt is stamped.
    An item the batch did not answer for is untouched: no stamp, no rows,
    so it stays "unvisited" and honest (refusals never cached).

    The insert is a Core ``executemany`` rather than ``session.add`` per row:
    one round trip for an item whose cast can run to the 200-Role cap, and no
    ORM identity-map interaction at all -- a rescan re-inserting the same
    (item, kind, person) key must not depend on the DELETE above having
    evicted the previous pass's persistent instance from the session.
    """
    rows = (
        await session.execute(
            select(MediaItem.id, MediaItem.rating_key).where(
                MediaItem.library == library,
                MediaItem.kind.in_(_KINDS.get(library_type, ())),
            )
        )
    ).all()
    if not rows:
        return (0, 0)
    by_key = {str(rating_key): item_id for item_id, rating_key in rows}
    fetched = await asyncio.to_thread(
        fetch_credit_index, section, list(by_key), chunk_size
    )
    answered_ids = []
    values = []
    for rating_key, credits in fetched.items():
        item_id = by_key.get(rating_key)
        if item_id is None:
            continue
        answered_ids.append(item_id)
        for kind, field in _FIELD_FOR_KIND.items():
            for person in getattr(credits, field):
                values.append({"item_id": item_id, "kind": kind, "person": person})
    if not answered_ids:
        return (0, 0)
    await session.execute(
        delete(ItemCredit).where(ItemCredit.item_id.in_(answered_ids))
    )
    if values:
        await session.execute(insert(ItemCredit), values)
    await session.execute(
        update(MediaItem)
        .where(MediaItem.id.in_(answered_ids))
        .values(credits_attempted_at=func.now())
    )
    return (len(answered_ids), len(values))


async def scan_credits(session: AsyncSession, server, config) -> str:
    """Every configured collections library, one summary string for the job
    record. A library that fails is logged and reported, never fatal to the
    others -- ``service.reconcile_libraries``' own containment shape.

    The credit count in the summary is what Plex returned, not a cast size:
    see this module's docstring on the 200-Role cap.
    """
    parts = []
    for name in config.collections.libraries:
        try:
            section = await asyncio.to_thread(server.library.section, name)
            library_type = _LIBRARY_TYPES.get(section.type)
            if library_type is None:
                parts.append("%s: skipped (unsupported type)" % name)
                continue
            stamped, written = await scan_library_credits(
                session, section, name, library_type
            )
            await session.commit()
            parts.append("%s: %d item(s) scanned, %d credit(s)" % (name, stamped, written))
        except Exception as error:  # class name only -- messages can quote URLs
            logger.exception("credits scan failed for %r", name)
            await session.rollback()
            parts.append("%s: failed (%s)" % (name, type(error).__name__))
    return "; ".join(parts) or "no collections libraries configured"


async def enumerate_credits(
    session: AsyncSession, kind: str, *, library: str, library_type: str
) -> list[tuple[str, int]]:
    """``(person, item_count)`` for one credit kind, most-appearances first,
    ties on the person ascending -- the total order ``facts_enumeration``
    keeps, for the same reason (stable family creation between passes).

    The count is over the items whose credits Plex RETURNED. It is a floor,
    not a cast census: an item truncated at the 200-Role cap (probe D1)
    undercounts everyone Plex left out, and an item with no row at all is
    ABSENT here rather than a zero.

    That bias is DIRECTIONAL, and the consequence matters wherever these pairs
    become a ranking: a person whose every appearance is in a >200-role cast is
    ABSENT from this list entirely -- not merely ranked low -- so a caller
    taking the most-credited N is taking the most-credited N OF WHAT PLEX
    ANSWERED, which is not the same claim and must not be published as one.
    """
    stmt = (
        select(ItemCredit.person, func.count())
        .select_from(ItemCredit)
        .join(MediaItem, MediaItem.id == ItemCredit.item_id)
        .where(
            ItemCredit.kind == kind,
            MediaItem.library == library,
            MediaItem.kind.in_(_KINDS.get(library_type, ())),
        )
        .group_by(ItemCredit.person)
        .order_by(func.count().desc(), ItemCredit.person.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [(str(person), int(count)) for person, count in rows if str(person)]


async def credits_coverage(
    session: AsyncSession, *, library: str, library_type: str
) -> tuple[int, int]:
    """``(items whose credits have been attempted, items in the library)`` --
    ``facts_enumeration.coverage``'s twin, over the credits stamp.

    "Attempted" is the honest word twice over. It counts items the scan
    reached, not items with credits: found-no-credits is a visit. And a
    visited item's cast may still be truncated at the 200-Role cap (probe
    D1), so full coverage here is not a claim that the credit rows are
    complete -- only that every item was asked.
    """
    kinds = _KINDS.get(library_type, ())
    base = select(func.count()).select_from(MediaItem).where(
        MediaItem.library == library, MediaItem.kind.in_(kinds)
    )
    total = (await session.execute(base)).scalar_one()
    attempted = (
        await session.execute(base.where(MediaItem.credits_attempted_at.is_not(None)))
    ).scalar_one()
    return int(attempted), int(total)
