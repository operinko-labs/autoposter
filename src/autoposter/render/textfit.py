import subprocess
from dataclasses import dataclass

from autoposter.config.schema import TextStyle

_QUOTE_TRANSLATION = str.maketrans(
    {
        "„": "'",  # „
        "“": "'",  # "
        "”": "'",  # "
        "‘": "'",  # '
        "’": "'",  # '
        "‚": "'",  # ‚
        "`": "",
    }
)


@dataclass(frozen=True)
class FitResult:
    point_size: int
    truncated: bool


def prepare_text(text: str, style: TextStyle) -> str:
    """Normalise quote characters, then apply all-caps."""
    cleaned = text.translate(_QUOTE_TRANSLATION)
    return cleaned.upper() if style.all_caps else cleaned


def build_fit_argv(magick: str, font_path: str, style: TextStyle, text: str) -> list[str]:
    """Argv that makes ImageMagick report the point size it would auto-fit to.

    ``-pointsize`` is deliberately absent: supplying ``-size`` without it is what
    triggers ``caption:`` auto-fitting.
    """
    return [
        magick,
        "-size", f"{style.max_width}x{style.max_height}",
        "-font", font_path,
        "-gravity", "center",
        "-fill", "black",
        "-interline-spacing", str(style.line_spacing),
        f"caption:{text}",
        "-format", "%[caption:pointsize]",
        "info:",
    ]


def _run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def fit_point_size(magick: str, font_path: str, style: TextStyle, text: str) -> FitResult:
    """Auto-fit the text, then clamp to the configured range.

    Falling below ``min_point_size`` means the title cannot be drawn legibly. The
    caller MUST abandon the render in that case — Posterizarr writes no file at
    all, and emitting one here would produce artwork the current system never
    would.
    """
    raw = _run(build_fit_argv(magick, font_path, style, text))
    try:
        fitted = int(float(raw))
    except ValueError as exc:
        raise RuntimeError(f"could not parse point size from magick output {raw!r}") from exc

    if fitted > style.max_point_size:
        return FitResult(point_size=style.max_point_size, truncated=False)
    if fitted < style.min_point_size:
        return FitResult(point_size=style.min_point_size, truncated=True)
    return FitResult(point_size=fitted, truncated=False)
