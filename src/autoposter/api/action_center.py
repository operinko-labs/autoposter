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
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.dialects.postgresql import insert

from autoposter.actions import flags
from autoposter.api.auth import require_session
from autoposter.db.models import ActionDismissal, EventLog, Job, MediaItem, Render
from autoposter.db.models import Session as SessionModel
from autoposter.db.refs import native_ids, refs_for_items
from autoposter.intake.arr import RenderIntent
from autoposter.queue.jobs import enqueue_batch

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
        # Names what this build has, never the value asked for: the query
        # string would otherwise be reflected into a served body.
        raise HTTPException(
            status_code=400,
            detail="unknown flag; this build has: " + ", ".join(flags.FLAGS),
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


def _undismissed(stmt):
    """Attach the dismissal anti-join to a select over ``renders``.

    The same two halves ``_scope`` applies -- ``_dismissal_join()`` as a LEFT
    OUTER JOIN, then ``ActionDismissal.id.is_(None)`` as a WHERE term -- kept
    in one call because they are only correct together: the join without the
    null test hides nothing, and the null test without the join is an error
    against a table that is not in the FROM list. It must stay OUTER: an
    inner join here drops every row that was never dismissed, which is nearly
    the whole library.

    It exists because the quality backfill's queries do not go through
    ``_scope`` and cannot -- they take no ``library``/``art_kind``, they
    answer a population question rather than build a page, and one of them
    selects ``MediaItem`` rather than ``Render``. They still have to give the
    SAME answer about the SAME row: ``_backfill_state`` and
    ``_select_backfill_batch`` queried ``renders`` and ``jobs`` with no
    ``ActionDismissal`` join at all, so a dismissed unscored row sat inside
    ``unscored_total`` and read as ``queued_for_scoring`` while the "Not yet
    scored" chip and its listing showed nothing -- observed in production on
    2026-09-04 as "0 of 2 unscored asset(s) queued for scoring" above a chip
    reading 0.

    A dismissal is the operator saying "leave this one", so dismissing an
    unscored row MEANS stop counting it as unscored -- the chip's reading,
    and now the header's. That is safe to do to a COUNT because the hiding
    expires on its own: ``evidence_expression()`` hashes
    ``quality_scored_at IS NOT NULL`` among the row's facts, so the day the
    row is scored, or re-rendered into any other fact, this join stops
    matching and the row returns to the header and the chip together.

    ``UNIQUE(item_id, art_kind)`` on the dismissals table means the join can
    never multiply a row, so a ``COUNT(*)`` through it stays a count of
    ``renders`` rows -- the property ``_dismissal_join``'s own docstring
    names for ``total``, and the reason ``done`` and ``unscored_count`` stay
    comparable after this change.
    """
    return stmt.outerjoin(ActionDismissal, _dismissal_join()).where(
        ActionDismissal.id.is_(None)
    )


def _reprocess_entries(
    items: list[MediaItem], plex_ids: dict[int, str]
) -> list[tuple[dict, str]]:
    """Build ``enqueue_batch``'s ``(payload, dedupe_key)`` entries for a list
    of items.

    Field-for-field the same ``RenderIntent`` construction as routes.py's
    ``_enqueue_reprocess``, so the single-item and batch enqueue paths cannot
    disagree about what one item's job looks like. ``plex_ids`` is the whole
    batch's Plex native ids, one query for every caller (``db/refs.native_ids``),
    not one per item.
    """
    entries = []
    for item in items:
        intent = RenderIntent(
            kind=item.kind,
            title=item.title,
            tmdb_id=item.tmdb_id,
            tvdb_id=item.tvdb_id,
            imdb_id=item.imdb_id,
            year=item.year,
            season_number=item.season_number,
            episode_number=item.episode_number,
            refs={"plex": plex_ids[item.id]} if item.id in plex_ids else {},
        )
        entries.append((asdict(intent), intent.dedupe_key))
    return entries


def _scope(config, library: str | None, art_kind: str | None, include_dismissed: bool) -> list:
    """The conditions every queue query shares.

    It LEADS with the excluded-library predicate rather than offering it as an
    option: a row in an excluded library is not a narrower view of the queue,
    it is not part of the queue at all -- nothing can re-render it, so listing
    it, counting it or enqueueing work for it is noise an operator cannot act
    on. Applied here rather than at each endpoint so the listing, the chip
    counts and the bulk re-search cannot disagree about the same row.
    """
    conditions = [flags.excluded_library_predicate(config)]
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
    conditions.extend(_scope(config, library, art_kind, include_dismissed))

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
    conditions = _scope(config, library, art_kind, include_dismissed)

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
    #: Bounded like `note`, and checked against the registry: the column is
    #: `String(32)` (db/models.py), and a chip that does not exist is not a
    #: fact worth storing.
    flag: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("flag")
    @classmethod
    def _must_be_a_known_flag(cls, value: str | None) -> str | None:
        if value is not None and value not in flags.FLAGS:
            raise ValueError(
                f"unknown flag {value!r}; this build has: " + ", ".join(flags.FLAGS)
            )
        return value


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

    The batch's own jobs are enqueued through ``enqueue_batch`` -- one set-based
    INSERT rather than one committed ``enqueue()`` per item, which is what let a
    500-item batch hold the request open for up to 500 sequential commits.
    """
    config = request.app.state.config_holder.current
    conditions = [_flag_predicate(config, body.flag)]
    conditions.extend(_scope(config, body.library, body.art_kind, body.include_dismissed))
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
                    f"{matched} flagged row(s) matched this filter; this batch covers "
                    f"{len(item_ids)} item(s) of it. Nothing was queued."
                ),
            }

        items = (
            (await session.execute(select(MediaItem).where(MediaItem.id.in_(item_ids))))
            .scalars()
            .all()
        ) if item_ids else []

        # One query for the whole batch's Plex ids, not one per item.
        plex_ids = await native_ids(session, [item.id for item in items], "plex")
        enqueued = await enqueue_batch(
            session, "process_item", _reprocess_entries(items, plex_ids)
        )

        status = "complete" if not item_ids else "enqueued"
        detail = (
            "complete: nothing matches this filter"
            if not item_ids
            else (
                f"{matched} flagged row(s) matched this filter; this batch covered "
                f"{len(item_ids)} item(s) of it, queued {enqueued}; a re-search may "
                "legitimately find the same art"
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


class RebuildRow(BaseModel):
    """One render row, keyed the way the queue keys them.

    ``UNIQUE(item_id, art_kind)`` on ``renders`` is what makes this pair an
    identity rather than a filter, which is why the row button can send it and
    mean exactly one asset.
    """

    item_id: int
    art_kind: str


class RebuildBody(BaseModel):
    """Either one named row, or the filter the bulk bar is showing.

    Both forms share ONE body and one endpoint because they are one action
    with one contract: the same predicate vocabulary, the same
    ``scheduler.drift_batch_size`` cap, the same response, the same auth. Two
    endpoints would be two contracts to keep in step, which is the reasoning
    ``_reprocess_entries`` already records for the two enqueue paths.

    ``row`` set means the operator pressed a row. ``row`` unset means the
    filter fields below describe the batch, field-for-field as
    ``BulkRerenderBody`` does.
    """

    row: RebuildRow | None = None
    flag: str | None = None
    library: str | None = None
    art_kind: str | None = None
    include_dismissed: bool = False
    #: False is the unguarded offer -- it writes nothing and answers with the
    #: numbers. True is what the page's two-step arm sends. A single-row press
    #: sends True with no arm: one row is what the operator clicked, and the
    #: action is reversible by construction.
    apply: bool = False


@router.post("/actions/rebuild")
async def rebuild_action(
    body: RebuildBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Rebuild the selected render(s): clear the fingerprints, queue the items.

    Roadmap row 233, ruled 2026-09-08 -- **delete means REBUILD**. Nothing is
    unlinked from the asset tree and nothing is asked of Plex. The published
    asset is replaced only when the new render lands, and ``_publish``
    (``render/pipeline.py``) keeps the outgoing generation as the backup copy,
    so this action is reversible by construction. An unlink would not be: the
    uploaded copy in Plex cannot be reclaimed, and unlinking first means the
    next render finds no target and writes no backup at all.

    Clearing the fingerprint is the substance, and it is the same move
    ``POST /actions/backfill`` already makes for the same reason -- the
    pipeline's unchanged-fingerprint short-circuit returns ABOVE the
    write-back (``render/pipeline.py:1448``), so an enqueue on its own
    re-renders nothing when the ladder picks the same artwork. That gap is
    precisely what this action exists to serve; ``/actions/rerender`` already
    covers the case where the inputs moved. ``badge_fingerprint`` goes with
    it, exactly as ``/items/{id}/renders/{kind}/clear-override`` clears both.

    Dismissals are NOT touched. A dismissal is scoped by the row's FACTS
    (``flags._EVIDENCE_COLUMNS``) and ``fingerprint`` is not one of them, so
    the hiding rule survives the clear by construction -- and that is right:
    the day the rebuild LANDS and moves any fact, ``_dismissal_join()`` stops
    matching and the row returns on its own. Deleting the dismissal here would
    be a second invalidation path competing with the self-expiring one.

    Always a 200 with a ``status``. ``dry run``, ``enqueued`` and ``complete``
    are all answers to an honest question, not errors for the client to style
    as failures. A second press on a row already cleared is a no-op that still
    reports (``cleared: 0``) rather than a 409: the state the operator asked
    for is the state the row is in. A press on a row that is no longer in the
    queue answers ``complete`` with zeroes rather than
    ``/actions/rerender``'s 404 -- one endpoint serves both forms, and the
    bulk form must answer 200 to an empty filter.

    Row 213: the body carries counts and Plex rating keys and nothing else --
    no asset path, no host, no free text. ``status`` is one of three fixed
    literals, and the ``events_log`` outcome is a fixed sentence with numbers.
    """
    config = request.app.state.config_holder.current
    batch_size = config.scheduler.drift_batch_size

    if body.row is not None:
        # The excluded-library predicate leads here too, for `_scope`'s own
        # stated reason: a row in an excluded library is not a narrower view
        # of the queue, it is not in the queue at all -- nothing can
        # re-render it, so clearing its fingerprint would strand it cleared.
        conditions = [
            flags.excluded_library_predicate(config),
            Render.item_id == body.row.item_id,
            Render.art_kind == body.row.art_kind,
        ]
    else:
        conditions = [_flag_predicate(config, body.flag)]
        conditions.extend(
            _scope(config, body.library, body.art_kind, body.include_dismissed)
        )

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

        # The ROWS, not just their item ids: this endpoint's write is on the
        # render row, so the cap has to bound the rows it clears. Ordered by
        # `id` so a second press over an unchanged filter takes the same batch.
        batch = (
            (
                await session.execute(
                    select(Render)
                    .join(MediaItem, Render.item_id == MediaItem.id)
                    .outerjoin(ActionDismissal, _dismissal_join())
                    .where(*conditions)
                    .order_by(Render.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

        if not body.apply:
            # Returned BEFORE any assignment below: a dry run that had already
            # NULLed the ORM objects would clear a batch's worth on whatever
            # flush this session made next. `cleared` and `items` still
            # answer the button's own question -- what a rebuild WOULD do --
            # read-only, the same split `bulk_rerender_action`'s dry run
            # keeps between `items` (honest) and `enqueued` (a write count
            # that stays zero because nothing was queued).
            preview_item_ids: list[int] = []
            would_clear = 0
            for render in batch:
                if render.fingerprint is not None or render.badge_fingerprint is not None:
                    would_clear += 1
                if render.item_id not in preview_item_ids:
                    preview_item_ids.append(render.item_id)
            return {
                "status": "dry run",
                "matched": matched,
                "selected": len(batch),
                "cleared": would_clear,
                "items": len(preview_item_ids),
                "enqueued": 0,
                "items_detail": [],
            }

        cleared = 0
        item_ids: list[int] = []
        for render in batch:
            if render.fingerprint is not None or render.badge_fingerprint is not None:
                cleared += 1
            render.fingerprint = None
            render.badge_fingerprint = None
            if render.item_id not in item_ids:
                item_ids.append(render.item_id)

        items = (
            (
                (await session.execute(select(MediaItem).where(MediaItem.id.in_(item_ids))))
                .scalars()
                .all()
            )
            if item_ids
            else []
        )
        # `enqueue_batch` rather than one awaited `enqueue()` per item, for
        # `bulk_rerender_action`'s reason: the cap is `drift_batch_size`, and
        # a batch that size would otherwise hold the request open for that
        # many sequential commits. The fingerprint clears ride the same
        # transaction -- the INSERT autoflushes them in and its commit lands
        # them -- so a batch that fails partway clears nothing rather than
        # clearing a batch's worth with nothing queued behind it.
        #
        # One query for the whole batch's Plex ids and one for its refs, not
        # one per item.
        plex_ids = await native_ids(session, [item.id for item in items], "plex")
        enqueued = await enqueue_batch(
            session, "process_item", _reprocess_entries(items, plex_ids)
        )
        refs_by_item = await refs_for_items(session, item_ids)

        if batch:
            # The ops rule: a write nobody can find afterwards is not an
            # operator action, it is a mystery. Counts only, and an outcome
            # sentence made of fixed words and numbers -- `/api/events` serves
            # `outcome` (it never selects `payload`), so row 213 applies to it.
            session.add(
                EventLog(
                    source="actions",
                    event_type="action_center_rebuild",
                    payload={
                        "matched": matched,
                        "selected": len(batch),
                        "cleared": cleared,
                        "items": len(item_ids),
                        "enqueued": enqueued,
                    },
                    outcome=(
                        f"action center rebuild: {len(batch)} render row(s) selected, "
                        f"{cleared} fingerprint(s) cleared, {enqueued} job(s) queued "
                        f"for {len(item_ids)} item(s)"
                    ),
                )
            )
        await session.commit()

    # Sorted by Plex native id, the same order `rating_keys` used to guarantee
    # -- not the item's own queue position, which is
    # `Render.id` order and would otherwise leak the batch's internal shape.
    items_detail = sorted(
        ({"id": i, "refs": refs_by_item.get(i, {})} for i in item_ids),
        key=lambda entry: (entry["refs"].get("plex") or "", entry["id"]),
    )
    return {
        "status": "complete" if not batch else "enqueued",
        "matched": matched,
        "selected": len(batch),
        "cleared": cleared,
        "items": len(item_ids),
        "enqueued": enqueued,
        "items_detail": items_detail,
    }


# --- the quality backfill ----------------------------------------------------
#
# Roadmap 11a's "backfill job scoring the existing library", in the
# api/facts_backfill.py shape: one batch per press, an honest `status` always
# returned as a 200, one events_log row per trigger, and a GET that answers
# standing progress so the button renders correctly on page load.
#
# It differs from that precedent in two ways, both deliberate.
#
# It keeps NO cursor row. The facts backfill needs one because it stamps
# `fetched_at` on everything it walks whether or not the walk filled anything,
# so "already visited" is not derivable from the data. This population is
# self-consuming: a row leaves it exactly when a render stamps
# `quality_scored_at`, so "what is left" is a WHERE clause and a second table
# would be a second source of truth to keep in step.
#
# And it does NOT park while TMDb's 429 window is open. The facts backfill
# parks because a batch gathered with TMDb skipped stamps `fetched_at` anyway
# and silently loses the columns it exists to fill. Nothing here stamps
# anything: `quality_scored_at` is written by the render's own write-back, and
# only when a render actually happened. A batch enqueued into a backoff window
# is simply paced by the workers, which is what D4 says the queue is for.


async def _parked_by_latest_job(session) -> set[str]:
    """Dedupe keys whose MOST RECENT ``process_item`` job is ``parked``.

    A stale ``parked`` row and a fresher ``pending``/``done`` row for the same
    key can coexist: ``uq_jobs_pending_dedupe`` only forbids two live
    pending/deferred rows for one key, not a parked row alongside a later one
    -- which is exactly what a press against a previously-parked item
    produces. Membership in the parked *state* alone over-counts (the
    unscorable-floor investigation); this asks for the state of the
    highest ``id`` per key instead. ``DISTINCT ON`` (Postgres) picks that row
    server-side rather than pulling every job row into Python.
    """
    rows = (
        await session.execute(
            select(Job.dedupe_key, Job.state)
            .distinct(Job.dedupe_key)
            .where(Job.kind == "process_item", Job.dedupe_key.isnot(None))
            .order_by(Job.dedupe_key, Job.id.desc())
        )
    ).all()
    return {dedupe_key for dedupe_key, state in rows if state == "parked"}


def _dedupe_key_for(item: MediaItem, plex_ids: dict[int, str]) -> str:
    return RenderIntent(
        kind=item.kind,
        title=item.title,
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        imdb_id=item.imdb_id,
        year=item.year,
        season_number=item.season_number,
        episode_number=item.episode_number,
        refs={"plex": plex_ids[item.id]} if item.id in plex_ids else {},
    ).dedupe_key


async def _backfill_state(session, config) -> tuple[int, int, int, int]:
    """``(done, total, blocked, queued_for_scoring)`` over the rows this
    backfill can actually score, in one shared pass.

    Scoped to ``status = 'rendered'``. A ``no_art``, ``skipped``,
    ``truncated`` or ``failed`` row returns from ``render_artifact`` long
    before the write-back that stamps ``quality_scored_at``, so counting one
    here would make a population the backfill can never finish -- a progress
    bar that stops at 94% forever. Those rows are already named by their own
    flags.

    ``blocked`` is the further subset of the unscored population whose item's
    MOST RECENT ``process_item`` job is ``parked`` (see
    ``_parked_by_latest_job``): a press cannot score these by re-rendering,
    because the job that would do it is sitting on Failures waiting for an
    operator (fix it, retry it, dismiss it). They are excluded from ``total``
    for the same "the bar must reach 100%" reason the render-status
    exclusions above are -- a row this button structurally cannot move must
    not sit in its own denominator, or ``done >= total`` never holds while it
    does.

    ``queued_for_scoring`` is the further subset whose item has an in-flight
    ``pending``/``running``/``deferred`` job -- still unscored, but already
    claimed by a job a press did not just mint. A key cannot be both: an item
    whose latest job is ``pending`` is not in ``blocked`` at all (a fresher
    job outranks an older parked one), so the two counts partition the
    unscored population rather than overlapping it.

    Both splits need the same item-by-item dedupe-key walk, so it runs ONCE,
    and only when there is something to check against: with no parked and no
    in-flight key at all, every unscored row is ordinary progress and the
    population is a plain ``COUNT(*)`` -- no ORM materialisation.

    This is deliberately narrower than the investigation's own accounting: a
    stale twin still reads as an ordinary unscored row here, because the only
    cheap read available -- ``media_items`` alone -- cannot tell one from a
    row that is simply next in line.

    The twins are the TWIN-MERGE job's territory (``scheduler/merge.py``), not
    the pruner's. That attribution was wrong when it was written: the prune's
    "gone" test is identity EXISTENCE, and a re-keyed item exists -- via the
    same GUID walk that forked it -- so ``plex_prune`` correctly reports zero
    against a library full of twins and could not be made to own them without
    changing what "gone" means. The merge job's own scan CAN tell, and its
    summary reports the count. That is the right surface for it, one click
    away, rather than a fourth number on this panel.

    Every one of the three population queries below carries
    ``flags.excluded_library_predicate`` -- the SAME expression the listing
    and the chip counts use. A row in an excluded library can never be
    scored (its job defers forever rather than parking, so it is not
    ``blocked`` either), and a denominator holding rows the button cannot
    move is a progress bar that stops short of 100% for good.

    Every population query below is ALSO anti-joined against
    ``action_dismissals``, through ``_undismissed`` -- the same
    ``_dismissal_join()`` plus ``ActionDismissal.id.is_(None)`` pair
    ``_scope`` gives the listing and the chip counts. A dismissal is the
    operator saying "leave this one", so a dismissed row is not in this
    population at all: not in ``done``, not in ``total``, not in ``blocked``
    and not in ``queued_for_scoring``. Without it, a dismissed unscored row
    sat inside ``unscored_total`` and read as queued while the "Not yet
    scored" chip showed 0 -- the header and the chip counting two different
    populations of the same table (production, 2026-09-04). ``parked_keys``
    and ``pending_keys`` stay unfiltered on purpose: they are only ever
    tested against items that already came out of that anti-joined walk.
    """
    excluded_rows = flags.excluded_library_predicate(config)

    done = (
        await session.execute(
            _undismissed(
                select(func.count())
                .select_from(Render)
                .join(MediaItem, MediaItem.id == Render.item_id)
                .where(
                    Render.status == "rendered",
                    Render.quality_scored_at.isnot(None),
                    excluded_rows,
                )
            )
        )
    ).scalar_one()

    parked_keys = await _parked_by_latest_job(session)
    pending_keys = set(
        (
            await session.execute(
                select(Job.dedupe_key).where(
                    Job.kind == "process_item",
                    Job.state.in_(("pending", "running", "deferred")),
                    Job.dedupe_key.isnot(None),
                )
            )
        ).scalars()
    )

    blocked = 0
    queued = 0
    if parked_keys or pending_keys:
        unscored_items = (
            (
                await session.execute(
                    _undismissed(
                        select(MediaItem)
                        .join(Render, Render.item_id == MediaItem.id)
                        .where(
                            Render.status == "rendered",
                            Render.quality_scored_at.is_(None),
                            excluded_rows,
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        # One query for the whole pass's Plex ids, not one per item.
        plex_ids = await native_ids(session, [item.id for item in unscored_items], "plex")
        for item in unscored_items:
            key = _dedupe_key_for(item, plex_ids)
            if key in parked_keys:
                blocked += 1
            elif key in pending_keys:
                queued += 1
        unscored_count = len(unscored_items)
    else:
        unscored_count = (
            await session.execute(
                _undismissed(
                    select(func.count())
                    .select_from(Render)
                    .join(MediaItem, MediaItem.id == Render.item_id)
                    .where(
                        Render.status == "rendered",
                        Render.quality_scored_at.is_(None),
                        excluded_rows,
                    )
                )
            )
        ).scalar_one()

    total = done + (unscored_count - blocked)
    return done, total, blocked, queued


async def _select_backfill_batch(session, batch_size: int, config) -> list[Render]:
    """The next ``batch_size`` unscored, rendered rows to score -- skipping
    any row whose ITEM already has a pending or deferred ``process_item`` job,
    or whose most recent ``process_item`` job is ``parked``.

    Without the in-flight exclusion, pressing the button again while the
    previous batch is still rendering re-selects the very rows that batch is
    working on: they are still unscored, because their re-render has not
    landed yet. Those rows then hit ``enqueue_batch``'s own dedupe -- which
    coalesces against ``pending`` and ``deferred`` alike -- and are silently
    dropped, so the press reports ``enqueued: 0`` while unrelated unscored
    rows sit untouched elsewhere in the table -- a dead button.

    Without the parked exclusion (the unscorable-floor investigation's third
    finding), a press silently resets a failure
    an operator has not yet seen: the item's job is sitting on Failures
    waiting for them, and re-selecting its row here mints a fresh job that
    starts the attempt cycle over. The Failures page's own retry/dismiss is
    the intended path back, not another press of this button.

    The exclusion is at the ITEM level, not the row's: a batch enqueues one
    job per ITEM (``_reprocess_entries``), keyed by
    ``RenderIntent.dedupe_key``, while this selects per-render-row -- an item
    with two unscored art kinds must have both rows skipped once either one
    is in flight or blocked.

    Anti-joined in the style ``arr/sync.py``'s ``enqueue_unknown_items`` uses
    for its own discovery: the pending and parked keys are fetched once each
    as plain Python sets, and each candidate row's item is tested against
    them, rather than reproducing ``RenderIntent.dedupe_key``'s
    id-precedence chain as SQL. Paged rather than one large ``LIMIT``, since
    the in-flight set can itself be up to ``batch_size`` items and no fixed
    multiple over that is safe to assume.

    The excluded-library predicate rides along for the same reason the
    in-flight and parked exclusions do: enqueueing an item this service can
    no longer resolve does not score it, it just mints another job that
    defers on an unbounded horizon.

    The dismissal anti-join rides along for a reason the in-flight and parked
    exclusions cannot cover: a dismissed row typically has NO job at all, so
    neither of those would skip it, and a press would clear the fingerprint
    of an asset the operator has explicitly set aside and mint a real
    re-render for it.
    """
    pending_keys = set(
        (
            await session.execute(
                select(Job.dedupe_key).where(
                    Job.kind == "process_item",
                    Job.state.in_(("pending", "deferred")),
                    Job.dedupe_key.isnot(None),
                )
            )
        ).scalars()
    )
    parked_keys = await _parked_by_latest_job(session)

    selected: list[Render] = []
    after_id = 0
    while len(selected) < batch_size:
        page = (
            await session.execute(
                _undismissed(
                    select(Render, MediaItem)
                    .join(MediaItem, MediaItem.id == Render.item_id)
                    .where(
                        Render.status == "rendered",
                        Render.quality_scored_at.is_(None),
                        Render.id > after_id,
                        flags.excluded_library_predicate(config),
                    )
                )
                .order_by(Render.id)
                .limit(batch_size)
            )
        ).all()
        if not page:
            break
        after_id = page[-1][0].id
        # One query per page's Plex ids, not one per item.
        plex_ids = await native_ids(session, [item.id for _, item in page], "plex")
        for render, item in page:
            if len(selected) >= batch_size:
                break
            key = _dedupe_key_for(item, plex_ids)
            if key in pending_keys or key in parked_keys:
                continue
            selected.append(render)

    return selected


@router.get("/actions/backfill")
async def backfill_status(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """How much of the scorable population has been scored, and how much of
    what is left is already queued for it. Reads only."""
    config = request.app.state.config_holder.current
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        done, total, blocked, queued_for_scoring = await _backfill_state(session, config)

    # Completion is derived FIRST, and by the POST's own rule: `done >= total`
    # <=> no unscored rendered row is left <=> the trigger selects 0. Testing
    # "has it started" first would let an empty library answer `not_started`
    # here forever while every POST answered `complete` -- the two endpoints
    # disagreeing about the one state a disabled button keys off.
    if done >= total:
        status = "complete"
    elif done == 0:
        status = "not_started"
    else:
        status = "in_progress"
    return {
        "status": status,
        "done": done,
        "total": total,
        "queued_for_scoring": queued_for_scoring,
        "unscored_total": total - done,
        "blocked": blocked,
    }


@router.post("/actions/backfill")
async def backfill_trigger(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """One batch: clear, enqueue, report -- or report completion idempotently.

    Clearing the fingerprint is the substance of this endpoint and is not
    optional. An enqueue on its own would not score anything: the pipeline's
    unchanged-fingerprint short-circuit returns ABOVE the write-back, so a
    re-processed row whose inputs have not moved reports "unchanged" and
    stamps nothing, and the backfill would run forever and finish never.
    ``/items/{id}/renders/{kind}/clear-override`` already makes exactly this
    move for exactly this reason.

    That is also the honest cost, and the page says so: this is a real
    re-render of every row it touches -- a provider selection against the 24h
    cache, a composite, a publish and an upload. It is the only way to recover
    an achieved language, which no column on a pre-existing row holds.

    Always a 200 with a ``status`` field. ``complete`` is an expected answer
    to an honest question, not an error for the client to style as a failure.

    The fingerprint clear is no longer committed on its own ahead of the
    enqueue: it stays a pending ORM change until ``enqueue_batch`` runs, whose
    own INSERT ... autoflushes it in and whose own commit lands it -- so a
    batch that fails partway clears no fingerprint rather than clearing a
    batch's worth with nothing queued behind it.
    """
    config = request.app.state.config_holder.current
    batch_size = config.scheduler.drift_batch_size

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        batch = await _select_backfill_batch(session, batch_size, config)

        item_ids = []
        for render in batch:
            render.fingerprint = None
            if render.item_id not in item_ids:
                item_ids.append(render.item_id)

        items = (
            (
                (await session.execute(select(MediaItem).where(MediaItem.id.in_(item_ids))))
                .scalars()
                .all()
            )
            if item_ids
            else []
        )
        # One query for the whole batch's Plex ids, not one per item.
        plex_ids = await native_ids(session, [item.id for item in items], "plex")
        enqueued = await enqueue_batch(
            session, "process_item", _reprocess_entries(items, plex_ids)
        )

        # AFTER the enqueue, not before: the operator pressing again while a
        # previous batch is still rendering (the live report this answers,
        # mid-run at 8214/17264) needs the depth this press just left behind,
        # not the depth it found.
        done, total, blocked, queued_for_scoring = await _backfill_state(session, config)
        unscored_total = total - done
        if not batch:
            detail = f"complete: all {total} rendered asset(s) have been scored"
        else:
            detail = (
                f"queued {enqueued} more; {queued_for_scoring} of {unscored_total} "
                "unscored now queued for scoring"
            )
        # The ops rule: a write nobody can find afterwards is not an operator
        # action, it is a mystery. One row per press, the completes included --
        # an operator reading the table should see the walk.
        session.add(
            EventLog(
                source="actions",
                event_type="action_center_quality_backfill",
                payload={
                    "selected": len(batch),
                    "items": len(item_ids),
                    "enqueued": enqueued,
                    "done": done,
                    "total": total,
                },
                outcome="action center quality backfill: " + detail,
            )
        )
        await session.commit()

    return {
        "status": "complete" if not batch else "enqueued",
        "selected": len(batch),
        "enqueued": enqueued,
        "done": done,
        "total": total,
        "queued_for_scoring": queued_for_scoring,
        "unscored_total": unscored_total,
        "blocked": blocked,
        "detail": detail,
    }
