"""Reset mode (Phase 7b, roadmap row 66): hand the poster back to Plex's agent.

Posterizarr's "poster reset". ``upload_artwork`` uploads a badged poster and
*locks* the field so Plex's metadata agent cannot reclaim it; this is the
inverse -- unlock the field and re-select the agent's own art
(``plex/artwork.py::reset_poster_to_agent_default``).

**It only ever touches art this service uploaded.** An operator who hand-set a
poster in Plex meant it, so "reset everything" must not mean "undo the human's
choices too". The two are told apart by the EXIF provenance stamped into every
image we upload (``plex/exif.py``): the mode probes each candidate's current
poster and skips anything ``parse_provenance`` does not recognise as ours. That
probe is a range read of a few kilobytes per item, not a download.

**The orphaned upload is not deleted.** Selecting the agent's art leaves the
image we uploaded on the Plex server as a dangling ``upload://`` entry; Plex
exposes no API to remove one. The response says so (``ORPHANED_UPLOAD_NOTE``)
so the operator is not left thinking the reset reclaimed the space.

Poster only, which is what row 66 asks for -- backgrounds are not reset.
Dry run by default, with the shared plausibility cap and the empty-table guard,
and the trigger endpoint raises the ``WorkerPause`` fence for an applied run.
Never ``.refresh()``: it would have Plex re-pull from its agents and revert
locked fields elsewhere in the library.
"""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import refuse_if_empty, refuse_if_implausible
from autoposter.db.models import MediaItem
from autoposter.plex.artwork import artwork_provenance, reset_poster_to_agent_default

logger = logging.getLogger(__name__)

ORPHANED_UPLOAD_NOTE = (
    "the poster this replaced stays on the Plex server as an orphaned "
    "upload:// image -- Plex offers no API to delete one"
)


@dataclass(frozen=True)
class ResetResult:
    """What a reset run did (or would do), or a refusal.

    ``items`` is the candidate set after the filters; ``items_with_our_art`` is
    how many of those are currently showing a poster this service uploaded --
    the items that would change, which the plausibility cap is measured against.
    On an applied run ``reset`` and ``failed`` split those items by outcome; an
    item Plex holds no agent poster for counts as ``failed``, because its
    visible poster did not change even though its field was unlocked.
    """

    items: int
    items_with_our_art: int
    reset: int
    failed: int
    dry_run: bool
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "reset",
            "dry_run": self.dry_run,
            "items": self.items,
            "items_with_our_art": self.items_with_our_art,
        }
        if self.refused is not None:
            # No note: nothing was replaced, so nothing was orphaned.
            body["status"] = "refused"
            body["reason"] = self.refused
            return body
        if self.dry_run:
            body["status"] = "dry run"
        else:
            body["status"] = "reset"
            body["reset"] = self.reset
            body["failed"] = self.failed
        body["note"] = ORPHANED_UPLOAD_NOTE
        return body


class ResetMode:
    """Unlock our posters and re-select Plex's agent art. See the module docstring."""

    def __init__(
        self, config, plex, http, headers: dict, *, apply: bool,
        kind: str | None = None, library: str | None = None, item_id: int | None = None,
    ) -> None:
        self._config = config
        self._plex = plex
        self._http = http
        self._headers = headers
        self._apply = apply
        self._kind = kind
        self._library = library
        self._item_id = item_id

    async def run(self, session: AsyncSession) -> ResetResult:
        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            # dry_run mirrors the success paths below (True iff apply was not
            # requested), not just self._apply -- see restore.py.
            return ResetResult(0, 0, 0, 0, not self._apply, refused=empty)

        conditions = []
        if self._kind is not None:
            conditions.append(MediaItem.kind == self._kind)
        if self._library is not None:
            conditions.append(MediaItem.library == self._library)
        if self._item_id is not None:
            conditions.append(MediaItem.id == self._item_id)

        rows = (
            await session.execute(
                select(MediaItem.rating_key).where(*conditions).order_by(MediaItem.id)
            )
        ).all()
        total = len(rows)

        base_url = self._config.plex.url
        # Which candidates are showing art we uploaded. Rating keys rather than
        # the fetched plexapi objects: the probe walks the whole filtered set,
        # which can be the entire library, while the write walks at most
        # ``max_changes`` of it -- holding every probed object alive to save the
        # applied run one fetch each would be the wrong trade. A dry run, the
        # default, never re-fetches at all.
        ours: list[str] = []
        for row in rows:
            try:
                plex_item = await self._plex.fetch_item(row.rating_key)
            except Exception:  # noqa: BLE001 - one bad item must not abort the probe
                logger.warning(
                    "reset: could not fetch Plex item %s", row.rating_key, exc_info=True
                )
                continue
            # None for a poster nobody stamped, for an item with no artwork at
            # all, and for a Plex that could not be asked -- all of which mean
            # "not provably ours", which is the safe direction here.
            if await artwork_provenance(
                self._http, plex_item, base_url, self._headers
            ) is not None:
                ours.append(row.rating_key)

        refusal = refuse_if_implausible(
            len(ours), total,
            self._config.artwork_modes.max_changes,
            self._config.artwork_modes.max_change_share,
        )
        if refusal is not None:
            return ResetResult(total, len(ours), 0, 0, not self._apply, refused=refusal)

        if not self._apply:
            return ResetResult(total, len(ours), 0, 0, dry_run=True)

        reset = failed = 0
        for rating_key in ours:
            try:
                plex_item = await self._plex.fetch_item(rating_key)
                selected = await asyncio.to_thread(
                    reset_poster_to_agent_default, plex_item
                )
            except Exception:  # noqa: BLE001 - see above
                logger.warning("reset: could not reset %s", rating_key, exc_info=True)
                failed += 1
                continue
            if selected:
                reset += 1
            else:
                # Unlocked, but Plex holds no agent poster to fall back to, so
                # what is displayed did not change.
                logger.warning("reset: no agent poster available for %s", rating_key)
                failed += 1

        return ResetResult(total, len(ours), reset, failed, dry_run=False)
