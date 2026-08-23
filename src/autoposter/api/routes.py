"""The /api router: login, logout and everything behind require_session."""
import asyncio
import logging
import os
from dataclasses import asdict

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from autoposter.api.artwork import router as artwork_router
from autoposter.api.dashboard_stream import router as dashboard_stream_router
from autoposter.api.logs import router as logs_router
from autoposter.api.snapshots import events_snapshot, status_snapshot
from autoposter.api.auth import (
    create_session,
    hash_password,
    prune_expired,
    require_session,
    revoke,
    session_for_token,
    verify_password,
)
from autoposter.db.models import (
    ItemFacts,
    Job,
    ManagedCollection,
    MediaItem,
    Render,
    ScheduledRun,
)
from autoposter.db.models import Session as SessionModel
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.queue.jobs import enqueue, enqueue_batch
from autoposter.render.pipeline import ART_KINDS_FOR, manual_override_path

logger = logging.getLogger(__name__)

# The names of the four periodic jobs, from the Job(name=...) literals in
# scheduler/jobs.py (:63, :157, :291, :517). Spelled out rather than imported
# from the job factories: importing those would pull plexapi and the arr/http
# client machinery into this module, which no request handler here needs. A
# name added there and not here can simply not be triggered by hand.
SCHEDULED_JOB_NAMES = frozenset({
    "collections_reconcile",
    "ratings_drift_sweep",
    "arr_sync",
    "asset_cleanup",
})

DEFAULT_EVENTS_LIMIT = 50
MAX_EVENTS_LIMIT = 200

DEFAULT_ITEMS_LIMIT = 50
MAX_ITEMS_LIMIT = 200

DEFAULT_JOBS_LIMIT = 50
MAX_JOBS_LIMIT = 200

# Never the real value -- see get_config()'s docstring.
_REDACTED = "***REDACTED***"

# Strong references to in-flight notification tasks: asyncio holds only a
# weak reference to a created task, so a fire-and-forget send nothing else
# references could be garbage-collected mid-flight. The done-callback drops
# each reference on completion. Module-level because the request that started
# the send has already answered by the time the send finishes.
_notification_tasks: set = set()


def _notify_in_background(send_coroutine) -> None:
    """Fire one ``Notifier.send`` without awaiting it.

    A response must never wait on the webhook -- one send's worst case is
    ~31.5s on the default retry config (see notify/dispatch.py), against
    endpoints that answer in seconds. ``send`` never raises and does its own
    outcome logging, so the task's result is deliberately dropped; in
    particular a disabled notifier's vacuous ``True`` is never reported as a
    delivery.
    """
    task = asyncio.create_task(send_coroutine)
    _notification_tasks.add(task)
    task.add_done_callback(_notification_done)


def _notification_done(task: asyncio.Task) -> None:
    _notification_tasks.discard(task)
    # Notifier.send never raises by contract, but an exception a task holds
    # unretrieved becomes a GC-time warning; retrieve and log it here so a
    # misbehaving injected notifier is named, not leaked -- the mirror of
    # Scheduler._notification_done (scheduler/core.py).
    if not task.cancelled() and task.exception() is not None:
        logger.warning("notification task failed", exc_info=task.exception())


def _escape_like(value: str) -> str:
    """Escape ``%``, ``_`` and the escape character itself, so a search term
    containing them matches literally instead of acting as an ILIKE wildcard."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


router = APIRouter(prefix="/api")

# Image bytes live in their own module: they are the only endpoints here that
# touch the filesystem, and the containment rules that go with that do not
# belong scattered through the JSON handlers.
router.include_router(artwork_router)

# The live log endpoints -- the in-memory capture of this process's own log
# stream (see api/logs.py for why it is a module of its own).
router.include_router(logs_router)

# The dashboard's push stream, and the per-process broadcaster behind it.
router.include_router(dashboard_stream_router)

# How long an issued session stays valid before the operator has to log in
# again.
SESSION_TTL_HOURS = 24

_dummy_hash_cache: str | None = None


def _dummy_hash() -> str:
    """The hash verified on every login attempt where no admin password is
    configured, so that response timing cannot reveal whether
    AUTOPOSTER_ADMIN_PASSWORD_HASH is set -- see verify_password's docstring
    in api/auth.py.

    Computed on first use rather than at import: bcrypt at cost 12 is a
    quarter of a second, which a module-level constant would spend on every
    import of this module, including in deployments that do have a password
    configured and will never need it.
    """
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = hash_password("no admin password is configured")
    return _dummy_hash_cache


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> dict:
    # Rate limit before anything expensive: this endpoint needs no
    # credentials to reach, and bcrypt at cost 12 is ~250 ms of CPU per
    # attempt -- a handful of requests a second would otherwise be enough to
    # starve the worker pool, the scheduler and the Plex probe that share
    # this loop.
    client = request.client.host if request.client else "unknown"
    if not request.app.state.login_rate_limiter.allow(client):
        raise HTTPException(status_code=429, detail="too many login attempts")

    secrets = request.app.state.secrets
    admin_hash = secrets.admin_password_hash
    # Always run the bcrypt check, even with no admin password configured --
    # short-circuiting here would let response timing reveal whether the
    # deployment is configured. In a thread, because it is pure CPU and the
    # event loop also carries the workers, the scheduler and /healthz.
    valid = await asyncio.to_thread(
        verify_password, body.password, admin_hash or _dummy_hash()
    )
    if not admin_hash or not valid:
        raise HTTPException(status_code=401, detail="invalid credentials")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Nothing else deletes expired rows, and login is the only rare
        # request that touches this table -- see prune_expired.
        await prune_expired(session)
        token = await create_session(session, ttl_hours=SESSION_TTL_HOURS)
        row = await session_for_token(session, token)
    return {"token": token, "expires_at": row.expires_at}


@router.get("/me")
async def me(current: SessionModel = Depends(require_session)) -> dict:
    return {"authenticated": True, "expires_at": current.expires_at}


@router.post("/logout")
async def logout(
    request: Request,
    authorization: str | None = Header(default=None),
    _: SessionModel = Depends(require_session),
) -> dict:
    token = authorization.removeprefix("Bearer ")
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await revoke(session, token)
    return {"ok": True}


@router.get("/status")
async def status(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    # The queries live in api/snapshots.py because the dashboard stream's
    # broadcaster builds this same body on its poll -- see that module.
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        return await status_snapshot(
            session, request.app.state.config, request.app.state.scheduler_intervals
        )


@router.get("/events")
async def events(
    request: Request,
    limit: int = DEFAULT_EVENTS_LIMIT,
    _: SessionModel = Depends(require_session),
) -> dict:
    capped_limit = min(max(limit, 1), MAX_EVENTS_LIMIT)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        return {"events": await events_snapshot(session, capped_limit)}


@router.get("/items")
async def list_items(
    request: Request,
    limit: int = DEFAULT_ITEMS_LIMIT,
    offset: int = 0,
    library: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    search: str | None = None,
    _: SessionModel = Depends(require_session),
) -> dict:
    capped_limit = min(max(limit, 1), MAX_ITEMS_LIMIT)
    capped_offset = max(offset, 0)

    conditions = []
    if library is not None:
        conditions.append(MediaItem.library == library)
    if kind is not None:
        conditions.append(MediaItem.kind == kind)
    if search is not None:
        conditions.append(MediaItem.title.ilike(f"%{_escape_like(search)}%", escape="\\"))
    if status is not None:
        conditions.append(
            select(Render.id)
            .where(Render.item_id == MediaItem.id, Render.status == status)
            .exists()
        )

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # A separate COUNT(*), not len() of a fully-fetched result -- this
        # library runs to ~16,000 rows.
        total = (
            await session.execute(
                select(func.count()).select_from(MediaItem).where(*conditions)
            )
        ).scalar_one()

        items = (
            (
                await session.execute(
                    select(MediaItem)
                    .where(*conditions)
                    .order_by(MediaItem.id)
                    .limit(capped_limit)
                    .offset(capped_offset)
                )
            )
            .scalars()
            .all()
        )

        # One extra query for the whole page's renders rather than one per
        # item, to avoid N+1 round trips.
        item_ids = [item.id for item in items]
        render_status_by_item: dict[int, dict[str, str]] = {item_id: {} for item_id in item_ids}
        if item_ids:
            render_rows = await session.execute(
                select(Render.item_id, Render.art_kind, Render.status)
                .where(Render.item_id.in_(item_ids))
                .order_by(Render.art_kind)
            )
            for item_id, art_kind, render_status in render_rows:
                render_status_by_item[item_id][art_kind] = render_status

    return {
        "total": total,
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "library": item.library,
                "kind": item.kind,
                "rating_key": item.rating_key,
                "render_status": render_status_by_item[item.id],
            }
            for item in items
        ],
    }


@router.get("/items/filters")
async def item_filters(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Distinct values for the library browser's filter controls, each sorted.

    Computed with `DISTINCT` in SQL rather than fetched and reduced in
    Python -- the items table runs to ~15,000 rows.

    "statuses" reports `Render.status` (pending|rendered|truncated|no_art|
    failed), not `Render.upload_status`. That is what list_items()'s own
    `status` query parameter already filters on, and what a user picking an
    item filter means by "status" -- whether the art rendered, not whether
    it made it to Plex.

    Must stay registered before /items/{item_id}: FastAPI matches routes in
    registration order, and "filters" would otherwise be parsed as an
    item_id and fail validation.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        libraries = (
            await session.execute(select(MediaItem.library).distinct().order_by(MediaItem.library))
        ).scalars().all()
        kinds = (
            await session.execute(select(MediaItem.kind).distinct().order_by(MediaItem.kind))
        ).scalars().all()
        statuses = (
            await session.execute(select(Render.status).distinct().order_by(Render.status))
        ).scalars().all()
    return {"libraries": libraries, "kinds": kinds, "statuses": statuses}


@router.get("/items/{item_id}")
async def item_detail(
    item_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        facts = (
            await session.execute(select(ItemFacts).where(ItemFacts.item_id == item_id))
        ).scalar_one_or_none()

        renders = (
            (await session.execute(select(Render).where(Render.item_id == item_id)))
            .scalars()
            .all()
        )

    return {
        "id": item.id,
        "title": item.title,
        "library": item.library,
        "kind": item.kind,
        "rating_key": item.rating_key,
        "facts": None
        if facts is None
        else {
            "critic_rating": facts.critic_rating,
            "audience_rating": facts.audience_rating,
            "content_rating": facts.content_rating,
            "genres": facts.genres,
            "studio": facts.studio,
            "originally_available": facts.originally_available,
        },
        "renders": [
            {
                "art_kind": render.art_kind,
                "status": render.status,
                "fingerprint": render.fingerprint,
                "badge_fingerprint": render.badge_fingerprint,
                "upload_status": render.upload_status,
                "adopted": render.adopted,
                # The only trace in the database that a manual override
                # supplied this artwork -- pipeline.py stamps
                # provider="manual" on that branch.
                "provider": render.provider,
                "rendered_at": render.rendered_at,
                "uploaded_at": render.uploaded_at,
            }
            for render in renders
        ],
    }


@router.get("/collections")
async def list_collections(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        rows = (
            (await session.execute(select(ManagedCollection).order_by(ManagedCollection.id)))
            .scalars()
            .all()
        )
    return {
        "collections": [
            {
                "id": row.id,
                "library": row.library,
                "title": row.title,
                "kind": row.kind,
                # Straight through, nulls included. NULL means no pass has
                # stamped this row -- permanently so for a smart collection,
                # whose filter Plex evaluates live, so there is no member
                # count to have. A zero delta is a different thing entirely:
                # a pass that ran and found nothing to change. Rendering
                # either as the other would be a false claim.
                "member_count": row.member_count,
                "last_added": row.last_added,
                "last_removed": row.last_removed,
                "last_reconciled_at": row.last_reconciled_at,
            }
            for row in rows
        ]
    }


@router.get("/jobs/parked")
async def parked_jobs(
    request: Request,
    limit: int = DEFAULT_JOBS_LIMIT,
    offset: int = 0,
    _: SessionModel = Depends(require_session),
) -> dict:
    capped_limit = min(max(limit, 1), MAX_JOBS_LIMIT)
    capped_offset = max(offset, 0)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Job)
                    .where(Job.state == "parked")
                    # id breaks ties: jobs parked in the same batch share an
                    # updated_at to the microsecond, and without a total
                    # order a paged read can repeat or skip rows.
                    .order_by(Job.updated_at.desc(), Job.id.desc())
                    .limit(capped_limit)
                    .offset(capped_offset)
                )
            )
            .scalars()
            .all()
        )
    return {
        "jobs": [
            {
                "id": row.id,
                "kind": row.kind,
                "payload": row.payload,
                "attempts": row.attempts,
                "reason": row.last_error,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    }


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Reset a parked job to pending and clear its attempt count, so the
    worker pool picks it up again. Acting on a job that is not currently
    parked -- unknown, already dismissed, or in any other state -- is a 404
    rather than a no-op or a 500.

    A parked job whose item has since been queued again by some other path
    (a webhook, the drift sweep) collides with uq_jobs_pending_dedupe, the
    partial unique index that allows one pending job per dedupe key. There
    is nothing to retry in that case -- the work is already queued -- so it
    is a 409 saying so, not the IntegrityError a 500 would come from.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        job = (
            await session.execute(select(Job).where(Job.id == job_id, Job.state == "parked"))
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="parked job not found")
        job.state = "pending"
        job.attempts = 0
        job.claimed_by = None
        job.claimed_at = None
        job.run_after = func.now()
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail="a job for this item is already queued",
            ) from None
    return {"id": job.id, "state": job.state}


@router.post("/jobs/{job_id}/dismiss")
async def dismiss_job(
    job_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Mark a parked job dismissed without deleting it -- what failed and why
    is worth keeping, and this project deletes nothing anywhere else either."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        job = (
            await session.execute(select(Job).where(Job.id == job_id, Job.state == "parked"))
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="parked job not found")
        job.state = "dismissed"
        await session.commit()
    return {"id": job.id, "state": job.state}


async def _enqueue_reprocess(session, item: MediaItem) -> int | None:
    """Queue a process_item job for one item; the job id, or None if deduped.

    Shared by ``reprocess_item`` and ``clear_manual_override`` so the second
    cannot drift from the first: the ``dedupe_key`` is what makes asking twice
    while the first request is still pending queue nothing the second time,
    and two hand-built RenderIntents would eventually disagree about it.
    """
    intent = RenderIntent(
        kind=item.kind,
        title=item.title,
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        imdb_id=item.imdb_id,
        year=item.year,
        season_number=item.season_number,
        episode_number=item.episode_number,
    )
    return await enqueue(
        session, kind="process_item", payload=asdict(intent), dedupe_key=intent.dedupe_key
    )


@router.post("/items/{item_id}/reprocess")
async def reprocess_item(
    item_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Enqueue a process_item job for one item, via the same enqueue()/dedupe_key
    convention every other intake path uses -- asking twice while the first
    request is still pending queues nothing the second time."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        job_id = await _enqueue_reprocess(session, item)
    return {"queued": job_id is not None, "job_id": job_id}


@router.post("/items/{item_id}/renders/{art_kind}/clear-override")
async def clear_manual_override(
    item_id: int, art_kind: str, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Take a hand-placed override out of play and re-render the item.

    A manual override is a file on the manualassets mount and nothing else --
    the pipeline stats for it on every pass (``render/pipeline.py``) and no
    database column can suppress one -- so the only way to clear it is to move
    the file. It is renamed to ``<name>.disabled`` rather than deleted: it is
    the operator's own artwork, this project deletes nothing anywhere else,
    and restoring it is a rename back. ``os.replace`` rather than ``rename``
    so a ``.disabled`` left by an earlier clear does not make this fail.

    Then the render row's fingerprints are cleared, because the rename alone
    changes nothing the next pass would notice in time: the fingerprint
    short-circuit returns "unchanged" before the override is even consulted.
    That ordering matters in the other direction too -- if the rename fails,
    nothing else happens, since a cleared fingerprint with the override still
    in place would re-render straight back to the override while this endpoint
    claimed to have cleared it.

    ``art_kind`` is the only caller-supplied value that reaches a path
    builder, so it is checked against the kinds the item can actually have
    before anything touches the mount; the path itself is built from config
    and the item's own columns, never from the request.
    """
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        if art_kind not in ART_KINDS_FOR.get(item.kind, ()):
            raise HTTPException(status_code=404, detail="unknown art kind for this item")
        if item.root_folder is None:
            # Nullable, and the asset layout is rooted at it -- an item without
            # one has nowhere an override could have been filed.
            raise HTTPException(status_code=409, detail="no manual override for this art kind")

        resolved = ResolvedItem(
            rating_key=item.rating_key, library=item.library, kind=item.kind,
            title=item.title, year=item.year,
            season_number=item.season_number, episode_number=item.episode_number,
            root_folder=item.root_folder, file_path=item.file_path, art_url=None,
            tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id, imdb_id=item.imdb_id,
        )
        # Offloaded like every other touch of this mount: manual_assets_root is
        # typically NFS, and a hung mount must not stall the event loop that
        # also carries the workers and the scheduler.
        override = await asyncio.to_thread(manual_override_path, config, resolved, art_kind)
        if override is None:
            raise HTTPException(status_code=409, detail="no manual override for this art kind")

        disabled = override.with_name(override.name + ".disabled")
        try:
            await asyncio.to_thread(os.replace, override, disabled)
        except OSError as exc:
            logger.warning("could not disable override %s: %s", override, exc)
            raise HTTPException(status_code=503, detail=str(exc)) from None

        render = (
            await session.execute(
                select(Render).where(Render.item_id == item_id, Render.art_kind == art_kind)
            )
        ).scalar_one_or_none()
        if render is not None:
            render.fingerprint = None
            render.badge_fingerprint = None
            await session.commit()

        job_id = await _enqueue_reprocess(session, item)
    return {"status": "cleared", "queued": job_id is not None}


@router.post("/scheduled-runs/{name}/run")
async def run_scheduled_job_now(
    name: str, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Make one periodic job due, so the next scheduler poll picks it up.

    Nothing is run here and nothing is waited on. ``claim_due``
    (scheduler/core.py) treats ``last_started_at IS NULL`` as due
    unconditionally, so nulling that column is the whole mechanism, and it
    does not care which replica happens to claim the row. The response
    carries the scheduler's poll interval so the UI can say when it will be
    picked up rather than pretending the work is done.

    Pressing this while the job is ALREADY running starts a second copy:
    the claim is not a lease (claim_due's own note), so nulling the column
    mid-run makes the row due again on the next poll. The jobs are
    reconciliation passes, so a double run wastes work rather than
    corrupting anything, and the UI is told to present the button
    accordingly -- but do not read this endpoint as idempotent.

    An INSERT ... ON CONFLICT rather than a read-then-write: the row may not
    exist yet -- the scheduler creates it on its first claim -- and an
    inserted row has a NULL ``last_started_at`` by construction. Only that
    one column is set on the conflict arm; ``last_status`` and
    ``last_detail`` are the previous run's history and stay.

    A name outside the allowlist is a 404: the column is free text, so
    without it any string at all would seed a row no scheduler will ever run,
    and the operator would be left with a job in the dashboard that never
    starts.
    """
    if name not in SCHEDULED_JOB_NAMES:
        raise HTTPException(status_code=404, detail="unknown scheduled job")
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await session.execute(
            insert(ScheduledRun)
            .values(name=name)
            .on_conflict_do_update(
                index_elements=["name"], set_={"last_started_at": None}
            )
        )
        await session.commit()
    return {
        "status": "requested",
        "poll_seconds": request.app.state.config.scheduler.poll_seconds,
    }


@router.post("/full-pass")
async def run_full_pass(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Enqueue a process_item job for every known item -- the "run the whole
    tool now" button. Cheap to trigger casually: unchanged items short-circuit
    on their render fingerprints.

    Named full-pass, not sweep: "sweep" in this codebase is the scheduled
    ratings-drift sweep (scheduler/jobs.py), which is stale-only, batched and
    movie/show-only, and this is none of those things.

    Every kind, not just movies and shows. Seasons and episodes are their own
    media_items rows, and process_item builds only ART_KINDS_FOR[intent.kind]
    for the one intent it is given (render/pipeline.py) -- there is no cascade
    from a show to its seasons -- so a movie/show-only pass would never touch
    a season poster or a title card. This mirrors the fan-out parse_sonarr
    already performs on the webhook path (intake/arr.py).

    Deduped in the database, not here: enqueue_batch carries the same ON
    CONFLICT clause as enqueue(), so triggering again while jobs from the
    last pass are still pending inserts nothing for those items -- and the
    response reports that honestly via ``skipped`` rather than claiming to
    have queued everything again.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    MediaItem.kind,
                    MediaItem.title,
                    MediaItem.tmdb_id,
                    MediaItem.tvdb_id,
                    MediaItem.imdb_id,
                    MediaItem.year,
                    MediaItem.season_number,
                    MediaItem.episode_number,
                )
            )
        ).all()
        entries = []
        for row in rows:
            intent = RenderIntent(
                kind=row.kind,
                title=row.title,
                tmdb_id=row.tmdb_id,
                tvdb_id=row.tvdb_id,
                imdb_id=row.imdb_id,
                year=row.year,
                season_number=row.season_number,
                episode_number=row.episode_number,
            )
            entries.append((asdict(intent), intent.dedupe_key))
        queued = await enqueue_batch(session, "process_item", entries)
    total = len(entries)
    skipped = total - queued
    # After enqueue_batch's commit, so the notification never describes jobs
    # the database does not yet show -- and fire-and-forget, so the response
    # does not wait on the webhook.
    _notify_in_background(
        request.app.state.notifier.send(
            "full_pass_enqueued",
            f"full pass enqueued: {queued} queued, {skipped} skipped, {total} total",
            {"total": total, "queued": queued, "skipped": skipped},
        )
    )
    return {"total": total, "queued": queued, "skipped": skipped}


@router.get("/config")
async def get_config(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """The running configuration, with every secret redacted.

    Provider API keys, the Plex token, the database URL and the admin
    password hash never leave this process -- every field of ``Secrets`` is
    replaced with the same marker regardless of whether it is set, so the
    response cannot even reveal a secret's length or prefix, let alone its
    value.

    ``notifications.url`` lives in ``Config``, not ``Secrets``, so the
    wholesale redaction above never touches it -- but it may embed a token
    in its path (Uptime-Kuma style), and every payload that reaches the
    operator over HTTP carries the host only (the ``_record_failure``
    stance in notify/dispatch.py). It is reduced to its host here; the full
    URL stays in the config file the operator already owns.
    """
    config = request.app.state.config
    secrets = request.app.state.secrets
    body = config.model_dump(mode="json")
    if body["notifications"]["url"]:
        try:
            host = httpx.URL(body["notifications"]["url"]).host or ""
        except Exception:  # a malformed URL must not break the endpoint
            host = ""
        body["notifications"]["url"] = host
    body["secrets"] = {field: _REDACTED for field in secrets.model_dump()}
    return body
