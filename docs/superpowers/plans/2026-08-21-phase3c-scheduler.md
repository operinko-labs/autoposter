# Phase 3c: Scheduler and Periodic Passes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the collections reconcile, the ratings drift sweep and the asset cleanup on their own cadences, coordinated across replicas, without blocking the event loop.

**Architecture:** A small scheduler in the same shape as the existing `PlexHealth` and `ImdbAutoRefresh` background tasks — one `run(stop_event)` coroutine started by the app lifespan. Each job's last-run time lives in the database rather than in process memory, and a job is claimed with `FOR UPDATE SKIP LOCKED` so two replicas never run the same pass at once. Job bodies are ordinary async functions; the scheduler only decides *when*.

**Tech Stack:** Python 3.13, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18, Alembic.

## Global Constraints

- **All database timestamps come from `func.now()`**, never `datetime.now()`. Host and container clocks differ by about 10 seconds here.
- **Nothing blocks the event loop.** The worker pool and the Plex liveness probe share it; a stalled loop can get the pod killed. Filesystem walks and Plex calls go through `asyncio.to_thread`.
- **A failing job must never kill the scheduler.** Log it and wait for the next interval, exactly as `ImdbAutoRefresh._maybe_refresh` does.
- **The asset cleanup moves files. It defaults to a dry run** and never deletes — files move to `backup_root`, matching the behaviour of the tool being replaced.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it; `.reload()` is fine.
- **Only collections carrying the ownership label are ever modified, and no collection is ever deleted** — Phase 3a and 3b's rules still apply through the collections job.
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- **No test may make a real outbound request.** An autouse fixture in `tests/conftest.py` enforces this.
- Baseline on branch start: 697 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.

**Why the IMDb dataset refresh keeps its own loop.** `ImdbAutoRefresh` is deliberately *not* migrated onto this scheduler. Its trigger is not a fixed interval but the staleness of the remote dataset, and it additionally runs on a miss-triggered path with its own cooldown. Folding it in would mean either losing that or bending the scheduler around one job. Leave it alone; note the reason where the scheduler is registered.

---

## A note on how specified each task is

Task 1 gives complete test code and a complete implementation sketch. **Tasks 2, 3 and 4
instead enumerate the test *cases* and leave you to write the code**, because their fakes
depend on the shape of modules from earlier phases that are easier to read than to restate.

Treat the enumerated cases as the contract: every bullet in a "Step 1" list is a test that
must exist and must genuinely be able to fail. Follow the style of the existing test files
named in each task. If a case turns out not to make sense against the real interfaces, say
so in your report rather than quietly dropping it.

Task 1's `claim_due` sketch is deliberately marked as unreliable — the note under it tells
you to implement the due-check properly in SQL. The tests define the contract there, not the
sketch.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/autoposter/scheduler/core.py` | Interval bookkeeping, claiming, and the run loop. Knows nothing about what jobs do. |
| `src/autoposter/scheduler/jobs.py` | The three job bodies. |
| `src/autoposter/db/models.py` | New `ScheduledRun` table. |
| `src/autoposter/config/schema.py` | The `scheduler` config section. |
| `src/autoposter/app.py` | Starts the scheduler alongside the existing background tasks. |

---

## Task 1: Scheduler core

**Files:**
- Create: `src/autoposter/scheduler/__init__.py` (empty)
- Create: `src/autoposter/scheduler/core.py`
- Modify: `src/autoposter/db/models.py`
- Create: one Alembic migration
- Test: `tests/test_scheduler_core.py`

**Interfaces:**
- Produces: `ScheduledRun` model with `id`, `name` (`String(64)`, unique), `last_started_at`, `last_finished_at`, `last_status` (`String(16)`), `last_detail` (`Text`), `created_at`, `updated_at`; the frozen dataclass `Job(name: str, interval_seconds: float, run: Callable[[AsyncSession], Awaitable[str]])`; `async claim_due(session, job, now_interval_seconds) -> bool`; `class Scheduler` with `__init__(session_factory, jobs: list[Job], poll_seconds: float = 60)` and `async run(stop_event)`.

**Notes for the implementer:**

- `claim_due` does the whole decision in one statement so two replicas cannot both win: `SELECT ... FROM scheduled_runs WHERE name = :name FOR UPDATE SKIP LOCKED`, then check whether `last_started_at` is null or older than the interval, and if so set `last_started_at = func.now()` and return `True`. A row that another replica holds is skipped, which correctly means "someone else is on it".
- Insert the row on first sight (`ON CONFLICT DO NOTHING`), so a new job needs no migration or seeding.
- The run loop wakes every `poll_seconds`, tries to claim each job, and runs the ones it claimed. Polling rather than sleeping until the next due time keeps it simple and makes a clock step harmless.
- Wait on `stop_event` with `asyncio.wait_for(..., timeout=poll_seconds)` so shutdown is immediate rather than up to a poll interval late — the same idiom `ImdbAutoRefresh.run` uses.
- The job's return value is a short human-readable summary stored in `last_detail`. On failure, record `last_status="failed"` and the exception text, then carry on.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler_core.py`:

```python
"""Scheduler bookkeeping and claiming."""
import asyncio

import pytest
from sqlalchemy import select, text

from autoposter.db.models import ScheduledRun
from autoposter.scheduler.core import Job, Scheduler, claim_due


def _job(name="demo", interval=3600, run=None):
    async def _noop(session):
        return "ok"

    return Job(name=name, interval_seconds=interval, run=run or _noop)


async def test_a_job_is_claimable_the_first_time(session):
    assert await claim_due(session, _job()) is True


async def test_a_job_is_not_claimable_again_inside_its_interval(session):
    job = _job(interval=3600)
    assert await claim_due(session, job) is True
    assert await claim_due(session, job) is False


async def test_a_job_is_claimable_once_its_interval_has_passed(session):
    job = _job(interval=3600)
    assert await claim_due(session, job) is True
    await session.execute(
        text("UPDATE scheduled_runs SET last_started_at = now() - interval '2 hours'")
    )
    assert await claim_due(session, job) is True


async def test_claiming_records_the_start_time_from_the_database_clock(session):
    await claim_due(session, _job())
    row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_started_at is not None


async def test_two_jobs_are_tracked_independently(session):
    assert await claim_due(session, _job(name="a")) is True
    assert await claim_due(session, _job(name="b")) is True
    rows = (await session.execute(select(ScheduledRun))).scalars().all()
    assert {r.name for r in rows} == {"a", "b"}


async def test_the_scheduler_runs_a_due_job_and_records_success(session_factory):
    ran = []

    async def body(session):
        ran.append(True)
        return "did the thing"

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(run=body)], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await task

    assert ran
    async with session_factory() as session:
        row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_status == "ok"
    assert row.last_detail == "did the thing"
    assert row.last_finished_at is not None


async def test_a_failing_job_is_recorded_and_the_scheduler_survives(session_factory):
    calls = []

    async def body(session):
        calls.append(True)
        raise RuntimeError("job exploded")

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(interval=0, run=body)], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await task

    assert len(calls) > 1, "scheduler stopped after the first failure"
    async with session_factory() as session:
        row = (await session.execute(select(ScheduledRun))).scalar_one()
    assert row.last_status == "failed"
    assert "job exploded" in row.last_detail


async def test_the_scheduler_stops_promptly_on_the_stop_event(session_factory):
    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job()], poll_seconds=30)
    task = asyncio.create_task(scheduler.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_an_empty_job_list_is_harmless(session_factory):
    stop = asyncio.Event()
    task = asyncio.create_task(Scheduler(session_factory, [], poll_seconds=0.01).run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
```

`session_factory` is an existing fixture in `tests/conftest.py`; if it is not exposed under that name, add a thin one alongside the current session fixture rather than changing the existing one.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_scheduler_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.scheduler'`

- [ ] **Step 3: Add the model**

In `src/autoposter/db/models.py`:

```python
class ScheduledRun(Base):
    """When each periodic job last ran.

    In the database rather than in process memory so that restarts do not
    re-run everything, and so two replicas coordinate rather than both firing
    the same pass.
    """

    __tablename__ = "scheduled_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ok | failed
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Write the scheduler**

Create `src/autoposter/scheduler/__init__.py` empty and `src/autoposter/scheduler/core.py`:

```python
"""Periodic job scheduling.

The same shape as the existing background tasks: one ``run(stop_event)``
coroutine the app lifespan starts and cancels. What makes this more than a
sleep loop is that the schedule lives in the database -- restarts do not
re-run everything, and two replicas coordinate through ``FOR UPDATE SKIP
LOCKED`` rather than both firing the same pass.

The scheduler decides only *when*. Job bodies are ordinary async functions
that take a session and return a short summary for the log.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from autoposter.db.models import ScheduledRun

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    name: str
    interval_seconds: float
    run: Callable[[AsyncSession], Awaitable[str]]


async def claim_due(session: AsyncSession, job: Job) -> bool:
    """Claim ``job`` if it is due, atomically.

    ``SKIP LOCKED`` means a row another replica is holding is treated as "not
    ours", which is exactly right: someone else is already running it.
    """
    await session.execute(
        insert(ScheduledRun).values(name=job.name).on_conflict_do_nothing(
            index_elements=["name"]
        )
    )
    row = (
        await session.execute(
            select(ScheduledRun)
            .where(ScheduledRun.name == job.name)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return False

    due = (
        await session.execute(
            select(
                (row.last_started_at is None)
                if row.last_started_at is None
                else func.now() - row.last_started_at
                >= func.make_interval(secs=job.interval_seconds)
            )
        )
    ).scalar_one()
    if not due:
        return False

    row.last_started_at = func.now()
    await session.flush()
    return True


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
        """Run one job if due. Never raises -- a failure waits for the next
        interval, the same containment the other background tasks use."""
        try:
            async with self._session_factory() as session:
                if not await claim_due(session, job):
                    await session.commit()
                    return
                await session.commit()
        except Exception:
            logger.warning("scheduler: could not claim %s", job.name, exc_info=True)
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
```

**Note on `claim_due`'s due check:** the expression shown above is awkward. Implement the comparison in SQL properly — a single `UPDATE scheduled_runs SET last_started_at = now() WHERE name = :name AND (last_started_at IS NULL OR last_started_at < now() - make_interval(secs => :seconds)) RETURNING id` combined with the `FOR UPDATE SKIP LOCKED` select is clearer and genuinely atomic. Use whichever form you can prove correct with the tests above; the tests define the contract, not the sketch.

- [ ] **Step 5: Generate the migration against a clean database**

```bash
docker compose down && docker compose up -d postgres && sleep 8
export AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
rtk proxy python -m alembic upgrade head
rtk proxy python -m alembic revision --autogenerate -m "scheduled runs"
```

`tests/conftest.py` calls `create_all` against the same database, so autogenerating after a test run yields an empty `pass` body that silently creates nothing on deploy. Confirm `upgrade()` contains a real `op.create_table('scheduled_runs', ...)`; a body of `pass` means start over from the reset. `docker compose down` **without** `-v`.

- [ ] **Step 6: Apply, test and commit**

```bash
rtk proxy python -m alembic upgrade head
rtk proxy python -m pytest tests/test_scheduler_core.py tests/test_migrations.py -v
rtk proxy ruff check src tests
git add src/autoposter/scheduler src/autoposter/db/models.py alembic tests/test_scheduler_core.py
git commit --no-gpg-sign -m "Add a database-coordinated periodic scheduler"
```

---

## Task 2: Collections pass

**Files:**
- Create: `src/autoposter/scheduler/jobs.py`
- Test: `tests/test_scheduler_collections_job.py`

**Interfaces:**
- Consumes: `reconcile_content_ratings`, `build_all` from the collections package; `Job` from `scheduler.core`.
- Produces: `make_collections_job(config, server_factory, http) -> Job`.

**Notes for the implementer:**

- Reuse the logic already in `src/autoposter/collections/__main__.py` rather than duplicating it. If that means extracting the per-library loop from `__main__` into a reusable function that both the CLI and this job call, do that — two copies of the reconciliation sequence would drift.
- `server_factory` is a zero-argument callable returning a connected `PlexServer`. Connecting is a blocking call, so it goes through `asyncio.to_thread`.
- The job returns a summary like `"Movies: 3 action(s); TV Shows: 0 action(s)"`.
- `apply_to_plex` still gates writes. A scheduled pass with it false is a periodic dry run, which is a perfectly reasonable way to watch what it *would* do before switching it on.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler_collections_job.py` covering: the job is skipped entirely when `collections.enabled` is false; a successful pass returns a summary naming each library; a failure in one library does not prevent the other; and the Plex connection happens off the event loop (assert the connecting call runs on a different thread).

Write the tests before the implementation and make them concrete — no placeholders. Use fakes in the style of `tests/test_collection_reconcile.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_scheduler_collections_job.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.scheduler.jobs'`

- [ ] **Step 3: Extract the shared reconcile loop and write the job**

Move the per-library reconciliation body out of `src/autoposter/collections/__main__.py` into a function in the collections package (for example `reconcile_libraries(session, server, config, http)`), have `__main__` call it, and have `make_collections_job` build a `Job` whose `run` calls the same function.

- [ ] **Step 4: Run tests, ruff and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter tests/test_scheduler_collections_job.py
git commit --no-gpg-sign -m "Run the collections reconcile on a schedule"
```

---

## Task 3: Ratings drift sweep

Ratings change without any file event, so nothing re-triggers an item. This sweep re-enqueues items whose facts have gone stale and lets the existing pipeline notice the change.

**Files:**
- Modify: `src/autoposter/scheduler/jobs.py`
- Test: `tests/test_scheduler_drift_job.py`

**Interfaces:**
- Consumes: `enqueue` from `autoposter.queue.jobs`; `ItemFacts`, `MediaItem` from models.
- Produces: `make_drift_job(config) -> Job`; `async sweep_stale_facts(session, max_age_days: float, batch_size: int) -> int` returning how many items were enqueued.

**Notes for the implementer:**

- Select items whose `ItemFacts.fetched_at` is older than `max_age_days`, **or which have no `ItemFacts` row at all**, ordered oldest first, limited to `batch_size`. The missing-row case matters: those are exactly the items that have never been processed.
- Enqueue with a `dedupe_key` so an item already queued is not queued twice — the queue's existing debounce does the rest.
- **The batch size is the safety valve.** A sweep that enqueued all ~16,000 items at once would swamp the workers and hammer every provider. Default it to something modest (500) and let successive runs work through the backlog: at weekly cadence with 500 per pass this is deliberately slow, so make the interval and batch size configurable together and say so in the docs.
- Return a summary like `"enqueued 500 item(s) with facts older than 7 days"`.
- Enqueue only `movie` and `show` items — seasons and episodes are covered by their parent's pass.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler_drift_job.py` covering: items with fresh facts are not enqueued; items with stale facts are; items with no facts row at all are; the batch size caps how many are enqueued; oldest-first ordering; the returned count; and that episodes are not enqueued. Use the real database session fixture and the real `enqueue`.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_scheduler_drift_job.py -v`
Expected: FAIL with `ImportError: cannot import name 'sweep_stale_facts'`

- [ ] **Step 3: Implement and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/scheduler/jobs.py tests/test_scheduler_drift_job.py
git commit --no-gpg-sign -m "Sweep items whose ratings may have drifted"
```

---

## Task 4: Asset cleanup

**Files:**
- Modify: `src/autoposter/scheduler/jobs.py`
- Test: `tests/test_scheduler_cleanup_job.py`

**Interfaces:**
- Produces: `make_cleanup_job(config) -> Job`; `async find_orphaned_assets(session, assets_root: Path) -> list[Path]`; `def move_to_backup(paths: list[Path], assets_root: Path, backup_root: Path) -> int`.

**Notes for the implementer:**

- An asset directory is orphaned when no `renders` row references a path beneath it. Compare on the directory, not on individual files — an item's folder holds several artifacts.
- **Move, never delete.** Files go to `backup_root`, preserving their path relative to `assets_root`, which is what the tool being replaced does. There must be no `unlink`, `rmtree` or equivalent anywhere in this module, and a test should assert that by inspecting the module source.
- **Default to a dry run** (`cleanup.apply: false`), returning what *would* move.
- **Refuse to run if the database has no renders at all.** An empty `renders` table would make every asset look orphaned, and a scheduled pass would then move all 18,000 files to the backup directory. That is the catastrophic case: an empty result set meaning "everything is garbage" rather than "something is wrong". Return an explanatory summary and change nothing.
- The filesystem walk goes through `asyncio.to_thread`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler_cleanup_job.py` using `tmp_path` for both roots, covering: a directory referenced by a render is left alone; an unreferenced directory is reported; dry run moves nothing; with `apply` true the file is moved and the relative path preserved; **nothing is moved when `renders` is empty**; the module contains no delete call; and the walk happens off the event loop.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_scheduler_cleanup_job.py -v`
Expected: FAIL with `ImportError: cannot import name 'find_orphaned_assets'`

- [ ] **Step 3: Implement and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/scheduler/jobs.py tests/test_scheduler_cleanup_job.py
git commit --no-gpg-sign -m "Move orphaned assets to the backup directory"
```

---

## Task 5: Configuration and wiring

**Files:**
- Modify: `src/autoposter/config/schema.py`
- Modify: `config/autoposter.example.yaml`
- Modify: `src/autoposter/app.py`
- Modify: `deploy/README.md`
- Test: `tests/test_scheduler_config.py`

**Config to add:**

```yaml
scheduler:
  enabled: true # run the periodic passes
  poll_seconds: 60 # how often to check whether anything is due
  collections_hours: 24 # collections reconcile
  drift_days: 7 # ratings drift sweep
  drift_max_age_days: 7 # re-check an item whose facts are older than this
  drift_batch_size: 500 # items enqueued per sweep; the safety valve
  cleanup_days: 7 # orphaned asset cleanup
  cleanup_apply: false # dry run by default: report what would move, move nothing
```

**Notes for the implementer:**

- Start the scheduler in `app.py`'s lifespan alongside `health_task` and `imdb_task`, on the same `stop_event`, cancelled and awaited the same way.
- Register only the jobs whose features are enabled, so a deployment with collections off does not run a collections pass that immediately returns.
- Add a comment where the scheduler is registered explaining that `ImdbAutoRefresh` deliberately keeps its own loop, because its trigger is remote dataset staleness plus a miss-triggered path rather than a fixed interval.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler_config.py` asserting the defaults above, in the style of `tests/test_collection_config.py` (which uses `load_config(EXAMPLE_CONFIG)` rather than a bare `Config()`). In particular assert `cleanup_apply` is false and `drift_batch_size` is 500.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_scheduler_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'scheduler'`

- [ ] **Step 3: Add the config, wire the lifespan, run the suite**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — 697 baseline plus roughly 40 new tests, 5 skipped

- [ ] **Step 4: Update the deployment docs**

In `deploy/README.md`, document each job and its cadence, that `cleanup_apply` defaults to false and moves rather than deletes, that the drift sweep is deliberately throttled by `drift_batch_size` and works through a backlog over successive runs, that `scheduled_runs` records the last outcome of each job, and that the IMDb dataset refresh runs on its own separate schedule for the reason given above.

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter config/autoposter.example.yaml deploy/README.md tests
git commit --no-gpg-sign -m "Wire the scheduler into the application lifespan"
```

---

## Deferred

- **The adoption run.** The one-time first-boot walk that builds `media_items` and `renders` for the existing library, hashes what is already on disk, and marks current Plex artwork as already badged, so cutover triggers zero re-renders. This is the actual cutover moment and deserves its own phase.
- **Arr sync.** Blocked: it needs outbound Radarr and Sonarr access, and the service has only inbound webhook intake today — no Arr client, and no `radarr`/`sonarr` entry in the config schema.
