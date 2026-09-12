"""Restore mode (Phase 7b, roadmap row 77): push the backup tree back to Plex.

The inverse of ``backup``: reads the Kometa-structured tree rooted at
``config.artwork_modes.plex_backup_root`` and uploads each present file back to
its Plex item via ``upload_artwork``. Filtered the way the library browser is --
by ``type`` (kind), ``library`` and ``item_id`` -- so an operator can restore
one item, one library or everything.

Dry-run by default (the ``cleanup.apply`` / ``badges.upload_to_plex`` posture):
with ``apply`` false it reports what it WOULD push and pushes nothing; with
``apply`` true it pushes. The shared plausibility cap refuses an implausibly
large operation with the real numbers -- a filter or a mount gone wrong -- and
the empty-table guard refuses against a ``media_items`` emptied by an unfinished
restore.

**The worker pool is paused around the push, not here.** Restore uploads to the
same Plex items the live render pipeline does, so the trigger endpoint raises
the ``WorkerPause`` fence for the duration (``routes.py``): a worker already
mid-job finishes it and then idles, so the two never race on one item. The mode
itself never calls ``.refresh()`` -- a metadata refresh would have Plex re-pull
from its agents and overwrite the very art being restored (the project-wide AST
guard forbids it).
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import refuse_if_empty, refuse_if_implausible
from autoposter.db.models import MediaItem
from autoposter.db.refs import native_ids
from autoposter.render import naming
from autoposter.render.pipeline import ART_KINDS_FOR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RestoreResult:
    """What a restore run did (or would do), or a refusal.

    ``items`` is the candidate set after the filters; ``items_with_backup`` is
    how many of those have at least one file in the backup tree -- the items
    that would change, which the plausibility cap is measured against.
    ``files`` is the total number of backup files present to push. On an applied
    run ``pushed`` and ``failed`` split those files by outcome.

    ``skipped`` counts (item, kind) pairs the planning phase could not even name
    a path for, which is the backup walk's own ``skipped`` in the one shape it
    can happen here: an episode Plex reports no number for. It is reported on a
    dry run too, because "these rows will never be restored" is exactly what a
    dry run exists to say.
    """

    items: int
    items_with_backup: int
    files: int
    skipped: int
    pushed: int
    failed: int
    dry_run: bool
    missing: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "restore",
            "dry_run": self.dry_run,
            "items": self.items,
            "items_with_backup": self.items_with_backup,
            "files": self.files,
            "skipped": self.skipped,
            "missing": self.missing,
        }
        if self.refused is not None:
            body["status"] = "refused"
            body["reason"] = self.refused
            return body
        if self.dry_run:
            body["status"] = "dry run"
        else:
            body["status"] = "restored"
            body["pushed"] = self.pushed
            body["failed"] = self.failed
        return body


class RestoreMode:
    """Push the backup tree back to Plex, filtered. See the module docstring."""

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

    async def run(self, session: AsyncSession) -> RestoreResult:
        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            # dry_run mirrors the success paths below (True iff apply was not
            # requested), not just self._apply -- a refusal was previously
            # never exposed to as_response(), so this was backwards and unseen.
            return RestoreResult(0, 0, 0, 0, 0, 0, not self._apply, refused=empty)

        conditions = []
        if self._kind is not None:
            conditions.append(MediaItem.kind == self._kind)
        if self._library is not None:
            conditions.append(MediaItem.library == self._library)
        if self._item_id is not None:
            conditions.append(MediaItem.id == self._item_id)

        rows = (
            await session.execute(
                select(
                    MediaItem.id,
                    MediaItem.library,
                    MediaItem.kind,
                    MediaItem.root_folder,
                    MediaItem.season_number,
                    MediaItem.episode_number,
                )
                .where(*conditions)
                .order_by(MediaItem.id)
            )
        ).all()
        total = len(rows)
        # One query for the whole filtered set's Plex ids, not one per row.
        plex_ids = await native_ids(session, [row.id for row in rows], "plex")
        # End the read transaction before the planning phase: what follows is a
        # stat per (item, kind) over a possible NFS mount and then a push per
        # file, and nothing reads the database again until the run is over. See
        # reset.py for why the rows survive the commit.
        await session.commit()

        backup_config = self._config.model_copy(
            update={"assets_root": Path(self._config.artwork_modes.plex_backup_root)}
        )

        # Which backup files exist for the filtered set, grouped by item so an
        # applied run fetches each Plex object once. Order preserved (by id).
        planned: dict[str, list[tuple[str, Path]]] = {}
        items_with_backup = 0
        files = skipped = 0
        missing = 0
        for row in rows:
            if row.root_folder is None:
                continue
            native_id = plex_ids.get(row.id)
            if native_id is None:
                logger.info("restore: %s has no Plex ref, skipped", row.id)
                missing += 1
                continue
            present: list[tuple[str, Path]] = []
            for art_kind in ART_KINDS_FOR[row.kind]:
                # The backup walk's guard, on the same naming call: Plex's TV
                # agent hands back ``index: None`` for year-grouped specials,
                # and ``asset_path`` cannot name a file without the number.
                # Unguarded it raises while planning, so the trigger 500s
                # before it pushes anything at all.
                missing_number = naming.missing_number(
                    art_kind, row.season_number, row.episode_number
                )
                if missing_number is not None:
                    logger.warning(
                        "restore: %s (native id %s) has no %s -- skipping its %s",
                        row.kind, native_id, missing_number, art_kind,
                    )
                    skipped += 1
                    continue
                path = naming.asset_path(
                    backup_config, row.library, row.root_folder, art_kind,
                    row.season_number, row.episode_number,
                )
                if await asyncio.to_thread(path.is_file):
                    present.append((art_kind, path))
            if present:
                planned[native_id] = present
                items_with_backup += 1
                files += len(present)

        refusal = refuse_if_implausible(
            items_with_backup, total,
            self._config.artwork_modes.max_changes,
            self._config.artwork_modes.max_change_share,
        )
        if refusal is not None:
            # See the empty-table refusal above: dry_run is not self._apply.
            return RestoreResult(
                total, items_with_backup, files, skipped, 0, 0,
                not self._apply, missing=missing, refused=refusal,
            )

        if not self._apply:
            return RestoreResult(
                total, items_with_backup, files, skipped, 0, 0, dry_run=True,
                missing=missing,
            )

        pushed = failed = 0
        for native_id, entries in planned.items():
            try:
                ref = await self._server.fetch_ref(native_id)
            except Exception:  # noqa: BLE001 - one bad item must not abort the run
                logger.warning(
                    "restore: could not fetch Plex item %s", native_id, exc_info=True
                )
                failed += len(entries)
                continue
            if ref is None:
                # Expected, not a crash: the item was deleted from Plex since
                # its DB row was written. One concise line, no traceback --
                # counted separately from real failures below (backup.py's
                # PR #112 hotfix shape).
                logger.info("restore: %s no longer in Plex, skipped", native_id)
                missing += 1
                continue
            for art_kind, path in entries:
                try:
                    data = await asyncio.to_thread(path.read_bytes)
                    await self._server.upload_artwork(ref, data, art_kind, lock=True)
                    pushed += 1
                except Exception:  # noqa: BLE001 - see above
                    logger.warning(
                        "restore: could not push %s for %s",
                        art_kind, native_id, exc_info=True,
                    )
                    failed += 1

        if missing:
            logger.info("restore: skipped %d item(s) no longer in Plex", missing)

        return RestoreResult(
            total, items_with_backup, files, skipped, pushed, failed, dry_run=False,
            missing=missing,
        )
