"""The overlay definition schema.

Every assertion cites the section of `.superpowers/sdd/p-overlay-grammar-probe.md`
that banks the behaviour. Sections, never line numbers -- the probe's lines move.
"""
import pytest
from pydantic import ValidationError

from autoposter.overlays.schema import OverlayDefinition


def _d(**over):
    base = {"name": "example"}
    base.update(over)
    return OverlayDefinition(**base)


def test_a_name_is_required_and_cannot_be_blank():
    """Probe section 1.1, identity: Kometa raises OverlayError on a missing
    or blank name."""
    with pytest.raises(ValidationError):
        OverlayDefinition()
    with pytest.raises(ValidationError):
        OverlayDefinition(name="   ")


def test_a_pipe_in_the_name_is_rejected():
    """Probe section 1.1: `|` is a reserved separator elsewhere in Kometa's
    pipeline and a name containing it is always refused."""
    with pytest.raises(ValidationError):
        OverlayDefinition(name="a|b")


def test_group_requires_a_weight():
    """Probe section 1.1, identity: `group` requires `weight`."""
    with pytest.raises(ValidationError):
        _d(group="ribbon")
    assert _d(group="ribbon", weight=190).weight == 190


def test_weight_cannot_be_negative():
    """Probe section 1.1: minimum 0."""
    with pytest.raises(ValidationError):
        _d(group="ribbon", weight=-1)


def test_the_offset_pair_is_all_or_nothing():
    """Probe section 1.1, positioning: giving only one of the two offsets
    raises. Align may still be omitted (probe section 3.2)."""
    with pytest.raises(ValidationError):
        _d(horizontal_offset=15)
    with pytest.raises(ValidationError):
        _d(vertical_offset=15)
    both = _d(horizontal_offset=15, vertical_offset=15)
    assert both.horizontal_align is None and both.vertical_align is None


def test_percentage_offsets_survive_as_strings():
    """Probe section 3.2: the `%` suffix survives into the stored offset and
    is re-parsed at draw time."""
    assert _d(horizontal_offset="10%", vertical_offset="10%").horizontal_offset == "10%"


def test_a_signed_offset_is_only_legal_under_center_alignment():
    """Probe section 3.2: a non-center alignment requires offset >= 0; center
    allows a signed offset, which is the only way an overlay lands left or
    above true centre."""
    assert _d(
        horizontal_offset=30, horizontal_align="right",
        vertical_offset=-105, vertical_align="center",
    ).vertical_offset == -105
    with pytest.raises(ValidationError):
        _d(horizontal_offset=-30, horizontal_align="right",
           vertical_offset=0, vertical_align="center")


def test_a_center_percentage_offset_is_bounded_to_plus_or_minus_fifty():
    """Probe section 3.2: -50% to 50% for the percent form under center."""
    assert _d(horizontal_offset="-50%", horizontal_align="center",
              vertical_offset=0, vertical_align="center").horizontal_offset == "-50%"
    with pytest.raises(ValidationError):
        _d(horizontal_offset="-51%", horizontal_align="center",
           vertical_offset=0, vertical_align="center")


def test_a_non_center_percentage_offset_is_bounded_to_zero_to_hundred():
    """Probe section 3.2."""
    with pytest.raises(ValidationError):
        _d(horizontal_offset="101%", horizontal_align="left",
           vertical_offset=0, vertical_align="top")


def test_back_width_and_height_default_to_the_minus_one_sentinel():
    """Probe section 3.5: -1 means "size to content" for every overlay that
    is not the special `backdrop` name."""
    d = _d()
    assert d.back_width == -1 and d.back_height == -1


def test_back_line_width_defaults_to_one_only_when_a_line_colour_is_set():
    """Probe section 1.1, backdrop."""
    assert _d().back_line_width is None
    assert _d(
        back_line_color="#FFFFFF", horizontal_offset=0, vertical_offset=0
    ).back_line_width == 1
    assert _d(
        back_line_color="#FFFFFF", back_line_width=4,
        horizontal_offset=0, vertical_offset=0,
    ).back_line_width == 4


def test_back_radius_defaults_to_kometas_zero_not_our_thirty():
    """Probe section 1.1 cites back_radius's type (int, overlay.py:200) but
    states no attribute default; 0 is this schema's own choice, distinct from
    the 30 every badge uses, which comes from Kometa's own templates.yml
    (probe section 5.5) -- a defaults-FILE choice, not the attribute's own
    default."""
    assert _d().back_radius == 0


def test_back_align_defaults_to_center():
    """Probe section 1.1, backdrop."""
    assert _d().back_align == "center"


def test_back_align_requires_back_width_to_be_set():
    """Probe section 1.1: back_align is only legal when back_width is also
    set (overlay.py:210-211). Explicitly writing back_align without
    back_width is refused; supplying both is fine."""
    with pytest.raises(ValidationError):
        _d(back_align="left")
    assert _d(back_align="left", back_width=305).back_align == "left"


def test_has_back_is_derived_from_the_two_colours():
    """Probe section 1.1: has_back = bool(back_color or back_line_color).

    A backdrop requires coordinates (probe section 3.3's has_coordinates()),
    so both colour cases are given horizontal_offset/vertical_offset here --
    see test_a_backdrop_without_coordinates_is_refused for the no-coordinates
    case.
    """
    assert _d().has_back is False
    assert _d(
        back_color="#00000099", horizontal_offset=0, vertical_offset=0
    ).has_back is True
    assert _d(
        back_line_color="#FFFFFF", horizontal_offset=0, vertical_offset=0
    ).has_back is True


def test_a_backdrop_without_coordinates_is_refused():
    """Probe section 1.1: a non-backdrop, non-queued overlay with a backdrop
    but no coordinates raises."""
    with pytest.raises(ValidationError):
        _d(back_color="#00000099")


def test_colours_resolve_to_rgba_and_an_invalid_one_is_refused():
    """Probe section 1.1: every colour goes through ImageColor.getcolor(.., 'RGBA')
    and an unparseable value raises."""
    d = _d(back_color="#00000099", horizontal_offset=0, vertical_offset=0)
    assert d.rgba("back_color") == (0, 0, 0, 153)
    assert _d().rgba("back_color") is None
    with pytest.raises(ValidationError):
        _d(back_color="not-a-colour", horizontal_offset=0, vertical_offset=0)


def test_font_defaults_match_kometas_attribute_defaults():
    """Probe section 1.1, text-only: font_size defaults to 36 (the class
    default at overlay.py:145), stroke_width to 0 (overlay.py:148). The probe
    states no font_color default -- #FFFFFF here is this schema's own choice,
    not a banked value."""
    d = _d()
    assert d.font_size == 36
    assert d.stroke_width == 0
    assert d.font_color == "#FFFFFF"


def test_addon_defaults_match_kometas():
    """Probe section 1.1, text-only: addon_offset 0, addon_position left."""
    d = _d()
    assert d.addon_offset == 0
    assert d.addon_position == "left"


def test_scale_width_and_height_must_be_positive():
    """M2: neither carried a bound, unlike every other integer on this model
    (`weight`, `back_line_width`, `back_radius`, `back_padding`, `font_size`,
    `stroke_width`). A negative value raises inside `Image.resize`; 0 was
    silently reinterpreted as 'native size' by `overlays/render.py::_scaled`'s
    `or` fallback -- the one value this schema was otherwise scrupulous about
    refusing rather than quietly ignoring. The probe's scale section
    (`.superpowers/sdd/p-overlay-grammar-probe.md` #55-57) banks only the
    parsing delegate, not a numeric range, so this stays a permissive
    positive-only floor rather than inventing a ceiling."""
    assert _d(scale_width=100, scale_height=50).scale_width == 100
    with pytest.raises(ValidationError):
        _d(scale_width=0)
    with pytest.raises(ValidationError):
        _d(scale_width=-5)
    with pytest.raises(ValidationError):
        _d(scale_height=0)
    with pytest.raises(ValidationError):
        _d(scale_height=-5)


def test_only_one_image_source_may_be_named():
    """Kometa's ladder (probe section 1.2) silently picks a winner when two are
    set. We refuse instead: an operator who set both meant one of them, and a
    silent precedence is the harder bug."""
    with pytest.raises(ValidationError):
        _d(file="a.png", url="https://example.invalid/a.png")


def test_the_backdrop_name_is_no_longer_refused():
    """Roadmap row 50: `backdrop` un-refused. Its whole point is a
    full-canvas layer, so unlike every other overlay with a backdrop, it
    needs no coordinates (probe section 1.1, overlay.py:325-330: offsets
    default to 0 for this name rather than being required)."""
    d = _d(name="backdrop", back_color="#00000099")
    assert d.has_back is True
    assert d.horizontal_offset is None


def test_a_non_backdrop_overlay_with_a_backdrop_still_needs_coordinates():
    """The exemption above is specific to the literal name 'backdrop', not
    to every name that merely contains the word -- unchanged from before
    this phase."""
    with pytest.raises(ValidationError):
        _d(name="backdrop_ribbon", back_color="#00000099")


def test_blur_nn_is_no_longer_refused_and_is_parsed_from_the_name():
    """Roadmap row 50: `blur(NN)` un-refused. Parsed on demand via
    `blur_amount` rather than stored as a separate field -- `badges/
    compose.py`'s pre-pass reads it directly off the definition."""
    assert _d(name="blur(30)").blur_amount == 30
    assert _d(name="blur(100)").blur_amount == 100
    assert _d().blur_amount is None


def test_a_malformed_blur_nn_is_refused_not_silently_degraded_to_fifty():
    """Adjudication A3: Kometa itself silently substitutes blur(50) on a
    malformed blur(NN) name (probe section 1.1, overlay.py:223-231) -- the
    one attribute-parse path in its whole class that degrades instead of
    raising. This schema refuses instead, matching every other validator in
    this file (its own docstring, lines 8-11) rather than letting a typo
    silently change blur strength."""
    with pytest.raises(ValidationError):
        _d(name="blur(0)")
    with pytest.raises(ValidationError):
        _d(name="blur(101)")
    with pytest.raises(ValidationError):
        _d(name="blur(abc)")
    with pytest.raises(ValidationError):
        _d(name="blur")


def test_a_padded_blur_name_is_canonicalised_not_silently_inert():
    """F2: `_validate` used to compare the stripped name against `_BLUR_FORM`
    but store `self.name` raw, so `blur_amount` (which reads `self.name`
    directly) and `render.py`'s `== "backdrop"` check saw the padding and
    diverged from what validation just proved. One canonical form: the
    stored name IS the stripped name."""
    assert _d(name="blur(30) ").name == "blur(30)"
    assert _d(name="blur(30) ").blur_amount == 30
    assert _d(name=" backdrop", back_color="#00000099").name == "backdrop"


def test_a_blur_definition_cannot_also_carry_an_image_or_backdrop():
    """F3: Kometa skips image-source resolution entirely for `blur*` names
    (probe section 1.1, image source note -- banked further in section 1.2,
    `overlay.py:218-219`), so a blur(NN) definition never carries an image or
    backdrop there. This schema now enforces the same premise
    `badges/compose.py`'s draw-loop skip relies on, refusing loudly instead
    of silently dropping the attribute."""
    with pytest.raises(ValidationError):
        _d(name="blur(30)", file="stamp.png")
    with pytest.raises(ValidationError):
        _d(name="blur(30)", builtin="ribbon")
    with pytest.raises(ValidationError):
        _d(name="blur(30)", url="https://example.invalid/a.png")
    with pytest.raises(ValidationError):
        _d(
            name="blur(30)", file="stamp.png",
            horizontal_offset=0, vertical_offset=0,
            back_color="#FF0000FF",
        )


def test_the_deferred_queue_attribute_is_refused_by_name():
    """Probe section 1.1, identity: `queue` is banked and deliberately not
    built this phase. `extra=\"forbid\"` alone would raise a generic
    "Extra inputs are not permitted" error that says nothing about deferral;
    the message must say so, matching the deferred-name-form refusals above."""
    with pytest.raises(ValidationError, match="roadmap row 97"):
        OverlayDefinition(name="example", queue="custom_queue_name")


def test_the_text_attribute_is_refused_not_silently_accepted():
    """T1's own note on this field: Kometa has no `text` attribute (probe
    section 2.1) -- the rendered literal lives in `name` as `text(LITERAL)`,
    which is what every builtin (`overlays/builtin.py`) and the operator draw
    path (`badges/compose.py::_draw_definitions`) actually read. `text` is
    this schema's own addition with no consumer anywhere in this codebase,
    so it is refused rather than accepted-and-ignored -- the same treatment
    as `queue` above, and the exact failure mode this module's own docstring
    warns against."""
    with pytest.raises(ValidationError, match="is not rendered"):
        _d(text="hello")


# --- the selection seam's one new field (overlay era, sub-phase C1) ---------


def test_a_definition_may_carry_a_condition():
    definition = OverlayDefinition(
        name="Direct-Play", condition={"resolution.regex": "(?i)2160|4k"}
    )
    assert definition.condition == {"resolution.regex": "(?i)2160|4k"}


def test_no_condition_is_the_default_and_means_draw_on_every_item():
    assert OverlayDefinition(name="plain").condition is None


def test_a_condition_naming_an_attribute_this_view_cannot_read_is_refused_at_load():
    """Global Constraint 10: refused at config load, naming the key and
    listing what is available -- never accepted and silently matching
    nothing at render time."""
    with pytest.raises(ValidationError) as caught:
        OverlayDefinition(name="x", condition={"genre": "Horror"})
    message = str(caught.value)
    assert "genre" in message
    assert "content_rating" in message


def test_a_condition_with_a_bad_operator_is_refused_at_load():
    with pytest.raises(ValidationError) as caught:
        OverlayDefinition(name="x", condition={"content_rating.contains": "PG"})
    assert "content_rating" in str(caught.value)


def test_an_empty_condition_is_refused_at_load():
    with pytest.raises(ValidationError):
        OverlayDefinition(name="x", condition={})


def test_the_refusal_names_the_overlay_it_came_from():
    """An operator with twenty definitions needs both halves to fix one."""
    with pytest.raises(ValidationError) as caught:
        OverlayDefinition(name="my-overlay", condition={"genre": "Horror"})
    assert "my-overlay" in str(caught.value)
