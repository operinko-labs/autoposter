"""Reconciling regular (list) collections.

These use sync semantics: members not re-selected are removed. That makes an
empty desired-set catastrophic, which is why several tests here are about
doing nothing.
"""
import inspect
import re
from itertools import permutations
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from plexapi.exceptions import NotFound
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

    def __init__(self, title, items=(), labels=(LABEL,), summary=None, summary_locked=False):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.summary = summary
        self._real_fields = [type("F", (), {"name": "summary", "locked": summary_locked})()]
        self._fields = []
        self.added = []
        self.removed = []
        self.moves = []
        self.sort_set = None
        self.sort_calls = 0
        self.summary_set = None
        self.summary_queries = []
        self.reloaded = 0
        # Stands in for ``collection._server``: the summary is written with a
        # raw item-level PUT, not ``editSummary``.
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    def reload(self):
        self.reloaded += 1
        self._cache = list(self._live)
        self._fields = self._real_fields

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return self._fields

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
        """Raises the way the live server does -- the section route plexapi
        takes 404s for collection summaries. See
        ``reconcile._edit_collection_summary``."""
        raise NotFound("(404) not_found; /library/sections/42/all?type=18")

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Stands in for ``server.query`` -- the item-level summary PUT, which
        also locks the field (``summary.locked=1`` rides on every write), or,
        since row 187, the summary CLEAR, whose ``summary.value`` is empty and
        whose ``summary.locked`` is 0; ``keep_blank_values`` is what keeps the
        empty value visible."""
        self.summary_queries.append({"key": key, "method": method})
        query = parse_qs(urlsplit(key).query, keep_blank_values=True)
        self.summary_set = query["summary.value"][0]
        self.summary = self.summary_set
        self._real_fields[0].locked = query.get("summary.locked") == ["1"]

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
    # On the item-level route -- the section route plexapi's own
    # ``editSummary`` takes 404s on the live server.
    assert len(existing.summary_queries) == 1
    assert existing.summary_queries[0]["key"].startswith("/library/metadata/c-Oscars")
    assert existing.summary_queries[0]["method"] == "PUT-SENTINEL"


async def test_a_matching_locked_summary_is_not_rewritten(session):
    existing = FakeCollection(
        "Oscars Winners 2026", items=[FakeItem("a")], summary="Already right.",
        summary_locked=True,
    )
    section = FakeSection([existing])
    await reconcile_list_collection(
        session, section, "Movies", "Oscars Winners 2026",
        [FakeItem("a"), FakeItem("b")], LABEL, summary="Already right.", dry_run=False,
    )
    assert existing.summary_set is None


async def test_a_matching_but_unlocked_summary_is_still_locked(session):
    """The Kometa-era state: the text is already right but the field is not
    locked. The write must still happen (it carries ``summary.locked=1``) --
    but it is a repair, not a change, so no "updated the summary" action is
    reported."""
    existing = FakeCollection(
        "Oscars Winners 2026", items=[FakeItem("a")], summary="Already right.",
        summary_locked=False,
    )
    section = FakeSection([existing])
    actions = await reconcile_list_collection(
        session, section, "Movies", "Oscars Winners 2026",
        [FakeItem("a"), FakeItem("b")], LABEL, summary="Already right.", dry_run=False,
    )
    assert existing.summary_set == "Already right."
    assert not any("summary" in a for a in actions)


async def test_a_removed_summary_is_cleared_on_the_update_path(session):
    """Row 187's list half: ``lists.py`` took the same set-only stance
    (``if summary:``). The hash gate means the clear runs when the
    definition changed -- here, a member was added and the summary is gone."""
    existing = FakeCollection(
        "IMDb Top 250", items=[FakeItem("a")],
        summary="The old text.", summary_locked=True,
    )
    section = FakeSection([existing])

    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )

    assert len(existing.summary_queries) == 1
    query = parse_qs(
        urlsplit(existing.summary_queries[0]["key"]).query, keep_blank_values=True
    )
    assert query["summary.value"] == [""]
    assert query["summary.locked"] == ["0"]
    assert any("cleared the summary" in action for action in actions)


async def test_an_unlocked_summary_is_not_cleared_on_the_update_path(session):
    existing = FakeCollection(
        "IMDb Top 250", items=[FakeItem("a")],
        summary="An operator's own text.", summary_locked=False,
    )
    section = FakeSection([existing])

    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )

    assert existing.summary_queries == []
    assert existing.summary == "An operator's own text."


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


async def test_reconciling_an_operator_row_upgrades_its_kind_to_manual(session):
    """Fix round item 2, the mirror of item 1: an operator blank
    (``ops/blank``) writes a row with ``kind="operator"``. If a definition is
    later pointed at that same title, this reconcile is that definition
    claiming it -- and leaving the row's kind at "operator" afterwards would
    make it lie forever: if the definition is later removed,
    ``engine._sweep`` reads "operator" as "no definition ever built this,
    never delete it" and reports a collection an operator made by hand, not
    one a since-removed definition actually owned. "manual" is the true story
    once a definition has reconciled the title.
    """
    section = FakeSection()
    section._existing["Divider"] = FakeCollection("Divider", items=[], labels=[LABEL])
    session.add(ManagedCollection(
        library="Movies", title="Divider", kind="operator",
        plex_rating_key="c-Divider", definition_hash="",
    ))
    await session.flush()

    await reconcile_list_collection(
        session, section, "Movies", "Divider", [FakeItem("a")], LABEL, dry_run=False,
    )

    row = (await session.execute(
        select(ManagedCollection).where(ManagedCollection.title == "Divider")
    )).scalar_one()
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


async def test_taking_over_a_smart_row_stamps_it_manual_and_fills_its_stamps(session):
    """The mirror of the operator take-over above, and the C11 remediation path
    in the smart -> list direction.

    ``reconcile.shape_conflict`` tells an operator switching a definition from
    ``smart_filter`` to a list builder to delete the collection in Plex and let
    the next pass create it. That pass arrives here with the smart definition's
    row -- ``kind="smart"``, and ``member_count``/the reconcile stamps NULL,
    which is exactly the invariant ``ManagedCollection`` documents for that
    kind. This reconcile is a definition claiming the title and maintaining its
    membership, so the row has to say so: leaving "smart" behind would keep a
    row claiming its stamps can never be filled while the lines below fill
    them.
    """
    section = FakeSection()
    session.add(ManagedCollection(
        library="Movies", title="IMDb Top 250", kind="smart",
        plex_rating_key="12345", definition_hash="the smart definition's",
    ))
    await session.flush()

    await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
    )

    row = (await session.execute(
        select(ManagedCollection).where(ManagedCollection.title == "IMDb Top 250")
    )).scalar_one()
    assert row.kind == "manual"
    member_count, added, removed, reconciled_at = await _stats(session)
    assert (member_count, added, removed) == (2, 2, 0)
    assert reconciled_at is not None


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


async def test_the_poster_refresh_fall_through_still_refreshes_the_stats(
    session, config_factory, tmp_path
):
    """There are two ways out of an unchanged-hash pass, and both must stamp.

    When ``posters_on`` is true and ``poster_sha256`` is still NULL the
    function does not take the short-circuit return -- it falls through to
    refresh the poster -- and that path also skips the write block further
    down, because ``definition_current`` is true. So the stamp has to sit
    ABOVE the short-circuit ``if``, not inside its body. This test is what
    stops a later refactor from tidying it inside: a list collection whose
    membership is correct but whose poster hash is missing would then record
    nothing at all, and an operator would be looking at a row that claims the
    collection has not been reconciled since the pass before.

    The poster fetch is answered with a 404 on purpose. ``apply_poster``
    reports a failure rather than raising, and leaves ``poster_sha256`` NULL,
    so the fall-through is exercised without this test needing to care about
    uploading.
    """
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    config.collections.posters = False
    section = FakeSection()
    items = [FakeItem("a"), FakeItem("b")]

    async def missing(request):
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(missing)) as http:
        await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", items, LABEL, dry_run=False,
            kind="chart", key="IMDb Top 250", http=http, config=config,
        )
        assert (await _stats(session))[1] == 2, "precondition: the create path stamped 2 adds"

        config.collections.posters = True
        actions = await reconcile_list_collection(
            session, section, "Movies", "IMDb Top 250", items, LABEL, dry_run=False,
            kind="chart", key="IMDb Top 250", http=http, config=config,
        )

    # Precondition, not the assertion under test: reaching apply_poster is
    # what proves this pass took the fall-through rather than the plain
    # short-circuit return, which a different test already covers.
    assert any("poster" in a for a in actions), actions
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None, "precondition: the poster hash stayed NULL"

    member_count, added, removed, reconciled_at = await _stats(session)
    assert member_count == 2
    assert added == 0, "the fall-through left the create pass's stale delta behind"
    assert removed == 0
    assert reconciled_at is not None
