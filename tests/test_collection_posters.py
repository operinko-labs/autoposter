"""Choosing a collection's poster.

The URLs here were each confirmed to exist upstream; they are not guesses. A
wrong URL 404s and the collection quietly keeps no poster, which is harder to
spot than an error. The original rows were checked by fetching them and
seeing a 200; the fourteen ceremonies added later were checked against the
recursive file listing of ``Kometa-Team/Default-Images`` at ``master``, which
answers the same question for 5 028 paths in one request.
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
        ("award_static", "oscars:best_picture_winner",
         f"{BASE}/award/oscars/best_picture_winner.jpg"),
        ("award_static", "oscars:best_director_winner",
         f"{BASE}/award/oscars/best_director_winner.jpg"),
        ("award_year", "oscars:2026", f"{BASE}/award/oscars/winner/2026.jpg"),
        # The ceremony's folder is Kometa's ``golden``, not its event key --
        # each of these was fetched and returned 200, like every other row.
        ("award_static", "golden_globes:best_picture_winner",
         f"{BASE}/award/golden/best_picture_winner.jpg"),
        ("award_static", "golden_globes:best_director_winner",
         f"{BASE}/award/golden/best_director_winner.jpg"),
        ("award_year", "golden_globes:2026", f"{BASE}/award/golden/winner/2026.jpg"),
        # The other fourteen ceremonies. Each path below was checked against
        # the full file listing of Kometa-Team/Default-Images at ``master``
        # (the git trees API, recursive, 5 028 entries under ``award/``) --
        # every static stem and every year image the committed fixtures cover.
        # The year folder is NOT the static folder for three of the sixteen:
        # only the Oscars, the Golden Globes and the Emmys keep year images
        # under ``winner/``, and ``nfr`` has no ``winner/`` folder at all, so
        # the older single-derivation rule would have 404'd for it.
        ("award_static", "bafta:winner", f"{BASE}/award/bafta/winner.jpg"),
        ("award_year", "bafta:2026", f"{BASE}/award/bafta/2026.jpg"),
        ("award_static", "berlinale:winner", f"{BASE}/award/berlinale/winner.jpg"),
        ("award_year", "berlinale:2026", f"{BASE}/award/berlinale/2026.jpg"),
        ("award_static", "cannes:winner", f"{BASE}/award/cannes/winner.jpg"),
        ("award_year", "cannes:2026", f"{BASE}/award/cannes/2026.jpg"),
        ("award_static", "cesar:winner", f"{BASE}/award/cesar/winner.jpg"),
        ("award_year", "cesar:2026", f"{BASE}/award/cesar/2026.jpg"),
        ("award_static", "choice:winner", f"{BASE}/award/choice/winner.jpg"),
        ("award_year", "choice:2026", f"{BASE}/award/choice/2026.jpg"),
        # ``emmy`` is Kometa's ``emmys`` folder, and it keeps ``winner/``.
        ("award_static", "emmy:winner", f"{BASE}/award/emmys/winner.jpg"),
        ("award_year", "emmy:2025", f"{BASE}/award/emmys/winner/2025.jpg"),
        ("award_static", "nfr:all_time", f"{BASE}/award/nfr/all_time.jpg"),
        ("award_year", "nfr:2025", f"{BASE}/award/nfr/2025.jpg"),
        ("award_static", "pca:winner", f"{BASE}/award/pca/winner.jpg"),
        ("award_year", "pca:2022", f"{BASE}/award/pca/2022.jpg"),
        ("award_static", "razzie:winner", f"{BASE}/award/razzie/winner.jpg"),
        ("award_year", "razzie:2026", f"{BASE}/award/razzie/2026.jpg"),
        ("award_static", "sag:winner", f"{BASE}/award/sag/winner.jpg"),
        ("award_year", "sag:2026", f"{BASE}/award/sag/2026.jpg"),
        ("award_static", "spirit:winner", f"{BASE}/award/spirit/winner.jpg"),
        ("award_year", "spirit:2026", f"{BASE}/award/spirit/2026.jpg"),
        ("award_static", "sundance:grand_jury_winner",
         f"{BASE}/award/sundance/grand_jury_winner.jpg"),
        ("award_year", "sundance:2026", f"{BASE}/award/sundance/2026.jpg"),
        ("award_static", "tiff:winner", f"{BASE}/award/tiff/winner.jpg"),
        ("award_year", "tiff:2025", f"{BASE}/award/tiff/2025.jpg"),
        ("award_static", "venice:winner", f"{BASE}/award/venice/winner.jpg"),
        ("award_year", "venice:2025", f"{BASE}/award/venice/2025.jpg"),
        ("chart", "IMDb Popular", f"{BASE}/chart/color/IMDb%20Popular.jpg"),
        ("chart", "IMDb Top 250", f"{BASE}/chart/color/IMDb%20Top%20250.jpg"),
        ("chart", "IMDb Lowest Rated", f"{BASE}/chart/color/IMDb%20Lowest%20Rated.jpg"),
        ("content_rating", "17", f"{BASE}/content_rating/cs/17.jpg"),
        ("content_rating", "1", f"{BASE}/content_rating/cs/1.jpg"),
        ("content_rating_other", "", f"{BASE}/content_rating/cs/NR.jpg"),
        # Style-scoped since C4, the award kinds' idiom: the style half names
        # the folder, so the shipped default and a picked style are the same
        # code path rather than a hardcoded ``orig`` and an exception to it.
        ("separator", "orig:content_rating",
         f"{BASE}/separators/orig/content_rating.jpg"),
        ("separator", "sand:chart", f"{BASE}/separators/sand/chart.jpg"),
    ],
)
def test_hosted_urls_match_the_verified_paths(kind, key, expected):
    assert hosted_poster_url(kind, key) == expected


def test_only_chart_keys_are_url_encoded():
    """Encoding the award year would be harmless; encoding its slash would
    not, so the encoding is deliberately per-kind rather than blanket."""
    assert "%2F" not in hosted_poster_url("award_year", "oscars:2026")
    assert "/winner/2026.jpg" in hosted_poster_url("award_year", "oscars:2026")


def test_an_unknown_kind_yields_no_url():
    assert hosted_poster_url("something_else", "x") is None


@pytest.mark.parametrize("key", ["filmfare:best_picture_winner", "2026", ""])
def test_an_award_key_without_a_known_event_yields_no_url(key):
    """A ceremony ``Default-Images`` has no folder for keeps no poster.

    The alternative is worse than a 404: every unmapped event would otherwise
    fall back to some other ceremony's folder and be given the wrong award's
    artwork, which looks like a working collection.
    """
    assert hosted_poster_url("award_static", key) is None
    assert hosted_poster_url("award_year", key) is None


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
