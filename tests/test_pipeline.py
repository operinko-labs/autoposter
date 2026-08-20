from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.plex.client import ResolvedItem
from autoposter.render.pipeline import (
    ART_KINDS_FOR, compute_fingerprint, manual_override_path, title_text_for,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def item(kind="movie", title="Dune: Part Two", season=None, episode=None, root="Dune (2024)"):
    return ResolvedItem(
        rating_key="1", library="Movies", kind=kind, title=title, year=2024,
        season_number=season, episode_number=episode, root_folder=root,
        file_path="/mnt/Media/Movies/Dune (2024)/x.mkv", art_url=None,
        tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )


def test_each_item_kind_maps_to_its_artifacts():
    assert ART_KINDS_FOR["movie"] == ["poster", "background"]
    assert ART_KINDS_FOR["show"] == ["poster", "background"]
    assert ART_KINDS_FOR["season"] == ["season_poster"]
    assert ART_KINDS_FOR["episode"] == ["title_card"]


def test_fingerprint_is_stable_for_identical_inputs():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    assert a == b


def test_fingerprint_changes_with_the_source_image():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "def", ["DUNE"])
    assert a != b


def test_fingerprint_changes_with_the_config_version():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v2", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    assert a != b


def test_fingerprint_changes_with_the_text():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["HEAT"])
    assert a != b


def test_poster_text_is_the_title(config):
    primary, secondary = title_text_for("poster", item(), config)
    assert primary == "Dune: Part Two"
    assert secondary is None


def test_background_has_no_text(config):
    primary, secondary = title_text_for("background", item(), config)
    assert primary is None
    assert secondary is None


def test_season_poster_text_is_the_season_title(config):
    primary, _ = title_text_for(
        "season_poster", item(kind="season", title="Season 2", season=2), config
    )
    assert primary == "Season 2"


def test_title_card_has_episode_title_and_a_numbering_line(config):
    primary, secondary = title_text_for(
        "title_card",
        item(kind="episode", title="Who Is Alive?", season=2, episode=3),
        config,
    )
    assert primary == "Who Is Alive?"
    assert secondary == "Season 2 • Episode 3"


def test_title_card_numbering_uses_unpadded_numbers(config):
    _, secondary = title_text_for(
        "title_card", item(kind="episode", title="X", season=1, episode=1), config
    )
    assert secondary == "Season 1 • Episode 1"


def test_manual_override_is_found_when_present(config, tmp_path):
    config.manual_assets_root = tmp_path
    target = tmp_path / "Movies" / "Dune (2024)"
    target.mkdir(parents=True)
    (target / "poster.jpg").write_bytes(b"x")
    assert manual_override_path(config, item(), "poster") == target / "poster.jpg"


def test_manual_override_is_none_when_absent(config, tmp_path):
    config.manual_assets_root = tmp_path
    assert manual_override_path(config, item(), "poster") is None


def test_publish_keeps_one_previous_generation(tmp_path):
    from autoposter.render.pipeline import _publish

    target = tmp_path / "assets" / "Dune (2024)" / "poster.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    working = tmp_path / "new.jpg"
    working.write_bytes(b"new")
    backup_root = tmp_path / "backup"

    _publish(working, target, backup_root)

    assert target.read_bytes() == b"new"
    assert (backup_root / "Dune (2024)" / "poster.jpg").read_bytes() == b"old"


def test_publish_without_an_existing_asset_writes_no_backup(tmp_path):
    from autoposter.render.pipeline import _publish

    target = tmp_path / "assets" / "Heat (1995)" / "poster.jpg"
    working = tmp_path / "new.jpg"
    working.write_bytes(b"new")
    backup_root = tmp_path / "backup"

    _publish(working, target, backup_root)

    assert target.read_bytes() == b"new"
    assert not backup_root.exists()
