"""The operator's own overlay images and font faces, as files (roadmap row 55).

Its own module for the reason `api/artwork.py` and `api/manual.py` are theirs:
these are the only handlers in this service that WRITE a request-named path,
and the four rules that make that safe are the substance of it.

**The roots never move.** `fonts_root` and `overlays_root` are hashed as
STRINGS into `config/loader.py::_shared_render_inputs`, a literal member of all
four art kinds' `render_version` payloads, so repointing either one would move
every stored fingerprint in the library. This module reads them; it never
suggests a different one.

**Containment is `api/artwork.py`'s idiom, not `overlays/sources.py::_confined`.**
`_confined` calls `Path.resolve()` and then checks parentage, which accepts a
symlink whose target is outside the root; and it is owned by two branches in
flight. Here both sides go through `os.path.realpath` and the result must be a
regular file whose parent IS the root -- so `..`, an absolute path, a
subdirectory and a planted symlink are each refused on their target rather than
on their spelling.

**Overwrite is refused (409), and that is a deliberate design decision rather
than caution.** The two stages disagree about what replacing a file in place
means. The artwork stage hashes the BYTES of the overlay and the fonts into
every render fingerprint (`config/impact.py:163,170,174,181`;
`render/pipeline.py::compute_fingerprint`), so an in-place replacement
re-renders and re-uploads every item drawn with it -- a storm. The badge stage
does not: `badges/compose.py::badge_fingerprint` hashes the shipped badge
MANIFEST and the definitions' FIELDS, never a file under `overlays_root`, so an
in-place replacement leaves every already-badged item on the old artwork while
new items get the new one -- a freeze, with no signal anywhere. Neither
behaviour is right for the other, so this module refuses to pick: an upload
under a NEW name moves nothing until a config value names it, and
delete-then-upload is how an operator says which of the two they meant.

**Row 213.** A refusal body never carries a path, a size, a part count, a
content type, a `str(exc)` or the submitted file name. A name refusal states
the RULE. The listing serves basenames and never a root.
"""

import asyncio
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from PIL import Image, ImageFont
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from autoposter.api.auth import require_session
from autoposter.config.loader import RENDER_ART_KINDS, config_for_library
from autoposter.db.models import EventLog
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

router = APIRouter()

FileKind = Literal["overlays", "fonts"]

# What each directory is FOR, and therefore what it lists, accepts and deletes.
# No woff/woff2 -- nothing in this tree renders one and ImageMagick may not
# either. No JPEG overlays -- nothing writes one, and an overlay needs alpha.
SUFFIXES: dict[str, frozenset[str]] = {
    "overlays": frozenset({".png"}),
    "fonts": frozenset({".ttf", ".otf"}),
}

# Named for a runtime dependency, not for being bundled. `fonts_root` is TWO
# things at once: the operator's font directory and the image's own
# `assets/fonts`, which `collections/separator_art.py:86` reads directly --
# `FONT = asset_path("fonts") / "Comfortaa-Medium.ttf"`, passed to magick's
# `-font` at `:168`/`:174`, bypassing `fonts_root` entirely. Deleting it breaks
# separator-collection art with a magick font error. `OFL.txt` and
# `PROVENANCE.md` are the licence and the provenance the OFL Reserved Font Name
# clause hangs on; they are also outside the suffix allowlist, so this set is
# what gives them a refusal that says why rather than one about file names.
PROTECTED: dict[str, frozenset[str]] = {
    "overlays": frozenset(),
    "fonts": frozenset({"Comfortaa-Medium.ttf", "OFL.txt", "PROVENANCE.md"}),
}

# Long enough for any real face or overlay name, short enough that a name is
# never a payload. Checked before the character set so a megabyte of legal
# characters is refused on length.
NAME_MAX = 64

_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

# The served refusals. Fixed sentences, every one of them, for the reason
# `api/manual.py:65-73` gives: these bodies are read out of a browser console
# and pasted into tickets, and a size, a path or an echoed name in one is a
# reflector. The name rule names the RULE and never the name.
FILE_NAME_REFUSED = (
    "the file name must be ASCII letters, digits, dashes and underscores plus "
    "one extension this directory accepts, and at most 64 characters"
)
FILE_NOT_FOUND = "there is no such file in that directory"
FILE_PROTECTED = "that file ships with this service and cannot be replaced or removed"
FILE_REFERENCED = "the running configuration still names that file"
FILE_EXISTS = "a file of that name is already there; delete it first"
FILE_UNREADABLE = "the uploaded file is not one this service can use"


class OutsideRoot(Exception):
    """A name that does not resolve to a regular file directly under the root."""


def root_for(config, kind: str) -> Path:
    """The directory this kind lives in.

    Read off `Config` every call rather than captured: the config editor swaps
    `app.state.config` wholesale, and a captured root would keep serving the
    previous deployment's directory after a save.
    """
    return Path(config.overlays_root if kind == "overlays" else config.fonts_root)


def normalised_name(kind: str, submitted: str) -> str:
    """`submitted` as a name this directory can hold, or the 422 it earns.

    Row 123 sidestepped this entirely -- its stored name comes from
    `manual_override_target`, so the browser's string was read for nothing.
    Row 55 cannot: the operator's chosen name IS the handle a config value
    later writes, and it has to survive being typed into `artwork.poster.
    overlay_file` afterwards. So the rule is deliberately narrower than the
    filesystem's: one basename, ASCII, one extension, and that extension on
    this kind's allowlist.

    `Path(submitted).name != submitted` is the first check and it is the one
    that matters: it refuses `../x.png`, `/etc/x.png` and `a/b.png` on their
    SHAPE, before any of them reaches a filesystem call.
    """
    if (
        not submitted
        or len(submitted) > NAME_MAX
        or Path(submitted).name != submitted
        or submitted.startswith(".")
        or submitted.count(".") != 1
        or set(submitted) - _NAME_CHARS
        or Path(submitted).suffix.lower() not in SUFFIXES[kind]
    ):
        # The name is not repeated, here or in the log line: it is attacker
        # input on the way to a body an operator pastes into a ticket.
        logger.warning("refused a file name for %s: outside the name rule", kind)
        raise HTTPException(status_code=422, detail=FILE_NAME_REFUSED)
    return submitted


def contained(root: Path, name: str) -> Path:
    """`name` as a regular file directly under `root`, or `OutsideRoot`.

    `api/artwork.py:74-99`'s idiom: both sides through `os.path.realpath`
    before being compared, so a symlink planted under the root and pointing at
    `/etc/shadow` is refused on its target. A lexical check -- `normpath`, or a
    `startswith` on the strings -- would let that through.

    Stricter than that idiom in one way, and it is the difference between a
    served file and a deleted one: the resolved parent must BE the root, not
    merely contain it, so a subdirectory is refused rather than descended.

    Synchronous and called from a thread: these roots can be any mount, so the
    realpath walk and the stat stay off the event loop that also carries the
    workers, the scheduler and the liveness probe.
    """
    real_root = Path(os.path.realpath(root))
    candidate = root / name
    if candidate.is_symlink():
        raise OutsideRoot("the name is a symbolic link")
    resolved = Path(os.path.realpath(candidate))
    if resolved.parent != real_root or resolved.name != name:
        raise OutsideRoot("the name does not resolve to a file in that directory")
    if not resolved.is_file():
        raise OutsideRoot("there is no regular file at that name")
    return resolved


def _library_family_definitions(config) -> list[tuple[str, object]]:
    """(where, definition) for every definition a LIBRARY draws and the global
    config does not.

    `badges.families` is overridable per library (`config/schema.py:1716`;
    `definitions` is excluded at `:1699`), so a family enabled on one library
    only expands into definitions whose `font:` is resolved by
    `overlays/sources.py:127-128` -- `fonts_root` FIRST, the bundled face only
    as a fallback. Enumerating the global config alone therefore reported
    `referenced_by: []` about a face a render reads, and the delete went
    through: that library's badge stage then silently switched to the bundled
    face, and `badges/compose.py::badge_fingerprint` hashes the manifest and
    the definitions' fields rather than font bytes, so nothing re-rendered and
    nothing was signalled.

    `config/loader.py::config_for_library` is the ONE place an override is
    applied -- the same call `render/pipeline.py:1651` makes -- so it is
    called here rather than the merge being re-stated. A library that names no
    override, or none that states families, gets the global object back and
    contributes nothing.

    A family the GLOBAL config already names is skipped: the global pass
    reports it, and reporting it again per library would say the same sentence
    once per configured library.

    `where` is the family an operator EDITS rather than the bundled definition
    it expands into, because the definition is not a line in their config.
    `FAMILIES` is imported here rather than at module scope for
    `BadgesConfig._check_families`' own reason (`config/schema.py:1367-1377`):
    constructing ~40 definitions pulls `overlays.selection` and `langcodes`,
    and an operator with no per-library family must not pay for one.
    """
    extras: list[tuple[str, str]] = []
    for library in config.libraries:
        effective = config_for_library(config, library)
        for family in effective.badges.families:
            if family not in config.badges.families:
                extras.append((library, family))
    if not extras:
        return []

    from autoposter.overlays.families import FAMILIES

    return [
        (f"libraries.{library}.badges.families.{family}", definition)
        for library, family in extras
        for definition in FAMILIES[family]
    ]


def _references(config, kind: str) -> dict[str, list[str]]:
    """{file name: the config paths that name it}, from the LIVE config.

    Enumerated from the readers rather than guessed, and the enumeration is
    the whole value of this function -- a file this misses is a file an
    operator can delete out from under a render.

    Overlays, two readers:
      * `artwork.<kind>.overlay_file` -- `config/impact.py:163` hashes the
        file's bytes into that kind's fingerprint;
      * a badge/overlay definition's `file:` -- `overlays/sources.py:187` --
        and, for a definition naming no `file`/`builtin`/`url` at all, the
        name-keyed last rung at `:212`, which looks for `<name>.png` here.

    Fonts, four readers:
      * each art kind's `text.font` (`impact.py:170`), the title card's
        `episode_text.font` (`:174`) and the season poster's
        `show_title.font` (`:181`);
      * both collection poster-title blocks
        (`collections/poster_title.py:457` -> `_font` -> `resolve_font_path`);
      * a definition's `font:` (`overlays/sources.py:128`).

    `all_definitions()` rather than `definitions`, because a FAMILY expands
    into definitions that can name files too (`config/schema.py:1398`). Keyed
    by the definition's NAME rather than its index for the same reason: the
    expansion means an index into `all_definitions()` is not an index into the
    `badges.definitions` an operator edits.

    And `all_definitions()` on the GLOBAL config is not the whole draw:
    `badges.families` is overridable per library, so
    `_library_family_definitions` adds every definition a library draws and
    the global config does not.

    `getattr(config.artwork, art_kind)` rather than
    `render/pipeline.py::art_config_for`, which is that one getattr: importing
    `render.pipeline` here would pull httpx, the provider ladder and the badge
    stack into a module that renders nothing.
    """
    found: dict[str, list[str]] = {}

    def note(value: str | None, where: str) -> None:
        # The BASENAME, unconditionally, rather than only a value that already
        # IS one. Nothing constrains these fields to a bare name
        # (`config/schema.py:329`, `:428`: plain `str`, no validator), and
        # every reader joins the value onto the root with pathlib's `/`, which
        # returns an ABSOLUTE right-hand side whole and drops a leading `./`.
        # So `/app/assets/overlays/brand.png` and `./brand.png` both resolve to
        # exactly the file this listing serves as `brand.png`, and reading
        # either as naming nothing would put a Delete button in front of an
        # input every poster fingerprint hashes.
        #
        # One-sided on purpose. A value naming a same-named file OUTSIDE the
        # root now earns a false REFUSAL, which is one config edit to undo; the
        # other way round is a false permit, and the deleted file then hashes
        # as `""` (`config/impact.py:117-123`) and re-renders the whole library.
        name = Path(value).name if value else ""
        if name:
            # One sentence per naming path: a family expands into many
            # definitions naming one face, and `referenced_by` repeating the
            # same family a dozen times says nothing the first one did not.
            where_names = found.setdefault(name, [])
            if where not in where_names:
                where_names.append(where)

    global_definitions = [
        (f"badges.definitions.{definition.name}", definition)
        for definition in config.badges.all_definitions()
    ]
    definitions = global_definitions + _library_family_definitions(config)
    if kind == "overlays":
        for art_kind in RENDER_ART_KINDS:
            settings = getattr(config.artwork, art_kind)
            note(settings.overlay_file, f"artwork.{art_kind}.overlay_file")
        for where, definition in definitions:
            if definition.file:
                note(definition.file, f"{where}.file")
            elif not definition.builtin and not definition.url:
                note(f"{definition.name}.png", f"{where}.name")
    else:
        for art_kind in RENDER_ART_KINDS:
            settings = getattr(config.artwork, art_kind)
            for field in ("text", "episode_text", "show_title"):
                style = getattr(settings, field, None)
                if style is not None:
                    note(style.font, f"artwork.{art_kind}.{field}.font")
        title = config.collections.poster_title
        note(title.title.font, "collections.poster_title.title.font")
        note(title.collection_line.font, "collections.poster_title.collection_line.font")
        for where, definition in definitions:
            note(definition.font, f"{where}.font")
    return found


def _listing(root: Path, kind: str, references: dict[str, list[str]]) -> list[dict]:
    """The directory as rows. Synchronous, called from a thread.

    `iterdir` rather than `rglob`: a subdirectory is not descended, because
    nothing under these roots reads one -- `overlays_root/.cache/` is this
    service's own download cache (`overlays/sources.py:239`), not an operator
    asset. A dotfile is skipped for the same reason, and it is also what keeps
    an interrupted upload's staging file invisible.
    """
    rows = []
    protected = PROTECTED[kind]
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            continue
        if entry.suffix.lower() not in SUFFIXES[kind]:
            continue
        stat = entry.stat()
        rows.append(
            {
                "name": entry.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                "protected": entry.name in protected,
                "referenced_by": references.get(entry.name, []),
            }
        )
    return rows


@router.get("/files/{kind}")
async def list_files(
    kind: FileKind, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """What is in this directory, by NAME.

    Session auth, the config editor's (`api/routes.py:1541`). Deliberately NOT
    on `api/auth.py`'s `ALLOWLIST`: a read-only API key is for a Homepage
    widget, and the shape of an operator's brand directory is not widget
    material. Row 51's structural keyed sweep pins exactly that allowlist, so
    this stays refused without any change there.

    A root that does not exist is an empty listing rather than a 500: on a
    deployment whose volume has not been seeded yet, "there is nothing here" is
    the honest answer and the upload route will create the directory.
    """
    config = request.app.state.config
    root = root_for(config, kind)
    references = _references(config, kind)
    if not await asyncio.to_thread(root.is_dir):
        return {"files": []}
    return {"files": await asyncio.to_thread(_listing, root, kind, references)}


@router.delete("/files/{kind}/{name}")
async def delete_file(
    kind: FileKind,
    name: str,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Remove one file the operator put here.

    Four refusals, in this order, and the order is the point: each one is
    cheaper and less trusting than the next, so a request that will be refused
    never reaches a filesystem call it did not have to make.

    1. the protected set -- `Comfortaa-Medium.ttf` because
       `collections/separator_art.py:86` reads it directly and separator art
       breaks without it, `OFL.txt` and `PROVENANCE.md` because the OFL
       Reserved Font Name clause hangs on them. It is a literal membership
       test on a frozenset, so it is cheaper than the name rule as well as
       ahead of it -- and ahead of it deliberately, because two of its three
       members carry a suffix this directory does not otherwise manage. Behind
       the name rule they would earn a refusal about file names, which is true
       and useless; in front of it they earn the one that says why;
    2. the name rule -- shape only, no I/O;
    3. containment -- the double-`realpath` check that this is a regular file
       directly under the root;
    4. `referenced_by` -- the running configuration still names it, so deleting
       it would leave the next render with a missing input (which
       `config/impact.py:117-123` hashes as `""`, silently changing the
       fingerprint rather than failing).

    404 for absent, for a directory, for a symlink, for anything that resolves
    outside and for a file that stops being one between the check and the
    `unlink` -- one status for every "there is nothing here you may delete", so
    the response cannot be used to probe what exists outside the root.
    """
    config = request.app.state.config
    if name in PROTECTED[kind]:
        raise HTTPException(status_code=409, detail=FILE_PROTECTED)
    normalised = normalised_name(kind, name)
    root = root_for(config, kind)
    try:
        target = await asyncio.to_thread(contained, root, normalised)
    except OutsideRoot:
        logger.warning("refused a %s delete: the name is not a file in that directory", kind)
        raise HTTPException(status_code=404, detail=FILE_NOT_FOUND) from None
    if _references(config, kind).get(normalised):
        raise HTTPException(status_code=409, detail=FILE_REFERENCED)

    try:
        await asyncio.to_thread(target.unlink)
    except OSError:
        # The window between the containment check and this call is benign for
        # containment -- `Path.unlink` is `os.unlink`, which never dereferences
        # a final symlink, so a file swapped for one removes the LINK and
        # nothing else -- but it is not benign for the status code. A second
        # browser tab deleting the same file first raises `FileNotFoundError`,
        # and a swap to a directory raises `IsADirectoryError`; uncaught,
        # either answers 500 where the same request a millisecond earlier
        # earned 404. `OSError` covers both and the permission case, and the
        # answer is the refusal this route already serves for "there is
        # nothing here you may delete" -- the exception is not served, so it
        # cannot carry the path it was raised on.
        logger.warning("a %s delete could not be completed: the file is no longer there", kind)
        raise HTTPException(status_code=404, detail=FILE_NOT_FOUND) from None

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        session.add(
            EventLog(
                source="files",
                event_type="asset_file_deleted",
                # The NAME is recorded, unlike an upload's own file name in
                # `api/manual.py:430`. There it is a browser's string this
                # service has no use for; here it is a name this service
                # normalised to its own character set and already serves in
                # its own listing, and an audit row that cannot say WHICH file
                # went is not an audit row.
                payload={"kind": kind, "name": normalised},
                outcome=f"deleted {kind}/{normalised}",
            )
        )
        await session.commit()

    return {"status": "deleted", "name": normalised}


def _verify(kind: str, path: Path) -> None:
    """These bytes are a file this service can actually use, or `SourceRefused`.

    **PNG.** Three checks, in the order that lets each one answer for itself.

    The format is read first, off the header, because it is the cheap one and
    because the two ways a `.png` can be something else are both answered
    there: Pillow refuses to open an SVG at all, and it opens a JPEG happily
    and says so. Both matter, because
    `render/artwork_fetch.py::_validate_image` RETURNS EARLY for anything
    `_looks_like_svg` recognises (`artwork_fetch.py:129-130`) and accepts any
    format Pillow decodes, so a JPEG or a WebP renamed `.png` walks straight
    through it. An overlay is composited by `badges/compose.py`'s Pillow
    `_load` and needs real PNG alpha; a `.png` that is secretly a JPEG would
    fail there, per item, in a worker thread, which is the failure this check
    moves to the door.

    Then `_validate_image` -- the same full-pixel decode added after job 40478,
    a PNG whose header is valid and whose IDAT stream contradicts it, which
    `magick identify` and Pillow's `verify()` both pass. Reused, never
    re-implemented; `overlays/sources.py:136-170` reuses it for exactly the
    same bytes arriving by a different door. It runs last because it is the
    expensive one, and because by then the file is known to be the one kind of
    file it is being asked about.

    **Font.** No equivalent exists in this tree, and the honest check is a real
    FreeType parse -- which is what `magick` will do anyway when
    `render/compositor.py::build_text_argv` and
    `collections/separator_art.py:168` hand it the `-font` argument. A font is
    executable-adjacent input to that parser, and this endpoint is the ONLY
    place a font's size is ever bounded: `render/artwork_fetch.py:64`'s
    `RENDER_MAX_BYTES` guards a downloaded artwork SOURCE, and the font path is
    a config value with no pre-check at all.

    Both decoders are handed operator bytes and may raise anything they like --
    Pillow's `UnidentifiedImageError`, an `OSError` out of zlib, a
    `struct.error` out of FreeType -- and every one of them means the same
    thing here, so both are caught wholesale and answered with the single fixed
    refusal this route serves.

    Neither branch reads the part's `Content-Type` or its file name --
    `api/manual.py:274-277`'s rule. Blocking; called from a thread.
    """
    from autoposter.render.artwork_fetch import SourceRefused, _validate_image

    if kind == "overlays":
        try:
            with Image.open(path) as image:
                fmt = image.format
        except Exception as exc:
            raise SourceRefused("an uploaded overlay is not an image this service opens") from exc
        if fmt != "PNG":
            raise SourceRefused("an uploaded overlay is not a PNG")
        _validate_image(path, "an uploaded overlay")
        return
    try:
        ImageFont.truetype(str(path), 40)
    except Exception as exc:
        raise SourceRefused("an uploaded font is not a parseable face") from exc


@router.post("/files/{kind}")
async def upload_file(
    kind: FileKind, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Put one operator-supplied file into this directory.

    **Two ceilings, doing two jobs**, both lifted from `api/manual.py` rather
    than re-derived. The budget on `receive` (`_budgeted_receive`, `:233-259`)
    bounds the READ, so an endless body is dropped at the wire instead of being
    spooled: Starlette's `max_part_size` bounds only parts with NO file name,
    and a FastAPI `UploadFile`/`File()` declaration spools the WHOLE body
    before a handler is entered. The running total in the copy loop is the
    exact cap -- `PICK_MAX_BYTES`, one number for every upload door in this
    service -- checked BEFORE each chunk is written, so the staged file cannot
    hold more than the cap even for an instant.

    **The protected set is checked FIRST**, on the submitted string, before the
    name rule and before a byte of the part is read -- the same order
    `delete_file` uses and for the same reason: `OFL.txt` and `PROVENANCE.md`
    carry a suffix `SUFFIXES["fonts"]` does not manage, so behind the name rule
    they would earn a refusal about file names, which is true and useless.

    **The submitted name reaches `normalised_name` whole.** Not
    `Path(submitted).name`, which would quietly REWRITE `../escape.png` into
    something storable instead of refusing it: the name rule's first check
    exists to refuse that shape, and taking the basename in front of it would
    take the refusal away.

    **Staged, verified, then linked.** The bytes land on a dotfile under the
    root (`mkstemp`, so it is on the same filesystem and the publish is
    atomic; a dotfile, so the listing never shows it and no config value can
    name it), are verified there, and only then become the operator's name.
    `os.link` rather than `os.replace` because `os.replace` overwrites
    silently and this route must not: `link` is the create-if-absent primitive,
    so the 409 is decided by the filesystem rather than by a check with a race
    in front of it. The staging file is removed in `finally` whichever way the
    request ends.

    **Overwrite is refused (409)** for the reason this module's docstring
    gives: the artwork stage storms on an in-place replacement and the badge
    stage freezes, and delete-then-upload is how the operator says which of the
    two they meant. Growing `badge_fingerprint` a file-bytes input instead is a
    Law B change with its own whole-library invalidation, and a different row.

    **The upload itself moves nothing.** No config value is written, so
    `render_version_for` is untouched for all four kinds and no stored
    fingerprint can move until the operator names the file in a config value --
    at which point row 111's per-kind version confines the invalidation to the
    kind that names it. `tests/test_api_files_upload.py`'s storm-guard tests
    are the pin.
    """
    from autoposter.api.candidates import PICK_MAX_BYTES
    from autoposter.api.manual import (
        UPLOAD_CHUNK_BYTES,
        UPLOAD_ENVELOPE_BYTES,
        UPLOAD_MALFORMED,
        UPLOAD_NO_FILE,
        UPLOAD_TOO_LARGE,
        _budgeted_receive,
        _UploadTooLarge,
    )
    from autoposter.render.artwork_fetch import SourceRefused

    # The parser's own exception, and it is imported rather than caught as the
    # `ValueError` it happens to subclass: `python-multipart` is a declared
    # dependency of this project under exactly this name (`pyproject.toml:37`)
    # and the name says what is being refused.
    from python_multipart.exceptions import MultipartParseError

    config = request.app.state.config
    root = root_for(config, kind)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)

    bounded = Request(
        request.scope,
        _budgeted_receive(request.receive, PICK_MAX_BYTES + UPLOAD_ENVELOPE_BYTES),
    )
    try:
        # max_files=1/max_fields=0: one part, named `file`, and nothing else.
        form = await bounded.form(max_files=1, max_fields=0)
    except _UploadTooLarge:
        logger.warning("refused a %s upload: past the byte budget", kind)
        raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE) from None
    except MultipartParseError:
        # Starlette converts only its OWN `MultiPartException`
        # (`starlette/requests.py:292`), and a body that declares a boundary
        # and then contradicts it -- a truncated browser upload, or a proxy
        # that rewrote the body without the header -- comes out of
        # `python_multipart` instead and would otherwise be an unhandled 500.
        # Its message carries offsets and byte values, so the served sentence
        # is the same fixed one the missing-boundary case earns.
        logger.warning("refused a %s upload: the multipart body does not parse", kind)
        raise HTTPException(status_code=422, detail=UPLOAD_MALFORMED) from None
    except StarletteHTTPException as exc:
        # Starlette re-raises the parser's MultiPartException as
        # HTTPException(400, exc.message) whenever the scope carries an "app"
        # key -- true for every real request -- and that message carries sizes
        # and part counts. Caught by the STARLETTE base class rather than
        # fastapi's subclass, which never sees the instance Starlette raises.
        if exc.status_code == 400:
            logger.warning("refused a %s upload: malformed multipart body", kind)
            raise HTTPException(status_code=422, detail=UPLOAD_MALFORMED) from None
        raise

    staged: Path | None = None
    try:
        part = form.get("file")
        if not isinstance(part, UploadFile) or not part.filename:
            raise HTTPException(status_code=422, detail=UPLOAD_NO_FILE)
        if part.filename in PROTECTED[kind]:
            raise HTTPException(status_code=409, detail=FILE_PROTECTED)
        name = normalised_name(kind, part.filename)

        handle, staged_path = await asyncio.to_thread(
            tempfile.mkstemp, prefix=".upload-", dir=str(root)
        )
        staged = Path(staged_path)
        size = 0
        with os.fdopen(handle, "wb") as sink:
            while chunk := await part.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > PICK_MAX_BYTES:
                    logger.warning("refused a %s upload: past the size cap", kind)
                    raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE)
                sink.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail=UPLOAD_NO_FILE)

        try:
            await asyncio.to_thread(_verify, kind, staged)
        except SourceRefused:
            # `str(exc)` is deliberately not served: it names decoders, sizes
            # and pixel counts. It is logged instead.
            logger.warning("refused a %s upload: the bytes are unusable", kind, exc_info=True)
            raise HTTPException(status_code=422, detail=FILE_UNREADABLE) from None

        # 0644 rather than mkstemp's 0600: these files are read by the same uid
        # that writes them AND are the same class of file the init container's
        # `cp` seeds, so matching that mode is what stops the listing showing
        # two kinds of file. Applied to the staging file, before it is linked,
        # so the published name never exists at the wrong mode.
        await asyncio.to_thread(os.chmod, staged, 0o644)
        try:
            await asyncio.to_thread(os.link, str(staged), str(root / name))
        except FileExistsError:
            raise HTTPException(status_code=409, detail=FILE_EXISTS) from None
    finally:
        await form.close()
        if staged is not None:
            await asyncio.to_thread(staged.unlink, True)

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        session.add(
            EventLog(
                source="files",
                event_type="asset_file_uploaded",
                # The NAME only, exactly as `delete_file` records it and for
                # the same reason: it is a name this service normalised to its
                # own character set and already serves in its own listing, and
                # an audit row that cannot say WHICH file arrived is not an
                # audit row. Nothing about the bytes, the part or the request.
                payload={"kind": kind, "name": name},
                outcome=f"stored {kind}/{name}",
            )
        )
        await session.commit()

    return {"status": "stored", "name": name}
