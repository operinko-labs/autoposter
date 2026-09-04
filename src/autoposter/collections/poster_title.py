"""A styled collection title, drawn onto a managed collection's poster.

Roadmap row 105. Posterizarr can print a collection's title, plus a fixed
"COLLECTION" line, onto the poster it writes for a collection
(``CollectionPosterOverlayPart`` / ``CollectionTitlePosterPart``). This module
is that capability, and it is **styled after those parts, not byte-matched to
them.** No captured Posterizarr output exists anywhere -- not on the live Plex
server, not under ``tests/fixtures/`` -- and Kometa has no equivalent at all
(it sets a collection's poster from a URL or a file and never composites), so
there is no oracle to render against. What IS transcribed is the INPUT side:
the operator's own Posterizarr config carries both parts with values
(``posterizarr-config.txt:274-289`` and ``:310-328``, gitignored, transcribed
by phase 5b), and those are the shipped defaults of
``config.schema.CollectionPosterTitleConfig``. A config is not a proof; the
row cell and the pull request say so in as many words.

**Standalone, like ``collections/separator_art.py``, and for the same
reason.** ``render/pipeline.py``'s ``compose_styled``/``_CANVAS``/
``art_config_for`` are NOT widened to carry a "collection" art kind: a
collection poster has no clearlogo branch, no language order and no title-card
second line, so the shared function would gain three dead branches -- and
widening that file would collide head-on with two open branches that rewrite
it. This module reaches for the same primitives ``separator_art`` does and
draws its own image.

**Pillow, not ImageMagick, and that IS a divergence from ``separator_art``.**
``textfit.fit_point_size`` measures by shelling out to ``magick``; every test
that composited for real would then need ``@pytest.mark.imagemagick``, and
CI's main run deselects that marker on a runner with no binary -- a composite
test that silently never ran would be worse than no composite test. So the FIT
is measured with Pillow's own metrics here, while ``prepare_text`` (quote
normalisation, forced breaks, all-caps) and ``FitResult`` (the truncation
vocabulary) are reused verbatim from ``render/textfit.py`` so the
normalisation and the contract live in one place. Pillow is already this
package's image decoder -- ``collections/posters._is_image`` opens every byte
string that reaches an upload with it.

**Clamp, do not refuse.** ``separator_art._render`` abandons a render whose
text will not fit above ``min_point_size``, on the argument that illegible
artwork is worse than none because it still gets hashed and so is never
retried. That argument does not transfer. A divider's label is one of ten
short words this service chooses; a collection title is whatever the operator
named their collection, and the title box here is narrower than the divider's.
So a title that will not fit is drawn at the floor with a WARNING naming it --
which is what Posterizarr does. If the floor's own hard-wrapped block is
still taller than ``max_height``, the block itself is cut down and its last
surviving line ellipsized: the clamp stays INSIDE its box, the same way
ImageMagick's ``caption:`` never draws outside the box it was given. The ONE
refusal is an empty title, which has nothing to draw.

**Determinism.** Same poster bytes + same font file + same settings + same
Pillow build => the same output bytes, which ``apply_poster``'s sha-compare
turns into "an unchanged pass uploads nothing". Without it the opt-in cost
would be one re-upload per pass rather than one in total. Across Pillow BUILDS
the bytes may drift and every affected collection re-uploads once, which is
the same bounded, self-settling cost turning the gate on carries anyway.

**Failure posture.** There are exactly three refusals and each is a
``CollectionTitleRefused`` carrying a written reason and nothing an operator
cannot act on -- never a filesystem path, and never another exception's message
(roadmap row 213). Only the font one has a name to carry, and it carries the
configured font NAME, which is the string the operator wrote: "the configured
font %r resolves neither under fonts_root nor to a bundled face". The other two
carry no name at all, because there is none that would help: "the collection
title is empty, so there is nothing to draw", and "the poster did not decode as
an image (%s)" with the underlying exception's CLASS NAME and never its text.
The caller reports whichever fired and uploads nothing, leaving
``poster_sha256`` where it was so a later pass retries: a missing poster is
cosmetic and must never fail the surrounding pass.
"""
import io
import json
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from autoposter.config.schema import CollectionPosterTitleConfig, TextStyle
from autoposter.overlays.sources import OverlaySourceError, resolve_font_path
from autoposter.render.textfit import FitResult, prepare_text

logger = logging.getLogger(__name__)

# The canvas Posterizarr's transcribed box sizes, offsets and point sizes are
# written against. It is the same number ``render/compositor.POSTER_SIZE``
# carries ("2000x3000"). Every pixel value in the config is scaled by the
# fetched poster's own width over this, so a 1000x1500 hosted default gets the
# same composition as a 2000x3000 one instead of a 1900-pixel-wide text box on
# a 1000-pixel-wide image.
REFERENCE_WIDTH = 2000

# Upstream's ``-quality 100%``, the value ``separator_art._render`` writes at.
_QUALITY = 100


class CollectionTitleRefused(Exception):
    """This collection's poster could not have a title drawn onto it.

    Carries the configured font NAME and a written reason, never a filesystem
    path and never another exception's text: the caller puts this message into
    a run-report action an operator reads (roadmap row 213). ``PosterPathRefused``
    in ``collections/posters.py`` is the same shape, one module along.
    """


def poster_title_parts(config) -> list[str]:
    """This section's contribution to a managed collection's definition hash.

    It has to be there, for the reason ``lists._settings_parts`` gives about
    the ride-along settings: a definition hash is what a reconcile pass
    short-circuits on, so a collection that is already current would never be
    revisited and the operator's edit would never be drawn -- a setting that
    reads as saved and silently is not. Here that is the whole roll-out:
    without this term, flipping the gate on leaves every stable managed
    collection short-circuiting on ``definition_current`` and its poster is
    re-composited only if something ELSE happens to move its definition. With
    it, the very next pass re-composites each of them exactly once.

    Folded as a SUFFIX by ``reconcile.definition_hash``,
    ``smart.smart_definition_hash`` and ``lists._members_hash`` -- the last
    element of the payload, in the same position ``_settings_parts`` already
    occupies in all three.

    ``reconcile.separator_hash`` deliberately does NOT fold it. A divider is
    never captioned -- its art already carries the group's name -- so these
    settings cannot move its bytes, and putting them in its hash would buy a
    summary-and-sort-title re-write that changes nothing.

    ``playlists.py:572``'s own call to ``lists._members_hash`` is left on the
    new ``config=None`` default rather than widened to pass ``config``: a
    playlist has no composited poster, so there is nothing for this term to
    move, and ``poster_title_parts(None)`` is ``[]`` -- hash-safe by
    construction, not merely unedited.

    **Nothing is contributed while the gate is off**, and that is an explicit
    ``if not settings.enabled`` rather than a hope about what the defaults dump
    to. Every hash already stored on every live server has to keep matching,
    byte for byte: otherwise merely SHIPPING this section, switched off, would
    re-reconcile every managed collection in the library to write nothing. It
    is the same guarantee ``_settings_parts``' defaults and ``_members_hash``'s
    ``sync`` mode give, made structural instead of arithmetic.

    Takes the whole ``Config`` rather than the sub-model, mirroring
    ``posters.posters_enabled(config, http)``: all three call sites already
    hold ``config`` for exactly that call, and one place knowing the attribute
    path is one place to change if it ever moves. ``None`` is accepted for the
    same reason ``posters_enabled`` accepts it -- a pass with no config
    composites nothing.

    ``sort_keys`` because a hash input must not move when pydantic field
    ordering does, and ``mode="json"`` because a ``TextStyle`` holds only JSON
    scalars and it is the dump ``config/loader.py::render_version`` already
    takes of ``artwork``. The WHOLE sub-model is dumped, ``enabled`` included:
    every knob in it changes the glyphs, so every knob has to reach the pass.
    """
    settings = getattr(getattr(config, "collections", None), "poster_title", None)
    if settings is None or not settings.enabled:
        return []
    return [
        "poster_title=%s"
        % json.dumps(settings.model_dump(mode="json"), sort_keys=True)
    ]


def _scaled(value: int, scale: float) -> int:
    """One configured pixel value at the fetched poster's scale, never below 1.

    A floor of 1 rather than 0: a zero point size or a zero-width box would
    make Pillow raise from inside the draw, which the caller would then have to
    report as an unnamed failure.
    """
    return max(1, round(value * scale))


def _scaled_offset(value: int, scale: float) -> int:
    """A SIGNED offset at the fetched poster's scale.

    Unlike ``_scaled`` this may be zero or negative: ``TextStyle.text_offset``
    carries a deliberate sign (its own ``_must_carry_sign`` validator insists on
    one), and a negative offset is how the render pipeline's title card hides a
    block off-canvas.
    """
    return round(value * scale)


def _font(fonts_root: Path | None, value: str) -> Path:
    """The configured face as a real file, through the shared two-rung finder.

    ``overlays/sources.resolve_font_path`` is that finder: the operator's own
    ``fonts_root`` first (confined, so a written value cannot leave the mount),
    this service's bundled faces second. Reused rather than re-implemented so
    there is ONE answer in this codebase to "where does a configured font come
    from" -- and so an operator who drops Posterizarr's own
    ``Colus-Regular.ttf`` into their ``fonts_root`` and names it here gets
    exactly that file.

    Its refusal names the overlay vocabulary, which is the wrong noun on a
    collection poster, so it is re-raised as this module's own with the font's
    NAME in it. The original is chained for the log, never interpolated into
    the message.
    """
    try:
        return resolve_font_path(fonts_root, value, "collection poster title")
    except OverlaySourceError as exc:
        raise CollectionTitleRefused(
            "the configured font %r resolves neither under fonts_root nor to a "
            "bundled face" % value
        ) from exc


def _break_word(font: ImageFont.FreeTypeFont, word: str, width: int) -> list[str]:
    """``word`` split between characters into pieces that each fit ``width``.

    Only ever reached at the point-size floor, for a single word wider than the
    whole box. At least one character goes on every piece, so a box narrower
    than one glyph terminates instead of looping forever.
    """
    pieces: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and font.getlength(candidate) > width:
            pieces.append(current)
            current = char
        else:
            current = candidate
    pieces.append(current)
    return pieces


def _wrap(
    font: ImageFont.FreeTypeFont, text: str, width: int, *, hard: bool
) -> list[str] | None:
    """``text`` broken into lines that each fit ``width``, or ``None``.

    The explicit newlines ``prepare_text`` inserted are honoured first, then
    each of those paragraphs is greedily word-wrapped -- the same shape
    ImageMagick's ``caption:`` produces, which is what the transcribed box
    sizes were measured against.

    ``None`` reports a single WORD wider than the box: no smaller line break
    exists, so the caller tries a smaller point size. At the floor the caller
    asks again with ``hard=True``, which breaks that word between characters
    rather than returning nothing -- a clamped render draws something.
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if font.getlength(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if font.getlength(word) <= width:
                current = word
                continue
            if not hard:
                return None
            pieces = _break_word(font, word, width)
            lines.extend(pieces[:-1])
            current = pieces[-1]
        lines.append(current)
    return lines


def _block_height(font: ImageFont.FreeTypeFont, lines: list[str], line_spacing: int) -> int:
    """The drawn height of a wrapped block, from the face's own metrics.

    ``getmetrics()`` rather than a per-line ``getbbox``: every line in a block
    is laid out on the same baseline pitch, so measuring one line's ink and
    another's would make a block of "AAA" shorter than a block of "ggg" and
    move the anchor for no reason an operator would recognise.
    """
    ascent, descent = font.getmetrics()
    return len(lines) * (ascent + descent) + line_spacing * max(0, len(lines) - 1)


def _ellipsize_to_box(
    font: ImageFont.FreeTypeFont,
    lines: list[str],
    width: int,
    height: int,
    line_spacing: int,
) -> list[str]:
    """Cut ``lines`` down to however many fit ``height``, ellipsizing the last.

    Reached only when the floor point size's hard-wrapped block is STILL
    taller than its box: the point size can shrink no further, so the only
    way left to stay inside ``max_height`` is to draw fewer lines.
    ImageMagick's ``caption:``, which the transcribed box sizes were measured
    against, never draws outside the box it was given -- an operator-chosen
    title that will not fit even at the floor should lose its tail, not push
    the poster's own 'COLLECTION' line off the bottom edge.
    """
    ascent, descent = font.getmetrics()
    pitch = ascent + descent + line_spacing
    max_lines = max(1, (height + line_spacing) // pitch)
    kept = lines[:max_lines]
    last = kept[-1]
    while last and font.getlength(last + "…") > width:
        last = last[:-1]
    kept[-1] = f"{last.rstrip()}…" if last else "…"
    return kept


def _fit(
    font_path: Path,
    text: str,
    box: tuple[int, int],
    line_spacing: int,
    floor: int,
    ceiling: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], FitResult, bool]:
    """The largest point size in ``[floor, ceiling]`` whose block fits ``box``.

    A binary search rather than a scan: both the wrapped line count and each
    line's height rise monotonically with the point size, so "fits" is a
    monotone predicate and the search finds exactly what a 150-step descending
    scan would, with eight ``truetype`` loads instead of 150.

    Returns the fitted face, its lines, a ``FitResult`` -- the same
    ``truncated`` vocabulary ``render/textfit.fit_point_size`` uses, so a
    reader of either is reading one contract -- and whether the lines were
    further ellipsized. Unlike that function's callers this module DRAWS a
    truncated fit rather than abandoning it; ``truncated`` is what the
    caller's WARNING is keyed off. Even at the floor the block must stay
    INSIDE ``box``: a hard-wrapped block still taller than ``height`` is cut
    down and its last surviving line ellipsized, never drawn overflowing.
    """
    width, height = box
    best: tuple[int, ImageFont.FreeTypeFont, list[str]] | None = None
    low, high = floor, ceiling
    while low <= high:
        mid = (low + high) // 2
        font = ImageFont.truetype(str(font_path), mid)
        lines = _wrap(font, text, width, hard=False)
        if lines is not None and _block_height(font, lines, line_spacing) <= height:
            best = (mid, font, lines)
            low = mid + 1
        else:
            high = mid - 1
    if best is not None:
        size, font, lines = best
        return font, lines, FitResult(point_size=size, truncated=False), False
    font = ImageFont.truetype(str(font_path), floor)
    lines = _wrap(font, text, width, hard=True) or [text]
    overflowed = _block_height(font, lines, line_spacing) > height
    if overflowed:
        lines = _ellipsize_to_box(font, lines, width, height, line_spacing)
    return font, lines, FitResult(point_size=floor, truncated=True), overflowed


def _draw(
    image: Image.Image,
    style: TextStyle,
    font_path: Path,
    text: str,
    scale: float,
    block_name: str,
    title: str,
) -> None:
    """Draw one wrapped, auto-fitted text block onto ``image``, in place.

    Horizontally centred always -- both Posterizarr parts are, and the schema
    admits only the three vertical gravities this honours. Vertically the block
    is anchored from the configured edge by the configured offset, both scaled
    to the poster actually fetched.
    """
    box = (_scaled(style.max_width, scale), _scaled(style.max_height, scale))
    line_spacing = _scaled(style.line_spacing, scale) if style.line_spacing else 0
    font, lines, fit, overflowed = _fit(
        font_path,
        text,
        box,
        line_spacing,
        _scaled(style.min_point_size, scale),
        _scaled(style.max_point_size, scale),
    )
    if fit.truncated:
        detail = (
            " and still overflowed it, so the text was truncated with an ellipsis"
            if overflowed
            else ""
        )
        logger.warning(
            "%s for %r does not fit its %dx%d box above %dpt; drawing it clamped "
            "at the floor%s",
            block_name, title, box[0], box[1], fit.point_size, detail,
        )

    block = _block_height(font, lines, line_spacing)
    offset = _scaled_offset(int(style.text_offset), scale)
    if style.gravity == "north":
        top = offset
    elif style.gravity == "center":
        top = (image.height - block) // 2 + offset
    else:
        # "south" -- the config validator (_gravities_must_be_drawable) admits
        # only these three, so there is no fourth case to guess at.
        top = image.height - offset - block

    ascent, descent = font.getmetrics()
    pitch = ascent + descent + line_spacing
    stroke = _scaled(style.stroke_width, scale) if style.add_stroke else 0
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text(
            ((image.width - font.getlength(line)) / 2, top + index * pitch),
            line,
            font=font,
            fill=style.font_color,
            stroke_width=stroke,
            stroke_fill=style.stroke_color,
        )


def compose_collection_title(
    settings: CollectionPosterTitleConfig,
    fonts_root: Path | None,
    data: bytes,
    title: str,
) -> bytes:
    """``data`` with ``title`` and the fixed second line drawn on it, as JPEG.

    Blocking -- Pillow decodes, draws and re-encodes a whole poster -- so
    ``apply_poster`` calls it through ``asyncio.to_thread``, the same treatment
    that path already gives ``uploadPoster``.

    The fonts are resolved BEFORE the image is decoded, so a misconfigured face
    costs no decode and refuses in the same breath whichever poster arrived.

    Raises ``CollectionTitleRefused`` for an empty title, for a font that
    resolves nowhere, and for bytes that do not decode as an image. Each is a
    skip the caller REPORTS by name; none uploads anything, so
    ``poster_sha256`` stays where it was and the next pass tries again.
    """
    if not title.strip():
        raise CollectionTitleRefused(
            "the collection title is empty, so there is nothing to draw"
        )

    blocks: list[tuple[TextStyle, str, str]] = []
    if settings.title.add_text:
        blocks.append((settings.title, prepare_text(title, settings.title), "the title block"))
    line_text = prepare_text(settings.collection_line_text, settings.collection_line)
    if settings.collection_line.add_text and line_text.strip():
        blocks.append((settings.collection_line, line_text, "the collection line"))
    if not blocks:
        # Nothing to draw -- return the input untouched rather than a
        # decode/re-encode that would move poster_sha256 for no visible
        # change and silently transcode a fetched PNG/WebP poster to JPEG,
        # discarding its alpha.
        return data
    fonts = [_font(fonts_root, style.font) for style, _, _ in blocks]

    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = opened.convert("RGB")
    except Exception as exc:
        raise CollectionTitleRefused(
            "the poster did not decode as an image (%s)" % type(exc).__name__
        ) from exc

    scale = image.width / REFERENCE_WIDTH
    for font_path, (style, text, block_name) in zip(fonts, blocks):
        _draw(image, style, font_path, text, scale, block_name, title)

    buffer = io.BytesIO()
    # No EXIF and no timestamp is what Pillow writes by default here, which is
    # what makes equal inputs give equal bytes -- separator_art needs an
    # explicit ``-strip`` for the same guarantee because ImageMagick embeds
    # ``date:create``/``date:modify`` on every run.
    image.save(buffer, format="JPEG", quality=_QUALITY, subsampling=0)
    return buffer.getvalue()
