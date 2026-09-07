import re
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
    """Normalise quote characters, apply the forced line breaks, then all-caps.

    Order matters and is fixed by roadmap row 42's two halves. Quotes are
    normalised first, so a ``newline_words`` key written with a plain
    apostrophe still matches a title that arrived with a typographic one.
    ``newline_words`` runs before ``newline_on_symbols``, so a manual break
    map can pre-empt a symbol rule for one specific phrase. Both run before
    ``all_caps``, so the keys are written in the title's own casing rather
    than shouted.

    ImageMagick's ``caption:`` honours a literal newline, so a break inserted
    here survives ``build_fit_argv``'s measurement and ``build_text_argv``'s
    draw identically -- the fit is measured on exactly the string drawn.

    With both settings at their defaults this returns exactly what it always
    did: an empty map substitutes nothing and an empty symbol list breaks
    nothing.
    """
    cleaned = text.translate(_QUOTE_TRANSLATION)
    for needle, replacement in style.newline_words.items():
        cleaned = cleaned.replace(needle, replacement)
    for symbol in style.newline_on_symbols:
        # The symbol swallows any whitespace immediately after it, so a break
        # after ": " doesn't leave a stray leading space on the next line.
        # The replacement is a callable, not an f-string, so a symbol
        # containing a backslash (e.g. "\") can never be misread as a
        # backreference such as \1 by re.sub's template parser.
        cleaned = re.sub(
            re.escape(symbol) + r"[ \t]*", lambda _m, symbol=symbol: f"{symbol}\n", cleaned
        )
    if style.newline_on_symbols:
        # A symbol at the very end of the text must not leave a trailing
        # blank line ImageMagick would measure.
        cleaned = cleaned.rstrip("\n")
    return cleaned.upper() if style.all_caps else cleaned


def escape_caption_text(text: str) -> str:
    """Neutralise ImageMagick ``caption:`` metacharacters in an arbitrary title.

    Movie and episode titles are attacker-free but not IM-safe: ImageMagick
    expands ``%`` property escapes inside caption text (e.g. "100% Wolf" (2020)
    would have its "%" substituted rather than drawn), so every ``%`` is doubled
    to render literally. ImageMagick also treats a caption value that *starts*
    with ``@`` as "read the text from this file" rather than literal text, so a
    leading ``@`` is backslash-escaped to force literal rendering.

    Both escapes were measured against ImageMagick 7.1.1-43 Q16 rather than
    assumed:

    - ``caption:%%`` renders one literal ``%`` (81px wide at 100pt) and
      ``caption:%%%%`` renders two (167px), against single- and double-character
      references of 63px and 134px. Doubling is therefore correct, and a title
      such as "100% Wolf" draws its percent sign instead of losing it.
    - ``caption:@HOME`` and ``caption:\\@HOME`` produced identical output
      (445x102), i.e. that build did not treat a leading ``@`` as a file read,
      so the backslash is consumed and the escape is a harmless no-op there.
      It is kept because the behaviour is build- and policy-dependent, and the
      cost of being wrong the other way is reading an arbitrary local file into
      a poster.
    """
    escaped = text.replace("%", "%%")
    if escaped.startswith("@"):
        escaped = "\\" + escaped
    return escaped


def build_fit_argv(magick: str, font_path: str, style: TextStyle, text: str) -> list[str]:
    """Argv that makes ImageMagick report the point size it would auto-fit to.

    ``-pointsize`` is deliberately absent: supplying ``-size`` without it is what
    triggers ``caption:`` auto-fitting. The text is escaped the same way as
    ``compositor._caption_group`` so the size measured here matches what is
    actually drawn.
    """
    return [
        magick,
        "-size", f"{style.max_width}x{style.max_height}",
        "-font", font_path,
        "-gravity", "center",
        "-fill", "black",
        "-interline-spacing", str(style.line_spacing),
        f"caption:{escape_caption_text(text)}",
        "-format", "%[caption:pointsize]",
        "info:",
    ]


# Cap on the magick stderr that reaches a RuntimeError message and, through
# `queue.jobs`, the `last_error` column. The bound `intake/routes.py`'s
# `_MAX_RAW_BODY_CHARS` puts on an unparseable webhook body, for the same
# reason: how big a database row gets is not a subprocess's decision.
#
# Here rather than in `compositor.py` because `compositor` already imports
# from this module (`escape_caption_text`) and the reverse import would be a
# cycle. Both magick entry points share the one bound (roadmap row 238,
# surface 2).
MAX_MAGICK_STDERR_CHARS = 2000


def cap_magick_stderr(stderr: str) -> str:
    """``stderr``, stripped, and truncated with a marker if it is over the cap."""
    text = stderr.strip()
    if len(text) > MAX_MAGICK_STDERR_CHARS:
        return text[:MAX_MAGICK_STDERR_CHARS] + "...(truncated)"
    return text


def _run(argv: list[str]) -> str:
    """Execute a magick command, raising with capped stderr attached on failure.

    stdout stays a pipe: this is the one magick call whose output is READ
    (the point size, parsed at the bottom of this function).
    """
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"magick failed ({result.returncode}): {' '.join(argv)}\n"
            f"{cap_magick_stderr(result.stderr)}"
        )
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
