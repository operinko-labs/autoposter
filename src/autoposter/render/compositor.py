import subprocess

from autoposter.config.schema import TextStyle
from autoposter.render.textfit import cap_magick_stderr, escape_caption_text

POSTER_SIZE = "2000x3000"
BACKGROUND_SIZE = "3840x2160"

# The smallest box containing every canvas above (widest width x tallest
# height). The stamp is the FIRST magick call on a freshly downloaded source
# and, until this bound existed, decoded and re-encoded it at whatever
# resolution the provider served -- at Q16-HDRI's 16 bytes per RGBA pixel, held
# twice, a 10000x10000 source is 3.2 GB in one process, and the pipeline runs
# five of them concurrently. That is the demand side of the production OOM
# (.superpowers/sdd/p-oom-investigation.md).
#
# Enforced with ImageMagick's ``>`` flag -- "resize only if larger than this" --
# so a source already inside the box is not resized at all and the stamp writes
# byte-identical output to what it wrote before the bound existed. That is what
# keeps tests/test_golden.py's byte-exact production parity true.
#
# Sources above the box lose resolution the later cover-fit could in principle
# have cropped from (an aspect-mismatched giant is scaled to fit rather than to
# cover), which is deliberate: that is precisely the shape this bounds.
STAMP_MAX_GEOMETRY = "3840x3000"

# Provenance marker. Posterizarr writes this exact string and later greps for it
# to decide whether artwork has already been processed. Keep it byte-identical.
PROVENANCE_COMMENT = "created with posterizarr"


def build_stamp_argv(magick: str, image: str) -> list[str]:
    """Stamp the provenance comment. Always the first operation on an asset.

    Also the point at which the source's resolution is bounded -- see
    ``STAMP_MAX_GEOMETRY``. This diverges from the captured Posterizarr command
    on purpose; ``tests/test_production_parity.py`` pins the divergence.
    """
    return [
        magick, image,
        "-resize", f"{STAMP_MAX_GEOMETRY}>",
        "-set", "comment", PROVENANCE_COMMENT,
        image,
    ]


def build_base_argv(
    magick: str,
    image: str,
    canvas: str,
    overlay_path: str | None,
    quality: str,
    add_border: bool,
    border_color: str,
    border_width: int,
) -> list[str]:
    """Cover-fit the source art to the canvas, then optionally overlay and border.

    ``-resize <canvas>^`` scales so both dimensions meet or exceed the target;
    ``-gravity center -extent <canvas>`` centre-crops the overshoot.
    ``-shave`` before ``-border`` keeps the final size exactly ``canvas``.
    """
    argv = [magick, image, "-resize", f"{canvas}^", "-gravity", "center", "-extent", canvas]
    if overlay_path:
        argv += [overlay_path, "-gravity", "south", "-quality", quality, "-composite"]
    if add_border:
        argv += [
            "-shave", f"{border_width}x{border_width}",
            "-bordercolor", border_color,
            "-border", str(border_width),
        ]
    argv.append(image)
    return argv


def _caption_group(style: TextStyle, font_path: str, point_size: int, text: str) -> list[str]:
    box = f"{style.max_width}x{style.max_height}"
    text = escape_caption_text(text)
    # Argument order matches the sequence Posterizarr actually emits, verified
    # against a production ImageMagickCommands.log. ImageMagick settings are
    # order-independent as long as they precede the operation they affect, so
    # this is about keeping the two byte-comparable rather than about behaviour.
    common = [
        "-font", font_path,
        "-pointsize", str(point_size),
    ]
    box_settings = [
        "-size", box,
        "-background", "none",
        "-interline-spacing", str(style.line_spacing),
        "-gravity", style.gravity,
    ]
    if not style.add_stroke:
        return (
            ["("]
            + common
            + ["-fill", style.font_color]
            + box_settings
            + [f"caption:{text}", "-trim", "+repage", "-extent", box]
            + [")"]
        )
    common = common + box_settings
    # Stroke first, fill second: in a two-image list -composite puts image[1] over
    # image[0], so the stroked copy sits behind.
    stroke = ["("] + common + [
        "-fill", style.stroke_color,
        "-stroke", style.stroke_color,
        "-strokewidth", str(style.stroke_width),
        f"caption:{text}",
    ] + [")"]
    fill = ["("] + common + [
        "-fill", style.font_color, "-stroke", "none", f"caption:{text}",
    ] + [")"]
    return (
        ["(", "-size", box, "-background", "none"]
        + stroke
        + fill
        + ["-gravity", style.gravity, "-composite", "-trim", "+repage", "-extent", box, ")"]
    )


def build_text_argv(
    magick: str,
    image: str,
    style: TextStyle,
    font_path: str,
    point_size: int,
    text: str,
    quality: str,
) -> list[str]:
    """Draw one text block onto the image.

    ``style.text_offset`` already carries its sign and is concatenated after
    ``+0`` to form the geometry, e.g. ``+0+300``.
    """
    return (
        [magick, image, "-gravity", "center", "-background", "None", "-layers", "Flatten"]
        + _caption_group(style, font_path, point_size, text)
        + [
            "-gravity", style.gravity,
            "-geometry", f"+0{style.text_offset}",
            "-quality", quality,
            "-composite",
            image,
        ]
    )


def build_logo_argv(
    magick: str, image: str, logo_path: str, style: TextStyle, quality: str,
    flat_color: str | None = None,
) -> list[str]:
    """Composite a clearlogo in place of the title text.

    The logo is fitted to the same box as the text block and placed at the same
    gravity and offset.

    ``flat_color`` (roadmap row 46) flattens the logo to one colour before it
    is resized: ``-fill C -colorize 100`` recolours every pixel while leaving
    the alpha channel alone, which is what keeps a flattened logo a logo
    rather than a coloured rectangle. Applied before ``-resize`` so the
    recolour costs one pass over the original, smaller-or-equal image.
    """
    group = ["(", "-background", "none"]
    if logo_path.lower().endswith(".svg"):
        group += ["-density", "300"]
    group += [logo_path]
    if flat_color:
        group += ["-fill", flat_color, "-colorize", "100"]
    group += ["-resize", f"{style.max_width}x{style.max_height}", ")"]
    return (
        [magick, image]
        + group
        + [
            "-gravity", style.gravity,
            "-geometry", f"+0{style.text_offset}",
            "-quality", quality,
            "-composite",
            image,
        ]
    )


def run(argv: list[str]) -> None:
    """Execute a magick command, raising with capped stderr attached on failure.

    stdout is DISCARDED rather than buffered (roadmap row 238, surface 2).
    This function returns None and no call site reads a stream from it, so
    `capture_output=True` held whatever a composite step chose to print in
    this process's memory for no reader -- with five workers doing it at once.
    The one magick call whose output IS read is `textfit._run`, which keeps
    its pipe.

    stderr is still captured, because it is the failure message; it is capped
    by `cap_magick_stderr` on the way into the RuntimeError, because that
    message becomes `job.last_error`.

    No argv token changes, so `tests/test_production_parity.py`'s full-list
    equalities and `tests/test_golden.py`'s byte-exact parity are untouched.
    """
    result = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"magick failed ({result.returncode}): {' '.join(argv)}\n"
            f"{cap_magick_stderr(result.stderr)}"
        )
