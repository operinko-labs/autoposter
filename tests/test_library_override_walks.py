"""The two config walks learn a ``dict[str, Model]`` arm (roadmap row 92).

Both walks find nested models through a closed vocabulary -- a bare model and
``list[Model]`` -- and both get a mapping of models wrong in their own way.
``config/descriptions.py`` serves it as NOTHING, so every leaf under it would
have no description and the completeness guard would fail.
``unknown_key_paths`` does something worse: ``_model_for`` unwraps
``get_args`` and answers with the mapping's VALUE model, so the walk descends
treating a Plex library name as a field name and reports ``libraries.Movies``
as an unknown setting -- a 422 on every save that names a real library.

Throwaway models here rather than the real schema, deliberately: this task
adds no schema field at all, and a test that needed one would make the arm
and the field impossible to review apart.
"""
from pydantic import BaseModel

from autoposter.config.descriptions import (
    _item_model_of,
    _mapping_model_of,
    _model_of,
    _walk,
)
from autoposter.config.overrides import (
    _mapping_model_for,
    _model_for,
    document_paths,
    empty_leaf_paths,
    unknown_key_paths,
)


class _Leaf(BaseModel):
    enabled: bool | None = None
    label: str | None = None


class _Block(BaseModel):
    section: _Leaf | None = None


class _Root(BaseModel):
    by_name: dict[str, _Block] = {}
    plain: str = ""


def test_the_mapping_helper_finds_the_value_model():
    """``dict[str, Model]``, and the two shapes it must not claim."""
    assert _mapping_model_of(_Root.model_fields["by_name"].annotation) is _Block
    assert _mapping_model_of(_Root.model_fields["plain"].annotation) is None
    assert _mapping_model_of(_Block.model_fields["section"].annotation) is None
    assert _mapping_model_for(_Root.model_fields["by_name"].annotation) is _Block
    assert _mapping_model_for(_Root.model_fields["plain"].annotation) is None


def test_a_mapping_of_primitives_is_still_left_alone():
    """Row 38's ``artwork.library_language_overrides`` shape.

    ``dict[str, dict[str, list[str]]]`` names no model at any depth, so both
    helpers must answer None and the walks must go on treating it as an
    opaque leaf. Teaching the arm must not change what that field does.
    """

    class _Ladders(BaseModel):
        per_library: dict[str, dict[str, list[str]]] = {}

    annotation = _Ladders.model_fields["per_library"].annotation
    assert _mapping_model_of(annotation) is None
    assert _mapping_model_for(annotation) is None
    assert _model_of(annotation) is None
    assert _item_model_of(annotation) is None


def test_the_descriptions_walk_reaches_a_mapped_model_under_a_wildcard():
    """The ``{}`` segment, beside the ``[]`` marker the walk already uses for
    a list of models. A library NAME is a whole path segment, so the wildcard
    is its own segment rather than a suffix -- ``by_name.{}.section.enabled``,
    not ``by_name{}.section.enabled`` -- and the settings page substitutes the
    real name into it."""
    described = _walk(_Root)
    assert "by_name" in described
    assert "by_name.{}.section" in described
    assert "by_name.{}.section.enabled" in described
    assert "by_name.{}.section.label" in described


def test_unknown_key_paths_reports_beneath_a_mapping_at_full_depth():
    """The 422 that matters: a typo under a real library name is reported at
    the leaf, and the library name itself is never reported at all."""
    document = {
        "by_name": {
            "Movies": {"section": {"enabled": True}},
            "TV Shows": {"section": {"nope": 1}},
        }
    }
    assert unknown_key_paths(document, _Root) == ["by_name.TV Shows.section.nope"]


def test_a_real_library_name_is_not_an_unknown_setting():
    """The defect this arm exists to close, stated on its own. Before it,
    ``_model_for`` answered ``_Block`` for the mapping annotation and the walk
    reported the KEY as an unknown field."""
    document = {"by_name": {"Movies": {"section": {"enabled": False}}}}
    assert unknown_key_paths(document, _Root) == []
    # And the primitive helper is deliberately unchanged: its contract is
    # "the model this annotation names", and the walk asks the mapping
    # question first rather than narrowing it.
    assert _model_for(_Root.model_fields["by_name"].annotation) is _Block


def test_an_unknown_section_under_a_library_is_still_reported():
    document = {"by_name": {"Movies": {"nosuch": {"enabled": False}}}}
    assert unknown_key_paths(document, _Root) == ["by_name.Movies.nosuch"]


def test_document_paths_already_reports_a_mapped_leaf():
    """Pinned rather than changed (read against the code): this walk is
    shape-driven and has never needed a model, so it already answers the
    dotted leaf the editor renders as "overridden" and ``_drop_refusal``
    counts. A change here would be a change for its own sake."""
    document = {"by_name": {"Movies": {"section": {"enabled": False, "label": "x"}}}}
    assert document_paths(document) == [
        "by_name.Movies.section.enabled",
        "by_name.Movies.section.label",
    ]


def test_empty_leaf_paths_still_refuses_a_cleared_library_block():
    """The freezing hazard's other half, also unchanged: a library row cleared
    to ``{}`` is refused outright, so nothing can seed the editor into storing
    a whole section wholesale on the next save."""
    assert empty_leaf_paths({"by_name": {"Movies": {}}}) == ["by_name.Movies"]
    assert empty_leaf_paths({"by_name": {}}) == ["by_name"]
