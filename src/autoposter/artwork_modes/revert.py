"""Revert mode (Phase 7b, roadmap row 65): put the un-badged base back on Plex.

Posterizarr's "remove overlays" mode. The badged image this service uploads is
never persisted anywhere -- ``badges.compose.compose()`` returns bytes that go
straight to Plex -- but the *base* it was composited over is, under
``config.assets_root``, named by the ``renders`` row that produced it. So
removing an overlay is not an image operation at all: it is re-uploading the
file already on disk, which is the same image without the badges.

Filtered the way restore is -- by ``type`` (kind), ``library`` and ``item_id``
-- and dry run by default, with the shared plausibility cap and the empty-table
guard. Only items whose base is actually on disk are acted on: a ``renders`` row
is created before anything is written, so ``no_art``, ``truncated``, ``skipped``
and ``failed`` rows all legitimately name a file that was never produced, and
those items are counted as candidates but never pushed.

**The worker pool is paused around the push, not here** -- the trigger endpoint
raises the ``WorkerPause`` fence for an applied run (``routes.py``), exactly as
it does for restore. The mode never calls ``.refresh()``.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from plexapi.exceptions import NotFound as PlexNotFound
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.api.artwork import OutsideAssetsRoot, resolve_asset
from autoposter.artwork_modes.base import refuse_if_empty, refuse_if_implausible
from autoposter.db.models import MediaItem, Render
from autoposter.plex.artwork import upload_artwork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RevertResult:
    """What a revert run did (or would do), or a refusal.

    ``items`` is the candidate set after the filters; ``items_with_base`` is how
    many of those have at least one base file on disk -- the items that would
    change, which the plausibility cap is measured against. ``files`` is the
    total number of base files present to push. On an applied run ``pushed`` and
    ``failed`` split those files by outcome.
    """

    items: int
    items_with_base: int
    files: int
    pushed: int
    failed: int
    dry_run: bool
    missing: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "revert",
            "dry_run": self.dry_run,
            "items": self.items,
            "items_with_base": self.items_with_base,
            "files": self.files,
            "missing": self.missing,
        }
        if self.refused is not None:
            body["status"] = "refused"
            body["reason"] = self.refused
            return body
        if self.dry_run:
            body["status"] = "dry run"
        else:
            body["status"] = "reverted"
            body["pushed"] = self.pushed
            body["failed"] = self.failed
        return body


class RevertMode:
    """Push the un-badged ``/assets`` base back to Plex. See the module docstring."""

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

    async def run(self, session: AsyncSession) -> RevertResult:
        # ``renders``, not ``media_items``: this mode reads render rows for the
        # paths, so an empty renders table is the one that would make it read
        # "nothing to compare against" as "nothing to do".
        empty = await refuse_if_empty(session, Render, table_name="renders")
        if empty is not None:
            # dry_run mirrors the success paths below (True iff apply was not
            # requested), not just self._apply -- see restore.py.
            return RevertResult(0, 0, 0, 0, 0, not self._apply, refused=empty)

        conditions = []
        if self._kind is not None:
            conditions.append(MediaItem.kind == self._kind)
        if self._library is not None:
            conditions.append(MediaItem.library == self._library)
        if self._item_id is not None:
            conditions.append(MediaItem.id == self._item_id)

        # The candidate count is items, not render rows, so the cap's "N of M
        # items" reads the way the restore one does. Counted separately from the
        # join below, which drops items that have no usable render row at all.
        total = (
            await session.execute(
                select(func.count()).select_from(MediaItem).where(*conditions)
            )
        ).scalar_one()

        rows = (
            await session.execute(
                select(MediaItem.rating_key, Render.art_kind, Render.asset_path)
                .join(Render, Render.item_id == MediaItem.id)
                # A NULL digest means this project never rendered or adopted
                # bytes into the row. On a cutover library a Kometa-era file can
                # occupy the exact path such a row names, and pushing it would
                # present foreign badged art as our clean base -- the same
                # refusal ``api/artwork.py::base_artwork`` makes when serving it.
                .where(Render.base_sha256.is_not(None), *conditions)
                .order_by(MediaItem.id, Render.art_kind)
            )
        ).all()
        # End the read transaction before the resolve/push phase: what follows
        # is a realpath and a stat per render row over a possible NFS mount and
        # then a push per file, and nothing reads the database again until the
        # run is over. See reset.py for why the rows survive the commit.
        await session.commit()

        assets_root = Path(self._config.assets_root)
        planned: dict[str, list[tuple[str, Path]]] = defaultdict(list)
        for row in rows:
            resolved = await asyncio.to_thread(
                _base_on_disk, row.asset_path, assets_root
            )
            if resolved is not None:
                planned[row.rating_key].append((row.art_kind, resolved))

        items_with_base = len(planned)
        files = sum(len(entries) for entries in planned.values())

        refusal = refuse_if_implausible(
            items_with_base, total,
            self._config.artwork_modes.max_changes,
            self._config.artwork_modes.max_change_share,
        )
        if refusal is not None:
            return RevertResult(
                total, items_with_base, files, 0, 0, not self._apply, refused=refusal
            )

        if not self._apply:
            return RevertResult(total, items_with_base, files, 0, 0, dry_run=True)

        pushed = failed = missing = 0
        for rating_key, entries in planned.items():
            try:
                plex_item = await self._plex.fetch_item(rating_key)
            except PlexNotFound:
                # Expected, not a crash: the item was deleted from Plex since
                # its render row was written. One concise line, no traceback --
                # counted separately from real failures below (backup.py's
                # PR #112 hotfix shape).
                logger.info("revert: %s no longer in Plex, skipped", rating_key)
                missing += 1
                continue
            except Exception:  # noqa: BLE001 - one bad item must not abort the run
                logger.warning(
                    "revert: could not fetch Plex item %s", rating_key, exc_info=True
                )
                failed += len(entries)
                continue
            for art_kind, path in entries:
                try:
                    data = await asyncio.to_thread(path.read_bytes)
                    await asyncio.to_thread(upload_artwork, plex_item, data, art_kind)
                    pushed += 1
                except Exception:  # noqa: BLE001 - see above
                    logger.warning(
                        "revert: could not push %s for %s",
                        art_kind, rating_key, exc_info=True,
                    )
                    failed += 1

        if missing:
            logger.info("revert: skipped %d item(s) no longer in Plex", missing)

        return RevertResult(
            total, items_with_base, files, pushed, failed, dry_run=False, missing=missing
        )


def _base_on_disk(asset_path: str, assets_root: Path) -> Path | None:
    """The resolved base file for a render row, or ``None`` if there is none.

    ``renders.asset_path`` is a filesystem path read out of the database and the
    database is not a trust boundary, so the path is proven to be inside the
    asset tree before it can become an upload -- ``resolve_asset`` does both
    that and the "is it actually a file" check. A row pointing outside the tree
    is worth a warning rather than a silent skip: nothing this project writes
    can produce one, so it means either tampering or an ``assets_root`` that no
    longer points where the rows were written.

    Blocking (``realpath`` and ``stat`` over a possible NFS mount); the caller
    runs it in a thread.
    """
    try:
        return resolve_asset(asset_path, assets_root)
    except OutsideAssetsRoot as exc:
        logger.warning("revert: refusing to push %s", exc)
        return None
    except FileNotFoundError:
        return None
