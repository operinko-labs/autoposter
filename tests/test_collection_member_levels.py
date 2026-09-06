"""Roadmap row 143: resolving a definition's members at episode level.

The contract row 143 asks for is a *builder* one: a builder says what its ids
NAME (``BuilderResult.level``) and the engine resolves them against an index of
that level, built at most once per level per pass. Row 88's operator-facing
``builder_level`` sits on top of this and is the next task's.

Everything here goes through the REAL ``run_library`` -- the entry-point law. A
helper-level test would not catch the two failures that actually matter -- an
episode index built on every pass (a second full traversal of a large library,
for nothing) and an episode-level membership resolved against the item index
(zero members, reported as "this library owns none of them").
"""
from types import SimpleNamespace

import httpx
import pytest

from autoposter.arr.client import RADARR, ArrClient
from autoposter.collections.builders import (
    BuilderContext,
    BuilderResult,
    SourceClients,
    register,
)
from autoposter.collections.engine import run_library
from autoposter.config.schema import CollectionDefinition

LABEL = "autoposter"


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, key, guids=(), item_type="show"):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]
        # plexapi's own item type, which is what ``Collection._create`` reads to
        # decide the collection's Plex ``type`` (pinned in
        # tests/test_plexapi_collection_contract.py).
        self.type = item_type


class FakeCollection:
    def __init__(self, title, items=()):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._labels = []
        self.summary = None
        self.sort_set = None
        self.titleSort = None
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
        removed = {i.ratingKey for i in items}
        self._live = [i for i in self._live if i.ratingKey not in removed]

    def moveItem(self, item, after=None):
        pass

    def sortUpdate(self, sort=None):
        self.sort_set = sort

    def editSortTitle(self, sortTitle, locked=True):
        self.titleSort = sortTitle

    def query(self, key, method=None, **kwargs):
        self.summary = key

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    """A Show library that answers ``all()`` and ``search(libtype=...)``."""

    key = 1

    def __init__(self, shows=(), episodes=(), seasons=(), existing=()):
        self._shows = list(shows)
        self._by_libtype = {"episode": list(episodes), "season": list(seasons)}
        self._existing = {c.title: c for c in existing}
        self.all_calls = 0
        self.searches: list[str] = []
        self.created: dict[str, list] = {}
        # Search-tail E-2: what a ``plex_search`` at episode level asks for.
        self.fetch_calls: list[str] = []

    def fetchItems(self, key):
        self.fetch_calls.append(key)
        return list(self._by_libtype["episode"])

    def all(self):
        self.all_calls += 1
        return list(self._shows)

    def search(self, libtype=None, **kwargs):
        self.searches.append(libtype)
        return list(self._by_libtype.get(libtype, []))

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
        "mdblist_sync_apply": False,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


@pytest.fixture
def registry_entry():
    """Register a builder for one test and take it back out again."""
    from autoposter.collections.builders import REGISTRY

    registered: list[str] = []

    def add(builder):
        register(builder)
        registered.append(builder.type_name)
        return builder

    yield add
    for name in registered:
        REGISTRY.pop(name, None)


class _LevelledBuilder:
    """A builder that declares what level its ids name."""

    def __init__(self, type_name, ids, level="item"):
        self.type_name = type_name
        self._ids = ids
        self._level = level
        self.builds = 0

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        self.builds += 1
        return BuilderResult(ids=list(self._ids), level=self._level)


def _section():
    return FakeSection(
        shows=[FakeItem("Severance", ["tvdb://371980"], "show")],
        episodes=[
            FakeItem("S01E01", ["tvdb://7645236"], "episode"),
            FakeItem("S01E02", ["tvdb://7645237"], "episode"),
        ],
        seasons=[FakeItem("Season 1", [], "season")],
    )


def test_builder_result_is_item_level_unless_it_says_otherwise():
    """Every builder that predates row 143 keeps meaning exactly what it meant."""
    assert BuilderResult(ids=[]).level == "item"


async def test_an_episode_level_builder_puts_episodes_in_the_collection(
    session, registry_entry
):
    """The entry-point law. The pass reaches ``createCollection`` with the
    EPISODE objects -- not with nothing, which is what resolving episode ids
    against the item index produced before row 143."""
    registry_entry(_LevelledBuilder(
        "test_level_episodes", [("tvdb", "7645236"), ("tvdb", "7645237")], "episode"
    ))
    section = _section()

    run = await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(title="Pilots", builder="test_level_episodes")],
        _config(), sources=SourceClients(),
    )

    assert [i.title for i in section.created["Pilots"]] == ["S01E01", "S01E02"]
    assert run.definitions[0].unresolved == 0


async def test_an_item_level_pass_never_searches(session, registry_entry):
    """Gate-off byte-identity: the second traversal is paid for only by a
    definition that asked for it."""
    registry_entry(_LevelledBuilder("test_level_items", [("tvdb", "371980")]))
    section = _section()

    await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(title="Shows", builder="test_level_items")],
        _config(), sources=SourceClients(),
    )

    assert section.searches == []
    assert section.all_calls == 1


async def test_each_level_is_indexed_at_most_once_per_pass(session, registry_entry):
    """Two episode definitions in one pass cost ONE episode traversal, the same
    budget rule the item index has always kept."""
    registry_entry(_LevelledBuilder("test_level_ep_a", [("tvdb", "7645236")], "episode"))
    registry_entry(_LevelledBuilder("test_level_ep_b", [("tvdb", "7645237")], "episode"))
    registry_entry(_LevelledBuilder("test_level_show", [("tvdb", "371980")]))
    section = _section()

    await run_library(
        session, section, "TV Shows", "Show",
        [
            CollectionDefinition(title="A", builder="test_level_ep_a"),
            CollectionDefinition(title="B", builder="test_level_ep_b"),
            CollectionDefinition(title="C", builder="test_level_show"),
        ],
        _config(), sources=SourceClients(),
    )

    assert section.searches == ["episode"]
    assert section.all_calls == 1


async def test_an_episode_id_resolved_at_item_level_is_reported_not_guessed(
    session, registry_entry
):
    """The failure row 143 removes, kept as a test: a builder that does NOT
    declare episode level has its episode ids counted as unowned rather than
    matched to something else."""
    registry_entry(_LevelledBuilder("test_level_wrong", [("tvdb", "7645236")]))
    section = _section()

    run = await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(title="Wrong", builder="test_level_wrong")],
        _config(), sources=SourceClients(),
    )

    assert run.definitions[0].unresolved == 1
    assert section.created == {}


async def test_a_builder_reads_the_engines_episode_index_through_the_bundle(
    session, registry_entry
):
    """``plex_pilots``' premise (row 56): a builder whose SOURCE is the library
    walks episodes through the accessor and hands back plex ids, and the walk it
    reads is the one the engine was going to build anyway."""
    seen = {}

    class _Walker:
        type_name = "test_level_walker"

        async def build(self, ctx: BuilderContext) -> BuilderResult:
            index = ctx.sources.plex.owned_index("episode")
            seen["keys"] = sorted(index["plex"])
            return BuilderResult(
                ids=[("plex", key) for key in sorted(index["plex"])], level="episode"
            )

    registry_entry(_Walker())
    section = _section()

    await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(title="Walked", builder="test_level_walker")],
        _config(), sources=SourceClients(),
    )

    assert seen["keys"] == ["S01E01", "S01E02"]
    assert [i.title for i in section.created["Walked"]] == ["S01E01", "S01E02"]
    assert section.searches == ["episode"], "the accessor must reuse the pass's index"


# --- roadmap row 88: the operator-facing level ------------------------------
#
# Row 143 made the ENGINE able to resolve at episode level when a builder says
# its ids name episodes. Row 88 is the other direction: an operator declaring
# it on the definition, for the builders that produce plain ids and cannot
# know (``plex_id``, a text file, an MDBList list of episode ids).


def test_builder_level_defaults_to_item():
    """Every definition in every existing config keeps meaning what it meant."""
    definition = CollectionDefinition(
        title="Anything", builder="plex_id", params={"ids": ["1"]}
    )
    assert definition.builder_level == "item"


async def test_a_definition_can_declare_episode_level_for_a_plain_builder(
    session, registry_entry
):
    """The entry-point law for row 88: a ``builder_level`` definition through
    the real ``run_library``, resolving against the real episode index."""
    registry_entry(_LevelledBuilder("test_bl_plain", [("tvdb", "7645236")]))
    section = _section()

    await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(
            title="Pilots", builder="test_bl_plain", builder_level="episode"
        )],
        _config(), sources=SourceClients(),
    )

    assert [i.title for i in section.created["Pilots"]] == ["S01E01"]
    assert section.searches == ["episode"]


async def test_an_item_level_definition_is_byte_identical_to_before(
    session, registry_entry
):
    """Gate-off: the default value must not buy a traversal, a refusal or a
    single new action string."""
    registry_entry(_LevelledBuilder("test_bl_off", [("tvdb", "371980")]))
    section = _section()

    run = await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(title="Shows", builder="test_bl_off")],
        _config(), sources=SourceClients(),
    )

    assert section.searches == []
    assert [i.title for i in section.created["Shows"]] == ["Severance"]
    assert run.definitions[0].failed is False
    assert not [a for a in run.actions if "builder_level" in a]


async def test_a_definition_and_a_builder_that_disagree_are_refused(
    session, registry_entry
):
    """Two non-default answers to the same question. Refusing is the only safe
    reading: silently preferring either one resolves against an index the other
    half never meant, which is an empty collection nobody asked for."""
    registry_entry(_LevelledBuilder("test_bl_clash", [("tvdb", "7645236")], "episode"))
    section = _section()

    run = await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(
            title="Clash", builder="test_bl_clash", builder_level="season"
        )],
        _config(), sources=SourceClients(),
    )

    assert section.created == {}
    assert run.definitions[0].failed is True
    [action] = [a for a in run.actions if "Clash" in a]
    assert "builder_level" in action and "season" in action and "episode" in action


async def test_a_non_item_level_on_a_movie_library_is_refused(
    session, registry_entry
):
    """A Movie library has no seasons and no episodes, so the search would
    return nothing -- and "returned nothing" is indistinguishable from a
    correct empty collection. Same refusal shape as ``require_library_type``."""
    registry_entry(_LevelledBuilder("test_bl_movie", [("tmdb", "278")]))
    section = FakeSection(shows=[FakeItem("Shawshank", ["tmdb://278"], "movie")])

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(
            title="Nope", builder="test_bl_movie", builder_level="episode"
        )],
        _config(), sources=SourceClients(),
    )

    assert section.created == {}
    assert run.definitions[0].failed is True
    [action] = [a for a in run.actions if "Nope" in a]
    assert "libraries:" in action and "Movie" in action


def test_builder_level_is_refused_on_a_smart_builder_that_cannot_read_it():
    """Search-tail E-2 lifted this for ``smart_filter``, which now types its
    stored filter by the level. The other four derive their own query and
    would load clean and apply nothing."""
    with pytest.raises(Exception) as error:
        CollectionDefinition(
            title="Smart", builder="cs_bucket", builder_level="episode"
        )
    message = str(error.value)
    assert "cs_bucket" in message
    assert "derives its own Plex search" in message


def test_builder_level_is_accepted_on_smart_filter():
    """The lift, from the other side. A ``smart_filter`` definition with a
    level loads, because the level reaches Plex as the search's ``type=``."""
    definition = CollectionDefinition(
        title="Pilots", builder="smart_filter",
        params={"all": {"episode_title.begins": "Pilot"}},
        builder_level="episode",
    )
    assert definition.builder_level == "episode"


def test_builder_level_is_refused_with_sync_to_mdb_list():
    """MDBList lists hold movies/shows. A season- or episode-level definition
    that also pushes to MDBList would push season/episode ids through
    ``sync_membership`` -> ``push_payload(is_movie=...)``, which reports them
    as show-level ids -- wrong data on a list the operator does not own, and
    nothing short of reading ``mdblist_sync.py`` would tell you why."""
    with pytest.raises(Exception) as error:
        CollectionDefinition(
            title="Nope", builder="plex_id", params={"ids": ["1"]},
            builder_level="episode", sync_to_mdb_list="someuser/some-list",
        )
    assert "sync_to_mdb_list" in str(error.value)


async def test_a_definition_and_a_builder_that_agree_are_not_refused(
    session, registry_entry
):
    """The agree-and-both-set cell: a definition's ``builder_level`` and its
    builder's ``result.level`` naming the SAME non-default level is not a
    disagreement, so the collection builds rather than being refused."""
    registry_entry(_LevelledBuilder("test_bl_agree", [("tvdb", "7645236")], "episode"))
    section = _section()

    run = await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(
            title="Agree", builder="test_bl_agree", builder_level="episode"
        )],
        _config(), sources=SourceClients(),
    )

    assert [i.title for i in section.created["Agree"]] == ["S01E01"]
    assert run.definitions[0].failed is False


# --- I1: the effective level, not just the declared one ---------------------
#
# The three `builder_level`-keyed schema validators run at config load and can
# only ever see `definition.builder_level`. A builder that self-declares a
# non-item `result.level` while `builder_level` stays at its "item" default
# satisfies every one of them -- so without a guard on the EFFECTIVE level,
# an episode-level builder reaches the Arr tag write with episode members.


async def test_an_effective_episode_level_with_an_arr_tag_is_refused_not_sent(
    session, registry_entry
):
    """``builder_level`` is undeclared (item, the default); the BUILDER says
    episode. That combination passes every schema validator, so the refusal
    must come from the engine, after it computes the effective level -- and it
    must fire before any Arr request, not merely before Plex is written to."""
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    registry_entry(_LevelledBuilder(
        "test_bl_effective_episode", [("tvdb", "7645236")], "episode"
    ))
    section = _section()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ArrClient(http, "https://radarr.example", "key", RADARR)

    async with http:
        run = await run_library(
            session, section, "TV Shows", "Show",
            [CollectionDefinition(
                title="Pilots", builder="test_bl_effective_episode",
                item_radarr_tag=["autoposter"],
            )],
            _config(arr_tag_apply=True), sources=SourceClients(radarr=client),
        )

    assert requests == [], "no listing, no tag lookup, no PUT -- nothing sent"
    assert section.created == {}, "nothing written"
    assert run.definitions[0].failed is True
    [action] = [a for a in run.actions if "Pilots" in a]
    assert "episode" in action


# --- search-tail E-2: the real plex_search builder, through the real entry point


async def test_a_plex_search_definition_at_episode_level_collects_episodes(session):
    """Search-tail E-2 through the REAL ``run_library`` and the REAL
    ``plex_search`` builder -- the entry-point law. The chain this proves end
    to end is the one no helper test covers: ``definition.builder_level`` ->
    ``build_search_url``'s ``search_type`` -> a ``type=4`` query ->
    ``BuilderResult(level='episode')`` -> ``owned_index('episode')`` ->
    ``resolve_external`` -> a collection whose members are episodes."""
    section = _section()

    run = await run_library(
        session, section, "TV Shows", "Show",
        [CollectionDefinition(
            title="Pilots", builder="plex_search",
            params={"all": {"episode_title.begins": "Pilot"}},
            builder_level="episode",
        )],
        _config(), sources=SourceClients(),
    )

    assert section.fetch_calls == [
        "/library/sections/1/all?type=4&sort=titleSort&episode.title%3C=Pilot"
    ]
    assert [i.title for i in section.created["Pilots"]] == ["S01E01", "S01E02"]
    assert section.searches == ["episode"]
    assert run.definitions[0].unresolved == 0
