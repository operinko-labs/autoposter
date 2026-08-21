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

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.service import reconcile_libraries
from autoposter.config.schema import Config
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
