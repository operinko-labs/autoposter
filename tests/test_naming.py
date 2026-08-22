from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.render.naming import (
    asset_path,
    derive_root_folder,
    episode_asset_name,
    season_asset_name,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def test_movie_root_folder_is_the_containing_directory():
    assert derive_root_folder(
        "/mnt/Media/Movies",
        "/mnt/Media/Movies/Dune Part Two (2024)/Dune Part Two Bluray-2160p.mkv",
        is_directory=False,
    ) == "Dune Part Two (2024)"


def test_movie_in_a_nested_quality_folder_uses_its_own_folder():
    assert derive_root_folder(
        "/mnt/Media/Movies",
        "/mnt/Media/Movies/4K/Dune Part Two (2024)/movie.mkv",
        is_directory=False,
    ) == "Dune Part Two (2024)"


def test_show_root_folder_is_the_series_directory():
    assert derive_root_folder(
        "/mnt/Media/TV", "/mnt/Media/TV/Severance", is_directory=True
    ) == "Severance"


def test_brackets_are_preserved_verbatim():
    assert derive_root_folder(
        "/mnt/Media/TV", "/mnt/Media/TV/Solo Leveling (2024) [tvdb-389597]", is_directory=True
    ) == "Solo Leveling (2024) [tvdb-389597]"


def test_trailing_separator_on_library_root_is_tolerated():
    assert derive_root_folder(
        "/mnt/Media/Movies/", "/mnt/Media/Movies/Heat (1995)/heat.mkv", is_directory=False
    ) == "Heat (1995)"


def test_windows_separators_are_handled():
    assert derive_root_folder(
        "D:\\Media\\Movies", "D:\\Media\\Movies\\Heat (1995)\\heat.mkv", is_directory=False
    ) == "Heat (1995)"


def test_path_outside_the_library_root_raises():
    with pytest.raises(ValueError, match="not inside"):
        derive_root_folder("/mnt/Media/Movies", "/somewhere/else/file.mkv", is_directory=False)


def test_season_names_are_zero_padded_to_two_digits():
    assert season_asset_name(1) == "Season01.jpg"
    assert season_asset_name(0) == "Season00.jpg"
    assert season_asset_name(12) == "Season12.jpg"


def test_three_digit_seasons_are_not_truncated():
    assert season_asset_name(100) == "Season100.jpg"


def test_episode_names_use_capital_s_and_e():
    assert episode_asset_name(1, 1) == "S01E01.jpg"
    assert episode_asset_name(2, 3) == "S02E03.jpg"
    assert episode_asset_name(1, 100) == "S01E100.jpg"


def test_poster_path(config):
    assert asset_path(config, "Movies", "Dune Part Two (2024)", "poster") == Path(
        "/assets/Movies/Dune Part Two (2024)/poster.jpg"
    )


def test_background_path(config):
    assert asset_path(config, "TV Shows", "Severance", "background") == Path(
        "/assets/TV Shows/Severance/background.jpg"
    )


def test_season_poster_path(config):
    assert asset_path(config, "TV Shows", "Severance", "season_poster", season_number=2) == Path(
        "/assets/TV Shows/Severance/Season02.jpg"
    )


def test_title_card_path(config):
    assert asset_path(
        config, "TV Shows", "Severance", "title_card", season_number=2, episode_number=3
    ) == Path("/assets/TV Shows/Severance/S02E03.jpg")


def test_season_poster_without_season_number_raises(config):
    with pytest.raises(ValueError, match="season_number"):
        asset_path(config, "TV Shows", "Severance", "season_poster")


def test_title_card_without_episode_number_raises(config):
    with pytest.raises(ValueError, match="episode_number"):
        asset_path(config, "TV Shows", "Severance", "title_card", season_number=2)


def test_flat_layout_when_library_folders_is_false(config):
    config.library_folders = False
    assert asset_path(config, "TV Shows", "Severance", "season_poster", season_number=2) == Path(
        "/assets/Severance_Season02.jpg"
    )
    assert asset_path(config, "Movies", "Heat (1995)", "poster") == Path("/assets/Heat (1995).jpg")


# Characters Plex allows in titles but at least one target filesystem forbids
# (":" and the rest are illegal on Windows/SMB; "/" is illegal everywhere).
_FILESYSTEM_FORBIDDEN = set(':/?*"<>|')


def test_illegal_title_characters_cannot_reach_the_asset_path(config):
    """Phase 5b, roadmap row 15: a Plex title may carry characters no
    filesystem accepts. Asset paths are built from the item's *on-disk*
    folder name (``derive_root_folder``) plus fixed file names -- the title
    is never a path input (``asset_path`` has no title parameter, and
    ``plex/client.py`` raises rather than falling back to the title when no
    media path is inside a library root) -- so a title like this one must
    leave the path exactly as the disk dictates, with no illegal characters
    in any segment below the assets root.
    """
    # The title Plex holds; it never enters the functions under test.
    _plex_title = 'Alien: Romulus? "Uncut" <4K> *HDR* | Part 1/2'

    root_folder = derive_root_folder(
        "/mnt/Media/Movies",
        "/mnt/Media/Movies/Alien Romulus (2024)/Alien.Romulus.2024.mkv",
        is_directory=False,
    )
    path = asset_path(config, "Movies", root_folder, "poster")

    assert path == Path("/assets/Movies/Alien Romulus (2024)/poster.jpg")
    for segment in path.parts[1:]:  # skip the "/" root part
        assert not (set(segment) & _FILESYSTEM_FORBIDDEN), segment


def test_flat_layout_paths_are_also_free_of_illegal_characters(config):
    """The flat layout concatenates the root folder verbatim
    (``naming.py:79-82``); it is fed by the same on-disk name, so it holds
    the same invariant."""
    config.library_folders = False
    path = asset_path(config, "Movies", "Alien Romulus (2024)", "background")
    assert path == Path("/assets/Alien Romulus (2024)_background.jpg")
    for segment in path.parts[1:]:
        assert not (set(segment) & _FILESYSTEM_FORBIDDEN), segment
