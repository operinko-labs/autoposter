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

from sqlalchemy import and_, case, func, literal, or_, select, true, update
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

# The identity server: the one items were ingested from, and whose library
# names every other server's are mapped into. Spelled the same way
# `deliveries.retry_pending_deliveries` spells it.
IDENTITY_SERVER = "plex"


async def present_libraries(server) -> set[str]:
    """The libraries ``server`` carries, in the configuration's name space."""
    return await server.library_names()


async def apply_presence(
    session: AsyncSession, server_name: str, present: set[str], *, now: datetime | None = None,
) -> dict:
    """Stamp `absent` where a library is not carried, re-arm where one is.

    Returns the counts PER TABLE --
    ``{"metadata": {"absent": n, "rearmed": n}, "artwork": {"absent": n, "rearmed": n}}``
    -- because the two are different units and summing them is not a number
    anyone can read: one item with two renders contributed 3 to a single
    total, so a 3000-item library answered `9000` and the operator-facing
    sentence built on it (spec §5) said nothing true. ``metadata`` counts
    ITEMS, ``artwork`` counts RENDERS. Does not commit -- the caller (a full
    pass's opening, a catch-up's step 1) owns the transaction.

    Applied to the IDENTITY SERVER too, by decision. "Not carried"
    is a fact about a server, and Plex is a server: a section renamed after
    ingest, one added to ``excluded_libraries`` later, or one since retyped
    genuinely no longer holds the item under the name the row records, and
    spec §1's rule is what should then apply. It is still the one case where
    "Plex-only deployments see no behaviour change beyond recorded rows" could
    stop being true, so it is logged at WARNING with the counts and the
    library names -- once per pass, since this function runs once per server
    per pass -- rather than only showing up as a silent stop.
    """
    now = now or datetime.now(timezone.utc)
    carried = sorted(present)
    not_carried = MediaItem.library.notin_(carried) if carried else true()

    metadata = {"absent": 0, "rearmed": 0}
    artwork = {"absent": 0, "rearmed": 0}
    metadata["absent"] = (await session.execute(
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
    artwork["absent"] = (await session.execute(
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

    if carried:
        metadata["rearmed"] = (await session.execute(
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
        artwork["rearmed"] = (await session.execute(
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

    if artwork["absent"] or artwork["rearmed"]:
        await rollup_stamped_renders(session, server_name, now)

    if server_name == IDENTITY_SERVER and (metadata["absent"] or artwork["absent"]):
        # Library NAMES, which are the operator's own words for his own
        # sections -- never an address (spec §1's "never a URL" holds for what
        # is logged as much as for what is stored).
        uncarried = sorted((await session.execute(
            select(MediaItem.library).where(not_carried).distinct()
        )).scalars())
        logger.warning(
            "%s is the identity server and now reports %d item(s) and %d render(s) "
            "absent, in: %s -- nothing is written to or delivered on those",
            server_name, metadata["absent"], artwork["absent"], ", ".join(uncarried),
        )

    return {"metadata": metadata, "artwork": artwork}


async def rollup_stamped_renders(
    session: AsyncSession, server_name: str | None = None, now: datetime | None = None,
    *, render_ids: list[int] | None = None,
) -> int:
    """``deliveries.rollup``'s precedence ladder, set-shaped, over the renders
    this call just restamped.

    PUBLIC, and named rather than private, because ``apply_presence`` is not
    its only caller: ``catchup.start_catch_up`` writes ``render_deliveries``
    set-shaped too, with the same ``next_attempt_at = now`` stamp, and owes
    the roll-up for exactly the same reason. The selector below is what makes
    one function serve both -- it names the rows by the timestamp, not by who
    wrote them.

    ``render_ids`` is the OTHER entry, for a caller whose rows carry no such
    stamp: ``catchup.cancel_catch_up`` puts each restored row back on the
    horizon it had before the catch-up and DELETES the rows its run created,
    so nothing is left to name them by except the render ids it collected
    before it wrote. The ladder and the write below are the same either way
    -- only the choice of rows differs, which is what makes this one function
    rather than two copies of a precedence ladder. A render in that set whose
    delivery rows were all deleted has nothing left to aggregate and keeps
    the status it carries: no rows at all is exactly the state it was in
    before the catch-up created one, so recomputing it would be the change
    rather than the correction.

    ``apply_presence`` writes ``render_deliveries`` directly and nothing else
    recomputes what it touched, so a render rolled up ``pending`` because of a
    Jellyfin row kept ``renders.upload_status = 'pending'`` after that row
    became ``absent`` -- and that column is what ``/api/library``'s filter and
    the dashboard tiles read. Spec §1's promise that the first pass "clears
    the retry queue a mismatched map leaves behind" was true of the queue and
    false of everything the operator can see.

    The whole ladder, not a blanket ``skipped``: a render whose Plex row is
    ``uploaded`` and whose Jellyfin row just went absent IS uploaded, and
    writing ``skipped`` over it would be a second wrong answer for the same
    column. One statement rather than a ``deliveries.rollup`` call per render,
    for the same reason the stamps above are set-shaped -- fifteen thousand
    renders. The rows this call touched are named by the timestamp it stamped
    them with (``attempted_at`` on an absent stamp, ``next_attempt_at`` on a
    re-arm), so no other server's renders are recomputed.

    ``renders.uploaded_at`` is deliberately left alone: presence never erases
    a delivered timestamp, so the maximum ``rollup`` carries forward has not
    moved.
    """
    ladder = case(
        (func.bool_or(RenderDelivery.status == "failed"), "failed"),
        (func.bool_or(RenderDelivery.status == "pending"), "pending"),
        (func.bool_or(RenderDelivery.status == "uploaded"), "uploaded"),
        else_="skipped",
    )
    if render_ids is not None:
        if not render_ids:
            return 0
        chosen = RenderDelivery.render_id.in_(render_ids)
    else:
        chosen = RenderDelivery.render_id.in_(
            select(RenderDelivery.render_id).where(
                RenderDelivery.server == server_name,
                or_(
                    and_(
                        RenderDelivery.status == "absent",
                        RenderDelivery.attempted_at == now,
                    ),
                    and_(
                        RenderDelivery.status == "pending",
                        RenderDelivery.next_attempt_at == now,
                    ),
                ),
            )
        )
    recomputed = (
        select(RenderDelivery.render_id.label("render_id"), ladder.label("status"))
        .where(chosen)
        .group_by(RenderDelivery.render_id)
        .subquery()
    )
    return (await session.execute(
        update(Render)
        .where(
            Render.id == recomputed.c.render_id,
            Render.upload_status.is_distinct_from(recomputed.c.status),
        )
        .values(upload_status=recomputed.c.status)
    )).rowcount


async def read_presence(servers) -> dict[str, set[str]]:
    """Ask every configured server which libraries it carries. No database.

    Separate from ``refresh_presence`` below so a caller can do the ASKING
    before it opens its transaction: each answer is a network round trip
    against a media server, and a full pass that asked inside its transaction
    held one open and idle for the whole of it.

    A server that cannot list its libraries right now contributes NO entry
    rather than an empty ``present`` set: an unreachable server would
    otherwise mark the entire library absent on it, which is the opposite of
    the truth and would take a catch-up to undo.

    An EMPTY-but-successful answer is treated the same way, and for the same
    reason: a Jellyfin still starting up, an API key without library scope,
    or an `excluded_libraries` that happens to name every folder all return
    cleanly with nothing in them, and stamping on that answer would mark
    every item and every render absent on that server in two statements --
    overwriting each row's `detail`, `attempts` and `next_attempt_at` with
    no record of what they were. A server that carries none of the libraries
    this deployment manages has nothing here to say anything about anyway,
    so skipping it is both safe and honest.
    """
    present: dict[str, set[str]] = {}
    for name, server in servers.items():
        try:
            libraries = await present_libraries(server)
        except Exception as exc:
            logger.warning(
                "could not read %s's library list; leaving presence unchanged (%s)",
                name, type(exc).__name__,
            )
            continue
        if not libraries:
            logger.warning("%s listed no libraries; leaving presence unchanged", name)
            continue
        present[name] = libraries
    return present


async def refresh_presence(
    session: AsyncSession, present: dict[str, set[str]],
) -> dict[str, dict]:
    """``apply_presence`` for every server ``read_presence`` got an answer
    from, in the caller's own transaction."""
    return {
        name: await apply_presence(session, name, libraries)
        for name, libraries in present.items()
    }
