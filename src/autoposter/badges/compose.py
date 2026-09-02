"""Base artwork plus badge inputs, out to encoded WebP bytes.

Mirrors Kometa's own sequence: open, convert to RGB, LANCZOS-resize to the
badge canvas, paste one full-canvas RGBA layer per badge, save as WebP at
quality 90 with the overlay EXIF marker.
"""
import hashlib
import io
import json
import logging
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageFont

from autoposter.badges.draw import composite, draw_text_centered, new_layer
from autoposter.badges.spec import ASSETS, IMAGES, canvas_for
from autoposter.overlays.builtin import BUILTIN_OVERLAYS
from autoposter.overlays.render import draw_overlay
from autoposter.overlays.schema import OverlayDefinition
from autoposter.overlays.sources import OverlaySourceError, resolve_font_path
from autoposter.overlays.variables import UnresolvedVariable, literal_of, render_text
from autoposter.badges.values import (
    MediaInfo,
    audience_text,
    audio_codec_image,
    commonsense_text,
    critic_text,
    episode_text,
    language_slots,
    resolution_image,
    runtime_text,
)
from autoposter.plex.exif import PROVENANCE_TAG, format_provenance

logger = logging.getLogger(__name__)

WEBP_QUALITY = 90
EXIF_OVERLAY_TAG = 0x04BC

@lru_cache(maxsize=1)
def manifest_sha() -> str:
    """Hash of the badge asset manifest, so replacing any badge file
    invalidates every fingerprint. Lazy for the same reason as the language
    table: an import-time read turns a missing asset into a startup crash."""
    return hashlib.sha256((ASSETS / "MANIFEST.sha256").read_bytes()).hexdigest()

# Where each image-valued badge's art lives, keyed by badge. The value is a
# directory: the badge's own value names the file inside it. Kept here rather
# than on the definition because the definition names ONE image and these
# badges pick one of many at render time from the item's own media -- which is
# a value question (badges/values.py), not a layout one.
IMAGE_BADGE_DIRS = {
    "resolution": IMAGES / "resolution",
    "audio_codec": IMAGES / "audio_codec" / "compact",
}
# Badges that pair a fixed logo with their text. The definition carries the
# POSITION and the GAP (addon_position / addon_offset); this carries the file.
BADGE_ICONS = {
    "critic": IMAGES / "rating" / "IMDb.png",
    "audience": IMAGES / "rating" / "TMDb.png",
    "commonsense": IMAGES / "Commonsense.png",
}


@dataclass(frozen=True)
class BadgeInputs:
    """Everything the badge layer needs, gathered from Plex and ItemFacts."""

    media: MediaInfo
    critic_rating: float | None = None
    audience_rating: float | None = None
    content_rating: str | None = None
    video_format: str | None = None


def badge_values(art_kind: str, inputs: BadgeInputs) -> dict[str, str]:
    """The badges that will actually be drawn, and what each will show.

    Absent values are omitted rather than mapped to a placeholder -- an item
    with no critic rating gets no critic badge, which is what Kometa does and
    what the episode oracle shows.
    """
    result: dict[str, str] = {}
    candidates = {
        "resolution": resolution_image(inputs.media),
        "audio_codec": audio_codec_image(inputs.media),
        "critic": critic_text(inputs.critic_rating),
        "audience": audience_text(inputs.audience_rating),
        "commonsense": commonsense_text(inputs.content_rating),
        "video_format": inputs.video_format,
        "runtimes": runtime_text(inputs.media.duration_ms),
    }
    if art_kind == "title_card":
        candidates["episode_info"] = episode_text(
            inputs.media.season_number, inputs.media.episode_number
        )
    for name, value in candidates.items():
        if value:
            result[name] = value

    slots = language_slots(inputs.media)
    if slots:
        result["languages"] = ",".join("%s:%s" % pair for pair in slots)
    return result


def _definitions_digest(definitions: list[OverlayDefinition]) -> str:
    """A stable digest of the definitions list's own content.

    ``model_dump(mode="json")`` plus ``sort_keys=True`` rather than
    ``repr()``: a definition's field order is a class-declaration detail, not
    part of what an operator configured, so the digest must not depend on it.
    The list's own ORDER is kept significant, though, and definitions are
    joined in the order given rather than sorted -- unlike each definition's
    fields, list order changes which member of a group wins ties (draw
    order), which is operator-visible.
    """
    parts = [json.dumps(d.model_dump(mode="json"), sort_keys=True) for d in definitions]
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def badge_fingerprint(
    base_fingerprint: str,
    art_kind: str,
    values: dict[str, str],
    asset_manifest_sha: str,
    definitions: list[OverlayDefinition] | None = None,
) -> str:
    """Hash everything that affects the badged image.

    ``base_fingerprint`` is the *render* fingerprint -- the hash covering
    everything that shapes the composited base, title text and fonts included --
    not the sha of the downloaded source bytes. A re-render that leaves the
    source unchanged (an episode title arriving to replace "TBA", say) still
    produces a different base, and the badged upload has to follow it.

    Deliberately separate from the base ``fingerprint``: a rating changing must
    re-badge and re-upload without re-fetching or re-compositing the base.

    ``definitions`` folds the operator's own overlays (roadmap row 97) into
    the gate: an operator who adds, edits or removes one must re-badge every
    already-uploaded item, or the change never reaches Plex. Hashed only when
    the list is non-empty -- an empty or absent list leaves ``parts``
    completely untouched, so the gate-off fingerprint for every item that
    configures no overlays stays byte-identical to what this function
    produced before ``definitions`` existed. This repo already paid for the
    alternative once (the mass-ops additive-keys re-fingerprint that
    invalidated ~18k rows in one run); the fix is the same one: don't
    perturb the hash for the case that has nothing to say.
    """
    parts = [base_fingerprint, art_kind, asset_manifest_sha]
    parts += ["%s=%s" % (k, values[k]) for k in sorted(values)]
    if definitions:
        parts.append(_definitions_digest(definitions))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _load(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def compose(
    base_path: Path,
    art_kind: str,
    inputs: BadgeInputs,
    fingerprint: str | None = None,
    definitions: list[OverlayDefinition] | None = None,
    resolved_images: dict[str, Path] | None = None,
    fonts_root: Path | None = None,
) -> bytes:
    """Badge the base artwork and return encoded WebP bytes.

    ``fingerprint``, when given, is stamped alongside the overlay marker as
    the image's provenance -- see ``autoposter.plex.exif`` -- so a later read
    of what Plex is serving can tell whether it is still ours and current.

    ``definitions`` are operator-defined overlays (roadmap row 97), drawn
    after every built-in badge and after the language stack. ``None`` and
    ``[]`` are the same thing and are byte-identical to the pre-row-97
    output.

    ``resolved_images`` maps a definition's name to an image already fetched
    for it. Resolution is async and this function is not: the caller does the
    I/O (``overlays.sources.resolve_image_path``) and hands the results in,
    which also keeps ``compose`` a pure function of its arguments.

    ``fonts_root`` is the root a definition's ``font:`` value is confined
    beneath (H1) -- the same discipline ``overlays.sources``'s ``file:``
    image source uses. A definition naming a font that fails to resolve
    (leaves ``fonts_root``, or does not exist) is skipped with a warning,
    not fatal to the rest of the item's badges.

    ``definitions`` may also include one or more ``blur(NN)`` names (roadmap
    row 50): the MAXIMUM NN across every one that survives suppression and
    group resolution is applied ONCE, to the whole base canvas, before any
    badge or overlay -- built-in or operator-defined -- is composited on
    top.
    """
    canvas = canvas_for(art_kind)
    poster = Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS)

    resolved_definitions = _resolve_definitions(definitions or [])
    blur = _blur_amount(resolved_definitions)
    if blur > 0:
        # Roadmap row 50: one GaussianBlur on the whole base canvas, before
        # any badge or overlay is composited -- Kometa's own per-item
        # pre-pass semantics (recon p-overlay-b-recon.md), not a
        # per-definition draw call. Badges therefore stay sharp on a
        # blurred background.
        poster = poster.filter(ImageFilter.GaussianBlur(blur))

    values = badge_values(art_kind, inputs)
    for name, value in values.items():
        if name == "languages":
            continue
        definition = BUILTIN_OVERLAYS[name]
        layer = new_layer(canvas)

        if name in IMAGE_BADGE_DIRS:
            image_path = IMAGE_BADGE_DIRS[name] / ("%s.png" % value)
            if not image_path.exists():
                continue
            draw_overlay(layer, definition, canvas, image=_load(image_path))
        else:
            font = ImageFont.truetype(definition.font, definition.font_size)
            icon_path = BADGE_ICONS.get(name)
            icon = _load(icon_path) if icon_path is not None and icon_path.exists() else None
            draw_overlay(layer, definition, canvas, image=icon, text=value, font=font)
        composite(poster, layer)

    _draw_languages(poster, canvas, inputs)

    _draw_definitions(poster, canvas, inputs, definitions or [], resolved_images or {}, fonts_root)

    exif = Image.Exif()
    exif[EXIF_OVERLAY_TAG] = "overlay"
    if fingerprint is not None:
        exif[PROVENANCE_TAG] = format_provenance(fingerprint)
    buffer = io.BytesIO()
    poster.save(buffer, format="WEBP", quality=WEBP_QUALITY, exif=exif)
    return buffer.getvalue()


def _draw_languages(poster: Image.Image, canvas: tuple[int, int], inputs: BadgeInputs) -> None:
    """Flag plus label per audio language, stacked 61px apart.

    Applied after every other badge because Kometa runs queue-based overlays
    last. No backdrop -- see the note in spec.py.
    """
    definition = BUILTIN_OVERLAYS["languages"]
    font = ImageFont.truetype(definition.font, definition.font_size)
    for index, (country, label) in enumerate(language_slots(inputs.media)):
        flag_path = IMAGES / "flag" / "round" / ("%s.png" % country)
        if not flag_path.exists():
            continue
        flag = _load(flag_path)
        top = definition.vertical_offset + index * 61
        layer = new_layer(canvas)
        layer.paste(flag, (definition.horizontal_offset, top), flag)
        draw_text_centered(
            layer, label, font,
            (definition.horizontal_offset + flag.width + 14, top,
             definition.horizontal_offset + flag.width + 90, top + flag.height),
        )
        composite(poster, layer)


def _variable_values(art_kind: str, inputs: BadgeInputs) -> dict[str, object]:
    """The item values an operator's <<variable>> tokens can resolve against.

    Deliberately only what this service already gathers. The data-source
    probe's 29 external rating sources are part of the GRAMMAR and are absent
    here on purpose: fetching them is row 100's data half, and a definition
    naming one is skipped rather than silently rendered wrong.
    """
    media = inputs.media
    values: dict[str, object] = {
        "content_rating": inputs.content_rating,
        "critic_rating": inputs.critic_rating,
        "audience_rating": inputs.audience_rating,
        "season_number": media.season_number,
        "episode_number": media.episode_number,
    }
    if media.duration_ms:
        values["runtime"] = media.duration_ms // 60000
        values["total_runtime"] = values["runtime"]
    return {k: v for k, v in values.items() if v is not None}


def _resolve_definitions(
    definitions: list[OverlayDefinition],
) -> list[OverlayDefinition]:
    """Apply suppression, then group weight. Probe section 4.1's order.

    Suppression runs FIRST: if A names B in suppress_overlays and both match,
    B is dropped outright and group weight never arbitrates that pair.
    """
    suppressed = {n for d in definitions for n in d.suppress_overlays}
    surviving = [d for d in definitions if d.name not in suppressed]

    winners: dict[str, OverlayDefinition] = {}
    result: list[OverlayDefinition] = []
    for definition in surviving:
        if not definition.group:
            result.append(definition)
            continue
        current = winners.get(definition.group)
        if current is None or definition.weight > current.weight:
            winners[definition.group] = definition
    return result + list(winners.values())


def _blur_amount(definitions: list[OverlayDefinition]) -> int:
    """The per-item blur pre-pass amount: the MAXIMUM NN across every
    blur(NN) definition in the already-resolved (suppressed, grouped) list --
    not sum, not last-wins, not first-wins. 0 means "no blur configured",
    the same case this phase must not perturb for any existing config
    (roadmap row 50)."""
    return max((d.blur_amount for d in definitions if d.blur_amount is not None), default=0)


def _draw_definitions(
    poster: Image.Image,
    canvas: tuple[int, int],
    inputs: BadgeInputs,
    definitions: list[OverlayDefinition],
    resolved_images: dict[str, Path],
    fonts_root: Path | None,
) -> None:
    """Draw the operator's own overlays, after every built-in one."""
    if not definitions:
        return
    values = _variable_values("", inputs)
    for definition in _resolve_definitions(definitions):
        if definition.blur_amount is not None:
            # The blur pre-pass already ran in compose(), before any badge
            # was drawn. This definition carries no image, no text and no
            # backdrop colour of its own -- routing it through draw_overlay
            # would be a harmless no-op call that composites an empty
            # transparent layer, correct by accident rather than by design.
            continue
        literal = literal_of(definition.name)
        text = None
        if literal is not None:
            try:
                text = render_text(literal, values)
            except UnresolvedVariable as exc:
                # Probe section 2.4: a per-item skip with a warning, never a
                # run abort.
                logger.warning(
                    "overlay %r skipped: no value for <<%s>>", definition.name, exc
                )
                continue
        image_path = resolved_images.get(definition.name)
        image = _load(image_path) if image_path is not None else None

        font_path = None
        if text is not None and definition.font:
            # H1: `font:` is confined beneath `fonts_root`, the same
            # discipline `overlays.sources`'s `file:` image source uses, and
            # a resolution failure skips just THIS definition rather than
            # escaping compose() for `pipeline.py`'s blanket per-item handler.
            if fonts_root is None:
                logger.warning(
                    "overlay %r has no usable font (no fonts_root configured); skipping it",
                    definition.name,
                )
                continue
            try:
                font_path = resolve_font_path(fonts_root, definition.font, definition.name)
            except OverlaySourceError as exc:
                logger.warning(
                    "overlay %r has no usable font (%s); skipping it",
                    definition.name, type(exc).__name__,
                )
                continue

        font = (
            ImageFont.truetype(str(font_path), definition.font_size)
            if font_path is not None
            else ImageFont.load_default(definition.font_size)
            if text is not None
            else None
        )
        layer = new_layer(canvas)
        draw_overlay(layer, definition, canvas, image=image, text=text, font=font)
        composite(poster, layer)


__all__ = ["manifest_sha", "BadgeInputs", "badge_fingerprint", "badge_values", "compose"]
