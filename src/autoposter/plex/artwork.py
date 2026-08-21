"""Upload badged artwork to Plex.

plexapi's upload methods take a filepath rather than bytes, so the encoded
image goes to a temporary file that is removed on both the success and the
failure path.
"""
import logging
import os
import tempfile

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
