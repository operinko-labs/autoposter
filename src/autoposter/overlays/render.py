"""Draw one overlay definition onto one full-canvas RGBA layer.

This is `badges/compose.py`'s per-badge layout logic with the `if name ==`
branches replaced by attribute reads -- which is the whole point of row 97:
an operator's definition takes the same path the nine builtins do.

The Pillow primitives stay in `badges/draw.py` and the coordinate maths stays
in `badges/geometry.py`; both are pinned by tests this phase does not touch
and are on the never-modify list (Global Constraint 15's file table), so
this module IMPORTS `paste_centered` and `draw_text_centered` rather than
re-implementing them -- a second copy of either is exactly what would drift.
The one place this module adds real logic beyond those two primitives is the
stroke branch of `_draw_text_centered` below: `draw_text_centered` has no
stroke parameters, none of the nine builtins sets a stroke, and `draw.py`
cannot be extended to add them without violating the never-modify list.
"""
from PIL import Image, ImageDraw, ImageFont

from autoposter.badges.draw import draw_text_centered, paste_centered
from autoposter.badges.geometry import backdrop_box
from autoposter.overlays.schema import OverlayDefinition


def _content_size(
    layer: Image.Image,
    definition: OverlayDefinition,
    image: Image.Image | None,
    text: str | None,
    font: ImageFont.FreeTypeFont | None,
) -> tuple[int, int]:
    """The overlay's own box, for the `-1` sentinel and for `get_cord`.

    Probe section 3.5: an un-set back_width/back_height shrinks to fit the
    overlay's own content. (The other arm of that sentinel -- stretching to
    the canvas -- belongs to the `backdrop` name, handled in `draw_overlay`
    below, roadmap row 50.)
    """
    width = height = 0
    if image is not None:
        width, height = image.size
    if text is not None and font is not None:
        left, top, right, bottom = ImageDraw.Draw(layer).textbbox(
            (0, 0), text, font=font, anchor="lt"
        )
        text_width, text_height = right - left, bottom - top
        if image is None:
            width, height = text_width, text_height
        elif definition.addon_position in ("left", "right"):
            width += definition.addon_offset + text_width
            height = max(height, text_height)
        else:
            height += definition.addon_offset + text_height
            width = max(width, text_width)
    return width, height


def _scaled(definition: OverlayDefinition, image: Image.Image) -> Image.Image:
    """Probe section 1.1: scale_width/scale_height resize before the draw.

    Neither set means the image is used at its native size and may overflow
    its box -- `badges/draw.py`'s documented rule, and what two of the
    audio-codec images actually do.
    """
    if definition.scale_width is None and definition.scale_height is None:
        return image
    width = definition.scale_width or image.width
    height = definition.scale_height or image.height
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _aligned_content_box(
    definition: OverlayDefinition,
    start: tuple[int, int],
    back_size: tuple[int, int],
    content: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Where the content sits inside the backdrop box, per `back_align`.

    Probe section 3.4, transcribed literally including its asymmetry: only
    `left/right/center/bottom` recompute the vertical position, and only
    `top/bottom/center/right` recompute the horizontal one -- so `left`
    centres vertically but stays flush against the box's own left edge
    horizontally, and `top` mirrors that on the other axis. Not smoothed
    over; it is what Kometa's own source does.
    """
    start_x, start_y = start
    back_width, back_height = back_size
    content_width, content_height = content
    main_x, main_y = start_x, start_y
    align = definition.back_align
    if align in ("left", "right", "center", "bottom"):
        main_y = start_y + (back_height - content_height) // (1 if align == "bottom" else 2)
    if align in ("top", "bottom", "center", "right"):
        main_x = start_x + (back_width - content_width) // (1 if align == "right" else 2)
    return (main_x, main_y, main_x + content_width, main_y + content_height)


def draw_overlay(
    layer: Image.Image,
    definition: OverlayDefinition,
    canvas: tuple[int, int],
    *,
    image: Image.Image | None = None,
    text: str | None = None,
    font: ImageFont.FreeTypeFont | None = None,
) -> tuple[int, int, int, int]:
    """Draw one overlay and return its backdrop box as `(x0, y0, x1, y1)`.

    The box comes back whether or not a backdrop was drawn, because a caller
    that composites more onto the same layer needs it either way -- the same
    contract `badges/draw.py::draw_backdrop` already has.
    """
    if image is not None:
        image = _scaled(definition, image)

    content = _content_size(layer, definition, image, text, font)
    if definition.name == "backdrop":
        # Probe section 3.5's other arm: for the special "backdrop" name, an
        # unset back_width/back_height stretches to the FULL CANVAS rather
        # than shrinking to content (overlay.py:449-452) -- every other
        # overlay uses the content-sized arm below.
        box_width = definition.back_width if definition.back_width != -1 else canvas[0]
        box_height = definition.back_height if definition.back_height != -1 else canvas[1]
    else:
        box_width = definition.back_width if definition.back_width != -1 else content[0]
        box_height = definition.back_height if definition.back_height != -1 else content[1]

    box = backdrop_box(
        canvas, (box_width, box_height),
        definition.horizontal_align, definition.horizontal_offset or 0,
        definition.vertical_align, definition.vertical_offset or 0,
        definition.back_padding,
    )

    if definition.has_back:
        ImageDraw.Draw(layer).rounded_rectangle(
            box,
            radius=definition.back_radius,
            fill=definition.rgba("back_color"),
            outline=definition.rgba("back_line_color"),
            width=definition.back_line_width or 1,
        )

    # `back_align` is only legal, per the schema, when the operator also set
    # `back_width` -- and none of the nine builtins sets either. Gated on
    # `model_fields_set` rather than on the field's value (which defaults to
    # "center" either way) so the untouched case takes the EXACT pre-existing
    # path below, byte for byte: `paste_centered`/`draw_text_centered` centre
    # on the box's own midpoint, which integer division can put a pixel away
    # from the probe section 3.4 formula's result for an even back_width
    # paired with odd content -- a real divergence (see the fix-round
    # report), not a hypothetical one, and exactly what this gate exists to
    # keep off every builtin's path.
    content_box = box
    if "back_align" in definition.model_fields_set:
        start = (box[0] + definition.back_padding, box[1] + definition.back_padding)
        content_box = _aligned_content_box(definition, start, (box_width, box_height), content)

    if image is not None and text is not None and font is not None:
        _draw_addon_group(layer, definition, content_box, image, text, font, content)
    elif image is not None:
        paste_centered(layer, image, content_box)
    elif text is not None and font is not None:
        _draw_text_centered(layer, definition, text, font, content_box)
    return box


def _draw_addon_group(
    layer: Image.Image,
    definition: OverlayDefinition,
    box: tuple[int, int, int, int],
    image: Image.Image,
    text: str,
    font: ImageFont.FreeTypeFont,
    content: tuple[int, int],
) -> None:
    """Lay the image and the text out as one group and centre the group.

    Probe section 1.1: `addon_position` picks the side, `addon_offset` the
    gap. Kometa centres the GROUP in the backdrop box rather than centring
    each piece, which is why the text's own size is measured first.
    """
    if definition.addon_position in ("left", "right"):
        start = box[0] + (box[2] - box[0] - content[0]) // 2
        if definition.addon_position == "left":
            image_box = (start, box[1], start + image.width, box[3])
            text_box = (image_box[2] + definition.addon_offset, box[1],
                        start + content[0], box[3])
        else:
            text_box = (start, box[1], start + content[0] - image.width
                        - definition.addon_offset, box[3])
            image_box = (text_box[2] + definition.addon_offset, box[1],
                         start + content[0], box[3])
    else:
        start = box[1] + (box[3] - box[1] - content[1]) // 2
        if definition.addon_position == "top":
            image_box = (box[0], start, box[2], start + image.height)
            text_box = (box[0], image_box[3] + definition.addon_offset, box[2],
                        start + content[1])
        else:
            text_box = (box[0], start, box[2], start + content[1] - image.height
                        - definition.addon_offset)
            image_box = (box[0], text_box[3] + definition.addon_offset, box[2],
                         start + content[1])
    paste_centered(layer, image, image_box)
    _draw_text_centered(layer, definition, text, font, text_box)


def _draw_text_centered(
    layer: Image.Image,
    definition: OverlayDefinition,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: tuple[int, int, int, int],
) -> None:
    """Centre text on a box, delegating to `badges/draw.py::draw_text_centered`
    for placement and fill.

    That primitive already does the `lt`-anchored bbox math and accepts a
    `color`, so the common case -- no stroke, which is every one of the nine
    builtins -- is a direct call and reuses it byte-for-byte. `draw.py` has no
    stroke parameters and is never modified by this phase, so the stroke case
    is the one genuine extension: it reproduces the same bbox math because
    there is no way to bolt a stroke onto an already-drawn call. This branch
    is unreachable from any of the nine builtins, none of which sets a
    stroke; it exists for an operator-authored definition that does.
    """
    color = definition.rgba("font_color")
    if definition.stroke_width == 0 and definition.stroke_color is None:
        draw_text_centered(layer, text, font, box, color=color)
        return
    drawing = ImageDraw.Draw(layer)
    left, top, right, bottom = drawing.textbbox((0, 0), text, font=font, anchor="lt")
    x = (box[0] + box[2]) // 2 - (right - left) // 2 - left
    y = (box[1] + box[3]) // 2 - (bottom - top) // 2 - top
    drawing.text(
        (x, y), text, font=font, fill=color, anchor="lt",
        stroke_width=definition.stroke_width,
        stroke_fill=definition.rgba("stroke_color"),
    )
