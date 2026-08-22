"""The /api router: login, logout and everything behind require_session."""
import asyncio
import logging
from dataclasses import asdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from autoposter.api.artwork import router as artwork_router
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
    EventLog,
    ItemFacts,
    Job,
    ManagedCollection,
    MediaItem,
    Render,
    ScheduledRun,
)
from autoposter.db.models import Session as SessionModel
from autoposter.intake.arr import RenderIntent
from autoposter.queue.jobs import enqueue, enqueue_batch

logger = logging.getLogger(__name__)

# jobs.state values (see db/models.py's Job docstring); always reported even
# when zero, so an empty database returns zeroed counts rather than an
# incomplete dict.
JOB_STATES = ("pending", "running", "done", "failed", "parked", "dismissed")

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
    task.add_done_callback(_notification_tasks.discard)


def _escape_like(value: str) -> str:
    """Escape ``%``, ``_`` and the escape character itself, so a search term
    containing them matches literally instead of acting as an ILIKE wildcard."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


router = APIRouter(prefix="/api")

# Image bytes live in their own module: they are the only endpoints here that
# touch the filesystem, and the containment rules that go with that do not
# belong scattered through the JSON handlers.
router.include_router(artwork_router)

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
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # GROUP BY in SQL rather than fetching every job row and counting in
        # Python -- the jobs table is the hot one at this library's size.
        state_counts = (
            await session.execute(select(Job.state, func.count()).group_by(Job.state))
        ).all()
        jobs_by_state = dict.fromkeys(JOB_STATES, 0)
        for state, count in state_counts:
            jobs_by_state[state] = count

        processed_last_24h = (
            await session.execute(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.state == "done",
                    Job.updated_at >= func.now() - timedelta(hours=24),
                )
            )
        ).scalar_one()

        scheduled_rows = (
            (await session.execute(select(ScheduledRun).order_by(ScheduledRun.name)))
            .scalars()
            .all()
        )

    return {
        "jobs_by_state": jobs_by_state,
        "workers": request.app.state.config.workers,
        "processed_last_24h": processed_last_24h,
        "scheduled_jobs": [
            {
                "name": row.name,
                "last_started_at": row.last_started_at,
                "last_finished_at": row.last_finished_at,
                "last_status": row.last_status,
                "last_detail": row.last_detail,
            }
            for row in scheduled_rows
        ],
    }


@router.get("/events")
async def events(
    request: Request,
    limit: int = DEFAULT_EVENTS_LIMIT,
    _: SessionModel = Depends(require_session),
) -> dict:
    capped_limit = min(max(limit, 1), MAX_EVENTS_LIMIT)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # payload is never selected -- it holds whole webhook bodies and can
        # carry tokens from the sending service.
        rows = (
            await session.execute(
                select(
                    EventLog.source, EventLog.event_type, EventLog.outcome, EventLog.received_at
                )
                .order_by(EventLog.received_at.desc())
                .limit(capped_limit)
            )
        ).all()
    return {
        "events": [
            {
                "source": row.source,
                "event_type": row.event_type,
                "outcome": row.outcome,
                "received_at": row.received_at,
            }
            for row in rows
        ]
    }


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
                select(Render.item_id, Render.art_kind, Render.status).where(
                    Render.item_id.in_(item_ids)
                )
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
        job_id = await enqueue(
            session, kind="process_item", payload=asdict(intent), dedupe_key=intent.dedupe_key
        )
    return {"queued": job_id is not None, "job_id": job_id}


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
    """
    config = request.app.state.config
    secrets = request.app.state.secrets
    body = config.model_dump(mode="json")
    body["secrets"] = {field: _REDACTED for field in secrets.model_dump()}
    return body
