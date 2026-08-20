"""Displayed badge strings.

The formats are Kometa's, not ours. Where a format looks wrong (an unrounded
critic rating, a truncating audience percentage) it is deliberate parity --
see docs/research/kometa-overlays.md section 3.3.
"""
import pytest

from autoposter.badges.values import (
    audience_text,
    commonsense_text,
    critic_text,
    episode_text,
    runtime_text,
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
