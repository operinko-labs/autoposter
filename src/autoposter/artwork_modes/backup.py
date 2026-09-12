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

It does carry the refusal idiom twice over: the empty-table guard every mode
runs, and a check that ``plex_backup_root`` already exists. That root is a
mount, and a missing mount is the one failure this mode cannot detect any other
way -- every write would simply succeed, into the container.

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
from autoposter.render import naming
from autoposter.render.pipeline import ART_KINDS_FOR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackupResult:
    """The tally of one backup walk, or a refusal.

    ``items`` counts media items walked. ``written``/``skipped``/``failed``
    count per **(item, kind)** outcome -- a movie has two kinds (poster,
    background), so a fully-written movie contributes 2 to ``written``, and a
    movie where one kind writes and the other errors contributes 1 to
    ``written`` and 1 to ``failed``. They therefore do NOT sum to ``items`` in
    general. Two situations never reach kind granularity and count once
    against the whole item instead: no ``root_folder`` to file assets under
    (``skipped``), and a Plex item that could not be fetched at all
    (``failed``). ``skipped`` also absorbs a kind whose file name cannot be
    built -- an episode Plex reports no number for (``naming.missing_number``).

    ``missing`` is a third whole-item bucket, counted separately from
    ``failed``: a rating key Plex 404s on because the item was deleted from
    Plex since its DB row was written. Expected, not a fetch failure -- see
    the per-item log line at INFO, not WARNING.
    """

    items: int
    written: int
    skipped: int
    failed: int
    missing: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        if self.refused is not None:
            return {
                "mode": "backup",
                "status": "refused",
                "reason": self.refused,
                "items": self.items,
                "written": self.written,
                "skipped": self.skipped,
                "failed": self.failed,
                "missing": self.missing,
            }
        # written == 0 with failed > 0 means nothing was actually saved --
        # an all-fail run must not read as a clean "backed up".
        status = "backup failed" if self.written == 0 and self.failed > 0 else "backed up"
        return {
            "mode": "backup",
            "status": status,
            "items": self.items,
            "written": self.written,
            "skipped": self.skipped,
            "failed": self.failed,
            "missing": self.missing,
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
        self._server = plex
        self._http = http
        self._headers = headers

    async def run(self, session: AsyncSession) -> BackupResult:
        # The empty-table guard every mode runs first: an empty media_items is
        # a restore that has not finished, not a library with nothing to back
        # up, so refuse rather than report "0 of 0 backed up" as success.
        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            return BackupResult(0, 0, 0, 0, refused=empty)

        # The backup root is a MOUNT the deployment provides (deploy/README.md),
        # not a directory this mode creates. Required to pre-exist rather than
        # mkdir-ed, because an unmounted path is indistinguishable from a
        # mounted one to ``_atomic_write``: every file would land in the
        # container's own filesystem, filling the node's disk with a backup that
        # reports success and disappears with the pod. Checked before anything
        # is read from Plex -- a whole-library walk is not worth spending to
        # discover the destination is not there.
        root = Path(self._config.artwork_modes.plex_backup_root)
        if not await asyncio.to_thread(root.is_dir):
            return BackupResult(0, 0, 0, 0, refused=(
                f"refused: the backup root {root} is not there; it is a mount "
                "this deployment has to provide, and writing into an unmounted "
                "container path would produce a backup nothing can restore "
                "from -- change nothing"
            ))

        # The manual_override_path trick: one naming function, re-rooted from the
        # live asset tree onto the backup tree by swapping assets_root only.
        backup_config = self._config.model_copy(update={"assets_root": root})

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
        # End the read transaction before the walk: what follows is a Plex
        # request and a file write per (item, kind) over the whole library, and
        # nothing reads the database again. See reset.py for why the rows
        # survive the commit.
        await session.commit()

        items = written = skipped = failed = missing_items = 0
        for row in rows:
            items += 1
            if row.root_folder is None:
                # Nullable, and the tree is rooted at it -- an item without one
                # has nowhere its art could be filed.
                skipped += 1
                continue
            try:
                ref = await self._server.fetch_ref(row.rating_key)
            except Exception:  # noqa: BLE001 - one bad item must not abort the walk
                logger.warning(
                    "backup: could not fetch Plex item %s", row.rating_key, exc_info=True
                )
                failed += 1
                continue
            if ref is None:
                # Expected, not a crash: the item was deleted from Plex after
                # its DB row was written. One concise line, no traceback --
                # counted separately from real failures below.
                logger.info("backup: %s no longer in Plex, skipped", row.rating_key)
                missing_items += 1
                continue

            # Per-(item, kind) tally: a partial item (one kind writes, another
            # errors) must show up in both written and failed, not just one.
            for art_kind in ART_KINDS_FOR[row.kind]:
                # The adoption walk's and the render pipeline's guard, for the
                # same reason: Plex's TV agent hands back ``index: None`` for
                # year-grouped specials, and ``asset_path`` cannot name a file
                # without the number. Unguarded it raises mid-walk, so the
                # trigger 500s after a partial tree is already on disk.
                missing = naming.missing_number(
                    art_kind, row.season_number, row.episode_number
                )
                if missing is not None:
                    logger.warning(
                        "backup: %s (rating_key %s) has no %s -- skipping its %s",
                        row.kind, row.rating_key, missing, art_kind,
                    )
                    skipped += 1
                    continue
                try:
                    fetched = await self._server.fetch_artwork(ref, art_kind)
                except Exception:  # noqa: BLE001 - see above
                    logger.warning(
                        "backup: could not read %s for %s",
                        art_kind, row.rating_key, exc_info=True,
                    )
                    failed += 1
                    continue
                if fetched is None:
                    # Plex served no artwork of this kind for this item.
                    skipped += 1
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
                    failed += 1
                    continue
                written += 1

        if missing_items:
            logger.info("backup: skipped %d item(s) no longer in Plex", missing_items)

        return BackupResult(items, written, skipped, failed, missing=missing_items)
