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

from autoposter.collections.builders import (
    REGISTRY,
    BuilderContext,
    BuilderResult,
    SourceClients,
    register,
)
from autoposter.collections.engine import _expand, definition_titles, run_definitions
from autoposter.collections.service import _managed_titles
from autoposter.collections.sources import AWARD_YEARS_TITLE, default_definitions
from autoposter.config.schema import CollectionDefinition

LABEL = "autoposter"

AWARD_FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")
# The event id is checked against this before the event file is asked for.
VALIDATION_FIXTURE = Path(
    "tests/fixtures/collections/event_validation.yml"
).read_text(encoding="utf-8")


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
    """``ok=False`` kills the **event file** and leaves the validation list
    working -- which is the failure the event memo has to cover. Killing both
    would prove only that the validation memo works."""
    async def handle(request):
        url = str(request.url)
        counter.append(url)
        if "event_validation" in url:
            return httpx.Response(200, text=VALIDATION_FIXTURE)
        if not ok:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text=AWARD_FIXTURE)

    return httpx.MockTransport(handle)


async def test_the_oscars_dataset_is_fetched_once_for_every_collection(session):
    """One file holds every ceremony, and seven collections read it. Seven
    requests would be seven chances to be rate-limited mid-pass.

    Two requests, not one: the validation list the event id is checked against
    is its own file, and it is memoised separately -- so the seven collections
    still cost one fetch each of two files, whatever the pass builds."""
    requests: list[str] = []
    section = FakeSection([("m1", ["imdb://tt31193180"])])
    config = _config(charts=False)

    async with httpx.AsyncClient(transport=_requests_for(requests)) as http:
        await _run(
            session, section,
            [d for d in default_definitions(config, "Movie") if d.builder != "cs_bucket"],
            config, http=http,
        )

    assert requests == [
        "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master/event_validation.yml",
        "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master/events/ev0000003.yml",
    ], requests


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

    assert requests == [
        "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master/event_validation.yml",
        "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master/events/ev0000003.yml",
    ], "the dead event file was asked for once, not once per collection"
    assert actions == [
        "'Oscars Best Picture Winners': source returned no items; "
        "leaving the collection untouched",
        "'Oscars Best Director Winners': source returned no items; "
        "leaving the collection untouched",
    ], "a dead dataset names no year collections -- it does not know the years"


# --- what the engine puts on a context ------------------------------------
#
# The bundle and the cache are threaded exactly the way ``summaries`` already
# is: built where config, secrets and the HTTP client all exist, handed down
# per pass. These pin that they arrive, because a builder reaching for a
# client the engine forgot to pass would report "not configured" against a
# service that is.


class _Probe:
    """A builder that records the context it was handed and builds nothing."""

    def __init__(self, type_name="test_probe"):
        self.type_name = type_name
        self.ctx = None

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        self.ctx = ctx
        return BuilderResult(ids=[])


class _CountingSection(FakeSection):
    """A section that counts the full walks ``section.all()`` costs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.walks = 0

    def all(self):
        self.walks += 1
        return super().all()


class _PlexReader:
    """A builder that reads the library through the bundle's Plex accessor."""

    def __init__(self, type_name):
        self.type_name = type_name
        self.index = None
        self.section = None

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        self.index = ctx.sources.plex.owned_index()
        self.section = ctx.sources.plex.section()
        return BuilderResult(ids=[("plex", "m1")])


async def test_the_engine_hands_every_builder_the_source_bundle(session, registry_entry):
    """One bundle per pass reaches the builder, clients and all."""
    probe = registry_entry(_Probe())
    bundle = SourceClients(mdblist=object(), tvdb=object(), radarr=object())
    section = FakeSection([("m1", ["imdb://tt1"])])

    await _run(
        session, section,
        [CollectionDefinition(title="Probe", builder="test_probe")],
        _config(), sources=bundle,
    )

    assert probe.ctx.sources.mdblist is bundle.mdblist
    assert probe.ctx.sources.tvdb is bundle.tvdb
    assert probe.ctx.sources.radarr is bundle.radarr


async def test_a_builder_gets_a_bundle_even_when_the_caller_passed_none(
    session, registry_entry
):
    """A direct caller with no clients to offer must not hand builders a None
    bundle: the "is this client configured" check belongs to one field, not to
    the bundle and then the field."""
    probe = registry_entry(_Probe("test_probe_no_bundle"))
    section = FakeSection([("m1", ["imdb://tt1"])])

    await _run(
        session, section,
        [CollectionDefinition(title="Probe", builder="test_probe_no_bundle")],
        _config(),
    )

    assert isinstance(probe.ctx.sources, SourceClients)
    assert probe.ctx.sources.mdblist is None


async def test_the_engine_hands_every_builder_the_provider_cache(session, registry_entry):
    """``ctx.cache`` was documented as never populated. It is now the process's
    real cache, which is what makes a list fetch through ``fetch_json`` cached
    and credential-stripped rather than raw."""
    probe = registry_entry(_Probe("test_probe_cache"))
    cache = object()
    section = FakeSection([("m1", ["imdb://tt1"])])

    await _run(
        session, section,
        [CollectionDefinition(title="Probe", builder="test_probe_cache")],
        _config(), cache=cache,
    )

    assert probe.ctx.cache is cache


async def test_the_plex_accessor_shares_the_engines_owned_index(session, registry_entry):
    """The index costs a full ``section.all()``. Two builders reading it, plus
    the engine resolving both their results, is still one walk -- an accessor
    that built its own would double the most expensive call in a pass."""
    first = registry_entry(_PlexReader("test_plex_reader_one"))
    second = registry_entry(_PlexReader("test_plex_reader_two"))
    section = _CountingSection([("m1", ["imdb://tt1"])])

    await _run(
        session, section,
        [
            CollectionDefinition(title="One", builder="test_plex_reader_one"),
            CollectionDefinition(title="Two", builder="test_plex_reader_two"),
        ],
        _config(),
    )

    assert section.walks == 1, "the accessor must reuse the engine's lazy index"
    assert first.index is second.index
    assert first.section is section
    assert "m1" in first.index["plex"]


# --- roadmap row 141: expansion drops the placeholder's settings -----------
#
# An expanding definition is the only definition an operator writes for a
# family of collections, so every per-collection setting on it -- labels, the
# sort title, the member cap -- is a setting for the whole family. Rebuilding
# the units as bare definitions silently dropped all of them.

_RIDE_ALONGS = {
    "labels": ["Awards"],
    "label_sync": True,
    "item_label": ["Oscar Winner"],
    "sort_title": "!110_Oscars",
    "collection_mode": "hide",
    "visible_library": True,
    "visible_home": False,
    "visible_shared": False,
    "hub_priority": 0,
    "limit": 25,
    "sync_mode": "append",
    "tmdb_summary": 42,
}


class _Expander:
    """An expanding builder returning whatever units the test gave it."""

    def __init__(self, units, type_name="test_expander"):
        self.type_name = type_name
        self._units = units

    async def expand(self, ctx: BuilderContext) -> list[CollectionDefinition]:
        return list(self._units)

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        return BuilderResult(ids=[])


def _unit(**overrides) -> CollectionDefinition:
    return CollectionDefinition(
        title="Unit", builder="plex_id", params={"ids": ["1"]}, **overrides
    )


async def test_expanded_definitions_inherit_the_placeholders_settings():
    """Row 141. Every field that describes the *collection* rather than its
    membership source rides along; the expander still owns its own title,
    params and summary."""
    placeholder = CollectionDefinition(
        title="Placeholder", builder="plex_id", params={"ids": ["1"]},
        summary="the placeholder's own", **_RIDE_ALONGS,
    )

    [expanded] = await _expand(
        _Expander([_unit(summary="the unit's own")]), placeholder,
        BuilderContext(library="Movies", library_type="Movie"),
    )

    assert {name: getattr(expanded, name) for name in _RIDE_ALONGS} == _RIDE_ALONGS
    assert expanded.title == "Unit"
    assert expanded.summary == "the unit's own"


async def test_an_expander_that_sets_a_field_itself_keeps_its_own_value():
    """Inheritance fills gaps; it never overrides. A builder that names a sort
    title per unit knows something the placeholder does not."""
    placeholder = CollectionDefinition(
        title="Placeholder", builder="plex_id", params={"ids": ["1"]},
        sort_title="!110_Placeholder", limit=25, labels=["Awards"],
    )

    [expanded] = await _expand(
        _Expander([_unit(sort_title="!110_Unit", limit=5)]), placeholder,
        BuilderContext(library="Movies", library_type="Movie"),
    )

    assert expanded.sort_title == "!110_Unit"
    assert expanded.limit == 5
    assert expanded.labels == ["Awards"], "the fields it did not set still ride along"


async def test_the_oscars_year_collections_inherit_the_placeholders_settings():
    """The live case: ``imdb_award_years`` is the one shipped expanding
    builder, and an operator labelling or capping it means all of its year
    collections. Its own per-year summary and sort are untouched."""
    placeholder = CollectionDefinition(
        title=AWARD_YEARS_TITLE, builder="imdb_award_years",
        labels=["Awards"], sort_title="!110_Oscars", limit=25,
    )

    async with httpx.AsyncClient(transport=_requests_for([])) as http:
        units = await _expand(
            REGISTRY["imdb_award_years"], placeholder,
            BuilderContext(library="Movies", library_type="Movie", http=http),
        )

    assert units, "the fixture holds at least one recent ceremony"
    for unit in units:
        assert unit.labels == ["Awards"]
        assert unit.sort_title == "!110_Oscars"
        assert unit.limit == 25
        assert unit.sort == "release", "the builder's own choice survives"
        assert unit.summary.startswith("Academy Awards")
