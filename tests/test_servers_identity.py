import pytest

from autoposter.servers.identity import identity_key, identity_key_for, parent_identity_key_for
from media_server_doubles import resolved


def test_movie_keys_on_first_provider_then_basename():
    assert identity_key("movie", tmdb_id=603, tvdb_id=None, imdb_id="tt0133093",
                        season_number=None, episode_number=None,
                        file_path="/a/The Matrix (1999)/The Matrix (1999) - 4K.mkv") \
        == "movie:tmdb:603::The Matrix (1999) - 4K.mkv"


def test_a_4k_and_hd_copy_stay_apart_by_basename():
    a = identity_key("movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None, file_path="/x/m - 4K.mkv")
    b = identity_key("movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None, file_path="/y/m - 1080p.mkv")
    assert a != b


def test_the_same_file_on_two_mounts_is_one_item():
    a = identity_key("movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None, file_path="/plex/Movies/m.mkv")
    b = identity_key("movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None, file_path="/jellyfin/Movies/m.mkv")
    assert a == b


def test_show_and_season_carry_no_file():
    assert identity_key("show", tmdb_id=None, tvdb_id=71663, imdb_id=None, season_number=None,
                        episode_number=None, file_path="/tv/The Simpsons") == "show:tvdb:71663::"
    assert identity_key("season", tmdb_id=None, tvdb_id=71663, imdb_id=None, season_number=2,
                        episode_number=None, file_path=None) == "season:tvdb:71663:s2:"


def test_episode_keys_on_series_id_and_coordinates_only():
    # An episode never carries its own file_path -- the Plex resolver rebinds
    # its match container to the SHOW for a season/episode intent and reads
    # file_path off THAT (plex/client.py), which is always None -- so an
    # episode's key, like a season's, never includes a file field, even when
    # a caller passes one.
    assert identity_key("episode", tmdb_id=None, tvdb_id=71663, imdb_id=None, season_number=2,
                        episode_number=3, file_path="/tv/s02e03.mkv") == "episode:tvdb:71663:s2e3:"


def test_precedence_is_tmdb_then_tvdb_then_imdb():
    k = identity_key("movie", tmdb_id=1, tvdb_id=2, imdb_id="tt3", season_number=None,
                     episode_number=None, file_path="f.mkv")
    assert k.startswith("movie:tmdb:1:")
    k = identity_key("movie", tmdb_id=None, tvdb_id=2, imdb_id="tt3", season_number=None,
                     episode_number=None, file_path="f.mkv")
    assert k.startswith("movie:tvdb:2:")


def test_no_provider_falls_back_to_path_then_legacy():
    # Five-field shape per spec §4.2 -- kind:provider:id:coords:basename, with
    # provider="path" and an EMPTY id slot -- not a shortened four-field form.
    # (An earlier draft of this test encoded the four-field form; that was wrong.)
    assert identity_key("movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
                        episode_number=None, file_path="/x/only.mkv") == "movie:path:::only.mkv"
    assert identity_key("movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
                        episode_number=None, file_path=None, legacy="123") == "movie:legacy:plex:123"


def test_path_fallback_still_carries_coordinates():
    """The empty id slot -- and, for an episode, the always-empty file slot
    too -- must not swallow the coords slot next to them."""
    assert identity_key("episode", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=2,
                        episode_number=3, file_path="/tv/s02e03.mkv",
                        root_folder="The Simpsons (1989)") == "episode:path::s2e3:The Simpsons (1989)"


def test_a_windows_path_yields_the_same_basename_as_posix():
    a = identity_key("movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None, file_path="/plex/Movies/Title (2020)/Title (2020).mkv")
    b = identity_key("movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None,
                     file_path="C:\\Media\\Movies\\Title (2020)\\Title (2020).mkv")
    assert a == b


def test_a_provider_less_show_keys_on_its_root_folder():
    assert identity_key("show", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
                        episode_number=None, file_path=None,
                        root_folder="The Simpsons (1989)") == "show:path:::The Simpsons (1989)"


def test_a_provider_less_season_keys_on_coordinates_and_root_folder():
    assert identity_key("season", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=2,
                        episode_number=None, file_path=None,
                        root_folder="The Simpsons (1989)") == "season:path::s2:The Simpsons (1989)"


def test_a_provider_less_movie_keys_on_root_folder_and_basename():
    assert identity_key("movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
                        episode_number=None, file_path="/m/Unmatched/u.mkv",
                        root_folder="Unmatched") == "movie:path:::Unmatched/u.mkv"
    # No root_folder at all: falls back to the bare basename, unchanged.
    assert identity_key("movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
                        episode_number=None, file_path="/m/Unmatched/u.mkv",
                        root_folder=None) == "movie:path:::u.mkv"


def test_two_same_named_provider_less_movies_in_different_folders_stay_apart():
    a = identity_key("movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None, file_path="/a/movie.mkv", root_folder="Dune (2024)")
    b = identity_key("movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None, file_path="/b/movie.mkv", root_folder="Arrival (2016)")
    assert a != b


def test_a_provider_less_show_with_an_empty_root_folder_and_no_legacy_still_raises():
    with pytest.raises(ValueError):
        identity_key("show", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
                     episode_number=None, file_path=None, root_folder="")


def test_a_show_with_a_tvdb_id_ignores_root_folder():
    assert identity_key("show", tmdb_id=None, tvdb_id=71663, imdb_id=None, season_number=None,
                        episode_number=None, file_path=None,
                        root_folder="The Simpsons (1989)") == "show:tvdb:71663::"


def test_a_movie_with_a_tmdb_id_keeps_the_bare_basename_ignoring_root_folder():
    assert identity_key("movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
                        episode_number=None, file_path="/a/The Matrix (1999)/m.mkv",
                        root_folder="The Matrix (1999)") == "movie:tmdb:603::m.mkv"


def test_identity_key_for_a_resolved_item_and_its_parent():
    import dataclasses
    ep = resolved("plex", "9", kind="episode", tmdb_id=None, tvdb_id=71663, season_number=2,
                  episode_number=3, file_path="/tv/s02e03.mkv")
    ep = dataclasses.replace(ep, parent_tvdb_id=71663)
    # No file field: an episode never carries its own file_path.
    assert identity_key_for(ep) == "episode:tvdb:71663:s2e3:"
    # An episode's parent is its own SEASON, not the show directly (two-hop
    # model: config/impact.py, api/routes.py's detail breadcrumb).
    assert parent_identity_key_for(ep) == "season:tvdb:71663:s2:"
