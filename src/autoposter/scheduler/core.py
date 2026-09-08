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
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.actions.digest import actionable_window_counts
from autoposter.db.models import ScheduledRun
from autoposter.notify.dispatch import NullNotifier
from autoposter.scheduler.run_history import (
    UNRECORDED,
    close_drained_full_passes,
    close_run,
    open_run,
)

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
    concurrent copy. Most jobs here run on a 24-hour or 7-day cadence and take
    minutes at most, so the window does not exist in practice for them. The
    one exception is ``stale_job_reclaim`` (``scheduler/jobs.py``), on a
    5-minute cadence -- the window is still not a practical concern for it,
    not because the cadence is long but because its body is a single
    idempotent UPDATE that completes well within its own interval, and the
    UPDATE itself is now safe to double-run (see the outer WHERE guard on
    ``queue/jobs.py``'s reclaim statement). A lease (a heartbeat column plus a
    takeover-after-expiry rule) would be the fix if a job that is *not*
    idempotent, or that can run longer than its own cadence, is ever added,
    and is deliberately not built now.
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
        self, session_factory, jobs: list[Job], poll_seconds: float = 60, notifier=None,
        config_holder=None,
    ):
        self._session_factory = session_factory
        self._jobs = jobs
        self._poll_seconds = poll_seconds
        # A NullNotifier stand-in (never None) when the caller has no
        # notifier -- the app._build_mdblist precedent -- so _maybe_run
        # notifies unconditionally.
        self._notifier = notifier if notifier is not None else NullNotifier()
        # The config HOLDER, never a Config: roadmap row 236's digest knob is a
        # live setting, so the emitter must deref on every tick rather than
        # capture the generation the scheduler was built with -- the reason
        # scheduler/jobs.py's factories take a holder too. Optional, like the
        # notifier: a Scheduler built without one still does its bookkeeping
        # and simply never emits the digest, so no existing construction site
        # has to change to keep working.
        self._config_holder = config_holder
        # Strong references to in-flight notification tasks: asyncio holds
        # only a weak reference to a created task, so a fire-and-forget send
        # nothing else references could be garbage-collected mid-flight. The
        # done-callback drops each reference on completion.
        self._notify_tasks: set[asyncio.Task] = set()

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self._close_drained_runs()
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

    async def _close_drained_runs(self) -> None:
        """Close any full pass that has finished draining (roadmap row 53).

        Here rather than as a fifth registered Job, for three reasons. A
        registered job would need a name in SCHEDULED_JOB_NAMES, a cadence
        setting and a dashboard row, to run one SELECT; it would write a
        `runs` row of its own on every pass, which is exactly the history
        noise the retention clause exists to bound; and it would be gated by
        `scheduler.enabled`, whereas this loop runs unconditionally (app.py
        registers stale_job_reclaim outside that gate) -- so a deployment with
        the maintenance passes off would otherwise leave every full pass
        `running` forever.

        The cadence is therefore `poll_seconds` (60 by default), which is
        ample: a full pass takes hours, and a minute of latency on its
        recorded end is a minute on a duration measured in hours.

        Contained exactly like `_maybe_run`: bookkeeping must never take the
        scheduler down.
        """
        windows: list[tuple[datetime, datetime]] = []
        try:
            async with self._session_factory() as session:
                closed = await close_drained_full_passes(
                    session, closed_windows=windows
                )
                await session.commit()
        except Exception:
            logger.warning("scheduler: could not close drained full passes", exc_info=True)
            return
        if closed:
            logger.info("scheduler: closed %d drained full pass(es)", closed)
            await self._notify_newly_actionable(windows)

    async def _notify_newly_actionable(
        self, windows: list[tuple[datetime, datetime]]
    ) -> None:
        """Roadmap row 236's digest: what each pass that just closed made
        actionable, as registry flag codes and integer counts.

        Here rather than in ``run_history`` because a flag predicate needs a
        ``Config`` and that module is deliberately config-free, and because
        this object already holds the notifier and the fire-and-forget seam.

        **One POST per closed pass**, never per asset and never per flag. Per
        asset was refused for the reason row 19 refused a POST per changed
        collection: one measured pass produced 1828 actionable rows, against a
        webhook budget of roughly 5 requests / 2 seconds (notify/dispatch.py).
        Per flag would be up to thirteen POSTs carrying what one embed's fields
        carry for free.

        **Nothing is sent when the pass produced nothing actionable** -- not an
        empty digest, not a "nothing new" POST. The gate is the DEFAULT
        population, not "any flag non-zero": ``skipped``,
        ``unknown_provenance`` and ``unscored`` are ``default_on=False`` as
        deliberate flood-avoidance, and a digest that fired because a disabled
        art kind produced three thousand ``skipped`` rows would be exactly that
        flood.

        There is no "emit on increase" comparison because there is nothing to
        compare against: every count is taken over ONE pass's own window, never
        as a running total, so a prune that deletes rows can only shrink a
        later window and can never announce a deletion as news. That is what
        this design buys over a stored high-water mark.

        Read-only -- no commit -- and contained exactly like the bookkeeping
        above: a counting query that fails must not take the scheduler down,
        and must not un-close a pass that is already closed and committed.
        """
        if self._config_holder is None:
            return
        # Deref per tick, not per process: an operator who turns the digest on
        # in the settings editor gets it on the next pass that closes.
        config = self._config_holder.current
        if not config.actionable_digest_enabled:
            return
        try:
            async with self._session_factory() as session:
                for started_at, finished_at in windows:
                    total, counts = await actionable_window_counts(
                        session, config, started_at, finished_at
                    )
                    if not total:
                        continue
                    # Fire-and-forget for the completion send's reason: one
                    # send's worst case is tens of seconds against a target
                    # answering 429, and this runs on the poll loop every job
                    # in this process waits behind.
                    self._start_notification(
                        "newly_actionable",
                        f"{total} newly actionable after a full pass",
                        counts,
                    )
        except Exception:
            logger.warning(
                "scheduler: could not count newly actionable renders", exc_info=True
            )

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

        # Roadmap row 53's history row, opened at the boundary that already
        # exists rather than at a new one: the claim above is committed, so
        # this row describes a run the database already shows in flight. Its
        # own transaction, and contained the same way the claim is -- run
        # history is bookkeeping, and failing to record it must never stop the
        # pass it would have recorded. `run_id` stays None in that case and
        # the completion write below simply has nothing to close.
        #
        # `UNRECORDED` is consulted here, before `open_run` is even called
        # (fix round 1, Critical): `stale_job_reclaim` is registered
        # unconditionally and runs every five minutes regardless of
        # `scheduler.enabled`, while the retention trim only ever runs from
        # inside the (conditionally-registered) cleanup pass -- recording
        # this job's passes would grow the table forever with nothing ever
        # trimming it. `run_id` stays None for an unrecorded name, which
        # already skips the `close_run` call below.
        run_id: int | None = None
        if job.name not in UNRECORDED:
            try:
                async with self._session_factory() as session:
                    run_id = await open_run(session, kind="scheduled", name=job.name)
                    await session.commit()
            except Exception:
                logger.warning(
                    "scheduler: could not open a run row for %s", job.name, exc_info=True
                )

        # Roadmap row 19's run_start, from the same after-the-commit position
        # the completion send uses: the claim above is committed, so the
        # scheduled_runs row already shows this run in flight and the payload
        # never describes a run the database does not. Fire-and-forget for the
        # completion send's reason -- an awaited send's worst case is ~31.5s
        # on the default retry config against an ordinary target, up to ~50s
        # against one answering 429 with a Retry-After clamped to the timeout
        # (notify/dispatch.py), and this loop runs every job sequentially, so
        # awaiting it here would delay the work the notification is
        # announcing.
        self._start_notification(
            "scheduled_run_started",
            f"scheduled run {job.name} started",
            {"job": job.name},
        )

        status, detail = "ok", ""
        try:
            async with self._session_factory() as session:
                detail = await job.run(session) or ""
                await session.commit()
        except Exception as error:
            status = "failed"
            # Class name only on the served copy (roadmap row 209): this
            # string is written to scheduled_runs.last_detail (served by
            # /api/snapshots, rendered by the dashboard) and carried in the
            # notification payload, and an arbitrary failure's str() commonly
            # embeds the URL it failed on -- the operator's base URL, in some
            # shapes a token. The full message and traceback stay on the
            # warning below: the pod log, the trusted sink. The exception is
            # an exception that BUILT its message for the served surfaces --
            # CollectionsPassFailed, PruneRefused, and plex.client's
            # ItemNotFound family (row 213 widened the contract to "reviewed
            # safe for a served surface") -- and says so with
            # ``served_detail = True``; those messages are redaction-reviewed
            # at their construction sites and pinned by their own tests.
            detail = (
                str(error)
                if getattr(error, "served_detail", False)
                else type(error).__name__
            )
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
                if run_id is not None:
                    # The same status and the same already-narrowed detail the
                    # row above records (row 213): copied, never re-derived.
                    # In the same transaction, so the two tables can never
                    # disagree about how this run ended.
                    await close_run(session, run_id, status=status, detail=detail)
                await session.commit()
        except Exception:
            logger.warning("scheduler: could not record %s result", job.name, exc_info=True)
            return
        # After the commit above, never before: the notification must not
        # describe a run the database does not yet show. And on a task of its
        # own, not awaited: one send's worst case is ~31.5s on the default
        # retry config against an ordinary target, up to ~50s against one
        # answering 429 with a Retry-After clamped to the timeout (see
        # notify/dispatch.py), while this loop runs every job sequentially --
        # an awaited send would stall every job behind it and the poll
        # cadence. The truncated detail matches what the row
        # recorded. send's boolean is deliberately ignored: the Notifier does
        # its own outcome logging, and a disabled notifier's vacuous True
        # must not be reported as a delivery.
        recorded = detail[:2000]
        self._start_notification(
            "scheduled_run_completed",
            f"scheduled run {job.name} finished: {status}",
            {"job": job.name, "status": status, "detail": recorded},
        )
        if status == "failed":
            # Row 19's `error` event, and its ONE call site: this is the only
            # boundary that knows a scheduled run failed, so emitting it here
            # rather than per job body keeps one event with one shape. Additive
            # on purpose -- scheduled_run_completed still fires for every run,
            # because dropping or renaming a shipped event would break the n8n
            # flow row 18 exists to keep alive. `recorded` is the same
            # class-name-only string the row holds (see the handler above).
            self._start_notification(
                "scheduled_run_failed",
                f"scheduled run {job.name} failed: {recorded}",
                {"job": job.name, "status": "failed", "detail": recorded},
            )

    def _start_notification(self, event: str, summary: str, detail: dict) -> None:
        task = asyncio.create_task(self._notifier.send(event, summary, detail))
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
