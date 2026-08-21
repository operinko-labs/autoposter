# Phase 3b: Chart and Award Collections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the IMDb chart and Oscars award collections in Plex, keeping their membership and rank order in sync with the source lists.

**Architecture:** Unlike Phase 3a's smart collections, these are regular list collections: membership is explicit and must be diffed on every pass. Each source resolves to an ordered list of IMDb ids, those ids are matched against a map built from one Plex call, and the collection's members are reconciled to match — adding what is new, removing what fell off, and preserving the source's own rank order.

**Tech Stack:** Python 3.13, httpx, PyYAML, plexapi 4.18.2, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18.

## Global Constraints

- **The specification is `docs/research/kometa-collections.md` §2, §3 and §6.** Where this plan and that document disagree, the document wins — report it if they do.
- **Never remove members when the source returned nothing or failed.** These collections use sync semantics: members not re-selected are removed. A failed chart fetch that yields an empty list would therefore *empty the collection*. Every reconcile path must treat an empty or errored source as "make no changes", not "remove everything". This is the single most dangerous property of the phase.
- **Only ever modify collections carrying our ownership label** (`collections.ownership_label`, default `autoposter`). The Movies library holds 305 collections and only a handful are ours.
- **Never delete a collection.** No deletion path exists in this phase, exactly as in Phase 3a.
- **Preserve source order.** Both families use `collection_order: custom`, meaning items appear in the order the builder returned them — IMDb rank order, not alphabetical.
- **Verified external sources** (tested against the live services, do not re-derive):
  - IMDb charts: one POST to `https://api.graphql.imdb.com/` per chart, body `{"query": "{ chartTitles(chart: { chartType: <TYPE> }, first: <N>) { edges { node { id } } total } }"}`, with headers `content-type: application/json` **and `x-imdb-client-name: imdb-web-next`**. Without that second header the API returns **403**.
  - Oscars: two GETs to `raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master/` — `event_validation.yml` and `events/ev0000003.yml` (302 KB, every ceremony year in one file).
- **IMDb's API response carries a non-commercial-use disclaimer.** This deployment is a private, single-operator install, which is within it. Do not build anything that redistributes the data.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it. `.reload()` is fine.
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- **Never hit the real IMDb API or GitHub in tests.** Use `httpx.MockTransport` and committed fixtures.
- Baseline on branch start: 606 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.
- **Read the python-plexapi documentation before using any of its functions.** Do not infer an API from a test double.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/autoposter/collections/charts.py` | Fetch IMDb chart ids over GraphQL. |
| `src/autoposter/collections/awards.py` | Fetch and resolve the Oscars award dataset. |
| `src/autoposter/collections/resolve.py` | Build the IMDb-id → Plex item map and resolve an ordered id list against it. |
| `src/autoposter/collections/lists.py` | Reconcile a list collection: create, set members, preserve order. |
| `src/autoposter/collections/__main__.py` | Extended to run the list collections alongside the smart ones. |
| `src/autoposter/config/schema.py` | `collections.charts` and `collections.awards` toggles. |

---

## Task 1: IMDb chart client

**Files:**
- Create: `src/autoposter/collections/charts.py`
- Create: `tests/fixtures/collections/imdb_chart.json`
- Test: `tests/test_collection_charts.py`

**Interfaces:**
- Produces: `CHARTS: dict[str, tuple[str, int]]` mapping chart key to `(chartType, first)`; `async fetch_chart(http: httpx.AsyncClient, chart: str) -> list[str]`.

**The chart table** (from `docs/research/kometa-collections.md` §3.1):

| Chart key | chartType | first |
|---|---|---|
| `popular_movies` | `MOST_POPULAR_MOVIES` | 100 |
| `top_movies` | `TOP_RATED_MOVIES` | 250 |
| `lowest_rated` | `LOWEST_RATED_MOVIES` | 100 |
| `popular_shows` | `MOST_POPULAR_TV_SHOWS` | 100 |
| `top_shows` | `TOP_RATED_TV_SHOWS` | 250 |

There is deliberately no `lowest_rated` equivalent for shows — IMDb has no such chart.

**Notes for the implementer:**

- Return ids in the order the API gave them. That order *is* the chart rank, and the collection preserves it.
- Raise on a non-2xx response or a malformed body rather than returning an empty list. An empty list reaching the reconciler would empty a collection; letting the exception propagate is what keeps that from happening.
- Kometa falls back to scraping the HTML chart page when GraphQL fails. That fallback is deliberately **not** implemented here: it is a second parser to maintain for a case where failing loudly is the safer outcome. Note this in the module docstring.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/collections/imdb_chart.json` — a trimmed real response shape:

```json
{
  "data": {
    "chartTitles": {
      "edges": [
        {"node": {"id": "tt0111161"}},
        {"node": {"id": "tt0068646"}},
        {"node": {"id": "tt0468569"}}
      ],
      "total": 250
    }
  }
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_collection_charts.py`:

```python
"""IMDb chart fetching.

Never touches the real API -- MockTransport only.
"""
import json
from pathlib import Path

import httpx
import pytest

from autoposter.collections.charts import CHARTS, fetch_chart

FIXTURE = json.loads(
    (Path("tests/fixtures/collections/imdb_chart.json")).read_text(encoding="utf-8")
)


def _transport(recorder, response=None):
    def handler(request):
        recorder.append(request)
        return response or httpx.Response(200, json=FIXTURE)

    return httpx.MockTransport(handler)


def test_every_configured_chart_has_a_type_and_a_count():
    assert set(CHARTS) == {
        "popular_movies", "top_movies", "lowest_rated", "popular_shows", "top_shows",
    }
    assert CHARTS["top_movies"] == ("TOP_RATED_MOVIES", 250)
    assert CHARTS["popular_movies"] == ("MOST_POPULAR_MOVIES", 100)


async def test_ids_are_returned_in_chart_order():
    """The order is the rank; the collection preserves it."""
    seen = []
    async with httpx.AsyncClient(transport=_transport(seen)) as http:
        ids = await fetch_chart(http, "top_movies")
    assert ids == ["tt0111161", "tt0068646", "tt0468569"]


async def test_the_client_name_header_is_sent():
    """Without it the API returns 403."""
    seen = []
    async with httpx.AsyncClient(transport=_transport(seen)) as http:
        await fetch_chart(http, "top_movies")
    assert seen[0].headers["x-imdb-client-name"] == "imdb-web-next"


async def test_the_query_carries_the_charts_type_and_count():
    seen = []
    async with httpx.AsyncClient(transport=_transport(seen)) as http:
        await fetch_chart(http, "lowest_rated")
    body = json.loads(seen[0].content)["query"]
    assert "LOWEST_RATED_MOVIES" in body
    assert "first: 100" in body


async def test_an_http_error_raises_rather_than_returning_nothing():
    """An empty list reaching the reconciler would empty a live collection."""
    seen = []
    transport = _transport(seen, httpx.Response(403, text="Forbidden"))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(Exception):
            await fetch_chart(http, "top_movies")


async def test_a_malformed_body_raises():
    seen = []
    transport = _transport(seen, httpx.Response(200, json={"data": {}}))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(Exception):
            await fetch_chart(http, "top_movies")


async def test_an_unknown_chart_raises():
    seen = []
    async with httpx.AsyncClient(transport=_transport(seen)) as http:
        with pytest.raises(KeyError):
            await fetch_chart(http, "not_a_chart")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_charts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.collections.charts'`

- [ ] **Step 4: Write the implementation**

Create `src/autoposter/collections/charts.py`:

```python
"""IMDb chart ids over the public GraphQL endpoint.

The ``x-imdb-client-name`` header is not optional -- without it the endpoint
returns 403.

Kometa falls back to scraping the HTML chart page when GraphQL fails. That
fallback is deliberately not reproduced here. These collections use sync
semantics, so an empty result would remove every member; failing loudly is
both safer and cheaper than maintaining a second parser for the failure case.
"""
import httpx

GRAPHQL_URL = "https://api.graphql.imdb.com/"
HEADERS = {"content-type": "application/json", "x-imdb-client-name": "imdb-web-next"}

# chart key -> (GraphQL chartType, how many to request)
CHARTS: dict[str, tuple[str, int]] = {
    "popular_movies": ("MOST_POPULAR_MOVIES", 100),
    "top_movies": ("TOP_RATED_MOVIES", 250),
    "lowest_rated": ("LOWEST_RATED_MOVIES", 100),
    "popular_shows": ("MOST_POPULAR_TV_SHOWS", 100),
    "top_shows": ("TOP_RATED_TV_SHOWS", 250),
}

QUERY = "{ chartTitles(chart: { chartType: %s }, first: %d) { edges { node { id } } total } }"


async def fetch_chart(http: httpx.AsyncClient, chart: str) -> list[str]:
    """The chart's IMDb ids, in rank order.

    Raises rather than returning an empty list on any failure -- see the
    module docstring.
    """
    chart_type, first = CHARTS[chart]
    response = await http.post(
        GRAPHQL_URL, headers=HEADERS, json={"query": QUERY % (chart_type, first)}
    )
    response.raise_for_status()
    payload = response.json()
    try:
        edges = payload["data"]["chartTitles"]["edges"]
    except (KeyError, TypeError) as error:
        raise ValueError("IMDb chart %r returned an unexpected body" % chart) from error
    ids = [edge["node"]["id"] for edge in edges]
    if not ids:
        raise ValueError("IMDb chart %r returned no ids" % chart)
    return ids
```

- [ ] **Step 5: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_collection_charts.py -v`
Expected: PASS — 7 passed

- [ ] **Step 6: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/collections/charts.py tests/test_collection_charts.py tests/fixtures/collections
git commit --no-gpg-sign -m "Fetch IMDb chart ids over GraphQL"
```

---

## Task 2: Oscars award dataset

**Files:**
- Create: `src/autoposter/collections/awards.py`
- Create: `tests/fixtures/collections/ev0000003.yml`
- Test: `tests/test_collection_awards.py`

**Interfaces:**
- Produces: `EVENT_ID = "ev0000003"`; `BEST_PICTURE: tuple[str, ...]`; `BEST_DIRECTOR: tuple[str, ...]`; `async fetch_event(http: httpx.AsyncClient, event_id: str = EVENT_ID) -> dict`; `winners_for_categories(event: dict, categories: tuple[str, ...]) -> list[str]`; `recent_years(event: dict, count: int = 5) -> list[str]`; `winners_for_year(event: dict, year: str) -> list[str]`.

**Dataset shape** — `{year: {award_group: {category: {"nominee": [...], "winner": [...]}}}}`, keys lower-cased. Years with no data appear as `{}`.

**Category filters** (from `defaults/award/oscars.yml`, §2.1):

```python
BEST_PICTURE = (
    "best motion picture of the year",
    "best picture",
    "best picture, production",
    "best picture, unique and artistic production",
)
BEST_DIRECTOR = (
    "best achievement in directing",
    "best director",
    "best director, comedy picture",
    "best director, dramatic picture",
)
```

Category naming has changed across ~95 ceremonies, which is why each award needs several variants.

**Notes for the implementer:**

- **`recent_years` must skip empty years.** The live dataset currently contains `'2027': {}`, a placeholder for a ceremony that has not happened. Including it would produce an empty `Oscars Winners 2027` collection. Selecting the five most recent *non-empty* years yields 2022–2026, which is exactly what production shows — this was verified against the real dataset.
- Return ids newest-year-first and, within a year, in the dataset's own category order. Kometa's static winner collections use dataset order (`collection_order: custom`).
- De-duplicate while preserving first occurrence: a film can win in more than one category or appear in several years' data.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/collections/ev0000003.yml` — a small stand-in with the real structure, including an empty placeholder year:

```yaml
'2027': {}
'2026':
  oscar:
    best achievement in directing:
      nominee: [tt14905854, tt30144839]
      winner: [tt30144839]
    best motion picture of the year:
      nominee: [tt30144839, tt31193180]
      winner: [tt31193180]
'2025':
  oscar:
    best achievement in directing:
      nominee: [tt1000001, tt1000002]
      winner: [tt1000002]
    best motion picture of the year:
      nominee: [tt1000002, tt1000003]
      winner: [tt1000003]
'2024':
  oscar:
    best picture:
      nominee: [tt2000001]
      winner: [tt2000001]
'2023':
  oscar:
    best picture:
      nominee: [tt3000001]
      winner: [tt3000001]
'2022':
  oscar:
    best picture:
      nominee: [tt4000001]
      winner: [tt4000001]
'2021':
  oscar:
    best picture:
      nominee: [tt5000001]
      winner: [tt5000001]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_collection_awards.py`:

```python
"""Oscars award dataset handling.

Never fetches the real dataset -- MockTransport and a committed fixture.
"""
from pathlib import Path

import httpx
import pytest

from autoposter.collections.awards import (
    BEST_DIRECTOR,
    BEST_PICTURE,
    fetch_event,
    recent_years,
    winners_for_categories,
    winners_for_year,
)

FIXTURE = Path("tests/fixtures/collections/ev0000003.yml").read_text(encoding="utf-8")


def _client(body=None, status=200):
    def handler(request):
        return httpx.Response(status, text=body if body is not None else FIXTURE)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_event_parses_the_dataset():
    async with _client() as http:
        event = await fetch_event(http)
    assert "2026" in event
    assert event["2026"]["oscar"]["best motion picture of the year"]["winner"] == ["tt31193180"]


async def test_fetch_event_raises_on_an_http_error():
    """An empty result would empty every Oscars collection."""
    async with _client(status=404) as http:
        with pytest.raises(Exception):
            await fetch_event(http)


async def test_recent_years_skips_empty_placeholder_years():
    """The live dataset carries '2027': {} for a ceremony that has not
    happened. Including it would create an empty Oscars Winners 2027."""
    async with _client() as http:
        event = await fetch_event(http)
    assert recent_years(event, count=5) == ["2026", "2025", "2024", "2023", "2022"]


async def test_recent_years_is_newest_first():
    async with _client() as http:
        event = await fetch_event(http)
    years = recent_years(event, count=3)
    assert years == sorted(years, reverse=True)


async def test_best_picture_winners_span_category_renames():
    """'best motion picture of the year' and 'best picture' are the same
    award under names used in different eras."""
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert "tt31193180" in winners  # 2026, "best motion picture of the year"
    assert "tt2000001" in winners  # 2024, "best picture"


async def test_best_director_winners_exclude_other_categories():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_DIRECTOR)
    assert "tt30144839" in winners
    assert "tt31193180" not in winners


async def test_nominees_are_never_included():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert "tt30144839" not in winners  # a 2026 best-picture nominee, not the winner


async def test_winners_are_newest_year_first():
    async with _client() as http:
        event = await fetch_event(http)
    winners = winners_for_categories(event, BEST_PICTURE)
    assert winners.index("tt31193180") < winners.index("tt1000003")


async def test_winners_for_year_returns_every_category():
    async with _client() as http:
        event = await fetch_event(http)
    assert sorted(winners_for_year(event, "2026")) == ["tt30144839", "tt31193180"]


async def test_duplicates_are_removed_keeping_first_occurrence():
    event = {
        "2026": {"oscar": {"best picture": {"winner": ["ttX"]},
                           "best motion picture of the year": {"winner": ["ttX"]}}},
    }
    assert winners_for_categories(event, BEST_PICTURE) == ["ttX"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_awards.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.collections.awards'`

- [ ] **Step 4: Write the implementation**

Create `src/autoposter/collections/awards.py`:

```python
"""Academy Awards winners, from the community IMDb-Awards dataset.

One file holds every ceremony year, so all seven Oscars collections resolve
from a single fetch. Shape is
``{year: {award_group: {category: {"nominee": [...], "winner": [...]}}}}``
with lower-cased keys; years with no data yet appear as ``{}``.
"""
import httpx
import yaml

EVENT_ID = "ev0000003"
BASE_URL = "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master"

# Category naming has changed across roughly 95 ceremonies, so each award
# needs every historical variant.
BEST_PICTURE = (
    "best motion picture of the year",
    "best picture",
    "best picture, production",
    "best picture, unique and artistic production",
)
BEST_DIRECTOR = (
    "best achievement in directing",
    "best director",
    "best director, comedy picture",
    "best director, dramatic picture",
)


async def fetch_event(http: httpx.AsyncClient, event_id: str = EVENT_ID) -> dict:
    """The whole event dataset. Raises rather than returning nothing."""
    response = await http.get("%s/events/%s.yml" % (BASE_URL, event_id))
    response.raise_for_status()
    event = yaml.safe_load(response.text)
    if not isinstance(event, dict) or not event:
        raise ValueError("award event %r returned an unexpected body" % event_id)
    return event


def recent_years(event: dict, count: int = 5) -> list[str]:
    """The most recent ceremony years that actually have data, newest first.

    Empty years must be skipped: the live dataset carries a placeholder for
    the next, unheld ceremony, and including it would create an empty
    collection for a year that has no winners.
    """
    return sorted((y for y in event if event[y]), reverse=True)[:count]


def _dedupe(ids: list[str]) -> list[str]:
    """Drop repeats, keeping first occurrence -- a film can win twice."""
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def winners_for_categories(event: dict, categories: tuple[str, ...]) -> list[str]:
    """Winners of the given categories across every year, newest first."""
    wanted = {c.lower() for c in categories}
    ids: list[str] = []
    for year in sorted((y for y in event if event[y]), reverse=True):
        for group in event[year].values():
            for category, entry in group.items():
                if category.lower() in wanted:
                    ids.extend(entry.get("winner") or [])
    return _dedupe(ids)


def winners_for_year(event: dict, year: str) -> list[str]:
    """Every category's winners for one ceremony year."""
    ids: list[str] = []
    for group in (event.get(year) or {}).values():
        for entry in group.values():
            ids.extend(entry.get("winner") or [])
    return _dedupe(ids)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_collection_awards.py -v`
Expected: PASS — 10 passed

- [ ] **Step 6: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/collections/awards.py tests/test_collection_awards.py tests/fixtures/collections
git commit --no-gpg-sign -m "Resolve Oscars winners from the community award dataset"
```

---

## Task 3: Library index and id resolution

**Files:**
- Create: `src/autoposter/collections/resolve.py`
- Test: `tests/test_collection_resolve.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `build_imdb_index(section) -> dict[str, object]`; `resolve_ids(index: dict[str, object], imdb_ids: list[str]) -> list[object]`.

**Notes for the implementer:**

- `section.all()` returns every item with its `guids` already populated — measured at 1,954 movies in 2.8 seconds against the production server, in a single call. There is no need to reload items individually, and doing so would turn one call into two thousand.
- A guid looks like `imdb://tt0322259`. Items carry `tmdb://` and `tvdb://` guids too; ignore those.
- `resolve_ids` preserves the order of `imdb_ids` and silently drops ids the library does not own. That is correct: a chart lists films regardless of whether you have them.
- An item with no imdb guid is simply absent from the index. Do not fall back to title matching — a wrong match puts the wrong film in a collection, which is worse than a missing one.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_resolve.py`:

```python
"""Mapping IMDb ids to owned Plex items."""
from autoposter.collections.resolve import build_imdb_index, resolve_ids


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, title, guids):
        self.title = title
        self.ratingKey = title
        self.guids = [FakeGuid(g) for g in guids]


class FakeSection:
    def __init__(self, items):
        self._items = items
        self.all_calls = 0

    def all(self):
        self.all_calls += 1
        return self._items


def _section():
    return FakeSection([
        FakeItem("Shawshank", ["imdb://tt0111161", "tmdb://278"]),
        FakeItem("Godfather", ["tmdb://238", "imdb://tt0068646"]),
        FakeItem("NoGuids", []),
        FakeItem("TmdbOnly", ["tmdb://999"]),
    ])


def test_the_index_is_built_from_one_call():
    """section.all() returns guids already populated; reloading per item
    would turn one request into thousands."""
    section = _section()
    build_imdb_index(section)
    assert section.all_calls == 1


def test_the_index_maps_imdb_ids_to_items():
    index = build_imdb_index(_section())
    assert index["tt0111161"].title == "Shawshank"
    assert index["tt0068646"].title == "Godfather"


def test_items_without_an_imdb_guid_are_absent():
    index = build_imdb_index(_section())
    assert len(index) == 2
    assert all(key.startswith("tt") for key in index)


def test_resolution_preserves_the_requested_order():
    """The order is the chart rank."""
    index = build_imdb_index(_section())
    items = resolve_ids(index, ["tt0068646", "tt0111161"])
    assert [i.title for i in items] == ["Godfather", "Shawshank"]


def test_unowned_ids_are_dropped_not_guessed():
    index = build_imdb_index(_section())
    items = resolve_ids(index, ["tt0111161", "tt9999999", "tt0068646"])
    assert [i.title for i in items] == ["Shawshank", "Godfather"]


def test_resolving_an_empty_list_yields_nothing():
    assert resolve_ids(build_imdb_index(_section()), []) == []


def test_duplicate_ids_resolve_once():
    index = build_imdb_index(_section())
    items = resolve_ids(index, ["tt0111161", "tt0111161"])
    assert len(items) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.collections.resolve'`

- [ ] **Step 3: Write the implementation**

Create `src/autoposter/collections/resolve.py`:

```python
"""Map IMDb ids onto the Plex items this library actually owns.

``section.all()`` returns every item with its guids already populated, so the
whole index costs one request -- measured at 1,954 movies in 2.8 seconds
against the production server. Reloading items individually to read their
guids would turn that into thousands of requests.
"""
import logging

logger = logging.getLogger(__name__)

IMDB_PREFIX = "imdb://"


def build_imdb_index(section) -> dict[str, object]:
    """``{imdb_id: plex_item}`` for everything in the library that has one."""
    index: dict[str, object] = {}
    for item in section.all():
        for guid in getattr(item, "guids", None) or []:
            value = getattr(guid, "id", "") or ""
            if value.startswith(IMDB_PREFIX):
                index.setdefault(value[len(IMDB_PREFIX):], item)
                break
    logger.debug("indexed %d item(s) by IMDb id", len(index))
    return index


def resolve_ids(index: dict[str, object], imdb_ids: list[str]) -> list[object]:
    """The owned items for these ids, in the order given.

    Ids the library does not own are dropped. There is deliberately no
    title-based fallback: a wrong match puts the wrong film in a collection,
    which is worse than a missing one.
    """
    items: list[object] = []
    seen: set[str] = set()
    for imdb_id in imdb_ids:
        if imdb_id in seen:
            continue
        seen.add(imdb_id)
        item = index.get(imdb_id)
        if item is not None:
            items.append(item)
    return items
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_collection_resolve.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/collections/resolve.py tests/test_collection_resolve.py
git commit --no-gpg-sign -m "Index library items by IMDb id and resolve chart ids"
```

---

## Task 4: List collection reconciler

The heart of the phase, and the part with real blast radius.

**Files:**
- Create: `src/autoposter/collections/lists.py`
- Test: `tests/test_collection_lists.py`

**Interfaces:**
- Consumes: `ManagedCollection` from models; the ownership-label helper from `reconcile.py` (reuse `_has_label`, promoting it to a public `has_label` rather than duplicating it).
- Produces: `async reconcile_list_collection(session, section, library: str, title: str, items: list, label: str, summary: str | None = None, dry_run: bool = True) -> list[str]`.

**Behaviour, in order:**

1. **If `items` is empty, make no changes at all and return a single explanatory action.** These collections use sync semantics, so an empty desired-set would remove every member. An empty list here means the source failed or returned nothing, and the only safe response is to do nothing. This is the most important rule in the phase.
2. Find the existing collection by title. If it exists **without** our label, record a conflict and return — never touch it.
3. If it does not exist, create it with the resolved items, apply the label, set `sortUpdate("custom")`, and set the summary.
4. If it exists, diff: add items that are missing, remove members no longer selected.
5. Ensure order matches the desired order, moving only items that are actually out of place, so an unchanged pass makes zero move calls.
6. Record or update the `ManagedCollection` row with `kind="manual"`.

**Notes for the implementer:**

- `section.createCollection(title=..., items=[...])` creates a regular collection. Do not pass `smart=True`.
- `collection.items()` returns current members in their current order. Compare by `ratingKey`.
- `moveItem(item, after=None)` moves an item; `after=None` moves it to the front. Walk the desired order and move only where the current sequence diverges.
- Reuse the ownership check from `reconcile.py` rather than writing a second one — a divergence between two ownership checks is exactly the kind of bug that would eventually touch a collection it should not.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_lists.py`:

```python
"""Reconciling regular (list) collections.

These use sync semantics: members not re-selected are removed. That makes an
empty desired-set catastrophic, which is why several tests here are about
doing nothing.
"""
from sqlalchemy import select

from autoposter.collections.lists import reconcile_list_collection
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"


class FakeItem:
    def __init__(self, key):
        self.ratingKey = key
        self.title = key


class FakeCollection:
    def __init__(self, title, items=(), labels=(LABEL,)):
        self.title = title
        self.ratingKey = "c-" + title
        self._items = list(items)
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.added = []
        self.removed = []
        self.moves = []
        self.sort_set = None
        self.summary_set = None
        self.reloaded = 0

    def reload(self):
        self.reloaded += 1

    @property
    def labels(self):
        return self._labels

    def items(self):
        return list(self._items)

    def addItems(self, items):
        self.added.extend(items)
        self._items.extend(items)

    def removeItems(self, items):
        self.removed.extend(items)
        for item in items:
            self._items = [i for i in self._items if i.ratingKey != item.ratingKey]

    def moveItem(self, item, after=None):
        self.moves.append((item.ratingKey, after.ratingKey if after else None))

    def sortUpdate(self, sort=None):
        self.sort_set = sort

    def editSummary(self, summary, locked=True):
        self.summary_set = summary

    def addLabel(self, labels, locked=True):
        self._labels.append(type("L", (), {"tag": labels})())


class FakeSection:
    def __init__(self, existing=()):
        self._existing = {c.title: c for c in existing}
        self.created = []

    def collections(self, **kw):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_lists.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.collections.lists'`

- [ ] **Step 3: Promote the ownership check**

In `src/autoposter/collections/reconcile.py`, rename `_has_label` to `has_label` and update its call site, so both reconcilers share one implementation. Two ownership checks that could drift apart is precisely the bug that would eventually modify a collection it should not.

- [ ] **Step 4: Write the implementation**

Create `src/autoposter/collections/lists.py`:

```python
"""Reconcile regular (list) collections against a source-ordered item list.

Unlike the smart collections in ``reconcile.py``, membership here is explicit
and diffed on every pass, with sync semantics: a member the source no longer
selects is removed.

That makes an empty desired-set dangerous. A failed chart fetch returning
nothing would, taken literally, empty a live collection -- so an empty list
means "make no changes", never "remove everything".
"""
import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.reconcile import has_label
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)


def _members_hash(items: list, summary: str | None) -> str:
    payload = "\x1f".join([summary or "", *[str(i.ratingKey) for i in items]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _enforce_order(collection, desired: list) -> int:
    """Move only the items that are out of place. Returns moves made."""
    current = [str(i.ratingKey) for i in collection.items()]
    wanted = [str(i.ratingKey) for i in desired]
    if current == wanted:
        return 0
    moves = 0
    previous = None
    for item in desired:
        current = [str(i.ratingKey) for i in collection.items()]
        position = current.index(str(item.ratingKey))
        expected = 0 if previous is None else current.index(str(previous.ratingKey)) + 1
        if position != expected:
            collection.moveItem(item, after=previous)
            moves += 1
        previous = item
    return moves


async def reconcile_list_collection(
    session: AsyncSession,
    section,
    library: str,
    title: str,
    items: list,
    label: str,
    summary: str | None = None,
    dry_run: bool = True,
) -> list[str]:
    """Bring one list collection in line with ``items`` (already in source order)."""
    if not items:
        return [
            "%r: source returned no items; leaving the collection untouched" % title
        ]

    existing = {collection.title: collection for collection in section.collections()}
    collection = existing.get(title)

    if collection is not None:
        collection.reload()
        if not has_label(collection, label):
            return [
                "conflict: %r exists without the %r label; leaving it untouched"
                % (title, label)
            ]

    record = (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()

    wanted = _members_hash(items, summary)
    if collection is not None and record is not None and record.definition_hash == wanted:
        return []

    if dry_run:
        return ["%s %r with %d item(s)" % (
            "would update" if collection else "would create", title, len(items))]

    actions: list[str] = []
    if collection is None:
        collection = section.createCollection(title=title, items=items, smart=False)
        collection.addLabel(label)
        collection.sortUpdate("custom")
        if summary:
            collection.editSummary(summary)
        actions.append("created %r with %d item(s)" % (title, len(items)))
    else:
        current = {str(i.ratingKey): i for i in collection.items()}
        desired = {str(i.ratingKey): i for i in items}

        adding = [i for key, i in desired.items() if key not in current]
        removing = [i for key, i in current.items() if key not in desired]
        if adding:
            collection.addItems(adding)
        if removing:
            collection.removeItems(removing)
        moves = _enforce_order(collection, items)
        if adding or removing or moves:
            actions.append(
                "updated %r: +%d -%d, %d move(s)" % (title, len(adding), len(removing), moves)
            )

    if record is None:
        record = ManagedCollection(
            library=library, title=title, kind="manual",
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

- [ ] **Step 5: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_collection_lists.py -v`
Expected: PASS — 10 passed

- [ ] **Step 6: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/collections tests/test_collection_lists.py
git commit --no-gpg-sign -m "Reconcile list collections with sync semantics"
```

---

## Task 5: Wiring and configuration

**Files:**
- Modify: `src/autoposter/config/schema.py`
- Modify: `config/autoposter.example.yaml`
- Create: `src/autoposter/collections/sources.py`
- Modify: `src/autoposter/collections/__main__.py`
- Modify: `deploy/README.md`
- Test: `tests/test_collection_sources.py`

**Interfaces:**
- Produces: `CHART_COLLECTIONS: dict[str, list[tuple[str, str]]]` mapping library type to `(collection title, chart key)`; `async build_all(http, session, section, library, library_type, label, config) -> list[str]`.

**The collection inventory** (from the live server, `docs/research/kometa-collections.md` §1):

| Library | Collection | Source |
|---|---|---|
| Movies | IMDb Popular | chart `popular_movies` |
| Movies | IMDb Top 250 | chart `top_movies` |
| Movies | IMDb Lowest Rated | chart `lowest_rated` |
| Movies | Oscars Best Picture Winners | award, `BEST_PICTURE` |
| Movies | Oscars Best Director Winners | award, `BEST_DIRECTOR` |
| Movies | Oscars Winners `<year>` ×5 | award, five most recent non-empty years |
| TV Shows | IMDb Popular | chart `popular_shows` |
| TV Shows | IMDb Top 250 | chart `top_shows` |

There is deliberately no `IMDb Lowest Rated` for shows — IMDb has no such chart, and the default omits it.

**Config to add** under the existing `collections:` section:

```yaml
  charts: true # IMDb Popular / Top 250 / Lowest Rated
  awards: true # Oscars winner collections
```

**Notes for the implementer:**

- Award collections apply to movies only.
- Fetch the award event **once** per run and reuse it for all seven collections — the whole dataset is one file, and re-fetching per collection would be seven downloads for identical data.
- A failure fetching one chart must not abort the others. Catch per source, log it, and pass an empty list to the reconciler — which then makes no changes, exactly as intended.
- Build the IMDb index once per library and share it across every collection in that library.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_sources.py`:

```python
"""The collection inventory and its per-source failure containment."""
import httpx

from autoposter.collections.sources import CHART_COLLECTIONS, build_all


def test_the_movie_inventory_matches_production():
    titles = [t for t, _ in CHART_COLLECTIONS["Movie"]]
    assert titles == ["IMDb Popular", "IMDb Top 250", "IMDb Lowest Rated"]


def test_shows_have_no_lowest_rated_chart():
    """IMDb has no lowest-rated TV chart; the default omits it deliberately."""
    titles = [t for t, _ in CHART_COLLECTIONS["Show"]]
    assert titles == ["IMDb Popular", "IMDb Top 250"]
    assert "IMDb Lowest Rated" not in titles


def test_show_charts_use_the_show_chart_keys():
    keys = dict(CHART_COLLECTIONS["Show"])
    assert keys["IMDb Top 250"] == "top_shows"
    assert keys["IMDb Popular"] == "popular_shows"
```

Add a test that one failing source does not prevent the others, using a `MockTransport` that returns 500 for the GraphQL endpoint and the award fixture for GitHub, asserting `build_all` returns actions mentioning the award collections and does not raise.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.collections.sources'`

- [ ] **Step 3: Write `sources.py`**

Create `src/autoposter/collections/sources.py` defining `CHART_COLLECTIONS`, and `build_all` which:

1. builds the IMDb index once for the section;
2. for each configured chart, fetches ids (catching and logging failures, yielding an empty list), resolves them, and calls `reconcile_list_collection`;
3. for movie libraries with awards enabled, fetches the event once, then reconciles the two static winner collections plus one per recent year, titled `Oscars Winners <year>`;
4. returns the concatenated actions.

Summaries come from the same translation source as Phase 3a's; use `"The winners of the %s Academy Awards."` for the year collections and the collection title as summary for charts only if a summary is genuinely known — otherwise pass `None` and leave whatever Plex has, rather than inventing text. Phase 3a shipped an invented summary that would have overwritten correct live text; do not repeat that.

- [ ] **Step 4: Extend the entry point**

In `src/autoposter/collections/__main__.py`, after the existing smart-collection reconciliation for each library, call `build_all` when `config.collections.charts` or `config.collections.awards` is enabled, passing an `httpx.AsyncClient`. Keep the existing per-library commit boundary.

- [ ] **Step 5: Run the full suite**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — 606 baseline plus roughly 40 new tests, 5 skipped

- [ ] **Step 6: Update the deployment docs**

In `deploy/README.md`, document the chart and award collections, that `apply_to_plex` still gates all writes, that a failed source leaves its collection untouched rather than emptying it, and that IMDb's API response carries a non-commercial-use disclaimer this deployment is within.

- [ ] **Step 7: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter config/autoposter.example.yaml deploy/README.md tests
git commit --no-gpg-sign -m "Build the IMDb chart and Oscars collections"
```

---

## Deferred

- **Collection posters.** Kometa downloads a static hosted image per collection from its Default-Images repo, preferring a local `/assets/<collection name>/poster.*` when one exists. Not implemented here; existing collection posters are left alone.
- **`Ratings Collections` separator.** A permanently-blank placeholder acting as a visual divider in Plex's alphabetised list.
- **Scheduling.** Still a one-shot entry point.
- **Arr sync.** Blocked on Radarr/Sonarr credentials and an outbound Arr client.
