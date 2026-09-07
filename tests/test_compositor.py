import subprocess
import sys
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render.compositor import (
    BACKGROUND_SIZE,
    POSTER_SIZE,
    STAMP_MAX_GEOMETRY,
    build_base_argv,
    build_logo_argv,
    build_stamp_argv,
    build_text_argv,
    run,
)
from autoposter.render.textfit import MAX_MAGICK_STDERR_CHARS

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def test_canvas_sizes_are_fixed():
    assert POSTER_SIZE == "2000x3000"
    assert BACKGROUND_SIZE == "3840x2160"


def test_stamp_writes_the_posterizarr_comment_and_bounds_the_source():
    argv = build_stamp_argv("magick", "/tmp/x.jpg")
    assert argv == [
        "magick", "/tmp/x.jpg",
        "-resize", "3840x3000>",
        "-set", "comment", "created with posterizarr",
        "/tmp/x.jpg",
    ]


def test_the_stamp_bound_contains_every_canvas():
    """The bound is only safe because no canvas is outside it.

    A canvas taller or wider than ``STAMP_MAX_GEOMETRY`` would mean the stamp
    shrinking a source the very next step needs at full size, so this is
    asserted against the canvas constants rather than trusted to a comment.
    """
    limit_w, limit_h = (int(n) for n in STAMP_MAX_GEOMETRY.split("x"))
    for canvas in (POSTER_SIZE, BACKGROUND_SIZE):
        width, height = (int(n) for n in canvas.split("x"))
        assert width <= limit_w
        assert height <= limit_h


def test_the_stamp_bound_only_shrinks():
    """``>`` is what keeps a normal-sized source byte-identical."""
    argv = build_stamp_argv("magick", "/tmp/x.jpg")
    assert argv[argv.index("-resize") + 1].endswith(">")


def test_base_argv_cover_fits_then_composites_the_overlay():
    argv = build_base_argv(
        "magick", "/tmp/x.jpg", POSTER_SIZE, "/overlays/overlay.png", "92%",
        add_border=False, border_color="white", border_width=30,
    )
    assert argv[argv.index("-resize") + 1] == "2000x3000^"
    assert argv[argv.index("-extent") + 1] == "2000x3000"
    assert "/overlays/overlay.png" in argv
    assert "-composite" in argv
    assert argv.index("/overlays/overlay.png") < argv.index("-composite")
    assert argv[argv.index("-quality") + 1] == "92%"
    assert argv[0] == "magick"
    assert argv[-1] == "/tmp/x.jpg"


def test_base_argv_without_overlay_has_no_composite():
    argv = build_base_argv(
        "magick", "/tmp/x.jpg", POSTER_SIZE, None, "92%",
        add_border=False, border_color="white", border_width=30,
    )
    assert "-composite" not in argv
    assert "-resize" in argv


def test_border_shaves_then_borders_to_preserve_canvas_size():
    argv = build_base_argv(
        "magick", "/tmp/x.jpg", POSTER_SIZE, None, "92%",
        add_border=True, border_color="white", border_width=30,
    )
    assert argv[argv.index("-shave") + 1] == "30x30"
    assert argv[argv.index("-bordercolor") + 1] == "white"
    assert argv[argv.index("-border") + 1] == "30"
    assert argv.index("-shave") < argv.index("-border")


def test_text_argv_builds_a_parenthesised_caption_group(config):
    style = config.artwork.poster.text
    argv = build_text_argv(
        "magick", "/tmp/x.jpg", style, "/fonts/Comfortaa-Medium.ttf", 140, "DUNE", "92%"
    )
    assert "(" in argv and ")" in argv
    assert argv[argv.index("-pointsize") + 1] == "140"
    assert "caption:DUNE" in argv
    assert "-trim" in argv
    assert "+repage" in argv
    assert argv[argv.index("-fill") + 1] == "white"


def test_text_offset_is_concatenated_after_plus_zero(config):
    style = config.artwork.poster.text
    argv = build_text_argv(
        "magick", "/tmp/x.jpg", style, "/f.ttf", 140, "DUNE", "92%"
    )
    assert argv[argv.index("-geometry") + 1] == "+0+300"


def test_negative_text_offset_is_preserved(config):
    style = config.artwork.title_card.text  # text_offset is "-400"
    argv = build_text_argv("magick", "/tmp/x.jpg", style, "/f.ttf", 100, "EP", "92%")
    assert argv[argv.index("-geometry") + 1] == "+0-400"


def test_stroke_draws_two_captions_when_enabled(config):
    style = config.artwork.poster.text.model_copy(update={"add_stroke": True})
    argv = build_text_argv("magick", "/tmp/x.jpg", style, "/f.ttf", 140, "DUNE", "92%")
    assert argv.count("caption:DUNE") == 2
    assert argv[argv.index("-strokewidth") + 1] == "6"


def test_no_stroke_draws_one_caption(config):
    argv = build_text_argv(
        "magick", "/tmp/x.jpg", config.artwork.poster.text, "/f.ttf", 140, "DUNE", "92%"
    )
    assert argv.count("caption:DUNE") == 1
    assert "-strokewidth" not in argv


def test_logo_argv_uses_a_single_sign_geometry(config):
    argv = build_logo_argv(
        "magick", "/tmp/x.jpg", "/tmp/logo.png", config.artwork.poster.text, "92%"
    )
    # Posterizarr emits a malformed "+0++300" here; we deliberately do not.
    assert argv[argv.index("-geometry") + 1] == "+0+300"
    assert argv[argv.index("-resize") + 1] == "1200x485"
    assert "/tmp/logo.png" in argv


def test_text_argv_escapes_percent_signs_in_the_title(config):
    style = config.artwork.poster.text
    argv = build_text_argv(
        "magick", "/tmp/x.jpg", style, "/f.ttf", 140, "100% WOLF", "92%"
    )
    assert "caption:100%% WOLF" in argv
    assert "caption:100% WOLF" not in argv


def test_text_argv_escapes_a_leading_at_sign_in_the_title(config):
    style = config.artwork.poster.text
    argv = build_text_argv(
        "magick", "/tmp/x.jpg", style, "/f.ttf", 140, "@HOME", "92%"
    )
    assert "caption:\\@HOME" in argv
    assert "caption:@HOME" not in argv


def test_logo_argv_adds_density_for_svg(config):
    argv = build_logo_argv(
        "magick", "/tmp/x.jpg", "/tmp/logo.svg", config.artwork.poster.text, "92%"
    )
    assert argv[argv.index("-density") + 1] == "300"


def test_run_discards_stdout_and_caps_the_stderr_it_attaches(monkeypatch):
    """Roadmap row 238, surface 2. Two unbounded buffers, one bound.

    ``capture_output=True`` held BOTH of magick's streams in this process's
    memory. Nothing reads ``compositor.run``'s stdout -- it returns None and
    every call site discards it -- so that half is now written to
    ``DEVNULL``; the one magick call whose output IS read is
    ``textfit._run``, which keeps its pipe. stderr is still captured, because
    it is the failure message, and is capped on its way into the
    ``RuntimeError``: that message becomes ``jobs.last_error``, a database row
    whose size must not be a subprocess's choice. Same bound and same marker
    as ``intake/routes.py``'s ``_MAX_RAW_BODY_CHARS``.

    Half one is the kwargs, spied without spawning anything. Half two is a
    real flooding stub -- ``sys.executable`` rather than a magick binary, so
    this test needs no ImageMagick and carries no ``imagemagick`` marker.
    """
    seen: dict = {}

    def _spy(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, None, "")

    monkeypatch.setattr(subprocess, "run", _spy)
    run(["magick", "/tmp/x.jpg"])
    assert seen["stdout"] is subprocess.DEVNULL
    assert seen["stderr"] is subprocess.PIPE
    assert "capture_output" not in seen
    monkeypatch.undo()

    flood = (
        "import sys;sys.stdout.write('o' * 2_000_000);sys.stderr.write('e' * 50_000);sys.exit(1)"
    )
    with pytest.raises(RuntimeError, match="magick failed") as excinfo:
        run([sys.executable, "-c", flood])

    message = str(excinfo.value)
    assert message.endswith("...(truncated)")
    assert "o" * 200 not in message
    assert len(message) < MAX_MAGICK_STDERR_CHARS + 500
