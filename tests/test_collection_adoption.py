"""Taking over collections created by the tool being replaced.

The dangerous direction here is adopting something that is not ours to adopt:
the Movies library holds 269 Plex franchise collections and five the operator
made by hand, none of which carry a label. Those must stay untouchable.
"""
from sqlalchemy import select

from autoposter.collections.lists import _members_hash, reconcile_list_collection
from autoposter.collections.reconcile import (
    claim_ownership,
    has_label,
    load_labels,
    prior_tool_label,
    protected_label,
    reconcile_content_ratings,
    resolve_collision,
)
from autoposter.db.models import ManagedCollection
from test_collection_lists import FakeCollection as ListCollection
from test_collection_lists import FakeItem
from test_collection_lists import FakeSection as ListSection
from test_collection_reconcile import FakeCollection as SmartCollection
from test_collection_reconcile import FakeSection as SmartSection

LABEL = "autoposter"


class FakeCollection:
    """Mirrors plexapi's lazy ``labels``: empty until ``reload()`` is called,
    just like a real ``Collection`` fetched from ``section.collections()``.

    Modelling that faithfully is the whole point of this double. ``labels``
    is a ``cached_data_property`` that ``_loadData`` never sets, so a fake
    that hands its labels over without a reload lets a missing
    ``load_labels()`` call pass every test here and then read nothing at all
    against a real server.
    """

    def __init__(self, title, labels=()):
        self.title = title
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self.reloaded = False
        self.added = []
        self.removed = []

    @property
    def labels(self):
        return self._labels

    def reload(self, **kw):
        self.reloaded = True
        self._labels = self._real_labels

    def addLabel(self, labels, locked=True):
        self.added.append(labels)
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels

    def removeLabel(self, labels, locked=True):
        self.removed.append(labels)
        self._real_labels = [x for x in self._real_labels if x.tag != labels]
        self._labels = self._real_labels


def _loaded(title, labels=()):
    """A collection whose labels have already been fetched, as every caller
    of the pure readers below is required to have done."""
    collection = FakeCollection(title, labels=labels)
    load_labels(collection)
    return collection


def test_a_prior_tool_label_is_recognised():
    assert prior_tool_label(_loaded("Age 17+ Movies", ["Kometa"]), ["Kometa"]) == "Kometa"


def test_the_readers_are_pure_and_see_nothing_without_load_labels():
    """``prior_tool_label``, ``has_label`` and ``protected_label`` no longer
    reload -- ``resolve_collision`` does it once for all three. A caller that
    forgets must come up empty here, not silently read a fake's eager list."""
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa", LABEL])
    assert prior_tool_label(collection, ["Kometa"]) is None
    assert has_label(collection, LABEL) is False
    assert protected_label(collection, ["Kometa"]) is None
    assert collection.reloaded is False


def test_a_collision_fetches_the_labels_exactly_once():
    """Three readers each forcing their own reload cost three GETs per
    collision -- roughly 150 wasted requests on a Movies pass."""
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    reloads = []
    collection.reload = lambda **kw: (
        reloads.append(1), setattr(collection, "_labels", collection._real_labels)
    )

    ok, message = resolve_collision(
        collection, LABEL, adopt=True, adopt_from=["Kometa"],
        remove_prior=False, dry_run=False, protect_labels=["Collection managed by Maintainerr"],
    )

    assert ok is True and "claimed" in message
    assert len(reloads) == 1


def test_an_unlabelled_collection_is_never_eligible():
    """The operator's hand-made collections carry no label at all."""
    assert prior_tool_label(_loaded("The Ninja Trilogy"), ["Kometa"]) is None


def test_another_tools_label_is_not_eligible_unless_configured():
    """Maintainerr's 'Deleted Soon' carries its own label and is not ours."""
    collection = _loaded("Deleted Soon", ["Collection managed by Maintainerr"])
    assert prior_tool_label(collection, ["Kometa"]) is None


def test_an_empty_adopt_list_disables_recognition_entirely():
    assert prior_tool_label(_loaded("Age 17+ Movies", ["Kometa"]), []) is None


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
    # Reconciled: the claimed collection got the filter-replacing PUT.
    assert any(
        one["key"].startswith("/library/collections/%s/items?" % theirs.ratingKey)
        for one in section.queries
    ), section.queries
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


async def test_lists_a_claim_is_reported_even_when_the_membership_already_matches(session):
    """The hash gate short-circuits a pass with nothing to change -- but the
    claim it ran first already wrote a label to Plex. Dropping that action
    reports '0 action(s)' for a pass that changed the collection's
    ownership. ``reconcile.py`` reports the claim in the same situation."""
    items = [FakeItem("a")]
    theirs = ListCollection("IMDb Top 250", items=items, labels=["Kometa"])
    section = ListSection([theirs])
    session.add(ManagedCollection(
        library="Movies", title="IMDb Top 250", kind="manual",
        plex_rating_key=theirs.ratingKey, definition_hash=_members_hash(items, None),
    ))
    await session.flush()

    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", items, LABEL,
        dry_run=False, adopt=True, adopt_from=["Kometa"],
    )

    assert LABEL in {label.tag for label in theirs.labels}
    assert any("claimed" in action.lower() for action in actions), (
        "the claim wrote a label to Plex -- the hash gate must not swallow it"
    )


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
