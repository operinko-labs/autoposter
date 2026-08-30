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
    assert leftover.reloaded is True, (
        "one reload, and only for a candidate about to be REPORTED: the family "
        "check below has to read the labels, and a family label cannot be asked "
        "for server-side because it is matched by prefix"
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


# --- A dynamic family's members are ours, and are not "left behind" ---
#
# Measured 2026-08-30: a pass that had just created, labelled and prefixed 19
# genre collections logged "55 prior-tool collection(s) left behind" naming
# them. ``definition_titles``' smart branch cannot enumerate a family offline
# (roadmap row 135), so the report has to recognise the family LABEL instead --
# which is on the collection, and is ours whatever the report can enumerate.
# Plex canonicalises its case too: the source prefix is "autoposter-dynamic: "
# and the server stores "Autoposter-dynamic: ".


def test_a_family_labelled_collection_is_not_reported_as_left_behind():
    """The false positive, exactly: 19 collections the same pass built."""
    ours = FakeCollection(
        "Action Movies", labels=["Kometa", "Autoposter-dynamic: Genres"],
    )
    section = FakeSection([ours])
    assert unmanaged_prior_collections(section, "Movie", _config()) == []
    assert section.label_filters == ["Kometa"]


def test_every_family_prefix_counts_not_just_the_dynamic_one():
    section = FakeSection([
        FakeCollection("Set In Japan", labels=["Kometa", "autoposter-facts: Countries"]),
        FakeCollection("Tom Hanks", labels=["Kometa", "Autoposter-credits: Actors"]),
    ])
    assert unmanaged_prior_collections(section, "Movie", _config()) == []


def test_a_family_label_does_not_swallow_a_look_alike():
    """Prefix-based, so the boundary matters: a LABEL that merely starts with
    the word is not one of ours.

    Both look-alikes are labels, not the title -- ``_family_labelled`` reads
    labels, so a look-alike in the title alone would pass against a prefix set
    of ``autoposter``, of ``autoposter-``, or of nothing at all, and this is the
    only boundary test the widened match has. ``Autoposter-dynamics`` pins the
    separator specifically: the prefixes carry ``": "`` precisely so a family
    name cannot run into a neighbouring label.
    """
    section = FakeSection([FakeCollection(
        "Autoposter Fan Picks",
        labels=["Kometa", "Autoposter Fan Picks", "Autoposter-dynamics"],
    )])
    assert unmanaged_prior_collections(section, "Movie", _config()) == [
        "Autoposter Fan Picks"
    ]


def test_an_adopt_from_entry_that_is_our_own_label_asks_the_server_nothing():
    """``collections(label=...)`` is case-insensitive server-side, so an
    ``adopt_from`` of ``autoposter`` against ``ownership_label='autoposter'``
    returned every collection this service owns as a prior tool's candidate --
    the other half of the 55-title report."""
    ours = FakeCollection("Action Movies", labels=["autoposter"])
    section = FakeSection([ours])
    assert unmanaged_prior_collections(
        section, "Movie", _config(adopt_from=["Kometa", "autoposter"])
    ) == []
    assert section.label_filters == ["Kometa"]
