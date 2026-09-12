"""Metadata backup (roadmap row 86): the undo story for the mass ops.

The ``artwork_modes/backup.py`` shape, one payload along. It walks every
``media_items`` row, reads what Plex is CURRENTLY serving for the fields this
service writes (``plex.writer.WRITABLE_BY_KIND``, plus each field's lock
state), and writes one YAML file per library under
``config.operations.metadata_backup_root``.

Plex-read-only and database-read-only, exactly like the artwork backup, so it
carries no ``apply`` flag -- reading from Plex and writing to disk is not a
destructive Plex operation. It carries the refusal idiom twice over instead:
the empty-``media_items`` guard every mode runs, and a check that the root
already exists, because that root is a MOUNT and a missing mount is the one
failure this cannot detect any other way (every write would simply succeed,
into the container).

**One file per LIBRARY, keyed by rating key.** Per item would be ~16,000
atomic writes for one backup. Keyed by Plex's rating key rather than by title
because titles collide and an undo file needs identity; ``title``, ``year``
and ``kind`` ride alongside as the human anchors. Consequence, stated plainly:
**this is OUR undo file, not a Kometa-loadable metadata file.** Nothing reads
it back yet -- the restore half is a separate row.

Deterministic on purpose: sorted keys, sorted rating keys, no timestamp
anywhere in the payload. A second backup of an unchanged library produces
byte-identical files, which is what makes "did anything change since the last
backup" answerable with a diff.

**Gated.** ``operations.metadata_backup_enabled`` is off by default and the
endpoint refuses while it is off, which is what gives a mode with no dry run a
meaningful "gate-off writes nothing" state.
"""

import asyncio
import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml
from plexapi.exceptions import NotFound as PlexNotFound
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import refuse_if_empty
from autoposter.db.models import MediaItem
from autoposter.plex.writer import _PLEX_FIELD_NAMES, _locked_in_plex, WRITABLE_BY_KIND

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetadataBackupResult:
    """The tally of one metadata-backup walk, or a refusal.

    ``items`` counts media items walked; ``captured`` counts those whose Plex
    record was read successfully; ``missing`` counts rating keys Plex 404s on
    (the item was deleted from Plex since its DB row was written -- expected,
    not a failure); ``failed`` counts everything else that went wrong per item;
    ``files`` counts the YAML files written, one per library that produced at
    least one captured item.
    """

    items: int
    captured: int
    failed: int
    missing: int = 0
    files: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "metadata_backup",
            "items": self.items,
            "captured": self.captured,
            "failed": self.failed,
            "missing": self.missing,
            "files": self.files,
        }
        if self.refused is not None:
            return {**body, "status": "refused", "reason": self.refused}
        # captured == 0 with failed > 0 means nothing was actually saved.
        status = (
            "metadata backup failed"
            if self.captured == 0 and self.failed > 0
            else "backed up"
        )
        return {**body, "status": status}


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, creating parent directories.

    ``backup.py::_atomic_write``, one encoding along: a temp file in the
    destination directory plus ``os.replace``, so a reader never sees a
    half-written file and a crash mid-write leaves the previous backup intact
    rather than a truncated one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def capture_item(item, kind: str) -> dict:
    """What Plex currently holds for the fields this service writes.

    Values, plus each field's lock state as ``<field>_locked``. A lock Plex did
    not report is OMITTED rather than defaulted to ``False``: "Plex said
    nothing about this field" and "Plex says it is unlocked" are different
    facts, and an undo file that cannot tell them apart would re-lock fields
    nobody locked.
    """
    row: dict[str, object] = {
        "kind": kind,
        "title": getattr(item, "title", None),
        "year": getattr(item, "year", None),
    }
    for field in sorted(WRITABLE_BY_KIND.get(kind, set())):
        attribute, plex_field = _PLEX_FIELD_NAMES[field]
        if field == "genres":
            value = [
                tag for tag in (
                    getattr(g, "tag", g) for g in getattr(item, "genres", None) or []
                ) if isinstance(tag, str)
            ]
        else:
            value = getattr(item, attribute, None)
            if hasattr(value, "strftime"):
                value = value.strftime("%Y-%m-%d")
        row[field] = value
        locked = _locked_in_plex(item, plex_field)
        if locked is not None:
            row[f"{field}_locked"] = locked
    return row


class MetadataBackupMode:
    """Export Plex's live metadata into the backup tree. See the module docstring."""

    def __init__(self, config, plex) -> None:
        self._config = config
        self._server = plex

    async def run(self, session: AsyncSession) -> MetadataBackupResult:
        if not self._config.operations.metadata_backup_enabled:
            return MetadataBackupResult(0, 0, 0, refused=(
                "refused: operations.metadata_backup_enabled is off, so no "
                "metadata was read and nothing was written -- change nothing"
            ))

        # The empty-table guard every mode runs: an empty media_items is a
        # restore that has not finished, not a library with nothing to back up.
        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            return MetadataBackupResult(0, 0, 0, refused=empty)

        root = Path(self._config.operations.metadata_backup_root)
        if not await asyncio.to_thread(root.is_dir):
            return MetadataBackupResult(0, 0, 0, refused=(
                f"refused: the metadata backup root {root} is not there; it is "
                "a mount this deployment has to provide, and writing into an "
                "unmounted container path would produce a backup nothing can "
                "restore from -- change nothing"
            ))

        rows = (
            await session.execute(
                select(
                    MediaItem.rating_key, MediaItem.library, MediaItem.kind
                ).order_by(MediaItem.id)
            )
        ).all()
        # End the read transaction before the walk: what follows is a Plex
        # request per item and a file write per library, and nothing reads the
        # database again (backup.py:177-181's precedent).
        await session.commit()

        by_library: dict[str, dict[str, dict]] = {}
        items = captured = failed = missing = 0
        for row in rows:
            items += 1
            try:
                plex_item = await self._server.fetch_item(row.rating_key)
            except PlexNotFound:
                logger.info(
                    "metadata backup: %s no longer in Plex, skipped", row.rating_key
                )
                missing += 1
                continue
            except Exception:  # noqa: BLE001 - one bad item must not abort the walk
                logger.warning(
                    "metadata backup: could not fetch Plex item %s",
                    row.rating_key, exc_info=True,
                )
                failed += 1
                continue
            try:
                by_library.setdefault(row.library, {})[str(row.rating_key)] = (
                    capture_item(plex_item, row.kind)
                )
            except Exception:  # noqa: BLE001 - see above
                logger.warning(
                    "metadata backup: could not read metadata for %s",
                    row.rating_key, exc_info=True,
                )
                failed += 1
                continue
            captured += 1

        files = 0
        for library, entries in sorted(by_library.items()):
            text = yaml.safe_dump(
                {"metadata": entries},
                sort_keys=True, allow_unicode=True, default_flow_style=False,
            )
            path = root / f"{library}.yml"
            try:
                await asyncio.to_thread(_atomic_write, path, text)
            except Exception:  # noqa: BLE001 - see above
                logger.warning(
                    "metadata backup: could not write %s", path, exc_info=True
                )
                failed += 1
                continue
            files += 1

        if missing:
            logger.info(
                "metadata backup: skipped %d item(s) no longer in Plex", missing
            )
        return MetadataBackupResult(items, captured, failed, missing=missing, files=files)
