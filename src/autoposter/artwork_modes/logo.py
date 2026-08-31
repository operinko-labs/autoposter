"""Logo modes (Phase 7b, roadmap rows 71 and 67): put a clearlogo on Plex, and
take ours back off.

Until now a clearlogo was a render-time input only -- ``render/pipeline.py``
composites one in place of a poster's title text and it rides into the poster's
fingerprint as ``logo_sha``. Nothing ever wrote a logo to Plex itself. These two
modes add that target and its undo:

* :class:`LogoMode` (row 71) finds items Plex has no clearlogo for, asks the
  existing provider ladder for the best one (``select_artwork(..., art.LOGO,
  ...)``, ranked by ``artwork.logo_language_order`` exactly as the render path
  ranks it) and uploads it to Plex's ``clearLogo`` field, locked.
* :class:`LogoRevertMode` (row 67) removes a clearlogo *this service* set, via
  the unlock/clear path, and only that.

**The marker is the crux, and it is not EXIF.** Every other "is this ours?"
decision in this project reads the EXIF provenance we stamp into the image
(``plex/exif.py``), but that cannot work here. The bytes we upload are the
provider's own file, passed through untouched -- the same file an operator would
hand-upload -- so an unstamped logo proves nothing, and stamping one would mean
re-encoding a PNG whose alpha channel is the point (``api/candidates.py`` is
explicit that a picked logo is "written through untouched"). Plex's own
``upload://`` keying does not separate us from an operator either: their upload
is keyed ``upload://`` just as ours is.

So the marker is ``media_items.logo_upload_key``: the ``upload://`` rating key
Plex filed OUR upload under, read back from Plex at upload time and recorded.
The revert acts on an item only when

1. that column is set -- this service set a logo here, and
2. Plex still has *that exact key* selected right now.

Condition 1 alone would clear a hand-set logo on an item we had also written to
in the past; condition 2 is what makes a logo an operator replaced ours with
their choice rather than ours to delete. Both failing directions cost the
operator a logo left in place, never one removed by surprise.

Both modes are dry run by default, carry the shared plausibility cap and the
empty-table guard, and are run inline by the trigger endpoint, which raises the
``WorkerPause`` fence for an applied run (``api/routes.py``). Neither ever calls
``.refresh()``.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from plexapi.exceptions import NotFound as PlexNotFound
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import refuse_if_empty, refuse_if_implausible
from autoposter.db.models import MediaItem
from autoposter.plex.artwork import (
    clear_logo, has_clearlogo, selected_uploaded_logo_key, upload_logo,
)
from autoposter.providers import base as art
from autoposter.providers.ladder import select_artwork

logger = logging.getLogger(__name__)

# A clearlogo belongs to a movie or a show, and to nothing below one -- the same
# restriction ``api/candidates.py`` makes when it allows ``"logo"`` on top of
# ``ART_KINDS_FOR`` for exactly these two kinds. A season's or an episode's LOGO
# request would answer with its show's logo, and putting a show's logo on one
# episode is a defect rather than a feature, so those kinds are not candidates
# at all -- the cap's denominator included, because "how much of the library
# would this touch" has to be measured against the part of it the mode can act
# on.
LOGO_ITEM_KINDS = ("movie", "show")

# The render path can take an SVG logo because ImageMagick rasterises it while
# compositing (``render/compositor.py::build_logo_argv``). Plex's clearLogo
# field takes a raster image, so an SVG pick is skipped here rather than pushed
# to a server that would make nothing of it.
UNUPLOADABLE_LOGO_SUFFIX = ".svg"


@dataclass(frozen=True)
class LogoUpdateResult:
    """What a logo update run did (or would do), or a refusal.

    ``items`` is the candidate set after the filters and the movie/show
    restriction; ``items_missing_logo`` is how many of those Plex currently
    shows no clearlogo for -- the items that would change, which the
    plausibility cap is measured against.

    There is no ``fields``/``files`` count as the reset and revert modes have:
    an item has exactly one clearlogo, so the per-item and per-field tallies
    would be the same number twice. On an applied run ``uploaded`` and
    ``failed`` split ``items_missing_logo`` by outcome, and ``failed`` means
    "the item still has no logo" whatever the cause -- no provider had one, the
    only pick was an SVG, or the upload itself did not go through.
    """

    items: int
    items_missing_logo: int
    uploaded: int
    failed: int
    dry_run: bool
    missing: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "logo",
            "dry_run": self.dry_run,
            "items": self.items,
            "items_missing_logo": self.items_missing_logo,
            "missing": self.missing,
        }
        if self.refused is not None:
            body["status"] = "refused"
            body["reason"] = self.refused
            return body
        if self.dry_run:
            body["status"] = "dry run"
        else:
            body["status"] = "updated"
            body["uploaded"] = self.uploaded
            body["failed"] = self.failed
        return body


@dataclass(frozen=True)
class LogoRevertResult:
    """What a logo revert run did (or would do), or a refusal.

    ``items`` is the candidate set after the filters and the movie/show
    restriction; ``items_with_our_logo`` is how many of those are marked *and*
    still showing that exact upload -- the items that would change, which the
    plausibility cap is measured against. On an applied run ``cleared`` and
    ``failed`` split them by outcome.

    No ``note`` about an orphaned upload, unlike the reset mode: Plex exposes a
    DELETE for the clearlogo field, so this really does remove the image.
    """

    items: int
    items_with_our_logo: int
    cleared: int
    failed: int
    dry_run: bool
    missing: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "logo_revert",
            "dry_run": self.dry_run,
            "items": self.items,
            "items_with_our_logo": self.items_with_our_logo,
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
            body["cleared"] = self.cleared
            body["failed"] = self.failed
        return body


def _conditions(kind: str | None, library: str | None, item_id: int | None) -> list:
    """The filter set both modes share, over the logo-capable kinds only."""
    conditions = [MediaItem.kind.in_(LOGO_ITEM_KINDS)]
    if kind is not None:
        conditions.append(MediaItem.kind == kind)
    if library is not None:
        conditions.append(MediaItem.library == library)
    if item_id is not None:
        conditions.append(MediaItem.id == item_id)
    return conditions


class LogoMode:
    """Fill in the clearlogos Plex is missing. See the module docstring."""

    def __init__(
        self, config, plex, http, headers: dict, providers: list, *, apply: bool,
        kind: str | None = None, library: str | None = None, item_id: int | None = None,
    ) -> None:
        self._config = config
        self._plex = plex
        self._http = http
        self._headers = headers
        self._providers = providers
        self._apply = apply
        self._kind = kind
        self._library = library
        self._item_id = item_id

    async def run(self, session: AsyncSession) -> LogoUpdateResult:
        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            # dry_run mirrors the success paths below (True iff apply was not
            # requested), not just self._apply -- see restore.py.
            return LogoUpdateResult(0, 0, 0, 0, not self._apply, refused=empty)

        rows = (
            await session.execute(
                select(
                    MediaItem.id, MediaItem.rating_key, MediaItem.kind,
                    MediaItem.tmdb_id, MediaItem.tvdb_id, MediaItem.imdb_id,
                )
                .where(*_conditions(self._kind, self._library, self._item_id))
                .order_by(MediaItem.id)
            )
        ).all()
        total = len(rows)
        # End the read transaction before the probe phase: what follows is one
        # Plex request per candidate, then a provider call and an upload per
        # missing logo. The per-item marker writes below open their own short
        # transactions. See reset.py for why the rows survive the commit.
        await session.commit()

        # Which candidates Plex shows no clearlogo for. One property read per
        # item -- and a dry run, the default, stops here, so it costs no
        # provider call at all: the report is "N items are missing a logo", not
        # "N logos were found", which would mean walking the ladder for the
        # whole library to produce a number that changes nothing.
        missing = []
        missing_from_plex = 0
        for row in rows:
            try:
                plex_item = await self._plex.fetch_item(row.rating_key)
                if not await has_clearlogo(plex_item):
                    missing.append(row)
            except PlexNotFound:
                # A stale rating_key (item deleted/moved in Plex) is expected,
                # not a crash -- one concise INFO line, no traceback, tallied
                # separately from a probe failure (backup.py's PR #112 hotfix
                # shape). NotFound's message embeds the server URL, so never
                # str(exc).
                logger.info("logo: %s no longer in Plex, skipped", row.rating_key)
                missing_from_plex += 1
            except Exception:  # noqa: BLE001 - one bad item must not abort the probe
                logger.warning(
                    "logo: could not probe Plex item %s", row.rating_key, exc_info=True
                )

        items_missing_logo = len(missing)
        refusal = refuse_if_implausible(
            items_missing_logo, total,
            self._config.artwork_modes.max_changes,
            self._config.artwork_modes.max_change_share,
        )
        if refusal is not None:
            if missing_from_plex:
                logger.info("logo: skipped %d item(s) no longer in Plex", missing_from_plex)
            return LogoUpdateResult(
                total, items_missing_logo, 0, 0, not self._apply,
                refused=refusal, missing=missing_from_plex,
            )

        if not self._apply:
            if missing_from_plex:
                logger.info("logo: skipped %d item(s) no longer in Plex", missing_from_plex)
            return LogoUpdateResult(
                total, items_missing_logo, 0, 0, dry_run=True, missing=missing_from_plex
            )

        uploaded = failed = 0
        for row in missing:
            # A tuple rather than "the marker or a sentinel", because ``None``
            # is a real *success* outcome here: the logo is on the item, but
            # Plex reported no uploaded logo selected, so there is nothing to
            # record. The upload still counts -- the item has its logo -- and
            # the revert simply will not claim it later.
            ok, marker = await self._upload_one(row)
            if not ok:
                failed += 1
                continue
            await self._record_marker(session, row, marker)
            uploaded += 1

        if missing_from_plex:
            logger.info("logo: skipped %d item(s) no longer in Plex", missing_from_plex)

        return LogoUpdateResult(
            total, items_missing_logo, uploaded, failed, dry_run=False,
            missing=missing_from_plex,
        )

    async def _record_marker(self, session: AsyncSession, row, marker: str | None) -> None:
        """Commit this item's marker before the next item's upload starts.

        Per item, not once at the end of the run, because the Plex side of this
        loop is per item and immediate: by the time the second item uploads,
        the first item's logo is already on the server and locked. One commit
        at the end would mean any interruption in between -- a redeploy, a
        proxy timeout on this long inline request, a cancelled task, a database
        error -- unwinds the session with EVERY marker in it uncommitted, while
        the logos they name stay on Plex. Those logos would then be
        unrevertable (the revert's first condition is that the column is set)
        and unre-markable (a re-run skips them, because Plex now reports a
        clearlogo for them). Committing here costs one round trip per uploaded
        item and leaves an interrupted run partial but truthful.

        A database error is this item's own, like every failure in
        ``_upload_one``: the session is rolled back so the next item's write
        starts clean. The item still counts as uploaded, because it *is* --
        the logo is on it. That is the same outcome as a successful upload
        Plex reported no key for: a logo the revert will not claim later.
        """
        try:
            await session.execute(
                update(MediaItem)
                .where(MediaItem.id == row.id)
                .values(logo_upload_key=marker)
            )
            await session.commit()
        except Exception:  # noqa: BLE001 - see the docstring
            await session.rollback()
            logger.warning(
                "logo: uploaded a clearlogo for %s but could not record its "
                "marker -- the revert will not claim it", row.rating_key, exc_info=True,
            )

    async def _upload_one(self, row) -> tuple[bool, str | None]:
        """Fetch and push one item's logo: ``(succeeded, marker to record)``.

        Every failure is this item's own: a provider that had nothing, a pick
        Plex cannot use, a download that did not arrive, an upload that did not
        go through. None of them may abort a run over a whole library.
        """
        selection = await select_artwork(
            self._providers,
            self._config.artwork.logo_language_order,
            art.ArtRequest(
                art_kind=art.LOGO,
                is_movie=row.kind == "movie",
                tmdb_id=row.tmdb_id,
                tvdb_id=row.tvdb_id,
                imdb_id=row.imdb_id,
            ),
        )
        candidate = selection.candidate
        if candidate is None:
            logger.warning("logo: no clearlogo on any provider for %s", row.rating_key)
            return False, None

        suffix = Path(httpx.URL(candidate.url).path).suffix or ".png"
        if suffix.lower() == UNUPLOADABLE_LOGO_SUFFIX:
            logger.warning(
                "logo: skipping %s for %s -- Plex's clearLogo field takes a "
                "raster image", candidate.url, row.rating_key,
            )
            return False, None

        try:
            plex_item = await self._plex.fetch_item(row.rating_key)
            response = await self._http.get(candidate.url, follow_redirects=True)
            response.raise_for_status()
            marker = await asyncio.to_thread(
                upload_logo, plex_item, response.content, suffix
            )
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning(
                "logo: could not upload a clearlogo for %s", row.rating_key, exc_info=True
            )
            return False, None
        return True, marker


class LogoRevertMode:
    """Remove the clearlogos this service set, and only those. See the module docstring."""

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

    async def run(self, session: AsyncSession) -> LogoRevertResult:
        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            # dry_run mirrors the success paths below -- see restore.py.
            return LogoRevertResult(0, 0, 0, 0, not self._apply, refused=empty)

        conditions = _conditions(self._kind, self._library, self._item_id)
        # The candidate count is every filtered item, as the reset mode counts
        # it, so the cap reads "N of M items in this library would change"
        # rather than "N of the M we already marked" -- which would be near
        # 100% on every run and so would never mean anything.
        total = (
            await session.execute(
                select(func.count()).select_from(MediaItem).where(*conditions)
            )
        ).scalar_one()

        marked = (
            await session.execute(
                select(MediaItem.id, MediaItem.rating_key, MediaItem.logo_upload_key)
                .where(MediaItem.logo_upload_key.is_not(None), *conditions)
                .order_by(MediaItem.id)
            )
        ).all()
        # End the read transaction before the probe phase: what follows is one
        # Plex request per marked item, then a clear per item of ours. The
        # marker clearing below opens its own transaction. See reset.py for why
        # the rows survive the commit.
        await session.commit()

        # Marked is not enough: Plex must still be showing that exact upload.
        # An operator who has since replaced our logo with their own made a
        # choice, and this mode does not undo the operator's choices.
        ours = []
        missing = 0
        for row in marked:
            try:
                plex_item = await self._plex.fetch_item(row.rating_key)
                selected = await asyncio.to_thread(selected_uploaded_logo_key, plex_item)
            except PlexNotFound:
                # Expected, not a crash: the item was deleted/moved in Plex
                # since it was marked. One concise line, no traceback --
                # counted separately from a real probe failure below
                # (backup.py's PR #112 hotfix shape).
                logger.info("logo revert: %s no longer in Plex, skipped", row.rating_key)
                missing += 1
                continue
            except Exception:  # noqa: BLE001 - one bad item must not abort the probe
                logger.warning(
                    "logo revert: could not probe Plex item %s",
                    row.rating_key, exc_info=True,
                )
                continue
            if selected == row.logo_upload_key:
                ours.append(row)

        items_with_our_logo = len(ours)
        refusal = refuse_if_implausible(
            items_with_our_logo, total,
            self._config.artwork_modes.max_changes,
            self._config.artwork_modes.max_change_share,
        )
        if refusal is not None:
            if missing:
                logger.info("logo revert: skipped %d item(s) no longer in Plex", missing)
            return LogoRevertResult(
                total, items_with_our_logo, 0, 0, not self._apply,
                refused=refusal, missing=missing,
            )

        if not self._apply:
            if missing:
                logger.info("logo revert: skipped %d item(s) no longer in Plex", missing)
            return LogoRevertResult(
                total, items_with_our_logo, 0, 0, dry_run=True, missing=missing
            )

        cleared = failed = 0
        for row in ours:
            try:
                plex_item = await self._plex.fetch_item(row.rating_key)
                await asyncio.to_thread(clear_logo, plex_item)
            except PlexNotFound:
                logger.info("logo revert: %s no longer in Plex, skipped", row.rating_key)
                missing += 1
                continue
            except Exception:  # noqa: BLE001 - see above
                logger.warning(
                    "logo revert: could not clear the clearlogo for %s",
                    row.rating_key, exc_info=True,
                )
                failed += 1
                continue
            # The marker goes with the logo: there is nothing of ours left on
            # the item to find. A failed clear keeps its marker, because its
            # logo is still ours and still there.
            await session.execute(
                update(MediaItem)
                .where(MediaItem.id == row.id)
                .values(logo_upload_key=None)
            )
            cleared += 1
        await session.commit()

        if missing:
            logger.info("logo revert: skipped %d item(s) no longer in Plex", missing)

        return LogoRevertResult(
            total, items_with_our_logo, cleared, failed, dry_run=False, missing=missing
        )
