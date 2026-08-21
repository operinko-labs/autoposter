"""Upload badged artwork to Plex, and read our own provenance back from it.

plexapi's upload methods take a filepath rather than bytes, so the encoded
image goes to a temporary file that is removed on both the success and the
failure path.
"""
import asyncio
import logging
import os
import tempfile

from autoposter.plex.exif import PROVENANCE_TAG, parse_provenance, probe_exif

logger = logging.getLogger(__name__)


def upload_artwork(plex_item, data: bytes, art_kind: str, lock: bool = True) -> None:
    """Upload one badged image and lock the field.

    An episode's badged image is its *poster*, not its art -- that is what Plex
    displays as the episode thumbnail -- so only ``background`` routes to
    ``uploadArt``.

    Locking matters: without it Plex's metadata agent can reclaim the field and
    replace what we just uploaded.
    """
    is_background = art_kind == "background"
    handle = tempfile.NamedTemporaryFile(suffix=".webp", delete=False)
    try:
        handle.write(data)
        handle.close()
        if is_background:
            plex_item.uploadArt(filepath=handle.name)
            if lock:
                plex_item.lockArt()
        else:
            plex_item.uploadPoster(filepath=handle.name)
            if lock:
                plex_item.lockPoster()
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            logger.warning("could not remove temporary upload file %s", handle.name)


async def artwork_provenance(http, plex_item, base_url: str, headers: dict) -> str | None:
    """The fingerprint recorded in ``plex_item``'s currently-selected artwork.

    ``None`` if the item has no artwork, the request fails, or what is there
    was never stamped by us. A format-aware range read via ``probe_exif`` --
    see ``autoposter.plex.exif`` -- rather than a full download: one request
    for a JPEG or an unstamped WebP, two only when the WebP header says there
    is an EXIF chunk at the end worth fetching.

    ``.thumb`` is read in a thread: on a partial ``plexapi`` object attribute
    access can trigger a blocking ``_reload()`` HTTP GET, and this is called
    per item across a whole library.

    Every failure answers ``None``. The caller treats that as "no usable
    provenance" and does the normal upload, so a Plex hiccup costs one
    redundant upload rather than an exception out of the badge stage.
    """
    try:
        thumb = await asyncio.to_thread(getattr, plex_item, "thumb", None)
        if not thumb:
            return None
        url = f"{base_url.rstrip('/')}{thumb}"
        tags = await probe_exif(http, url, headers)
    except Exception:
        logger.debug("could not read artwork provenance", exc_info=True)
        return None
    return parse_provenance(tags.get(PROVENANCE_TAG))
