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
from autoposter.config.schema import (
    CollectionDefinition,
    CollectionsConfig,
    definition_config_hash,
)

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


def test_the_definition_hash_ignores_key_order(free_form_builder):
    """The hash detects edits to a definition. YAML key order is not an edit --
    re-ordering keys in the file must not orphan the live collection.

    Built on a builder with no ``params_model``: two keys are what makes this
    test mean anything, and a params model that accepted a second, arbitrary
    key would not be doing its job (see the load-time checks below)."""
    one = CollectionDefinition(
        title="X", builder=free_form_builder, params={"ids": ["1", "2"], "extra": 1}
    )
    other = CollectionDefinition(
        builder=free_form_builder, params={"extra": 1, "ids": ["1", "2"]}, title="X"
    )

    assert definition_config_hash(one) == definition_config_hash(other)


def test_the_definition_hash_changes_when_a_setting_changes():
    base = CollectionDefinition(title="X", builder="plex_id", params={"ids": ["1"]})
    changed = [
        CollectionDefinition(title="X", builder="plex_id", params={"ids": ["2"]}),
        CollectionDefinition(
            title="X", builder="plex_id", params={"ids": ["1"]}, sync_mode="append"
        ),
        CollectionDefinition(
            title="X", builder="plex_id", params={"ids": ["1"]}, limit=10
        ),
        CollectionDefinition(
            title="X", builder="plex_id", params={"ids": ["1"]}, summary="new"
        ),
    ]

    hashes = {definition_config_hash(d) for d in [base, *changed]}
    assert len(hashes) == len(changed) + 1


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

    assert len(config.definitions) == 1
    assert config.definitions[0].builder in ("plex_id",)


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
