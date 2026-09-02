"""Where an overlay's image comes from.

Kometa's precedence is banked in `.superpowers/sdd/p-overlay-grammar-probe.md`
section 1.2: `file` > `default`/`pmm` > `git` > `repo` > `url` > a name-keyed
fallback. Two of those rungs do not exist for this service and are not
invented -- there is no Kometa Configs-repo mirror (`git`) and no custom-repo
setting (`repo`). The plan's ladder table states each rung and why.

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
from pathlib import Path

from autoposter.net.guard import FetchRefused, guarded_download
from autoposter.overlays.assets import IMAGES
from autoposter.overlays.schema import OverlayDefinition

# Probe section 1.2: Kometa validates `Content-Type == "image/png"` on every
# downloaded overlay image and rejects anything else. Carried verbatim into
# net/guard.py's allowlist parameter.
OVERLAY_CONTENT_TYPES = frozenset({"image/png"})

# render/pipeline.py::_validate_image's own ceiling (#131), duplicated rather
# than imported: pipeline.py imports this module (Task 3 Step 12), so
# importing back would be a cycle. See `_validate_downloaded_image` for why
# the check itself is duplicated too.
_MAX_PIXELS = 64_000_000


class OverlaySourceError(Exception):
    """This overlay's image could not be resolved.

    Carries the overlay's name and the reason, never a URL or a host -- the
    same rule net/guard.py holds itself to, for the same reason: this message
    reaches a log line an operator reads.
    """


def _confined(root: Path, value: str, name: str) -> Path:
    """A path beneath `root`, or a refusal."""
    candidate = (root / value).resolve()
    root = root.resolve()
    if root not in candidate.parents and candidate != root:
        raise OverlaySourceError(
            f"overlay {name!r}: the configured path leaves its root directory"
        )
    return candidate


def _validate_downloaded_image(path: Path, name: str) -> None:
    """Full-decode a downloaded overlay image before it is trusted.

    render/pipeline.py::_validate_image added this same protection (#131) for
    provider artwork after job 40478 -- a PNG whose header is valid and whose
    IDAT stream contradicts it, which only a full pixel decode catches; a
    Content-Type check (net/guard.py's allowlist, above) does not. The plan
    predates #131 and its ladder steps did not carry this over; it is wired
    in here because a corrupt overlay image never reaches magick at all --
    badges/compose.py's `_load` hands it straight to Pillow -- so the same
    hole #131 closed for provider artwork is open here until this runs.

    Lazy PIL import for the same reason `overlays/schema.py::_as_rgba` gives:
    this module is on `render/pipeline.py`'s import path, which already pulls
    Pillow at module scope, but paying for the import only when a url source
    is actually downloaded keeps that a property of this function rather than
    an assumption a future caller could break.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            width, height = image.size
            pixels = width * height
            if pixels > _MAX_PIXELS:
                raise OverlaySourceError(
                    f"overlay {name!r}: downloaded image is {width}x{height} "
                    f"({pixels}px), over the {_MAX_PIXELS}px ceiling"
                )
            image.load()
    except (
        UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError
    ) as exc:
        raise OverlaySourceError(
            f"overlay {name!r}: downloaded image did not decode ({type(exc).__name__})"
        ) from None


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
        return path

    if definition.url:
        return await _download(definition, overlays_root, http, max_bytes)

    # Probe section 1.2 step 6: the operator's own folder, keyed by the
    # overlay's own name.
    fallback = _confined(overlays_root, f"{name}.png", name)
    return fallback if fallback.exists() else None


async def _download(
    definition: OverlayDefinition, overlays_root: Path, http, max_bytes: int
) -> Path:
    """Fetch through the SSRF guard, cached by URL.

    Content-addressed rather than TTL'd (the plan's adjudication A7): the key
    IS the URL, so a changed URL is a new file and an unchanged one is never
    re-fetched. An operator who replaces the image behind a stable URL clears
    `<overlays_root>/.cache/`.
    """
    cache = overlays_root / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / (
        hashlib.sha256(definition.url.encode("utf-8")).hexdigest() + ".png"
    )
    if destination.exists():
        return destination
    try:
        await guarded_download(
            http, definition.url, destination,
            max_bytes=max_bytes, content_types=OVERLAY_CONTENT_TYPES,
        )
    except FetchRefused as exc:
        destination.unlink(missing_ok=True)
        raise OverlaySourceError(
            f"overlay {definition.name!r}: image download refused ({exc})"
        ) from exc
    try:
        # render/pipeline.py's own decode (`_validate_image`) is dispatched
        # the same way, off the event loop: a full pixel decode is blocking
        # CPU work, and this coroutine has awaited callers (apply_badges's
        # per-definition loop) that must not stall on it.
        await asyncio.to_thread(_validate_downloaded_image, destination, definition.name)
    except OverlaySourceError:
        destination.unlink(missing_ok=True)
        raise
    return destination
