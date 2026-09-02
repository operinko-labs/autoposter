"""The generalised per-definition draw.

`badges/compose.py` today decides an overlay's layout with `if name ==`
branches. This module's job is to make that decision from ATTRIBUTES, so an
operator's definition gets the same treatment the nine builtins do.

Assertions are on pixels, following tests/test_badge_draw.py: the failure
that matters is "drawn in the wrong place", which mocking cannot catch.
"""
from PIL import Image, ImageFont

from autoposter.overlays.assets import INTER_MEDIUM, POSTER_CANVAS
from autoposter.overlays.render import draw_overlay
from autoposter.overlays.schema import OverlayDefinition


def _layer():
    return Image.new("RGBA", POSTER_CANVAS, (0, 0, 0, 0))


def test_the_box_matches_the_coordinate_formula_with_padding():
    """Probe sections 3.3 and 1.1: back_padding expands the box on all four
    sides. Same numbers tests/test_badge_geometry.py measured for `critic`."""
    layer = _layer()
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="c", horizontal_offset=30, horizontal_align="right",
            vertical_offset=-105, vertical_align="center",
            back_width=160, back_height=160, back_padding=15,
            back_color="#00000099", back_radius=30,
        ),
        POSTER_CANVAS,
    )
    assert box == (795, 550, 985, 740)


def test_no_backdrop_is_drawn_when_no_colour_was_given():
    """Probe section 1.1: has_back is derived. The box still comes back --
    callers place content against it either way."""
    layer = _layer()
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="l", horizontal_offset=15, horizontal_align="left",
            vertical_offset=223, vertical_align="top",
            back_width=190, back_height=105,
        ),
        POSTER_CANVAS,
    )
    assert box == (15, 223, 205, 328)
    assert layer.getpixel((100, 260))[3] == 0


def test_a_backdrop_is_drawn_when_a_colour_was_given():
    layer = _layer()
    draw_overlay(
        layer,
        OverlayDefinition(
            name="r", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=305, back_height=105,
            back_color="#00000099", back_radius=30,
        ),
        POSTER_CANVAS,
    )
    assert layer.getpixel((160, 67))[3] == 153
    # Rounded, not square.
    assert layer.getpixel((16, 16))[3] == 0
    assert layer.getpixel((500, 500))[3] == 0


def test_an_addon_on_the_left_lays_image_then_gap_then_text():
    """Probe section 1.1: addon_position left, addon_offset the gap. This is
    the layout `commonsense` uses -- icon beside the text."""
    layer = _layer()
    icon = Image.new("RGBA", (60, 60), (255, 0, 0, 255))
    font = ImageFont.truetype(str(INTER_MEDIUM), 55)
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="text(x)", horizontal_offset=15, horizontal_align="left",
            vertical_offset=1125, vertical_align="top",
            back_width=305, back_height=105, back_color="#00000099",
            back_radius=30, addon_offset=15, addon_position="left",
        ),
        POSTER_CANVAS,
        image=icon, text="17+", font=font,
    )
    assert box == (15, 1125, 320, 1230)
    # The icon is left of the box centre, the ink right of it.
    assert layer.crop((15, 1125, 167, 1230)).getbbox() is not None
    assert layer.crop((175, 1125, 320, 1230)).getbbox() is not None


def test_an_addon_on_top_lays_image_above_the_text():
    """Probe section 1.1: addon_position top. This is the layout the two
    rating badges use -- logo above the number."""
    layer = _layer()
    icon = Image.new("RGBA", (60, 60), (0, 255, 0, 255))
    font = ImageFont.truetype(str(INTER_MEDIUM), 55)
    draw_overlay(
        layer,
        OverlayDefinition(
            name="text(y)", horizontal_offset=30, horizontal_align="right",
            vertical_offset=-105, vertical_align="center",
            back_width=160, back_height=160, back_padding=15,
            back_color="#00000099", back_radius=30,
            addon_offset=15, addon_position="top",
        ),
        POSTER_CANVAS,
        image=icon, text="4.9", font=font,
    )
    # Green icon pixels in the upper half of the padded box, none in the lower.
    # Matched as the exact icon colour, not just G==255 and A==255 -- white
    # text ink (255, 255, 255, 255) also satisfies that looser pair, and at
    # Inter-Medium 55pt "4.9"'s own ink genuinely reaches into the last few
    # rows before the box's bottom edge in this pinned environment.
    upper = layer.crop((795, 550, 985, 645))
    assert any(p == (0, 255, 0, 255) for p in upper.getdata())
    lower = layer.crop((795, 700, 985, 740))
    assert not any(p == (0, 255, 0, 255) for p in lower.getdata())


def test_an_image_with_no_text_is_centred_and_never_resized():
    """`badges/draw.py`'s rule, carried forward: two audio-codec images are
    135px against a 105px box and Kometa lets them overflow."""
    layer = _layer()
    tall = Image.new("RGBA", (200, 135), (0, 0, 255, 255))
    draw_overlay(
        layer,
        OverlayDefinition(
            name="a", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=305, back_height=105,
        ),
        POSTER_CANVAS,
        image=tall,
    )
    assert layer.getpixel((167, 10))[3] == 255
    assert layer.getpixel((167, 124))[3] == 255


def test_scale_width_and_height_resize_the_image_before_it_is_drawn():
    """Probe section 1.1, scale. None of the nine builtins uses it; an
    operator definition can."""
    layer = _layer()
    big = Image.new("RGBA", (400, 400), (255, 255, 0, 255))
    draw_overlay(
        layer,
        OverlayDefinition(
            name="s", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=305, back_height=105,
            scale_width=40, scale_height=40,
        ),
        POSTER_CANVAS,
        image=big,
    )
    # 40x40 centred on (15,15,320,120): x 147..187, y 47..87.
    assert layer.getpixel((167, 67))[3] == 255
    assert layer.getpixel((120, 67))[3] == 0


def _color_bbox(layer: Image.Image, box: tuple[int, int, int, int], color: tuple[int, int, int, int]):
    """The bounding box, in ``box``-local coordinates, of pixels matching
    ``color`` exactly -- lets a test locate a solid-colour icon or a glyph's
    fully-opaque interior without hand-computing font metrics."""
    region = layer.crop(box)
    mask = Image.new("L", region.size, 0)
    mask.putdata([255 if px == color else 0 for px in region.getdata()])
    return mask.getbbox()


def test_back_width_and_height_default_to_shrink_wrap_the_content():
    """Probe section 3.5: the `-1` sentinel (this schema's own default,
    `schema.py`) sizes the backdrop to the overlay's own content rather than
    to a fixed box -- the `backdrop`-name arm that stretches to the full
    canvas is refused, not built (the plan's deferral table). None of the
    nine builtins reaches this: every one states `back_width`/`back_height`
    explicitly."""
    layer = _layer()
    icon = Image.new("RGBA", (40, 20), (255, 0, 0, 255))
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="a", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
        ),
        POSTER_CANVAS,
        image=icon,
    )
    assert box == (15, 15, 55, 35)


def test_the_backdrop_name_stretches_the_minus_one_sentinel_to_the_full_canvas():
    """Probe section 3.5's other arm: for the special 'backdrop' name, an
    unset back_width/back_height stretches to the FULL CANVAS instead of
    shrinking to content -- the arm every other overlay name never reaches
    (roadmap row 50)."""
    layer = _layer()
    box = draw_overlay(
        layer,
        OverlayDefinition(name="backdrop", back_color="#00000099"),
        POSTER_CANVAS,
    )
    assert box == (0, 0, POSTER_CANVAS[0], POSTER_CANVAS[1])
    assert layer.getpixel((10, 10))[3] == 153


def test_the_backdrop_name_with_an_explicit_box_is_not_stretched():
    """The stretch arm only fires on the -1 sentinel; an explicit
    back_width/back_height on a 'backdrop'-named overlay is honoured exactly
    like any other overlay's box."""
    layer = _layer()
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="backdrop", back_color="#00000099",
            horizontal_offset=0, horizontal_align="left",
            vertical_offset=0, vertical_align="top",
            back_width=200, back_height=100,
        ),
        POSTER_CANVAS,
    )
    assert box == (0, 0, 200, 100)


def test_addon_position_right_and_bottom_mirror_left_and_top():
    """Probe section 1.1: `addon_position` right/bottom put the image AFTER
    the text instead of before it -- the two arms of `_draw_addon_group`
    that no builtin reaches (all nine use left or top). Located by isolating
    each element's exact colour rather than hand-computing font metrics, so
    the assertion doesn't depend on this environment's font rasterisation."""
    layer = _layer()
    icon = Image.new("RGBA", (60, 60), (255, 0, 0, 255))
    font = ImageFont.truetype(str(INTER_MEDIUM), 55)
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="text(x)", horizontal_offset=15, horizontal_align="left",
            vertical_offset=1125, vertical_align="top",
            back_width=305, back_height=105, back_color="#00000099",
            back_radius=30, addon_offset=15, addon_position="right",
        ),
        POSTER_CANVAS,
        image=icon, text="17+", font=font,
    )
    icon_box = _color_bbox(layer, box, (255, 0, 0, 255))
    text_box = _color_bbox(layer, box, (255, 255, 255, 255))
    assert icon_box is not None and text_box is not None
    assert icon_box[0] >= text_box[2], "the icon must sit to the right of the text"

    layer = _layer()
    icon = Image.new("RGBA", (60, 60), (0, 255, 0, 255))
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="text(y)", horizontal_offset=30, horizontal_align="right",
            vertical_offset=-105, vertical_align="center",
            back_width=160, back_height=160, back_padding=15,
            back_color="#00000099", back_radius=30,
            addon_offset=15, addon_position="bottom",
        ),
        POSTER_CANVAS,
        image=icon, text="4.9", font=font,
    )
    icon_box = _color_bbox(layer, box, (0, 255, 0, 255))
    text_box = _color_bbox(layer, box, (255, 255, 255, 255))
    assert icon_box is not None and text_box is not None
    assert icon_box[1] >= text_box[3], "the icon must sit below the text"


def test_a_stroke_draws_an_outline_around_the_text():
    """Probe section 1.1: `stroke_width`/`stroke_color`, reachable by any
    operator definition though none of the nine builtins sets either.
    `draw.py::draw_text_centered` has no stroke parameters and is never
    modified by this phase (module docstring), so this branch is
    `render.py`'s own extension -- unreachable from any builtin, but real."""
    layer = _layer()
    font = ImageFont.truetype(str(INTER_MEDIUM), 55)
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="text(x)", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=305, back_height=105,
            font_color="#FFFFFF", stroke_width=4, stroke_color="#FF0000",
        ),
        POSTER_CANVAS,
        text="A", font=font,
    )
    colors = {px for px in layer.crop(box).getdata() if px[3] > 0}
    assert (255, 0, 0, 255) in colors, "the stroke colour must appear"
    assert (255, 255, 255, 255) in colors, "the fill colour must still appear"


def test_back_align_left_shifts_content_flush_against_the_backs_left_edge():
    """Probe section 3.4, transcribed literally: `back_align: left` is in
    the vertical-recompute list but not the horizontal one, so it centres
    content vertically while leaving it flush against the backdrop's own
    left edge horizontally -- the asymmetry is in Kometa's own source, not
    smoothed over here. `back_align` is only legal (schema validator) when
    `back_width` is also given, so both are set explicitly below."""
    layer = _layer()
    icon = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="a", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=305, back_height=205, back_align="left",
        ),
        POSTER_CANVAS,
        image=icon,
    )
    icon_box = _color_bbox(layer, box, (255, 0, 0, 255))
    assert icon_box is not None
    assert icon_box[0] == 0, "flush against the box's own left edge"
    assert icon_box[1] == 82, "vertically centred: (205 - 40) // 2"


def test_back_align_is_never_read_when_the_operator_did_not_set_it():
    """None of the nine builtins sets `back_align` -- confirmed by
    `overlays/builtin.py`, which passes no such keyword to any of its nine
    `OverlayDefinition(...)` calls -- so parity requires `draw_overlay` to
    never apply the section 3.4 formula for them. It doesn't: the formula
    only runs when `back_align` is in `model_fields_set`, i.e. the operator
    actually wrote it, and the untouched path below is the exact
    `paste_centered`-based centring this file's other tests already pin.

    The numbers here are deliberately chosen where the two would disagree if
    the gate were ever removed -- an EVEN `back_width` (160, `critic`'s own
    value) against an ODD-width icon (41px): `paste_centered`'s
    centre-of-box math and the section 3.4 formula round differently for
    that parity combination (see the fix-round report), so this assertion
    would move by a pixel the day the gate stops being live, even though no
    existing pin would say a word."""
    layer = _layer()
    icon = Image.new("RGBA", (41, 41), (255, 0, 0, 255))
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="a", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=160, back_height=160,
        ),
        POSTER_CANVAS,
        image=icon,
    )
    icon_box = _color_bbox(layer, box, (255, 0, 0, 255))
    assert icon_box is not None
    # Absolute placement is (box centre 95) - (icon width 41 // 2 == 20) = 75;
    # local to the box (which starts at x=15) that is 60.
    assert icon_box[0] == 60
