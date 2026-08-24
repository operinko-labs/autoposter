from pathlib import Path

import pytest

from autoposter.config.loader import build_config, load_config, read_config_document
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


def test_notifications_retry_count_of_zero_is_rejected_at_load(tmp_path):
    """A notifier built from ``retry_count: 0`` would attempt nothing and
    report every send as failed; that has to fail config validation, not
    ship."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace(
            "retry_count: 3", "retry_count: 0", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="retry_count"):
        load_config(bad)


def test_notifications_timeout_of_zero_is_rejected_at_load(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace(
            "timeout_seconds: 10 # per-attempt HTTP timeout",
            "timeout_seconds: 0 # per-attempt HTTP timeout",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="timeout_seconds"):
        load_config(bad)


def test_imdb_refresh_defaults_from_the_example_config():
    operations = load_config(EXAMPLE).operations
    assert operations.imdb_refresh_hours == 6
    assert operations.imdb_refresh_enabled is True


def test_artwork_modes_round_trips_from_the_example_config():
    modes = load_config(EXAMPLE).artwork_modes
    assert modes.plex_backup_root == Path("/plexbackup")
    # Every Plex-writing mode is dry run by default, the cleanup.apply posture.
    assert modes.restore_apply is False
    assert modes.reset_apply is False
    assert modes.revert_apply is False
    assert modes.logo_apply is False
    assert modes.logo_revert_apply is False
    assert modes.max_changes == 500
    assert modes.max_change_share == 0.25


def test_artwork_modes_defaults_when_the_section_is_absent():
    """Attached via default_factory, so a config document that omits the
    section still gets the full defaults -- the same guarantee every other
    optional section (operations, badges, cleanup, ...) already carries."""
    document = read_config_document(EXAMPLE)
    document.pop("artwork_modes")
    modes = build_config(document).artwork_modes
    assert modes.plex_backup_root == Path("/plexbackup")
    assert modes.max_changes == 500


def test_config_version_changes_with_content(tmp_path):
    a = load_config(EXAMPLE)
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("min_point_size: 83", "min_point_size: 84"),
        encoding="utf-8",
    )
    assert a.version != load_config(changed).version


# --- config.version covers render-affecting settings ONLY --------------------
#
# It is the first component of every render fingerprint, so anything it covers
# invalidates all ~16,000 stored fingerprints when it changes. The documented
# cutover in deploy/README.md has the operator edit `adopt.apply` twice; when
# this was a hash of the raw file bytes, that edit alone re-rendered the whole
# library through the provider ladder.


def _variant(tmp_path, name, old, new):
    path = tmp_path / name
    text = EXAMPLE.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is no longer in the example config"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return load_config(path)


def test_version_is_stable_across_two_loads_of_identical_content(tmp_path):
    copy = tmp_path / "copy.yaml"
    copy.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    assert load_config(EXAMPLE).version == load_config(copy).version


def test_flipping_adopt_apply_does_not_change_the_version(tmp_path):
    """The cutover procedure's own edit must not strand every adopted row."""
    changed = _variant(
        tmp_path, "adopt.yaml",
        "apply: false # dry run by default: produce the report",
        "apply: true # dry run by default: produce the report",
    )
    assert changed.adopt.apply is True
    assert changed.version == load_config(EXAMPLE).version


def test_adding_a_comment_does_not_change_the_version(tmp_path):
    changed = _variant(
        tmp_path, "commented.yaml", "assets_root: /assets",
        "# a note the operator left for themselves\nassets_root: /assets",
    )
    assert changed.version == load_config(EXAMPLE).version


def test_retuning_the_drift_batch_size_does_not_change_the_version(tmp_path):
    changed = _variant(tmp_path, "drift.yaml", "drift_batch_size: 500", "drift_batch_size: 250")
    assert changed.scheduler.drift_batch_size == 250
    assert changed.version == load_config(EXAMPLE).version


def test_changing_an_artwork_setting_does_change_the_version(tmp_path):
    changed = _variant(tmp_path, "artwork.yaml", "output_quality: 92%", "output_quality: 88%")
    assert changed.artwork.output_quality == "88%"
    assert changed.version != load_config(EXAMPLE).version


def test_load_config_and_build_config_are_one_construction_path():
    """The overrides layer builds its ``Config`` from a merged dict rather
    than from the file, so validation and the ``version`` derivation must live
    in a piece both callers share -- not be duplicated into the new path,
    where it could drift and silently start versioning merged configs
    differently from file-only ones."""
    from_file = load_config(EXAMPLE)
    from_document = build_config(read_config_document(EXAMPLE))
    assert from_document.model_dump(mode="json") == from_file.model_dump(mode="json")
    assert from_document.version == from_file.version


def test_changing_an_asset_root_does_change_the_version(tmp_path):
    changed = _variant(
        tmp_path, "roots.yaml",
        "overlays_root: /app/assets/overlays",
        "overlays_root: /app/assets/overlays-v2",
    )
    assert changed.version != load_config(EXAMPLE).version
