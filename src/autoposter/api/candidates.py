"""Browsing every provider's artwork for one item and art kind.

The render path asks the ladder for *one* image (``providers/ladder.py``) and
throws the rest away. This endpoint asks the same clients the same question and
returns the whole answer, so an operator can see what the automatic pick chose
between -- and, in the next task, choose differently.

Two things make a browse different from a selection, and both are deliberate:

*Nothing is discarded.* ``best_candidate`` drops every candidate whose language
is not in the configured order, which is right for an unattended render and
exactly wrong here: those are the images the ladder refused, and hiding them
would leave the picker unable to offer the one an operator went looking for.
They are sorted last instead, using the ladder's own ``rank_key`` so the top of
the grid is the order the renderer would have used.

*Nothing fails the request.* The providers are fanned out concurrently and one
raising costs that provider's rows and nothing else. A 500 here takes the whole
picker away over a single flaky upstream, and the other two providers' images
were already in hand when it happened.

No provider is asked for anything the item's own columns did not supply: the
``ArtRequest`` is built from the database row exactly as ``render_artifact``
builds its own, so the list shown is the list the ladder chose from.
"""
import asyncio
import inspect
import logging
import os
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy import select

from autoposter.api.auth import require_session
from autoposter.db.models import EventLog, MediaItem, Render
from autoposter.db.models import Session as SessionModel
from autoposter.db.refs import native_ids
from autoposter.net.guard import FetchRefused, store_body
from autoposter.plex.client import ResolvedItem
from autoposter.providers import base as art
from autoposter.providers.ladder import rank_key
from autoposter.render.pipeline import (
    ART_KINDS_FOR, LOGO_OVERRIDE_SUFFIXES, SourceRefused, _validate_image,
    art_config_for, logo_override_path, manual_override_target,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# The kinds whose poster render composites a clearlogo (render/pipeline.py), so
# the only kinds for which a picked logo would be consumed by anything.
LOGO_BROWSABLE_ITEM_KINDS = frozenset({"movie", "show"})

# TMDB serves size variants by URL prefix. Its clients build every URL from
# IMAGE_BASE, so the swap is a prefix replacement -- keyed on the URL and not on
# the candidate's provider label, which is a client's own ``name`` attribute and
# must never be what decides that a URL can be rewritten.
TMDB_ORIGINAL_PREFIX = "https://image.tmdb.org/t/p/original"
TMDB_THUMB_PREFIX = "https://image.tmdb.org/t/p/w342"


def thumb_url(url: str) -> str:
    """A grid-sized variant where one exists, otherwise the image itself.

    TVDB and Fanart serve a single size per artwork, so their candidates are
    their own thumbnails; a grid of TMDB originals would be tens of megabytes.
    """
    if url.startswith(TMDB_ORIGINAL_PREFIX):
        return TMDB_THUMB_PREFIX + url[len(TMDB_ORIGINAL_PREFIX):]
    return url


def language_order_for(config, art_kind: str) -> list[str]:
    """The preference list the renderer would rank this art kind with.

    Logos are ranked by ``artwork.logo_language_order``, which is a different
    list with different contents -- no ``xx`` in the shipped config -- so
    reusing the poster's would put a textless logo first in a grid where the
    render would have put it last.
    """
    if art_kind == art.LOGO:
        return config.artwork.logo_language_order
    return art_config_for(config, art_kind).language_order


def _art_request(item: MediaItem, art_kind: str) -> art.ArtRequest:
    """The request ``render_artifact`` builds for this item, field for field.

    ``season_id`` is left unset here as it is there: it is a TVDB-internal id
    that TVDBClient resolves for itself from ``tvdb_id`` and the season number
    (providers/tvdb.py), and filling it from anywhere else would be inventing a
    value the render path never had.
    """
    return art.ArtRequest(
        art_kind=art_kind,
        is_movie=item.kind == "movie",
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        imdb_id=item.imdb_id,
        season_number=item.season_number,
        episode_number=item.episode_number,
    )


async def _fetch(provider, request: art.ArtRequest) -> list[art.ArtCandidate]:
    """One provider's whole list, asking for the widest one it can give.

    TMDB narrows its own response to the configured languages and is the only
    client that does; it takes ``all_languages`` to stop. Detected from the
    signature rather than from the provider's name so a client that grows the
    same keyword gets the same treatment, and one that never will is called the
    way it always was.
    """
    if "all_languages" in inspect.signature(provider.fetch).parameters:
        return await provider.fetch(request, all_languages=True)
    return await provider.fetch(request)


async def _item_for_kind(session, item_id: int, art_kind: str) -> MediaItem:
    """The item, once ``art_kind`` is known to be one it can actually have.

    ``art_kind`` is the only caller-supplied value on these routes that reaches
    a config lookup and a path builder, so it is checked against the item's own
    kind first -- the clear-override idiom. ``"logo"`` is allowed on top of
    ``ART_KINDS_FOR`` for movies and shows, because a logo is first-class at
    the provider layer and absent downstream: only the poster render
    composites one, so only the kinds that have a poster can use one.
    """
    item = (
        await session.execute(select(MediaItem).where(MediaItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")

    allowed = set(ART_KINDS_FOR.get(item.kind, ()))
    if item.kind in LOGO_BROWSABLE_ITEM_KINDS:
        allowed.add(art.LOGO)
    if art_kind not in allowed:
        raise HTTPException(status_code=404, detail="unknown art kind for this item")
    return item


@router.get("/items/{item_id}/candidates/{art_kind}")
async def browse_candidates(
    item_id: int,
    art_kind: str,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Every provider's artwork for one item and art kind, in ladder order.

    ``art_kind`` is checked against the item's own kind before any provider is
    asked -- the same idiom as clear-override, and for the same reason: it is
    caller-supplied, and an unchecked value reaches config lookups and provider
    clients that have no answer for it. ``"logo"`` is allowed on top of
    ``ART_KINDS_FOR`` for movies and shows, because a logo is first-class at the
    provider layer and absent downstream: there is no render row for one, and
    ``current`` is therefore always null for it.
    """
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = await _item_for_kind(session, item_id, art_kind)

        current = None
        if art_kind != art.LOGO:
            render = (
                await session.execute(
                    select(Render).where(Render.item_id == item_id, Render.art_kind == art_kind)
                )
            ).scalar_one_or_none()
            if render is not None:
                current = {"source_url": render.source_url, "provider": render.provider}

        art_request = _art_request(item, art_kind)

    providers = request.app.state.providers
    # return_exceptions, so one client raising does not cancel the siblings that
    # had already answered -- a bare gather propagates the first exception and
    # this endpoint would 500 holding two thirds of a usable list.
    results = await asyncio.gather(
        *(_fetch(provider, art_request) for provider in providers), return_exceptions=True
    )

    candidates: list[art.ArtCandidate] = []
    errors: dict[str, str] = {}
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            # The type name, never str(exc): httpx puts the full request URL in
            # an HTTPStatusError's message and Fanart passes its API key as a
            # query parameter, so the message is a credential leak into both the
            # response body and this log line. The traceback stays in the log,
            # where the ladder already puts its own provider failures.
            errors[provider.name] = type(result).__name__
            logger.warning(
                "provider %s failed browsing %s for item %d",
                provider.name, art_kind, item_id, exc_info=result,
            )
            continue
        candidates.extend(result)

    order = language_order_for(config, art_kind)
    # rank_key sorts UNRANKED (a language the config never asked for) last of
    # its own accord, which is why the ladder's filter is not reused here.
    candidates.sort(key=lambda candidate: rank_key(candidate, order))

    return {
        "candidates": [
            {
                "provider": candidate.provider,
                "url": candidate.url,
                "thumb_url": thumb_url(candidate.url),
                "language": candidate.language,
                "width": candidate.width,
                "height": candidate.height,
                "score": candidate.score,
                "includes_text": candidate.includes_text,
            }
            for candidate in candidates
        ],
        "errors": errors,
        "current": current,
    }


# --- picking ----------------------------------------------------------------

# What a picked image is allowed to arrive as. Checked against the *response's*
# Content-Type before a byte is kept, which is one more check than the render
# path's own ``_download`` makes -- there the URL came from a provider client
# that had just parsed it out of that provider's JSON, whereas here a redirect
# chain ending somewhere else would otherwise write an HTML error page into the
# asset tree under a .jpg name. Same three types api/artwork.py serves.
PICK_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

# The mirror is fixed to .jpg (render/naming.py), so anything else has to be
# re-encoded. 95 rather than the config's output_quality: this is the *base*
# the compositor then reads, overlays and re-encodes at output_quality, and
# quantising twice at the same setting throws away detail the operator picked
# this image for.
PICK_JPEG_QUALITY = 95


class PickBody(BaseModel):
    """The claim. Neither field is trusted until the fan-out has confirmed it."""

    provider: str
    url: str


# Larger than any real poster: the biggest legitimate art this endpoint will
# ever be asked to fetch is a few MiB. This request is now reachable from an
# authenticated operator rather than only from the ladder's own trusted
# providers, so the stream needs its own ceiling rather than trusting the
# other end to behave.
PICK_MAX_BYTES = 50 * 1024 * 1024


# The name this module has raised since 6d. The class itself moved to
# net/guard.py in 6e along with the body checks below, so the manual endpoints
# get one implementation of them rather than a second copy that drifts;
# ``BodyRefused`` (what ``store_body`` raises) is a subclass, so every existing
# ``except DownloadRefused`` and ``raises(DownloadRefused)`` still means what
# it did. The transcode helpers below raise it directly.
DownloadRefused = FetchRefused


async def _download_artwork(http: httpx.AsyncClient, url: str, destination: Path) -> None:
    """``pipeline._download``'s streaming shape, plus the checks it lacks.

    ``follow_redirects=True`` and no address check, deliberately: the URL
    reaching here has already been proven to be one a provider client just
    offered (see ``pick_candidate``), which is this endpoint's whole defence.
    An operator-supplied URL goes through ``net/guard.guarded_download``
    instead, which turns the redirects off and validates every hop.
    """
    async with http.stream("GET", url, follow_redirects=True) as response:
        await store_body(
            response, destination,
            max_bytes=PICK_MAX_BYTES, content_types=PICK_CONTENT_TYPES,
        )


def _prepare_jpeg(source: Path, destination: Path) -> None:
    """Re-encode a picked image as the JPEG the mirror path names.

    A decode is also the only real check that these bytes are the image the
    Content-Type claimed, so a source no decoder accepts is a refusal rather
    than a file written for the compositor to choke on later.

    A source that is already a JPEG is copied through: re-encoding it would
    cost a generation of quality to produce the same container it is in.
    """
    try:
        with Image.open(source) as image:
            if image.format == "JPEG":
                # load(), not verify(): Pillow's JPEG plugin has no verify()
                # of its own, so it falls back to the base class's near-no-op
                # and a truncated entropy-coded scan sails through. Every
                # other branch below decodes the whole image via load() or
                # convert(), so a JPEG that passed only a header parse and
                # copied through untouched would reach the mirror with a
                # truncated body the compositor chokes on later.
                image.load()
                destination.write_bytes(source.read_bytes())
                return
            image.load()
            # RGB explicitly: JPEG has no alpha, and Pillow refuses to save an
            # RGBA image as one rather than flattening it for us.
            image.convert("RGB").save(
                destination, format="JPEG", quality=PICK_JPEG_QUALITY
            )
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DownloadRefused(f"undecodable image ({type(exc).__name__})") from exc


def _verify_image(source: Path) -> None:
    """Confirm the bytes really are an image, without re-encoding them.

    The transcode below doubles as this check for every other kind, but a
    picked logo is written through untouched -- it needs the alpha channel a
    JPEG cannot carry -- so a Content-Type of ``image/png`` over a body that is
    nothing of the sort would otherwise land on the mount as ``logo.png`` and
    be handed to the compositor on the next poster render.

    Delegates to ``render/pipeline._validate_image`` -- the same full pixel
    decode the render path uses -- rather than ``Image.verify()``. This
    branch's own pinned measurement (``tests/test_render_input_guard.py``)
    proves ``verify()`` is a header parse that the 'Inside Out 2' poison shape
    sails through; a picked logo lands on the manual mount and is staged
    straight into ``build_logo_argv`` on the next render (``_stage_override``,
    unvalidated), so ``verify()`` here was a second, live entrance for the
    exact defect the provider lane closes.

    The refusal is re-worded rather than passed through verbatim: this
    endpoint's own callers (``api/manual.py``) already promise an
    ``"undecodable image"`` detail for exactly this failure, and that promise
    predates this change.
    """
    try:
        _validate_image(source, "the picked logo")
    except SourceRefused as exc:
        raise DownloadRefused(f"undecodable image ({exc})") from None


def _clear_stale_logo_overrides(config, item: ResolvedItem, kept_suffix: str) -> None:
    """Remove every other logo suffix so the new pick cannot be shadowed.

    ``find_logo_override`` returns the first existing suffix in
    ``LOGO_OVERRIDE_SUFFIXES`` order, so a ``logo.png`` left over from an
    earlier pick would keep winning over a newer ``logo.webp`` forever.
    Missing files are fine -- there is usually nothing to remove.
    """
    for suffix in LOGO_OVERRIDE_SUFFIXES:
        if suffix == kept_suffix:
            continue
        logo_override_path(config, item, suffix).unlink(missing_ok=True)


def _install(source: Path, target: Path) -> None:
    """``pipeline._publish``'s atomic write, for the manual mount.

    No backup generation: ``_publish`` keeps one because it is overwriting a
    rendered asset this service produced, whereas everything under
    manual_assets_root is the operator's own and clear-override's ``.disabled``
    rename is how a file there is taken out of play.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.tmp")
    try:
        staging.write_bytes(source.read_bytes())
        os.replace(staging, target)
    except Exception:
        staging.unlink(missing_ok=True)
        raise


@router.post("/items/{item_id}/candidates/{art_kind}/pick")
async def pick_candidate(
    item_id: int,
    art_kind: str,
    body: PickBody,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Make one provider's candidate this item's base image.

    **The URL in the body is a claim, not an instruction.** The browse fan-out
    is re-run server-side (a cache hit, in the normal case) and the request is
    refused unless ``(provider, url)`` is one of the pairs that fan-out just
    returned. So the only URLs this service ever fetches are ones a provider
    client put in front of it, and a caller cannot use this endpoint to make
    the container issue a request of their choosing -- to the link-local
    metadata service, to an internal admin port, or to anywhere else it can
    reach and they cannot. Nothing else here defends that boundary: there is no
    allowlist, no scheme check and no egress policy behind it.

    The rest is clear-override run backwards, in its order and for its reasons.
    The file lands first, atomically; only then are the fingerprints nulled and
    a reprocess queued. Reversing that would leave a row inviting a re-render
    with nothing on the mount to render from, and the automatic pick would
    quietly come back while the UI reported the operator's choice as taken.
    """
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = await _item_for_kind(session, item_id, art_kind)
        if item.root_folder is None:
            # Nullable, and the whole mirror layout is rooted at it.
            raise HTTPException(status_code=409, detail="this item has no asset folder")
        native_id = (await native_ids(session, [item.id], "plex")).get(item.id)
        if native_id is None:
            raise HTTPException(status_code=409, detail="this item has no Plex id")
        resolved = ResolvedItem(
            server="plex", native_id=native_id, library=item.library, kind=item.kind,
            title=item.title, year=item.year,
            season_number=item.season_number, episode_number=item.episode_number,
            root_folder=item.root_folder, file_path=item.file_path, art_url=None,
            tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id, imdb_id=item.imdb_id,
        )
        art_request = _art_request(item, art_kind)

    providers = request.app.state.providers
    results = await asyncio.gather(
        *(_fetch(provider, art_request) for provider in providers), return_exceptions=True
    )
    offered: set[tuple[str, str]] = set()
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            # Type name only, for the reason browse_candidates gives above.
            logger.warning(
                "provider %s failed validating a pick of %s for item %d: %s",
                provider.name, art_kind, item_id, type(result).__name__, exc_info=result,
            )
            continue
        offered.update((candidate.provider, candidate.url) for candidate in result)

    if (body.provider, body.url) not in offered:
        # Host at most, never the URL: this line is the record of a rejected
        # claim, and writing the claim itself into the log is how a log becomes
        # an injection surface. The response says nothing back about what was
        # asked for either -- an endpoint that echoes its input is a reflector.
        try:
            host = httpx.URL(body.url).host
        except httpx.InvalidURL:
            # A malformed claim (e.g. an unparsable IPv6 host) is still a
            # refusal, not a 500 -- and the exception's own message embeds
            # the input.
            host = "invalid-url"
        logger.warning(
            "refused a pick of %s for item %d: %s offered no such image (host %s)",
            art_kind, item_id, body.provider, host,
        )
        raise HTTPException(
            status_code=422, detail="that image is not one of this item's candidates"
        )

    http = request.app.state.http
    if http is None:
        # None on an app without the background lifespan (app.py).
        raise HTTPException(status_code=503, detail="no HTTP client on this instance")

    with tempfile.TemporaryDirectory() as tmpdir:
        downloaded = Path(tmpdir) / "picked"
        try:
            await _download_artwork(http, body.url, downloaded)
            if art_kind == art.LOGO:
                # The original bytes, untranscoded: a logo is composited over
                # the poster and needs the alpha channel a JPEG cannot carry.
                # The suffix is constrained to the ones find_logo_override
                # looks for, so a picked file is a findable one.
                suffix = Path(httpx.URL(body.url).path).suffix.lower()
                if suffix not in LOGO_OVERRIDE_SUFFIXES:
                    suffix = ".png"
                await asyncio.to_thread(_verify_image, downloaded)
                target, staged = logo_override_path(config, resolved, suffix), downloaded
            else:
                target = manual_override_target(config, resolved, art_kind)
                staged = Path(tmpdir) / "picked.jpg"
                await asyncio.to_thread(_prepare_jpeg, downloaded, staged)
        except (DownloadRefused, httpx.HTTPError) as exc:
            logger.warning(
                "could not fetch the %s picked for item %d from %s: %s",
                art_kind, item_id, body.provider, type(exc).__name__, exc_info=exc,
            )
            # The provider's name and nothing else. httpx puts the full request
            # URL in its exception messages and Fanart passes its API key as a
            # query parameter, so str(exc) in a detail is a credential leak.
            raise HTTPException(
                status_code=502,
                detail=f"could not fetch the picked image from {body.provider}",
            ) from None

        try:
            # Offloaded like every other touch of this mount: manual_assets_root
            # is typically NFS and a hung mount must not stall the event loop.
            await asyncio.to_thread(_install, staged, target)
            if art_kind == art.LOGO:
                await asyncio.to_thread(_clear_stale_logo_overrides, config, resolved, suffix)
        except OSError as exc:
            logger.warning("could not write the picked image to %s: %s", target, exc)
            raise HTTPException(
                status_code=503, detail="could not write to the override mount"
            ) from None

    # Deferred to here rather than imported at module scope: api/routes.py
    # imports this module's router, so the other direction is a cycle.
    from autoposter.api.routes import _enqueue_reprocess

    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        # A logo has no render row of its own and never will: it rides into the
        # POSTER's fingerprint as ``logo_sha`` (render/pipeline.py), so the
        # poster is the row a picked logo invalidates.
        row_kind = "poster" if art_kind == art.LOGO else art_kind
        render = (
            await session.execute(
                select(Render).where(Render.item_id == item_id, Render.art_kind == row_kind)
            )
        ).scalar_one_or_none()
        if render is not None:
            # An ADOPTED row's short-circuit sits above the override lookup
            # (render_artifact), so without this its next pass answers
            # "adopted" and the picked image never reaches a pixel. Any other
            # row would move on its own -- the override's path and digest are
            # fingerprint inputs -- but one rule for both is cheaper than
            # knowing which a row is.
            render.fingerprint = None
            render.badge_fingerprint = None

        session.add(
            EventLog(
                source="picker",
                event_type="candidate_picked",
                # Under an override the render row stamps provider="manual" and
                # a filesystem path, so this is the only surviving record of
                # whose image it actually is. Host at most, never the URL: an
                # events row is read casually and pasted into tickets.
                payload={
                    "item_id": item_id,
                    "art_kind": art_kind,
                    "provider": body.provider,
                    "host": httpx.URL(body.url).host,
                },
                outcome=f"{item.library}/{item.title} {art_kind} from {body.provider}",
            )
        )
        await session.commit()

        job_id = await _enqueue_reprocess(session, item)
    return {"status": "picked", "queued": job_id is not None}
