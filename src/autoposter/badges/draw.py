"""Pillow primitives for badge rendering.

Each badge becomes a full-canvas RGBA layer that is pasted onto the poster
using its own alpha as the mask -- the same call Kometa makes. Using
``Image.alpha_composite`` instead would give different results where layers
overlap, so it is deliberately not used here.
"""
from PIL import Image, ImageDraw, ImageFont

from autoposter.badges.geometry import backdrop_box
from autoposter.badges.spec import BACK_COLOR, FONT_COLOR, BadgeSpec


def new_layer(canvas: tuple[int, int]) -> Image.Image:
    """A transparent full-canvas layer to draw one badge onto."""
    return Image.new("RGBA", canvas, (0, 0, 0, 0))


def draw_backdrop(
    layer: Image.Image, spec: BadgeSpec, canvas: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Draw the badge's rounded backdrop and return its box.

    The box is returned even when ``has_back`` is false, because callers need
    it to place content regardless of whether anything was drawn behind it.
    """
    box = backdrop_box(
        canvas, spec.box, spec.h_align, spec.h_offset,
        spec.v_align, spec.v_offset, spec.padding,
    )
    if spec.has_back:
        ImageDraw.Draw(layer).rounded_rectangle(box, radius=spec.radius, fill=BACK_COLOR)
    return box


def paste_centered(layer: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Centre an image on a box at its native size.

    Never resizes. Several badge images are taller than their backdrop -- two
    audio codec images are 135px against a 105px box -- and Kometa lets them
    overflow rather than shrinking them.
    """
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    layer.paste(image, (cx - image.width // 2, cy - image.height // 2), image)


def draw_text_centered(
    layer: Image.Image,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int, int] = FONT_COLOR,
) -> None:
    """Draw text centred on a box, anchored top-left like Kometa does."""
    drawing = ImageDraw.Draw(layer)
    left, top, right, bottom = drawing.textbbox((0, 0), text, font=font)
    x = (box[0] + box[2]) // 2 - (right - left) // 2 - left
    y = (box[1] + box[3]) // 2 - (bottom - top) // 2 - top
    drawing.text((x, y), text, font=font, fill=color, anchor="lt")


def composite(base: Image.Image, layer: Image.Image) -> None:
    """Paste a badge layer onto the base using the layer's alpha as the mask."""
    base.paste(layer, (0, 0), layer)
