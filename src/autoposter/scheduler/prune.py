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

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import MediaItem
from autoposter.intake.arr import RenderIntent

logger = logging.getLogger(__name__)


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
        """How many asset directories this prune would orphan.

        One per pruned movie or show: seasons and episodes keep their artwork
        under the show's folder (``render/naming.py``), so they orphan nothing
        of their own. Reported because those directories become the existing
        ``asset_cleanup`` sweep's work -- this module touches no files at all.
        """
        return sum(1 for candidate in self.prunable if candidate.kind in ("movie", "show"))


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
    gone = {
        candidate.id
        for candidate, resolved in zip(candidates, resolved_flags)
        if not resolved
    }

    held: set[int] = set()
    for candidate, resolved in zip(candidates, resolved_flags):
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
