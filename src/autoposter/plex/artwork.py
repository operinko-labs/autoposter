"""Upload badged artwork to Plex, and read what Plex is serving back off it.

plexapi's upload methods take a filepath rather than bytes, so the encoded
image goes to a temporary file that is removed on both the success and the
failure path.

Reading back comes in two depths over the same fetch path: ``artwork_provenance``
wants only the EXIF fingerprint and pays a range request or two for it, while
``fetch_artwork`` wants the whole image because a human is about to look at it.
Both derive the URL the same way, through ``_artwork_url``.
"""
import asyncio
import logging
import os
import tempfile

from autoposter.plex.exif import PROVENANCE_TAG, parse_provenance, probe_exif

logger = logging.getLogger(__name__)

# Which Plex field holds the artwork for one of our art kinds. The inverse of
# ``upload_artwork``'s split below, and it has to stay the inverse: an episode's
# badged image is its *poster*, so ``background`` is the only kind that is Plex's
# ``art``. Reading ``.art`` for a title card would answer with the show's
# backdrop -- a real image, plausibly rendered, and the wrong one.
PLEX_ART_FIELDS = {
    "poster": "thumb",
    "season_poster": "thumb",
    "title_card": "thumb",
    "background": "art",
}

# The whole image, unlike the provenance probe's few kilobytes, over a link that
# may be a LAN hop or a tunnel. Long enough for a poster on a slow one; short
# enough that a caller waiting on a dead Plex is told so rather than left
# holding a spinner for the client default.
#
# It bounds *this* request only. A caller that also resolves the item through
# plexapi first (api/artwork.py's live endpoint does) pays that leg's timeout as
# well, and plexapi's is its own: PlexServer is constructed without a `timeout`
# argument in main.py and app.py, so it uses plexapi.TIMEOUT, 30s. Worst case
# against a Plex that accepts connections and then says nothing is therefore
# ~45s, not 15s.
ARTWORK_FETCH_TIMEOUT = 15.0

# How Plex keys an image somebody pushed to the server -- this service's own
# uploads included. Everything else a ``posters()`` listing returns came from a
# metadata agent, so this prefix is what separates "art we (or an operator) put
# there" from "art Plex found for itself".
UPLOADED_POSTER_PREFIX = "upload://"


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
        # Closed by the ``with`` even when the write itself fails, so the
        # ``finally`` below never unlinks a file that is still open.
        with handle:
            handle.write(data)
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


def _agent_default_poster(posters):
    """The entry in a ``posters()`` listing that is Plex's own agent art.

    ``None`` when the listing holds nothing but uploads -- an item whose agents
    never found a poster, so there is no default to hand the field back to.

    Plex returns uploaded and agent-supplied images from the one call and tells
    them apart by rating key (``upload://...`` vs the agent's own), listing the
    agent's in the agent's preference order. So the first non-upload entry *is*
    the default Plex would pick for itself. ``ratingKey`` is read defensively:
    a listing entry without one is not something we can prove is agent art.
    """
    for poster in posters:
        rating_key = getattr(poster, "ratingKey", "") or ""
        if rating_key.startswith(UPLOADED_POSTER_PREFIX):
            continue
        return poster
    return None


def reset_poster_to_agent_default(plex_item) -> bool:
    """Unlock ``plex_item``'s poster and hand the field back to Plex's agent art.

    The inverse of what ``upload_artwork`` does: that uploads and *locks* so the
    agent cannot reclaim the field, and this unlocks and re-selects the agent's
    own image. ``True`` when an agent poster was found and selected, ``False``
    when Plex holds none.

    The unlock happens first and unconditionally, so an item with no agent art
    still ends up unlocked -- that half is what the operator asked for, and it
    lets the agent fill the field on its own next pass. The caller counts the
    ``False`` as a failed reset even so, because the visible poster did not
    change.

    Never ``.refresh()``, which the project-wide AST guard forbids: it would
    have Plex re-pull from its agents, reverting locked fields elsewhere.
    ``setPoster`` is a targeted select and needs no reload afterwards -- nothing
    reads this object again once the selection is made.
    """
    plex_item.unlockPoster()
    poster = _agent_default_poster(plex_item.posters())
    if poster is None:
        return False
    plex_item.setPoster(poster)
    return True


async def _artwork_url(plex_item, base_url: str, art_kind: str = "poster") -> str | None:
    """The absolute URL Plex serves ``plex_item``'s current artwork from.

    ``None`` when the item has no artwork of that kind at all.

    The field is read in a thread: on a partial ``plexapi`` object attribute
    access can trigger a blocking ``_reload()`` HTTP GET. That is a plain GET
    for the item's own metadata -- it is not ``refresh()``, which would ask
    Plex to re-pull from its agents and can overwrite the artwork we uploaded.
    """
    field = PLEX_ART_FIELDS.get(art_kind, "thumb")
    path = await asyncio.to_thread(getattr, plex_item, field, None)
    if not path:
        return None
    return f"{base_url.rstrip('/')}{path}"


async def fetch_artwork(
    http, plex_item, base_url: str, headers: dict, art_kind: str = "poster",
    timeout: float = ARTWORK_FETCH_TIMEOUT,
) -> tuple[bytes, str] | None:
    """The bytes Plex is currently serving for ``plex_item``, and their type.

    ``None`` when Plex has no artwork of that kind -- either the field is empty,
    the URL it names 404s, or the body that comes back is empty, all of which
    are the same answer to the caller and a different one from "Plex could not
    be asked". An empty body in particular must not become a 200 with zero
    bytes: the UI draws that as a broken image rather than as "nothing here".

    Deliberately *not* defensive, unlike ``artwork_provenance``: this answers a
    person who opened a page, so a transport failure has to reach them as
    "Plex is not answering" rather than being swallowed into "no artwork".
    Raises ``httpx.HTTPError`` for both, and carries its own ``timeout`` so a
    Plex that accepts the connection and then says nothing cannot hold the
    request open for however long the shared client's default happens to be.
    """
    url = await _artwork_url(plex_item, base_url, art_kind)
    if url is None:
        return None
    response = await http.get(url, headers=headers, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    if not response.content:
        return None
    return response.content, response.headers.get("content-type", "")


async def artwork_provenance(http, plex_item, base_url: str, headers: dict) -> str | None:
    """The fingerprint recorded in ``plex_item``'s currently-selected artwork.

    ``None`` if the item has no artwork, the request fails, or what is there
    was never stamped by us. A format-aware range read via ``probe_exif`` --
    see ``autoposter.plex.exif`` -- rather than a full download: one request
    for a JPEG or an unstamped WebP, two only when the WebP header says there
    is an EXIF chunk at the end worth fetching.

    ``.thumb`` is read in a thread -- see ``_artwork_url`` -- because on a
    partial ``plexapi`` object attribute access can trigger a blocking
    ``_reload()`` HTTP GET, and this is called per item across a whole library.

    Every failure answers ``None``. The caller treats that as "no usable
    provenance" and does the normal upload, so a Plex hiccup costs one
    redundant upload rather than an exception out of the badge stage.
    """
    try:
        url = await _artwork_url(plex_item, base_url)
        if url is None:
            return None
        tags = await probe_exif(http, url, headers)
    except Exception:
        logger.debug("could not read artwork provenance", exc_info=True)
        return None
    return parse_provenance(tags.get(PROVENANCE_TAG))
