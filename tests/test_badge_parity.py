"""Parity against real production output.

Both oracle pairs were pulled from the live Plex server: a base that our own
pipeline produced, and the badged image the tool being replaced
uploaded for the same item. We cannot be byte-identical -- we encode our own
WebP from our own base -- so the assertion is that badging the base moves it
substantially *towards* the production output inside each badge region, and
leaves it alone everywhere else.
"""
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autoposter.badges.compose import BadgeInputs, compose
from autoposter.badges.geometry import backdrop_box
from autoposter.badges.spec import BADGES
from autoposter.badges.values import MediaInfo

ORACLE = Path("tests/fixtures/oracle")

# `after < before` alone is not load-bearing: the box comes from the same
# BadgeSpec the composer draws with, so a badge drawn at the wrong-but-still-
# overlapping position would still register as an improvement. The measured
# ratios are 0.00 to 0.14, so this bound has generous headroom over correct
# output while a misplaced badge cannot clear it.
MAX_RESIDUAL = 0.25


def _as_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.int16)


def _region_error(a: np.ndarray, b: np.ndarray, box) -> float:
    x0, y0, x1, y1 = box
    return float(np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean())


ALL_SOULS = BadgeInputs(
    media=MediaInfo(("1080",), ("English (EAC3 5.1)",), 6, 4845912, ("en",),
                   frozenset(), None, None),
    critic_rating=4.9, audience_rating=6.3, content_rating="17", video_format="WEB",
)


@pytest.mark.parametrize(
    "badge", ["resolution", "audio_codec", "critic", "audience", "commonsense",
              "video_format", "runtimes"],
)
def test_each_poster_badge_moves_towards_production_output(badge):
    canvas = (1000, 1500)
    base_path = ORACLE / "All_Souls_base_no_overlay.jpg"
    oracle = _as_array(Image.open(ORACLE / "All_Souls_plex_overlaid.jpg"))
    bare = _as_array(Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS))
    ours = _as_array(Image.open(io.BytesIO(compose(base_path, "poster", ALL_SOULS))))

    spec = BADGES[badge]
    box = backdrop_box(canvas, spec.box, spec.h_align, spec.h_offset,
                       spec.v_align, spec.v_offset, spec.padding)
    before = _region_error(bare, oracle, box)
    after = _region_error(ours, oracle, box)
    assert after < before * MAX_RESIDUAL, (
        "badge %r: residual error %.1f is not below %.0f%% of the bare %.1f"
        % (badge, after, MAX_RESIDUAL * 100, before)
    )


def test_non_badge_area_is_untouched():
    """The centre of the poster carries no badge; badging must not disturb it."""
    canvas = (1000, 1500)
    base_path = ORACLE / "All_Souls_base_no_overlay.jpg"
    bare = _as_array(Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS))
    ours = _as_array(Image.open(io.BytesIO(compose(base_path, "poster", ALL_SOULS))))
    assert _region_error(bare, ours, (350, 400, 700, 500)) < 6.0


# Read off the oracle output itself: it shows "480P SD", the AAC logo, "SDTV",
# "Runtime: 0h 23m" and a TMDB badge reading "100%".
EPISODE = BadgeInputs(
    media=MediaInfo(("480",), ("English (AAC Stereo)",), 2, 1380000, ("en",),
                   frozenset(), 1, 1),
    critic_rating=None, audience_rating=10.0, content_rating=None, video_format="SDTV",
)


@pytest.mark.parametrize("badge", ["resolution", "audio_codec", "episode_info", "runtimes"])
def test_each_episode_badge_moves_towards_production_output(badge):
    canvas = (1920, 1080)
    base_path = ORACLE / "8OO10C_S01E01_base_no_overlay.jpg"
    oracle = _as_array(Image.open(ORACLE / "8OO10C_S01E01_plex_overlaid.jpg"))
    bare = _as_array(Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS))
    ours = _as_array(Image.open(io.BytesIO(compose(base_path, "title_card", EPISODE))))

    spec = BADGES[badge]
    box = backdrop_box(canvas, spec.box, spec.h_align, spec.h_offset,
                       spec.v_align, spec.v_offset, spec.padding)
    before = _region_error(bare, oracle, box)
    after = _region_error(ours, oracle, box)
    assert after < before * MAX_RESIDUAL, (
        "badge %r: residual error %.1f is not below %.0f%% of the bare %.1f"
        % (badge, after, MAX_RESIDUAL * 100, before)
    )


def test_the_suppressed_critic_badge_region_stays_bare():
    """This episode has no IMDb rating, and production drew no critic badge
    there. Drawing one would be a parity failure that no other test catches."""
    canvas = (1920, 1080)
    base_path = ORACLE / "8OO10C_S01E01_base_no_overlay.jpg"
    bare = _as_array(Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS))
    ours = _as_array(Image.open(io.BytesIO(compose(base_path, "title_card", EPISODE))))
    spec = BADGES["critic"]
    box = backdrop_box(canvas, spec.box, spec.h_align, spec.h_offset,
                       spec.v_align, spec.v_offset, spec.padding)
    assert _region_error(bare, ours, box) < 6.0
