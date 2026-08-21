"""What ``unmanaged_prior_collections`` reports after an adoption pass.

It reports titles only -- there is no argument for a service deciding what
to do with a collection it does not manage, so nothing here modifies,
claims or deletes anything.

The prior-tool label is matched by the *server*: ``labels`` is a
``cached_data_property`` that ``section.collections()`` never populates, so
reading it here would cost a ``reload()`` GET for every one of the library's
305 collections on every pass. ``FakeSection`` below therefore filters the
way ``LibrarySection.collections(label=...)`` does -- and hands back
collections whose ``labels`` are empty until reloaded, so any implementation
that goes back to filtering client-side reports nothing and fails.
"""
from types import SimpleNamespace

from autoposter.collections.service import unmanaged_prior_collections
from autoposter.config.schema import CollectionsConfig
from test_collection_adoption import FakeCollection

MAINTAINERR = "Collection managed by Maintainerr"


class FakeSection:
    """Stands in for ``LibrarySection.collections(label=...)``.

    The label match happens here, on the "server", against labels the
    returned objects do not expose until ``reload()`` -- exactly as plexapi
    behaves. ``label_filters`` records what was asked for, so the request
    count is assertable.
    """

    def __init__(self, collections=()):
        self._collections = list(collections)
        self.label_filters = []

    def collections(self, label=None, **kwargs):
        self.label_filters.append(label)
        if label is None:
            return list(self._collections)
        return [
            collection for collection in self._collections
            if label in {tag.tag for tag in collection._real_labels}
        ]


def _config(adopt_from=("Kometa",), protect_labels=(), **overrides):
    options = {"charts": False, "awards": False, "separators": False}
    options.update(overrides)
    return SimpleNamespace(
        collections=CollectionsConfig(
            adopt_from=list(adopt_from), protect_labels=list(protect_labels), **options,
        )
    )


def test_a_managed_title_is_not_reported_even_though_it_carries_a_prior_label():
    """Whether it was adopted or left as a conflict, either way it was
    already accounted for during the reconcile pass."""
    section = FakeSection([FakeCollection("Age 17+ Movies", labels=["Kometa"])])
    assert unmanaged_prior_collections(section, "Movie", _config()) == []


def test_an_unmanaged_title_carrying_a_prior_label_is_reported():
    section = FakeSection([FakeCollection("Ratings Collections", labels=["Kometa"])])
    assert unmanaged_prior_collections(section, "Movie", _config()) == [
        "Ratings Collections"
    ]


def test_the_separator_stops_being_reported_once_it_is_managed():
    section = FakeSection([FakeCollection("Ratings Collections", labels=["Kometa"])])
    assert unmanaged_prior_collections(section, "Movie", _config(separators=True)) == []


def test_a_dynamically_named_oscars_year_title_is_recognised_as_managed():
    """Those titles are not statically listable, so they are recovered from
    the collections handed to ``_managed_titles`` -- which are now only the
    prior-labelled candidates, not the whole library."""
    section = FakeSection([FakeCollection("Oscars Winners 2026", labels=["Kometa"])])
    assert unmanaged_prior_collections(section, "Movie", _config(awards=True)) == []


def test_the_match_is_made_by_the_server_side_label_filter():
    """The load-bearing assertion of this module. The candidates come back
    with empty ``labels`` -- as real ones do -- so nothing here may decide
    membership by reading them, and one filtered request must replace the
    per-collection ``reload()`` scan of the whole library."""
    leftover = FakeCollection("Ratings Collections", labels=["Kometa"])
    section = FakeSection([leftover, FakeCollection("The Ninja Trilogy")])

    assert unmanaged_prior_collections(section, "Movie", _config()) == [
        "Ratings Collections"
    ]
    assert section.label_filters == ["Kometa"], (
        "the whole library must not be listed and rescanned -- one "
        "server-side-filtered request per adopt_from entry"
    )
    assert leftover.reloaded is False, (
        "with no protect_labels configured there is nothing left to read off "
        "the collection, so it must not be reloaded either"
    )


def test_an_unlabelled_collection_is_never_reported_regardless_of_title():
    """The operator's hand-made collections and Plex's own franchise
    collections carry no label at all -- an unmanaged title alone is never
    enough to be reported."""
    section = FakeSection([FakeCollection("The Ninja Trilogy")])
    assert unmanaged_prior_collections(section, "Movie", _config()) == []


def test_another_tools_label_is_not_reported_unless_it_is_in_adopt_from():
    section = FakeSection([FakeCollection("Deleted Soon", labels=[MAINTAINERR])])
    assert unmanaged_prior_collections(section, "Movie", _config()) == []


def test_a_protected_collection_is_never_reported_even_carrying_a_prior_label():
    """A Maintainerr collection the previous tool also labelled. Reporting it
    as 'left behind' invites the operator to act on the one collection this
    service works hardest never to touch."""
    theirs = FakeCollection("Deleted Soon", labels=[MAINTAINERR, "Kometa"])
    section = FakeSection([theirs])

    assert unmanaged_prior_collections(
        section, "Movie", _config(protect_labels=[MAINTAINERR])
    ) == []
    assert theirs.reloaded is True, "the protected check has to fetch the labels"


def test_a_leftover_without_a_protected_label_survives_the_protect_check():
    section = FakeSection([FakeCollection("Ratings Collections", labels=["Kometa"])])
    assert unmanaged_prior_collections(
        section, "Movie", _config(protect_labels=[MAINTAINERR])
    ) == ["Ratings Collections"]


def test_results_are_sorted_and_deduplicated_across_adopt_from_entries():
    section = FakeSection([
        FakeCollection("Zebra Collection", labels=["Kometa"]),
        FakeCollection("Apple Collection", labels=["Kometa", "Plex Meta Manager"]),
    ])
    assert unmanaged_prior_collections(
        section, "Movie", _config(adopt_from=["Kometa", "Plex Meta Manager"])
    ) == ["Apple Collection", "Zebra Collection"]


def test_an_empty_adopt_from_reports_nothing_and_asks_the_server_nothing():
    section = FakeSection([FakeCollection("Ratings Collections", labels=["Kometa"])])
    assert unmanaged_prior_collections(section, "Movie", _config(adopt_from=[])) == []
    assert section.label_filters == []
