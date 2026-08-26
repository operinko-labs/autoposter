"""The sort matrices, checked as a transcription rather than as behaviour.

Nothing here computes anything. The tables are Kometa's, copied PRE-ENCODED --
``%3Adesc`` and ``%2C`` as they stand in ``modules/plex.py`` -- because
re-encoding them here would be a second implementation of a thing that already
has exactly one correct answer, and the ways to get it subtly wrong (encoding
the comma but not the colon, encoding twice) all produce a URL Plex answers
with a plausible, differently-ordered set.
"""
import pytest

from autoposter.collections.search_sorts import (
    KNOWN_SORT_NAMES,
    MOVIE_SORTS,
    SHOW_SORTS,
    SORT_TYPES,
    SortNotAvailable,
    require_sort_for_libtype,
    sort_argument,
)


def test_the_matrices_have_the_recorded_sizes():
    """modules/plex.py:604-636 and :637-667. Fifteen name pairs plus ``random``
    for movies, fourteen plus ``random`` for shows."""
    assert len(MOVIE_SORTS) == 31
    assert len(SHOW_SORTS) == 29


def test_every_name_but_random_is_a_directional_pair():
    for table in (MOVIE_SORTS, SHOW_SORTS):
        directional = [name for name in table if name != "random"]
        assert len(directional) % 2 == 0
        stems = {name.rsplit(".", 1)[0] for name in directional}
        for stem in stems:
            assert f"{stem}.asc" in table
            assert f"{stem}.desc" in table


def test_every_descending_value_carries_the_encoded_colon():
    """``%3Adesc``, not ``:desc``. The watchlist table (plex.py:788-797) uses
    the UNENCODED form for a different endpoint, which is exactly the sort of
    near-miss a hand-retyped table picks up."""
    for table in (MOVIE_SORTS, SHOW_SORTS):
        for name, value in table.items():
            if name.endswith(".desc"):
                assert "%3Adesc" in value, name
                assert ":desc" not in value.replace("%3Adesc", ""), name


def test_the_weird_rows_are_copied_and_not_guessed():
    # resolution sorts by mediaHeight, NOT by videoResolution -- Plex has no
    # sortable resolution field, and the tag values ("4k", "1080", "sd") do not
    # order lexically anyway.
    assert MOVIE_SORTS["resolution.asc"] == "mediaHeight"
    assert MOVIE_SORTS["resolution.desc"] == "mediaHeight%3Adesc"
    # random has no direction at all, in either table.
    assert MOVIE_SORTS["random"] == "random"
    assert SHOW_SORTS["random"] == "random"
    assert "random.asc" not in MOVIE_SORTS
    # release and originally_available are two names for one wire value.
    assert MOVIE_SORTS["release.asc"] == MOVIE_SORTS["originally_available.asc"]
    # the show table's episode_* sorts reach the EPISODE libtype's field.
    assert SHOW_SORTS["episode_added.asc"] == "episode.addedAt"
    # unplayed on a show sorts by the UNVIEWED LEAF COUNT -- how many episodes
    # are unwatched -- which is a number, not the boolean the search attribute
    # of the same name asks about.
    assert SHOW_SORTS["unplayed.asc"] == "unviewedLeafCount"


def test_the_sort_types_carry_kometas_type_numbers():
    """modules/plex.py:779-787. ``type=1``/``type=2`` are what make the query
    return movies/shows rather than the section's default."""
    assert SORT_TYPES["movie"].key == 1
    assert SORT_TYPES["show"].key == 2
    assert SORT_TYPES["movie"].default_sort == "title.asc"
    assert SORT_TYPES["show"].default_sort == "title.asc"


def test_an_absent_sort_by_uses_the_type_default():
    """Every URL Kometa builds carries a sort (builder.py:4140-4141), so ours
    must too -- an omitted sort is not an omitted parameter."""
    assert sort_argument("movie") == "titleSort"
    assert sort_argument("show", ()) == "titleSort"


def test_several_sorts_join_with_an_encoded_comma():
    assert (
        sort_argument("movie", ["critic_rating.desc", "title.asc"])
        == "rating%3Adesc%2CtitleSort"
    )


def test_a_show_only_sort_refuses_on_a_movie_library_naming_both():
    with pytest.raises(SortNotAvailable) as error:
        require_sort_for_libtype("movie", ["episode_added.desc"])
    message = str(error.value)
    assert "episode_added.desc" in message
    assert "movie" in message
    assert "libraries:" in message


def test_a_movie_only_sort_refuses_on_a_show_library():
    with pytest.raises(SortNotAvailable):
        require_sort_for_libtype("show", ["duration.asc"])


def test_a_sort_both_tables_carry_is_accepted_on_both():
    require_sort_for_libtype("movie", ["added.desc"])
    require_sort_for_libtype("show", ["added.desc"])


def test_known_sort_names_is_the_union():
    assert KNOWN_SORT_NAMES == frozenset(MOVIE_SORTS) | frozenset(SHOW_SORTS)
    assert "duration.asc" in KNOWN_SORT_NAMES      # movie only
    assert "episode_added.asc" in KNOWN_SORT_NAMES  # show only
    assert "nonsense.asc" not in KNOWN_SORT_NAMES
