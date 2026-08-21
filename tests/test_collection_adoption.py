"""Taking over collections created by the tool being replaced.

The dangerous direction here is adopting something that is not ours to adopt:
the Movies library holds 269 Plex franchise collections and five the operator
made by hand, none of which carry a label. Those must stay untouchable.
"""
from sqlalchemy import select

from autoposter.collections.lists import reconcile_list_collection
from autoposter.collections.reconcile import (
    claim_ownership,
    prior_tool_label,
    reconcile_content_ratings,
)
from autoposter.db.models import ManagedCollection
from test_collection_lists import FakeCollection as ListCollection
from test_collection_lists import FakeItem
from test_collection_lists import FakeSection as ListSection
from test_collection_reconcile import FakeCollection as SmartCollection
from test_collection_reconcile import FakeSection as SmartSection

LABEL = "autoposter"


class FakeCollection:
    def __init__(self, title, labels=()):
        self.title = title
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.added = []
        self.removed = []

    @property
    def labels(self):
        return self._labels

    def reload(self):
        pass

    def addLabel(self, labels, locked=True):
        self.added.append(labels)
        self._labels.append(type("L", (), {"tag": labels})())

    def removeLabel(self, labels, locked=True):
        self.removed.append(labels)
        self._labels = [x for x in self._labels if x.tag != labels]


def test_a_prior_tool_label_is_recognised():
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    assert prior_tool_label(collection, ["Kometa"]) == "Kometa"


def test_an_unlabelled_collection_is_never_eligible():
    """The operator's hand-made collections carry no label at all."""
    assert prior_tool_label(FakeCollection("The Ninja Trilogy"), ["Kometa"]) is None


def test_another_tools_label_is_not_eligible_unless_configured():
    """Maintainerr's 'Deleted Soon' carries its own label and is not ours."""
    collection = FakeCollection("Deleted Soon", labels=["Collection managed by Maintainerr"])
    assert prior_tool_label(collection, ["Kometa"]) is None


def test_an_empty_adopt_list_disables_recognition_entirely():
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    assert prior_tool_label(collection, []) is None


def test_claiming_adds_our_label():
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    claim_ownership(collection, LABEL, "Kometa", remove_prior=False)
    assert collection.added == [LABEL]


def test_the_prior_label_is_kept_by_default():
    """Keeping both is reversible; stripping labels is not."""
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    claim_ownership(collection, LABEL, "Kometa", remove_prior=False)
    assert collection.removed == []
    assert {label.tag for label in collection.labels} == {"Kometa", LABEL}


def test_the_prior_label_can_be_removed_on_request():
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    claim_ownership(collection, LABEL, "Kometa", remove_prior=True)
    assert collection.removed == ["Kometa"]
    assert {label.tag for label in collection.labels} == {LABEL}


def test_extra_labels_are_left_alone():
    """The Oscars year collections also carry 'Oscars Winners Awards'."""
    collection = FakeCollection("Oscars Winners 2026", labels=["Oscars Winners Awards", "Kometa"])
    claim_ownership(collection, LABEL, "Kometa", remove_prior=True)
    assert "Oscars Winners Awards" in {label.tag for label in collection.labels}


# --- End-to-end: both reconcilers, driven through the real collision branch ---


async def test_smart_adopt_off_leaves_a_kometa_collision_as_a_conflict(session):
    theirs = SmartCollection("Age 17+ Movies", labels=["Kometa"])
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False, adopt=False,
    )
    assert theirs.updated_filters is None
    assert theirs.labels_added == []
    assert any("conflict" in a.lower() for a in actions)


async def test_smart_adopt_on_claims_and_reconciles(session):
    theirs = SmartCollection("Age 17+ Movies", labels=["Kometa"])
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        adopt=True, adopt_from=["Kometa"],
    )
    assert theirs.labels_added == [LABEL]
    assert theirs.updated_filters is not None
    assert any("claimed" in a.lower() for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert any(r.title == "Age 17+ Movies" for r in rows)


async def test_smart_adopt_on_does_not_claim_an_unlabelled_collision(session):
    theirs = SmartCollection("Age 17+ Movies")  # the operator's, no label at all
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        adopt=True, adopt_from=["Kometa"],
    )
    assert theirs.labels_added == []
    assert theirs.updated_filters is None
    assert any("conflict" in a.lower() for a in actions)


async def test_smart_dry_run_reports_would_adopt_and_claims_nothing(session):
    theirs = SmartCollection("Age 17+ Movies", labels=["Kometa"])
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=True,
        adopt=True, adopt_from=["Kometa"],
    )
    assert theirs.labels_added == []
    assert any("would adopt" in a.lower() for a in actions)


async def test_lists_adopt_off_leaves_a_kometa_collision_as_a_conflict(session):
    theirs = ListCollection("IMDb Top 250", items=[FakeItem("a")], labels=["Kometa"])
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False, adopt=False,
    )
    assert theirs.added == []
    assert LABEL not in {label.tag for label in theirs.labels}
    assert any("conflict" in a.lower() for a in actions)


async def test_lists_adopt_on_claims_and_reconciles(session):
    theirs = ListCollection("IMDb Top 250", items=[FakeItem("a")], labels=["Kometa"])
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
        adopt=True, adopt_from=["Kometa"],
    )
    assert LABEL in {label.tag for label in theirs.labels}
    assert [i.ratingKey for i in theirs.added] == ["b"]
    assert any("claimed" in a.lower() for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert any(r.title == "IMDb Top 250" for r in rows)


async def test_lists_adopt_on_does_not_claim_an_unlabelled_collision(session):
    theirs = ListCollection("IMDb Top 250", items=[FakeItem("a")], labels=[])
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("b")], LABEL, dry_run=False, adopt=True, adopt_from=["Kometa"],
    )
    assert theirs.added == []
    assert theirs.removed == []
    assert LABEL not in {label.tag for label in theirs.labels}
    assert any("conflict" in a.lower() for a in actions)


async def test_lists_dry_run_reports_would_adopt_and_claims_nothing(session):
    theirs = ListCollection("IMDb Top 250", items=[FakeItem("a")], labels=["Kometa"])
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=True,
        adopt=True, adopt_from=["Kometa"],
    )
    assert LABEL not in {label.tag for label in theirs.labels}
    assert any("would adopt" in a.lower() for a in actions)
