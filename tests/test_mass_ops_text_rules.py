"""Rows 39, 40, 42, 43 -- the text rules.

Each row's first assertion is its default pin: the same input with the key
unset must produce exactly what it produced before this phase.
"""
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.plex.client import ResolvedItem
from autoposter.render import pipeline
from autoposter.render.textfit import prepare_text

EXAMPLE = Path("config/autoposter.example.yaml")


def episode(title="Pilot", season=1, number=3):
    return ResolvedItem(
        rating_key="e1", library="TV Shows", kind="episode", title=title,
        year=2020, season_number=season, episode_number=number,
        root_folder="Dark", file_path=None, art_url=None, tmdb_id=1,
        tvdb_id=2, imdb_id="tt1", parent_rating_key="s1",
    )


def _title_card(config, **overrides):
    return config.model_copy(update={
        "artwork": config.artwork.model_copy(update={
            "title_card": config.artwork.title_card.model_copy(update=overrides),
        }),
    })


# --- row 43: season name override -------------------------------------------

def test_season_label_is_unchanged_when_no_override_is_configured():
    config = load_config(EXAMPLE)
    _, secondary = pipeline.title_text_for("title_card", episode(season=0), config)
    assert secondary == "Season 0 • Episode 3"


def test_a_season_name_override_replaces_the_season_half():
    config = _title_card(load_config(EXAMPLE), season_name_overrides={"0": "Specials"})
    _, secondary = pipeline.title_text_for("title_card", episode(season=0), config)
    assert secondary == "Specials • Episode 3"


def test_an_unlisted_season_keeps_the_default_wording():
    config = _title_card(load_config(EXAMPLE), season_name_overrides={"0": "Specials"})
    _, secondary = pipeline.title_text_for("title_card", episode(season=2), config)
    assert secondary == "Season 2 • Episode 3"


# --- row 40: SkipJapTitle ----------------------------------------------------

@pytest.mark.parametrize("title", ["彼女", "キャンプ", "ひらがな"])
def test_cjk_titles_are_detected(title):
    assert pipeline.has_cjk(title) is True


@pytest.mark.parametrize("title", ["Pilot", "L'été", "Загадка", ""])
def test_non_cjk_titles_are_not_detected(title):
    assert pipeline.has_cjk(title) is False


def test_a_cjk_title_is_not_skipped_when_the_key_is_unset():
    config = load_config(EXAMPLE)
    assert pipeline._should_skip_title(config, episode("彼女"), "title_card") is False


def test_a_cjk_title_is_skipped_when_the_key_is_on():
    config = _title_card(load_config(EXAMPLE), skip_cjk_titles=True)
    assert pipeline._should_skip_title(config, episode("彼女"), "title_card") is True


def test_the_cjk_rule_is_independent_of_skip_tba():
    config = _title_card(load_config(EXAMPLE), skip_cjk_titles=True)
    config = config.model_copy(update={"skip_tba": False})
    assert pipeline._should_skip_title(config, episode("彼女"), "title_card") is True
    assert pipeline._should_skip_title(config, episode("TBA"), "title_card") is False


def test_the_cjk_rule_only_applies_to_title_cards():
    config = _title_card(load_config(EXAMPLE), skip_cjk_titles=True)
    assert pipeline._should_skip_title(config, episode("彼女"), "poster") is False


# --- row 42: newline rules ---------------------------------------------------

def _style(**overrides):
    config = load_config(EXAMPLE)
    return config.artwork.title_card.text.model_copy(update=overrides)


def test_prepare_text_is_unchanged_when_no_newline_rule_is_configured():
    assert prepare_text("Fire: Walk With Me", _style(all_caps=False)) == "Fire: Walk With Me"


def test_a_symbol_forces_a_break_after_itself():
    style = _style(all_caps=False, newline_on_symbols=[":"])
    assert prepare_text("Fire: Walk With Me", style) == "Fire:\nWalk With Me"


def test_a_symbol_at_the_very_end_adds_no_trailing_break():
    style = _style(all_caps=False, newline_on_symbols=[":"])
    assert prepare_text("Fire:", style) == "Fire:"


def test_a_newline_word_is_substituted_before_caps_are_applied():
    style = _style(all_caps=True, newline_words={"Walk With": "Walk\nWith"})
    assert prepare_text("Fire Walk With Me", style) == "FIRE WALK\nWITH ME"


def test_quote_normalisation_still_happens_first():
    style = _style(all_caps=False, newline_words={"'em": "'em\n"})
    assert prepare_text("‘em all", style) == "'em\n all"


# --- row 39: SkipLocal*TextAdd ----------------------------------------------

def test_local_text_is_drawn_when_the_key_is_unset():
    config = load_config(EXAMPLE)
    assert pipeline.draw_text_for_local_source(config, "poster") is True


def test_local_text_is_suppressed_when_the_key_is_on():
    config = load_config(EXAMPLE)
    config = config.model_copy(update={
        "artwork": config.artwork.model_copy(update={
            "poster": config.artwork.poster.model_copy(
                update={"skip_local_text_add": True}
            ),
        }),
    })
    assert pipeline.draw_text_for_local_source(config, "poster") is False
