"""Where an overlay's image comes from.

Kometa's precedence, banked by the overlay grammar probe
section 1.2: `file` > `default`/`pmm` > `git` > `repo` > `url` > a name-keyed
fallback. Two of those rungs do not exist for this service and are not
invented -- there is no Kometa Configs-repo mirror (`git`) and no custom-repo
setting (`repo`).

The `builtin` rung resolves against THIS service's bundled overlay-stamp tree,
which is the same shape as Kometa's own `defaults/overlays/images/` -- probe
section 6 specifically corrects the earlier premise that these assets come
from the `Kometa-Team/Default-Images` repo. They do not; that is a different
repo with a different (poster-sized) naming scheme.

Both path rungs are confined to their root. A definition is operator-typed
config, and an operator-typed path that can leave its mount is a file-read
primitive, not a convenience.
"""
import asyncio
import hashlib
import os
import uuid
from pathlib import Path

import httpx

from autoposter.net.guard import FetchRefused, guarded_download
from autoposter.overlays.assets import IMAGES, INTER_BOLD, INTER_MEDIUM
from autoposter.overlays.schema import OverlayDefinition

# Probe section 1.2: Kometa validates `Content-Type == "image/png"` on every
# downloaded overlay image and rejects anything else. Carried verbatim into
# net/guard.py's allowlist parameter.
OVERLAY_CONTENT_TYPES = frozenset({"image/png"})


class OverlaySourceError(Exception):
    """This overlay's image could not be resolved.

    Carries the overlay's name and the reason, never a URL or a host -- the
    same rule net/guard.py holds itself to, for the same reason: this message
    reaches a log line an operator reads.
    """


def _confined(root: Path, value: str, name: str) -> Path:
    """A path strictly beneath `root`, or a refusal.

    `root` itself is refused too, not just an escape from it: the only
    earlier exception was `candidate != root`, so `file: "."` (or any
    directory under the root) passed containment and `.exists()`, handing
    back a directory that `_load` then failed on deep inside the compose
    thread instead of a clean refusal here.
    """
    candidate = (root / value).resolve()
    root = root.resolve()
    if root not in candidate.parents:
        raise OverlaySourceError(
            f"overlay {name!r}: the configured path leaves its root directory"
        )
    return candidate


# The faces this service SHIPS, keyed by the bare filename a definition
# writes (roadmap row 100, adjudication A-4).
#
# Why this exists: the `aspect` family is the first shipped family that draws
# TEXT, and a family definition's `font:` flows through `resolve_font_path`
# below, which confines the value strictly beneath the OPERATOR's own
# `fonts_root` mount. The bundled faces live under `assets/badges/fonts/` and
# are reachable only by the BUILTIN draw path (`badges/compose.py` calls
# `ImageFont.truetype(definition.font, ...)` directly for the nine builtins,
# and never consults `fonts_root` for them). So without this rung, every
# aspect badge would be refused and skipped for every operator whose
# `fonts_root` does not happen to contain Inter-Medium -- a visible parity
# divergence on every aspect badge, or none at all.
#
# Why a dict rather than a directory join: this is an EXACT-NAME lookup on
# the whole written value, so no operator string is ever joined onto a path
# here and the rung adds no traversal surface of its own. A written value
# with any path structure in it -- `../fonts/Inter-Medium.ttf`,
# `fonts/Inter-Medium.ttf`, `./Inter-Medium.ttf` -- misses this table and is
# handled by `_confined` exactly as it was before, which is a refusal.
#
# Why not a new `OverlayDefinition` field: a field would owe
# `badges/compose.py::_definitions_digest` its one-line `pop` under that
# function's LAW, and getting that wrong moves the digest for every
# already-badged item. An accessor branch owes nothing, so the
# digest-evolution law is never engaged and no already-badged item can move.
BUNDLED_FONTS: dict[str, Path] = {
    "Inter-Bold.ttf": INTER_BOLD,
    "Inter-Medium.ttf": INTER_MEDIUM,
}


def resolve_font_path(fonts_root: Path | None, value: str, name: str) -> Path:
    """A definition's `font:` value, as a real file on disk, or a refusal.

    Two rungs, in this order:

    1. **the operator's own mount.** The field's own description promises "a
       path beneath fonts_root" -- the same rung this schema's `file:` image
       source uses, so it reuses the same `_confined` (root itself refused,
       not just an escape from it) rather than trusting the string raw.
       `badges/compose.py::_draw_definitions` was passing the raw string
       straight to `ImageFont.truetype`, which resolves against the process
       cwd, not `fonts_root` -- guaranteed to fail for the documented
       spelling, and the one path in this module that skipped `_confined`
       entirely (H1).
    2. **this service's own bundled faces** (`BUNDLED_FONTS` above,
       adjudication A-4). Consulted only when rung 1 did not produce an
       existing file, so an operator who puts their own `Inter-Medium.ttf` in
       `fonts_root` gets theirs -- the bundle is a fallback, never an
       override.

    `fonts_root` is optional because `compose`'s own parameter is: a caller
    that passes no mount can still draw a bundled face, which is what makes
    the `aspect` family work everywhere. A traversal attempt still raises
    from `_confined` before rung 2 is reached, so widening this function did
    not widen the containment check H1 added.

    Raises `OverlaySourceError` when neither rung answers. The caller
    (`_draw_definitions`) catches it and skips just THAT definition, naming
    it -- never silently.
    """
    bundled = BUNDLED_FONTS.get(value)
    if fonts_root is not None:
        path = _confined(fonts_root, value, name)
        if path.exists():
            return path
    if bundled is not None and bundled.exists():
        return bundled
    raise OverlaySourceError(f"overlay {name!r}: the configured font does not exist")


def _validate_overlay_image(path: Path, name: str) -> None:
    """Full-decode an overlay image before it is trusted, whatever rung it
    came from.

    Originally only the `url:` rung's own downloaded bytes (hence the name
    this used to have); `file:`, `builtin:` and the name-keyed fallback
    handed their paths to `badges/compose.py`'s `_load` -- straight into
    Pillow, since overlay images never go through magick -- fully undecoded.
    A file under `overlays_root` that is not a decodable image (a truncated
    upload, a saved HTML error page, a `.png` that is really a `.webp`)
    raised `UnidentifiedImageError` deep inside the compose worker thread
    instead of a clean per-definition refusal here. Renamed and called from
    every rung in `resolve_image_path` below.

    Reuses render/pipeline.py's own `_validate_image` (#131) -- the same
    protection added for provider artwork after job 40478 (a PNG whose header
    is valid and whose IDAT stream contradicts it, which only a full pixel
    decode catches) -- rather than keeping a second copy that can drift from
    it.

    Call-time import, the same trick `overlays/schema.py::_as_rgba` uses for
    Pillow: `render/pipeline.py` imports this module at its own top level,
    so a module-level import back here would be the real
    cycle. A call-time one is not -- by the time this function actually
    runs both modules have already finished loading, which is the same
    reasoning `api/candidates.py:47` already relies on to import
    `_validate_image` from `render.pipeline` directly.
    """
    from autoposter.render.pipeline import SourceRefused, _validate_image

    try:
        _validate_image(path, f"overlay {name!r}")
    except SourceRefused as exc:
        raise OverlaySourceError(str(exc)) from None


async def resolve_image_path(
    definition: OverlayDefinition,
    *,
    overlays_root: Path,
    http,
    max_bytes: int,
) -> Path | None:
    """This overlay's image on disk, or None when it has no image.

    Returning None is not a failure: a text overlay carrying no addon has no
    image, and the caller draws text alone.
    """
    name = definition.name

    if definition.file:
        path = _confined(overlays_root, definition.file, name)
        if not path.exists():
            raise OverlaySourceError(f"overlay {name!r}: the configured file does not exist")
        await asyncio.to_thread(_validate_overlay_image, path, name)
        return path

    if definition.builtin:
        # Probe section 1.2: a leading `overlays/images/` prefix is stripped
        # and `.png` appended when missing.
        value = definition.builtin.removeprefix("overlays/images/")
        if not value.endswith(".png"):
            value += ".png"
        path = _confined(IMAGES, value, name)
        if not path.exists():
            raise OverlaySourceError(
                f"overlay {name!r}: no bundled overlay image by that name"
            )
        await asyncio.to_thread(_validate_overlay_image, path, name)
        return path

    if definition.url:
        return await _download(definition, overlays_root, http, max_bytes)

    # Probe section 1.2 step 6: the operator's own folder, keyed by the
    # overlay's own name.
    fallback = _confined(overlays_root, f"{name}.png", name)
    if not fallback.exists():
        return None
    await asyncio.to_thread(_validate_overlay_image, fallback, name)
    return fallback


async def _download(
    definition: OverlayDefinition, overlays_root: Path, http, max_bytes: int
) -> Path:
    """Fetch through the SSRF guard, cached by URL.

    Content-addressed rather than TTL'd (the plan's adjudication A7): the key
    IS the URL, so a changed URL is a new file and an unchanged one is never
    re-fetched. An operator who replaces the image behind a stable URL clears
    `<overlays_root>/.cache/`.

    Downloaded to a uniquely-named temp file under `.cache/` and moved into
    place with `os.replace` only once it has passed both the guard and the
    decode below -- `os.replace` is atomic, so a concurrent worker resolving
    the same URL (`workers: 5`) can never observe a partially written file at
    the permanent cache path, which `destination.exists()` above treats as a
    permanent hit. `except BaseException` mirrors
    `render/pipeline.py::_download`'s own precedent for the same download
    shape: a mid-stream `ConnectError`, a timeout or the task being
    cancelled must not leave the temp file behind either.
    """
    cache = overlays_root / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / (
        hashlib.sha256(definition.url.encode("utf-8")).hexdigest() + ".png"
    )
    if destination.exists():
        return destination
    tmp = cache / f"{destination.name}.tmp-{uuid.uuid4().hex}"
    try:
        try:
            await guarded_download(
                http, definition.url, tmp,
                max_bytes=max_bytes, content_types=OVERLAY_CONTENT_TYPES,
            )
        except FetchRefused as exc:
            raise OverlaySourceError(
                f"overlay {definition.name!r}: image download refused ({exc})"
            ) from exc
        except httpx.HTTPError as exc:
            # A transport failure -- a dead host, a timeout, a reset
            # mid-stream -- is not `FetchRefused` (that class is guard.py's
            # own deliberate refusals) and would otherwise escape this
            # function as a bare httpx exception. `apply_badges`'s
            # per-definition loop only catches `OverlaySourceError`, so an
            # untranslated transport error would fail the whole badge stage
            # for every item, over one definition's flaky CDN. Translated
            # here, in the module that already holds the "never name the
            # URL/host" rule this message follows.
            raise OverlaySourceError(
                f"overlay {definition.name!r}: image download failed "
                f"({type(exc).__name__})"
            ) from exc
        # render/pipeline.py's own decode (`_validate_image`) is dispatched
        # the same way, off the event loop: a full pixel decode is blocking
        # CPU work, and this coroutine has awaited callers (apply_badges's
        # per-definition loop) that must not stall on it.
        await asyncio.to_thread(_validate_overlay_image, tmp, definition.name)
        os.replace(tmp, destination)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return destination
