"""What ``unmanaged_prior_collections`` reports after an adoption pass.

It reports titles only -- there is no argument for a service deciding what
to do with a collection it does not manage, so nothing here modifies,
claims or deletes anything.
"""
from autoposter.collections.service import unmanaged_prior_collections
from test_collection_adoption import FakeCollection

ADOPT_FROM = ["Kometa"]


def test_a_managed_title_is_not_reported_even_though_it_carries_a_prior_label():
    """Whether it was adopted or left as a conflict, either way it was
    already accounted for during the reconcile pass."""
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    assert unmanaged_prior_collections([collection], {"Age 17+ Movies"}, ADOPT_FROM) == []


def test_an_unmanaged_title_carrying_a_prior_label_is_reported():
    collection = FakeCollection("Ratings Collections", labels=["Kometa"])
    assert unmanaged_prior_collections(
        [collection], {"Age 17+ Movies"}, ADOPT_FROM
    ) == ["Ratings Collections"]


def test_an_unlabelled_collection_is_never_reported_regardless_of_title():
    """The operator's hand-made collections and Plex's own franchise
    collections carry no label at all -- an unmanaged title alone is never
    enough to be reported."""
    collection = FakeCollection("The Ninja Trilogy")
    assert unmanaged_prior_collections([collection], set(), ADOPT_FROM) == []


def test_results_are_sorted():
    collections = [
        FakeCollection("Zebra Collection", labels=["Kometa"]),
        FakeCollection("Apple Collection", labels=["Kometa"]),
    ]
    assert unmanaged_prior_collections(collections, set(), ADOPT_FROM) == [
        "Apple Collection", "Zebra Collection",
    ]


def test_an_empty_adopt_from_reports_nothing():
    collection = FakeCollection("Ratings Collections", labels=["Kometa"])
    assert unmanaged_prior_collections([collection], set(), []) == []
