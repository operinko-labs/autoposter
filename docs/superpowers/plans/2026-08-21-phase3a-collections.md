# Phase 3a: Common Sense Collections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile the Common Sense age-bucket collections in Plex as native smart collections, safely, without ever touching a collection we do not own.

**Architecture:** These collections are Plex-native *smart* collections: Plex evaluates their filter live, so there is no membership to diff and no per-item bookkeeping. The entire job is to compute each bucket's filter from the content ratings actually present in the library and create or update the collection when that filter changes. Ownership is established by a Plex label, which is also the hard boundary that stops us modifying anything else.

**Tech Stack:** Python 3.13, plexapi 4.18.2, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18, Alembic.

## Global Constraints

- **The specification is `docs/research/kometa-collections.md`.** It was derived from the pinned Kometa image (`kometateam/kometa` v2.4.8, digest `sha256:c58f6d4a...`) and verified against the live production server. Where this plan and that document disagree, the document wins — report it if they do.
- **Never delete a collection.** Not when it is empty, not when its filter matches nothing. Two production buckets (`Age 12+ Movies`, `Age 13+ Movies`) sit at 0 items and are expected to persist. Kometa's own `delete_below_minimum` defaults to false. Deletion is out of scope for this phase entirely — there is no code path that removes a collection.
- **Only ever modify collections carrying our ownership label.** The Movies library has 305 collections of which only 31 are managed: 269 are Plex/TMDB franchise collections, 5 are hand-made by the operator, and one belongs to another tool. Touching any of those is the worst failure this phase could have.
- **The bucket filter is data-dependent.** For each bucket, the filter is the bucket's own key (only if some library item literally carries that bare rating) plus every candidate in its `addons` list that some library item actually carries. Candidates absent from the library are omitted. The bucket table is `assets/collections/content_rating_cs.json`.
- **Titles and summaries are exact.** `Age <N>+ <Movie|Show>s` and `Not Rated <Movie|Show>s`; summary `<Movie|Show>s that are rated <N> according to the Common Sense Rating System.` Both were read off the live server.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it.
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- Baseline on branch start: 557 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.
- **Read the python-plexapi documentation before using any of its functions.** Do not infer an API from a test double. Verified signatures for this phase are listed in Task 3.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/autoposter/collections/buckets.py` | Derive each bucket's filter from present ratings. Pure functions, no Plex, no database. |
| `src/autoposter/collections/reconcile.py` | Create or update the smart collections in Plex, and record what we own. |
| `src/autoposter/db/models.py` | New `ManagedCollection` table. |
| `src/autoposter/config/schema.py` | The `collections` config section. |
| `tests/test_plexapi_collection_contract.py` | Asserts the plexapi methods we depend on exist with the shapes we expect. |

---

## Task 1: Bucket derivation

Pure logic: given the content ratings present in a library, produce each bucket's title, summary and filter values.

**Files:**
- Create: `src/autoposter/collections/__init__.py` (empty)
- Create: `src/autoposter/collections/buckets.py`
- Test: `tests/test_collection_buckets.py`

**Interfaces:**
- Produces: the frozen dataclass `Bucket(key: str, title: str, summary: str, values: tuple[str, ...])`; `load_table() -> dict`; `derive_buckets(present: set[str], library_type: str) -> list[Bucket]`.

**Notes for the implementer:**

- `library_type` is `"Movie"` or `"Show"`; titles pluralise it (`Age 17+ Movies`, `Age 17+ Shows`).
- The catch-all bucket has key `"other"`, title `Not Rated <Type>s`, and its values are every present rating claimed by no numbered bucket. Its summary is `<Type>s that are not rated according to the Common Sense Rating System.`
- A numbered bucket with no matching values is still returned, with an empty `values` tuple. Production keeps such collections rather than deleting them, and the caller needs to know they exist.
- Order the returned values deterministically (sorted), so an unchanged library produces an identical filter and therefore no write.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_buckets.py`:

```python
"""Common Sense bucket derivation.

The expected values below were taken from the live production server, not
invented -- see the validation table in assets/collections/PROVENANCE.md.
"""
from autoposter.collections.buckets import derive_buckets, load_table


def test_the_table_covers_all_eighteen_buckets():
    table = load_table()
    assert table["include"] == [str(n) for n in range(1, 19)]
    assert len(table["addons"]) == 18


def test_bucket_seventeen_reproduces_the_live_movies_filter():
    """Derived against the Movies library's real rating set, this is exactly
    what the live 'Age 17+ Movies' smart collection filters on."""
    present = {"17", "R", "TV-14", "TV-MA", "PG", "G"}
    buckets = {b.key: b for b in derive_buckets(present, "Movie")}
    assert sorted(buckets["17"].values) == ["17", "R", "TV-14", "TV-MA"]


def test_a_bare_key_is_only_included_when_the_library_carries_it():
    with_bare = {b.key: b for b in derive_buckets({"17", "R"}, "Movie")}
    without_bare = {b.key: b for b in derive_buckets({"R"}, "Movie")}
    assert "17" in with_bare["17"].values
    assert "17" not in without_bare["17"].values
    assert "R" in without_bare["17"].values


def test_candidates_absent_from_the_library_are_omitted():
    buckets = {b.key: b for b in derive_buckets({"R"}, "Movie")}
    assert buckets["17"].values == ("R",)


def test_titles_and_summaries_match_production_exactly():
    movies = {b.key: b for b in derive_buckets({"17"}, "Movie")}
    shows = {b.key: b for b in derive_buckets({"17"}, "Show")}
    assert movies["17"].title == "Age 17+ Movies"
    assert shows["17"].title == "Age 17+ Shows"
    assert movies["17"].summary == (
        "Movies that are rated 17 according to the Common Sense Rating System."
    )


def test_empty_buckets_are_still_returned():
    """Two production buckets sit at zero items and must persist; the caller
    cannot decide that without being told they exist."""
    buckets = {b.key: b for b in derive_buckets({"R"}, "Movie")}
    assert buckets["1"].values == ()
    assert len(buckets) == 19  # 18 numbered plus the catch-all


def test_the_catch_all_collects_ratings_no_bucket_claimed():
    """'tmdb' is the literal string a misconfiguration wrote into ten shows;
    on the live server it is the sole member of the Not Rated Shows filter."""
    buckets = {b.key: b for b in derive_buckets({"tmdb", "R"}, "Show")}
    assert buckets["other"].title == "Not Rated Shows"
    assert buckets["other"].values == ("tmdb",)


def test_finnish_ratings_fall_into_the_catch_all():
    """Faithful to the tool being replaced: no bucket claims fi/K-* ratings,
    which is why Not Rated Movies holds 28 items in production."""
    buckets = {b.key: b for b in derive_buckets({"fi/K-16", "fi/S", "R"}, "Movie")}
    assert sorted(buckets["other"].values) == ["fi/K-16", "fi/S"]


def test_values_are_sorted_so_an_unchanged_library_produces_an_identical_filter():
    a = derive_buckets({"R", "TV-14", "17"}, "Movie")
    b = derive_buckets({"17", "TV-14", "R"}, "Movie")
    assert [x.values for x in a] == [x.values for x in b]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_buckets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.collections'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/collections/__init__.py` empty, and `src/autoposter/collections/buckets.py`:

```python
"""Common Sense age-bucket derivation.

The filter for a bucket is not a fixed list. It is the bucket's own key --
included only when some library item literally carries that bare rating --
plus every candidate from the bucket's `addons` list that the library
actually carries. Candidates nothing carries are left out, so the same
bucket yields different filters on different libraries.

Verified against production: deriving this way reproduces the live filters
for Movies bucket 17, Shows bucket 14 and the Shows catch-all exactly.
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

TABLE_PATH = (
    Path(__file__).resolve().parents[3] / "assets" / "collections" / "content_rating_cs.json"
)

SUMMARY = "%ss that are rated %s according to the Common Sense Rating System."
OTHER_SUMMARY = "%ss that are not rated according to the Common Sense Rating System."


@dataclass(frozen=True)
class Bucket:
    """One age bucket, resolved against a specific library."""

    key: str
    title: str
    summary: str
    values: tuple[str, ...]


@lru_cache(maxsize=1)
def load_table() -> dict:
    """The static bucket/candidate table, generated from Kometa's defaults."""
    with open(TABLE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def derive_buckets(present: set[str], library_type: str) -> list[Bucket]:
    """Resolve every bucket against the ratings this library actually has.

    Buckets that match nothing are still returned with empty ``values``:
    production keeps such collections rather than deleting them, and the
    caller cannot make that decision without knowing they exist.
    """
    table = load_table()
    buckets: list[Bucket] = []
    claimed: set[str] = set()

    for key in table["include"]:
        values = [key] if key in present else []
        values += [a for a in table["addons"][key] if a in present and a != key]
        claimed.update(values)
        buckets.append(
            Bucket(
                key=key,
                title="Age %s+ %ss" % (key, library_type),
                summary=SUMMARY % (library_type, key),
                values=tuple(sorted(values)),
            )
        )

    buckets.append(
        Bucket(
            key="other",
            title="Not Rated %ss" % library_type,
            summary=OTHER_SUMMARY % library_type,
            values=tuple(sorted(present - claimed)),
        )
    )
    return buckets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_collection_buckets.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/collections tests/test_collection_buckets.py
git commit --no-gpg-sign -m "Derive Common Sense buckets from a library's own ratings"
```

---

## Task 2: Managed collection records

**Files:**
- Modify: `src/autoposter/db/models.py`
- Create: one Alembic migration under `alembic/versions/`
- Test: `tests/test_managed_collections.py`

**Interfaces:**
- Produces: `ManagedCollection` with columns `id`, `library` (`String(128)`), `title` (`String(255)`), `kind` (`String(16)`, `smart` or `manual`), `plex_rating_key` (`String(32)`, nullable), `definition_hash` (`String(64)`), `created_at`, `updated_at`; unique constraint on `(library, title)`.

**Notes for the implementer:**

- `definition_hash` is what makes reconciliation cheap: hash the desired filter and summary, and skip the Plex write when it is unchanged. Without it every pass rewrites 51 collections for no reason.
- Follow the same clean-database migration sequence as previous phases — `tests/conftest.py` calls `create_all` against the same database, so autogenerating after a test run yields an empty no-op migration that silently creates nothing on deploy. Reset (`docker compose down` **without** `-v`), `alembic upgrade head`, then autogenerate, then confirm `upgrade()` contains a real `op.create_table`. `tests/test_migrations.py` guards this and must pass.
- This is a new table, so it starts empty and needs no `server_default` backfill concerns.

- [ ] **Step 1: Write the failing test**

Create `tests/test_managed_collections.py`:

```python
"""The record of which collections we own."""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoposter.db.models import ManagedCollection


async def test_a_managed_collection_round_trips(session):
    row = ManagedCollection(
        library="Movies", title="Age 17+ Movies", kind="smart",
        plex_rating_key="123", definition_hash="a" * 64,
    )
    session.add(row)
    await session.flush()
    loaded = (
        await session.execute(select(ManagedCollection).where(ManagedCollection.id == row.id))
    ).scalar_one()
    assert loaded.title == "Age 17+ Movies"
    assert loaded.kind == "smart"


async def test_the_same_title_cannot_be_managed_twice_in_one_library(session):
    session.add(ManagedCollection(library="Movies", title="Age 17+ Movies",
                                  kind="smart", definition_hash="a"))
    await session.flush()
    session.add(ManagedCollection(library="Movies", title="Age 17+ Movies",
                                  kind="smart", definition_hash="b"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_the_same_title_in_two_libraries_is_fine(session):
    """'IMDb Top 250' legitimately exists in both libraries."""
    session.add(ManagedCollection(library="Movies", title="IMDb Top 250",
                                  kind="manual", definition_hash="a"))
    session.add(ManagedCollection(library="TV Shows", title="IMDb Top 250",
                                  kind="manual", definition_hash="a"))
    await session.flush()
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert len(rows) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_managed_collections.py -v`
Expected: FAIL with `ImportError: cannot import name 'ManagedCollection'`

- [ ] **Step 3: Add the model**

In `src/autoposter/db/models.py`:

```python
class ManagedCollection(Base):
    """A collection this service owns.

    Ownership is the whole point of this table. The Movies library holds 305
    collections and only a small fraction are ours -- the rest are Plex's own
    franchise collections, another tool's, or hand-made by the operator.
    Nothing outside this table is ever modified.
    """

    __tablename__ = "managed_collections"
    __table_args__ = (
        UniqueConstraint("library", "title", name="uq_managed_collection_library_title"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    library: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(255))
    # smart | manual
    kind: Mapped[str] = mapped_column(String(16), default="smart")
    plex_rating_key: Mapped[str | None] = mapped_column(String(32))
    # Hash of the desired filter and summary, so an unchanged pass writes nothing.
    definition_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Generate the migration against a clean database**

```bash
docker compose down && docker compose up -d postgres && sleep 8
export AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
rtk proxy python -m alembic upgrade head
rtk proxy python -m alembic revision --autogenerate -m "managed collections"
```

Open the generated file and confirm `upgrade()` contains a real `op.create_table('managed_collections', ...)` with the unique constraint. A body of `pass` means the diff was taken against a database that already had the table — start over from the reset.

- [ ] **Step 5: Apply, test and commit**

```bash
rtk proxy python -m alembic upgrade head
rtk proxy python -m pytest tests/test_managed_collections.py tests/test_migrations.py -v
rtk proxy ruff check src tests
git add src/autoposter/db/models.py alembic tests/test_managed_collections.py
git commit --no-gpg-sign -m "Record which collections this service owns"
```

---

## Task 3: plexapi contract test

Before writing code against plexapi, pin the API surface. An earlier phase shipped three separate bugs where a hand-written test double implemented an API that plexapi does not actually have, and each one only surfaced against the real server.

**Files:**
- Create: `tests/test_plexapi_collection_contract.py`

**Interfaces:** none — this task adds only a test.

**Verified signatures** (checked against the installed plexapi 4.18.2):

```
LibrarySection.createCollection(title, items=None, smart=False, limit=None,
                                libtype=None, sort=None, filters=None, **kwargs)
LibrarySection.listFilterChoices(field, libtype=None)
LibrarySection.collections(**kwargs)
LibrarySection.collection(title)
Collection.updateFilters(libtype=None, limit=None, sort=None, filters=None, **kwargs)
Collection.filters()
Collection.addLabel(labels, locked=True)
Collection.removeLabel(labels, locked=True)
Collection.editSummary(summary, locked=True)
```

Note `Collection.smart` is set by `_loadData` rather than declared as a class attribute, so a contract test must accept either.

- [ ] **Step 1: Write the test**

Create `tests/test_plexapi_collection_contract.py`:

```python
"""Pins the plexapi surface this phase depends on.

Not a test of our code. It exists because a hand-written test double will
happily implement an API that plexapi does not have -- which has already
cost this project three bugs that only appeared against a real server. This
asserts against the real classes, offline.
"""
import inspect

import pytest
from plexapi.collection import Collection
from plexapi.library import LibrarySection


@pytest.mark.parametrize(
    "name,required",
    [
        ("createCollection", ["title", "smart", "libtype", "sort", "filters"]),
        ("listFilterChoices", ["field", "libtype"]),
        ("collection", ["title"]),
    ],
)
def test_library_section_methods_take_the_parameters_we_pass(name, required):
    method = getattr(LibrarySection, name)
    params = inspect.signature(method).parameters
    for parameter in required:
        assert parameter in params, "%s lost its %r parameter" % (name, parameter)


@pytest.mark.parametrize(
    "name,required",
    [
        ("updateFilters", ["libtype", "sort", "filters"]),
        ("addLabel", ["labels"]),
        ("removeLabel", ["labels"]),
        ("editSummary", ["summary"]),
    ],
)
def test_collection_methods_take_the_parameters_we_pass(name, required):
    method = getattr(Collection, name)
    params = inspect.signature(method).parameters
    for parameter in required:
        assert parameter in params, "%s lost its %r parameter" % (name, parameter)


def test_collection_exposes_filters_and_smart():
    """`smart` is assigned in _loadData rather than declared, so accept either."""
    assert hasattr(Collection, "filters")
    assert "smart" in inspect.getsource(Collection._loadData) or hasattr(Collection, "smart")


def test_collections_are_listable_from_a_section():
    assert callable(LibrarySection.collections)


def test_collection_delete_exists_but_we_never_call_it():
    """Deleting a collection is out of scope for this phase. The method is
    pinned here so that if a later phase adds deletion, it is a deliberate
    change against a known API rather than an accident."""
    assert callable(Collection.delete)
```

- [ ] **Step 2: Run it**

Run: `rtk proxy python -m pytest tests/test_plexapi_collection_contract.py -v`
Expected: PASS — 12 passed

- [ ] **Step 3: Commit**

```bash
rtk proxy ruff check src tests
git add tests/test_plexapi_collection_contract.py
git commit --no-gpg-sign -m "Pin the plexapi collection surface this phase uses"
```

---

## Task 4: Reconciler

**Files:**
- Create: `src/autoposter/collections/reconcile.py`
- Test: `tests/test_collection_reconcile.py`

**Interfaces:**
- Consumes: `Bucket`, `derive_buckets` from `buckets.py`; `ManagedCollection` from models.
- Produces: `definition_hash(bucket: Bucket) -> str`; `async reconcile_content_ratings(session, section, library_type: str, label: str, dry_run: bool = True) -> list[str]` returning a human-readable list of the actions taken or that would be taken.

**Notes for the implementer:**

- Read the library's present ratings with `section.listFilterChoices("contentRating")`; each choice's `.title` is the rating string.
- The Plex filter shape for a bucket is `{"contentRating": list(bucket.values)}` passed as `filters=` — plexapi builds the OR itself. Sort is `originallyAvailableAt:desc`, matching production. `libtype` is `"movie"` or `"show"`.
- **A bucket with no values must not create a collection.** An empty filter would match everything, which would be catastrophic — an `Age 1+ Movies` collection containing the whole library. If the collection already exists and the bucket is now empty, leave it exactly as it is; do not update and do not delete.
- Before touching an existing collection, verify it carries the ownership label. If a collection with the target title exists **without** our label, do not modify it — record a conflict in the returned actions and move on. That is the case where an operator has hand-made a collection with a colliding name, and silently overwriting it would be the single worst thing this phase could do.
- Skip the Plex write entirely when `definition_hash` matches the stored one.
- `dry_run=True` is the default: compute and report every action without performing any of it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_reconcile.py`:

```python
"""Reconciling Common Sense smart collections.

The fakes here mirror plexapi's real signatures, which are pinned separately
in tests/test_plexapi_collection_contract.py.
"""
from sqlalchemy import select

from autoposter.collections.reconcile import reconcile_content_ratings
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


class FakeChoice:
    def __init__(self, title):
        self.title = title


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.updated_filters = None
        self.summary_set = None
        self.labels_added = []

    @property
    def labels(self):
        return self._labels

    def updateFilters(self, libtype=None, limit=None, sort=None, filters=None, **kw):
        self.updated_filters = filters

    def editSummary(self, summary, locked=True):
        self.summary_set = summary

    def addLabel(self, labels, locked=True):
        self.labels_added.append(labels)


class FakeSection:
    def __init__(self, ratings, existing=()):
        self._ratings = list(ratings)
        self._existing = {c.title: c for c in existing}
        self.created = []

    def listFilterChoices(self, field, libtype=None):
        assert field == "contentRating"
        return [FakeChoice(r) for r in self._ratings]

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, limit=None,
                         libtype=None, sort=None, filters=None, **kw):
        self.created.append((title, smart, libtype, sort, filters))
        collection = FakeCollection(title, labels=[LABEL], rating_key=str(len(self.created)))
        self._existing[title] = collection
        return collection


async def test_dry_run_performs_no_writes(session):
    section = FakeSection({"R", "17"})
    actions = await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=True)
    assert section.created == []
    assert any("Age 17+ Movies" in a for a in actions)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == []


async def test_creates_a_smart_collection_with_the_derived_filter(session):
    section = FakeSection({"R", "17"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    created = {c[0]: c for c in section.created}
    title, smart, libtype, sort, filters = created["Age 17+ Movies"]
    assert smart is True
    assert libtype == "movie"
    assert sort == "originallyAvailableAt:desc"
    assert sorted(filters["contentRating"]) == ["17", "R"]


async def test_an_empty_bucket_creates_nothing(session):
    """An empty filter would match the entire library."""
    section = FakeSection({"R"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    titles = [c[0] for c in section.created]
    assert "Age 1+ Movies" not in titles
    assert "Age 17+ Movies" in titles


async def test_a_second_pass_over_an_unchanged_library_writes_nothing(session):
    section = FakeSection({"R", "17"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    first = len(section.created)
    for collection in section.collections():
        collection.updated_filters = None
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    assert len(section.created) == first
    assert all(c.updated_filters is None for c in section.collections())


async def test_a_changed_rating_set_updates_the_existing_filter(session):
    section = FakeSection({"R"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    section._ratings.append("TV-MA")
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    collection = section._existing["Age 17+ Movies"]
    assert sorted(collection.updated_filters["contentRating"]) == ["R", "TV-MA"]


async def test_an_unlabelled_collection_with_a_colliding_title_is_never_touched(session):
    """The operator has hand-made collections. Overwriting one because its
    name collides would be the worst failure this phase could have."""
    theirs = FakeCollection("Age 17+ Movies", labels=["something-else"])
    section = FakeSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    assert theirs.updated_filters is None
    assert theirs.summary_set is None
    assert any("conflict" in a.lower() for a in actions)


async def test_nothing_is_ever_deleted(session):
    """There is no deletion path in this phase at all."""
    import inspect

    from autoposter.collections import reconcile

    assert ".delete(" not in inspect.getsource(reconcile)


async def test_managed_collections_are_recorded(session):
    section = FakeSection({"R", "17"})
    await reconcile_content_ratings(session, section, "Movie", LABEL, dry_run=False)
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    titles = {r.title for r in rows}
    assert "Age 17+ Movies" in titles
    assert all(r.library == "Movie" and r.kind == "smart" for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_reconcile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.collections.reconcile'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/collections/reconcile.py`:

```python
"""Create and update the Common Sense smart collections in Plex.

These are Plex-native smart collections, so there is no membership to
maintain -- Plex evaluates the filter live. The entire job is to keep each
collection's filter matching what the library's ratings imply.

Two safety rules shape this module. Nothing is ever deleted, and nothing
without our ownership label is ever modified: the Movies library holds 305
collections of which only a handful are ours, the rest being Plex's own
franchise collections, another tool's, or hand-made by the operator.
"""
import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.buckets import Bucket, derive_buckets
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)

SORT = "originallyAvailableAt:desc"
LIBTYPES = {"Movie": "movie", "Show": "show"}


def definition_hash(bucket: Bucket) -> str:
    """Hash the desired filter and summary, so an unchanged pass writes nothing."""
    payload = "\x1f".join([bucket.title, bucket.summary, *bucket.values])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _has_label(collection, label: str) -> bool:
    return any(tag.tag == label for tag in (getattr(collection, "labels", None) or []))


async def reconcile_content_ratings(
    session: AsyncSession,
    section,
    library_type: str,
    label: str,
    dry_run: bool = True,
) -> list[str]:
    """Bring this library's Common Sense collections in line with its ratings.

    Returns a description of every action taken -- or, under ``dry_run``,
    every action that would be taken.
    """
    present = {choice.title for choice in section.listFilterChoices("contentRating")}
    existing = {collection.title: collection for collection in section.collections()}
    libtype = LIBTYPES[library_type]

    stored = {
        row.title: row
        for row in (
            await session.execute(
                select(ManagedCollection).where(ManagedCollection.library == library_type)
            )
        ).scalars()
    }

    actions: list[str] = []
    for bucket in derive_buckets(present, library_type):
        if not bucket.values:
            # An empty filter matches the entire library. Never create one,
            # and leave any existing collection exactly as it is.
            continue

        wanted = definition_hash(bucket)
        collection = existing.get(bucket.title)

        if collection is not None and not _has_label(collection, label):
            actions.append(
                "conflict: %r exists without the %r label; leaving it untouched"
                % (bucket.title, label)
            )
            continue

        record = stored.get(bucket.title)
        if collection is not None and record is not None and record.definition_hash == wanted:
            continue

        if dry_run:
            actions.append(
                "%s %r -> %s"
                % ("would update" if collection else "would create",
                   bucket.title, ", ".join(bucket.values))
            )
            continue

        if collection is None:
            collection = section.createCollection(
                title=bucket.title, smart=True, libtype=libtype, sort=SORT,
                filters={"contentRating": list(bucket.values)},
            )
            collection.addLabel(label)
            actions.append("created %r" % bucket.title)
        else:
            collection.updateFilters(
                libtype=libtype, sort=SORT,
                filters={"contentRating": list(bucket.values)},
            )
            actions.append("updated %r" % bucket.title)

        collection.editSummary(bucket.summary)

        if record is None:
            record = ManagedCollection(
                library=library_type, title=bucket.title, kind="smart",
                plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                definition_hash=wanted,
            )
            session.add(record)
        else:
            record.definition_hash = wanted
            record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")

    await session.flush()
    return actions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_collection_reconcile.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/collections/reconcile.py tests/test_collection_reconcile.py
git commit --no-gpg-sign -m "Reconcile Common Sense smart collections safely"
```

---

## Task 5: Configuration and entry point

**Files:**
- Modify: `src/autoposter/config/schema.py`
- Modify: `config/autoposter.example.yaml`
- Create: `src/autoposter/collections/__main__.py`
- Modify: `deploy/README.md`
- Test: `tests/test_collection_config.py`

**Interfaces:**
- Produces: `CollectionsConfig` with `enabled: bool = True`, `apply_to_plex: bool = False`, `ownership_label: str = "autoposter"`, `libraries: list[str] = ["Movies", "TV Shows"]`.

**Notes for the implementer:**

- `apply_to_plex` defaults to **false**, mirroring `operations.write_to_plex` and `badges.upload_to_plex`. Reconciliation runs and reports; nothing is written until the operator opts in.
- `ownership_label` is the safety boundary. The tool being replaced uses `Kometa`; we default to `autoposter` so the two never contend for the same collections. Changing it after a run would orphan everything previously created, so document that.
- `__main__.py` is a one-shot entry point (`python -m autoposter.collections`) that connects to Plex, reconciles each configured library, and prints the actions. Scheduling belongs to a later phase; this makes the phase usable and inspectable now.
- The library type for a section comes from its `type` attribute: `movie` or `show`. Map to `"Movie"` / `"Show"` for `derive_buckets`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_config.py`:

```python
"""Collections configuration."""
from autoposter.config.schema import Config


def test_collections_default_to_reporting_without_writing():
    """Consistent with operations.write_to_plex and badges.upload_to_plex:
    nothing reaches the live server until the operator opts in."""
    config = Config()
    assert config.collections.enabled is True
    assert config.collections.apply_to_plex is False


def test_the_ownership_label_defaults_to_our_own_name():
    """It must not be 'Kometa' -- the tool being replaced uses that label,
    and sharing it would make both tools claim the same collections."""
    config = Config()
    assert config.collections.ownership_label == "autoposter"
    assert config.collections.ownership_label != "Kometa"


def test_both_libraries_are_configured_by_default():
    assert Config().collections.libraries == ["Movies", "TV Shows"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'collections'`

- [ ] **Step 3: Add the config section**

Add a `CollectionsConfig` model to `src/autoposter/config/schema.py` alongside the existing section models, with the four fields and defaults above, and reference it from `Config` as `collections: CollectionsConfig = CollectionsConfig()`. Mirror it into `config/autoposter.example.yaml`:

```yaml
collections:
  enabled: true # reconcile the Common Sense age-bucket collections
  apply_to_plex: false # dry run by default: report the actions, change nothing
  ownership_label: autoposter # only collections carrying this label are ever modified
  libraries: # Plex library names to reconcile
    - Movies
    - TV Shows
```

- [ ] **Step 4: Write the entry point**

Create `src/autoposter/collections/__main__.py`:

```python
"""One-shot collection reconciliation: ``python -m autoposter.collections``.

Scheduling belongs to a later phase. This exists so the reconciliation can be
run and inspected now, which matters because the first real run against a
library is the one worth reading carefully before anything is written.
"""
import asyncio
import logging

from autoposter.config.loader import load_config
from autoposter.collections.reconcile import reconcile_content_ratings
from autoposter.db import session_scope
from autoposter.plex.client import build_server

LIBRARY_TYPES = {"movie": "Movie", "show": "Show"}

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()
    if not config.collections.enabled:
        logger.info("collections are disabled in config")
        return

    server = build_server(config)
    async with session_scope() as session:
        for name in config.collections.libraries:
            section = server.library.section(name)
            library_type = LIBRARY_TYPES.get(section.type)
            if library_type is None:
                logger.info("skipping %r: unsupported library type %r", name, section.type)
                continue

            actions = await reconcile_content_ratings(
                session, section, library_type,
                config.collections.ownership_label,
                dry_run=not config.collections.apply_to_plex,
            )
            logger.info("%s: %d action(s)", name, len(actions))
            for action in actions:
                logger.info("   %s", action)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
```

Adapt `load_config`, `session_scope` and `build_server` to whatever the existing modules actually expose — check `src/autoposter/app.py` for how the running service builds each of these, and reuse those helpers rather than inventing new ones. If a helper does not exist in that shape, use the equivalent that does; do not add a new abstraction for this.

- [ ] **Step 5: Run the tests**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — 557 baseline plus roughly 32 new tests, 5 skipped

- [ ] **Step 6: Update the deployment docs**

In `deploy/README.md`, document: that collection reconciliation is manual in this phase (`python -m autoposter.collections`), that `apply_to_plex` defaults to false and what the dry-run output looks like, that `ownership_label` is the safety boundary and changing it after a run orphans everything previously created, and that **no collection is ever deleted by this service** — including empty ones, which are expected and normal.

- [ ] **Step 7: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter config/autoposter.example.yaml deploy/README.md tests
git commit --no-gpg-sign -m "Add collections configuration and a one-shot reconcile entry point"
```

---

## Deferred to later phases

- **IMDb chart and Oscars collections** (`IMDb Popular`, `IMDb Top 250`, `IMDb Lowest Rated`, the seven Oscars collections). These are regular collections needing a full membership diff with sync semantics, plus external data sources — IMDb's GraphQL API and a cached GitHub award dataset. Genuinely separate work from smart collections, which need no membership handling at all.
- **The `Ratings Collections` separator.** A permanently-blank placeholder that exists only as a visual divider in Plex's alphabetised list. Trivial, but it belongs with the collections it separates.
- **Scheduling.** This phase ships a one-shot entry point; the periodic scheduler covers the collections diff, the ratings drift sweep and asset cleanup together.
- **Arr sync.** Blocked: it needs outbound Radarr and Sonarr API access, and the service currently has only inbound webhook intake — there is no Arr client and no `radarr`/`sonarr` entry in the config schema. It needs base URLs and API keys from the operator.
- **Adopting the existing Kometa collections.** The 51 live collections carry the `Kometa` label, not ours. Whether to relabel them (taking ownership in place) or create ours alongside is an operator decision, not one to make silently — relabelling touches 51 live collections.
