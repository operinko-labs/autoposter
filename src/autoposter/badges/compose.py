"""Base artwork plus badge inputs, out to encoded WebP bytes.

Mirrors Kometa's own sequence: open, convert to RGB, LANCZOS-resize to the
badge canvas, paste one full-canvas RGBA layer per badge, save as WebP at
quality 90 with the overlay EXIF marker.
"""
import hashlib
import io
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFont

from autoposter.badges.draw import composite, draw_text_centered, new_layer
from autoposter.badges.spec import ASSETS, IMAGES, canvas_for
from autoposter.overlays.builtin import BUILTIN_OVERLAYS
from autoposter.overlays.render import draw_overlay
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


def badge_fingerprint(
    base_fingerprint: str, art_kind: str, values: dict[str, str], asset_manifest_sha: str
) -> str:
    """Hash everything that affects the badged image.

    ``base_fingerprint`` is the *render* fingerprint -- the hash covering
    everything that shapes the composited base, title text and fonts included --
    not the sha of the downloaded source bytes. A re-render that leaves the
    source unchanged (an episode title arriving to replace "TBA", say) still
    produces a different base, and the badged upload has to follow it.

    Deliberately separate from the base ``fingerprint``: a rating changing must
    re-badge and re-upload without re-fetching or re-compositing the base.
    """
    parts = [base_fingerprint, art_kind, asset_manifest_sha]
    parts += ["%s=%s" % (k, values[k]) for k in sorted(values)]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _load(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def compose(
    base_path: Path, art_kind: str, inputs: BadgeInputs, fingerprint: str | None = None
) -> bytes:
    """Badge the base artwork and return encoded WebP bytes.

    ``fingerprint``, when given, is stamped alongside the overlay marker as
    the image's provenance -- see ``autoposter.plex.exif`` -- so a later read
    of what Plex is serving can tell whether it is still ours and current.
    """
    canvas = canvas_for(art_kind)
    poster = Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS)

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


__all__ = ["manifest_sha", "BadgeInputs", "badge_fingerprint", "badge_values", "compose"]
