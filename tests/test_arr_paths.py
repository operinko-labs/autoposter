"""Translating Plex paths into the paths Radarr and Sonarr see.

Verified live: Plex mounts /mnt/Media (capital M), the services use
/mnt/media (lowercase). Getting this wrong registers a path the service
cannot read.
"""
import pytest

from autoposter.arr.paths import map_path

PLEX = "/mnt/Media"
ARR = "/mnt/media"


def test_the_real_movie_case():
    assert map_path("/mnt/Media/Movies/Dune (2021)", PLEX, ARR) == "/mnt/media/Movies/Dune (2021)"


def test_the_real_show_case():
    assert map_path("/mnt/Media/TV/Severance", PLEX, ARR) == "/mnt/media/TV/Severance"


def test_mapping_is_case_sensitive():
    """/mnt/media and /mnt/Media are different roots; that is the whole point."""
    assert map_path("/mnt/media/Movies/X", PLEX, ARR) is None


def test_a_path_outside_the_plex_root_is_not_guessed():
    """Registering an unreadable path is worse than skipping the item."""
    assert map_path("/somewhere/else/X", PLEX, ARR) is None


def test_a_sibling_root_with_a_shared_prefix_does_not_match():
    assert map_path("/mnt/Media2/Movies/X", PLEX, ARR) is None


def test_trailing_slashes_do_not_double_the_separator():
    assert map_path("/mnt/Media/Movies/X", "/mnt/Media/", "/mnt/media/") == "/mnt/media/Movies/X"


def test_backslashes_are_normalised():
    assert map_path("\\mnt\\Media\\Movies\\X", PLEX, ARR) == "/mnt/media/Movies/X"


def test_the_root_itself_maps_to_the_other_root():
    assert map_path("/mnt/Media", PLEX, ARR) == "/mnt/media"


@pytest.mark.parametrize("bad", [None, ""])
def test_an_empty_path_is_not_mapped(bad):
    assert map_path(bad, PLEX, ARR) is None
