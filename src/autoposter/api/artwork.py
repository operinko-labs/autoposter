"""Serving artwork bytes to the UI.

The library browser and the item detail view need to show what this project
rendered, and until now nothing served an image at all -- the API returned
fingerprints and statuses and the SPA had nothing to draw.

The one thing to be careful about here is that ``renders.asset_path`` is a
full filesystem path read out of the database, so the obvious implementation
is ``FileResponse(render.asset_path)`` and the obvious implementation is an
arbitrary-file-read primitive. The database is not a trust boundary: a
poisoned row, an ``assets_root`` repointed after the rows were written, a
future import path that takes a path from somewhere else -- any of those turn
that one line into "hand the caller any file this process can read". So every
path is resolved and checked against the asset tree before a byte is read.

Its sibling ``live_artwork`` serves what Plex is currently showing instead, so
the detail view can put the two side by side. That one reads nothing from disk
-- the badged image is never written anywhere -- and proxies Plex.
"""
import asyncio
import logging
import os
from pathlib import Path

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from plexapi.exceptions import NotFound as PlexNotFound
from plexapi.exceptions import PlexApiException
from sqlalchemy import select

from autoposter.api.auth import require_session
from autoposter.db.models import MediaItem, Render
from autoposter.db.models import Session as SessionModel
from autoposter.plex.artwork import PLEX_ART_FIELDS, fetch_artwork
from autoposter.render.pipeline import ART_KINDS_FOR

logger = logging.getLogger(__name__)

# Keyed by the file's own suffix, never by anything the caller sent. Not
# `mimetypes.guess_type`: that consults /etc/mime.types and, on Windows, the
# registry, so the content type of the same file would depend on the host the
# service happens to run on. The render pipeline writes .jpg (see
# render/naming.py); the rest are here so a hand-placed override in another
# format is still labelled correctly.
CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
FALLBACK_CONTENT_TYPE = "application/octet-stream"
# On every response that carries bytes. The content-type whitelists above are
# the whole defence -- there is no security-headers middleware in this project
# -- and these are the first endpoints serving image bytes from our own origin,
# one of them remote-controlled, so a browser must not be allowed to sniff its
# way to a different interpretation of the body.
NOSNIFF = {"X-Content-Type-Options": "nosniff"}
# Behind a session, so no shared cache may keep a copy, and always revalidated
# so a re-render is picked up immediately -- the revalidation is what the ETag
# below makes cheap (a 304 with no body and no file read).
CACHE_CONTROL = "private, max-age=0, must-revalidate"
# What a Content-Type coming back from Plex is allowed to become on our own
# response. Plex's header is remote input, and echoing it verbatim would let
# whatever is at that URL choose how a browser interprets the body; anything
# unrecognised is served as an opaque download instead.
ALLOWED_UPSTREAM_TYPES = frozenset(CONTENT_TYPES.values())

router = APIRouter()


class OutsideAssetsRoot(Exception):
    """A renders row whose asset_path resolves outside the asset tree."""


def _read_asset(asset_path: str, assets_root: Path) -> bytes:
    """Return the bytes of ``asset_path``, having proven it is inside the tree.

    Both sides are put through ``realpath`` before being compared, so a
    symlink planted inside ``assets_root`` and pointing at ``/etc/passwd`` is
    rejected on its target rather than accepted on its name. A purely lexical
    check -- ``normpath``, or comparing the strings -- would let that through.

    Synchronous, and called from a thread: ``assets_root`` can be an NFS
    mount, so the ``realpath`` walk, the ``stat`` and the read all have to
    stay off the event loop that also carries the workers, the scheduler and
    the liveness probe.
    """
    root = Path(os.path.realpath(assets_root))
    resolved = Path(os.path.realpath(asset_path))
    if resolved == root or not resolved.is_relative_to(root):
        raise OutsideAssetsRoot(f"{asset_path!r} resolves to {resolved}, outside {root}")
    # A path that is not a regular file -- missing, or a directory -- is the
    # 404 below rather than the IsADirectoryError/FileNotFoundError a bare
    # read_bytes() would turn into a 500.
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    return resolved.read_bytes()


def _if_none_match(header: str | None, etag: str) -> bool:
    """Does the client's ``If-None-Match`` name the entity we would serve?

    The weak comparison RFC 9110 requires for this header: ``W/"x"`` and
    ``"x"`` match each other, and ``*`` matches anything we have.
    """
    if not header:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == etag:
            return True
    return False


@router.get("/items/{item_id}/artwork/{art_kind}")
async def base_artwork(
    item_id: int,
    art_kind: str,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> Response:
    """The base image this project rendered for one item and art kind.

    ``art_kind`` selects a row -- ``(item_id, art_kind)`` is unique -- and is
    never joined onto a filesystem path. The path comes from the row.

    404, not 500, when the row points at a file that is not there: a render
    row is created before anything is written to disk, so ``no_art``,
    ``truncated``, ``skipped`` and ``failed`` rows all legitimately name a
    file that was never produced.

    The library browser's grid calls this once per tile, so a matching
    ``If-None-Match`` answers 304 before the file is opened at all. The ETag is
    ``renders.base_sha256`` -- the digest of the very bytes served, already on
    the row -- so it changes exactly when a re-render changes the image.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        render = (
            await session.execute(
                select(Render).where(Render.item_id == item_id, Render.art_kind == art_kind)
            )
        ).scalar_one_or_none()
    if render is None:
        raise HTTPException(status_code=404, detail="artwork not found")

    headers = dict(NOSNIFF)
    if render.base_sha256:
        # Nullable: a row can exist before anything is rendered into it. No
        # digest, no ETag -- an invented one would be a lie about the bytes.
        headers["ETag"] = f'"{render.base_sha256}"'
        headers["Cache-Control"] = CACHE_CONTROL
        if _if_none_match(request.headers.get("if-none-match"), headers["ETag"]):
            return Response(status_code=304, headers=headers)

    assets_root = Path(request.app.state.config.assets_root)
    try:
        content = await asyncio.to_thread(_read_asset, render.asset_path, assets_root)
    except OutsideAssetsRoot as exc:
        # Worth a warning rather than a silent 404: nothing this project
        # writes can produce such a row, so seeing one means either the
        # database was tampered with or assets_root no longer points where the
        # rows were written -- both of which the operator needs to know about.
        logger.warning("refusing to serve render %d: %s", render.id, exc)
        raise HTTPException(status_code=404, detail="artwork not found") from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="artwork not found") from None

    suffix = Path(render.asset_path).suffix.lower()
    return Response(
        content=content,
        media_type=CONTENT_TYPES.get(suffix, FALLBACK_CONTENT_TYPE),
        headers=headers,
    )


@router.get("/items/{item_id}/artwork/{art_kind}/live")
async def live_artwork(
    item_id: int,
    art_kind: str,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> Response:
    """The artwork Plex is actually serving for one item, beside which the
    detail view shows the base image from ``base_artwork``.

    Proxied rather than recomposed. The badged image is never persisted --
    ``badges.compose.compose()`` returns bytes that go straight to Plex -- so
    the only way to answer "is the right image live?" is to ask Plex. Building
    it again would put badge-complexity CPU on the thread pool for a casually
    opened page, and would answer "what would we generate?" instead.

    Three outcomes the UI has to tell apart, so they get three statuses:
    ``404`` when Plex has no such item or no artwork of that kind, ``503``
    when Plex cannot be asked, and the bytes otherwise.
    """
    # Not a filesystem path here -- this one picks the plexapi field. Checked
    # anyway so an unknown kind is a 404 rather than quietly answering with the
    # poster, which PLEX_ART_FIELDS.get's default would do.
    if art_kind not in PLEX_ART_FIELDS:
        raise HTTPException(status_code=404, detail="unknown art kind")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        row = (
            await session.execute(
                select(MediaItem.rating_key, MediaItem.kind).where(MediaItem.id == item_id)
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="item not found")
    rating_key, kind = row

    # The kind has to be checked against the item, not only against the field
    # map: plexapi exposes `.art` on an Episode, so /title-card-item/background
    # would otherwise answer 200 with the *show's* backdrop while the base
    # endpoint 404s the same request for want of a render row. The two are read
    # side by side, so they have to agree on what exists.
    if art_kind not in ART_KINDS_FOR.get(kind, ()):
        raise HTTPException(
            status_code=404, detail=f"a {kind} has no {art_kind}"
        )

    plex = request.app.state.plex
    http = request.app.state.http
    if plex is None or http is None:
        # No Plex connection in this process -- the background services that
        # own it are not running. Not the caller's fault and not permanent.
        #
        # Logged because the body reads as "Plex is down" while the cause is
        # local wiring: app.state.plex comes from main.build() and
        # app.state.http from the lifespan's run_background branch, so an
        # operator seeing this and finding nothing in the log would go looking
        # at their Plex server instead of at this process.
        missing = " and ".join(
            name for name, value in (("plex", plex), ("http", http)) if value is None
        )
        logger.warning(
            "cannot serve live artwork for item %d: app.state.%s unset, so this "
            "process has no Plex connection",
            item_id, missing,
        )
        raise HTTPException(status_code=503, detail="this instance is not connected to Plex")

    try:
        # A plain GET for the item (see PlexClient.fetch_item); never
        # .refresh(), which would have Plex re-pull from its agents and can
        # overwrite the artwork this service uploaded.
        plex_item = await plex.fetch_item(rating_key)
    except PlexNotFound:
        raise HTTPException(status_code=404, detail="Plex no longer has this item") from None
    except (requests.RequestException, PlexApiException) as exc:
        logger.warning("could not reach Plex for item %d: %s", item_id, exc)
        raise HTTPException(
            status_code=503, detail=f"Plex could not be reached ({type(exc).__name__})"
        ) from None

    config = request.app.state.config
    headers = {"X-Plex-Token": request.app.state.secrets.plex_token}
    try:
        fetched = await fetch_artwork(http, plex_item, config.plex.url, headers, art_kind)
    except httpx.HTTPStatusError as exc:
        logger.warning("Plex refused the artwork request for item %d: %s", item_id, exc)
        raise HTTPException(
            status_code=503, detail=f"Plex answered {exc.response.status_code}"
        ) from None
    except httpx.HTTPError as exc:
        logger.warning("could not fetch artwork from Plex for item %d: %s", item_id, exc)
        raise HTTPException(
            status_code=503, detail=f"Plex could not be reached ({type(exc).__name__})"
        ) from None
    except (requests.RequestException, PlexApiException) as exc:
        # Not only httpx: fetch_artwork starts by reading .thumb/.art off the
        # plexapi object, and on a partial object plexapi answers a None-valued
        # attribute with a blocking _reload() GET -- through `requests`, and
        # exactly on the "this item has no artwork of that kind" path that is
        # meant to answer 404. A Plex that drops between the two calls would
        # otherwise leave this raising out of the handler as a 500.
        logger.warning("could not read artwork metadata from Plex for item %d: %s", item_id, exc)
        raise HTTPException(
            status_code=503, detail=f"Plex could not be reached ({type(exc).__name__})"
        ) from None

    if fetched is None:
        raise HTTPException(status_code=404, detail="Plex has no artwork for this item")

    content, upstream_type = fetched
    media_type = upstream_type.split(";")[0].strip().lower()
    # This endpoint's whole meaning is "right now", and no validator is sent,
    # so make that explicit rather than leaving heuristic caching to decide.
    headers = dict(NOSNIFF, **{"Cache-Control": "no-store"})
    return Response(
        content=content,
        media_type=media_type if media_type in ALLOWED_UPSTREAM_TYPES else FALLBACK_CONTENT_TYPE,
        headers=headers,
    )
