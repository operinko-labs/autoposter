"""The engine's operational knobs: append, the delete sweep, honest failure.

Three things a reconcile pass can do beyond "make this collection match this
list", each with a guard that has to be provably falsifiable:

- **append** adds and never removes, so a collection can carry members this
  service did not choose. The membership hash carries the mode, or a pass that
  switches modes would short-circuit on a hash computed under the old one and
  the switch would silently not take effect.
- **the delete sweep** is the only place this service deletes anything. It is
  off by default, it only ever considers a collection carrying the ownership
  label *and* a ``managed_collections`` row, a protected label beats it, and
  past ``max_deletes`` it refuses the whole sweep with the numbers rather than
  cascading a config mistake into a wiped library.
- **failure reporting**: a definition whose source died leaves its collection
  alone (that invariant is ``test_builder_engine.py``'s), but the pass must
  stop calling that outcome a success -- the scheduled job's ``last_status``
  is the only place an operator ever sees it.
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from autoposter.collections.builders import REGISTRY, BuilderContext, BuilderResult, register
from autoposter.collections.engine import definition_titles_for, run_definitions, run_library
from autoposter.collections.service import (
    CollectionsPassFailed,
    reconcile_libraries,
)
from autoposter.config.holder import ConfigHolder
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import EventLog, ManagedCollection, ScheduledRun
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import make_collections_job

LABEL = "autoposter"


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, key, guids=()):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]


class FakeCollection:
    """The ``test_builder_engine.py`` fake plus what a sweep needs: a
    ``delete()`` that records itself, and labels that only a ``reload()``
    populates (the caching model ``load_labels`` is written against)."""

    def __init__(self, title, items=(), labels=(), rating_key=None):
        self.title = title
        self.ratingKey = rating_key or ("c-" + title)
        self._live = list(items)
        self._cache = list(items)
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self.summary = None
        self.sort_set = None
        self.deleted = False
        self.moves = 0
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

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
        self.moves += 1
        self._live = [i for i in self._live if i.ratingKey != item.ratingKey]
        if after is None:
            self._live.insert(0, item)
        else:
            position = [i.ratingKey for i in self._live].index(after.ratingKey)
            self._live.insert(position + 1, item)

    def sortUpdate(self, sort=None):
        self.sort_set = sort

    def query(self, key, method=None, **kwargs):
        self.summary = key

    def addLabel(self, labels, locked=True):
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels

    def delete(self):
        self.deleted = True


class FakeSection:
    def __init__(self, items=(), existing=(), section_type="movie"):
        self._items = [FakeItem(key, guids) for key, guids in items]
        self._existing = {c.title: c for c in existing}
        self.type = section_type

    def all(self):
        return list(self._items)

    def listFilterChoices(self, field, libtype=None):
        """No content ratings present, so the Common Sense family in the
        shipped inventory derives no buckets. Those are
        ``test_collection_reconcile.py``'s; this file is about what the engine
        does around them."""
        return []

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        collection = FakeCollection(title, items or [])
        self._existing[title] = collection
        return collection


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": False, "awards": False,
        "separators": False, "definitions": [], "libraries": ["Movies"],
        "delete_unconfigured": False, "max_deletes": 5, "enabled": True,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


@pytest.fixture
def registry_entry():
    """Register a builder for one test and take it back out again."""
    registered: list[str] = []

    def add(builder):
        register(builder)
        registered.append(builder.type_name)
        return builder

    yield add

    for type_name in registered:
        del REGISTRY[type_name]


class _Listing:
    """A builder returning whatever ids the test gave it."""

    def __init__(self, type_name, ids, summary=None):
        self.type_name = type_name
        self.ids = list(ids)
        self._summary = summary

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        return BuilderResult(ids=list(self.ids), summary=self._summary)


class _Dead:
    """A builder whose source is down."""

    type_name = "knobs_dead_source"

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        raise httpx.HTTPError("the source is down")


async def _run(session, section, definitions, config, **kwargs):
    return await run_definitions(
        session, section, "Movies", "Movie", definitions, config, **kwargs
    )


async def _managed_row(session, library, title, hash_value="seed"):
    """A ``managed_collections`` row, as a pass that created the collection
    would have left it. The sweep will not consider a collection without one."""
    row = ManagedCollection(
        library=library, title=title, kind="manual",
        plex_rating_key="c-" + title, definition_hash=hash_value,
    )
    session.add(row)
    await session.flush()
    return row


# --- sync_mode: append -----------------------------------------------------

async def test_append_adds_new_members_and_removes_none(session, registry_entry):
    """The whole point of the mode: a member the source no longer names --
    or never named -- stays. Sync would take it out."""
    registry_entry(_Listing("knobs_append", [("imdb", "tt2")]))
    manual = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Appended", [manual], labels=[LABEL])
    section = FakeSection(
        [("m9", ["imdb://tt9"]), ("m2", ["imdb://tt2"])], existing=[live]
    )

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Appended", builder="knobs_append", sync_mode="append"
        )],
        _config(),
    )

    assert [i.ratingKey for i in live._live] == ["m9", "m2"], (
        "append must add the new member and keep the one the source does not name"
    )
    assert actions == ["updated 'Appended': +1 -0, 0 move(s)"]


async def test_append_leaves_the_existing_members_where_they_were(
    session, registry_entry
):
    """Ordering applies to what append adds, not to what was already there.
    Sync owns the whole collection and so owns its order; append explicitly
    does not own the whole collection, so reordering it would be a write
    against members the operator chose."""
    registry_entry(_Listing("knobs_append_order", [("imdb", "tt2"), ("imdb", "tt3")]))
    manual = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Appended", [manual], labels=[LABEL])
    section = FakeSection(
        [("m9", ["imdb://tt9"]), ("m2", ["imdb://tt2"]), ("m3", ["imdb://tt3"])],
        existing=[live],
    )

    await _run(
        session, section,
        [CollectionDefinition(
            title="Appended", builder="knobs_append_order", sync_mode="append"
        )],
        _config(),
    )

    assert [i.ratingKey for i in live._live] == ["m9", "m2", "m3"], (
        "new members are appended in source order, after what was there"
    )
    assert live.moves == 0, "an append pass must not move an existing member"


async def test_append_creates_the_collection_when_there_is_none(
    session, registry_entry
):
    registry_entry(_Listing("knobs_append_create", [("imdb", "tt1"), ("imdb", "tt2")]))
    section = FakeSection([("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])])

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Fresh", builder="knobs_append_create", sync_mode="append"
        )],
        _config(),
    )

    assert actions == ["created 'Fresh' with 2 item(s)"]
    assert [i.ratingKey for i in section._existing["Fresh"]._live] == ["m1", "m2"]


async def test_switching_the_mode_re_reconciles_instead_of_short_circuiting(
    session, registry_entry
):
    """The membership hash has to carry the mode. An append pass and a sync
    pass over the same list produce the same members hash otherwise, so the
    switch to sync would short-circuit on the append pass's stored hash and
    the manually-added member it was supposed to remove would survive."""
    registry_entry(_Listing("knobs_mode_flip", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"]), ("m9", ["imdb://tt9"])])

    await _run(
        session, section,
        [CollectionDefinition(
            title="Flipped", builder="knobs_mode_flip", sync_mode="append"
        )],
        _config(),
    )
    live = section._existing["Flipped"]
    live._live.append(FakeItem("m9", ["imdb://tt9"]))  # added by hand in Plex

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Flipped", builder="knobs_mode_flip", sync_mode="sync"
        )],
        _config(),
    )

    assert [i.ratingKey for i in live._live] == ["m1"], (
        "switching to sync must re-reconcile: sync owns the whole membership"
    )
    assert actions == ["updated 'Flipped': +0 -1, 0 move(s)"]


# --- the delete sweep ------------------------------------------------------

async def test_an_unconfigured_collection_is_only_reported_by_default(
    session, registry_entry
):
    """``delete_unconfigured`` is off unless the operator says otherwise, and
    off means reported -- the leftovers posture, applied to our own."""
    registry_entry(_Listing("knobs_kept", [("imdb", "tt1")]))
    orphan = FakeCollection("Retired Chart", [FakeItem("m1")], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[orphan])
    await _managed_row(session, "Movies", "Retired Chart")

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Kept", builder="knobs_kept")],
        _config(), sweep=True,
    )

    assert orphan.deleted is False
    assert any("Retired Chart" in action for action in run.actions)
    assert any("delete_unconfigured" in action for action in run.actions), (
        "the report has to name the setting that would act on it"
    )
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Retired Chart")
        )
    ).scalar_one_or_none() is not None


async def test_an_unconfigured_collection_is_deleted_when_opted_in(
    session, registry_entry
):
    registry_entry(_Listing("knobs_kept_2", [("imdb", "tt1")]))
    orphan = FakeCollection("Retired Chart", [FakeItem("m1")], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[orphan])
    await _managed_row(session, "Movies", "Retired Chart")

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Kept", builder="knobs_kept_2")],
        _config(delete_unconfigured=True), sweep=True,
    )

    assert orphan.deleted is True
    assert "deleted 'Retired Chart': no definition builds it" in run.actions
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Retired Chart")
        )
    ).scalar_one_or_none() is None, "the managed row goes with the collection"


async def test_a_collection_without_the_ownership_label_is_never_deleted(session):
    """The ownership boundary. A row can name a collection this service no
    longer owns -- the label was stripped by hand, or the collection was
    recreated by someone else under the same title -- and deleting on the row
    alone would delete a stranger's collection."""
    theirs = FakeCollection("Not Ours", [FakeItem("m1")], labels=["SomeoneElse"])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[theirs])
    await _managed_row(session, "Movies", "Not Ours")

    run = await run_library(
        session, section, "Movies", "Movie", [],
        _config(delete_unconfigured=True), sweep=True,
    )

    assert theirs.deleted is False
    assert not any("deleted" in action for action in run.actions)
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Not Ours")
        )
    ).scalar_one_or_none() is not None


async def test_a_collection_with_no_managed_row_is_never_deleted(session):
    """The other half of the boundary: our label on a collection no pass of
    ours ever recorded is not enough. A hand-labelled collection is the
    operator's."""
    theirs = FakeCollection("Hand Labelled", [FakeItem("m1")], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[theirs])

    run = await run_library(
        session, section, "Movies", "Movie", [],
        _config(delete_unconfigured=True), sweep=True,
    )

    assert theirs.deleted is False
    assert not any("deleted" in action for action in run.actions)


async def test_a_protected_label_beats_the_delete_sweep(session):
    """Protected wins over everything else, ownership included -- the rule
    ``resolve_collision`` already applies to writes, applied to deletes."""
    protected = FakeCollection(
        "Deleted Soon", [FakeItem("m1")],
        labels=[LABEL, "Collection managed by Maintainerr"],
    )
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[protected])
    await _managed_row(session, "Movies", "Deleted Soon")

    run = await run_library(
        session, section, "Movies", "Movie", [],
        _config(
            delete_unconfigured=True,
            protect_labels=["Collection managed by Maintainerr"],
        ),
        sweep=True,
    )

    assert protected.deleted is False
    assert any("protected" in action for action in run.actions)


async def test_the_sweep_refuses_past_the_cap_and_says_by_how_much(
    session, registry_entry
):
    """The cleanup-cap precedent. A config edit that drops every definition
    must not cascade into a wiped library: past the cap the sweep refuses
    *entirely* -- not "the first five" -- reports the numbers, and the rest of
    the pass still runs."""
    registry_entry(_Listing("knobs_kept_3", [("imdb", "tt1")]))
    orphans = [
        FakeCollection("Retired %d" % n, [FakeItem("m1")], labels=[LABEL])
        for n in range(3)
    ]
    section = FakeSection([("m1", ["imdb://tt1"])], existing=orphans)
    for orphan in orphans:
        await _managed_row(session, "Movies", orphan.title)

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Kept", builder="knobs_kept_3")],
        _config(delete_unconfigured=True, max_deletes=2), sweep=True,
    )

    assert not any(orphan.deleted for orphan in orphans), (
        "past the cap the sweep deletes nothing at all"
    )
    refusal = [a for a in run.actions if "refus" in a]
    assert len(refusal) == 1, run.actions
    assert "3" in refusal[0] and "2" in refusal[0], (
        "the refusal reports the real numbers: how many, and the cap"
    )
    assert "created 'Kept' with 1 item(s)" in run.actions, (
        "a refused sweep must not stop the definitions reconciling"
    )


async def test_a_dry_run_lists_the_would_deletes_without_deleting(session):
    orphan = FakeCollection("Retired Chart", [FakeItem("m1")], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[orphan])
    await _managed_row(session, "Movies", "Retired Chart")

    run = await run_library(
        session, section, "Movies", "Movie", [],
        _config(delete_unconfigured=True, apply_to_plex=False), sweep=True,
    )

    assert orphan.deleted is False
    assert "would delete 'Retired Chart': no definition builds it" in run.actions
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Retired Chart")
        )
    ).scalar_one_or_none() is not None


async def test_a_delete_is_recorded_as_an_event(session):
    orphan = FakeCollection("Retired Chart", [FakeItem("m1")], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[orphan])
    await _managed_row(session, "Movies", "Retired Chart")

    await run_library(
        session, section, "Movies", "Movie", [],
        _config(delete_unconfigured=True), sweep=True,
    )

    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.source, e.event_type) for e in events] == [
        ("collections", "collection_deleted")
    ]
    assert events[0].payload["title"] == "Retired Chart"
    assert events[0].payload["library"] == "Movies"


async def test_the_sweep_is_scoped_to_the_library_it_is_sweeping(
    session, registry_entry
):
    """A definition aimed at another library does not make its title managed
    *here*. The leftovers report is deliberately over-inclusive the other way
    -- reporting nothing is safe there -- but a delete decision that read the
    same set would leave an orphan in this library unswept forever."""
    registry_entry(_Listing("knobs_elsewhere", [("imdb", "tt1")]))
    orphan = FakeCollection("Kids Picks", [FakeItem("m1")], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[orphan])
    await _managed_row(session, "Movies", "Kids Picks")
    definitions = [CollectionDefinition(
        title="Kids Picks", builder="knobs_elsewhere", libraries=["Kids Movies"]
    )]

    assert definition_titles_for(
        definitions, section.collections(), "Movies", "Movie", _config()
    ) == set()

    await run_library(
        session, section, "Movies", "Movie", definitions,
        _config(delete_unconfigured=True), sweep=True,
    )

    assert orphan.deleted is True


async def test_the_award_years_keep_their_collections_out_of_the_sweep(session):
    """The expanding definition's own title is never a collection; the year
    collections it stands for are recognised by the builder's pattern. Missing
    that would delete every Oscars year collection on the first swept pass."""
    year = FakeCollection("Oscars Winners 2026", [FakeItem("m1")], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[year])
    await _managed_row(session, "Movies", "Oscars Winners 2026")
    definitions = [CollectionDefinition(
        title="Oscars Winners (recent ceremonies)", builder="imdb_award_years"
    )]

    titles = definition_titles_for(
        definitions, section.collections(), "Movies", "Movie", _config()
    )

    assert titles == {"Oscars Winners 2026"}
    assert "Oscars Winners (recent ceremonies)" not in titles


# --- row 115: a failed definition is a failed pass -------------------------

def _service_config(libraries=("Movies",), **overrides):
    config = _config(libraries=list(libraries), **overrides)
    config.scheduler = SimpleNamespace(collections_hours=24)
    return config


class FakeLibrary:
    def __init__(self, sections):
        self._sections = sections

    def section(self, name):
        return self._sections[name]


class FakeServer:
    def __init__(self, sections):
        self.library = FakeLibrary(sections)


async def test_a_failed_definition_makes_its_library_not_ok(session, registry_entry):
    """Row 115. The collection is still left alone -- that invariant does not
    move -- but a pass where the source died is not a success, and until now
    the only outcome that survived was "N action(s)"."""
    registry_entry(_Dead())
    section = FakeSection([("m1", ["imdb://tt1"])])
    config = _service_config()
    config.collections.definitions = [
        CollectionDefinition(title="Dead Chart", builder="knobs_dead_source")
    ]

    async with httpx.AsyncClient() as http:
        result = await reconcile_libraries(session, FakeServer({"Movies": section}), config, http)

    assert result.failed is True
    assert result.libraries[0].ok is False
    assert result.libraries[0].failed_definitions == ["Dead Chart"]
    assert "Dead Chart" in result.detail


async def test_a_clean_pass_is_still_reported_as_ok(session, registry_entry):
    registry_entry(_Listing("knobs_healthy", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"])])
    config = _service_config()
    config.collections.definitions = [
        CollectionDefinition(title="Healthy", builder="knobs_healthy")
    ]

    async with httpx.AsyncClient() as http:
        result = await reconcile_libraries(session, FakeServer({"Movies": section}), config, http)

    assert result.failed is False
    assert result.libraries[0].ok is True
    assert "Movies: 1 action(s)" in result.summary


async def test_the_scheduled_job_fails_when_a_definition_failed(
    session, registry_entry
):
    """The job is where an operator sees it: ``last_status`` comes from
    whether ``run`` raised, so a contained failure has to be re-raised here or
    the run is recorded as ok."""
    registry_entry(_Dead())
    section = FakeSection([("m1", ["imdb://tt1"])])
    config = _service_config()
    config.collections.definitions = [
        CollectionDefinition(title="Dead Chart", builder="knobs_dead_source")
    ]

    async with httpx.AsyncClient() as http:
        job = make_collections_job(
            ConfigHolder(config), lambda: FakeServer({"Movies": section}), http
        )
        with pytest.raises(CollectionsPassFailed) as failure:
            await job.run(session)

    assert "Dead Chart" in str(failure.value)
    assert "Movies" in str(failure.value)


async def test_the_scheduled_run_row_records_the_failure(
    session_factory, registry_entry
):
    """End to end through the scheduler, because that is the only surface an
    operator reads: the ``scheduled_runs`` row for a pass whose source died
    said ``ok`` before this, with the failure visible nowhere but the log."""
    registry_entry(_Dead())
    section = FakeSection([("m1", ["imdb://tt1"])])
    config = _service_config()
    config.collections.definitions = [
        CollectionDefinition(title="Dead Chart", builder="knobs_dead_source")
    ]

    async with httpx.AsyncClient() as http:
        job = make_collections_job(
            ConfigHolder(config), lambda: FakeServer({"Movies": section}), http
        )
        stop = asyncio.Event()
        scheduler = Scheduler(session_factory, [job], poll_seconds=0.01)
        task = asyncio.create_task(scheduler.run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await task

    async with session_factory() as verify:
        row = (
            await verify.execute(
                select(ScheduledRun).where(ScheduledRun.name == "collections_reconcile")
            )
        ).scalar_one()
    assert row.last_status == "failed"
    assert "Dead Chart" in row.last_detail
