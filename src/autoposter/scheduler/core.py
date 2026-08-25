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
from autoposter.notify.dispatch import NullNotifier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    """One periodic pass and how often it should run.

    ``interval_seconds`` is either a number or a zero-argument callable
    returning one. The callable form is what makes a cadence edit live: the
    factories in ``scheduler/jobs.py`` build it as a deref of the config
    holder, so ``current_interval`` below reads the *current* generation on
    every poll rather than the one that was current when the job was built.

    A union field plus this one resolver was chosen over giving ``Job`` a
    holder reference and a ``current_interval`` method that reads a named
    config path: the holder form would push config knowledge into the
    scheduler core (which today knows nothing about ``Config``), would need a
    holder even for the jobs tests construct by hand, and could not express a
    cadence derived from a field -- ``collections_hours * 3600`` -- without a
    second callable anyway. The callable keeps every existing ``Job(...)``
    construction site and every test working unchanged.
    """

    name: str
    interval_seconds: float | Callable[[], float]
    run: Callable[[AsyncSession], Awaitable[str]]

    def current_interval(self) -> float:
        """This job's cadence right now, in seconds."""
        value = self.interval_seconds
        return float(value() if callable(value) else value)


async def claim_due(session: AsyncSession, job: Job) -> bool:
    """Claim ``job`` if it is due, atomically.

    A single ``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)
    RETURNING id``: the subquery only considers a row that is both due and
    not currently locked by another replica's in-flight claim, so a row
    another replica holds is correctly treated as "not ours" rather than
    blocking until it is released. The due check itself runs entirely in
    SQL against the database clock, never ``datetime.now()``. The cadence is
    resolved here, per poll, rather than read off a value captured when the
    job was built -- that is what makes a cadence edit take effect without a
    restart (see ``Job``).

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
        {"name": job.name, "seconds": job.current_interval()},
    )
    return result.first() is not None


class Scheduler:
    """Runs jobs on their intervals until the stop event is set."""

    def __init__(
        self, session_factory, jobs: list[Job], poll_seconds: float = 60, notifier=None
    ):
        self._session_factory = session_factory
        self._jobs = jobs
        self._poll_seconds = poll_seconds
        # A NullNotifier stand-in (never None) when the caller has no
        # notifier -- the app._build_mdblist precedent -- so _maybe_run
        # notifies unconditionally.
        self._notifier = notifier if notifier is not None else NullNotifier()
        # Strong references to in-flight notification tasks: asyncio holds
        # only a weak reference to a created task, so a fire-and-forget send
        # nothing else references could be garbage-collected mid-flight. The
        # done-callback drops each reference on completion.
        self._notify_tasks: set[asyncio.Task] = set()

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
        # Nothing else logs between the claim and the finish -- a multi-minute
        # job (the prune walk, in production) was otherwise silent for its
        # whole run, indistinguishable from a scheduler that never woke up.
        logger.info("scheduler: %s started", job.name)

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
            return
        # After the commit above, never before: the notification must not
        # describe a run the database does not yet show. And on a task of its
        # own, not awaited: one send's worst case is ~31.5s on the default
        # retry config (see notify/dispatch.py), while this loop runs every
        # job sequentially -- an awaited send would stall every job behind it
        # and the poll cadence. The truncated detail matches what the row
        # recorded. send's boolean is deliberately ignored: the Notifier does
        # its own outcome logging, and a disabled notifier's vacuous True
        # must not be reported as a delivery.
        self._start_notification(job.name, status, detail[:2000])

    def _start_notification(self, name: str, status: str, detail: str) -> None:
        task = asyncio.create_task(
            self._notifier.send(
                "scheduled_run_completed",
                f"scheduled run {name} finished: {status}",
                {"job": name, "status": status, "detail": detail},
            )
        )
        self._notify_tasks.add(task)
        task.add_done_callback(self._notification_done)

    def _notification_done(self, task: asyncio.Task) -> None:
        self._notify_tasks.discard(task)
        # Notifier.send never raises by contract, but an exception a task
        # holds unretrieved becomes a GC-time warning; retrieve and log it
        # here so a misbehaving notifier is named, not leaked.
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "notification task failed", exc_info=task.exception()
            )
