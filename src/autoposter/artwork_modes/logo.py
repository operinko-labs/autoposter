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
import tempfile
from dataclasses import dataclass
from pathlib import Path

from plexapi.exceptions import NotFound as PlexNotFound
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import refuse_if_empty, refuse_if_implausible
from autoposter.db.models import MediaItem
from autoposter.plex.artwork import selected_uploaded_logo_key
from autoposter.providers import base as art
from autoposter.render.artwork_fetch import pick_guarded_logo
from autoposter.servers.base import CAP_LOGO_UPLOAD_KEY

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


@dataclass(frozen=True)
class LogoUpdateResult:
    """What a logo update run did (or would do), or a refusal.

    ``items`` is the candidate set after the filters and the movie/show
    restriction; ``items_missing_logo`` is how many of those Plex currently
    shows no clearlogo for -- the items that would change, which the
    plausibility cap is measured against.

    There is no ``fields``/``files`` count as the reset and revert modes have:
    an item has exactly one clearlogo, so the per-item and per-field tallies
    would be the same number twice. On an applied run four counters split
    ``items_missing_logo`` by outcome, and the split is by the question the
    operator actually asks next:

    * ``uploaded`` -- the logo is on Plex AND a marker records which upload is
      ours, so Logo revert can claim it back;
    * ``unmarked`` -- the logo is on Plex but nothing records it, so the revert
      never will. Both ways there is no marker count here: the marker write
      failed, and Plex reported no ``upload://`` key to record. They are the
      same fact for the operator, because the revert's first condition is a
      non-null ``logo_upload_key``;
    * ``no_logo_available`` -- the ladder offered nothing usable: no provider
      had a logo, or every candidate was over the pixel ceiling, undecodable,
      or an SVG Plex's clearLogo field cannot take;
    * ``upload_failed`` -- a candidate was picked and the fetch or the upload
      itself did not go through.

    There is deliberately no ``failed`` total: a coarse number served beside
    its own summands reads, on the Modes page, as more failures than there
    were.

    ``probe_failed`` is the third whole-item bucket, beside ``missing``: an item
    the probe could not ask Plex about at all. Unlike the applied-run counters
    it is served on a DRY RUN too, because that is the run it matters on -- an
    unreachable Plex would otherwise report ``items_missing_logo: 0`` and read
    as "nothing to do" rather than "nothing could be asked".
    """

    items: int
    items_missing_logo: int
    uploaded: int
    unmarked: int
    no_logo_available: int
    upload_failed: int
    dry_run: bool
    missing: int = 0
    probe_failed: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "logo",
            "dry_run": self.dry_run,
            "items": self.items,
            "items_missing_logo": self.items_missing_logo,
            "missing": self.missing,
            "probe_failed": self.probe_failed,
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
            body["unmarked"] = self.unmarked
            body["no_logo_available"] = self.no_logo_available
            body["upload_failed"] = self.upload_failed
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

    ``probe_failed`` counts marked items the probe could not ask Plex about at
    all, beside ``missing``. A marked item that cannot be probed is not "not
    ours" -- it is unknown -- and it is served on a dry run for the same reason
    the updater's is.
    """

    items: int
    items_with_our_logo: int
    cleared: int
    failed: int
    dry_run: bool
    missing: int = 0
    probe_failed: int = 0
    refused: str | None = None

    def as_response(self) -> dict:
        body = {
            "mode": "logo_revert",
            "dry_run": self.dry_run,
            "items": self.items,
            "items_with_our_logo": self.items_with_our_logo,
            "missing": self.missing,
            "probe_failed": self.probe_failed,
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
        self._server = plex
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
            return LogoUpdateResult(
                0, 0, 0, 0, 0, 0, not self._apply, refused=empty
            )

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
        probe_failed = 0
        for row in rows:
            try:
                ref = await self._server.fetch_ref(row.rating_key)
            except Exception:  # noqa: BLE001 - one bad item must not abort the probe
                logger.warning(
                    "logo: could not probe Plex item %s", row.rating_key, exc_info=True
                )
                probe_failed += 1
                continue
            if ref is None:
                # A stale rating_key (item deleted/moved in Plex) is expected,
                # not a crash -- one concise INFO line, no traceback, tallied
                # separately from a probe failure (backup.py's PR #112 hotfix
                # shape).
                logger.info("logo: %s no longer in Plex, skipped", row.rating_key)
                missing_from_plex += 1
                continue
            if not await self._server.has_clearlogo(ref):
                missing.append((row, ref))

        items_missing_logo = len(missing)
        refusal = refuse_if_implausible(
            items_missing_logo, total,
            self._config.artwork_modes.max_changes,
            self._config.artwork_modes.max_change_share,
        )
        if refusal is not None:
            if missing_from_plex:
                logger.info("logo: skipped %d item(s) no longer in Plex", missing_from_plex)
            if probe_failed:
                logger.info("logo: could not probe %d item(s)", probe_failed)
            return LogoUpdateResult(
                total, items_missing_logo, 0, 0, 0, 0, not self._apply,
                refused=refusal, missing=missing_from_plex, probe_failed=probe_failed,
            )

        if not self._apply:
            if missing_from_plex:
                logger.info("logo: skipped %d item(s) no longer in Plex", missing_from_plex)
            if probe_failed:
                logger.info("logo: could not probe %d item(s)", probe_failed)
            return LogoUpdateResult(
                total, items_missing_logo, 0, 0, 0, 0, dry_run=True,
                missing=missing_from_plex, probe_failed=probe_failed,
            )

        uploaded = unmarked = no_logo_available = upload_failed = 0
        for row, ref in missing:
            # An outcome name rather than a bool, because "it did not work" is
            # two different operator problems here: nothing usable was on the
            # ladder, and the upload did not go through.
            outcome, marker = await self._upload_one(row, ref)
            if outcome == "no_logo_available":
                no_logo_available += 1
                continue
            if outcome == "upload_failed":
                upload_failed += 1
                continue
            # The logo is on the item either way. What splits the two buckets
            # is whether a marker records it: without one the revert's first
            # condition can never hold, so the operator has to be told which
            # of these it can claim back.
            if await self._record_marker(session, row, marker):
                uploaded += 1
            else:
                unmarked += 1

        if missing_from_plex:
            logger.info("logo: skipped %d item(s) no longer in Plex", missing_from_plex)
        if probe_failed:
            logger.info("logo: could not probe %d item(s)", probe_failed)

        return LogoUpdateResult(
            total, items_missing_logo, uploaded, unmarked, no_logo_available,
            upload_failed, dry_run=False, missing=missing_from_plex,
            probe_failed=probe_failed,
        )

    async def _record_marker(self, session: AsyncSession, row, marker: str | None) -> bool:
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
        starts clean. Returns whether the item ends up with a marker the revert
        can find -- False both when the write failed and when Plex reported no
        ``upload://`` key to record, because those are the same fact for the
        operator: the logo is on Plex and the revert will not claim it.
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
            return False
        # The write itself is still made when ``marker`` is None: it clears any
        # stale key an earlier run left on an item whose logo has since gone.
        return marker is not None

    async def _upload_one(self, row, ref) -> tuple[str, str | None]:
        """Fetch and push one item's logo: ``(outcome, marker to record)``.

        The outcome is ``"uploaded"``, ``"no_logo_available"`` (the ladder
        offered nothing this field can take) or ``"upload_failed"`` (a pick was
        made and the fetch or the upload threw). Every failure is this item's
        own: a provider that had nothing, a set of picks Plex cannot use, a
        download that did not arrive, an upload that did not go through. None
        of them may abort a run over a whole library -- and the caller counts
        the two apart, because they are two different operator problems.

        The fetch goes through ``render/artwork_fetch.pick_guarded_logo``, the
        same guarded walk the render path uses, rather than the bare
        ``http.get`` this method used to make. That bare GET was the second
        door onto job 40478's 'Inside Out 2' clearlogo -- a 32000x18839 PNG
        (602,848,000px) that ``providers/ladder.rank_key`` ranks FIRST because
        it sorts on ``-pixels``. It missed the ``RENDER_MAX_BYTES`` cap and the
        full-decode validation entirely, and took the ladder's first answer
        with no ceiling and no way to ask for the next one, so a title whose
        best logo was a bomb had that bomb pushed to Plex -- or, once it failed,
        had nothing pushed on every run after that.

        ``raster_only=True``: the render path composites an SVG clearlogo
        happily (ImageMagick rasterises it), but Plex's ``clearLogo`` field
        takes a raster image, so an SVG is skipped and the ladder re-asked.

        The ``ArtRequest`` is byte-for-byte the one this method always built --
        no ``prefer_clearart``, no season or episode numbers, which a movie or
        show row has none of. That is deliberate: ``exclude_urls`` starts
        empty, so an item whose best clearlogo is under the ceiling asks once
        and gets exactly the pick it got before this guard existed. The guard
        re-uploads for the affected items only; it does not move the library.
        """
        try:
            with tempfile.TemporaryDirectory() as tmp:
                logo_path, _sha, skipped = await pick_guarded_logo(
                    self._http,
                    self._providers,
                    self._config.artwork.logo_language_order,
                    art.ArtRequest(
                        art_kind=art.LOGO,
                        is_movie=row.kind == "movie",
                        tmdb_id=row.tmdb_id,
                        tvdb_id=row.tvdb_id,
                        imdb_id=row.imdb_id,
                    ),
                    Path(tmp),
                    native_id=row.rating_key,
                    raster_only=True,
                )
                if logo_path is None:
                    # One line, naming the item and nothing else -- each
                    # skipped candidate has already been logged with its own
                    # reason by `pick_guarded_logo`, and neither line carries a
                    # provider URL (row 209).
                    logger.warning(
                        "logo: no usable clearlogo for %s -- %d candidate(s) skipped",
                        row.rating_key, skipped,
                    )
                    return "no_logo_available", None
                # Read inside the temporary directory's scope: it is removed on
                # the way out of this `with`, and `upload_logo` wants bytes.
                data = logo_path.read_bytes()
                marker = await self._server.upload_logo(ref, data, logo_path.suffix)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning(
                "logo: could not upload a clearlogo for %s", row.rating_key, exc_info=True
            )
            return "upload_failed", None
        return "uploaded", marker


class LogoRevertMode:
    """Remove the clearlogos this service set, and only those. See the module docstring."""

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
        probe_failed = 0
        # selected_uploaded_logo_key stays Plex-only -- Jellyfin (or any server
        # without CAP_LOGO_UPLOAD_KEY) never wrote a marker, so `marked` is
        # empty for it in practice; this guard is the explicit statement of
        # that rather than a reliance on the data happening to be empty.
        if CAP_LOGO_UPLOAD_KEY in self._server.capabilities:
            for row in marked:
                try:
                    plex_item = await self._server.fetch_item(row.rating_key)
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
                    probe_failed += 1
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
            if probe_failed:
                logger.info("logo revert: could not probe %d item(s)", probe_failed)
            return LogoRevertResult(
                total, items_with_our_logo, 0, 0, not self._apply,
                refused=refusal, missing=missing, probe_failed=probe_failed,
            )

        if not self._apply:
            if missing:
                logger.info("logo revert: skipped %d item(s) no longer in Plex", missing)
            if probe_failed:
                logger.info("logo revert: could not probe %d item(s)", probe_failed)
            return LogoRevertResult(
                total, items_with_our_logo, 0, 0, dry_run=True,
                missing=missing, probe_failed=probe_failed,
            )

        cleared = failed = 0
        for row in ours:
            try:
                ref = await self._server.fetch_ref(row.rating_key)
            except Exception:  # noqa: BLE001 - see above
                logger.warning(
                    "logo revert: could not fetch Plex item %s",
                    row.rating_key, exc_info=True,
                )
                failed += 1
                continue
            if ref is None:
                logger.info("logo revert: %s no longer in Plex, skipped", row.rating_key)
                missing += 1
                continue
            try:
                await self._server.clear_logo(ref)
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
        if probe_failed:
            logger.info("logo revert: could not probe %d item(s)", probe_failed)

        return LogoRevertResult(
            total, items_with_our_logo, cleared, failed, dry_run=False,
            missing=missing, probe_failed=probe_failed,
        )
