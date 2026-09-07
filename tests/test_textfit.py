import sys
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.config.schema import TextStyle
from autoposter.render.textfit import (
    MAX_MAGICK_STDERR_CHARS,
    FitResult,
    build_fit_argv,
    escape_caption_text,
    fit_point_size,
    prepare_text,
    _run,
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


def test_percent_signs_are_doubled_so_they_render_literally():
    assert escape_caption_text("100% Wolf") == "100%% Wolf"


def test_a_leading_at_sign_is_backslash_escaped():
    assert escape_caption_text("@Home") == "\\@Home"


def test_an_at_sign_not_in_leading_position_is_untouched():
    assert escape_caption_text("Look @Home") == "Look @Home"


def test_text_without_special_characters_is_unchanged():
    assert escape_caption_text("DUNE") == "DUNE"


def test_fit_argv_escapes_percent_signs_in_the_caption(style):
    argv = build_fit_argv("magick", "/fonts/Comfortaa-Medium.ttf", style, "100% Wolf")
    assert "caption:100%% Wolf" in argv


def test_fit_argv_escapes_a_leading_at_sign_in_the_caption(style):
    argv = build_fit_argv("magick", "/fonts/Comfortaa-Medium.ttf", style, "@Home")
    assert "caption:\\@Home" in argv


def test_run_attaches_stderr_and_the_command_on_failure(monkeypatch):
    def fake_run(argv, **kwargs):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "magick: unable to read font `/f.ttf' @ error/annotate.c/1234."

        return Result()

    monkeypatch.setattr("autoposter.render.textfit.subprocess.run", fake_run)
    from autoposter.render.textfit import _run

    with pytest.raises(RuntimeError) as excinfo:
        _run(["magick", "-font", "/f.ttf", "info:"])
    assert "unable to read font" in str(excinfo.value)
    assert "/f.ttf" in str(excinfo.value)
    assert "1" in str(excinfo.value)


def test_the_fit_probe_keeps_its_stdout_and_caps_its_stderr():
    """The twin of ``compositor.run``, and the reason surface 2's bound is not
    one line copied twice: ``_run`` READS stdout (``textfit.py:151`` parses the
    point size out of it), so its pipe stays. The stderr that reaches the
    RuntimeError is capped, guarding the pod log line and the exception
    message -- never ``jobs.last_error``: a bare ``RuntimeError`` has no
    ``served_detail``, so ``worker.py``'s ``_served_reason`` writes only the
    exception's class name to that column.
    """
    ok = "import sys; sys.stdout.write('  120  \\n')"
    assert _run([sys.executable, "-c", ok]) == "120"

    flood = "import sys; sys.stderr.write('e' * 50_000); sys.exit(1)"
    with pytest.raises(RuntimeError, match="magick failed") as excinfo:
        _run([sys.executable, "-c", flood])

    message = str(excinfo.value)
    assert message.endswith("...(truncated)")
    assert len(message) < MAX_MAGICK_STDERR_CHARS + 500
