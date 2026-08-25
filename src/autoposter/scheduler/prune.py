"""Retiring ``media_items`` rows the pipeline can never resolve again.

Roadmap row 129. An item that leaves Plex -- deleted, moved into a library the
operator excluded, or re-matched under a new rating key -- leaves its
``media_items`` row behind. Nothing removes it: ``_upsert_media_item``
(``render/pipeline.py``) is the table's only writer and it never deletes. Every
full pass then re-enqueues that row, the job fails ``plex.resolve``, retries
out and parks -- and the next full pass does it again, forever. This module
finds those rows and, once ``config.prune.apply`` is on, deletes them.

Hard delete, with an audit row each, rather than a soft-delete column. A
``deleted_at`` would be this schema's first and would have to be honoured by
every one of the dozen readers of ``media_items`` -- and each reader that
missed the filter would re-introduce exactly the bug this sweep exists to kill.
The record is kept in ``events_log`` instead (the ``collections/engine.py``
delete-audit precedent), and the row itself is reconstructible: if the item
ever comes back, ``process_item`` upserts it again.

"Gone" here means "the pipeline cannot resolve it", not "Plex does not have
it". Those differ in one important case: an item moved into
``plex.excluded_libraries`` is still on the server but is permanently
unreachable to every code path in this project, which is precisely the
condition that produced the parked-forever rows. See ``PlexClient.exists_many``.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import SHARE_CHECK_MIN_ITEMS, refuse_if_empty
from autoposter.config.holder import ConfigHolder
from autoposter.db.models import EventLog, MediaItem, Render
# Aliased: ``Job`` in this package means the scheduler's dataclass
# (``scheduler/core.py``), and the queue row of the same name would shadow it.
from autoposter.db.models import Job as QueuedJob
from autoposter.intake.arr import RenderIntent
from autoposter.scheduler.core import Job

logger = logging.getLogger(__name__)

# The events_log identity of a prune. Constants because the audit row is the
# only surviving record of a deleted item, and a typo in either would make a
# library's worth of them unfindable.
PRUNE_SOURCE = "prune"
PRUNE_EVENT = "media_item_pruned"


class PruneRefused(Exception):
    """A pass that could not be trusted to run at all.

    Raised rather than returned, the ``CollectionsPassFailed`` precedent
    (``scheduler/jobs.py``): ``last_status`` is decided by whether the job body
    raised (``scheduler/core.py``), and a scan that broke off part-way is a
    failure an operator must see as one -- not an ``ok`` run whose detail
    happens to say otherwise. The data-plausibility refusals stay ordinary
    return values, because those describe a pass that ran correctly and
    declined the work it found.

    The line is drawn at where the trouble was met, not at how bad it sounds.
    Raised for what interrupts the scan itself: unexpected, mid-walk, with the
    pass's own state no longer known. Returned for the unhealthy-Plex refusal
    as well, even though a dead server is worse news than an implausible
    count -- because that is the scheduler's ordinary wait condition, the same
    signal that pauses the workers, checked before any work begins. It
    describes a pass that never started, not one that broke.
    """


@dataclass(frozen=True)
class PruneCandidate:
    """One ``media_items`` row, as plain data.

    Read as columns rather than as an ORM object: a sweep holds every row in
    the library at once, and ``updated_at`` has to survive into the delete as a
    value the session cannot quietly refresh underneath it -- it is half the
    delete's key.
    """

    id: int
    rating_key: str
    kind: str
    library: str
    title: str
    parent_id: int | None
    tmdb_id: int | None
    tvdb_id: int | None
    imdb_id: str | None
    year: int | None
    season_number: int | None
    episode_number: int | None
    logo_upload_key: str | None
    updated_at: datetime


@dataclass(frozen=True)
class PruneScan:
    """What one sweep found.

    ``gone`` is every row that failed to resolve; ``prunable`` is the subset it
    is safe to delete, which is smaller whenever a descendant still resolves.
    ``total`` is the denominator the plausibility caps need -- "40 of 16,000"
    is ordinary churn, "15,900 of 16,000" means the server, not the library,
    changed.
    """

    prunable: list[PruneCandidate]
    gone: int
    held: int
    total: int

    @property
    def directories(self) -> int:
        """How many asset directories this prune would orphan -- see ``_directory_count``."""
        return _directory_count(self.prunable)


def _directory_count(candidates: list[PruneCandidate]) -> int:
    """How many asset directories these candidates would orphan.

    One per movie or show: seasons and episodes keep their artwork under the
    show's folder (``render/naming.py``), so they orphan nothing of their own.
    Shared by ``PruneScan.directories`` (the scan-time count) and the
    applied-pass recount in ``make_prune_job`` (which counts off what was
    actually deleted, not off the candidates offered).
    """
    return sum(1 for candidate in candidates if candidate.kind in ("movie", "show"))


def intent_for(candidate: PruneCandidate) -> RenderIntent:
    """The intent the pipeline would build for this row.

    Field for field what ``_enqueue_reprocess`` builds (``api/routes.py``),
    ``rating_key`` included -- that is what lets the probe try the stored Plex
    identity before falling back to a GUID search. The probe has to ask exactly
    what the pipeline asks, or the sweep would decide "gone" on a question the
    pipeline never poses.
    """
    return RenderIntent(
        kind=candidate.kind,
        title=candidate.title,
        tmdb_id=candidate.tmdb_id,
        tvdb_id=candidate.tvdb_id,
        imdb_id=candidate.imdb_id,
        year=candidate.year,
        season_number=candidate.season_number,
        episode_number=candidate.episode_number,
        rating_key=candidate.rating_key,
    )


def _ancestors(candidate: PruneCandidate, by_id: dict[int, PruneCandidate]) -> list[int]:
    """Every ancestor id of ``candidate``, nearest first.

    Guarded against a cycle: ``parent_id`` is a self-referential foreign key,
    so a bad row could in principle point at its own descendant, and a sweep
    that hung on it would take the scheduler's whole poll loop with it.
    """
    chain: list[int] = []
    seen = {candidate.id}
    current = candidate
    while current.parent_id is not None and current.parent_id not in seen:
        seen.add(current.parent_id)
        chain.append(current.parent_id)
        parent = by_id.get(current.parent_id)
        if parent is None:
            break
        current = parent
    return chain


async def find_prunable(session: AsyncSession, plex) -> PruneScan:
    """Every ``media_items`` row the pipeline can no longer resolve, safe to delete.

    One probe pass, then two rules over its result.

    The probe (``PlexClient.exists_many``) answers "does the pipeline still
    find this row's item". Nothing is caught: a probe that raises takes the
    whole sweep with it, because a server that answers nothing would otherwise
    report the entire library as gone.

    The first rule is the cascade guard. ``media_items.parent_id`` deletes
    ``ondelete="CASCADE"`` (``db/models.py``), so removing a show silently
    removes its seasons and episodes. A parent is therefore prunable only when
    it AND every descendant probed gone; one resolvable descendant holds every
    ancestor above it, and the held count is reported rather than swallowed. A
    gone child under a surviving parent is still prunable on its own -- the
    rule protects live rows from a cascade, not gone rows from themselves.

    The second is the ordering. The returned list is deepest-first, so an
    episode is deleted before its season and a season before its show. That is
    what gives every row its own audit row: were the show deleted first, the
    cascade would take the rest without one.
    """
    rows = (
        await session.execute(
            select(
                MediaItem.id,
                MediaItem.rating_key,
                MediaItem.kind,
                MediaItem.library,
                MediaItem.title,
                MediaItem.parent_id,
                MediaItem.tmdb_id,
                MediaItem.tvdb_id,
                MediaItem.imdb_id,
                MediaItem.year,
                MediaItem.season_number,
                MediaItem.episode_number,
                MediaItem.logo_upload_key,
                MediaItem.updated_at,
            ).order_by(MediaItem.id)
        )
    ).all()
    candidates = [
        PruneCandidate(
            id=row.id,
            rating_key=row.rating_key,
            kind=row.kind,
            library=row.library,
            title=row.title,
            parent_id=row.parent_id,
            tmdb_id=row.tmdb_id,
            tvdb_id=row.tvdb_id,
            imdb_id=row.imdb_id,
            year=row.year,
            season_number=row.season_number,
            episode_number=row.episode_number,
            logo_upload_key=row.logo_upload_key,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    if not candidates:
        return PruneScan(prunable=[], gone=0, held=0, total=0)

    resolved_flags = await plex.exists_many([intent_for(c) for c in candidates])
    by_id = {candidate.id: candidate for candidate in candidates}
    # strict=True: a mismatched flags list would silently drop a live
    # descendant from the held-computation and permit exactly the
    # over-deletion the cascade guard prevents. This turns that into a loud
    # ValueError the job layer treats as a probe failure.
    gone = {
        candidate.id
        for candidate, resolved in zip(candidates, resolved_flags, strict=True)
        if not resolved
    }

    held: set[int] = set()
    for candidate, resolved in zip(candidates, resolved_flags, strict=True):
        if resolved:
            held.update(_ancestors(candidate, by_id))

    prunable = [c for c in candidates if c.id in gone and c.id not in held]
    prunable.sort(key=lambda c: len(_ancestors(c, by_id)), reverse=True)
    return PruneScan(
        prunable=prunable,
        gone=len(gone),
        held=len(gone & held),
        total=len(candidates),
    )


@dataclass(frozen=True)
class RetireOutcome:
    """What one applied pass actually removed.

    ``pruned`` is the rating keys of rows that were really deleted, not the
    candidates offered: the job disposal downstream must key off what happened,
    or it would dismiss the queued work of a row that survived. ``skipped``
    counts rows that changed under the pass and were therefore left alone.
    """

    pruned: list[str]
    skipped: int


def implausible_prune_count(prunable: int, total: int, prune) -> str | None:
    """Return a refusal summary if this many prunable rows cannot be believed.

    The ``_implausible_orphan_count`` shape (``scheduler/jobs.py``) with the
    causes that apply to this sweep. Two caps, either of which trips: the
    absolute one catches a large library gone wrong in bulk, the share one
    catches a small library where any absolute cap high enough to be useful on
    the large one would never fire. Both report the real numbers, because the
    operator's next question is always "how far off is it".

    This is a second line, not the first: the unhealthy-Plex guard catches a
    server that does not answer. These caps catch the worse case -- one that
    answers, wrongly, because it was rebuilt, is still loading its sections, or
    lost the library the rows belong to.
    """
    if prunable and prunable > prune.max_prunes:
        return (
            f"refused: {prunable} of {total} media_items row(s) look unresolvable, "
            f"more than the safety cap of {prune.max_prunes}; this usually means "
            "Plex was rebuilt, a library was renamed or newly excluded, or the "
            "server answered from a partly-loaded state -- change nothing"
        )
    share = prunable / total if total else 0.0
    if total >= SHARE_CHECK_MIN_ITEMS and share > prune.max_prune_share:
        return (
            f"refused: {prunable} of {total} media_items row(s) look unresolvable "
            f"({share:.0%}), more than the safety cap of "
            f"{prune.max_prune_share:.0%}; this usually means Plex was rebuilt, a "
            "library was renamed or newly excluded, or the server answered from a "
            "partly-loaded state -- change nothing"
        )
    return None


async def retire(session: AsyncSession, candidates: list[PruneCandidate]) -> RetireOutcome:
    """Delete each candidate, with one flushed audit row per delete.

    Two things make this safe to run beside a live worker pool.

    **The delete is keyed on ``(id, updated_at)``, not on the id alone.** The
    table's only writer, ``_upsert_media_item`` (``render/pipeline.py``),
    stamps ``updated_at=now()`` on its ON CONFLICT arm, so a row a worker
    re-resolved and re-upserted between this pass's probe and this delete no
    longer matches and survives untouched. The probe's answer is only ever as
    good as the row it was asked about, and a row that changed since is
    counted as skipped rather than deleted on the strength of a stale
    observation. (The residual window -- two writes landing in the same
    database microsecond -- is not reachable in practice: the value being
    compared was written by a transaction that had already committed before
    this pass read it.)

    **A row that survives its own delete shields every ancestor above it in
    this pass.** ``media_items.parent_id`` cascades, so the guard alone would
    protect a leaf and then lose it anyway: candidates arrive deepest-first, so
    a skipped child is followed by its still-gone parent, whose own
    ``updated_at`` the child's upsert never touched -- that delete matches, and
    the cascade takes the live child with it, unaudited and absent from
    ``pruned``. So a skip propagates upward through ``blocked``: this is the
    same rule ``find_prunable`` applies at probe time (one resolvable
    descendant holds every ancestor), applied again at delete time, where the
    evidence is a row that changed rather than an item that resolved.

    A row inserted under a gone parent between the candidate SELECT and that
    parent's delete sits outside both guards -- it was never a candidate, so
    nothing shields the parent, and the cascade removes it with no audit row
    of its own -- but no live row is lost (such a row is itself unresolvable
    and rating keys are not reused), only its audit, and the next pass would
    have pruned it anyway.

    **The audit is flushed per row, before the next row's delete**, the way
    ``collections/engine.py`` flushes its ``collection_deleted`` rows. The
    flush is an ordering, not a commit -- audits and deletes commit or roll
    back together -- and what it buys is that the audit can never be lost while
    its delete survives: the ``EventLog`` is on the connection before the next
    candidate's work begins. The payload carries the row's whole identity,
    ``logo_upload_key`` included -- a pruned item's uploaded clearlogo can
    never be reverted again, because the marker that made the revert safe dies
    with the row, so the key is written down rather than silently lost.

    Candidates must arrive deepest-first, as ``find_prunable`` returns them:
    a parent deleted first would take its descendants before they get audit
    rows of their own, and ``blocked`` would never see the child at all.
    """
    pruned: list[str] = []
    skipped = 0
    blocked: set[int] = set()  # ids whose deletion a skipped descendant forbids
    for candidate in candidates:
        # Checked before the render_count query so an inherited block costs no
        # queries at all; a guard miss still pays one, because the count has to
        # be taken before the delete or the cascade would empty it first.
        if candidate.id in blocked:
            logger.info(
                "prune: %s (%s) held by a descendant that changed under the pass",
                candidate.rating_key, candidate.kind,
            )
            if candidate.parent_id is not None:
                blocked.add(candidate.parent_id)
            skipped += 1
            continue
        render_count = (
            await session.execute(
                select(func.count())
                .select_from(Render)
                .where(Render.item_id == candidate.id)
            )
        ).scalar_one()
        # synchronize_session=False because nothing here holds ORM MediaItem
        # objects -- the sweep reads columns -- and the default strategies have
        # to guess at how to reconcile a criteria DELETE with an identity map
        # that has nothing in it to reconcile.
        removed = (
            await session.execute(
                delete(MediaItem)
                .where(MediaItem.id == candidate.id)
                .where(MediaItem.updated_at == candidate.updated_at)
                .returning(MediaItem.id)
                .execution_options(synchronize_session=False)
            )
        ).first()
        if removed is None:
            logger.info(
                "prune: %s (%s) changed under the pass; left alone",
                candidate.rating_key, candidate.kind,
            )
            if candidate.parent_id is not None:
                blocked.add(candidate.parent_id)
            skipped += 1
            continue
        session.add(EventLog(
            source=PRUNE_SOURCE,
            event_type=PRUNE_EVENT,
            payload={
                "media_item_id": candidate.id,
                "rating_key": candidate.rating_key,
                "kind": candidate.kind,
                "library": candidate.library,
                "title": candidate.title,
                "year": candidate.year,
                "season_number": candidate.season_number,
                "episode_number": candidate.episode_number,
                "tmdb_id": candidate.tmdb_id,
                "tvdb_id": candidate.tvdb_id,
                "imdb_id": candidate.imdb_id,
                "render_count": render_count,
                "logo_upload_key": candidate.logo_upload_key,
            },
            outcome="pruned: no Plex item resolves for this row",
        ))
        await session.flush()
        pruned.append(candidate.rating_key)
    return RetireOutcome(pruned=pruned, skipped=skipped)


async def dismiss_jobs_for(session: AsyncSession, rating_keys: list[str]) -> int:
    """Dismiss the pending and parked jobs queued for rows that were pruned.

    A parked job for a pruned row is pure Failures-page noise, and a pending
    one would park by construction -- nothing can resolve it any more. Jobs
    carry no foreign key to ``media_items`` (``db/models.py``), so they are
    found the only way the payload allows: by the ``rating_key`` the
    ``RenderIntent`` carries. Payloads written before that field existed carry
    none; those are out of scope, already parked, and dismissible by hand.

    ``running`` jobs are deliberately left alone. A claimed job is never
    interrupted anywhere in this project (``api/jobs.py``), and one running
    against a pruned row simply parks itself for the next applied pass to
    dismiss.

    ``with_for_update(skip_locked=True)`` mirrors the queue's own claim
    (``queue/jobs.py``'s ``SELECT ... FOR UPDATE SKIP LOCKED``): while this
    transaction holds a row a worker skips it rather than claiming it
    underneath the flip, and a row a worker already holds is skipped here
    rather than blocking the sweep behind it -- that row is ``running``, which
    this does not touch anyway.
    """
    if not rating_keys:
        return 0
    jobs = (
        await session.execute(
            select(QueuedJob)
            .where(QueuedJob.kind == "process_item")
            .where(QueuedJob.state.in_(("pending", "parked")))
            .where(QueuedJob.payload["rating_key"].astext.in_(rating_keys))
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for job in jobs:
        job.state = "dismissed"
    await session.flush()
    return len(jobs)


def cleanup_cap_warning(directories: int, cleanup) -> str:
    """The tail of the summary's file sentence when a prune would disarm the
    asset sweep.

    ``asset_cleanup`` refuses ENTIRELY past ``cleanup.max_orphans``
    (``scheduler/jobs.py``), so a large prune does not merely make work for it:
    it can stop that pass doing anything at all, including the orphans it would
    otherwise have handled. Said in the summary rather than acted on -- this
    module handles no files by design, because the cleanup sweep moves them to
    ``backup_root`` and never deletes, which is safer than anything a pruner
    would add.

    Only the absolute cap is checked, and the caveat is that the other one
    cannot be: ``cleanup.max_orphan_share`` is measured against the directory
    count of the whole asset tree, which only the cleanup scan ever has. So a
    prune under ``max_orphans`` can still leave the next sweep over its share
    cap, and nothing here can say so.
    """
    if directories > cleanup.max_orphans:
        return (
            f" -- WARNING: that is more than cleanup.max_orphans "
            f"({cleanup.max_orphans}), so the next asset_cleanup pass will refuse "
            "entirely and move nothing"
        )
    return ""


def make_prune_job(
    holder: ConfigHolder,
    plex_factory: Callable[[], object],
    is_healthy: Callable[[], bool],
) -> Job:
    """Build the scheduled ``media_items`` prune job.

    The ``make_cleanup_job`` shape: scheduled, dry run by default, one summary
    string per run, hand-triggerable once the name is in
    ``SCHEDULED_JOB_NAMES``. Not a Plex-writing mode, because this writes only
    to the database -- the mode fence machinery (``WorkerPause``, ``drain``,
    the process-wide lock) exists to keep two writers off Plex and buys this
    nothing, and a walk of every rating key in the library is the wrong thing
    to hold an HTTP request open for.

    ``plex_factory`` is a zero-argument callable returning a connected
    ``PlexClient``. It runs through ``asyncio.to_thread`` because connecting
    blocks -- the same contract ``make_collections_job``'s ``server_factory``
    has, and for the same reason: this job shares the event loop with the
    worker pool and the Plex liveness probe.

    ``is_healthy`` is ``PlexHealth.healthy``, read per run. For every other
    consumer an unhealthy Plex means "wait"; for this one it means "refuse",
    and that difference is the whole safety story. A server that answers
    nothing makes EVERY row read as gone, so a pruner running during an outage
    would delete the library. It is checked first, before the table is read and
    before a client is built.

    Config comes off ``holder`` per run -- ``prune.apply``, both caps, the
    cleanup caps the warning is measured against, and this job's own cadence --
    so every one of them is live.
    """

    async def run(session: AsyncSession) -> str:
        config = holder.current
        if not is_healthy():
            return (
                "refused: Plex is unhealthy, so every row would look gone; "
                "change nothing"
            )

        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            return empty

        try:
            # The factory is inside the try because it connects: a refused
            # connection, a rejected token or a plexapi BadRequest all raise
            # here, and every one of those messages carries the server address.
            plex = await asyncio.to_thread(plex_factory)
            scan = await find_prunable(session, plex)
        except Exception as exc:
            # The class name only, never str(exc) and never a URL: a Plex
            # error's message carries the server address and sometimes the
            # token, and this string is stored in scheduled_runs.last_detail,
            # which the dashboard renders. The full traceback goes to the log
            # for whoever can read it.
            logger.warning("plex_prune: scanning for prunable rows failed", exc_info=True)
            raise PruneRefused(
                f"refused: scanning for prunable rows failed "
                f"({type(exc).__name__}), so no row's absence can be trusted; "
                "change nothing"
            ) from None

        refusal = implausible_prune_count(len(scan.prunable), scan.total, config.prune)
        if refusal is not None:
            return refusal

        held = f"{scan.held} row(s) held because a descendant still resolves"

        def files_sentence(directories: int) -> str:
            return (
                f"{directories} asset director(ies) orphan for asset_cleanup to "
                f"handle{cleanup_cap_warning(directories, config.cleanup)}"
            )

        if not config.prune.apply:
            return (
                f"dry run: {len(scan.prunable)} of {scan.total} media_items row(s) "
                f"would be pruned; {held}; {files_sentence(scan.directories)}"
            )

        outcome = await retire(session, scan.prunable)
        dismissed = await dismiss_jobs_for(session, outcome.pruned)
        # Counted off what was actually deleted, not off the candidates:
        # ``retire`` leaves any row that changed under the pass, and those
        # orphan nothing. Reporting the candidate total would claim directories
        # no delete created and could fire the cleanup-cap warning over a
        # threshold this prune never crossed. The dry run has no such
        # distinction to make -- there, the candidates are the whole story.
        pruned_keys = set(outcome.pruned)
        directories = _directory_count(
            [c for c in scan.prunable if c.rating_key in pruned_keys]
        )
        summary = (
            f"pruned {len(outcome.pruned)} of {scan.total} media_items row(s); "
            f"{held}; dismissed {dismissed} queued job(s); "
            f"{files_sentence(directories)}"
        )
        if outcome.skipped:
            summary += (
                f"; {outcome.skipped} row(s) changed under the pass or were "
                "shielded by one that did, and were left"
            )
        return summary

    return Job(
        name="plex_prune",
        interval_seconds=lambda: holder.current.scheduler.prune_days * 24 * 3600,
        run=run,
    )
