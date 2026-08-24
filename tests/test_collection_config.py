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

from autoposter.config.live import frozen_reason
from autoposter.config.loader import build_config, load_config, read_config_document
from autoposter.config.schema import (
    CollectionDefinition,
    CollectionsConfig,
    definition_config_hash,
)

EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


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


def test_the_definition_hash_ignores_key_order():
    """The hash detects edits to a definition. YAML key order is not an edit --
    re-ordering keys in the file must not orphan the live collection."""
    one = CollectionDefinition(
        title="X", builder="plex_id", params={"ids": ["1", "2"], "extra": 1}
    )
    other = CollectionDefinition(
        builder="plex_id", params={"extra": 1, "ids": ["1", "2"]}, title="X"
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


def test_definitions_are_editable_without_a_restart():
    """The whole section is live except `collections.enabled`; a definition
    added in Settings has to take effect on the next reconcile."""
    assert frozen_reason("collections.definitions") is None
