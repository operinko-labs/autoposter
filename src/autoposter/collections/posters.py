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
import asyncio
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

# Award event key -> its folder under ``award/`` in ``Default-Images``. Kometa's
# own folder names, read off its defaults (``image: award/oscars/...``,
# ``image: award/golden/...``) and confirmed to exist in the repository, not
# derived from the event key -- "golden_globes" would have been ``golden``
# wrong. An event missing here keeps no poster at all: a guessed path either
# 404s (a poster nobody notices is missing) or, worse, resolves to some other
# ceremony's artwork.
AWARD_SEGMENTS = {"oscars": "oscars", "golden_globes": "golden"}


class PosterPathRefused(Exception):
    """A collection's poster path does not land inside ``assets_root``.

    Raised rather than returned, and raised from the one place the layout is
    spelled (``_poster_candidates``), so neither the reconciler's read nor the
    manual endpoint's write can be the caller that forgot to check.
    """


def hosted_poster_url(kind: str, key: str) -> str | None:
    """The default poster URL for a collection of the given kind, or ``None``.

    ``kind`` is one of ``award_static``, ``award_year``, ``chart``,
    ``content_rating``, ``content_rating_other``, ``separator``; ``key`` is
    the piece that varies. Only the chart key is URL-encoded -- the others
    have no spaces, and encoding the year path's slash would break
    ``award/oscars/winner/2026``. An unrecognised kind returns ``None``
    rather than guessing: a wrong URL 404s and the collection quietly keeps
    no poster, which is harder to spot than an error.

    The two award kinds take an **event-scoped** key, ``"<event>:<stem>"`` --
    ``"oscars:best_picture_winner"``, ``"golden_globes:2026"``. The event half
    is a key of ``AWARD_SEGMENTS``; anything else returns ``None`` by the same
    rule as an unrecognised kind, which is what makes "this ceremony has no
    hosted artwork" expressible rather than a wrong path.
    """
    if kind in ("award_static", "award_year"):
        event, _, stem = key.partition(":")
        segment = AWARD_SEGMENTS.get(event)
        if segment is None or not stem:
            return None
        if kind == "award_static":
            return f"{DEFAULT_IMAGES_BASE}/award/{segment}/{stem}.jpg"
        return f"{DEFAULT_IMAGES_BASE}/award/{segment}/winner/{stem}.jpg"
    if kind == "chart":
        return f"{DEFAULT_IMAGES_BASE}/chart/color/{quote(key, safe='')}.jpg"
    if kind == "content_rating":
        return f"{DEFAULT_IMAGES_BASE}/content_rating/cs/{key}.jpg"
    if kind == "content_rating_other":
        return f"{DEFAULT_IMAGES_BASE}/content_rating/cs/NR.jpg"
    if kind == "separator":
        return f"{DEFAULT_IMAGES_BASE}/separators/orig/{key}.jpg"
    return None


def _poster_candidates(config: Config, library: str, title: str) -> tuple[Path, ...]:
    """Every path a local poster for this collection could occupy, in order.

    One spelling of the layout, so the lookup below and the manual-poster
    endpoint that *writes* one cannot disagree about where it lives -- the
    failure mode of two spellings is a file written successfully and then
    never looked at, which is what ``manual_override_target`` exists to
    prevent on the item side.

    ``library`` and ``title`` are interpolated verbatim, and neither is a
    value this service chose: a title comes from operator config or from a
    collection adopted out of Plex, where ``../`` and a leading ``/`` are both
    legal characters. So every candidate is contained here (roadmap row 124),
    by the double-``realpath`` idiom ``api/manual.py`` uses for the manual
    assets mount -- both sides resolved before being compared, so a directory
    inside the root that symlinks out of it is caught on its target rather
    than waved through by a lexical check. Joining an absolute path onto the
    root yields the absolute path (pathlib's rule), so ``/etc`` needs no case
    of its own; it simply resolves outside.

    The candidates are returned unresolved. The comparison is what needed the
    realpath, not the value -- and callers compare the returned path against
    the layout they expect.
    """
    root = Path(config.assets_root)
    if config.library_folders:
        folder = root / library / title
        candidates = tuple(folder / f"poster.{ext}" for ext in _LOCAL_EXTENSIONS)
    else:
        candidates = tuple(root / f"{title}.{ext}" for ext in _LOCAL_EXTENSIONS)

    real_root = Path(os.path.realpath(root))
    for candidate in candidates:
        if not Path(os.path.realpath(candidate)).is_relative_to(real_root):
            raise PosterPathRefused(
                "the title steers its path outside the assets root"
            )
    return candidates


def poster_override_target(config: Config, library: str, title: str) -> Path:
    """Where an operator-supplied poster for this collection is written.

    The first candidate, which is the ``jpg`` one -- and that ordering is
    load-bearing rather than incidental: the endpoint transcodes to JPEG, and
    because ``jpg`` is probed first a poster left behind in another format by
    an earlier hand-placement cannot shadow the new file.

    Raises ``PosterPathRefused`` when the title steers the write outside
    ``assets_root``; the caller turns that into a refusal rather than a write.
    """
    return _poster_candidates(config, library, title)[0]


def local_poster_path(config: Config, library: str, title: str) -> Path | None:
    """The operator's own poster for this collection, if one exists.

    Checked in ``jpg``, ``jpeg``, ``png``, ``webp`` order at
    ``<assets_root>/<library>/<title>/poster.<ext>``, or -- when
    ``config.library_folders`` is false -- the flat layout's
    ``<assets_root>/<title>.<ext>``. This is how ``prioritize_assets: true``
    worked in the tool being replaced: a file here overrides the hosted
    default, so it must never be silently skipped in favour of a download.

    Raises ``PosterPathRefused`` for a title that escapes ``assets_root``:
    "no local poster" and "this collection's path is not ours to read" are
    different answers, and returning None for the second would let the hosted
    default be applied to a collection whose identity is already suspect.
    """
    for candidate in _poster_candidates(config, library, title):
        if candidate.is_file():
            return candidate
    return None


def posters_enabled(config, http: httpx.AsyncClient | None) -> bool:
    """Whether the reconcilers should run their poster step at all.

    Both reconcilers reach ``apply_poster`` from two places now -- a
    definition that changed, and a row whose ``poster_sha256`` is still NULL
    -- so the gate lives here rather than being spelled out at each one.
    """
    return config is not None and http is not None and config.collections.posters


def _is_image(data: bytes) -> bool:
    """Whether ``data`` decodes as an image.

    Every byte string that reaches an upload goes through here, whichever
    branch produced it. Uploading a non-image would be worse than uploading
    nothing: it would also get hashed and recorded, so a later pass would
    never retry it.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception:
        return False
    return True


def _write_and_upload(collection, data: bytes) -> None:
    """Write ``data`` to a temporary file, upload it, and lock the field.

    Blocking: ``uploadPoster`` is a synchronous ``requests`` POST of the whole
    image, so callers run this in a thread. The file is written inside a
    ``with`` so it is closed even when the write fails, and removed on both
    the success and the failure path.

    Locking matters for the same reason it does in ``plex/artwork.py``:
    without it Plex's metadata agent can reclaim the field, and a reclaimed
    poster would never be re-applied because the hash still matches.
    """
    handle = tempfile.NamedTemporaryFile(delete=False)
    try:
        with handle:
            handle.write(data)
        collection.uploadPoster(filepath=handle.name)
        collection.lockPoster()
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            logger.warning("could not remove temporary poster file %s", handle.name)


async def fetch_poster(http: httpx.AsyncClient, url: str) -> bytes | None:
    """Fetch a poster and validate it, or ``None`` on any failure.

    A 200 response is not proof of an image -- a failure mode upstream can
    still answer 200 with an HTML body -- so the body is checked with
    ``_is_image`` before being trusted.
    """
    try:
        response = await http.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        logger.info("could not fetch poster from %s", url)
        return None
    data = response.content
    if not _is_image(data):
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

    Both branches are validated with ``_is_image``: an operator's file can be
    truncated, zero-byte, or an HTML error page saved as ``poster.jpg`` just
    as easily as a response body can. An unusable local file falls through to
    the hosted default rather than failing the collection outright.

    plexapi's ``uploadPoster`` only accepts a filepath, so the bytes are
    written to a temporary file, never through its ``url=`` form: that makes
    the Plex server fetch the image itself, so we would neither see nor hash
    what actually landed. That write and the upload are a blocking
    ``requests`` POST of the whole image, so they run in
    ``asyncio.to_thread`` -- the same treatment ``render/pipeline.py`` gives
    ``upload_artwork``. Left on the loop, a first pass over 51 collections
    would stall the scheduler and the liveness probe for the duration.

    Under ``dry_run`` the poster is still resolved and fetched -- that is
    deliberate, so the report can say whether the source is reachable -- but
    nothing is uploaded and ``record.poster_sha256`` is left untouched.

    Any failure (no source, an unreachable URL, a non-image body) leaves the
    collection untouched and is reported rather than raised: a missing
    poster is cosmetic and must never fail the surrounding pass.
    """
    data: bytes | None = None
    source = ""
    try:
        local = local_poster_path(config, library, record.title)
    except PosterPathRefused as exc:
        # Row 124. Refused rather than falling through to the hosted default:
        # the objection is to the collection's title, which the local branch
        # and the manual endpoint's write both interpolate, so a collection
        # that fails it has no poster path at all rather than an unusable one.
        logger.warning("refused a poster path for %r: %s", record.title, exc)
        return "refused a poster for %r: %s" % (record.title, exc)
    if local is not None:
        candidate = local.read_bytes()
        if _is_image(candidate):
            data = candidate
            source = "local file %s" % local
        else:
            logger.info(
                "local poster %s did not decode as an image; using the hosted default", local
            )
    if data is None:
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

    try:
        await asyncio.to_thread(_write_and_upload, collection, data)
    except Exception:
        logger.exception("failed to upload the poster for %r", record.title)
        return "failed to upload the poster for %r" % record.title

    record.poster_sha256 = digest
    await session.flush()
    return "set the poster for %r from %s" % (record.title, source)
