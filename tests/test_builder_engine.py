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
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from plexapi.exceptions import BadRequest
from pydantic import ValidationError

from autoposter.collections import groups
from autoposter.collections.builders import (
    REGISTRY,
    BuilderContext,
    BuilderResult,
    SourceClients,
    register,
)
from autoposter.collections.engine import (
    _expand,
    definition_titles,
    run_definitions,
    run_library,
)
from autoposter.collections.service import _managed_titles
from autoposter.collections.sources import AWARD_YEARS_TITLE, default_definitions
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ItemFacts

from conftest import seed_media_item
from autoposter.facts.mdblist import MDBListClient
from autoposter.providers.tmdb_lists import TmdbListClient

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
    def __init__(self, key, guids, **attributes):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]
        # Listing attributes, for the tests that filter. ``filter_values``
        # reads them off the item with ``object.__getattribute__``, so a plain
        # instance attribute is exactly what a resolved plexapi item presents.
        self.__dict__.update(attributes)


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
        self.titleSort = None
        self.sort_title_set = None
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

    def editSortTitle(self, sortTitle, locked=True):
        # Row 49: EVERY managed collection derives its group's sort-title
        # prefix now, so this route is reached on every apply here rather than
        # only for a definition that named one.
        self.sort_title_set = sortTitle
        self.titleSort = sortTitle

    def query(self, key, method=None, **kwargs):
        self.summary = key

    def addLabel(self, labels, locked=True):
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels


class FakeTag:
    """A ``<Genre>``/``<Label>``/``<Collection>`` child, as plexapi presents it."""

    def __init__(self, tag):
        self.tag = tag


class FakeMetadataItem:
    """What ``section.fetchItems`` hands back for the batched tier-2 read.

    A DIFFERENT object from the listing ``FakeItem`` above, deliberately: the
    whole point of the batched read is that the listing item does not carry
    these families, so an implementation that got its tags off the resolved
    item rather than out of the enrichment would find nothing here.
    """

    def __init__(self, key, genres=(), labels=(), collections=()):
        self.ratingKey = key
        self.genres = [FakeTag(t) for t in genres]
        self.labels = [FakeTag(t) for t in labels]
        self.collections = [FakeTag(t) for t in collections]
        # Languages come off ``<Media><Part><Stream>``; no test here needs one,
        # and an empty media list is what a show carries anyway.
        self.media = []


class FakeSection:
    def __init__(self, items=(), existing=(), metadata=None):
        # ``(key, guids)``, or ``(key, guids, attributes)`` for a test that
        # filters on a listing attribute.
        self._items = [
            FakeItem(entry[0], entry[1], **(entry[2] if len(entry) > 2 else {}))
            for entry in items
        ]
        self._existing = {c.title: c for c in existing}
        # ``{rating_key: FakeMetadataItem}`` for the tier-2 enrichment. A key
        # the test leaves out is one Plex does not answer for, which is the
        # refusal law's own case.
        self._metadata = dict(metadata or {})
        self.fetches: list[list[str]] = []

    def all(self):
        return list(self._items)

    def fetchItems(self, ekey):
        """plexapi's list-of-ints form -- ``/library/metadata/{k1,k2,...}``.

        Every call is recorded, because "how many fetches, for which keys" is
        what the enrichment tests are actually asserting.
        """
        self.fetches.append([str(key) for key in ekey])
        return [self._metadata[str(key)] for key in ekey if str(key) in self._metadata]

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        collection = FakeCollection(title, items or [])
        self._existing[title] = collection
        return collection

    def listFilterChoices(self, field, libtype=None):
        """Roadmap row 158's vocabulary read. Every tier-2 test in this file
        filters on ``genre: Horror``, so the fake has to be a library that USES
        that word -- otherwise the engine drops the value and the memberships
        below become assertions about the drop rather than about the
        enrichment. What row 158 itself does with an unknown value is
        ``tests/test_collection_filter_vocabulary.py``'s subject."""
        return [
            type("Choice", (), {"title": t, "key": t})()
            for t in ("Horror", "Thriller", "Comedy")
        ]


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": True, "awards": True,
        "separators": False, "definitions": [], "presets": [],
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
        # Row 49: a test builder belongs to no catalog category, so its
        # collections land in the operator group -- last in the canonical
        # order, section 110. Every collection this service manages derives one
        # now, which is why this line appears throughout this file.
        "set the sort title of 'Healthy' to '!110_Healthy'",
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

    assert actions == [
        "created 'Top Two' with 2 item(s)",
        "set the sort title of 'Top Two' to '!110_Top Two'",
    ]
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

    assert first == [
        "created 'Every Other' with 1 item(s)",
        "set the sort title of 'Every Other' to '!110_Every Other'",
    ]
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
    assert in_december == [
        "created 'Christmas' with 1 item(s)",
        "set the sort title of 'Christmas' to '!110_Christmas'",
    ]


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


async def test_mdblist_sync_reads_is_movie_off_the_real_library_type(
    session, registry_entry
):
    """Row 31, entry-point law. ``ctx.library_type`` is title-cased
    (``"Movie"``/``"Show"``, per ``service.LIBRARY_TYPES`` and every other
    consumer of the field) -- not the lowercase ``"movie"`` a helper-level
    test asserting ``is_movie`` as a literal would let slip past. Both passes
    go through the real ``run_library`` entry point and the real
    ``MDBListClient`` over an ``httpx.MockTransport``, so what is pinned is
    the actual payload key on the wire, not an intermediate value."""
    registry_entry(_Listing("test_mdblist_movie", [("imdb", "tt1")]))
    registry_entry(_Listing("test_mdblist_show", [("imdb", "tt1")]))

    seen: dict[str, str] = {}

    def handler(request):
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MDBListClient("KEY", http)

        await run_library(
            session, FakeSection([("m1", ["imdb://tt1"])]), "Movies", "Movie",
            [CollectionDefinition(
                title="Movie Push", builder="test_mdblist_movie",
                sync_to_mdb_list="me/heat",
            )],
            _config(mdblist_sync_apply=True),
            sources=SourceClients(mdblist=client),
        )
        assert '"movies"' in seen["body"], seen["body"]

        await run_library(
            session, FakeSection([("m1", ["imdb://tt1"])]), "TV Shows", "Show",
            [CollectionDefinition(
                title="Show Push", builder="test_mdblist_show",
                sync_to_mdb_list="me/heat",
            )],
            _config(mdblist_sync_apply=True),
            sources=SourceClients(mdblist=client),
        )
        assert '"shows"' in seen["body"], seen["body"]


# --- roadmap row 141: expansion drops the placeholder's settings -----------
#
# An expanding definition is the only definition an operator writes for a
# family of collections, so every per-collection setting on it -- labels, the
# sort title, the member cap -- is a setting for the whole family. Rebuilding
# the units as bare definitions silently dropped all of them.

_RIDE_ALONGS = {
    "filters": {"year.gte": 2000},
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
    "changes_webhook": "http://hooks.example.test/changes/fake-1",
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
        filters={"year.gte": 2000},
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
        assert unit.filters == {"year.gte": 2000}, (
            "a filter on the placeholder is a filter on every year it expands to"
        )
        assert unit.sort == "release", "the builder's own choice survives"
        assert unit.summary.startswith("Academy Awards")


async def test_a_family_that_built_nothing_says_why_in_the_pass(session):
    """A refusing family is the one definition shape a pass could report
    NOTHING about.

    ``run_library`` appends one ``DefinitionResult`` per unit an expander
    RETURNS, so a family that refuses -- an empty enumeration, an all-excluded
    family, an over-cap fan-out, a franchise TMDb cannot name -- returns ``[]``
    and produces no row at all: not that it ran, not why it built nothing, not
    how far the facts pipeline has got. ``facts_family`` writes those reasons
    into the pass's scratch for exactly this, and here is where they join the
    other "nothing was done, and here is why" strings the pass already reports
    (the filter-emptied and nothing-owned branches).

    The engine asks the REGISTRY entry rather than importing the module -- the
    same protocol ``_family_state`` states for ``family_label``.
    """
    await seed_media_item(session, "m1", library="Movies", kind="movie", title="X")

    actions = await _run(
        session, FakeSection([("m1", ["imdb://tt1"])]),
        [CollectionDefinition(
            title="Countries of origin", builder="facts_family",
            params={"type": "origin_country"},
        )],
        _config(),
    )

    assert actions == [
        "'Countries of origin' built nothing: no origin_country values are "
        "stored for 'Movies' yet. The facts pipeline has visited 0 of 1 "
        "item(s) there; this family fills in as the ratings-drift sweep works "
        "through the rest (scheduler.drift_days, scheduler.drift_batch_size)"
    ]


async def test_a_familys_notes_reach_the_pass_beside_the_units_it_did_build(session):
    """The other half of the wire above: a family that builds SOMETHING and
    still has something to say.

    The refusal shapes return ``[]``, so the slice is the only thing the pass
    reports for them. A franchise TMDb cannot name is the shape where both
    halves run -- the key is dropped and reported, the rest of the family is
    returned and built -- and ``deploy/README.md`` promises an operator that a
    family refusing "for any other reason" says so too. The note has to land in
    the same ``actions`` list as the unit's own line, and before it.
    """
    for rating_key, collection_id in (("m1", 1241), ("m2", 8091)):
        item = await seed_media_item(session, rating_key, library="Movies", kind="movie", title="X")
        session.add(ItemFacts(item_id=item.id, tmdb_collection_id=collection_id))
    await session.flush()

    def handler(request):
        if request.url.path.endswith("/8091"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={
            "id": 1241, "name": "Harry Potter Collection", "parts": [{"id": 671}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        actions = await _run(
            session, FakeSection([("m1", ["tmdb://671"])]),
            [CollectionDefinition(
                title="Franchises", builder="facts_family",
                params={"type": "tmdb_collection"},
            )],
            _config(),
            http=http,
            sources=SourceClients(tmdb=TmdbListClient("tok", http)),
        )

    dropped = [
        index for index, action in enumerate(actions)
        if "8091" in action and "title_override" in action
    ]
    built = actions.index("created 'Harry Potter Collection' with 1 item(s)")
    assert dropped, actions
    assert dropped[0] < built, actions


# --- roadmap row 96: the filter stage --------------------------------------
#
# ``filters:`` narrows what the builder resolved, in one stage between
# resolution and the member cap. Four properties are load-bearing and each has
# a test here: the ORDER (a cap counts what survives the filter, not what the
# filter was given), the COUNT (``filtered`` says how many were excluded, and
# it reaches the preview), what the rest of the outcome is computed FROM (the
# post-filter set -- ``skipped``, the diff, the members written), and
# CONTAINMENT (a filter that cannot evaluate leaves its collection exactly as
# it was, the way a dead source does).


async def test_a_filter_keeps_the_items_that_pass_and_counts_the_rest(
    session, registry_entry
):
    registry_entry(_Listing(
        "test_filtered", [("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt3")]
    ))
    section = FakeSection([
        ("m1", ["imdb://tt1"], {"year": 1994}),
        ("m2", ["imdb://tt2"], {"year": 2005}),
        ("m3", ["imdb://tt3"], {"year": 2011}),
    ])

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(
            title="This Century", builder="test_filtered", filters={"year.gte": 2000}
        )],
        _config(),
    )

    assert [i.ratingKey for i in section._existing["This Century"]._live] == ["m2", "m3"]
    [result] = run.definitions
    assert result.filtered == 1, "the count is what the filter excluded"
    assert result.failed is False and result.skipped is False


async def test_the_filter_runs_before_the_limit(session, registry_entry):
    """The seam's order, and the test is the mutation: filtering *after* the
    cap gives this definition one member, not two -- the cap would spend a slot
    on ``m1`` and the filter would then throw it away. ``limit`` counts
    collection members (see ``test_the_limit_caps_members_after_resolution``),
    and an item the filter excludes was never going to be one."""
    registry_entry(_Listing(
        "test_filter_limit", [("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt3")]
    ))
    section = FakeSection([
        ("m1", ["imdb://tt1"], {"year": 1994}),
        ("m2", ["imdb://tt2"], {"year": 2005}),
        ("m3", ["imdb://tt3"], {"year": 2011}),
    ])

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Two Modern", builder="test_filter_limit",
            filters={"year.gte": 2000}, limit=2,
        )],
        _config(),
    )

    assert actions == [
        "created 'Two Modern' with 2 item(s)",
        "set the sort title of 'Two Modern' to '!110_Two Modern'",
    ]
    assert [i.ratingKey for i in section._existing["Two Modern"]._live] == ["m2", "m3"]


async def test_a_definition_whose_filter_excludes_everything_is_skipped(
    session, registry_entry
):
    """Empty after filtering is empty, which ``lists.py`` reads as "make no
    changes" -- the collection keeps the members it had. A filter that matches
    nothing must not empty a live collection, exactly as a dead source must
    not."""
    registry_entry(_Listing("test_filter_all_out", [("imdb", "tt1")]))
    kept = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Nothing Left", [kept], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"], {"year": 1994})], existing=[live])

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(
            title="Nothing Left", builder="test_filter_all_out",
            filters={"year.gte": 2000},
        )],
        _config(),
    )

    assert [i.ratingKey for i in live._live] == ["m9"]
    [result] = run.definitions
    assert result.skipped is True and result.filtered == 1
    assert result.failed is False, "a filter that matches nothing is not a failure"


async def test_a_filter_excluding_every_member_says_so_not_the_source(
    session, registry_entry
):
    """The action string is the operator's report of what happened. The
    source did return an item here -- the filter is what emptied the
    collection -- so the message must name the filter, not repeat the
    dead-source wording (roadmap: filter outcomes misattributed)."""
    registry_entry(_Listing("test_filter_all_out_msg", [("imdb", "tt1")]))
    kept = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Nothing Left Msg", [kept], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"], {"year": 1994})], existing=[live])

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Nothing Left Msg", builder="test_filter_all_out_msg",
            filters={"year.gte": 2000},
        )],
        _config(),
    )

    assert actions == [
        "'Nothing Left Msg': the filter excluded every member; "
        "leaving the collection untouched"
    ]


async def test_a_filter_that_cannot_evaluate_says_so_not_the_source(
    session, registry_entry
):
    """Same report discipline for the failure path: the message must say the
    filter could not be evaluated, not that the source returned nothing."""
    registry_entry(_Listing("test_filter_broken_msg", [("imdb", "tt1")]))
    kept = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Broken Filter Msg", [kept], labels=[LABEL])
    section = FakeSection([
        ("m1", ["imdb://tt1"], {"year": "nineteen ninety-four"}),
    ], existing=[live])

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Broken Filter Msg", builder="test_filter_broken_msg",
            filters={"year.gte": 2000},
        )],
        _config(),
    )

    assert actions == [
        "'Broken Filter Msg': the filter could not be evaluated; "
        "leaving the collection untouched"
    ]


# --- roadmap row 197: the tier-2 enrichment pass -------------------------------
#
# ``genre``/``label``/``collection``/``audio_language``/``subtitle_language``
# are not in the section listing -- the listing truncates or strips them -- so
# a definition filtering on one costs ONE batched
# ``/library/metadata/{k1,k2,...}`` read for its resolved set, taken before
# evaluation. Four properties, and the third is the law the whole tier rests
# on:
#
# - the fetch is SCOPED to the resolved set, and happens once for it;
# - the memo is the PASS's, so a second definition pays only for keys the
#   first did not fetch (``ctx.run_cache``, engine.py:306 -- a fresh dict per
#   definition would leave every test below green and refetch the overlap);
# - an item the batch did not answer for REFUSES the definition. Never
#   "this item has no genres": that is a full, plausible, wrong membership,
#   and the containment is a filter failure's -- empty items, "make no
#   changes", the definition reported failed and the pass going on;
# - a tier-1-only filter pays nothing at all.


async def test_a_tier2_filter_enriches_only_the_resolved_set(session, registry_entry):
    """One fetch, for exactly the keys this definition resolved -- not the
    library. ``m104`` is in the library and in the metadata index and must not
    be asked for: the enrichment is scoped to what the builder returned, which
    is what keeps a 40-item definition at one request instead of 1955."""
    registry_entry(_Listing(
        "test_t2_scope", [("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt3")]
    ))
    section = FakeSection(
        [("101", ["imdb://tt1"]), ("102", ["imdb://tt2"]),
         ("103", ["imdb://tt3"]), ("104", ["imdb://tt4"])],
        metadata={
            "101": FakeMetadataItem("101", genres=("Horror", "Thriller")),
            "102": FakeMetadataItem("102", genres=("Comedy",)),
            "103": FakeMetadataItem("103", genres=("Horror",)),
            "104": FakeMetadataItem("104", genres=("Horror",)),
        },
    )

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(
            title="Scary", builder="test_t2_scope", filters={"genre": "Horror"}
        )],
        _config(),
    )

    assert section.fetches == [["101", "102", "103"]], (
        "one batched read, for the resolved set and nothing else"
    )
    assert [i.ratingKey for i in section._existing["Scary"]._live] == ["101", "103"]
    [result] = run.definitions
    assert result.filtered == 1 and result.failed is False


async def test_a_tier2_filter_reads_the_enrichment_not_the_listing_item(
    session, registry_entry
):
    """The mutation this catches is the tempting one: reading ``item.genres``
    off the resolved item instead of the enrichment. The listing items here
    carry a DIFFERENT, truncated genre set -- ``m101`` lists as Comedy and is
    really a Horror film, which is the probe's own `2 Fast 2 Furious` shape --
    so an accessor that read the item would build the exact inverse of this
    collection."""
    registry_entry(_Listing("test_t2_source", [("imdb", "tt1"), ("imdb", "tt2")]))
    section = FakeSection(
        [("101", ["imdb://tt1"], {"genres": [FakeTag("Comedy")]}),
         ("102", ["imdb://tt2"], {"genres": [FakeTag("Horror")]})],
        metadata={
            "101": FakeMetadataItem("101", genres=("Comedy", "Horror")),
            "102": FakeMetadataItem("102", genres=("Comedy",)),
        },
    )

    await _run(
        session, section,
        [CollectionDefinition(
            title="Really Scary", builder="test_t2_source", filters={"genre": "Horror"}
        )],
        _config(),
    )

    assert [i.ratingKey for i in section._existing["Really Scary"]._live] == ["101"]


async def test_two_tier2_definitions_share_the_run_cache(session, registry_entry):
    """THE WIRING CHECK. The enrichment's memo is the PASS's ``run_cache``
    (engine.py:306), the same dict every builder's scratch lives on -- not a
    fresh one per definition. Handing each definition its own would leave every
    other test in this block passing and quietly refetch every overlapping key,
    which on a library of forty definitions over one section is the N+1 this
    tier was built to avoid, one batch at a time.

    So the second definition's fetch asks for ``103`` alone: ``102`` was
    answered by the first definition's read and is already in the pass's
    cache."""
    registry_entry(_Listing("test_t2_first", [("imdb", "tt1"), ("imdb", "tt2")]))
    registry_entry(_Listing("test_t2_second", [("imdb", "tt2"), ("imdb", "tt3")]))
    section = FakeSection(
        [("101", ["imdb://tt1"]), ("102", ["imdb://tt2"]), ("103", ["imdb://tt3"])],
        metadata={
            "101": FakeMetadataItem("101", genres=("Horror",)),
            "102": FakeMetadataItem("102", genres=("Horror",)),
            "103": FakeMetadataItem("103", genres=("Horror",)),
        },
    )

    await _run(
        session, section,
        [
            CollectionDefinition(
                title="Scary One", builder="test_t2_first", filters={"genre": "Horror"}
            ),
            CollectionDefinition(
                title="Scary Two", builder="test_t2_second", filters={"genre": "Horror"}
            ),
        ],
        _config(),
    )

    assert section.fetches == [["101", "102"], ["103"]], (
        "the second definition pays only for the key the first did not fetch"
    )
    assert [i.ratingKey for i in section._existing["Scary Two"]._live] == ["102", "103"]


async def test_an_item_missing_from_the_enrichment_refuses_the_definition(
    session, registry_entry
):
    """THE REFUSAL LAW. Plex answers the batch without ``103``. The
    plausible-and-wrong reading is "then it has no genres", which would build
    this collection in full, minus one member, with nothing anywhere saying so.
    Instead the definition refuses: contained exactly as a filter that could not
    run -- failed, no items, the live collection untouched -- and the action
    string names the enrichment so an operator is not sent looking at the
    source.

    The second definition proves the containment is per DEFINITION: its own
    keys were all answered, so it builds normally in the same pass."""
    registry_entry(_Listing(
        "test_t2_gone", [("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt3")]
    ))
    registry_entry(_Listing("test_t2_fine", [("imdb", "tt1"), ("imdb", "tt2")]))
    kept = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Refused", [kept], labels=[LABEL])
    section = FakeSection(
        [("101", ["imdb://tt1"]), ("102", ["imdb://tt2"]), ("103", ["imdb://tt3"])],
        existing=[live],
        # ``103`` is deliberately absent: Plex no longer holds it, or held it
        # back. Absence from the answer is not an answer.
        metadata={
            "101": FakeMetadataItem("101", genres=("Horror",)),
            "102": FakeMetadataItem("102", genres=("Horror",)),
        },
    )

    run = await run_library(
        session, section, "Movies", "Movie",
        [
            CollectionDefinition(
                title="Refused", builder="test_t2_gone", filters={"genre": "Horror"}
            ),
            CollectionDefinition(
                title="Unaffected", builder="test_t2_fine", filters={"genre": "Horror"}
            ),
        ],
        _config(),
    )

    refused, unaffected = run.definitions
    assert refused.failed is True and refused.skipped is True
    assert [i.ratingKey for i in live._live] == ["m9"], (
        "a refused definition leaves its collection exactly as it was"
    )
    assert refused.actions == [
        "'Refused': could not read genre for every resolved item (the batched "
        "Plex metadata read failed or skipped items), so the filter was not "
        "evaluated and nothing was changed this pass",
        "'Refused': the filter could not be evaluated; leaving the collection "
        "untouched",
    ]

    assert unaffected.failed is False
    assert [i.ratingKey for i in section._existing["Unaffected"]._live] == ["101", "102"]
    assert section.fetches == [["101", "102", "103"]], (
        "the key Plex did not answer is memoised, not asked again this pass"
    )


async def test_a_refusal_reports_nothing_derived_from_the_plex_failure(
    session, registry_entry
):
    """The containment invariant's other half, kept for the new read too: a
    plexapi failure's message can quote the tokenised URL it failed on, so
    nothing derived from it reaches an action string."""
    registry_entry(_Listing("test_t2_dead_read", [("imdb", "tt1")]))

    class DeadSection(FakeSection):
        def fetchItems(self, ekey):
            self.fetches.append([str(key) for key in ekey])
            raise BadRequest("https://plex.example/library/metadata/101?X-Plex-Token=SECRET")

    section = DeadSection([("101", ["imdb://tt1"])])

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Dead Read", builder="test_t2_dead_read", filters={"genre": "Horror"}
        )],
        _config(),
    )

    assert not any("SECRET" in action or "X-Plex-Token" in action for action in actions)
    assert any("could not read genre" in action for action in actions)


async def test_a_tier1_only_filter_makes_no_batch_call(session, registry_entry):
    """The enrichment activates off the TABLE ROW, so a filter naming no
    batched attribute must not buy a Plex round trip. This is the regression
    that would otherwise cost one extra request per definition across every
    existing ``filters:`` block in the catalog."""
    registry_entry(_Listing("test_t2_none", [("imdb", "tt1"), ("imdb", "tt2")]))
    section = FakeSection(
        [("101", ["imdb://tt1"], {"year": 1985}), ("102", ["imdb://tt2"], {"year": 2005})],
        metadata={"101": FakeMetadataItem("101"), "102": FakeMetadataItem("102")},
    )

    await _run(
        session, section,
        [CollectionDefinition(
            title="Modern", builder="test_t2_none", filters={"year.gte": 1990}
        )],
        _config(),
    )

    assert section.fetches == []
    assert [i.ratingKey for i in section._existing["Modern"]._live] == ["102"]


async def test_a_definition_resolving_nothing_makes_no_batch_call(
    session, registry_entry
):
    """An empty resolved set has nothing to enrich, and asking Plex for zero
    keys is a request that can only fail or return nothing."""
    registry_entry(_Listing("test_t2_empty", [("imdb", "tt404")]))
    section = FakeSection([("101", ["imdb://tt1"])])

    await _run(
        session, section,
        [CollectionDefinition(
            title="Nothing", builder="test_t2_empty", filters={"genre": "Horror"}
        )],
        _config(),
    )

    assert section.fetches == []


# --- ids returned, none of them owned -------------------------------------------
#
# The third way a collection ends up empty, and the third wording. "Source
# returned no items" sends an operator looking for a dead provider; "this
# library owns none of them" sends them to look at the definition, which is
# where the answer is -- a list about the wrong medium, a chart of titles
# nobody has, a universe list of episodes.


async def test_ids_the_library_does_not_own_are_not_reported_as_no_items(
    session, registry_entry
):
    """The source returned three ids here. Saying it returned none is not a
    kinder way of putting it, it is a different diagnosis."""
    registry_entry(_Listing(
        "test_none_owned", [("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt3")]
    ))
    kept = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Owns None", [kept], labels=[LABEL])
    section = FakeSection([("m9", ["imdb://tt9"])], existing=[live])

    actions = await _run(
        session, section,
        [CollectionDefinition(title="Owns None", builder="test_none_owned")],
        _config(),
    )

    assert actions == [
        "'Owns None': the source returned 3 id(s), none of which this library "
        "owns; leaving the collection untouched"
    ]
    assert [i.ratingKey for i in live._live] == ["m9"], (
        "the wording changed; the containment did not"
    )


async def test_a_source_that_really_returned_nothing_still_says_so(
    session, registry_entry
):
    """The old wording is not replaced, it is narrowed to the case it is true
    of. Nothing came back here, so there is nothing this library could own."""
    registry_entry(_Listing("test_genuinely_empty", []))
    kept = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Really Empty", [kept], labels=[LABEL])
    section = FakeSection([("m9", ["imdb://tt9"])], existing=[live])

    actions = await _run(
        session, section,
        [CollectionDefinition(title="Really Empty", builder="test_genuinely_empty")],
        _config(),
    )

    assert actions == [
        "'Really Empty': source returned no items; leaving the collection untouched"
    ]


async def test_a_filter_that_emptied_the_set_still_outranks_the_new_wording(
    session, registry_entry
):
    """Two of the three can be true at once -- ids went unresolved AND the
    filter excluded the rest -- and the filter is the later cause, so it is the
    one reported. One id resolves here and the filter drops it; the other two
    are unowned."""
    registry_entry(_Listing(
        "test_unowned_and_filtered", [("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt3")]
    ))
    section = FakeSection([("m1", ["imdb://tt1"], {"year": 1994})])

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Both Causes", builder="test_unowned_and_filtered",
            filters={"year.gte": 2000},
        )],
        _config(),
    )

    assert actions == [
        "'Both Causes': the filter excluded every member; "
        "leaving the collection untouched"
    ]


async def test_the_preview_diff_is_taken_against_the_filtered_set(
    session, registry_entry
):
    """``adding``/``removing`` describe the members a pass would write, and a
    pass writes what the filter kept. A diff taken before the filter would
    promise to add an item the filter is about to drop."""
    registry_entry(_Listing(
        "test_filter_preview", [("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt3")]
    ))
    stale = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Previewed", [stale], labels=[LABEL])
    section = FakeSection([
        ("m1", ["imdb://tt1"], {"year": 1994}),
        ("m2", ["imdb://tt2"], {"year": 2005}),
        ("m3", ["imdb://tt3"], {"year": 2011}),
    ], existing=[live])

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(
            title="Previewed", builder="test_filter_preview", filters={"year.gte": 2000}
        )],
        _config(), dry_run=True, preview=True,
    )

    [result] = run.definitions
    assert (result.adding, result.removing) == (2, 1), (
        "m2 and m3 would be added and the stale member removed -- m1 is filtered "
        "out and is neither"
    )
    assert result.filtered == 1


async def test_the_preview_counts_respect_ownership_resolution(
    session, registry_entry
):
    """Roadmap row 142(b). The preview's ``adding``/``removing`` used to be
    diffed against the current Plex listing BEFORE the ownership rule ran, so
    a collision-blocked collection -- one that exists under our title without
    our label -- previewed as if the write would apply when the pass will
    refuse it. The counts must be zero, and the conflict must still be
    reported exactly once (by the reconcile step's own dry-run message, not
    doubled by the preview block)."""
    registry_entry(_Listing(
        "test_preview_conflict", [("imdb", "tt1"), ("imdb", "tt2")]
    ))
    # No LABEL: this collection is somebody else's, and adopt is off in
    # ``_config()``.
    foreign = FakeCollection("Contested", [FakeItem("m9", ["imdb://tt9"])])
    section = FakeSection(
        [("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])], existing=[foreign],
    )

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Contested", builder="test_preview_conflict")],
        _config(), dry_run=True, preview=True,
    )

    [result] = run.definitions
    assert (result.adding, result.removing) == (0, 0), (
        "a collision-blocked collection must not preview a write the pass "
        "will refuse"
    )
    conflicts = [a for a in result.actions if "conflict" in a]
    assert len(conflicts) == 1, (
        "the conflict is reported exactly once -- by the reconcile step, "
        "not doubled by the preview block: %r" % result.actions
    )


async def test_the_preview_counts_respect_the_shape_rule(
    session, registry_entry
):
    """Roadmap row 215, completing row 142(b)'s principle: the preview must
    not report counts for a write the pass will refuse. The ownership half
    already holds (the test above); this is the SHAPE half, and the case that
    slips through is precisely the OWNED one -- a smart collection carrying
    our own label under a list definition passes ``would_proceed`` (it is
    ours), previews real adding/removing counts, and then
    ``reconcile_list_collection`` refuses it at the shape gate, because
    ``addItems`` on a smart collection is answered by Plex with nothing
    useful and a success report. Counts must be zero; the conflict is
    reported exactly once, by the reconcile step's own message, not doubled
    by the preview block."""
    registry_entry(_Listing(
        "test_preview_shape", [("imdb", "tt1"), ("imdb", "tt2")]
    ))
    # OUR label: the ownership gate says yes -- the owned case is the one
    # nothing covered before this test.
    ours = FakeCollection(
        "Reshaped", [FakeItem("m9", ["imdb://tt9"])], labels=[LABEL],
    )
    ours.smart = True  # ``shape_conflict`` reads this; a real one carries it.
    section = FakeSection(
        [("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])], existing=[ours],
    )

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Reshaped", builder="test_preview_shape")],
        _config(), dry_run=True, preview=True,
    )

    [result] = run.definitions
    assert (result.adding, result.removing) == (0, 0), (
        "a shape-blocked collection must not preview a write the pass "
        "will refuse"
    )
    conflicts = [a for a in result.actions if "shape conflict" in a]
    assert len(conflicts) == 1, (
        "the shape conflict is reported exactly once -- by the reconcile "
        "step, not doubled by the preview block: %r" % result.actions
    )


async def test_the_preview_counts_an_adoption_the_pass_will_carry_out(
    session, registry_entry
):
    """Row 142(b). ``resolve_collision`` answers three ways, not
    two: ``ok`` is ``False`` both for a write the pass will REFUSE and for an
    eligible adoption held back only by ``dry_run`` -- a write the real pass
    WILL perform. Gating the preview on ``ok`` alone reported 0/0 for exactly the
    collection ``adopt`` exists to take over: a prior tool's, under our title,
    with adoption on. The counts must be the real ones the adopting pass would
    write, and the "would adopt" message must still arrive exactly once."""
    registry_entry(_Listing(
        "test_preview_adopt", [("imdb", "tt1"), ("imdb", "tt2")]
    ))
    # Somebody else's label, and ``adopt_from`` names it: the pass would claim
    # this collection and then reconcile its members.
    stale = FakeItem("m9", ["imdb://tt9"])
    prior = FakeCollection("Migrating", [stale], labels=["Kometa"])
    section = FakeSection(
        [("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])], existing=[prior],
    )

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Migrating", builder="test_preview_adopt")],
        _config(adopt=True), dry_run=True, preview=True,
    )

    [result] = run.definitions
    assert (result.adding, result.removing) == (2, 1), (
        "an adoption the pass will perform must preview its real deltas, not "
        "the zeros of a refusal"
    )
    adoptions = [a for a in result.actions if "would adopt" in a]
    assert len(adoptions) == 1, (
        "the adoption is reported exactly once -- by the reconcile step, "
        "not doubled by the preview block: %r" % result.actions
    )


async def test_the_preview_still_refuses_a_protected_collection_under_adopt(
    session, registry_entry
):
    """The other half of row 142(b): turning ``adopt`` on must not
    turn the gate off. A protected collection is a refusal ``resolve_collision`` reaches
    BEFORE adoption, so the preview owes it zeros even though the collection
    also carries an ``adopt_from`` label."""
    registry_entry(_Listing(
        "test_preview_protected", [("imdb", "tt1"), ("imdb", "tt2")]
    ))
    stale = FakeItem("m9", ["imdb://tt9"])
    guarded = FakeCollection("Guarded", [stale], labels=["Kometa", "Maintainerr"])
    section = FakeSection(
        [("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])], existing=[guarded],
    )

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Guarded", builder="test_preview_protected")],
        _config(adopt=True, protect_labels=["Maintainerr"]),
        dry_run=True, preview=True,
    )

    [result] = run.definitions
    assert (result.adding, result.removing) == (0, 0), (
        "a protected collection must not preview a write the pass will refuse, "
        "adopt on or off"
    )
    protections = [a for a in result.actions if "protected" in a]
    assert len(protections) == 1, (
        "the protection is reported exactly once: %r" % result.actions
    )


async def test_a_filter_that_cannot_evaluate_leaves_its_collection_alone(
    session, registry_entry
):
    """Containment, applied to the stage. Evaluation is total by construction
    -- the accessors are total, the model defines a result for a missing value,
    and a deferred attribute refuses at config load -- so this should be
    unreachable. It is guarded anyway, on the engine's rule that one
    definition's failure is never the pass's, and the containment has to be the
    dead-source one: keeping the unfiltered items would write the very members
    the operator asked to exclude."""
    registry_entry(_Listing("test_filter_broken", [("imdb", "tt1")]))
    registry_entry(_Listing("test_filter_healthy", [("imdb", "tt2")]))
    kept = FakeItem("m9", ["imdb://tt9"])
    live = FakeCollection("Broken Filter", [kept], labels=[LABEL])
    section = FakeSection([
        # A year the view hands back as a word: the predicate model raises
        # rather than treating a wrongly-typed value as missing.
        ("m1", ["imdb://tt1"], {"year": "nineteen ninety-four"}),
        ("m2", ["imdb://tt2"], {"year": 2005}),
    ], existing=[live])

    run = await run_library(
        session, section, "Movies", "Movie",
        [
            CollectionDefinition(
                title="Broken Filter", builder="test_filter_broken",
                filters={"year.gte": 2000},
            ),
            CollectionDefinition(title="Healthy", builder="test_filter_healthy"),
        ],
        _config(),
    )

    assert [i.ratingKey for i in live._live] == ["m9"], "nothing was applied to it"
    assert run.failures == ["Broken Filter"], (
        "and the pass does not report itself clean"
    )
    assert "Healthy" in section._existing, "the definition after it still ran"
    assert not any("nineteen" in action for action in run.actions), (
        "nothing derived from the exception reaches an action string"
    )


async def test_a_relative_date_filter_measures_every_item_against_one_date(
    session, registry_entry, monkeypatch
):
    """A relative window (``added: 30`` is "in the last 30 days") reads the
    clock once per definition, not once per item: a long pass would otherwise
    measure the first half of a collection against one instant and the rest
    against a later one, and produce a membership no single instant would.

    The clock read is ``datetime.now()`` with no timezone -- the run's MOMENT
    in the runner's local clock, which is what the date
    operators compare against (Kometa's ``current_time`` is the same call).
    ``_Clock`` subclasses ``datetime`` and intercepts only the no-argument
    form, so the engine's own ``datetime.now(UTC)`` pass timestamp is
    untouched and this test cannot pass by breaking that instead.
    """
    reads = []

    class _Clock(datetime):
        @staticmethod
        def now(tz=None):
            if tz is not None:
                return datetime.now(tz)
            reads.append(1)
            return datetime(2026, 8, 25, 14, 30)

    monkeypatch.setattr("autoposter.collections.engine.datetime", _Clock)
    registry_entry(_Listing(
        "test_filter_recent", [("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt3")]
    ))
    section = FakeSection([
        ("m1", ["imdb://tt1"], {"addedAt": datetime(2026, 8, 20)}),
        ("m2", ["imdb://tt2"], {"addedAt": datetime(2026, 8, 24)}),
        ("m3", ["imdb://tt3"], {"addedAt": datetime(2024, 1, 1)}),
    ])

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(
            title="Recently Added", builder="test_filter_recent", filters={"added": 30}
        )],
        _config(),
    )

    assert [
        i.ratingKey for i in section._existing["Recently Added"]._live
    ] == ["m1", "m2"]
    assert run.definitions[0].filtered == 1
    assert reads == [1], "one moment for the collection, not one per item"


def test_no_shipped_definition_carries_a_filter():
    """The golden gate's other half. ``filters:`` is an operator's tool: a
    default definition that gained one would change the membership of a live
    collection nobody asked to change, and ``golden_port.json`` would be a
    record of what this service used to do."""
    config = _config(charts=True, awards=True, separators=True)
    for library_type in ("Movie", "Show"):
        for definition in default_definitions(config, library_type):
            assert definition.filters is None, definition.title


async def test_an_expanded_family_member_files_under_its_placeholders_group(
    session, registry_entry
):
    """Row 210 / the !100_ misroute, reproduced at the engine's own seam.

    The expanding builder here does what facts_family does at
    facts_family.py:434-443: it returns a unit carrying the MEMBER's title and
    a DIFFERENT builder, so neither the preset index (keyed on the
    placeholder's title) nor the builder table can place it. Before the fix
    the unit fell through to the operator group and this asserted
    '!100_Ant-Man'; the expected prefix below is DERIVED from the
    placeholder's own index entry, so this test survives the franchises
    group's later renumbering without an edit.
    """

    class _Family:
        type_name = "test_family"

        async def build(self, ctx):
            raise AssertionError("an expanding builder is never dispatched to build")

        async def expand(self, ctx):
            return [CollectionDefinition(title="Ant-Man", builder="test_member")]

    registry_entry(_Family())
    registry_entry(_Listing("test_member", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"])])
    config = _config(presets=["content_franchises"])

    order = groups.effective_order(config)
    index = groups.preset_groups(config, "Movie")
    prefix = groups.sort_prefix(index["Franchises"], order)
    # The prefix the bug produced, DERIVED rather than spelled '!100_': the
    # operator group's number is its position in the effective order, so a
    # group inserted ahead of it renumbers this and a literal would go
    # vacuously true -- passing while looking like a regression guard.
    misrouted = groups.sort_prefix(groups.OPERATOR_GROUP, order)

    actions = await _run(
        session, section,
        [CollectionDefinition(title="Franchises", builder="test_family")],
        config,
    )

    assert f"set the sort title of 'Ant-Man' to '{prefix}Ant-Man'" in actions
    assert prefix != misrouted
    assert not any(f"{misrouted}Ant-Man" in action for action in actions)
