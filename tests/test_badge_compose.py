"""Composition and fingerprinting."""
import io
from pathlib import Path

from PIL import Image

from autoposter.badges.compose import (
    BadgeInputs,
    _variable_values,
    badge_fingerprint,
    badge_values,
    compose,
)
from autoposter.badges.values import MediaInfo
from autoposter.overlays.schema import OverlayDefinition

ORACLE = Path("tests/fixtures/oracle")


def _inputs(**over):
    base = dict(
        media=MediaInfo("1080", "eac3", 6, 4845912, ("en",), frozenset(), 1, 1),
        critic_rating=4.9, audience_rating=6.3, content_rating="17", video_format="WEB",
    )
    base.update(over)
    return BadgeInputs(**base)


def test_badge_values_for_a_poster():
    values = badge_values("poster", _inputs())
    assert values["resolution"] == "1080p"
    assert values["audio_codec"] == "plus"
    assert values["critic"] == "4.9"
    assert values["audience"] == "63%"
    assert values["commonsense"] == "17+"
    assert values["video_format"] == "WEB"
    assert values["runtimes"] == "Runtime: 1h 20m"


def test_episode_info_appears_only_on_title_cards():
    assert "episode_info" not in badge_values("poster", _inputs())
    assert badge_values("title_card", _inputs())["episode_info"] == "S01E01"


def test_absent_values_are_omitted_entirely():
    values = badge_values("poster", _inputs(critic_rating=None, content_rating=None))
    assert "critic" not in values
    assert "commonsense" not in values
    assert "audience" in values


def test_fingerprint_changes_when_a_displayed_value_changes():
    a = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m")
    b = badge_fingerprint("abc", "poster", {"critic": "8.7"}, "m")
    assert a != b


def test_fingerprint_changes_when_the_base_changes():
    a = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m")
    b = badge_fingerprint("xyz", "poster", {"critic": "8.6"}, "m")
    assert a != b


def test_fingerprint_changes_when_a_badge_asset_changes():
    """Replacing a badge PNG must invalidate every render that uses it."""
    a = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "manifest-1")
    b = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "manifest-2")
    assert a != b


def test_fingerprint_is_stable_across_key_ordering():
    a = badge_fingerprint("abc", "poster", {"critic": "8.6", "audience": "63%"}, "m")
    b = badge_fingerprint("abc", "poster", {"audience": "63%", "critic": "8.6"}, "m")
    assert a == b


def test_compose_produces_a_webp_at_the_poster_canvas():
    data = compose(ORACLE / "All_Souls_base_no_overlay.jpg", "poster", _inputs())
    image = Image.open(io.BytesIO(data))
    assert image.format == "WEBP"
    assert image.size == (1000, 1500)


def test_compose_produces_a_landscape_webp_for_a_title_card():
    data = compose(ORACLE / "8OO10C_S01E01_base_no_overlay.jpg", "title_card", _inputs())
    image = Image.open(io.BytesIO(data))
    assert image.size == (1920, 1080)


def test_compose_stamps_the_overlay_exif_marker():
    """Kometa writes this tag and reads it back to detect already-overlaid
    images; anything consuming our output should see the same marker."""
    data = compose(ORACLE / "All_Souls_base_no_overlay.jpg", "poster", _inputs())
    assert Image.open(io.BytesIO(data)).getexif().get(0x04BC) == "overlay"


def test_variable_values_merges_ratings_in():
    inputs = _inputs(ratings={"mdb_average_rating": 6.5, "plex_imdb_rating": 7.7})
    values = _variable_values("", inputs)
    assert values["mdb_average_rating"] == 6.5
    assert values["plex_imdb_rating"] == 7.7


def test_variable_values_drops_a_none_valued_rating():
    inputs = _inputs(ratings={"mdb_rating": None, "user_rating": 8.0})
    values = _variable_values("", inputs)
    assert "mdb_rating" not in values
    assert values["user_rating"] == 8.0


def test_an_empty_ratings_dict_changes_nothing():
    """Global Constraint 6: the default is an empty dict, and an empty dict
    merged in is the pre-C2a output exactly."""
    with_default = _variable_values("", _inputs())
    with_empty = _variable_values("", _inputs(ratings={}))
    assert with_default == with_empty


def test_unchanged_rating_values_do_not_move_the_fingerprint():
    """MEDIUM finding, preflight review, ruled for correctness: `ratings`
    folds each definition's USED rating-token VALUE into the fingerprint too
    -- guarded the same way `outcomes` is (Global Constraint 6). The same
    resolved values passed twice must produce the same digest."""
    stamp = OverlayDefinition(name="text(<<mdb_rating>>)")
    ratings = {"mdb_rating": 6.5}
    a = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m", [stamp], ratings=ratings)
    b = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m", [stamp], ratings=dict(ratings))
    assert a == b


def test_a_changed_rating_value_used_by_a_definition_moves_the_fingerprint():
    """The staleness gap this closes: a definition that NAMES a rating token
    must re-badge when that token's resolved value moves, even though the
    config (`definitions`) itself did not change at all -- otherwise an
    already-uploaded item's rating text goes stale forever."""
    stamp = OverlayDefinition(name="text(<<mdb_rating>>)")
    before = badge_fingerprint(
        "abc", "poster", {"critic": "8.6"}, "m", [stamp], ratings={"mdb_rating": 6.5},
    )
    after = badge_fingerprint(
        "abc", "poster", {"critic": "8.6"}, "m", [stamp], ratings={"mdb_rating": 7.0},
    )
    assert before != after


def test_a_definition_naming_no_rating_token_is_byte_identical_regardless_of_ratings():
    """The non-empty guard, the storm pin's own discipline extended: a
    config whose definitions name no rating token -- every config predating
    this phase included -- must not move at all, no matter what `ratings`
    carries."""
    stamp = OverlayDefinition(name="text(HELLO)")
    without_ratings = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m", [stamp])
    with_ratings = badge_fingerprint(
        "abc", "poster", {"critic": "8.6"}, "m", [stamp],
        ratings={"mdb_rating": 6.5, "user_rating": 8.0},
    )
    assert without_ratings == with_ratings
