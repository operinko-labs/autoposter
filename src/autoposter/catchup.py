"""The catch-up run (spec §3): make one server match what this service
already has.

It is one pass over the DATABASE, not over the servers. Nothing is rendered
and nothing is fetched from a provider: a catch-up delivers and writes what
exists, which is the whole reason it can re-arm fifteen thousand items in one
statement rather than queueing fifteen thousand jobs. Items this service has
never rendered are left to the full pass, the only thing that composes new
artwork.

The retry pass (``deliveries.retry_pending_deliveries``) drains the backlog a
catch-up marks, scoped by ``run_id``, so a catch-up needs no worker of its
own and cannot race the ordinary pipeline: both write through the same two
tables and the same per-row savepoint.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, case, literal, null, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import (
    ItemFacts, ItemMetadataOverride, MediaItem, MetadataWrite, Render, RenderDelivery, Run,
)
from autoposter.scheduler.run_history import open_run
from autoposter.servers.presence import (
    apply_presence, present_libraries, rollup_stamped_renders,
)

logger = logging.getLogger(__name__)

# The `runs.kind` a catch-up carries. Beside `scheduled` and `full_pass`, and
# distinct from both: a catch-up is neither a scheduled job's pass nor a
# whole-library render, and the runs list groups by this.
CATCH_UP_KIND = "catch_up"

# The floor a per-run cadence is held to, matching every other cadence in
# this project: the scheduler polls at its own interval and a sub-minute
# request would only mean "every poll" while reading like something finer.
MIN_CADENCE_SECONDS = 60


class CatchUpRefused(Exception):
    """Why a catch-up cannot start right now, in one sentence the button shows.

    Row 213: the sentence is built from fixed words plus a server NAME and an
    exception CLASS name. Never an address, never a credential, never an
    exception's own message.
    """


def _run_name(server_name: str) -> str:
    """The `runs.name` a catch-up for one server carries.

    Per server, not one shared name: ``trim_run_history`` keeps the newest
    500 rows PER NAME, so a shared name would let a busy server's catch-ups
    evict a quiet one's history.
    """
    return f"{CATCH_UP_KIND}:{server_name}"


async def start_catch_up(
    session: AsyncSession, servers, config, name: str, *,
    health: dict | None = None, cadence_seconds: int | None = None,
    now: datetime | None = None,
) -> int:
    """Open a catch-up run for ``name`` and mark its backlog. Returns the run id.

    Refuses -- with the sentence the button shows -- when the server is not
    configured, when a catch-up for it is already in flight, when its health
    poller says it is down, or when it cannot list its libraries. The last of
    those is a refusal rather than a silent empty presence on purpose: a
    server that cannot answer must never be told it carries nothing.

    ``health`` is ``app.state.server_health`` -- the poller map, not the
    registry, which deliberately does not carry health (``servers/registry.py``).
    It defaults to ``None`` so a direct caller need not have one, which means
    an endpoint that forgets to pass it loses that refusal silently: every
    wiring site must pass ``health=app.state.server_health``.

    Does not commit. The caller owns the transaction, so the run row, the
    presence stamps and the marking either all land or none do.
    """
    server = servers.get(name)
    if server is None:
        raise CatchUpRefused(f"no media server named {name!r} is configured")

    poller = (health or {}).get(name)
    if poller is not None and not poller.healthy:
        raise CatchUpRefused(f"{name} is not reachable right now; try again once it is back")

    # BEFORE the in-flight check, and so before this transaction has read or
    # written anything: this is a network round trip against a media server
    # and holding a pooled connection idle-in-transaction for the whole of a
    # hung server's timeout is what `presence.read_presence` exists to avoid.
    # It costs one library listing on a start that is then refused, which is
    # the cheaper half of the trade.
    try:
        present = await present_libraries(server)
    except Exception as exc:
        raise CatchUpRefused(
            f"{name} could not list its libraries ({type(exc).__name__})"
        ) from exc

    # The in-flight check and the `open_run` INSERT below are one READ
    # COMMITTED transaction and nothing in the schema forbids two open runs
    # for one server, so without this both halves of a double-clicked button
    # -- or the button racing the automatic post-save start (spec §3) -- pass
    # the check and open a run each, and the second re-marks the first's rows
    # out from under it. The lock is held to the end of THIS transaction and
    # is keyed per server, so two servers' catch-ups still start in parallel.
    # An advisory lock rather than a partial unique index: no migration, and
    # it serialises the whole start rather than only the INSERT.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _run_name(name)},
    )
    in_flight = (await session.execute(
        select(Run.id).where(
            Run.kind == CATCH_UP_KIND, Run.server == name, Run.finished_at.is_(None)
        )
    )).scalars().first()
    if in_flight is not None:
        raise CatchUpRefused(f"a catch-up for {name} is already in flight")

    now = now or datetime.now(timezone.utc)
    carried = sorted(present)
    if carried:
        # Step 1 (spec §3): presence first, so a library that has gone is
        # stamped `absent` before anything is marked due, and one that has
        # reappeared is re-armed and therefore included below.
        await apply_presence(session, name, present, now=now)
    else:
        # `read_presence`'s rule, which this function has to keep rather than
        # rediscover: an EMPTY-but-successful answer is a Jellyfin still
        # starting up, an API key without library scope, or an
        # `excluded_libraries` that happens to name every folder. Stamping on
        # it would mark every item and every render absent on that server in
        # two statements, overwriting each row's `detail`, `attempts` and
        # `next_attempt_at` with no record of what they were -- and it would
        # take a second catch-up, once the server had woken up, to undo.
        logger.warning(
            "%s listed no libraries; its catch-up stamps nothing and marks nothing", name,
        )

    # `is None`, not `or`: an explicit `0` is an operator asking for the
    # fastest drain there is, which the floor below answers with 60 -- `or`
    # would have read it as "unset" and silently used the scheduler's quarter
    # of an hour instead.
    cadence = (
        config.scheduler.pending_deliveries_minutes * 60
        if cadence_seconds is None else cadence_seconds
    )
    run_id = await open_run(session, kind=CATCH_UP_KIND, name=_run_name(name))
    await session.execute(
        update(Run).where(Run.id == run_id).values(
            server=name, cadence_seconds=max(MIN_CADENCE_SECONDS, int(cadence)),
        )
    )

    if not carried:
        # Nothing is carried, so nothing is due. The run still exists and
        # closes on the drain job's first look, which is what gives the
        # operator a row saying so rather than a button that did nothing.
        return run_id

    # Every gate below is read off the GLOBAL config, never
    # `config_for_library`: a catch-up is one statement per table over every
    # carried library at once, and a per-library toggle would turn each into
    # one statement per library. Out of scope deliberately -- the retry pass
    # that drains these rows re-reads the per-library config per row
    # (`deliveries.retry_pending_deliveries`) and records `skipped` for a row
    # a library override turns off, so an override is still honoured, one
    # pass later, on the rows it actually covers.
    operations = config.operations
    writes_metadata = operations.enabled and getattr(
        operations, f"write_to_{name}", False
    )
    if writes_metadata:
        # Step 2, metadata: every item in a carried library is re-written. A
        # metadata write is cheap, idempotent, and planned against what the
        # server currently holds by the writer itself, so there is no "already
        # correct" shortcut worth the read it would cost.
        #
        # The ONE exception is an item this service has nothing to say about,
        # and the predicate MIRRORS `apply_metadata`'s own gate term for term
        # (`pipeline.py`: `not facts.is_empty() or has_verbs or has_parental
        # or has_overrides`) rather than approximating it. Two of those terms
        # are per-item -- an `item_facts` row, an `item_metadata_override`
        # row -- and two are CONFIG-LEVEL: a configured field verb or an
        # enabled parental-label fetch makes every item writable, because a
        # verb IS its field's source and must fire even when no provider has
        # anything to say. Getting that wrong under-arms exactly the items
        # `persist_facts` writes no row for -- most commonly seasons -- so
        # with verbs on, every season would be written by every full pass and
        # armed by no catch-up, silently.
        #
        # A row for an item none of the terms cover is left exactly as it is:
        # not created, and not moved off the true word it already carries.
        writes_every_item = bool(operations.field_verbs) or operations.parental_labels_enabled
        has_something_to_write = true() if writes_every_item else or_(
            MediaItem.id.in_(select(ItemFacts.item_id)),
            MediaItem.id.in_(select(ItemMetadataOverride.item_id)),
        )
        writable = select(MediaItem.id).where(
            MediaItem.library.in_(carried), has_something_to_write
        )
        # `skipped` joins `absent` outside the re-arm, unlike a `written` row:
        # a skipped row is an EXEMPTION (row 35) or a switched-off write, and
        # re-arming it costs a resolve, a label read and a plan per exempt item
        # to record the same word again -- while blanking the stored reason the
        # item page shows in the meantime. An exemption that is LIFTED is
        # re-armed by the full pass, which re-evaluates the labels; a catch-up
        # is not the thing that learns that.
        #
        # UPDATE first, then INSERT ... DO NOTHING: the update must not see the
        # rows the insert is about to create, or every fresh row would record a
        # `previous_status` of `pending` and a cancel would leave it behind.
        await session.execute(
            update(MetadataWrite)
            .where(
                MetadataWrite.server == name,
                MetadataWrite.status.notin_(("absent", "skipped")),
                MetadataWrite.item_id.in_(writable),
            )
            .values(
                status="pending",
                # PostgreSQL evaluates every SET expression against the row as
                # it was, so this is the OLD status -- which is exactly what a
                # cancel has to put back. NULL is not "no previous status" but
                # "a run CREATED this row" (`db/models.py`), and a cancel
                # deletes such a row rather than inventing a word for it, so a
                # second run marking a row the first one created must keep the
                # NULL rather than write `pending` over it.
                previous_status=case(
                    (
                        and_(
                            MetadataWrite.run_id.isnot(None),
                            MetadataWrite.previous_status.is_(None),
                        ),
                        null(),
                    ),
                    else_=MetadataWrite.status,
                ),
                detail=None, attempted_at=now, next_attempt_at=now, attempts=0,
                run_id=run_id,
            )
        )
        await session.execute(
            insert(MetadataWrite)
            .from_select(
                ["item_id", "server", "status", "attempted_at", "next_attempt_at", "run_id"],
                select(
                    MediaItem.id, literal(name), literal("pending"),
                    literal(now), literal(now), literal(run_id),
                ).where(MediaItem.library.in_(carried), has_something_to_write),
            )
            .on_conflict_do_nothing(constraint="uq_metadata_write_item_server")
        )

    if not (config.badges.enabled and getattr(config.badges, f"upload_to_{name}", False)):
        # `deliver`'s own rule, which a catch-up must not break: "an
        # upload-disabled server must never get a `pending` catch-up row, only
        # for the very next retry pass to immediately overwrite it with
        # `skipped`" (`pipeline.py`). `upload_to_<name>` defaults to OFF, and
        # spec §3 starts a catch-up automatically after a restart that
        # introduced a server -- so without this, the first catch-up a new
        # Jellyfin ever gets arms the whole library for a server it may not
        # upload to, and the drain spends every batch writing `skipped`.
        return run_id

    # Step 2, artwork: only the rows that are BEHIND. A row this server has
    # already uploaded at the render's current badge fingerprint is serving
    # exactly the bytes a catch-up would send, so it is left alone -- which
    # is what keeps a catch-up over a settled library nearly free.
    #
    # `art_kind != "background"` is `deliver`'s own first gate, repeated
    # here: a background render is never delivered to any server, so a
    # `pending` row for one would be a due row no pass can ever settle.
    rendered = and_(Render.status == "rendered", Render.art_kind != "background")
    behind = (
        select(RenderDelivery.id)
        .join(Render, Render.id == RenderDelivery.render_id)
        .join(MediaItem, MediaItem.id == Render.item_id)
        .where(
            RenderDelivery.server == name,
            MediaItem.library.in_(carried),
            rendered,
            or_(
                RenderDelivery.status.in_(("pending", "failed")),
                and_(
                    RenderDelivery.status == "uploaded",
                    # IS DISTINCT FROM, not `!=`: a NULL fingerprint is a row
                    # uploaded before that column existed, and "we do not know
                    # what it is serving" is behind, not equal.
                    RenderDelivery.fingerprint.is_distinct_from(Render.badge_fingerprint),
                ),
            ),
        )
    )
    await session.execute(
        update(RenderDelivery)
        .where(RenderDelivery.id.in_(behind))
        .values(
            status="pending",
            # The metadata UPDATE's `case` and its reason, for this table.
            previous_status=case(
                (
                    and_(
                        RenderDelivery.run_id.isnot(None),
                        RenderDelivery.previous_status.is_(None),
                    ),
                    null(),
                ),
                else_=RenderDelivery.status,
            ),
            detail=None, attempted_at=now, next_attempt_at=now, attempts=0, run_id=run_id,
        )
    )
    await session.execute(
        insert(RenderDelivery)
        .from_select(
            ["render_id", "server", "status", "attempted_at", "next_attempt_at", "run_id"],
            select(
                Render.id, literal(name), literal("pending"),
                literal(now), literal(now), literal(run_id),
            )
            .join(MediaItem, MediaItem.id == Render.item_id)
            .where(MediaItem.library.in_(carried), rendered),
        )
        .on_conflict_do_nothing(constraint="uq_delivery_render_server")
    )

    # The roll-up `apply_presence` owes for the same reason, over the rows
    # THIS function just stamped: `renders.upload_status` is what
    # `/api/library`'s filter, the dashboard tiles and the action centre read,
    # and nothing else recomputes what a set-shaped write to
    # `render_deliveries` touched. Without it a `failed` row a catch-up moved
    # to `pending` leaves the render reading `failed` -- flagged in the action
    # centre and served stale on the item page -- for the whole drain, which
    # for 15k rows in batches of 500 is hours, because only the retry pass's
    # per-row `rollup` ever corrects it. The selector matches on this
    # server's `pending` rows stamped with exactly this `now`, which is what
    # the two statements above wrote.
    await rollup_stamped_renders(session, name, now)
    return run_id
