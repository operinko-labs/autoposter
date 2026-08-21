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
    # Kometa's translations file, verbatim (docs/research/kometa-collections.md:410):
    # "<<library_translationU>>s that are Unrated, Not Rated or any other
    # uncommon Ratings." -- not the invented "not rated" text this used to
    # write over the live 'Not Rated Movies'/'Not Rated Shows' summaries.
    assert movies["other"].title == "Not Rated Movies"
    assert movies["other"].summary == (
        "Movies that are Unrated, Not Rated or any other uncommon Ratings."
    )
    assert shows["other"].title == "Not Rated Shows"
    assert shows["other"].summary == (
        "Shows that are Unrated, Not Rated or any other uncommon Ratings."
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


def test_load_table_reads_from_the_configured_asset_root(monkeypatch, tmp_path):
    """In the container the package is pip-installed into site-packages while
    the table is copied to ``/app/assets``, so a path derived by walking up
    from ``__file__`` resolves into the library root instead. This must go
    through ``AUTOPOSTER_ASSETS_ROOT`` (via ``autoposter.assets``), not a
    fixed relative walk."""
    import importlib
    import json
    import sys

    from autoposter import assets

    collections_dir = tmp_path / "collections"
    collections_dir.mkdir()
    (collections_dir / "content_rating_cs.json").write_text(
        json.dumps({"include": ["1"], "addons": {"1": []}}), encoding="utf-8"
    )
    monkeypatch.setenv(assets.ENV_VAR, str(tmp_path))
    assets.assets_root.cache_clear()
    sys.modules.pop("autoposter.collections.buckets", None)
    try:
        buckets = importlib.import_module("autoposter.collections.buckets")
        assert buckets.load_table() == {"include": ["1"], "addons": {"1": []}}
    finally:
        assets.assets_root.cache_clear()


def test_the_module_imports_cleanly_with_a_missing_asset_root(monkeypatch, tmp_path):
    """``load_table()`` reads its table lazily behind ``lru_cache``, so a
    missing asset root must not break import -- matching the badges modules'
    behaviour in test_assets_root.py."""
    import importlib
    import sys

    from autoposter import assets

    monkeypatch.setenv(assets.ENV_VAR, str(tmp_path / "does-not-exist"))
    assets.assets_root.cache_clear()
    sys.modules.pop("autoposter.collections.buckets", None)
    try:
        importlib.import_module("autoposter.collections.buckets")  # must not raise
    finally:
        assets.assets_root.cache_clear()
