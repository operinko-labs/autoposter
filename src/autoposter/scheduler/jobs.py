"""Job factories for the periodic scheduler.

Each factory wraps a ``Job`` (see ``scheduler.core``) around reconciliation
logic that already exists elsewhere in the codebase, rather than
reimplementing it -- two copies of a reconciliation sequence would drift
apart, and the copy nobody watches (the scheduled one) is the one that would
drift silently.
"""
import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.service import reconcile_libraries
from autoposter.config.schema import Config
from autoposter.db.models import ItemFacts, MediaItem
from autoposter.intake.arr import RenderIntent
from autoposter.queue.jobs import enqueue
from autoposter.scheduler.core import Job

logger = logging.getLogger(__name__)

# How config.scheduler eventually makes this configurable is a later task;
# once a day is a reasonable default cadence for a pass that mostly finds
# nothing left to do once a library has already been reconciled.
COLLECTIONS_INTERVAL_SECONDS = 24 * 3600


def make_collections_job(
    config: Config, server_factory: Callable[[], object], http: httpx.AsyncClient
) -> Job:
    """Build the scheduled collections-reconcile job.

    ``server_factory`` is a zero-argument callable returning a connected
    ``PlexServer``. Connecting is a blocking call, so it runs through
    ``asyncio.to_thread`` -- this job shares the event loop with the worker
    pool and the Plex liveness probe, and a stalled loop risks the pod being
    killed as unresponsive.

    ``config.collections.apply_to_plex`` still gates every write inside
    ``reconcile_libraries``; running this job with it off is simply a
    periodic dry run, a sensible way to watch what a pass would do before
    switching writes on.
    """

    async def run(session: AsyncSession) -> str:
        if not config.collections.enabled:
            return "skipped: collections disabled"
        server = await asyncio.to_thread(server_factory)
        return await reconcile_libraries(session, server, config, http)

    return Job(
        name="collections_reconcile",
        interval_seconds=COLLECTIONS_INTERVAL_SECONDS,
        run=run,
    )


# How config.scheduler eventually makes these configurable together is a
# later task. Weekly at 500 items a pass is deliberately slow -- ratings
# drift is not urgent, and this exists only to make sure every item is
# revisited eventually, not to catch a change quickly.
DRIFT_INTERVAL_SECONDS = 7 * 24 * 3600
DRIFT_MAX_AGE_DAYS = 7.0
DRIFT_BATCH_SIZE = 500


async def sweep_stale_facts(session: AsyncSession, max_age_days: float, batch_size: int) -> int:
    """Re-enqueue movies and shows whose gathered facts have gone stale.

    Ratings change without any file event, so nothing else re-triggers these
    items. This does no provider work itself -- it enqueues the same
    ``process_item`` job the webhook intake path uses, so the existing
    pipeline notices whatever changed. Seasons and episodes are left out;
    they are covered by their parent movie/show's pass.

    An item with no ``ItemFacts`` row at all is included alongside stale
    ones: those have never been processed, and an inner join would silently
    skip them forever. They sort first, as the most stale of all.

    ``batch_size`` is the safety valve -- enqueuing every stale item at once
    would swamp the worker pool and hammer every provider -- so only the
    oldest ``batch_size`` candidates are taken, leaving the rest for the
    next run.
    """
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, max_age_days * 86400)
    stmt = (
        select(MediaItem)
        .outerjoin(ItemFacts, ItemFacts.item_id == MediaItem.id)
        .where(MediaItem.kind.in_(("movie", "show")))
        .where(or_(ItemFacts.fetched_at.is_(None), ItemFacts.fetched_at < cutoff))
        .order_by(ItemFacts.fetched_at.asc().nulls_first())
        .limit(batch_size)
    )
    items = (await session.execute(stmt)).scalars().all()

    enqueued = 0
    for item in items:
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
            session,
            kind="process_item",
            payload=asdict(intent),
            dedupe_key=intent.dedupe_key,
        )
        if job_id is not None:
            enqueued += 1

    return enqueued


def make_drift_job(config: Config) -> Job:
    """Build the scheduled ratings-drift sweep job.

    ``config`` is accepted for the same signature shape as
    ``make_collections_job`` and for a future enable/config toggle; nothing
    here reads from it yet.
    """

    async def run(session: AsyncSession) -> str:
        count = await sweep_stale_facts(session, DRIFT_MAX_AGE_DAYS, DRIFT_BATCH_SIZE)
        return (
            f"enqueued {count} item(s) with facts older than "
            f"{DRIFT_MAX_AGE_DAYS:g} days"
        )

    return Job(
        name="ratings_drift_sweep",
        interval_seconds=DRIFT_INTERVAL_SECONDS,
        run=run,
    )
