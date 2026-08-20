"""Pixel-level parity against real Posterizarr output.

The fixtures in ``tests/fixtures/golden`` are not synthetic. They were taken
from the production deployment this service replaces:

- ``expected_poster.jpg`` — the finished poster Posterizarr wrote to
  ``/assets/Movies/All Souls (2023) {tmdb-940143}/poster.jpg``
- ``source_textless.jpg`` / ``logo.png`` — the exact TMDB artwork Posterizarr
  downloaded to build it, recorded in its ``ImageChoices.csv``
- ``overlay.png`` — the fade overlay from the production config
- ``Comfortaa-Medium.ttf`` — the production font

The production config sets ``use_logo: true`` with ``logo_text_fallback:
false``, so a poster is art + fade overlay + clearlogo, with **no title text**.
That is the path this test exercises, because it is the one the library
actually contains. Testing the text path here would prove nothing about the
18,008 assets already on disk.

Run with production's own ImageMagick build (7.1.2-29 **Q16-HDRI**), this
sequence reproduced the real asset at ``RMSE 0`` — a byte-identical
843,045-byte JPEG. That is the strongest available evidence that the pipeline
matches.

The tolerance below exists because ImageMagick builds are not bit-identical to
each other. The runtime container currently ships Debian's 7.1.1-43 **Q16**
(no HDRI); HDRI changes internal pixel math, and that build reproduces the same
asset at ``RMSE 0.00077`` — 0.077%, visually indistinguishable, but not
bit-equal. Pinning a Q16-HDRI build would restore exact equality.

This does not cause the existing library to be re-rendered: adoption compares
fingerprints, not pixels, so the 18,008 assets already on disk are adopted
untouched either way. The difference only affects newly rendered items.

If this test fails by a wide margin, the compositing pipeline has drifted from
what the existing library was built with.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render.compositor import (
    POSTER_SIZE,
    build_base_argv,
    build_logo_argv,
    build_stamp_argv,
    run,
)

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

pytestmark = pytest.mark.skipif(
    shutil.which("magick") is None or not (GOLDEN / "expected_poster.jpg").exists(),
    reason="requires ImageMagick and the harvested golden fixtures",
)


def _rmse(a: Path, b: Path) -> float:
    """Normalised root-mean-square difference between two images.

    ``magick compare`` writes ``1234.5 (0.0188)`` to stderr; the parenthesised
    value is the normalised one.
    """
    result = subprocess.run(
        ["magick", "compare", "-metric", "RMSE", str(a), str(b), "null:"],
        capture_output=True,
        text=True,
    )
    text = result.stderr.strip()
    if "(" not in text:
        raise AssertionError(f"could not parse compare output: {text!r}")
    return float(text.split("(")[1].split(")")[0])


def _identify(path: Path, fmt: str) -> str:
    result = subprocess.run(
        ["magick", "identify", "-format", fmt, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_poster_matches_the_production_asset(tmp_path):
    """Our pipeline reproduces a real production poster exactly."""
    config = load_config(EXAMPLE)
    settings = config.artwork.poster

    assert config.artwork.use_logo is True
    assert config.artwork.logo_text_fallback is False

    working = tmp_path / "poster.jpg"
    shutil.copy(GOLDEN / "source_textless.jpg", working)

    run(build_stamp_argv(config.magick_binary, str(working)))
    run(
        build_base_argv(
            config.magick_binary,
            str(working),
            POSTER_SIZE,
            str(GOLDEN / "overlay.png"),
            config.artwork.output_quality,
            settings.add_border,
            settings.border_color,
            settings.border_width,
        )
    )
    run(
        build_logo_argv(
            config.magick_binary,
            str(working),
            str(GOLDEN / "logo.png"),
            settings.text,
            config.artwork.output_quality,
        )
    )

    expected = GOLDEN / "expected_poster.jpg"

    # 0 on production's Q16-HDRI build; 0.00077 on Debian's Q16 build. The
    # threshold is far below any visible difference but far above the observed
    # cross-build noise, so a genuine pipeline regression still fails loudly.
    difference = _rmse(working, expected)
    assert difference < 0.005, (
        f"rendered poster differs from the production asset (RMSE {difference}) — "
        "the compositing pipeline has drifted from what the existing library "
        "was built with"
    )

    # Structural properties that must hold on any build.
    assert _identify(working, "%wx%h") == _identify(expected, "%wx%h")
    assert _identify(expected, "%[comment]") == "created with posterizarr"
    assert _identify(working, "%[comment]") == "created with posterizarr"


def test_rendered_poster_has_the_production_dimensions(tmp_path):
    """Guards the cover-fit step independently of the overlay and logo."""
    config = load_config(EXAMPLE)
    working = tmp_path / "poster.jpg"
    shutil.copy(GOLDEN / "source_textless.jpg", working)

    run(
        build_base_argv(
            config.magick_binary,
            str(working),
            POSTER_SIZE,
            None,
            config.artwork.output_quality,
            False,
            "white",
            30,
        )
    )
    result = subprocess.run(
        ["magick", "identify", "-format", "%wx%h", str(working)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == POSTER_SIZE
