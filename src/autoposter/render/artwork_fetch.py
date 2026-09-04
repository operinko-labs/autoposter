"""Fetch one artwork body safely, and pick a clearlogo that survives that fetch.

Lifted verbatim out of ``render/pipeline.py`` -- #131's byte cap and
full-decode validation, and #153's guarded clearlogo ladder walk -- for one
reason: the mass-ops logo updater (``artwork_modes/logo.py``) needs the SAME
guard, and could not have it. It cannot import ``render/pipeline``, which pulls
in the badges, overlays, facts and compositor machinery a mass op has no
business loading; and ``render/pipeline`` cannot import a helper that lives in
``artwork_modes`` either. Until this module existed the only ways to close the
updater's door were a second copy of the walk or an import cycle.

This module is a LEAF: it imports the provider ladder and nothing else of ours.
``render/pipeline`` re-exports every name it used to own, so ``api/candidates``,
``app.py`` and the two guard suites keep importing them from there unchanged.

The guard exists for job 40478 -- 'Inside Out 2', a 32000x18839 clearlogo
(602,848,000px) that ``providers/ladder.rank_key`` ranks FIRST because it sorts
on ``-pixels``. It reached the render path and the mass-ops updater by two
separate doors; this module is the one door both now go through.
"""

import asyncio
import hashlib
import logging
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from autoposter.providers.base import ArtRequest
from autoposter.providers.ladder import select_artwork

logger = logging.getLogger(__name__)


class SourceRefused(Exception):
    """Downloaded artwork this service will not hand to ImageMagick.

    A render job that raises this parks like any other failure. It carries
    ``served_detail = True`` (the row-213 marker, ``plex/client.ItemNotFound``'s
    mechanism), reviewed safe for a served surface: every raise site below
    interpolates only the stage label (``"the poster source"``, ``"the
    clearlogo"``) and facts about the downloaded bytes themselves (dimensions,
    pixel count, byte count, the decode exception's class name) or, at the
    aggregate site, the item's own Plex rating key -- no URL, no filesystem
    path, no token. So ``queue/worker._served_reason`` serves the
    class-prefixed message (``"SourceRefused: <stage> did not decode after
    download (DecompressionBombError)"``, e.g.) for ``job.last_error`` instead
    of the bare class name -- the operator's only way to tell a corrupt-source
    park from every other kind without opening the pod log. The full message
    and traceback still reach the pod log through ``exc_info=True`` on the
    handler's own logging line.
    """

    served_detail = True


# The render path's ceiling on one downloaded artwork body. Same value, and the
# same reasoning, as ``api/candidates.PICK_MAX_BYTES``: far above any real
# ``/original/`` poster or backdrop, low enough that a body which simply never
# stops arriving is refused rather than written to the working directory. It is
# a separate constant rather than an import because ``api/candidates`` imports
# from this module -- the arrow cannot be drawn the other way.
RENDER_MAX_BYTES = 50 * 1024 * 1024

# How many leading bytes ``_looks_like_svg`` reads before giving up. Generous
# enough for a UTF-8 BOM, leading whitespace, and an XML prolog ahead of the
# root element, and tiny next to ``RENDER_MAX_BYTES``.
_SVG_SNIFF_BYTES = 256

# Pillow has no SVG decoder, and fanart.tv genuinely serves SVG clearlogos --
# ``compositor.build_logo_argv`` has a ``-density 300`` branch for exactly
# those, so refusing every SVG here would refuse artwork that renders
# correctly today. What decides SVG-ness must be the bytes themselves, not a
# suffix pulled from the provider's URL: a suffix is the provider's own claim,
# forgeable by whatever answers that URL, and job 40478 -- the incident this
# guard exists for -- was itself a clearlogo. So this is a content sniff, not
# a filename check: whitespace/BOM-tolerant, and true only for a document that
# actually opens with ``<?xml`` or ``<svg``.
def _looks_like_svg(path: Path) -> bool:
    with path.open("rb") as handle:
        prefix = handle.read(_SVG_SNIFF_BYTES)
    prefix = prefix.lstrip(b"\xef\xbb\xbf").lstrip(b" \t\r\n")
    return prefix.startswith(b"<?xml") or prefix.startswith(b"<svg")


# The pixel ceiling ``_validate_image`` refuses artwork above, checked from
# the header *before* ``load()`` allocates a decode buffer. Comfortably covers
# real artwork -- an 8K poster is 7680x4320 = 33,177,600px -- and sits well
# under Pillow's own decompression-bomb thresholds at the installed default
# (``Image.MAX_IMAGE_PIXELS`` = 89,478,485; the error only fires above double
# that, 178,956,970px, and the band in between just warns and decodes in
# full). Without this, that warn band is a ~537-716MB decode with no cap of
# its own, and five workers (``queue/worker.run_workers``) can each be doing
# one in the same process.
_ARTWORK_MAX_PIXELS = 64_000_000


def _validate_image(path: Path, stage: str) -> None:
    """Decode ``path`` in full, or refuse it.

    A full pixel decode (``load()``), not a header parse. The production
    failure this exists for -- job 40478, the 'Inside Out 2' clearlogo -- is a
    PNG whose header is valid and whose IDAT stream contradicts it;
    ImageMagick's own report is ``IDAT: Too much image data``. ``magick
    identify`` and Pillow's ``verify()`` read the header and pass such a file;
    only a decode that walks the compressed stream fails it. Before the infra
    layer's ``MAGICK_*`` caps that image cost ~6 GiB inside the compositor and
    OOMKilled the pod; after them it surfaced as an opaque ``magick failed
    (1)`` from ``compositor.run``. Either way the honest place to refuse it is
    here, at the download, with a message that names what was wrong.

    ``Image.MAX_IMAGE_PIXELS`` is left at Pillow's default and active -- it is
    a module global and mutating it would silently retune ``api/candidates``
    too -- but its default only raises above 178,956,970px; the
    89,478,485-178,956,970px band just warns and decodes. ``_ARTWORK_MAX_PIXELS``
    is this function's own, tighter ceiling for exactly that band, checked
    from ``image.size`` before ``load()`` ever allocates a decode buffer.

    **Nothing is evicted on a refusal, because nothing cached these bytes.**
    ``providers/cache.py`` is a TTL cache of decoded *JSON* payloads keyed by
    the metadata request (``providers/fetch.py``); image bodies never pass
    through it, and ``_download`` streams from the provider's CDN on every
    attempt. So a retry of a refused job re-fetches, and a source repaired
    upstream renders on the next attempt with no cache to invalidate.
    """
    if _looks_like_svg(path):
        return
    try:
        with Image.open(path) as image:
            width, height = image.size
            pixels = width * height
            if pixels > _ARTWORK_MAX_PIXELS:
                raise SourceRefused(
                    f"{stage} is {width}x{height} ({pixels}px), over the "
                    f"{_ARTWORK_MAX_PIXELS}px artwork ceiling"
                )
            image.load()
    except (
        UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError
    ) as exc:
        # `from None`, not `from exc`: the message already names the cause
        # (`type(exc).__name__`), and chaining would hold the original
        # exception -- and, through its traceback, this frame's partially
        # decoded ``image`` -- reachable for as long as something logs or
        # handles the ``SourceRefused``, which spans ``worker.run_once``'s
        # ``session.rollback()`` and ``fail()`` (L3).
        raise SourceRefused(
            f"{stage} did not decode after download ({type(exc).__name__})"
        ) from None


async def _download(
    http: httpx.AsyncClient, url: str, destination: Path, *, stage: str,
    headers: dict[str, str] | None = None, follow_redirects: bool = True,
) -> str:
    """Fetch artwork to ``destination`` and return its SHA-256.

    Two guards stand between a provider's CDN and the first ``magick`` process,
    because until they existed there were none: ``net/guard.store_body`` has
    had a byte cap since 6d and this download -- the one the whole render path
    uses -- had never grown one (``api/candidates._download_artwork``'s
    docstring says so in as many words).

    The cap is counted chunk by chunk rather than read off ``Content-Length``:
    the header is the other end's claim, and a body that never stops arriving
    sends none. A refusal of either kind removes the partial file before it
    propagates, so no half-written source is left for a later step to find.
    The check runs before each chunk is written or hashed, so nothing over the
    cap ever reaches disk -- but ``aiter_bytes`` decodes compression with no
    ``max_length`` of its own, so one already-decoded chunk can itself be
    larger than a single read would suggest. The transient peak in memory is
    therefore ``RENDER_MAX_BYTES`` plus that one chunk, not a hard
    ``RENDER_MAX_BYTES`` ceiling.

    ``store_body``'s third check, the Content-Type allowlist, is deliberately
    NOT copied. A Content-Type is a claim and only a decoder settles it (that
    module's own words); the decode below settles it strictly, so an allowlist
    would add no safety and would newly refuse a CDN that answers
    ``application/octet-stream`` over bytes that decode perfectly.

    ``stage`` names what is being fetched, for the refusal message.

    ``headers`` and ``follow_redirects`` are both keyword-only and default to
    the values every existing call site already gets (no headers, redirects
    followed), so nothing already calling this changes. The plex-preview
    fallback (roadmap row 241) is the first caller to pass either: an
    ``X-Plex-Token`` header, and ``follow_redirects=False`` (adjudication A5)
    because a custom auth header is not one httpx strips on a cross-origin
    redirect, and PMS never needs to redirect an image blob anyway.
    """
    digest = hashlib.sha256()
    size = 0
    try:
        async with http.stream(
            "GET", url, follow_redirects=follow_redirects, headers=headers
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > RENDER_MAX_BYTES:
                        raise SourceRefused(
                            f"{stage} exceeded the {RENDER_MAX_BYTES}-byte "
                            "download cap"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
        await asyncio.to_thread(_validate_image, destination, stage)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


# How many clearlogo candidates one poster may try before it renders without
# one. Every attempt past the first is a download this pass did not previously
# make, and a title whose whole logo set is corrupt would otherwise walk a
# provider's entire catalogue on every visit. Three is two more chances than
# the pipeline had before the 'Inside Out 2' bomb and still a bounded cost per
# item. Counted per ATTEMPT, not per download: a candidate dropped on its
# reported size spends one too, which is what keeps a run of oversized
# candidates bounded as well.
_MAX_LOGO_ATTEMPTS = 3


async def pick_guarded_logo(
    http: httpx.AsyncClient,
    providers: list,
    language_order: list[str],
    request: ArtRequest,
    tmpdir: Path,
    *,
    rating_key: str,
    raster_only: bool = False,
) -> tuple[Path | None, str, int]:
    """The clearlogo for this item: ``(path, sha256, candidates skipped)``.

    Was ``render/pipeline._pick_logo``, generalised in exactly two ways so the
    mass-ops logo updater can share it rather than grow a second copy: the
    caller hands in a built ``ArtRequest`` and its own ``language_order``
    (rather than a ``Config`` and a ``ResolvedItem``, which a ``MediaItem`` row
    is not), and ``raster_only`` says whether an SVG is acceptable at the far
    end. Nothing else about the walk changed.

    Three guards, all from the 'Inside Out 2' clearlogo:

    * A candidate whose provider-REPORTED ``width * height`` is over
      ``_ARTWORK_MAX_PIXELS`` is skipped without being downloaded -- the same
      ceiling ``_validate_image`` would refuse it at, read from the same
      metadata the ladder ranked it by, so the check costs nothing and needs no
      new provider field. A candidate reporting no dimensions at all (TMDB
      omits them on some entries; Fanart's ``_int_or_none`` answers None for a
      non-numeric) is downloaded exactly as before: unknown is not "too big",
      and the full decode still settles it.
    * A candidate that IS downloaded and then refused is skipped and the ladder
      is asked again with that URL excluded (``select_artwork``'s
      ``exclude_urls``).
    * Under ``raster_only``, a candidate whose downloaded BYTES are an SVG is
      skipped the same way. The render path composites SVG happily
      (``compositor.build_logo_argv`` has a ``-density 300`` branch for exactly
      those), but Plex's ``clearLogo`` field takes a raster image. This is a
      content sniff on what arrived, not a check of the suffix in the
      provider's URL -- a suffix is the provider's own claim, forgeable by
      whatever answers that URL, and it is the same reason ``_looks_like_svg``
      exists at all. It costs one download of a file that turns out to be
      unusable; ``_validate_image`` returns early for an SVG, so that download
      is the whole cost.

    ``(None, "", n)`` when nothing usable was found. Every caller treats that
    as a FALL-THROUGH, never a failure of the wider unit of work: the render
    path renders the poster without a logo, and the updater skips the item and
    moves to the next one. The refusal outcome stays for the BASE image alone.

    Non-``SourceRefused`` exceptions -- a 404, a connect error -- are NOT
    caught here and never trigger a re-ask. They propagate to the caller's own
    containment, which is exactly where they went before this walk existed.

    The re-ask is usually free: ``providers/cache.py`` is a TTL cache of the
    decoded JSON payload keyed by the metadata request (``providers/fetch.py``
    consults it before issuing anything), so a second walk within the TTL is a
    database read rather than a network hit -- but ONLY when
    ``providers.cache_ttl_seconds > 0``, because ``app.py:173`` builds no cache
    at all when it is zero. With caching off, each attempt past the first is a
    real outbound listing request per provider, which is the other half of why
    ``_MAX_LOGO_ATTEMPTS`` is small. Image bodies are never cached either way,
    which is also why a refused candidate is re-fetched rather than remembered
    across passes.
    """
    tried: set[str] = set()
    skipped = 0
    for _ in range(_MAX_LOGO_ATTEMPTS):
        selection = await select_artwork(
            providers, language_order, request, exclude_urls=tried
        )
        candidate = selection.candidate
        if candidate is None:
            break
        tried.add(candidate.url)
        # `or 0` on both halves: an unreported dimension must read as "not over
        # the ceiling", never as a zero-sized image to reject.
        if (candidate.width or 0) * (candidate.height or 0) > _ARTWORK_MAX_PIXELS:
            skipped += 1
            continue
        suffix = Path(httpx.URL(candidate.url).path).suffix or ".png"
        logo_path = tmpdir / f"logo{suffix}"
        try:
            logo_sha = await _download(
                http, candidate.url, logo_path, stage="the clearlogo"
            )
        except SourceRefused as exc:
            skipped += 1
            # No URL: `exc` already names the stage and what was wrong with the
            # bytes (dimensions, or the decode exception's class name), and a
            # provider URL in a log line is the one thing row 209 keeps out of
            # them. `_download` has already removed the partial file.
            logger.warning("clearlogo candidate refused for %s: %s", rating_key, exc)
            continue
        if raster_only and _looks_like_svg(logo_path):
            skipped += 1
            logo_path.unlink(missing_ok=True)
            logger.warning(
                "clearlogo candidate for %s is an SVG, which Plex's clearLogo "
                "field cannot take", rating_key,
            )
            continue
        return logo_path, logo_sha, skipped
    return None, "", skipped
