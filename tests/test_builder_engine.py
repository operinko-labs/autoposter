"""The engine's own decisions: containment, the member cap, schedule gating.

What a builder returns is the builder's business; what happens to it is the
engine's, and these are the four rules a collection's safety rests on:

- a source that fails leaves its collection exactly as it was, and the rest of
  the pass runs;
- ``limit`` counts members, so it is applied after resolution;
- a definition outside its schedule is skipped, not failed -- and is still a
  managed title, or the leftovers report would invite an operator to delete a
  collection that is merely waiting for its turn.

The membership modes, the delete sweep and how a failed definition is reported
are ``tests/test_builder_knobs.py``'s.

The Oscars memoisation is here too: seven collections, one dataset, one request
-- and one request when it fails, not seven.
"""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, BuilderResult, register
from autoposter.collections.engine import definition_titles, run_definitions
from autoposter.collections.service import _managed_titles
from autoposter.collections.sources import default_definitions
from autoposter.config.schema import CollectionDefinition

LABEL = "autoposter"

AWARD_FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, key, guids):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]


class FakeCollection:
    def __init__(self, title, items=(), labels=()):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self.summary = None
        self.sort_set = None
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


class FakeSection:
    def __init__(self, items=(), existing=()):
        self._items = [FakeItem(key, guids) for key, guids in items]
        self._existing = {c.title: c for c in existing}

    def all(self):
        return list(self._items)

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
        "protect_labels": [], "posters": False, "charts": True, "awards": True,
        "separators": False, "definitions": [],
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
        self._ids = ids
        self._summary = summary
        self.builds = 0

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        self.builds += 1
        return BuilderResult(ids=list(self._ids), summary=self._summary)


class _Dead:
    """A builder whose source is down."""

    type_name = "test_dead_source"

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        raise httpx.HTTPError("the source is down")


async def _run(session, section, definitions, config, **kwargs):
    return await run_definitions(
        session, section, "Movies", "Movie", definitions, config, **kwargs
    )


async def test_a_dead_source_leaves_its_collection_alone_and_the_pass_goes_on(
    session, registry_entry
):
    """The containment invariant, end to end. The failed definition's
    collection keeps every member it had -- an empty desired set must never be
    read as "remove everything" -- and the healthy definition after it is still
    built."""
    registry_entry(_Dead())
    registry_entry(_Listing("test_healthy", [("imdb", "tt2")]))
    kept = FakeItem("m1", ["imdb://tt1"])
    live = FakeCollection("Dead Chart", [kept], labels=[LABEL])
    section = FakeSection(
        [("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])], existing=[live]
    )

    actions = await _run(
        session, section,
        [
            CollectionDefinition(title="Dead Chart", builder="test_dead_source"),
            CollectionDefinition(title="Healthy", builder="test_healthy"),
        ],
        _config(),
    )

    assert actions == [
        "'Dead Chart': source returned no items; leaving the collection untouched",
        "created 'Healthy' with 1 item(s)",
    ]
    assert [i.ratingKey for i in live._live] == ["m1"], (
        "a failed source must not empty the collection it was going to fill"
    )


async def test_a_dead_source_reports_nothing_about_the_exception(session, registry_entry):
    """Provider errors routinely carry the URL they failed on, and those carry
    credentials. Whatever a builder raises stays in the log."""
    registry_entry(_Dead())
    section = FakeSection([("m1", ["imdb://tt1"])])

    actions = await _run(
        session, section,
        [CollectionDefinition(title="Dead Chart", builder="test_dead_source")],
        _config(),
    )

    assert not any("down" in action or "HTTPError" in action for action in actions)


async def test_the_limit_caps_members_after_resolution(session, registry_entry):
    """A limit counts collection members, not candidate ids: capping before
    resolution would leave a short collection whenever the library happened to
    be missing one of the first few titles."""
    registry_entry(
        _Listing("test_limited", [("imdb", "tt404"), ("imdb", "tt1"), ("imdb", "tt2"),
                                  ("imdb", "tt3")])
    )
    section = FakeSection([
        ("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"]), ("m3", ["imdb://tt3"]),
    ])

    actions = await _run(
        session, section,
        [CollectionDefinition(title="Top Two", builder="test_limited", limit=2)],
        _config(),
    )

    assert actions == ["created 'Top Two' with 2 item(s)"]
    assert [i.ratingKey for i in section._existing["Top Two"]._live] == ["m1", "m2"]


async def test_a_definition_gated_to_every_other_pass_runs_on_alternate_passes(
    session, registry_entry
):
    builder = registry_entry(_Listing("test_gated", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"])])
    definition = CollectionDefinition(
        title="Every Other", builder="test_gated", schedule={"every_n_runs": 2}
    )

    first = await _run(session, section, [definition], _config(), run_index=0)
    second = await _run(session, section, [definition], _config(), run_index=1)
    third = await _run(session, section, [definition], _config(), run_index=2)

    assert first == ["created 'Every Other' with 1 item(s)"]
    assert second == [], "a gated-off pass must contribute no actions"
    assert third == [], "and must not have been rebuilt -- the hash is unchanged"
    assert builder.builds == 2, (
        "the gate must skip before the fetch, not after it"
    )


async def test_a_months_window_skips_the_definition_outside_it(session, registry_entry):
    from datetime import UTC, datetime

    registry_entry(_Listing("test_seasonal", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"])])
    definition = CollectionDefinition(
        title="Christmas", builder="test_seasonal", schedule={"months": [12]}
    )

    in_july = await _run(
        session, section, [definition], _config(), now=datetime(2026, 7, 1, tzinfo=UTC)
    )
    in_december = await _run(
        session, section, [definition], _config(), now=datetime(2026, 12, 1, tzinfo=UTC)
    )

    assert in_july == []
    assert in_december == ["created 'Christmas' with 1 item(s)"]


async def test_a_definition_aimed_at_another_library_is_skipped(session, registry_entry):
    registry_entry(_Listing("test_targeted", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"])])

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Kids Only", builder="test_targeted", libraries=["Kids Movies"]
        )],
        _config(),
    )

    assert actions == []
    assert section._existing == {}


async def test_a_gated_definition_is_still_a_managed_title(registry_entry):
    """Skipping a pass is not abandoning a collection. If the leftovers report
    stopped counting a gated definition as managed, it would name that
    collection as a prior tool's litter."""
    registry_entry(_Listing("test_gated_titles", [("imdb", "tt1")]))
    definition = CollectionDefinition(
        title="Every Other", builder="test_gated_titles", schedule={"every_n_runs": 12}
    )

    assert "Every Other" in definition_titles([definition], [], "Movie", _config())


def test_the_smart_family_refuses_the_membership_knobs():
    """Plex evaluates a smart collection's filter itself, so there is no
    membership for a limit to cap or an append to add to. Accepting the setting
    and ignoring it would be worse than refusing it."""
    with pytest.raises(ValidationError, match="limit"):
        CollectionDefinition(title="Buckets", builder="cs_bucket", limit=5)
    with pytest.raises(ValidationError, match="sync_mode"):
        CollectionDefinition(title="Buckets", builder="cs_bucket", sync_mode="append")


def test_managed_titles_covers_every_definition_a_pass_runs(registry_entry):
    """The leftovers report and the reconcile pass must agree about what this
    service manages, so both read the same definition list -- including the
    operator's own, and including the Oscars years, which only exist as titles
    once the dataset has been read."""
    registry_entry(_Listing("test_operator_defined", [("imdb", "tt1")]))
    config = _config(separators=True)
    config.collections.definitions = [
        CollectionDefinition(title="My Favourites", builder="test_operator_defined")
    ]
    candidates = [
        SimpleNamespace(title="Oscars Winners 2026"),
        SimpleNamespace(title="The Ninja Trilogy"),
    ]

    titles = _managed_titles(candidates, "Movie", config)

    assert {
        "Age 17+ Movies", "Not Rated Movies", "Ratings Collections",
        "IMDb Popular", "IMDb Top 250", "IMDb Lowest Rated",
        "Oscars Best Picture Winners", "Oscars Best Director Winners",
        "Oscars Winners 2026", "My Favourites",
    } <= titles
    assert "The Ninja Trilogy" not in titles
    assert not any(title.startswith("Oscars Winners (") for title in titles), (
        "the placeholder definition the year collections expand from is not "
        "itself a collection"
    )


def _requests_for(counter, ok=True):
    async def handle(request):
        counter.append(str(request.url))
        if not ok:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=AWARD_FIXTURE)

    return httpx.MockTransport(handle)


async def test_the_oscars_dataset_is_fetched_once_for_every_collection(session):
    """One file holds every ceremony, and seven collections read it. Seven
    requests would be seven chances to be rate-limited mid-pass."""
    requests: list[str] = []
    section = FakeSection([("m1", ["imdb://tt31193180"])])
    config = _config(charts=False)

    async with httpx.AsyncClient(transport=_requests_for(requests)) as http:
        await _run(
            session, section,
            [d for d in default_definitions(config, "Movie") if d.builder != "cs_bucket"],
            config, http=http,
        )

    assert len(requests) == 1, requests


async def test_a_dead_oscars_dataset_is_asked_for_once_too(session):
    """The memo has to cover the failure. Otherwise a source that is down is
    retried once per collection -- seven requests to a server already in
    trouble, and seven stack traces."""
    requests: list[str] = []
    section = FakeSection([("m1", ["imdb://tt31193180"])])
    config = _config(charts=False)

    async with httpx.AsyncClient(transport=_requests_for(requests, ok=False)) as http:
        actions = await _run(
            session, section,
            [d for d in default_definitions(config, "Movie") if d.builder != "cs_bucket"],
            config, http=http,
        )

    assert len(requests) == 1, requests
    assert actions == [
        "'Oscars Best Picture Winners': source returned no items; "
        "leaving the collection untouched",
        "'Oscars Best Director Winners': source returned no items; "
        "leaving the collection untouched",
    ], "a dead dataset names no year collections -- it does not know the years"
