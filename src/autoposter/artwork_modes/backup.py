"""Backup mode (Phase 7b, roadmap row 76): copy Plex's current artwork to disk.

Traverses every ``media_items`` row -- the full-pass precedent
(``routes.py::run_full_pass``) -- and for each item, for each art kind the item
can carry (``ART_KINDS_FOR``), reads the bytes Plex is currently serving
(``fetch_artwork``) and writes them into a Kometa-structured tree rooted at
``config.artwork_modes.plex_backup_root``. The tree layout is produced by
``naming.asset_path``, which roots at ``config.assets_root``; the mode
re-roots it with the ``model_copy`` trick ``manual_override_path`` uses so a
single naming function serves both the live asset tree and the backup tree.

Backup is Plex-read-only and DB-read-only: it never writes to Plex and never
touches the database. It therefore carries NO ``apply`` flag -- reading from
Plex and writing to disk is not a destructive Plex operation, so the dry-run
posture the Plex-writing modes share does not apply here.

**Fan-out: a single walk, not per-item jobs.** Backup is idempotent -- a rerun
overwrites the same files at the same paths, so "resume after a failure" is just
"run it again" and loses no work. A per-item fan-out would put thousands of jobs
on the queue for one backup and scatter the ``{items, written, skipped,
failed}`` tally across independent job rows the trigger response could not
aggregate. Most of the retry/resume benefit is kept anyway: a per-item failure
is caught and counted rather than aborting the walk, so one unreachable item
costs only its own line in the tally.
"""

import asyncio
import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import refuse_if_empty
from autoposter.db.models import MediaItem
from autoposter.plex.artwork import fetch_artwork
from autoposter.render import naming
from autoposter.render.pipeline import ART_KINDS_FOR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackupResult:
    """The per-item tally of one backup walk, or a refusal.

    Every item lands in exactly one of ``written``/``skipped``/``failed``, so
    the three sum to ``items``: ``written`` when at least one art file was
    saved, ``skipped`` when Plex served nothing for the item (or it has no
    ``root_folder`` to file assets under), ``failed`` when an error kept every
    kind from being read or written.
    """

    items: int
    written: int
    skipped: int
    failed: int
    refused: str | None = None

    def as_response(self) -> dict:
        if self.refused is not None:
            return {"mode": "backup", "status": "refused", "reason": self.refused}
        return {
            "mode": "backup",
            "status": "backed up",
            "items": self.items,
            "written": self.written,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, creating parent directories.

    A temp file in the destination directory plus ``os.replace`` so a reader --
    or a concurrent restore -- never sees a half-written file, and a crash
    mid-write leaves the previous backup intact rather than a truncated one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class BackupMode:
    """Copy Plex's live artwork into the backup tree. See the module docstring."""

    def __init__(self, config, plex, http, headers: dict) -> None:
        self._config = config
        self._plex = plex
        self._http = http
        self._headers = headers

    async def run(self, session: AsyncSession) -> BackupResult:
        # The empty-table guard every mode runs first: an empty media_items is
        # a restore that has not finished, not a library with nothing to back
        # up, so refuse rather than report "0 of 0 backed up" as success.
        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            return BackupResult(0, 0, 0, 0, refused=empty)

        base_url = self._config.plex.url
        # The manual_override_path trick: one naming function, re-rooted from the
        # live asset tree onto the backup tree by swapping assets_root only.
        backup_config = self._config.model_copy(
            update={"assets_root": Path(self._config.artwork_modes.plex_backup_root)}
        )

        rows = (
            await session.execute(
                select(
                    MediaItem.rating_key,
                    MediaItem.library,
                    MediaItem.kind,
                    MediaItem.root_folder,
                    MediaItem.season_number,
                    MediaItem.episode_number,
                ).order_by(MediaItem.id)
            )
        ).all()

        items = written = skipped = failed = 0
        for row in rows:
            items += 1
            if row.root_folder is None:
                # Nullable, and the tree is rooted at it -- an item without one
                # has nowhere its art could be filed.
                skipped += 1
                continue
            try:
                plex_item = await self._plex.fetch_item(row.rating_key)
            except Exception:  # noqa: BLE001 - one bad item must not abort the walk
                logger.warning(
                    "backup: could not fetch Plex item %s", row.rating_key, exc_info=True
                )
                failed += 1
                continue

            wrote_any = False
            had_error = False
            for art_kind in ART_KINDS_FOR[row.kind]:
                try:
                    fetched = await fetch_artwork(
                        self._http, plex_item, base_url, self._headers, art_kind
                    )
                except Exception:  # noqa: BLE001 - see above
                    logger.warning(
                        "backup: could not read %s for %s",
                        art_kind, row.rating_key, exc_info=True,
                    )
                    had_error = True
                    continue
                if fetched is None:
                    continue
                data, _content_type = fetched
                path = naming.asset_path(
                    backup_config, row.library, row.root_folder, art_kind,
                    row.season_number, row.episode_number,
                )
                try:
                    await asyncio.to_thread(_atomic_write, path, data)
                except Exception:  # noqa: BLE001 - see above
                    logger.warning(
                        "backup: could not write %s for %s",
                        path, row.rating_key, exc_info=True,
                    )
                    had_error = True
                    continue
                wrote_any = True

            if wrote_any:
                written += 1
            elif had_error:
                failed += 1
            else:
                # Plex served no artwork of any kind for this item.
                skipped += 1

        return BackupResult(items, written, skipped, failed)
