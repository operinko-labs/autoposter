"""Common Sense bucket derivation.

The expected values below were taken from the live production server, not
invented -- see the validation table in assets/collections/PROVENANCE.md.
"""
from autoposter.collections.buckets import derive_buckets, load_table


def test_the_table_covers_all_eighteen_buckets():
    table = load_table()
    assert table["include"] == [str(n) for n in range(1, 19)]
    assert len(table["addons"]) == 18


def test_bucket_seventeen_reproduces_the_live_movies_filter():
    """Derived against the Movies library's real rating set, this is exactly
    what the live 'Age 17+ Movies' smart collection filters on."""
    present = {"17", "R", "TV-14", "TV-MA", "PG", "G"}
    buckets = {b.key: b for b in derive_buckets(present, "Movie")}
    assert sorted(buckets["17"].values) == ["17", "R", "TV-14", "TV-MA"]


def test_a_bare_key_is_only_included_when_the_library_carries_it():
    with_bare = {b.key: b for b in derive_buckets({"17", "R"}, "Movie")}
    without_bare = {b.key: b for b in derive_buckets({"R"}, "Movie")}
    assert "17" in with_bare["17"].values
    assert "17" not in without_bare["17"].values
    assert "R" in without_bare["17"].values


def test_candidates_absent_from_the_library_are_omitted():
    buckets = {b.key: b for b in derive_buckets({"R"}, "Movie")}
    assert buckets["17"].values == ("R",)


def test_titles_and_summaries_match_production_exactly():
    movies = {b.key: b for b in derive_buckets({"17"}, "Movie")}
    shows = {b.key: b for b in derive_buckets({"17"}, "Show")}
    assert movies["17"].title == "Age 17+ Movies"
    assert shows["17"].title == "Age 17+ Shows"
    assert movies["17"].summary == (
        "Movies that are rated 17 according to the Common Sense Rating System."
    )


def test_empty_buckets_are_still_returned():
    """Two production buckets sit at zero items and must persist; the caller
    cannot decide that without being told they exist."""
    buckets = {b.key: b for b in derive_buckets({"R"}, "Movie")}
    assert buckets["1"].values == ()
    assert len(buckets) == 19  # 18 numbered plus the catch-all


def test_the_catch_all_collects_ratings_no_bucket_claimed():
    """'tmdb' is the literal string a misconfiguration wrote into ten shows;
    on the live server it is the sole member of the Not Rated Shows filter."""
    buckets = {b.key: b for b in derive_buckets({"tmdb", "R"}, "Show")}
    assert buckets["other"].title == "Not Rated Shows"
    assert buckets["other"].values == ("tmdb",)


def test_finnish_ratings_fall_into_the_catch_all():
    """Faithful to the tool being replaced: no bucket claims fi/K-* ratings,
    which is why Not Rated Movies holds 28 items in production."""
    buckets = {b.key: b for b in derive_buckets({"fi/K-16", "fi/S", "R"}, "Movie")}
    assert sorted(buckets["other"].values) == ["fi/K-16", "fi/S"]


def test_values_are_sorted_so_an_unchanged_library_produces_an_identical_filter():
    a = derive_buckets({"R", "TV-14", "17"}, "Movie")
    b = derive_buckets({"17", "TV-14", "R"}, "Movie")
    assert [x.values for x in a] == [x.values for x in b]
