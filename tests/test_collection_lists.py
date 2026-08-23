"""Reconciling regular (list) collections.

These use sync semantics: members not re-selected are removed. That makes an
empty desired-set catastrophic, which is why several tests here are about
doing nothing.
"""
import inspect
import re
from itertools import permutations

import pytest
from sqlalchemy import select

from autoposter.collections import lists
from autoposter.collections.lists import _enforce_order, reconcile_list_collection
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


class FakeItem:
    def __init__(self, key):
        self.ratingKey = key
        self.title = key


class FakeCollection:
    """Models plexapi's item caching faithfully.

    ``Collection.items()`` returns ``self._items``, a ``cached_data_property``
    that ``addItems``, ``removeItems`` and ``moveItem`` never invalidate --
    only ``reload()`` does (pinned in test_plexapi_collection_contract.py).
    So this double keeps two lists: ``_live`` is the server's truth, which
    every write mutates, and ``_cache`` is what ``items()`` returns, which
    only ``reload()`` refreshes. A double that let writes show through
    immediately would hide exactly the bug this models.
    """

    def __init__(self, title, items=(), labels=(LABEL,), summary=None):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.summary = summary
        self.added = []
        self.removed = []
        self.moves = []
        self.sort_set = None
        self.sort_calls = 0
        self.summary_set = None
        self.reloaded = 0

    def reload(self):
        self.reloaded += 1
        self._cache = list(self._live)

    @property
    def labels(self):
        return self._labels

    def items(self):
        return list(self._cache)

    def order(self):
        """The order a fresh client would see -- the server's, not the cache's."""
        return [i.ratingKey for i in self._live]

    def addItems(self, items):
        self.added.extend(items)
        self._live.extend(items)

    def removeItems(self, items):
        self.removed.extend(items)
        keys = {i.ratingKey for i in items}
        self._live = [i for i in self._live if i.ratingKey not in keys]

    def moveItem(self, item, after=None):
        self.moves.append((item.ratingKey, after.ratingKey if after else None))
        self._live = [i for i in self._live if i.ratingKey != item.ratingKey]
        if after is None:
            self._live.insert(0, item)
        else:
            position = [i.ratingKey for i in self._live].index(after.ratingKey)
            self._live.insert(position + 1, item)

    def sortUpdate(self, sort=None):
        self.sort_set = sort
        self.sort_calls += 1

    def editSummary(self, summary, locked=True):
        self.summary_set = summary
        self.summary = summary

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    def __init__(self, existing=()):
        self._existing = {c.title: c for c in existing}
        self.created = []
        self.collection_calls = 0

    def collections(self, **kw):
        self.collection_calls += 1
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
    assert existing.order() == ["a", "b"], "the moves left the wrong final order"


@pytest.mark.parametrize("start", ["".join(p) for p in permutations("abcd")])
async def test_every_starting_order_ends_up_correct(session, start):
    """The order enforcement must land on the source order from any starting
    permutation, not merely issue some moves.

    ``_enforce_order`` used to re-read ``collection.items()`` on every
    iteration, which returns plexapi's un-invalidated cache -- so it computed
    each move against positions that predated the previous move. Over all
    permutations of four items, 8 of 24 ended mis-ordered.
    """
    existing = FakeCollection("IMDb Top 250", items=[FakeItem(k) for k in start])
    section = FakeSection([existing])
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem(k) for k in "abcd"], LABEL, dry_run=False,
    )
    assert existing.order() == list("abcd")


def test_enforce_order_lands_on_the_source_order_for_every_permutation():
    """The same property over every permutation of 3, 4 and 5 items (150 in
    total), driven directly so the assertion is on order alone."""
    failures = []
    for size in (3, 4, 5):
        keys = "abcde"[:size]
        wanted = [FakeItem(k) for k in keys]
        for start in permutations(keys):
            collection = FakeCollection("c", items=[FakeItem(k) for k in start])
            _enforce_order(collection, wanted)
            if collection.order() != list(keys):
                failures.append((start, collection.order()))
    assert not failures, "%d of 150 permutations ended mis-ordered: %r" % (
        len(failures), failures[:5],
    )


def test_enforce_order_sees_items_added_since_the_last_read():
    """Items added by ``addItems`` are invisible to ``items()`` until a
    ``reload()``. Ordering them without one raised ``ValueError: 'b' is not
    in list``, which aborted the whole run before the library committed."""
    collection = FakeCollection("c", items=[FakeItem("a")])
    collection.addItems([FakeItem("b")])
    assert [i.ratingKey for i in collection.items()] == ["a"]  # the stale cache

    _enforce_order(collection, [FakeItem("b"), FakeItem("a")])

    assert collection.reloaded == 1
    assert collection.order() == ["b", "a"]


def test_order_enforcement_reloads_and_never_refreshes():
    """Order enforcement needs ``.reload()`` (see the test above); the
    project-wide ban on ``.refresh()`` is enforced by the AST walk in
    tests/test_plex_writer.py::test_no_refresh_calls_project_wide."""
    assert ".reload()" in inspect.getsource(lists)


async def test_a_second_identical_pass_short_circuits_on_the_stored_hash(session):
    """The first pass stores ``definition_hash``; the second must return
    before it reads members, diffs or writes anything."""
    section = FakeSection()
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, summary="Top rated.", dry_run=False,
    )
    collection = section._existing["IMDb Top 250"]
    assert collection.sort_calls == 1

    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, summary="Top rated.", dry_run=False,
    )

    assert actions == []
    assert collection.added == []
    assert collection.removed == []
    assert collection.moves == []
    assert collection.sort_calls == 1
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.title == "IMDb Top 250"


async def test_a_corrected_summary_reaches_an_existing_collection(session):
    """The summary is part of the members hash, so changing it takes the
    update branch. Writing it only on create meant the new hash was stored
    against the old summary and no later pass would ever try again."""
    existing = FakeCollection(
        "Oscars Winners 2026", items=[FakeItem("a")], summary="The old, wrong text.",
    )
    section = FakeSection([existing])
    actions = await reconcile_list_collection(
        session, section, "Movies", "Oscars Winners 2026", [FakeItem("a")], LABEL,
        summary="Academy Awards (Oscars) Winners for 2026.", dry_run=False,
    )
    assert existing.summary_set == "Academy Awards (Oscars) Winners for 2026."
    assert any("summary" in a for a in actions)


async def test_a_matching_summary_is_not_rewritten(session):
    existing = FakeCollection(
        "Oscars Winners 2026", items=[FakeItem("a")], summary="Already right.",
    )
    section = FakeSection([existing])
    await reconcile_list_collection(
        session, section, "Movies", "Oscars Winners 2026",
        [FakeItem("a"), FakeItem("b")], LABEL, summary="Already right.", dry_run=False,
    )
    assert existing.summary_set is None


async def test_the_sort_mode_is_configurable(session):
    """The five dynamic Oscars year collections use ``release``, not
    ``custom`` (kometa-collections.md §2.4)."""
    section = FakeSection()
    await reconcile_list_collection(
        session, section, "Movies", "Oscars Winners 2026", [FakeItem("a")], LABEL,
        sort="release", dry_run=False,
    )
    assert section._existing["Oscars Winners 2026"].sort_set == "release"


async def test_the_sort_mode_defaults_to_custom(session):
    section = FakeSection()
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", [FakeItem("a")], LABEL, dry_run=False,
    )
    assert section._existing["IMDb Top 250"].sort_set == "custom"


async def test_a_hoisted_collection_listing_is_used_instead_of_listing_again(session):
    """The production Movies section holds 305 collections, so the listing is
    made once per run by the caller and passed in."""
    existing = FakeCollection("IMDb Top 250", items=[FakeItem("a")])
    section = FakeSection([existing])
    hoisted = {c.title: c for c in section.collections()}
    section.collection_calls = 0

    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False, existing=hoisted,
    )

    assert section.collection_calls == 0
    assert existing.order() == ["a", "b"]


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


async def _stats(session):
    """The reconcile stats as the database holds them.

    Read as columns rather than through the ORM instance on purpose:
    ``last_reconciled_at`` is assigned ``func.now()``, so the mapped attribute
    holds a SQL expression until a refresh replaces it, and refreshing it
    through attribute access would need a greenlet context these tests do not
    have.
    """
    return (
        await session.execute(
            select(
                ManagedCollection.member_count,
                ManagedCollection.last_added,
                ManagedCollection.last_removed,
                ManagedCollection.last_reconciled_at,
            )
        )
    ).one()


async def test_the_create_path_stamps_the_reconcile_stats(session):
    """A brand-new collection added every one of its members, so the create
    path's delta is the whole list."""
    section = FakeSection()
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )
    member_count, added, removed, reconciled_at = await _stats(session)
    assert member_count == 2
    assert added == 2
    assert removed == 0
    assert reconciled_at is not None


async def test_the_update_path_stamps_the_delta_the_action_string_reports(session):
    """The stamped delta and the summary line an operator reads must be the
    same numbers. Two independent counts would drift silently, so this asserts
    the columns against the numbers parsed back out of the action string."""
    section = FakeSection()
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )

    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("c")], LABEL, dry_run=False,
    )

    update_line = next(a for a in actions if a.startswith("updated "))
    reported = re.search(r"\+(\d+) -(\d+)", update_line)
    assert reported, update_line
    member_count, added, removed, reconciled_at = await _stats(session)
    assert (added, removed) == (int(reported.group(1)), int(reported.group(2)))
    assert (added, removed) == (1, 1)
    assert member_count == 2
    assert reconciled_at is not None


async def test_the_unchanged_hash_path_refreshes_the_stats_and_zeroes_the_delta(session):
    """The short-circuit still confirmed membership is as desired, so the row
    is a fresh observation: the count is re-affirmed and the delta is zero.

    The first pass stamps ``last_added=2``, so a short-circuit that skipped
    the stamp would leave that stale 2 behind -- which is what this asserts
    against."""
    section = FakeSection()
    items = [FakeItem("a"), FakeItem("b")]
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250", items, LABEL, dry_run=False,
    )
    assert (await _stats(session))[1] == 2, "precondition: the create path stamped 2 adds"

    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )

    assert actions == [], "precondition: this pass must be the short-circuit"
    member_count, added, removed, reconciled_at = await _stats(session)
    assert member_count == 2
    assert added == 0
    assert removed == 0
    assert reconciled_at is not None


async def test_a_dry_run_stamps_nothing(session):
    """Dry-run is write-free, and that has to hold on both paths a dry run can
    reach with a row already present: the unchanged short-circuit and the
    would-update branch."""
    section = FakeSection()
    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )
    before = await _stats(session)
    assert before[:3] == (2, 2, 0), "precondition: the create path stamped 2/2/0"

    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=True,
    )
    assert await _stats(session) == before, "the short-circuit stamped under dry-run"

    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b"), FakeItem("c")], LABEL, dry_run=True,
    )
    assert await _stats(session) == before, "the would-update branch stamped under dry-run"
