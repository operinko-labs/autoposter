"""The /api router: login, logout and everything behind require_session."""
import asyncio
import logging
import os
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from autoposter.api.action_center import router as action_center_router
from autoposter.api.artwork import router as artwork_router
from autoposter.artwork_modes.backup import BackupMode
from autoposter.artwork_modes.logo import LogoMode, LogoRevertMode
from autoposter.artwork_modes.reset import ResetMode
from autoposter.artwork_modes.restore import RestoreMode
from autoposter.artwork_modes.revert import RevertMode
from autoposter.metadata_backup import MetadataBackupMode
from autoposter.api.candidates import router as candidates_router
from autoposter.api.collections_builders import router as collections_builders_router
from autoposter.api.dashboard_stream import router as dashboard_stream_router
from autoposter.api.facts_backfill import router as facts_backfill_router
from autoposter.api.files import router as files_router
from autoposter.api.item_overrides import router as item_overrides_router
from autoposter.api.jobs import payload_number, payload_text
from autoposter.api.jobs import router as jobs_router
from autoposter.api.logs import router as logs_router
from autoposter.api.manual import router as manual_router
from autoposter.api.mismatches import router as mismatches_router
from autoposter.api.playlists import router as playlists_router
from autoposter.api.secret_rotation import router as secret_rotation_router
from autoposter.api.servers import router as servers_router
from autoposter.api.snapshots import events_snapshot, status_snapshot
from autoposter.api.testing import router as testing_router
from autoposter.api.version import router as version_router
from autoposter.api.stats import router as stats_router
from autoposter.api.auth import (
    ApiKeyPrincipal,
    api_key_or_session,
    create_session,
    hash_password,
    prune_expired,
    require_session,
    revoke,
    session_for_token,
    verify_password,
)
from autoposter.config.descriptions import FIELD_DESCRIPTIONS
from autoposter.config.impact import affected_items, count_affected, count_collection_posters
from autoposter.config.live import (
    FROZEN_SECTIONS,
    LIVE_EXCEPTIONS,
    frozen_reason,
    is_inert,
    swap_config,
)
from autoposter.config.loader import build_config, moved_kinds, read_config_document
from autoposter.config.overrides import (
    STORE_FORMAT,
    _read_file_document,
    document_paths,
    document_revision,
    empty_leaf_paths,
    load_overrides_document,
    merge_overrides,
    store_contents,
    store_row,
    unknown_key_paths,
    without_migrated_sections,
    write_store,
)
from autoposter.config.schema import Config, library_override_refusals
from autoposter.config.snapshots import capture_snapshot, list_snapshots, load_snapshot
from autoposter.db.models import (
    EventLog,
    ItemFacts,
    Job,
    ManagedCollection,
    ManagedPlaylist,
    MediaItem,
    MetadataWrite,
    Render,
    RenderDelivery,
    ScheduledRun,
)
from autoposter.db.models import Session as SessionModel
from autoposter.db.refs import native_ids, refs_for_items
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.queue.jobs import enqueue, enqueue_batch
from autoposter.render.pipeline import ART_KINDS_FOR, manual_override_path
from autoposter.scheduler.run_history import FULL_PASS_NAME, open_run
from autoposter.servers.presence import read_presence, refresh_presence
from autoposter.servers.registry import require_plex

logger = logging.getLogger(__name__)

# The names of the six periodic jobs, from the Job(name=...) literals in
# scheduler/jobs.py and scheduler/prune.py. Spelled out rather than imported
# from the job factories: importing those would pull plexapi and the arr/http
# client machinery into this module, which no request handler here needs. A
# name added there and not here can simply not be triggered by hand -- which
# is exactly what happened to credits_scan for a while, and what
# tests/test_api_scheduled_runs.py's agreement guard now catches in either
# direction.
SCHEDULED_JOB_NAMES = frozenset({
    "asset_stats",
    "collections_reconcile",
    "ratings_drift_sweep",
    "credits_scan",
    "plex_maintenance",
    "arr_sync",
    "asset_cleanup",
    "plex_prune",
    "plex_merge",
    "stale_job_reclaim",
    "pending_deliveries",
    "catch_up_drain",
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
    ~31.5s on the default retry config against an ordinary target, up to
    ~50s against one that answers 429 with a ``Retry-After`` clamped to the
    timeout (see notify/dispatch.py), against endpoints that answer in
    seconds. ``send`` never raises and does its own
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

# The cross-provider candidate browser: the only handler that talks to the
# provider clients, and the only one that fans out over all of them at once.
router.include_router(candidates_router)

# Manual mode: the same install tail as the picker, from an operator-supplied
# URL or mount path instead of a provider's candidate. Its own module because
# the SSRF guard and the mount containment that make an arbitrary source safe
# are the substance of it.
router.include_router(manual_router)

# Testing mode: render one styled sample artifact of any kind against a
# generated canvas and return the bytes inline. Its own module because it drives
# the pipeline's compositing seam directly and writes nothing anywhere.
router.include_router(testing_router)

# The collections preview: the builder engine run as a dry run, reporting what
# a pass would do. Its own module for the reason testing mode is -- it drives
# an engine directly and writes nothing.
router.include_router(collections_builders_router)

# The playlist definitions, their preview and the delete. Its own module for
# the reason the collections builders are, plus one of its own: a playlist's
# ownership predicate is a managed_playlists row keyed on the rating key, not
# the label-plus-row pair collections use, and the two must not be reachable
# from one handler that believes they are the same.
router.include_router(playlists_router)

# The jobs overview: what is pending or running, and the per-job cancel. Its
# own module because cancelling races the worker pool, and the row locking and
# state fork that make it safe are the substance of it. The parked-job
# endpoints below stay here -- they belong to the Failures page, which is a
# different question about a different set of states.
router.include_router(jobs_router)

# The one-shot facts backfill (roadmap row 206): the catch-up trigger for the
# never-written facts columns. Its own module because it is the one write
# surface that belongs to facts rather than to collections or the queue, and
# the park-while-TMDb-is-blocked rule is the substance of it.
router.include_router(facts_backfill_router)

# The id-mismatch view: where Plex and Radarr/Sonarr disagree about what a
# folder holds. Its own module because it is the only handler that pairs the
# two services against Plex, and it pairs them by path for a reason that needs
# writing down.
router.include_router(mismatches_router)

# The sidebar's version line: what this container is running, and whether a
# newer release has been published. Its own module because the rules about
# which builds may ask that question at all -- and what a tri-state
# `update_available` means -- are the substance of it.
router.include_router(version_router)

# The storage stats (roadmap row 52): how many artifacts this service has
# rendered per library and art kind, and how many bytes they occupy. Its own
# module because it is the only endpoint here that answers from a column the
# render pipeline stamps and a scheduled pass back-fills, and because the rule
# that makes it safe -- it never touches the filesystem, on an NFS assets_root
# -- is the substance of it.
router.include_router(stats_router)

# The Action Center (roadmap rows 11a/11b): the curation queue over the
# artwork this service chose and rendered. Its own module because the queries
# there are a different kind from every other listing here -- they select
# predicates built from the LIVE config, once into the WHERE clause and again
# as labelled booleans, so that "why is this row here" and "which rows are
# here" are one definition. Folding that into this module would put a second,
# subtler kind of query beside the plain column listings.
router.include_router(action_center_router)

# Per-item metadata overrides (roadmap row 99): the values an operator has
# declared for one item, which beat every provider source for that item. Its
# own module because it is the only place in this API where an operator's
# free text becomes a value written into Plex, and the two rules that makes
# necessary -- never seed a row from a provider (the freezing hazard) and
# never echo the value in a refusal (row 213) -- are the substance of it.
router.include_router(item_overrides_router)

# Webhook secret rotation (roadmap row 255). Its own module because it is the
# only handler here that imports `setup_arr`, and because the rule that makes
# it safe -- refuse unless `boot` said the secret came from the state file --
# is the substance of it.
router.include_router(secret_rotation_router)

# The operator's own overlay images and font faces, as files (roadmap row 55).
# Its own module because these are the only handlers here that WRITE a
# request-named filesystem path, and the four refusals that make that safe --
# the name rule, the protected set, the double-realpath containment and the
# `referenced_by` check -- are the substance of it.
router.include_router(files_router)

# The per-server catch-up and retry-failed buttons (spec §5). Its own module
# because it is the one surface that turns a CatchUpRefused into the 409 the
# button shows, and that translation is the whole of what it does -- every
# rule about what a catch-up may do lives in catchup.py.
router.include_router(servers_router)

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
    request: Request, _: SessionModel | ApiKeyPrincipal = Depends(api_key_or_session)
) -> dict:
    """Queue counts, worker count and the scheduled-job table.

    Each scheduled job carries a derived ``status`` (see
    ``api.snapshots._run_status``) so the dashboard can show a long run in
    progress instead of the previous run's outcome for its whole duration.

    Two things about it are surprising rather than wrong. The hand-trigger
    (``POST /api/scheduled-runs/{name}/run``) nulls ``last_started_at``
    without starting anything, so re-triggering a job that is already running
    briefly reports it as not-running until the scheduler's next claim
    re-stamps that column. And during a deployment rollout, the new pod's
    boot instant is later than the old pod's still-live run's start, so for
    the overlap window the new pod's own ``/api/status`` labels that live run
    ``interrupted`` even though it has not actually died yet.
    """
    # The queries live in api/snapshots.py because the dashboard stream's
    # broadcaster builds this same body on its poll -- see that module.
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        return await status_snapshot(
            session, request.app.state.config, request.app.state.scheduler_intervals,
            request.app.state.started_at,
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

        # One query for the whole page's refs, not one per row.
        refs_by_item = await refs_for_items(session, item_ids)

    return {
        "total": total,
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "library": item.library,
                "kind": item.kind,
                "refs": refs_by_item.get(item.id, {}),
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
    failed|skipped), not `Render.upload_status`. That is what list_items()'s own
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

        # One query for every render's deliveries, not one per render: the
        # page shows every art kind at once, and a render can have one row
        # per configured server. Ordered by server so the
        # chips do not reorder between page loads.
        deliveries_by_render: dict[int, list[RenderDelivery]] = {}
        if renders:
            delivery_rows = (
                await session.execute(
                    select(RenderDelivery).where(
                        RenderDelivery.render_id.in_([render.id for render in renders])
                    ).order_by(RenderDelivery.server)
                )
            ).scalars().all()
            for delivery in delivery_rows:
                deliveries_by_render.setdefault(delivery.render_id, []).append(delivery)

        show = None
        if item.kind in ("season", "episode") and item.parent_id is not None:
            parent = (
                await session.execute(select(MediaItem).where(MediaItem.id == item.parent_id))
            ).scalar_one_or_none()
            if parent is not None and parent.kind == "show":
                # A season's parent IS the show.
                show = parent
            elif (
                parent is not None
                and parent.kind == "season"
                and parent.parent_id is not None
            ):
                # An episode's parent_id points at its SEASON row (see
                # render/pipeline.py's _upsert_media_item and
                # adopt/walk.py's _resolved_episode), not the show directly --
                # one more hop is needed to reach it.
                show = (
                    await session.execute(
                        select(MediaItem).where(MediaItem.id == parent.parent_id)
                    )
                ).scalar_one_or_none()

        # Spec §5: the item page carries both tables' rows per server. One
        # query each, not one per server -- the page shows every server at
        # once and there are at most two.
        metadata_rows = (
            await session.execute(
                select(MetadataWrite)
                .where(MetadataWrite.item_id == item_id)
                .order_by(MetadataWrite.server)
            )
        ).scalars().all()
        art_kind_by_render = {render.id: render.art_kind for render in renders}
        by_server: dict[str, dict] = {}
        for row in metadata_rows:
            by_server.setdefault(row.server, {"metadata": None, "artwork": []})["metadata"] = {
                "status": row.status,
                "detail": row.detail,
                "attempts": row.attempts,
                "attempted_at": row.attempted_at,
                "written_at": row.written_at,
                "next_attempt_at": row.next_attempt_at,
            }
        for render_id, rows in deliveries_by_render.items():
            for delivery in rows:
                by_server.setdefault(
                    delivery.server, {"metadata": None, "artwork": []}
                )["artwork"].append((render_id, {
                    "art_kind": art_kind_by_render[render_id],
                    "status": delivery.status,
                    "detail": delivery.detail,
                    "attempts": delivery.attempts,
                    "attempted_at": delivery.attempted_at,
                    "uploaded_at": delivery.uploaded_at,
                    "next_attempt_at": delivery.next_attempt_at,
                }))
        servers_block = [
            {
                "server": name,
                "metadata": entry["metadata"],
                # Sorted by art kind so the table does not reorder between
                # page loads -- the same rule the delivery chips already keep.
                # By render id within the kind, because an item CAN hold two
                # renders of one kind (a re-render row) and the art kind alone
                # left those two in whatever order the query returned, which
                # is the exact instability the sort was added to remove. The
                # id rides alongside rather than in the payload: it is a sort
                # key here, not something the page shows.
                "artwork": [
                    row for _, row in sorted(
                        entry["artwork"], key=lambda pair: (pair[1]["art_kind"], pair[0])
                    )
                ],
            }
            for name, entry in sorted(by_server.items())
        ]

        refs = await refs_for_items(session, [item.id])

    return {
        "id": item.id,
        "title": item.title,
        "library": item.library,
        "kind": item.kind,
        "refs": refs.get(item.id, {}),
        "season_number": item.season_number,
        "episode_number": item.episode_number,
        # The show an episode or season belongs to, or null when the item has
        # no parent (a movie/show) or the parent hasn't been processed yet --
        # _upsert_media_item leaves parent_id null in that case rather than
        # inventing one, and this degrades the same honest way.
        "parent": None if show is None else {"id": show.id, "title": show.title},
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
        "servers": servers_block,
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
                # Which image of that provider's many, and whether it carried
                # burned-in text. The candidate browser marks the in-use base
                # against its own list, which `provider` alone cannot identify.
                # Under a manual override these are the override's own path
                # and a null textlessness -- see the "manual" branch in
                # render/pipeline.py, which has no candidate to describe.
                "source_url": render.source_url,
                "textless": render.textless,
                "rendered_at": render.rendered_at,
                "uploaded_at": render.uploaded_at,
                "deliveries": [
                    {
                        "server": delivery.server,
                        "status": delivery.status,
                        "attempted_at": delivery.attempted_at,
                        "uploaded_at": delivery.uploaded_at,
                        "next_attempt_at": delivery.next_attempt_at,
                        "detail": delivery.detail,
                    }
                    for delivery in deliveries_by_render.get(render.id, [])
                ],
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


@router.get("/playlists")
async def list_playlists(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """The ``managed_playlists`` rows -- what this service owns and what the
    last pass saw. ``GET /collections``' twin, with two differences that are
    the whole of 98a's design: there is no ``library`` (a playlist belongs to
    none; ``libraries`` is the definition's scope, recorded for the report) and
    ``plex_rating_key`` is served, because for a playlist it IS the ownership
    predicate rather than a convenience."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        rows = (
            (await session.execute(select(ManagedPlaylist).order_by(ManagedPlaylist.id)))
            .scalars()
            .all()
        )
    return {
        "playlists": [
            {
                "id": row.id,
                "title": row.title,
                "plex_rating_key": row.plex_rating_key,
                "libraries": row.libraries,
                # Straight through, nulls included. NULL means no pass has
                # stamped this row; a zero delta is a different thing entirely
                # -- a pass that ran and found nothing to change. Rendering
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
    jobs = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        jobs.append(
            {
                "id": row.id,
                "kind": row.kind,
                "attempts": row.attempts,
                "reason": row.last_error,
                "updated_at": row.updated_at,
                # The four payload fields that name the item, and nothing
                # else -- api/jobs.py's precedent for the Jobs page. The
                # payload is never echoed wholesale: it can carry provider
                # ids and, for other kinds, source URLs.
                "title": payload_text(payload, "title"),
                "item_kind": payload_text(payload, "kind"),
                "season_number": payload_number(payload, "season_number"),
                "episode_number": payload_number(payload, "episode_number"),
            }
        )
    return {"jobs": jobs}


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
    partial unique index that allows one pending-or-deferred job per dedupe
    key -- deferred included, so this also fires when the item's re-queued
    job is off waiting on Plex rather than pending outright. There is
    nothing to retry in either case -- the work is already queued, running
    or waiting -- so it is a 409 saying so, not the IntegrityError a 500
    would come from.
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

    The row's Plex ref rides along: this item has already been resolved once,
    so the job need not ask an agent to find it again -- and for an adopted
    season or episode the stored external ids are the *item's* own, which the
    GUID search would misread as the series'. It does not enter the dedupe key
    (intake/arr.py).
    """
    plex_ids = await native_ids(session, [item.id], "plex")
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
    return await enqueue(
        session, kind="process_item", payload=asdict(intent), dedupe_key=intent.dedupe_key
    )


@router.post("/items/{item_id}/reprocess")
async def reprocess_item(
    item_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Enqueue a process_item job for one item, via the same enqueue()/dedupe_key
    convention every other intake path uses -- asking twice while the first
    request is still pending queues nothing the second time.

    ``note`` is always ``None``. It used to carry a warning when the row being
    re-run had an identity twin under another Plex rating key -- rows keyed on
    Plex's own id could fork like that; rows keyed on identity (spec §4.2)
    cannot, so there is no twin left to report. The key stays in the response
    rather than disappearing, so a client need not special-case its absence.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        job_id = await _enqueue_reprocess(session, item)
    return {"queued": job_id is not None, "job_id": job_id, "note": None}


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

        plex_ids = await native_ids(session, [item.id], "plex")
        native_id = plex_ids.get(item.id)
        if native_id is None:
            raise HTTPException(status_code=409, detail="this item has no Plex id")

        resolved = ResolvedItem(
            server="plex", native_id=native_id, library=item.library, kind=item.kind,
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
            # Roadmap row 248, under row 213's served-string law: the errno and
            # the absolute path on the mount stay on this WARNING -- the pod
            # log is the trusted sink (row 207) -- and the served detail is the
            # fixed sentence api/manual.py and api/candidates.py already serve
            # for the same mount and the same OSError, so the three sites now
            # agree. str(exc) on an os.replace failure is "[Errno N] <libc
            # text>: '<absolute path>'": an unbounded string the OS chose,
            # which is exactly the case the generic rule exists for.
            logger.warning("could not disable override %s: %s", override, exc)
            raise HTTPException(
                status_code=503, detail="could not write to the override mount"
            ) from None

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

    Each intent carries its row's Plex ref so the job resolves straight to the
    item instead of searching by external id. That matters most for the
    adopted seasons and episodes, whose stored ids are their own rather than
    the series' -- see ``PlexClient._fetch_by_rating_key_sync``. The ref is not
    part of the dedupe key, so this changes nothing about what deduplicates.

    Recorded as a run (roadmap row 53): a `runs` row of kind `full_pass` is
    opened here, in this same session and therefore this same transaction, so
    that its `started_at` and every enqueued job's `created_at` resolve to one
    `transaction_timestamp()` and every job this pass creates is inside its own
    run's window by construction. Nothing here closes the row -- the pass has
    no end at this point, it has only just queued the work. The scheduler's
    poll loop closes it when the drain finishes (scheduler/core.py's
    `_close_drained_runs`).

    Pressing twice opens two rows whose windows overlap, and both will count
    the same drain. That is the honest consequence of a button that is not
    idempotent (see above); reusing an already-open row instead would mean a
    single row nothing ever closed could suppress every future pass's history.

    Presence first (spec §1). Every configured server is asked which
    libraries it carries, and the items of a library it does not carry are
    stamped `absent` in both outcome tables -- once per library, not once per
    attempt. A server that cannot answer right now is skipped rather than
    treated as carrying nothing, because "unreachable" and "does not have it"
    are different facts and only one of them is worth writing down.
    """
    # Spec §4.4 step 6: a Jellyfin index built once and reused between passes
    # (see jellyfin/index.py) would otherwise answer a full pass with
    # whatever library shape it happened to hold at the last resolve --
    # invalidated here so every server that keeps one rebuilds on its next
    # resolve, in this pass. `getattr(..., None)` rather than a capability
    # check: only Jellyfin's client currently defines `invalidate`, and a
    # server with no such concept (Plex resolves live, no index to go stale)
    # simply has nothing to call.
    for server in request.app.state.servers.values():
        invalidate = getattr(server, "invalidate", None)
        if invalidate is not None:
            invalidate()

    # ASKED here, outside the transaction below, and stamped inside it. Each
    # answer is a network round trip against a media server -- and the loop
    # above has just invalidated Jellyfin's index, so its answer is a live
    # call rather than a cached one. Asking inside the transaction held it
    # open and idle for every server's round trip.
    present = await read_presence(request.app.state.servers)

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Spec §1: presence is recomputed at the START of every full pass, in
        # this same transaction as the run row and the enqueue -- so a pass
        # either opens with its `absent` rows stamped or does not open at
        # all. A library that has reappeared on a server flips its items back
        # to `pending` here and they flow through the ordinary retry, which
        # is what makes a library map change eventually self-correcting even
        # without the catch-up.
        presence_outcomes = await refresh_presence(session, present)
        rows = (
            await session.execute(
                select(
                    MediaItem.id,
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
        # One query for the whole pass's Plex ids, not one per row.
        plex_ids = await native_ids(session, [row.id for row in rows], "plex")
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
                refs={"plex": plex_ids[row.id]} if row.id in plex_ids else {},
            )
            entries.append((asdict(intent), intent.dedupe_key))
        # Before enqueue_batch, which is what commits this transaction: the
        # row and the jobs then share one transaction timestamp.
        await open_run(session, kind="full_pass", name=FULL_PASS_NAME)
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
    return {"total": total, "queued": queued, "skipped": skipped, "presence": presence_outcomes}


class ModeFilterBody(BaseModel):
    """Filters and the apply switch for a filtered, Plex-writing artwork mode.

    Shared by restore, revert and reset, which take the same three filters and
    the same switch. ``type`` is the item kind (movie|show|season|episode),
    mapping onto the ``/api/items`` filter idiom; ``apply`` is optional so an
    omitted value falls back to that mode's ``config.artwork_modes.*_apply``
    flag -- the dry-run-by-default posture -- rather than being forced true by
    a missing field.
    """

    type: str | None = None
    library: str | None = None
    item_id: int | None = None
    apply: bool | None = None


def _require_plex(request: Request) -> tuple:
    """The Plex client and http client, or a 503 if this process has neither.

    Both come from the lifespan's ``run_background`` branch, so a replica
    running without the background services -- or a test app -- has them unset.
    The artwork modes talk to Plex, so there is nothing to do without them; the
    503 mirrors ``live_artwork``'s own answer to the same condition.
    """
    plex = request.app.state.plex
    http = request.app.state.http
    if plex is None or http is None:
        raise HTTPException(status_code=503, detail="this instance is not connected to Plex")
    return plex, http


@router.post("/artwork-modes/backup", dependencies=[Depends(require_plex)])
async def run_artwork_backup(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Copy Plex's current artwork into the backup tree (roadmap row 76).

    Read-only w.r.t. Plex and the database: it reads what Plex is serving and
    writes a Kometa-structured tree under ``plex_backup_root``, so there is no
    dry-run to run and no worker pause to raise -- nothing here writes to Plex.
    The response reports the per-item tally the walk produced.
    """
    plex, _http = _require_plex(request)
    config = request.app.state.config
    mode = BackupMode(config, plex)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await mode.run(session)
    return result.as_response()


@router.post("/metadata-backup", dependencies=[Depends(require_plex)])
async def run_metadata_backup(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Export Plex's current metadata to a YAML backup (roadmap row 86).

    Read-only w.r.t. Plex and the database -- it reads what Plex is serving and
    writes one file per library under ``operations.metadata_backup_root`` -- so
    there is no dry run to run and no worker pause to raise. Gated on
    ``operations.metadata_backup_enabled``, which is what the refusal names
    while it is off.
    """
    plex, _http = _require_plex(request)
    config = request.app.state.config
    mode = MetadataBackupMode(config, plex)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await mode.run(session)
    return result.as_response()


# What a second applied run is told while one is already going. A 409 with a
# ``detail`` sentence rather than a 200 carrying ``status: "busy"``: nothing
# ran, so there are no counts to report in the shape the other responses use,
# and the page already renders a failed trigger's ``detail`` in that mode's own
# error slot. The client disables every apply path while a run is in flight, so
# reaching this means two operators (or two tabs) pressed Confirm at once.
MODE_BUSY_DETAIL = (
    "another artwork mode is already running on this instance; wait for it to "
    "finish before starting another"
)


async def _run_plex_writing_mode(request: Request, mode, apply: bool) -> dict:
    """Run one Plex-writing mode inline and return its parallel response body.

    Inline rather than through the worker dispatch map, deliberately: the pause
    fence has to be held across the whole operation, and the trigger answers
    with the counts synchronously so the operator sees what a dry run would do
    without going and reading a job row.

    Only an applied run writes to Plex, so only it raises the fence; a dry run
    touches nothing on Plex, must not idle the live pipeline, and is free to run
    alongside anything else. The fence is released in ``WorkerPause.paused``'s
    ``finally``, so a mode that raises does not leave the pool idled forever.

    An applied run does two more things before it writes a byte:

    * **It drains.** Raising the fence only stops the pool *claiming*; a worker
      already mid-job is still writing to the same Plex items. ``drain()`` waits
      for that job to finish, which is what the plan's "pool finishes in-flight
      then idles" actually requires (see ``WorkerPause``).
    * **It takes the process-wide mode lock.** Two concurrent applied runs would
      each enter ``paused()`` and the first to finish would drop the fence while
      the second was still writing -- and they would be writing to Plex at the
      same time as each other besides. One at a time; a second trigger while one
      runs is answered 409 (``MODE_BUSY_DETAIL``) rather than queued, because an
      operator who pressed Confirm twice wants to be told, not to have the
      second run start silently some minutes later.

    The lock is per process, exactly as the fence is, and carries the same
    limitation: a second replica is not serialised against this one.
    """
    session_factory = request.app.state.session_factory
    if not apply:
        async with session_factory() as session:
            result = await mode.run(session)
        return result.as_response()

    lock = request.app.state.mode_lock
    # Safe without a lock of its own: nothing is awaited between the check and
    # the acquire, so no other task can take the lock in between.
    if lock.locked():
        raise HTTPException(status_code=409, detail=MODE_BUSY_DETAIL)
    async with lock:
        pause = request.app.state.worker_pause
        with pause.paused():
            await pause.drain()
            async with session_factory() as session:
                result = await mode.run(session)
    return result.as_response()


@router.post("/artwork-modes/restore", dependencies=[Depends(require_plex)])
async def run_artwork_restore(
    body: ModeFilterBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Push the backup tree back to Plex, filtered (roadmap row 77).

    Dry-run by default: with ``apply`` false (or omitted, falling back to
    ``config.artwork_modes.restore_apply``) it reports what it WOULD push and
    pushes nothing. The response names dry-run vs applied and the counts either
    way; see ``_run_plex_writing_mode`` for the worker fence.
    """
    plex, http = _require_plex(request)
    config = request.app.state.config
    headers = {"X-Plex-Token": request.app.state.secrets.plex_token}
    apply = body.apply if body.apply is not None else config.artwork_modes.restore_apply
    mode = RestoreMode(
        config, plex, http, headers, apply=apply,
        kind=body.type, library=body.library, item_id=body.item_id,
    )
    return await _run_plex_writing_mode(request, mode, apply)


@router.post("/artwork-modes/revert", dependencies=[Depends(require_plex)])
async def run_artwork_revert(
    body: ModeFilterBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Put the un-badged ``/assets`` base back on Plex, filtered (roadmap row 65).

    The "remove overlays" mode: it re-uploads the base image the badged one was
    composited over, which is the same picture without the badges. Dry-run by
    default (``config.artwork_modes.revert_apply``); items whose base is not on
    disk are counted but never pushed.
    """
    plex, http = _require_plex(request)
    config = request.app.state.config
    headers = {"X-Plex-Token": request.app.state.secrets.plex_token}
    apply = body.apply if body.apply is not None else config.artwork_modes.revert_apply
    mode = RevertMode(
        config, plex, http, headers, apply=apply,
        kind=body.type, library=body.library, item_id=body.item_id,
    )
    return await _run_plex_writing_mode(request, mode, apply)


@router.post("/artwork-modes/reset", dependencies=[Depends(require_plex)])
async def run_artwork_reset(
    body: ModeFilterBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Unlock our artwork and hand the fields back to Plex's agent (roadmap row 66).

    Poster *and* background, because the pipeline uploads and locks both, so a
    complete undo has to release both. Acts only on fields currently showing
    artwork this service uploaded, told apart from a hand-set one by its EXIF
    provenance -- so "reset everything" never means "undo the operator's own
    choices". ``reset``/``failed`` therefore count fields, not items, and
    ``fields`` reports how many a dry run would touch. Dry-run by default
    (``config.artwork_modes.reset_apply``). The response carries a ``note``
    saying the replaced upload stays on the Plex server: nothing here can delete
    it, and the UI has to say so.
    """
    plex, _http = _require_plex(request)
    config = request.app.state.config
    apply = body.apply if body.apply is not None else config.artwork_modes.reset_apply
    mode = ResetMode(
        config, plex, apply=apply,
        kind=body.type, library=body.library, item_id=body.item_id,
    )
    return await _run_plex_writing_mode(request, mode, apply)


@router.post("/artwork-modes/logo", dependencies=[Depends(require_plex)])
async def run_artwork_logo(
    body: ModeFilterBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Upload a clearlogo for every item Plex has none for (roadmap row 71).

    Movies and shows only -- a clearlogo belongs to one of those and to nothing
    below one. The logo comes from the same provider ladder the render path
    walks, ranked by ``artwork.logo_language_order``, and is uploaded to Plex's
    own ``clearLogo`` field and locked; this is a separate target from the
    badged poster, not a variation on it.

    Items that already show a clearlogo are left alone: "fill in the missing
    ones" is not "overwrite every one". Dry-run by default
    (``config.artwork_modes.logo_apply``), and a dry run spends no provider call
    at all -- it reports how many items are missing a logo, not which logo each
    would get.
    """
    plex, http = _require_plex(request)
    config = request.app.state.config
    headers = {"X-Plex-Token": request.app.state.secrets.plex_token}
    apply = body.apply if body.apply is not None else config.artwork_modes.logo_apply
    mode = LogoMode(
        config, plex, http, headers, request.app.state.providers, apply=apply,
        kind=body.type, library=body.library, item_id=body.item_id,
    )
    return await _run_plex_writing_mode(request, mode, apply)


@router.post("/artwork-modes/logo-revert", dependencies=[Depends(require_plex)])
async def run_artwork_logo_revert(
    body: ModeFilterBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Remove the clearlogos this service set, and only those (roadmap row 67).

    Acts on an item only when the logo updater recorded a marker for it *and*
    Plex still has that exact upload selected, so a logo an operator set by hand
    -- or one they replaced ours with since -- is never touched. Dry-run by
    default (``config.artwork_modes.logo_revert_apply``).

    Unlike the poster reset, this leaves nothing orphaned on the Plex server:
    Plex exposes a DELETE for the clearlogo field, so the response carries no
    note about a dangling upload.
    """
    plex, http = _require_plex(request)
    config = request.app.state.config
    headers = {"X-Plex-Token": request.app.state.secrets.plex_token}
    apply = (
        body.apply if body.apply is not None
        else config.artwork_modes.logo_revert_apply
    )
    mode = LogoRevertMode(
        config, plex, http, headers, apply=apply,
        kind=body.type, library=body.library, item_id=body.item_id,
    )
    return await _run_plex_writing_mode(request, mode, apply)


def _host_only(url: str) -> str:
    """A notification URL reduced to its host.

    ``notifications.url`` may embed a token in its path (Uptime-Kuma style),
    and every payload that reaches the operator over HTTP carries the host
    only -- the ``_record_failure`` stance in notify/dispatch.py.
    """
    try:
        return httpx.URL(url).host or ""
    except Exception:  # a malformed URL must not break the endpoint
        return ""


# Config paths whose served value is *not* the stored value, mapped to the
# reduction ``GET /api/config`` applies. Derived rather than duplicated below,
# because three things have to agree about this set and any drift between them
# corrupts a stored setting: the redaction itself, the ``redacted_paths`` the
# response advertises, and the keep-sentinel resolution on the write path.
_REDACTORS: dict[str, Callable[[str], str]] = {"notifications.url": _host_only}
REDACTED_PATHS: tuple[str, ...] = tuple(_REDACTORS)

# Paths the service computes rather than the operator setting. ``version`` is
# the render-settings hash (config/loader.py's render_version), stored on each
# Render row so a settings change is detectable as staleness -- writing one by
# hand overrides it with a value the next load recomputes away. Served so the
# editor can render it read-only instead of offering an edit that does nothing
# (roadmap row 112). Not a refusal: an override on it is still accepted and
# still inert, exactly as before.
COMPUTED_PATHS: tuple[str, ...] = ("version",)

# What an editor sends at a ``REDACTED_PATHS`` path to mean "leave the stored
# override exactly as it is".
#
# This exists because a page that was served a *truncated* value cannot send
# that value back: doing so would store the truncation as the override and
# destroy the real setting (a push token silently gone), while leaving the
# path out would drop the override instead. Neither is expressible from
# redacted data, so the editor says "keep" and the server -- which still has
# the stored value -- resolves it. Spelled exactly once, here, and published
# by the GET as ``keep_sentinel`` so no client carries a copy of it.
KEEP_SENTINEL = "***KEEP***"

_MISSING = object()


def _read_path(document: dict, path: str) -> object:
    """The value ``path`` names in ``document``, or ``_MISSING``."""
    current: object = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _set_path(document: dict, path: str, value: object) -> None:
    """Write ``value`` at ``path``, creating the intermediate mappings."""
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _sentinel_occurrences(value: object, path: str = "") -> list[tuple[str, bool]]:
    """Every place ``KEEP_SENTINEL`` appears in a document.

    ``(dotted path, replaceable)``, where ``replaceable`` is true only when the
    sentinel *is* the whole value at that path -- the one shape a stored
    override can be substituted into. A sentinel buried inside a list is
    reported at the list's own path with false, so it is rejected outright
    rather than half-resolved into a list the operator never asked for.
    """
    if isinstance(value, str):
        return [(path, True)] if value == KEEP_SENTINEL else []
    if isinstance(value, dict):
        found: list[tuple[str, bool]] = []
        for key, child in value.items():
            where = f"{path}.{key}" if path else str(key)
            found.extend(_sentinel_occurrences(child, where))
        return found
    if isinstance(value, list):
        buried = any(_sentinel_occurrences(item, path) for item in value)
        return [(path, False)] if buried else []
    return []


async def _resolve_keep_sentinels(request: Request, document: dict) -> dict:
    """``document`` with every keep sentinel replaced by the stored override.

    Returns the document unchanged when it carries no sentinel, which is the
    ordinary case. The sentinel is resolved *before* validation and before
    persistence, so it never reaches either as a literal -- a config whose
    ``notifications.url`` is the string ``***KEEP***`` is not a config anyone
    asked for, and storing one would be exactly the corruption this guards.

    Two ways to get a 422, both labelled with the path:

    - the sentinel anywhere but as the whole value at a ``REDACTED_PATHS``
      path, which is meaningless -- nothing there was redacted, so the client
      was never denied the real value and has no reason to ask for it back;
    - the sentinel at a redacted path with no stored override, where there is
      no value to keep. Answering it with the mounted file's value would turn
      "keep what is stored" into "freeze today's file value as an override".
    """
    occurrences = _sentinel_occurrences(document)
    if not occurrences:
        return document

    async with request.app.state.session_factory() as session:
        stored = await load_overrides_document(session)

    resolved = deepcopy(document)
    errors: list[dict] = []
    for path, replaceable in occurrences:
        if not replaceable or path not in REDACTED_PATHS:
            errors.append(
                _error(path, "the keep marker is only accepted as the whole value of a redacted setting")
            )
            continue
        value = _read_path(stored, path)
        if value is _MISSING:
            errors.append(_error(path, "there is no stored override here to keep"))
            continue
        _set_path(resolved, path, value)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return resolved


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
    in its path (Uptime-Kuma style), so it is reduced to its host by
    ``_REDACTORS``; the full URL stays in the config file the operator
    already owns.

    Carries eight things the editor needs beyond the values themselves.
    ``overridden_paths`` is the provenance: which of these values come from
    the database overrides rather than the mounted YAML, so the UI can mark
    them and offer "revert to base". ``frozen_paths`` maps each restart-only
    path to the reason a live swap cannot reach it (see config/live.py) --
    sent as data so the editor can flag a field without duplicating this
    project's startup wiring in TypeScript. ``redacted_paths`` and
    ``keep_sentinel`` are the other half of the same honesty: they name the
    values this response is *not* telling the truth about, and give the
    editor the one token it can send back for them without either destroying
    the stored value or dropping it (see ``KEEP_SENTINEL``).
    ``field_descriptions`` maps each setting's dotted path to what that
    setting does, condensed from the schema's own comments (roadmap row 217)
    -- what the page renders as the row's hover text. It deliberately says
    nothing about *when* a change applies: that is ``frozen_paths``' fact and
    the editor renders it separately. A few of its keys carry a ``[]``
    segment (``collections.definitions[].title``): those describe the fields
    of the objects inside a list, which this editor cannot edit yet (roadmap
    row 138). ``[]`` is a marker in that map only -- no endpoint here accepts
    a path containing it.
    ``computed_paths`` and ``live_paths`` are the last two, and both exist so
    the editor never contradicts this service about a path it already knows
    the answer for. The first names the values this process derives rather
    than reads (``version``), which the editor renders read-only instead of
    offering an inert edit. The second names the paths a broader frozen
    prefix would otherwise swallow but which are genuinely read per use
    (``config/live.LIVE_EXCEPTIONS``) -- without it the editor flags
    ``plex.resolve_max_attempts`` as needing a restart while ``frozen_reason``
    here correctly says it does not.
    ``overrides_revision`` is the eighth and the newest: a content hash of the
    stored overrides document, which every editor carries away with its seed
    and sends back with its save. It is what lets the write path tell a save
    composed against *this* document apart from one composed against a document
    that has since moved -- the difference between a save and a silent deletion
    of everything another page added in between.
    """
    config = request.app.state.config
    secrets = request.app.state.secrets
    body = config.model_dump(mode="json")
    # Roadmap row 92. Re-dumped with `exclude_unset` so a library block is
    # served as the leaves it STATES rather than as the shape of the model
    # that holds them: the wholesale dump carries a null for every unstated
    # leaf, and `ConfigNode` is shape-driven, so each one would render as a
    # read-only "(not set)" row under the matrix.
    #
    # `exclude_unset` and not `exclude_none`, and the difference is real: an
    # operator who writes `user_rating_source: null` for one library has SAID
    # something ("this library uses no user rating source"), `config_for_library`
    # merges it, and `exclude_none` would hide the one leaf that is hardest to
    # explain the absence of. This is the same predicate the seam merges on,
    # so what is served and what takes effect agree by construction.
    body["libraries"] = {
        name: override.model_dump(mode="json", exclude_unset=True)
        for name, override in config.libraries.items()
    }
    for path, redact in _REDACTORS.items():
        value = _read_path(body, path)
        if isinstance(value, str) and value:
            _set_path(body, path, redact(value))
    body["secrets"] = {field: _REDACTED for field in secrets.model_dump()}
    async with request.app.state.session_factory() as session:
        try:
            document = await load_overrides_document(session)
        except ValueError as exc:
            # A hand-edited config_overrides row whose document is not a JSON
            # object -- see load_overrides_document's docstring. An operator
            # needs to know what to fix, not a traceback.
            raise HTTPException(
                status_code=500,
                detail="config overrides row is corrupt (not a JSON object); fix or delete it",
            ) from exc
    body["overridden_paths"] = sorted(document_paths(document))
    # Computed from the same document `overridden_paths` came from: one extra
    # hash, no extra query, and it arrives with the seed -- which is exactly
    # the invariant a stale-write check needs, because a page that seeded from
    # this response holds this token for what it seeded from.
    body["overrides_revision"] = document_revision(document)
    body["frozen_paths"] = dict(FROZEN_SECTIONS)
    body["redacted_paths"] = list(REDACTED_PATHS)
    body["keep_sentinel"] = KEEP_SENTINEL
    body["field_descriptions"] = dict(FIELD_DESCRIPTIONS)
    body["computed_paths"] = list(COMPUTED_PATHS)
    body["live_paths"] = sorted(LIVE_EXCEPTIONS)
    return body


# A normal edit drops 0 or 1 override; the 2026-09-01 incident dropped 17. A
# module constant rather than a setting on purpose: a destructiveness cap an
# operator can raise from the same page the destructive write comes from is
# not a cap, and this phase is deliberately disjoint from config/schema.py.
OVERRIDE_DROP_CAP = 3


class OverridesBody(BaseModel):
    """The whole overrides document, always sent whole.

    Not a patch: the document *is* the operator's complete set of deltas, and
    "revert this field to the file's value" is expressed by the key being
    absent from it. A patch shape would need an out-of-band way to say
    "delete", and the obvious candidate -- sending ``null`` -- already means
    something else (see ``_validated_generation``).

    The envelope forbids extras and the *document* does not, and the asymmetry
    is deliberate. Forbidding here turns one specific catastrophe into an error
    message: a body of ``{"artwork": ..., "scheduler": ...}`` -- the document
    sent bare, which is what a hand-written fetch produces -- used to match
    zero declared fields under pydantic's default ``extra="ignore"``, bind
    ``document`` to its ``{}`` default, and wipe every stored override with a
    200 (the 2026-09-01 incident). Inside the document, ``unknown_key_paths``
    gives a far better error than pydantic could, at full depth and with the
    dotted path an operator can act on, so nothing is gained by forbidding
    twice.

    ``confirm`` authorises a destructive write -- one that empties the store or
    drops more than ``OVERRIDE_DROP_CAP`` paths. A flag rather than a second
    endpoint, for the reason ``DeleteRequest.confirm`` gives in
    ``api/collections_builders.py``: what is being confirmed is *this*
    document against *this* store, which a separate endpoint could not name.
    ``POST /api/config/preview`` accepts it and ignores it, so all three arms
    keep taking one body shape.

    ``document`` has no default. It did once, and a body carrying only
    ``confirm`` bound it to ``{}`` -- the confirm was truthy, so
    ``_drop_refusal`` never ran, and the result was a 200 that emptied the
    store with no ``document`` key in sight. Requiring the field costs no
    caller anything real (every one of them always sends it) and closes that
    hole for free.

    ``expected_revision`` is the token that came with the seed this document
    was composed from (``GET /api/config``'s ``overrides_revision``). When it
    is present and no longer current, the write is refused with a 409 rather
    than overwriting whatever arrived in between. When it is absent the write
    proceeds, so a scripted client is not broken by this upgrade; the four
    pages are held to sending it by their own tests, not by this model.
    """

    model_config = ConfigDict(extra="forbid")

    document: dict
    expected_revision: str | None = None
    confirm: bool = False


def _error(path: str, message: str) -> dict:
    return {"path": path, "message": message}


def _dotted(loc: tuple) -> str:
    """A pydantic error location as a dotted config path."""
    return ".".join(str(part) for part in loc)


def _changed_paths(before: dict, after: dict, prefix: str = "") -> list[str]:
    """Dotted paths whose value differs between two config dumps.

    Both directions: a key the candidate config no longer overrides has still
    changed, because reverting to the file's value is a change like any other.
    """
    changed: list[str] = []
    for key in set(before) | set(after):
        where = f"{prefix}.{key}" if prefix else str(key)
        old, new = before.get(key), after.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            changed.extend(_changed_paths(old, new, where))
        elif old != new:
            changed.append(where)
    return changed


def _restart_required(before: Config, after: Config) -> list[str]:
    """Which of the changed paths a generation swap does not reach, minus the
    ones a restart does not reach either.

    Those -- currently just ``api_docs_enabled`` -- are reported separately by
    ``_inert_changes``, so this list stays a promise the editor can keep:
    every path in it, restarting really does apply.
    """
    changed = _changed_paths(before.model_dump(mode="json"), after.model_dump(mode="json"))
    return sorted(
        path for path in changed if frozen_reason(path) is not None and not is_inert(path)
    )


def _inert_changes(before: Config, after: Config) -> list[str]:
    """Changed paths that no restart can apply either -- only editing the
    mounted config file reaches them (``config.live.INERT_SECTIONS``)."""
    changed = _changed_paths(before.model_dump(mode="json"), after.model_dump(mode="json"))
    return sorted(path for path in changed if is_inert(path))


def _render_affecting(before: Config, after: Config) -> bool:
    """Whether an impact count could possibly differ from "nothing".

    The cheap short-circuit the preview needs, and it has to be exact in one
    direction: the impact walk approximates two render-time facts it cannot
    know (config/impact.py's docstring), so running it against an edit that
    changes nothing about rendering would report a non-zero count made
    entirely of that approximation. An operator retuning ``scheduler`` cadences
    must see "no re-renders", not "~40 items".

    Enumerated rather than guessed. The walk reads exactly two things off the
    config: the render version -- which by construction covers ``artwork``,
    the asset roots and ``library_folders`` (config/loader.py's
    ``render_version``/``render_version_for``) -- and ``skip_tba``, which
    gates title cards and is deliberately *not* in the version. Nothing else
    it touches can move without one of those moving.

    Since roadmap row 111 the version is computed PER ART KIND, so "the
    version moved" is "some kind's version moved". ``config.version`` is
    retained as the wholesale hash and is kept here as the cheap
    short-circuit: it covers a strict superset of every per-kind payload, so
    an edit that leaves it alone cannot have moved a single kind and the eight
    per-kind hashes are skipped entirely. An operator retuning ``scheduler``
    cadences pays two dict dumps, exactly as before.
    """
    if after.skip_tba != before.skip_tba:
        return True
    if after.version == before.version:
        return False
    return bool(moved_kinds(before, after))


# The refusal the Custom collections panel makes in copy (`FILE_ROWS_NOTE`),
# said once here so the API and the UI cannot drift apart on the wording.
DEFINITIONS_GUARD_REFUSAL = (
    "the mounted config file lists collections.definitions, and an overrides "
    "list replaces the file's WHOLESALE -- storing this one would stop every "
    "file-defined definition being built. Either keep managing definitions in "
    "the config file, or move those rows into the overrides once and empty the "
    "file's definitions list"
)


async def _definitions_guard(request: Request, base: dict, document: dict) -> None:
    """Refuse the FIRST stored ``collections.definitions`` override while the
    mounted file still lists definitions.

    The freezing guard, moved from the panel into the API. It was UI-only:
    `CustomCollectionsPanel` disables Create while any listed row has
    ``provenance: "file"``, which is a control an operator can bypass with one
    `curl`. What the bypass costs is not a validation error -- the document is
    perfectly valid -- it is every file-defined collection silently ceasing to
    be built, discovered whenever somebody next looks at Plex.

    The predicate is the FIRST such store, not "the file lists definitions".
    Once an override is stored the file's list is already shadowed, and that
    is the state the definitions EDITOR lives in (the listing reads
    ``"override"`` there, and the panel offers Edit and Remove) -- refusing
    there would brick the editor on every deployment whose YAML kept a
    ``definitions:`` block. So this fires exactly on the transition, which is
    exactly when ``fileRows`` is true in the panel.

    Reached before anything is written, like every other refusal on this path.
    """
    incoming = document.get("collections")
    if not isinstance(incoming, dict) or "definitions" not in incoming:
        return
    section = base.get("collections")
    file_rows = section.get("definitions") if isinstance(section, dict) else None
    if not isinstance(file_rows, list) or not file_rows:
        return
    async with request.app.state.session_factory() as session:
        try:
            held = await load_overrides_document(session)
        except ValueError as exc:
            # The same answer `GET /api/config` gives the same corrupt row: an
            # operator needs to know what to fix, not an AttributeError.
            raise HTTPException(
                status_code=500,
                detail="config overrides row is corrupt (not a JSON object); fix or delete it",
            ) from exc
    stored_section = held.get("collections")
    if isinstance(stored_section, dict) and "definitions" in stored_section:
        return
    raise HTTPException(
        status_code=422,
        detail=[_error("collections.definitions", DEFINITIONS_GUARD_REFUSAL)],
    )


async def _validated_generation(
    request: Request, document: dict, *, check_empty_leaves: bool = True
) -> tuple[dict, Config]:
    """The document to store and the generation it describes, or a 422.

    Returns the *resolved* document, not the one that arrived: keep sentinels
    are substituted here (``_resolve_keep_sentinels``) and every caller
    persists what comes back, so the sentinel cannot reach the database or
    pydantic as a literal by any route.

    Nothing here persists, swaps or enqueues anything: every failure mode is
    reached before the first write, which is what "never half-apply" means in
    practice. The callers below rely on that ordering, and a test reorders it
    to prove they do.

    ``null`` is a value, not an eraser. Writing ``{"workers": null}`` asks for
    a config whose ``workers`` is null, which fails validation and comes back
    as a 422 -- it does not "unset" the override. Reverting a setting to the
    mounted file's value is expressed by leaving the key out of the document
    entirely; the document is always sent whole, so an absent key is
    unambiguous. Both halves are pinned by tests.

    ``check_empty_leaves`` guards the ``{}``-leaf refusal below and defaults
    on for every editor-facing caller. A restored delta is not editor input:
    it is the mounted file plus a delta both already validated at boot, and
    the file is free to spell an unset optional model section as ``{}``
    (``text: TextStyle | None`` at ``config/schema.py`` -- unset means no
    text, ``{}`` means a defaulted ``TextStyle()``, and the two are not the
    same value). Dropping such a ``{}`` to satisfy this guard would silently
    change what the restored configuration means, so the one caller restoring
    a delta turns the guard off instead of feeding it a document it never
    wrote.
    """
    # First, so that everything below -- the unknown-key walk, the merge and
    # pydantic -- sees real values rather than a marker they would each
    # misjudge in their own way.
    document = await _resolve_keep_sentinels(request, document)

    refusals = library_override_refusals(document)
    if refusals:
        # Ahead of the unknown-key walk deliberately (roadmap row 92). These
        # ARE real settings -- they are just structurally global -- so
        # "unknown setting" would be a true sentence that sent the operator
        # hunting for a typo that is not there. Same walk the file loader
        # uses, so the editor and the mounted YAML refuse the same keys for
        # the same stated reason.
        raise HTTPException(
            status_code=422,
            detail=[_error(path, reason) for path, reason in refusals],
        )

    unknown = unknown_key_paths(document)
    if unknown:
        # Ahead of pydantic on purpose: pydantic ignores unknown keys, so
        # without this a typo is stored, merged, validated clean and silently
        # does nothing for as long as the operator believes it took effect.
        raise HTTPException(
            status_code=422,
            detail=[_error(path, "unknown setting") for path in sorted(unknown)],
        )

    empty = empty_leaf_paths(document) if check_empty_leaves else []
    if empty:
        # {} is not a leaf document_paths can report honestly (its own
        # isinstance(value, dict) and value check is false for it) -- it
        # would report the WHOLE section as one override and seed the editor
        # into storing it wholesale on the next save. No caller can produce
        # this today; refusing it is the honest answer either way.
        raise HTTPException(
            status_code=422,
            detail=[
                _error(path, "empty object -- remove the key instead of setting {}")
                for path in sorted(empty)
            ],
        )

    base = await asyncio.to_thread(read_config_document, request.app.state.config_path)
    await _definitions_guard(request, base, document)
    try:
        merged = merge_overrides(base, document)
    except ValueError as exc:  # a `secrets` key anywhere in the document
        raise HTTPException(status_code=422, detail=[_error("secrets", str(exc))]) from exc
    try:
        return document, build_config(merged)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[_error(_dotted(e["loc"]), e["msg"]) for e in exc.errors()],
        ) from exc


def _drop_refusal(stored: dict, document: dict) -> str | None:
    """Why this write is too destructive to do unasked, or None.

    Counted in ``document_paths`` units -- exactly what ``GET /api/config``
    reports as ``overridden_paths`` -- so the operator, the API and this
    sentence all count the same things.
    """
    before = set(document_paths(stored))
    if not before:
        # Nothing to destroy. A fresh deployment's first save lands here and
        # must not be asked to confirm anything.
        return None
    if not document:
        return (
            f"this would clear all {len(before)} stored overrides; "
            "send confirm: true to do it deliberately"
        )
    dropped = sorted(before - set(document_paths(document)))
    if len(dropped) <= OVERRIDE_DROP_CAP:
        return None
    return (
        f"this would drop {len(dropped)} stored overrides "
        f"({', '.join(dropped)}); send confirm: true to do it deliberately"
    )


async def _persist_and_swap(
    request: Request,
    document: dict,
    after: Config,
    *,
    expected_revision: str | None = None,
    confirm: bool = False,
    reason: str = "save",
) -> dict:
    """Store the document, swap the running generation, log the event.

    Only ever called with an ``after`` that ``_validated_generation`` already
    built, so by the time anything is written the config is known to be whole.
    ``swap_config`` is three assignments and a dict refresh with no I/O, so it
    cannot fail after the row is committed either.

    This is also where every *write-only* guard lives, and none of them live in
    ``_validated_generation``. That function promises "nothing here persists,
    swaps or enqueues anything" and ``POST /api/config/preview`` routes through
    it -- a preview must never be refused for being destructive, because
    answering "what would this do" is the whole of its job. Guards that fire on
    a write belong on the write path, which ``put_config_overrides`` and
    ``apply_config_overrides`` both funnel through, so one insertion covers
    both.

    The stored document is read here, in the same session as the upsert, rather
    than in an earlier one: a separate read would be a TOCTOU window inside the
    fix for a TOCTOU bug.

    The revision compare goes here, under the row lock, for the same reason and
    one more: a check in ``_validated_generation`` would 409 a preview, and a
    check in a separate earlier session would be a TOCTOU window inside the fix
    for a TOCTOU bug. ``SELECT ... FOR UPDATE`` is what makes read-compare-write
    one step; the unconditional upsert it replaced was not.

    The pre-write snapshot goes in the same block for the same reason the guards
    do -- it is part of the write, not a step beside it.

    Crash-consistent by construction: if the process dies between the commit
    and ``swap_config``, requests keep being served by the old generation until
    restart, at which point ``load_effective_config`` reads the persisted
    overrides back off the database and starts on the new one -- correct by
    design, not by luck. ``api_docs_enabled`` is the one exception: a restart
    re-reads the database overrides but not the mounted file, so it lands back
    exactly where the file left it -- which is why it is reported in ``inert``
    below rather than ``restart_required``.
    """
    before = request.app.state.config
    async with request.app.state.session_factory() as session:
        # The row itself, not just what it says: whether one exists at all is
        # what decides the format stamp below, and a row holding nothing but
        # sections that left the schema says exactly what no row says.
        row = await store_row(session, for_update=True)
        stored, meta = store_contents(row)
        if expected_revision is not None:
            current = document_revision(stored)
            if expected_revision != current:
                # Loudly, and without a suggestion to retry: a client that
                # retried would re-apply an edit onto a document its operator
                # has not seen, which is a quieter version of the bug this
                # check exists to stop.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "these settings changed somewhere else while this "
                            "page was open; nothing was saved"
                        ),
                        "current_revision": current,
                        "changed_paths": sorted(_changed_paths(stored, document)),
                    },
                )
        if not confirm:
            refusal = _drop_refusal(stored, document)
            if refusal is not None:
                raise HTTPException(
                    status_code=422, detail=[_error("document", refusal)]
                )

        # Pre-write, in this session and this transaction. Same transaction is
        # the whole point: a snapshot that commits without its write, or a
        # write that commits without its snapshot, is worse than neither.
        await capture_snapshot(session, stored, reason, format=meta.get("format", 1))

        # Through the store's own writer, so the row's metadata is written
        # rather than left to the column default: a first-ever save that
        # inserted an empty one would say "this is a delta" about the whole
        # document it had just stored, and the next boot would merge it over
        # the mounted file.
        #
        # The format is stamped only where it cannot be a lie: on a row that
        # already says it, and where there is no row at all, which is the
        # insert this guards. Every other existing row is written through with
        # its metadata verbatim, because the document this save writes over it
        # is a delta too -- the editor composed it from the paths that row made
        # overridden -- and a raised format would tell the next boot to build a
        # whole configuration out of a fragment and fail on the first required
        # setting the fragment does not carry.
        if row is None or meta.get("format") == STORE_FORMAT:
            meta = {**meta, "format": STORE_FORMAT}
        await write_store(session, document, meta)
        session.add(
            EventLog(
                source="config",
                event_type="overrides_updated",
                # Versions and counts, never the document. The overrides carry
                # no secrets (merge_overrides refuses a `secrets` key
                # outright), but an audit row is read casually and copied into
                # tickets, and *which* settings an operator changed are not
                # this table's business. How many there were before and after
                # is: the 2026-09-01 wipe would have read `12 -> 0` here
                # instead of costing an investigation.
                payload={
                    "version_before": before.version,
                    "version_after": after.version,
                    "paths_before": len(document_paths(stored)),
                    "paths_after": len(document_paths(document)),
                    "reason": reason,
                },
                outcome=f"version {before.version} -> {after.version}",
            )
        )
        await session.commit()

    restart_required = _restart_required(before, after)
    inert = _inert_changes(before, after)
    swap_config(request.app, after)
    return {
        "version_before": before.version,
        "version_after": after.version,
        "restart_required": restart_required,
        "inert": inert,
        "overrides_revision": document_revision(document),
    }


@router.put("/config/overrides")
async def put_config_overrides(
    body: OverridesBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Save the overrides document and hot-swap the running configuration.

    This is also the "regenerate later" arm: nothing is enqueued, and the
    scheduled drift sweep and the full-pass button both pick the new
    fingerprints up on their own. ``POST /api/config/apply`` is this plus an
    immediate enqueue.

    An invalid document changes nothing at all -- no row, no swap, no event --
    and comes back as a 422 listing ``{path, message}`` per problem.

    A destructive save -- one that empties a non-empty store, or drops more
    than ``OVERRIDE_DROP_CAP`` of its paths -- is refused with a 422 naming the
    paths, and needs ``confirm: true``. A body that is not the ``{"document":
    ...}`` envelope is refused by the model before this runs.
    """
    document, after = await _validated_generation(request, body.document)
    return await _persist_and_swap(
        request,
        document,
        after,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
    )


@router.post("/config/preview")
async def preview_config_overrides(
    body: OverridesBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """What saving this document would do, without doing any of it.

    Validates exactly as the save does (same 422 shape, same never-half-apply
    ordering -- there is simply nothing after the validation to half-apply),
    then counts the stored renders the candidate config would invalidate.

    ``impact`` is null when the edit cannot change a rendered image: the count
    is an approximation (config/impact.py) and reporting its noise for a
    scheduler tweak would be worse than reporting nothing. When it is not
    null, it is an over-estimate by construction -- render it with a "~".

    ``collection_posters`` is a separate count and deliberately not part of
    ``impact``: ``config/impact.py`` walks the ``renders`` table, which has no
    row for a collection, so a ``collections.poster_title`` edit reports a null
    impact beside a real collection-poster count. Also an over-estimate --
    render it with a "~".

    Migrated sections are stripped first, for the same reason
    ``import_config_overrides`` strips them: a pre-migration backup previewed
    here must validate the same way importing it would, or the panel's
    "Import these settings" button never appears for the file the strip
    exists to accept.
    """
    before = request.app.state.config
    _, after = await _validated_generation(request, without_migrated_sections(body.document))
    impact = None
    collection_posters = 0
    render_affecting = _render_affecting(before, after)
    # A second reason to open a session, and the same "do not pay for a
    # scheduler tweak" posture: a collections.poster_title edit moves no render
    # fingerprint at all (config/loader.py's render_version excludes the whole
    # section) and so reports a null impact, while genuinely re-uploading every
    # managed collection's poster once. Both numbers are true at once.
    poster_change = after.collections.poster_title != before.collections.poster_title
    if render_affecting or poster_change:
        async with request.app.state.session_factory() as session:
            if render_affecting:
                impact = asdict(await count_affected(session, after))
            collection_posters = await count_collection_posters(session, before, after)
    return {
        "version_before": before.version,
        "version_after": after.version,
        "restart_required": _restart_required(before, after),
        "inert": _inert_changes(before, after),
        "impact": impact,
        "collection_posters": collection_posters,
    }


@router.post("/config/apply")
async def apply_config_overrides(
    body: OverridesBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Save, swap, and re-render the affected items now.

    The apply-now arm of the same save: identical validation and persistence
    (``_persist_and_swap``), then one ``process_item`` job per *item* behind
    the affected renders. Enqueued through ``enqueue_batch``, so the same
    pending-dedupe arbiter the full-pass button uses applies -- an item with a
    pass already queued is reported as ``skipped`` rather than queued twice.

    An edit that cannot change a rendered image queues nothing, for the reason
    the preview reports null impact for it.

    A destructive save -- one that empties a non-empty store, or drops more
    than ``OVERRIDE_DROP_CAP`` of its paths -- is refused with a 422 naming the
    paths, and needs ``confirm: true``, exactly as ``PUT /api/config/overrides``
    is. A body that is not the ``{"document": ...}`` envelope is refused by the
    model before this runs.
    """
    document, after = await _validated_generation(request, body.document)
    before = request.app.state.config
    saved = await _persist_and_swap(
        request,
        document,
        after,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
        reason="apply",
    )

    entries: list[tuple[dict, str]] = []
    if _render_affecting(before, after):
        async with request.app.state.session_factory() as session:
            entries = [
                (asdict(intent), intent.dedupe_key)
                for intent in await affected_items(session, after)
            ]
            queued = await enqueue_batch(session, "process_item", entries)
    else:
        queued = 0
    return {**saved, "queued": queued, "skipped": len(entries) - queued}


class SnapshotRestoreBody(BaseModel):
    """Restore one previous overrides document. ``confirm`` for a big drop.

    Restore is a save, not a bypass: it re-validates against the config on
    file, snapshots the current document first (so the restore is itself
    undoable), and respects the same drop cap and the same revision check a PUT
    does. Its body is therefore the save body minus the document, which the
    snapshot id supplies.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: str | None = None
    confirm: bool = False


def _redacted_document(document: dict) -> dict:
    """A snapshot as it may be served: the same redaction ``GET /api/config``
    applies, applied to the same paths.

    A stored snapshot holds ``notifications.url`` in full -- it has to, or a
    restore could not put the push token back. Serving that raw from a new
    endpoint would hand out the exact value the config endpoint is careful to
    withhold.
    """
    shown = deepcopy(document)
    for path, redact in _REDACTORS.items():
        value = _read_path(shown, path)
        if isinstance(value, str) and value:
            _set_path(shown, path, redact(value))
    return shown


@router.get("/config/snapshots")
async def get_config_snapshots(
    request: Request, _: SessionModel = Depends(require_session)
) -> list[dict]:
    """Every kept previous overrides document, newest first, metadata only.

    Enough to label a row -- how many settings it held, when it was displaced
    and by what -- and no documents: see ``list_snapshots``.
    """
    async with request.app.state.session_factory() as session:
        return await list_snapshots(session)


@router.get("/config/snapshots/{snapshot_id}")
async def get_config_snapshot(
    snapshot_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """One previous overrides document, redacted the way the live one is."""
    async with request.app.state.session_factory() as session:
        try:
            document, snapshot_format = await load_snapshot(session, snapshot_id)
        except LookupError:
            raise HTTPException(
                status_code=404, detail=f"no config snapshot {snapshot_id}"
            ) from None
        rows = {row["id"]: row for row in await list_snapshots(session)}
    meta = rows.get(snapshot_id, {})
    return {
        "id": snapshot_id,
        "created_at": meta.get("created_at"),
        "path_count": meta.get("path_count"),
        "reason": meta.get("reason"),
        "format": snapshot_format,
        "document": _redacted_document(document),
    }


@router.post("/config/snapshots/{snapshot_id}/restore")
async def restore_config_snapshot(
    snapshot_id: int,
    body: SnapshotRestoreBody,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Put a previous overrides document back, as a save.

    Not a bypass and not a second write path: the stored (unredacted) document
    goes through ``_validated_generation`` and ``_persist_and_swap`` exactly as
    a PUT's would, so it is re-validated against the config file as it stands
    *now* -- the mounted YAML may have moved under this snapshot since it was
    taken, and a snapshot from before a schema change must fail loudly here
    rather than brick the pod at the next boot.

    ``without_migrated_sections`` runs first, and it is not optional. A whole
    section that left the schema is stripped from the *stored* document on
    every read, but a raw snapshot row still holds it -- so without this the
    one recovery path would 422 on exactly the old snapshots recovery exists
    for.

    A snapshot older than the store format is a DELTA, not a document: it is a
    statement about the file that was mounted when it was taken, and it is
    restored the way it was applied -- merged over that file -- rather than
    stored as though it were the whole configuration, which would 422 on every
    required field the delta never mentioned.
    """
    async with request.app.state.session_factory() as session:
        try:
            snapshot, snapshot_format = await load_snapshot(session, snapshot_id)
        except LookupError:
            raise HTTPException(
                status_code=404, detail=f"no config snapshot {snapshot_id}"
            ) from None

    candidate = without_migrated_sections(snapshot)
    is_delta = snapshot_format < STORE_FORMAT
    if is_delta:
        base = _read_file_document(request.app.state.config_path)
        if base is None:
            raise HTTPException(
                status_code=422,
                detail=[
                    _error(
                        "document",
                        "this snapshot is a delta from before the stored "
                        "document replaced the configuration file, and that "
                        "file is not readable, so there is nothing to merge "
                        "it over",
                    )
                ],
            )
        try:
            candidate = merge_overrides(base, candidate)
        except ValueError as exc:  # a `secrets` key anywhere in the snapshot
            raise HTTPException(
                status_code=422, detail=[_error("secrets", str(exc))]
            ) from exc

    document, after = await _validated_generation(
        request, candidate, check_empty_leaves=not is_delta
    )
    return await _persist_and_swap(
        request,
        document,
        after,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
        reason="restore",
    )


#: The export envelope's format discriminator. Bumped only when the shape of
#: `document` changes in a way an older reader would misread -- not when a
#: config field is added, which the document already absorbs by construction.
OVERRIDES_EXPORT_FORMAT = 1


class ConfigImportBody(BaseModel):
    """An exported overrides file, on its way back in.

    The same envelope ``GET /api/config/overrides/export`` writes, so the two
    are one format rather than two that agree by habit. ``autoposter_overrides``
    is required and is the point of the envelope: without a discriminator,
    somebody would eventually import a whole ``GET /api/config`` dump, which
    would freeze today's file values as permanent overrides -- the exact hazard
    ``documentFromConfig``'s docstring warns about, arriving through a button.

    ``exported_at`` is carried so a hand-inspected file round-trips unchanged;
    nothing reads it.

    ``document`` has no default, for the same reason ``OverridesBody.document``
    no longer does: a body carrying only ``autoposter_overrides`` and
    ``confirm`` used to bind ``document`` to its ``{}`` default, and with
    ``confirm`` truthy the drop refusal never ran -- a 200 that emptied the
    store with no ``document`` key in sight. Requiring the field costs no
    caller anything real and closes that hole for free.
    """

    model_config = ConfigDict(extra="forbid")

    autoposter_overrides: int
    document: dict
    exported_at: str | None = None
    expected_revision: str | None = None
    confirm: bool = False


@router.get("/config/overrides/export")
async def export_config_overrides(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """The stored overrides document, in a thin envelope, for safekeeping.

    ``document`` is deliberately the same key name and the same shape ``PUT
    /api/config/overrides`` takes, so an exported file's ``document`` value
    pastes straight into a PUT body and back.

    **Unredacted, deliberately, and this is a trade rather than an oversight.**
    ``GET /api/config`` reduces ``notifications.url`` to its host because that
    response is for a page; this response is a backup, and a backup that
    redacts is broken -- re-importing one would write the bare host over the
    real URL and destroy the push token it embeds. So the file holds the
    notification URL, the UI's download button says so in plain words, and the
    operator decides where the file goes. ``secrets`` is absent by
    construction: ``merge_overrides`` refuses the key outright, so there is no
    API token in it either way.

    (The alternative considered and rejected: exporting with the keep sentinel
    at every redacted path. That round-trips correctly on the *same*
    deployment and is useless as a transfer to a different one, which is most
    of what a backup is for.)
    """
    async with request.app.state.session_factory() as session:
        try:
            document = await load_overrides_document(session)
        except ValueError as exc:
            # A hand-edited config_overrides row whose document is not a JSON
            # object -- see load_overrides_document's docstring. An operator
            # needs to know what to fix, not a traceback.
            raise HTTPException(
                status_code=500,
                detail="config overrides row is corrupt (not a JSON object); fix or delete it",
            ) from exc
    return {
        "autoposter_overrides": OVERRIDES_EXPORT_FORMAT,
        "exported_at": datetime.now(UTC).isoformat(),
        "document": document,
    }


@router.post("/config/overrides/import")
async def import_config_overrides(
    body: ConfigImportBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Restore an exported overrides file. A save with a file picker in front.

    No import-only code path exists on purpose. The document goes through
    ``_validated_generation`` and ``_persist_and_swap`` exactly as a PUT's
    would: the same five refusal gates, the same pre-write snapshot, the same
    drop cap, the same revision check. An import is the highest-risk *drop* in
    this service -- one stale export can drop dozens of paths at once -- which
    is precisely the reason to route it through the guards rather than around
    them.

    Migrated sections are stripped first, for the reason
    ``restore_config_snapshot`` gives: a file exported before a section left the
    schema is exactly the file somebody reaches for a year later.
    """
    if body.autoposter_overrides != OVERRIDES_EXPORT_FORMAT:
        raise HTTPException(
            status_code=422,
            detail=[
                _error(
                    "autoposter_overrides",
                    f"unsupported export format {body.autoposter_overrides}; "
                    f"this service writes and reads {OVERRIDES_EXPORT_FORMAT}",
                )
            ],
        )

    document, after = await _validated_generation(
        request, without_migrated_sections(body.document)
    )
    return await _persist_and_swap(
        request,
        document,
        after,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
        reason="import",
    )
