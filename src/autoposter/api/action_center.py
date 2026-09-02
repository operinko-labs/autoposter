"""The Action Center: the curation queue over the artwork this service chose.

Roadmap rows 11a/11b. The unit is one ``renders`` row -- one chosen artwork
asset, which is exactly what ``UNIQUE(item_id, art_kind)`` already keys -- and
every reason a row is in the queue is a SQL predicate from
``actions/flags.py``, built against the config that is live for THIS request.
Nothing here stores a verdict: re-pointing ``language_order`` or
``providers.order`` re-shapes every list and every count with no row write.

Its own module rather than more of ``routes.py`` because the queries here are
a different kind. Every other listing selects columns; these select predicates
-- once into the WHERE clause and again as labelled boolean columns, so what a
row is *shown* as cannot drift from what it was *filtered* by.

Every action is an ENQUEUE. ``_enqueue_reprocess`` plus the
``uq_jobs_pending_dedupe`` partial index is what answers 11b's own stated risk
-- "one 're-search all 400 flagged items' click is a burst": the bulk press
collapses to distinct items, asking twice while the first is pending queues
nothing the second time, and the worker pool paces the provider calls exactly
as an ordinary pass does. There is no inline provider call anywhere in this
file.

Two honest caveats that the page's copy repeats, and that no amount of UI can
remove:

 * A re-search is a re-*search*, not a guarantee. An already-rendered row
   re-runs provider selection against the 24h ``provider_cache`` and may
   legitimately find the same art and stay flagged.
 * The work is queued per ITEM, because ``process_item`` is the only unit the
   queue has. Re-searching a flagged poster re-renders that item's background
   too.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.dialects.postgresql import insert

from autoposter.actions import flags
from autoposter.api.auth import require_session
from autoposter.db.models import ActionDismissal, EventLog, MediaItem, Render
from autoposter.db.models import Session as SessionModel

router = APIRouter()

DEFAULT_ACTIONS_LIMIT = 50
MAX_ACTIONS_LIMIT = 200


def _flag_predicate(config, flag: str | None):
    """The WHERE clause one chip means, or the default population for no chip.

    An unknown code is a 400 naming what this build has, not a silently
    unfiltered queue: a typo in a bookmarked URL would otherwise answer with
    the whole library and read as the filter working.
    """
    if flag is None:
        return flags.default_predicate(config)
    try:
        return flags.predicate_for(flag, config)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown flag {flag!r}; this build has: " + ", ".join(flags.FLAGS)
            ),
        ) from None


def _dismissal_join():
    """The LEFT JOIN condition that hides a dismissed row -- and only while its
    facts hold.

    The evidence equality is the whole mechanism. ``evidence_expression()``
    recomputes the hash from the row's current columns on every read, so a
    re-render that changes a fact makes this condition stop matching and the
    row returns on its own: no sweep, no invalidation job, no resurrection
    bug. ``UNIQUE(item_id, art_kind)`` on the dismissals table means this join
    can never multiply a row, which is what keeps ``total`` honest.
    """
    return and_(
        ActionDismissal.item_id == Render.item_id,
        ActionDismissal.art_kind == Render.art_kind,
        ActionDismissal.evidence == flags.evidence_expression(),
    )


def _scope(library: str | None, art_kind: str | None, include_dismissed: bool) -> list:
    conditions = []
    if library is not None:
        conditions.append(MediaItem.library == library)
    if art_kind is not None:
        conditions.append(Render.art_kind == art_kind)
    if not include_dismissed:
        conditions.append(ActionDismissal.id.is_(None))
    return conditions


@router.get("/actions")
async def list_actions(
    request: Request,
    flag: str | None = None,
    library: str | None = None,
    art_kind: str | None = None,
    include_dismissed: bool = False,
    limit: int = DEFAULT_ACTIONS_LIMIT,
    offset: int = 0,
    _: SessionModel = Depends(require_session),
) -> dict:
    """One page of the queue, with the whole match's `total`.

    Each registered flag is selected a second time as a labelled boolean, so
    the row's own ``flags`` list is computed by the same SQL that filtered it.
    Deriving the labels in Python instead would be a second definition of each
    flag, and the two would eventually disagree about the same row.

    The order is total -- ``updated_at DESC, id DESC``. ``updated_at`` alone is
    not: a full pass stamps thousands of rows inside one transaction
    timestamp, and page 2 would hand back rows page 1 already showed.
    """
    config = request.app.state.config_holder.current
    capped_limit = min(max(limit, 1), MAX_ACTIONS_LIMIT)
    capped_offset = max(offset, 0)
    conditions = [_flag_predicate(config, flag)]
    conditions.extend(_scope(library, art_kind, include_dismissed))

    labelled = [
        entry.predicate(config).label(f"is_{code}") for code, entry in flags.FLAGS.items()
    ]

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # A separate COUNT(*), not len() of a fetched result: renders runs to
        # tens of thousands of rows.
        total = (
            await session.execute(
                select(func.count())
                .select_from(Render)
                .join(MediaItem, Render.item_id == MediaItem.id)
                .outerjoin(ActionDismissal, _dismissal_join())
                .where(*conditions)
            )
        ).scalar_one()

        rows = (
            await session.execute(
                select(
                    Render,
                    MediaItem.title,
                    MediaItem.library,
                    MediaItem.kind,
                    flags.evidence_expression().label("evidence"),
                    ActionDismissal.id.isnot(None).label("dismissed"),
                    *labelled,
                )
                .join(MediaItem, Render.item_id == MediaItem.id)
                .outerjoin(ActionDismissal, _dismissal_join())
                .where(*conditions)
                .order_by(Render.updated_at.desc(), Render.id.desc())
                .limit(capped_limit)
                .offset(capped_offset)
            )
        ).all()

    items = []
    for row in rows:
        render = row[0]
        fired = [code for code in flags.FLAGS if getattr(row, f"is_{code}")]
        items.append(
            {
                "item_id": render.item_id,
                "art_kind": render.art_kind,
                "title": row.title,
                "library": row.library,
                "kind": row.kind,
                "status": render.status,
                "upload_status": render.upload_status,
                "provider": render.provider,
                "flags": fired,
                # The factual sentence behind each flag, in the same order, so
                # the page renders the server's own words rather than
                # restating a fact it would have to keep in step.
                "details": [flags.detail_for(code, render) for code in fired],
                "dismissed": bool(row.dismissed),
                "evidence": row.evidence,
                "quality_scored_at": render.quality_scored_at,
                "updated_at": render.updated_at,
            }
        )

    return {"total": total, "limit": capped_limit, "offset": capped_offset, "items": items}


@router.get("/actions/summary")
async def actions_summary(
    request: Request,
    library: str | None = None,
    art_kind: str | None = None,
    include_dismissed: bool = False,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Every flag's count, and the default population's total, in one query.

    One grouped pass with a conditional sum per flag rather than a query per
    chip: eleven round trips for a header row would be eleven sequential scans
    of the same table.

    The registry's label, description and both switches ride along, so the
    page renders the chips the server actually has rather than a list of its
    own that a new flag would silently fall out of.
    """
    config = request.app.state.config_holder.current
    conditions = _scope(library, art_kind, include_dismissed)

    counters = [
        func.sum(case((entry.predicate(config), 1), else_=0)).label(f"n_{code}")
        for code, entry in flags.FLAGS.items()
    ]
    counters.append(
        func.sum(case((flags.default_predicate(config), 1), else_=0)).label("n_default")
    )

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        row = (
            await session.execute(
                select(*counters)
                .select_from(Render)
                .join(MediaItem, Render.item_id == MediaItem.id)
                .outerjoin(ActionDismissal, _dismissal_join())
                .where(*conditions)
            )
        ).one()

    # SUM over no rows is NULL, not 0 -- an empty library must read as zero
    # counts rather than as eleven nulls the page would render as blanks.
    def count(name: str) -> int:
        return int(getattr(row, name) or 0)

    return {
        "total": count("n_default"),
        "flags": [
            {
                "code": code,
                "label": entry.label,
                "description": entry.description,
                "default_on": entry.default_on,
                "instant": entry.instant,
                "count": count(f"n_{code}"),
            }
            for code, entry in flags.FLAGS.items()
        ],
    }


class DismissBody(BaseModel):
    item_id: int
    art_kind: str
    #: Which chip the operator was looking at. Recorded for the audit; it does
    #: not narrow what the dismissal covers -- see ActionDismissal's docstring.
    flag: str | None = None
    note: str | None = Field(default=None, max_length=500)


@router.post("/actions/dismiss")
async def dismiss_action(
    body: DismissBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Stop showing this render row while its facts stay as they are.

    The evidence is computed by the database, from the same expression the
    queue's join recomputes on every read. Computing it here in Python instead
    would be a second definition of "these facts", and the day the two
    disagreed every dismissal would either stick forever or never stick at
    all.

    Upserted rather than inserted: ``UNIQUE(item_id, art_kind)`` makes a
    second press a conflict, and an operator re-dismissing a row whose facts
    have moved must get a fresh dismissal, not a 500.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        evidence = (
            await session.execute(
                select(flags.evidence_expression()).where(
                    Render.item_id == body.item_id, Render.art_kind == body.art_kind
                )
            )
        ).scalar_one_or_none()
        if evidence is None:
            raise HTTPException(
                status_code=404, detail="no render row for that item and art kind"
            )

        await session.execute(
            insert(ActionDismissal)
            .values(
                item_id=body.item_id,
                art_kind=body.art_kind,
                flag=body.flag,
                evidence=evidence,
                note=body.note,
            )
            .on_conflict_do_update(
                constraint="uq_action_dismissal_item_kind",
                set_={
                    "evidence": evidence,
                    "flag": body.flag,
                    "note": body.note,
                    "dismissed_at": func.now(),
                },
            )
        )
        await session.commit()

    return {"dismissed": True, "evidence": evidence}


class UndismissBody(BaseModel):
    item_id: int
    art_kind: str


@router.post("/actions/undismiss")
async def undismiss_action(
    body: UndismissBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Put the row back in the queue.

    Deleting the row rather than flagging it: a dismissal is a hiding rule and
    nothing else, it carries no history worth keeping, and a soft-deleted one
    would have to be excluded from the join that is the whole mechanism.
    Undismissing something that was never dismissed is a no-op answering 200,
    because that is what the operator asked for and it is now true.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await session.execute(
            delete(ActionDismissal).where(
                ActionDismissal.item_id == body.item_id,
                ActionDismissal.art_kind == body.art_kind,
            )
        )
        await session.commit()
    return {"dismissed": False}


class RerenderBody(BaseModel):
    item_id: int


@router.post("/actions/rerender")
async def rerender_action(
    body: RerenderBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Queue one item for another pass.

    A 200 with ``queued: false`` when the pending dedupe swallows it, not a
    409: the work IS queued, which is what the operator wanted, and styling
    that as a failure would teach them to press again. This mirrors
    ``/items/{id}/reprocess`` exactly, which is the point -- two enqueue
    surfaces that answered differently would be two contracts.
    """
    # Deferred to here rather than imported at module scope: api/routes.py
    # imports this module's router, so the other direction is a cycle. Shared
    # rather than hand-built so the dedupe key cannot drift from the one every
    # other intake path uses.
    from autoposter.api.routes import _enqueue_reprocess

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == body.item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        job_id = await _enqueue_reprocess(session, item)
    return {"queued": job_id is not None, "job_id": job_id}


class BulkRerenderBody(BaseModel):
    flag: str | None = None
    library: str | None = None
    art_kind: str | None = None
    include_dismissed: bool = False
    #: False is the unguarded offer -- it writes nothing and answers with the
    #: numbers. True is what the page's two-step arm sends.
    apply: bool = False


@router.post("/actions/bulk/rerender")
async def bulk_rerender_action(
    body: BulkRerenderBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Queue one batch of the current filter, or count what one would queue.

    Always a 200 with a ``status`` field. ``dry run``, ``enqueued`` and
    ``complete`` are all expected answers to an honest question, not errors
    for the client to style as failures -- the ``facts_backfill`` precedent.

    One batch of ``scheduler.drift_batch_size`` per press, not the whole
    match: the response has to stay bounded, and the operator pressing again
    is the pacing. ``matched`` counts render ROWS and ``items`` counts the
    distinct items in this batch, because the queue's unit is a row and the
    work's unit is an item -- 400 flagged rows across 200 items are 200 jobs.
    ``enqueued`` is jobs actually created, so a batch the pending dedupe
    swallowed reports 0 rather than claiming work it did not queue.

    One ``events_log`` row per applied press, the ops rule: a write nobody can
    find afterwards is not an operator action, it is a mystery. The dry run
    writes none, because it changed nothing.
    """
    from autoposter.api.routes import _enqueue_reprocess

    config = request.app.state.config_holder.current
    conditions = [_flag_predicate(config, body.flag)]
    conditions.extend(_scope(body.library, body.art_kind, body.include_dismissed))
    batch_size = config.scheduler.drift_batch_size

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        matched = (
            await session.execute(
                select(func.count())
                .select_from(Render)
                .join(MediaItem, Render.item_id == MediaItem.id)
                .outerjoin(ActionDismissal, _dismissal_join())
                .where(*conditions)
            )
        ).scalar_one()

        item_ids = (
            (
                await session.execute(
                    select(Render.item_id)
                    .join(MediaItem, Render.item_id == MediaItem.id)
                    .outerjoin(ActionDismissal, _dismissal_join())
                    .where(*conditions)
                    .group_by(Render.item_id)
                    .order_by(Render.item_id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

        if not body.apply:
            return {
                "status": "dry run",
                "matched": matched,
                "items": len(item_ids),
                "enqueued": 0,
                "detail": (
                    f"{matched} flagged row(s) across {len(item_ids)} item(s) in this "
                    "batch. Nothing was queued."
                ),
            }

        items = (
            (await session.execute(select(MediaItem).where(MediaItem.id.in_(item_ids))))
            .scalars()
            .all()
        ) if item_ids else []

        enqueued = 0
        for item in items:
            if await _enqueue_reprocess(session, item) is not None:
                enqueued += 1

        status = "complete" if not item_ids else "enqueued"
        detail = (
            "complete: nothing matches this filter"
            if not item_ids
            else (
                f"queued {enqueued} of {len(item_ids)} item(s) carrying {matched} "
                "flagged row(s); a re-search may legitimately find the same art"
            )
        )
        session.add(
            EventLog(
                source="actions",
                event_type="action_center_bulk_rerender",
                payload={
                    "flag": body.flag,
                    "library": body.library,
                    "art_kind": body.art_kind,
                    "matched": matched,
                    "items": len(item_ids),
                    "enqueued": enqueued,
                },
                outcome="action center bulk re-search: " + detail,
            )
        )
        await session.commit()

    return {
        "status": status,
        "matched": matched,
        "items": len(item_ids),
        "enqueued": enqueued,
        "detail": detail,
    }
