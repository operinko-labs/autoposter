from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

SECRET_NAMES = (
    "DATABASE_URL", "PLEX_TOKEN", "TMDB_TOKEN",
    "TVDB_APIKEY", "FANART_APIKEY", "WEBHOOK_SECRET",
)


def test_example_config_loads():
    cfg = load_config(EXAMPLE)
    assert cfg.assets_root == Path("/assets")
    assert cfg.workers == 5
    assert cfg.providers.order == ["TMDB", "TVDB", "Fanart"]
    assert cfg.artwork.poster.language_order == ["xx", "en", "fi"]
    assert "Muskarit" in cfg.plex.excluded_libraries


def test_poster_text_style_matches_posterizarr():
    style = load_config(EXAMPLE).artwork.poster.text
    assert style.min_point_size == 83
    assert style.max_point_size == 250
    assert style.max_width == 1200
    assert style.max_height == 485
    assert style.text_offset == "+300"
    assert style.all_caps is True
    assert style.add_stroke is False


def test_background_text_is_disabled():
    assert load_config(EXAMPLE).artwork.background.text.add_text is False


def test_title_card_has_two_text_blocks():
    tc = load_config(EXAMPLE).artwork.title_card
    assert tc.text.text_offset == "-150"
    assert tc.episode_text.text_offset == "+100"
    assert tc.season_label == "Season"
    assert tc.episode_label == "Episode"


def test_text_offset_without_sign_is_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace('"+300"', '"300"'), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="explicit sign"):
        load_config(bad)


def test_language_order_rejects_bad_codes(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("[xx, en, fi]", "[xx, english]", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="two-letter"):
        load_config(bad)


def test_secrets_come_from_env(monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.setenv("AUTOPOSTER_" + name, "value-" + name)
    secrets = Secrets.from_env()
    assert secrets.tmdb_token == "value-TMDB_TOKEN"
    assert secrets.database_url == "value-DATABASE_URL"


def test_missing_secret_names_the_variable(monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.setenv("AUTOPOSTER_" + name, "x")
    monkeypatch.delenv("AUTOPOSTER_TMDB_TOKEN")
    with pytest.raises(RuntimeError, match="AUTOPOSTER_TMDB_TOKEN"):
        Secrets.from_env()


def test_imdb_refresh_defaults_from_the_example_config():
    operations = load_config(EXAMPLE).operations
    assert operations.imdb_refresh_hours == 24
    assert operations.imdb_refresh_enabled is True


def test_config_version_changes_with_content(tmp_path):
    a = load_config(EXAMPLE)
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("min_point_size: 83", "min_point_size: 84"),
        encoding="utf-8",
    )
    assert a.version != load_config(changed).version
