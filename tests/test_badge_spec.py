"""The badge definition table.

These assertions look tautological -- they restate the table. That is the
point: these numbers are the parity contract, and a silent edit to one of them
would otherwise only surface as a subtly misplaced badge in a rendered image.
"""
import pytest

from autoposter.badges.geometry import backdrop_box
from autoposter.badges.spec import (
    BACK_COLOR,
    BADGES,
    EPISODE_CANVAS,
    POSTER_CANVAS,
    canvas_for,
)


def test_every_expected_badge_is_defined():
    assert set(BADGES) == {
        "resolution", "audio_codec", "critic", "audience", "commonsense",
        "video_format", "runtimes", "episode_info", "languages",
    }


def test_canvas_sizes():
    assert POSTER_CANVAS == (1000, 1500)
    assert EPISODE_CANVAS == (1920, 1080)


@pytest.mark.parametrize(
    "art_kind,expected",
    [("poster", (1000, 1500)), ("season_poster", (1000, 1500)),
     ("title_card", (1920, 1080)), ("background", (1920, 1080))],
)
def test_canvas_for(art_kind, expected):
    assert canvas_for(art_kind) == expected


def test_backdrop_is_sixty_percent_black():
    assert BACK_COLOR == (0, 0, 0, 153)


@pytest.mark.parametrize(
    "name,canvas,expected",
    [
        ("resolution", POSTER_CANVAS, (15, 15, 320, 120)),
        ("commonsense", POSTER_CANVAS, (15, 1125, 320, 1230)),
        ("video_format", POSTER_CANVAS, (15, 1365, 320, 1470)),
        ("runtimes", POSTER_CANVAS, (385, 1365, 985, 1470)),
        ("critic", POSTER_CANVAS, (795, 550, 985, 740)),
        ("audience", POSTER_CANVAS, (795, 760, 985, 950)),
        ("audio_codec", EPISODE_CANVAS, (808, 15, 1113, 120)),
        ("episode_info", EPISODE_CANVAS, (1600, 825, 1905, 930)),
        ("runtimes", EPISODE_CANVAS, (1305, 945, 1905, 1050)),
    ],
)
def test_specs_produce_the_measured_boxes(name, canvas, expected):
    spec = BADGES[name]
    assert backdrop_box(canvas, spec.box, spec.h_align, spec.h_offset,
                        spec.v_align, spec.v_offset, spec.padding) == expected


def test_commonsense_and_episode_info_keep_their_surprising_offsets():
    """Both come from a YAML branch that fires unexpectedly; both were
    confirmed against pixels. A "tidy-up" to 30 would be a regression."""
    assert BADGES["commonsense"].v_offset == 270
    assert BADGES["episode_info"].v_offset == 150


def test_languages_has_no_backdrop():
    """Every other badge draws one; neither oracle image shows one here."""
    assert BADGES["languages"].has_back is False
    assert all(BADGES[n].has_back for n in BADGES if n != "languages")
