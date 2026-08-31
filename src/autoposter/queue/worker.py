import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping

import requests
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import WorkerPause
from autoposter.db.models import Job
from autoposter.plex.client import ItemNotFound, PlexPathMismatch
from autoposter.queue.jobs import (
    DEFER_INTERVAL_SECONDS,
    MAX_ATTEMPTS,
    claim,
    complete,
    fail,
    release,
)

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 2.0

# One dispatch entry per job kind. The handler owns decoding its own payload
# (a process_item job's is a RenderIntent; a mode job's is its filters), so the
# map values share one signature regardless of kind. Wired in app.py, where the
# per-process dependencies each handler closes over are constructed.
JobHandler = Callable[[AsyncSession, Job], Awaitable[None]]


async def run_once(
    session: AsyncSession,
    worker_id: str,
    handlers: Mapping[str, JobHandler],
    pause: WorkerPause | None = None,
) -> bool:
    """Claim and run at most one job. Returns False when nothing was due.

    ``pause``, when given, is the Plex-writing modes' fence. It is consulted
    once more *here*, after the claim, and the job is counted as active while
    its handler runs -- see ``WorkerPause``'s docstring for why the gate in
    ``run_worker`` alone is not enough.
    """
    job = await claim(session, worker_id)
    if job is None:
        return False

    # Captured up front: a rollback() below expires every attribute on this ORM
    # object, and re-loading one afterwards needs IO that isn't safe to trigger
    # via plain attribute access on an AsyncSession.
    job_id = job.id

    if pause is not None and pause.is_paused:
        # The fence rose between run_worker's gate and this claim, which are
        # several awaits apart. Hand the job straight back -- pending, and with
        # the attempt claim() just charged given back -- rather than running it
        # in full behind a mode that has already drained and started writing.
        await release(session, job_id)
        return False

    handler = handlers.get(job.kind)
    if handler is None:
        # Not retryable — a rescheduled unknown kind would spin until it parks anyway.
        job.state = "parked"
        job.last_error = f"unknown job kind {job.kind!r}"
        await session.commit()
        return True

    # Counted as active for the whole of the job, bookkeeping included, so a
    # mode's drain waits for the job to be *finished*, not merely for its
    # handler to have returned.
    tracker = pause.running_job() if pause is not None else contextlib.nullcontext()
    with tracker:
        try:
            await handler(session, job)
        except asyncio.CancelledError:
            # Shutdown, not a job failure: hand it straight back so it's immediately
            # claimable again, without charging a retry attempt, then let the
            # cancellation continue propagating so the worker task actually stops.
            # Rolled back first for the same reason as the branches below: a
            # cancellation landing mid-database-operation can leave the session in
            # a failed transaction, and release()'s SELECT would raise
            # PendingRollbackError instead of releasing the job.
            await session.rollback()
            await release(session, job_id)
            raise
        except PlexPathMismatch as exc:
            # A subclass of ItemNotFound, so it must be caught here, ahead of
            # the ItemNotFound clause below: Python matches except clauses
            # top to bottom by isinstance, so a broader clause listed first
            # would take it, and a bare re-raise from inside that clause exits
            # the whole try/except rather than falling through to a sibling
            # clause -- catch order is what keeps this from being swallowed,
            # not a condition inside the ItemNotFound branch.
            #
            # This one is not a wait: the item resolved in Plex, but its file
            # path does not map under any of the library's roots, which is a
            # path-mapping misconfiguration that no amount of deferring fixes.
            # It needs a human, so it parks and reaches Failures exactly like
            # the generic failure below, instead of deferring forever.
            logger.warning("job %s failed: %s", job_id, exc, exc_info=True)
            await session.rollback()
            await fail(session, job_id, f"{type(exc).__name__}: {exc}")
        except ItemNotFound as exc:
            # Plex has not indexed this item yet. Nothing is wrong with the job
            # and no budget can be the right one: a movie added to Radarr before
            # its release is weeks from resolving, and every finite cap turns
            # that wait into a permanent parked "failure". So it is deferred on
            # a long, unbounded horizon instead -- see fail()'s docstring.
            logger.info("job %s deferred, waiting for Plex: %s", job_id, exc)
            # The handler may have left the session mid-transaction (e.g. a DB error
            # surfaced first); fail() issues a SELECT, which would raise
            # PendingRollbackError on a failed transaction instead of rescheduling.
            await session.rollback()
            # Class-prefixed, message KEPT (roadmap row 209): this string is
            # served at api/jobs.py:147 as waiting_reason, and both
            # ItemNotFound raise sites (plex/client.py:635, :645) interpolate
            # only the job's own item fields -- title, ids, rating key --
            # none of which is a host, URL, token or file path. So
            # the reason stays a reason (the PlexPathMismatch branch's shape)
            # rather than a bare class name. PlexPathMismatch itself, whose
            # message DOES carry file paths, is caught by its own clause
            # above and never reaches this one.
            await fail(
                session,
                job_id,
                f"{type(exc).__name__}: {exc}",
                defer_seconds=DEFER_INTERVAL_SECONDS,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            # Plex itself is unreachable (a connection/timeout error surfacing
            # from _LazyPlexServer's connect attempt, see main.py). Unlike the
            # branch above this one IS bounded, and deliberately: an outage ends
            # in hours, so a job that cannot get through for the whole of the
            # larger budget has a problem a human should see.
            logger.info("job %s waiting for Plex: %s", job_id, exc)
            await session.rollback()
            # config.plex.resolve_max_attempts, threaded through via the exception
            # rather than a run_once parameter — see _handle_intent in app.py.
            max_attempts = getattr(exc, "max_attempts", MAX_ATTEMPTS)
            # Class name only (roadmap row 209): requests' ConnectionError and
            # Timeout str() embed the Plex host and port, and this string is
            # served at api/jobs.py:146 as last_error. The full message is on
            # the INFO line above -- the pod log, the trusted sink.
            await fail(session, job_id, type(exc).__name__, max_attempts)
        except Exception as exc:  # noqa: BLE001 - the queue is the error boundary
            logger.warning("job %s failed: %s", job_id, exc, exc_info=True)
            await session.rollback()
            await fail(session, job_id, f"{type(exc).__name__}: {exc}")
        else:
            await complete(session, job_id)
    return True


async def run_worker(
    worker_id: str,
    session_factory,
    handlers: Mapping[str, JobHandler],
    stop_event: asyncio.Event,
    is_healthy: Callable[[], bool] | None = None,
    pause: WorkerPause | None = None,
) -> None:
    """Claim jobs until stopped, sleeping briefly when the queue is empty.

    ``handlers`` maps ``job.kind`` to the coroutine that runs it; an unknown
    kind parks (see ``run_once``).

    ``pause``, when given, is the in-process fence a Plex-writing mode raises so
    it does not race the live pipeline: while it is paused the worker idles and
    re-checks instead of claiming, exactly as the health gate does, so a worker
    already mid-job finishes it and *then* idles -- in-flight work is never
    interrupted. Checked before ``is_healthy`` so a paused pool does not even
    probe Plex. Passed on to ``run_once``, which re-checks it after the claim
    (this gate and that claim are several awaits apart) and counts the job as
    active while it runs, so the mode can drain on it.

    ``is_healthy``, when given, gates claiming: while it returns False the
    worker sleeps and re-checks instead of claiming, so jobs stay ``pending``
    (no attempts burned) during a Plex outage rather than being claimed and
    immediately failed. ``None`` means claim unconditionally, matching the
    previous behaviour for existing callers.

    Every job kind in this phase needs Plex, so gating *all* claiming here is
    correct; revisit this once a job kind that doesn't need Plex exists.
    """
    while not stop_event.is_set():
        if pause is not None and pause.is_paused:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue
        if is_healthy is not None and not is_healthy():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            async with session_factory() as session:
                did_work = await run_once(session, worker_id, handlers, pause)
        except Exception:
            logger.exception("worker %s loop error", worker_id)
            did_work = False
        if not did_work:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass


async def run_workers(
    count: int,
    session_factory,
    handlers: Mapping[str, JobHandler],
    stop_event: asyncio.Event,
    is_healthy: Callable[[], bool] | None = None,
    pause: WorkerPause | None = None,
) -> None:
    await asyncio.gather(
        *(
            run_worker(
                f"worker-{index}", session_factory, handlers, stop_event, is_healthy, pause
            )
            for index in range(count)
        )
    )
