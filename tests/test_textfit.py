from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.config.schema import TextStyle
from autoposter.render.textfit import (
    FitResult,
    build_fit_argv,
    fit_point_size,
    prepare_text,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def style() -> TextStyle:
    return load_config(EXAMPLE).artwork.poster.text


def test_fit_argv_asks_magick_for_the_caption_point_size(style):
    argv = build_fit_argv("magick", "/fonts/Comfortaa-Medium.ttf", style, "DUNE")
    assert argv[0] == "magick"
    assert "-size" in argv
    assert argv[argv.index("-size") + 1] == "1200x485"
    assert "caption:DUNE" in argv
    assert "%[caption:pointsize]" in argv
    assert argv[-1] == "info:"
    # -pointsize must be absent, otherwise magick will not auto-fit.
    assert "-pointsize" not in argv


def test_fit_argv_passes_font_and_interline_spacing(style):
    argv = build_fit_argv("magick", "/fonts/Comfortaa-Medium.ttf", style, "DUNE")
    assert argv[argv.index("-font") + 1] == "/fonts/Comfortaa-Medium.ttf"
    assert argv[argv.index("-interline-spacing") + 1] == "0"


def test_all_caps_is_applied():
    style = TextStyle(
        min_point_size=10, max_point_size=100, max_width=100, max_height=100,
        text_offset="+0", all_caps=True,
    )
    assert prepare_text("Dune: Part Two", style) == "DUNE: PART TWO"


def test_all_caps_can_be_disabled():
    style = TextStyle(
        min_point_size=10, max_point_size=100, max_width=100, max_height=100,
        text_offset="+0", all_caps=False,
    )
    assert prepare_text("Dune: Part Two", style) == "Dune: Part Two"


def test_smart_quotes_are_normalised(style):
    assert prepare_text("“Heat”", style) == "'HEAT'"
    assert prepare_text("‚Heat‘", style) == "'HEAT'"


def test_backticks_are_removed(style):
    assert prepare_text("He`at", style) == "HEAT"


def test_result_is_clamped_to_the_maximum(style, monkeypatch):
    monkeypatch.setattr("autoposter.render.textfit._run", lambda argv: "999")
    result = fit_point_size("magick", "/f.ttf", style, "A")
    assert result == FitResult(point_size=250, truncated=False)


def test_result_below_the_minimum_marks_truncated(style, monkeypatch):
    monkeypatch.setattr("autoposter.render.textfit._run", lambda argv: "40")
    result = fit_point_size("magick", "/f.ttf", style, "A VERY LONG TITLE INDEED")
    assert result.truncated is True
    assert result.point_size == 83


def test_result_inside_the_range_is_used_as_is(style, monkeypatch):
    monkeypatch.setattr("autoposter.render.textfit._run", lambda argv: "140")
    assert fit_point_size("magick", "/f.ttf", style, "A") == FitResult(140, False)


def test_unparseable_magick_output_raises(style, monkeypatch):
    monkeypatch.setattr("autoposter.render.textfit._run", lambda argv: "not a number")
    with pytest.raises(RuntimeError, match="point size"):
        fit_point_size("magick", "/f.ttf", style, "A")
