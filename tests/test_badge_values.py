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
