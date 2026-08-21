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

from PIL import Image, ImageDraw, ImageFont

from autoposter.badges.draw import (
    composite,
    draw_backdrop,
    draw_text_centered,
    new_layer,
    paste_centered,
)
from autoposter.badges.spec import ASSETS, BADGES, IMAGES, canvas_for
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

WEBP_QUALITY = 90
EXIF_OVERLAY_TAG = 0x04BC
# Kometa's ``addon_offset``: the gap between a badge's logo and its text.
ADDON_OFFSET = 15

@lru_cache(maxsize=1)
def manifest_sha() -> str:
    """Hash of the badge asset manifest, so replacing any badge file
    invalidates every fingerprint. Lazy for the same reason as the language
    table: an import-time read turns a missing asset into a startup crash."""
    return hashlib.sha256((ASSETS / "MANIFEST.sha256").read_bytes()).hexdigest()

# Badges whose value is an image stem rather than a string to draw.
# ``compact`` is Kometa's audio_codec file default and what production uses --
# see docs/research/kometa-overlays.md section 3.2. The ``standard`` variants
# are taller two-line renders that our config never selects.
IMAGE_BADGES = {
    "resolution": IMAGES / "resolution",
    "audio_codec": IMAGES / "audio_codec" / "compact",
}
# Badges that pair a logo above or beside their text.
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


def _text_size(
    layer: Image.Image, text: str, font: ImageFont.FreeTypeFont
) -> tuple[int, int]:
    """The ink extent of ``text``, measured with the anchor it is drawn with.

    Kometa lays a badge's logo and text out as one group and centres the group
    in the backdrop box, so the text's own size is needed before either is
    placed.
    """
    left, top, right, bottom = ImageDraw.Draw(layer).textbbox(
        (0, 0), text, font=font, anchor="lt"
    )
    return right - left, bottom - top


def compose(base_path: Path, art_kind: str, inputs: BadgeInputs) -> bytes:
    """Badge the base artwork and return encoded WebP bytes."""
    canvas = canvas_for(art_kind)
    poster = Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS)

    values = badge_values(art_kind, inputs)
    for name, value in values.items():
        if name == "languages":
            continue
        spec = BADGES[name]
        layer = new_layer(canvas)
        box = draw_backdrop(layer, spec, canvas)

        if name in IMAGE_BADGES:
            image_path = IMAGE_BADGES[name] / ("%s.png" % value)
            if not image_path.exists():
                continue
            paste_centered(layer, _load(image_path), box)
        else:
            icon_path = BADGE_ICONS.get(name)
            font = ImageFont.truetype(str(spec.font), spec.font_size)
            if icon_path is not None and icon_path.exists():
                icon = _load(icon_path)
                text_width, text_height = _text_size(layer, value, font)
                if name == "commonsense":
                    # Icon to the left of the text, 15px gap.
                    total = icon.width + ADDON_OFFSET + text_width
                    start = box[0] + (box[2] - box[0] - total) // 2
                    icon_box = (start, box[1], start + icon.width, box[3])
                    text_box = (icon_box[2] + ADDON_OFFSET, box[1], start + total, box[3])
                else:
                    # Rating logos sit above the number, 15px gap.
                    total = icon.height + ADDON_OFFSET + text_height
                    start = box[1] + (box[3] - box[1] - total) // 2
                    icon_box = (box[0], start, box[2], start + icon.height)
                    text_box = (box[0], icon_box[3] + ADDON_OFFSET, box[2], start + total)
                paste_centered(layer, icon, icon_box)
                draw_text_centered(layer, value, font, text_box)
            else:
                draw_text_centered(layer, value, font, box)
        composite(poster, layer)

    _draw_languages(poster, canvas, inputs)

    exif = Image.Exif()
    exif[EXIF_OVERLAY_TAG] = "overlay"
    buffer = io.BytesIO()
    poster.save(buffer, format="WEBP", quality=WEBP_QUALITY, exif=exif)
    return buffer.getvalue()


def _draw_languages(poster: Image.Image, canvas: tuple[int, int], inputs: BadgeInputs) -> None:
    """Flag plus label per audio language, stacked 61px apart.

    Applied after every other badge because Kometa runs queue-based overlays
    last. No backdrop -- see the note in spec.py.
    """
    spec = BADGES["languages"]
    font = ImageFont.truetype(str(spec.font), spec.font_size)
    for index, (country, label) in enumerate(language_slots(inputs.media)):
        flag_path = IMAGES / "flag" / "round" / ("%s.png" % country)
        if not flag_path.exists():
            continue
        flag = _load(flag_path)
        top = spec.v_offset + index * 61
        layer = new_layer(canvas)
        layer.paste(flag, (spec.h_offset, top), flag)
        draw_text_centered(
            layer, label, font,
            (spec.h_offset + flag.width + 14, top, spec.h_offset + flag.width + 90, top + flag.height),
        )
        composite(poster, layer)


__all__ = ["manifest_sha", "BadgeInputs", "badge_fingerprint", "badge_values", "compose"]
