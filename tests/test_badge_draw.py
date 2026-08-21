"""Pillow drawing primitives.

Assertions are on pixels rather than on calls, because the failure mode that
matters is "the badge is drawn in the wrong place", which mocking cannot catch.
"""
from PIL import Image, ImageFont

from autoposter.badges.draw import (
    composite,
    draw_backdrop,
    draw_text_centered,
    new_layer,
    paste_centered,
)
from autoposter.badges.spec import BADGES, INTER_MEDIUM, POSTER_CANVAS


def test_new_layer_is_a_fully_transparent_canvas():
    layer = new_layer(POSTER_CANVAS)
    assert layer.size == POSTER_CANVAS
    assert layer.mode == "RGBA"
    assert layer.getpixel((500, 750)) == (0, 0, 0, 0)


def test_draw_backdrop_fills_its_box_and_nothing_else():
    layer = new_layer(POSTER_CANVAS)
    box = draw_backdrop(layer, BADGES["resolution"], POSTER_CANVAS)
    assert box == (15, 15, 320, 120)
    # Solidly inside the box.
    assert layer.getpixel((160, 67))[3] == 153
    # Outside it, untouched.
    assert layer.getpixel((500, 500))[3] == 0


def test_backdrop_corners_are_rounded():
    """A square corner would mean radius was ignored."""
    layer = new_layer(POSTER_CANVAS)
    draw_backdrop(layer, BADGES["resolution"], POSTER_CANVAS)
    assert layer.getpixel((16, 16))[3] == 0


def test_paste_centered_does_not_resize_a_large_image():
    layer = new_layer(POSTER_CANVAS)
    tall = Image.new("RGBA", (200, 135), (255, 0, 0, 255))
    paste_centered(layer, tall, (15, 15, 320, 120))
    # 135 tall centred on a 105-tall box overflows by 15px each way.
    assert layer.getpixel((167, 10))[3] == 255
    assert layer.getpixel((167, 124))[3] == 255


def test_paste_centered_centres_a_small_image():
    layer = new_layer(POSTER_CANVAS)
    small = Image.new("RGBA", (100, 20), (0, 255, 0, 255))
    paste_centered(layer, small, (15, 15, 320, 120))
    cx, cy = (15 + 320) // 2, (15 + 120) // 2
    assert layer.getpixel((cx, cy)) == (0, 255, 0, 255)
    assert layer.getpixel((cx - 60, cy))[3] == 0


def test_draw_text_centered_marks_pixels_inside_the_box():
    layer = new_layer(POSTER_CANVAS)
    font = ImageFont.truetype(str(INTER_MEDIUM), 55)
    draw_text_centered(layer, "WEB", font, (15, 1365, 320, 1470))
    region = layer.crop((15, 1365, 320, 1470))
    assert region.getbbox() is not None, "nothing was drawn inside the box"
    outside = layer.crop((400, 1365, 700, 1470))
    assert outside.getbbox() is None, "pixels were drawn outside the box"


def test_composite_uses_the_layer_alpha_as_a_mask():
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    layer = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    layer.putpixel((5, 5), (255, 0, 0, 255))
    composite(base, layer)
    assert base.getpixel((5, 5)) == (255, 0, 0)
    assert base.getpixel((0, 0)) == (255, 255, 255)
