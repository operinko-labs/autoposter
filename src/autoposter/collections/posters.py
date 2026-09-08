"""Choosing which poster a managed collection should carry.

Three pure decisions: which hosted URL a collection's default poster lives at
(``hosted_poster_url``), which TMDb image URL a person's profile path points to
(``tmdb_profile_url`` -- the one source that is not Kometa's ``Default-Images``,
and the reason is on the function), and whether the operator has placed a local
override that should win instead (``local_poster_path``). Fetching and uploading
is a separate, later step -- these three never make a network call.

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
from typing import NamedTuple

import httpx
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from autoposter.collections.groups import SEPARATOR_STYLES
from autoposter.collections.poster_title import (
    CollectionTitleRefused,
    compose_collection_title,
)
from autoposter.config.schema import Config
from autoposter.db.models import ManagedCollection
from autoposter.net.guard import BodyRefused, FetchRefused, guarded_download
from autoposter.providers.tmdb import IMAGE_BASE

logger = logging.getLogger(__name__)

DEFAULT_IMAGES_BASE = "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"

# The one poster kind whose URL is not a ``Default-Images`` path. Named here and
# imported by the builder that emits it, so the producer and the consumer cannot
# drift into two spellings of one string.
TMDB_PROFILE_KIND = "tmdb_profile"

# The one kind whose art already carries its collection's name, whether this
# service generated it (``separator_art.py`` bakes the divider's title in) or
# fetched upstream's captioned ``separators/<style>/<stem>.jpg``. Named here
# so the producer below and the row-105 composite cannot drift into two
# spellings of one string.
SEPARATOR_KIND = "separator"

# Roadmap row 222. The one fixed phrase the definition's own poster URL is ever
# reported as. NEVER the URL: it is an operator's own string, it can carry
# ``user:password@`` userinfo, a signed query parameter or the name of an
# internal host, and this value is interpolated into ``apply_poster``'s return
# string, which ``collections/service.py`` serves and logs.
DEFINITION_POSTER_SOURCE = "the definition's poster URL"

# What that rung is allowed to fetch, and how much of it. The same values and
# the same reasoning as ``api/candidates.PICK_MAX_BYTES`` /
# ``PICK_CONTENT_TYPES``: far above any real poster, low enough that a body
# which simply never stops arriving is refused rather than kept. Declared here
# rather than imported because nothing under ``collections/`` imports
# ``api/``, and this rung must not be the first to -- the same argument
# ``render/artwork_fetch.RENDER_MAX_BYTES`` records for its own copy.
DEFINITION_POSTER_MAX_BYTES = 50 * 1024 * 1024
DEFINITION_POSTER_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

_LOCAL_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


class AwardImages(NamedTuple):
    """Where one ceremony's two kinds of poster live under ``award/``.

    Both are Kometa's own folder names, read off the two ``image:`` values in
    its ``defaults/award/<file>.yml`` -- the ``collections:`` block's for the
    static winners collection, the ``dynamic_collections:`` block's for the
    year ones -- and each confirmed to exist in ``Default-Images``. Neither is
    derived from the event key: "golden_globes" would have been ``golden``
    wrong, and "emmy" would have been ``emmys`` wrong.

    They are two fields because they are genuinely two folders. Only the
    Oscars, the Golden Globes and the Emmys keep their year images under a
    ``winner/`` subfolder; the other thirteen ceremonies keep them beside the
    static image, and the National Film Registry has no ``winner/`` folder at
    all -- so deriving one path from the other would 404 in one direction for
    thirteen ceremonies and in the other for one.
    """

    static: str
    year: str


# Award event key -> its folders. An event missing here keeps no poster at
# all: a guessed path either 404s (a poster nobody notices is missing) or,
# worse, resolves to some other ceremony's artwork.
AWARD_SEGMENTS = {
    "oscars": AwardImages("oscars", "oscars/winner"),
    "golden_globes": AwardImages("golden", "golden/winner"),
    "bafta": AwardImages("bafta", "bafta"),
    "berlinale": AwardImages("berlinale", "berlinale"),
    "cannes": AwardImages("cannes", "cannes"),
    "cesar": AwardImages("cesar", "cesar"),
    "choice": AwardImages("choice", "choice"),
    "emmy": AwardImages("emmys", "emmys/winner"),
    "nfr": AwardImages("nfr", "nfr"),
    "pca": AwardImages("pca", "pca"),
    "razzie": AwardImages("razzie", "razzie"),
    "sag": AwardImages("sag", "sag"),
    "spirit": AwardImages("spirit", "spirit"),
    "sundance": AwardImages("sundance", "sundance"),
    "tiff": AwardImages("tiff", "tiff"),
    "venice": AwardImages("venice", "venice"),
}


class PosterPathRefused(Exception):
    """A collection's poster path does not land inside ``assets_root``.

    Raised rather than returned, and raised from the one place the layout is
    spelled (``_poster_candidates``), so neither the reconciler's read nor the
    manual endpoint's write can be the caller that forgot to check.
    """


def hosted_poster_url(kind: str, key: str) -> str | None:
    """The default poster URL for a collection of the given kind, or ``None``.

    ``kind`` is one of ``award_static``, ``award_year``, ``content_rating``,
    ``content_rating_other``, ``separator``; ``key`` is the piece that varies.
    No key here is URL-encoded: none of them carries a space, and encoding the
    year path's slash would break ``award/oscars/winner/2026``. ``chart`` was
    the one encoded kind and is a ``default_images`` family since roadmap row
    252 -- ``candidate_urls`` builds the byte-identical URL there, and leaving
    the branch here as a fallback would have re-fetched a proven 404 a second
    time on every pass. An unrecognised kind returns ``None``
    rather than guessing: a wrong URL 404s and the collection quietly keeps
    no poster, which is harder to spot than an error.

    The two award kinds take an **event-scoped** key, ``"<event>:<stem>"`` --
    ``"oscars:best_picture_winner"``, ``"golden_globes:2026"``. The event half
    is a key of ``AWARD_SEGMENTS``; anything else returns ``None`` by the same
    rule as an unrecognised kind, which is what makes "this ceremony has no
    hosted artwork" expressible rather than a wrong path.

    The separator kind takes a **style-scoped** key in the same shape,
    ``"<style>:<stem>"`` -- ``"orig:chart"``, ``"sand:@content"``. The style
    half is one of ``groups.SEPARATOR_STYLES``, and a stem starting with ``@``
    names art this service GENERATES rather than fetches, so both return
    ``None`` here: "no hosted path for this key" is the honest answer for a
    style that does not exist and for art that was never upstream's.
    """
    if kind in ("award_static", "award_year"):
        event, _, stem = key.partition(":")
        images = AWARD_SEGMENTS.get(event)
        if images is None or not stem:
            return None
        folder = images.static if kind == "award_static" else images.year
        return f"{DEFAULT_IMAGES_BASE}/award/{folder}/{stem}.jpg"
    if kind == "content_rating":
        return f"{DEFAULT_IMAGES_BASE}/content_rating/cs/{key}.jpg"
    if kind == "content_rating_other":
        return f"{DEFAULT_IMAGES_BASE}/content_rating/cs/NR.jpg"
    if kind == SEPARATOR_KIND:
        # "<style>:<stem>", the award kinds' idiom one branch up. A stem
        # starting with '@' is GENERATED art (``separator_art.py``) and has no
        # hosted path; an unknown style is refused the same way -- a wrong URL
        # 404s and the collection quietly keeps no poster, which is harder to
        # spot than an absence. The config validator is the loud refusal for a
        # bad style; this is the belt behind it.
        style, _, stem = key.partition(":")
        if style not in SEPARATOR_STYLES or not stem or stem.startswith("@"):
            return None
        return f"{DEFAULT_IMAGES_BASE}/separators/{style}/{stem}.jpg"
    return None


def tmdb_profile_url(profile_path: str) -> str | None:
    """The image-CDN URL for one TMDb profile path, or ``None``.

    Deliberately not a row of ``hosted_poster_url``'s table. Every kind there is
    a ``Default-Images`` path built from a key THIS SERVICE chose -- a chart
    name, a ceremony year, a content-rating bucket -- and the whole reason that
    function can refuse an unrecognised kind is that it owns the vocabulary. A
    profile path is an opaque file path TMDb handed back on a person's own
    record; the two share nothing but returning a string, and one table holding
    both would have a column of curated folder names with a provider's opaque
    path in it.

    NOT KOMETA: this is upstream's *fallback* person poster, not its primary
    one. Kometa's people packs set ``url_poster`` from the
    ``Kometa-Team/People-Images-<<style>>`` repositories
    (``defaults/templates.yml:216``), and ``url_poster``
    (``modules/library.py:453``) outranks ``tmdb_person``
    (``modules/library.py:462``) on a first-match-wins ladder -- so in a stock
    run the hosted photo wins and the TMDb one is only reached when it is
    absent. Our kind name is ours; upstream's attribute for this behaviour is
    ``tmdb_person``, and its own ``tmdb_profile`` -- higher on that same
    ladder -- is something else. We ship the fallback alone: the hosted
    source is keyed by the person's *name* across six per-style repositories,
    and our person collections are keyed by TMDb *id*, so adopting it would
    be new machinery rather than reuse. There is no ``Default-Images`` people
    folder to fall back on either -- checked 2026-08-29, it does not exist --
    so no people URL is to be derived from ``AWARD_SEGMENTS``' pattern.

    ``IMAGE_BASE`` is ``providers/tmdb.py``'s, the same base the artwork
    pipeline fetches every other TMDb image from, so the size segment is
    configured in one place rather than two. Its ``original`` segment matches
    upstream's byte-for-byte, though upstream reads the base from TMDb's
    ``/configuration`` at startup and we pin the string -- same value, and one
    fewer request per run.

    A path TMDb did not shape -- empty, or not rooted at ``/`` -- returns
    ``None`` by the same rule an unrecognised kind does: a guessed URL 404s and
    the collection quietly keeps no poster, which is harder to spot than an
    error.
    """
    if not profile_path or not profile_path.startswith("/"):
        return None
    return f"{IMAGE_BASE}{profile_path}"


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


async def download_definition_poster(http: httpx.AsyncClient, url: str) -> bytes:
    """Fetch a definition's ``poster_url`` through the SSRF guard.

    Deliberately NOT ``fetch_poster`` above, and this is roadmap row 222's
    security decision rather than a style one. That function is a bare
    ``http.get`` on the shared client with no scheme allowlist, no address
    check and no redirect control. It is safe today only because every URL it
    has ever been handed was built by this repository from
    ``DEFAULT_IMAGES_BASE``, ``providers.tmdb.IMAGE_BASE`` or
    ``default_images.candidate_urls`` -- ``net/guard.py`` names this module by
    name as a reason it passes ``follow_redirects=False`` per request instead
    of configuring the shared client's defaults. A ``poster_url`` is the first
    operator-typed string this module has ever fetched, so it goes through
    ``guarded_download`` like every other operator-supplied source
    (``api/manual.py``'s ``_staged_source``, ``overlays/sources.py``'s image
    cache), inheriting the guard's two documented residuals -- DNS rebinding,
    and NAT64/6to4 literals -- unchanged and unre-litigated.

    The bytes are then put through ``_is_image``, which is both the gate every
    other rung in this module passes and the same class of check
    ``api/manual.py::install_collection_poster`` gets from
    ``api/candidates._prepare_jpeg``: a Content-Type is the other end's claim
    and only a decoder settles it. There is no transcode here because there is
    nothing to transcode INTO -- that endpoint writes a file literally named
    ``poster.jpg`` and has to make the name true, whereas this rung hands bytes
    to ``_write_and_upload``, which names its own temporary file.

    The download lands in a temporary directory rather than the
    ``.generated/`` cache: what stops a re-upload is ``record.poster_sha256``
    (see ``apply_poster``), and what stops a re-FETCH is the caller's
    short-circuit -- ``lists.py`` does not reach the poster block at all while
    the membership hash is current and a poster is already recorded.

    Raises ``FetchRefused`` (the guard's own refusals, whose messages carry a
    reason and never the URL) or ``httpx.HTTPError`` (a transport failure,
    whose message CAN embed the URL and must not be repeated). The caller
    reports; nothing here raises out of a pass.
    """
    with tempfile.TemporaryDirectory() as workspace:
        destination = Path(workspace) / "poster"
        await guarded_download(
            http, url, destination,
            max_bytes=DEFINITION_POSTER_MAX_BYTES,
            content_types=DEFINITION_POSTER_CONTENT_TYPES,
        )
        data = destination.read_bytes()
    if not _is_image(data):
        raise BodyRefused("the body did not decode as an image")
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
    generated: Path | None = None,
    poster_url: str | None = None,
) -> str | None:
    """Give ``collection`` its poster, uploading only when something changed.

    Resolution order: a local override first (read directly off disk, no
    request made), the definition's own ``poster_url`` second (roadmap row
    222 -- fetched through the SSRF guard, never through ``fetch_poster``),
    a file some source already produced third when one resolves -- the
    caller's generated separator art (``collections/separator_art.py``), or
    this collection's family poster cached from
    ``Kometa-Team/Default-Images`` (``collections/default_images.py``), both
    read directly off disk -- the source ``kind`` names fourth (a hosted
    default from the six-kind table, or a person's TMDb profile photo),
    nothing fifth. The bytes are hashed and compared against
    ``record.poster_sha256`` -- a match means an unchanged pass uploads
    nothing, the same guarantee ``definition_hash`` already gives the
    collection's filter.

    ``poster_url`` sits below the local file and above everything this
    service can find for itself, which is the priority roadmap row 222 asked
    for: a file on disk still wins, because an operator who put one there
    meant it -- and ``api/manual.py``'s poster endpoint writes THROUGH that
    rung, so an image installed from the Collections page outranks a
    ``poster_url`` added to the config later. A URL still beats a generic
    default, cached or hosted.

    Both branches are validated with ``_is_image``: an operator's file can be
    truncated, zero-byte, or an HTML error page saved as ``poster.jpg`` just
    as easily as a response body can. An unusable local file falls through to
    whatever source ``kind`` names rather than failing the collection outright.

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
    # Whether row 105's composite may draw on whatever these rungs produce.
    # Set on each rung rather than inferred afterwards: "an operator's own file
    # is theirs" and "a divider is already captioned" are decisions, and a
    # decision that falls out of the ordering is one a later edit can undo
    # without noticing (adjudications A-2, A-3).
    composable = False
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
                "local poster %s did not decode as an image; using whatever source kind names",
                local,
            )
    if data is None and poster_url:
        # Roadmap row 222. Above every default and below the operator's own
        # file. Any failure here is REPORTED and the collection left alone,
        # like every other poster failure: a missing poster is cosmetic and
        # must never fail the surrounding pass. It deliberately does not fall
        # through to the default below -- the operator named an address, and
        # silently substituting a generic poster for it would read as the URL
        # having worked.
        try:
            data = await download_definition_poster(http, poster_url)
        except FetchRefused as exc:
            # Row 213. The guard's own messages are URL-free by construction
            # (its module docstring, "a refusal never names the URL"), so the
            # reason is safe to hand back verbatim -- which is the point:
            # "that source was refused" with no reason leaves an operator with
            # a working URL and no idea why. ``api/manual.py``'s
            # ``_staged_source`` makes the same call for the same value.
            logger.warning(
                "refused the definition's poster URL for %r: %s", record.title, exc
            )
            return "could not use %s for %r: %s" % (
                DEFINITION_POSTER_SOURCE, record.title, exc,
            )
        except httpx.HTTPError as exc:
            # NOT str(exc), and NOT exc_info: httpx's exception messages -- and
            # the traceback exc_info would attach -- can embed the full request
            # URL, which is the one thing that must not reach even the pod log.
            # The class name only, the same treatment ``api/manual.py`` gives
            # this branch.
            logger.warning(
                "the definition's poster URL for %r failed: %s",
                record.title, type(exc).__name__,
            )
            return "could not fetch %s for %r" % (DEFINITION_POSTER_SOURCE, record.title)
        source = DEFINITION_POSTER_SOURCE
        # Row 105's composite is NOT drawn on it, for adjudication A-2's own
        # reason one rung up: an operator's own choice of image is theirs. A
        # URL typed into the definition is that choice as squarely as a file
        # dropped on the mount, and the art an operator points at is usually
        # already captioned.
        composable = False
    if data is None:
        # A file some source already produced, below the operator's own
        # override and above anything fetched fresh: generated separator art
        # (``separator_art.py``) or a cached Default-Images family poster
        # (``default_images.py``). Both are poster SOURCES, not overrides, so
        # ``prioritize_assets``' guarantee is unchanged -- the local branch
        # above has already had its say.
        #
        # Imported here rather than at module scope: ``default_images`` reads
        # ``DEFAULT_IMAGES_BASE`` and ``fetch_poster`` from THIS module, and a
        # top-level import would be a cycle. The one call is per collection,
        # not per item.
        from autoposter.collections import default_images

        cached = generated
        label = "generated separator art"
        if cached is None and kind in default_images.FAMILIES:
            cached = await default_images.ensure_default_image(
                config, http, kind, key,
            )
            label = "the hosted default image"
        if cached is not None:
            try:
                candidate = cached.read_bytes()
            except OSError:
                candidate = b""
            if _is_image(candidate):
                data = candidate
                source = label
                # ``generated`` is the caller's separator art, which already
                # carries the divider's title (separator_art._render). A cached
                # Default-Images family poster is a plain fetched image and may
                # be drawn on.
                composable = cached is not generated
            else:
                logger.info(
                    "%s %s did not decode as an image; using whatever source "
                    "kind names", label, cached,
                )
    if data is None:
        # Two poster SOURCES now, dispatched on ``kind`` here rather than inside
        # ``hosted_poster_url`` -- see ``tmdb_profile_url`` for why a TMDb file
        # path is not a row of that table. Everything past this line is
        # unchanged: the same fetch, the same ``_is_image`` validation, the same
        # hash-compare and the same locked upload serve both.
        #
        # NOT KOMETA, and the divergence is here rather than in the source
        # function because this is the line that could undo it: upstream hands
        # the profile URL straight to Plex (``modules/plex.py:1216-1217``,
        # ``item.uploadPoster(url=...)``) and never sees the bytes. Reaching for
        # plexapi's ``url=`` form for this kind would destroy the hash-compare
        # below -- we would have nothing to hash -- so every source, this one
        # included, is downloaded first. See the docstring above.
        url = (
            tmdb_profile_url(key) if kind == TMDB_PROFILE_KIND
            else hosted_poster_url(kind, key)
        )
        if url is None:
            return "no poster source for %r" % record.title
        data = await fetch_poster(http, url)
        if data is None:
            return "could not fetch a usable poster for %r from %s" % (record.title, url)
        source = (
            "the TMDb profile photo"
            if kind == TMDB_PROFILE_KIND
            else "the hosted default"
        )
        composable = True

    # Roadmap row 105, and this is the only line it costs: above the digest, so
    # the composited bytes are what ``poster_sha256`` remembers and an
    # unchanged pass still uploads nothing; below every source rung, so one
    # call serves all of them.
    #
    # Two exclusions, both named above rather than incidental: an operator's
    # own file is never restyled (``composable`` is False on the local rung,
    # and api/manual.py's poster endpoint writes THROUGH that rung), and a
    # divider is never captioned twice.
    #
    # A failure here uploads nothing and leaves ``poster_sha256`` where it was,
    # so the next pass retries -- a missing poster is cosmetic and must never
    # fail the surrounding pass. The caller stamps ``record.definition_hash =
    # wanted`` before this function is ever reached, so on its own that would
    # only rescue the retry for a collection whose ``poster_sha256`` is still
    # NULL: a collection that already has a poster keeps
    # ``definition_hash == wanted`` and the caller's short-circuit
    # (``definition_current and not (posters_on and poster_sha256 is None)``)
    # never calls back in, even after the operator fixes the font. Blanking
    # ``definition_hash`` here undoes that stamp so the very next pass sees
    # ``definition_current`` as False regardless of ``poster_sha256`` -- the
    # same sentinel the caller already uses for "settings did not apply".
    # Guarded by ``not dry_run``, matching ``poster_sha256`` above: a dry run
    # composites to check reachability but persists nothing.
    styling = config.collections.poster_title
    if styling.enabled and composable and kind != SEPARATOR_KIND:
        try:
            data = await asyncio.to_thread(
                compose_collection_title,
                styling, Path(config.fonts_root), data, record.title,
            )
        except CollectionTitleRefused as exc:
            logger.warning(
                "did not draw a title onto the poster for %r: %s", record.title, exc
            )
            if not dry_run:
                record.definition_hash = ""
            return "did not draw a title onto the poster for %r: %s" % (
                record.title, exc,
            )
        except Exception:
            # Row 213: an unmarked exception's text never reaches a served
            # action string. The log carries the traceback; the report carries
            # a sentence.
            logger.exception("failed to draw a title onto the poster for %r", record.title)
            if not dry_run:
                record.definition_hash = ""
            return "failed to draw a title onto the poster for %r" % record.title
        source = "%s, with the collection title drawn on" % source

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


LOCAL_ASSET_KIND = "local_asset"


def _write_and_upload_unowned(collection, data: bytes) -> None:
    """Write ``data`` to a temporary file and upload it, without locking.

    Deliberately not ``_write_and_upload``: that helper also calls
    ``collection.lockPoster()``, which is right for a collection this service
    OWNS (see its docstring) but wrong here -- an unmanaged collection's
    poster field is not ours to claim, so it is set once and left for Plex's
    own agent (or the operator) to manage from there.
    """
    handle = tempfile.NamedTemporaryFile(delete=False)
    try:
        with handle:
            handle.write(data)
        collection.uploadPoster(filepath=handle.name)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            logger.warning("could not remove temporary poster file %s", handle.name)


async def apply_local_posters_to_unmanaged(
    session: AsyncSession,
    config: Config,
    http,
    library: str,
    listing: dict,
    dry_run: bool = True,
) -> list[str]:
    """Give collections this service does NOT manage their local poster.

    Roadmap row 37 (Kometa's ``assets_for_all_collections``). ``listing`` is
    the library's collections keyed by title, already filtered to the ones
    this service does not own -- the caller holds that listing and the
    ownership decision, and this function makes neither.

    ``http`` is accepted and unused: this path never downloads anything. A
    local file is the only source, by definition of the row -- there is no
    hosted default for a collection nobody here defines. It is in the
    signature so the call site reads like every other poster call and so a
    later hosted-fallback change needs no signature churn.

    The hash ledger is a ``ManagedCollection`` row with
    ``kind=LOCAL_ASSET_KIND``. That is a LEDGER, not an ownership claim: the
    ownership label is never applied, and ``engine._sweep`` skips this kind
    the same way it already skips ``"operator"``. Reusing the row means no
    migration and no second definition of "what poster did we last set".

    Read with ``getattr(..., False)``, the same defensive read
    ``groups.py``'s ``separators``/``group_order`` use: several existing
    engine tests build ``config.collections`` as a hand-rolled
    ``SimpleNamespace`` that predates this row and carries only the fields
    its own test needs, and this function is reached from ``run_library``'s
    sweep-gated pass on every one of them.
    """
    if not getattr(config.collections, "assets_for_all_collections", False):
        return []

    results: list[str] = []
    for title, collection in sorted(listing.items()):
        try:
            local = local_poster_path(config, library, title)
        except PosterPathRefused as exc:
            results.append(f"refused {title!r}: {exc}")
            continue
        if local is None:
            continue

        data = await asyncio.to_thread(local.read_bytes)
        if not _is_image(data):
            results.append(f"skipped {title!r}: the local file is not a readable image")
            continue
        digest = hashlib.sha256(data).hexdigest()

        row = (
            await session.execute(
                select(ManagedCollection).where(
                    ManagedCollection.library == library,
                    ManagedCollection.title == title,
                )
            )
        ).scalar_one_or_none()
        if row is not None and row.poster_sha256 == digest:
            continue
        if dry_run:
            results.append(f"would apply a local poster to {title!r}")
            continue

        await asyncio.to_thread(_write_and_upload_unowned, collection, data)
        if row is None:
            row = ManagedCollection(
                library=library, title=title, kind=LOCAL_ASSET_KIND,
                definition_hash="",
            )
            session.add(row)
        row.poster_sha256 = digest
        await session.flush()
        results.append(f"poster applied to {title!r} from a local asset")
    return results
