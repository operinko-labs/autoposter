"""Pins the plexapi surface this phase depends on.

Not a test of our code. It exists because a hand-written test double will
happily implement an API that plexapi does not have -- which has already
cost this project three bugs that only appeared against a real server. This
asserts against the real classes, offline.
"""
import inspect

import pytest
from plexapi.collection import Collection
from plexapi.library import LibrarySection


@pytest.mark.parametrize(
    "name,required",
    [
        ("createCollection", ["title", "smart", "libtype", "sort", "filters"]),
        ("listFilterChoices", ["field", "libtype"]),
        ("collection", ["title"]),
    ],
)
def test_library_section_methods_take_the_parameters_we_pass(name, required):
    method = getattr(LibrarySection, name)
    params = inspect.signature(method).parameters
    for parameter in required:
        assert parameter in params, "%s lost its %r parameter" % (name, parameter)


@pytest.mark.parametrize(
    "name,required",
    [
        ("updateFilters", ["libtype", "sort", "filters"]),
        ("addLabel", ["labels"]),
        ("removeLabel", ["labels"]),
        ("editSummary", ["summary"]),
    ],
)
def test_collection_methods_take_the_parameters_we_pass(name, required):
    method = getattr(Collection, name)
    params = inspect.signature(method).parameters
    for parameter in required:
        assert parameter in params, "%s lost its %r parameter" % (name, parameter)


def test_collection_exposes_filters_and_smart():
    """`smart` is assigned in _loadData rather than declared, so accept either."""
    assert hasattr(Collection, "filters")
    assert "smart" in inspect.getsource(Collection._loadData) or hasattr(Collection, "smart")


def test_collections_are_listable_from_a_section():
    assert callable(LibrarySection.collections)


def test_collection_delete_exists_but_we_never_call_it():
    """Deleting a collection is out of scope for this phase. The method is
    pinned here so that if a later phase adds deletion, it is a deliberate
    change against a known API rather than an accident."""
    assert callable(Collection.delete)


def test_collection_labels_is_lazy_and_must_be_reloaded_explicitly():
    """``labels`` is a ``cached_data_property`` -- ``_loadData`` never sets
    it, so a collection fetched via ``section.collections()`` only has it
    populated by an implicit reload gated on ``plexapi.autoreload``. That
    global can be turned off, so our ownership check must call
    ``collection.reload()`` itself rather than rely on it."""
    from plexapi.base import cached_data_property

    assert isinstance(Collection.__dict__.get("labels"), cached_data_property)
    assert callable(Collection.reload)
