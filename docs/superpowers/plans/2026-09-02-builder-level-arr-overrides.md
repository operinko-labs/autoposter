# Builder-Level & Arr Overrides (rows 143, 88, 89) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** close roadmap rows 143 (episode-aware resolution), 88 (`builder_level` collections, PHASE-1 LIST SLICE) and 89 (per-definition Arr overrides — the read-only restriction plus the tag write-back) — Phase 11's finale.

**Architecture:** three disjoint pieces, sequenced. (1) Row 143 makes the owned index *level-aware*: `build_owned_index(section, level)` walks `section.all()` for `"item"` (byte-identical to today) and `section.search(libtype=...)` for `"season"`/`"episode"`, and `BuilderResult` gains a `level` field so a builder can declare that its ids name episodes. The engine caches one index per level per pass. (2) Row 88 adds the operator-facing half, `CollectionDefinition.builder_level`, threads it into `_run_one`'s resolution and gives `create_blank_collection` its `type=3/4` branch; membership diffing, ordering and creation already work at any granularity (plexapi derives a collection's Plex `type` from `items[0].type`, pinned by a contract test in T1). (3) Row 89 is Arr-side and touches none of the above: two boolean restriction fields filter a resolved membership down to what the one configured Radarr/Sonarr instance holds, and two tag fields write labels back through `PUT /api/v3/{movie|series}/editor` — the client's first PUT, whose body shape is BANKED from the services' own OpenAPI specs before a line of it is written.

**Tech Stack:** Python 3.13, httpx (async, `MockTransport` in tests), plexapi 4.18.2, pytest + pytest-asyncio, SQLAlchemy async, Pydantic v2.

## Global Constraints

Every task's requirements implicitly include this section.

1. **Branch.** `feat/builder-level`, cut from post-#126 `origin/main` (`a4ad5cb`, the parental-labels merge). Do not rebase mid-phase.
2. **Sequencing is binding.** T1 (row 143) → T2 (row 88's slice) → T3 (row 89) → T4 (wrap). T2 consumes T1's contract; T3 is a different subsystem and shares only `CollectionDefinition`'s file.
3. **The 173/179 fence.** Smart / `plex_search` sub-libtypes are OUT. Nothing in this phase touches `collections/smart.py`, `collections/search_url.py`, `collections/search_sorts.py` or `PlexSearchParams`. A `builder_level` on a smart definition is REFUSED at config load with a message naming rows 173/179.
4. **Kometa's `add_missing` family is a declared non-goal** (roadmap `2026-08-22-full-parity-roadmap.md:1213-1215`). Row 89 is read-only *filtering* against what the Arr already holds, plus a tag write. Nothing in this phase ever asks Radarr/Sonarr to acquire content, and no code path in it issues `POST /api/v3/{movie,series}` or any `DELETE`.
5. **Single-instance Arr.** `Secrets.radarr`/`Secrets.sonarr` are singular (`config/schema.py:2382`/`2386`). No multi-instance selection is added; the restriction fields are booleans, not instance names.
6. **No guessed wire shapes.** T3 Step 1 BANKS the Radarr and Sonarr OpenAPI sections into `.superpowers/sdd/p-arr-api-capture.md`, verbatim and cited by path, BEFORE any PUT code is written. If a banked shape disagrees with what this plan quotes, STOP and report — do not adapt the code silently.
7. **The gated-feature entry-point law.** Every gated feature here is proven through the REAL entry point (`collections.engine.run_library`) with the real transport double, not through a helper alone: T2 with a `builder_level` definition, T3 with `radarr_restrict`/`item_radarr_tag` definitions. Gate-off is byte-identical to pre-phase behaviour — for T1/T2 that means `section.search` is never called for an item-level pass; for T3 it means ZERO requests reach the Arr transport.
8. **Apply-flag posture.** The tag write-back is opt-in twice, the `sync_to_mdb_list` precedent (row 31): the definition must name tags AND the deployment must set `collections.arr_tag_apply` (default `False`). Off reports what it would write and sends nothing. `dry_run` and `preview` also suppress it.
9. **Transport-pinned negatives.** Assertions about "no request was made" are made against an `httpx.MockTransport` handler that records every request, and assertions about a write pin the METHOD and the PATH, not just the body — `PUT /api/v3/movie/editor` and `DELETE /api/v3/movie/editor` take the SAME body, and the second deletes movies.
10. **`MockTransport` only.** No test in this plan opens a socket. `tests/conftest.py`'s `no_outbound_network` autouse fixture is not to be bypassed.
11. **Class-name-only on served surfaces.** An Arr failure reaching a `DefinitionResult.actions` string carries `type(exc).__name__` and a fixed sentence, never `str(exc)` — an httpx error's message carries the request URL. Full detail with traceback goes to the log via `logger.exception`.
12. **Every refusal names its reason and its setting.** An action string saying only "skipped" is half a message.
13. **Membership that cannot be evaluated leaves the collection untouched.** The `_passing`/`engine._run_one` containment law: a full, plausible, WRONG membership is worse than no change. An Arr restriction that cannot run empties the desired set (which `lists.py` reads as "make no changes") and reports `failed`, exactly as a filter that cannot run does.
14. **Config-field descriptions say WHAT, never WHEN.** `tests/test_config_descriptions.py` enforces a non-empty `Field(description=...)` on every field, with no "after the next run"-shaped text. No `dict[str, SomeModel]` field is added.
15. **Suites stated-then-measured.** T1 Step 1 MEASURES the post-#126 baseline. **4503/383 is STALE** — it was measured on `feat/parental-labels` before #126 merged (`.superpowers/sdd/progress.md:3827`). Every later task states its expected count BEFORE running and reconciles any difference in its report.
16. **Container discipline.** Unique compose project per task (**`pbl1` … `pbl4`**), always with the `.superpowers/isolated-db.yml` overlay. Tee output to a path under `/app/.superpowers/` inside the container — never rely on streamed stdout (`rtk proxy` filters streamed logs and `--rm` deletes the container). A long run uses **no `--rm`**, is started detached, waited on with a foreground `docker wait`, and read back with `docker cp`. Teardown is `docker compose -p <project> down` — **never** `down -v`.
17. **Never assert wall-clock ordering across sleeps in a container test.** The dev machine's Docker clock steps ~2.7s backwards every ~27s.
18. **Commits** are conventional, `--no-gpg-sign`, staged **by name** (never `git add -A`), and carry **no AI attribution** of any kind — no `Co-Authored-By`, no tool name, nothing. Same for the PR body.
19. **Read-only outside the named files.** At the end of every task, `git diff --stat <task-start-sha> -- src/ tests/` must list only files named in that task's **Files** block.
20. **RED before GREEN on every behavioural step:** write the failing test, run it, watch it fail for the RIGHT reason, then implement.

---

## File Structure

| File | Change | Row | Task |
|---|---|---|---|
| `src/autoposter/collections/ids.py` | `MemberLevel`, `MEMBER_LEVELS`, `LIBTYPE_FOR_LEVEL` | 143 | T1 |
| `src/autoposter/collections/resolve.py` | `build_owned_index(section, level=...)` | 143 | T1 |
| `src/autoposter/collections/builders/base.py` | `BuilderResult.level`; `PlexSectionAccess` re-export of `MemberLevel` | 143 | T1 |
| `src/autoposter/collections/builders/sources_bundle.py` | `PlexSectionAccess.owned_index(level=...)` | 143 | T1 |
| `src/autoposter/collections/engine.py` | per-level index cache; `_run_one` resolves at the effective level; T2's refusals; T3's two calls | 143, 88, 89 | T1, T2, T3 |
| `src/autoposter/config/schema.py` | `CollectionDefinition.builder_level`; `radarr_restrict`/`sonarr_restrict`/`item_radarr_tag`/`item_sonarr_tag`; `CollectionsConfig.arr_tag_apply`; two validators | 88, 89 | T2, T3 |
| `src/autoposter/collections/reconcile.py` | `COLLECTION_TYPES`; `create_blank_collection`'s `type` lookup | 88 | T2 |
| `src/autoposter/arr/client.py` | `create_tag`, `apply_tags`, `entry_ids_by_external_id` — the client's first PUT | 89 | T3 |
| `src/autoposter/arr/sync.py` | `_GUID_KEY`→`GUID_KEY`, `_external_id`→`external_id` (a second consumer) | 89 | T3 |
| `src/autoposter/collections/arr_overrides.py` | **new** — `restricted_members`, `tag_members` | 89 | T3 |
| `tests/test_collection_resolve.py` | level-aware index tests | 143 | T1 |
| `tests/test_plexapi_collection_contract.py` | pin `Collection._create`'s `type`-from-`items[0].type` derivation | 143 | T1 |
| `tests/test_collection_member_levels.py` | **new** — T1's engine/entry-point tests, then T2's | 143, 88 | T1, T2 |
| `tests/test_collection_reconcile.py` | `create_blank_collection`'s type branch | 88 | T2 |
| `tests/test_arr_client.py` | the write methods, transport-pinned | 89 | T3 |
| `tests/test_collection_arr_overrides.py` | **new** — T3's unit + entry-point tests | 89 | T3 |
| `.superpowers/sdd/p-arr-api-capture.md` | **new** — the banked swagger sections | 89 | T3 |
| `config/autoposter.example.yaml` | document the five new fields | 88, 89 | T2, T3 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | close 143/88/89; correct the reversed `Depends on` cells | — | T4 |
| `.superpowers/sdd/progress.md` | the phase's ship entry | — | T4 |
| `.superpowers/sdd/p-row8889-pr-body.md` | **new** — the PR body with the operator checkboxes | — | T4 |

---

## The row-143 → row-88 interface contract

T1 produces exactly these names. T2 consumes them and adds nothing to them.

```python
# autoposter/collections/ids.py
MemberLevel = Literal["item", "season", "episode"]
MEMBER_LEVELS: frozenset[str]                       # {"item", "season", "episode"}
LIBTYPE_FOR_LEVEL: dict[str, str | None]            # {"item": None, "season": "season", "episode": "episode"}

# autoposter/collections/resolve.py
def build_owned_index(section, level: MemberLevel = "item") -> OwnedIndex: ...

# autoposter/collections/builders/base.py
@dataclass(frozen=True)
class BuilderResult:
    ids: list[ExternalId]
    summary: str | None = None
    poster_kind: str | None = None
    poster_key: str | None = None
    level: MemberLevel = "item"      # T1 adds this, last, so positional construction is unchanged

# autoposter/collections/builders/sources_bundle.py
class PlexSectionAccess:
    def owned_index(self, level: str = "item") -> dict: ...

# autoposter/collections/engine.py (inside run_library)
def owned_index(level: str = "item") -> OwnedIndex: ...   # one index per level, built at most once per pass
```

---

# Task 1: Row 143 — episode-aware resolution

**Files:**
- Modify: `src/autoposter/collections/ids.py`
- Modify: `src/autoposter/collections/resolve.py:57-84`
- Modify: `src/autoposter/collections/builders/base.py:88-110` (`BuilderResult`), `:51-70` (`__all__`)
- Modify: `src/autoposter/collections/builders/sources_bundle.py:49-77` (`PlexSectionAccess`)
- Modify: `src/autoposter/collections/engine.py:354-377` (the index closure), `:676` (`_run_one`'s resolve), `:861-867` (the mdblist call's index)
- Test: `tests/test_collection_resolve.py` (modify)
- Test: `tests/test_collection_member_levels.py` (create)
- Test: `tests/test_plexapi_collection_contract.py` (modify)

**Interfaces:**
- Consumes: `autoposter.collections.ids.NAMESPACES`, `ExternalId`; `autoposter.collections.resolve.resolve_external(index, ids)` (UNCHANGED — it is namespace-generic and needs no level); `autoposter.collections.engine.run_library(...)`.
- Produces: the whole "row-143 → row-88 interface contract" block above.

- [ ] **Step 1: Cut the branch and MEASURE the baseline**

```bash
git fetch origin
git checkout -b feat/builder-level a4ad5cb
git rev-parse HEAD
```

Expected: `a4ad5cb...` (the #126 merge). Then measure — do not assume the number:

```bash
docker compose -p pbl1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pbl1-base test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t1-baseline.log'
docker wait pbl1-base
docker cp pbl1-base:/app/.superpowers/run-t1-baseline.log .superpowers/run-t1-baseline.log
docker rm pbl1-base
tail -5 .superpowers/run-t1-baseline.log
```

Then the frontend suite:

```bash
docker compose -p pbl1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'cd frontend && npm test -- --run 2>&1 | tee /app/.superpowers/run-t1-vitest.log'
tail -5 .superpowers/run-t1-vitest.log
```

Record BOTH measured numbers verbatim in the task report as **the phase baseline**. `4503/383` is the STALE pre-#126 hypothesis (`.superpowers/sdd/progress.md:3827`) and must not be reused as a fact. Every later task's expected count is `MEASURED_BASELINE + <tests added>`.

- [ ] **Step 2: Write the failing resolve tests**

Append to `tests/test_collection_resolve.py` (the existing `FakeGuid`/`FakeItem`/`FakeSection`/`_section` helpers stay as they are; `FakeSection` grows a `search`):

```python
# --- roadmap row 143: the index at season and episode level -----------------
#
# A Show library's ``section.all()`` returns Shows only, and a Show carries no
# per-episode guid, so an episode-level definition has nothing to resolve
# against. The traversal answer was banked by 8b Task 5
# (``.superpowers/sdd/task-5-report.md``): ``section.search(libtype="episode")``
# returns episodes in one call. What was missing is the CONTRACT, not the query.


class FakeSearchSection(FakeSection):
    """A section that answers ``all()`` and ``search(libtype=...)`` separately.

    Deliberately DIFFERENT objects per level: an implementation that walked
    ``all()`` and filtered would find episodes that are not there, which is the
    whole point of the second traversal.
    """

    def __init__(self, items, by_libtype=None):
        super().__init__(items)
        self._by_libtype = dict(by_libtype or {})
        self.searches: list[str] = []

    def search(self, libtype=None, **kwargs):
        self.searches.append(libtype)
        return list(self._by_libtype.get(libtype, []))


def _show_section():
    return FakeSearchSection(
        [FakeItem("Severance", ["tvdb://371980"])],
        {
            "episode": [
                FakeItem("S01E01", ["tvdb://7645236", "imdb://tt11248124"]),
                FakeItem("NoEpisodeGuids", []),
            ],
            "season": [FakeItem("Season 1", [])],
        },
    )


def test_the_item_level_index_is_still_one_all_call_and_no_search():
    """Gate-off byte-identity: an item-level pass must not gain a request."""
    section = _show_section()
    build_owned_index(section)
    assert section.all_calls == 1
    assert section.searches == []


def test_an_episode_level_index_comes_from_a_libtype_search_not_from_all():
    section = _show_section()
    index = build_owned_index(section, "episode")
    assert section.searches == ["episode"]
    assert section.all_calls == 0
    assert index["tvdb"]["7645236"].title == "S01E01"
    assert index["imdb"]["tt11248124"].title == "S01E01"


def test_a_season_level_index_comes_from_the_season_libtype_search():
    section = _show_section()
    index = build_owned_index(section, "season")
    assert section.searches == ["season"]
    assert index["plex"]["Season 1"].title == "Season 1"


def test_an_episode_with_no_guids_is_still_reachable_by_rating_key():
    """Season and episode guid coverage is agent-dependent; the rating key is
    always there, which is what makes ``plex_id``-shaped episode definitions
    work on a library whose agent populates no episode guids."""
    index = build_owned_index(_show_section(), "episode")
    assert index["plex"]["NoEpisodeGuids"].title == "NoEpisodeGuids"


def test_the_show_and_the_episode_index_are_separate_answers():
    """The show's own tvdb id must not appear in the episode index, and the
    episode's must not appear in the item index: they are different id spaces
    and merging them would resolve a series id to an episode."""
    section = _show_section()
    items = build_owned_index(section, "item")
    episodes = build_owned_index(section, "episode")
    assert "371980" in items["tvdb"] and "371980" not in episodes["tvdb"]
    assert "7645236" in episodes["tvdb"] and "7645236" not in items["tvdb"]


def test_an_unknown_level_is_a_key_error_not_a_silent_item_walk():
    """A typo must not quietly resolve the whole library at item level, which
    is a full, plausible, wrong membership."""
    import pytest
    with pytest.raises(KeyError):
        build_owned_index(_show_section(), "chapter")


def test_resolving_against_an_episode_index_needs_no_new_resolver():
    """``resolve_external`` is namespace-generic: the level is a property of
    the INDEX, not of the lookup. This test exists so a future refactor that
    adds a level parameter to the resolver has to delete it deliberately."""
    index = build_owned_index(_show_section(), "episode")
    resolved = resolve_external(index, [("tvdb", "7645236"), ("tvdb", "999")])
    assert [i.title for i in resolved.items] == ["S01E01"]
    assert resolved.unresolved == 1
```

- [ ] **Step 3: Run them and watch them fail**

```bash
docker compose -p pbl1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_resolve.py 2>&1 | tee /app/.superpowers/run-t1-red1.log'
```

Expected: FAIL — `build_owned_index() takes 1 positional argument but 2 were given`.

- [ ] **Step 4: Add the level vocabulary**

Append to `src/autoposter/collections/ids.py`:

```python
# What a definition's members ARE, as opposed to what namespace names them.
# "item" is the library's own granularity -- a Movie in a Movie library, a Show
# in a Show library -- and is what every builder that predates roadmap row 143
# means. The other two exist because a Show library's own items carry no
# per-episode guid, so an episode-level membership has to be resolved against a
# SECOND traversal of the same library (``resolve.build_owned_index``).
MemberLevel = Literal["item", "season", "episode"]
MEMBER_LEVELS: frozenset[str] = frozenset({"item", "season", "episode"})

# The ``libtype`` each level is searched under. None is "do not search at all":
# ``section.all()`` is one request for the whole library and is what the item
# level has always used, so the item level must keep costing exactly that.
LIBTYPE_FOR_LEVEL: dict[str, str | None] = {
    "item": None,
    "season": "season",
    "episode": "episode",
}
```

- [ ] **Step 5: Make the index level-aware**

In `src/autoposter/collections/resolve.py`, extend the import and replace the head of `build_owned_index`:

```python
from autoposter.collections.ids import (
    LIBTYPE_FOR_LEVEL,
    NAMESPACES,
    ExternalId,
    MemberLevel,
)
```

```python
def build_owned_index(section, level: MemberLevel = "item") -> OwnedIndex:
    """``{namespace: {value: plex_item}}`` for everything in the library.

    One ``section.all()`` pass. Within an item, the first guid of a namespace
    claims it -- the IMDb-only index this replaces stopped at an item's first
    ``imdb://`` guid, and reproducing that exactly is what lets the ported
    sources keep their output. Across items, the first item to claim a value
    keeps it; a duplicate guid on a second item does not steal the mapping.

    ``level`` (roadmap row 143) is what the index's entries ARE. ``"item"`` is
    the library's own granularity and still costs exactly one ``section.all()``.
    ``"season"``/``"episode"`` walk the same library again through
    ``section.search(libtype=...)``, because a Show's own guids say nothing
    about its episodes -- which is precisely why no builder could hand an
    episode back before this. The two indexes are kept SEPARATE rather than
    merged: a tvdb series id and a tvdb episode id are different id spaces, and
    merging them would let a series id resolve to an episode.

    An unknown level raises ``KeyError`` rather than falling back to the item
    walk. The fallback would be a full, plausible, wrong membership, which is
    the one outcome every rule in this package exists to prevent.
    """
    libtype = LIBTYPE_FOR_LEVEL[level]
    entries = section.all() if libtype is None else section.search(libtype=libtype)
    index: OwnedIndex = {namespace: {} for namespace in NAMESPACES}
    for item in entries:
```

(the loop body below is unchanged), and the log line becomes:

```python
    logger.debug(
        "indexed %d %s(s): %s",
        len(index["plex"]),
        level,
        ", ".join("%d by %s" % (len(index[ns]), ns) for ns in GUID_PREFIXES),
    )
```

- [ ] **Step 6: Run the resolve tests to green**

```bash
docker compose -p pbl1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_resolve.py 2>&1 | tee /app/.superpowers/run-t1-green1.log'
```

Expected: PASS (the seven new tests plus the file's existing ones).

- [ ] **Step 7: Write the failing engine + entry-point tests**

Create `tests/test_collection_member_levels.py`:

```python
"""Roadmap row 143: resolving a definition's members at episode level.

The contract row 143 asks for is a *builder* one: a builder says what its ids
NAME (``BuilderResult.level``) and the engine resolves them against an index of
that level, built at most once per level per pass. Row 88's operator-facing
``builder_level`` sits on top of this and is the next task's.

Everything here goes through the REAL ``run_library``: the entry-point law. A
helper-level test would not catch the two failures that actually matter -- an
episode index built on every pass (a second full traversal of a large library,
for nothing) and an episode-level membership resolved against the item index
(zero members, reported as "this library owns none of them").
"""
from types import SimpleNamespace

import pytest

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

    def __init__(self, shows=(), episodes=(), seasons=(), existing=()):
        self._shows = list(shows)
        self._by_libtype = {"episode": list(episodes), "season": list(seasons)}
        self._existing = {c.title: c for c in existing}
        self.all_calls = 0
        self.searches: list[str] = []
        self.created: dict[str, list] = {}

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
```

- [ ] **Step 8: Run them and watch them fail**

```bash
docker compose -p pbl1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_member_levels.py 2>&1 | tee /app/.superpowers/run-t1-red2.log'
```

Expected: FAIL — `BuilderResult.__init__() got an unexpected keyword argument 'level'`.

- [ ] **Step 9: Add `BuilderResult.level`**

In `src/autoposter/collections/builders/base.py`, extend the ids import and `__all__`:

```python
from autoposter.collections.ids import (
    MEMBER_LEVELS,
    NAMESPACES,
    ExternalId,
    MemberLevel,
    Namespace,
)
```

```python
__all__ = [
    "MEMBER_LEVELS",
    "NAMESPACES",
    "PREFERENCE",
    "Builder",
    "BuilderContext",
    "BuilderResult",
    "ExternalId",
    "LibraryTypeMismatch",
    "MemberLevel",
    "Namespace",
    "PlexIdBuilder",
    "PlexIdParams",
    "PlexSectionAccess",
    "REGISTRY",
    "SmartBuilder",
    "SmartContext",
    "SourceClients",
    "best_external_id",
    "register",
    "require_library_type",
]
```

and add the field at the END of `BuilderResult` (last, so every positional construction in the tree keeps working), with the docstring paragraph:

```python
    poster_key: str | None = None
    # Roadmap row 143. What this builder's ids NAME: the library's own items
    # (the default, and what every builder written before 143 means), or the
    # seasons/episodes inside them. The engine resolves against an index of
    # this level, and a builder that gets it wrong resolves nothing and is
    # reported as such -- which is the honest failure, since the alternative
    # (matching a series id to an episode) is a wrong collection nobody can see.
    level: MemberLevel = "item"
```

- [ ] **Step 10: Teach `PlexSectionAccess` the level**

In `src/autoposter/collections/builders/sources_bundle.py`, replace `PlexSectionAccess.__init__`/`owned_index`:

```python
    def __init__(self, section: object, owned_index: Callable[..., dict]):
        self._section = section
        self._owned_index = owned_index

    def section(self) -> object:
        """The plexapi ``LibrarySection`` this pass is running against."""
        return self._section

    def owned_index(self, level: str = "item") -> dict:
        """``{namespace: {value: plex_item}}`` for the whole library.

        The engine's own index, built at most once per LEVEL per library per
        pass (roadmap row 143). ``"item"`` is the library's own granularity and
        costs the ``section.all()`` the engine was paying anyway;
        ``"episode"``/``"season"`` cost one extra traversal, and only for the
        builders and definitions that ask.
        """
        return self._owned_index(level)
```

- [ ] **Step 11: Cache one index per level in the engine**

In `src/autoposter/collections/engine.py`, replace the `index = None` declaration and the `owned_index` closure:

```python
    run_cache: dict = dict(run_cache_seed or {})
    # One owned index per MEMBER LEVEL (roadmap row 143), each built at most
    # once. A dict rather than a single slot because an episode-level
    # definition and an item-level one in the same pass are two different
    # traversals of the same library, and neither may pay for the other's.
    indexes: dict[str, dict] = {}
    existing: dict | None = None

    def owned_index(level: str = "item"):
        if level not in indexes:
            indexes[level] = build_owned_index(section, level)
        return indexes[level]
```

- [ ] **Step 12: Resolve at the builder's declared level**

In `_run_one`, replace line 676 and the mdblist call's index argument so the pass uses ONE index for both:

```python
    # Roadmap row 143: the level is the BUILDER's answer to "what do my ids
    # name". Resolving episode ids against the item index does not error, it
    # matches nothing -- and "matched nothing" looks exactly like a correct
    # collection of titles the library does not own.
    index = owned_index(result.level)
    resolved = resolve_external(index, result.ids)
```

and at the mdblist push (previously `owned_index()`):

```python
        outcome.actions += await sync_membership(
            definition, items, index, result.ids,
```

- [ ] **Step 13: Run the level tests to green**

```bash
docker compose -p pbl1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_member_levels.py 2>&1 | tee /app/.superpowers/run-t1-green2.log'
```

Expected: PASS (6 tests).

- [ ] **Step 14: Pin the plexapi derivation row 88 will rely on**

Append to `tests/test_plexapi_collection_contract.py`:

```python
# --- Roadmap row 143/88: a collection's Plex type comes from its members -----
#
# ``lists.reconcile_list_collection`` creates through ``section.createCollection``
# and never passes a type. That is safe at season/episode granularity ONLY
# because plexapi derives the type from the members themselves. If a plexapi
# upgrade ever changes that, an episode-level collection would be created as a
# show collection and every member would silently fail to attach -- so the
# derivation is pinned here rather than assumed.

def test_collection_create_derives_the_plex_type_from_the_first_items_type():
    import inspect

    from plexapi.collection import Collection

    source = inspect.getsource(Collection._create)
    assert "itemType = items[0].type" in source
    assert "utils.searchType(itemType)" in source


def test_search_type_still_maps_season_and_episode_to_3_and_4():
    """The four values ``create_blank_collection`` branches on (row 88)."""
    from plexapi.utils import SEARCHTYPES

    assert SEARCHTYPES["movie"] == 1
    assert SEARCHTYPES["show"] == 2
    assert SEARCHTYPES["season"] == 3
    assert SEARCHTYPES["episode"] == 4
```

- [ ] **Step 15: Run the full suite and ruff**

```bash
docker compose -p pbl1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pbl1-t1 test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t1-full.log'
docker wait pbl1-t1
docker cp pbl1-t1:/app/.superpowers/run-t1-full.log .superpowers/run-t1-full.log
docker rm pbl1-t1
tail -5 .superpowers/run-t1-full.log

docker compose -p pbl1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'ruff check src tests 2>&1 | tee /app/.superpowers/run-t1-ruff.log'
```

Expected: `MEASURED_BASELINE + 15` passed (7 resolve + 6 level + 2 contract), skips unchanged, ruff clean. State the expected number in the report BEFORE reading the log, then reconcile.

- [ ] **Step 16: Confirm the diff is inside the Files block**

```bash
git diff --stat a4ad5cb -- src/ tests/
```

Expected: exactly `collections/ids.py`, `collections/resolve.py`, `collections/builders/base.py`, `collections/builders/sources_bundle.py`, `collections/engine.py`, `tests/test_collection_resolve.py`, `tests/test_collection_member_levels.py`, `tests/test_plexapi_collection_contract.py`.

- [ ] **Step 17: Commit**

```bash
git add src/autoposter/collections/ids.py src/autoposter/collections/resolve.py \
  src/autoposter/collections/builders/base.py \
  src/autoposter/collections/builders/sources_bundle.py \
  src/autoposter/collections/engine.py tests/test_collection_resolve.py \
  tests/test_collection_member_levels.py tests/test_plexapi_collection_contract.py
git commit --no-gpg-sign -m "feat(collections): resolve a definition's members at episode or season level (row 143)"
docker compose -p pbl1 down
```

---

# Task 2: Row 88's phase-1 slice — `builder_level` on a definition

**Files:**
- Modify: `src/autoposter/config/schema.py:1007-1191` (the field) and `:1321` (a new validator, placed before `_hub_priority_needs_a_promotion`)
- Modify: `src/autoposter/collections/engine.py` (`_run_one`: the effective level, two refusals)
- Modify: `src/autoposter/collections/reconcile.py:36` (`COLLECTION_TYPES`) and `:634-668` (`create_blank_collection`)
- Modify: `config/autoposter.example.yaml`
- Test: `tests/test_collection_member_levels.py` (modify)
- Test: `tests/test_collection_reconcile.py` (modify)

**Interfaces:**
- Consumes: T1's contract — `MemberLevel`, `MEMBER_LEVELS`, `LIBTYPE_FOR_LEVEL`, `build_owned_index(section, level)`, `BuilderResult.level`, the engine's `owned_index(level)`.
- Produces: `CollectionDefinition.builder_level: Literal["item", "season", "episode"] = "item"`; `autoposter.collections.reconcile.COLLECTION_TYPES: dict[str, int]`. T3 consumes `builder_level` in one validator only.

**Scope fence:** LIST collections only. Smart/`plex_search` episode collections are rows 173/179 and are refused here by name. `collections/smart.py`'s own `type` hardcode is deliberately NOT touched — a smart episode collection is the fenced row's, and changing that line would ship half of it.

- [ ] **Step 1: Write the failing schema + engine tests**

Append to `tests/test_collection_member_levels.py`:

```python
# --- roadmap row 88: the operator-facing level ------------------------------
#
# Row 143 made the ENGINE able to resolve at episode level when a builder says
# its ids name episodes. Row 88 is the other direction: an operator declaring
# it on the definition, for the builders that produce plain ids and cannot know
# (``plex_id``, a text file, an MDBList list of episode ids).


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


def test_builder_level_is_refused_on_a_smart_builder():
    """Rows 173/179 own the smart/`plex_search` side of this question. A
    definition that asked for it here would load clean and never apply."""
    with pytest.raises(Exception) as error:
        CollectionDefinition(
            title="Smart", builder="cs_bucket", builder_level="episode"
        )
    assert "173" in str(error.value) and "179" in str(error.value)
```

- [ ] **Step 2: Run them and watch them fail**

```bash
docker compose -p pbl2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_member_levels.py 2>&1 | tee /app/.superpowers/run-t2-red1.log'
```

Expected: FAIL — `CollectionDefinition` has no `builder_level` (pydantic `extra` is not permitted on this model, so construction errors).

- [ ] **Step 3: Add the field**

In `src/autoposter/config/schema.py`, after `sync_mode` (line 1053-1062) add:

```python
    # Roadmap row 88, on top of row 143's resolution contract. The default is
    # the library's own granularity, which is what every definition written
    # before this meant. LIST collections only: the smart/`plex_search` side of
    # season/episode collections is rows 173/179 and is refused below.
    builder_level: Literal["item", "season", "episode"] = Field(
        default="item",
        description=(
            "Whether this definition's members are the library's own items, "
            "their seasons, or their episodes."
        ),
    )
```

- [ ] **Step 4: Add the smart refusal**

In `src/autoposter/config/schema.py`, immediately before `_hub_priority_needs_a_promotion` (line 1321):

```python
    @model_validator(mode="after")
    def _builder_level_needs_a_list_builder(self) -> "CollectionDefinition":
        """Row 88 ships the LIST half of season/episode collections.

        A smart collection's membership is a filter Plex evaluates itself, so
        the level is part of the SEARCH -- the ``type:`` selector (roadmap row
        179) and the season/episode predicate families (row 173), both filed as
        their own rows with their own sort matrices. Accepting the field here
        would load clean, apply nothing, and read as configured.
        """
        from autoposter.collections.builders import REGISTRY

        if self.builder_level == "item":
            return self
        if getattr(REGISTRY.get(self.builder), "smart", False):
            raise ValueError(
                f"'builder_level' does not apply to {self.builder!r}: a smart "
                "collection's members are chosen by a filter Plex evaluates "
                "itself, so asking for seasons or episodes is a question about "
                "the SEARCH -- the 'type:' selector (roadmap row 179) and the "
                "season/episode predicate families (row 173), neither of which "
                "is built yet. Use a list builder for a season- or "
                "episode-level collection"
            )
        return self
```

- [ ] **Step 5: Resolve at the effective level, and refuse the two impossible combinations**

In `src/autoposter/collections/engine.py`'s `_run_one`, replace the resolve block written in T1 Step 12 with:

```python
    # Roadmap rows 143 + 88: what this definition's members ARE. The builder's
    # own answer (``result.level``) is what a builder that KNOWS says --
    # a library-walking episode builder; the definition's ``builder_level`` is
    # what an operator says for a builder that produces plain ids and cannot
    # know. Either may be non-default; both being non-default and DIFFERENT is
    # two answers to one question, and picking one silently resolves against an
    # index the other half never meant.
    declared = getattr(definition, "builder_level", "item")
    if declared != "item" and result.level != "item" and declared != result.level:
        outcome.failed = True
        outcome.skipped = True
        outcome.actions.append(
            "%r: builder_level is %r but %r builds %s-level members; nothing "
            "was applied. Remove builder_level, or point the definition at a "
            "builder that produces %s ids"
            % (definition.title, declared, definition.builder, result.level, declared)
        )
        return outcome
    level = declared if declared != "item" else result.level
    if level != "item" and ctx.library_type != "Show":
        outcome.failed = True
        outcome.skipped = True
        outcome.actions.append(
            "%r: %s-level members exist only in a Show library, and this pass "
            "is running against a %s library, where the search would match "
            "nothing at all. Narrow the definition with `libraries:` so it only "
            "targets Show libraries"
            % (definition.title, level, ctx.library_type)
        )
        return outcome
    index = owned_index(level)
    resolved = resolve_external(index, result.ids)
```

- [ ] **Step 6: Run the level tests to green**

```bash
docker compose -p pbl2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_member_levels.py 2>&1 | tee /app/.superpowers/run-t2-green1.log'
```

Expected: PASS (12 tests — T1's 6 plus these 6).

- [ ] **Step 7: Write the failing `create_blank_collection` tests**

Append to `tests/test_collection_reconcile.py`:

```python
# --- roadmap row 88: a blank collection at season or episode granularity -----
#
# The LIST path creates through ``section.createCollection``, where plexapi
# derives the type from the members (pinned in
# tests/test_plexapi_collection_contract.py). This raw POST is the OTHER
# creation route -- the separator and the ``ops/blank`` endpoint -- and it
# hardcoded ``1 if libtype == "movie" else 2``, which answers "season" and
# "episode" with "show".

class _BlankPostSection:
    """Captures the raw POST ``create_blank_collection`` makes."""

    key = "7"

    def __init__(self):
        self.queries: list[str] = []
        self._server = self
        self._session = type("Sess", (), {"post": "POST-SENTINEL"})()

    def _uriRoot(self):
        return "server://abc/com.plexapp.plugins.library"

    def query(self, key, method=None, **kwargs):
        self.queries.append(key)

    def collection(self, title):
        return type("C", (), {"title": title})()


def _created_type(libtype: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    from autoposter.collections.reconcile import create_blank_collection

    section = _BlankPostSection()
    create_blank_collection(section, libtype, "Blank")
    [query] = section.queries
    return parse_qs(urlsplit(query).query)["type"][0]


def test_a_blank_collection_carries_plexs_own_type_for_every_libtype():
    assert _created_type("movie") == "1"
    assert _created_type("show") == "2"
    assert _created_type("season") == "3"
    assert _created_type("episode") == "4"


def test_an_unknown_libtype_raises_instead_of_creating_a_show_collection():
    """The old ``else 2`` made every unrecognised libtype a show collection --
    a real object in the operator's library, of the wrong kind, reported as
    created."""
    import pytest

    with pytest.raises(KeyError):
        _created_type("chapter")
```

- [ ] **Step 8: Run them and watch them fail**

```bash
docker compose -p pbl2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_reconcile.py 2>&1 | tee /app/.superpowers/run-t2-red2.log'
```

Expected: FAIL — `_created_type("season") == "2"`, and no `KeyError` for `"chapter"`.

- [ ] **Step 9: Give the POST Plex's own type table**

In `src/autoposter/collections/reconcile.py`, beside `LIBTYPES` (line 36):

```python
LIBTYPES = {"Movie": "movie", "Show": "show"}

# Plex's own collection ``type`` values, which are ``plexapi.utils.SEARCHTYPES``
# for the four libtypes a collection can hold (pinned in
# tests/test_plexapi_collection_contract.py). A table rather than the
# ``1 if movie else 2`` this replaces: that expression answered "season" and
# "episode" with "show" (roadmap row 88), and a KeyError on an unknown libtype
# is a loud failure where the old default was a real collection of the wrong
# kind, created and reported as a success.
COLLECTION_TYPES = {"movie": 1, "show": 2, "season": 3, "episode": 4}
```

and in `create_blank_collection`:

```python
    args = {
        "type": COLLECTION_TYPES[libtype],
        "title": title,
        "smart": 0,
        "sectionId": section.key,
        "uri": "%s/library/metadata" % server._uriRoot(),
    }
```

Add to that function's docstring, after the "The title is a parameter..." paragraph:

```
    ``libtype`` is Plex's own name for what the collection HOLDS -- movie,
    show, season or episode (roadmap row 88). Nothing in the list path reaches
    here: ``lists.reconcile_list_collection`` creates through
    ``section.createCollection``, where plexapi derives the type from the
    members themselves. This route is the separator's and the ``ops/blank``
    endpoint's, and it carries the full table so the two creation paths cannot
    disagree about what a season collection is.
```

- [ ] **Step 10: Run the reconcile tests to green**

```bash
docker compose -p pbl2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_reconcile.py 2>&1 | tee /app/.superpowers/run-t2-green2.log'
```

Expected: PASS.

- [ ] **Step 11: Document the field in the example config**

In `config/autoposter.example.yaml`, inside the commented `definitions:` example block (near line 148, beside `sync_to_mdb_list`), add:

```yaml
  #       # row 88: whether this definition's members are the library's own
  #       # items (the default), their seasons, or their episodes. Show
  #       # libraries only, and list builders only -- a smart collection's
  #       # level is rows 173/179.
  #       builder_level: episode
```

- [ ] **Step 12: Run the full suite, the descriptions guard and ruff**

```bash
docker compose -p pbl2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pbl2-t2 test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t2-full.log'
docker wait pbl2-t2
docker cp pbl2-t2:/app/.superpowers/run-t2-full.log .superpowers/run-t2-full.log
docker rm pbl2-t2
tail -5 .superpowers/run-t2-full.log

docker compose -p pbl2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_config_descriptions.py tests/test_example_config_matches_schema.py 2>&1 | tee /app/.superpowers/run-t2-guards.log'
docker compose -p pbl2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'ruff check src tests 2>&1 | tee /app/.superpowers/run-t2-ruff.log'
```

Expected: `T1_TOTAL + 8` passed (6 level + 2 reconcile), guards PASS, ruff clean. State the expected number BEFORE reading the log, then reconcile.

- [ ] **Step 13: Commit**

```bash
git add src/autoposter/config/schema.py src/autoposter/collections/engine.py \
  src/autoposter/collections/reconcile.py config/autoposter.example.yaml \
  tests/test_collection_member_levels.py tests/test_collection_reconcile.py
git commit --no-gpg-sign -m "feat(collections): builder_level for season and episode list collections (row 88, list slice)"
docker compose -p pbl2 down
```

---

# Task 3: Row 89 — per-definition Arr overrides

**Files:**
- Create: `.superpowers/sdd/p-arr-api-capture.md` (Step 1, BEFORE any code)
- Modify: `src/autoposter/arr/sync.py:30`, `:79-81`, `:210`, `:245`
- Modify: `src/autoposter/arr/client.py` (three new methods)
- Create: `src/autoposter/collections/arr_overrides.py`
- Modify: `src/autoposter/config/schema.py` (four definition fields, one config flag, one validator)
- Modify: `src/autoposter/collections/engine.py` (`_run_one`: the restriction step and the tag step)
- Modify: `config/autoposter.example.yaml`
- Test: `tests/test_arr_client.py` (modify)
- Test: `tests/test_collection_arr_overrides.py` (create)

**Interfaces:**
- Consumes: `autoposter.arr.client.ArrClient`, `ArrKind`, `RADARR`, `SONARR`; `autoposter.collections.builders.SourceClients.radarr/.sonarr` (None when unconfigured); `CollectionDefinition.builder_level` (T2) for one refusal; the engine's `ctx.run_cache` memo idiom from `collections/builders/arr.py::_tag_map`.
- Produces:
  - `autoposter.arr.sync.GUID_KEY: dict[str, str]`, `external_id(item, guid_key) -> str | None` (promoted from private — a second consumer)
  - `ArrClient.entry_ids_by_external_id(entries: list[dict]) -> dict[str, int]`
  - `async ArrClient.create_tag(label: str) -> int`
  - `async ArrClient.apply_tags(entry_ids: list[int], tag_ids: list[int]) -> None`
  - `async autoposter.collections.arr_overrides.restricted_members(definition, items, *, library_type, radarr, sonarr, run_cache) -> tuple[list | None, list[str]]`
  - `async autoposter.collections.arr_overrides.tag_members(definition, items, *, library_type, radarr, sonarr, run_cache, apply) -> list[str]`
  - `CollectionDefinition.radarr_restrict`, `.sonarr_restrict`, `.item_radarr_tag`, `.item_sonarr_tag`; `CollectionsConfig.arr_tag_apply`

**The highest-stakes external write in this phase.** Everything below the banking step depends on the banking step. Do not reorder it.

- [ ] **Step 1: BANK the Radarr and Sonarr API sections**

Fetch both specs and extract the sections this task writes against:

```bash
mkdir -p .superpowers/sdd
curl -sS -o /tmp/radarr-openapi.json \
  https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json
curl -sS -o /tmp/sonarr-openapi.json \
  https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json
sha256sum /tmp/radarr-openapi.json /tmp/sonarr-openapi.json
python - <<'PY'
import json
for name, res, ed, item in (
    ("radarr", "MovieResource", "MovieEditorResource", "/api/v3/movie/{id}"),
    ("sonarr", "SeriesResource", "SeriesEditorResource", "/api/v3/series/{id}"),
):
    d = json.load(open(f"/tmp/{name}-openapi.json", encoding="utf-8"))
    print("=" * 20, name, d["info"]["version"])
    editor = item.rsplit("/", 1)[0] + "/editor"
    for path in (item, editor, "/api/v3/tag"):
        print("---", path)
        print(json.dumps(d["paths"][path], indent=1))
    for schema in (ed, "ApplyTags", "TagResource"):
        print("---", schema)
        print(json.dumps(d["components"]["schemas"][schema], indent=1))
    print("---", res, "tags property / required")
    s = d["components"]["schemas"][res]
    print("required:", s.get("required"), "additionalProperties:", s.get("additionalProperties"))
    print("tags:", json.dumps(s["properties"]["tags"]))
    print("property count:", len(s["properties"]))
PY
```

Write `.superpowers/sdd/p-arr-api-capture.md` containing, VERBATIM, every block that command printed, each under a heading citing its source: the spec URL, the retrieval date, the file's sha256, and the JSON pointer (e.g. `radarr openapi.json → paths → "/api/v3/movie/editor" → put`). Add a short preamble stating the three findings the code depends on:

1. `PUT /api/v3/movie/editor` (Radarr) / `PUT /api/v3/series/editor` (Sonarr) take `MovieEditorResource`/`SeriesEditorResource` — `{"movieIds"|"seriesIds": [int], "tags": [int], "applyTags": "add"}` — a **tag-only** write. `applyTags` is the enum `["add", "remove", "replace"]`.
2. `PUT /api/v3/{movie|series}/{id}` takes the FULL `MovieResource`/`SeriesResource` (49 and 45 properties, `additionalProperties: false`, no `required` list). It is banked and deliberately NOT used: a full-body echo is how a dropped field becomes a NULLed field on the operator's Arr, and it is one request per member instead of one per pass.
3. `DELETE /api/v3/movie/editor` takes the **same body** as the PUT and deletes the movies. Method pinning in the tests is therefore load-bearing, not decoration.

**STOP GATE:** if any banked block disagrees with what this plan quotes below (endpoint path, body key names, the `applyTags` enum, the `TagResource` shape), STOP and report the difference. Do not adapt the code to a shape the plan has not seen.

- [ ] **Step 2: Commit the capture on its own**

```bash
git add .superpowers/sdd/p-arr-api-capture.md
git commit --no-gpg-sign -m "docs(arr): bank Radarr and Sonarr's own OpenAPI sections for row 89's write path"
```

- [ ] **Step 3: Write the failing client tests**

Append to `tests/test_arr_client.py`:

```python
# --- roadmap row 89(b): the client's first WRITE ----------------------------
#
# Shapes are the services' own, banked verbatim in
# `.superpowers/sdd/p-arr-api-capture.md` -- `paths → "/api/v3/tag" → post`
# and `paths → "/api/v3/{movie,series}/editor" → put`. Nothing here is recalled
# from memory, and nothing here uses the full-body `PUT /api/v3/movie/{id}`:
# echoing a 49-property resource back is how a dropped field becomes a NULLed
# one on the operator's own Radarr.

RADARR_TAGS = [{"id": 3, "label": "kids"}, {"id": 5, "label": "4k"}]


async def test_entry_ids_by_external_id_maps_the_service_ids():
    """The Arr's INTERNAL id is what the editor endpoint takes; the external id
    is what a Plex item can be matched by. This is the join between them."""
    async with _fake_http(lambda request: httpx.Response(200, json=RADARR_MOVIES)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        mapping = client.entry_ids_by_external_id(await client.listing())

    assert mapping == {"438631": 1}


async def test_create_tag_posts_the_label_and_returns_the_new_id():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        seen["key"] = request.headers.get("X-Api-Key")
        return httpx.Response(200, json={"id": 9, "label": "autoposter"})

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "secret-key", RADARR)
        tag_id = await client.create_tag("autoposter")

    assert tag_id == 9
    assert seen["method"] == "POST"
    assert seen["url"] == "https://radarr.example/api/v3/tag"
    assert seen["body"] == {"label": "autoposter"}
    assert seen["key"] == "secret-key"


async def test_create_tag_raises_on_non_2xx():
    """An unknown tag id would be written onto items as a number meaning
    nothing, so a failed creation must never be swallowed."""
    async with _fake_http(lambda request: httpx.Response(400)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(httpx.HTTPStatusError):
            await client.create_tag("autoposter")


async def test_apply_tags_puts_the_editor_body_for_radarr():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await client.apply_tags([1, 2], [9])

    assert seen["method"] == "PUT", "DELETE takes the same body and deletes the movies"
    assert seen["url"] == "https://radarr.example/api/v3/movie/editor"
    assert seen["body"] == {"movieIds": [1, 2], "tags": [9], "applyTags": "add"}


async def test_apply_tags_puts_the_editor_body_for_sonarr():
    seen = {}

    async def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://sonarr.example", "key", SONARR)
        await client.apply_tags([4], [9])

    assert seen["method"] == "PUT"
    assert seen["url"] == "https://sonarr.example/api/v3/series/editor"
    assert seen["body"] == {"seriesIds": [4], "tags": [9], "applyTags": "add"}


async def test_apply_tags_is_additive_never_replace():
    """``applyTags`` is the enum ["add", "remove", "replace"] (banked). This
    service tags items it manages; it does not own an Arr's tag vocabulary, so
    it must never take a tag off something."""
    seen = {}

    async def handler(request):
        seen["body"] = json.loads(request.read())
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await client.apply_tags([1], [9])

    assert seen["body"]["applyTags"] == "add"


async def test_apply_tags_with_nothing_to_tag_makes_no_request():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200)

    async with _fake_http(handler) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        await client.apply_tags([], [9])
        await client.apply_tags([1], [])

    assert requests == []


async def test_apply_tags_raises_on_non_2xx():
    async with _fake_http(lambda request: httpx.Response(500)) as http:
        client = ArrClient(http, "https://radarr.example", "key", RADARR)
        with pytest.raises(httpx.HTTPStatusError):
            await client.apply_tags([1], [9])
```

and add `import json` to the file's imports (beside `import httpx`).

- [ ] **Step 4: Run them and watch them fail**

```bash
docker compose -p pbl3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_arr_client.py 2>&1 | tee /app/.superpowers/run-t3-red1.log'
```

Expected: FAIL — `AttributeError: 'ArrClient' object has no attribute 'create_tag'`.

- [ ] **Step 5: Add the three client methods**

Append to `src/autoposter/arr/client.py` (after `add`):

```python
    def entry_ids_by_external_id(self, entries: list[dict]) -> dict[str, int]:
        """``{external id: the service's own id}`` for a listing.

        The join the tag write needs: a Plex item is matched by its tmdb/tvdb
        guid, and the editor endpoint takes the service's INTERNAL ids. Derived
        from the listing already fetched rather than from a second request, and
        skipping the same entries ``ordered_ids_in`` skips -- an entry with no
        external id cannot be matched to a Plex item at all, so tagging it
        would be tagging something nobody named.
        """
        mapping: dict[str, int] = {}
        for entry in entries:
            value, entry_id = entry.get(self._kind.id_field), entry.get("id")
            if value and entry_id is not None:
                mapping.setdefault(str(value), int(entry_id))
        return mapping

    async def create_tag(self, label: str) -> int:
        """Add a label to the instance's tag vocabulary and return its id.

        ``POST /api/v3/tag`` with a ``TagResource`` body (banked:
        ``.superpowers/sdd/p-arr-api-capture.md``, ``paths → "/api/v3/tag" →
        post``). One flat vocabulary per service, as ``tags()`` above says.

        Raises on a non-2xx like everything else here, and for a sharp reason:
        a creation that failed but was treated as fine would leave the caller
        writing an id that means nothing, or another tag entirely.
        """
        url = f"{self._base_url}/api/v3/tag"
        response = await self._http.post(url, json={"label": label}, headers=self._headers())
        response.raise_for_status()
        return int(response.json()["id"])

    async def apply_tags(self, entry_ids: list[int], tag_ids: list[int]) -> None:
        """Add tags to entries the service already holds. The client's first PUT.

        ``PUT /api/v3/movie/editor`` (Radarr) / ``PUT /api/v3/series/editor``
        (Sonarr) with ``{"<resource>Ids": [...], "tags": [...], "applyTags":
        "add"}`` -- banked verbatim in
        ``.superpowers/sdd/p-arr-api-capture.md``.

        The editor endpoint rather than ``PUT /api/v3/{movie,series}/{id}``,
        deliberately. That one takes the FULL resource (49 properties on
        Radarr's ``MovieResource``, ``additionalProperties: false``), so a
        write built from anything less than a fresh, complete GET can blank a
        field on the operator's own instance -- and it costs one request per
        item where this costs one per pass.

        ``applyTags`` is pinned to ``"add"``. The enum also has ``"remove"``
        and ``"replace"``; this service tags items it manages and does not own
        the instance's tag vocabulary, so it must never take a tag off
        anything. **Note that ``DELETE`` on this same URL takes this same body
        and deletes the items** -- the method is part of the contract, which is
        why the tests pin it.

        Nothing to tag is not a request: an empty list on either side would be
        a body the service is free to interpret however it likes.
        """
        if not entry_ids or not tag_ids:
            return
        url = f"{self._base_url}/api/v3/{self._kind.resource}/editor"
        payload = {
            f"{self._kind.resource}Ids": list(entry_ids),
            "tags": list(tag_ids),
            "applyTags": "add",
        }
        response = await self._http.put(url, json=payload, headers=self._headers())
        response.raise_for_status()
```

- [ ] **Step 6: Run the client tests to green**

```bash
docker compose -p pbl3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_arr_client.py 2>&1 | tee /app/.superpowers/run-t3-green1.log'
```

Expected: PASS (8 new tests plus the file's existing ones).

- [ ] **Step 7: Promote the guid mapping to public**

In `src/autoposter/arr/sync.py`, rename the two module-level names and update their two call sites — a second consumer (`collections/arr_overrides.py`) needs exactly this mapping, and a second implementation of "which external id does this service compare by" would eventually disagree with this one:

```python
# ArrKind.name -> the key parse_guids() uses for that kind's external id.
#
# Public, like ``source_path``/``norm_path``/``shares_tree`` below and for the
# same reason: ``collections/arr_overrides.py`` (roadmap row 89) matches Plex
# items to arr entries by exactly this rule, and two spellings of it would
# eventually disagree about whether an item is registered.
GUID_KEY = {"radarr": "tmdb", "sonarr": "tvdb"}
```

```python
def external_id(item, guid_key: str) -> str | None:
    """The external id an arr instance would know this Plex item by.

    Public for the reason ``GUID_KEY`` above is.
    """
    guids = parse_guids([g.id for g in getattr(item, "guids", None) or []])
    return guids.get(guid_key)
```

Line 210 becomes `guid_key = GUID_KEY[kind.name]` and line 245 `ext_id = external_id(item, guid_key)`.

- [ ] **Step 8: Write the failing override tests**

Create `tests/test_collection_arr_overrides.py`:

```python
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
```

- [ ] **Step 9: Run them and watch them fail**

```bash
docker compose -p pbl3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_arr_overrides.py 2>&1 | tee /app/.superpowers/run-t3-red2.log'
```

Expected: collection error — `ModuleNotFoundError: autoposter.collections.arr_overrides`.

- [ ] **Step 10: Add the four definition fields, the apply flag and the validator**

In `src/autoposter/config/schema.py`, after `builder_level` (T2's field):

```python
    # Roadmap row 89(a). Read-only: the membership is narrowed to what the one
    # configured instance already holds. Kometa's add_missing family -- telling
    # Radarr/Sonarr to ACQUIRE content -- is a declared non-goal.
    radarr_restrict: bool = Field(
        default=False,
        description="Keep only the members Radarr already holds; drop the rest from this collection.",
    )
    sonarr_restrict: bool = Field(
        default=False,
        description="Keep only the members Sonarr already holds; drop the rest from this collection.",
    )
    # Roadmap row 89(b). Unset writes nothing; the deployment ALSO has to set
    # collections.arr_tag_apply, so a definition copied from someone else's
    # config cannot start writing to their Radarr on its own.
    item_radarr_tag: list[str] = Field(
        default_factory=list,
        description="Radarr tags added to every member of this collection that Radarr holds.",
    )
    item_sonarr_tag: list[str] = Field(
        default_factory=list,
        description="Sonarr tags added to every member of this collection that Sonarr holds.",
    )
```

In `CollectionsConfig`, after `mdblist_sync_apply` (line 1483-1493):

```python
    # Roadmap row 89(b), the deployment-level half of the two gates. Off
    # reports what each definition would tag and writes nothing, the same
    # posture apply_to_plex takes for Plex itself.
    arr_tag_apply: bool = Field(
        default=False,
        description=(
            "Actually write definitions' item_radarr_tag/item_sonarr_tag tags "
            "to Radarr and Sonarr; off only reports what would be written."
        ),
    )
```

And a validator on `CollectionDefinition`, beside `_builder_level_needs_a_list_builder`:

```python
    @model_validator(mode="after")
    def _arr_overrides_need_a_list_builder_at_item_level(self) -> "CollectionDefinition":
        """Row 89's fields describe MEMBERS an arr instance could know.

        Two combinations cannot mean anything, and both would load clean and do
        nothing visible: a smart collection's members are chosen by Plex, so
        there is no resolved membership to restrict or tag; and a season or an
        episode carries no tmdbId or tvdbId an arr instance holds, so the
        restriction could only ever exclude everything.
        """
        from autoposter.collections.builders import REGISTRY

        named = [
            name for name in
            ("radarr_restrict", "sonarr_restrict", "item_radarr_tag", "item_sonarr_tag")
            if getattr(self, name)
        ]
        if not named:
            return self
        listed = ", ".join(repr(name) for name in named)
        if getattr(REGISTRY.get(self.builder), "smart", False):
            raise ValueError(
                f"{listed} does not apply to {self.builder!r}: a smart "
                "collection's members are chosen by a filter Plex evaluates "
                "itself, so this definition has no resolved membership to "
                "restrict or tag"
            )
        if self.builder_level != "item":
            raise ValueError(
                f"{listed} cannot be combined with builder_level "
                f"{self.builder_level!r}: a season or an episode carries no "
                "tmdb or tvdb id Radarr or Sonarr would know it by, so the "
                "restriction could only ever exclude every member"
            )
        return self
```

- [ ] **Step 11: Write the overrides module**

Create `src/autoposter/collections/arr_overrides.py`:

```python
"""Row 89: what one definition asks of the Radarr or Sonarr instance.

Two halves that fail in opposite directions, and the difference is the whole
design.

- ``restricted_members`` narrows a resolved membership to the items the ONE
  configured instance already holds. It is read-only. When it cannot be
  evaluated -- the service is not configured, or the listing failed -- it
  returns ``None``, which the engine treats exactly as a filter that could not
  run: the desired set is emptied, ``lists.py`` reads that as "make no
  changes", and the collection is left as it was. Not restricting would write
  precisely the members the operator asked to exclude, which is a full,
  plausible, wrong collection nobody can see is wrong.
- ``tag_members`` writes tags back. It is a side channel: the collection has
  already been applied to Plex, and a dead Arr must not turn a good pass into
  a failed one. So it never raises, and it reports what it could not do.

Opt-in twice, the ``sync_to_mdb_list`` precedent (row 31): the definition names
tags AND the deployment sets ``collections.arr_tag_apply``.

Kometa's ``add_missing``/``radarr_add_all``/``radarr_remove_by_tag`` family --
telling an instance to ACQUIRE or DROP content -- is a declared non-goal
(``docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:1213-1215``).
Nothing here adds, removes or deletes anything on an arr instance; the only
write is an additive tag.
"""
import logging

from autoposter.arr.sync import GUID_KEY, external_id

logger = logging.getLogger(__name__)

# Which library type each service's ids can mean, the same rule
# ``builders/arr.py`` states: Radarr's tmdbId is only ever a movie and Sonarr's
# tvdbId only ever a show.
_LIBRARY_TYPE = {"radarr": "Movie", "sonarr": "Show"}


async def _listing(client, service: str, run_cache: dict) -> list[dict]:
    """The instance's listing, fetched at most once per service per pass.

    The memo idiom ``builders/arr.py::_tag_map`` uses, failures included: a
    config with a dozen restricted definitions costs one GET, and a dead
    service is asked once rather than a dozen times on the pass that can least
    afford it.
    """
    key = f"arr.listing.{service}"
    if key not in run_cache:
        try:
            run_cache[key] = await client.listing()
        except Exception as error:  # noqa: BLE001 - memoised and re-raised below
            run_cache[key] = error
    entries = run_cache[key]
    if isinstance(entries, BaseException):
        raise entries
    return entries


async def _tag_map(client, service: str, run_cache: dict) -> dict[str, int]:
    """The label-to-id vocabulary, memoised on the SAME key
    ``builders/arr.py::_tag_map`` uses, so a pass with both a tag-list builder
    and a tagging definition costs one ``/api/v3/tag`` round trip."""
    key = f"arr.tags.{service}"
    if key not in run_cache:
        try:
            run_cache[key] = await client.tags()
        except Exception as error:  # noqa: BLE001 - memoised and re-raised below
            run_cache[key] = error
    tags = run_cache[key]
    if isinstance(tags, BaseException):
        raise tags
    return tags


async def restricted_members(
    definition, items: list, *, library_type: str, radarr, sonarr, run_cache: dict
) -> tuple[list | None, list[str]]:
    """``(kept, actions)``. ``kept is None`` means "could not be evaluated".

    ``None`` is the refusal the engine contains, not an error: see the module
    docstring for why the alternative is worse.
    """
    asked = [
        (service, client)
        for service, client in (("radarr", radarr), ("sonarr", sonarr))
        if getattr(definition, f"{service}_restrict", False)
    ]
    if not asked:
        return items, []

    actions: list[str] = []
    kept = items
    for service, client in asked:
        field = f"{service}_restrict"
        wanted_type = _LIBRARY_TYPE[service]
        if library_type != wanted_type:
            return None, [
                "%r: %s only holds %s entries, and this pass is running against "
                "a %s library, where it would exclude every member; nothing was "
                "changed. Narrow the definition with `libraries:`"
                % (definition.title, field, wanted_type.lower(), library_type)
            ]
        if client is None:
            return None, [
                "%r: %s is not configured, so %s could not be evaluated and "
                "nothing was changed"
                % (definition.title, service.title(), field)
            ]
        try:
            entries = await _listing(client, service, run_cache)
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            logger.exception(
                "%r: listing %s for %s failed", definition.title, service, field
            )
            return None, [
                "%r: %s could not be listed (%s), so %s was not evaluated and "
                "nothing was changed"
                % (definition.title, service.title(), type(exc).__name__, field)
            ]
        held = client.ids_in(entries)
        guid_key = GUID_KEY[service]
        before = len(kept)
        kept = [
            item for item in kept
            if (value := external_id(item, guid_key)) is not None and value in held
        ]
        actions.append(
            "%r: %s kept %d of %d member(s) %s holds"
            % (definition.title, field, len(kept), before, service.title())
        )

    if items and not kept:
        actions.append(
            "%r: the Arr restriction excluded every member; leaving the "
            "collection untouched" % definition.title
        )
    return kept, actions


async def tag_members(
    definition, items: list, *, library_type: str, radarr, sonarr,
    run_cache: dict, apply: bool,
) -> list[str]:
    """Write this definition's Arr tags onto the members the instance holds.

    Never raises: the collection has already been applied to Plex, and this is
    a side channel. Every failure comes back as an action string carrying the
    exception's CLASS NAME only -- an httpx error's message carries the request
    URL, and the URL is where an api key would be if anyone put one there.
    """
    asked = [
        (service, client, list(dict.fromkeys(labels)))
        for service, client, labels in (
            ("radarr", radarr, definition.item_radarr_tag),
            ("sonarr", sonarr, definition.item_sonarr_tag),
        )
        if labels
    ]
    if not asked:
        return []

    actions: list[str] = []
    for service, client, labels in asked:
        field = f"item_{service}_tag"
        named = ", ".join(labels)
        if library_type != _LIBRARY_TYPE[service]:
            actions.append(
                "%r: %s only holds %s entries, and this pass is running against "
                "a %s library, so nothing was tagged"
                % (definition.title, field, _LIBRARY_TYPE[service].lower(), library_type)
            )
            continue
        if client is None:
            actions.append(
                "%r: %s is not configured, so no member was tagged %s"
                % (definition.title, service.title(), named)
            )
            continue
        try:
            entries = await _listing(client, service, run_cache)
            mapping = client.entry_ids_by_external_id(entries)
            guid_key = GUID_KEY[service]
            entry_ids, missing = [], 0
            for item in items:
                value = external_id(item, guid_key)
                entry_id = mapping.get(value) if value else None
                if entry_id is None:
                    missing += 1
                else:
                    entry_ids.append(entry_id)
            if missing:
                actions.append(
                    "%r: %d member(s) %s does not hold were skipped by %s"
                    % (definition.title, missing, service.title(), field)
                )
            if not entry_ids:
                actions.append(
                    "%r: no member is held by %s, so nothing was tagged %s"
                    % (definition.title, service.title(), named)
                )
                continue
            if not apply:
                actions.append(
                    "%r: would tag %d member(s) in %s with %s "
                    "(collections.arr_tag_apply is off)"
                    % (definition.title, len(entry_ids), service.title(), named)
                )
                continue
            vocabulary = await _tag_map(client, service, run_cache)
            tag_ids = []
            for label in labels:
                tag_id = vocabulary.get(label)
                if tag_id is None:
                    tag_id = await client.create_tag(label)
                    # The memo is the pass's, and a second definition naming the
                    # same new tag must not create it again.
                    vocabulary[label] = tag_id
                    actions.append(
                        "%r: created the %s tag %r"
                        % (definition.title, service.title(), label)
                    )
                tag_ids.append(tag_id)
            await client.apply_tags(entry_ids, tag_ids)
            actions.append(
                "%r: tagged %d member(s) in %s with %s"
                % (definition.title, len(entry_ids), service.title(), named)
            )
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.exception(
                "%r: tagging %s with %s failed", definition.title, service, named
            )
            actions.append(
                "%r: tagging %d member(s) in %s failed (%s); the collection "
                "itself was applied as usual"
                % (definition.title, len(items), service.title(), type(exc).__name__)
            )
    return actions
```

- [ ] **Step 12: Wire both halves into the engine**

In `src/autoposter/collections/engine.py`, add the import beside the mdblist one:

```python
from autoposter.collections.arr_overrides import restricted_members, tag_members
```

In `_run_one`, AFTER the `filters` block and BEFORE the `limit` cap (line 754), insert:

```python
    # Roadmap row 89(a). After the filter and before the cap, for the reason
    # the filter is before the cap (row 96): ``limit`` counts collection
    # MEMBERS, and capping before a stage that can still remove one would leave
    # a short collection. Read-only -- see ``arr_overrides``.
    restriction_stopped = False
    if not filter_failed and (
        definition.radarr_restrict or definition.sonarr_restrict
    ):
        had_items = bool(items)
        kept, restrict_actions = await restricted_members(
            definition, items,
            library_type=ctx.library_type,
            radarr=ctx.sources.radarr, sonarr=ctx.sources.sonarr,
            run_cache=ctx.run_cache,
        )
        outcome.actions += restrict_actions
        if kept is None:
            outcome.failed = True
            items = []
            restriction_stopped = True
        else:
            items = kept
            # ``restricted_members`` has already appended the sentence naming
            # what happened, so the reconcile call below must be skipped rather
            # than allowed to report "source returned no items" on top of it.
            restriction_stopped = had_items and not items
```

and change the reporting branch at line 801 from `if filter_emptied_a_non_empty_set:` to:

```python
    if restriction_stopped:
        # ``restricted_members`` said which service and why; calling the
        # reconciler with an empty list would add "source returned no items",
        # which is false -- the source returned items and the restriction is
        # what removed them.
        pass
    elif filter_emptied_a_non_empty_set:
```

Finally, beside the mdblist push at the end of `_run_one` (line 861), add:

```python
    # Roadmap row 89(b), in the same place and for the same reason as row 31's
    # push: after everything that decides the membership, so what is tagged is
    # what the collection actually holds.
    if definition.item_radarr_tag or definition.item_sonarr_tag:
        outcome.actions += await tag_members(
            definition, items,
            library_type=ctx.library_type,
            radarr=ctx.sources.radarr, sonarr=ctx.sources.sonarr,
            run_cache=ctx.run_cache,
            apply=config.collections.arr_tag_apply and not dry_run and not preview,
        )
```

- [ ] **Step 13: Run the override tests to green**

```bash
docker compose -p pbl3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_arr_overrides.py 2>&1 | tee /app/.superpowers/run-t3-green2.log'
```

Expected: PASS (18 tests).

- [ ] **Step 14: Document the fields in the example config**

In `config/autoposter.example.yaml`, beside `mdblist_sync_apply` (line ~86):

```yaml
  arr_tag_apply: false # row 89: actually write definitions' item_radarr_tag/item_sonarr_tag tags to Radarr/Sonarr; off only reports what would be written
```

and in the commented `definitions:` block, beside `builder_level`:

```yaml
  #       # row 89: keep only the members the configured instance already
  #       # holds, and tag those members there. Read-only otherwise -- this
  #       # never asks Radarr or Sonarr to acquire anything.
  #       radarr_restrict: true
  #       item_radarr_tag: ["autoposter"]
```

- [ ] **Step 15: Run the full suite, the guards and ruff**

```bash
docker compose -p pbl3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pbl3-t3 test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t3-full.log'
docker wait pbl3-t3
docker cp pbl3-t3:/app/.superpowers/run-t3-full.log .superpowers/run-t3-full.log
docker rm pbl3-t3
tail -5 .superpowers/run-t3-full.log

docker compose -p pbl3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_config_descriptions.py tests/test_example_config_matches_schema.py tests/test_arr_sync.py 2>&1 | tee /app/.superpowers/run-t3-guards.log'
docker compose -p pbl3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'ruff check src tests 2>&1 | tee /app/.superpowers/run-t3-ruff.log'
```

Expected: `T2_TOTAL + 26` passed (8 client + 18 overrides), guards PASS (`test_arr_sync.py` proves the `external_id` promotion broke nothing), ruff clean. State the expected number BEFORE reading the log, then reconcile.

- [ ] **Step 16: Confirm no forbidden verb reached the code**

```bash
grep -rn "add_missing\|radarr_add_all\|remove_by_tag" src/autoposter/ && echo FOUND || echo "clean"
grep -rn "\.delete(\|applyTags.*replace\|applyTags.*remove" src/autoposter/arr/ src/autoposter/collections/arr_overrides.py && echo FOUND || echo "clean"
```

Expected: `clean` twice. If anything is found, STOP.

- [ ] **Step 17: Commit**

```bash
git add src/autoposter/arr/client.py src/autoposter/arr/sync.py \
  src/autoposter/collections/arr_overrides.py src/autoposter/collections/engine.py \
  src/autoposter/config/schema.py config/autoposter.example.yaml \
  tests/test_arr_client.py tests/test_collection_arr_overrides.py
git commit --no-gpg-sign -m "feat(collections): per-definition Arr restriction and member tagging (row 89)"
docker compose -p pbl3 down
```

---

# Task 4: The wrap — rows closed, the reversed table cells corrected, the PR

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (rows 143, 88, 89 close; rows 143/173/179's `Depends on` cells corrected)
- Modify: `.superpowers/sdd/progress.md`
- Create: `.superpowers/sdd/p-row8889-pr-body.md`

**Interfaces:** consumes every measured number from T1-T3's reports. Produces no code.

- [ ] **Step 1: Close row 143**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, line 245, append to row 143's description cell (before the ` | M | parity-only |` columns) and correct its `Depends on` cell:

```
 **answered builder-level:** delivered — `collections/ids.py` carries `MemberLevel`/`LIBTYPE_FOR_LEVEL`, `resolve.build_owned_index(section, level)` walks `section.all()` at item level (unchanged, one request) and `section.search(libtype=...)` at season/episode level, and `BuilderResult.level` is the builder's own declaration of what its ids name. The engine caches one index per level per pass, so a library with no episode-level definition pays nothing and two of them pay one traversal. The indexes are kept SEPARATE, not merged: a tvdb series id and a tvdb episode id are different id spaces. Season/episode guid coverage is agent-dependent, which is why the `plex` namespace (rating keys) is the reliable route and an unresolved id is counted rather than guessed
```

and change its dependency cell from `95, 56, 88` to `95, 56` — 88 depends on 143, not the reverse, and leaving the edge in makes a literal cycle with the sequencing that shipped.

- [ ] **Step 2: Close row 88 with its slice stated plainly**

Line 190, append to the description cell:

```
 **answered builder-level (PHASE-1 SLICE):** delivered for LIST collections — `CollectionDefinition.builder_level` (`item` default / `season` / `episode`) resolves a definition's ids against row 143's level-aware index, and `reconcile.COLLECTION_TYPES` gives the raw creation POST Plex's own `type` 1/2/3/4 in place of `1 if movie else 2`. The list path itself needed no change: plexapi's `Collection._create` derives the collection's type from `items[0].type` (pinned in `tests/test_plexapi_collection_contract.py`), and `lists.member_diff`/`_enforce_order` were already granularity-agnostic. **NOT in the slice:** smart / `plex_search` episode collections, which are rows 173 (the season/episode predicate families) and 179 (the `type:` selector) — a `builder_level` on a smart definition is refused at config load, naming both rows. Two more refusals, both at load or contained at run: a non-item level on a Movie library, and a definition whose `builder_level` contradicts its builder's own declared level
```

- [ ] **Step 3: Close row 89 with the operator residual named**

Line 191, append to the description cell:

```
 **answered builder-level:** delivered — (a) `radarr_restrict`/`sonarr_restrict` narrow a resolved membership to what the one configured instance already holds, reusing `arr/sync.py`'s own `GUID_KEY`/`external_id` mapping (promoted from private for this second consumer) and one memoised `listing()` per service per pass. A restriction that cannot be evaluated EMPTIES the desired set rather than writing the unrestricted collection — the containment law, since not restricting writes exactly the members the operator asked to exclude. (b) `item_radarr_tag`/`item_sonarr_tag` write tags back through the client's first PUT, gated twice (`collections.arr_tag_apply`, default off) and additive: `PUT /api/v3/{movie,series}/editor` with `{"<resource>Ids": [...], "tags": [...], "applyTags": "add"}`, the shape banked verbatim from the services' own OpenAPI specs in `.superpowers/sdd/p-arr-api-capture.md`. The full-body `PUT /api/v3/{movie,series}/{id}` was banked and deliberately NOT used: echoing a 49-property resource back is how a dropped field becomes a NULLed one on the operator's instance, and it costs one request per member instead of one per pass. Kometa's `add_missing` family stays a declared non-goal — nothing here acquires, removes or deletes. **Unverified against live:** no Radarr/Sonarr round-trip was performed; the shapes are the services' own published specs and a wrong one fails loudly through `raise_for_status`, but the live tag round-trip is the OPERATOR's checkbox on the PR (row 31's precedent)
```

- [ ] **Step 4: Correct the reversed `Depends on` cells on rows 173 and 179**

Row 173 (line 269): change `101, 179, 88` to `101, 179`. Row 179 (line 275): change `101, 88` to `101`.

Both rows' own prose says the opposite of their dependency cells — 173: *"Collections whose MEMBERS are seasons or episodes are row 88, which is a different question again"*; 179: *"Distinct from row 88, which is about a collection whose MEMBERS are seasons or episodes"*. The recon found no code path in either direction (`p-row8889-recon.md`, row 88 §5). The cells are a table artifact, corrected here to match the prose the rows themselves carry. Add one line under the table's own notes recording the correction:

```
Rows 143/173/179's `Depends on` cells previously named row 88 in the wrong
direction (88 depends on 143's resolution; 173/179 are the search-predicate
side and depend on neither). Corrected when 143/88/89 shipped, against those
rows' own prose — no code dependency was found in either direction.
```

- [ ] **Step 5: Write the progress entry**

Append to `.superpowers/sdd/progress.md`, in the file's existing entry style, with the MEASURED numbers from T1 and T3 (never the stale 4503/383):

```
ROWS 143/88/89 SHIPPED (builder-level & arr overrides, Phase 11 finale):
episode/season-aware resolution (143), `builder_level` list collections (88,
phase-1 slice — smart/plex_search fenced with 173/179), per-definition Arr
restriction + member tagging (89). Suites <MEASURED>/<MEASURED>. Baseline
re-measured post-#126: the prior 4503/383 was measured on feat/parental-labels
before that merge. Residual: 89(b)'s live Radarr/Sonarr tag round-trip is the
operator's PR checkbox. Phase 11: complete.
```

- [ ] **Step 6: Write the PR body**

Create `.superpowers/sdd/p-row8889-pr-body.md`. Plain prose, no AI attribution of any kind, with the two operator checkboxes — and referencing (never duplicating) the MDBList checkbox if it is still open on an unmerged PR:

```markdown
# Builder-level collections and per-definition Arr overrides (rows 143, 88, 89)

Phase 11's finale, in three sequenced pieces.

**Row 143 — episode-aware resolution.** The owned index learns a level:
`build_owned_index(section, level)` still costs one `section.all()` at item
level and adds one `section.search(libtype=...)` traversal per level a pass
actually uses. `BuilderResult.level` is how a builder says what its ids name.
Item-level passes are unchanged, down to the request count.

**Row 88 — `builder_level` collections, phase-1 slice.** A definition can
declare `builder_level: season|episode` and have its members resolved at that
level. LIST collections only: the smart/`plex_search` side is rows 173 and 179,
and a `builder_level` on a smart definition is refused at config load naming
both. The raw creation POST gains Plex's own type table (1/2/3/4) in place of
`1 if movie else 2`; the list path needed no change because plexapi derives a
collection's type from its members.

**Row 89 — per-definition Arr overrides.** `radarr_restrict`/`sonarr_restrict`
narrow a collection to what the configured instance already holds, read-only,
reusing the guid mapping `arr/sync.py` already uses. `item_radarr_tag`/
`item_sonarr_tag` write tags back through the client's first PUT — the bulk
editor endpoint with `applyTags: add`, whose shape is banked verbatim from
Radarr's and Sonarr's own OpenAPI specs in `.superpowers/sdd/p-arr-api-capture.md`.
Off by default and gated a second time by `collections.arr_tag_apply`. Kometa's
`add_missing` family remains out of scope: nothing here acquires or deletes.

A restriction that cannot be evaluated leaves the collection untouched and
reports why, rather than writing the unrestricted membership.

## Operator checkbox

- [ ] **Arr tag round-trip.** With `collections.arr_tag_apply: true` and one
  definition carrying `item_radarr_tag: ["autoposter-test"]`, run a pass from
  somewhere that can reach the instance, confirm the tag appears on the members
  in Radarr, then remove it. No live Radarr/Sonarr write was performed in this
  branch — the shapes come from the services' published specs, not from a
  round-trip.

<!-- If PR #12x's MDBList round-trip checkbox is still open, reference it here
     by number rather than repeating it. -->
```

- [ ] **Step 7: Verify the citations resolve**

```bash
docker compose -p pbl4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_citation_anchors.py 2>&1 | tee /app/.superpowers/run-t4-citations.log'
```

Expected: PASS. Every path this task's prose names must exist.

- [ ] **Step 8: Run the full suite one last time**

```bash
docker compose -p pbl4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pbl4-t4 test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t4-full.log'
docker wait pbl4-t4
docker cp pbl4-t4:/app/.superpowers/run-t4-full.log .superpowers/run-t4-full.log
docker rm pbl4-t4
tail -5 .superpowers/run-t4-full.log
```

Expected: identical to T3's total (this task changes no code). Also re-run the frontend suite once to confirm it is unmoved from T1's measurement:

```bash
docker compose -p pbl4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'cd frontend && npm test -- --run 2>&1 | tee /app/.superpowers/run-t4-vitest.log'
tail -5 .superpowers/run-t4-vitest.log
```

- [ ] **Step 9: Commit and open the PR**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md \
  .superpowers/sdd/progress.md .superpowers/sdd/p-row8889-pr-body.md
git commit --no-gpg-sign -m "docs(roadmap): rows 143, 88 (list slice) and 89 close; the reversed depends-on cells corrected"
git push -u origin feat/builder-level
```

Open the PR against `main` with `.superpowers/sdd/p-row8889-pr-body.md` as the body, verbatim. No AI attribution anywhere in it.

```bash
docker compose -p pbl4 down
```

---

## Self-Review

**Spec coverage.**

| Requirement (facts C1-C4) | Task |
|---|---|
| 143 first: OwnedIndex episode key, BuilderResult episode id form, lists.py applying it | T1 (Steps 4-14; "lists.py applying it" is proven end-to-end at Step 7 and pinned at Step 14 — the list path needed no signature change) |
| 88 ships the LIST slice; season/episode members via 143 + `type=3/4` | T2 |
| Smart/`plex_search` fenced with 173/179 | T2 Step 4 (refusal), Global Constraint 3 |
| The table-artifact correction | T4 Step 4 (and Step 1 — row 143's own cell carries the same reversed edge) |
| 89 as ONE task: (a) restriction first, (b) the tag write-back | T3 (Steps 10-12 in that order) |
| The swagger-banked PUT shapes, never guessed | T3 Steps 1-2, before any write code |
| 89b's live round-trip as the OPERATOR's checkbox | T4 Step 6 |
| OUT: 173/179, add_missing, multi-instance | Global Constraints 3-5; T3 Step 16 greps for the forbidden verbs |
| 143 proven RED on an episode fixture before 88 consumes it | T1 Steps 2-3, 7-8 |
| 88's branch proven against type=3/4 creation, MockPlex, no live writes | T2 Steps 7-10 |
| The gated-feature entry-point law for both, gate-off byte-identical | T2 Steps 1 (2 tests), T3 Step 8 (3 tests) |
| PUT bodies pinned against banked shapes, cited by section | T3 Steps 3, 5 (docstrings cite the capture file) |
| Opt-in default-off with the apply-flag posture | T3 Step 10 (`arr_tag_apply`), tests at Step 8 |
| Transport-pinned zero-request negatives | T3 Step 3 (`test_apply_tags_with_nothing_to_tag_makes_no_request`), Step 8 (`test_the_gate_off_pass_makes_no_arr_request_at_all`) |
| Suites from a T1-MEASURED baseline | T1 Step 1; Global Constraint 15 |
| Rows close, PR body plain with two operator checkboxes | T4 |
| 4 tasks, branch `feat/builder-level`, pbl1-pbl4 | Global Constraints 1, 16 |

**Type consistency.** `MemberLevel`/`MEMBER_LEVELS`/`LIBTYPE_FOR_LEVEL` are defined in T1 Step 4 and used under those exact names in T1 Steps 5, 9 and T2 Step 3. `build_owned_index(section, level)` is defined once (T1 Step 5) and called with the same signature in T1 Steps 11 and T2. `BuilderResult.level` is added last in the dataclass (T1 Step 9), so every positional `BuilderResult(ids=...)` in the tree is unaffected. `owned_index(level)` (T1 Step 11) is what `PlexSectionAccess.owned_index(level)` (T1 Step 10) delegates to and what `_run_one` calls (T1 Step 12, revised T2 Step 5). `GUID_KEY`/`external_id` are promoted once (T3 Step 7) and imported under those names in `arr_overrides.py` (T3 Step 11). `entry_ids_by_external_id`/`create_tag`/`apply_tags` are defined in T3 Step 5 and called in T3 Step 11 with matching signatures. `restricted_members`/`tag_members`'s keyword-only signatures match their engine call sites (T3 Step 12) and their tests (T3 Step 8).

**Placeholder scan.** No TBD, no "similar to Task N", no "add validation", no "write tests for the above". Every code step carries the code it changes, every command carries its expected output, and every test asserts a named behaviour rather than a shape.
