"""Kometa's coordinate formula, checked against measured production output.

Every expected value here was measured from a real overlaid image in
tests/fixtures/oracle/, not derived from the same formula under test.
"""
import pytest

from autoposter.badges.geometry import backdrop_box, get_cord

POSTER = (1000, 1500)
EPISODE = (1920, 1080)


@pytest.mark.parametrize(
    "value,image_value,over_value,align,expected",
    [
        (15, 1000, 305, "left", 15),
        (15, 1500, 105, "top", 15),
        (15, 1920, 600, "right", 1305),
        (30, 1500, 105, "bottom", 1365),
        (0, 1000, 305, "center", 348),
        (0, 1920, 305, "center", 808),
        (-105, 1500, 190, "center", 550),
        (105, 1500, 190, "center", 760),
    ],
)
def test_get_cord_matches_kometa(value, image_value, over_value, align, expected):
    assert get_cord(value, image_value, over_value, align) == expected


def test_percentage_offsets_resolve_against_the_canvas():
    assert get_cord("10%", 1000, 305, "left") == 100


@pytest.mark.parametrize(
    "canvas,box,h_align,h_off,v_align,v_off,padding,expected",
    [
        (POSTER, (305, 105), "left", 15, "top", 15, 0, (15, 15, 320, 120)),
        (POSTER, (305, 105), "left", 15, "bottom", 270, 0, (15, 1125, 320, 1230)),
        (POSTER, (305, 105), "left", 15, "bottom", 30, 0, (15, 1365, 320, 1470)),
        (POSTER, (600, 105), "right", 15, "bottom", 30, 0, (385, 1365, 985, 1470)),
        (POSTER, (160, 160), "right", 30, "center", -105, 15, (795, 550, 985, 740)),
        (POSTER, (160, 160), "right", 30, "center", 105, 15, (795, 760, 985, 950)),
        (EPISODE, (305, 105), "left", 15, "top", 15, 0, (15, 15, 320, 120)),
        (EPISODE, (305, 105), "center", 0, "top", 15, 0, (808, 15, 1113, 120)),
        (EPISODE, (305, 105), "right", 15, "bottom", 150, 0, (1600, 825, 1905, 930)),
        (EPISODE, (305, 105), "left", 15, "bottom", 30, 0, (15, 945, 320, 1050)),
        (EPISODE, (600, 105), "right", 15, "bottom", 30, 0, (1305, 945, 1905, 1050)),
    ],
)
def test_backdrop_box_matches_measured_production_output(
    canvas, box, h_align, h_off, v_align, v_off, padding, expected
):
    assert backdrop_box(canvas, box, h_align, h_off, v_align, v_off, padding) == expected


def test_padding_expands_the_box_on_every_side():
    unpadded = backdrop_box(POSTER, (160, 160), "right", 30, "center", -105, 0)
    padded = backdrop_box(POSTER, (160, 160), "right", 30, "center", -105, 15)
    assert padded == (unpadded[0] - 15, unpadded[1] - 15, unpadded[2] + 15, unpadded[3] + 15)
