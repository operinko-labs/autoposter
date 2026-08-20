import shutil
import subprocess
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render.compositor import (
    POSTER_SIZE, build_base_argv, build_logo_argv, build_stamp_argv, build_text_argv, run,
)
from autoposter.render.textfit import fit_point_size, prepare_text

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

# Fixtures an operator must harvest into tests/fixtures/golden/ before these
# tests will run (absent the directory or ImageMagick, they skip cleanly):
#   source_textless.jpg        a textless source poster, as a provider would serve it
#   overlay.png                 the poster fade overlay named by artwork.poster.overlay_file
#   Comfortaa-Medium.ttf         the font named by artwork.poster.text.font
#   clearlogo.png                a clearlogo for the same title
#   expected_poster_with_logo.jpg
#       the finished asset from the production /assets tree for a title rendered
#       under the production default (use_logo: true, logo_text_fallback: false):
#       art + overlay + clearlogo, no title text
#   expected_poster_with_text.jpg
#       the finished asset from the production /assets tree for a title rendered
#       under logo_text_fallback: true with no logo available on any provider:
#       art + overlay + title text, no clearlogo
#
# The first test below is the one that matters for parity: it is the only path
# production actually takes with the example config. The second test documents
# the still-reachable logo_text_fallback: true configuration; that is not the
# production default and is kept only as a secondary check.

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


def test_poster_matches_the_posterizarr_reference_use_logo_true_no_fallback(tmp_path):
    """Production default: use_logo=true, logo_text_fallback=false -> no title text."""
    config = load_config(EXAMPLE)
    assert config.artwork.use_logo is True
    assert config.artwork.logo_text_fallback is False
    style = config.artwork.poster.text
    overlay = str(GOLDEN / "overlay.png")
    logo = str(GOLDEN / "clearlogo.png")

    working = tmp_path / "poster.jpg"
    shutil.copy(GOLDEN / "source_textless.jpg", working)

    run(build_stamp_argv(config.magick_binary, str(working)))
    run(build_base_argv(
        config.magick_binary, str(working), POSTER_SIZE, overlay,
        config.artwork.output_quality, False, "white", 30,
    ))
    run(build_logo_argv(
        config.magick_binary, str(working), logo, style, config.artwork.output_quality,
    ))

    assert _rmse(working, GOLDEN / "expected_poster_with_logo.jpg") < 0.02


def test_poster_matches_the_posterizarr_reference_logo_text_fallback_true(tmp_path):
    """logo_text_fallback=true with no logo available -> falls back to drawn title."""
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

    assert _rmse(working, GOLDEN / "expected_poster_with_text.jpg") < 0.02
