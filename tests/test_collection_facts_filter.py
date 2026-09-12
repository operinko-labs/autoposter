"""Roadmap row 156: the facts read, and the filter it feeds, end to end.

**Why the entry-point test at the bottom of this file is the load-bearing
one.** The standing rule on this tree is that a gated feature needs one test
through the real entry point: two same-branch defects have shipped where the
helper tests passed and the wired path differed. So the last two tests here go
through `engine.run_library` with a real session, a real `media_items`/
`item_facts` pair, and the same `FakeSection` shape the row-158 suite uses --
and the second one runs TWO passes over one `run_library` call each, with the
facts row arriving in between, because "the membership moves when the facts
arrive" is the whole claim of the sparsity story and it is not observable in a
single pass.

There is no Kometa golden for any of this and there cannot be: Kometa has no
attribute of these three names, so its `check_filter` would raise on the config
rather than produce a member list. `tests/test_collection_filter_oracle.py` is
untouched by this row.
"""
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from autoposter.collections.engine import run_library
from autoposter.collections.facts_read import (
    FactsUnavailable,
    ItemFactsValues,
    ensure_facts,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.db import refs
from autoposter.db.models import ItemFacts

from conftest import seed_media_item

LABEL = "autoposter"


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key
        self.title = "m%s" % rating_key
        self.guids = []
        self.contentRating = None
        self.media = [SimpleNamespace(videoResolution="1080")]


class FakeCollection:
    def __init__(self, title, items=()):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._real_labels = []
        self._labels = []
        self.summary = None
        self.titleSort = None
        self._server = self
        self._session = type("Sess", (), {"put": "PUT"})()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return []

    def reload(self, **kw):
        self._cache = list(self._live)
        self._labels = self._real_labels

    def items(self):
        return list(self._cache)

    def addItems(self, items):
        self._live.extend(items)

    def removeItems(self, items):
        removed = {i.ratingKey for i in items}
        self._live = [i for i in self._live if i.ratingKey not in removed]

    def moveItem(self, item, after=None):
        pass

    def sortUpdate(self, sort=None):
        pass

    def editSortTitle(self, sortTitle, locked=True):
        self.titleSort = sortTitle

    def addLabel(self, label, locked=True):
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def query(self, key, method=None, **kwargs):
        pass


class FakeSection:
    def __init__(self, items=()):
        self._items = list(items)
        self._existing = {}

    def all(self):
        return list(self._items)

    def fetchItems(self, ekey):
        return []

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        collection = FakeCollection(title, items or [])
        self._existing[title] = collection
        return collection

    def listFilterChoices(self, field, libtype=None):
        return []


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": False, "awards": False,
        "separators": False, "definitions": [], "presets": [],
        "libraries": ["Movies"], "delete_unconfigured": False, "max_deletes": 5,
        "enabled": True,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


async def _seed(session, rating_key, *, library="Movies", facts=None):
    """A `media_items` row, and optionally its `item_facts` row.

    The three states this row is about are all reachable from here: an item
    with a facts row carrying a value, an item with a facts row whose column
    is NULL (`facts={"content_rating": None}`), and an item with no facts row
    at all (`facts=None`).
    """
    item = await seed_media_item(
        session, str(rating_key), library=library, kind="movie",
        title="m%s" % rating_key,
    )
    if facts is not None:
        session.add(ItemFacts(item_id=item.id, **facts))
        await session.commit()
    return item


# --- the read ----------------------------------------------------------------


async def test_the_read_answers_for_every_key_it_was_asked_about(session):
    """The contract that makes the engine's stage simple: ASKED implies
    PRESENT. Unlike the tier-2 batched Plex read -- where a key the batch did
    not answer for means "unknown", and the engine refuses the definition
    rather than evaluating -- absence here is KNOWLEDGE. This service's own
    database is authoritative about its own rows, so "no facts row" and "no
    media_items row" are both answers, and both are the all-None value the
    missing rule excludes on."""
    await _seed(session, "1", facts={"content_rating": "13", "critic_rating": 7.8,
                                     "audience_rating": 6.9})
    await _seed(session, "2", facts={"content_rating": None})
    await _seed(session, "3")

    facts = await ensure_facts(session, {}, "Movies", ["1", "2", "3", "999"])

    assert set(facts) == {"1", "2", "3", "999"}
    assert facts["1"] == ItemFactsValues(
        common_sense_rating="13", imdb_rating=7.8, tmdb_rating=6.9,
    )
    assert facts["2"] == ItemFactsValues()
    assert facts["3"] == ItemFactsValues()
    assert facts["999"] == ItemFactsValues()


async def test_the_read_is_memoised_on_the_run_cache_for_the_whole_pass(session):
    """`ensure_tags`'s law one tier along: two definitions over overlapping
    sets cost one query for the union, not two for the parts. The second call
    asks for a key the first already fetched plus one it did not, and only the
    new key reaches the database -- observed here via the ``media_items``
    statements the second call actually issues (``tests/test_api_auth.py``'s
    ``before_cursor_execute`` idiom, over a different table), rather than
    trusting the returned values alone to notice a ``to_fetch`` computation
    that re-fetched key "1"."""
    await _seed(session, "1", facts={"content_rating": "13"})
    await _seed(session, "2", facts={"content_rating": "16"})
    run_cache: dict = {}

    first = await ensure_facts(session, run_cache, "Movies", ["1"])

    statements: list[tuple] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "media_items" in statement:
            statements.append(parameters)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        second = await ensure_facts(session, run_cache, "Movies", ["1", "2"])
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert first is second, "the same dict, mutated in place"
    assert second["1"].common_sense_rating == "13"
    assert second["2"].common_sense_rating == "16"
    assert len(statements) == 1, "one query for the union, not one per key"
    assert "2" in statements[0], "the new key must reach the database"
    assert "1" not in statements[0], "the already-cached key must not"


async def test_two_libraries_sharing_a_run_cache_get_independent_reads(session):
    """Task-2 review, Minor 2: the memo key folds in the library, so a key
    answered `ItemFactsValues()` for one library's ask is never served,
    unchanged, to a different library's ask over the same shared run_cache.
    Item "999" exists only in Shows; a Movies ask for it must see no row, and
    a Shows ask straight after, over the SAME run_cache, must still see its
    real value rather than the Movies memo's all-None answer."""
    await _seed(session, "999", library="Shows", facts={"content_rating": "13"})
    run_cache: dict = {}

    movies = await ensure_facts(session, run_cache, "Movies", ["999"])
    shows = await ensure_facts(session, run_cache, "Shows", ["999"])

    assert movies["999"] == ItemFactsValues()
    assert shows["999"].common_sense_rating == "13"


async def test_the_read_scopes_on_the_library(session):
    """The join is scoped the way `facts_enumeration`'s are, so a filter on
    Movies never reads a Shows row.

    `media_items.rating_key` carries a UNIQUE index (the initial schema's
    `ix_media_items_rating_key`), so one key cannot exist in two libraries and
    the brief's two-row form of this test is unschedulable against the real
    database. What the scope must still prevent is the same key ANSWERING from
    the wrong library: a key that lives only in Shows comes back all-None for
    a Movies ask -- excluded by ruling C3's missing rule -- rather than
    carrying the Shows row's band across.
    """
    await _seed(session, "7", library="Shows", facts={"content_rating": "16"})
    await _seed(session, "8", library="Movies", facts={"content_rating": "13"})

    facts = await ensure_facts(session, {}, "Movies", ["7", "8"])

    assert facts["7"] == ItemFactsValues()
    assert facts["8"].common_sense_rating == "13"


async def test_a_failed_read_is_memoised_and_carries_no_query_text(session):
    """Two properties, and the second is roadmap row 213. The failure is
    memoised so a dead database costs one attempt per pass rather than one per
    definition, and the message is class-name-only: a SQLAlchemy error's own
    message can quote the statement and the DSN."""
    run_cache: dict = {}
    broken = SimpleNamespace()  # no `execute`, no `begin_nested`

    with pytest.raises(FactsUnavailable) as first:
        await ensure_facts(broken, run_cache, "Movies", ["1"])
    with pytest.raises(FactsUnavailable) as second:
        await ensure_facts(broken, run_cache, "Movies", ["2"])

    assert second.value is first.value, "the memo, not a second attempt"
    assert "SELECT" not in str(first.value)
    assert "postgresql" not in str(first.value)


async def test_a_failed_read_logs_the_root_cause_once_though_the_served_message_stays_fixed(
    session,
    caplog,
):
    """Task-2 review, Important 1. Row 213 forbids the root cause from
    reaching the served, class-name-only message -- it says nothing about the
    log. Without a local `logger.exception`, a `TypeError` or an
    `AttributeError` here (the shape `broken` drives, by having neither
    `execute` nor `begin_nested`) is written off for the whole pass as "the
    database did not answer", with its own traceback discarded."""
    run_cache: dict = {}
    broken = SimpleNamespace()  # no `execute`, no `begin_nested`

    with caplog.at_level(logging.ERROR):
        with pytest.raises(FactsUnavailable) as excinfo:
            await ensure_facts(broken, run_cache, "Movies", ["1"])

    assert "AttributeError" in caplog.text
    assert str(excinfo.value) == (
        "the facts read failed (AttributeError); every definition filtering "
        "on a facts-backed attribute is refused this pass"
    )


# --- the view ----------------------------------------------------------------


def test_the_view_reads_the_three_names_off_the_facts_values():
    from autoposter.collections.filter_values import PlexItemView

    view = PlexItemView(FakeItem("1"), facts=ItemFactsValues(
        common_sense_rating="13", imdb_rating=7.8, tmdb_rating=6.9,
    ))
    assert view.get("common_sense_rating") == "13"
    assert view.get("imdb_rating") == pytest.approx(7.8)
    assert view.get("tmdb_rating") == pytest.approx(6.9)


def test_a_facts_attribute_on_a_view_built_without_facts_raises():
    """`tags=`'s law, one tier along, and for the identical reason: None here
    must never read as "this item has no value", because the table turns that
    into a defined match result -- a full, plausible, wrong collection. An
    item that genuinely has no facts row is `ItemFactsValues()`, which is a
    different object and a different answer."""
    from autoposter.collections.filter_values import (
        EnrichmentNotLoaded,
        PlexItemView,
    )

    with pytest.raises(EnrichmentNotLoaded, match="common_sense_rating"):
        PlexItemView(FakeItem("1")).get("common_sense_rating")


def test_an_item_with_no_facts_row_reads_as_no_value_not_as_not_loaded():
    from autoposter.collections.filter_values import PlexItemView

    view = PlexItemView(FakeItem("1"), facts=ItemFactsValues())
    assert view.get("common_sense_rating") is None
    assert view.get("imdb_rating") is None


# --- the entry point ---------------------------------------------------------


async def test_a_facts_filter_selects_through_the_real_entry_point(session):
    """THE load-bearing test (standing memory: a gated feature needs one test
    through the real entry point). Three items in one pass, one of each state:
    a facts row carrying `13`, a facts row whose column is NULL, and no facts
    row at all. Only the first is a member -- ruling C3's missing rule, seen
    from the outside."""
    await _seed(session, "101", facts={"content_rating": "13", "critic_rating": 8.1})
    await _seed(session, "102", facts={"content_rating": None, "critic_rating": None})
    await _seed(session, "103")
    section = FakeSection([FakeItem("101"), FakeItem("102"), FakeItem("103")])
    definition = CollectionDefinition(
        title="Gentle", builder="plex_all", filters={"common_sense_rating": "13"},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert result.failed is False
    assert [i.ratingKey for i in section._existing["Gentle"]._live] == ["101"]


async def test_a_negated_facts_filter_still_excludes_the_ungathered(session):
    """Ruling C3's sharp end, through the entry point rather than at the unit.
    `common_sense_rating.not: 13` keeps the item whose band is `16` and drops
    BOTH ungathered items -- where every other tag row in this table would
    have kept them, turning a cold library into a collection of everything."""
    await _seed(session, "201", facts={"content_rating": "16"})
    await _seed(session, "202", facts={"content_rating": "13"})
    await _seed(session, "203")
    section = FakeSection([FakeItem("201"), FakeItem("202"), FakeItem("203")])
    definition = CollectionDefinition(
        title="Not Thirteen", builder="plex_all",
        filters={"common_sense_rating.not": "13"},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert result.failed is False
    assert [i.ratingKey for i in section._existing["Not Thirteen"]._live] == ["201"]


async def test_the_membership_moves_when_the_facts_arrive_between_passes(session):
    """The sparsity story, as a behaviour rather than a paragraph. Pass one
    runs against a library the facts sweep has not reached: the collection is
    empty, correctly, and nothing is marked failed. The drift sweep then fills
    one row. Pass two selects it. TWO `run_library` calls, deliberately -- the
    read is memoised on the PASS's `run_cache`, so a single call could never
    show the value changing, and a memo that outlived the pass would make this
    test go red, which is the point."""
    await _seed(session, "301")
    await _seed(session, "302")
    section = FakeSection([FakeItem("301"), FakeItem("302")])
    definition = CollectionDefinition(
        title="Gentle", builder="plex_all", filters={"imdb_rating.gte": 8.0},
    )

    first = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )
    assert first.definitions[0].failed is False
    assert "Gentle" not in section._existing or not section._existing["Gentle"]._live

    item = (await _seed(session, "303", facts={"critic_rating": 8.4}))
    section._items.append(FakeItem("303"))

    second = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    assert second.definitions[0].failed is False
    assert [i.ratingKey for i in section._existing["Gentle"]._live] == ["303"]
    assert (await refs.native_ids(session, [item.id], "plex")) == {item.id: "303"}


async def test_a_failed_facts_read_refuses_the_definition_class_name_only(session):
    """C4's failure shape, mirroring the tier-2 batched refusal at the same
    stage: the definition is FAILED, its items are emptied (which `lists.py`
    reads as "make no changes", so the collection is left exactly as it was
    rather than rewritten from a partial answer), and one action line names the
    definition, the attributes and the exception's CLASS -- never its message,
    which for a SQLAlchemy error can carry the DSN."""
    await _seed(session, "401", facts={"content_rating": "13"})
    section = FakeSection([FakeItem("401")])
    definition = CollectionDefinition(
        title="Gentle", builder="plex_all", filters={"common_sense_rating": "13"},
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("connection to postgresql://user:secret@db/app failed")

    import autoposter.collections.engine as engine_module

    original = engine_module.ensure_facts
    engine_module.ensure_facts = boom
    try:
        run = await run_library(
            session, section, "Movies", "Movie", [definition], _config(),
        )
    finally:
        engine_module.ensure_facts = original

    [result] = run.definitions
    assert result.failed is True
    [line] = [a for a in result.actions if "common_sense_rating" in a]
    assert "Gentle" in line
    assert "RuntimeError" in line
    assert "secret" not in line
    assert "postgresql" not in line
