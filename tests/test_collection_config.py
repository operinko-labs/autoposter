"""Collections configuration.

``Config()`` with no arguments cannot be built directly -- ``assets_root``,
``plex``, ``providers`` and ``artwork`` are all required fields with no
defaults, independent of anything in this phase. The existing badges config
tests (see conftest.py's ``config_with_badges`` and friends) work around this
by loading the example config instead, so these tests do the same.
"""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderResult, register
from autoposter.collections.sources import AWARD_YEARS_TITLE
from autoposter.config.live import frozen_reason
from autoposter.config.loader import build_config, load_config, read_config_document
from autoposter.config.schema import CollectionDefinition, CollectionsConfig

EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


class _FreeFormBuilder:
    """A builder that declares no ``params_model``.

    Params are the builder's own business, and a builder with no model has
    made no claim about them -- so the load-time check has nothing to check
    and must let anything through.
    """

    type_name = "config_tests_free_form"

    async def build(self, ctx) -> BuilderResult:
        return BuilderResult(ids=[])


@pytest.fixture
def free_form_builder():
    register(_FreeFormBuilder())
    try:
        yield _FreeFormBuilder.type_name
    finally:
        del REGISTRY[_FreeFormBuilder.type_name]


def test_collections_default_to_reporting_without_writing():
    """Consistent with operations.write_to_plex and badges.upload_to_plex:
    nothing reaches the live server until the operator opts in."""
    config = load_config(EXAMPLE_CONFIG)
    assert config.collections.enabled is True
    assert config.collections.apply_to_plex is False


def test_the_ownership_label_defaults_to_our_own_name():
    """It must not be 'Kometa' -- the tool being replaced uses that label,
    and sharing it would make both tools claim the same collections."""
    config = load_config(EXAMPLE_CONFIG)
    assert config.collections.ownership_label == "autoposter"
    assert config.collections.ownership_label != "Kometa"


def test_both_libraries_are_configured_by_default():
    assert load_config(EXAMPLE_CONFIG).collections.libraries == ["Movies", "TV Shows"]


def test_the_separator_toggle_defaults_to_enabled():
    assert load_config(EXAMPLE_CONFIG).collections.separators is True


# --- Per-collection builder definitions ---------------------------------
#
# `builder:` names a registry key. An unknown one has to fail here, at config
# load, rather than hours later inside a scheduled reconcile where the only
# symptom is one collection quietly not being built.


def _document_with_definitions(definitions: list[dict]) -> dict:
    """The example config, with `collections.definitions` replaced.

    Goes through the real loader (`read_config_document` + `build_config`), so
    these tests exercise the same path `load_config` and the overrides layer
    both end in -- not a hand-built `CollectionsConfig`.
    """
    document = read_config_document(EXAMPLE_CONFIG)
    document["collections"]["definitions"] = definitions
    return document


def test_definitions_default_to_none_configured():
    """The shipped sources (charts, awards, separators) are unaffected by this
    field being empty."""
    assert load_config(EXAMPLE_CONFIG).collections.definitions == []


def test_a_definition_naming_an_unknown_builder_fails_at_config_load():
    document = _document_with_definitions(
        [{"title": "Nope", "builder": "no_such_builder"}]
    )

    with pytest.raises(ValidationError, match="no_such_builder"):
        build_config(document)


def test_the_unknown_builder_error_names_the_builders_that_do_exist():
    """An operator who mis-types a builder gets the list, not just a rejection."""
    document = _document_with_definitions([{"title": "Nope", "builder": "plex_ids"}])

    with pytest.raises(ValidationError, match="plex_id"):
        build_config(document)


def test_the_registry_names_every_builder_when_one_is_misspelled():
    """The "known builders" message was unpinned, so
    a builder added to the registry without a catalog row would have quietly
    stopped appearing in the one message an operator sees when they typo."""
    with pytest.raises(ValidationError) as caught:
        CollectionDefinition(title="X", builder="dynamik", params={})
    message = str(caught.value)
    for name in REGISTRY:
        assert name in message, name


def test_a_definition_naming_a_registered_builder_loads_with_its_defaults():
    document = _document_with_definitions(
        [{"title": "Hand Picked", "builder": "plex_id", "params": {"ids": ["1"]}}]
    )

    definition = build_config(document).collections.definitions[0]

    assert definition.title == "Hand Picked"
    assert definition.builder == "plex_id"
    assert definition.params == {"ids": ["1"]}
    # sync is the default membership mode; append only ever adds.
    assert definition.sync_mode == "sync"
    assert definition.sort == "custom"
    # None, not [], so "use collections.libraries" stays distinguishable from
    # "target no library at all".
    assert definition.libraries is None
    assert definition.limit is None
    assert definition.schedule is None
    assert definition.labels == []
    assert definition.summary is None
    assert definition.sort_title is None
    assert definition.collection_mode is None


def test_an_unknown_sync_mode_is_rejected():
    with pytest.raises(ValidationError):
        CollectionDefinition(title="X", builder="plex_id", sync_mode="replace")


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_that_could_not_produce_a_collection_is_rejected(limit):
    with pytest.raises(ValidationError):
        CollectionDefinition(title="X", builder="plex_id", limit=limit)


def test_a_schedule_gate_that_would_never_run_is_rejected():
    with pytest.raises(ValidationError):
        CollectionDefinition(
            title="X", builder="plex_id", schedule={"every_n_runs": 0}
        )


@pytest.mark.parametrize("month", [0, 13])
def test_a_schedule_window_outside_the_calendar_is_rejected(month):
    with pytest.raises(ValidationError):
        CollectionDefinition(
            title="X", builder="plex_id", schedule={"months": [month]}
        )


# --- the builder's own params, checked at config load ----------------------
#
# `builder:` has been validated here since the beginning, for the reason in
# the block comment above: a typo that surfaced inside a scheduled reconcile
# reads as "that collection stopped updating", hours later. A mis-spelled
# *param* was exactly the same failure one level down -- the builder rejected
# it mid-pass, the engine contained it as a dead source, and the operator saw
# one collection quietly not built. The builder's `params_model` says what it
# takes, so the config can be held to it at load.


def test_a_definition_whose_params_the_builder_rejects_fails_at_config_load():
    document = _document_with_definitions([
        {"title": "Hand Picked", "builder": "plex_id",
         "params": {"ids": ["1"], "colour": "red"}}
    ])

    with pytest.raises(ValidationError, match="Hand Picked"):
        build_config(document)


def test_the_params_error_names_the_offending_field():
    """The definition's title says which collection, the field says which key.
    Neither alone is enough to fix a config with twenty definitions in it."""
    document = _document_with_definitions([
        {"title": "Hand Picked", "builder": "plex_id",
         "params": {"ids": ["1"], "colour": "red"}}
    ])

    with pytest.raises(ValidationError, match="colour"):
        build_config(document)


@pytest.mark.parametrize(
    ("builder", "params", "expected_fragment"),
    [
        pytest.param(
            "mdblist_list",
            {"list": "https://mdblist.com/lists/a/b?apikey=SECRET"},
            "not an MDBList list",
            id="mdblist_list",
        ),
        pytest.param(
            "imdb_list",
            {"list": "ls123?token=SECRET"},
            "not an IMDb list id",
            id="imdb_list",
        ),
        pytest.param(
            "imdb_watchlist",
            {"user": "ur99?token=SECRET"},
            "not an IMDb user id",
            id="imdb_watchlist",
        ),
        pytest.param(
            "tvdb_list",
            {"slug": "https://thetvdb.com/lists/x?apikey=SECRET"},
            "not a TVDb list slug",
            id="tvdb_list",
        ),
        pytest.param(
            "text_file",
            {"path": "/abs/SECRET.txt"},
            "is an absolute path",
            id="text_file",
        ),
        pytest.param(
            "imdb_id",
            {"ids": ["https://example.com/x?apikey=SECRET"]},
            "not an IMDb id",
            id="imdb_id",
        ),
        pytest.param(
            "tmdb_movie",
            {"ids": ["https://example.com/x?apikey=SECRET"]},
            "not a TMDb id",
            id="tmdb_id",
        ),
        pytest.param(
            "tvdb_movie",
            {"ids": ["https://example.com/x?apikey=SECRET"]},
            "not a TVDb id",
            id="tvdb_id",
        ),
    ],
)
def test_a_credential_bearing_param_is_refused_without_echoing_it(
    builder, params, expected_fragment
):
    """`CollectionDefinition`'s params check composes each inner error's `msg`
    verbatim, and `api/routes.py`'s ValidationError seam serves exactly those
    `msg` values on five config endpoints -- so a validator that interpolated
    `{value!r}` handed an operator's pasted API key back on all five. Pinned
    against all eight of the changed validators (`mdblist_list`, `imdb_list`,
    `imdb_watchlist`, `tvdb_list`, `text_file`, `imdb_id`, `tmdb_movie`,
    `tvdb_movie`): each case's pasted value carries `SECRET`, so if that
    validator's `f"{value!r} "` head were restored, `SECRET` would reappear in
    `served` and the assertion below would fail.

    Asserted against `errors()[…]["msg"]`, which is what is served, rather than
    `str(exc)`: pydantic appends its own `input_value=` tail to that string and
    no message change can remove it. Nothing serves that tail -- the config
    seam serves `msg`, and `queue/worker.py` stores the class name.
    """
    with pytest.raises(ValidationError) as error:
        CollectionDefinition.model_validate({
            "title": "Leaky",
            "builder": builder,
            "params": params,
        })

    served = "; ".join(item["msg"] for item in error.value.errors())
    assert "SECRET" not in served, served
    assert expected_fragment in served
    if builder == "mdblist_list":
        assert "list: 14" in served, "the guidance tail is what tells the operator what to write"


def test_a_param_of_the_wrong_type_fails_at_config_load():
    document = _document_with_definitions(
        [{"title": "Hand Picked", "builder": "plex_id", "params": {"ids": "tt0000001"}}]
    )

    with pytest.raises(ValidationError, match="ids"):
        build_config(document)


def test_a_definition_missing_a_required_param_fails_at_config_load():
    """``plex_id`` with no ids builds nothing, and nothing is the reconciler's
    "make no changes" -- so without this it would not even fail visibly."""
    document = _document_with_definitions(
        [{"title": "Hand Picked", "builder": "plex_id", "params": {}}]
    )

    with pytest.raises(ValidationError, match="ids"):
        build_config(document)


def test_a_builder_with_no_params_model_takes_any_params(free_form_builder):
    """Params are the builder's business; a builder that declares no model has
    claimed nothing for the config to check."""
    definition = CollectionDefinition(
        title="Anything", builder=free_form_builder, params={"whatever": [1, 2]}
    )

    assert definition.params == {"whatever": [1, 2]}


def test_an_expanding_definitions_placeholder_is_not_held_to_the_params_model():
    """The placeholder an expanding builder stands behind is never a collection
    and never carries the params of one -- ``imdb_award_years`` takes a year,
    and the year is only known once the dataset has been read. Holding the
    placeholder to that model would refuse the shipped default config."""
    definition = CollectionDefinition(
        title=AWARD_YEARS_TITLE, builder="imdb_award_years"
    )

    assert definition.params == {}


def test_a_non_empty_placeholder_params_is_still_held_to_the_params_model():
    """Fix round F1: the exemption above is narrowed to an *empty* params
    dict, not the whole builder. The engine never reads a placeholder's
    params -- ``expand`` builds its own -- so garbage left there used to load
    clean and stay wrong forever, never even a runtime error. Non-empty
    params on an expanding builder must fail here, at config load, exactly
    like any other builder's."""
    document = _document_with_definitions([
        {"title": AWARD_YEARS_TITLE, "builder": "imdb_award_years",
         "params": {"garbage": 1}}
    ])

    with pytest.raises(ValidationError, match="garbage"):
        build_config(document)


def test_the_example_config_declares_definitions_as_a_real_key():
    """`test_example_config_matches_schema.py` reads the example with
    `yaml.safe_load`, which cannot see comments -- so a purely commented-out
    example would not be gated by it at all. The empty list is the part that
    is."""
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert data["collections"]["definitions"] == []
    assert "definitions" in CollectionsConfig.model_fields


def _commented_definitions_example() -> dict:
    """The worked example that sits commented above `definitions: []`.

    Anchored on the `definitions:` line and taking the contiguous comment lines
    that follow it, because the same `  #   ` prefix is used elsewhere in the
    file (the notifications payload shapes).
    """
    prefix = "  #   "
    lines = EXAMPLE_CONFIG.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line == prefix + "definitions:")
    block = []
    for line in lines[start:]:
        if not line.startswith(prefix):
            break
        block.append(line[len("  # "):])
    return yaml.safe_load("\n".join(block))


def test_the_commented_example_definition_is_one_the_schema_accepts():
    """Documentation an operator uncomments has to load. This is the same
    reasoning as test_example_config_matches_schema.py, one level down: an
    example that means nothing is worse than no example."""
    block = _commented_definitions_example()

    config = CollectionsConfig(**block)

    assert [one.builder for one in config.definitions] == [
        "plex_id", "dynamic", "plex_id", "smart_url", "plex_search", "plex_search",
        "imdb_search",
    ]
    assert config.definitions[0].filters == {
        "year.gte": 2000, "content_rating": ["PG-13", "R"]
    }, "the documented filter has to be one the filter model accepts too"
    assert config.definitions[0].sync_to_mdb_list == "myuser/my-list"
    assert config.definitions[0].radarr_restrict is True
    assert config.definitions[0].item_radarr_tag == ["autoposter"], (
        "row 89's fields are documented on the item-level Hand Picked "
        "example, which the schema has to actually accept"
    )
    assert config.definitions[1].params["type"] == "decade", (
        "every key in the dynamic example is held to DynamicParams by "
        "CollectionDefinition itself, so a documented `<<token>>` nothing "
        "resolves, a dead upstream knob or an unknown type fails right here"
    )
    assert config.definitions[2].builder_level == "episode", (
        "row 88's builder_level is documented on its own list-builder "
        "example (Season Premieres), separate from Hand Picked, since it "
        "cannot combine with sync_to_mdb_list or the row 89 Arr fields"
    )


def test_a_definition_colliding_with_a_shipped_collection_is_rejected():
    """Definition identity is ``(library, title)``, so two definitions naming
    the same title in the same library are one collection built twice -- the
    second overwrites the first every pass, and the members hash flaps between
    them forever. The collision is knowable when the config loads, and that is
    where it is refused."""
    document = _document_with_definitions(
        [{"title": "IMDb Top 250", "builder": "plex_id", "params": {"ids": ["1"]}}]
    )

    with pytest.raises(ValidationError, match="IMDb Top 250"):
        build_config(document)


def test_the_collision_error_names_what_it_collided_with():
    document = _document_with_definitions(
        [{"title": "Ratings Collections", "builder": "plex_id", "params": {"ids": ["1"]}}]
    )

    with pytest.raises(ValidationError, match="built-in"):
        build_config(document)


def test_a_collision_with_a_group_separator_names_the_group_and_the_toggle():
    """"Ratings Collections" is not itself a built-in collection -- it is the
    content-ratings group's blank divider, folded into the reserved set by
    ``groups.separator_titles``. The refusal above says only "a built-in
    collection this service already builds", which sends an operator hunting
    through the built-in toggles for a collection by that name; there is none.
    The message has to name the group and ``collections.separators``, the
    setting that actually frees the title."""
    document = _document_with_definitions(
        [{"title": "Ratings Collections", "builder": "plex_id", "params": {"ids": ["1"]}}]
    )

    with pytest.raises(ValidationError, match="content_ratings.*separator"):
        build_config(document)


def test_a_collision_with_a_group_separator_is_freed_by_the_toggle():
    """The other half of the message's claim: switching the toggle off is
    what actually frees the title, mirroring
    ``test_a_definition_may_take_a_shipped_title_the_toggle_switched_off``."""
    document = _document_with_definitions(
        [{"title": "Ratings Collections", "builder": "plex_id", "params": {"ids": ["1"]}}]
    )
    document["collections"]["separators"] = False

    assert len(build_config(document).collections.definitions) == 1


def test_a_definition_may_not_claim_the_operator_groups_own_divider_title():
    """"Collections" is the one divider title the enumeration above cannot see.

    It reserves the separator titles of the groups ``default_definitions``
    implies -- and that runs "before operator config" (``sources.py``), so the
    operator group, which activates from these very ``definitions:`` entries,
    contributes nothing to the reserved set. Unrefused, both writers own the
    row: the list reconciler stores a members hash and a member sort title,
    ``reconcile_separator`` overwrites the summary, the sort title and the hash,
    and the next pass reverses it -- forever, silently. Reserved here for the
    same reason every other separator title is.
    """
    document = _document_with_definitions(
        [{"title": "Collections", "builder": "plex_id", "params": {"ids": ["1"]}}]
    )

    with pytest.raises(ValidationError, match="operator.*separator"):
        build_config(document)

    # The other half of the message's claim, as for the built-in dividers:
    # the toggle is what actually frees the title.
    document["collections"]["separators"] = False
    assert len(build_config(document).collections.definitions) == 1


def test_a_definition_claiming_the_fence_title_names_the_group_and_the_toggle():
    """"Other Collections" is the closing fence's title (``groups.TAIL_GROUP``),
    reserved through ``separator_titles`` like every other divider -- but the
    title-to-group map was built from ``separator_groups``, which enumerates the
    POSITIONED groups only and so has never held the fence. That gap made this
    one divider refuse through the generic "a built-in collection this service
    already builds" message: the same hunt for a nonexistent built-in the
    content-ratings case above was fixed to avoid. Built from
    ``separator_specs`` instead -- the same source the reserved set itself comes
    from -- so the two cannot disagree about which titles are separators.
    """
    document = _document_with_definitions(
        [{"title": "Other Collections", "builder": "plex_id", "params": {"ids": ["1"]}}]
    )

    with pytest.raises(ValidationError, match="other.*separator"):
        build_config(document)

    # And the same second half: the toggle frees it.
    document["collections"]["separators"] = False
    assert len(build_config(document).collections.definitions) == 1


def test_two_operator_definitions_may_not_share_a_title_in_one_library():
    document = _document_with_definitions([
        {"title": "Hand Picked", "builder": "plex_id", "params": {"ids": ["1"]}},
        {"title": "Hand Picked", "builder": "plex_id", "params": {"ids": ["2"]}},
    ])

    with pytest.raises(ValidationError, match="Hand Picked"):
        build_config(document)


def test_the_same_title_in_two_different_libraries_is_fine():
    """``(library, title)`` is the identity, so the same title in libraries
    that do not overlap is two collections, not a collision."""
    document = _document_with_definitions([
        {"title": "Hand Picked", "builder": "plex_id", "params": {"ids": ["1"]},
         "libraries": ["Movies"]},
        {"title": "Hand Picked", "builder": "plex_id", "params": {"ids": ["2"]},
         "libraries": ["TV Shows"]},
    ])

    assert len(build_config(document).collections.definitions) == 2


def test_a_definition_may_take_a_shipped_title_the_toggle_switched_off():
    """The charts are not built when ``charts: false``, so their titles are
    not taken. Refusing on a title nothing builds would be refusing a config
    that works."""
    document = _document_with_definitions(
        [{"title": "IMDb Top 250", "builder": "plex_id", "params": {"ids": ["1"]}}]
    )
    document["collections"]["charts"] = False

    assert len(build_config(document).collections.definitions) == 1


def test_the_delete_sweep_is_off_by_default_and_capped():
    """Deleting is opt-in, and even opted in it is capped: a config edit that
    drops every definition must not cascade into a wiped library."""
    config = load_config(EXAMPLE_CONFIG)
    assert config.collections.delete_unconfigured is False
    assert config.collections.max_deletes == 5


def test_definitions_are_editable_without_a_restart():
    """The whole section is live except `collections.enabled`; a definition
    added in Settings has to take effect on the next reconcile."""
    assert frozen_reason("collections.definitions") is None


# --- roadmap row 96: `filters:` on a definition ----------------------------
#
# The same discipline as `params:` above, and for the same reason: a filter is
# only ever read inside a pass, so anything wrong with one that is not caught
# here surfaces hours later as a collection that quietly stopped being right.
# Two classes of wrong, and the second is the one the parser alone cannot see:
#
# - the SHAPE (an attribute that does not exist, a modifier its type does not
#   take, a value that is not of that type) -- `filters.parse_filters`'s job;
# - the SOURCE TIER. The parser gates on the attribute TABLE, and the table
#   carries rows the item view deliberately cannot read. Phase B split that
#   half three ways rather than two: a `listing` row ships free, a
#   `tier2-batched` row ships through the engine's enrichment pass (one
#   batched `/library/metadata/{k1,k2,...}` read per definition), and a
#   `tier2-deferred` or `unprobed` row still refuses. `network` is the only
#   row left on the first of those refusals, and it is there because Plex
#   1.43.4 emits the attrib nowhere at all -- a fact no read at any tier can
#   change. Without the check, such a filter would load, run, and raise
#   `AttributeNotInListing` per item mid-pass -- silent until it runs, which
#   is exactly what load-time validation exists to prevent.


def test_a_filter_on_a_shipped_attribute_loads():
    document = _document_with_definitions([
        {"title": "Modern", "builder": "plex_id", "params": {"ids": ["1"]},
         "filters": {"year.gte": 2000, "content_rating": ["PG", "PG-13"]}}
    ])

    definition = build_config(document).collections.definitions[0]

    assert definition.filters == {"year.gte": 2000, "content_rating": ["PG", "PG-13"]}


def test_a_definition_without_filters_has_none():
    """None rather than `{}`: an empty mapping is a filter block an operator
    wrote and left empty, which `parse_filters` refuses."""
    assert CollectionDefinition(title="X", builder="plex_id",
                                params={"ids": ["1"]}).filters is None


def test_a_genre_filter_now_loads_because_the_batched_read_feeds_it():
    """The refusal this test used to assert, flipped by phase B. `genre` was
    refused because Plex's section listing truncates it to two per item, so
    there was no way to read it that would not answer WRONG. There is one now:
    the batched `/library/metadata/{k1,k2,...}` read returns the family full,
    and the engine pays exactly one such fetch for the definition's resolved
    set. So `genre:` loads -- and the thing that makes that safe is not this
    validator, it is the engine's refusal law (an item the batch did not answer
    for refuses the definition rather than evaluating without it)."""
    document = _document_with_definitions([
        {"title": "Scary", "builder": "plex_id", "params": {"ids": ["1"]},
         "filters": {"genre": "Horror"}}
    ])

    definition = build_config(document).collections.definitions[0]

    assert definition.filters == {"genre": "Horror"}


@pytest.mark.parametrize(
    "attribute", ["genre", "label", "collection",
                  "audio_language", "subtitle_language"],
)
def test_every_batched_filter_attribute_loads(attribute):
    """All five that moved, by name. A row left on the deferred tier while its
    accessor shipped -- or moved to the batched tier with no accessor -- fails
    here or in `tests/test_collection_filter_values.py`, which pin the two
    halves against the same table column."""
    definition = CollectionDefinition(
        title="X", builder="plex_id", params={"ids": ["1"]},
        filters={attribute: "whatever"},
    )

    assert definition.filters == {attribute: "whatever"}


def test_network_still_refuses_and_names_the_stranding():
    """The one row phase B could not move, and the copy has to say WHY it is
    different from the five that did: not "the listing is incomplete" (the
    batched read answers that now) but "Plex emits it nowhere at all", which no
    read at any tier can fix. The TVDb/TMDb route is named as a separate
    attribute under a distinct name rather than offered as a substitution --
    conflating it with `studio` is exactly the same-name-different-filter bug
    the table refuses elsewhere."""
    with pytest.raises(ValidationError) as raised:
        CollectionDefinition(
            title="E4 shows", builder="plex_all", params={},
            filters={"network": "E4"},
        )

    message = str(raised.value)
    assert "network" in message, "the attribute that is wrong"
    assert "tier2-deferred" in message, "and that it is deferred, not unknown"
    assert "emits it nowhere" in message, "and WHY, in phase B's terms"
    assert "distinct name" in message, "the TVDb/TMDb route, named not offered"
    assert "E4 shows" in message and "filters.network" in message, (
        "the definition and the key, the params discipline"
    )


def test_the_refusal_lists_the_batched_names_among_the_filterable_ones():
    """The "filterable today" tail is an operator's next move, so it has to
    grow with the tier: refusing `network` while listing only the nine listing
    rows would send someone to `plex_search` for a `genre:` filter that now
    works here."""
    with pytest.raises(ValidationError) as raised:
        CollectionDefinition(
            title="Stranded", builder="plex_all", params={},
            filters={"network": "E4"},
        )

    message = str(raised.value)
    for name in ("year", "studio", "genre", "label", "collection",
                 "audio_language", "subtitle_language"):
        assert name in message, name


def test_a_definition_filtering_on_country_says_the_listing_was_never_probed():
    """The ``unprobed`` tier's own copy, and the reason it is a separate tier.

    ``country`` IS one of Kometa's filter attributes, so the parser lets it
    through -- but 9a probed the seven attributes its own tier named and
    ``<Country>`` was not among them. Answering with the tier2-deferred
    sentence would cite a probe verdict that does not exist, which is the
    confident-and-wrong failure the probe exists to prevent.

    This test asked about ``plays`` until phase B, whose probe (d) walked the
    listing and answered the question -- which is what ``unprobed`` was for.
    Five rows still carry the tier, and this one is 10a's.
    """
    from autoposter.config.schema import CollectionDefinition

    with pytest.raises(ValueError) as error:
        CollectionDefinition(
            title="Nordic", builder="plex_all", filters={"country": "Denmark"}
        )
    message = str(error.value)
    assert "never probed" in message
    assert "plex_search" in message


def test_a_definition_filtering_on_the_per_account_rows_now_loads():
    """The other side of the row above: phase B's probe (d) moved ``plays``,
    ``last_played`` and ``user_rating`` onto the listing tier, so a definition
    naming them loads instead of refusing. All three are per-account -- the
    value is whichever account the pass token authenticated as -- which is a
    property of the ANSWER and not a reason to refuse the question."""
    from autoposter.config.schema import CollectionDefinition

    definition = CollectionDefinition(
        title="Rewatched", builder="plex_all",
        filters={"plays.gt": 3, "last_played.after": "2026-01-01",
                 "user_rating.gte": 8},
    )

    assert definition.filters is not None


def test_a_definition_filtering_on_unplayed_is_refused_by_the_parser():
    """A ``search-only`` row never reaches the source-tier loop: Kometa has no
    ``unplayed`` FILTER at all, so ``parse_filters`` refuses it one layer up
    and names the block it does belong to."""
    from autoposter.config.schema import CollectionDefinition

    with pytest.raises(ValueError) as error:
        CollectionDefinition(
            title="Unseen", builder="plex_all", filters={"unplayed": True}
        )
    assert "not a client-side filter" in str(error.value)


def test_an_unknown_filter_attribute_refuses_at_config_load():
    document = _document_with_definitions([
        {"title": "Typo", "builder": "plex_id", "params": {"ids": ["1"]},
         "filters": {"genres": "Horror"}}
    ])

    with pytest.raises(ValidationError) as raised:
        build_config(document)

    message = str(raised.value)
    assert "genres" in message and "Typo" in message
    assert "unknown filter attribute" in message


def test_a_modifier_the_attributes_type_does_not_take_refuses_at_config_load():
    """`.before` is a date modifier and `year` is an int. Kometa has no such
    spelling, and accepting it would mean guessing which of two readings the
    operator meant."""
    with pytest.raises(ValidationError, match="year") as raised:
        CollectionDefinition(
            title="Old", builder="plex_id", params={"ids": ["1"]},
            filters={"year.before": 2000},
        )

    assert "Old" in str(raised.value), "the definition, not just the key"


def test_a_filter_value_of_the_wrong_type_refuses_at_config_load():
    with pytest.raises(ValidationError, match="whole number"):
        CollectionDefinition(
            title="Old", builder="plex_id", params={"ids": ["1"]},
            filters={"year.gte": "the nineties"},
        )


def test_a_smart_definition_refuses_filters():
    """The `_smart_definitions_refuse_what_they_cannot_apply` class. Plex
    evaluates a smart
    collection's membership from its own filter, so there is no resolved list
    for a post-builder filter to narrow -- and 9c is where a smart definition's
    filter is written into the Plex-side search instead."""
    with pytest.raises(ValidationError, match="filters"):
        CollectionDefinition(
            title="Buckets", builder="cs_bucket", filters={"year.gte": 2000}
        )


def test_a_plex_search_definition_validates_its_params_at_load():
    definition = CollectionDefinition(
        title="Recent Horror",
        builder="plex_search",
        params={"all": {"genre": "Horror", "added": 90}, "sort_by": "added.desc"},
    )
    assert definition.builder == "plex_search"


def test_a_plex_search_definition_with_a_bad_key_names_the_title_and_the_key():
    with pytest.raises(ValueError) as error:
        CollectionDefinition(
            title="Recent Horror",
            builder="plex_search",
            params={"all": {"genre": "Horror"}, "sort": "added.desc"},
        )
    message = str(error.value)
    assert "Recent Horror" in message
    assert "sort_by" in message


def test_a_plex_search_definition_may_also_carry_a_client_side_filters_block():
    """D2(b): the server narrows and the client refines. Both are honoured and
    neither is folded into the other."""
    definition = CollectionDefinition(
        title="Recent Horror, well rated",
        builder="plex_search",
        params={"all": {"genre": "Horror"}},
        filters={"audience_rating.gte": 7},
    )
    assert definition.filters == {"audience_rating.gte": 7}


def test_plex_search_is_not_a_smart_builder_so_the_membership_knobs_apply():
    """A smart definition refuses limit/sync_mode/item_label/filters
    (schema.py:416-459) because Plex owns its membership. plex_search resolves
    real members through the engine, so all four mean what they always did."""
    definition = CollectionDefinition(
        title="Top 25 Horror",
        builder="plex_search",
        params={"all": {"genre": "Horror"}, "sort_by": "critic_rating.desc", "limit": 50},
        limit=25,
        sync_mode="append",
        item_label=["Horror night"],
    )
    assert definition.limit == 25
    assert definition.params["limit"] == 50


def test_a_definition_defaults_to_no_changes_webhook():
    """Row 19's per-collection webhook is opt-in per definition: an operator
    who configures none gets exactly the notifications they get today."""
    definition = CollectionDefinition(
        title="Hand Picked", builder="plex_id", params={"ids": ["1"]}
    )
    assert definition.changes_webhook == ""


# --- row 222: the definition's own poster URL -------------------------------


def test_a_definition_defaults_to_no_poster_url():
    """Row 222's field is opt-in: a definition that names none behaves exactly
    as it does today, which is what keeps every stored hash byte-identical."""
    definition = CollectionDefinition(
        title="Hand Picked", builder="plex_id", params={"ids": ["1"]}
    )
    assert definition.poster_url is None


def test_an_https_poster_url_is_accepted_verbatim():
    """Stored unmodified: the guard, not this validator, decides whether the
    address is fetchable, and a validator that rewrote the operator's string
    would make `GET /api/config` serve something they did not write."""
    definition = CollectionDefinition(
        title="DC Extended Universe",
        builder="plex_id",
        params={"ids": ["1"]},
        poster_url="https://posters.invalid/dceu.jpg",
    )
    assert definition.poster_url == "https://posters.invalid/dceu.jpg"


def test_an_http_poster_url_is_accepted():
    """`http` as well as `https`: the guard's own allowlist is both
    (`net/guard.py:96`), and a LAN image host on plain http is a real
    deployment shape."""
    definition = CollectionDefinition(
        title="DC Extended Universe",
        builder="plex_id",
        params={"ids": ["1"]},
        poster_url="http://posters.invalid/dceu.jpg",
    )
    assert definition.poster_url == "http://posters.invalid/dceu.jpg"


@pytest.mark.parametrize(
    "value,fragment",
    [
        ("ftp://posters.invalid/SECRET.jpg", "http:// or https://"),
        ("file:///etc/SECRET", "http:// or https://"),
        ("posters.invalid/SECRET.jpg", "http:// or https://"),
        ("https://operator:SECRET@posters.invalid/dceu.jpg", "user:password@"),
        ("https://", "names no host"),
        ("https://[SECRET-INTERNAL-HOST]/a.jpg", "not a parsable URL"),
        ("http://[::SECRET1", "not a parsable URL"),
    ],
)
def test_a_bad_poster_url_is_refused_without_echoing_it(value, fragment):
    """Row 213, held to the rule `tests/test_collection_config.py`'s
    credential-bearing-param test already states: what is SERVED is
    `errors()[...]["msg"]` -- `api/routes.py`'s ValidationError seam serves
    exactly those on five config endpoints -- so the message is what must
    never carry the operator's own string. An operator's poster URL can be an
    intranet address, or carry `user:password@` userinfo or a signed query
    parameter, which is the same class of value `net/guard.py:79-84` refuses
    to write into a log line.

    Every case's value carries the literal `SECRET`, so a validator that
    interpolated `{value!r}` would put it straight back into `served`.
    """
    with pytest.raises(ValidationError) as error:
        CollectionDefinition.model_validate({
            "title": "Leaky",
            "builder": "plex_id",
            "params": {"ids": ["1"]},
            "poster_url": value,
        })

    served = "; ".join(item["msg"] for item in error.value.errors())
    assert "SECRET" not in served, served
    assert "poster_url" in served
    assert fragment in served


def test_an_over_long_poster_url_is_refused_without_echoing_it():
    """A bound rather than none: this string is stored in the config document,
    served by `GET /api/config`, carried by an overrides export and folded into
    a hash on every pass. 2048 is generous next to any real image URL."""
    value = "https://posters.invalid/" + ("SECRET" * 400) + ".jpg"

    with pytest.raises(ValidationError) as error:
        CollectionDefinition.model_validate({
            "title": "Leaky",
            "builder": "plex_id",
            "params": {"ids": ["1"]},
            "poster_url": value,
        })

    served = "; ".join(item["msg"] for item in error.value.errors())
    assert "SECRET" not in served, served
    assert "2048" in served


@pytest.mark.parametrize(
    "value",
    [
        "https://posters.invalid/SECRET.jpg\n",
        "https://po\tsters.invalid/SECRET.jpg",
        " https://posters.invalid/SECRET.jpg",
    ],
)
def test_a_poster_url_with_whitespace_anywhere_is_refused(value):
    """Row 222 / I-1. `urlsplit` strips leading/trailing whitespace and drops
    embedded tab/CR/LF before checking the shape, but this validator must not
    store a different string than the one it checked -- so a value carrying
    whitespace anywhere is refused outright rather than silently rewritten.
    The operator fixes the paste; nothing here strips it for them.

    Each value carries the literal `SECRET`, so a validator that interpolated
    `{value!r}` would put it straight back into `served`, the same
    discriminating marker `test_a_bad_poster_url_is_refused_without_echoing_it`
    uses above."""
    with pytest.raises(ValidationError) as error:
        CollectionDefinition.model_validate({
            "title": "Leaky",
            "builder": "plex_id",
            "params": {"ids": ["1"]},
            "poster_url": value,
        })

    served = "; ".join(item["msg"] for item in error.value.errors())
    assert "SECRET" not in served, served
    assert "poster_url" in served
    assert "whitespace" in served


def test_a_clean_poster_url_is_still_accepted():
    """The whitespace refusal must not catch an ordinary value: a URL with no
    whitespace or control characters anywhere is accepted and returned
    byte-identical to what was passed in."""
    value = "https://posters.invalid/dceu.jpg"
    definition = CollectionDefinition(
        title="DC Extended Universe",
        builder="plex_id",
        params={"ids": ["1"]},
        poster_url=value,
    )
    assert definition.poster_url == value
