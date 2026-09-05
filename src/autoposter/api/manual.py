"""Installing an artifact the operator chose themselves.

Manual mode is the 6d pick flow with the *source* of the image changed and
nothing else. A pick took a ``(provider, url)`` pair and proved the URL was
one a provider client had just offered; this takes a bare string, which is
either a URL or a path on the manual-assets mount, and proves something about
it instead:

* a URL goes through ``net/guard.guarded_download`` -- scheme allowlist,
  address checks after resolution, redirects followed by hand -- because
  without it an authenticated caller can point this process at anything the
  container can route to and the caller cannot. That guard is the security
  invariant of this phase; see its module docstring;
* a path is resolved against ``manual_assets_root`` and put through the
  double-``realpath`` containment ``api/artwork.py`` already uses, so ``..``,
  an absolute path, and a symlink planted on the mount all land outside the
  root and are refused on their *target* rather than accepted on their name.

Everything after that is 6d's tail, reused rather than restated: verify or
transcode, install atomically, null the fingerprints, enqueue. It is in that
order for the reason ``clear_manual_override`` gives -- nulled fingerprints
with no file behind them re-render straight back to the provider's automatic
pick while the UI reports the operator's choice as taken.

The collection endpoint writes and stops. There is no upload here: a
collection poster is applied by the reconciler (``collections/posters.py``),
and "Diff now" is how an operator makes that happen immediately. Uploading
from here would be a second, divergent path to Plex that neither hashes into
``poster_sha256`` nor locks the field.
"""
import asyncio
import logging
import os
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from autoposter.api.auth import require_session
from autoposter.api.candidates import (
    PICK_CONTENT_TYPES, PICK_MAX_BYTES, DownloadRefused, _clear_stale_logo_overrides,
    _install, _item_for_kind, _prepare_jpeg, _verify_image,
)
from autoposter.collections.posters import PosterPathRefused, poster_override_target
from autoposter.db.models import EventLog, ManagedCollection, MediaItem, Render
from autoposter.db.models import Session as SessionModel
from autoposter.net.guard import BodyRefused, FetchRefused, TargetRefused, guarded_download
from autoposter.plex.client import ResolvedItem
from autoposter.providers import base as art
from autoposter.render.pipeline import (
    LOGO_OVERRIDE_SUFFIXES, logo_override_path, manual_override_target,
)

logger = logging.getLogger(__name__)

router = APIRouter()

URL_SCHEMES = ("http://", "https://")

# The served refusals for the upload route. Fixed sentences, every one of them:
# a size, a part count, a content type or a file name in a response body is a
# reflector, and these bodies are read out of a browser console and pasted into
# tickets (row 213).
UPLOAD_TOO_LARGE = "the upload exceeds the size cap"
UPLOAD_MALFORMED = "the upload is not a usable multipart form"
UPLOAD_NO_FILE = "the upload has no file part"
UPLOAD_NO_LOGO = "a logo cannot be uploaded; give a URL or a mount path instead"

# Room for the boundary lines, the part's own headers and the closing
# delimiter, on top of the image itself. It bounds the READ; the exact cap is
# still applied to the part's own bytes below, so this number never decides
# whether an image is too large.
UPLOAD_ENVELOPE_BYTES = 16 * 1024

# Read size for copying the part out of Starlette's spool. The same order of
# magnitude httpx streams a download in; nothing depends on the value.
UPLOAD_CHUNK_BYTES = 64 * 1024


class _UploadTooLarge(Exception):
    """The request body passed its budget while it was still arriving."""


class ManualSource(BaseModel):
    """Where the operator wants the image taken from.

    One field, because the two forms are told apart by their own shape rather
    than by a second field the caller could set inconsistently with the first.
    """

    source: str


class OutsideMount(Exception):
    """A source path that does not resolve to a file inside the mount."""


def _is_url(source: str) -> bool:
    return source.lower().startswith(URL_SCHEMES)


def _unusable_status(source: str) -> int:
    """Whose fault it is that these bytes are not an image.

    A URL that answered with something no decoder accepts is the far end
    failing, which is the 502 the pick endpoint already returns for it -- and
    it keeps one rule for every URL failure, since the status, Content-Type
    and size refusals above are 502 too. A file on the operator's own mount is
    the request being wrong, which is a 422.
    """
    return 502 if _is_url(source) else 422


def _mount_source(manual_assets_root: Path, source: str) -> Path:
    """Resolve ``source`` inside the mount, or refuse it.

    ``api/artwork.py``'s idiom, for its reasons: both sides go through
    ``realpath`` before being compared, so a symlink on the mount pointing at
    ``/etc/shadow`` is rejected on its target. A lexical check -- ``normpath``,
    or a ``startswith`` on the strings -- would let that through, and so would
    rejecting only literal ``..`` segments.

    Joining an absolute path onto the root yields the absolute path (pathlib's
    rule), so ``/etc/passwd`` needs no special case: it simply resolves
    outside the root. The empty string resolves *to* the root, which is a
    directory and not a file, and is refused for both reasons.

    Synchronous and called from a thread: ``manual_assets_root`` is typically
    NFS, so the realpath walk and the stat stay off the event loop.
    """
    root = Path(os.path.realpath(manual_assets_root))
    resolved = Path(os.path.realpath(root / source))
    if resolved == root or not resolved.is_relative_to(root):
        raise OutsideMount("the source path is not inside the manual assets mount")
    if not resolved.is_file():
        raise OutsideMount("there is no file at that path on the manual assets mount")
    return resolved


async def _staged_source(config, http, source: str, workspace: Path) -> Path:
    """The operator's source, on local disk and proven fetchable.

    Returns the mount path itself for a file source -- the callers only read
    it, and ``_install`` copies through a staging file of its own, so a copy
    here would buy nothing.
    """
    if not _is_url(source):
        try:
            return await asyncio.to_thread(_mount_source, config.manual_assets_root, source)
        except OutsideMount as exc:
            # The reason, never the path: the response body is read out of a
            # browser console and pasted into tickets, and an endpoint that
            # echoes its input is a reflector. The path is the operator's own
            # string, so there is nothing to learn from repeating it.
            logger.warning("refused a manual source path: %s", exc)
            raise HTTPException(status_code=422, detail=str(exc)) from None

    if http is None:
        # None on an app without the background lifespan (app.py).
        raise HTTPException(status_code=503, detail="no HTTP client on this instance")

    destination = workspace / "source"
    try:
        await guarded_download(
            http, source, destination,
            max_bytes=PICK_MAX_BYTES, content_types=PICK_CONTENT_TYPES,
        )
    except TargetRefused as exc:
        # The request was never made. The guard's message carries the reason
        # and the hop and never the URL, so it is safe to hand back verbatim
        # -- which is the point: "that source was refused" with no reason
        # leaves an operator with a working URL and no idea why.
        logger.warning("refused to fetch a manual source: %s", exc)
        raise HTTPException(status_code=422, detail=f"that source was refused: {exc}") from None
    except BodyRefused as exc:
        # The request was made, and the response came back, but it is not
        # usable artwork -- "status 404", "content type outside the artwork
        # allowlist", "image exceeded the size cap", "empty body". Handed
        # back verbatim like TargetRefused above: the guard's own messages
        # are URL-free by construction (see the guard's module docstring,
        # "a refusal never names the URL"), so there is nothing to withhold
        # and an actionable reason is what the plan promised.
        logger.warning("manual source fetch produced an unusable body: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except (FetchRefused, httpx.HTTPError) as exc:
        # The raw transport failure, plus any future FetchRefused subclass
        # this branch has no specific handling for. NOT str(exc), and NOT
        # exc_info, for the httpx half: its exception messages -- and the
        # traceback exc_info would attach -- can embed the full request URL,
        # which is the one thing that must not reach even the server log. A
        # reason string only, same as the guard's own exceptions carry.
        logger.warning("manual source fetch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502, detail="could not fetch the image from that URL"
        ) from None
    return destination


def _resolved(item: MediaItem) -> ResolvedItem:
    return ResolvedItem(
        rating_key=item.rating_key, library=item.library, kind=item.kind,
        title=item.title, year=item.year,
        season_number=item.season_number, episode_number=item.episode_number,
        root_folder=item.root_folder, file_path=item.file_path, art_url=None,
        tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id, imdb_id=item.imdb_id,
    )


def _logo_suffix(source: str) -> str:
    """The container a picked logo keeps, constrained to the findable ones.

    A logo is composited over the poster and needs the alpha channel a JPEG
    cannot carry, so it is installed untranscoded -- which makes its suffix
    part of the contract with ``find_logo_override``. Taken from the URL's
    path (never its query) or from the file name, the same rule 6d uses.
    """
    if _is_url(source):
        try:
            name = httpx.URL(source).path
        except httpx.InvalidURL:
            return ".png"
    else:
        name = source
    suffix = Path(name).suffix.lower()
    return suffix if suffix in LOGO_OVERRIDE_SUFFIXES else ".png"


def _budgeted_receive(receive, budget: int):
    """``receive``, refusing to hand over more than ``budget`` bytes of body.

    This wrapper is the difference between a cap and a promise. Starlette's
    ``max_part_size`` bounds only the parts that have NO file name
    (``formparsers.MultiPartParser.on_part_data``); a file part is spooled to a
    ``SpooledTemporaryFile`` with no limit at all, and FastAPI's
    ``UploadFile``/``File()`` declaration spools the WHOLE body before a handler
    is ever entered. So a route that declared its body and checked the size
    afterwards would answer 413 to a request that had already filled the disk.

    Refusing inside ``receive`` stops the read at the wire instead: the parser
    never sees another chunk, and the spooled part it had accumulated is bounded
    by the budget and released as the exception unwinds.
    """
    remaining = budget

    async def budgeted():
        nonlocal remaining
        message = await receive()
        if message["type"] == "http.request":
            remaining -= len(message.get("body", b""))
            if remaining < 0:
                raise _UploadTooLarge
        return message

    return budgeted


async def _uploaded_source(request: Request, workspace: Path) -> Path:
    """The one uploaded part, on local disk, under the same cap a URL gets.

    Two ceilings, doing two jobs. The budget on ``receive`` bounds the READ, so
    an endless body is dropped rather than spooled. The running total in the
    copy loop is the exact cap -- ``PICK_MAX_BYTES``, the same number
    ``net/guard.store_body`` enforces on a URL -- checked BEFORE each chunk is
    written, so the staged file cannot hold more than the cap even for an
    instant.

    Nothing about the part except its bytes is read. Its ``Content-Type`` is a
    client claim (``_prepare_jpeg``'s decode is the only content check) and its
    file name is a browser's own string that this service has no use for: the
    stored name comes from ``manual_override_target`` alone.
    """
    bounded = Request(
        request.scope, _budgeted_receive(request.receive, PICK_MAX_BYTES + UPLOAD_ENVELOPE_BYTES)
    )
    try:
        # max_files=1/max_fields=0: one part, named `file`, and nothing else.
        form = await bounded.form(max_files=1, max_fields=0)
    except _UploadTooLarge:
        logger.warning("refused a manual upload: past the byte budget")
        raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE) from None
    except StarletteHTTPException as exc:
        # Starlette's own Request._get_form catches the parser's own
        # MultiPartException and re-raises it as HTTPException(400,
        # exc.message) whenever the request scope carries an "app" key --
        # true for every real request. Its message carries sizes and part
        # counts and is withheld the same way a class name would be. Caught
        # by the STARLETTE base class rather than fastapi's: fastapi's
        # HTTPException subclasses it, so the instance Starlette itself
        # raises here is never caught by the subclass alone.
        if exc.status_code == 400:
            logger.warning("refused a manual upload: malformed multipart body")
            raise HTTPException(status_code=422, detail=UPLOAD_MALFORMED) from None
        raise

    try:
        part = form.get("file")
        if not isinstance(part, UploadFile):
            raise HTTPException(status_code=422, detail=UPLOAD_NO_FILE)
        destination = workspace / "source"
        size = 0
        with destination.open("wb") as handle:
            while chunk := await part.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > PICK_MAX_BYTES:
                    # Before the write, exactly as store_body does it: the file
                    # on disk never holds more than the cap allows.
                    logger.warning("refused a manual upload: past the size cap")
                    raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE)
                handle.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail=UPLOAD_NO_FILE)
    finally:
        await form.close()
    return destination


async def _target_item(session_factory, item_id: int, art_kind: str) -> ResolvedItem:
    """The item this request names, resolved -- or the refusal it earns.

    Both checks happen before anything is fetched, read or written, which is
    the clear-override idiom: a request this endpoint has no answer for never
    reaches the network, the mount, or an upload's byte budget. ``root_folder``
    is nullable and the whole mirror layout is rooted at it, so an item without
    one has nowhere for an override to go.
    """
    async with session_factory() as session:
        item = await _item_for_kind(session, item_id, art_kind)
        if item.root_folder is None:
            raise HTTPException(status_code=409, detail="this item has no asset folder")
        return _resolved(item)


async def _install_and_record(
    request: Request,
    item_id: int,
    art_kind: str,
    resolved: ResolvedItem,
    staged: Path,
    workspace: Path,
    *,
    logo_suffix: str,
    unusable_status: int,
    source_kind: str,
) -> dict:
    """6d's tail: verify or transcode, install atomically, null, record, enqueue.

    Lifted whole out of ``install_manual_source`` so the upload route runs the
    SAME code rather than a second copy of it -- roadmap row 123 asks for "a
    multipart endpoint sharing the same verify/transcode/install tail", and two
    spellings of a tail that writes a mirror the pipeline later stats is exactly
    the drift ``manual_override_target`` was split out to prevent.

    Three parameters carry everything the two callers disagree about:

    * ``logo_suffix`` -- the container a logo keeps, read from the source's own
      name by the JSON route. The upload route never reaches it: it refuses a
      logo outright, because there is no name it is allowed to read.
    * ``unusable_status`` -- whose fault it is that these bytes are not an
      image. 502 for a URL (the far end failed), 422 for a file on the mount or
      an upload (the request was wrong).
    * ``source_kind`` -- the FORM the source took, and the only thing about it
      the events row is allowed to record.
    """
    config = request.app.state.config
    session_factory = request.app.state.session_factory

    try:
        if art_kind == art.LOGO:
            await asyncio.to_thread(_verify_image, staged)
            target = logo_override_path(config, resolved, logo_suffix)
        else:
            target = manual_override_target(config, resolved, art_kind)
            transcoded = workspace / "source.jpg"
            await asyncio.to_thread(_prepare_jpeg, staged, transcoded)
            staged = transcoded
    except DownloadRefused as exc:
        logger.warning(
            "the manual source for %s of item %d is not usable artwork: %s",
            art_kind, item_id, exc,
        )
        raise HTTPException(status_code=unusable_status, detail=str(exc)) from None

    try:
        # Offloaded like every other touch of this mount: manual_assets_root
        # is typically NFS and a hung mount must not stall the event loop.
        await asyncio.to_thread(_install, staged, target)
        if art_kind == art.LOGO:
            await asyncio.to_thread(_clear_stale_logo_overrides, config, resolved, logo_suffix)
    except OSError as exc:
        # Sanitized: os.replace's OSError carries the destination's full
        # filesystem path, which would leak the server's directory layout.
        logger.warning("could not write the manual source to %s: %s", target, exc)
        raise HTTPException(
            status_code=503, detail="could not write to the override mount"
        ) from None

    # Deferred rather than imported at module scope: api/routes.py imports this
    # module's router, so the other direction is a cycle.
    from autoposter.api.routes import _enqueue_reprocess

    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")

        # A logo has no render row of its own and never will: it rides into the
        # POSTER's fingerprint as ``logo_sha`` (render/pipeline.py), so the
        # poster is the row a manual logo invalidates.
        row_kind = "poster" if art_kind == art.LOGO else art_kind
        render = (
            await session.execute(
                select(Render).where(Render.item_id == item_id, Render.art_kind == row_kind)
            )
        ).scalar_one_or_none()
        if render is not None:
            # The fingerprint short-circuit sits above the override lookup, so
            # without this the next pass answers "unchanged" and the operator's
            # image never reaches a pixel.
            render.fingerprint = None
            render.badge_fingerprint = None

        session.add(
            EventLog(
                source="manual",
                event_type="manual_source_installed",
                # Which FORM the source took, and not the source itself --
                # not even its host. A picked candidate's host was a
                # provider's; this one is an operator's own string and can
                # carry userinfo credentials or the name of an internal host,
                # and an events row is read casually and pasted into tickets.
                # An upload's own file name is withheld for the same reason.
                payload={
                    "item_id": item_id,
                    "art_kind": art_kind,
                    "source_kind": source_kind,
                },
                outcome=f"{item.library}/{item.title} {art_kind} from a manual source",
            )
        )
        await session.commit()

        job_id = await _enqueue_reprocess(session, item)
    return {"status": "installed", "queued": job_id is not None}


@router.post("/items/{item_id}/renders/{art_kind}/manual")
async def install_manual_source(
    item_id: int,
    art_kind: str,
    body: ManualSource,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Make an operator-supplied image this item's base artwork.

    The pick endpoint's tail, with the pick's offered-set check replaced by
    the SSRF guard and the mount containment: those two are what stand between
    "the operator chose this image" and "this process fetches or reads
    whatever it is told to".

    ``art_kind`` is validated against the item's own kind before anything is
    fetched, read or written -- the clear-override idiom -- so a request this
    endpoint has no answer for never reaches the network or the mount.
    """
    config = request.app.state.config
    resolved = await _target_item(request.app.state.session_factory, item_id, art_kind)

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        staged = await _staged_source(config, request.app.state.http, body.source, workspace)
        return await _install_and_record(
            request, item_id, art_kind, resolved, staged, workspace,
            # A pure function on the request's own string, so hoisting it out
            # of the logo branch costs nothing and reads nothing extra.
            logo_suffix=_logo_suffix(body.source),
            unusable_status=_unusable_status(body.source),
            source_kind="url" if _is_url(body.source) else "file",
        )


@router.post("/collections/{collection_id}/poster")
async def install_collection_poster(
    collection_id: int,
    body: ManualSource,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Give a managed collection a poster of the operator's choosing.

    Written to ``poster_override_target`` -- the first path
    ``local_poster_path`` probes -- so the reconciler finds it by the lookup
    it already had, and so a poster left on the mount in another format
    cannot shadow this one (``jpg`` is probed first).

    Transcoded rather than written through: ``local_poster_path`` accepts four
    extensions but this endpoint writes exactly one name, so a PNG stored
    under ``poster.jpg`` would be a file whose name and contents disagree. The
    decode is also the only real check that the bytes are the image the
    Content-Type claimed.

    No upload: ``apply_poster`` hashes what it uploads into
    ``poster_sha256`` and locks the field, and a second path to Plex from here
    would do neither. The response says so.
    """
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        collection = (
            await session.execute(
                select(ManagedCollection).where(ManagedCollection.id == collection_id)
            )
        ).scalar_one_or_none()
        if collection is None:
            raise HTTPException(status_code=404, detail="collection not found")
        library, title = collection.library, collection.title

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        staged = await _staged_source(config, request.app.state.http, body.source, workspace)

        transcoded = workspace / "poster.jpg"
        try:
            await asyncio.to_thread(_prepare_jpeg, staged, transcoded)
        except DownloadRefused as exc:
            logger.warning(
                "the manual poster for collection %d is not usable artwork: %s",
                collection_id, exc,
            )
            raise HTTPException(
                status_code=_unusable_status(body.source), detail=str(exc)
            ) from None

        try:
            target = poster_override_target(config, library, title)
        except PosterPathRefused as exc:
            # The title is a stored value, not this request's input, so there
            # is nothing reflected by naming it -- but the same rule as
            # ``_staged_source`` applies to the path itself, which is not
            # repeated. 422: the request names a collection this endpoint
            # cannot write a poster for.
            logger.warning("refused the poster path for %r/%r: %s", library, title, exc)
            raise HTTPException(status_code=422, detail=str(exc)) from None
        try:
            # _install creates the parent: a collection that has never had a
            # local poster has no directory under assets_root at all.
            await asyncio.to_thread(_install, transcoded, target)
        except OSError as exc:
            logger.warning("could not write the collection poster to %s: %s", target, exc)
            raise HTTPException(
                status_code=503, detail="could not write to the assets mount"
            ) from None

    async with session_factory() as session:
        record = (
            await session.execute(
                select(ManagedCollection).where(ManagedCollection.id == collection_id)
            )
        ).scalar_one_or_none()
        if record is not None:
            # The poster short-circuit sits above apply_poster
            # (collections/lists.py:305, smart.py:375, reconcile.py:853,1107),
            # so without this the next pass -- dry-run or real -- answers
            # "unchanged" for a membership-stable collection and the
            # operator's image never reaches Plex. Mirrors the fingerprint
            # null in install_manual_source above. No row yet is a no-op,
            # the same posture that endpoint takes when render is None.
            record.poster_sha256 = None

        session.add(
            EventLog(
                source="manual",
                event_type="collection_poster_installed",
                payload={
                    "collection_id": collection_id,
                    "source_kind": "url" if _is_url(body.source) else "file",
                },
                outcome=f"{library}/{title} poster from a manual source",
            )
        )
        await session.commit()

    return {"status": "installed", "applies": "next reconcile"}


@router.post("/items/{item_id}/renders/{art_kind}/manual/upload")
async def upload_manual_source(
    item_id: int,
    art_kind: str,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Make an image from the operator's own machine this item's base artwork.

    The third form of ``install_manual_source``'s request and the same tail:
    what the URL branch proves with ``net/guard`` and the path branch proves
    with double-``realpath`` containment, this branch proves with a byte budget
    and a decode. There is nothing to contain -- the bytes never had a path --
    and nothing to guard against -- no request leaves this process.

    Declared with no body parameter on purpose. ``UploadFile``/``File()`` would
    put the whole body on disk before this function ran, which is the one thing
    the cap exists to prevent; ``_uploaded_source`` reads the form itself under
    a budget instead. That also keeps this route off the
    ``RequestValidationError`` path entirely: every refusal below is a fixed
    sentence written here.

    Session-only, like every other write on this router, and deliberately NOT
    in ``api/auth.ALLOWLIST``: an API key opens three GETs and nothing else, and
    ``tests/test_api_key.py``'s sweep fails if that ever stops being true.

    No logo: ``_logo_suffix`` derives a logo's stored name from the SOURCE's own
    name, and an upload's name is a browser's string this endpoint is not
    allowed to read or keep. A logo still installs from a URL or a mount path.

    And never a Plex upload. This writes to ``manual_assets_root`` and stops,
    exactly as the other two sources do -- an ``upload://`` entry cannot be
    deleted through Plex's API (rows 241/242), so a browser-originated Plex
    write would be unrecoverable.
    """
    resolved = await _target_item(request.app.state.session_factory, item_id, art_kind)
    if art_kind == art.LOGO:
        raise HTTPException(status_code=422, detail=UPLOAD_NO_LOGO)

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        staged = await _uploaded_source(request, workspace)
        return await _install_and_record(
            request, item_id, art_kind, resolved, staged, workspace,
            # Never reached: the logo branch is refused above. Named anyway so
            # the helper's contract has no optional half.
            logo_suffix=".png",
            # The caller's file, so the caller's fault -- the same 422 a bad
            # file on the mount earns, never the 502 a failing far end does.
            unusable_status=422,
            source_kind="upload",
        )
