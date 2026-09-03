from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import Job

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30

# How long a ``deferred`` job waits between looks. Deliberately hours rather
# than the backoff curve's minutes: a deferred job is waiting for the library
# to catch up with a release, which happens on a scale of days, and there is no
# attempt budget being spent to keep short.
DEFER_INTERVAL_SECONDS = 6 * 60 * 60

_CLAIM_SQL = text(
    """
    UPDATE jobs
       SET state = 'running',
           claimed_by = :worker,
           claimed_at = now(),
           attempts = attempts + 1,
           updated_at = now()
     WHERE id = (
           SELECT id
             FROM jobs
            WHERE state IN ('pending', 'deferred')
              AND run_after <= now()
            ORDER BY run_after, id
              FOR UPDATE SKIP LOCKED
            LIMIT 1
     )
    RETURNING id
    """
)


# Mirrors uq_jobs_pending_dedupe's predicate (db/models.py) exactly -- the ON
# CONFLICT clauses below only fire against rows this partial index covers.
_DEDUPE_INDEX_WHERE = text("state IN ('pending', 'deferred')")


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict,
    dedupe_key: str | None = None,
    delay_seconds: int = 0,
) -> int | None:
    """Add a job to the queue.

    When ``dedupe_key`` matches a job that is already pending, nothing is inserted
    and ``None`` is returned — that is the debounce for webhook bursts. A burst of
    events therefore produces a single pass whose delay is measured from the first
    event, which bounds latency instead of postponing work indefinitely.

    When ``dedupe_key`` matches a job that is ``deferred`` instead, that row is
    woken rather than debounced: its state goes back to ``pending`` with
    ``run_after`` set to this call's own schedule -- honouring ``delay_seconds``
    exactly as a fresh insert would -- and its id is returned exactly as a fresh
    insert's would be. A deferred job is waiting on Plex to catch up with a
    release it cannot see yet (see ``fail()``'s docstring), and a new event
    naming the same key -- above all the download webhook that finally lands
    -- is a targeted signal that the wait may be over. Leaving it deferred
    would make that webhook wait out the rest of a up-to-``DEFER_INTERVAL_SECONDS``
    horizon instead of processing promptly, which is the whole point of the
    event arriving at all. ``attempts`` is left untouched by the wake: ``fail()``
    already reset it to 0 the moment the row went deferred, so it already carries
    a full retry budget. ``enqueue_batch()`` makes the opposite choice against
    the same predicate -- see its docstring for why a sweep is not this kind of
    signal.
    """
    # run_after is computed by Postgres, not by this process. claim() compares it
    # against the database's now(), and an app clock that drifts from the database
    # clock would otherwise make jobs run early or late by the size of the drift.
    run_after = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay_seconds)
    stmt = insert(Job).values(
        kind=kind, payload=payload, dedupe_key=dedupe_key, run_after=run_after
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_update(
            index_elements=["dedupe_key"],
            index_where=_DEDUPE_INDEX_WHERE,
            # Postgres treats a DO UPDATE whose WHERE fails to match as DO
            # NOTHING for that row -- an existing *pending* row therefore
            # still debounces to a no-op with nothing returned, preserving
            # the contract above unchanged.
            set_={
                "state": "pending",
                "run_after": stmt.excluded.run_after,
                "updated_at": func.now(),
                "last_error": None,
            },
            where=(Job.__table__.c.state == "deferred"),
        )
    result = await session.execute(stmt.returning(Job.id))
    job_id = result.scalar_one_or_none()
    await session.commit()
    return job_id


# The Failures-page incident this sweep exists to fix: before #138 stopped
# re-selecting blocked items, every backfill press for an already-parked item
# minted a fresh job that failed and parked alongside the previous parked
# rows for the same item -- 12 piled up for one movie. uq_jobs_pending_dedupe
# only ever covered 'pending' and 'deferred' (see the index's comment in
# db/models.py), so nothing about the index stopped the pile from growing,
# and widening it to also cover 'parked' is deliberately not the fix: a
# parked item must stay re-enqueueable by an explicit retry or a fresh
# search (#138's design), and a unique index would block exactly that,
# turning "already have a parked row" into "can never park again" for the
# same key. The invariant this project actually wants -- at most one *live*
# parked row per item -- is instead kept honest the same way complete()
# keeps deferred rows honest below: a sweep, run every time a job parks,
# retiring whatever parked siblings for the same key came before it. The
# newest park wins, because it carries the freshest failure reason.
_DISMISS_PARKED_SIBLINGS_SQL = text(
    """
    UPDATE jobs
       SET state = 'dismissed',
           updated_at = now()
     WHERE dedupe_key = :dedupe_key
       AND state = 'parked'
       AND id <> :job_id
    """
)


# Rows per INSERT statement in enqueue_batch. At 3 bind parameters per row
# this stays far below asyncpg's limit while keeping a 15,000-item library at
# ~15 round trips instead of 15,000.
BATCH_ROWS = 1000


async def enqueue_batch(
    session: AsyncSession, kind: str, entries: list[tuple[dict, str]]
) -> int:
    """Add many jobs in a few set-based statements; return how many were inserted.

    Coalescing target matches ``enqueue()``'s widened index (an entry whose
    ``dedupe_key`` already has a pending *or* deferred job inserts nothing and
    is simply not counted), but the outcome for a deferred collision is
    deliberately the opposite of ``enqueue()``'s: DO NOTHING, not a wake.
    This backs the full-pass and quality-backfill sweeps, not a targeted
    event -- a scheduled pass is not fresh evidence that any one item's Plex
    situation changed, it is the same "might need reprocessing" guess
    repeated on a timer, and a deferred row already *is* exactly that guess,
    still waiting on its own horizon. Waking every deferred row on every pass
    would just have each retry once and likely re-defer (ItemNotFound is
    still ItemNotFound) -- harmless churn, bounded by one attempt per pass,
    but it buys nothing over leaving the row for its own horizon or a real
    event to wake it. Duplicate keys *within* ``entries`` collapse the same
    way: ON CONFLICT DO NOTHING skips a row that conflicts with one inserted
    earlier in the same statement.

    Set-based on purpose: this backs the full-pass trigger, and one awaited
    ``enqueue()`` (a commit each) per item would hold the calling request open
    for the whole library. ``run_after`` is left to its server default,
    ``now()`` on the database clock, matching ``enqueue()``.
    """
    inserted = 0
    for start in range(0, len(entries), BATCH_ROWS):
        batch = entries[start : start + BATCH_ROWS]
        stmt = insert(Job).values(
            [{"kind": kind, "payload": payload, "dedupe_key": key} for payload, key in batch]
        ).on_conflict_do_nothing(
            index_elements=["dedupe_key"], index_where=_DEDUPE_INDEX_WHERE
        )
        result = await session.execute(stmt)
        inserted += result.rowcount
    await session.commit()
    return inserted


async def claim(session: AsyncSession, worker_id: str) -> Job | None:
    """Atomically take the next due job. Concurrent callers never collide."""
    result = await session.execute(_CLAIM_SQL, {"worker": worker_id})
    job_id = result.scalar_one_or_none()
    await session.commit()
    if job_id is None:
        return None
    return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


# The gap between one reclaimed job's run_after and the next. Production
# incident: a periodic reclaim that set every reclaimed job's run_after to
# the same now() released 5 stuck jobs into the worker pool simultaneously,
# and the concurrent renders OOMKilled the pod within seconds. The jobs died
# together (the same restart orphaned all of them), so restarting them
# together is exactly what killed it -- see reclaim_stale()'s docstring.
RECLAIM_STAGGER_SECONDS = 30

_RECLAIM_SQL = text(
    """
    WITH stale AS (
        SELECT id, ROW_NUMBER() OVER (ORDER BY claimed_at, id) - 1 AS ordinal
          FROM jobs
         WHERE state = 'running'
           AND claimed_at < now() - make_interval(secs => :older_than_seconds)
    )
    UPDATE jobs
       SET state = 'pending',
           claimed_by = NULL,
           claimed_at = NULL,
           run_after = now() + make_interval(secs => stale.ordinal * :stagger_seconds),
           updated_at = now()
      FROM stale
     WHERE jobs.id = stale.id
       -- Restated from the CTE, not redundant: under READ COMMITTED, a second
       -- reclaim blocked on this row (a concurrent sweep, e.g. a new pod's
       -- boot reclaim racing the outgoing pod's periodic tick) re-checks only
       -- this outer qualification against the row's *current* version when it
       -- unblocks -- the CTE was already materialized and does not re-run.
       -- `jobs.id = stale.id` alone survives that re-check no matter what
       -- happened to the row meanwhile, so each restated predicate is closing
       -- a distinct interleaving:
       -- - claimed_at < cutoff fails the re-check when a live worker
       --   re-claimed the row in the interim (fresh claimed_at), because
       --   now() is transaction_timestamp() and stays pinned at the blocked
       --   sweep's own transaction start -- without this predicate the fresh
       --   claim gets wiped out from under the live worker.
       -- - state = 'running' independently fails the re-check when the row
       --   finished instead: complete() sets state = 'done' but deliberately
       --   leaves claimed_at alone, so a job that ran past the threshold and
       --   then completed still has a stale claimed_at -- without this
       --   predicate a done job gets resurrected to pending.
       -- Dropping either predicate reopens its own interleaving; neither is
       -- redundant with the other.
       AND jobs.state = 'running'
       AND jobs.claimed_at < now() - make_interval(secs => :older_than_seconds)
    """
)


async def reclaim_stale(session: AsyncSession, older_than_seconds: int = 900) -> int:
    """Return jobs stuck at ``running`` (process died mid-job) back to ``pending``.

    Only claims older than the threshold are touched, not every ``running`` row, so
    this stays correct if a second replica is genuinely still working a job. The
    cutoff is computed by the database clock, matching claim()/enqueue()/fail().

    A reclaimed job is one whose worker died mid-job, so its old ``run_after``
    describes a schedule that no longer means anything — the work is overdue, not
    pending a future slot. But "overdue" does not mean "all at once": jobs
    reclaimed together died together (the same crashed process, or the same pod
    restart), and readmitting a whole batch to the worker pool in the same instant
    reproduces whatever conditions killed them the first time -- see
    ``RECLAIM_STAGGER_SECONDS``. So each reclaimed row's ``run_after`` is now() plus
    its ordinal (claim age, oldest first) times the stagger, not a flat now() --
    the first row is still immediately claimable, and the rest re-enter the pool
    at a paced rate rather than all in the same instant. That bounds the
    *admission* rate, not concurrency: with several workers and a render that
    outlasts the stagger, reclaimed jobs still end up running side by side a
    couple of minutes later -- see ``RECLAIM_STAGGER_SECONDS``.
    """
    result = await session.execute(
        _RECLAIM_SQL,
        {"older_than_seconds": older_than_seconds, "stagger_seconds": RECLAIM_STAGGER_SECONDS},
    )
    await session.commit()
    return result.rowcount


async def release(session: AsyncSession, job_id: int) -> None:
    """Return a claimed job to ``pending`` without charging it a retry attempt.

    Used when a worker is cancelled (graceful shutdown) mid-job: the job didn't
    fail, the process just stopped, so it should be immediately claimable again
    exactly as if it had never been picked up.
    """
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.state = "pending"
    job.claimed_by = None
    job.claimed_at = None
    job.attempts = max(job.attempts - 1, 0)
    await session.commit()


# uq_jobs_pending_dedupe now covers 'pending' and 'deferred' both, so
# enqueue()/enqueue_batch() can no longer produce two *simultaneously live*
# rows for the same key -- a fresh event either wakes the existing deferred
# row (enqueue()) or is coalesced away (enqueue_batch()). This sweep is not
# dead code even so: 'running' is not covered by the partial index at all, so
# a second, independent row can still be inserted for a key whose first job
# is mid-attempt. If that second row itself later defers while the first is
# still in flight, and the first then completes, the second is exactly the
# stranded row this sweep exists to retire (see test_queue.py's
# test_complete_dismisses_a_deferred_sibling_created_while_the_first_ran).
#
# Matches on dedupe_key alone, so this sweep inherits RenderIntent.dedupe_key's
# precision exactly (intake/arr.py) -- including its title-fallback: two
# same-kind, same-title items with no external id at all (a remake, a
# re-release) share a key, and here that means the second item's success can
# dismiss the first item's still-waiting deferred row rather than merely
# suppressing its enqueue. Requires a webhook carrying no tmdb/tvdb/imdb id,
# which Radarr/Sonarr essentially always send, so the risk is narrow; not
# special-cased.
_DISMISS_DEFERRED_SIBLINGS_SQL = text(
    """
    UPDATE jobs
       SET state = 'dismissed',
           updated_at = now()
     WHERE dedupe_key = :dedupe_key
       AND state = 'deferred'
       AND id <> :job_id
    """
)


async def complete(session: AsyncSession, job_id: int) -> None:
    """Mark a job done, and retire any deferred row still waiting on the same item.

    The second half handles what the widened dedupe index cannot: 'running'
    rows fall outside uq_jobs_pending_dedupe's partial predicate, so an event
    arriving for a key while its job is already running still inserts an
    independent second row rather than waking the first. If that second row
    then defers on its own before the first (still in flight) completes, the
    deferred row is left waiting for something that has already happened. It
    would resolve on its own next look and redo finished work, which is
    harmless but not free, and until then it reads as outstanding on the Jobs
    page. Dismissed, not deleted, matching every other disposal in this
    project. See ``_DISMISS_DEFERRED_SIBLINGS_SQL``'s comment above for why
    this is narrower than it once was but not gone.
    """
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.state = "done"
    job.last_error = None
    if job.dedupe_key is not None:
        await session.execute(
            _DISMISS_DEFERRED_SIBLINGS_SQL,
            {"dedupe_key": job.dedupe_key, "job_id": job_id},
        )
    await session.commit()


async def fail(
    session: AsyncSession,
    job_id: int,
    error: str,
    max_attempts: int = MAX_ATTEMPTS,
    defer_seconds: int | None = None,
) -> str:
    """Decide what happens after an attempt that did not succeed: dismiss,
    defer, park, or reschedule with backoff -- in that order of precedence.

    A job an operator cancelled while it was running is dismissed here instead,
    whatever budget it had left. This is the only place that decides what
    happens after an attempt that did not succeed -- every one of ``run_once``'s
    non-success branches comes through it. Honouring the cancellation at the
    caller instead would have to be written three times, and would let both the
    Plex-connectivity path and the unbounded deferral below ignore it.

    ``defer_seconds`` is the third outcome, and it is not a failure at all: the
    attempt found nothing wrong with the job, only that Plex cannot see the item
    yet (``run_once``'s ``ItemNotFound`` branch). Such a job goes ``deferred``
    and comes back after that long a wait, with NO attempt cap -- an unreleased
    movie added to Radarr can be weeks from resolving, and any finite budget
    turns that wait into a permanent parked "failure" nothing resurrects.
    ``attempts`` is reset with it, because a wait is not a failed attempt: a job
    that has waited a dozen times still deserves its full retry budget the day
    Plex answers and something else genuinely breaks.

    Cancellation still outranks the deferral -- it has to, since the horizon is
    unbounded and dismissing is the operator's only way to end one.

    Parking also dismisses any other parked row sharing this job's dedupe_key
    -- see ``_DISMISS_PARKED_SIBLINGS_SQL``'s comment for why that is a sweep
    here rather than a widened unique index.
    """
    # Read without FOR UPDATE, unlike cancel_job's own read of this row: a
    # cancel that commits between this SELECT and the UPDATE below is missed
    # here, so this failure reschedules the job instead of dismissing it. That
    # is benign, not a bug to close -- cancel_requested is still True on the
    # row afterwards, so the job's *next* failure sees it and dismisses the job
    # there. The race costs the cancel one extra attempt; it never loses the
    # cancel. Locking here was reviewed and judged not worth it for that.
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.last_error = error
    job.claimed_by = None
    job.claimed_at = None
    if job.cancel_requested:
        job.state = "dismissed"
    elif defer_seconds is not None:
        job.state = "deferred"
        job.attempts = 0
        # Database clock again, for the same reason as enqueue().
        job.run_after = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, defer_seconds)
    elif job.attempts >= max_attempts:
        job.state = "parked"
        if job.dedupe_key is not None:
            await session.execute(
                _DISMISS_PARKED_SIBLINGS_SQL,
                {"dedupe_key": job.dedupe_key, "job_id": job_id},
            )
    else:
        job.state = "pending"
        backoff = BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1))
        # Database clock again, for the same reason as enqueue().
        job.run_after = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, backoff)
    await session.commit()
    return job.state
