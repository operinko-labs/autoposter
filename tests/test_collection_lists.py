"""Reconciling regular (list) collections.

These use sync semantics: members not re-selected are removed. That makes an
empty desired-set catastrophic, which is why several tests here are about
doing nothing.
"""
from sqlalchemy import select

from autoposter.collections.lists import reconcile_list_collection
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


class FakeItem:
    def __init__(self, key):
        self.ratingKey = key
        self.title = key


class FakeCollection:
    def __init__(self, title, items=(), labels=(LABEL,)):
        self.title = title
        self.ratingKey = "c-" + title
        self._items = list(items)
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.added = []
        self.removed = []
        self.moves = []
        self.sort_set = None
        self.summary_set = None
        self.reloaded = 0

    def reload(self):
        self.reloaded += 1

    @property
    def labels(self):
        return self._labels

    def items(self):
        return list(self._items)

    def addItems(self, items):
        self.added.extend(items)
        self._items.extend(items)

    def removeItems(self, items):
        self.removed.extend(items)
        for item in items:
            self._items = [i for i in self._items if i.ratingKey != item.ratingKey]

    def moveItem(self, item, after=None):
        self.moves.append((item.ratingKey, after.ratingKey if after else None))

    def sortUpdate(self, sort=None):
        self.sort_set = sort

    def editSummary(self, summary, locked=True):
        self.summary_set = summary

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    def __init__(self, existing=()):
        self._existing = {c.title: c for c in existing}
        self.created = []

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        self.created.append((title, list(items or []), smart))
        collection = FakeCollection(title, items=items or [], labels=[])
        self._existing[title] = collection
        return collection


async def test_an_empty_source_changes_nothing(session):
    """A failed chart fetch must never empty a live collection."""
    existing = FakeCollection("IMDb Top 250", items=[FakeItem("a"), FakeItem("b")])
    section = FakeSection([existing])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", [], LABEL, dry_run=False
    )
    assert existing.removed == []
    assert existing.added == []
    assert any("no items" in a.lower() for a in actions)


async def test_dry_run_writes_nothing(session):
    section = FakeSection()
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", [FakeItem("a")], LABEL, dry_run=True
    )
    assert section.created == []
    assert (await session.execute(select(ManagedCollection))).scalars().all() == []


async def test_creates_a_regular_collection_with_custom_order(session):
    section = FakeSection()
    items = [FakeItem("a"), FakeItem("b")]
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", items, LABEL,
        summary="Top rated.", dry_run=False,
    )
    title, created_items, smart = section.created[0]
    assert smart is False
    assert [i.ratingKey for i in created_items] == ["a", "b"]
    collection = section._existing["IMDb Top 250"]
    assert collection.sort_set == "custom"
    assert collection.summary_set == "Top rated."


async def test_new_chart_entries_are_added(session):
    existing = FakeCollection("IMDb Top 250", items=[FakeItem("a")])
    section = FakeSection([existing])
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )
    assert [i.ratingKey for i in existing.added] == ["b"]


async def test_entries_that_fell_off_the_chart_are_removed(session):
    existing = FakeCollection("IMDb Top 250", items=[FakeItem("a"), FakeItem("b")])
    section = FakeSection([existing])
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", [FakeItem("a")], LABEL, dry_run=False,
    )
    assert [i.ratingKey for i in existing.removed] == ["b"]


async def test_an_unchanged_pass_makes_no_calls_at_all(session):
    existing = FakeCollection("IMDb Top 250", items=[FakeItem("a"), FakeItem("b")])
    section = FakeSection([existing])
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )
    assert existing.added == []
    assert existing.removed == []
    assert existing.moves == []


async def test_order_is_corrected_when_it_drifts(session):
    existing = FakeCollection("IMDb Top 250", items=[FakeItem("b"), FakeItem("a")])
    section = FakeSection([existing])
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )
    assert existing.moves, "expected the out-of-place item to be moved"


async def test_an_unlabelled_collection_is_never_touched(session):
    theirs = FakeCollection("IMDb Top 250", items=[FakeItem("x")], labels=["someone-else"])
    section = FakeSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", [FakeItem("a")], LABEL, dry_run=False,
    )
    assert theirs.added == []
    assert theirs.removed == []
    assert any("conflict" in a.lower() for a in actions)


async def test_nothing_is_ever_deleted(session):
    import inspect

    from autoposter.collections import lists

    assert ".delete(" not in inspect.getsource(lists)


async def test_the_collection_is_recorded_as_managed(session):
    section = FakeSection()
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", [FakeItem("a")], LABEL, dry_run=False,
    )
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.library == "Movies"
    assert row.title == "IMDb Top 250"
    assert row.kind == "manual"
