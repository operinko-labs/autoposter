"""Roadmap row 269 -- the two definition keys, their refusals, and the second
sort-title source.

Every refusal here is a LOAD-time refusal: the failure mode of a definition
key that loads and applies nothing is a setting that reads as configured
forever (``config/schema.py``'s ``_REFUSED_PLAYLIST_FIELDS`` states the rule).
"""
import pytest
from pydantic import ValidationError

from autoposter.collections.engine import _INHERITED_BY_EXPANSION, _completed
from autoposter.config.schema import (
    CollectionDefinition,
    OperationsConfig,
    OperationsOverride,
    PlaylistDefinition,
)


def test_both_keys_default_off():
    definition = CollectionDefinition(title="Bond", builder="tmdb_list", params={"id": 1})
    assert definition.member_sort is False
    assert definition.create_collection is True


def test_member_sort_loads_on_a_list_builder():
    definition = CollectionDefinition(
        title="Bond", builder="tmdb_list", params={"id": 1}, member_sort=True,
    )
    assert definition.member_sort is True


def test_member_sort_is_refused_on_a_smart_builder():
    """A smart builder hands Plex a filter and holds no ordered member list,
    so there is no order to record -- structural, not a per-builder taste."""
    with pytest.raises(ValidationError, match="member_sort"):
        CollectionDefinition(
            title="Genres", builder="dynamic", params={"type": "genre"}, member_sort=True,
        )


def test_create_collection_false_is_refused_on_a_smart_builder():
    with pytest.raises(ValidationError, match="create_collection"):
        CollectionDefinition(
            title="Genres", builder="dynamic", params={"type": "genre"},
            create_collection=False,
        )


def test_create_collection_false_needs_member_sort():
    with pytest.raises(ValidationError, match="create_collection"):
        CollectionDefinition(
            title="Bond", builder="tmdb_list", params={"id": 1}, create_collection=False,
        )
    assert CollectionDefinition(
        title="Bond", builder="tmdb_list", params={"id": 1},
        member_sort=True, create_collection=False,
    ).create_collection is False


def test_the_keys_are_refused_on_a_playlist():
    with pytest.raises(ValidationError, match="member_sort"):
        PlaylistDefinition(title="P", builder="tmdb_list", params={"id": 1}, member_sort=True)
    with pytest.raises(ValidationError, match="create_collection"):
        PlaylistDefinition(
            title="P", builder="tmdb_list", params={"id": 1}, create_collection=False,
        )


def test_expanded_units_inherit_both_keys():
    """Row 141's inheritance: the placeholder is the only definition an
    operator writes for a family, so the franchise pack works with one line."""
    assert "member_sort" in _INHERITED_BY_EXPANSION
    assert "create_collection" in _INHERITED_BY_EXPANSION
    placeholder = CollectionDefinition(
        title="Franchises", builder="facts_family", params={"type": "tmdb_collection"},
        member_sort=True, create_collection=False,
    )
    unit = CollectionDefinition(title="Alien", builder="tmdb_collection", params={"id": 8091})
    completed = _completed(placeholder, unit)
    assert completed.member_sort is True
    assert completed.create_collection is False


def test_collections_is_a_second_sort_title_source():
    assert OperationsConfig(sort_title_source="collections").sort_title_source == "collections"
    assert OperationsOverride(sort_title_source="collections").sort_title_source == "collections"
    with pytest.raises(ValidationError):
        OperationsConfig(sort_title_source="imdb_list")
