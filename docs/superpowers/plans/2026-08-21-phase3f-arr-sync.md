# Phase 3f: Radarr and Sonarr Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register Plex items that Radarr and Sonarr do not know about, in place and without downloading anything, and catch any Plex item this service has never processed.

**Architecture:** A small read-mostly client per service. The sync lists what each service already has, compares against Plex by external id, and registers the difference against the file already on disk. A separate safety net enqueues any Plex item with no `media_items` row, so the library converges even when a webhook is missed.

**Tech Stack:** Python 3.13, httpx, plexapi 4.18.2, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18.

## Global Constraints

- **Never trigger a search or a download.** Radarr's `addOptions.searchForMovie` and Sonarr's `addOptions.searchForMissingEpisodes` must both be `false` on every request this service makes. Getting that wrong would start grabbing releases for the whole library. This is the single most dangerous flag in the phase and it deserves its own test.
- **Never remove anything from Radarr or Sonarr, and never modify an item they already have.** This phase only adds items they are missing. There is no delete or update path.
- **Adding is off by default** (`add_existing: false`) and additionally gated by a dry run, exactly as every other outward-facing write in this project.
- **Path mapping is mandatory and case-sensitive.** Verified live: Plex mounts `/mnt/Media/Movies` and `/mnt/Media/TV` (capital M); Radarr and Sonarr use `/mnt/media/Movies` and `/mnt/media/TV` (lowercase). Registering an unmapped path would point the service at a directory it cannot read.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it; `.reload()` is fine.
- **Nothing blocks the event loop.**
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- **No test may make a real outbound request** — an autouse fixture in `tests/conftest.py` enforces this. Use `httpx.MockTransport`.
- Baseline on branch start: 892 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.

## Verified against the live services and the published API specs

These were checked directly; do not re-derive or contradict them.

- **Authentication is the `X-Api-Key` request header** (both services also accept an `apikey` query parameter; use the header).
- **Radarr 6.4.0.10523**, 1,982 movies. Quality profiles include `HD Bluray + WEB` = id **7**. Single root folder `/mnt/media/Movies`, accessible.
- **Sonarr 4.0.18.2978**, 286 series. Quality profiles include `WEB-1080p` = id **7**. Single root folder `/mnt/media/TV`, accessible.
- Endpoints, from the projects' own OpenAPI documents: `GET|POST /api/v3/movie`, `GET|POST /api/v3/series`, `GET /api/v3/qualityprofile`, `GET /api/v3/rootfolder`.
- `MovieResource` carries `tmdbId`, `title`, `qualityProfileId`, `rootFolderPath`, `monitored`, `minimumAvailability`, `path`, `addOptions`. `SeriesResource` carries `tvdbId`, `title`, `qualityProfileId`, `rootFolderPath`, `monitored`, `path`, `addOptions`, `seasonFolder`, `seriesType`.
- `AddMovieOptions` has `searchForMovie`; `AddSeriesOptions` has `searchForMissingEpisodes`.
- **The current gap is small**: 0 movies in Plex are missing from Radarr; **2 series are missing from Sonarr** (`Limitless`, `The Moomins`), and one show carries no TVDB id at all so it can never be matched. Expect the first real run to add two series and nothing else.

## Configuration this mirrors

The tool being replaced is configured with `add_existing: true`, `add_missing: false`, `upgrade_existing: false`, `monitor: true` (Radarr) / `monitor: all` (Sonarr), `availability: announced`, `series_type: standard`, `season_folder: true`, and the path mapping above. `add_missing` — adding items Plex does *not* have, which would download them — is **out of scope for this phase entirely**.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/autoposter/arr/client.py` | HTTP client for one service: list, resolve a quality profile, add an item. |
| `src/autoposter/arr/paths.py` | Plex path to service path mapping. Pure functions. |
| `src/autoposter/arr/sync.py` | Compare Plex against a service and register the difference. |
| `src/autoposter/scheduler/jobs.py` | The scheduled job. |
| `src/autoposter/config/schema.py` | The `radarr` and `sonarr` config sections. |

---

## Task 1: Path mapping

**Files:**
- Create: `src/autoposter/arr/__init__.py` (empty)
- Create: `src/autoposter/arr/paths.py`
- Test: `tests/test_arr_paths.py`

**Interfaces:**
- Produces: `map_path(path: str, plex_root: str, arr_root: str) -> str | None`.

**Notes for the implementer:**

- The mapping replaces a leading `plex_root` with `arr_root`. It is **case-sensitive** — `/mnt/Media` and `/mnt/media` are different strings and the whole point is translating between them.
- A path that does not start with `plex_root` returns `None`. Do not guess: registering a path the service cannot read is worse than skipping the item, because the service then believes it has a file it cannot access.
- Normalise separators to forward slashes, and do not let a trailing slash on either root produce a doubled separator.
- Prefix matching must be on **path segments**, not raw string prefix: `/mnt/Media2/x` must not match a `plex_root` of `/mnt/Media`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arr_paths.py`:

```python
"""Translating Plex paths into the paths Radarr and Sonarr see.

Verified live: Plex mounts /mnt/Media (capital M), the services use
/mnt/media (lowercase). Getting this wrong registers a path the service
cannot read.
"""
import pytest

from autoposter.arr.paths import map_path

PLEX = "/mnt/Media"
ARR = "/mnt/media"


def test_the_real_movie_case():
    assert map_path("/mnt/Media/Movies/Dune (2021)", PLEX, ARR) == "/mnt/media/Movies/Dune (2021)"


def test_the_real_show_case():
    assert map_path("/mnt/Media/TV/Severance", PLEX, ARR) == "/mnt/media/TV/Severance"


def test_mapping_is_case_sensitive():
    """/mnt/media and /mnt/Media are different roots; that is the whole point."""
    assert map_path("/mnt/media/Movies/X", PLEX, ARR) is None


def test_a_path_outside_the_plex_root_is_not_guessed():
    """Registering an unreadable path is worse than skipping the item."""
    assert map_path("/somewhere/else/X", PLEX, ARR) is None


def test_a_sibling_root_with_a_shared_prefix_does_not_match():
    assert map_path("/mnt/Media2/Movies/X", PLEX, ARR) is None


def test_trailing_slashes_do_not_double_the_separator():
    assert map_path("/mnt/Media/Movies/X", "/mnt/Media/", "/mnt/media/") == "/mnt/media/Movies/X"


def test_backslashes_are_normalised():
    assert map_path("\\mnt\\Media\\Movies\\X", PLEX, ARR) == "/mnt/media/Movies/X"


def test_the_root_itself_maps_to_the_other_root():
    assert map_path("/mnt/Media", PLEX, ARR) == "/mnt/media"


@pytest.mark.parametrize("bad", [None, ""])
def test_an_empty_path_is_not_mapped(bad):
    assert map_path(bad, PLEX, ARR) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_arr_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.arr'`

- [ ] **Step 3: Implement, run, ruff, commit**

```bash
rtk proxy python -m pytest tests/test_arr_paths.py -v
rtk proxy ruff check src tests
git add src/autoposter/arr tests/test_arr_paths.py
git commit --no-gpg-sign -m "Map Plex paths to the paths Radarr and Sonarr see"
```

---

## Task 2: The service client

**Files:**
- Create: `src/autoposter/arr/client.py`
- Test: `tests/test_arr_client.py`

**Interfaces:**
- Produces: the frozen dataclass `ArrKind(name: str, resource: str, id_field: str)` with module constants `RADARR = ArrKind("radarr", "movie", "tmdbId")` and `SONARR = ArrKind("sonarr", "series", "tvdbId")`; and `class ArrClient` with `__init__(http, base_url, api_key, kind)`, `async existing_ids() -> set[str]`, `async quality_profile_id(name) -> int | None`, `async root_folders() -> list[str]`, `async add(payload: dict) -> dict`.

**Notes for the implementer:**

- Every request sends `X-Api-Key`. Never put the key in a query string or a log line.
- `existing_ids` returns the external ids as **strings**, so they compare directly against the ids read off Plex guids. Skip entries whose id field is absent or zero — an unmatched item in the service has no external id and must not collide with anything.
- `quality_profile_id` matches the configured profile **by exact name** and returns `None` when absent. A missing profile must be reported, never silently replaced with another profile's id.
- Raise on a non-2xx rather than returning an empty collection. An empty `existing_ids` would make every Plex item look missing, which under `add_existing` is a request to register the entire library.
- Strip a trailing slash from `base_url` so a configured `https://radarr.example/` does not produce a doubled path.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arr_client.py` covering, with `httpx.MockTransport` only:
- `X-Api-Key` is sent on every request and the key never appears in the URL;
- `existing_ids` returns string ids for both kinds and skips entries with a missing or zero id field;
- a non-2xx from the list endpoint **raises** rather than returning an empty set (state in the test docstring why: an empty set means "add the whole library");
- `quality_profile_id` resolves an exact name and returns `None` for one that is absent, and does not fall back to another profile;
- `root_folders` returns the configured paths;
- `add` posts to the right resource path and returns the parsed body;
- a `base_url` with a trailing slash does not produce a doubled path.

Use the real shapes from the live services in the fixtures — Radarr profile `HD Bluray + WEB` is id 7 with root `/mnt/media/Movies`; Sonarr profile `WEB-1080p` is id 7 with root `/mnt/media/TV`.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_arr_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.arr.client'`

- [ ] **Step 3: Implement, run, ruff, commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/arr/client.py tests/test_arr_client.py
git commit --no-gpg-sign -m "Add a Radarr and Sonarr API client"
```

---

## Task 3: Registering what the services are missing

**Files:**
- Create: `src/autoposter/arr/sync.py`
- Test: `tests/test_arr_sync.py`

**Interfaces:**
- Consumes: `ArrClient`, `RADARR`, `SONARR`, `map_path`.
- Produces: the frozen dataclass `ArrSyncReport(checked: int, missing: int, added: int, skipped_no_id: int, skipped_no_path: int, failed: int, titles: list[str])`; and `async sync_section(client, section, kind, settings, dry_run: bool = True) -> ArrSyncReport`.

**Notes for the implementer:**

- Read Plex external ids from each item's `guids` — `tmdb://` for movies, `tvdb://` for shows. An item with no such guid is counted in `skipped_no_id` and never added; there is nothing to match it on. One show in the live library is in exactly this state.
- The path to register is the item's own location mapped through `map_path`: `item.locations[0]` for a movie's file (use its **parent directory**, since the service registers a folder) and for a show the series directory itself. A path that will not map is counted in `skipped_no_path` and skipped.
- **`addOptions` must set the search flag to `false`.** For Radarr `{"searchForMovie": False}`, for Sonarr `{"searchForMissingEpisodes": False}`. There must be a test that fails if either is true or absent.
- Under `dry_run` the report is identical except `added` is zero and no POST is issued.
- A failure adding one item must not abort the rest: count it in `failed`, log it, continue.
- `section.all()` returns items with guids and locations already populated in one call, so build the whole comparison from that — do not reload per item.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arr_sync.py` covering: an item already known to the service is not added; a missing item is added with the mapped path, the resolved quality profile id and the configured monitor setting; **the search flag is false in the posted body** (for both kinds, as its own test); an item with no external id is skipped and counted; an item whose path will not map is skipped and counted; dry run posts nothing; one failing add does not prevent the others; and the report counts add up.

Use fakes in the style of `tests/test_collection_resolve.py` for the Plex section, and `httpx.MockTransport` for the service.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_arr_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.arr.sync'`

- [ ] **Step 3: Implement, run, ruff, commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/arr/sync.py tests/test_arr_sync.py
git commit --no-gpg-sign -m "Register Plex items the services are missing, without downloading"
```

---

## Task 4: The safety net

Independent of Radarr and Sonarr: any Plex item this service has never recorded gets enqueued, so a missed webhook self-corrects.

**Files:**
- Modify: `src/autoposter/arr/sync.py`
- Test: `tests/test_arr_safety_net.py`

**Interfaces:**
- Produces: `async enqueue_unknown_items(session, section, kind: str, batch_size: int = 500) -> int`.

**Notes for the implementer:**

- Select the Plex rating keys present in the section, find which have no `media_items` row, and enqueue a job for each, capped at `batch_size`.
- Use the same `dedupe_key` convention the rest of the queue uses, so an item already queued is not queued twice.
- The cap is a safety valve for the same reason the drift sweep has one: on a first run against a fresh database **every** item is unknown, and enqueuing ~16,000 jobs at once would swamp the workers and every provider. Successive runs work through the backlog.
- Compare in the database with a single `NOT IN`/anti-join over the rating keys, not one query per item.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arr_safety_net.py` covering: an item with a `media_items` row is not enqueued; one without is; the batch size caps how many are enqueued; running twice does not double-enqueue; and the returned count is right. Use the real session fixture and the real `enqueue`.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_arr_safety_net.py -v`
Expected: FAIL with `ImportError: cannot import name 'enqueue_unknown_items'`

- [ ] **Step 3: Implement, run, ruff, commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/arr/sync.py tests/test_arr_safety_net.py
git commit --no-gpg-sign -m "Enqueue Plex items this service has never seen"
```

---

## Task 5: Configuration, scheduling and documentation

**Files:**
- Modify: `src/autoposter/config/schema.py`
- Modify: `config/autoposter.example.yaml`
- Modify: `src/autoposter/scheduler/jobs.py`
- Modify: `src/autoposter/app.py`
- Modify: `deploy/README.md`
- Test: `tests/test_arr_config.py`

**Config to add**, mirroring the tool being replaced:

```yaml
radarr:
  enabled: false # off until base_url and api_key are set
  base_url: http://radarr.media.svc.cluster.local
  api_key: "" # from AUTOPOSTER_RADARR_APIKEY
  add_existing: false # register Plex items Radarr does not know about, in place
  plex_path: /mnt/Media # Plex mounts the library here (capital M)
  arr_path: /mnt/media # Radarr sees the same files here (lowercase)
  quality_profile: HD Bluray + WEB
  monitor: true
  minimum_availability: announced

sonarr:
  enabled: false
  base_url: http://sonarr.media.svc.cluster.local
  api_key: "" # from AUTOPOSTER_SONARR_APIKEY
  add_existing: false
  plex_path: /mnt/Media
  arr_path: /mnt/media
  quality_profile: WEB-1080p
  monitor: true
  season_folder: true
  series_type: standard

arr_sync:
  enabled: true # the safety net runs even when the services are not configured
  hours: 24
  batch_size: 500
```

**Notes for the implementer:**

- **The API keys must come from the environment**, following whatever pattern the existing secrets use (see how the Plex token and the MDBList key are loaded — there is a `Secrets` class). Never put a key in the example YAML and never log one.
- The scheduled job runs the safety net whenever `arr_sync.enabled`, and the per-service sync only when that service is `enabled`. Both services being unconfigured must be a clean no-op, not an error.
- Register the job with the existing scheduler exactly as the collections, drift and cleanup jobs are registered.
- Nothing about the search flags is configurable. There is no legitimate reason for this service to trigger a download, and a config key that could would eventually be set by accident.

- [ ] **Step 1: Write the failing test**

Create `tests/test_arr_config.py` asserting the schema defaults directly (not values loaded from the example YAML): both services default to `enabled: false` and `add_existing: false`; the path defaults are `/mnt/Media` and `/mnt/media`; and `arr_sync.batch_size` is 500.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_arr_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'radarr'`

- [ ] **Step 3: Add the config, wire the job, run the suite**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — 892 baseline plus roughly 45 new tests, 5 skipped

`tests/test_example_config_matches_schema.py` asserts every key in the example config exists in the schema.

- [ ] **Step 4: Document it**

In `deploy/README.md`: how to configure both services and where the keys come from; that `add_existing` is off by default and registers items **in place without downloading**; that the path mapping is case-sensitive and why (`/mnt/Media` in Plex, `/mnt/media` in the services); that nothing is ever removed from or modified in either service; that the safety net runs independently of both; and the expected first-run outcome for this library — **0 movies and 2 series** (`Limitless` and `The Moomins`), with one show carrying no TVDB id that can never be matched.

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter config/autoposter.example.yaml deploy/README.md tests
git commit --no-gpg-sign -m "Configure and schedule the Radarr and Sonarr sync"
```

---

## Deferred

- **`add_missing`** — adding items Plex does not have, which downloads them. Deliberately out of scope; the tool being replaced has it disabled too.
- **`upgrade_existing`** — changing the quality profile of items the services already hold. Also disabled in the configuration being replaced.
- **Collection posters**, still unimplemented from an earlier phase.
