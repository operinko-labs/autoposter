"""Displayed badge strings.

The formats are Kometa's, not ours. Where a format looks wrong (an unrounded
critic rating, a truncating audience percentage) it is deliberate parity --
see docs/research/kometa-overlays.md section 3.3.
"""
import pytest

from autoposter.badges.values import (
    MediaInfo,
    audience_text,
    commonsense_text,
    critic_text,
    episode_text,
    language_slots,
    media_info_from_plex,
    plex_native_ratings,
    runtime_text,
    video_format_text,
)


@pytest.mark.parametrize(
    "ms,expected",
    [
        (4845912, "Runtime: 1h 20m"),
        (3600000, "Runtime: 1h 0m"),
        (5400000, "Runtime: 1h 30m"),
        (600000, "Runtime: 0h 10m"),
        (9000000, "Runtime: 2h 30m"),
    ],
)
def test_runtime_text(ms, expected):
    assert runtime_text(ms) == expected


@pytest.mark.parametrize("missing", [None, 0])
def test_runtime_is_suppressed_without_a_duration(missing):
    assert runtime_text(missing) is None


def test_episode_text_zero_pads_both_numbers():
    assert episode_text(1, 1) == "S01E01"
    assert episode_text(12, 7) == "S12E07"
    assert episode_text(2, 145) == "S02E145"


@pytest.mark.parametrize("season,episode", [(None, 1), (1, None), (None, None)])
def test_episode_text_is_suppressed_when_either_number_is_missing(season, episode):
    assert episode_text(season, episode) is None


@pytest.mark.parametrize(
    "rating,expected",
    [("17", "17+"), ("10", "10+"), ("1", "1+"), ("NR", "NR"), ("nr", "NR")],
)
def test_commonsense_text(rating, expected):
    assert commonsense_text(rating) == expected


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_commonsense_is_suppressed_without_a_rating(bad):
    assert commonsense_text(bad) is None


def test_commonsense_rejects_non_numeric_junk():
    """Ten production shows carry the literal string 'tmdb' in contentRating,
    mass-written by an earlier misconfiguration. Rendering a badge reading
    'tmdb+' would be worse than rendering nothing."""
    assert commonsense_text("tmdb") is None
    assert commonsense_text("TV-MA") is None


def test_critic_text_is_completely_unformatted():
    assert critic_text(9.0) == "9.0"
    assert critic_text(4.9) == "4.9"
    assert critic_text(8.65) == "8.65"


def test_audience_text_multiplies_by_ten_and_truncates():
    assert audience_text(6.3) == "63%"
    assert audience_text(8.65) == "86%"
    assert audience_text(10.0) == "100%"


@pytest.mark.parametrize("fn", [critic_text, audience_text])
def test_ratings_are_suppressed_when_absent(fn):
    assert fn(None) is None


@pytest.mark.parametrize("fn", [critic_text, audience_text])
def test_ratings_are_suppressed_when_zero(fn):
    """Plex reports 0.0 for 'no rating', and a badge reading 0.0 or 0% is
    wrong rather than merely ugly."""
    assert fn(0.0) is None


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/m/Dune (2021)/Dune.2021.BluRay.REMUX.2160p.mkv", "REMUX"),
        ("/m/Heat (1995)/Heat.1995.Blu-Ray.1080p.mkv", "BLU-RAY"),
        ("/m/Heat (1995)/Heat.1995.BD.1080p.mkv", "BLU-RAY"),
        ("/m/Heat (1995)/Heat.1995.HD-DVD.1080p.mkv", "BLU-RAY"),
        ("/m/Show/S01E01 - WEBDL-1080p.mkv", "WEB"),
        ("/m/Show/S01E01.WEBRip.720p.mkv", "WEB"),
        ("/m/Show/S01E01.HDTV.720p.mkv", "HDTV"),
        ("/m/Show/S01E01.HD-TV.720p.mkv", "HDTV"),
        ("/m/Old (1974)/Old.1974.DVD.480p.mkv", "DVD"),
        ("/m/Show/S01E01.SDTV.mkv", "SDTV"),
        ("/m/Cam (2018)/Cam.2018.TELESYNC.mkv", "TELESYNC"),
        ("/m/Cam (2018)/Cam.2018.HDCAM.mkv", "CAM"),
        ("/m/Show/S01E01.mkv", None),
        ("", None),
        (None, None),
    ],
)
def test_video_format_text_reproduces_kometas_filepath_regexes(path, expected):
    """Straight from `video_format.yml`: eight `filepath.regex` filters, the
    overlay key displayed verbatim, and `ignore_blank_results: true` meaning a
    path matching nothing draws no badge at all."""
    info = MediaInfo(None, None, None, None, (), frozenset(), None, None, path)
    assert video_format_text(info) == expected


def test_video_format_prefers_the_higher_weighted_overlay():
    """All eight share `group: quality`, so only one is ever drawn -- the one
    with the highest weight. A remux of a Blu-ray matches both patterns and
    Kometa awards it to REMUX (60) over BLU-RAY (50)."""
    info = MediaInfo(None, None, None, None, (), frozenset(), None, None,
                     "/m/Dune (2021)/Dune.2021.BluRay.REMUX.2160p.mkv")
    assert video_format_text(info) == "REMUX"


class _Rating:
    def __init__(self, image, value):
        self.image = image
        self.value = value


class _RatedItem:
    def __init__(self, user_rating=None, ratings=()):
        if user_rating is not None:
            self.userRating = user_rating
        self.ratings = ratings


def test_plex_native_ratings_reads_all_four_by_image_prefix():
    """Probe section 2.3.6, transcribed verbatim: the rottentomatoes://
    discrimination is by URL SUFFIX (ripe/rotten = critic, anything else =
    audience), not by a separate field."""
    item = _RatedItem(
        user_rating=8.0,
        ratings=(
            _Rating("imdb://image.rating", 7.7),
            _Rating("themoviedb://image.rating", 8.4),
            _Rating("rottentomatoes://image.rating.ripe", 9.0),
            _Rating("rottentomatoes://image.rating.upright", 8.8),
        ),
    )
    assert plex_native_ratings(item) == {
        "user_rating": 8.0,
        "plex_imdb_rating": 7.7,
        "plex_tmdb_rating": 8.4,
        "plex_tomatoes_rating": 9.0,
        "plex_tomatoesaudience_rating": 8.8,
    }


def test_plex_native_ratings_reads_the_rotten_suffix_as_critic_too():
    item = _RatedItem(ratings=(_Rating("rottentomatoes://image.rating.rotten", 3.0),))
    assert plex_native_ratings(item)["plex_tomatoes_rating"] == 3.0


def test_plex_native_ratings_is_total_over_an_unrated_item():
    item = _RatedItem()
    assert plex_native_ratings(item) == {
        "user_rating": None,
        "plex_imdb_rating": None,
        "plex_tmdb_rating": None,
        "plex_tomatoes_rating": None,
        "plex_tomatoesaudience_rating": None,
    }


def test_plex_native_ratings_does_not_trigger_a_partial_object_reload():
    """The reload hazard C1's T1 review flagged (finding C3) for
    OverlayItemView applies identically here: `item` may be the same PARTIAL
    plexapi object apply_badges uploads to, and a plain `getattr` on an
    UNSET attribute trips PlexPartialObject.__getattribute__'s reload
    branch -- one blocking `requests` GET, inline. A fake with no `reload`
    method proves the accessor never calls it."""
    class _PartialNoReload:
        ratings = ()

    assert plex_native_ratings(_PartialNoReload()) == {
        "user_rating": None,
        "plex_imdb_rating": None,
        "plex_tmdb_rating": None,
        "plex_tomatoes_rating": None,
        "plex_tomatoesaudience_rating": None,
    }


def _item_with_streams(audio, subtitles, extra_version=None):
    """A plexapi-shaped stand-in carrying one `<Media>` with one `<Part>`
    whose `streams` list holds the given `(languageCode, streamType)` pairs.
    `extra_version` is a second `(audio, subtitles)` pair, shaped as a SECOND
    `<Media>` child, so the whole-list walk can be exercised.

    Streams are plain objects, not partial ones, which is what the real
    `<Stream>` children are too -- `media_info_from_plex` reads them with a
    plain `getattr` and always has."""
    def _version(audio_codes, subtitle_codes):
        streams = [
            type("S", (), {"streamType": kind, "languageCode": code})()
            for code, kind in list(audio_codes) + list(subtitle_codes)
        ]
        part = type("P", (), {"file": "/movies/X/X.mkv", "streams": streams})()
        return type("M", (), {
            "parts": [part], "videoResolution": "1080",
            "audioCodec": "eac3", "audioChannels": 6, "aspectRatio": 1.78,
        })()

    versions = [_version(audio, subtitles)]
    if extra_version is not None:
        versions.append(_version(*extra_version))
    return type("I", (), {
        "media": versions, "duration": 4845912,
        "seasonNumber": None, "episodeNumber": None,
    })()


def test_the_stream_language_lists_come_from_the_streams_the_badge_pass_walks():
    """C7's A-3, the data half. `media_info_from_plex` already reloads the
    item and holds its `<Media>`/`<Part>`/`<Stream>` children; these two
    fields are one more walk of those SAME already-in-hand objects -- zero
    extra Plex requests, zero extra bytes, which is the cost property the
    whole sub-phase rests on."""
    item = _item_with_streams(
        audio=[("eng", 2), ("fin", 2)],
        subtitles=[("eng", 3), ("swe", 3), ("fin", 3)],
    )
    info = media_info_from_plex(item)
    assert info.audio_stream_languages == ("en", "fi")
    assert info.subtitle_stream_languages == ("en", "sv", "fi")


def test_every_stream_counts_because_kometa_counts_streams_not_languages():
    """**Kometa's rule, transcribed** (`modules/plex.py:2915-2922`): the
    filter value is `test_number.extend([a.language for a in
    part.audioStreams()])` over every part of every `<Media>` -- a flat list
    of STREAMS with no dedupe at all -- and `.count_*` is `len()` of it
    (`plex.py:2931-2932`). So a film whose three English subtitle tracks are
    full, SDH and forced has THREE subtitle-language entries upstream, and
    `subtitle_language.count_gte: 2` fires on it. This plan's first draft
    pinned the opposite (one distinct language) as "what `language_count`
    counts"; the pinned image says otherwise and upstream wins.

    The audio list's third stream carries no `languageCode` at all.
    Kometa's own line is `a.language for a in part.audioStreams()` -- it
    never skips a stream, so an unlabelled one is still one MORE entry, not
    a dropped one; two English tracks plus one unlabelled track is a count
    of THREE, not two."""
    item = _item_with_streams(
        audio=[("eng", 2), ("eng", 2), (None, 2)],
        subtitles=[("eng", 3), ("eng", 3), ("dan", 3), ("eng", 3)],
    )
    info = media_info_from_plex(item)
    assert info.audio_stream_languages == ("en", "en", "")
    assert info.subtitle_stream_languages == ("en", "en", "da", "en")


def test_the_stream_lists_span_every_media_version():
    """The other half of `plex.py:2915-2922`'s shape: the outer loop is `for
    media in item.media`, not `item.media[0]`. A dual-version item whose
    remux carries Finnish audio and whose web-dl carries English has BOTH,
    which is what Kometa counts. (The `audio_languages` field above
    deliberately does NOT do this -- see the next test.)"""
    item = _item_with_streams(
        audio=[("eng", 2)], subtitles=[("eng", 3)],
        extra_version=([("fin", 2)], [("swe", 3)]),
    )
    info = media_info_from_plex(item)
    assert info.audio_stream_languages == ("en", "fi")
    assert info.subtitle_stream_languages == ("en", "sv")


def test_an_item_with_no_subtitle_streams_has_empty_tuples_not_none():
    """`()` rather than `None`, matching `audio_languages`' shape:
    `filters._is_missing` reads an empty sequence as missing for a `tag`
    attribute, and the `.count_*` operators reduce BOTH shapes to zero before
    that rule runs (Concern D), so one shape across all three fields keeps
    `OverlayItemView.get` a two-line branch."""
    item = _item_with_streams(audio=[("eng", 2)], subtitles=[])
    info = media_info_from_plex(item)
    assert info.subtitle_stream_languages == ()
    assert info.audio_stream_languages == ("en",)


@pytest.mark.parametrize("raw", ["und", "mis", "qaa"])
def test_a_code_langcodes_cannot_resolve_passes_through_unchanged_not_truncated(raw):
    """`langcodes.Language.get(raw).language` is `None` for `und` -- Plex's
    own "undetermined" tag -- so the fallback that used to read `or raw[:2]`
    yielded `"un"`: a real-looking but WRONG two-letter code, on the one
    input where the library declines to answer. `mis`/`qaa` are ISO 639-2
    codes `langcodes` already answers with themselves unchanged, pinning
    that the non-broken path stays untouched. The honest rule for a code
    `langcodes` cannot map to ISO 639-1 is the same one `base_language_code`
    (`collections/filters.py:2253`) documents: keep the raw tag unchanged
    rather than truncate it into a different code."""
    item = _item_with_streams(audio=[(raw, 2)], subtitles=[])
    info = media_info_from_plex(item)
    assert info.audio_stream_languages == (raw,)


def test_the_distinct_audio_languages_field_is_untouched_and_still_deduplicates():
    """The guard on the coexistence. `audio_languages` is the FLAG badge's
    input (`language_slots` below it) and wants distinct languages off the
    primary version; `audio_stream_languages` is the FILTER dialect's value
    and wants every stream. Widening the old field instead of adding the new
    one would have drawn a duplicate flag on every multi-track item, so the
    two are pinned apart here rather than left to a reader's assumption."""
    item = _item_with_streams(
        audio=[("eng", 2), ("eng", 2), ("fin", 2)], subtitles=[]
    )
    info = media_info_from_plex(item)
    assert info.audio_languages == ("en", "fi")
    assert info.audio_stream_languages == ("en", "en", "fi")
    assert language_slots(info) == language_slots(
        MediaInfo("1080", "eac3", 6, 4845912, ("en", "fi"), frozenset(), None, None)
    )


def test_the_new_fields_are_defaulted_so_positional_construction_still_works():
    """`MediaInfo` is frozen and every construction site predating this phase
    passes the first nine fields positionally (`tests/test_badge_parity.py`,
    `tests/test_overlay_engine_golden.py`'s ALL_SOULS/EPISODE among them, and
    they are parity-pin files this plan may not edit -- Global Constraint 8).
    A non-defaulted tenth or eleventh field would be a TypeError in all of
    them."""
    info = MediaInfo("1080", "eac3", 6, 4845912, ("en",), frozenset(), None, None)
    assert info.audio_stream_languages == ()
    assert info.subtitle_stream_languages == ()
