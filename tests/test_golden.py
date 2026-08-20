import shutil
import subprocess
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render.compositor import (
    POSTER_SIZE, build_base_argv, build_stamp_argv, build_text_argv, run,
)
from autoposter.render.textfit import fit_point_size, prepare_text

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

pytestmark = pytest.mark.skipif(
    shutil.which("magick") is None or not GOLDEN.exists(),
    reason="requires ImageMagick and harvested golden fixtures",
)


def _rmse(a: Path, b: Path) -> float:
    """Root-mean-square difference between two images, as reported by magick."""
    result = subprocess.run(
        ["magick", "compare", "-metric", "RMSE", str(a), str(b), "null:"],
        capture_output=True, text=True,
    )
    # magick writes "1234.56 (0.0188)" to stderr; the parenthesised value is normalised.
    text = result.stderr.strip()
    return float(text.split("(")[1].rstrip(")"))


def test_poster_matches_the_posterizarr_reference(tmp_path):
    config = load_config(EXAMPLE)
    style = config.artwork.poster.text
    font = str(GOLDEN / "Comfortaa-Medium.ttf")
    overlay = str(GOLDEN / "overlay.png")

    working = tmp_path / "poster.jpg"
    shutil.copy(GOLDEN / "source_textless.jpg", working)

    title = prepare_text("DUNE: PART TWO", style)
    fit = fit_point_size(config.magick_binary, font, style, title)
    assert fit.truncated is False

    run(build_stamp_argv(config.magick_binary, str(working)))
    run(build_base_argv(
        config.magick_binary, str(working), POSTER_SIZE, overlay,
        config.artwork.output_quality, False, "white", 30,
    ))
    run(build_text_argv(
        config.magick_binary, str(working), style, font,
        fit.point_size, title, config.artwork.output_quality,
    ))

    assert _rmse(working, GOLDEN / "expected_poster.jpg") < 0.02
