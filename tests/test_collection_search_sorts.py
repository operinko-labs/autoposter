"""The sort matrices, checked as a transcription rather than as behaviour.

Nothing here computes anything. The tables are Kometa's, copied PRE-ENCODED --
``%3Adesc`` and ``%2C`` as they stand in ``modules/plex.py`` -- because
re-encoding them here would be a second implementation of a thing that already
has exactly one correct answer, and the ways to get it subtly wrong (encoding
the comma but not the colon, encoding twice) all produce a URL Plex answers
with a plausible, differently-ordered set.
"""
from pathlib import Path

import pytest

from autoposter.collections.search_sorts import (
    EPISODE_SORTS,
    KNOWN_SORT_NAMES,
    MOVIE_SORTS,
    SEASON_SORTS,
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
            # And the two halves are the SAME field. Without this, a transposed
            # or duplicated hand-copy -- ``"year.asc": "year%3Adesc"`` -- passes
            # every other test in this file, which is precisely this table's
            # threat model. Scoped to movie/show on purpose: it does NOT hold
            # for the deferred matrices (plex.py:668-778). Their multi-term
            # values put ``%3Adesc`` on an INNER term and leave the rest of the
            # tie-break chain alone -- ``episode_sorts["show.desc"]`` is
            # ``show.titleSort%3Adesc%2Cseason.index%3AnullsLast%2C...``, six
            # terms, one of which changed. Task 6 must not extend this line.
            assert table[f"{stem}.desc"] == table[f"{stem}.asc"] + "%3Adesc", stem


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


def test_a_movie_show_refusal_names_library_kinds_only():
    """Task 2 review I-1: ``others`` used to draw from all four ``SORT_TYPES``,
    so ``duration.asc`` (a MOVIE and an EPISODE sort) refused on a show library
    named "episode or movie" and told the operator to narrow `libraries:` onto
    an episode library, which does not exist. The library branch only ever
    names library kinds now."""
    with pytest.raises(SortNotAvailable) as error:
        require_sort_for_libtype("show", ["duration.asc"])
    message = str(error.value)
    assert "is a movie sort" in message
    assert "episode" not in message
    assert "only targets movie libraries" in message


def test_a_sort_both_tables_carry_is_accepted_on_both():
    require_sort_for_libtype("movie", ["added.desc"])
    require_sort_for_libtype("show", ["added.desc"])


def test_known_sort_names_is_the_union():
    assert KNOWN_SORT_NAMES == (
        frozenset(MOVIE_SORTS) | frozenset(SHOW_SORTS)
        | frozenset(SEASON_SORTS) | frozenset(EPISODE_SORTS)
    )
    assert "duration.asc" in KNOWN_SORT_NAMES       # movie only
    assert "episode_added.asc" in KNOWN_SORT_NAMES  # show only
    assert "season.asc" in KNOWN_SORT_NAMES         # season/episode only
    assert "nonsense.asc" not in KNOWN_SORT_NAMES


def test_the_sort_tables_cover_exactly_the_library_types_the_table_knows():
    """``SORT_TYPES`` used to restate ``filters.ITEM_KINDS`` and search-tail
    E-2 makes that equality the wrong claim: a season and an episode are SEARCH
    levels, not library kinds, and ``ITEM_KINDS`` must stay two. The property
    the equality was protecting survives as the containment -- a third kind
    joining ``ITEM_KINDS`` would still reach ``sort_argument`` as a bare
    ``KeyError`` on the ``SORT_TYPES`` lookup, which is the unexplained-failure
    outcome ``require_sort_for_libtype``'s docstring exists to prevent -- and
    the closed set of search levels is pinned beside it, so a fifth appearing
    is a deliberate edit here rather than a table nobody checks."""
    from autoposter.collections.filters import ITEM_KINDS

    assert set(ITEM_KINDS) <= set(SORT_TYPES)
    assert set(ITEM_KINDS) == {"movie", "show"}
    assert set(SORT_TYPES) == {"movie", "show", "season", "episode"}


def test_asking_a_direction_of_random_is_a_refusal():
    """The module docstring's claim, pinned. ``random`` is the one name with no
    ``.asc``/``.desc`` pair, so ``random.desc`` is not a sort at all -- and the
    refusal must come from the gate rather than from a ``KeyError`` inside
    ``sort_argument``."""
    with pytest.raises(SortNotAvailable) as error:
        require_sort_for_libtype("movie", ["random.desc"])
    assert "random.desc" in str(error.value)

    require_sort_for_libtype("movie", ["random"])


def test_the_deferred_matrices_are_genuinely_absent():
    """The docstring claim, narrowed by E-2. plex.py:668-778 holds season,
    episode, artist, album and track; the first two arrive with the
    ``builder_level`` search levels that reach them and the last three do not,
    so no name of theirs may have leaked in. Every name below is real -- it
    exists in one of those three tables and in none of ours."""
    for name in (
        "played.asc",        # artist_sorts, album_sorts, track_sorts
        "album_artist.asc",  # album_sorts, track_sorts
        "popularity.asc",    # track_sorts
    ):
        assert name not in KNOWN_SORT_NAMES
    # And the two that are no longer deferred, asserted from the other side so
    # this test cannot pass by the matrices having quietly vanished again.
    assert "season.asc" in KNOWN_SORT_NAMES
    assert "show.asc" in KNOWN_SORT_NAMES


ORACLE_DRIVER = Path(__file__).resolve().parent / "oracle" / "9b" / "kometa_build_filter.py"


def test_the_appended_matrices_are_the_vendored_drivers_own_literals():
    """Not a second transcription. The driver holds Kometa v2.4.8's
    ``season_sorts``/``episode_sorts`` pasted from ``modules/plex.py:668-715``;
    this module holds the same 44 pairs. They are compared BY VALUE, reading
    the driver as text -- the idiom
    ``test_the_oracles_vocabulary_fixture_matches_this_files_copy`` uses for
    ``CHOICES``, and the only coupling that does not break the driver's
    isolation from ``src``. Without it the two are two hand-copies of one
    source, which is exactly the failure mode this file exists to exclude.
    """
    import ast

    tree = ast.parse(ORACLE_DRIVER.read_text(encoding="utf-8"))
    literals = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("season_sorts", "episode_sorts")
    }
    assert literals["season_sorts"] == dict(SEASON_SORTS)
    assert literals["episode_sorts"] == dict(EPISODE_SORTS)


def test_the_appended_matrices_have_the_recorded_sizes():
    """modules/plex.py:668-715. Nine names for a season search, thirty-five for
    an episode one -- a count, so a truncated paste fails here rather than
    producing a table that is right about everything it contains."""
    assert len(SEASON_SORTS) == 9
    assert len(EPISODE_SORTS) == 35


def test_the_appended_matrices_carry_commas_the_shipped_two_do_not():
    """The module docstring's restated claim. ``sort_argument`` joins several
    sorts with ``%2C``; the movie and show values carry no comma of their own,
    and the season/episode values DO -- a chained tie-break stored inside one
    value. The join keeps working because concatenating a value that already
    contains ``%2C`` is still a valid ``sort=`` term, and that is the whole
    reason the docstring's parenthesis had to be rewritten rather than deleted.
    """
    for table in (MOVIE_SORTS, SHOW_SORTS):
        assert not any("%2C" in value for value in table.values())
    assert any("%2C" in value for value in SEASON_SORTS.values())
    assert any("%2C" in value for value in EPISODE_SORTS.values())


def test_the_four_sort_types_carry_kometas_type_numbers_and_defaults():
    """modules/plex.py:779-787. The ``type=`` byte and the sort an omitted
    ``sort_by`` becomes, for all four search levels.

    If either default disagrees with the vendored driver's own
    ``sort_types`` entries, the DRIVER is right and this assertion is the bug:
    record it and use the transcribed value."""
    assert SORT_TYPES["movie"].key == 1
    assert SORT_TYPES["show"].key == 2
    assert SORT_TYPES["season"].key == 3
    assert SORT_TYPES["episode"].key == 4
    assert SORT_TYPES["movie"].default_sort == "title.asc"
    assert SORT_TYPES["show"].default_sort == "title.asc"
    assert SORT_TYPES["season"].default_sort == "season.asc"
    assert SORT_TYPES["episode"].default_sort == "title.asc"


def test_an_episode_search_refuses_a_show_sort_with_the_level_sentence():
    """Facts C6, the sort trap. ``episode_added.desc`` is a SHOW sort
    (SHOW_SORTS), so the first thing that happens to an operator who adds
    ``builder_level: episode`` to a working E-1 definition is that their
    existing ``sort_by`` is refused. The generic message would have said "this
    pass is running against a episode library", which is not a thing -- the
    search level is not a library type. Fixed sentence, naming the sort name
    and the level token and nothing else (row 213)."""
    with pytest.raises(SortNotAvailable) as error:
        require_sort_for_libtype("episode", ["episode_added.desc"])
    message = str(error.value)
    assert "sorts show rows" in message
    assert "`builder_level: episode` search needs an episode sort" in message
    assert "library" not in message


def test_a_season_search_refuses_a_name_no_table_carries():
    """The other branch of the same sentence: a sort that is not a sort
    anywhere still has to name the level rather than a library."""
    with pytest.raises(SortNotAvailable) as error:
        require_sort_for_libtype("season", ["nonsense.asc"])
    message = str(error.value)
    assert "is not a sort at all" in message
    assert "`builder_level: season` search needs a season sort" in message
    assert "library" not in message
