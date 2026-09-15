"""Per-server delivery of a badged render (spec §5.2, §5.3).

``render_deliveries`` is the per-server truth; ``Render.upload_status``
(``rollup``, below) stays the roll-up so every existing query and dashboard
that reads it keeps working unchanged.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import fields
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.loader import config_for_library
from autoposter.db.models import ItemFacts, MediaItem, MetadataWrite, Render, RenderDelivery
from autoposter.db.refs import refs_for
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
from autoposter.plex.item_overrides import load_overrides
from autoposter.plex.writer import exemption_reason
from autoposter.servers.base import CAP_LOCK_ARTWORK, ItemNotFound, PathMismatch

logger = logging.getLogger(__name__)

# queue/jobs.py's DEFER_INTERVAL_SECONDS, the same horizon: §5.3 keeps the
# existing "no finite cap" stance for an unresolved server, just moved from
# the job to the delivery row.
RETRY_SECONDS = 6 * 60 * 60

# The status vocabulary, spelled once (spec §1/§5.2). These four words are
# shared by both outcome tables; each table's own terminal word (``uploaded``
# for a delivery, ``written`` for a metadata write) is ``_upsert_outcome``'s
# ``terminal_status`` argument, and the check below admits that one too.
#
# Checked rather than left to a docstring: the column is a plain
# ``String(24)``, so a typo at a call site (``"write"`` for ``"written"``)
# stores silently and then reads as neither written nor pending -- invisible
# until an operator asks the item page why a server says nothing.
STATUSES = ("pending", "failed", "skipped", "absent")


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


# Every ``GatheredFacts`` field ``item_facts`` actually stores. DERIVED, not
# listed: rows 32/33a/99/227 added four ``GatheredFacts`` fields with no
# column (``user_rating``, ``original_title``, ``added_at``, ``sort_title``)
# and the next such field must not be able to break the retry the way those
# four did -- see ``facts_from_row``.
_STORED_FACT_FIELDS = tuple(
    f.name for f in fields(GatheredFacts) if hasattr(ItemFacts, f.name)
)


def facts_from_row(row: ItemFacts | None) -> GatheredFacts:
    """The stored ``item_facts`` row as the ``GatheredFacts`` the writer reads.

    The ORM row is NOT duck-compatible with ``GatheredFacts``, which is what
    an earlier version of this module assumed: ``plex.writer.plan_edits`` --
    which both writers use -- dereferences ``facts.user_rating`` for every
    kind of item, guarded only by ``WRITABLE_BY_KIND`` membership, and
    ``ItemFacts`` has no such attribute. Handing it the row raised
    ``AttributeError`` out of the savepoint on every due metadata row, so the
    retry could not complete a single write.

    The unpersisted four are left at their defaults, which is honest: a retry
    writes what the database holds, and it holds none of them. ``plan_edits``
    then sees ``user_rating=None`` and skips the field, exactly as intended.
    """
    if row is None:
        return GatheredFacts()
    return GatheredFacts(**{name: getattr(row, name) for name in _STORED_FACT_FIELDS})


async def _upsert_outcome(
    session: AsyncSession, model, constraint: str, key: dict[str, object], status: str, *,
    detail: str | None, retry_in: float | None, count_attempt: bool,
    terminal_status: str, terminal_at: str, extra_terminal: dict[str, object] | None = None,
    reset_attempts: bool = False, leave_run: bool = False, keep_next_attempt: bool = False,
) -> int:
    """Shared upsert body for ``record`` and ``record_metadata`` (spec §1/§2).

    ``terminal_status``/``terminal_at`` are ``"uploaded"``/``"uploaded_at"``
    for deliveries and ``"written"``/``"written_at"`` for metadata writes;
    ``extra_terminal`` is the one further column that rides along with the
    terminal timestamp (``fingerprint``, deliveries only). Both are stamped
    only on a terminal-status call and, on conflict, never erased by a later
    non-terminal one -- see ``record``'s own docstring for why.

    An ``absent`` row is never written over from here, whatever the caller
    asks for: spec §1 says an item whose library a server does not carry "is
    never resolved there, and is never retried", and making that a clause on
    the conflict rather than a check each caller remembers is what makes it
    true of the callers Phases B-D have yet to add. Postgres re-evaluates the
    clause against the latest row version after taking the row lock, so it
    also closes the window where a caller's own snapshot predates a full
    pass's presence stamp. ``presence.apply_presence``'s re-arm is a plain
    ``UPDATE`` and is unaffected -- it is the one thing entitled to move a
    row off ``absent``.

    ``run_id``/``previous_status`` are otherwise never named here, so a row
    keeps its scope across every retry inside the run that armed it -- which
    is what the catch-up run's run-scoped progress counts, by run and
    status, and what its cancel restores. ``leave_run`` is the one
    exception: a row the ORDINARY pipeline re-arms has left that run, and
    carrying the run id on would have a later cancel or progress query act
    on rows the run no longer owns. A terminal outcome inside a run keeps
    both columns; clearing them when the run closes is the run's own
    business.

    ``keep_next_attempt`` is the third half of that: a row inside an OPEN run
    keeps the horizon that run gave it, because the run's own drain owns its
    timing (spec §3) and an ordinary pass pushing it six hours out would stall
    the drain of a row the run is still counting. ``COALESCE``, not a blanket
    keep: a stored NULL is a row with no horizon at all, and a ``pending`` row
    with no ``next_attempt_at`` is never due, so that one takes the new value.
    """
    if status not in STATUSES and status != terminal_status:
        raise ValueError(f"{status!r} is not an outcome status")
    now = datetime.now(timezone.utc)
    counted = count_attempt and status in ("pending", "failed")
    is_terminal = status == terminal_status
    values = dict(
        **key, status=status, detail=detail,
        attempted_at=now,
        next_attempt_at=(now + timedelta(seconds=retry_in)) if status == "pending" else None,
        attempts=1 if counted else 0,
    )
    if leave_run:
        values["run_id"] = None
        values["previous_status"] = None
    values[terminal_at] = now if is_terminal else None
    for column, value in (extra_terminal or {}).items():
        values[column] = value if is_terminal else None
    stmt = insert(model).values(**values)
    updatable = {k: v for k, v in values.items() if k not in key}
    if keep_next_attempt:
        updatable["next_attempt_at"] = func.coalesce(
            model.next_attempt_at, values["next_attempt_at"]
        )
    if not is_terminal:
        # A failed, skipped or pending outcome must never
        # erase what this server DID deliver/write last time. ``rollup`` reads
        # ``uploaded_at`` back into ``renders.uploaded_at``, which the item
        # page's Uploaded column shows, and an operator reads a blank there as
        # "never delivered" rather than "delivered, then broke" -- the
        # distinction the single-server code kept by leaving the column alone
        # on a failure.
        updatable.pop(terminal_at)
        for column in (extra_terminal or {}):
            updatable.pop(column)
    else:
        # And a TERMINAL call that names no value for one of those columns
        # keeps what is stored, for the same reason: `record(..., "uploaded")`
        # without a `fingerprint=` is a caller that does not know which bytes
        # this server holds, not one asserting it holds none.
        for column, value in (extra_terminal or {}).items():
            if value is None:
                updatable.pop(column)
    if counted:
        if not reset_attempts:
            # The EXISTING row's value plus one: naming the column in `set_`
            # renders `attempts = <table>.attempts + 1`, which is what makes
            # the increment atomic against a concurrent pass.
            updatable["attempts"] = model.attempts + 1
        # And with BOTH flags, `values`' own 1 stands: reset, then count.
        # The two used to be mutually exclusive in effect -- `reset_attempts`
        # was silently ignored whenever `count_attempt` was true -- which left
        # a caller whose event is both a fresh start AND a real attempt with
        # no way to say so. `pipeline._write`'s write failure against a row
        # that was already `failed` is that caller: the full pass re-arms an
        # exhausted row, and this failure is its first new attempt.
    elif status in ("pending", "failed") and not reset_attempts:
        updatable.pop("attempts")
    stmt = stmt.on_conflict_do_update(
        constraint=constraint,
        set_=updatable,
        where=model.status != "absent",
    ).returning(model.attempts)
    # `scalar_one_or_none`, because the clause above can leave the conflicting
    # row alone and then there is no RETURNING row at all. Zero is the honest
    # answer for the budget: nothing was attempted against an absent server.
    attempts = (await session.execute(stmt)).scalar_one_or_none() or 0
    await session.flush()
    return attempts


async def record(
    session: AsyncSession, render_id: int, server: str, status: str, *,
    detail: str | None = None, retry_in: float | None = None,
    fingerprint: str | None = None, count_attempt: bool = True,
    reset_attempts: bool = False, leave_run: bool = False,
    keep_next_attempt: bool = False,
) -> int:
    """Upsert this render's delivery row for ``server``; return its ``attempts``.

    ``attempted_at`` is stamped on every call, ``skipped`` included: it is
    the "we last looked at this server" timestamp, not a success marker.

    ``attempts`` is the retry budget's counter (spec §2). It climbs on a
    ``pending`` or ``failed`` outcome and is reset to zero by anything that
    settles the row -- so the budget bounds the CURRENT streak of trouble
    rather than the row's whole history. ``count_attempt=False`` is the
    resolution-miss case: the item is simply not on that server yet, which
    is a wait and not an attempt at delivering anything, and spending budget
    on it would turn "the server has not scanned this file" into a
    permanent ``failed``.

    ``reset_attempts`` is the RE-ARM: an uncounted ``pending`` that means
    "start this row over" rather than "keep waiting" puts the counter back to
    zero. Without it, ``failed`` -- itself a counted attempt -- left the row
    permanently above the budget, so a re-armed row was exhausted again by
    its very first failure and spec 2's "stays visible as `failed` until the
    next full pass or catch-up re-arms it" gave it one retry rather than the
    whole budget. ``deliver``'s re-arm of a missing/pending/failed row passes
    it; the catch-up re-arm must pass it too. The WAIT sites
    (``ItemNotFound``, an identity server that cannot be sampled) do not:
    they are the same streak of trouble continuing, not a fresh start.

    ``reset_attempts`` composes with ``count_attempt``: both together leave
    ``attempts`` at 1 -- reset, then count -- which is what a caller whose
    event is a fresh start AND a real attempt says (``pipeline._write``'s
    write failure against a row that was already ``failed``).

    ``leave_run`` is the re-arm's other half: a row the ordinary pipeline
    re-arms has left the catch-up that armed it, so both scope columns go
    back to NULL. The same two sites pass it that pass ``reset_attempts``.

    ``fingerprint`` is the badge fingerprint actually delivered. Stored on
    ``uploaded`` only, and -- exactly like ``uploaded_at`` -- never erased by
    a later non-``uploaded`` outcome, because the catch-up's "is this row
    behind the render" question is about what this server IS serving.
    """
    return await _upsert_outcome(
        session, RenderDelivery, "uq_delivery_render_server",
        {"render_id": render_id, "server": server}, status,
        detail=detail, retry_in=retry_in, count_attempt=count_attempt,
        terminal_status="uploaded", terminal_at="uploaded_at",
        extra_terminal={"fingerprint": fingerprint},
        reset_attempts=reset_attempts, leave_run=leave_run,
        keep_next_attempt=keep_next_attempt,
    )


async def record_metadata(
    session: AsyncSession, item_id: int, server: str, status: str, *,
    detail: str | None = None, retry_in: float | None = None,
    count_attempt: bool = True, reset_attempts: bool = False, leave_run: bool = False,
    keep_next_attempt: bool = False,
) -> int:
    """Upsert this item's metadata-write row for ``server``; return ``attempts``.

    The sibling of ``record`` (spec §1), field for field, with ``written``
    where artwork says ``uploaded`` and ``written_at`` where it says
    ``uploaded_at``. Keyed on the ITEM: a metadata write has no art kind.
    ``reset_attempts`` and ``leave_run`` mean what they mean there -- the
    re-arm, which the catch-up run is the other writer of for this table.
    """
    return await _upsert_outcome(
        session, MetadataWrite, "uq_metadata_write_item_server",
        {"item_id": item_id, "server": server}, status,
        detail=detail, retry_in=retry_in, count_attempt=count_attempt,
        terminal_status="written", terminal_at="written_at",
        reset_attempts=reset_attempts, leave_run=leave_run,
        keep_next_attempt=keep_next_attempt,
    )


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
        # `absent` sits HERE, alongside `skipped`, by decision rather than by
        # falling through: a server that does not carry the item's library has
        # nothing to say about this render, so an absent row must never raise
        # the roll-up above what the servers that DO carry it report -- and a
        # render whose every row is absent or skipped is exactly the "nothing
        # to report" that `skipped` means. The item page reads this
        # back, so it is written down rather than left to be rediscovered.
        status = "skipped"
    uploaded_at = max((u for _, u in rows if u is not None), default=None)
    values: dict[str, object] = {"upload_status": status}
    if uploaded_at is not None:
        # The roll-up half: the column is carried forward,
        # never overwritten with NULL. No delivery row holding a timestamp
        # means nothing this pass learned anything new about when the render
        # was last delivered -- which is not the same as learning it never was.
        values["uploaded_at"] = uploaded_at
    await session.execute(
        update(Render).where(Render.id == render_id).values(**values)
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
    session: AsyncSession, servers, config, *, http=None, mdblist=None,
    now: datetime | None = None, server: str | None = None, run_id: int | None = None,
) -> str:
    """The retry pass for BOTH outcome tables (spec §2): due rows only, each
    re-resolved on the ONE server it is still owed to.

    Only that server, never every server for the item: the other servers
    already have their own row (uploaded/written, skipped, or independently
    pending), and re-running them here would be a second, uncoordinated pass
    racing the one the next webhook or full-pass item triggers.

    ``server`` narrows the pass to one server's rows and ``run_id`` to one
    catch-up's (spec §3); both filters apply to both tables, because a
    catch-up marks both. NO ``run_id`` means the ORDINARY rows -- those no
    catch-up armed -- and not "every row": a catch-up stamps its backlog
    `next_attempt_at = now` while ordinary rows sit six hours out, so an
    unscoped pass that took them would hand a catch-up's thousands of rows the
    whole `LIMIT 500` budget of every scheduled pass until they drained --
    days, on a 16k-row catch-up -- while ordinary due rows waited, and would
    advance the catch-up's own run-scoped progress from outside it.

    ``pipeline.compose_badged_bytes`` is imported lazily, inside the
    function: a top-level import in that direction risks a cycle, since
    ``pipeline`` calls back into this module.
    """
    from autoposter.render import pipeline as _pipeline
    from autoposter.render.pipeline import upsert_server_ref

    now = now or datetime.now(timezone.utc)
    # Read once per pass, not per row: a config swap mid-pass would otherwise
    # let one pass apply two budgets, and the rows it bounded first would be
    # judged by a number the operator has already replaced.
    max_attempts = config.scheduler.delivery_attempts

    def _scoped(stmt, table):
        if server is not None:
            stmt = stmt.where(table.server == server)
        # Always a clause on the scope, never "no clause": see the docstring
        # on why an unscoped pass must not drain a catch-up's rows.
        stmt = stmt.where(
            table.run_id.is_(None) if run_id is None else table.run_id == run_id
        )
        return stmt

    due = (await session.execute(_scoped(
        select(RenderDelivery, Render, MediaItem)
        .join(Render, Render.id == RenderDelivery.render_id)
        .join(MediaItem, MediaItem.id == Render.item_id)
        .where(RenderDelivery.status == "pending", RenderDelivery.next_attempt_at <= now)
        # `, id`: a catch-up stamps thousands of rows with ONE timestamp, and
        # without a tiebreak which 500 of them a pass takes is arbitrary --
        # which makes a catch-up's drain unobservable and unrepeatable, and
        # the catch-up run's progress reporting reads exactly that drain.
        .order_by(RenderDelivery.next_attempt_at, RenderDelivery.id)
        .limit(500),
        RenderDelivery,
    ))).all()
    metadata_due = (await session.execute(_scoped(
        select(MetadataWrite, MediaItem)
        .join(MediaItem, MediaItem.id == MetadataWrite.item_id)
        .where(MetadataWrite.status == "pending", MetadataWrite.next_attempt_at <= now)
        .order_by(MetadataWrite.next_attempt_at, MetadataWrite.id)
        .limit(500),
        MetadataWrite,
    ))).all()

    # The due rows are DETACHED before either loop runs. `_commit_row`'s
    # rollback on a failed commit expires every object the session holds, and
    # both loops read theirs across rows -- so the row after the failed one
    # would die reading its own id, the same way the pre-savepoint
    # `session.rollback()` used to kill the rest of the pass. Nothing below
    # writes through these objects: every write in this module is a Core
    # statement keyed on an id, and the one function that does mutate a
    # `Render` (`compose_badged_bytes`, on `render.badge_fingerprint`) leaves
    # it alone under `force=True`, which is the only way this pass calls it.
    # `if obj in session`, because one item can appear in both queries.
    for obj in (o for row in (*due, *metadata_due) for o in row):
        if obj in session:
            session.expunge(obj)

    uploaded = written = still_pending = 0
    per_server: dict[str, dict[str, int]] = {}

    def _tally(name: str, outcome: str) -> None:
        """One clause of spec §2's sentence, per server.

        ``due`` counts every row this pass looked at and the other five count
        what it DID, so the five add up to it: every row reaches exactly one
        outcome. ``skipped`` is the counter that makes that true -- without
        it a server whose toggle is off read `N due, 0 uploaded, 0 written,
        0 pending, 0 failed`, with nothing in the sentence saying why.
        """
        counts = per_server.setdefault(
            name,
            {"due": 0, "uploaded": 0, "written": 0, "pending": 0, "failed": 0, "skipped": 0},
        )
        counts[outcome] += 1

    def _counters() -> tuple:
        """This row's starting point, so a failed commit can put the pass's
        own counters back where they were before it."""
        return uploaded, written, still_pending, {n: dict(c) for n, c in per_server.items()}

    async def _commit_row(name: str, before: tuple) -> None:
        """The ROW's commit: an outcome is durable the moment its side effect
        has happened (spec §2).

        The pass used to hold one transaction over as many as 1,000 rows and
        ~3,000 round trips and commit once at the end, so a single failure at
        that commit discarded every outcome row while up to 500 images and
        500 metadata payloads had already landed on real servers -- and the
        summary still claimed them. Committing per row bounds the loss to the
        row that failed, and bounds the idle-in-transaction window to one
        row's work rather than the whole pass's.

        Safe for the loops above it because the session factory is
        ``expire_on_commit=False`` (db/base.py): the objects the due queries
        returned stay usable across a commit.

        A commit that fails is the one outcome this pass may not claim: the
        counters go back to what they were before the row, and the row is
        counted ``pending`` -- which is what it still is in the database.
        """
        nonlocal uploaded, written, still_pending
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning(
                "pending deliveries: the commit for a %s row failed (%s)",
                name, failure_detail(exc),
            )
            uploaded, written, still_pending, restored = before
            per_server.clear()
            per_server.update(restored)
            _tally(name, "due")
            still_pending += 1
            _tally(name, "pending")

    for delivery, render, item in due:
        # One row's own failure -- a bug, a bad refs_for lookup, anything not
        # already turned into a delivery outcome below -- must not take the
        # rest of the pass down with it; every other due row still deserves
        # its own attempt.
        #
        # A SAVEPOINT per row is what makes that
        # true of a DATABASE failure too, which is the likeliest thing to
        # land in the handler below -- a statement error out of `record`'s
        # upsert, a `rollup` UPDATE, a dropped connection -- and which
        # leaves the transaction aborted. `ROLLBACK TO SAVEPOINT` un-aborts
        # it while reaching only this row's own work: nothing commits until
        # the end of the pass, so a plain `session.rollback()` here threw
        # away every EARLIER row's `record`/`rollup` as well, and expired
        # every object the `due` query returned along with them -- which is
        # how the row after next came to die reading its own id. Rolling the
        # savepoint back is automatic on the way out of this block, and
        # leaves every other row's work, and every object, untouched.
        render_id, server_name = render.id, delivery.server
        before = _counters()
        _tally(server_name, "due")
        try:
            async with session.begin_nested():
                # `target`, not `server`: the keyword-only `server` filter
                # above is what `_scoped` closes over, and rebinding it here
                # would leave a MediaServer object in it for the rest of the
                # pass -- silent today, a wrong due query for the next scoped
                # read added to this function.
                target = servers.get(delivery.server)
                if target is None:
                    # The config that named this server is gone (an operator
                    # removed the block); no amount of retrying resolves that,
                    # unlike an ItemNotFound or a transport hiccup.
                    await record(session, render.id, delivery.server, "failed", detail="config: server removed")
                    _tally(delivery.server, "failed")
                    await rollup(session, render.id)
                    continue

                # Per-library, exactly like apply_badges (render/pipeline.py)
                # resolves before reading any badges.* setting: a library
                # override must gate a retry the same way it gated the delivery
                # this row is a retry OF.
                row_config = config_for_library(config, item.library)

                if not getattr(row_config.badges, f"upload_to_{delivery.server}", False):
                    await record(
                        session, render.id, delivery.server, "skipped",
                        detail=f"config: badges.upload_to_{delivery.server} is off",
                    )
                    _tally(delivery.server, "skipped")
                    await rollup(session, render.id)
                    continue

                refs = await refs_for(session, item.id)
                try:
                    resolved_item = await target.resolve(_intent_for(item, refs))
                except PathMismatch as exc:
                    # A path-mapping mismatch that no retry fixes (spec §6.2).
                    # The detail goes through `failure_detail`
                    # like every other one -- category plus class name, never the
                    # exception's own message, which carries an operator path.
                    await record(session, render.id, delivery.server, "failed", detail=failure_detail(exc))
                    _tally(delivery.server, "failed")
                    await rollup(session, render.id)
                    continue
                except ItemNotFound:
                    # Not on this server yet -- a wait, not an attempt at the
                    # delivery, so it spends no budget (spec §2, §5.3).
                    await record(
                        session, render.id, delivery.server, "pending",
                        retry_in=RETRY_SECONDS, count_attempt=False,
                    )
                    still_pending += 1
                    _tally(delivery.server, "pending")
                    await rollup(session, render.id)
                    continue
                except Exception as exc:
                    # A transport error: the server may simply be down right now.
                    logger.warning("delivery to %s failed to resolve (%s)", delivery.server, failure_detail(exc))
                    attempts = await record(
                        session, render.id, delivery.server, "pending",
                        detail=failure_detail(exc), retry_in=RETRY_SECONDS,
                    )
                    if attempts >= max_attempts:
                        # Spec §2: nothing retries forever. The row stays
                        # visible as `failed` until the next full pass or
                        # catch-up re-arms it.
                        await record(
                            session, render.id, delivery.server, "failed",
                            detail=failure_detail(exc),
                        )
                        _tally(delivery.server, "failed")
                    else:
                        still_pending += 1
                        _tally(delivery.server, "pending")
                    await rollup(session, render.id)
                    continue

                await upsert_server_ref(session, item.id, resolved_item)

                # The identity the FULL pass composes from:
                # `process_item` samples live media info and native
                # ratings off Plex alone and passes `None` when this pass
                # resolved no Plex item (see `compose_badged_bytes`' own
                # docstring), and those values feed both the badge and the
                # fingerprint. Composing here without them produced a poster
                # missing the resolution/format overlays every other server
                # already has -- visibly different artwork on the retried
                # server until the next full pass. So the same identity is
                # re-resolved here, and reused for free when it IS the server
                # this row is owed to.
                identity_name = "plex" if "plex" in refs and servers.get("plex") is not None else None
                identity_item = resolved_item if identity_name == delivery.server else None
                if identity_name is not None and identity_item is None:
                    try:
                        identity_item = await servers.get(identity_name).resolve(_intent_for(item, refs))
                    except PathMismatch as exc:
                        # `PathMismatch` subclasses `ItemNotFound`, so without
                        # this clause a mount mismatch on the identity server
                        # would become an unbounded `pending`, retried
                        # forever. No retry fixes one (spec
                        # §6.2) -- the same `failed` the delivery server's own
                        # resolve records two blocks above.
                        await record(session, render.id, delivery.server, "failed", detail=failure_detail(exc))
                        _tally(delivery.server, "failed")
                        await rollup(session, render.id)
                        continue
                    except ItemNotFound as exc:
                        # "It does not have the item" is not "it never will":
                        # Plex raises a bare `ItemNotFound` for a file it has
                        # not scanned yet and for one whose media parts it is
                        # still analysing (`plex/client.py` says so in its own
                        # words), and this row is pending precisely because
                        # the same new file is working its way through both
                        # servers' scans. So this is a WAIT, and a counted one:
                        # spec §2's "nothing retries forever" bounds it with
                        # the attempts budget -- exactly the delivery server's
                        # own transport arm three blocks above -- rather than
                        # by abandoning the identity on the first miss.
                        attempts = await record(
                            session, render.id, delivery.server, "pending",
                            detail=failure_detail(exc), retry_in=RETRY_SECONDS,
                        )
                        if attempts < max_attempts:
                            still_pending += 1
                            _tally(delivery.server, "pending")
                            await rollup(session, render.id)
                            continue
                        # Budget spent: a scan gap would have healed by now, so
                        # the ref really is stale, and waiting past this point
                        # is the unbounded wait §2 rules out -- a delivery owed
                        # to ANOTHER server held forever against a server that
                        # has nothing to say. Fall through with `identity_item`
                        # still `None`, which is exactly the
                        # `server=None, ref=None` the compose below already
                        # passes for an item with no Plex ref: the
                        # resolution/format overlays drop out and the delivery
                        # lands. Retiring the ref itself is the prune pass's
                        # own work.
                        logger.info(
                            "delivery to %s composes without %s's media info: "
                            "it does not have the item after %s tries",
                            delivery.server, identity_name, attempts,
                        )
                    except Exception as exc:
                        # Never deliver overlay-less bytes: not being able to
                        # sample the identity server is a wait, and the row
                        # keeps its normal horizon. Uncounted for EVERY
                        # failure here, transport errors included -- which is
                        # deliberately NOT the delivery server's own rule two
                        # blocks above, where a transport error is counted and
                        # exhaustible. Nothing was attempted against the
                        # server this row is owed to; a third party being
                        # unreachable must not spend that row's budget and
                        # turn it `failed`.
                        logger.warning(
                            "delivery to %s waits on %s (%s)",
                            delivery.server, identity_name, failure_detail(exc),
                        )
                        await record(
                            session, render.id, delivery.server, "pending",
                            detail=failure_detail(exc),
                            retry_in=RETRY_SECONDS, count_attempt=False,
                        )
                        still_pending += 1
                        _tally(delivery.server, "pending")
                        await rollup(session, render.id)
                        continue

                try:
                    # What the compose below actually produced, which is NOT
                    # `render.badge_fingerprint`: a forced compose deliberately
                    # leaves that column alone, and the two differ routinely --
                    # this pass runs hours later and folds in MDBList's
                    # per-pass ratings, the current definitions digest and,
                    # when the item has no Plex ref, a `plex_item=None` that
                    # drops the resolution/format overlays entirely. Recording
                    # the render's own fingerprint against these bytes is what
                    # would make a server holding visibly different artwork
                    # read as up to date to the catch-up -- the failure mode
                    # the catch-up exists to find.
                    composed: dict = {}
                    data = await _pipeline.compose_badged_bytes(
                        session, row_config, render, item, http=http, mdblist=mdblist,
                        out=composed,
                        server=servers.get(identity_name) if identity_item is not None else None,
                        ref=identity_item.ref if identity_item is not None else None,
                        # A due row means THIS server
                        # does not have these bytes, so the unchanged-fingerprint
                        # gate -- which answers for the servers that DO -- must
                        # not turn this pass into a no-op. The three "not a badge
                        # candidate" checks still apply, and a `None` from one of
                        # those is the `skipped` below.
                        force=True,
                    )
                    if data is None:
                        # A pending row outlives the state that
                        # created it. Badges turned off for this library, or a
                        # render that has since gone `failed`, leaves nothing to
                        # compose -- and uploading `None` would turn that into a
                        # sticky `failed` row with a misleading detail. `skipped`
                        # is the honest outcome, the same one `deliver` records
                        # for a server there is nothing to send to.
                        await record(session, render.id, delivery.server, "skipped")
                        _tally(delivery.server, "skipped")
                        await rollup(session, render.id)
                        continue
                    await target.upload_artwork(
                        resolved_item.ref, data, render.art_kind,
                        row_config.badges.lock_artwork and CAP_LOCK_ARTWORK in target.capabilities,
                    )
                except Exception as exc:
                    # The item DID resolve; compositing or the upload itself is
                    # what failed. That is not "not there yet" -- it is a real
                    # problem against an item we found, the same distinction
                    # apply_badges' own upload except clause draws (records
                    # `failed`, not another `pending`).
                    logger.warning("delivery to %s failed (%s)", delivery.server, failure_detail(exc))
                    await record(session, render.id, delivery.server, "failed", detail=failure_detail(exc))
                    _tally(delivery.server, "failed")
                    await rollup(session, render.id)
                    continue

                await record(
                    session, render.id, delivery.server, "uploaded",
                    fingerprint=composed.get("fingerprint"),
                )
                uploaded += 1
                _tally(delivery.server, "uploaded")
                await rollup(session, render.id)
        except Exception as exc:
            logger.warning(
                "delivery retry for render %s/%s failed unexpectedly (%s)",
                render_id, server_name, type(exc).__name__,
            )
            still_pending += 1
            _tally(server_name, "pending")
        finally:
            # `finally`, not a line after the `try`: every branch above leaves
            # this block with a `continue`, which would step straight over
            # anything written there.
            await _commit_row(server_name, before)

    for write_row, item in metadata_due:
        item_id, server_name = item.id, write_row.server
        before = _counters()
        _tally(server_name, "due")
        # The same SAVEPOINT-per-row isolation the artwork loop documents
        # above: one row's database failure must not abort the pass.
        try:
            async with session.begin_nested():
                target = servers.get(server_name)
                if target is None:
                    await record_metadata(
                        session, item_id, server_name, "failed",
                        detail="config: server removed",
                    )
                    _tally(server_name, "failed")
                    continue
                row_config = config_for_library(config, item.library)
                if not row_config.operations.enabled:
                    # `apply_metadata` returns before it writes a row at all
                    # when operations are off, so this pass would otherwise be
                    # the one code path still writing to a server the operator
                    # has switched off entirely. The row says which switch.
                    await record_metadata(
                        session, item_id, server_name, "skipped",
                        detail="config: operations.enabled is off",
                    )
                    _tally(server_name, "skipped")
                    continue
                if not getattr(row_config.operations, f"write_to_{server_name}", False):
                    await record_metadata(
                        session, item_id, server_name, "skipped",
                        detail=f"config: operations.write_to_{server_name} is off",
                    )
                    _tally(server_name, "skipped")
                    continue
                refs = await refs_for(session, item_id)
                try:
                    resolved_item = await target.resolve(_intent_for(item, refs))
                except PathMismatch as exc:
                    # No retry fixes a mount mismatch (spec §6.2) -- the same
                    # ruling the artwork half makes above.
                    await record_metadata(
                        session, item_id, server_name, "failed", detail=failure_detail(exc),
                    )
                    _tally(server_name, "failed")
                    continue
                except ItemNotFound:
                    # The ONE wait: the item is simply not on this server
                    # yet, which is not an attempt at the write (spec §2's
                    # "a resolution miss").
                    await record_metadata(
                        session, item_id, server_name, "pending",
                        retry_in=RETRY_SECONDS, count_attempt=False,
                    )
                    still_pending += 1
                    _tally(server_name, "pending")
                    continue
                except Exception as exc:
                    # A transport error is NOT a resolution miss: the server
                    # is down, and spec §2's "nothing retries forever" has to
                    # hold for the production failure mode it was written
                    # for. Counted and exhaustible, exactly like the artwork
                    # twin above -- the same event must not spend budget on
                    # one table and not the other, or a Jellyfin that is down
                    # reads `0 failed` on the metadata half forever.
                    logger.warning(
                        "metadata retry on %s failed to resolve (%s)",
                        server_name, failure_detail(exc),
                    )
                    attempts = await record_metadata(
                        session, item_id, server_name, "pending",
                        detail=failure_detail(exc), retry_in=RETRY_SECONDS,
                    )
                    if attempts >= max_attempts:
                        await record_metadata(
                            session, item_id, server_name, "failed",
                            detail=failure_detail(exc),
                        )
                        _tally(server_name, "failed")
                    else:
                        still_pending += 1
                        _tally(server_name, "pending")
                    continue

                await upsert_server_ref(session, item_id, resolved_item)
                # The facts the DATABASE holds, never a fresh provider
                # gather: a retry exists to get what this service already
                # decided onto a server that refused it, and re-gathering
                # would make this a second metadata pipeline with its own
                # provider budget. The `ItemFacts` row is COPIED into a
                # `GatheredFacts` rather than handed over as itself: the two
                # are not duck-compatible, and `facts_from_row` above says
                # why.
                facts = facts_from_row((await session.execute(
                    select(ItemFacts).where(ItemFacts.item_id == item_id)
                )).scalar_one_or_none())
                overrides: dict[str, object] = {}
                if row_config.operations.item_overrides_enabled:
                    overrides = await load_overrides(session, item_id)
                try:
                    exempt = exemption_reason(
                        row_config.operations, resolved_item.native_id, resolved_item.imdb_id,
                        await target.item_labels(resolved_item.ref),
                    )
                    if exempt is not None:
                        await record_metadata(
                            session, item_id, server_name, "skipped", detail=exempt,
                        )
                        _tally(server_name, "skipped")
                        continue
                    # `parental_categories=None`: row 85's categories are
                    # fetched by the full pass, which holds the IMDb client.
                    # Passing None means this retry writes the facts and
                    # leaves those labels to the pass that owns them.
                    await target.apply_facts(
                        resolved_item.ref, facts, row_config.operations, None, overrides,
                    )
                except AttributeError:
                    # `apply_metadata._write`'s own convention: a server
                    # missing `item_labels` or `apply_facts` is a wiring bug
                    # -- a programming error, not the runtime server failure
                    # this handler is for -- and must not become a row that
                    # spends the budget and then sticks at `failed`.
                    raise
                except Exception as exc:
                    logger.warning(
                        "metadata retry on %s failed (%s)", server_name, failure_detail(exc),
                    )
                    attempts = await record_metadata(
                        session, item_id, server_name, "pending",
                        detail=failure_detail(exc), retry_in=RETRY_SECONDS,
                    )
                    if attempts >= max_attempts:
                        await record_metadata(
                            session, item_id, server_name, "failed",
                            detail=failure_detail(exc),
                        )
                        _tally(server_name, "failed")
                    else:
                        still_pending += 1
                        _tally(server_name, "pending")
                    continue
                # Row 269's sort-position tombstone is deliberately NOT
                # deleted here, unlike in `apply_metadata`: the clear this
                # write sent is idempotent, so the next full pass re-sends it
                # and deletes the row then -- and a delete here would need a
                # commit of its own, which the savepoint above owns.
                await record_metadata(session, item_id, server_name, "written")
                written += 1
                _tally(server_name, "written")
        except Exception as exc:
            logger.warning(
                "metadata retry for item %s/%s failed unexpectedly (%s)",
                item_id, server_name, type(exc).__name__,
            )
            still_pending += 1
            _tally(server_name, "pending")
        finally:
            await _commit_row(server_name, before)

    # Summed from the per-server counts rather than carried as two more
    # running totals: the head used to name three numbers that could not
    # reconcile -- a row that went `failed` was in neither `done` nor `still
    # pending`, so a pass that failed both its rows read `2 due, 0 done, 0
    # still pending` -- and the head is the half the dashboard shows first.
    failed = sum(c["failed"] for c in per_server.values())
    skipped = sum(c["skipped"] for c in per_server.values())
    summary = (
        f"pending deliveries: {len(due) + len(metadata_due)} due, "
        f"{uploaded + written} done, {still_pending} still pending, "
        f"{failed} failed, {skipped} skipped"
    )
    if per_server:
        # Spec §2's sentence, one clause per server, sorted so the run
        # history's detail reads the same way twice for the same work.
        summary += "; " + "; ".join(
            f"{name}: {c['due']} due, {c['uploaded']} uploaded, {c['written']} written, "
            f"{c['pending']} pending, {c['failed']} failed, {c['skipped']} skipped"
            for name, c in sorted(per_server.items())
        )
    # No closing commit: every row committed its own outcome as soon as its
    # side effect had happened (`_commit_row`), so there is nothing left here
    # to lose. A pass with no due rows leaves only the two SELECTs' read
    # transaction, which the caller's `async with session_factory()` closes.
    return summary


# The statuses that are NOT an ending. Everything else either table can hold
# -- `uploaded`, `written`, `skipped`, `absent` -- is a settled outcome and
# has nothing left to warn about.
_UNSETTLED = ("pending", "failed")


async def outcome_warnings(
    session: AsyncSession, item_id: int, servers: Iterable[str], *,
    misses: Iterable[str] = (),
) -> str | None:
    """One sentence naming every server this item still owes something to.

    The ``done_with_warnings`` half of spec §4. A job ends ``done`` only when
    every server it touched recorded ``uploaded``, ``written``, ``skipped``
    or ``absent``; anything still ``pending`` or ``failed`` is named here,
    with the stored detail in brackets -- which is already a category and a
    class name and never a URL, because ``failure_detail`` is the only thing
    that writes one.

    ``servers`` is the names THIS pass touched. A server the pass never
    considered is not this job's warning, even if it has an old row of its
    own -- which is what keeps a job about one server from reporting
    another's unrelated backlog, and what keeps a server the deployment no
    longer configures out of the sentence entirely.

    ``misses`` is the subset of those that did not resolve at all, named as
    ``<server>: not found``. Rows alone are not enough to answer for one: a
    missed server gets a ``pending`` delivery row only when the badge stage
    runs AND that server's upload toggle is on, and it gets no
    ``metadata_writes`` row at all, since those are written per RESOLVED
    server. So on a badges-off deployment the item a server has never scanned
    left no row anywhere and the job finished plain ``done`` -- the exact
    silence this state exists to break. A server that has an unsettled ROW is
    reported by the row: that says more than "not found" does, and a server
    cannot honestly be both.

    A miss on a server whose ``absent`` row says it does not carry this
    item's library must never reach here -- spec §1 says such an item "is
    never resolved there, and is never retried", so nothing is owed. Keeping
    that set out of BOTH arguments is the caller's job: the caller is what
    knows which servers it skipped for being absent and which it asked anyway
    because the item's identity was not yet established.
    """
    wanted = set(servers)
    if not wanted:
        return None
    missed = set(misses) & wanted
    artwork = (await session.execute(
        select(RenderDelivery.server, RenderDelivery.status, RenderDelivery.detail)
        .join(Render, Render.id == RenderDelivery.render_id)
        .where(Render.item_id == item_id, RenderDelivery.status.in_(_UNSETTLED))
    )).all()
    metadata = (await session.execute(
        select(MetadataWrite.server, MetadataWrite.status, MetadataWrite.detail)
        .where(MetadataWrite.item_id == item_id, MetadataWrite.status.in_(_UNSETTLED))
    )).all()
    clauses: list[tuple[str, str, str]] = []
    # `artwork` first so the sort below names a server's art before its
    # metadata, which is the order the item page's own table uses.
    for kind, rows in (("artwork", artwork), ("metadata", metadata)):
        for server_name, status, detail in rows:
            if server_name not in wanted:
                continue
            clause = f"{server_name}: {kind} {status}"
            if detail:
                clause += f" ({detail})"
            clauses.append((server_name, kind, clause))
    # The misses that no row already answers for. Sorted in with the rest by
    # server name; the empty kind is a sort slot, not a word -- a server with
    # a row is never also reported as a miss, so it never renders.
    named_by_a_row = {server_name for server_name, _kind, _clause in clauses}
    for server_name in missed - named_by_a_row:
        clauses.append((server_name, "", f"{server_name}: not found"))
    if not clauses:
        return None
    # One clause per rendered sentence fragment: a movie has one poster and
    # one background, and naming the same server twice for two art kinds that
    # failed the same way would make the sentence longer without saying
    # anything more. De-duplicated on the RENDERED clause, so two identical
    # failures collapse and two different ones do not.
    seen: set[str] = set()
    ordered: list[str] = []
    for _server_name, _kind, clause in sorted(clauses):
        if clause in seen:
            continue
        seen.add(clause)
        ordered.append(clause)
    return "; ".join(ordered)
