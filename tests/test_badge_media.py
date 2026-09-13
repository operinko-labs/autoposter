"""Media attributes and image-badge filename selection.

Fakes here mirror the shape of a real plexapi Video object closely enough to
exercise the extraction, but the mapping assertions are what matter: a wrong
filename silently renders the wrong badge.
"""
import pytest

from autoposter.assets import asset_path
from autoposter.badges.compose import BadgeInputs, badge_fingerprint, badge_values
from autoposter.badges.values import (
    AUDIO_CODEC_WEIGHTS,
    MediaInfo,
    RESOLUTION_WEIGHTS,
    _award,
    audio_codec_image,
    language_slots,
    media_info_from_plex,
    resolution_image,
    video_format_text,
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
    audio = FakeStream(2, codec="eac3", language="English", languageCode="eng",
                       channels=6, extendedDisplayTitle="English (EAC3 5.1)")
    sub = FakeStream(3, codec="srt", language="Finnish", languageCode="fin")
    media = FakeMedia([FakePart([video, audio, sub])], videoResolution="1080",
                      audioChannels=6)
    return FakeItem([media], duration=4845912, season=1, episode=1)


def test_media_info_reads_the_attributes_badges_need():
    info = media_info_from_plex(_item())
    assert info.video_resolutions == ("1080",)
    assert info.audio_track_titles == ("English (EAC3 5.1)",)
    assert audio_codec_image(info) == "plus"
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
    assert info.video_resolutions == ()
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
    info = MediaInfo((resolution,), (), None, None, (), flags, None, None)
    assert resolution_image(info) == expected


def test_resolution_image_is_suppressed_for_an_unknown_resolution():
    info = MediaInfo(("240",), (), None, None, (), frozenset(), None, None)
    assert resolution_image(info) is None


@pytest.mark.parametrize(
    "title,expected",
    [("English (EAC3 5.1)", "plus"), ("English (AC3 5.1)", "digital"),
     ("English (TRUEHD 7.1)", "truehd"), ("English (DTS 5.1)", "dts"),
     ("English (AAC Stereo)", "aac"), ("English (FLAC 5.1)", "flac"),
     ("English (OPUS 5.1)", "opus")],
)
def test_audio_codec_image_names(title, expected):
    info = MediaInfo((), (title,), None, None, (), frozenset(), None, None)
    assert audio_codec_image(info) == expected


def test_audio_codec_image_is_suppressed_for_an_unmapped_codec():
    info = MediaInfo((), ("English (Vorbis 5.1)",), None, None, (), frozenset(),
                     None, None)
    assert audio_codec_image(info) is None


def test_language_slots_map_to_flags_and_labels():
    info = MediaInfo((), (), None, None, ("en", "ja"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN"), ("jp", "JA")]


def test_language_slots_order_by_kometa_weight_not_input_order():
    """English (weight 610) outranks Finnish (280) regardless of track order."""
    info = MediaInfo((), (), None, None, ("fi", "en"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN"), ("fi", "FI")]


def test_language_slots_are_capped():
    info = MediaInfo((), (), None, None, ("en", "de", "fr", "es"), frozenset(), None, None)
    assert len(language_slots(info, limit=3)) == 3


def test_unknown_languages_are_dropped_not_guessed():
    info = MediaInfo((), (), None, None, ("en", "zzz"), frozenset(), None, None)
    assert language_slots(info) == [("us", "EN")]


def test_dolby_vision_over_hlg_names_a_file_that_exists():
    """Profile 8.4 streams report both DV and HLG. No `dvhlg` asset is
    vendored for any resolution, so concatenating both would name a
    nonexistent PNG and the badge would fail at render time."""
    info = MediaInfo(("1080",), (), None, None, (), frozenset({"dv", "hlg"}), None, None)
    assert resolution_image(info) == "1080pdv"


def test_dv_hdr_plus_picks_the_most_specific_vendored_variant():
    info = MediaInfo(("4k",), (), None, None, (), frozenset({"dv", "hdr", "plus"}), None, None)
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
                info = MediaInfo((resolution,), (), None, None, (), frozenset(combo),
                                 None, None)
                stem = resolution_image(info)
                assert (root / ("%s.png" % stem)).exists(), (
                    "%s + %s -> %s.png which does not exist" % (resolution, combo, stem)
                )


def _item_with_path(path, video_kw=None):
    video = FakeStream(1, codec="hevc", **(video_kw or {"colorTrc": "smpte2084"}))
    media = FakeMedia([FakePart([video], file=path)], videoResolution="1080",
                      audioChannels=6)
    return FakeItem([media], duration=4845912)


def _version(resolution=None, titles=(), path=None, video_kw=None):
    """One `<Media>` with one `<Part>`: an optional video stream carrying
    `video_kw`, one audio stream per string in `titles` (each tagged `eng`),
    and `path` as the part's file. Those three are everything this phase
    reads. `audioCodec` is deliberately NOT set: nothing reads it any more,
    and a fixture that still carried it would suggest otherwise."""
    streams = []
    if video_kw is not None:
        streams.append(FakeStream(1, codec="hevc", **video_kw))
    for title in titles:
        streams.append(FakeStream(2, languageCode="eng", extendedDisplayTitle=title))
    return FakeMedia([FakePart(streams, file=path)], videoResolution=resolution,
                     audioChannels=6)


def _multi(*versions, duration=None):
    return FakeItem(list(versions), duration=duration)


def test_media_info_carries_the_file_path():
    """The video_format badge and the HDR10+ resolution variant are both
    filepath regexes in Kometa, so the path is a badge input, not diagnostics."""
    info = media_info_from_plex(_item_with_path("/m/Show/S01E01.WEBDL-1080p.mkv"))
    assert info.file_paths == ("/m/Show/S01E01.WEBDL-1080p.mkv",)


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


def test_norwegian_bokmal_and_nynorsk_draw_norways_flag_through_the_alias():
    """Was `..._loses_its_flag_and_that_is_a_decision_not_an_accident`, and it
    is INVERTED here rather than deleted, because the decision it filed on
    roadmap row 243 has now been taken.

    `nob` reduces to `nb` and `nno` to `nn`; `languages.json` is keyed by ISO
    639-1 and carries `no` but neither of those, so both lost their flag --
    `nob` one it used to get by accident off the retired `[:2]` slice, `nno`
    one it never had. `_FLAG_ALIASES` maps both to `no` BEFORE the table
    membership test, so both now draw Norway's flag. Upstream agrees:
    `languages.yml:403` and `:407` at the pinned digest both carry
    `country: no`. `nor`, the far commoner tag, reduces to `no` directly and
    is asserted here unchanged -- it is the control, and if it ever moves the
    alias has been wired in the wrong place.

    The remedy NOT taken is still not taken: `languages.json` gains no keys,
    so `assets/badges/MANIFEST.sha256` does not move and no
    `badge_fingerprint` moves for any item without a Norwegian track."""
    assert language_slots(media_info_from_plex(_audio_only_item("nob"))) == [("no", "NO")]
    assert language_slots(media_info_from_plex(_audio_only_item("nno"))) == [("no", "NO")]
    assert language_slots(media_info_from_plex(_audio_only_item("nor"))) == [("no", "NO")]


def test_the_aliased_norwegian_flags_name_the_same_vendored_asset_as_nor_does():
    """An alias is only worth anything if the stem it produces has a PNG
    behind it. `compose.py::_draw_languages` skips a missing flag file with a
    bare `continue` -- no error, no fallback, no log line -- so an alias
    pointing at an unvendored country would look exactly like the bug it
    fixes. All three codes must resolve to ONE stem and ONE vendored file, not
    to three that merely happen to render."""
    stems = set()
    for code in ("nob", "nno", "nor"):
        slots = language_slots(media_info_from_plex(_audio_only_item(code)))
        assert len(slots) == 1, code
        stems.add(slots[0][0])
    assert stems == {"no"}, stems
    path = asset_path("badges", "images", "flag", "round", "no.png")
    assert path.exists(), "the aliased Norwegian slot names no vendored flag"


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


# --- Kometa's weight tables, transcribed and pinned ------------------------
#
# Read from the pinned image `kometateam/kometa`
# sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a --
# `defaults/overlays/resolution.yml` (sha256 0ee1533e…df3f) and
# `defaults/overlays/audio_codec.yml` (sha256 3a1f1b03…b334). These two tests
# are the transcription itself: they exist so a mistyped weight is a failing
# test rather than a silently wrong badge on somebody's poster.


def test_the_resolution_weight_table_is_kometas_thirty_nine_rows_verbatim():
    """`resolution.yml:255-371`, in the file's own order. Note the shape of
    what is NOT here: 576p and 480p carry no `hlg` row upstream, which is why
    each of those tiers has six rows and not seven, and the last six rows are
    the `key: ""` tail (`:354-371`) that fires for an item whose resolution is
    none of the five tiers."""
    assert RESOLUTION_WEIGHTS == (
        ("4k", "dvhdrplus", 160), ("4k", "dvhdr", 158), ("4k", "plus", 155),
        ("4k", "dv", 150), ("4k", "hlg", 141), ("4k", "hdr", 140), ("4k", "", 130),
        ("1080p", "dvhdrplus", 130), ("1080p", "dvhdr", 128), ("1080p", "plus", 125),
        ("1080p", "dv", 120), ("1080p", "hlg", 111), ("1080p", "hdr", 110),
        ("1080p", "", 100),
        ("720p", "dvhdrplus", 99), ("720p", "dvhdr", 98), ("720p", "plus", 95),
        ("720p", "dv", 90), ("720p", "hlg", 81), ("720p", "hdr", 80),
        ("720p", "", 70),
        ("576p", "dvhdrplus", 69), ("576p", "dvhdr", 68), ("576p", "plus", 65),
        ("576p", "dv", 60), ("576p", "hdr", 50), ("576p", "", 40),
        ("480p", "dvhdrplus", 39), ("480p", "dvhdr", 38), ("480p", "plus", 35),
        ("480p", "dv", 30), ("480p", "hdr", 20), ("480p", "", 10),
        ("", "dvhdrplus", 9), ("", "dvhdr", 8), ("", "plus", 7), ("", "dv", 5),
        ("", "hlg", 2), ("", "hdr", 1),
    )
    assert len(RESOLUTION_WEIGHTS) == 39
    weights = [weight for _key, _alt, weight in RESOLUTION_WEIGHTS]
    assert weights == sorted(weights, reverse=True), (
        "the file's own order is non-increasing in weight; keep it that way so "
        "config order and weight order are the same thing"
    )


def test_the_audio_codec_weight_table_is_kometas_sixteen_rows_verbatim():
    """`audio_codec.yml:104-166`'s weights against `:61-95`'s regexes, in
    weight order. All sixteen are reachable now, where the retired
    `AUDIO_CODECS` map could only ever answer ten of them."""
    assert [(stem, weight) for stem, weight, _pattern in AUDIO_CODEC_WEIGHTS] == [
        ("truehd_atmos", 160), ("dtsx", 150), ("plus_atmos", 140),
        ("dolby_atmos", 130), ("truehd", 120), ("ma", 110), ("flac", 100),
        ("pcm", 90), ("hra", 80), ("plus", 70), ("dtses", 60), ("dts", 50),
        ("digital", 40), ("aac", 30), ("mp3", 20), ("opus", 10),
    ]


def test_the_award_takes_the_strictly_greater_weight_so_a_tie_keeps_config_order():
    """`modules/overlays.py:582-586` at the pinned digest:

        for v in gv:
            if final is None or overlay_groups[gk][v] > overlay_groups[gk][final]:
                final = v

    STRICTLY greater, so equal weights keep whichever was met first -- config
    order. `badges/compose.py:428` already arbitrates definition groups by the
    same rule; `_award` is that rule written once for the value tables. The one
    tie in the oracle is 4K-Dovetail (130) against 1080P-DV-HDR-Plus-Dovetail
    (130), `resolution.yml:273-277`."""
    assert _award([("first", 130), ("second", 130)]) == "first"
    assert _award([("first", 130), ("second", 131)]) == "second"
    assert _award([("only", 1)]) == "only"
    assert _award([]) is None


def test_every_resolution_weight_row_names_a_vendored_png():
    """A row whose `<key><alt>.png` is missing would render as a silently
    absent badge -- `compose.py` skips a missing image file with a bare
    `continue`. 39 rows, 39 files."""
    from pathlib import Path

    root = Path("assets/badges/images/resolution")
    for key, alt, _weight in RESOLUTION_WEIGHTS:
        stem = key + alt
        assert (root / ("%s.png" % stem)).exists(), "%s.png is not vendored" % stem


def test_every_audio_codec_weight_row_names_a_vendored_png():
    """The six stems the retired `AUDIO_CODECS` map could not reach --
    truehd_atmos, dtsx, plus_atmos, dolby_atmos, hra, dtses -- are vendored
    already, which is what makes folding upstream's whole table in a
    transcription rather than an asset project. `compose.py:56` draws from
    `compact`."""
    from pathlib import Path

    root = Path("assets/badges/images/audio_codec/compact")
    for stem, _weight, _pattern in AUDIO_CODEC_WEIGHTS:
        assert (root / ("%s.png" % stem)).exists(), "%s.png is not vendored" % stem


# --- Every `<Media>`, awarded by Kometa's weights (roadmap row 106) ---------


# MEASURED on the cut ref, BEFORE any source edit -- the
# only way a "this did not move" pin can mean anything. Do not recompute them
# from the code under test.
SINGLE_VERSION_FINGERPRINT = "7c21f6ec22f64511e7af1285aed62b03bf07ff8e5d64f751545aa12c83c1db4f"
MEDIA_ZERO_FINGERPRINT = "b7e4f28af0694ddf6568af24ed8f12a393b513f88a4d9d15616c0c2753323f36"


def test_the_resolution_list_spans_every_media_version():
    item = _multi(_version("1080", (), "/m/X/a.mkv"), _version("4k", (), "/m/X/b.mkv"))
    assert media_info_from_plex(item).video_resolutions == ("1080", "4k")


def test_the_audio_track_titles_span_every_media_version():
    """`modules/plex.py:2791-2794` at the pinned digest:

        for media in item.media:
            for part in media.parts:
                values.extend([a.extendedDisplayTitle
                               for a in part.audioStreams()
                               if a.extendedDisplayTitle])

    Every version, listing order, and a falsy title dropped rather than
    carried as an empty string -- upstream's own guard."""
    item = _multi(
        _version("1080", ("English (AAC Stereo)", ""), "/m/X/a.mkv"),
        _version("4k", ("English (DTS-HD MA 5.1)",), "/m/X/b.mkv"),
    )
    assert media_info_from_plex(item).audio_track_titles == (
        "English (AAC Stereo)", "English (DTS-HD MA 5.1)",
    )


def test_the_file_path_list_spans_every_part_of_every_version():
    """Kometa's `filepath` is `item.locations` (`modules/plex.py:2804-2805`),
    which is every file of every version -- so a version split across two
    parts contributes both, not just the first."""
    two_part = FakeMedia(
        [FakePart([], file="/m/X/a1.mkv"), FakePart([], file="/m/X/a2.mkv")],
        videoResolution="1080", audioChannels=6,
    )
    item = _multi(two_part, _version("4k", (), "/m/X/b.mkv"))
    assert media_info_from_plex(item).file_paths == (
        "/m/X/a1.mkv", "/m/X/a2.mkv", "/m/X/b.mkv",
    )


def test_the_hdr_flags_are_the_union_across_every_version():
    item = _multi(
        _version("1080", (), "/m/X/a.mkv", {"DOVIPresent": True, "colorTrc": "bt709"}),
        _version("4k", (), "/m/X/b.mkv", {"colorTrc": "smpte2084"}),
    )
    assert media_info_from_plex(item).hdr_flags == frozenset({"dv", "hdr"})


def test_the_hdr10_plus_marker_is_read_off_every_path_not_just_the_first():
    item = _multi(
        _version("1080", (), "/m/X/a.1080p.mkv", {"colorTrc": "smpte2084"}),
        _version("4k", (), "/m/X/b.2160p.HDR10+.mkv", {"colorTrc": "smpte2084"}),
    )
    assert "plus" in media_info_from_plex(item).hdr_flags


def test_a_dts_hd_ma_4k_second_version_wins_both_badges_off_an_aac_1080p_first():
    """The row's own example, and the whole point of the phase. Ben-Hur is
    badged AAC/1080p/WEB today because Plex happens to list the web-dl first;
    the DTS-HD MA 4K remux at `media[1]` is what Kometa describes."""
    item = _multi(
        _version("1080", ("English (AAC Stereo)",),
                 "/m/Ben-Hur (1959)/Ben-Hur.1959.WEBDL-1080p.mkv"),
        _version("4k", ("English (DTS-HD MA 5.1)",),
                 "/m/Ben-Hur (1959)/Ben-Hur.1959.REMUX-2160p.mkv"),
    )
    info = media_info_from_plex(item)
    assert resolution_image(info) == "4k"
    assert audio_codec_image(info) == "ma"
    assert video_format_text(info) == "REMUX"


def test_the_hdr_flags_are_item_level_so_a_4k_sdr_beside_a_1080p_dv_awards_4kdv():
    """The consequence of the any-of formulation, stated rather than
    stumbled into. No single file here is 4K Dolby Vision -- but
    `resolution.yml:243-251`'s resolution predicate and its
    `has_dolby_vision` filter are two INDEPENDENT item-level reads, so
    `4K-DV-Dovetail` (150) fires and outranks both `4K-Dovetail` (130) and
    `1080P-DV-Dovetail` (120). Upstream's arithmetic, transcribed."""
    item = _multi(
        _version("4k", (), "/m/X/X.2160p.mkv", {"colorTrc": "bt709"}),
        _version("1080", (), "/m/X/X.1080p.DV.mkv",
                 {"DOVIPresent": True, "colorTrc": "bt709"}),
    )
    info = media_info_from_plex(item)
    assert info.hdr_flags == frozenset({"dv"})
    assert resolution_image(info) == "4kdv"


def test_the_one_hundred_thirty_tie_is_unreachable_through_the_item_level_any_of():
    """`4K-Dovetail` (`resolution.yml:273-274`) and
    `1080P-DV-HDR-Plus-Dovetail` (`:276-277`) both weigh 130 -- the oracle's
    one tie. It cannot be reached from a real item: the flags that let the
    1080p row fire are item-level, so the moment they hold,
    `4K-DV-HDR-Plus-Dovetail` (160) fires too. The comparator's tie rule is
    pinned directly in
    `test_the_award_takes_the_strictly_greater_weight_so_a_tie_keeps_config_order`;
    this pins the item-level consequence, so a future refactor that reaches
    for an unstable sort has both halves to answer to."""
    item = _multi(
        _version("4k", (), "/m/X/X.2160p.mkv", {"colorTrc": "bt709"}),
        _version("1080", (), "/m/X/X.1080p.DV.HDR10Plus.mkv",
                 {"DOVIPresent": True, "colorTrc": "smpte2084"}),
    )
    assert resolution_image(media_info_from_plex(item)) == "4kdvhdrplus"


def test_video_format_takes_the_highest_weighted_label_across_every_path():
    """`video_format.yml:64-65` filters on `filepath.regex` with
    `plex_all: true` -- `item.locations`, every path. A web-dl beside a remux
    is REMUX (60) over WEB (40)."""
    item = _multi(
        _version("1080", (), "/m/X/X.WEBDL-1080p.mkv"),
        _version("4k", (), "/m/X/X.REMUX-2160p.mkv"),
    )
    assert video_format_text(media_info_from_plex(item)) == "REMUX"


def test_hlg_outranks_hdr_because_the_weight_table_says_so():
    """The retired `_HDR_SUFFIXES` tuple checked `hdr` before `hlg`;
    `resolution.yml:288-292` weighs 1080P-HLG at 111 and 1080P-HDR at 110. The
    weight table governs now. One video stream cannot report two transfer
    curves, so this needs two versions -- which is also the only way the
    disagreement was ever reachable, and why no single-version item moves
    because of it."""
    item = _multi(
        _version("1080", (), "/m/X/X.a.mkv", {"colorTrc": "smpte2084"}),
        _version("1080", (), "/m/X/X.b.mkv", {"colorTrc": "arib-std-b67"}),
    )
    info = media_info_from_plex(item)
    assert info.hdr_flags == frozenset({"hdr", "hlg"})
    assert resolution_image(info) == "1080phlg"


def test_576p_and_480p_carry_no_hlg_row_so_an_hlg_flag_draws_the_plain_badge():
    """Upstream ships seven rows for 4k, 1080p and 720p and only six for 576p
    and 480p -- there is no 576P-HLG or 480P-HLG overlay
    (`resolution.yml:318-353`). This repository vendors `576phlg.png` and
    `480phlg.png` anyway; they are now unreachable, which is upstream's answer
    and not an oversight here."""
    for plex_value, expected in (("576", "576p"), ("480", "480p")):
        item = _multi(_version(plex_value, (), "/m/X/X.mkv",
                               {"colorTrc": "arib-std-b67"}))
        assert resolution_image(media_info_from_plex(item)) == expected


def test_an_unknown_resolution_with_a_dolby_vision_stream_draws_the_tail_badge():
    """`resolution.yml:354-371`'s six `key: ""` rows fire for every item, so
    an item Plex reports as none of the five tiers still gets a badge when it
    carries an alt: `resolution/dv.png`, not nothing. With no flags at all it
    still gets nothing, which is what
    `test_resolution_image_is_suppressed_for_an_unknown_resolution` above
    pins."""
    item = _multi(_version("240", (), "/m/X/X.mkv",
                           {"DOVIPresent": True, "colorTrc": "bt709"}))
    assert resolution_image(media_info_from_plex(item)) == "dv"


def test_an_atmos_track_title_outranks_the_plain_truehd_badge():
    """One of the six stems the retired ten-entry map could never reach.
    `audio_codec.yml:64-65`'s truehd_atmos regex needs both tokens anywhere in
    the string; 160 beats truehd's 120."""
    item = _multi(_version("4k", ("English (TRUEHD 7.1) Atmos",), "/m/X/X.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) == "truehd_atmos"


def test_a_dts_x_title_outranks_dts_hd_ma():
    """`audio_codec.yml:66-67`'s dtsx (150) against `:74-75`'s ma (110). Plex
    reports `dca-ma` for both, which is why the old identifier map could not
    tell them apart at all."""
    item = _multi(_version("4k", ("English (DTS-HD MA 7.1) DTS-X",), "/m/X/X.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) == "dtsx"


def test_a_codec_token_in_the_file_path_counts_when_the_track_title_is_silent():
    """`audio_codec.yml:98-100` is a LIST of two filters --
    `audio_track_title.regex` OR `filepath.regex` -- and either satisfies the
    overlay."""
    item = _multi(_version("1080", ("English",), "/m/X/X.1080p.TrueHD.Atmos.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) == "truehd_atmos"


def test_a_stereo_track_title_draws_the_aac_badge_because_upstream_says_so():
    """`audio_codec.yml:90-91`'s aac regex is `\\b(aac|stereo|2\\.0)\\b`:
    upstream reads "Stereo" and "2.0" as AAC, whatever the actual codec is.
    Transcribed, not corrected -- the same posture `video_format_text`'s
    HD-DVD-is-BLU-RAY case has held since row 14."""
    item = _multi(_version("1080", ("English (Vorbis Stereo)",), "/m/X/X.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) == "aac"


def test_no_codec_token_anywhere_draws_no_audio_codec_badge():
    """The one direction in which this phase REMOVES a badge, pinned so it
    cannot happen quietly. The retired map read Plex's `media.audioCodec` and
    so always answered; Kometa reads track titles and file paths
    (`audio_codec.yml:98-100`) and answers nothing when neither carries a
    codec token. Rare -- Plex's `extendedDisplayTitle` carries the codec for
    every stream it knows -- but real, and counted by the affected-count query
    in the roadmap row before this ships."""
    item = _multi(_version("1080", ("English",), "/m/X/X.1080p.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) is None


def test_a_single_version_item_keeps_its_badge_values_and_fingerprint_to_the_byte():
    """The storm guard's load-bearing half. `badge_values`' `resolution`,
    `audio_codec` and `video_format` keys are `badge_fingerprint` inputs, so
    anything this change moves re-badges and re-uploads once. A ONE-version
    item must move NOTHING: its resolution set is one tier, its flag union is
    one version's flags, its path list is one path, and its track title
    resolves to the same stem the Plex identifier did. Both literals were
    captured on the pre-change code, not re-derived from the
    function under test -- a re-derivation would pass even if the formula and
    the pin moved together."""
    item = _multi(
        _version("1080", ("English (EAC3 5.1)",), "/m/Show/S01E01.WEBDL-1080p.mkv",
                 {"colorTrc": "bt709"}),
        duration=4845912,
    )
    info = media_info_from_plex(item)
    values = badge_values("poster", BadgeInputs(media=info,
                                                video_format=video_format_text(info)))
    assert values == {
        "resolution": "1080p", "audio_codec": "plus", "video_format": "WEB",
        "runtimes": "Runtime: 1h 20m", "languages": "us:EN",
    }
    assert badge_fingerprint("base-fp", "poster", values, "manifest-sha") == (
        SINGLE_VERSION_FINGERPRINT
    )


def test_the_two_version_item_moves_its_fingerprint_off_the_media_zero_one():
    """The movement, stated honestly and pinned in both directions. The
    left-hand dict is what the shipped code produces for this item today
    (captured on the pre-change code) and is what is in the database; the right-hand
    one is what replaces it. Only the badge layer redraws: `badge_fingerprint`
    is deliberately separate from the base `fingerprint`, so no source art is
    re-fetched and no base is re-composited. Measured ceiling for the whole
    library: 50 movies and 224 episodes carry more than one `<Media>`."""
    item = _multi(
        _version("1080", ("English (AAC Stereo)",),
                 "/m/Ben-Hur (1959)/Ben-Hur.1959.WEBDL-1080p.mkv"),
        _version("4k", ("English (DTS-HD MA 5.1)",),
                 "/m/Ben-Hur (1959)/Ben-Hur.1959.REMUX-2160p.mkv"),
        duration=4845912,
    )
    info = media_info_from_plex(item)
    values = badge_values("poster", BadgeInputs(media=info,
                                                video_format=video_format_text(info)))
    assert values == {
        "resolution": "4k", "audio_codec": "ma", "video_format": "REMUX",
        "runtimes": "Runtime: 1h 20m", "languages": "us:EN",
    }
    assert badge_fingerprint("base-fp", "poster", values, "manifest-sha") != (
        MEDIA_ZERO_FINGERPRINT
    )
    assert badge_fingerprint(
        "base-fp", "poster",
        {"resolution": "1080p", "audio_codec": "aac", "video_format": "WEB",
         "runtimes": "Runtime: 1h 20m", "languages": "us:EN"},
        "manifest-sha",
    ) == MEDIA_ZERO_FINGERPRINT
