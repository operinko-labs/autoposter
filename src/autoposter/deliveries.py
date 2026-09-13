"""Per-server delivery of a badged render (spec §5.2, §5.3).

``render_deliveries`` is the per-server truth; ``Render.upload_status``
(``rollup``, below) stays the roll-up so every existing query and dashboard
that reads it keeps working unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import MediaItem, Render, RenderDelivery
from autoposter.db.refs import refs_for
from autoposter.intake.arr import RenderIntent
from autoposter.servers.base import CAP_LOCK_ARTWORK, ItemNotFound, PathMismatch

logger = logging.getLogger(__name__)

# queue/jobs.py's DEFER_INTERVAL_SECONDS, the same horizon: §5.3 keeps the
# existing "no finite cap" stance for an unresolved server, just moved from
# the job to the delivery row.
RETRY_SECONDS = 6 * 60 * 60


def failure_detail(exc: Exception) -> str:
    """``category: ClassName``, never a URL (spec §6.1) -- this string is
    stored in ``render_deliveries.detail`` and shown to an operator, and an
    httpx exception's own message carries the server address.

    ``status``/``connect`` mirror ``api/version.py``'s ``_failure_reason``;
    unlike that function, anything else here falls back to ``error`` rather
    than ``parse`` -- nothing in this module ever parses a response body, so
    a third-category failure here is some other problem the server client's
    own code raised, not a decode failure.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"status: {type(exc).__name__} {exc.response.status_code}"
    if isinstance(exc, httpx.TransportError):
        return f"connect: {type(exc).__name__}"
    return f"error: {type(exc).__name__}"


async def record(
    session: AsyncSession, render_id: int, server: str, status: str, *,
    detail: str | None = None, retry_in: float | None = None,
) -> None:
    """Upsert this render's delivery row for ``server``.

    ``attempted_at`` is stamped on every call, ``skipped`` included: it is
    the "we last looked at this server" timestamp, not a success marker.
    """
    now = datetime.now(timezone.utc)
    values = dict(
        render_id=render_id, server=server, status=status, detail=detail,
        attempted_at=now,
        uploaded_at=now if status == "uploaded" else None,
        next_attempt_at=(now + timedelta(seconds=retry_in)) if status == "pending" else None,
    )
    stmt = insert(RenderDelivery).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_delivery_render_server",
        set_={k: v for k, v in values.items() if k not in ("render_id", "server")},
    )
    await session.execute(stmt)
    await session.flush()


async def rollup(session: AsyncSession, render_id: int) -> str:
    """Recompute ``Render.upload_status``/``uploaded_at`` from this render's
    deliveries (spec §5.2) and write both back.

    Precedence is ``failed`` over ``pending`` over ``uploaded`` over
    ``skipped``: a render that reached even one server is not "nothing to
    report" (``skipped``), and one server still failing is worth surfacing
    even while every other server that was tried already succeeded -- an
    operator who only sees the best outcome would never learn a server needs
    attention. ``pending`` outranks ``uploaded`` for the same reason in the
    other direction: a delivery still in flight means the render is not done
    yet, whatever has succeeded so far.
    """
    rows = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.uploaded_at)
        .where(RenderDelivery.render_id == render_id)
    )).all()
    statuses = {status for status, _ in rows}
    if "failed" in statuses:
        status = "failed"
    elif "pending" in statuses:
        status = "pending"
    elif "uploaded" in statuses:
        status = "uploaded"
    else:
        status = "skipped"
    uploaded_at = max((u for _, u in rows if u is not None), default=None)
    await session.execute(
        update(Render).where(Render.id == render_id)
        .values(upload_status=status, uploaded_at=uploaded_at)
    )
    await session.flush()
    return status


def _intent_for(item: MediaItem, refs: dict[str, str]) -> RenderIntent:
    """The intent a retry re-resolves with.

    Field for field what ``scheduler/merge.py``'s ``intent_for_row`` and
    ``scheduler/prune.py``'s ``intent_for`` build from their own row types --
    but there is no ``MergeRow`` or ``PruneCandidate`` here, only the
    persisted ``MediaItem`` the pending delivery's render joins to.
    """
    return RenderIntent(
        kind=item.kind, title=item.title, tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id,
        imdb_id=item.imdb_id, year=item.year, season_number=item.season_number,
        episode_number=item.episode_number, refs=refs,
    )


async def retry_pending_deliveries(
    session: AsyncSession, servers, config, *, http=None, mdblist=None, now: datetime | None = None,
) -> str:
    """The pending-delivery retry pass (spec §5.3): due rows only, each
    re-resolved on the ONE server it is still owed to.

    Only that server, never every server for the item: the other servers
    already have their own delivery row (uploaded, skipped or independently
    pending), and re-running them here would be a second, uncoordinated
    delivery pass racing the one the next webhook or full-pass item triggers.
    A pending row is that server's unfinished business alone.

    ``pipeline.compose_badged_bytes`` (Task 19) is imported lazily, inside
    the function: importing it at module load time would require the name
    to exist on ``autoposter.render.pipeline`` before Task 19 lands it there,
    and a top-level import in that direction risks a cycle once ``pipeline``
    calls back into this module.
    """
    from autoposter.render import pipeline as _pipeline
    from autoposter.render.pipeline import upsert_server_ref

    now = now or datetime.now(timezone.utc)
    due = (await session.execute(
        select(RenderDelivery, Render, MediaItem)
        .join(Render, Render.id == RenderDelivery.render_id)
        .join(MediaItem, MediaItem.id == Render.item_id)
        .where(RenderDelivery.status == "pending", RenderDelivery.next_attempt_at <= now)
        .order_by(RenderDelivery.next_attempt_at)
        .limit(500)
    )).all()

    uploaded = still_pending = 0
    for delivery, render, item in due:
        server = servers.get(delivery.server)
        if server is None:
            # The config that named this server is gone (an operator removed
            # the block); no amount of retrying resolves that, unlike an
            # ItemNotFound or a transport hiccup.
            await record(session, render.id, delivery.server, "failed", detail="config: server removed")
            await rollup(session, render.id)
            continue

        if not getattr(config.badges, f"upload_to_{delivery.server}", False):
            await record(session, render.id, delivery.server, "skipped")
            await rollup(session, render.id)
            continue

        refs = await refs_for(session, item.id)
        try:
            resolved_item = await server.resolve(_intent_for(item, refs))
        except PathMismatch as exc:
            # A path-mapping mismatch that no retry fixes (spec §6.2).
            await record(session, render.id, delivery.server, "failed", detail=f"{type(exc).__name__}: {exc}")
            await rollup(session, render.id)
            continue
        except ItemNotFound:
            await record(session, render.id, delivery.server, "pending", retry_in=RETRY_SECONDS)
            still_pending += 1
            await rollup(session, render.id)
            continue
        except Exception as exc:
            logger.warning("delivery to %s failed to resolve (%s)", delivery.server, failure_detail(exc))
            await record(
                session, render.id, delivery.server, "pending",
                detail=failure_detail(exc), retry_in=RETRY_SECONDS,
            )
            still_pending += 1
            await rollup(session, render.id)
            continue

        await upsert_server_ref(session, item.id, resolved_item)

        try:
            data = await _pipeline.compose_badged_bytes(session, config, render, item, http=http, mdblist=mdblist)
            await server.upload_artwork(
                resolved_item.ref, data, render.art_kind,
                config.badges.lock_artwork and CAP_LOCK_ARTWORK in server.capabilities,
            )
        except Exception as exc:
            logger.warning("delivery to %s failed (%s)", delivery.server, failure_detail(exc))
            await record(
                session, render.id, delivery.server, "pending",
                detail=failure_detail(exc), retry_in=RETRY_SECONDS,
            )
            still_pending += 1
            await rollup(session, render.id)
            continue

        await record(session, render.id, delivery.server, "uploaded")
        uploaded += 1
        await rollup(session, render.id)

    await session.commit()
    return f"pending deliveries: {len(due)} due, {uploaded} uploaded, {still_pending} still pending"
