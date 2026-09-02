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
