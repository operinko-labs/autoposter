"""History-table retention (perf spec D2): ``jobs``, ``events_log`` and
``provider_cache`` stop growing forever.

Called by the cleanup pass (``scheduler/jobs.py``'s ``make_cleanup_job``)
beside ``trim_run_history``, for the reason that job's docstring gives for
the trim riding there: a scheduled job of its own would need a name, a
cadence setting, an allowlist entry and a dashboard row to run three DELETEs
a week, and that pass is already the tree's housekeeping pass.

Fixed module constants, not settings -- the operator's decision (spec
Decisions table, "Retention"), and ``RUN_HISTORY_KEEP``'s reason: these bound
tables, they are not an operator's tuning decision.

Every age is measured on the DATABASE clock -- ``now()`` inside the SQL --
never this process's. ``updated_at``, ``received_at`` and ``expires_at`` are
all stamped by Postgres, and a Python-computed cutoff would move each window
by however far the two clocks disagree.

Batched: each statement deletes at most ``BATCH`` rows, chosen by a LIMITed
sub-select, and the loop repeats until a statement deletes nothing. The first
pass after this ships meets every row these tables have ever held, and one
unbounded DELETE over that is one enormous statement where a batch is not.

One transaction per TABLE, committed when that table's loop finishes. A
failure rolls back that table only, is logged, and the next table still
runs; ``apply_retention`` never raises, so the orphan walk after it in the
cleanup pass runs whatever happens here.
"""

import logging
from datetime import timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Finished jobs older than this go -- except the newest row per dedupe_key.
JOB_RETENTION_DAYS = 30
# Webhook and pass events older than this go, from the sources below only.
EVENT_RETENTION_DAYS = 90
# How long past `expires_at` a provider cache row is kept. `providers/cache.py`
# already ignores an expired row, so this is only slack against a read that
# races the delete.
PROVIDER_CACHE_GRACE = timedelta(days=1)
# Rows per DELETE statement. Read at call time, so a test can shrink it.
BATCH = 5000

# The FINISHED states. pending, running and deferred are live work; parked
# and failed are what the Failures page and Action Center show an operator,
# and deleting one would hide a problem rather than history.
PRUNABLE_JOB_STATES = ("done", "done_with_warnings", "dismissed")

# The sources whose rows are traffic, not record. Everything else --
# prune, merge, config, manual, picker, files, facts -- is an audit or
# operator trail and is kept forever: the prune audit row is the only record
# that a deleted item ever existed (scheduler/prune.py).
PRUNED_EVENT_SOURCES = ("radarr", "sonarr", "notifier", "actions", "collections", "playlists")

# Why the newest row per key is kept whatever its age: Action Center's
# `_parked_by_latest_job` (api/action_center.py) reads the state of the
# highest-id process_item row per dedupe_key, and a newer `done` does NOT
# retire an older `parked` sibling -- queue/jobs.py sweeps parked siblings
# only when a job parks. Deleting a newest `done` would promote that older
# parked row to "latest" and report an item blocked that is not. Deleting any
# NON-newest row can never change what DISTINCT ON picks.
#
# Only process_item rows carry a dedupe_key today (every enqueue site passes
# kind="process_item"). A keyed row of any other kind is left alone rather
# than guessed about, and the newer-row probe is scoped to process_item so
# that ix_jobs_latest_per_key (kind = 'process_item' AND dedupe_key IS NOT
# NULL) serves it instead of a scan per candidate.
#
# NULL-key rows have no "newest" to protect and nothing reads them by key;
# past the window they go.
_JOBS_SQL = text(
    """
    DELETE FROM jobs
     WHERE id IN (
           SELECT j.id
             FROM jobs j
            WHERE j.state IN :states
              AND j.updated_at < now() - make_interval(days => :days)
              AND (
                   j.dedupe_key IS NULL
                   OR (
                       j.kind = 'process_item'
                       AND EXISTS (
                           SELECT 1
                             FROM jobs newer
                            WHERE newer.kind = 'process_item'
                              AND newer.dedupe_key = j.dedupe_key
                              AND newer.id > j.id
                       )
                   )
              )
            LIMIT :batch
     )
    """
).bindparams(bindparam("states", expanding=True))

_EVENTS_SQL = text(
    """
    DELETE FROM events_log
     WHERE id IN (
           SELECT id
             FROM events_log
            WHERE source IN :sources
              AND received_at < now() - make_interval(days => :days)
            LIMIT :batch
     )
    """
).bindparams(bindparam("sources", expanding=True))

_PROVIDER_CACHE_SQL = text(
    """
    DELETE FROM provider_cache
     WHERE key IN (
           SELECT key
             FROM provider_cache
            WHERE expires_at < now() - make_interval(secs => :grace_seconds)
            LIMIT :batch
     )
    """
)


async def _delete_in_batches(session: AsyncSession, statement, params: dict) -> int:
    removed = 0
    while True:
        result = await session.execute(statement, {**params, "batch": BATCH})
        if result.rowcount == 0:
            return removed
        removed += result.rowcount


async def _prune(session: AsyncSession, table: str, statement, params: dict) -> int | None:
    try:
        removed = await _delete_in_batches(session, statement, params)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception(
            "retention: pruning %s failed and was rolled back; the next cleanup "
            "pass tries again",
            table,
        )
        return None
    if removed:
        logger.info("retention: removed %d %s row(s)", removed, table)
    return removed


async def apply_retention(session: AsyncSession) -> dict[str, int | None]:
    """Prune the three history tables; rows removed per table, ``None`` for a
    table that failed and was rolled back. Never raises -- see the module
    docstring.

    The statements are read from this module's globals at call time, so a
    test can swap one for a statement that fails.

    Precondition: ``session`` carries no uncommitted work when this is
    called. ``_prune`` commits after each table and rolls back on that
    table's failure alone; anything left pending from before this call would
    ride along with the first table's commit, or be discarded by the first
    table's rollback.
    """
    plan = (
        ("jobs", _JOBS_SQL,
         {"states": list(PRUNABLE_JOB_STATES), "days": JOB_RETENTION_DAYS}),
        ("events_log", _EVENTS_SQL,
         {"sources": list(PRUNED_EVENT_SOURCES), "days": EVENT_RETENTION_DAYS}),
        ("provider_cache", _PROVIDER_CACHE_SQL,
         {"grace_seconds": PROVIDER_CACHE_GRACE.total_seconds()}),
    )
    counts: dict[str, int | None] = {}
    for table, statement, params in plan:
        counts[table] = await _prune(session, table, statement, params)
    return counts


def describe_retention(counts: dict[str, int | None]) -> str:
    """The cleanup pass's summary suffix: what went, and which table failed."""
    removed = [f"{count} {table}" for table, count in counts.items() if count]
    failed = [table for table, count in counts.items() if count is None]
    note = ""
    if removed:
        note += "; retention removed " + ", ".join(removed) + " row(s)"
    if failed:
        note += "; retention failed for " + ", ".join(failed) + " (see the log)"
    return note
