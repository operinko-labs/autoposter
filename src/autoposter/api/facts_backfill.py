"""The one-shot facts backfill trigger (roadmap row 206).

The ops/* precedent (``api/collections_builders.py``), applied to a facts op:
session-auth'd, JSON status back, one ``events_log`` row per write. NOT behind
``collections.enabled`` and NOT behind a Plex connection -- this touches only
the database and the job queue, so it works on any replica; the workers that
drain the queue are where Plex and the providers come in.

``POST`` enqueues ONE batch of ``scheduler.drift_batch_size`` and advances the
cursor; the operator presses again until the answer is "complete" (the sweep's
own docstring says why everything-at-once is wrong: the worker pool and every
provider budget). While TMDb's shared 429 window is open the trigger PARKS --
status ``parked``, nothing enqueued, cursor unmoved -- because a batch
gathered with TMDb skipped stamps ``fetched_at`` anyway and silently loses the
columns this sweep exists to fill until the drift sweep ages them back in.

``GET`` answers the standing progress so the button can render it on page
load. Progress is measured against the LIVE parent count, never the 2252 the
roadmap row measured in production.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from autoposter.api.auth import require_session
from autoposter.db.models import EventLog, FactsBackfillState, MediaItem
from autoposter.db.models import Session as SessionModel
from autoposter.facts.tmdb_budget import TmdbRateBudget
from autoposter.scheduler.jobs import backfill_facts

router = APIRouter()

_PARENT_KINDS = ("movie", "show")


async def _progress(session) -> tuple[int, int, int | None]:
    """``(done, total, cursor)`` against the live population."""
    state = (
        await session.execute(
            select(FactsBackfillState).where(FactsBackfillState.id == 1)
        )
    ).scalar_one_or_none()
    cursor = state.cursor_item_id if state is not None else None
    total = (
        await session.execute(
            select(func.count()).select_from(MediaItem)
            .where(MediaItem.kind.in_(_PARENT_KINDS))
        )
    ).scalar_one()
    if cursor is None:
        return 0, total, None
    done = (
        await session.execute(
            select(func.count()).select_from(MediaItem)
            .where(MediaItem.kind.in_(_PARENT_KINDS))
            .where(MediaItem.id <= cursor)
        )
    ).scalar_one()
    return done, total, cursor


@router.get("/facts/backfill")
async def backfill_status(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """The standing progress: how much of the parent population a trigger has
    walked. Reads only -- pressing nothing costs nothing."""
    async with request.app.state.session_factory() as session:
        done, total, cursor = await _progress(session)
    # Completion is derived FIRST, and by the one rule that is provably the
    # POST's own: ``done >= total`` <=> no parent past the cursor <=>
    # ``backfill_facts`` selects 0. Testing ``cursor is None`` first would let
    # an empty library answer ``not_started`` here forever while every POST
    # answered ``complete`` -- the two endpoints disagreeing about one state,
    # and the state a disabled-button consumer keys off.
    if done >= total:
        status = "complete"
    elif cursor is None:
        status = "not_started"
    else:
        status = "in_progress"
    return {"status": status, "done": done, "total": total}


@router.post("/facts/backfill")
async def backfill_trigger(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """One batch: stamp, enqueue, advance the cursor -- or park, or report
    completion idempotently. Always 200 with a ``status`` field: ``parked``
    and ``complete`` are expected answers to an honest question, not errors
    for the client to style as failures."""
    config = request.app.state.config_holder.current
    session_factory = request.app.state.session_factory

    # A fresh budget object per request is deliberate: the window itself is a
    # database row on the database clock (facts/tmdb_budget.py), so this
    # carries no state of its own and shares the workers' window exactly.
    budget = TmdbRateBudget(session_factory, config.operations.tmdb_backoff_seconds)
    if await budget.blocked():
        async with session_factory() as session:
            done, total, _cursor = await _progress(session)
        return {
            "status": "parked", "enqueued": 0, "done": done, "total": total,
            "detail": (
                "TMDb is inside its 429 backoff window; nothing was enqueued "
                "and the cursor did not move. Trigger again in a minute."
            ),
        }

    async with session_factory() as session:
        batch = await backfill_facts(session, config.scheduler.drift_batch_size)
        done, total, _cursor = await _progress(session)
        if batch.selected == 0:
            detail = (
                "complete: all %d movie/show item(s) have been walked" % total
            )
        else:
            detail = "enqueued %d item(s); %d of %d walked so far" % (
                batch.enqueued, done, total,
            )
        # The ops rule: a write nobody can find afterwards is not an operator
        # action, it is a mystery. One row per trigger that reaches this point,
        # the completes included -- an operator reading the table should see
        # the walk. (The park above returns before here and writes no row.)
        session.add(EventLog(
            source="facts",
            event_type="facts_backfill",
            payload={
                "selected": batch.selected, "enqueued": batch.enqueued,
                "done": done, "total": total,
            },
            outcome="facts backfill: " + detail,
        ))
        await session.commit()

    return {
        "status": "complete" if batch.selected == 0 else "enqueued",
        "enqueued": batch.enqueued, "done": done, "total": total,
        "detail": detail,
    }
