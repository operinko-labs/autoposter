"""Choosing a collection's poster.

The URLs here were each fetched and confirmed to return 200; they are not
guesses. A wrong URL 404s and the collection quietly keeps no poster, which
is harder to spot than an error.
"""
import pytest

from autoposter.collections.posters import (
    PosterPathRefused,
    hosted_poster_url,
    local_poster_path,
    poster_override_target,
)

BASE = "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"


@pytest.mark.parametrize(
    "kind,key,expected",
    [
        ("award_static", "best_picture_winner", f"{BASE}/award/oscars/best_picture_winner.jpg"),
        ("award_static", "best_director_winner", f"{BASE}/award/oscars/best_director_winner.jpg"),
        ("award_year", "2026", f"{BASE}/award/oscars/winner/2026.jpg"),
        ("chart", "IMDb Popular", f"{BASE}/chart/color/IMDb%20Popular.jpg"),
        ("chart", "IMDb Top 250", f"{BASE}/chart/color/IMDb%20Top%20250.jpg"),
        ("chart", "IMDb Lowest Rated", f"{BASE}/chart/color/IMDb%20Lowest%20Rated.jpg"),
        ("content_rating", "17", f"{BASE}/content_rating/cs/17.jpg"),
        ("content_rating", "1", f"{BASE}/content_rating/cs/1.jpg"),
        ("content_rating_other", "", f"{BASE}/content_rating/cs/NR.jpg"),
        ("separator", "content_rating", f"{BASE}/separators/orig/content_rating.jpg"),
    ],
)
def test_hosted_urls_match_the_verified_paths(kind, key, expected):
    assert hosted_poster_url(kind, key) == expected


def test_only_chart_keys_are_url_encoded():
    """Encoding the award year would be harmless; encoding its slash would
    not, so the encoding is deliberately per-kind rather than blanket."""
    assert "%2F" not in hosted_poster_url("award_year", "2026")
    assert "/winner/2026.jpg" in hosted_poster_url("award_year", "2026")


def test_an_unknown_kind_yields_no_url():
    assert hosted_poster_url("something_else", "x") is None


def test_a_local_poster_is_found(tmp_path, config_factory):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / "Movies" / "IMDb Top 250"
    folder.mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(b"x")
    assert local_poster_path(config, "Movies", "IMDb Top 250") == folder / "poster.jpg"


def test_extensions_are_tried_in_order(tmp_path, config_factory):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / "Movies" / "IMDb Top 250"
    folder.mkdir(parents=True)
    (folder / "poster.png").write_bytes(b"x")
    (folder / "poster.webp").write_bytes(b"x")
    assert local_poster_path(config, "Movies", "IMDb Top 250").name == "poster.png"


def test_no_local_poster_returns_none(tmp_path, config_factory):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    assert local_poster_path(config, "Movies", "IMDb Top 250") is None


def test_the_flat_layout_is_honoured(tmp_path, config_factory):
    config = config_factory(assets_root=str(tmp_path), library_folders=False)
    (tmp_path / "IMDb Top 250.jpg").write_bytes(b"x")
    assert local_poster_path(config, "Movies", "IMDb Top 250").name == "IMDb Top 250.jpg"


# --- row 124: the title is interpolated into the path, so contain it --------
#
# ``ManagedCollection.title`` reaches ``_poster_candidates`` verbatim. Titles
# come from operator config and from collections adopted out of Plex, so a
# title carrying ``..`` -- or an absolute path, which pathlib's join rule turns
# into the absolute path itself -- would steer both the reconciler's READ and
# the manual endpoint's WRITE outside ``assets_root``.


@pytest.mark.parametrize(
    "library,title",
    [
        ("Movies", "../../etc"),
        ("Movies", "../../sibling"),
        ("../..", "IMDb Top 250"),
        ("Movies", "/etc"),
    ],
)
def test_a_title_that_escapes_the_assets_root_is_refused(
    tmp_path, config_factory, library, title
):
    root = tmp_path / "assets"
    root.mkdir()
    config = config_factory(assets_root=str(root), library_folders=True)

    with pytest.raises(PosterPathRefused):
        local_poster_path(config, library, title)
    with pytest.raises(PosterPathRefused):
        poster_override_target(config, library, title)


def test_the_flat_layout_is_contained_too(tmp_path, config_factory):
    """The flat layout interpolates the title straight under the root, so it
    escapes with one fewer ``..`` than the foldered one."""
    root = tmp_path / "assets"
    root.mkdir()
    config = config_factory(assets_root=str(root), library_folders=False)

    with pytest.raises(PosterPathRefused):
        poster_override_target(config, "Movies", "../escaped")


def test_a_symlinked_title_is_refused_on_its_target(tmp_path, config_factory):
    """``realpath`` on both sides, not ``normpath`` or a ``startswith``: a
    directory inside the root that points out of it is the case a lexical
    check waves through. Skipped where the OS will not make the symlink."""
    root = tmp_path / "assets"
    (root / "Movies").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "Movies" / "Escaped").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create the symlink")
    config = config_factory(assets_root=str(root), library_folders=True)

    with pytest.raises(PosterPathRefused):
        poster_override_target(config, "Movies", "Escaped")
