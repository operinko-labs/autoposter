"""Reset mode (Phase 7b, roadmap row 66): hand the artwork back to Plex's agent.

Posterizarr's "poster reset". ``upload_artwork`` uploads a badged image and
*locks* the field so Plex's metadata agent cannot reclaim it; this is the
inverse -- unlock the field and re-select the agent's own art
(``plex/artwork.py::reset_artwork_to_agent_default``).

**Both fields, not just the poster -- on the items that have both.** The
pipeline uploads and locks a poster *and* a background on a movie or a show, so
a complete undo has to release both: the mode walks ``RESET_ART_KINDS`` for the
item's own kind, and every count below that is not the candidate count is per
(item, field). A season and an episode get their poster only, because that is
all the pipeline ever wrote to them -- their ``art`` is the show's backdrop
Plex hands down, ours by provenance but never ours to reset.

**It only ever touches art this service uploaded.** An operator who hand-set a
poster in Plex meant it, so "reset everything" must not mean "undo the human's
choices too". The two are told apart by the EXIF provenance stamped into every
image we upload (``plex/exif.py``): the mode probes each candidate's current
artwork -- once per field -- and skips anything ``parse_provenance`` does not
recognise as ours. Each probe is a range read of a few kilobytes, not a
download, and a field the item has no artwork in costs no request at all.

**The orphaned upload is not deleted.** Selecting the agent's art leaves the
image we uploaded on the Plex server as a dangling ``upload://`` entry; Plex's
HTTP API cannot delete a single ``upload://`` entry (an entry-addressed DELETE
answers 404, verified 2026-09-05) -- ImageMaid removes them by editing Plex's
database directly. The response says so (``ORPHANED_UPLOAD_NOTE``) so the
operator is not left thinking the reset reclaimed the space.

Dry run by default, with the shared plausibility cap and the empty-table guard,
and the trigger endpoint raises the ``WorkerPause`` fence for an applied run.
Never ``.refresh()``: it would have Plex re-pull from its agents and revert
locked fields elsewhere in the library.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import refuse_if_empty, refuse_if_implausible
from autoposter.db.models import MediaItem
from autoposter.servers.base import CAP_ARTWORK_PROVENANCE, CAP_RESET_TO_AGENT_DEFAULT

logger = logging.getLogger(__name__)

# Which Plex artwork fields this mode may touch on an item of each kind, as the
# art kinds ``plex/artwork.py`` routes on: the poster (Plex's ``thumb``) and the
# background (Plex's ``art``). The other kinds this project renders --
# ``season_poster``, ``title_card`` -- are posters of their own items, so
# probing them here would ask the same field twice.
#
# Per kind, and NOT ``("poster", "background")`` for everything, because the
# pipeline only ever uploads a background to a movie or a show
# (``pipeline.ART_KINDS_FOR``). Plex fills a season's and an episode's ``art``
# from the show's backdrop -- which on a badged library is our own stamped
# upload -- so a flat list would find our provenance on the ``art`` field of
# every episode of every badged show: a dry run's counts would be inflated by
# thousands, and an applied run would unlock and re-select a field this service
# never wrote to.
RESET_ART_KINDS = {
    "movie": ("poster", "background"),
    "show": ("poster", "background"),
    "season": ("poster",),
    "episode": ("poster",),
}

ORPHANED_UPLOAD_NOTE = (
    "the artwork this replaced stays on the Plex server as an orphaned "
    "upload:// image -- Plex's HTTP API cannot delete a single upload:// "
    "entry (verified 2026-09-05); ImageMaid removes them by editing Plex's "
    "database directly"
)


@dataclass(frozen=True)
class ResetResult:
    """What a reset run did (or would do), or a refusal.

    ``items`` is the candidate set after the filters; ``items_with_our_art`` is
    how many of those are currently showing artwork this service uploaded in at
    least one of the fields its kind has in ``RESET_ART_KINDS`` -- the items
    that would change, which the plausibility cap is measured against.
    ``fields`` is the total number of (item, field) pairs that are ours, the
    revert result's ``files`` in the shape this mode needs: a movie or a show
    can contribute two, a season or an episode at most one.

    ``reset`` and ``failed`` are therefore per field, not per item: on an
    applied run they split ``fields`` by outcome. A field Plex holds no agent
    art for counts as ``failed``, because what is displayed did not change even
    though the field was unlocked.

    ``probe_failed`` counts candidates the probe could not fetch from Plex at
    all, beside ``missing``: they never reach the provenance read, so they drop
    out of ``items_with_our_art`` silently without it. Served on a dry run,
    which is the run it matters on.
    """

    items: int
    items_with_our_art: int
    fields: int
    reset: int
    failed: int
    dry_run: bool
    missing: int = 0
    probe_failed: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "reset",
            "dry_run": self.dry_run,
            "items": self.items,
            "items_with_our_art": self.items_with_our_art,
            "fields": self.fields,
            "missing": self.missing,
            "probe_failed": self.probe_failed,
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
    """Unlock our artwork and re-select Plex's agent art. See the module docstring."""

    def __init__(
        self, config, plex, http, headers: dict, *, apply: bool,
        kind: str | None = None, library: str | None = None, item_id: int | None = None,
    ) -> None:
        self._config = config
        self._server = plex
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
            return ResetResult(0, 0, 0, 0, 0, not self._apply, refused=empty)

        conditions = []
        if self._kind is not None:
            conditions.append(MediaItem.kind == self._kind)
        if self._library is not None:
            conditions.append(MediaItem.library == self._library)
        if self._item_id is not None:
            conditions.append(MediaItem.id == self._item_id)

        rows = (
            await session.execute(
                select(MediaItem.rating_key, MediaItem.kind)
                .where(*conditions)
                .order_by(MediaItem.id)
            )
        ).all()
        total = len(rows)
        # End the read transaction before the probe phase. What follows is one
        # Plex request per candidate over a whole library and touches no row of
        # this result again (they are plain tuples, not ORM objects, so nothing
        # is expired), and holding a transaction open across all of it would
        # leave the connection idle-in-transaction for the length of the run --
        # pinning the oldest xmin and blocking autovacuum meanwhile.
        await session.commit()

        # Which fields of which candidates are showing art we uploaded. Rating
        # keys rather than the fetched refs: the probe walks the whole
        # filtered set, which can be the entire library, while the write walks at
        # most ``max_changes`` of it -- holding every probed ref alive to save
        # the applied run one fetch each would be the wrong trade. A dry run, the
        # default, never re-fetches at all.
        ours: dict[str, list[str]] = {}
        missing = 0
        probe_failed = 0
        for row in rows:
            try:
                ref = await self._server.fetch_ref(row.rating_key)
            except Exception:  # noqa: BLE001 - one bad item must not abort the probe
                logger.warning(
                    "reset: could not fetch Plex item %s", row.rating_key, exc_info=True
                )
                probe_failed += 1
                continue
            if ref is None:
                # Expected, not a crash: the item was deleted from Plex since
                # it was last scanned. One concise line, no traceback --
                # counted separately from a real probe failure below (backup.py's
                # PR #112 hotfix shape).
                logger.info("reset: %s no longer in Plex, skipped", row.rating_key)
                missing += 1
                continue
            # None for artwork nobody stamped, for a field the item has nothing
            # in, and for a Plex that could not be asked -- all of which mean
            # "not provably ours", which is the safe direction here. Per field,
            # so an item whose poster is ours but whose background an operator
            # set by hand has only its poster reset.
            try:
                kinds = [
                    art_kind for art_kind in RESET_ART_KINDS[row.kind]
                    if CAP_ARTWORK_PROVENANCE in self._server.capabilities
                    and await self._server.artwork_provenance(ref, art_kind) is not None
                ]
            except Exception:  # noqa: BLE001 - one bad item must not abort the probe
                logger.warning(
                    "reset: could not probe Plex item %s", row.rating_key, exc_info=True
                )
                probe_failed += 1
                continue
            if kinds:
                ours[row.rating_key] = kinds

        items_with_our_art = len(ours)
        fields = sum(len(kinds) for kinds in ours.values())

        # Items, not fields: the cap asks "how much of the library would this
        # touch", which is the same question whichever fields it touches on each.
        refusal = refuse_if_implausible(
            items_with_our_art, total,
            self._config.artwork_modes.max_changes,
            self._config.artwork_modes.max_change_share,
        )
        if refusal is not None:
            if missing:
                logger.info("reset: skipped %d item(s) no longer in Plex", missing)
            if probe_failed:
                logger.info("reset: could not probe %d item(s)", probe_failed)
            return ResetResult(
                total, items_with_our_art, fields, 0, 0, not self._apply,
                refused=refusal, missing=missing, probe_failed=probe_failed,
            )

        if not self._apply:
            if missing:
                logger.info("reset: skipped %d item(s) no longer in Plex", missing)
            if probe_failed:
                logger.info("reset: could not probe %d item(s)", probe_failed)
            return ResetResult(
                total, items_with_our_art, fields, 0, 0, dry_run=True,
                missing=missing, probe_failed=probe_failed,
            )

        reset = failed = 0
        for rating_key, kinds in ours.items():
            try:
                ref = await self._server.fetch_ref(rating_key)
            except Exception:  # noqa: BLE001 - see above
                logger.warning(
                    "reset: could not fetch Plex item %s", rating_key, exc_info=True
                )
                failed += len(kinds)
                continue
            if ref is None:
                logger.info("reset: %s no longer in Plex, skipped", rating_key)
                missing += 1
                continue
            for art_kind in kinds:
                if CAP_RESET_TO_AGENT_DEFAULT not in self._server.capabilities:
                    logger.warning(
                        "reset: %s does not support resetting %s to the agent default",
                        self._server.name, art_kind,
                    )
                    failed += 1
                    continue
                try:
                    selected = await self._server.reset_artwork_to_agent_default(ref, art_kind)
                except Exception:  # noqa: BLE001 - see above
                    logger.warning(
                        "reset: could not reset %s for %s",
                        art_kind, rating_key, exc_info=True,
                    )
                    failed += 1
                    continue
                if selected:
                    reset += 1
                else:
                    # Unlocked, but Plex holds no agent art to fall back to, so
                    # what is displayed did not change.
                    logger.warning(
                        "reset: no agent %s available for %s", art_kind, rating_key
                    )
                    failed += 1

        if missing:
            logger.info("reset: skipped %d item(s) no longer in Plex", missing)
        if probe_failed:
            logger.info("reset: could not probe %d item(s)", probe_failed)

        return ResetResult(
            total, items_with_our_art, fields, reset, failed, dry_run=False,
            missing=missing, probe_failed=probe_failed,
        )
