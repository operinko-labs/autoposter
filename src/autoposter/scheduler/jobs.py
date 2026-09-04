"""Job factories for the periodic scheduler.

Each factory wraps a ``Job`` (see ``scheduler.core``) around reconciliation
logic that already exists elsewhere in the codebase, rather than
reimplementing it -- two copies of a reconciliation sequence would drift
apart, and the copy nobody watches (the scheduled one) is the one that would
drift silently.
"""
import asyncio
import itertools
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.arr.client import RADARR, SONARR, ArrClient, ArrKind
from autoposter.arr.sync import (
    ArrSyncRefused,
    ArrSyncSettings,
    enqueue_unknown_items,
    sync_section,
)
from autoposter.collections.credits import scan_credits
from autoposter.collections.playlists import PlaylistsPassFailed, reconcile_playlists
from autoposter.collections.service import (
    CollectionsPassFailed,
    build_source_clients,
    reconcile_libraries,
)
from autoposter.config.holder import ConfigHolder
from autoposter.config.schema import RadarrConfig, Secrets, SonarrConfig
from autoposter.db.models import FactsBackfillState, ItemFacts, MediaItem, Render
from autoposter.intake.arr import RenderIntent
from autoposter.queue.jobs import enqueue, reclaim_stale
from autoposter.scheduler.core import Job

logger = logging.getLogger(__name__)

# How often the stale-claim reclaim sweep runs. Not read off the config holder
# like the other jobs in this module -- this is a queue-correctness sweep, not
# a tunable maintenance pass, so it gets a plain constant rather than a new
# scheduler.* setting.
STALE_RECLAIM_INTERVAL_SECONDS = 5 * 60


def make_stale_reclaim_job() -> Job:
    """Build the scheduled stale-claim reclaim sweep.

    ``reclaim_stale`` (``queue/jobs.py``) is also called once at app startup --
    that boot-time call is kept as-is and is what catches a claim orphaned by
    THIS process's own restart. This periodic sweep is for everything a boot
    reclaim structurally cannot catch: a claim that goes stale while the
    process is already up and running (its owner died without the pod
    restarting), and the incident this job was added for -- two restarts
    minutes apart, where the second boot's reclaim skipped jobs the first
    restart had just orphaned because their claims were still younger than
    the 900s threshold. Without a periodic sweep, nothing ever revisits a
    ``running`` row again after that.

    Takes no config: the 900s threshold stays a hard-coded argument to
    ``reclaim_stale`` (not read from here), preserving the property its own
    docstring documents -- a replica genuinely still working a job is never
    stolen from. This job's own cadence is ``STALE_RECLAIM_INTERVAL_SECONDS``,
    a module constant rather than a holder setting, for the same reason: this
    is queue correctness, not an operator-tunable maintenance pass.
    """

    async def run(session: AsyncSession) -> str:
        reclaimed = await reclaim_stale(session)
        if reclaimed:
            logger.info("reclaimed %d stale job(s)", reclaimed)
        return f"reclaimed {reclaimed} stale job(s)" if reclaimed else "nothing to reclaim"

    return Job(
        name="stale_job_reclaim",
        interval_seconds=STALE_RECLAIM_INTERVAL_SECONDS,
        run=run,
    )


def make_collections_job(
    holder: ConfigHolder,
    server_factory: Callable[[], object],
    http: httpx.AsyncClient,
    summaries=None,
    secrets: Secrets | None = None,
    cache=None,
    notifier=None,
) -> Job:
    """Build the scheduled collections-reconcile job.

    The config is taken from ``holder`` per run and per cadence check, never
    closured: an operator who edits a collections setting or this job's
    cadence sees it honoured on the next pass rather than at the next
    restart. The one thing a swap cannot change is whether this job exists at
    all -- that is decided when the job set is registered, which is why
    ``collections.enabled`` is in ``config/live.py``'s ``FROZEN_SECTIONS``
    even though the guard below still reads it live.

    ``server_factory`` is a zero-argument callable returning a connected
    ``PlexServer``. Connecting is a blocking call, so it runs through
    ``asyncio.to_thread`` -- this job shares the event loop with the worker
    pool and the Plex liveness probe, and a stalled loop risks the pod being
    killed as unresponsive.

    ``config.collections.apply_to_plex`` still gates every write inside
    ``reconcile_libraries``; running this job with it off is simply a
    periodic dry run, a sensible way to watch what a pass would do before
    switching writes on.

    ``summaries`` is the process's TMDB facts client, which a definition
    configured with ``tmdb_summary:`` borrows its summary through. Closured
    rather than read per run: it wraps the process's HTTP client and provider
    cache, neither of which a config swap replaces.

    ``secrets`` and ``cache`` are what the builders' own clients are built
    from -- per run, not closured, because the config half of that (whether
    Radarr is enabled, and at which URL) is live-editable. Without ``secrets``
    the pass still runs, with every source client absent: the shipped
    definitions need none of them, and a definition that does reports itself
    failed rather than taking the pass down.

    ``notifier`` is the process's notifier, forwarded to the pass for row 19's
    per-collection webhooks. Absent, the pass sends nothing.

    The playlists half (roadmap row 98a) runs after the collections half, in
    this job rather than in one of its own: it needs the same server, the same
    clients and the same config generation, and a second job would be a second
    cadence for two passes an operator thinks of as one. It is a SIBLING of
    ``reconcile_libraries`` and not a step inside it -- a playlist belongs to no
    library, so it cannot be committed under one -- and it carries its own
    per-definition commit boundary. The two switches are honoured
    independently: ``collections.enabled`` off with ``playlists.enabled`` on
    runs the playlists half alone, because skipping it would be a setting that
    reads as configured and silently is not. That independence only means
    anything because ``app.py`` registers this job on
    ``collections.enabled or playlists.enabled`` -- the paragraph above about
    ``collections.enabled`` being frozen now applies to ``playlists.enabled``
    too, and both are in ``FROZEN_SECTIONS`` for the one reason: whether this
    job exists is decided when the job set is built.
    """
    summaries_client = summaries

    async def run(session: AsyncSession) -> str:
        config = holder.current
        collections_on = config.collections.enabled
        playlists_on = config.playlists.enabled
        if not collections_on and not playlists_on:
            return "skipped: collections and playlists disabled"

        server = await asyncio.to_thread(server_factory)
        # Which pass this is, for definitions gated to every Nth run. Derived
        # from the clock rather than counted in the database: it needs no
        # schema, survives restarts and cannot drift between replicas, and the
        # cost -- a skipped pass shifts which runs a gated definition lands on
        # -- does not matter to something asking to run every other pass.
        interval = max(config.scheduler.collections_hours * 3600, 1)
        run_index = int(time.time() // interval)
        sources = (
            build_source_clients(config, secrets, http, cache)
            if secrets is not None else None
        )

        lines: list[str] = []
        collections_detail: str | None = None
        playlists_detail: str | None = None

        if collections_on:
            result = await reconcile_libraries(
                session, server, config, http, run_index=run_index,
                summaries=summaries_client, sources=sources, cache=cache,
                notifier=notifier,
            )
            lines.append(result.summary)
            if result.failed:
                collections_detail = result.detail
        else:
            lines.append("collections disabled")

        if playlists_on:
            # Below the collections half and outside its result handling: the
            # playlists pass commits per definition, so a collections failure
            # has already been contained and committed by the time this runs,
            # and a playlists failure must not un-record it.
            playlists = await reconcile_playlists(
                session, server, config, http, run_index=run_index,
                sources=sources, cache=cache,
            )
            lines.append(playlists.summary)
            if playlists.failed:
                playlists_detail = playlists.detail

        summary = "; ".join(lines)
        # Raised, not returned, because ``last_status`` is decided by whether
        # this coroutine raised (scheduler/core.py). Raised HERE, after both
        # halves: everything that succeeded has already committed, so nothing is
        # rolled back by this.
        #
        # Which class is raised is decided by which half broke, so the name in
        # the log and in scheduled_runs.last_status points at the right
        # subsystem: ``PlaylistsPassFailed`` only when the playlists half is the
        # ONLY thing that failed, and ``CollectionsPassFailed`` otherwise --
        # including when both did, because a collections failure is the larger
        # fact and the detail below carries both.
        details = [d for d in (collections_detail, playlists_detail) if d]
        if details:
            error = (
                PlaylistsPassFailed if collections_detail is None
                else CollectionsPassFailed
            )
            raise error("; ".join(details))
        return summary

    return Job(
        name="collections_reconcile",
        interval_seconds=lambda: holder.current.scheduler.collections_hours * 3600,
        run=run,
    )


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

    Ordering is by the *attempt*, not by ``fetched_at`` alone.
    ``gather_facts`` stamps ``fetched_at`` only when it succeeds, so an item
    that cannot be resolved at all -- no external ids, gone from Plex, a
    provider that keeps erroring -- would keep its NULL or ancient timestamp
    and sort to the front of every sweep forever. Once ``batch_size`` such
    items exist, nothing else is ever revisited and the sweep stops doing the
    one thing it is for. So every selected item's ``facts_attempted_at`` is
    stamped here, whether or not the attempt later succeeds, and the sort key
    is the later of the two timestamps: a failing item goes to the back of
    the queue like anything else, and simply comes round again in time.
    """
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, max_age_days * 86400)
    # GREATEST ignores NULLs, so an item that has neither been fetched nor
    # attempted still sorts first -- as the most stale of all.
    last_touched = func.greatest(ItemFacts.fetched_at, MediaItem.facts_attempted_at)
    stmt = (
        select(MediaItem)
        .outerjoin(ItemFacts, ItemFacts.item_id == MediaItem.id)
        .where(MediaItem.kind.in_(("movie", "show")))
        .where(or_(ItemFacts.fetched_at.is_(None), ItemFacts.fetched_at < cutoff))
        .order_by(last_touched.asc().nulls_first())
        .limit(batch_size)
    )
    items = (await session.execute(stmt)).scalars().all()

    return await _stamp_and_enqueue(session, items)


async def _stamp_and_enqueue(session: AsyncSession, items) -> int:
    """The sweep's tail, shared with ``backfill_facts``: stamp every selected
    item's ``facts_attempted_at`` (see ``sweep_stale_facts``'s docstring for
    why the stamp is unconditional), then enqueue the same ``process_item``
    job the webhook intake path uses. Returns how many were actually
    enqueued -- ``dedupe_key`` folds an item whose identical job is already
    pending."""
    if items:
        await session.execute(
            update(MediaItem)
            .where(MediaItem.id.in_([item.id for item in items]))
            .values(facts_attempted_at=func.now())
        )

    # Per-item enqueue() here does wake a deferred item rather than leaving it
    # alone, but this runs on a 7-day cadence over at most drift_batch_size
    # items, and the unconditional facts_attempted_at stamp above already
    # pushed a woken-then-re-deferred item to the back of the sort order --
    # worst case one wasted Plex lookup per deferred item per week.
    # enqueue_batch() isn't a drop-in: it returns a rowcount not per-item ids,
    # and drops delay_seconds entirely.
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
            # The row's own Plex identity, like every other row-derived intent
            # in the tree. Without it this sweep was one of the two SILENT
            # twin producers: no key means no rating-key hint, so every drift
            # job took the GUID walk, resolved the live key and upserted a
            # second row -- and the pipeline's fork warning never fired,
            # because it only fires when the intent carried a key to disagree
            # with. The pipeline's identity re-key closes the hole either way;
            # carrying the key also makes the fork visible in the log.
            rating_key=item.rating_key,
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


_BACKFILL_ROW_ID = 1


@dataclass(frozen=True)
class BackfillBatch:
    """One trigger's outcome. ``selected == 0`` is the idempotent "complete":
    nothing past the cursor, nothing stamped, cursor unmoved."""

    selected: int
    enqueued: int


async def backfill_facts(session: AsyncSession, batch_size: int) -> BackfillBatch:
    """One batch of the one-shot facts catch-up (roadmap row 206).

    ``sweep_stale_facts`` with the age predicate dropped and a cursor in its
    place -- NOT a new pipeline. The sweep's population rule (movies and
    shows; children ride on the parent's pass), its ``batch_size`` valve, its
    selection-time ``facts_attempted_at`` stamp and its enqueue are all kept;
    what is dropped is the staleness test and the staleness *ordering*: this
    walk exists because freshness says nothing about the never-written
    columns, and it orders by ``MediaItem.id`` so the persisted cursor makes
    a re-trigger resume exactly where the last one stopped.

    The cursor lives in ``facts_backfill_state`` (one row, ``id=1``) so pods
    behind one database share one walk. It advances only when items were
    selected; the TMDb-window park is the CALLER's (``api/facts_backfill.py``)
    -- by the time this runs, the trigger has decided to spend.

    Not scheduled, deliberately: the row's own rejection of a migration hook
    applies to any unattended trigger -- this fires because an operator asked.
    """
    state = (
        await session.execute(
            select(FactsBackfillState).where(FactsBackfillState.id == _BACKFILL_ROW_ID)
        )
    ).scalar_one_or_none()
    cursor = state.cursor_item_id if state is not None else None

    stmt = (
        select(MediaItem)
        .where(MediaItem.kind.in_(("movie", "show")))
        .order_by(MediaItem.id.asc())
        .limit(batch_size)
    )
    if cursor is not None:
        stmt = stmt.where(MediaItem.id > cursor)
    items = (await session.execute(stmt)).scalars().all()
    if not items:
        return BackfillBatch(selected=0, enqueued=0)

    enqueued = await _stamp_and_enqueue(session, items)

    upsert = insert(FactsBackfillState).values(
        id=_BACKFILL_ROW_ID, cursor_item_id=items[-1].id, updated_at=func.now()
    )
    await session.execute(
        upsert.on_conflict_do_update(
            index_elements=["id"],
            set_={"cursor_item_id": items[-1].id, "updated_at": func.now()},
        )
    )
    return BackfillBatch(selected=len(items), enqueued=enqueued)


def make_drift_job(holder: ConfigHolder) -> Job:
    """Build the scheduled ratings-drift sweep job.

    Weekly by default -- ratings drift is not urgent, and this exists only to
    make sure every item is eventually revisited, not to catch a change
    quickly. Both the cadence and the sweep's own settings come off the holder
    per use, so an edit to either is live.
    """

    async def run(session: AsyncSession) -> str:
        scheduler = holder.current.scheduler
        max_age_days = scheduler.drift_max_age_days
        count = await sweep_stale_facts(session, max_age_days, scheduler.drift_batch_size)
        return f"enqueued {count} item(s) with facts older than {max_age_days:g} days"

    return Job(
        name="ratings_drift_sweep",
        interval_seconds=lambda: holder.current.scheduler.drift_days * 24 * 3600,
        run=run,
    )


def make_credits_job(holder: ConfigHolder, server_factory: Callable[[], object]) -> Job:
    """Build the scheduled library-credits scan (roadmap rows 197/194).

    ``server_factory`` is the same zero-argument connected-``PlexServer``
    contract ``make_collections_job`` takes, run in a thread for the same
    reason. Settings come off the holder per run, so an edit is live.
    """

    async def run(session: AsyncSession) -> str:
        server = await asyncio.to_thread(server_factory)
        return await scan_credits(session, server, holder.current)

    return Job(
        name="credits_scan",
        interval_seconds=lambda: holder.current.scheduler.credits_scan_days * 24 * 3600,
        run=run,
    )


def make_maintenance_job(holder: ConfigHolder, server_factory: Callable[[], object]) -> Job:
    """Build the scheduled Plex maintenance pass (roadmap row 36).

    Registered unconditionally under ``scheduler.enabled``, like the credits
    and cleanup jobs, and reading its three switches off the holder per run --
    so flipping one is live and needs no ``FROZEN_SECTIONS`` entry (a per-job
    ``enabled`` flag would need one; see app.py's job-set comment).

    Each operation is a single blocking plexapi call, offloaded like every
    other Plex call in this module. A failure is reported in the summary
    rather than raised, and the remaining operations still run: these three
    are independent, and losing ``optimize`` because ``emptyTrash`` timed out
    would be a worse outcome than either.
    """

    async def run(session: AsyncSession) -> str:
        config = holder.current
        wanted = [
            ("clean_bundles", "cleanBundles"),
            ("empty_trash", "emptyTrash"),
            ("optimize", "optimize"),
        ]
        enabled = [
            (setting, method) for setting, method in wanted
            if getattr(config.maintenance, setting)
        ]
        if not enabled:
            return "skipped: no maintenance operation is enabled"

        server = await asyncio.to_thread(server_factory)
        ran: list[str] = []
        failed: list[str] = []
        for setting, method in enabled:
            try:
                await asyncio.to_thread(getattr(server.library, method))
            except Exception as error:
                # The full message and traceback go to the pod log -- the
                # trusted sink (roadmap row 207). The summary below is
                # scheduled_runs.last_detail: served by /api/snapshots and the
                # dashboard stream, carried in the notification payload -- and
                # a plexapi/requests failure's str() embeds the host, port and
                # URL it failed on. Class name only there (row 213's rule).
                logger.warning("maintenance: %s failed", setting, exc_info=True)
                failed.append(f"{setting} failed ({type(error).__name__})")
            else:
                ran.append(setting)

        summary = f"ran {', '.join(ran)}" if ran else "ran nothing"
        return f"{summary}; {'; '.join(failed)}" if failed else summary

    return Job(
        name="plex_maintenance",
        interval_seconds=lambda: holder.current.scheduler.maintenance_days * 24 * 3600,
        run=run,
    )


def _radarr_settings(cfg: RadarrConfig) -> ArrSyncSettings:
    return ArrSyncSettings(
        plex_root=cfg.plex_path,
        arr_root=cfg.arr_path,
        quality_profile=cfg.quality_profile,
        monitored=cfg.monitor,
        minimum_availability=cfg.minimum_availability,
    )


def _sonarr_settings(cfg: SonarrConfig) -> ArrSyncSettings:
    return ArrSyncSettings(
        plex_root=cfg.plex_path,
        arr_root=cfg.arr_path,
        quality_profile=cfg.quality_profile,
        monitored=cfg.monitor,
        series_type=cfg.series_type,
        season_folder=cfg.season_folder,
    )


async def _sync_one_service(
    http: httpx.AsyncClient,
    section_title: str,
    items: list,
    arr_kind: ArrKind,
    service_cfg,
    settings: ArrSyncSettings,
    api_key: str,
) -> str:
    """Register one section's missing items with one service.

    ``sync_section`` raises ``ValueError`` when the configured quality
    profile cannot be resolved, and ``ArrSyncRefused`` when the service's
    own answers say it is not the instance this sync was configured for --
    operator misconfigurations, not transient faults. Both are caught here,
    not left to propagate: they must be loud (logged with a full traceback)
    but they must not take down the other service's sync or the safety-net
    enqueue that runs after this in ``make_arr_sync_job``. Any other failure
    (the service unreachable, a bad api key, ...) is contained the same way
    for the same reason.
    """
    client = ArrClient(http, service_cfg.base_url, api_key, arr_kind)
    try:
        report = await sync_section(
            client, items, arr_kind, settings, dry_run=not service_cfg.add_existing
        )
    except ArrSyncRefused as exc:
        logger.error(
            "arr_sync: %s refused for %r: %s", arr_kind.name, section_title, exc, exc_info=True
        )
        return f"{section_title} {arr_kind.name}: refused, {exc}"
    except Exception:
        logger.error(
            "arr_sync: %s sync failed for %r", arr_kind.name, section_title, exc_info=True
        )
        return f"{section_title} {arr_kind.name}: failed, see log"
    return (
        f"{section_title} {arr_kind.name}: checked {report.checked}, "
        f"missing {report.missing}, added {report.added}, failed {report.failed}, "
        f"misassigned {report.skipped_path_taken}"
    )


def make_arr_sync_job(
    holder: ConfigHolder,
    server_factory: Callable[[], object],
    http: httpx.AsyncClient,
    secrets: Secrets,
) -> Job:
    """Build the scheduled Radarr/Sonarr sync and safety-net job.

    Two independent things run per Plex library: the Radarr/Sonarr
    registration (only for the service configured ``enabled`` for that
    library's type) and the safety-net enqueue (unconditional, whenever
    ``arr_sync.enabled`` -- see ``enqueue_unknown_items``). Neither service
    being configured is a clean no-op, not an error: with both disabled this
    job only runs the safety net.

    Config comes off ``holder`` per run and per cadence check, so every
    setting this pass reads -- including ``arr_sync.hours`` -- is live.
    ``secrets`` and ``server_factory`` are not: secrets are env-only and the
    Plex connection details are frozen at startup.

    ``server_factory`` is a zero-argument callable returning a connected
    ``PlexServer``, the same contract ``make_collections_job`` uses, and for
    the same reason it runs through ``asyncio.to_thread``: connecting is a
    blocking call sharing the event loop with the worker pool and the Plex
    liveness probe. Listing the sections and listing each section's items
    block for the same reason and are offloaded the same way -- and each
    section is listed exactly once per pass, with that one list handed to
    both the registration and the safety net.
    """

    async def run(session: AsyncSession) -> str:
        config = holder.current
        if not config.arr_sync.enabled:
            return "skipped: arr_sync disabled"
        server = await asyncio.to_thread(server_factory)
        sections = await asyncio.to_thread(server.library.sections)

        parts: list[str] = []
        for section in sections:
            if section.title in config.plex.excluded_libraries:
                continue

            if section.type == "movie":
                plex_kind = "movie"
            elif section.type == "show":
                plex_kind = "show"
            else:
                continue

            # Listed once per pass, off the loop, and shared by both halves:
            # a section of ~2,000 items takes seconds to list, and this loop
            # is shared with the worker pool and the Plex liveness probe.
            items = await asyncio.to_thread(section.all)

            if plex_kind == "movie" and config.radarr.enabled:
                parts.append(await _sync_one_service(
                    http, section.title, items, RADARR, config.radarr,
                    _radarr_settings(config.radarr), secrets.radarr_apikey,
                ))
            elif plex_kind == "show" and config.sonarr.enabled:
                parts.append(await _sync_one_service(
                    http, section.title, items, SONARR, config.sonarr,
                    _sonarr_settings(config.sonarr), secrets.sonarr_apikey,
                ))

            enqueued = await enqueue_unknown_items(
                session, items, plex_kind, section.title,
                batch_size=config.arr_sync.batch_size,
            )
            parts.append(f"{section.title}: enqueued {enqueued} unknown item(s)")

        return "; ".join(parts) if parts else "no libraries to sync"

    return Job(
        name="arr_sync",
        interval_seconds=lambda: holder.current.arr_sync.hours * 3600,
        run=run,
    )


@dataclass(frozen=True)
class OrphanScan:
    """What one sweep of ``assets_root`` found.

    ``scanned`` is every directory holding at least one file -- the pool
    ``orphaned`` was chosen from. The caller needs both to judge whether the
    result is plausible: "40 of 12,000" is routine churn, "11,900 of 12,000"
    means the database and the filesystem disagree about where assets live.
    """

    orphaned: list[Path]
    scanned: int


@dataclass(frozen=True)
class MoveOutcome:
    """Counts from one ``move_to_backup`` batch.

    ``failed`` is reported rather than raised: a single unmovable directory
    must not throw away the record of the ones already moved, which is what
    letting ``shutil.Error`` escape mid-loop used to do.
    """

    moved: int
    failed: int


async def find_orphaned_assets(session: AsyncSession, assets_root: Path) -> OrphanScan:
    """Return every asset directory under ``assets_root`` no ``renders`` row references.

    Matches on the directory, not individual files: an item's asset folder
    holds several artifacts (poster, background, season posters, ...), so a
    directory is orphaned only when no render's ``asset_path`` is beneath it
    at all -- one surviving artifact is enough to keep the whole folder.

    Orphan-hood is decided by set membership, not by comparing every
    directory against every render path. Every ancestor of every recorded
    ``asset_path`` goes into one ``kept`` set, and a directory is orphaned
    exactly when it is not in that set: linear in (directories + render
    paths) rather than the product of the two. The product form was ~2.5e8
    ``is_relative_to`` calls for this library -- minutes of wall time.

    *All* of it -- the walk, the resolving and the matching -- runs off the
    event loop through one ``asyncio.to_thread``. The worker pool and the
    Plex liveness probe share that loop, and a stall long enough to make the
    probe miss its beat gets the pod killed.

    Both sides are ``resolve()``d inside that thread, so an ``asset_path``
    recorded through a symlink or with a ``..`` segment still matches the
    directory the walk reports rather than looking orphaned.

    ``assets_root`` itself is never a candidate, even when it directly
    contains files: it is not an orphan of itself, and treating it as one
    would make ``relative_to`` yield ``Path('.')`` and move the entire asset
    tree in a single ``shutil.move``.

    Dot-prefixed directories -- ``<assets_root>/.generated/`` in particular --
    are pruned from the walk entirely, not merely excluded from the result.
    ``collections/separator_art.py`` caches generated divider art there; the
    cache is service-owned, deliberately losable, and by construction never
    referenced by a ``renders`` row, so an unguarded walk would report it as
    orphaned forever. Pruning it out of ``dirnames`` also keeps it out of
    ``scanned``, which ``_implausible_orphan_count`` divides by -- counting a
    directory that can never be "kept" would understate every real orphan
    share.
    """
    rows = (await session.execute(select(Render.asset_path))).all()
    recorded = [path for (path,) in rows]

    def _scan() -> OrphanScan:
        root = assets_root.resolve()
        kept: set[Path] = set()
        for raw in recorded:
            kept.update(Path(raw).resolve().parents)

        orphaned: list[Path] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            if not filenames:
                continue
            directory = Path(dirpath).resolve()
            if directory == root:
                continue
            scanned += 1
            if directory not in kept:
                orphaned.append(directory)
        return OrphanScan(orphaned=orphaned, scanned=scanned)

    return await asyncio.to_thread(_scan)


def _free_destination(destination: Path) -> Path:
    """Return ``destination`` if nothing is there, else the same name with a
    numeric suffix.

    ``shutil.move`` onto an existing *directory* moves the source *inside*
    it, so a second cleanup pass over a re-created folder would silently
    produce ``backup/Movies/X/X``; a third raises ``shutil.Error``. A
    distinct destination keeps each pass's copy separate and inspectable.
    """
    if not destination.exists():
        return destination
    for suffix in itertools.count(1):
        candidate = destination.with_name(f"{destination.name}.{suffix}")
        if not candidate.exists():
            return candidate
    raise AssertionError("unreachable")  # pragma: no cover


def move_to_backup(paths: list[Path], assets_root: Path, backup_root: Path) -> MoveOutcome:
    """Move each directory in ``paths`` to ``backup_root``, preserving its path
    relative to ``assets_root``. Never deletes anything -- ``shutil.move`` is a
    rename (or copy-then-source-removal on the same call, not this module's).

    A directory that cannot be moved is logged and counted, not raised: the
    remaining orphans are still attempted and the caller still learns how
    many moved.
    """
    root = assets_root.resolve()
    moved = 0
    failed = 0
    for path in paths:
        try:
            resolved = path.resolve()
            if resolved == root or root not in resolved.parents:
                # Belt and braces against the worst outcome this module has:
                # ``assets_root.relative_to(assets_root)`` is ``Path('.')``,
                # ``backup_root / '.'`` is ``backup_root``, and that one move
                # relocates the entire asset tree.
                raise ValueError(f"{path} is not a directory inside {root}")
            relative = resolved.relative_to(root)
            destination = _free_destination(backup_root / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
        except (OSError, ValueError, shutil.Error):
            failed += 1
            logger.warning("cleanup: could not move %s to backup", path, exc_info=True)
        else:
            moved += 1
    return MoveOutcome(moved=moved, failed=failed)


# The share check needs a large enough sample to mean anything: "2 of 3
# directories are orphaned" is a normal small tree, not evidence of a
# misconfiguration. Below this many directories only the absolute cap applies.
SHARE_CHECK_MIN_DIRS = 20


def _implausible_orphan_count(orphaned: int, scanned: int, cleanup) -> str | None:
    """Return a refusal summary if this many orphans cannot be believed.

    Two caps, either of which trips. The absolute one catches a large tree
    that has gone wrong; the share one catches a small tree, where any
    absolute cap set high enough to be useful on the large tree would never
    fire. Both report the real numbers, because the operator's next question
    is always "how far off is it".
    """
    if orphaned and orphaned > cleanup.max_orphans:
        return (
            f"refused: {orphaned} of {scanned} asset directory(ies) look orphaned, "
            f"more than the safety cap of {cleanup.max_orphans}; this usually means "
            "assets_root, the mount or library_folders changed, or renders was "
            "only partly restored -- change nothing"
        )
    share = orphaned / scanned if scanned else 0.0
    if scanned >= SHARE_CHECK_MIN_DIRS and share > cleanup.max_orphan_share:
        return (
            f"refused: {orphaned} of {scanned} asset directory(ies) look orphaned "
            f"({share:.0%}), more than the safety cap of "
            f"{cleanup.max_orphan_share:.0%}; this usually means assets_root, the "
            "mount or library_folders changed, or renders was only partly "
            "restored -- change nothing"
        )
    return None


def make_cleanup_job(holder: ConfigHolder) -> Job:
    """Build the scheduled orphaned-asset cleanup job.

    Refuses to do anything if ``renders`` has no rows at all: an empty table
    would make ``find_orphaned_assets`` see every directory as orphaned, and
    a scheduled pass would then move the entire asset tree to the backup
    directory in one go. That check happens here, before
    ``find_orphaned_assets`` is ever called.

    An empty table is only the loudest version of that failure, though. Rows
    can exist and still describe a *different* tree than the one on disk --
    ``assets_root`` repointed, a volume remounted elsewhere, ``library_folders``
    toggled (which changes the whole naming scheme), a partial database
    restore. Every directory then looks orphaned and the empty-table guard
    passes happily. So the result is sanity-checked too: past
    ``cleanup.max_orphans`` directories, or past ``cleanup.max_orphan_share``
    of the tree, "almost everything is garbage" is read as "something is
    wrong" and the pass refuses with the actual numbers rather than treating
    it as a work order.

    Dry run by default (``config.cleanup.apply``), the same posture as
    ``badges.upload_to_plex`` and ``collections.apply_to_plex``. Read off the
    holder per run, so switching the dry run off takes effect on the next
    pass -- as does an edit to ``assets_root``, the safety caps or the
    cadence.
    """

    async def run(session: AsyncSession) -> str:
        config = holder.current
        any_render = (await session.execute(select(Render.id).limit(1))).first()
        if any_render is None:
            return (
                "refused: the renders table is empty, so every asset would "
                "look orphaned; change nothing"
            )

        assets_root = Path(config.assets_root)
        scan = await find_orphaned_assets(session, assets_root)
        orphaned, scanned = scan.orphaned, scan.scanned

        refusal = _implausible_orphan_count(len(orphaned), scanned, config.cleanup)
        if refusal is not None:
            return refusal

        if not config.cleanup.apply:
            return (
                f"dry run: {len(orphaned)} of {scanned} asset directory(ies) "
                "would move to backup"
            )

        backup_root = Path(config.backup_root)
        outcome = await asyncio.to_thread(move_to_backup, orphaned, assets_root, backup_root)
        summary = f"moved {outcome.moved} of {len(orphaned)} orphaned directory(ies) to backup"
        if outcome.failed:
            summary += f"; {outcome.failed} could not be moved (see the log)"
        return summary

    return Job(
        name="asset_cleanup",
        interval_seconds=lambda: holder.current.scheduler.cleanup_days * 24 * 3600,
        run=run,
    )
