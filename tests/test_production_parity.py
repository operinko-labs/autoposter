"""Command-level parity against ImageMagick commands Posterizarr really ran.

Every expected value below was copied verbatim from
``/config/Logs/ImageMagickCommands.log`` (and its rotated archives) on the
production Posterizarr deployment this service replaces. They are not
reconstructions.

This complements ``test_golden.py``: that test proves the finished pixels
match, but it needs an ImageMagick binary. These assertions are pure string
comparisons, so they run everywhere and pin the exact argument sequence — which
is what catches a structural regression (an operand reordered, a flag dropped)
with a readable diff rather than an opaque pixel difference.

Two production quirks are deliberately reproduced or deliberately not:

- The offset carries its own sign and is concatenated after ``+0``, so a
  negative ``text_offset`` becomes ``-geometry +0-<n>``. Both signs appear in
  the real logs.
- Posterizarr's *logo* branch emits a malformed doubled sign (``+0++300``)
  caused by a string-concatenation bug, while its *text* branches emit the
  correct ``+0+300``. We emit the correct form in both cases. This was verified
  to be safe: compositing the same image with ``+0+50`` and ``+0++50`` under
  ImageMagick 7 produces a pixel difference of ``AE 0``, so ImageMagick ignores
  the extra sign and the output is identical.

One value is a deliberate divergence rather than a reproduction: the captured
log's title-card ``text_offset`` was ``-150``. That value pursued the same
hide-off-canvas idiom the example config still uses (see
``config/autoposter.example.yaml``), but was never pixel-verified and leaves
tall glyphs visible at some title lengths. The example config now defaults to
``-400``, so ``test_episode_title_text_matches_production`` below pins that
value instead of the log's ``-150``.
"""

from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render import compositor
from autoposter.render.textfit import build_fit_argv, prepare_text

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

# Paths as they appeared in the production log, kept verbatim so the comparison
# is against the real command rather than a tidied version of it.
TC_IMAGE = "/config/temp/81607_The Ark_S03E04.jpg"
POSTER_IMAGE = "/config/temp/164727_All Souls (2023) _tmdb-940143_.jpg"
FONT = "/config/temp/Comfortaa-Medium.ttf"
TC_OVERLAY = "/config/temp/bottom-up-fade-background.png"
POSTER_OVERLAY = "/config/temp/overlay.png"
LOGO = "/config/temp/All Souls (2023) {tmdb-940143}_logo.png"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def test_provenance_stamp_matches_production_plus_the_resolution_bound(config):
    """The captured command, plus one deliberate addition.

    Posterizarr's own stamp is ``magick <img> -set comment <c> <img>`` and
    nothing else, which is what this pinned until the production OOM
    investigation: the stamp is the first
    magick call on a freshly downloaded source and decoded it at whatever
    resolution the provider served, at 16 bytes per pixel under Q16-HDRI,
    across five concurrent workers.

    ``-resize 3840x3000>`` is therefore a knowing divergence rather than a
    drift. The ``>`` makes it a no-op for every source already inside the box
    -- which is every real poster and backdrop, and the golden fixture -- so
    ``tests/test_golden.py``'s byte-exact parity against the production asset
    still holds. Only a source larger than any canvas is touched at all.
    """
    assert compositor.build_stamp_argv(config.magick_binary, TC_IMAGE) == [
        "magick", TC_IMAGE,
        "-resize", "3840x3000>",
        "-set", "comment", "created with posterizarr",
        TC_IMAGE,
    ]


def test_title_card_base_matches_production(config):
    settings = config.artwork.title_card
    assert compositor.build_base_argv(
        config.magick_binary, TC_IMAGE, compositor.BACKGROUND_SIZE, TC_OVERLAY,
        config.artwork.output_quality, settings.add_border,
        settings.border_color, settings.border_width,
    ) == [
        "magick", TC_IMAGE,
        "-resize", "3840x2160^", "-gravity", "center", "-extent", "3840x2160",
        TC_OVERLAY, "-gravity", "south", "-quality", "92%", "-composite",
        TC_IMAGE,
    ]


def test_poster_base_matches_production(config):
    settings = config.artwork.poster
    assert compositor.build_base_argv(
        config.magick_binary, POSTER_IMAGE, compositor.POSTER_SIZE, POSTER_OVERLAY,
        config.artwork.output_quality, settings.add_border,
        settings.border_color, settings.border_width,
    ) == [
        "magick", POSTER_IMAGE,
        "-resize", "2000x3000^", "-gravity", "center", "-extent", "2000x3000",
        POSTER_OVERLAY, "-gravity", "south", "-quality", "92%", "-composite",
        POSTER_IMAGE,
    ]


def test_point_size_probe_matches_production(config):
    style = config.artwork.title_card.text
    assert build_fit_argv(
        config.magick_binary, FONT, style, "IN PLAIN SIGHT"
    ) == [
        "magick",
        "-size", "2500x300",
        "-font", FONT,
        "-gravity", "center",
        "-fill", "black",
        "-interline-spacing", "0",
        "caption:IN PLAIN SIGHT",
        "-format", "%[caption:pointsize]",
        "info:",
    ]


def test_episode_title_text_matches_production(config):
    """Pins ``-400``, not the captured log's ``-150`` — see the module docstring."""
    style = config.artwork.title_card.text
    assert compositor.build_text_argv(
        config.magick_binary, TC_IMAGE, style, FONT, 140, "IN PLAIN SIGHT",
        config.artwork.output_quality,
    ) == [
        "magick", TC_IMAGE,
        "-gravity", "center", "-background", "None", "-layers", "Flatten",
        "(", "-font", FONT, "-pointsize", "140", "-fill", "white",
        "-size", "2500x300", "-background", "none",
        "-interline-spacing", "0", "-gravity", "south",
        "caption:IN PLAIN SIGHT", "-trim", "+repage", "-extent", "2500x300", ")",
        "-gravity", "south", "-geometry", "+0-400",
        "-quality", "92%", "-composite", TC_IMAGE,
    ]


def test_episode_numbering_text_matches_production(config):
    style = config.artwork.title_card.episode_text
    assert compositor.build_text_argv(
        config.magick_binary, TC_IMAGE, style, FONT, 80, "SEASON 3 • EPISODE 4",
        config.artwork.output_quality,
    ) == [
        "magick", TC_IMAGE,
        "-gravity", "center", "-background", "None", "-layers", "Flatten",
        "(", "-font", FONT, "-pointsize", "80", "-fill", "white",
        "-size", "2500x150", "-background", "none",
        "-interline-spacing", "0", "-gravity", "south",
        "caption:SEASON 3 • EPISODE 4",
        "-trim", "+repage", "-extent", "2500x150", ")",
        "-gravity", "south", "-geometry", "+0+100",
        "-quality", "92%", "-composite", TC_IMAGE,
    ]


def test_logo_composite_matches_production_apart_from_the_doubled_sign(config):
    """Production emits ``+0++300`` here; we emit ``+0+300``.

    Everything else must be identical. The two geometries were verified
    equivalent under ImageMagick 7 (pixel difference ``AE 0``).
    """
    style = config.artwork.poster.text
    assert compositor.build_logo_argv(
        config.magick_binary, POSTER_IMAGE, LOGO, style, config.artwork.output_quality
    ) == [
        "magick", POSTER_IMAGE,
        "(", "-background", "none", LOGO, "-resize", "1200x485", ")",
        "-gravity", "south", "-geometry", "+0+300",
        "-quality", "92%", "-composite", POSTER_IMAGE,
    ]


def test_episode_numbering_uses_the_production_bullet_and_unpadded_numbers(config):
    """The production log contains ``SEASON 3 • EPISODE 4``.

    The bullet is U+2022 (UTF-8 ``e2 80 a2``, confirmed from the log bytes) and
    the numbers are unpadded — unlike the zero-padded ``S03E04.jpg`` filename.
    """
    settings = config.artwork.title_card
    line = (
        f"{settings.season_label} 3 • {settings.episode_label} 4"
    )
    assert prepare_text(line, settings.episode_text) == "SEASON 3 • EPISODE 4"
    assert "•".encode("utf-8") == b"\xe2\x80\xa2"
