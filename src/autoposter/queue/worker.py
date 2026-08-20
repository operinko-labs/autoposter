import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import Job
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound
from autoposter.queue.jobs import claim, complete, fail, release

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 2.0


async def run_once(session: AsyncSession, worker_id: str, handler) -> bool:
    """Claim and run at most one job. Returns False when nothing was due."""
    job = await claim(session, worker_id)
    if job is None:
        return False

    if job.kind != "process_item":
        # Not retryable — a rescheduled unknown kind would spin until it parks anyway.
        job.state = "parked"
        job.last_error = f"unknown job kind {job.kind!r}"
        await session.commit()
        return True

    try:
        intent = RenderIntent(**job.payload)
        await handler(session, intent)
    except asyncio.CancelledError:
        # Shutdown, not a job failure: hand it straight back so it's immediately
        # claimable again, without charging a retry attempt, then let the
        # cancellation continue propagating so the worker task actually stops.
        await release(session, job.id)
        raise
    except ItemNotFound as exc:
        # Expected right after an import: Plex has not scanned the new file yet.
        logger.info("job %s waiting for Plex: %s", job.id, exc)
        await fail(session, job.id, str(exc))
    except Exception as exc:  # noqa: BLE001 - the queue is the error boundary
        logger.warning("job %s failed: %s", job.id, exc, exc_info=True)
        await fail(session, job.id, f"{type(exc).__name__}: {exc}")
    else:
        await complete(session, job.id)
    return True


async def run_worker(worker_id: str, session_factory, handler, stop_event: asyncio.Event) -> None:
    """Claim jobs until stopped, sleeping briefly when the queue is empty."""
    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                did_work = await run_once(session, worker_id, handler)
        except Exception:
            logger.exception("worker %s loop error", worker_id)
            did_work = False
        if not did_work:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass


async def run_workers(count: int, session_factory, handler, stop_event: asyncio.Event) -> None:
    await asyncio.gather(
        *(
            run_worker(f"worker-{index}", session_factory, handler, stop_event)
            for index in range(count)
        )
    )
