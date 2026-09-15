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

This asserts **byte-identical** output, not merely similar output: the
sequence reproduces the real asset's 843,045-byte JPEG identically in every
segment but one. The JPEG comment is the exception, on purpose: Posterizarr
stamps "created with posterizarr" and greps its own assets for it, so this
service stamps its own name instead and the comparison below looks past the
comment segment alone. Every pixel and every other byte still has to match.

That requires a **Q16-HDRI** ImageMagick build, which is what the runtime image
ships and what production runs. HDRI changes internal pixel maths, so a Q16
build without it renders the same source 0.077% differently (``RMSE 0.00077``)
— visually indistinguishable, but not bit-equal. On a machine without such a
build these therefore skip rather than fail; **in CI they fail**, because a
skip there leaves the central claim unproven while reporting green (see the
``imagemagick`` fixture in ``tests/conftest.py``). The exact patch version does
not matter (verified equal across 7.1.2-27 and 7.1.2-29).

If this fails on an HDRI build, the compositing pipeline has drifted from what
the existing library was built with.
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

# "hdri" is what makes the `imagemagick` fixture in conftest.py insist on a
# Q16-HDRI build rather than any `magick`. That fixture also decides what an
# unsuitable one means: a skip locally, a failure in CI. The marker selects
# these tests too -- the main CI run deselects them with `-m "not imagemagick"`
# and a later step runs them somewhere a Q16-HDRI build exists, because this
# file is the one that proves the byte-identical claim and it had never once
# executed in CI.
pytestmark = pytest.mark.imagemagick("hdri")


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


def _without_jpeg_comment(data: bytes) -> bytes:
    """``data`` with every JPEG COM segment (marker 0xFFFE) removed.

    Walks the marker segments before the scan data; the entropy-coded stream
    after SOS is left untouched. Anything that is not a well-formed marker
    walk is returned as it is, so a malformed file fails the comparison
    rather than passing it by accident.
    """
    if data[:2] != bytes([0xFF, 0xD8]):
        return data
    out = bytearray(data[:2])
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan: the rest is the coded stream
            break
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            out += data[i:i + 2]
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        segment = data[i:i + 2 + length]
        if marker != 0xFE:
            out += segment
        i += 2 + length
    out += data[i:]
    return bytes(out)


def _identify(path: Path, fmt: str) -> str:
    result = subprocess.run(
        ["magick", "identify", "-format", fmt, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_poster_matches_the_production_asset(tmp_path):
    """Our pipeline reproduces a real production poster byte-for-byte."""
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

    difference = _rmse(working, expected)
    assert difference == 0.0, (
        f"rendered poster differs from the production asset (RMSE {difference}) — "
        "the compositing pipeline has drifted from what the existing library "
        "was built with"
    )
    assert _without_jpeg_comment(working.read_bytes()) == _without_jpeg_comment(
        expected.read_bytes()
    ), "pixels match but the encoded bytes differ — check the ImageMagick build"
    assert _identify(working, "%wx%h") == _identify(expected, "%wx%h")
    # The production asset is Posterizarr's; ours carries this service's name.
    assert _identify(expected, "%[comment]") == "created with posterizarr"
    assert _identify(working, "%[comment]") == "created with autoposter"


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
