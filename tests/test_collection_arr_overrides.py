"""Roadmap row 89: per-definition Radarr/Sonarr overrides.

Two halves, and they fail in opposite directions:

- the RESTRICTION narrows a membership to what the one configured instance
  already holds. It is read-only, and when it cannot be evaluated it empties
  the desired set -- which ``lists.py`` reads as "make no changes" -- rather
  than writing the full, unrestricted collection the operator asked to narrow.
- the TAG WRITE-BACK is the client's first PUT, opt-in twice (the definition
  names tags AND ``collections.arr_tag_apply`` is on), additive, and contained:
  a dead Arr must not fail a collection that applied to Plex perfectly well.

Kometa's ``add_missing``/``radarr_add_all`` family -- telling an Arr to ACQUIRE
content -- is a declared non-goal (roadmap spec lines 1213-1215). Nothing here
POSTs a movie or a series, and nothing here DELETEs.
"""
import json
from types import SimpleNamespace

import httpx
import pytest

from autoposter.arr.client import RADARR, SONARR, ArrClient
from autoposter.collections.arr_overrides import restricted_members, tag_members
from autoposter.collections.builders import (
    BuilderContext,
    BuilderResult,
    SourceClients,
    register,
)
from autoposter.collections.engine import run_library
from autoposter.config.schema import CollectionDefinition

LABEL = "autoposter"

RADARR_MOVIES = [
    {"id": 1, "title": "Dune", "tmdbId": 438631, "tags": [3]},
    {"id": 2, "title": "Heat", "tmdbId": 949, "tags": []},
]
SONARR_SERIES = [{"id": 4, "title": "Severance", "tvdbId": 371980, "tags": []}]


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, key, guids=(), item_type="movie"):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]
        self.type = item_type


def _definition(**overrides) -> CollectionDefinition:
    return CollectionDefinition(
        title="Heat", builder="plex_id", params={"ids": ["1"]}, **overrides
    )


def _client(handler, kind=RADARR, base="https://radarr.example"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return http, ArrClient(http, base, "key", kind)


# --- (a) the restriction ---------------------------------------------------

async def test_the_restriction_keeps_only_what_the_instance_holds():
    async def handler(request):
        return httpx.Response(200, json=RADARR_MOVIES)

    http, client = _client(handler)
    items = [
        FakeItem("Dune", ["tmdb://438631"]),
        FakeItem("Unregistered", ["tmdb://11111"]),
    ]
    async with http:
        kept, actions = await restricted_members(
            _definition(radarr_restrict=True), items,
            library_type="Movie", radarr=client, sonarr=None, run_cache={},
        )

    assert [i.title for i in kept] == ["Dune"]
    assert any("Radarr" in a and "1" in a for a in actions)


async def test_the_restriction_costs_one_listing_per_pass():
    """The pass's ``run_cache``, the same memo ``builders/arr.py`` uses for the
    tag vocabulary: a config with a dozen restricted definitions is one GET."""
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=RADARR_MOVIES)

    http, client = _client(handler)
    run_cache: dict = {}
    items = [FakeItem("Dune", ["tmdb://438631"])]
    async with http:
        for _ in range(3):
            await restricted_members(
                _definition(radarr_restrict=True), items,
                library_type="Movie", radarr=client, sonarr=None, run_cache=run_cache,
            )

    assert len(calls) == 1


async def test_an_unconfigured_service_refuses_rather_than_not_restricting():
    """The containment law (``engine._passing``): a full, plausible, WRONG
    membership is worse than no change. Not restricting would write exactly the
    members the operator asked to exclude."""
    kept, actions = await restricted_members(
        _definition(radarr_restrict=True), [FakeItem("Dune", ["tmdb://438631"])],
        library_type="Movie", radarr=None, sonarr=None, run_cache={},
    )

    assert kept is None
    assert any("Radarr is not configured" in a for a in actions)


async def test_a_failed_listing_refuses_and_names_only_the_class():
    """An httpx error's message carries the request URL and the URL is where an
    api key would be if anyone ever put one there (facts C1.4)."""
    async def handler(request):
        return httpx.Response(500)

    http, client = _client(handler)
    async with http:
        kept, actions = await restricted_members(
            _definition(radarr_restrict=True), [FakeItem("Dune", ["tmdb://438631"])],
            library_type="Movie", radarr=client, sonarr=None, run_cache={},
        )

    assert kept is None
    assert any("HTTPStatusError" in a for a in actions)
    assert not any("radarr.example" in a for a in actions)


async def test_a_restriction_that_excludes_everything_says_so():
    async def handler(request):
        return httpx.Response(200, json=[])

    http, client = _client(handler)
    async with http:
        kept, actions = await restricted_members(
            _definition(radarr_restrict=True), [FakeItem("Dune", ["tmdb://438631"])],
            library_type="Movie", radarr=client, sonarr=None, run_cache={},
        )

    assert kept == []
    assert any("excluded every member" in a for a in actions)


async def test_the_wrong_service_for_the_library_is_refused():
    """Radarr's tmdbId only ever means a movie and Sonarr's tvdbId only ever a
    show -- ``require_library_type``'s rule, restated where the id is."""
    kept, actions = await restricted_members(
        _definition(radarr_restrict=True), [FakeItem("Severance", ["tvdb://371980"], "show")],
        library_type="Show", radarr=object(), sonarr=None, run_cache={},
    )

    assert kept is None
    assert any("radarr_restrict" in a and "Show" in a for a in actions)


async def test_no_restriction_asked_for_is_no_work_and_no_message():
    kept, actions = await restricted_members(
        _definition(), [FakeItem("Dune", ["tmdb://438631"])],
        library_type="Movie", radarr=None, sonarr=None, run_cache={},
    )

    assert [i.title for i in kept] == ["Dune"]
    assert actions == []


# --- (b) the tag write-back ------------------------------------------------

async def test_the_tag_write_is_off_until_the_deployment_says_otherwise():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=RADARR_MOVIES)

    http, client = _client(handler)
    async with http:
        actions = await tag_members(
            _definition(item_radarr_tag=["autoposter"]),
            [FakeItem("Dune", ["tmdb://438631"])],
            library_type="Movie", radarr=client, sonarr=None, run_cache={}, apply=False,
        )

    assert requests == [], "an off gate sends nothing at all"
    assert any("would tag" in a and "arr_tag_apply" in a for a in actions)


async def test_the_tag_write_creates_a_missing_tag_once_and_applies_it():
    seen = {"tag_posts": 0, "puts": []}

    async def handler(request):
        path = request.url.path
        if path == "/api/v3/movie" and request.method == "GET":
            return httpx.Response(200, json=RADARR_MOVIES)
        if path == "/api/v3/tag" and request.method == "GET":
            return httpx.Response(200, json=[{"id": 3, "label": "kids"}])
        if path == "/api/v3/tag" and request.method == "POST":
            seen["tag_posts"] += 1
            return httpx.Response(200, json={"id": 9, "label": "autoposter"})
        if path == "/api/v3/movie/editor" and request.method == "PUT":
            seen["puts"].append(json.loads(request.read()))
            return httpx.Response(200)
        raise AssertionError(f"unexpected {request.method} {path}")

    http, client = _client(handler)
    run_cache: dict = {}
    items = [FakeItem("Dune", ["tmdb://438631"]), FakeItem("Heat", ["tmdb://949"])]
    async with http:
        actions = await tag_members(
            _definition(item_radarr_tag=["autoposter", "kids"]), items,
            library_type="Movie", radarr=client, sonarr=None,
            run_cache=run_cache, apply=True,
        )

    assert seen["tag_posts"] == 1, "'kids' already exists; only 'autoposter' is created"
    assert seen["puts"] == [
        {"movieIds": [1, 2], "tags": [9, 3], "applyTags": "add"}
    ]
    assert any("tagged 2 member(s)" in a for a in actions)


async def test_members_the_instance_does_not_hold_are_skipped_and_counted():
    async def handler(request):
        path = request.url.path
        if path == "/api/v3/movie":
            return httpx.Response(200, json=RADARR_MOVIES)
        if path == "/api/v3/tag" and request.method == "GET":
            return httpx.Response(200, json=[{"id": 9, "label": "autoposter"}])
        if path == "/api/v3/movie/editor":
            return httpx.Response(200)
        raise AssertionError(path)

    http, client = _client(handler)
    async with http:
        actions = await tag_members(
            _definition(item_radarr_tag=["autoposter"]),
            [FakeItem("Dune", ["tmdb://438631"]), FakeItem("Nope", [])],
            library_type="Movie", radarr=client, sonarr=None, run_cache={}, apply=True,
        )

    assert any("1 member(s) Radarr does not hold" in a for a in actions)


async def test_a_failed_tag_write_is_contained_and_class_named():
    async def handler(request):
        path = request.url.path
        if path == "/api/v3/movie":
            return httpx.Response(200, json=RADARR_MOVIES)
        if path == "/api/v3/tag":
            return httpx.Response(200, json=[{"id": 9, "label": "autoposter"}])
        return httpx.Response(500)

    http, client = _client(handler)
    async with http:
        actions = await tag_members(
            _definition(item_radarr_tag=["autoposter"]),
            [FakeItem("Dune", ["tmdb://438631"])],
            library_type="Movie", radarr=client, sonarr=None, run_cache={}, apply=True,
        )

    assert any("HTTPStatusError" in a and "applied as usual" in a for a in actions)
    assert not any("radarr.example" in a for a in actions)


async def test_the_sonarr_half_writes_series_ids():
    seen = {}

    async def handler(request):
        path = request.url.path
        if path == "/api/v3/series":
            return httpx.Response(200, json=SONARR_SERIES)
        if path == "/api/v3/tag":
            return httpx.Response(200, json=[{"id": 9, "label": "autoposter"}])
        if path == "/api/v3/series/editor":
            seen["body"] = json.loads(request.read())
            return httpx.Response(200)
        raise AssertionError(path)

    http, client = _client(handler, SONARR, "https://sonarr.example")
    async with http:
        await tag_members(
            _definition(item_sonarr_tag=["autoposter"]),
            [FakeItem("Severance", ["tvdb://371980"], "show")],
            library_type="Show", radarr=None, sonarr=client, run_cache={}, apply=True,
        )

    assert seen["body"] == {"seriesIds": [4], "tags": [9], "applyTags": "add"}


# --- the schema ------------------------------------------------------------

def test_the_four_definition_fields_are_off_by_default():
    definition = _definition()
    assert definition.radarr_restrict is False
    assert definition.sonarr_restrict is False
    assert definition.item_radarr_tag == []
    assert definition.item_sonarr_tag == []


def test_an_arr_override_on_a_smart_builder_is_refused():
    with pytest.raises(Exception) as error:
        CollectionDefinition(title="Smart", builder="cs_bucket", radarr_restrict=True)
    assert "radarr_restrict" in str(error.value)


def test_an_arr_override_with_a_non_item_builder_level_is_refused():
    """An episode has no tmdbId and no tvdbId an Arr would know it by, so the
    combination could only ever exclude everything."""
    with pytest.raises(Exception) as error:
        CollectionDefinition(
            title="Eps", builder="plex_id", params={"ids": ["1"]},
            builder_level="episode", radarr_restrict=True,
        )
    assert "builder_level" in str(error.value)


# --- the entry-point law ---------------------------------------------------

class _Listing:
    def __init__(self, type_name, ids):
        self.type_name = type_name
        self._ids = ids

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        return BuilderResult(ids=list(self._ids))


class FakeCollection:
    def __init__(self, title, items=()):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._labels = []
        self.summary = None
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

    def items(self):
        return list(self._cache)

    def addItems(self, items):
        self._live.extend(items)

    def removeItems(self, items):
        pass

    def moveItem(self, item, after=None):
        pass

    def sortUpdate(self, sort=None):
        pass

    def editSortTitle(self, sortTitle, locked=True):
        pass

    def query(self, key, method=None, **kwargs):
        self.summary = key

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    def __init__(self, items=()):
        self._items = list(items)
        self._existing: dict = {}
        self.created: dict[str, list] = {}

    def all(self):
        return list(self._items)

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        self.created[title] = list(items or [])
        collection = FakeCollection(title, items or [])
        self._existing[title] = collection
        return collection


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": True, "awards": True,
        "separators": False, "definitions": [], "presets": [],
        "mdblist_sync_apply": False, "arr_tag_apply": False,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


@pytest.fixture
def registry_entry():
    from autoposter.collections.builders import REGISTRY

    registered: list[str] = []

    def add(builder):
        register(builder)
        registered.append(builder.type_name)
        return builder

    yield add
    for name in registered:
        REGISTRY.pop(name, None)


async def test_the_gate_off_pass_makes_no_arr_request_at_all(session, registry_entry):
    """Gate-off byte-identity, transport-pinned: a definition that names
    neither field must not so much as list the instance."""
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=RADARR_MOVIES)

    registry_entry(_Listing("test_arr_off", [("tmdb", "438631")]))
    section = FakeSection([FakeItem("Dune", ["tmdb://438631"])])
    http, client = _client(handler)

    async with http:
        await run_library(
            session, section, "Movies", "Movie",
            [CollectionDefinition(title="Off", builder="test_arr_off")],
            _config(arr_tag_apply=True), sources=SourceClients(radarr=client),
        )

    assert requests == []
    assert [i.title for i in section.created["Off"]] == ["Dune"]


async def test_restriction_and_tagging_through_the_real_run_library(
    session, registry_entry
):
    """The entry-point law for row 89: both halves of one definition, through
    the real engine and the real ``ArrClient`` over a MockTransport, so what is
    pinned is the actual membership and the actual bytes on the wire."""
    seen = {"puts": []}

    async def handler(request):
        path = request.url.path
        if path == "/api/v3/movie" and request.method == "GET":
            return httpx.Response(200, json=RADARR_MOVIES)
        if path == "/api/v3/tag" and request.method == "GET":
            return httpx.Response(200, json=[{"id": 9, "label": "autoposter"}])
        if path == "/api/v3/movie/editor" and request.method == "PUT":
            seen["puts"].append(json.loads(request.read()))
            return httpx.Response(200)
        raise AssertionError(f"unexpected {request.method} {path}")

    registry_entry(_Listing("test_arr_both", [("tmdb", "438631"), ("tmdb", "77777")]))
    section = FakeSection([
        FakeItem("Dune", ["tmdb://438631"]),
        FakeItem("Unregistered", ["tmdb://77777"]),
    ])
    http, client = _client(handler)

    async with http:
        run = await run_library(
            session, section, "Movies", "Movie",
            [CollectionDefinition(
                title="Managed", builder="test_arr_both",
                radarr_restrict=True, item_radarr_tag=["autoposter"],
            )],
            _config(arr_tag_apply=True), sources=SourceClients(radarr=client),
        )

    assert [i.title for i in section.created["Managed"]] == ["Dune"], (
        "the unregistered member is what the restriction exists to drop"
    )
    assert seen["puts"] == [{"movieIds": [1], "tags": [9], "applyTags": "add"}]
    assert run.definitions[0].failed is False


async def test_an_unevaluable_restriction_leaves_the_collection_untouched(
    session, registry_entry
):
    """The containment law through the real entry point: a dead Radarr must not
    produce the unrestricted collection."""
    async def handler(request):
        return httpx.Response(500)

    registry_entry(_Listing("test_arr_dead", [("tmdb", "438631")]))
    section = FakeSection([FakeItem("Dune", ["tmdb://438631"])])
    http, client = _client(handler)

    async with http:
        run = await run_library(
            session, section, "Movies", "Movie",
            [CollectionDefinition(
                title="Dead", builder="test_arr_dead", radarr_restrict=True
            )],
            _config(), sources=SourceClients(radarr=client),
        )

    assert section.created == {}
    assert run.definitions[0].failed is True
    assert not any("source returned no items" in a for a in run.actions)
