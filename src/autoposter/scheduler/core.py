"""Periodic job scheduling.

The same shape as the existing background tasks: one ``run(stop_event)``
coroutine the app lifespan starts and cancels. What makes this more than a
sleep loop is that the schedule lives in the database -- restarts do not
re-run everything, and two replicas coordinate through ``FOR UPDATE SKIP
LOCKED`` rather than both firing the same pass.

The scheduler decides only *when*. Job bodies are ordinary async functions
that take a session and return a short summary for the log.

Note: ``ImdbAutoRefresh`` (see ``facts/imdb.py``) deliberately does not run
on this scheduler. Its trigger is dataset staleness, not a fixed interval,
and it also has a separate miss-triggered path with its own cooldown --
folding it in would mean losing that or bending this scheduler around one
job, so it keeps its own loop.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ScheduledRun

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    name: str
    interval_seconds: float
    run: Callable[[AsyncSession], Awaitable[str]]


async def claim_due(session: AsyncSession, job: Job) -> bool:
    """Claim ``job`` if it is due, atomically.

    A single ``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)
    RETURNING id``: the subquery only considers a row that is both due and
    not currently locked by another replica's in-flight claim, so a row
    another replica holds is correctly treated as "not ours" rather than
    blocking until it is released. The due check itself runs entirely in
    SQL against the database clock, never ``datetime.now()``.

    Known limitation: the claim is not a lease. ``last_started_at`` is
    stamped and committed immediately, and the row lock is released with that
    commit -- so a job whose *runtime* exceeds its own interval becomes due
    again while it is still running, and a second replica can start a
    concurrent copy. Every job here runs on a 24-hour or 7-day cadence and
    takes minutes at most, so the window does not exist in practice; a lease
    (a heartbeat column plus a takeover-after-expiry rule) would be the fix
    if a sub-hour cadence is ever added, and is deliberately not built now.
    """
    await session.execute(
        insert(ScheduledRun).values(name=job.name).on_conflict_do_nothing(
            index_elements=["name"]
        )
    )
    result = await session.execute(
        text(
            """
            UPDATE scheduled_runs
            SET last_started_at = now()
            WHERE id = (
                SELECT id FROM scheduled_runs
                WHERE name = :name
                  AND (
                    last_started_at IS NULL
                    OR last_started_at < now() - make_interval(secs => :seconds)
                  )
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id
            """
        ),
        {"name": job.name, "seconds": float(job.interval_seconds)},
    )
    return result.first() is not None


class Scheduler:
    """Runs jobs on their intervals until the stop event is set."""

    def __init__(self, session_factory, jobs: list[Job], poll_seconds: float = 60):
        self._session_factory = session_factory
        self._jobs = jobs
        self._poll_seconds = poll_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            for job in self._jobs:
                if stop_event.is_set():
                    break
                await self._maybe_run(job)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                continue
            else:
                return

    async def _maybe_run(self, job: Job) -> None:
        """Run one job if due. Never raises -- a failure is recorded and the
        scheduler waits for the next interval, the same containment
        ``ImdbAutoRefresh._maybe_refresh`` uses."""
        try:
            async with self._session_factory() as session:
                claimed = await claim_due(session, job)
                await session.commit()
        except Exception:
            logger.warning("scheduler: could not claim %s", job.name, exc_info=True)
            return
        if not claimed:
            return

        status, detail = "ok", ""
        try:
            async with self._session_factory() as session:
                detail = await job.run(session) or ""
                await session.commit()
        except Exception as error:
            status, detail = "failed", str(error)
            logger.warning("scheduler: %s failed", job.name, exc_info=True)

        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(ScheduledRun).where(ScheduledRun.name == job.name)
                    )
                ).scalar_one()
                row.last_finished_at = func.now()
                row.last_status = status
                row.last_detail = detail[:2000]
                await session.commit()
        except Exception:
            logger.warning("scheduler: could not record %s result", job.name, exc_info=True)
