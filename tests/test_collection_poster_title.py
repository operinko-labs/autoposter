"""``collections.poster_title`` and ``collections/poster_title.py`` (row 105).

Pillow only. Nothing in this file invokes ``magick``, so nothing in it carries
``@pytest.mark.imagemagick`` -- CI's main run deselects that marker on a runner
with no binary, and a composite test that silently never ran would be worse
than no composite test.

The first test in the file is the storm guard, and it is the reason the section
lives under ``collections`` rather than under ``artwork``: ``render_version``
(``config/loader.py:48-55``) hashes ``config.artwork.model_dump()`` WHOLESALE,
so a key added under ``artwork`` moves ``config.version`` at its own default
value and strands every stored render fingerprint in the library.
"""
import io

import pytest
from PIL import Image

from autoposter.assets import asset_path
from autoposter.collections.poster_title import (
    CollectionTitleRefused,
    REFERENCE_WIDTH,
    compose_collection_title,
)
from autoposter.config.loader import render_version
from autoposter.config.schema import CollectionPosterTitleConfig

# A face that is committed to this repository and is reachable with no
# ``fonts_root`` at all, so these tests never depend on an operator mount.
BUNDLED = "Inter-Medium.ttf"


def _poster(width: int = 200, height: int = 300, colour: str = "navy") -> bytes:
    """A plain JPEG poster of a given size. Plain rather than noisy so that
    "some pixels changed" is unambiguously the text and not the encoder."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="JPEG", quality=100)
    return buffer.getvalue()


def _settings(**overrides) -> CollectionPosterTitleConfig:
    return CollectionPosterTitleConfig(**overrides)


def test_the_section_moves_no_render_version(config_factory):
    """The whole storm argument, in one assertion.

    Not "the default happens to be off": ``collections`` is excluded from
    ``render_version``'s hashed dict by construction (its docstring names the
    section at ``loader.py:37-40``), so turning the gate ON and changing every
    knob in it must still leave the value digit-for-digit identical.
    """
    config = config_factory()
    before = render_version(config)

    config.collections.poster_title.enabled = True
    config.collections.poster_title.collection_line_text = "SET"
    config.collections.poster_title.title.font = "Other-Font.ttf"
    config.collections.poster_title.title.min_point_size = 11
    config.collections.poster_title.title.max_point_size = 12
    config.collections.poster_title.title.max_width = 13
    config.collections.poster_title.title.max_height = 14
    config.collections.poster_title.title.text_offset = "-15"
    config.collections.poster_title.title.gravity = "north"
    config.collections.poster_title.collection_line.max_width = 16

    assert render_version(config) == before


def test_the_default_is_off_and_carries_the_transcribed_values(config_factory):
    """The shipped defaults, pinned against the operator's own Posterizarr file.

    ``posterizarr-config.txt:310-328`` (CollectionPosterOverlayPart) for the
    title block; ``:274-289`` (CollectionTitlePosterPart) for the fixed line's
    colour, stroke, caps and lineSpacing and for the word itself. The three
    values that are OURS rather than transcribed -- the font name, and the
    fixed line's offset and box -- are asserted in the second half, so a future
    edit cannot quietly promote one of them to "upstream's".
    """
    settings = config_factory().collections.poster_title

    assert settings.enabled is False
    assert settings.collection_line_text == "COLLECTION"
    # Transcribed, CollectionPosterOverlayPart.
    assert settings.title.all_caps is True
    assert settings.title.font_color == "white"
    assert settings.title.min_point_size == 100
    assert settings.title.max_point_size == 250
    assert settings.title.max_width == 1900
    assert settings.title.max_height == 500
    assert settings.title.text_offset == "+300"
    assert settings.title.gravity == "south"
    assert settings.title.line_spacing == 0
    assert settings.title.add_stroke is False
    assert settings.title.stroke_color == "black"
    assert settings.title.stroke_width == 6
    # Ours, and named as ours: Colus-Regular.ttf is not vendored, and both
    # Posterizarr parts carry "+300" because they are two poster TYPES rather
    # than two blocks on one image.
    assert settings.title.font == BUNDLED
    assert settings.collection_line.font == BUNDLED
    assert settings.collection_line.text_offset == "+120"
    assert settings.collection_line.max_width == 1200
    assert settings.collection_line.max_height == 150
    assert settings.collection_line.min_point_size == 40
    assert settings.collection_line.max_point_size == 90


def test_a_gravity_this_module_cannot_anchor_is_refused_by_name():
    """``TextStyle.gravity`` is an ImageMagick vocabulary and this module draws
    with Pillow, which anchors vertically only. A value it cannot honour is a
    loud refusal at config-validation time rather than a block drawn somewhere
    the operator did not ask for."""
    with pytest.raises(ValueError) as excinfo:
        CollectionPosterTitleConfig(
            collection_line={
                "font": BUNDLED, "min_point_size": 40, "max_point_size": 90,
                "max_width": 1200, "max_height": 150, "text_offset": "+120",
                "gravity": "southeast",
            },
        )
    assert "collections.poster_title.collection_line.gravity" in str(excinfo.value)
    assert "'southeast'" in str(excinfo.value)


def test_a_color_pillow_cannot_draw_is_refused_by_name():
    """``font_color``/``stroke_color`` describe an ImageMagick vocabulary but
    this module draws with Pillow's ``ImageColor``, a narrower parser. A value
    it cannot resolve is a loud refusal at config-validation time rather than
    a traceback repeating on every subsequent pass."""
    with pytest.raises(ValueError) as excinfo:
        CollectionPosterTitleConfig(
            collection_line={
                "font": BUNDLED, "min_point_size": 40, "max_point_size": 90,
                "max_width": 1200, "max_height": 150, "text_offset": "+120",
                "font_color": "not-a-colour",
            },
        )
    assert "collections.poster_title.collection_line.font_color" in str(excinfo.value)


def test_a_color_pillow_can_draw_is_accepted():
    """A hex value and a name Pillow does recognise both save cleanly."""
    settings = CollectionPosterTitleConfig(
        collection_line={
            "font": BUNDLED, "min_point_size": 40, "max_point_size": 90,
            "max_width": 1200, "max_height": 150, "text_offset": "+120",
            "font_color": "#ffffff", "stroke_color": "white",
        },
    )
    assert settings.collection_line.font_color == "#ffffff"
    assert settings.collection_line.stroke_color == "white"


# --- the composite itself -----------------------------------------------------
#
# Every test below draws for real, with Pillow, against a plain-colour poster.
# "Plain" is load-bearing: it makes "these pixels changed" mean the text and
# nothing else, and it makes the byte-identity assertion below meaningful.


def _changed_rows(before: bytes, after: bytes) -> set[int]:
    """Which pixel rows the composite touched.

    Compared row-by-row rather than by a whole-image diff so the positional
    assertions can say WHERE the text landed, which is what the gravity and
    the scaling are about.
    """
    original = Image.open(io.BytesIO(before)).convert("RGB")
    composited = Image.open(io.BytesIO(after)).convert("RGB")
    assert original.size == composited.size
    rows = set()
    for y in range(original.height):
        for x in range(0, original.width, 7):  # a stride, not every column
            if original.getpixel((x, y)) != composited.getpixel((x, y)):
                rows.add(y)
                break
    return rows


def test_an_empty_title_is_refused_by_name():
    """The ONE refusal. Everything else clamps (adjudication A-6)."""
    with pytest.raises(CollectionTitleRefused) as excinfo:
        compose_collection_title(_settings(), None, _poster(), "   ")
    assert "empty" in str(excinfo.value)


def test_a_font_that_resolves_nowhere_is_refused_by_name(tmp_path):
    """Named by the FONT, which is the string the operator wrote and the one
    they can act on -- and by nothing else: no path, and no other exception's
    text (roadmap row 213)."""
    settings = _settings()
    settings.title.font = "NoSuchFace.ttf"
    with pytest.raises(CollectionTitleRefused) as excinfo:
        compose_collection_title(settings, tmp_path, _poster(), "A Collection")
    message = str(excinfo.value)
    assert "'NoSuchFace.ttf'" in message
    assert str(tmp_path) not in message


def test_bytes_that_do_not_decode_are_refused_by_name():
    """A poster source can hand back an HTML error page saved as .jpg. The
    refusal carries the exception's CLASS NAME and never its text."""
    with pytest.raises(CollectionTitleRefused) as excinfo:
        compose_collection_title(_settings(), None, b"<html>not an image</html>", "A Collection")
    assert "did not decode" in str(excinfo.value)


def test_a_composite_draws_into_the_lower_band_and_stays_a_jpeg():
    """The happy path, through the bundled face with no fonts_root at all."""
    source = _poster()
    out = compose_collection_title(_settings(), None, source, "A Collection")

    assert out != source
    image = Image.open(io.BytesIO(out))
    assert image.format == "JPEG"
    assert image.size == (200, 300)

    rows = _changed_rows(source, out)
    assert rows, "the composite drew nothing at all"
    # gravity south, +300 title offset, +120 line offset, scaled to this
    # 200-wide poster (0.1x): the fixed line's own box bottom is exactly
    # 300 - round(120*0.1) = 288, which nothing may draw below -- the real
    # south bound, not the tautological "< height" the poster size gives for
    # free. The lower bound is tightened to the measured band (240) rather
    # than the loose half-image split the un-scaled assertion used to allow.
    assert min(rows) > 235
    assert max(rows) < 288


def test_the_same_inputs_give_byte_identical_output():
    """What apply_poster's sha-compare turns into 'an unchanged pass uploads
    nothing'. Without this the gate-on cost would be one re-upload PER PASS
    rather than one in total."""
    source = _poster()
    first = compose_collection_title(_settings(), None, source, "A Collection")
    second = compose_collection_title(_settings(), None, source, "A Collection")
    assert first == second


def test_a_long_title_is_clamped_rather_than_refused(caplog):
    """Adjudication A-6, and the deliberate divergence from separator_art.

    ``separator_art._render`` abandons a render whose text will not fit above
    the floor. That argument does not transfer: a divider's label is one of ten
    short words this service chooses, while a collection title is whatever the
    operator named their collection. Posterizarr clamps and writes; so do we,
    with a WARNING naming the title.
    """
    source = _poster()
    title = "The Extraordinarily Long Collection Of Films " * 6
    with caplog.at_level("WARNING"):
        out = compose_collection_title(_settings(), None, source, title)

    assert out != source
    assert any("clamped" in record.message for record in caplog.records)
    # MED-2: the clamp must stay inside its own box -- see
    # test_a_very_long_title_is_clamped_inside_its_own_box for the tight pin;
    # this reuses the same real south bound as the short-title composite.
    rows = _changed_rows(source, out)
    assert min(rows) >= 220
    assert max(rows) < 288


def test_a_very_long_title_is_clamped_inside_its_own_box(caplog):
    """MED-2: an unbounded clamp would erase the poster's art and push the
    fixed 'COLLECTION' line off the bottom edge. 800 characters is well past
    what any hard-wrapped floor-size block could hold in the title's 190x50
    box, so this proves the ellipsis-truncation path, not just the point-size
    floor the shorter clamp test above already covers.
    """
    source = _poster()
    title = ("The Extraordinarily Long Collection Of Films " * 20)[:800]

    # Isolate the title block: the changed rows must land ONLY inside its own
    # box, not spill into (or past) where the fixed line lives.
    title_only = _settings()
    title_only.collection_line.add_text = False
    with caplog.at_level("WARNING"):
        out = compose_collection_title(title_only, None, source, title)

    rows = _changed_rows(source, out)
    # The title block's own box: south, +300 offset, 500 max_height, scaled to
    # this 200-wide poster (0.1x) -- box_bottom = 300 - 30 = 270, box_top =
    # box_bottom - 50 = 220. A clamped block must land inside it; the small
    # margin below 270 is antialiasing bleed, well under one line (13px at
    # the 10pt floor) -- no slack larger than that.
    assert min(rows) >= 220
    assert max(rows) < 273
    assert any(
        "clamped" in record.message and "ellipsis" in record.message
        for record in caplog.records
    )

    # And with both blocks on, the fixed second line -- which never reads the
    # title -- is untouched: pixel-identical to the short-title composite. A
    # comfortably interior slice of its own box, clear of either title's ink.
    baseline = compose_collection_title(_settings(), None, source, "A Collection")
    both = compose_collection_title(_settings(), None, source, title)
    region = (0, 275, 200, 286)
    baseline_line = Image.open(io.BytesIO(baseline)).convert("RGB").crop(region)
    long_title_line = Image.open(io.BytesIO(both)).convert("RGB").crop(region)
    assert baseline_line.tobytes() == long_title_line.tobytes()


def test_the_operators_own_font_beats_the_bundled_face(tmp_path):
    """Rung one is the operator's mount, rung two is the bundle -- the bundle
    is a fallback, never an override. Proven by putting a DIFFERENT face under
    fonts_root under the bundled name and getting different pixels."""
    (tmp_path / "Inter-Medium.ttf").write_bytes(
        (asset_path("fonts") / "Comfortaa-Medium.ttf").read_bytes()
    )
    source = _poster()
    theirs = compose_collection_title(_settings(), tmp_path, source, "A Collection")
    bundled = compose_collection_title(_settings(), None, source, "A Collection")
    assert theirs != bundled


def test_the_boxes_scale_with_the_poster_actually_fetched():
    """The transcribed values are written against a 2000-pixel-wide canvas
    (``render/compositor.POSTER_SIZE`` is "2000x3000"), and the hosted defaults
    this service fetches are not all that size. Unscaled, a 1900-pixel text box
    on a 1000-pixel poster would run the title off both edges.

    The assertion is proportional, not absolute: on a half-size poster the
    text must land in the same FRACTION of the image as on a full-size one.
    """
    full = _poster(200, 300)
    half = _poster(100, 150)
    full_rows = _changed_rows(full, compose_collection_title(_settings(), None, full, "A Collection"))
    half_rows = _changed_rows(half, compose_collection_title(_settings(), None, half, "A Collection"))

    assert full_rows and half_rows
    assert abs(min(full_rows) / 300 - min(half_rows) / 150) < 0.05
    assert abs(max(full_rows) / 300 - max(half_rows) / 150) < 0.05


def test_the_fixed_second_line_can_be_switched_off():
    """Posterizarr's AddCollectionTitle, as this schema spells it."""
    source = _poster()
    both = compose_collection_title(_settings(), None, source, "A Collection")
    settings = _settings()
    settings.collection_line.add_text = False
    title_only = compose_collection_title(settings, None, source, "A Collection")

    assert both != title_only
    assert len(_changed_rows(source, title_only)) < len(_changed_rows(source, both))


def test_an_empty_fixed_line_draws_the_title_alone():
    """The same outcome by the other spelling -- an operator who empties the
    word rather than switching the block off."""
    source = _poster()
    settings = _settings()
    settings.collection_line.add_text = False
    off = compose_collection_title(settings, None, source, "A Collection")

    emptied = _settings()
    emptied.collection_line_text = ""
    assert compose_collection_title(emptied, None, source, "A Collection") == off


def test_the_reference_width_is_the_canvas_the_values_were_written_against():
    """Pinned as a constant rather than a literal 2000 in three places: it is
    the same number ``render/compositor.POSTER_SIZE`` carries, and the whole
    scaling argument rests on it."""
    assert REFERENCE_WIDTH == 2000


def test_a_north_gravity_draws_at_the_top():
    """The gravity branch, proven positionally rather than by reading it."""
    source = _poster()
    settings = _settings()
    settings.title.gravity = "north"
    settings.title.text_offset = "+100"
    settings.collection_line.add_text = False

    rows = _changed_rows(source, compose_collection_title(settings, None, source, "A Collection"))
    assert rows
    assert min(rows) >= 10
    assert max(rows) < 150


def test_a_center_gravity_draws_in_the_middle():
    """LOW-3: ``_COLLECTION_GRAVITIES`` admits three anchors; only ``south``
    (the default) and ``north`` were proven positionally before this."""
    source = _poster()
    settings = _settings()
    settings.title.gravity = "center"
    settings.title.text_offset = "+0"
    settings.collection_line.add_text = False

    rows = _changed_rows(source, compose_collection_title(settings, None, source, "A Collection"))
    assert rows
    assert min(rows) > 100
    assert max(rows) < 200
    assert abs((min(rows) + max(rows)) / 2 - 150) < 30


def test_both_blocks_off_returns_the_input_bytes_unchanged():
    """N-4: with nothing to draw, re-encoding would still move
    ``poster_sha256`` for a composite that changed nothing, and would
    silently transcode a fetched PNG/WebP poster to JPEG, discarding its
    alpha. Returning ``data`` untouched closes both."""
    settings = _settings()
    settings.title.add_text = False
    settings.collection_line.add_text = False
    source = _poster()

    assert compose_collection_title(settings, None, source, "A Collection") is source
