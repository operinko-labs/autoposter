"""The nine builtin overlays, re-expressed.

The parity proof lives in the EXISTING badge pins, which this phase never
edits. These assertions cover what those pins cannot see: that the definition
carries the attribute in the schema's own vocabulary rather than in a
transcription that happens to produce the same box.
"""
from autoposter.overlays.builtin import BUILTIN_OVERLAYS


def test_every_badge_is_defined_as_an_overlay():
    assert set(BUILTIN_OVERLAYS) == {
        "resolution", "audio_codec", "critic", "audience", "commonsense",
        "video_format", "runtimes", "episode_info", "languages",
    }


def test_the_image_only_badges_keep_badgespecs_default_font_size():
    """A3 claims `BADGES` (derived from these definitions -- T2 Step 7) is
    unchanged in shape AND value. `resolution` and `audio_codec` name no
    font_size (they draw no text), so without stating it explicitly here the
    schema's own default (36) would silently replace `BadgeSpec.font_size`'s
    default (55) once `_as_spec` forwards it -- a divergence no existing pin
    reads, since `test_badge_spec.py` and `test_badge_parity.py` never
    assert `font`/`font_size`."""
    assert BUILTIN_OVERLAYS["resolution"].font_size == 55
    assert BUILTIN_OVERLAYS["audio_codec"].font_size == 55
