"""Row 49: collection -> group -> section number -> sort title, all pure."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from autoposter.collections import groups
from autoposter.config.schema import CollectionDefinition, CollectionsConfig


def config(**kwargs):
    return SimpleNamespace(collections=CollectionsConfig(**kwargs))


def test_canonical_order_is_the_nine_catalog_categories_plus_operator():
    from autoposter.collections.catalog import CATEGORIES

    assert groups.CANONICAL_ORDER[-1] == groups.OPERATOR_GROUP
    assert set(groups.CANONICAL_ORDER[:-1]) == set(CATEGORIES)
    assert groups.CANONICAL_ORDER == (
        "charts", "awards", "content_ratings", "content", "location",
        "media", "people", "production", "time", "operator",
    )


def test_the_canonical_order_is_not_the_catalogs_alphabetical_declaration():
    # LAW Addendum 2.4: ``catalog.CATEGORIES`` declares its keys alphabetically,
    # so iterating it for tab order would silently file awards ahead of charts.
    # The canonical order is this module's own pinned list, and this is what
    # catches a later "just use CATEGORIES" simplification.
    from autoposter.collections.catalog import CATEGORIES

    assert groups.CANONICAL_ORDER[:-1] != tuple(CATEGORIES)


def test_section_numbers_are_position_times_ten():
    order = groups.CANONICAL_ORDER
    assert groups.section_number("charts", order) == "010"
    assert groups.section_number("content_ratings", order) == "030"
    assert groups.section_number("operator", order) == "100"


def test_sort_prefix_and_member_sort_title():
    order = groups.CANONICAL_ORDER
    assert groups.sort_prefix("content_ratings", order) == "!030_"
    assert (
        groups.sort_prefix("content_ratings", order) + "Age 13+ Movies"
        == "!030_Age 13+ Movies"
    )


def test_separator_naming_follows_the_transcribed_kometa_formula():
    order = groups.CANONICAL_ORDER
    # docs/research/kometa-collections.md:429 -- "<<key_name>> Collections" /
    # "Section separator for <<key_name>> Collections."
    assert groups.separator_title("content_ratings") == "Ratings Collections"
    assert (
        groups.separator_summary("content_ratings")
        == "Section separator for Ratings Collections."
    )
    assert groups.separator_title("charts") == "Chart Collections"
    assert groups.separator_sort_title("charts", order) == "!010_!Chart Collections"


def test_every_group_has_a_separator_title_and_a_summary():
    for group in groups.CANONICAL_ORDER:
        assert groups.separator_title(group)
        assert groups.separator_summary(group).startswith("Section separator for ")


def test_the_operator_group_is_not_called_collections_collections():
    assert groups.separator_title(groups.OPERATOR_GROUP) == "Collections"
    assert (
        groups.separator_summary(groups.OPERATOR_GROUP)
        == "Section separator for Collections."
    )


def test_group_order_reorders_and_renumbers():
    order = groups.effective_order(config(group_order=["awards", "charts"]))
    assert order[:2] == ("awards", "charts")
    assert groups.section_number("awards", order) == "010"
    assert groups.section_number("charts", order) == "020"
    # Unnamed groups keep the canonical order behind the named ones.
    assert order[2:] == groups.CANONICAL_ORDER[2:]
    assert sorted(order) == sorted(groups.CANONICAL_ORDER)


def test_no_group_order_is_the_canonical_order():
    assert groups.effective_order(config()) == groups.CANONICAL_ORDER


def test_group_order_refuses_an_unknown_name_and_lists_the_valid_set():
    with pytest.raises(ValidationError) as caught:
        CollectionsConfig(group_order=["chartz"])
    message = str(caught.value)
    assert "chartz" in message
    assert ", ".join(groups.CANONICAL_ORDER) in message


def test_group_order_refuses_a_repeated_name():
    with pytest.raises(ValidationError) as caught:
        CollectionsConfig(group_order=["charts", "charts"])
    assert "twice" in str(caught.value)


def test_group_order_accepts_the_whole_canonical_order_and_a_partial_one():
    assert CollectionsConfig(group_order=list(groups.CANONICAL_ORDER)).group_order
    assert CollectionsConfig(group_order=["operator"]).group_order == ["operator"]
    assert CollectionsConfig().group_order is None


def test_builtins_are_grouped_by_builder():
    assert groups.builtin_group("imdb_chart") == "charts"
    assert groups.builtin_group("imdb_award") == "awards"
    assert groups.builtin_group("cs_bucket") == "content_ratings"
    assert groups.builtin_group("plex_search") is None


def test_every_ceremony_year_builder_is_an_award():
    from autoposter.collections.builders.imdb_award import EVENTS

    for event in EVENTS.values():
        assert groups.builtin_group(event.years_builder) == "awards"


def test_builtin_groups_agree_with_the_catalogs_setting_rows():
    # The three SETTING_PRESETS rows are the catalog's own statement of which
    # category each shipped family belongs to; the builder table must not drift
    # from them (collections/catalog.py:546-589).
    from autoposter.collections.catalog import SETTING_PRESETS

    by_key = {preset.key: preset.category for preset in SETTING_PRESETS}
    assert groups.builtin_group("imdb_chart") == by_key["imdb_charts"]
    assert groups.builtin_group("imdb_award") == by_key["oscars"]
    assert groups.builtin_group("cs_bucket") == by_key["content_ratings_divider"]


def test_every_group_a_preset_can_name_is_a_group_this_module_knows():
    # A category the catalog grew and this module never heard of would be a
    # KeyError inside ``separator_title`` on a live pass.
    from autoposter.collections.catalog import CATALOG

    for preset in CATALOG:
        assert preset.category in groups.CANONICAL_ORDER


def test_a_preset_definition_takes_its_presets_category():
    index = groups.preset_groups(config(presets=["chart_tracearr_movies"]), "Movie")
    assert index
    assert set(index.values()) == {"charts"}


def test_an_unknown_preset_key_expands_to_nothing_rather_than_raising():
    # The refusal for a bad key belongs to the config validator alone -- a
    # KeyError here would be a 500 on a settings save.
    assert groups.preset_groups(SimpleNamespace(
        collections=SimpleNamespace(presets=["nope"], group_order=None)
    ), "Movie") == {}


def test_an_operator_definition_falls_into_the_operator_group():
    definition = CollectionDefinition(title="Hand Picked", builder="plex_all")
    assert groups.group_for(definition, {}) == groups.OPERATOR_GROUP


def test_a_preset_title_outranks_the_builder_table():
    definition = CollectionDefinition(
        title="Ours", builder="imdb_chart", params={"chart": "top_movies"}
    )
    assert groups.group_for(definition, {"Ours": "people"}) == "people"


def test_sort_prefix_for_joins_the_group_lookup_to_the_number():
    definition = CollectionDefinition(title="Hand Picked", builder="plex_all")
    assert groups.sort_prefix_for(definition, {}, groups.CANONICAL_ORDER) == "!100_"


def test_separator_specs_cover_the_groups_present_in_order():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
        CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                             params={"chart": "top_movies"}),
    ]
    specs = groups.separator_specs(definitions, "Movie", config())
    assert [spec.group for spec in specs] == ["charts", "content_ratings"]
    assert [spec.title for spec in specs] == [
        "Chart Collections", "Ratings Collections",
    ]
    assert specs[1].sort_title == "!030_!Ratings Collections"


def test_separator_specs_carry_the_measured_poster_key_or_none():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
        CollectionDefinition(title="Hand Picked", builder="plex_all"),
    ]
    by_group = {
        spec.group: spec.poster_key
        for spec in groups.separator_specs(definitions, "Movie", config())
    }
    assert by_group["content_ratings"] == "content_rating"
    # Task 1 measured no separators/orig stem for the operator group; no poster
    # is the graceful answer, not a guessed URL that 404s.
    assert by_group[groups.OPERATOR_GROUP] is None


def test_the_measured_poster_keys_are_the_three_that_returned_200():
    # docs/research/collection-sort-probe/README.md -- chart, award and
    # content_rating resolved; the other seven groups have no upstream art.
    assert groups.SEPARATOR_POSTER_KEYS == {
        "charts": "chart",
        "awards": "award",
        "content_ratings": "content_rating",
    }


def test_separator_titles_are_the_specs_titles():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
    ]
    assert groups.separator_titles(definitions, "Movie", config()) == {
        "Ratings Collections"
    }


def test_separators_false_yields_no_specs_and_no_titles():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
    ]
    off = config(separators=False)
    assert groups.separator_specs(definitions, "Movie", off) == []
    assert groups.separator_titles(definitions, "Movie", off) == set()


def test_group_order_moves_the_separators_number_too():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
    ]
    specs = groups.separator_specs(
        definitions, "Movie", config(group_order=["content_ratings"])
    )
    assert [spec.sort_title for spec in specs] == ["!010_!Ratings Collections"]


def test_definition_titles_counts_the_separator_titles():
    """The engine's enumeration seam: one owner for a separator's title, so a
    divider cannot be counted by one path and swept as an orphan by another."""
    from autoposter.collections.engine import definition_titles

    definitions = [
        CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                             params={"chart": "top_movies"}),
    ]
    titles = definition_titles(definitions, [], "Movie", config())
    assert "IMDb Top 250" in titles
    assert "Chart Collections" in titles

    off = config(separators=False)
    assert "Chart Collections" not in definition_titles(definitions, [], "Movie", off)


# --- the per-member ordering key (LAW Addendum 1/2) --------------------------


def test_the_ordering_key_is_appended_only_when_a_family_supplies_one():
    assert groups.member_sort_title("!030_", "Age 13+ Movies", "13") == (
        "!030_13_Age 13+ Movies"
    )
    assert groups.member_sort_title("!100_", "Hand Picked") == "!100_Hand Picked"
    assert groups.member_sort_title("!100_", "Hand Picked", None) == "!100_Hand Picked"


def test_common_sense_buckets_order_by_ascending_age_zero_padded():
    assert groups.age_order("13") == "13"
    assert groups.age_order("1") == "01"
    assert groups.age_order("18") == "18"


def test_the_leftovers_bucket_takes_the_sentinel():
    assert groups.LEFTOVERS_ORDER == "99"
    assert groups.age_order("other") == groups.LEFTOVERS_ORDER


def test_every_shipped_bucket_sorts_in_the_order_the_table_lists_them():
    from autoposter.collections.buckets import derive_buckets

    keys = [groups.age_order(bucket.key) for bucket in derive_buckets(set(), "Movie")]
    # LAW Addendum 3: "99" is higher than every zero-padded age key this table
    # produces, so this is a real leftovers-last pin against Task 1's measured
    # ascending block -- unlike the dropped ``~`` sentinel, which that same
    # capture showed filing BEFORE the ages on this server's collation, the
    # opposite of what the requirement needs.
    assert keys == sorted(keys)
    assert keys[-1] == groups.LEFTOVERS_ORDER
    assert len(set(keys)) == len(keys)


def test_award_years_invert_so_the_newest_ceremony_files_first():
    assert groups.year_order(2026) < groups.year_order(2025) < groups.year_order(2024)
    assert groups.year_order(2026) == "7973"
    keys = [groups.year_order(year) for year in (2022, 2023, 2024, 2025, 2026)]
    assert sorted(keys) == list(reversed(keys))


def test_a_chart_definition_orders_by_its_place_in_the_chart_inventory():
    from autoposter.collections.builders.imdb_chart import CHARTS_FOR

    for library_type, charts in CHARTS_FOR.items():
        keys = [
            groups.definition_order(
                CollectionDefinition(
                    title="whatever", builder="imdb_chart", params={"chart": chart}
                ),
                library_type,
            )
            for chart in charts
        ]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)
    assert groups.definition_order(
        CollectionDefinition(
            title="IMDb Popular", builder="imdb_chart",
            params={"chart": "popular_movies"},
        ),
        "Movie",
    ) == "00"


def test_a_definition_no_family_orders_has_no_ordering_key():
    # Operator definitions, and the two families whose collections only exist
    # after a run-time expansion (their keys are age_order/year_order's). Plus
    # the real cross-library case: IMDb has no lowest-rated TV chart, so that
    # definition reaching a Show library has no place in that library's
    # inventory and no key either.
    for definition in (
        CollectionDefinition(title="Hand Picked", builder="plex_all"),
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
        CollectionDefinition(title="Oscars Winners", builder="imdb_award_years"),
        CollectionDefinition(
            title="IMDb Lowest Rated", builder="imdb_chart",
            params={"chart": "lowest_rated"},
        ),
    ):
        assert groups.definition_order(definition, "Show") is None


def test_the_awards_winners_definition_leads_its_ceremony_year_expansions():
    # Review Important 1: a group mixing an unkeyed parent with a keyed
    # expansion sorted the parent AFTER the keys -- a digit-first key always
    # sorts ahead of a letter-first title -- so every ceremony year filed
    # ahead of the winners collection it belongs with, the reverse of
    # Kometa's measured hand-order ('!130_Oscars !1', winners leading). The
    # winners definition now takes LEADING_ORDER so it sorts first within its
    # own family's block.
    winners = CollectionDefinition(
        title="Oscars Winners", builder="imdb_award", params={"award": "best_picture"}
    )
    winners_key = groups.definition_order(winners, "Movie")
    assert winners_key == groups.LEADING_ORDER

    prefix = groups.sort_prefix("awards", groups.CANONICAL_ORDER)
    winners_sort_title = groups.member_sort_title(prefix, "Oscars Winners", winners_key)
    year_sort_title = groups.member_sort_title(
        prefix, "Oscars Winners 2026", groups.year_order(2026)
    )
    assert winners_sort_title < year_sort_title


# --- the derived sort title, out of band ------------------------------------


def test_an_explicit_sort_title_is_never_replaced():
    definition = CollectionDefinition(
        title="Hand Picked", builder="plex_all", sort_title="!999_mine"
    )
    view = groups.with_derived_sort_title(definition, "!100_", "Hand Picked")
    assert view is definition
    assert view.sort_title == "!999_mine"


def test_the_derived_view_reads_through_to_the_definition():
    definition = CollectionDefinition(
        title="Hand Picked", builder="plex_all", labels=["x"], collection_mode="hide"
    )
    view = groups.with_derived_sort_title(definition, "!100_", "Hand Picked")
    assert view.sort_title == "!100_Hand Picked"
    assert view.labels == ["x"]
    assert view.collection_mode == "hide"
    assert view.title == "Hand Picked"
    # Out of band: the definition itself is untouched, and nothing about it
    # reads as operator-set -- which is what engine._completed checks.
    assert definition.sort_title is None
    assert "sort_title" not in definition.model_fields_set


def test_the_derived_view_carries_the_familys_ordering_key():
    definition = CollectionDefinition(
        title="Common Sense age ratings", builder="cs_bucket"
    )
    view = groups.with_derived_sort_title(
        definition, "!030_", "Age 13+ Movies", groups.age_order("13")
    )
    assert view.sort_title == "!030_13_Age 13+ Movies"


def test_no_prefix_and_no_settings_are_both_pass_through():
    definition = CollectionDefinition(title="Hand Picked", builder="plex_all")
    assert groups.with_derived_sort_title(definition, None, "Hand Picked") is definition
    assert groups.with_derived_sort_title(None, "!100_", "Hand Picked") is None


def test_the_derived_view_reaches_the_settings_hash():
    from autoposter.collections.lists import _settings_parts

    definition = CollectionDefinition(title="Hand Picked", builder="plex_all")
    assert _settings_parts(definition) == []
    view = groups.with_derived_sort_title(definition, "!100_", "Hand Picked")
    assert _settings_parts(view) == ["sort_title='!100_Hand Picked'"]


def test_the_derived_view_is_read_only():
    definition = CollectionDefinition(title="Hand Picked", builder="plex_all")
    view = groups.with_derived_sort_title(definition, "!100_", "Hand Picked")
    with pytest.raises(AttributeError):
        view.labels = ["nope"]
    assert repr(view).startswith("<derived sort_title=")


def test_the_module_imports_nothing_from_the_package_at_module_scope():
    # The whole reason every reconciler can import this one: ``catalog``
    # imports ``config.schema``, and ``builders/__init__`` imports
    # ``cs_bucket`` which imports ``reconcile``, so a module-scope import here
    # would close a ring. Read as source rather than trusted to a comment.
    import ast
    import pathlib

    import autoposter.collections.groups as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("autoposter") for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("autoposter")
