"""The nine badges `badges/spec.py` used to hardcode, re-expressed as overlay
definitions.

Every number here came from `badges/spec.py` unchanged; the CHANGE is that
each is now an attribute of the general schema rather than a field of a
bespoke dataclass. `badges/spec.py`'s `BADGES` is derived from this dict, so
the five existing badge pin files are the oracle for the swap and none of
them is edited (the parity law: shape AND value, not shape only).

Values come from Kometa v2.4.8's overlay defaults, cross-checked against
measured pixels from two production oracle images -- see
docs/research/kometa-overlays.md. Attribute semantics come from the overlay
grammar probe, cited by section.
"""
from autoposter.overlays.assets import INTER_BOLD, INTER_MEDIUM
from autoposter.overlays.schema import OverlayDefinition

BUILTIN_OVERLAYS: dict[str, OverlayDefinition] = {
    # A plain image overlay: the left/top alignment is get_cord's `else`
    # branch, where the offset is the raw distance from the origin edge
    # (probe section 3.3). No `builtin:` value: `resolution.png`/
    # `resolution/` is a directory, and `resolve_image_path` refuses a
    # `builtin:` that resolves to a missing file. This badge's own value
    # names the file inside `IMAGE_BADGE_DIRS["resolution"]` at render time
    # -- a value question, not a layout one, so the definition
    # names no specific image. `font_size=55` is stated explicitly because
    # the schema's own default is 36, `BadgeSpec.font_size` defaults to 55,
    # and this badge draws no text either way -- the parity law is shape AND
    # value, not shape only (`test_specs_produce_the_measured_boxes` never
    # asserts `font_size`, so a silent 55 -> 36 drift would pass unnoticed).
    "resolution": OverlayDefinition(
        name="resolution",
        horizontal_align="left", horizontal_offset=15,
        vertical_align="top", vertical_offset=15,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font_size=55,
    ),
    # A plain text overlay. `bottom` is get_cord's inward-from-the-far-edge
    # branch: canvas - overlay - offset (probe section 3.3). The literal is
    # the value itself, so `text` carries no <<variable>> token -- the value
    # arrives from badges/values.py, which this phase does not touch.
    "video_format": OverlayDefinition(
        name="text(video_format)",
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=30,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font=str(INTER_MEDIUM), font_size=55,
    ),
    # The literal is Kometa's own, from runtimes.yml (probe section 5.2):
    # `text: "Runtime: "` + `format: "<<runtimeH>>h <<runtimeM>>m"`, joined
    # by that file's `final_name`. `badges/values.py::runtime_text` still
    # produces this string today and is untouched by this phase; recording
    # the literal here is what lets an operator copy the shape.
    "runtimes": OverlayDefinition(
        name="text(Runtime: <<runtimeH>>h <<runtimeM>>m)",
        horizontal_align="right", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=30,
        back_width=600, back_height=105,
        back_color="#00000099", back_radius=30,
        font=str(INTER_MEDIUM), font_size=55,
    ),
    # 150, not 30: Kometa's `vertical_align.exists: false` branch fires here
    # even though the file sets `vertical_align: bottom`. Measured at y=825 on
    # a 1080-high canvas, which is exactly 1080 - 105 - 150. A tidy-up to 30
    # is a regression and test_badge_spec.py pins it.
    "episode_info": OverlayDefinition(
        name="text(S<<season_number0>>E<<episode_number0>>)",
        horizontal_align="right", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=150,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font=str(INTER_MEDIUM), font_size=55,
    ),
    # `center` is get_cord's third formula: canvas/2 - overlay/2 + offset
    # (probe section 3.3), the only one where the offset is a nudge rather
    # than a distance from an edge. `compact` is Kometa's audio_codec file
    # default and what production uses -- see docs/research/kometa-overlays.md
    # section 3.2. Two of these images are 135px tall against a 105px box and
    # are deliberately NOT scaled: Kometa lets them overflow. No `builtin:`
    # value, for the same reason as `resolution`: `audio_codec/compact` is a
    # directory, not a file, and this badge's own value names the file inside
    # `IMAGE_BADGE_DIRS["audio_codec"]`. `font_size=55` stated
    # explicitly for the same shape-and-value reason as `resolution`.
    "audio_codec": OverlayDefinition(
        name="audio_codec",
        horizontal_align="center", horizontal_offset=0,
        vertical_align="top", vertical_offset=15,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font_size=55,
    ),
    # 270 for the same reason as episode_info's 150: Kometa's
    # `vertical_align.exists: false` branch fires even though the file sets
    # `vertical_align: bottom`. Confirmed in pixels.
    #
    # The first addon overlay: icon beside the text. `addon_position: left`
    # and `addon_offset: 15` (probe section 1.1) are what
    # badges/compose.py used to express as `if name == "commonsense"`.
    "commonsense": OverlayDefinition(
        name="text(<<content_rating>>)",
        builtin="Commonsense",
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font=str(INTER_MEDIUM), font_size=55,
        addon_offset=15, addon_position="left",
    ),
    # `back_padding: 15` turns the 160x160 content box into a 190x190
    # backdrop, expanding on all four sides (probe section 1.1). The -105
    # vertical offset is legal only because the alignment is `center` --
    # probe section 3.2 -- and it moves the badge ABOVE true centre, which is
    # what puts it over `audience`.
    "critic": OverlayDefinition(
        name="text(<<critic_rating#>>)",
        builtin="rating/IMDb",
        horizontal_align="right", horizontal_offset=30,
        vertical_align="center", vertical_offset=-105,
        back_width=160, back_height=160, back_padding=15,
        back_color="#00000099", back_radius=30,
        font=str(INTER_BOLD), font_size=63,
        addon_offset=15, addon_position="top",
    ),
    # critic's twin below the centreline: +105 where critic is -105. The
    # literal uses the `%` modifier (probe section 2.3): x10-as-int, which
    # truncates rather than rounds -- badges/values.py::audience_text already
    # records that production output really does.
    "audience": OverlayDefinition(
        name="text(<<audience_rating%>>%)",
        builtin="rating/TMDb",
        horizontal_align="right", horizontal_offset=30,
        vertical_align="center", vertical_offset=105,
        back_width=160, back_height=160, back_padding=15,
        back_color="#00000099", back_radius=30,
        font=str(INTER_BOLD), font_size=63,
        addon_offset=15, addon_position="top",
    ),
    # The only badge without a backdrop: the production config applies the
    # languages overlay twice, the second time fully transparent, and neither
    # oracle image shows a backdrop behind the flag. `has_back` is DERIVED
    # from the colours (probe section 1.1), so omitting back_color is what
    # switches it off -- there is no separate flag to get wrong.
    #
    # This is the badge that looks most like a Kometa queue: one flag per
    # audio language, stacked 61px apart. It is deliberately NOT expressed as
    # one -- the queue is filed forward on row 97, and
    # badges/compose.py::_draw_languages keeps its bespoke loop.
    "languages": OverlayDefinition(
        name="text(<<audio_language>>)",
        horizontal_align="left", horizontal_offset=15,
        vertical_align="top", vertical_offset=223,
        back_width=190, back_height=105,
        back_radius=26,
        font=str(INTER_BOLD), font_size=50,
    ),
}
