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
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    and_, case, delete, func, literal, or_, select, text, true, update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import (
    ItemFacts, ItemMetadataOverride, MediaItem, MetadataWrite, Render, RenderDelivery, Run,
)
from autoposter.deliveries import RETRY_SECONDS, retry_pending_deliveries
from autoposter.scheduler.run_history import close_run, open_run
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

# How many CONSECUTIVE batches may move nothing before the drain stops the
# run, counted in `runs.idle_drains`. Two rather than one: a media server
# that is briefly unreachable turns every row of a batch into a resolution
# miss, which by design changes neither status nor attempts, so one idle
# batch is an outage and not a verdict. Two rather than more: every batch
# past the first costs a scan of the run's rows and a poll's wait, and a run
# that has moved nothing twice has nothing this pass can do -- its rows are
# released and the unscoped retry pass carries on with them.
MAX_IDLE_DRAINS = 2


class CatchUpRefused(Exception):
    """Why a catch-up cannot start right now, in one sentence the button shows.

    Row 213: the sentence is built from fixed words plus a server NAME and an
    exception CLASS name. Never an address, never a credential, never an
    exception's own message.

    ``transient`` says whether the reason can pass on its own, which is what
    an AUTOMATIC request needs to know: a server that is down, or that
    could not answer when asked for its libraries, will be up again --
    and spec §3's post-restart trigger fires exactly ONCE, on the first poll
    after boot, which is precisely when a co-restarting Jellyfin is still
    starting up. Such a request is re-queued. "Not configured" and "already in
    flight" never resolve themselves that way -- the second because the work
    is already happening -- and are dropped, which is what keeps a queue of
    names from growing forever.
    """

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


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
    configured, when the scheduler is off, when a catch-up for it is already
    in flight, when its health poller says it is down, or when it cannot list
    its libraries. The last of those is a refusal rather than a silent empty
    presence on purpose: a server that cannot answer must never be told it
    carries nothing.

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

    # The drain job is registered inside `app.py`'s `if config.scheduler.enabled`
    # gate, so with the scheduler off nothing would ever take a batch: the
    # backlog this would mark is invisible to the unscoped retry pass (it
    # takes `run_id IS NULL` rows only) and to the pipeline's re-arm doors,
    # and the run would never close -- which makes the in-flight check refuse
    # every later catch-up for that server forever. Not transient: waiting
    # never switches the scheduler on.
    if not config.scheduler.enabled:
        raise CatchUpRefused("the scheduler is off; a catch-up needs it to drain")

    poller = (health or {}).get(name)
    if poller is not None and not poller.healthy:
        raise CatchUpRefused(
            f"{name} is not reachable right now; try again once it is back",
            transient=True,
        )

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
            f"{name} could not list its libraries ({type(exc).__name__})",
            transient=True,
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
                # it was, so the `else_` is the OLD status -- which is exactly
                # what a cancel has to put back.
                #
                # A row that is ALREADY armed keeps what the run that armed it
                # recorded, verbatim: its current `pending` was put there
                # by a catch-up, not by the world, and copying it here would
                # have a later cancel restore a
                # delivered row to `pending` -- a due row nothing will ever
                # settle, with the truth of what the server holds lost. NULL
                # is carried on for the same reason under its own meaning: not
                # "no previous status" but "a run CREATED this row"
                # (`db/models.py`), which is what tells a cancel to delete it
                # rather than invent a word for it.
                #
                # A row that has SETTLED inside a run is not already armed --
                # `uploaded`/`written`/`failed` is a word the world put there
                # since -- so it records that word like any ordinary row.
                previous_status=case(
                    (
                        and_(
                            MetadataWrite.run_id.isnot(None),
                            MetadataWrite.status == "pending",
                        ),
                        MetadataWrite.previous_status,
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
            # The metadata UPDATE's `case` and its reasons, for this table.
            previous_status=case(
                (
                    and_(
                        RenderDelivery.run_id.isnot(None),
                        RenderDelivery.status == "pending",
                    ),
                    RenderDelivery.previous_status,
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


# What "done" means in each table, so progress can count EVERY row a run
# owns and not only the ones carrying the obvious word.
#
# `skipped` is the mainline case rather than an exotic one: the retry pass
# records it for a per-library toggle switched off mid-drain or an exemption
# found during the write, and none of those calls pass `leave_run`, so the
# row keeps its `run_id`. `absent` cannot reach a run-owned row through
# `_upsert_outcome` -- its conflict clause refuses to write over one -- but
# `apply_presence`'s plain UPDATE can, so it is counted here rather than left
# to fall between the buckets. A row counted in no bucket is a `total`
# smaller than the backlog the run actually marked, with nothing accounting
# for the difference, which is worse than a number an operator disagrees
# with: it is one he cannot check.
_DONE_STATUSES = {
    RenderDelivery: frozenset({"uploaded", "skipped", "absent"}),
    MetadataWrite: frozenset({"written", "skipped", "absent"}),
}


async def run_tallies(session: AsyncSession, run_id: int) -> dict:
    """``due``/``done``/``failed``/``total`` for one run, counted live from the
    two outcome tables.

    Public because a catch-up's FINISH owes the same three numbers its cancel
    does, and both have to stamp them through ``finish_catch_up`` below.
    """
    counts = {"due": 0, "done": 0, "failed": 0}
    for table, done in _DONE_STATUSES.items():
        rows = (await session.execute(
            select(table.status, func.count())
            .where(table.run_id == run_id)
            .group_by(table.status)
        )).all()
        for status, total in rows:
            if status == "pending":
                counts["due"] += int(total)
            elif status in done:
                counts["done"] += int(total)
            elif status == "failed":
                counts["failed"] += int(total)
    return {**counts, "total": counts["due"] + counts["done"] + counts["failed"]}


async def _attempts_spent(session: AsyncSession, run_id: int) -> int:
    """The budget one run has spent: the sum of ``attempts`` over its rows.

    Private, and read only by the drain: it is the other half of "this batch
    moved something". Deliberately NOT a key in ``run_tallies`` -- that dict
    is splatted straight into the served progress response, so a term added
    there becomes an undocumented API field.
    """
    total = 0
    for table in (RenderDelivery, MetadataWrite):
        total += int((await session.execute(
            select(func.coalesce(func.sum(table.attempts), 0))
            .where(table.run_id == run_id)
        )).scalar_one())
    return total


async def finish_catch_up(
    session: AsyncSession, run_id: int, *, status: str, detail: str,
    tallies: dict | None = None,
) -> dict:
    """Stamp a catch-up's final tallies on its run row, then close it.

    The tallies have to outlive the rows: closing a catch-up RELEASES
    everything it owned (``cancel_catch_up`` below, and the finish this
    function owes), so counting them from the two tables afterwards answers zero for a
    run that marked thousands, and the operator's progress line would go
    blank at the exact moment it became history.

    Stored in the run's own three generic count columns rather than in new
    ones: ``db/models.py`` keeps them NULL for a scheduled job because "a
    scheduled job's window overlaps whatever the pool happened to be doing",
    and a catch-up is the other run whose window IS its own work -- its rows
    are the ones it marked and nothing else's. ``processed`` is the backlog
    it marked, ``failed`` what ran out of budget, ``deferred`` what was still
    due when it ended (a wait, which is what that column means). ``done`` is
    the remainder, which is why it needs no column of its own.

    ``tallies`` is for a caller that must count BEFORE it writes:
    ``cancel_catch_up`` restores and releases its rows first, and by the time
    this runs there is nothing left to count. Everything else lets it count
    here.

    Does not commit.
    """
    tallies = tallies if tallies is not None else await run_tallies(session, run_id)
    # Only a run that is still OPEN may be stamped and closed, and this is
    # the one place every catch-up closer goes through, so the clause lives
    # here rather than in `close_run` (shared with the scheduler and the full
    # pass, neither of which wants it). Without it a cancel that commits
    # while a drain is mid-batch is overwritten by that drain's own finish:
    # the drain sees nothing left carrying the run id, reads it as a run that
    # marked nothing, and rewrites the cancelled run's status, detail and
    # three counts with zeros. The loser of the race returns what it counted
    # and releases nothing further -- the winner's own release has run.
    stamped = (await session.execute(
        update(Run).where(Run.id == run_id, Run.finished_at.is_(None)).values(
            processed=tallies["total"], failed=tallies["failed"], deferred=tallies["due"],
        )
    )).rowcount
    if not stamped:
        return tallies
    await close_run(session, run_id, status=status, detail=detail)
    # The release, AFTER the tallies above are stamped: a run that is over
    # owns nothing, or a later progress query goes on counting rows for it and
    # `pipeline`'s re-arm doors keep asking about a run that has finished. The
    # settled rows keep their outcome and lose only the two scope columns --
    # nothing uploaded or written is undone. A no-op for `cancel_catch_up`,
    # which has already released its own rows by the time it calls this: it
    # has to, because it counts and restores them first.
    for table in (RenderDelivery, MetadataWrite):
        await session.execute(
            update(table).where(table.run_id == run_id)
            .values(previous_status=None, run_id=None)
        )
    return tallies


async def catch_up_progress(session: AsyncSession, name: str) -> dict | None:
    """The current or last catch-up for ``name``, with its progress.

    An OPEN run is counted from the outcome rows themselves rather than from
    anything stored: stored counters would be a third thing that can disagree
    with the two tables, and the two tables are what the operator is actually
    asking about. ``due`` is what is still pending, ``done`` what settled --
    delivered, written, skipped or absent -- and ``failed`` what ran out of
    budget.

    A FINISHED run no longer owns its rows, because closing it released them,
    so it serves the tallies its close stamped (``finish_catch_up``). The
    fallback is the live count, which is the honest answer for a run closed
    by something that did not stamp them -- zeros, but zeros that match what
    the tables now say rather than a number invented for the gap.
    """
    run = (await session.execute(
        select(Run)
        .where(Run.kind == CATCH_UP_KIND, Run.server == name)
        .order_by(Run.started_at.desc(), Run.id.desc())
        .limit(1)
    )).scalars().first()
    if run is None:
        return None

    if run.finished_at is not None and run.processed is not None:
        due, failed = run.deferred or 0, run.failed or 0
        counts = {
            "due": due, "failed": failed,
            "done": run.processed - due - failed, "total": run.processed,
        }
    else:
        counts = await run_tallies(session, run.id)

    return {
        "run_id": run.id,
        "server": run.server,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "cadence_seconds": run.cadence_seconds,
        "detail": run.detail,
        **counts,
    }


async def drain_catch_ups(
    session: AsyncSession, servers, config, *, http=None, mdblist=None,
    now: datetime | None = None,
) -> str:
    """Take one batch from every open catch-up whose cadence has elapsed.

    The backlog is drained by the ORDINARY retry pass, scoped to this run's
    ``run_id`` (spec §3), so a catch-up shares the per-row savepoint, the
    per-library gates and the attempt budget with every other delivery --
    there is no second delivery path to keep in step with the first.

    The per-run cadence is honoured here rather than by registering a second
    scheduled job per run: ``last_drained_at`` is a column, so two replicas
    polling the same database agree about when the last batch was taken.

    A run closes the moment nothing of its is still due -- `ok` even when some
    of its rows failed: the run did what it was asked, and the failed rows are
    the operator's business, counted in the detail. ``finish_catch_up`` is
    what closes it, so the tallies are stamped on the run row BEFORE the rows
    they were counted from are released.

    A run whose last ``MAX_IDLE_DRAINS`` batches moved NOTHING closes too,
    with the same `ok` and a detail naming what it left. A resolution miss
    is a wait, not a failure -- it spends no attempt budget --
    so a row for a file the server will never scan stays `pending` for ever,
    and without this the run would drain empty batches for ever while
    ``start_catch_up``'s in-flight check refused every later catch-up for that
    server, automatic ones included. Nothing is lost by closing: the release
    hands those rows back as ORDINARY pending waits, which the unscoped
    pending-deliveries pass keeps retrying on its own cadence.

    Whether a batch moved anything is this run's OWN tallies AND the budget
    its rows have spent, either side of it: a batch that really attempted
    every row and failed leaves the statuses identical but the attempts
    higher, and a batch that spends budget is not an idle one. The count of
    consecutive idle batches lives in ``runs.idle_drains`` -- a column, for
    ``last_drained_at``'s reason (two replicas share the database and nothing
    else) and because a snapshot of the counts cannot tell one idle batch
    from two in a row.

    Each run's batch is contained AND committed on its own: a failure outside
    a row (the retry pass contains those itself, behind savepoints) would
    otherwise abort the whole pass -- and since the runs are walked in a
    stable order, the same run would be first in line on the next poll and the
    ones behind it would never get a batch. The per-run commit is what keeps
    the containment's rollback from reaching back into an earlier run's
    finish, which the returned sentence has already claimed.

    Commits, like every scheduled pass's body.
    """
    now = now or datetime.now(timezone.utc)
    open_runs = (await session.execute(
        select(
            Run.id, Run.server, Run.cadence_seconds, Run.last_drained_at,
            Run.idle_drains,
        )
        .where(Run.kind == CATCH_UP_KIND, Run.finished_at.is_(None))
        .order_by(Run.id)
    )).all()
    if not open_runs:
        return "catch-up: nothing in flight"

    clauses = []
    for run in open_runs:
        run_id, server_name = run.id, run.server
        # `is None`, not `or`: the column's invariant is that
        # `start_catch_up` already floored it, so the fallback is for a row
        # that somehow carries NULL -- and an explicit `0` there is the
        # fastest drain an operator can ask for, exactly as the write site
        # says.
        cadence = (
            MIN_CADENCE_SECONDS if run.cadence_seconds is None else run.cadence_seconds
        )
        if (
            run.last_drained_at is not None
            and (now - run.last_drained_at).total_seconds() < cadence
        ):
            clauses.append(f"{server_name} waiting {cadence}s between batches")
            continue
        try:
            # Taken BEFORE the batch and compared with the same numbers after
            # it: that is what "this batch moved something" means, and it is
            # read from this run's rows alone. The spent budget is half of
            # it, because a batch in which every row was really attempted and
            # failed-but-not-exhausted leaves each row `pending` with one more
            # attempt against it -- a status histogram identical either side,
            # so on a server refusing every write two such batches would close
            # the run as idle after ~1000 of fifteen thousand rows.
            before = await run_tallies(session, run_id)
            before_spent = await _attempts_spent(session, run_id)
            # The retry pass commits per row, so nothing below may rely on an
            # ORM object loaded before it -- which is why the run's fields are
            # read into locals above rather than kept as a `Run` instance.
            await retry_pending_deliveries(
                session, servers, config, http=http, mdblist=mdblist, now=now,
                server=server_name, run_id=run_id,
            )
            tallies = await run_tallies(session, run_id)
            spent = await _attempts_spent(session, run_id)
            idle = (
                0 if (tallies, spent) != (before, before_spent)
                else run.idle_drains + 1
            )
            await session.execute(
                update(Run).where(Run.id == run_id)
                .values(last_drained_at=now, idle_drains=idle)
            )
            if tallies["due"] == 0:
                detail = f"catch-up: {tallies['done']} done, {tallies['failed']} failed"
                await finish_catch_up(
                    session, run_id, status="ok", detail=detail, tallies=tallies,
                )
                clause = (
                    f"{server_name} finished, {tallies['done']} done, "
                    f"{tallies['failed']} failed"
                )
            elif idle >= MAX_IDLE_DRAINS:
                detail = (
                    f"catch-up: stopped with {tallies['due']} still due, "
                    f"{tallies['done']} done, {tallies['failed']} failed"
                )
                await finish_catch_up(
                    session, run_id, status="ok", detail=detail, tallies=tallies,
                )
                clause = (
                    f"{server_name} stopped with {tallies['due']} still due, "
                    f"{tallies['done']} done, {tallies['failed']} failed"
                )
            else:
                clause = (
                    f"{server_name} {tallies['due']} still due, "
                    f"{tallies['done']} done, {tallies['failed']} failed"
                )
            # This run's own boundary: the clause is held in a local until the
            # commit returns, so a commit that raises produces only the
            # `failed (...)` clause below rather than that one AND a claim the
            # rollback has just discarded. Committing per run is what keeps
            # the containment's rollback from reaching back into an earlier
            # run's finish, which the returned sentence has already claimed.
            await session.commit()
            clauses.append(clause)
        except Exception as exc:
            # Row 213: the class name, never the message -- this can be a
            # transport error carrying a server's address.
            logger.warning(
                "catch-up drain for %s failed (%s)", server_name, type(exc).__name__,
            )
            await session.rollback()
            clauses.append(f"{server_name} failed ({type(exc).__name__})")
    # Each run committed its own work above; this closes the read transaction
    # a pass in which every run was mid-cadence would otherwise leave open.
    await session.commit()
    return "catch-up: " + "; ".join(clauses)


async def cancel_catch_up(
    session: AsyncSession, name: str, *, now: datetime | None = None
) -> dict:
    """Stop the catch-up in flight for ``name`` and put its due rows back.

    Only rows still ``pending`` are restored: anything this run already got
    written or uploaded stays exactly as it is (spec §3, "nothing already
    written is undone"). A row the run CREATED has no previous status to
    return to, so it is removed rather than left as an invented ``pending``
    nothing will ever drain.

    Every remaining row of the run is then released -- the settled ones
    included, which keep their outcome and lose only the two scope columns. A
    cancelled run owns nothing afterwards, or a later progress query would go
    on counting for a run that is over, and the ordinary pipeline's own
    release (``leave_run``) would be the only thing ever clearing them, one
    row at a time, if it happened to touch them at all.

    A cancel does not stop the batch already in flight. ``retry_pending_deliveries``
    detaches up to 500 rows before it starts writing, so a batch that was
    running when this committed goes on attempting those rows and recording
    their outcomes afterwards, over what was just restored. Nothing already
    written is undone by that -- an upload that happened is recorded
    truthfully -- but the ``restored``/``removed`` counts returned here can be
    stale by up to one batch, and a row the drain re-records lands as an
    ordinary unscoped one, because this has already cleared its two scope
    columns.

    Does not commit.
    """
    now = now or datetime.now(timezone.utc)
    run = (await session.execute(
        select(Run).where(
            Run.kind == CATCH_UP_KIND, Run.server == name, Run.finished_at.is_(None)
        )
    )).scalars().first()
    if run is None:
        raise CatchUpRefused(f"no catch-up for {name} is in flight")

    # Both reads come BEFORE anything is written, and for the same reason:
    # `run_id` is the only handle on what this run marked, and every
    # statement below clears it. A tally taken afterwards counts nothing, and
    # a render id list taken afterwards is empty.
    tallies = await run_tallies(session, run.id)
    render_ids = list((await session.execute(
        select(RenderDelivery.render_id).where(RenderDelivery.run_id == run.id)
    )).scalars())

    restored = removed = 0
    for table in (RenderDelivery, MetadataWrite):
        removed += (await session.execute(
            delete(table).where(
                table.run_id == run.id,
                table.status == "pending",
                table.previous_status.is_(None),
            )
        )).rowcount
        restored += (await session.execute(
            update(table)
            .where(
                table.run_id == run.id,
                table.status == "pending",
                table.previous_status.isnot(None),
            )
            .values(
                status=table.previous_status,
                # A row that was ALREADY pending before the catch-up marked
                # it gets the ordinary horizon back rather than the one this
                # run shortened it to; anything else has no horizon at all.
                # The exact pre-catch-up instant is not stored, and a fresh
                # horizon is the honest approximation -- it delays that row by
                # at most one retry interval and never strands it.
                next_attempt_at=case(
                    (table.previous_status == "pending", now + timedelta(seconds=RETRY_SECONDS)),
                    else_=None,
                ),
                previous_status=None,
                run_id=None,
            )
        )).rowcount
        await session.execute(
            update(table).where(table.run_id == run.id)
            .values(previous_status=None, run_id=None)
        )

    # `start_catch_up` rolled every render whose row it armed up to
    # `pending`, and the statements above put those rows back to `failed` or
    # `uploaded`, or deleted them outright, and nothing else recomputes
    # `renders.upload_status` -- the column `/api/library`'s filter, the
    # dashboard tiles and the action centre read. By id rather than by
    # timestamp: a restored row carries the horizon it had before the
    # catch-up, so the stamp the other entry names rows by is gone.
    await rollup_stamped_renders(session, render_ids=render_ids)

    detail = (
        f"cancelled: {tallies['total']} marked, {tallies['done']} done, "
        f"{tallies['failed']} failed; {restored} restored, {removed} removed"
    )
    await finish_catch_up(
        session, run.id, status="cancelled", detail=detail, tallies=tallies,
    )
    return {"run_id": run.id, "restored": restored, "removed": removed, "detail": detail}


async def retry_failed(
    session: AsyncSession, name: str, *, now: datetime | None = None
) -> dict:
    """Re-arm ``name``'s ``failed`` rows in both tables, without a catch-up.

    The narrow half of the Servers tab's two buttons (spec §5): a catch-up
    re-arms everything that is behind, this re-arms only what gave up. The
    budget is reset with the status, or the very next pass would fail the row
    again on its first attempt.

    EVERY ``failed`` row for that server, inside a run or outside one, and as
    an ORDINARY row -- both scope columns back to NULL. The unscoped retry
    pass takes ``run_id IS NULL`` rows only, so a row left
    in the run that armed it would be re-armed here and then drained by
    nothing until that run's own cadence came round; and a run whose rows this
    took back is no longer the owner of them.

    Does not commit.
    """
    now = now or datetime.now(timezone.utc)
    counts = {}
    for key, table in (("artwork", RenderDelivery), ("metadata", MetadataWrite)):
        counts[key] = (await session.execute(
            update(table)
            .where(table.server == name, table.status == "failed")
            .values(
                status="pending", detail=None, attempted_at=now, next_attempt_at=now,
                attempts=0, previous_status=None, run_id=None,
            )
        )).rowcount
    # The roll-up `start_catch_up` owes for the same reason and by the same
    # selector: this is a set-shaped write to `render_deliveries` stamped
    # `next_attempt_at = now`, and nothing else recomputes
    # `renders.upload_status` behind it.
    await rollup_stamped_renders(session, name, now)
    return {"server": name, **counts}


# The two per-server settings that change which items a server is expected to
# carry, and therefore the two a save must trigger a catch-up on (spec §3).
# `library_map` is Jellyfin-only today; reading it with getattr keeps this
# honest for a server block that has no such field.
_LIBRARY_SHAPE_FIELDS = ("library_map", "excluded_libraries")

# The per-server delivery toggles, by server. Flipping one of these ON does
# not change which items a server is expected to carry, but it does change
# which of them it was allowed to receive: every row the pipeline skipped
# while the toggle was off is now owed to that server, and nothing else heals
# them before the next full pass. An OFF flip owes nothing, so only
# `False -> True` is reported.
_DELIVERY_TOGGLES = {
    "plex": (("operations", "write_to_plex"), ("badges", "upload_to_plex")),
    "jellyfin": (("operations", "write_to_jellyfin"), ("badges", "upload_to_jellyfin")),
}


def _toggle_states(config, section: str, field: str) -> dict[str, bool | None]:
    """One toggle's EFFECTIVE value per scope: globally, and per library.

    Keyed by library name, with ``""`` for the global setting. A library's
    per-library override of the same field is nullable and ``None`` there
    means "the global one" (see ``config.schema.LibraryOverride``), so the
    global value is substituted rather than carried as None -- otherwise a
    library that inherits a `False -> True` flip would read as unchanged.

    ``getattr`` throughout, and never a raise: this runs against whatever two
    config objects the swap was handed, including a generation that predates
    a section existing at all. A section that is not there is ``None``, and
    ``None`` is neither False nor True, so it reports no flip.
    """
    block = getattr(config, section, None)
    global_value = getattr(block, field, None) if block is not None else None
    states = {"": global_value}
    for library, override in (getattr(config, "libraries", None) or {}).items():
        section_override = getattr(override, section, None)
        value = (
            getattr(section_override, field, None)
            if section_override is not None else None
        )
        states[library] = global_value if value is None else value
    return states


def _toggle_turned_on(old_config, new_config, section: str, field: str) -> bool:
    """Whether this toggle went ``False -> True`` in any scope.

    The UNION of both configs' scopes, not the new config's alone.
    A library block can appear or disappear between generations as easily as
    a field inside it can change, and a scope missing on one side falls back
    to THAT side's global value -- which is exactly what the library was
    effectively running on while it had no block. So a library whose
    ``False`` override is dropped under a ``True`` global reads as the
    `False -> True` flip it is, and a library that gains a ``True`` override
    under a ``False`` global reads as one too.
    """
    old = _toggle_states(old_config, section, field)
    new = _toggle_states(new_config, section, field)
    return any(
        new.get(scope, new[""]) is True and old.get(scope, old[""]) is False
        for scope in old.keys() | new.keys()
    )


def _toggle_ever_on(config, section: str, field: str) -> bool:
    """Whether this toggle is on in ANY scope of one config.

    ``section.enabled`` is the master switch for its half and is read per
    scope the same way: metadata operations that are switched off write
    nothing whatever ``write_to_<server>`` says, and badges that are switched
    off upload nothing whatever ``upload_to_<server>`` says.
    """
    on = _toggle_states(config, section, field)
    gate = _toggle_states(config, section, "enabled")
    return any(
        value is True and gate.get(scope, gate[""]) is not False
        for scope, value in on.items()
    )


def servers_with_delivery_enabled(config, names) -> list[str]:
    """Of ``names``, those this config actually delivers something to.

    The boot trigger's filter. ``start_catch_up`` writes an outcome row
    only for a half it is configured to deliver, so a server with
    every toggle off is never recorded, ``servers_never_seen`` would keep
    reporting it, and every restart would open and immediately close a no-op
    run for it forever. A server this deployment writes nothing to has
    nothing to catch up on, so it is simply not queued.

    ON in ANY scope is enough: a single library that overrides one toggle to
    True is a library this server is owed rows for. A name this module has no
    toggles for is kept rather than dropped -- an unknown server is not one
    this predicate is entitled to silence.
    """
    return [
        name for name in names
        if name not in _DELIVERY_TOGGLES or any(
            _toggle_ever_on(config, section, field)
            for section, field in _DELIVERY_TOGGLES[name]
        )
    ]


def servers_with_changed_libraries(old_config, new_config) -> list[str]:
    """The configured servers whose delivery scope changed between two configs.

    Pure, and deliberately so: the caller is ``swap_config``, a synchronous
    function with no session, so this decides WHO and the drain job decides
    when.

    Two kinds of change count. The library SHAPE -- the map and the
    exclusions -- changes which items the server is expected to carry. The
    delivery TOGGLES change which of them it was allowed to receive, and a
    toggle that has just been switched on leaves behind every row skipped
    while it was off.

    A server present in only one of the two configs is not reported. A server
    that has just been added has no outcome rows at all, which is what
    ``servers_never_seen`` answers on the next boot; reporting it here as
    well would queue the same catch-up twice for one change.
    """
    changed = []
    # The toggle map's own keys, not a second list of the same two names: a
    # name in the tuple that the dict lacked would `KeyError` below.
    for name in _DELIVERY_TOGGLES:
        old_block = getattr(old_config, name, None)
        new_block = getattr(new_config, name, None)
        if old_block is None or new_block is None:
            continue
        shape_changed = any(
            getattr(old_block, field, None) != getattr(new_block, field, None)
            for field in _LIBRARY_SHAPE_FIELDS
        )
        if shape_changed or any(
            _toggle_turned_on(old_config, new_config, section, field)
            for section, field in _DELIVERY_TOGGLES[name]
        ):
            changed.append(name)
    return changed


async def servers_never_seen(session: AsyncSession, names) -> list[str]:
    """Of ``names``, the servers this database has never recorded an outcome for.

    Spec §3's "a restart that introduced X", read against the only durable
    evidence there is: a server with no ``render_deliveries`` and no
    ``metadata_writes`` row, on a database that already holds items, is a
    server this deployment has just gained. No extra state is needed, and the
    catch-up's own marking writes those rows, so the answer stops being yes
    once one has run.

    That last sentence is only true of a server this deployment actually
    delivers to: ``start_catch_up`` writes no row at all for a server whose
    every toggle is off, and such a server would be reported here on every
    boot forever. It is ``servers_with_delivery_enabled`` that keeps it out
    of ``names`` -- the pair of predicates is what makes the trigger fire
    once, not this one alone.

    One hole the pair does not close: a server whose only enabled half has
    nothing to deliver yet -- ``upload_to_<server>`` on with no non-background
    render `rendered` -- passes that filter, gets no row written, and is
    therefore queued again on the next boot, opening a run that finds nothing
    and closes on the drain job's first look. A no-op per restart, which
    stops the moment the first render is delivered.

    Silent on an empty database: a fresh deployment's first full pass covers
    every server anyway, and a catch-up with nothing to catch up on is noise.
    """
    has_items = (await session.execute(select(MediaItem.id).limit(1))).scalars().first()
    if has_items is None:
        return []
    seen = set(
        (await session.execute(select(RenderDelivery.server).distinct())).scalars()
    ) | set(
        (await session.execute(select(MetadataWrite.server).distinct())).scalars()
    )
    return [name for name in names if name not in seen]
