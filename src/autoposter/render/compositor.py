import subprocess

from autoposter.config.schema import TextStyle
from autoposter.render.textfit import escape_caption_text

POSTER_SIZE = "2000x3000"
BACKGROUND_SIZE = "3840x2160"

# Provenance marker. Posterizarr writes this exact string and later greps for it
# to decide whether artwork has already been processed. Keep it byte-identical.
PROVENANCE_COMMENT = "created with posterizarr"


def build_stamp_argv(magick: str, image: str) -> list[str]:
    """Stamp the provenance comment. Always the first operation on an asset."""
    return [magick, image, "-set", "comment", PROVENANCE_COMMENT, image]


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
    common = [
        "-font", font_path,
        "-pointsize", str(point_size),
        "-size", box,
        "-background", "none",
        "-interline-spacing", str(style.line_spacing),
        "-gravity", style.gravity,
    ]
    if not style.add_stroke:
        return (
            ["("] + common + ["-fill", style.font_color, f"caption:{text}",
                              "-trim", "+repage", "-extent", box] + [")"]
        )
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
    magick: str, image: str, logo_path: str, style: TextStyle, quality: str
) -> list[str]:
    """Composite a clearlogo in place of the title text.

    The logo is fitted to the same box as the text block and placed at the same
    gravity and offset.
    """
    group = ["(", "-background", "none"]
    if logo_path.lower().endswith(".svg"):
        group += ["-density", "300"]
    group += [logo_path, "-resize", f"{style.max_width}x{style.max_height}", ")"]
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
    """Execute a magick command, raising with stderr attached on failure."""
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"magick failed ({result.returncode}): {' '.join(argv)}\n{result.stderr.strip()}"
        )
