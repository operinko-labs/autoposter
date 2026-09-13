"""Row 49: collection -> group -> section number -> sort title, all pure."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from autoposter.collections import groups
from autoposter.config.schema import CollectionDefinition, CollectionsConfig


def config(**kwargs):
    return SimpleNamespace(collections=CollectionsConfig(**kwargs))


def catalog_definitions(key, library_type="Movie"):
    """A catalog preset's own definitions -- what the engine would be handed
    for a config that ticked that key."""
    from autoposter.collections.catalog import BY_KEY

    return BY_KEY[key].definitions(library_type)


@pytest.fixture
def chart_section():
    """A section double with two items in it, for the end-to-end reconciles.

    The fakes come from this phase's other new file by import rather than by
    copy: a fourth fake section would be roadmap row 191's forked-double debt
    committed fresh instead of inherited.
    """
    from test_collection_group_separators import FakeSection

    section = FakeSection()
    section.items = [SimpleNamespace(ratingKey=1), SimpleNamespace(ratingKey=2)]
    return section


def test_canonical_order_is_the_ten_catalog_categories_plus_operator():
    from autoposter.collections.catalog import CATEGORIES

    assert groups.CANONICAL_ORDER[-1] == groups.OPERATOR_GROUP
    assert set(groups.CANONICAL_ORDER[:-1]) == set(CATEGORIES)
    # franchises after content -- the natural reading order.
    # This renumbers every group behind it; the disclosure lives in
    # deploy/README.md's group_order entry and the golden needed no amendment
    # for it (no golden scenario reaches section 050).
    assert groups.CANONICAL_ORDER == (
        "charts", "awards", "content_ratings", "content", "franchises",
        "location", "media", "people", "production", "time", "operator",
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
    assert groups.section_number("operator", order) == "110"


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
    assert groups.sort_prefix_for(definition, {}, groups.CANONICAL_ORDER) == "!110_"


def test_separator_specs_cover_the_groups_present_in_order():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
        CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                             params={"chart": "top_movies"}),
    ]
    specs = groups.separator_specs(definitions, "Movie", config())
    assert [spec.group for spec in specs] == ["charts", "content_ratings", "other"]
    assert [spec.title for spec in specs] == [
        "Chart Collections", "Ratings Collections", "Other Collections",
    ]
    assert specs[1].sort_title == "!030_!Ratings Collections"


def test_separator_specs_carry_a_style_bearing_poster_key_for_every_group():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
        CollectionDefinition(title="Hand Picked", builder="plex_all"),
    ]
    by_group = {
        spec.group: spec.poster_key
        for spec in groups.separator_specs(definitions, "Movie", config())
    }
    assert by_group["content_ratings"] == "orig:content_rating"
    # The operator group has no measured upstream stem (the recon's inventory);
    # it GENERATES now instead of going bare -- the '@' marks the kind.
    assert by_group[groups.OPERATOR_GROUP] == "orig:@operator"


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
        "Ratings Collections", "Other Collections",
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
    assert [spec.sort_title for spec in specs] == [
        "!010_!Ratings Collections", "!999_!Other Collections",
    ]


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
    assert "Other Collections" in titles

    off = config(separators=False)
    assert "Chart Collections" not in definition_titles(definitions, [], "Movie", off)
    assert "Other Collections" not in definition_titles(definitions, [], "Movie", off)


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
    # "99" is higher than every zero-padded age key this table
    # produces, so this is a real leftovers-last pin against the measured
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


def test_a_ceremony_year_unit_orders_by_its_inverted_year():
    """The expansion, not the placeholder.

    ``imdb_award.expand`` returns one definition per ceremony carrying
    ``params={"year": ...}``, and by the time the engine reconciles one it is an
    ordinary definition naming exactly one collection -- so its key is decided
    from the definition in hand, the same as a chart's. That is the one call
    site ``year_order`` has: without it, Kometa's measured newest-first
    hand-order (the one regression measured rather than inferred) would
    be replaced by an alphabetical block running oldest ceremony first.
    """
    for builder in ("imdb_award_years", "cannes_award_years"):
        # A STRING year, which is the shape ``expand`` really produces --
        # ``ImdbAwardYearParams.year`` is declared ``str`` because the dataset's
        # ceremony keys are.
        unit = CollectionDefinition(
            title="Oscars Winners 2026", builder=builder, params={"year": "2026"},
        )
        assert groups.definition_order(unit, "Movie") == groups.year_order(2026)

    # A year that is not a number takes no key rather than raising: ``params``
    # is the raw dict here, and this function is reachable from the config
    # validator.
    assert groups.definition_order(
        CollectionDefinition(
            title="Oscars Winners", builder="imdb_award_years", params={"year": "n/a"},
        ),
        "Movie",
    ) is None


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


# --- the derived sort title, end to end --------------------------------------


async def test_a_chart_collection_gets_its_groups_prefix(session, chart_section):
    """The end-to-end shape: no sort_title in the config, one in Plex."""
    from autoposter.collections.lists import reconcile_list_collection

    definition = CollectionDefinition(
        title="IMDb Top 250", builder="imdb_chart", params={"chart": "top_movies"}
    )
    await reconcile_list_collection(
        session, chart_section, "Movies", "IMDb Top 250",
        chart_section.items, "autoposter", dry_run=False,
        settings=definition, sort_prefix="!010_",
    )
    made = chart_section._collections["IMDb Top 250"]
    assert made.sort_title_set == "!010_IMDb Top 250"


async def test_a_chart_collection_carries_its_inventory_position(session, chart_section):
    """The ordering key travels the same road as the prefix.

    Without it a chart block would be alphabetical, which is not the order the
    chart inventory lists them in and not the order the probe measured.
    """
    from autoposter.collections.lists import reconcile_list_collection

    definition = CollectionDefinition(
        title="IMDb Top 250", builder="imdb_chart", params={"chart": "top_movies"}
    )
    await reconcile_list_collection(
        session, chart_section, "Movies", "IMDb Top 250",
        chart_section.items, "autoposter", dry_run=False,
        settings=definition, sort_prefix="!010_",
        sort_order=groups.definition_order(definition, "Movie"),
    )
    made = chart_section._collections["IMDb Top 250"]
    assert made.sort_title_set == "!010_01_IMDb Top 250"


async def test_an_explicit_sort_title_still_wins_end_to_end(session, chart_section):
    from autoposter.collections.lists import reconcile_list_collection

    definition = CollectionDefinition(
        title="IMDb Top 250", builder="imdb_chart", params={"chart": "top_movies"},
        sort_title="!999_mine",
    )
    await reconcile_list_collection(
        session, chart_section, "Movies", "IMDb Top 250",
        chart_section.items, "autoposter", dry_run=False,
        settings=definition, sort_prefix="!010_",
    )
    assert chart_section._collections["IMDb Top 250"].sort_title_set == "!999_mine"


async def test_a_smart_collection_gets_its_groups_prefix_and_then_settles(
    session, chart_section
):
    """The SMART reconciler's leg of the same road, both halves.

    ``lists.py`` had three tests above and this reconciler had none, yet it is
    the one ``dynamic`` drives once per generated key -- a whole family per
    library. The title asserted is the COLLECTION's, not the definition's:
    ``settings`` here is the family definition (``"Genres"``), and the derived
    value has to come from the title this call names.

    No ``sort_order``: neither shape reaching this reconciler has a natural
    order, so the derived value is the plain ``!<NNN>_<title>`` form -- see the
    reconciler's own docstring.

    The three passes are the MIGRATION DIRECTION, not just idempotency: the
    first runs with no prefix, so the row it stores carries the un-prefixed
    hash an existing deployment already has. The derived value is folded in
    BEFORE ``smart_definition_hash``, so the second pass -- the first one after
    this feature ships -- no longer matches that row and rewrites the sort
    title; the third finds the hash current, short-circuits, and writes nothing
    again. A first pass that started from its own prefixed hash would prove
    only the settling half.
    """
    from autoposter.collections.smart import reconcile_smart_collection

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"}
    )
    args = (session, chart_section, "Movies", "Movie", "Top Horror movies",
            "?type=1&genre=1138", "autoposter")
    kwargs = dict(dry_run=False, settings=definition, sort_prefix="!100_")

    actions = await reconcile_smart_collection(
        *args, dry_run=False, settings=definition
    )
    made = chart_section._collections["Top Horror movies"]
    assert any("created 'Top Horror movies'" in one for one in actions)
    assert made.sort_title_set is None

    # Plex's own create marks the collection smart; ``FakeSection.collection``
    # is shape-agnostic, so without this the passes below meet ``shape_conflict``
    # instead of the hash. Same story for ``subtype``: a
    # collection this fake creates carries no level of its own, so the passes
    # below would meet the level refusal instead of the hash without it.
    made.smart = True
    made.subtype = "movie"

    # The action string itself, not merely "something happened": what the
    # migration pass has to report is the sort title it wrote.
    assert "set the sort title of 'Top Horror movies' to '!100_Top Horror movies'" in (
        await reconcile_smart_collection(*args, **kwargs)
    )
    assert made.sort_title_set == "!100_Top Horror movies"

    made.sort_title_set = None
    assert await reconcile_smart_collection(*args, **kwargs) == []
    assert made.sort_title_set is None


async def test_a_dynamic_family_forwards_its_contexts_prefix_to_every_member(session):
    """``SmartContext.sort_prefix`` -> ``DynamicBuilder.apply`` -> the reconciler.

    The forwarding line is one keyword in ``builders/dynamic.py``; dropping it
    in a refactor would leave a whole generated family with no sort title at
    all, and neither the golden fixture (which has no ``dynamic`` collection in
    any scenario) nor the reconcile test above would notice. The doubles and
    the context builder come from that builder's own test file by import, for
    the reason ``chart_section`` above imports its section rather than forking
    a fourth one.
    """
    import dataclasses

    from test_builder_dynamic import FakeSection, _ctx, _definition

    from autoposter.collections.builders import REGISTRY

    section = FakeSection()
    ctx = dataclasses.replace(
        _ctx(session, section, _definition()), sort_prefix="!100_"
    )
    await REGISTRY["dynamic"].apply(ctx)

    # One prefix, two titles -- the family shares a group, not a sort title.
    assert {title: made.titleSort for title, made in section._existing.items()} == {
        "Top Horror movies": "!100_Top Horror movies",
        "Top Drama movies": "!100_Top Drama movies",
    }


async def test_a_smart_filter_definition_forwards_its_contexts_prefix(session):
    """The mirror of the test above, on the other smart builder.

    ``builders/smart_filter.py``'s forward is the same one keyword, and the
    golden fixture has no ``smart_filter`` collection in any scenario either --
    so dropping it would leave every operator-written smart definition with no
    group prefix and nothing would say so. One definition, not a family: this
    builder builds the collection its definition names.
    """
    import dataclasses

    from test_builder_smart_filter import FakeSection, _ctx, _definition

    from autoposter.collections.builders import REGISTRY

    section = FakeSection()
    ctx = dataclasses.replace(
        _ctx(session, section, _definition()), sort_prefix="!100_"
    )
    await REGISTRY["smart_filter"].apply(ctx)

    assert section._existing["Recent Horror"].titleSort == "!100_Recent Horror"


def test_each_member_of_a_family_gets_its_own_title_in_the_prefix():
    """A family shares a GROUP, not a sort title.

    ``CollectionDefinition.sort_title``'s docstring says an explicit one goes to
    every collection of a family verbatim, and that is unchanged. A DERIVED one
    is per collection: the block is what the section number makes, and inside it
    each collection sorts by its own key and its own name.
    """
    family = CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket")
    first = groups.with_derived_sort_title(
        family, "!030_", "Age 13+ Movies", groups.age_order("13")
    )
    second = groups.with_derived_sort_title(
        family, "!030_", "Age 17+ Movies", groups.age_order("17")
    )
    assert first.sort_title == "!030_13_Age 13+ Movies"
    assert second.sort_title == "!030_17_Age 17+ Movies"


def test_the_derived_value_changes_the_definition_hash_exactly_once():
    """The migration, in one assertion.

    A pass short-circuits on this hash. If the derived sort title did not reach
    it, the first pass after row 49 would find every hash current, skip every
    collection, and never write a sort title at all.

    What this pins is that ``with_derived_sort_title``'s view REACHES
    ``definition_hash``, one call away. It is a characterization test, green
    before the reconcilers were wired: the evidence that the wrap sits before
    the hash IN A PASS is the golden fixture's ``movies_apply_again`` scenario,
    which re-runs an already-reconciled library and carries the changed cells
    with no new actions. Deleting that scenario believing this test covers it
    would lose the only proof of the placement.
    """
    from autoposter.collections.buckets import Bucket
    from autoposter.collections.reconcile import definition_hash

    bucket = Bucket(key="13", title="Age 13+ Movies",
                    summary="...", values=("PG-13",))
    family = CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket")
    before = definition_hash(bucket, family, "url")
    view = groups.with_derived_sort_title(
        family, "!030_", bucket.title, groups.age_order(bucket.key)
    )
    after = definition_hash(bucket, view, "url")
    assert before != after
    assert after == definition_hash(
        bucket,
        groups.with_derived_sort_title(
            family, "!030_", bucket.title, groups.age_order(bucket.key)
        ),
        "url",
    )


def test_expansion_never_inherits_a_derived_sort_title():
    """The other placeholder-inheritance trap. ``engine._completed`` fills an expanded unit from the
    placeholder for every field the unit did not set -- reading
    ``model_fields_set``. A derived value written onto the placeholder would be
    marked set, and all five Oscars year collections would share one sort title
    instead of each getting its own.

    Like the test above, this pins the proxy's read-only property directly and
    was green before the reconcilers were wired; the golden's per-year cells
    (``movies_apply``, four distinct ceremony sort titles) are what prove the
    engine actually keeps them apart in a pass."""
    from autoposter.collections.engine import _completed

    placeholder = CollectionDefinition(
        title="Oscars Winners (recent ceremonies)", builder="imdb_award_years"
    )
    groups.with_derived_sort_title(placeholder, "!020_", placeholder.title)
    unit = CollectionDefinition(title="Oscars Winners 2026", builder="imdb_award_years")
    assert _completed(placeholder, unit).sort_title is None


def test_group_listing_serves_every_group_in_effective_order():
    """The catalog endpoint's `groups` array, one layer down: the whole
    enumeration, effective order, position == index — so the frontend never
    holds a copy of the keys (the row-156 instinct, applied to TypeScript)."""
    listing = groups.group_listing(config())

    assert [entry["key"] for entry in listing] == list(groups.CANONICAL_ORDER)
    assert listing[0] == {
        "key": "charts", "title": "Chart Collections",
        "section": "010", "position": 0,
    }
    assert listing[-1] == {
        "key": "operator", "title": "Collections",
        "section": "110", "position": 10,
    }


def test_group_listing_reorders_and_renumbers_under_group_order():
    """`position` and `section` are both EFFECTIVE, not canonical: a partial
    `group_order` moves the named group to the front and renumbers everything,
    exactly as `effective_order`/`section_number` decide for the pass itself."""
    listing = groups.group_listing(config(group_order=["operator"]))

    assert listing[0] == {
        "key": "operator", "title": "Collections",
        "section": "010", "position": 0,
    }
    assert [entry["key"] for entry in listing][1:4] == [
        "charts", "awards", "content_ratings",
    ]
    assert listing[1]["section"] == "020"
    assert [entry["position"] for entry in listing] == list(range(11))


# --- row 210: an expanded unit inherits its placeholder's group -------------


def test_an_expanded_unit_falls_back_to_its_placeholders_group():
    """The misroute, at its mechanism. A facts_family unit carries the
    MEMBER's title and builder (facts_family.py:434-443), so both lookups miss
    and the unit filed under the operator group -- 65 live franchises under
    !100_, unheaded. The placeholder's group is the fall-through now; the
    operator group remains the answer only when there is no parent to defer to.
    """
    unit = CollectionDefinition(
        title="Ant-Man", builder="tmdb_collection", params={"id": 9741}
    )
    index = {"Franchises": "content"}
    # No parent: today's (wrong for expansions, right for operator definitions)
    # answer, unchanged.
    assert groups.group_for(unit, index) == groups.OPERATOR_GROUP
    # The fix: the placeholder's group threads through as the fall-through.
    assert groups.group_for(unit, index, parent="content") == "content"


def test_the_parent_never_outranks_the_index_or_the_builder_table():
    """Precedence is untouched: a preset's own category and the builder table
    (including the award-years suffix rule) still win over the parent. Only
    the operator fall-through defers."""
    named = CollectionDefinition(title="Ours", builder="plex_all")
    assert groups.group_for(named, {"Ours": "people"}, parent="content") == "people"
    chart = CollectionDefinition(
        title="IMDb Top 250", builder="imdb_chart", params={"chart": "top_movies"}
    )
    assert groups.group_for(chart, {}, parent="content") == "charts"
    year = CollectionDefinition(
        title="Oscars Winners 2026", builder="imdb_award_years",
        params={"year": "2026"},
    )
    assert groups.group_for(year, {}, parent="content") == "awards"


def test_a_location_family_unit_inherits_location_not_operator():
    """The recon's 'same fall-through waits for location_region/continent'
    pinned before either is switched on live. facts_value serves location AND
    media, which is exactly why the fix must NOT be a _BUILTIN_GROUPS row for
    the member builder -- this test plus the media packs' existing behaviour
    hold that shape out.

    ``location_region`` carries no ``readiness=`` line (catalog.py:1659), so it
    is READY and the validator takes it; the plain ``config()`` helper is
    enough.
    """
    index = groups.preset_groups(config(presets=["location_region"]), "Movie")
    assert index.get("Regions") == "location"
    unit = CollectionDefinition(
        title="Western Europe", builder="facts_value",
        params={"field": "origin_country", "values": ["FR", "DE"]},
    )
    assert groups.group_for(unit, index, parent=index["Regions"]) == "location"


def test_sort_prefix_for_threads_the_parent_through():
    unit = CollectionDefinition(
        title="Ant-Man", builder="tmdb_collection", params={"id": 9741}
    )
    assert groups.sort_prefix_for(
        unit, {}, groups.CANONICAL_ORDER, parent="content"
    ) == groups.sort_prefix("content", groups.CANONICAL_ORDER)


# --- the style-bearing poster key ---------------------------------------------


def test_the_poster_key_carries_the_style_and_the_art_kind():
    """Hosted groups keep their measured stem behind the style; every other
    group gets the generated marker -- '<style>:@<group>' -- so one key names
    both which art and which style, and folding it into the separator hash is
    what makes a style change a definition change."""
    cfg = config()
    assert groups.separator_poster_key("charts", cfg) == "orig:chart"
    assert groups.separator_poster_key("content_ratings", cfg) == "orig:content_rating"
    assert groups.separator_poster_key("content", cfg) == "orig:@content"
    assert groups.separator_poster_key("operator", cfg) == "orig:@operator"
    sand = config(separator_style="sand")
    assert groups.separator_poster_key("charts", sand) == "sand:chart"
    assert groups.separator_poster_key("media", sand) == "sand:@media"


def test_the_22_styles_are_the_measured_folder_names():
    # p-div-font.md §2d: `$colors` at create_default_posters.ps1:5538 and the
    # contents-API listing of separators/ agree exactly, 2026-08-30 -- @base
    # excluded (it is the textless layer, not a style).
    assert len(groups.SEPARATOR_STYLES) == 22
    assert "orig" in groups.SEPARATOR_STYLES
    assert "@base" not in groups.SEPARATOR_STYLES
    assert groups.SEPARATOR_STYLES == tuple(sorted(groups.SEPARATOR_STYLES))


def test_separator_style_is_validated_against_the_styles():
    assert CollectionsConfig().separator_style == "orig"
    assert CollectionsConfig(separator_style="sand").separator_style == "sand"
    with pytest.raises(ValidationError) as caught:
        CollectionsConfig(separator_style="taupe")
    message = str(caught.value)
    assert "taupe" in message
    assert "sand" in message  # the refusal lists the valid set


# --- the franchises group -----------------------------------------------------


def test_the_franchises_divider_follows_the_transcribed_formula():
    assert groups.separator_title("franchises") == "Franchise Collections"
    assert (
        groups.separator_summary("franchises")
        == "Section separator for Franchise Collections."
    )
    assert groups.section_number("franchises", groups.CANONICAL_ORDER) == "050"


def test_a_franchise_family_unit_now_files_under_franchises():
    """The fix and the franchises group, composed: the placeholder's category moved to
    'franchises', so the parent fall-through lands the members there."""
    index = groups.preset_groups(config(presets=["content_franchises"]), "Movie")
    assert index["Franchises"] == "franchises"
    unit = CollectionDefinition(
        title="Ant-Man", builder="tmdb_collection", params={"id": 9741}
    )
    assert groups.group_for(unit, index, parent=index["Franchises"]) == "franchises"


# --- the fence -----------------------------------------------------------------


def test_the_fence_closes_every_active_tab():
    """'Other Collections' (title OURS), pinned at 999 -- outside the
    canonical renumber, so no future group insertion moves it -- whenever ANY
    managed group is active. It fences the managed blocks off from Plex's own
    unprefixed tail (the recon measured 278 rows of it)."""
    definitions = [
        CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                             params={"chart": "top_movies"}),
    ]
    specs = groups.separator_specs(definitions, "Movie", config())
    assert [spec.group for spec in specs] == ["charts", groups.TAIL_GROUP]
    fence = specs[-1]
    assert fence.title == "Other Collections"
    assert fence.summary == "Section separator for Other Collections."
    assert fence.sort_title == "!999_!Other Collections"
    assert fence.poster_key == "orig:@other"


def test_no_active_groups_means_no_fence():
    assert groups.separator_specs([], "Movie", config()) == []
    off = config(separators=False)
    definitions = [
        CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                             params={"chart": "top_movies"}),
    ]
    assert groups.separator_specs(definitions, "Movie", off) == []


def test_the_fence_outsorts_every_canonical_section():
    """The 999 pin's actual property: numerically past any section the
    position-times-ten arithmetic can produce for the shipped table, so the
    fence stays the LAST managed block however the groups are reordered."""
    last = groups.section_number(
        groups.CANONICAL_ORDER[-1], groups.CANONICAL_ORDER
    )
    assert groups.TAIL_SECTION == "999"
    assert groups.TAIL_SECTION > last


def test_the_fence_title_joins_the_managed_set():
    definitions = [
        CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                             params={"chart": "top_movies"}),
    ]
    titles = groups.separator_titles(definitions, "Movie", config())
    assert titles == {"Chart Collections", "Other Collections"}


def test_group_order_cannot_name_the_fence():
    # 'other' is a pseudo-group: pinned, not reorderable, not listed.
    with pytest.raises(ValidationError):
        CollectionsConfig(group_order=["other"])
    assert all(entry["key"] != "other" for entry in groups.group_listing(config()))


def test_the_universe_and_dc_units_file_under_franchises():
    """The operator directive's first half. Both packs' collections are
    curated LIST definitions carrying their own titles, so they resolve
    through ``group_for``'s route 1 -- the preset index -- and the index is
    built from the preset's ``category``. Moving the category moves the band,
    with no per-definition edit."""
    index = groups.preset_groups(
        config(presets=["content_universes", "content_dc"]), "Movie",
    )
    assert index["Fast & Furious"] == "franchises"
    assert index["Marvel Cinematic Universe"] == "franchises"
    assert index["Star Wars Universe"] == "franchises"
    assert index["DC Universe"] == "franchises"
    assert index["DC Extended Universe"] == "franchises"
    assert index["In Association With DC"] == "franchises"

    unit = CollectionDefinition(
        title="Fast & Furious", builder="imdb_list",
        params={"list": "ls4102351575"},
    )
    order = groups.CANONICAL_ORDER
    assert groups.group_for(unit, index) == "franchises"
    assert groups.sort_prefix_for(unit, index, order) == "!050_"


def test_the_moved_packs_activate_the_franchise_separator_at_050():
    """The separator half, asserted beside the units: a band whose members
    moved but whose divider did not would be the exact disagreement the
    !040 split was. The universes packs alone -- no ``content_franchises`` --
    must now raise the franchises divider on their own."""
    cfg = config(
        presets=["content_universes", "content_dc"], separators=True,
    )
    definitions = [
        *catalog_definitions("content_universes"),
        *catalog_definitions("content_dc"),
    ]
    specs = groups.separator_specs(definitions, "Movie", cfg)
    franchises = next(spec for spec in specs if spec.group == "franchises")
    assert franchises.title == "Franchise Collections"
    assert franchises.sort_title == "!050_!Franchise Collections"
    # ...and nothing of theirs is left behind in the content band.
    assert "content" not in {spec.group for spec in specs}


def test_a_moved_category_changes_the_definition_hash_so_the_prefix_self_heals():
    """No migration. ``sort_prefix`` reaches the stored hash through
    ``with_derived_sort_title`` -> ``lists._settings_parts`` (which is why
    that wrapper exists at all, ``groups._DerivedSortTitle``'s docstring), so
    a collection whose band changed is not short-circuited as already-current
    on the next real pass -- it is re-reconciled and its sort title rewritten.
    This pins the mechanism the phase relies on rather than trusting it."""
    from autoposter.collections import lists

    definition = CollectionDefinition(
        title="Fast & Furious", builder="imdb_list",
        params={"list": "ls4102351575"},
    )
    items = [SimpleNamespace(ratingKey=1), SimpleNamespace(ratingKey=2)]
    order = groups.CANONICAL_ORDER
    assert groups.sort_prefix("content", order) == "!040_"
    assert groups.sort_prefix("franchises", order) == "!050_"

    def hashed(group):
        return lists._members_hash(items, None, "sync", groups.with_derived_sort_title(
            definition, groups.sort_prefix(group, order), definition.title, None,
        ))

    assert hashed("content") != hashed("franchises")
