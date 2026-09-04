"""Media attributes and image-badge filename selection.

Fakes here mirror the shape of a real plexapi Video object closely enough to
exercise the extraction, but the mapping assertions are what matter: a wrong
filename silently renders the wrong badge.
"""
import pytest

from autoposter.assets import asset_path
from autoposter.badges.compose import BadgeInputs, badge_fingerprint, badge_values
from autoposter.badges.values import (
    MediaInfo,
    audio_codec_image,
    language_slots,
    media_info_from_plex,
    resolution_image,
)


class FakeStream:
    def __init__(self, stream_type, **kw):
        self.streamType = stream_type
        for k, v in kw.items():
            setattr(self, k, v)


class FakePart:
    def __init__(self, streams, file=None):
        self.streams = streams
        self.file = file


class FakeMedia:
    def __init__(self, parts, **kw):
        self.parts = parts
        for k, v in kw.items():
            setattr(self, k, v)


class FakeItem:
    def __init__(self, media, duration=None, season=None, episode=None):
        self.media = media
        self.duration = duration
        self.seasonNumber = season
        self.episodeNumber = episode
        self.reloaded = False

    def reload(self):
        self.reloaded = True


def _item():
    video = FakeStream(1, codec="hevc", colorTrc="bt709")
    audio = FakeStream(2, codec="eac3", language="English", languageCode="eng", channels=6)
    sub = FakeStream(3, codec="srt", language="Finnish", languageCode="fin")
    media = FakeMedia([FakePart([video, audio, sub])], videoResolution="1080",
                      audioCodec="eac3", audioChannels=6)
    return FakeItem([media], duration=4845912, season=1, episode=1)


def test_media_info_reads_the_attributes_badges_need():
    info = media_info_from_plex(_item())
    assert info.video_resolution == "1080"
    assert info.audio_codec == "eac3"
    assert info.audio_channels == 6
    assert info.duration_ms == 4845912
    assert info.season_number == 1
    assert info.episode_number == 1
    assert info.audio_languages == ("en",)


def test_media_info_reloads_when_media_is_absent():
    """A search result carries no stream detail until reloaded."""
    item = FakeItem([])
    media_info_from_plex(item)
    assert item.reloaded is True


def test_media_info_on_an_item_with_no_media_is_all_empty():
    info = media_info_from_plex(FakeItem([]))
    assert info.video_resolution is None
    assert info.audio_languages == ()


@pytest.mark.parametrize(
    "resolution,flags,expected",
    [
        ("1080", frozenset(), "1080p"),
        ("1080", frozenset({"hdr"}), "1080phdr"),
        ("1080", frozenset({"dv"}), "1080pdv"),
        ("1080", frozenset({"dv", "hdr"}), "1080pdvhdr"),
        ("1080", frozenset({"hlg"}), "1080phlg"),
        ("4k", frozenset(), "4k"),
        ("4k", frozenset({"hdr"}), "4khdr"),
        ("720", frozenset(), "720p"),
        ("480", frozenset(), "480p"),
        ("576", frozenset(), "576p"),
    ],
)
def test_resolution_image_names(resolution, flags, expected):
    info = MediaInfo(resolution, None, None, None, (), flags, None, None)
    assert resolution_image(info) == expected


def test_resolution_image_is_suppressed_for_an_unknown_resolution():
    info = MediaInfo("240", None, None, None, (), frozenset(), None, None)
    assert resolution_image(info) is None


@pytest.mark.parametrize(
    "codec,expected",
    [("eac3", "plus"), ("ac3", "digital"), ("truehd", "truehd"), ("dca", "dts"),
     ("aac", "aac"), ("flac", "flac"), ("opus", "opus")],
)
def test_audio_codec_image_names(codec, expected):
    info = MediaInfo(None, codec, None, None, (), frozenset(), None, None)
    assert audio_codec_image(info) == expected


def test_audio_codec_image_is_suppressed_for_an_unmapped_codec():
    info = MediaInfo(None, "sometkingelse", None, None, (), frozenset(), None, None)
    assert audio_codec_image(info) is None


def test_language_slots_map_to_flags_and_labels():
    info = MediaInfo(None, None, None, None, ("en", "ja"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN"), ("jp", "JA")]


def test_language_slots_order_by_kometa_weight_not_input_order():
    """English (weight 610) outranks Finnish (280) regardless of track order."""
    info = MediaInfo(None, None, None, None, ("fi", "en"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN"), ("fi", "FI")]


def test_language_slots_are_capped():
    info = MediaInfo(None, None, None, None, ("en", "de", "fr", "es"), frozenset(), None, None)
    assert len(language_slots(info, limit=3)) == 3


def test_unknown_languages_are_dropped_not_guessed():
    info = MediaInfo(None, None, None, None, ("en", "zzz"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN")]


def test_dolby_vision_over_hlg_names_a_file_that_exists():
    """Profile 8.4 streams report both DV and HLG. No `dvhlg` asset is
    vendored for any resolution, so concatenating both would name a
    nonexistent PNG and the badge would fail at render time."""
    info = MediaInfo("1080", None, None, None, (), frozenset({"dv", "hlg"}), None, None)
    assert resolution_image(info) == "1080pdv"


def test_dv_hdr_plus_picks_the_most_specific_vendored_variant():
    info = MediaInfo("4k", None, None, None, (), frozenset({"dv", "hdr", "plus"}), None, None)
    assert resolution_image(info) == "4kdvhdrplus"


def test_every_resolution_image_name_exists_on_disk():
    """Guards the whole mapping, not just the combinations we thought to list."""
    import itertools
    from pathlib import Path

    flags = ("dv", "hdr", "hlg", "plus")
    root = Path("assets/badges/images/resolution")
    for resolution in ("4k", "1080", "720", "576", "480"):
        for size in range(len(flags) + 1):
            for combo in itertools.combinations(flags, size):
                info = MediaInfo(resolution, None, None, None, (), frozenset(combo), None, None)
                stem = resolution_image(info)
                assert (root / ("%s.png" % stem)).exists(), (
                    "%s + %s -> %s.png which does not exist" % (resolution, combo, stem)
                )


def test_every_audio_codec_image_name_exists_on_disk():
    from pathlib import Path

    from autoposter.badges.values import AUDIO_CODECS

    root = Path("assets/badges/images/audio_codec/compact")
    for codec in AUDIO_CODECS:
        info = MediaInfo(None, codec, None, None, (), frozenset(), None, None)
        stem = audio_codec_image(info)
        assert (root / ("%s.png" % stem)).exists(), "%s -> %s.png missing" % (codec, stem)


def _item_with_path(path, video_kw=None):
    video = FakeStream(1, codec="hevc", **(video_kw or {"colorTrc": "smpte2084"}))
    media = FakeMedia([FakePart([video], file=path)], videoResolution="1080",
                      audioCodec="eac3", audioChannels=6)
    return FakeItem([media], duration=4845912)


def test_media_info_carries_the_file_path():
    """The video_format badge and the HDR10+ resolution variant are both
    filepath regexes in Kometa, so the path is a badge input, not diagnostics."""
    info = media_info_from_plex(_item_with_path("/m/Show/S01E01.WEBDL-1080p.mkv"))
    assert info.file_path == "/m/Show/S01E01.WEBDL-1080p.mkv"


def test_hdr10_plus_is_detected_from_the_file_path():
    """Plex's video stream carries no HDR10+ marker -- plexapi 4.18.2
    `VideoStream` has colorTrc and the DOVI fields and nothing else -- so
    Kometa reads it off the path, and so do we. Without this the vendored
    `*plus.png` assets are unreachable."""
    info = media_info_from_plex(_item_with_path("/m/Dune (2021)/Dune.2021.HDR10+.2160p.mkv"))
    assert "plus" in info.hdr_flags
    assert resolution_image(info) == "1080pplus"


def test_hdr10_plus_outranks_plain_hdr():
    """An HDR10+ stream is backwards-compatible and reports smpte2084 too, so
    the `hdr` flag is always set alongside `plus`. Matching `hdr` first would
    leave every `*plus.png` unreachable, which is the bug this guards."""
    info = media_info_from_plex(_item_with_path("/m/X/X.HDR10Plus.2160p.mkv"))
    assert info.hdr_flags >= {"hdr", "plus"}
    assert resolution_image(info) == "1080pplus"


def test_dolby_vision_over_hdr10_plus_names_the_dvhdrplus_asset():
    info = media_info_from_plex(
        _item_with_path("/m/X/X.DV.HDR10+.2160p.mkv",
                        {"colorTrc": "smpte2084", "DOVIPresent": True})
    )
    assert resolution_image(info) == "1080pdvhdrplus"


def test_a_path_without_an_hdr10_plus_marker_gets_no_plus_flag():
    info = media_info_from_plex(_item_with_path("/m/X/X.HDR10.2160p.mkv"))
    assert "plus" not in info.hdr_flags
    assert resolution_image(info) == "1080phdr"


# --- Plex's ISO 639-2 languageCode, reduced to the flag table's key ---------
#
# `languages.json` is keyed by ISO 639-1 and its entries name a COUNTRY, which
# is what `images/flag/round/<country>.png` is called. Plex hands us ISO 639-2.
# The hop between them used to be `[:2]`, which is a truncation and not a
# conversion: right for `eng`, wrong for `swe`, and absent for `ger`.


def _audio_only_item(*codes):
    """A plexapi-shaped stand-in carrying one audio stream per `languageCode`
    and nothing else -- no resolution, no codec, no duration, no file path --
    so `badge_values` below yields the `languages` key and NOTHING else. That
    is what makes the literal digests in the two fingerprint tests readable:
    every other input to `badge_fingerprint` is a literal in the call."""
    streams = [FakeStream(2, languageCode=code) for code in codes]
    return FakeItem([FakeMedia([FakePart(streams)])])


@pytest.mark.parametrize(
    "code,expected_reduced,expected_slots",
    [
        # The 639-2/B forms whose first two letters are not their 639-1 code.
        # `swe` is the dangerous one: `sw` is a REAL key (Swahili), so the
        # badge drew a Tanzanian flag on a Swedish track rather than no flag.
        ("swe", "sv", [("se", "SV")]),
        ("ger", "de", [("de", "DE")]),
        ("cze", "cs", [("cz", "CS")]),
        ("dut", "nl", [("nl", "NL")]),
        ("gre", "el", [("gr", "EL")]),
        ("ice", "is", [("is", "IS")]),
        ("chi", "zh", [("cn", "ZH")]),
        # The three "no language here" tags. `langcodes` answers `und` with
        # None and the other two with themselves, so all three keep their raw
        # tag -- and none is a `languages.json` key, so all three draw NO
        # flag, exactly as before. The slice reached the same no-flag outcome
        # for the wrong reason (`und` -> `un`), which is why the reduced code
        # is asserted alongside the slots.
        ("und", "und", []),
        ("mis", "mis", []),
        ("qaa", "qaa", []),
    ],
)
def test_a_three_letter_code_reaches_its_own_flag_not_a_two_letter_neighbour(
    code, expected_reduced, expected_slots
):
    info = media_info_from_plex(_audio_only_item(code))
    assert info.audio_languages == (expected_reduced,)
    assert language_slots(info) == expected_slots


def test_every_flag_this_fix_now_names_is_actually_vendored():
    """`compose.py::_draw_languages` skips a missing flag file with a bare
    `continue` -- no error, no fallback, no log line. A country stem with no
    PNG behind it would therefore look exactly like the bug being fixed here.
    All seven are vendored; this asserts it rather than trusting it."""
    for country in ("se", "de", "cz", "nl", "gr", "is", "cn"):
        path = asset_path("badges", "images", "flag", "round", "%s.png" % country)
        assert path.exists(), "no vendored flag for %r" % country


def test_an_already_correct_code_keeps_its_badge_fingerprint_to_the_byte():
    """The other half of the storm guard. `badge_values`' `languages` string
    is a `badge_fingerprint` input, so this change re-badges and re-uploads
    every item it moves. It must move NOTHING else: `eng` reduced to `en`
    under the old slice and reduces to `en` now, so this digest is a literal
    -- computed from the pre-fix code path -- and it must survive the fix
    unchanged. If it moves, the change is re-rendering the whole library."""
    info = media_info_from_plex(_audio_only_item("eng"))
    values = badge_values("poster", BadgeInputs(media=info))
    assert values == {"languages": "us:EN"}
    assert badge_fingerprint("base-fp", "poster", values, "manifest-sha") == (
        "0b2c79c1a0a7e7457ce682f226ea5856584f48f57c211ace5c9d743972e1dbdb"
    )


def test_a_swedish_track_moves_the_badge_fingerprint_off_the_swahili_one():
    """The movement, stated honestly and pinned in both directions. Every
    item with a Swedish audio track re-badges and re-uploads ONCE after this
    lands -- it is currently serving a Tanzanian flag, which is the reason
    for the change. `render/pipeline.py`'s gate compares the new digest to
    the stored one, so the old literal below is what is in the database
    today and the new one is what replaces it. Only the badge layer redraws:
    `badge_fingerprint` is deliberately separate from the base `fingerprint`,
    so no source art is re-fetched and no base is re-composited."""
    info = media_info_from_plex(_audio_only_item("swe"))
    values = badge_values("poster", BadgeInputs(media=info))
    assert values == {"languages": "se:SV"}
    assert badge_fingerprint("base-fp", "poster", values, "manifest-sha") == (
        "2ea31fff79ac563243bcc593554f8e6da1f67d3a2ea3194af7d9039ba3e85611"
    )
    assert badge_fingerprint(
        "base-fp", "poster", {"languages": "tz:SW"}, "manifest-sha"
    ) == "2dfb76dff72106d30eb948df88fc8344d23ae9081427781a7768f1d5454ff0cd"


def test_norwegian_bokmal_loses_its_flag_and_that_is_a_decision_not_an_accident():
    """The one regression this fix causes, pinned so it cannot happen
    quietly. `nob` reduces to `nb`, and `languages.json` carries `no` but
    neither `nb` nor `nn` -- so a track tagged `nob` draws no flag, where the
    `[:2]` slice gave it a Norwegian one by accident. 26 other codes also lose
    a flag the slice gave them (`arm` drew Egypt's, `fij` drew Finland's,
    `jav` drew Japan's); those were WRONG, so losing them is the fix. This one
    was right, which is what makes it the exception. `nor`, the far commoner
    tag, reduces to `no` and is untouched (asserted below as the contrast).
    `langcodes` 3.5.1 offers no way back to a table key:
    `broader_tags()` returns `["nb", "und"]`, neither of which
    `languages.json` carries either. The two remedies are the
    operator's call and are filed on the roadmap row, not taken here: adding
    `nb`/`nn` to `languages.json` would move `MANIFEST.sha256`, which is a
    `badge_fingerprint` input, and re-render the WHOLE library."""
    assert language_slots(media_info_from_plex(_audio_only_item("nob"))) == []
    assert language_slots(media_info_from_plex(_audio_only_item("nor"))) == [("no", "NO")]


def test_the_flag_loop_and_the_filter_walk_share_one_converter():
    """DRY, asserted rather than intended. Before this change the tree held
    three spellings of the same reduction: `base_language_code` in
    `collections/filters.py`, a copy of its body inlined in C2b's per-stream
    walk, and a `[:2]` slice in the flag loop five lines above it -- which is
    how the two loops in ONE function came to disagree about `swe`. Imported
    inside the test rather than at module scope so that the RED run reports
    THIS test as the only failure for the missing module."""
    from autoposter import lang
    from autoposter.badges import values as badge_values_module
    from autoposter.collections import filters

    assert filters.base_language_code is lang.base_language_code
    assert badge_values_module.base_language_code is lang.base_language_code
