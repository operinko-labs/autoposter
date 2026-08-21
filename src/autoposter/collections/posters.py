"""Choosing which poster a managed collection should carry.

Two pure decisions: which hosted URL a collection's default poster lives at
(``hosted_poster_url``), and whether the operator has placed a local override
that should win instead (``local_poster_path``). Fetching and uploading is a
separate, later step -- this module never makes a network call.

The hosted defaults come from ``Kometa-Team/Default-Images``, a repository
with no LICENSE file and no licence statement, so the sibling
``Kometa-Team/Kometa`` MIT grant does not literally extend to it -- and in any
case an MIT grant only covers what the licensor owns, not the IMDb, AMPAS or
Common Sense marks these images embed. This is a private single-operator
deployment: the images are fetched at runtime rather than vendored into this
repository, and it replaces a tool (Kometa) that does exactly the same thing.
That is a lighter posture than ``assets/badges/``, which *are* committed
here. If this repository is ever published, revisit this alongside
``assets/badges/PROVENANCE.md``.
"""
import hashlib
import io
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.schema import Config
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)

DEFAULT_IMAGES_BASE = "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"

_LOCAL_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


def hosted_poster_url(kind: str, key: str) -> str | None:
    """The default poster URL for a collection of the given kind, or ``None``.

    ``kind`` is one of ``award_static``, ``award_year``, ``chart``,
    ``content_rating``, ``content_rating_other``, ``separator``; ``key`` is
    the piece that varies. Only the chart key is URL-encoded -- the others
    have no spaces, and encoding the year path's slash would break
    ``award/oscars/winner/2026``. An unrecognised kind returns ``None``
    rather than guessing: a wrong URL 404s and the collection quietly keeps
    no poster, which is harder to spot than an error.
    """
    if kind == "award_static":
        return f"{DEFAULT_IMAGES_BASE}/award/oscars/{key}.jpg"
    if kind == "award_year":
        return f"{DEFAULT_IMAGES_BASE}/award/oscars/winner/{key}.jpg"
    if kind == "chart":
        return f"{DEFAULT_IMAGES_BASE}/chart/color/{quote(key, safe='')}.jpg"
    if kind == "content_rating":
        return f"{DEFAULT_IMAGES_BASE}/content_rating/cs/{key}.jpg"
    if kind == "content_rating_other":
        return f"{DEFAULT_IMAGES_BASE}/content_rating/cs/NR.jpg"
    if kind == "separator":
        return f"{DEFAULT_IMAGES_BASE}/separators/orig/{key}.jpg"
    return None


def local_poster_path(config: Config, library: str, title: str) -> Path | None:
    """The operator's own poster for this collection, if one exists.

    Checked in ``jpg``, ``jpeg``, ``png``, ``webp`` order at
    ``<assets_root>/<library>/<title>/poster.<ext>``, or -- when
    ``config.library_folders`` is false -- the flat layout's
    ``<assets_root>/<title>.<ext>``. This is how ``prioritize_assets: true``
    worked in the tool being replaced: a file here overrides the hosted
    default, so it must never be silently skipped in favour of a download.
    """
    root = Path(config.assets_root)
    if config.library_folders:
        folder = root / library / title
        candidates = (folder / f"poster.{ext}" for ext in _LOCAL_EXTENSIONS)
    else:
        candidates = (root / f"{title}.{ext}" for ext in _LOCAL_EXTENSIONS)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


async def fetch_poster(http: httpx.AsyncClient, url: str) -> bytes | None:
    """Fetch a poster and validate it, or ``None`` on any failure.

    A 200 response is not proof of an image -- a failure mode upstream can
    still answer 200 with an HTML body -- so the response is opened with
    Pillow before being trusted. Uploading that page as a collection poster
    would be worse than uploading nothing: it would also get hashed and
    recorded, so a later pass would never retry it.
    """
    try:
        response = await http.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        logger.info("could not fetch poster from %s", url)
        return None
    data = response.content
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception:
        logger.info("poster at %s did not decode as an image", url)
        return None
    return data


async def apply_poster(
    session: AsyncSession,
    http: httpx.AsyncClient,
    config: Config,
    collection,
    record: ManagedCollection,
    library: str,
    kind: str,
    key: str,
    dry_run: bool = True,
) -> str | None:
    """Give ``collection`` its poster, uploading only when something changed.

    Resolution order: a local override first (read directly off disk, no
    request made), the hosted default second, nothing third. The bytes are
    hashed and compared against ``record.poster_sha256`` -- a match means an
    unchanged pass uploads nothing, the same guarantee ``definition_hash``
    already gives the collection's filter.

    plexapi's ``uploadPoster`` only accepts a filepath, so the bytes are
    written to a ``NamedTemporaryFile`` that is removed on both the success
    and the failure path, never through its ``url=`` form: that makes the
    Plex server fetch the image itself, so we would neither see nor hash
    what actually landed.

    Under ``dry_run`` the poster is still resolved and fetched -- that is
    deliberate, so the report can say whether the source is reachable -- but
    nothing is uploaded and ``record.poster_sha256`` is left untouched.

    Any failure (no source, an unreachable URL, a non-image body) leaves the
    collection untouched and is reported rather than raised: a missing
    poster is cosmetic and must never fail the surrounding pass.
    """
    local = local_poster_path(config, library, record.title)
    if local is not None:
        data = local.read_bytes()
        source = "local file %s" % local
    else:
        url = hosted_poster_url(kind, key)
        if url is None:
            return "no poster source for %r" % record.title
        data = await fetch_poster(http, url)
        if data is None:
            return "could not fetch a usable poster for %r from %s" % (record.title, url)
        source = "the hosted default"

    digest = hashlib.sha256(data).hexdigest()
    if digest == record.poster_sha256:
        return None

    if dry_run:
        return "would set the poster for %r from %s" % (record.title, source)

    handle = tempfile.NamedTemporaryFile(delete=False)
    try:
        handle.write(data)
        handle.close()
        collection.uploadPoster(filepath=handle.name)
    except Exception:
        logger.exception("failed to upload the poster for %r", record.title)
        return "failed to upload the poster for %r" % record.title
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            logger.warning("could not remove temporary poster file %s", handle.name)

    record.poster_sha256 = digest
    await session.flush()
    return "set the poster for %r from %s" % (record.title, source)
