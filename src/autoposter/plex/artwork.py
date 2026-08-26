"""Upload badged artwork to Plex, and read what Plex is serving back off it.

Every upload here goes through a temporary file, removed on both the success and
the failure path. Not because plexapi requires one -- its ``utils.openOrRead``
takes bytes and file-likes as happily as a path -- but so the three upload
targets (poster, art, clearlogo) hand plexapi the same kind of thing, with the
same removal handling, rather than one of them being the odd one out.

Reading back comes in two depths over the same fetch path: ``artwork_provenance``
wants only the EXIF fingerprint and pays a range request or two for it, while
``fetch_artwork`` wants the whole image because a human is about to look at it.
Both derive the URL the same way, through ``_artwork_url``.

The clearlogo target (``upload_logo`` and its inverse ``clear_logo``) is a
third, separate field: Plex's ``clearLogo``, not the ``thumb``/``art`` pair the
badged images go to. It is deliberately not another ``art_kind`` routed through
``upload_artwork``, because routing it there would make a caller that passes
``"logo"`` by mistake upload a poster.
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
# argument in main.py and collections/__main__.py, so it uses plexapi.TIMEOUT,
# 30s. Worst case against a Plex that accepts connections and then says
# nothing is therefore ~45s, not 15s.
ARTWORK_FETCH_TIMEOUT = 15.0

# How Plex keys an image somebody pushed to the server -- this service's own
# uploads included. Everything else a ``posters()`` or ``arts()`` listing returns
# came from a metadata agent, so this prefix is what separates "art we (or an
# operator) put there" from "art Plex found for itself".
UPLOADED_ARTWORK_PREFIX = "upload://"


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


def upload_logo(plex_item, data: bytes, suffix: str = ".png") -> str | None:
    """Upload one clearlogo to Plex's ``clearLogo`` field, lock it, and return
    the ``upload://`` rating key Plex filed it under -- ``None`` if Plex reports
    no uploaded logo selected afterwards.

    A separate target from ``upload_artwork``, not another ``art_kind`` routed
    through it: the clearlogo is its own Plex field (``uploadLogo``/``lockLogo``,
    plexapi >= 4.16), not the ``thumb``/``art`` pair the badged images go to. The
    temp-file dance is the same for symmetry with ``upload_artwork`` rather than
    from necessity -- plexapi's ``openOrRead`` accepts a file-like object, so
    bytes could be handed over directly, but one upload path that behaves two
    ways is a worse trade than one temp file. It is removed on both paths.

    Locking is for the same reason ``upload_artwork`` locks: without it Plex's
    metadata agent can reclaim the field and replace what we just uploaded.

    The returned key is the logo revert's marker. It is read back rather than
    assumed because only Plex knows what it keyed the upload as, and the revert
    compares it against what is selected *at revert time* -- that comparison is
    what keeps it off a logo an operator has since replaced ours with.
    """
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        # Closed by the ``with`` even when the write itself fails, so the
        # ``finally`` below never unlinks a file that is still open.
        with handle:
            handle.write(data)
        plex_item.uploadLogo(filepath=handle.name)
        plex_item.lockLogo()
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            logger.warning("could not remove temporary upload file %s", handle.name)
    return selected_uploaded_logo_key(plex_item)


def selected_uploaded_logo_key(plex_item) -> str | None:
    """The rating key of the clearlogo Plex currently shows, if somebody uploaded it.

    ``None`` for an item showing agent-supplied art, for one showing nothing, and
    for a listing entry without a usable key -- everything, that is, except "an
    uploaded image is selected right now". The ``upload://`` restriction matters
    on both sides of the marker: an agent's rating key could never equal one we
    recorded, so admitting one here could only ever produce a false match.

    Blocking: ``logos()`` is an HTTP GET through plexapi. Callers offload it.
    """
    for entry in plex_item.logos():
        if not getattr(entry, "selected", False):
            continue
        rating_key = getattr(entry, "ratingKey", "") or ""
        return rating_key if rating_key.startswith(UPLOADED_ARTWORK_PREFIX) else None
    return None


def clear_logo(plex_item) -> None:
    """Unlock the ``clearLogo`` field and remove the logo currently on it.

    The inverse of :func:`upload_logo`, and unlike the poster reset this really
    does remove the image rather than leaving it behind as an orphaned
    ``upload://`` entry: Plex exposes a DELETE for the clearlogo field where it
    exposes none for a poster. The unlock happens first so the agent is free to
    fill the field on its own next pass.

    Never ``.refresh()``, which the project-wide AST guard forbids: it would have
    Plex re-pull from its agents and revert locked fields elsewhere.
    """
    plex_item.unlockLogo()
    plex_item.deleteLogo()


async def has_clearlogo(plex_item) -> bool:
    """Whether Plex is showing a clearlogo for ``plex_item``.

    ``plexapi``'s ``logo`` is a *property* computed from the item's ``images``
    rather than a parsed attribute like ``thumb``, so reading it on a partial
    object can trigger a blocking ``_reload()`` HTTP GET -- a plain metadata
    read, not ``refresh()``. Offloaded for the same reason ``_artwork_url``
    offloads its own field read: this runs once per item across a whole library.
    """
    return bool(await asyncio.to_thread(getattr, plex_item, "logo", None))


def _agent_default(listing):
    """The entry in a ``posters()``/``arts()`` listing that is Plex's own agent art.

    ``None`` when the listing holds nothing provably from an agent -- an item
    whose agents never found an image, so there is no default to hand the field
    back to.

    Plex returns uploaded and agent-supplied images from the one call and tells
    them apart by rating key (``upload://...`` vs the agent's own), listing the
    agent's in the agent's preference order. So the first non-upload entry *is*
    the default Plex would pick for itself. ``ratingKey`` is read defensively
    and an entry without one is *skipped*, not returned: a listing entry with no
    key is not something we can prove is agent art, and selecting it would hand
    the field an image of unknown origin. Skipping costs the caller a ``False``
    -- field unlocked, nothing selected -- which is the safe direction.
    """
    for entry in listing:
        rating_key = getattr(entry, "ratingKey", "") or ""
        if not rating_key or rating_key.startswith(UPLOADED_ARTWORK_PREFIX):
            continue
        return entry
    return None


def reset_artwork_to_agent_default(plex_item, art_kind: str) -> bool:
    """Unlock one artwork field on ``plex_item`` and hand it back to Plex's agent.

    The inverse of what ``upload_artwork`` does: that uploads and *locks* so the
    agent cannot reclaim the field, and this unlocks and re-selects the agent's
    own image. ``True`` when an agent image was found and selected, ``False``
    when Plex holds none.

    Routed on ``art_kind`` exactly as ``upload_artwork`` routes, and it has to
    stay the inverse of it: ``background`` is Plex's ``art`` field, and every
    other kind -- an episode's title card included -- is its poster. The three
    calls are picked up front so the unlock/select sequence itself is written
    once rather than per field. ``art_kind`` is required for the same reason
    ``upload_artwork``'s is: a caller that forgets which field it meant should
    say so, not silently be handed the poster.

    The unlock happens first and unconditionally, so an item with no agent art
    still ends up unlocked -- that half is what the operator asked for, and it
    lets the agent fill the field on its own next pass. The caller counts the
    ``False`` as a failed reset even so, because the visible artwork did not
    change.

    Never ``.refresh()``, which the project-wide AST guard forbids: it would
    have Plex re-pull from its agents, reverting locked fields elsewhere.
    ``setPoster``/``setArt`` are targeted selects and need no reload afterwards
    -- nothing reads this object again once the selection is made.
    """
    is_background = art_kind == "background"
    unlock = plex_item.unlockArt if is_background else plex_item.unlockPoster
    listing = plex_item.arts if is_background else plex_item.posters
    select = plex_item.setArt if is_background else plex_item.setPoster

    unlock()
    chosen = _agent_default(listing())
    if chosen is None:
        return False
    select(chosen)
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


async def artwork_provenance(
    http, plex_item, base_url: str, headers: dict, art_kind: str = "poster"
) -> str | None:
    """The fingerprint recorded in ``plex_item``'s currently-selected artwork.

    ``None`` if the item has no artwork of that kind, the request fails, or what
    is there was never stamped by us. A format-aware range read via
    ``probe_exif`` -- see ``autoposter.plex.exif`` -- rather than a full
    download: one request for a JPEG or an unstamped WebP, two only when the
    WebP header says there is an EXIF chunk at the end worth fetching.

    ``art_kind`` picks the field, through the same ``PLEX_ART_FIELDS`` map the
    rest of this module routes on, and defaults to the poster -- which is what
    the badge stage's probe asks about. Reset asks about the background too.

    The field is read in a thread -- see ``_artwork_url`` -- because on a
    partial ``plexapi`` object attribute access can trigger a blocking
    ``_reload()`` HTTP GET, and this is called per item across a whole library.

    Every failure answers ``None``. The caller treats that as "no usable
    provenance" and does the normal upload, so a Plex hiccup costs one
    redundant upload rather than an exception out of the badge stage.
    """
    try:
        url = await _artwork_url(plex_item, base_url, art_kind)
        if url is None:
            return None
        tags = await probe_exif(http, url, headers)
    except Exception:
        logger.debug("could not read artwork provenance", exc_info=True)
        return None
    return parse_provenance(tags.get(PROVENANCE_TAG))
