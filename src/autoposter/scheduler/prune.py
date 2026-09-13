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
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import SHARE_CHECK_MIN_ITEMS, refuse_if_empty
from autoposter.config.holder import ConfigHolder
from autoposter.db.models import EventLog, MediaItem, MediaItemServerRef, Render
# Aliased: ``Job`` in this package means the scheduler's dataclass
# (``scheduler/core.py``), and the queue row of the same name would shadow it.
from autoposter.db.models import Job as QueuedJob
from autoposter.db.refs import native_ids as native_ids_for, refs_for_items
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

    # Read by scheduler/core.py's failure branch (roadmap row 209): every
    # message this class is raised with is hand-built served-safe -- the
    # probe-failure site names the exception class only, never str(exc) and
    # never a URL (see the comment at that raise) -- so the scheduler serves
    # it verbatim instead of narrowing it to "PruneRefused".
    served_detail = True


@dataclass(frozen=True)
class PruneCandidate:
    """One ``media_items`` row, as plain data.

    Read as columns rather than as an ORM object: a sweep holds every row in
    the library at once, and ``updated_at`` has to survive into the delete as a
    value the session cannot quietly refresh underneath it -- it is half the
    delete's key.

    ``refs`` is a SNAPSHOT of every server ref this row had at SCAN time, not
    a live read -- Task 19's ``_prune_stale_refs`` deletes an individually
    stale ref (a survivor whose OTHER server ref still resolves) before
    ``retire`` ever runs, and a row going away entirely has every ref of its
    own removed by the cascade the moment ``retire`` deletes it. Either way, a
    live ``refs_for`` read inside ``retire`` would see less than the row
    actually had; the audit is written from this snapshot instead.
    """

    id: int
    native_id: str | None
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
    refs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PruneScan:
    """What one sweep found.

    ``gone`` is every row that failed to resolve; ``prunable`` is the subset it
    is safe to delete, which is smaller whenever a descendant still resolves.
    ``total`` is the denominator the plausibility caps need -- "40 of 16,000"
    is ordinary churn, "15,900 of 16,000" means the server, not the library,
    changed.

    ``excluded`` is how many of ``prunable`` are there because their library
    is in ``plex.excluded_libraries``, rather than because Plex lost them.
    Reported separately because the two are different news: one is churn, the
    other is the operator's own configuration catching up with rows that
    predate it, and reading the second as the first would look like data loss.
    """

    prunable: list[PruneCandidate]
    gone: int
    held: int
    total: int
    excluded: int = 0
    # Fix round 1, C1: every ref `_scan_stale_refs` found doomed, collected
    # but NOT deleted here -- a scheduled DRY RUN must never write, and the
    # plausibility caps have to see this population before anything is
    # removed. `make_prune_job`'s apply branch deletes these, guarded on
    # (id, updated_at), after `implausible_prune_count` passes.
    stale_refs: "list[StaleRef]" = field(default_factory=list)

    @property
    def directories(self) -> int:
        """How many asset directories this prune would orphan -- see ``_directory_count``."""
        return _directory_count(self.prunable)


@dataclass(frozen=True)
class StaleRef:
    """One ``media_item_server_refs`` row a server no longer resolves (or
    whose library that server now excludes), found by ``_scan_stale_refs``.

    Deleted, guarded on ``(id, updated_at)``, only in the apply branch --
    never during the scan itself (C1). ``item_id`` is what lets the caller
    skip a ref whose item is ALSO being retired this pass: the whole-row
    cascade removes it, so a separate delete would be redundant.
    """

    id: int
    item_id: int
    server: str
    updated_at: datetime


def _directory_count(candidates: list[PruneCandidate]) -> int:
    """How many asset directories these candidates would orphan.

    One per movie or show: seasons and episodes keep their artwork under the
    show's folder (``render/naming.py``), so they orphan nothing of their own.
    Shared by ``PruneScan.directories`` (the scan-time count) and the
    applied-pass recount in ``make_prune_job`` (which counts off what was
    actually deleted, not off the candidates offered).
    """
    return sum(1 for candidate in candidates if candidate.kind in ("movie", "show"))


_UNSET = object()


def intent_for(candidate, *, server: str = "plex", native_id=_UNSET) -> RenderIntent:
    """The intent the pipeline would build for this row.

    Field for field what ``_enqueue_reprocess`` builds (``api/routes.py``),
    the stored native id included -- that is what lets the probe try the
    stored identity before falling back to a GUID search. The probe has to ask
    exactly what the pipeline asks, or the sweep would decide "gone" on a
    question the pipeline never poses.

    ``server``/``native_id`` default to ``"plex"``/``candidate.native_id``,
    which is every existing direct caller's shape (a ``PruneCandidate``,
    unchanged). ``_scan_stale_refs`` (Task 19 fix round 1, reusing this
    rather than a second inlined copy) passes both explicitly, once per
    server, against a plain SQL row that carries the same field names but no
    ``native_id`` attribute of its own -- hence the sentinel default rather
    than reading ``candidate.native_id`` unconditionally.
    """
    if native_id is _UNSET:
        native_id = candidate.native_id if server == "plex" else None
    return RenderIntent(
        kind=candidate.kind,
        title=candidate.title,
        tmdb_id=candidate.tmdb_id,
        tvdb_id=candidate.tvdb_id,
        imdb_id=candidate.imdb_id,
        year=candidate.year,
        season_number=candidate.season_number,
        episode_number=candidate.episode_number,
        refs={server: native_id} if native_id else {},
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


async def _scan_stale_refs(
    session: AsyncSession, servers, excluded: dict[str, frozenset[str]],
) -> list[StaleRef]:
    """Find every ref a server no longer resolves (Task 19 ruling 6, fix
    round 1 C1; spec §5.5): per server in ``servers``, ``exists_many`` over
    the intents of every row that HAS a ref on that server -- built from
    THAT ref's own native id, never another server's, via ``intent_for``.
    ``False``, or a row whose own ``library`` that server excludes (the
    ``_search_sync`` GUID-fallback hazard ``find_prunable`` used to fold in
    directly, see its old docstring), makes the ref a candidate.

    COLLECTS ONLY -- nothing is deleted here. A scheduled dry run
    (``config.prune.apply`` off) must never write, and the plausibility caps
    (``implausible_prune_count``) have to see this population before
    anything is removed; both were violated when this function deleted
    inline. The caller (``make_prune_job``'s apply branch) is what deletes
    the returned refs, guarded on ``(id, updated_at)`` -- the same optimistic
    guard ``retire`` applies to the ``media_items`` delete, so a ref a worker
    re-upserted (``upsert_server_ref``'s own ON CONFLICT arm bumps this same
    column) between this scan and that delete survives rather than being
    removed on the strength of an observation that stopped being true
    mid-pass.

    Every server's rows are read, and the whole read transaction released
    (``session.rollback()``), BEFORE any server is asked to resolve --
    ``find_prunable``'s own note explains why (13-minute idle transaction,
    measured) and the same cost applies per server here. Collecting rather
    than deleting does not change this: no write happens between the reads
    and the probes either way.

    Nothing is caught: a server that raises takes the whole sweep with it,
    same as the old single-probe version -- a server that answers nothing
    would otherwise report every one of its refs gone.
    """
    rows_by_server: dict[str, list] = {}
    for name in servers:
        rows = (
            await session.execute(
                select(
                    MediaItemServerRef.id, MediaItemServerRef.native_id,
                    MediaItemServerRef.updated_at,
                    # Fix round 3, M1: the ref's OWN library -- the name that
                    # server knows this item by (spec §4.1) -- because it is
                    # that server's `excluded_libraries` it is compared
                    # against below. `MediaItem.library` is the identity
                    # server's name for it, which is a different string on
                    # every other server.
                    MediaItemServerRef.library,
                    MediaItem.id.label("item_id"), MediaItem.kind,
                    MediaItem.title, MediaItem.tmdb_id, MediaItem.tvdb_id, MediaItem.imdb_id,
                    MediaItem.year, MediaItem.season_number, MediaItem.episode_number,
                )
                .join(MediaItem, MediaItem.id == MediaItemServerRef.item_id)
                .where(MediaItemServerRef.server == name)
            )
        ).all()
        if rows:
            rows_by_server[name] = rows
    if not rows_by_server:
        return []

    await session.rollback()

    stale: list[StaleRef] = []
    for name, rows in rows_by_server.items():
        server = servers[name]
        server_excluded = excluded.get(name, frozenset())
        intents = [
            intent_for(row, server=name, native_id=row.native_id) for row in rows
        ]
        # strict=True: a mismatched flags list must be loud, not silently
        # drop a row from consideration -- the same discipline the old
        # single-probe version applied.
        resolved_flags = await server.exists_many(intents)
        for row, ok in zip(rows, resolved_flags, strict=True):
            if ok and row.library not in server_excluded:
                continue
            stale.append(StaleRef(
                id=row.id, item_id=row.item_id, server=name, updated_at=row.updated_at,
            ))
    return stale


async def _retire_stale_refs(
    session: AsyncSession, stale_refs: list[StaleRef], pruned_item_ids: set[int],
) -> int:
    """Delete the refs ``_scan_stale_refs`` found doomed (fix round 1 C1),
    called ONLY from the apply branch, after ``implausible_prune_count`` has
    passed and ``retire`` has run.

    A ref whose item is ALSO in ``pruned_item_ids`` is skipped: the
    whole-row cascade (``media_items.parent_id``/refs both
    ``ondelete="CASCADE"``) already removed it along with the row, and a
    second delete here would be redundant (harmless, but redundant -- and
    counting it would double-report work this pass did not do twice).
    Guarded on ``(id, updated_at)``, same as ``retire``'s own delete: a ref a
    worker re-upserted between the scan and this delete no longer matches
    and survives.
    """
    retired = 0
    for ref in stale_refs:
        if ref.item_id in pruned_item_ids:
            continue
        result = await session.execute(
            delete(MediaItemServerRef)
            .where(MediaItemServerRef.id == ref.id)
            .where(MediaItemServerRef.updated_at == ref.updated_at)
        )
        retired += result.rowcount or 0
    return retired


async def find_prunable(
    session: AsyncSession, servers, excluded: dict[str, frozenset[str]] | None = None,
) -> PruneScan:
    """Every ``media_items`` row the pipeline can no longer resolve on ANY
    configured server, safe to delete.

    Task 19 ruling 6 turns the old single-Plex probe into one per server
    (``_prune_stale_refs``, above): a row is reachable when it still holds a
    ref on at least one of them, whatever server that is -- Plex artwork is
    never held to Jellyfin's absence and vice versa. ``excluded`` maps server
    name to that server's own excluded libraries (``plex.excluded_libraries``,
    ``jellyfin.excluded_libraries``); a missing key means that server excludes
    nothing.

    That reachability answer is folded in ONCE, above both rules below, so
    the cascade guard and the gone set cannot disagree about the same row: a
    row holds its ancestors when it will itself survive the sweep, not merely
    when some server still resolved it before exclusion was applied.

    The first rule is the cascade guard. ``media_items.parent_id`` deletes
    ``ondelete="CASCADE"`` (``db/models.py``), so removing a show silently
    removes its seasons and episodes. A parent is therefore prunable only when
    it AND every descendant is unreachable; one descendant that survives this
    sweep holds every ancestor above it, and the held count is reported rather
    than swallowed. A gone child under a surviving parent is still prunable on
    its own -- the rule protects live rows from a cascade, not gone rows from
    themselves.

    The second is the ordering. The returned list is deepest-first, so an
    episode is deleted before its season and a season before its show. That is
    what gives every row its own audit row: were the show deleted first, the
    cascade would take the rest without one.
    """
    excluded = excluded or {}
    rows = (
        await session.execute(
            select(
                MediaItem.id,
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
    if not rows:
        return PruneScan(prunable=[], gone=0, held=0, total=0, excluded=0)
    # The Plex native id specifically, unchanged: `intent_for` (retry
    # resolution) and `dismiss_jobs_for` (job-payload matching) both still key
    # on it, whatever else this row has a ref on.
    plex_ids = await native_ids_for(session, [row.id for row in rows], "plex")
    # A snapshot, not a live read: a row going away entirely has every ref
    # removed by the cascade the instant `retire` deletes it, and a
    # surviving row's individually-stale ref is deleted by the apply
    # branch's own `_retire_stale_refs` call -- either way, reading
    # `refs_for` INSIDE `retire` would report less than the row actually had
    # at scan time (see PruneCandidate's own docstring).
    refs_by_item = await refs_for_items(session, [row.id for row in rows])
    candidates = [
        PruneCandidate(
            id=row.id,
            native_id=plex_ids.get(row.id),
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
            refs=refs_by_item.get(row.id, {}),
        )
        for row in rows
    ]

    # The reads above opened a transaction the per-server walk below would
    # otherwise hold idle for its whole duration -- 13 minutes on a
    # 15,794-row library, measured, pinning a pooled connection and the
    # vacuum horizon for nothing. PruneCandidate is a frozen dataclass and
    # retire() re-reads every row anyway, deleting on (id, updated_at)
    # precisely so a row that changed under the pass survives.
    await session.rollback()

    stale_refs = await _scan_stale_refs(session, servers, excluded)

    # Reachable means "still holds a ref on at least one server that
    # `_scan_stale_refs` did NOT find stale" -- computed against the CURRENT
    # ref rows (nothing has been deleted; C1), so a ref on a server this
    # pass never touched at all (not in `servers`) still counts as live,
    # same as before.
    #
    # Fix round 2, NB1: matched on (id, updated_at), not id alone. A ref
    # re-upserted between the scan above and this read -- `retry_pending_
    # deliveries` calls `upsert_server_ref` on its own, unrelated to this
    # sweep, and bumps only the ref's own `updated_at` -- must not still
    # read as the same stale observation; the id is unchanged but the row
    # is not the one that was found gone.
    stale_ref_keys = {(ref.id, ref.updated_at) for ref in stale_refs}
    all_refs = (
        await session.execute(
            select(MediaItemServerRef.item_id, MediaItemServerRef.id, MediaItemServerRef.updated_at)
        )
    ).all()
    remaining_ids = {
        item_id
        for item_id, ref_id, updated_at in all_refs
        if (ref_id, updated_at) not in stale_ref_keys
    }
    by_id = {candidate.id: candidate for candidate in candidates}
    reachable = [candidate.id in remaining_ids for candidate in candidates]
    gone = {
        candidate.id
        for candidate, ok in zip(candidates, reachable, strict=True)
        if not ok
    }

    held: set[int] = set()
    for candidate, ok in zip(candidates, reachable, strict=True):
        if ok:
            held.update(_ancestors(candidate, by_id))

    prunable = [c for c in candidates if c.id in gone and c.id not in held]
    prunable.sort(key=lambda c: len(_ancestors(c, by_id)), reverse=True)
    all_excluded: frozenset[str] = frozenset().union(*excluded.values()) if excluded else frozenset()
    return PruneScan(
        prunable=prunable,
        gone=len(gone),
        held=len(gone & held),
        total=len(candidates),
        excluded=sum(1 for c in prunable if c.library in all_excluded),
        stale_refs=stale_refs,
    )


@dataclass(frozen=True)
class RetireOutcome:
    """What one applied pass actually removed.

    ``pruned`` is the ``media_items.id`` of every row really deleted, not the
    candidates offered: the job disposal and the directory count downstream
    must key off what happened, or they would speak for a row that survived.

    The id, not the Plex native id, because a candidate can have NO native id
    at all -- a row with no ``media_item_server_refs`` entry for Plex reads
    back as ``native_id=None`` (``find_prunable``), and a list holding a
    ``None`` matched every other ref-less candidate when the caller filtered
    on it. ``media_items.id`` is the one identifier every row is guaranteed
    to have.

    ``skipped`` counts rows that changed under the pass and were therefore
    left alone.
    """

    pruned: list[int]
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
    pruned: list[int] = []
    skipped = 0
    blocked: set[int] = set()  # ids whose deletion a skipped descendant forbids
    for candidate in candidates:
        # Checked before the render_count query so an inherited block costs no
        # queries at all; a guard miss still pays one, because the count has to
        # be taken before the delete or the cascade would empty it first.
        if candidate.id in blocked:
            logger.info(
                "prune: %s (%s) held by a descendant that changed under the pass",
                candidate.native_id, candidate.kind,
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
        # `candidate.refs`, the scan-time snapshot -- never a live `refs_for`
        # read here: Task 19's `_prune_stale_refs` (the caller's own prune
        # pass, ahead of `retire`) may already have deleted this row's only
        # stale ref before this point, on top of the FK cascade this delete
        # itself triggers, so a live read at either seam would report less
        # than the row actually had. See PruneCandidate's own docstring.
        refs = candidate.refs
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
                candidate.native_id, candidate.kind,
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
                # Legacy key, kept for any reader still watching for it
                # (spec §4.5); "refs" is the full per-server picture.
                "rating_key": candidate.native_id,
                "refs": refs,
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
        pruned.append(candidate.id)
    return RetireOutcome(pruned=pruned, skipped=skipped)


async def dismiss_jobs_for(session: AsyncSession, native_ids: list[str]) -> int:
    """Dismiss the pending, deferred and parked jobs queued for rows that were pruned.

    A parked job for a pruned row is pure Failures-page noise, and a pending
    one would park by construction -- nothing can resolve it any more. A
    deferred one is worse than either: its horizon has no end, so a job left
    waiting for an item that has been pruned would look for it every six hours
    for the life of the deployment. Jobs
    carry no foreign key to ``media_items`` (``db/models.py``), so they are
    found the only way the payload allows: by the Plex native id the
    ``RenderIntent`` carries, matched against BOTH ``payload["refs"]["plex"]``
    and the legacy ``payload["rating_key"]`` a job queued before ``refs``
    existed still carries. That is harmless rather than a gap: webhook-born
    payloads carry no Plex id by design (``intake/arr.py`` -- Sonarr and
    Radarr know nothing about Plex), and an item Plex never held has no
    ``media_items`` row for this sweep to have been retiring in the first
    place. Such a job -- deferred or otherwise -- ends only by operator
    Cancel.

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
    if not native_ids:
        return 0
    jobs = (
        await session.execute(
            select(QueuedJob)
            .where(QueuedJob.kind == "process_item")
            .where(QueuedJob.state.in_(("pending", "deferred", "parked")))
            .where(or_(
                QueuedJob.payload["refs"]["plex"].astext.in_(native_ids),
                QueuedJob.payload["rating_key"].astext.in_(native_ids),
            ))
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
    servers_factory: Callable[[], object],
    unhealthy_servers: Callable[[], list[str]],
) -> Job:
    """Build the scheduled ``media_items`` prune job.

    The ``make_cleanup_job`` shape: scheduled, dry run by default, one summary
    string per run, hand-triggerable once the name is in
    ``SCHEDULED_JOB_NAMES``. Not a Plex-writing mode, because this writes only
    to the database -- the mode fence machinery (``WorkerPause``, ``drain``,
    the process-wide lock) exists to keep two writers off Plex and buys this
    nothing, and a walk of every rating key in the library is the wrong thing
    to hold an HTTP request open for.

    ``servers_factory`` is a zero-argument callable returning a connected
    ``Servers`` registry (Task 19: every configured server, not one). It runs
    through ``asyncio.to_thread`` because connecting blocks -- the same
    contract ``make_collections_job``'s ``server_factory`` has, and for the
    same reason: this job shares the event loop with the worker pool and the
    liveness probes.

    ``unhealthy_servers`` names the configured servers whose liveness probe
    is currently unhappy, read per run (fix round 3, M2: a list rather than
    the old ``is_healthy`` boolean, so the refusal can say WHICH server is
    out -- on a dual deployment a Jellyfin outage used to produce a sentence
    naming Plex). For every other consumer an unhealthy server means "wait";
    for this one it means "refuse", and that difference is the whole safety
    story. A server that answers nothing makes EVERY row read as gone, so a
    pruner running during an outage would delete the library. It is checked
    first, before the table is read and before a client is built.

    Config comes off ``holder`` per run -- ``prune.apply``, both caps, the
    cleanup caps the warning is measured against, and this job's own cadence --
    so every one of them is live.

    ``excluded_libraries`` is read off the holder here too, per server, and is
    what makes an excluded library's rows retirable even while its server
    still answers for them -- these sections are frozen for the client the
    WORKERS hold, not for this job's, which is rebuilt per run (``app.py``).
    """

    async def run(session: AsyncSession) -> str:
        config = holder.current
        unhealthy = unhealthy_servers()
        if unhealthy:
            return (
                f"refused: {', '.join(unhealthy)} "
                f"{'is' if len(unhealthy) == 1 else 'are'} unhealthy, so every "
                "row would look gone; change nothing"
            )

        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            return empty

        try:
            # The factory is inside the try because it connects: a refused
            # connection, a rejected token or a plexapi BadRequest all raise
            # here, and every one of those messages carries the server address.
            servers = await asyncio.to_thread(servers_factory)
            # Read off the holder like everything else this job uses, so an
            # edited exclusion list reaches the sweep on its next run. Each
            # section is a FROZEN_SECTIONS entry (config/live.py) -- true of
            # the CLIENTS the workers hold, which are built once at startup,
            # and not of this job's, which are rebuilt per run
            # (app.py's servers_factory lambda).
            excluded = {
                "plex": frozenset(config.plex.excluded_libraries if config.plex else []),
                "jellyfin": frozenset(config.jellyfin.excluded_libraries if config.jellyfin else []),
            }
            scan = await find_prunable(session, servers, excluded)
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
                f"would be pruned; {scan.excluded} row(s) in excluded libraries "
                f"would be retired; {held}; refs to retire: {len(scan.stale_refs)}; "
                f"{files_sentence(scan.directories)}"
            )

        outcome = await retire(session, scan.prunable)
        # Matched on ``id``, never on ``native_id``: a candidate with no Plex
        # ref reads back as ``native_id=None``, and a None in the set matched
        # every other ref-less candidate -- claiming their directories and
        # dismissing their queued jobs on the strength of a row that was
        # skipped.
        pruned_ids = set(outcome.pruned)
        # C1: the doomed refs `_scan_stale_refs` found are only deleted now,
        # after the plausibility caps above have passed and the whole-row
        # retire has run -- a ref whose item was ALSO just retired is
        # skipped (the cascade already took it).
        refs_retired = await _retire_stale_refs(session, scan.stale_refs, pruned_ids)
        # Counted off what was actually deleted, not off the candidates:
        # ``retire`` leaves any row that changed under the pass, and those
        # orphan nothing. Reporting the candidate total would claim directories
        # no delete created and could fire the cleanup-cap warning over a
        # threshold this prune never crossed. The dry run has no such
        # distinction to make -- there, the candidates are the whole story.
        deleted = [c for c in scan.prunable if c.id in pruned_ids]
        # Only the rows that HAVE a Plex id: the job payloads are matched by
        # that id, so a None would match nothing useful and is not worth
        # sending.
        dismissed = await dismiss_jobs_for(
            session, [c.native_id for c in deleted if c.native_id is not None]
        )
        directories = _directory_count(deleted)
        # The same rule for the excluded population, and for the same reason:
        # "retired" must describe rows that are gone, not rows that were
        # offered and then shielded by a concurrent re-upsert. `excluded` is
        # now per-server (Task 19); the union of every server's excluded
        # libraries is what `scan.excluded` itself is measured against too.
        all_excluded = frozenset().union(*excluded.values()) if excluded else frozenset()
        excluded_pruned = sum(1 for c in deleted if c.library in all_excluded)
        summary = (
            f"pruned {len(outcome.pruned)} of {scan.total} media_items row(s); "
            f"{excluded_pruned} row(s) in excluded libraries retired; "
            f"{held}; refs retired: {refs_retired}; dismissed {dismissed} queued job(s); "
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
