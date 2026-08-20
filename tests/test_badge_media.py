"""Media attributes and image-badge filename selection.

Fakes here mirror the shape of a real plexapi Video object closely enough to
exercise the extraction, but the mapping assertions are what matter: a wrong
filename silently renders the wrong badge.
"""
import pytest

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
    def __init__(self, streams):
        self.streams = streams


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
