"""The <<variable>> text grammar.

Banked in `.superpowers/sdd/p-overlay-grammar-probe.md` sections 2.1 to 2.4.
Cited by section throughout.
"""
import pytest

from autoposter.overlays.variables import (
    DOUBLE_MODS,
    RATING_SOURCES,
    SINGLE_MODS,
    VAR_MODS,
    UnresolvedVariable,
    format_value,
    literal_of,
    render_text,
    tokens_in,
)


def test_the_text_form_is_unwrapped_and_anything_else_is_not_text():
    """Probe section 2.1: a text overlay's name is `text(LITERAL)`."""
    assert literal_of("text(Runtime: <<runtimeH>>h)") == "Runtime: <<runtimeH>>h"
    assert literal_of("resolution") is None


def test_a_bare_token_is_found_with_an_empty_modifier():
    """Probe section 2.1."""
    assert tokens_in("<<title>>") == [("title", "")]


def test_a_two_character_modifier_wins_over_a_one_character_one():
    """Probe section 2.3: double_mods are tried first, which is why W and WU
    do not collide."""
    assert tokens_in("<<season_number>>WU") == [("season_number", "WU")]
    assert tokens_in("<<season_number>>W") == [("season_number", "W")]


def test_a_modifier_illegal_for_its_variable_is_not_a_token():
    """Probe section 2.3: the modifier table is per variable class, not global.
    `U` uppercases a string; it is not in runtime's set, so `<<runtime>>U` is
    the bare runtime token followed by a literal U."""
    assert tokens_in("<<runtime>>U") == [("runtime", "")]


def test_literal_text_outside_the_tokens_is_left_alone():
    """Probe section 2.1: everything not inside << >> passes through."""
    assert tokens_in("Runtime: <<runtimeH>>h <<runtimeM>>m") == [
        ("runtime", "H"), ("runtime", "M"),
    ]


def test_the_date_bracket_form_is_its_own_token():
    """Probe section 2.3: originally_available takes `[FORMAT]`."""
    assert tokens_in("<<originally_available[%Y]>>") == [
        ("originally_available", "[%Y]"),
    ]


@pytest.mark.parametrize(
    "var,mod,value,expected",
    [
        # Probe section 2.3, runtime row: total minutes / hours part / minutes part.
        ("runtime", "", 80, "80"),
        ("runtime", "H", 80, "1"),
        ("runtime", "M", 80, "20"),
        ("total_runtime", "H", 80, "1"),
        # Probe section 2.3, float row: /10 as given, x10 as int, /10 without a
        # trailing .0, /2 to one decimal.
        ("audience_rating", "", 6.3, "6.3"),
        ("audience_rating", "%", 6.3, "63"),
        ("audience_rating", "#", 8.0, "8"),
        ("audience_rating", "#", 8.6, "8.6"),
        ("audience_rating", "/", 6.4, "3.2"),
        # Probe section 2.3, string row.
        ("title", "", "The Ark", "The Ark"),
        ("title", "U", "The Ark", "THE ARK"),
        ("title", "L", "The Ark", "the ark"),
        ("title", "P", "the ark", "The Ark"),
        # Probe section 2.3, integer row: zero-padded to 2 and to 3.
        ("season_number", "", 1, "1"),
        ("season_number", "0", 1, "01"),
        ("season_number", "00", 1, "001"),
        ("episode_number", "0", 12, "12"),
    ],
)
def test_format_value_matches_the_banked_modifier_table(var, mod, value, expected):
    assert format_value(var, mod, value) == expected


def test_the_words_modifiers_spell_a_number_out():
    """Probe section 2.3, integer row: W via num2words, WU uppercased, WL
    lowercased."""
    assert format_value("season_number", "W", 3) == "three"
    assert format_value("season_number", "WU", 3) == "THREE"
    assert format_value("season_number", "WL", 3) == "three"


def test_the_percent_modifier_truncates_rather_than_rounds():
    """Probe section 2.3 gives `%` as x10-as-int. int() truncates, and
    `badges/values.py`'s own docstring records that production really does
    truncate here."""
    assert format_value("audience_rating", "%", 6.39) == "63"


def test_the_date_format_is_applied_through_strftime():
    """Probe section 2.3, originally_available row."""
    import datetime

    assert format_value(
        "originally_available", "[%Y-%m]", datetime.date(2023, 4, 5)
    ) == "2023-04"
    assert format_value(
        "originally_available", "", datetime.date(2023, 4, 5)
    ) == "2023-04-05"


def test_render_text_substitutes_every_token_and_keeps_the_literal():
    """Probe section 2.4 step 4."""
    assert render_text(
        "Runtime: <<runtimeH>>h <<runtimeM>>m", {"runtime": 80}
    ) == "Runtime: 1h 20m"


def test_render_text_substitutes_the_date_bracket_form():
    """Probe section 2.4 step 4: the bracket form goes through re.sub because
    the format string may contain regex metacharacters."""
    import datetime

    assert render_text(
        "(<<originally_available[%Y]>>)",
        {"originally_available": datetime.date(2023, 4, 5)},
    ) == "(2023)"


def test_a_variable_with_no_value_raises_rather_than_rendering_a_hole():
    """Probe section 2.4: an unresolved variable adds the overlay to the
    `unresolved` set -- the item is skipped, it is not drawn with a gap."""
    with pytest.raises(UnresolvedVariable):
        render_text("<<critic_rating>>", {})


def test_the_rating_vocabulary_is_the_banked_twenty_seven():
    """Probe section 2.2: 27 external rating sources. The NAMES are grammar and
    ship here; the per-source API fetches are row 100's data half and do not."""
    assert len(RATING_SOURCES) == 27
    assert "imdb_rating" in RATING_SOURCES
    assert "trakt_user_rating" in RATING_SOURCES
    assert "anidb_average_rating" in RATING_SOURCES
    # The three Plex-native ratings are float vars but NOT rating sources --
    # they read off the item rather than through an external call.
    for native in ("audience_rating", "critic_rating", "user_rating"):
        assert native not in RATING_SOURCES
        assert native in VAR_MODS


def test_the_modifier_sets_are_the_deduplicated_union():
    """Probe section 2.3: single_mods and double_mods across every variable."""
    assert SINGLE_MODS == {"H", "L", "M", "%", "#", "/", "U", "P", "W", "0", "["}
    assert DOUBLE_MODS == {"WU", "WL", "00"}
