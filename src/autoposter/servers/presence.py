"""Which libraries a server carries, and the `absent` rule (spec §1).

`absent` is decided ONCE per library and server, not per item attempt: a
library a server does not carry will never hold the item, so an item there
is never resolved, never retried, and shown as one neutral line. Presence is
recomputed at the start of every full pass and every catch-up, so a library
that appears later flips its items back to `pending` and they flow through
the ordinary retry.

Set-shaped SQL rather than a loop over `deliveries.record`: a library holds
fifteen thousand items on the deployment this was built for, and stamping
them one upsert at a time would make a full pass's opening a minute of round
trips.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import literal, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import MediaItem, MetadataWrite, Render, RenderDelivery

logger = logging.getLogger(__name__)

# The stored reason. Fixed words, no server address and no library list:
# `detail` is a served string (spec §1, "never a URL").
ABSENT_DETAIL = "library: not carried by this server"

# What `absent` must never overwrite: a server that HAS the item's metadata,
# or HAS its artwork, is telling the truth about itself, and a stale library
# map must not turn that into "not carried".
_KEEP_METADATA = ("written", "absent")
_KEEP_ARTWORK = ("uploaded", "absent")


async def present_libraries(server) -> set[str]:
    """The libraries ``server`` carries, in the configuration's name space."""
    return await server.library_names()


async def apply_presence(
    session: AsyncSession, server_name: str, present: set[str], *, now: datetime | None = None,
) -> dict:
    """Stamp `absent` where a library is not carried, re-arm where one is.

    Returns ``{"absent": rows stamped, "rearmed": rows re-armed}`` counted
    across BOTH tables. Does not commit -- the caller (a full pass's opening,
    a catch-up's step 1) owns the transaction.
    """
    now = now or datetime.now(timezone.utc)
    carried = sorted(present)
    not_carried = MediaItem.library.notin_(carried) if carried else true()

    absent = 0
    absent += (await session.execute(
        insert(MetadataWrite)
        .from_select(
            ["item_id", "server", "status", "detail", "attempted_at"],
            select(
                MediaItem.id, literal(server_name), literal("absent"),
                literal(ABSENT_DETAIL), literal(now),
            ).where(not_carried),
        )
        .on_conflict_do_update(
            constraint="uq_metadata_write_item_server",
            set_={
                "status": "absent", "detail": ABSENT_DETAIL,
                "attempted_at": now, "next_attempt_at": None, "attempts": 0,
            },
            # Spec §1's first-pass reclassification lives in this WHERE: a
            # `pending` row for a library that is in fact absent becomes
            # `absent` here, which is what clears the retry queue a
            # mismatched map leaves behind today.
            where=MetadataWrite.status.notin_(_KEEP_METADATA),
        )
    )).rowcount
    absent += (await session.execute(
        insert(RenderDelivery)
        .from_select(
            ["render_id", "server", "status", "detail", "attempted_at"],
            select(
                Render.id, literal(server_name), literal("absent"),
                literal(ABSENT_DETAIL), literal(now),
            )
            .join(MediaItem, MediaItem.id == Render.item_id)
            .where(not_carried),
        )
        .on_conflict_do_update(
            constraint="uq_delivery_render_server",
            set_={
                "status": "absent", "detail": ABSENT_DETAIL,
                "attempted_at": now, "next_attempt_at": None, "attempts": 0,
            },
            where=RenderDelivery.status.notin_(_KEEP_ARTWORK),
        )
    )).rowcount

    rearmed = 0
    if carried:
        rearmed += (await session.execute(
            update(MetadataWrite)
            .where(
                MetadataWrite.server == server_name,
                MetadataWrite.status == "absent",
                MetadataWrite.item_id.in_(
                    select(MediaItem.id).where(MediaItem.library.in_(carried))
                ),
            )
            .values(status="pending", detail=None, next_attempt_at=now, attempts=0)
        )).rowcount
        rearmed += (await session.execute(
            update(RenderDelivery)
            .where(
                RenderDelivery.server == server_name,
                RenderDelivery.status == "absent",
                RenderDelivery.render_id.in_(
                    select(Render.id)
                    .join(MediaItem, MediaItem.id == Render.item_id)
                    .where(MediaItem.library.in_(carried))
                ),
            )
            .values(status="pending", detail=None, next_attempt_at=now, attempts=0)
        )).rowcount

    return {"absent": absent, "rearmed": rearmed}


async def refresh_presence(session: AsyncSession, servers) -> dict[str, dict]:
    """``apply_presence`` for every configured server.

    A server that cannot list its libraries right now contributes NO entry
    rather than an empty ``present`` set: an unreachable server would
    otherwise mark the entire library absent on it, which is the opposite of
    the truth and would take a catch-up to undo.
    """
    outcomes: dict[str, dict] = {}
    for name, server in servers.items():
        try:
            present = await present_libraries(server)
        except Exception as exc:
            logger.warning(
                "could not read %s's library list; leaving presence unchanged (%s)",
                name, type(exc).__name__,
            )
            continue
        outcomes[name] = await apply_presence(session, name, present)
    return outcomes
