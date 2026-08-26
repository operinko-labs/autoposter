# ROW-79 TRACEARR BUILDERS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Destination of this file when the phase branch is cut:** `docs/superpowers/plans/2026-08-27-row79-tracearr.md` (this draft lives at `.superpowers/sdd/row79-plan-draft.md`, which is gitignored staging — move it, do not copy it).

**Branch:** `feat/tracearr-builders`, cut from the `feat/plex-pruner` tip (`74fa9f5`) — the defaults-catalog PR (#76) was merged, but the pruner PR (#77) was still pending at execution start, and both it and this phase touch `src/autoposter/config/schema.py`, `src/autoposter/collections/catalog.py` and the roadmap; cutting from its tip avoids a guaranteed three-way conflict. No PR for this branch opens until #77 is merged.

**Execution:** superpowers:subagent-driven-development — one fresh subagent per task, two-stage review between tasks.

**Goal:** Ship `tracearr_most_watched`, a collection builder that ranks a Plex library's most-watched titles from Tracearr's own watch history, plus the client, config, secret and two catalog rows it needs — so an operator can switch on "Most Watched Movies" / "Most Watched Shows" and get a collection that reflects what their household actually played.

**Architecture:** Tracearr publishes **no** most-watched, popular or top-N endpoint on either public API version (`docs/research/tracearr-api-harvest.md:736-748`; the only such endpoint is on the internal session-JWT API and an API key cannot reach it). So the ranking is computed on our side, in three separable layers:

1. `src/autoposter/providers/tracearr.py` — the transport. Bearer auth, v2 cursor paging, the SPA-200 guard, 404-raises, and an error-message discipline that keeps the operator's cluster-internal base URL out of every log line.
2. `src/autoposter/collections/activity.py` — a **pure** function over history records. Buckets plays by `show_media_id` (shows) / `media_id` (movies), merges identity-less plays by the Plex rating keys their bucket has already been seen under, ranks by plays or watch time, truncates.
3. `src/autoposter/collections/builders/tracearr.py` — the builder. Turns surviving buckets into namespaced external ids: movies short-circuit off the record's own ids; shows resolve one `GET /api/v2/public/media/{show_media_id}` per surviving bucket, memoised per pass; an identity-less-only bucket falls back to `("plex", rating_key)`.

**Tech Stack:** Python 3.14, httpx (via `providers/fetch.py`), pydantic v2 params models, pytest/pytest-asyncio with `httpx.MockTransport`, ruff, Docker Compose.

---

## Global Constraints

Every task's requirements implicitly include this section.

**Testing is container-only.** The suite refuses to run outside the container (`tests/conftest.py:15-33` raises when `AUTOPOSTER_TEST_DATABASE_URL` is unset). Every test command in this plan takes the form:

```
docker compose -p <unique> -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest <paths> -x -q
```

- Each task uses its own `-p` project name (`row79t1`, `row79t2`, `row79t3`, `row79t4`) so concurrent tasks never share a database.
- Teardown at the end of each task: `docker compose -p <unique> down`. **NEVER `down -v`** — that destroys the volume other work may still be using.
- Allow up to 1800s per command; the compose image build plus a contended host makes a cold first run slow.
- The shared test database is **not** concurrency-safe (`tests/conftest.py:124-128`): never add `-n`/xdist, never run two pytest commands against the same `-p` project at the same time.

**Golden gate + lint, before the commit step of every task.** Both must pass:

```
docker compose -p <unique> -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_port_golden.py -x -q
docker compose -p <unique> -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test ruff check .
```

`tests/fixtures/collections/golden_port.json` is **never** edited. Nothing this phase adds is in `sources.default_definitions` and the two catalog rows are opt-in, so the golden output cannot move; if it does, the change is wrong.

**Full repo-relative paths always.** Every file this plan touches is written out in full (`src/autoposter/collections/activity.py`, not "the activity module"). Do not resolve shorthand from memory.

**No timestamp flakes (roadmap row 119).** No test asserts wall-clock timing or a value derived from `datetime.now()`. The window computation is a pure function taking `now` as an argument (`activity.since_instant(days, now)`) precisely so it can be tested against a fixed instant; the builder's own tests assert only that a `since` parameter was *sent*, never what it contained.

**No async-SQLAlchemy expired-attribute reads.** Nothing in this phase touches the database or an ORM session at all. If a task finds itself reaching for `AsyncSession`, it has left the plan — stop and flag it. (A prior plan shipped a test that read attributes off an expired ORM reference after commit; there is no such surface here and none is to be introduced.)

**Secrets (binding).**
- The Tracearr API key is `AUTOPOSTER_TRACEARR_APIKEY`, a **soft** secret on `Secrets` (`os.environ.get(..., "")` in `from_env`, the `mdblist_apikey` tier). It is **never** a config field, never in a cache key, never in a log line, never in a fixture, never in an exception message.
- Test key literals are obviously fake: `"trr_pub_test"`. Nothing else.
- `tracearr.base_url` is operator config and a cluster-internal hostname. It must never reach a log line, an API response, an event payload or an exception message. `httpx.HTTPStatusError.__str__` embeds the full URL and the engine logs a failed build with `logger.exception` (traceback included, `src/autoposter/collections/engine.py:430-439`) — so every httpx error is re-raised as `TracearrRefused` naming the **path** and the original exception's **class name**, with `from None` so the chained message cannot reach the log either.
- Every error string is class-name-only: no `str(exc)` of a third-party exception crosses a module boundary.

**Fixtures are cut from the banked payloads, verbatim.** The banked payloads under `docs/research/tracearr/payloads/` are already scrubbed (`docs/research/tracearr-api-harvest.md:52-72`: PII redacted, cursors decoded/remapped/re-encoded, media identity deliberately kept real). Every fixture this phase adds is either the whole `body` object of a banked payload, or a **subset of its `data` array with the records byte-identical**. The only permitted edit is setting `meta.nextCursor` to `null` so a one-page fixture terminates, and each fixture that carries that edit says so in the test module docstring. One fixture is *constructed* rather than cut (`tracearr_history_end.json`, the empty terminal page) because the harvest never banked one — it is labelled as constructed, and its shape is the spec's own `CursorMeta`.

**The aggregation oracle.** `tests/fixtures/collections/tracearr_history_window.json` (the verbatim body of `docs/research/tracearr/payloads/v2-history-since-window.json`: 50 records, `GET /api/v2/public/history?since=2026-07-26&pageSize=50`) is the oracle. The harvest's worked example (`docs/research/tracearr-api-harvest.md:768-819`) reports **50 records → 2 skipped → 48 folded into 21 buckets, Warehouse 13 at 18 plays**. Adjudication A1 amends it: the 2 identity-less records are **merged by seen rating key**, not skipped, so the correct answer is **50 records → 21 buckets, Warehouse 13 at 20 plays**, and the harvest's own note (`:811-813`, "the honest count for that title in this window is 20, not the 18 above") is what the merge rule delivers. Every expected number in Task 2 was derived by reading the payload file, not remembered.

**Commit messages** carry no `Co-Authored-By` trailer and no AI attribution.

---

## Verified ground truth

Every line reference below was read in the working tree before this plan was written. Line numbers drift — spot-check before relying on one.

**The builder seam**
- `src/autoposter/collections/builders/base.py:94-131` — `BuilderContext(library, library_type, http, config, cache, run_cache, sources)`; `run_cache` is per-library-per-pass scratch and a builder that memoises there must memoise the **failure** too (`:116-121`).
- `src/autoposter/collections/builders/base.py:70-91` — `BuilderResult(ids, summary, poster_kind, poster_key)`; a builder with no artwork leaves both poster fields `None`.
- `src/autoposter/collections/builders/base.py:202-217` — `REGISTRY` and `register`, which refuses a duplicate `type_name`.
- `src/autoposter/collections/builders/base.py:229-252` — `require_library_type(subject, library_type, allowed)`; `allowed` may be any collection of library types, including a dict whose **keys** are them (the `CHART_ENDPOINTS` idiom).
- `src/autoposter/collections/ids.py:15-18` — `Namespace = Literal["imdb", "tmdb", "tvdb", "plex"]`, `ExternalId = tuple[Namespace, str]`. There are no other namespaces.
- `src/autoposter/collections/builders/mdblist.py:159-200` — the client-bearing builder precedent: validate params, `require_library_type`, `ctx.sources.<client> is None` → own refusal class, per-library-type id preference, drop-with-a-debug-log for a member with no usable id.
- `src/autoposter/collections/builders/mdblist.py:128-147` — the `run_cache` memo that stores the **exception**.
- `src/autoposter/collections/builders/imdb_chart.py:34-53` — the params-model precedent (`extra="forbid"`, a field validator naming the legal values).
- `src/autoposter/collections/builders/__init__.py:10-73` (imports) and `:75-132` (the `register(...)` block) — registration lives here and nowhere else.
- `src/autoposter/collections/engine.py:411-447` — the containment invariant: `build` raising is logged with `logger.exception` and becomes `BuilderResult(ids=[])`; nothing derived from the exception reaches an action string.

**Clients and the bundle**
- `src/autoposter/providers/fetch.py:17-51` — `fetch_json(method, url, params, request, cache, ttl_seconds)`; 404 → `None`; the cache key never sees `request`'s headers; `cache=None` means no cache at all.
- `src/autoposter/providers/tmdb_lists.py:58-61` — `BASE_URL`/`MAX_PAGES`; `:166-187` — `_get`, where `None` (a 404) becomes a raise because "an empty membership means remove every member"; `:198-250` — the page loop with the `for/else` cap warning.
- `src/autoposter/collections/builders/sources_bundle.py:79-118` — `SourceClients`, a frozen dataclass whose every field defaults to `None`; absent means `None` and `None` means the builder raises.
- `src/autoposter/collections/service.py:200-257` — `build_source_clients(config, secrets, http, cache)`, built once per pass because radarr/sonarr are live-editable; `:260-269` — `_arr_client`, the `enabled and base_url and api_key` triple the Tracearr client copies exactly.
- `src/autoposter/config/live.py:30-84` — `FROZEN_SECTIONS`. `radarr`/`sonarr` are deliberately **absent**, which is why their clients can be rebuilt per pass; `tracearr` is absent for the same reason and is **not** to be added.
- `src/autoposter/config/loader.py:18-51` — `render_version` is an allowlist over render-relevant fields, so a new config section cannot disturb a single stored fingerprint.

**Config and secrets**
- `src/autoposter/config/schema.py:13-20` — `_SECRET_ENV`, the **hard** secrets. `tracearr_apikey` does not go here.
- `src/autoposter/config/schema.py:23-81` — `Secrets`; the soft tier at `:32-65`; `from_env`'s `os.environ.get(..., "")` lines at `:75-80`.
- `src/autoposter/config/schema.py:909-951` — `RadarrConfig`/`SonarrConfig`: `enabled: bool = False`, `base_url: str = ""`, and an explicit docstring line saying `api_key` is deliberately not a field.
- `src/autoposter/config/schema.py:1015-1052` — `Config`; the section fields at `:1031-1046`.
- `src/autoposter/config/schema.py:257-353` — `CollectionDefinition`; `:355-380` — params are validated against the builder's `params_model` at config load.

**The catalog**
- `src/autoposter/collections/catalog.py:66-76` — `CATEGORIES`, nine of them, `"charts": "Charts"` among them.
- `src/autoposter/collections/catalog.py:122-165` — `PresetCollection(title, builder, params, filters, library_types)`; `params` is a tuple of pairs so the row stays hashable.
- `src/autoposter/collections/catalog.py:168-308` — `Preset`; `definitions()` filters on `collection.library_types or self.library_types`.
- `src/autoposter/collections/catalog.py:534` — `NOT_KOMETA = "no Kometa defaults file -- "`; `:1262-1278` — `_check_kometa_sources`, which accepts any string carrying that prefix and refuses any other path not in `_KOMETA_DEFAULTS`.
- `src/autoposter/collections/catalog.py:585-587` — `_BOTH`/`_MOVIE`/`_SHOW`.
- `src/autoposter/collections/catalog.py:590-658` — the CHARTS section and `CHART_PRESETS`.
- `src/autoposter/collections/catalog.py:1232-1259` — the count-checksum comment and the `CATALOG` tuple.
- `tests/test_collection_catalog.py:186-223` — `CATALOG_CHECKSUM` (`"charts": (8, 0, 1)`) and the test that counts the real table against it.

**The harvest (binding API contract)**
- `docs/research/tracearr-api-harvest.md:274-290` — v2 cursor paging: `cursor` + `pageSize` (0–100, default 25), `meta.nextCursor` until `null`, an unreadable cursor is a 400.
- `:383-422` — `GET /api/v2/public/history`: newest-first, one record per **play** (a resume chain whose sessions total ≥2 min — already deduped and already filtered, so `len(records)` **is** the play count Tracearr reports); query params include `since`/`until` (instants), `media_type` (`movie|episode|...`), `server_id`; the `user` block is the PII-bearing one.
- `:336-350` — rate limits: 240/min **shared across the whole v2 tree**, headers `x-ratelimit-limit`/`-remaining`/`-reset`, the limiter runs **before** authentication.
- `:324-327` — **two** error envelopes: `{"statusCode","error","message"}` from route handlers, bare `{"error": "Not Found"}` from the not-found fallback.
- `:656-681` — the confirmed error table and the three conclusions: a malformed `{ref}` is indistinguishable from an unknown one (both 404 `NotFoundError`); **the presence of `x-ratelimit-*` is the reliable tell that a route matched**, which settles the SPA-200 trap; the rate limiter runs before auth.
- `:560-566` — finding 3: `GET /api/v2/public/media/show:tvdb:<episode id>` is a **live-verified 404** (`payloads/v2-media-show-by-tvdb-ref.json`). The external-id short-circuit is **movies only**.
- `:568-576` — finding 4: 2 of 50 records have `media_id`, `show_media_id`, `library_id`, `imdb_id`, `tmdb_id`, `tvdb_id` **all null** while still carrying `grandparent_rating_key` and `rating_key`.
- `:584-588` — a v2 cursor is base64 of `{"t":…,"id":<session uuid>}`: opaque, and **never** logged.
- `:590-595` — finding 7: undocumented keys are present and documented keys are absent; **parse leniently**, a strict model will reject live payloads.
- `:830-832` — `progress_ms`/`total_duration_ms` arrive as **strings**; `duration_ms`, the field this phase sums, is a real integer.
- `:750-767` — the recipe; `:768-819` — the worked example; `:821-848` — the implementer notes.

**The banked payloads this phase cuts from**
- `docs/research/tracearr/payloads/v2-history-since-window.json` — `{name, request, status, response_headers, body}`; `body = {"data": [50 records], "meta": {"nextCursor": "eyJ0…", "pageSize": 50}}`. 37 episode records, 13 movie records, 2 of the episodes identity-less (record ids `…000000000045` and `…000000000046`, both Warehouse 13, both `grandparent_rating_key: "75916"`).
- `docs/research/tracearr/payloads/v2-history-movies.json` — `body = {"data": [10 movie records], "meta": {...}}`, from `?pageSize=10&media_type=movie`. Ten distinct movies, one play each.
- `docs/research/tracearr/payloads/v2-history-page2-cursor.json` — `body` of the cursor-followed second page (10 records), the proof that paging is disjoint and continues.
- `docs/research/tracearr/payloads/v2-media-show-by-uuid.json` — `body` = Silo's media document: `id "faf036e2-8459-4ace-a8ba-19486b6289c6"`, `media_type "show"`, `tvdb_id 403245`, `tmdb_id 125988`, `imdb_id "tt14688458"`, `availability[0].rating_key "63856"`.
- `docs/research/tracearr/payloads/v2-media-movie-by-uuid.json` — `body` = Blade: Trinity's: `id "79e224e2-5dd6-447f-9ebc-c9e798300128"`, `tmdb_id 36648`, `imdb_id "tt0359013"`, `tvdb_id 1700`.
- `docs/research/tracearr/payloads/err-401-no-auth.json`, `err-404-unknown-media-ref.json`, `err-404-unmatched-api-path.json`, `err-200-spa-trap.json` — the four error shapes the client is held to.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/autoposter/providers/tracearr.py` | **Create.** The transport: `TracearrRefused`, `TracearrClient` with `history()` and `media()`, `API_PREFIX`, `PAGE_SIZE`, `MAX_PAGES`. Nothing about ranking, nothing about collections. |
| `src/autoposter/collections/activity.py` | **Create.** The pure ranking: `Bucket`, `rank()`, `since_instant()`, `MEDIA_KINDS`, `HISTORY_MEDIA_TYPE`, `METRICS`. No HTTP, no config, no clients. |
| `src/autoposter/collections/builders/tracearr.py` | **Create.** `TracearrBuilderRefused`, `TracearrMostWatchedParams`, `TracearrMostWatchedBuilder` — the translate half, plus the per-pass media-document memo. |
| `src/autoposter/collections/builders/sources_bundle.py` | **Modify.** One `tracearr` field on `SourceClients`. |
| `src/autoposter/collections/builders/__init__.py` | **Modify.** One import, one `register(...)` line. |
| `src/autoposter/collections/service.py` | **Modify.** `_tracearr_client` and one entry in `build_source_clients`. |
| `src/autoposter/config/schema.py` | **Modify.** `Secrets.tracearr_apikey` + its `from_env` line; `TracearrConfig`; `Config.tracearr`. |
| `src/autoposter/collections/catalog.py` | **Modify.** `TRACEARR_PRESETS` (two rows in the Charts category), added to `CATALOG`; the count-checksum comment. |
| `config/autoposter.example.yaml` | **Modify.** The `tracearr:` block. |
| `.env.example` | **Modify.** `AUTOPOSTER_TRACEARR_APIKEY`. |
| `deploy/README.md` | **Modify.** The secret in the Secrets list, plus a "Tracearr watch-history collections" subsection. |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modify.** Row 79 closure note (with its two corrections), row 93's catalog count, and the new recently-added row. |
| `tests/test_tracearr_client.py` | **Create.** Transport, error hygiene, config and bundle wiring. |
| `tests/test_collection_activity.py` | **Create.** The 50-record oracle, the merge rule, both metrics, the ordering contract. |
| `tests/test_builder_tracearr.py` | **Create.** The builder end to end, plus the never-an-episode-id invariant. |
| `tests/test_collection_catalog.py` | **Modify.** `CATALOG_CHECKSUM["charts"]` 8 → 10. |
| `tests/test_builder_mdblist.py`, `tests/test_builder_tmdb.py`, `tests/test_builder_tvdb.py`, `tests/test_builder_plex_watchlist.py`, `tests/test_scheduler_collections_job.py` | **Modify.** Each carries a hand-rolled `SimpleNamespace` config listing "only the sections `build_source_clients` reads". Adding a section to that function makes all five `AttributeError` unless they gain `tracearr=SimpleNamespace(enabled=False, base_url="")`. This is Task 1's job and is easy to miss. |
| `tests/fixtures/collections/tracearr_*.json` | **Create.** Six fixtures — see Task 2 Step 1 for the exact cut. |

Three modules rather than one, and the split is not stylistic. The ranking is the part with a real oracle behind it (50 banked records with known answers) and it must be testable without a transport; the transport is the part with the security discipline (base URL, bearer token, SPA guard) and must be testable without a ranking; the builder is the part that knows about libraries and namespaces. `src/autoposter/collections/awards.py` is the existing precedent for a pure domain module sitting beside the builders that read it.

---

### Task 1: The client, the config, the secret, the bundle

Compose project name for this task: **`row79t1`**.

**Files:**
- Create: `src/autoposter/providers/tracearr.py`
- Modify: `src/autoposter/config/schema.py` (`Secrets` at `:23-81`, a new `TracearrConfig` after `SonarrConfig` which ends at `:951`, and `Config` at `:1015-1052`)
- Modify: `src/autoposter/collections/builders/sources_bundle.py` (the `TYPE_CHECKING` import block at `:37-43`, the `SourceClients` fields at `:79-118`)
- Modify: `src/autoposter/collections/service.py` (`build_source_clients` at `:200-257`, `_arr_client` at `:260-269`)
- Create: `tests/test_tracearr_client.py`
- Create: `tests/fixtures/collections/tracearr_media_show.json`, `tests/fixtures/collections/tracearr_media_movie.json`
- Modify: `tests/test_builder_mdblist.py:392-399`, `tests/test_builder_tmdb.py:448-455`, `tests/test_builder_tvdb.py:321-328`, `tests/test_builder_plex_watchlist.py:262-268`, `tests/test_scheduler_collections_job.py:216-221`

**Interfaces:**
- Consumes: `fetch_json` (`src/autoposter/providers/fetch.py:17-51`), `SourceClients` (`src/autoposter/collections/builders/sources_bundle.py:79-118`).
- Produces, for Tasks 2–4:
  - `autoposter.providers.tracearr.API_PREFIX: str` = `"/api/v2/public"`
  - `autoposter.providers.tracearr.PAGE_SIZE: int` = `100`
  - `autoposter.providers.tracearr.MAX_PAGES: int` = `10`
  - `autoposter.providers.tracearr.TracearrRefused(Exception)`
  - `autoposter.providers.tracearr.TracearrNotFound(TracearrRefused)` — the one distinguishable case: a route matched and answered 404. Its own class so a *caller* can decide whether one missing item is fatal, while everything else stays one dead source. Task 3 is the caller that needs the distinction.
  - `autoposter.providers.tracearr.TracearrClient(client: httpx.AsyncClient, base_url: str, api_key: str, max_pages: int = MAX_PAGES)`
    - `async history(self, *, since: str, media_type: str, page_size: int = PAGE_SIZE) -> list[dict]`
    - `async media(self, media_id: str) -> dict`
  - `autoposter.config.schema.TracearrConfig(enabled: bool = False, base_url: str = "")`, registered as `Config.tracearr`
  - `autoposter.config.schema.Secrets.tracearr_apikey: str = ""`
  - `SourceClients.tracearr: TracearrClient | None = None`

- [ ] **Step 1: Cut the two media-document fixtures**

Run from the repository root (host `python`; this is not pytest, so it needs no container):

```bash
python - <<'PY'
import json, pathlib
src = pathlib.Path("docs/research/tracearr/payloads")
dst = pathlib.Path("tests/fixtures/collections")
for banked, name in (
    ("v2-media-show-by-uuid.json", "tracearr_media_show.json"),
    ("v2-media-movie-by-uuid.json", "tracearr_media_movie.json"),
):
    body = json.loads((src / banked).read_text(encoding="utf-8"))["body"]
    (dst / name).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(name, body["id"], body["media_type"], body["tvdb_id"], body["tmdb_id"], body["imdb_id"])
PY
```

Expected output, exactly:

```
tracearr_media_show.json faf036e2-8459-4ace-a8ba-19486b6289c6 show 403245 125988 tt14688458
tracearr_media_movie.json 79e224e2-5dd6-447f-9ebc-c9e798300128 movie 1700 36648 tt0359013
```

- [ ] **Step 2: Write the failing client tests**

Create `tests/test_tracearr_client.py`:

```python
"""Tracearr's public v2 read API: the transport under the activity builder.

Never touches the real instance -- MockTransport only.

Four properties carry the weight here, and each is a way a *silent* wrong
answer could reach a sync-mode collection or a credential could reach a log:

- **the single-page-app 200.** Tracearr serves its SPA on every unmatched path,
  so a mistyped base URL answers ``200 text/html`` rather than a 404. Every
  matched v2 route carries ``x-ratelimit-*`` headers and both fallbacks carry
  none, so header presence is the route-matched tell (harvest
  ``docs/research/tracearr-api-harvest.md:669-678``).
- **cursor paging is followed and capped.** ``meta.nextCursor`` goes back as
  ``cursor`` until it is null; the cap keeps a runaway upstream bounded.
- **a 404 raises rather than returning nothing.** ``fetch_json`` turns 404 into
  ``None`` -- right for artwork, and an empty membership one layer down means
  "remove every member".
- **neither the base URL nor the API key can reach a log.** The engine logs a
  failed build with ``logger.exception``, traceback included, and
  ``httpx.HTTPStatusError`` puts the whole URL in its own message.

Fixtures: ``tracearr_media_show.json`` / ``tracearr_media_movie.json`` are the
verbatim ``body`` objects of ``docs/research/tracearr/payloads/
v2-media-show-by-uuid.json`` and ``v2-media-movie-by-uuid.json``.
"""
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from autoposter.collections.service import build_source_clients
from autoposter.config.schema import Config, Secrets, TracearrConfig
from autoposter.providers.tracearr import (
    API_PREFIX,
    MAX_PAGES,
    TracearrClient,
    TracearrNotFound,
    TracearrRefused,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"

BASE_URL = "http://tracearr.test.invalid"
API_KEY = "trr_pub_test"
SHOW_UUID = "faf036e2-8459-4ace-a8ba-19486b6289c6"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _matched(payload, status=200):
    """A response from a route that matched: rate-limit headers present.

    The headers are what tell a matched route from the SPA fallback, so a test
    helper that forgot them would make every test look like the trap.
    """
    return httpx.Response(
        status,
        json=payload,
        headers={
            "x-ratelimit-limit": "240",
            "x-ratelimit-remaining": "237",
            "x-ratelimit-reset": "60",
        },
    )


def _routed(routes: dict, seen: list | None = None, default=None):
    """Serve ``{path: response-or-callable}``; anything else is the SPA."""

    def handler(request):
        if seen is not None:
            seen.append(request)
        entry = routes.get(request.url.path)
        if entry is None:
            return default or httpx.Response(
                200, text="<!doctype html><html><head><title>Tracearr</title>",
                headers={"content-type": "text/html"},
            )
        if callable(entry):
            return entry(request)
        return _matched(entry)

    return httpx.MockTransport(handler)


def _client(http, **kwargs):
    return TracearrClient(http, BASE_URL, API_KEY, **kwargs)


def _history(records, cursor=None):
    return {"data": records, "meta": {"nextCursor": cursor, "pageSize": 100}}


# --- the request itself -------------------------------------------------------


async def test_the_key_travels_as_a_bearer_token_on_the_v2_public_prefix():
    seen: list = []
    routes = {f"{API_PREFIX}/history": _history([])}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert seen[0].url.path == "/api/v2/public/history"
    assert seen[0].url.host == "tracearr.test.invalid"
    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert seen[0].url.params["since"] == "2026-07-26T00:00:00Z"
    assert seen[0].url.params["media_type"] == "movie"
    assert seen[0].url.params["pageSize"] == "100"


async def test_a_trailing_slash_on_the_base_url_does_not_double_up():
    seen: list = []
    routes = {f"{API_PREFIX}/history": _history([])}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        client = TracearrClient(http, BASE_URL + "/", API_KEY)
        await client.history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert seen[0].url.path == "/api/v2/public/history"


# --- paging -------------------------------------------------------------------


async def test_history_follows_the_cursor_until_it_is_null():
    """The cursor is opaque and embeds a session identifier, so it is handed
    straight back and never inspected. Two pages, disjoint, in order."""
    seen: list = []
    pages = iter([_history([{"id": "a"}], cursor="CURSOR-1"), _history([{"id": "b"}])])
    routes = {f"{API_PREFIX}/history": lambda request: _matched(next(pages))}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        records = await _client(http).history(
            since="2026-07-26T00:00:00Z", media_type="episode"
        )

    assert [record["id"] for record in records] == ["a", "b"]
    assert len(seen) == 2
    assert "cursor" not in seen[0].url.params
    assert seen[1].url.params["cursor"] == "CURSOR-1"


async def test_history_stops_at_the_page_cap_and_says_so(caplog):
    """A truncated answer that says nothing looks exactly like a complete one
    -- the ``tmdb_lists``/``imdb_lists`` judgement, one API over."""
    routes = {f"{API_PREFIX}/history": lambda request: _matched(
        _history([{"id": "x"}], cursor="ALWAYS-MORE")
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with caplog.at_level(logging.WARNING):
            records = await _client(http, max_pages=3).history(
                since="2026-07-26T00:00:00Z", media_type="episode"
            )

    assert len(records) == 3
    assert "3-page cap" in caplog.text
    assert BASE_URL not in caplog.text
    assert API_KEY not in caplog.text


async def test_the_default_page_cap_is_ten():
    assert MAX_PAGES == 10


# --- the SPA trap and the two error envelopes ---------------------------------


async def test_a_200_with_no_rate_limit_header_is_the_spa_and_not_data():
    """The trap this client exists to survive: a mistyped base URL answers 200
    with the app's own index.html, and a client that decoded it as an empty
    collection would remove every member on the next pass."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert "x-ratelimit-limit" in str(error.value)
    assert BASE_URL not in str(error.value)


async def test_the_bare_envelope_404_is_a_misrouted_request_not_a_missing_item():
    """``{"error": "Not Found"}`` comes from the not-found fallback, which sits
    outside the rate-limit plugin -- so it is caught by the same header check
    rather than being mistaken for "this media does not exist"."""
    routes = {f"{API_PREFIX}/history": lambda request: httpx.Response(
        404, json={"error": "Not Found"}
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert "x-ratelimit-limit" in str(error.value)


async def test_a_matched_404_raises_its_own_class_rather_than_answering_nothing():
    """``fetch_json`` turns a 404 into ``None``. For a collection that would
    mean "remove every member", so every method here raises instead.

    A *subclass*, because a matched 404 is the one refusal a caller can
    sometimes recover from -- one title of twenty having been deleted. It is
    still a ``TracearrRefused``, so a caller that does not care about the
    distinction is unaffected.
    """
    routes = {f"{API_PREFIX}/media/{SHOW_UUID}": lambda request: _matched(
        {"statusCode": 404, "error": "NotFoundError", "message": "Not Found"}, status=404
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrNotFound) as error:
            await _client(http).media(SHOW_UUID)

    assert isinstance(error.value, TracearrRefused)
    assert "404" in str(error.value)
    assert BASE_URL not in str(error.value)


async def test_the_spa_and_the_transport_failures_are_not_the_not_found_class():
    """The distinction is only worth having if it is narrow: everything a
    caller cannot recover from must stay the base class, or a per-item
    ``except TracearrNotFound`` would start swallowing dead deployments."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TracearrRefused) as spa:
            await _client(http).media(SHOW_UUID)

    assert not isinstance(spa.value, TracearrNotFound)

    def boom(request):
        raise httpx.ConnectError("connection refused")

    routes = {f"{API_PREFIX}/media/{SHOW_UUID}": boom}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as dead:
            await _client(http).media(SHOW_UUID)

    assert not isinstance(dead.value, TracearrNotFound)
    assert "ConnectError" in str(dead.value)
    assert BASE_URL not in str(dead.value)


# --- error hygiene ------------------------------------------------------------


async def test_a_server_error_names_the_class_and_never_the_base_url():
    """``httpx.HTTPStatusError`` puts the whole URL in its own message and the
    engine logs a failed build with its traceback, so the httpx error is
    re-raised ``from None`` with the class name and the path only."""
    routes = {f"{API_PREFIX}/history": lambda request: _matched(
        {"statusCode": 500, "error": "InternalServerError", "message": "boom"}, status=500
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    assert "HTTPStatusError" in str(error.value)
    assert "/history" in str(error.value)
    assert BASE_URL not in str(error.value)
    assert API_KEY not in str(error.value)
    # ``from None``: the chained httpx message, which carries the URL, must not
    # be reachable through the exception the engine logs. ``__cause__`` is
    # None and ``__suppress_context__`` is True are exactly what ``raise ...
    # from None`` sets, and together they are what keeps ``logger.exception``
    # from printing "During handling of the above exception" followed by the
    # full URL.
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "base_url, leak",
    [
        ("http://tracearr.internal:notaport", "notaport"),
        ("http://tracearr❤secret.internal", "tracearr❤secret.internal"),
    ],
    ids=["garbage-port", "bad-codepoint-host"],
)
async def test_a_malformed_base_url_is_refused_without_quoting_itself(base_url, leak):
    """The input this module's URL hygiene exists for, and the one it used to
    miss: ``httpx.InvalidURL`` is a *sibling* of ``httpx.HTTPError``, not a
    descendant, so an ``except httpx.HTTPError`` never saw it.

    It is raised while building the request -- before any transport runs -- and
    its own message quotes the offending component: ``Invalid port: 'notaport'``
    for the first case and ``Invalid IDNA hostname: '<host>'`` for the second,
    which is the whole cluster-internal hostname. Both would reach the log
    through the engine's ``logger.exception``, and both would escape as a
    non-``TracearrRefused`` class, breaking the "one dead source" contract the
    builder's per-bucket handling is built on.
    """
    async with httpx.AsyncClient(transport=_routed({})) as http:
        client = TracearrClient(http, base_url, API_KEY)
        with pytest.raises(TracearrRefused) as error:
            await client.history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    assert "InvalidURL" in message
    assert "/history" in message
    assert leak not in message
    assert API_KEY not in message
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "base_url",
    [
        "http://xn--tracearr.internal",
        "http://xn--tracearr-secret-cluster.internal",
    ],
    ids=["punycode-codepoint", "punycode-bidi"],
)
async def test_a_punycode_base_url_is_refused_by_the_total_guard(base_url):
    """The *second* idna call site, and the one an enumerated catch tuple missed.

    ``httpx``'s ``URL.host`` property (``_urls.py``) runs ``idna.decode(host)``
    on any host starting ``xn--``, unguarded -- a separate path from the
    ``idna.encode`` in ``_urlparse.py``, which httpx does wrap in
    ``InvalidURL``. It is reached on the request-build path, so it lands inside
    this client's ``try``, and it raises ``idna``'s own errors: they subclass
    ``UnicodeError``, so they are neither ``httpx.HTTPError`` nor
    ``httpx.InvalidURL`` nor ``json.JSONDecodeError``.

    Their messages carry a *transform* of the operator's host rather than the
    host verbatim -- ``Codepoint U+02E9 at position 1 of '<decoded label>'`` --
    which is still derived from the base URL and still must not reach a log.
    The point of the test is less these two classes than that the guard no
    longer depends on having named them.
    """
    async with httpx.AsyncClient(transport=_routed({})) as http:
        client = TracearrClient(http, base_url, API_KEY)
        with pytest.raises(TracearrRefused) as error:
            await client.history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    # The whole message is client-constructed: a fixed sentence around one bare
    # class name. Asserting the *entire* shape rather than a list of forbidden
    # substrings is what makes this a test of the invariant rather than a test
    # of the two idna spellings that happen to be raised today -- no quoted
    # host, no decoded label, no codepoint listing can satisfy it.
    assert re.fullmatch(
        r"the Tracearr watch history: Tracearr refused /history \(\w+\)", message
    ), message
    assert "xn--" not in message
    assert "secret" not in message
    assert API_KEY not in message
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


async def test_no_repr_of_the_client_carries_the_key():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        client = _client(http)
        assert API_KEY not in repr(client)


async def test_a_media_id_that_is_not_a_uuid_is_refused_before_any_request():
    """``media_id`` is interpolated into a path. It always comes from Tracearr
    itself, and it is checked anyway: a value with a slash in it would address
    a different endpoint entirely."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(TracearrRefused):
            await _client(http).media("../../v1/public/users")

    assert seen == []


# --- the media document -------------------------------------------------------


async def test_media_returns_the_show_document_verbatim():
    document = load("tracearr_media_show.json")
    routes = {f"{API_PREFIX}/media/{SHOW_UUID}": document}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        answer = await _client(http).media(SHOW_UUID)

    assert answer == document
    assert answer["tvdb_id"] == 403245
    assert answer["availability"][0]["rating_key"] == "63856"


async def test_records_are_handed_back_unparsed():
    """The API carries undocumented keys and omits documented ones (harvest
    finding 7), so a model here would reject live payloads. Records cross this
    boundary as the dicts Tracearr sent."""
    record = {"id": "a", "media_type": "movie", "an_undocumented_key": 1}
    routes = {f"{API_PREFIX}/history": _history([record])}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        records = await _client(http).history(
            since="2026-07-26T00:00:00Z", media_type="movie"
        )

    assert records == [record]


async def test_a_response_with_no_data_array_is_refused():
    routes = {f"{API_PREFIX}/history": {"meta": {"nextCursor": None}}}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused, match="'data'"):
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")


async def test_a_matched_route_answering_html_is_refused_not_decoded():
    """Past the SPA guard, so the header check cannot help: a route that *did*
    match but whose body is HTML -- an upstream proxy's error page in front of a
    live Tracearr, which is why it still carries the rate-limit headers.

    Without the decode inside the wrapped block this escapes as a raw
    ``json.JSONDecodeError``, which is not a ``TracearrRefused`` and so is
    invisible to the builder's per-bucket handling. The page is also attacker-
    or infrastructure-controlled text, so nothing from it may be quoted.
    """
    body = "<!doctype html><html><body>gateway PROXYSECRET failed</body></html>"
    routes = {f"{API_PREFIX}/history": lambda request: httpx.Response(
        200, text=body,
        headers={"content-type": "text/html", "x-ratelimit-limit": "240"},
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    assert "JSONDecodeError" in message
    assert "/history" in message
    assert "PROXYSECRET" not in message
    assert BASE_URL not in message
    assert API_KEY not in message


async def test_a_body_that_is_not_decodable_text_is_refused():
    """``response.json()`` decodes before it parses, so a body that is not text
    at all raises ``UnicodeDecodeError`` -- a ``ValueError``, and *not* a
    ``json.JSONDecodeError``, which is why an enumerated catch tuple listing the
    latter still let this through.

    Its message quotes the offending bytes' position and the codec's guess at
    the encoding, so a response body an upstream can control was reaching the
    log. Under the total guard the class name is all that survives.
    """
    routes = {f"{API_PREFIX}/history": lambda request: httpx.Response(
        200, content=b"\xff\xfe\x00secret",
        headers={"x-ratelimit-limit": "240"},
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    assert "UnicodeDecodeError" in message
    assert "/history" in message
    assert "secret" not in message
    assert BASE_URL not in message
    assert API_KEY not in message


async def test_a_top_level_json_array_is_refused_rather_than_returned():
    """``_get`` annotates ``-> dict`` and both callers ``.get`` what it returns,
    so a top-level array used to escape as an ``AttributeError`` from one frame
    down -- and ``media()`` is worse, since it would *return* the list to the
    ranking code, where the failure is much harder to attribute.

    The type's name is a stable label; the payload's contents are not quoted.
    """
    routes = {f"{API_PREFIX}/history": lambda request: _matched(
        [{"id": "a", "title": "A Private Title"}]
    )}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _client(http).history(since="2026-07-26T00:00:00Z", media_type="movie")

    message = str(error.value)
    assert "list" in message
    assert "/history" in message
    assert "A Private Title" not in message
    assert BASE_URL not in message
    assert API_KEY not in message
    assert not isinstance(error.value, TracearrNotFound)


# --- config, secret and bundle wiring -----------------------------------------


def _secrets(tracearr_apikey=API_KEY):
    return Secrets(
        database_url="postgresql+asyncpg://unused", plex_token="x",
        tmdb_token="x", tvdb_apikey="x", fanart_apikey="x", webhook_secret="x",
        tracearr_apikey=tracearr_apikey,
    )


def _bundle_config(enabled=True, base_url=BASE_URL):
    """Only the sections ``build_source_clients`` reads."""
    return SimpleNamespace(
        providers=SimpleNamespace(cache_ttl_seconds=3600),
        radarr=SimpleNamespace(enabled=False, base_url=""),
        sonarr=SimpleNamespace(enabled=False, base_url=""),
        tracearr=SimpleNamespace(enabled=enabled, base_url=base_url),
        manual_assets_root="/manual",
    )


def test_the_config_block_defaults_off_and_carries_no_api_key_field():
    """The credential is ``AUTOPOSTER_TRACEARR_APIKEY``, never a config field --
    the RadarrConfig/SonarrConfig rule, and the reason a config document can be
    read, written and audited without ever holding a secret."""
    block = TracearrConfig()

    assert block.enabled is False
    assert block.base_url == ""
    assert "api_key" not in TracearrConfig.model_fields
    assert "apikey" not in TracearrConfig.model_fields
    assert "tracearr" in Config.model_fields


def test_the_secret_is_soft_so_a_deployment_without_one_still_boots(monkeypatch):
    """The ``mdblist_apikey`` tier: absent means the Tracearr definitions
    report themselves failed, not that the process refuses to start."""
    for name in (
        "AUTOPOSTER_DATABASE_URL", "AUTOPOSTER_PLEX_TOKEN", "AUTOPOSTER_TMDB_TOKEN",
        "AUTOPOSTER_TVDB_APIKEY", "AUTOPOSTER_FANART_APIKEY", "AUTOPOSTER_WEBHOOK_SECRET",
    ):
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("AUTOPOSTER_TRACEARR_APIKEY", raising=False)

    assert Secrets.from_env().tracearr_apikey == ""

    monkeypatch.setenv("AUTOPOSTER_TRACEARR_APIKEY", API_KEY)
    assert Secrets.from_env().tracearr_apikey == API_KEY


async def test_the_pass_bundle_carries_a_client_when_all_three_are_present():
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(_bundle_config(), _secrets(), http)

    assert isinstance(sources.tracearr, TracearrClient)
    assert API_KEY not in repr(sources)


@pytest.mark.parametrize(
    "config_kwargs, secret",
    [
        ({"enabled": False}, API_KEY),
        ({"base_url": ""}, API_KEY),
        ({}, ""),
    ],
    ids=["disabled", "no-base-url", "no-key"],
)
async def test_a_half_configured_tracearr_is_no_client_at_all(config_kwargs, secret):
    """The ``_arr_client`` triple: ``enabled`` is the operator's switch, and a
    blank base URL or key is a half-configured service whose every request
    would fail confusingly rather than saying "not configured"."""
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(
            _bundle_config(**config_kwargs), _secrets(secret), http
        )

    assert sources.tracearr is None


def test_tracearr_is_not_a_frozen_section():
    """Clients are rebuilt per pass (``build_source_clients``), so an operator
    who switches Tracearr on does not have to restart to use it -- the
    radarr/sonarr posture, deliberately."""
    from autoposter.config.live import FROZEN_SECTIONS

    assert "tracearr" not in FROZEN_SECTIONS
```

- [ ] **Step 3: Run the tests to verify they fail**

Run:
```
docker compose -p row79t1 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_tracearr_client.py -x -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'autoposter.providers.tracearr'`.

- [ ] **Step 4: Write the client**

Create `src/autoposter/providers/tracearr.py`:

```python
"""Tracearr's public v2 read API: the transport under the activity builders.

Tracearr publishes **no** most-watched, popular or top-N endpoint in either API
version (``docs/research/tracearr-api-harvest.md``, "The headline finding": the
only such endpoint is on the internal session-JWT API and an API key cannot
reach it). So the ranking row 79 asks for is computed on this side, from raw
watch history. This module is only the transport -- the ranking is
``autoposter.collections.activity`` and the builder is
``autoposter.collections.builders.tracearr``.

Four decisions, each of them a way a wrong answer or a leaked credential could
otherwise reach a live deployment.

**The single-page-app 200.** Tracearr serves its SPA on every unmatched path,
so a mistyped base URL answers ``200 text/html`` rather than a 404 and a naive
client would decode nothing and build an empty collection -- which one layer
down means "remove every member". Every matched v2 route carries
``x-ratelimit-*`` headers; both fallbacks (the bare-envelope 404 and the SPA
200) sit outside the rate-limit plugin and carry none. Header presence is
therefore the route-matched tell, and it is cheaper and more robust than
sniffing the body.

**Cursor paging.** ``meta.nextCursor`` is passed back as ``cursor`` until it is
null, bounded by ``MAX_PAGES`` so a runaway upstream costs a bounded number of
requests. A cursor is opaque *and* is base64 of ``{"t": ..., "id": <session
uuid>}``, so it is handed straight back, never inspected and never logged.

**The base URL never leaves this module.** It is a cluster-internal hostname an
operator put in their YAML. ``httpx.HTTPStatusError`` puts the full URL in its
own message, and the engine logs a failed build with ``logger.exception`` --
traceback included (``collections/engine.py``). So every exception raised while
fetching becomes a ``TracearrRefused`` naming the path and the original
exception's CLASS, raised ``from None`` so the chained message cannot reach the
log either. That catch is
deliberately *total* rather than a list of classes: the libraries under here
interpolate what they were handed into their own messages -- the full URL, an
offending port, the hostname, a punycode-decoded transform of it -- and which
class carries which is a moving target across dependency versions. ``_get``
records why at length. The API key lives in a header and enters no message, no
cache key and no ``repr``.

**404 raises -- as its own class.** ``fetch_json`` turns a 404 into ``None``,
which is the right answer for artwork and the wrong one here, for the reason
``providers/tmdb_lists.py`` already records: an empty membership means "remove
every member" (``lists.reconcile_list_collection``). So every method raises.
What is different here is that a matched 404 raises ``TracearrNotFound``, a
subclass, because this client has two kinds of caller: one asking "give me the
window" (where a 404 is the source being gone) and one asking "who is this one
title" (where a 404 is one title being gone, in a list of twenty). The
transport does not get to decide which of those is fatal -- it only makes the
two distinguishable. Everything else, the SPA fallback included, stays a plain
``TracearrRefused``, which is a dead source however it is caught.

No response cache, and that is a decision rather than an omission. ``/history``
is not cached server-side, the ranking is recomputed every pass by design (the
harvest's "recompute rather than cache ranks", because ``/history``'s
``since``/``until`` are instants while ``/media/{ref}/stats``' windows are UTC
calendar days and the two demonstrably disagree), and every page after the
first carries a unique cursor -- so a response cache would store one entry per
pass per page and never serve one. The per-pass memo that *does* pay for itself
is the builder's, on ``ctx.run_cache``.

**The budget.** 240 requests/minute, shared across the whole v2 tree and spent
even by requests that fail authentication. One collection costs one page per
100 plays in its window plus, on a Show library, one ``/media/{ref}`` per
ranked title -- which is why the builder's ``limit`` is applied *before* those
calls rather than after.
"""
import logging
import re

import httpx

from autoposter.providers.fetch import fetch_json

logger = logging.getLogger(__name__)

__all__ = [
    "API_PREFIX",
    "MAX_PAGES",
    "PAGE_SIZE",
    "TracearrClient",
    "TracearrNotFound",
    "TracearrRefused",
]

# Every route this client uses is under the v2 public tree. v1 is not a
# camelCase mirror of it: v1 ``/history`` returns raw sessions rather than
# plays and carries no media identity at all, so it cannot be joined to Plex.
API_PREFIX = "/api/v2/public"

# Tracearr's own maximum. A 30-day window on a busy server is a handful of
# pages at this size and a few dozen at the default of 25.
PAGE_SIZE = 100

# How many pages one call will ever fetch. The same bound, and the same
# reasoning, as ``providers/tmdb_lists.MAX_PAGES``: a definition's ``limit``
# trims after the fact and cannot stop a fetch that has already happened.
MAX_PAGES = 10

# The header whose presence means a v2 route matched. See the module docstring.
RATE_LIMIT_HEADER = "x-ratelimit-limit"

# A canonical media id is a uuid. Checked because the value is interpolated
# into a path: it always comes from Tracearr itself, and a value with a slash
# in it would address a different endpoint entirely.
_MEDIA_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


class TracearrRefused(Exception):
    """Tracearr could not answer this, or answered something unusable.

    One class for the cases a caller cannot usefully tell apart -- the SPA
    fallback, a transport failure, a response with no ``data`` array -- because
    the engine logs the class name either way and the message says which. It
    carries the path and, for a transport failure, the original exception's
    class name. It never carries the base URL (a cluster-internal hostname) and
    never the API key.
    """


class TracearrNotFound(TracearrRefused):
    """A v2 route matched and answered 404.

    A subclass rather than a flag, and it exists for exactly one caller: the
    builder resolves one ``/media/{uuid}`` per ranked title, and a title
    deleted from Tracearr between the history read and that lookup is a
    transient, per-item fact -- not a reason to fail a whole collection.
    Distinguishing it here means the *caller* decides, while everything the
    caller cannot recover from (no route, no connection, a mangled envelope)
    stays a plain ``TracearrRefused``.

    Note what it is NOT a licence for: the history endpoint answering 404 is
    still fatal, because the builder never catches this class around that call.
    """


class TracearrClient:
    """Reads watch history and media identity from one Tracearr instance."""

    name = "TRACEARR"

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        max_pages: int = MAX_PAGES,
    ):
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._max_pages = max_pages

    def __repr__(self) -> str:
        # Neither the key nor the base URL: a bundle's ``repr`` reaches logs
        # and test output, and ``SourceClients`` is a dataclass whose default
        # repr would otherwise print whatever this returns.
        return "TracearrClient(...)"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "accept": "application/json"}

    async def _get(self, path: str, params: dict, subject: str) -> dict:
        """One request. Raises rather than answering ``None`` or HTML.

        ``fetch_json`` is used for the decode-and-check-404 half only; the
        cache is deliberately off (module docstring), which also means the
        bearer token cannot reach a cache key because no key is ever built.
        """
        url = f"{self._base_url}{API_PREFIX}{path}"

        async def request() -> httpx.Response:
            response = await self._client.get(url, params=params, headers=self._headers())
            if RATE_LIMIT_HEADER not in response.headers:
                # The SPA 200 and the bare-envelope 404 both land here. Neither
                # is data, and both mean the request never reached the API.
                raise TracearrRefused(
                    f"{subject}: {path} answered {response.status_code} with no "
                    f"{RATE_LIMIT_HEADER!r} header, so it was served by Tracearr's "
                    "single-page app or its not-found fallback rather than by the "
                    "API. Check the configured base URL."
                )
            return response

        try:
            payload = await fetch_json(
                method="GET",
                url=url,
                params=params,
                request=request,
                cache=None,
                ttl_seconds=0,
            )
        except TracearrRefused:
            # The guard's own refusal, already hygienic. It is raised inside
            # ``request()``, which runs inside this ``try``, so it needs an
            # explicit pass-through or the clause below would re-wrap it and
            # lose the message that names the missing rate-limit header.
            raise
        except Exception as error:
            # Deliberately total, and the breadth is the point rather than a
            # shortcut.
            #
            # The invariant is that NO third-party exception text may cross
            # this boundary: ``base_url`` is a cluster-internal hostname, the
            # engine logs a failed build with ``logger.exception``, and the
            # libraries under here interpolate whatever they were handed into
            # their own messages -- the full URL, the offending port, the
            # hostname, a punycode-decoded transform of it, a fragment of an
            # undecodable response body.
            #
            # That surface cannot be enumerated. Two rounds of listing the
            # classes that can escape were each defeated by the first probe
            # outside the tested inputs: ``httpx.InvalidURL`` is a *sibling* of
            # ``HTTPError`` rather than a descendant, and then ``idna``'s errors
            # (``UnicodeError`` subclasses, so in neither) escape the *second*,
            # unguarded ``idna`` call site -- ``_urls.py``'s ``URL.host``
            # property, which ``idna.decode``s any ``xn--`` host on the
            # request-build path -- while ``response.json()`` raises
            # ``UnicodeDecodeError`` rather than ``JSONDecodeError`` on a body
            # that is not decodable text. ``pyproject.toml`` pins ``httpx>=0.27``
            # with no upper bound, so any such list is validated against one
            # patch version of a dependency free to move those call sites again.
            #
            # So the guard is closed by construction instead: nothing leaves
            # ``_get`` except ``TracearrRefused`` and its subclass, whatever
            # httpx, idna or the stdlib decide to raise next. The class name is
            # the diagnostic that survives, and ``from None`` suppresses the
            # chain -- without it ``logger.exception`` prints "During handling
            # of the above exception" followed by the original message, which is
            # the leak this whole arrangement exists to prevent. Losing the
            # traceback is the accepted price; it is not recovered by logging
            # ``exc_info`` here, because that would put the URL in a log line
            # just as surely.
            detail = type(error).__name__
            if isinstance(error, httpx.HTTPStatusError):
                # An integer, carrying neither URL nor credential. Without it a
                # rejected API key (401), a spent rate-limit budget (429) and a
                # dead instance (500) are one indistinguishable log line -- and
                # a 401 carries rate-limit headers, so it passes the guard and
                # lands exactly here.
                detail = f"{detail} {error.response.status_code}"
            raise TracearrRefused(
                f"{subject}: Tracearr refused {path} ({detail})"
            ) from None
        if payload is None:
            raise TracearrNotFound(
                f"{subject}: Tracearr answered 404 for {path}. Building an empty "
                "collection instead would remove every member it has."
            )
        if not isinstance(payload, dict):
            # A matched route answering a top-level array or scalar. Both
            # callers annotate ``dict`` and both go on to ``.get`` it, so
            # without this the failure surfaces as an ``AttributeError`` from
            # somewhere downstream -- outside the ``TracearrRefused`` contract
            # the builder's per-bucket handling is built on, and for ``media()``
            # only after the wrong type has been handed to the ranking code.
            # The type's name only: a payload this client cannot parse is
            # exactly the payload it must not quote into a log.
            raise TracearrRefused(
                f"{subject}: {path} answered a {type(payload).__name__}, not a JSON "
                "object"
            )
        return payload

    async def history(
        self, *, since: str, media_type: str, page_size: int = PAGE_SIZE
    ) -> list[dict]:
        """Every play in the window, newest first, as Tracearr's own records.

        ``since`` is an ISO-8601 instant (never a calendar day: ``/history``
        and ``/media/{ref}/stats`` use different window semantics and their
        numbers do not agree). ``media_type`` is Tracearr's own word --
        ``"movie"`` or ``"episode"``.

        A record **is** a play: the API groups sessions into resume chains and
        drops chains under two minutes, so counting records is counting plays
        and nothing here re-filters.

        Records are handed back unparsed. The API carries undocumented keys and
        omits documented ones, so a strict model would reject live payloads;
        the only fields anything downstream reads are the dozen named in
        ``collections/activity.py``.
        """
        subject = "the Tracearr watch history"
        base: dict[str, object] = {
            "since": since,
            "media_type": media_type,
            "pageSize": page_size,
        }
        records: list[dict] = []
        cursor = None
        for _ in range(self._max_pages):
            params = dict(base)
            if cursor is not None:
                params["cursor"] = cursor
            payload = await self._get("/history", params, subject)
            data = payload.get("data")
            if not isinstance(data, list):
                raise TracearrRefused(
                    f"{subject}: the response carries no 'data' array. Reading that "
                    "as an empty window would empty the collection."
                )
            if not all(isinstance(record, dict) for record in data):
                raise TracearrRefused(
                    f"{subject}: the response's 'data' array holds something that is "
                    "not a record"
                )
            records += data
            meta = payload.get("meta")
            cursor = meta.get("nextCursor") if isinstance(meta, dict) else None
            if not cursor:
                break
        else:
            # Every page spent with a cursor still outstanding: the one exit
            # that hands back a *truncated* window. Silent truncation looks
            # exactly like a complete answer -- the judgement
            # ``providers/tmdb_lists`` and ``collections/imdb_lists`` both made.
            logger.warning(
                "%s: stopped at the %d-page cap with %d record(s); the window may "
                "hold more plays than this pass will see",
                subject, self._max_pages, len(records),
            )
        return records

    async def media(self, media_id: str) -> dict:
        """One canonical media document: SHOW-level ids and availability.

        The only correct route from an episode play to the show it belongs to.
        An episode record's own ``imdb_id``/``tmdb_id``/``tvdb_id`` are the
        EPISODE's, and ``GET /media/show:tvdb:<episode id>`` is a live-verified
        404 (harvest finding 3, banked as
        ``docs/research/tracearr/payloads/v2-media-show-by-tvdb-ref.json``) --
        so a show's ids come from here or from nowhere.
        """
        if not _MEDIA_ID.match(media_id):
            raise TracearrRefused(
                f"{media_id!r} is not a canonical Tracearr media id (a uuid), so it "
                "would address some other endpoint rather than a media document"
            )
        return await self._get(f"/media/{media_id}", {}, f"Tracearr media {media_id}")
```

- [ ] **Step 5: Add the config, the secret and the bundle field**

In `src/autoposter/config/schema.py`, add the soft secret to `Secrets` — immediately after the `plex_account_token` field (`:65`):

```python
    # Soft secret, same reasoning as mdblist_apikey: ``tracearr.enabled``
    # defaults to false, so a deployment that never configures Tracearr must
    # still boot. Left empty, ``build_source_clients`` builds no client at all
    # and every tracearr_most_watched definition reports itself failed while
    # the rest of the pass proceeds.
    tracearr_apikey: str = ""
```

and the matching line in `from_env`, after the `plex_account_token` line (`:80`):

```python
        values["tracearr_apikey"] = os.environ.get("AUTOPOSTER_TRACEARR_APIKEY", "")
```

Add `TracearrConfig` immediately after `SonarrConfig` (which ends at `:951`):

```python
class TracearrConfig(BaseModel):
    """Where the watch-history collections read their plays from.

    ``api_key`` is deliberately not a field here -- it comes from
    ``Secrets.tracearr_apikey`` (``AUTOPOSTER_TRACEARR_APIKEY``), the same
    pattern ``RadarrConfig`` records and every other credential in this project
    follows. ``base_url`` may be a cluster-internal hostname, which is why
    ``providers/tracearr.py`` keeps it out of every log line and every
    exception message.

    Not in ``config/live.FROZEN_SECTIONS``, for the radarr/sonarr reason: the
    client is built once per collections pass rather than once per process, so
    switching Tracearr on takes effect at the next pass rather than at the next
    restart.
    """

    enabled: bool = False
    base_url: str = ""
```

Register it on `Config`, immediately after `sonarr` (`:1043`):

```python
    tracearr: TracearrConfig = Field(default_factory=TracearrConfig)
```

In `src/autoposter/collections/builders/sources_bundle.py`, add to the `TYPE_CHECKING` block (`:37-43`, keeping alphabetical order within the `autoposter.providers` group):

```python
    from autoposter.providers.tracearr import TracearrClient
```

and to `SourceClients`, after `sonarr` (`:98`):

```python
    # None when Tracearr is disabled, has no base URL, or no
    # AUTOPOSTER_TRACEARR_APIKEY is set. Absent means None and None means the
    # builder raises: a watch-history ranking with no history to read could
    # only produce an empty collection, which one layer down means "remove
    # every member".
    tracearr: "TracearrClient | None" = None
```

In `src/autoposter/collections/service.py`, add the import beside the other provider clients and the entry in `build_source_clients`, after `sonarr=` (`:251`):

```python
        tracearr=_tracearr_client(config.tracearr, secrets.tracearr_apikey, http),
```

and the factory, immediately after `_arr_client` (`:269`):

```python
def _tracearr_client(
    service, api_key: str, http: httpx.AsyncClient
) -> TracearrClient | None:
    """One Tracearr client, or None if this deployment has no such service.

    The ``_arr_client`` triple, for the same reasons: ``enabled`` is the
    operator's switch, and a blank ``base_url`` or api key is a half-configured
    service whose every request would fail with a confusing error rather than
    with "not configured".
    """
    if not (service.enabled and service.base_url and api_key):
        return None
    return TracearrClient(http, service.base_url, api_key)
```

with `from autoposter.providers.tracearr import TracearrClient` added to the imports at the top of the file.

- [ ] **Step 6: Repair the five hand-rolled bundle configs**

Each of these builds a `SimpleNamespace` carrying "only the sections `build_source_clients` reads", so adding a section makes all five raise `AttributeError`. Add `tracearr=SimpleNamespace(enabled=False, base_url=""),` immediately after the `sonarr=` line in each:

- `tests/test_builder_mdblist.py:392-399` (`_bundle_config`)
- `tests/test_builder_tmdb.py:448-455` (`_bundle_config`)
- `tests/test_builder_tvdb.py:321-328` (`_bundle_config`)
- `tests/test_builder_plex_watchlist.py:262-268` (the inline `config = SimpleNamespace(...)`)
- `tests/test_scheduler_collections_job.py:216-221` — this one assigns the sections one at a time; add `config.tracearr = SimpleNamespace(enabled=False, base_url="")` after the `config.sonarr = ...` line.

- [ ] **Step 7: Run the new tests to verify they pass**

Run:
```
docker compose -p row79t1 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_tracearr_client.py -x -q
```
Expected: PASS, 29 passed — 25 test functions, of which the half-configured case
is parametrized three ways and the malformed-`base_url` and punycode-`base_url`
cases two ways each (22 + 3 + 2 + 2).

- [ ] **Step 8: Run the tests the new config section could have broken**

Run:
```
docker compose -p row79t1 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_mdblist.py tests/test_builder_tmdb.py tests/test_builder_tvdb.py tests/test_builder_plex_watchlist.py tests/test_scheduler_collections_job.py tests/test_config.py tests/test_config_live.py tests/test_config_impact.py tests/test_example_config_matches_schema.py -q
```
Expected: PASS, no failures. A failure here is Step 6 left incomplete.

- [ ] **Step 9: Full suite, golden gate and lint**

Run:
```
docker compose -p row79t1 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest -q
docker compose -p row79t1 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_port_golden.py -x -q
docker compose -p row79t1 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test ruff check .
```
Expected: the full suite green (record the number of passed/skipped as this branch's baseline), the golden gate green, `ruff check .` printing `All checks passed!`.

- [ ] **Step 10: Commit**

```bash
git add src/autoposter/providers/tracearr.py src/autoposter/config/schema.py \
  src/autoposter/collections/builders/sources_bundle.py src/autoposter/collections/service.py \
  tests/test_tracearr_client.py tests/fixtures/collections/tracearr_media_show.json \
  tests/fixtures/collections/tracearr_media_movie.json tests/test_builder_mdblist.py \
  tests/test_builder_tmdb.py tests/test_builder_tvdb.py tests/test_builder_plex_watchlist.py \
  tests/test_scheduler_collections_job.py
git commit --no-gpg-sign -m "feat(tracearr): the v2 public read client, its config block and its soft secret"
```

- [ ] **Step 11: Teardown**

```
docker compose -p row79t1 down
```
(**never** `down -v`.)

---

### Task 2: The ranking — buckets, the merge rule, and the 50-record oracle

Compose project name for this task: **`row79t2`**.

**Files:**
- Create: `src/autoposter/collections/activity.py`
- Create: `tests/fixtures/collections/tracearr_history_window.json`, `tracearr_history_end.json`, `tracearr_history_silo.json`, `tracearr_history_movies.json`, `tracearr_history_unidentified.json`, `tracearr_history_page2.json`
- Create: `tests/test_collection_activity.py`

**Interfaces:**
- Consumes: nothing. This module imports `dataclasses`, `datetime`, `logging` and `collections.abc` and nothing else — it is pure by construction and a test pins that.
- Produces, for Tasks 3–4:
  - `autoposter.collections.activity.MEDIA_KINDS: dict[str, str]` = `{"Movie": "movie", "Show": "show"}` (library type → the kind of thing ranked)
  - `autoposter.collections.activity.HISTORY_MEDIA_TYPE: dict[str, str]` = `{"movie": "movie", "show": "episode"}` (kind → Tracearr's `media_type`)
  - `autoposter.collections.activity.METRICS: tuple[str, ...]` = `("plays", "watch_time")`
  - `autoposter.collections.activity.Bucket` — frozen dataclass with fields `media_id: str | None`, `rating_key: str | None`, `title: str`, `plays: int`, `watch_time_ms: int`, `imdb_id: str | None = None`, `tmdb_id: str | None = None`, `tvdb_id: str | None = None`
  - `autoposter.collections.activity.rank(records: Iterable[dict], *, media_kind: str, metric: str, limit: int) -> list[Bucket]`
  - `autoposter.collections.activity.since_instant(days: int, now: datetime) -> str`

- [ ] **Step 1: Cut the six history fixtures**

Run from the repository root:

```bash
python - <<'PY'
import json, pathlib
src = pathlib.Path("docs/research/tracearr/payloads")
dst = pathlib.Path("tests/fixtures/collections")

def banked(name):
    return json.loads((src / name).read_text(encoding="utf-8"))["body"]

def write(name, payload):
    (dst / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(name, len(payload["data"]), "records, nextCursor",
          "set" if payload["meta"]["nextCursor"] else "null")

window = banked("v2-history-since-window.json")
# 1. The oracle: the whole banked body, untouched, cursor included.
write("tracearr_history_window.json", window)

# 2. The terminal page. CONSTRUCTED, not cut -- the harvest never banked one,
#    because the live instance's history never ran out inside a page budget.
#    Shape is the spec's own CursorMeta.
write("tracearr_history_end.json", {"data": [], "meta": {"nextCursor": None, "pageSize": 100}})

# 3. Page two of the paging pair: the banked cursor-followed page, verbatim
#    records, cursor nulled so the pair terminates.
page2 = banked("v2-history-page2-cursor.json")
write("tracearr_history_page2.json",
      {"data": page2["data"], "meta": {"nextCursor": None, "pageSize": 10}})

# 4. The three Silo episode records, verbatim, cursor nulled.
silo = [r for r in window["data"]
        if r.get("show_media_id") == "faf036e2-8459-4ace-a8ba-19486b6289c6"]
assert len(silo) == 3, len(silo)
write("tracearr_history_silo.json",
      {"data": silo, "meta": {"nextCursor": None, "pageSize": 100}})

# 5. The banked movie-filtered page, verbatim records, cursor nulled.
movies = banked("v2-history-movies.json")
write("tracearr_history_movies.json",
      {"data": movies["data"], "meta": {"nextCursor": None, "pageSize": 100}})

# 6. The two identity-less records ALONE, verbatim. In the full window they
#    merge into the Warehouse 13 bucket; on their own there is no bucket to
#    merge into, which is the plex-keyed last resort the builder needs proved.
lost = [r for r in window["data"]
        if not r.get("media_id") and not r.get("show_media_id")]
assert len(lost) == 2, len(lost)
write("tracearr_history_unidentified.json",
      {"data": lost, "meta": {"nextCursor": None, "pageSize": 100}})
PY
```

Expected output, exactly:

```
tracearr_history_window.json 50 records, nextCursor set
tracearr_history_end.json 0 records, nextCursor null
tracearr_history_page2.json 10 records, nextCursor null
tracearr_history_silo.json 3 records, nextCursor null
tracearr_history_movies.json 10 records, nextCursor null
tracearr_history_unidentified.json 2 records, nextCursor null
```

Then verify the cuts are byte-identical to the banked records:

```bash
python - <<'PY'
import json, pathlib
src = pathlib.Path("docs/research/tracearr/payloads")
dst = pathlib.Path("tests/fixtures/collections")
window = json.loads((src / "v2-history-since-window.json").read_text(encoding="utf-8"))["body"]
by_id = {r["id"]: r for r in window["data"]}
for name in ("tracearr_history_window", "tracearr_history_silo",
             "tracearr_history_unidentified"):
    for record in json.loads((dst / f"{name}.json").read_text(encoding="utf-8"))["data"]:
        assert record == by_id[record["id"]], (name, record["id"])
movies = json.loads((src / "v2-history-movies.json").read_text(encoding="utf-8"))["body"]
cut = json.loads((dst / "tracearr_history_movies.json").read_text(encoding="utf-8"))
assert cut["data"] == movies["data"]
page2 = json.loads((src / "v2-history-page2-cursor.json").read_text(encoding="utf-8"))["body"]
assert json.loads((dst / "tracearr_history_page2.json").read_text(encoding="utf-8"))["data"] == page2["data"]
print("every cut record is byte-identical to the banked one")
PY
```

Expected output: `every cut record is byte-identical to the banked one`.

- [ ] **Step 2: Write the failing ranking tests**

Create `tests/test_collection_activity.py`:

```python
"""Ranking Tracearr's watch history: the oracle, and the merge rule.

``tracearr_history_window.json`` is the verbatim ``body`` of
``docs/research/tracearr/payloads/v2-history-since-window.json`` --
``GET /api/v2/public/history?since=2026-07-26&pageSize=50``, one page, 50
records, ``meta.nextCursor`` non-null (so it is the newest slice of the window,
roughly 2026-08-14 to 2026-08-24, not the whole 30 days). The uuids in it are
real, unscrubbed canonical media ids: media identity is deliberately kept in
these fixtures, and the PII-bearing ``user`` block was scrubbed at capture.

**Which fixtures carry an edit, and what the edit is.** Four of them --
``tracearr_history_page2.json``, ``tracearr_history_silo.json``,
``tracearr_history_movies.json`` and ``tracearr_history_unidentified.json`` --
have ``meta.nextCursor`` set to ``null`` so a one-page fixture terminates;
every record inside them is byte-identical to the banked capture and nothing
else was touched. ``tracearr_history_end.json`` is the one fixture that is
CONSTRUCTED rather than cut: an empty page in the spec's own ``CursorMeta``
shape, written by hand because the harvest never banked a terminal page (the
live instance's history never ran out inside a page budget). Nothing else in
this directory is invented.

The harvest's own worked example (``docs/research/tracearr-api-harvest.md``
:768-819) reports **50 records, 2 skipped, 48 folded into 21 buckets, Warehouse
13 at 18 plays** -- and then says, in its own words, that the honest count for
that title is **20, not 18**. This module is that correction implemented: an
identity-less play joins the bucket whose rating keys it has already been seen
under, so the answer is **50 records, 21 buckets, Warehouse 13 at 20 plays**,
with nothing double-counted and nothing dropped.

Every expected number below was computed from the fixture, not remembered.

A few tests are the exception to all of that, and each says so in its own name
(the ``__synthetic_records`` suffix) and in its docstring. They use records
written by hand, inline, for cases the capture does not contain: every movie
record in the banked window carries a ``media_id`` and all three external ids,
so the movie branch of the merge, the per-id coalesce and the zero-as-absence
rule have no captured data behind them at all. Nothing synthetic is written to
a fixture file, so nothing invented can later be mistaken for a capture.
"""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autoposter.collections.activity import (
    HISTORY_MEDIA_TYPE,
    MEDIA_KINDS,
    METRICS,
    Bucket,
    rank,
    since_instant,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"

WAREHOUSE_13 = "1626e7f7-6d58-4172-817c-bd56f95a295d"
REACHER = "40082858-dfa4-4508-b833-61b9b2654583"
COLIN = "e9141b5c-646d-422a-97dc-38e6dd00bd53"
SILO = "faf036e2-8459-4ace-a8ba-19486b6289c6"
HOUSE_OF_THE_DRAGON = "1a87c4a5-44ea-4fc9-899f-932f1b4adfdb"
TED_LASSO = "1193b102-4538-44a7-b7bb-00dbe26dd69c"
LANTERNS = "dc76248f-854d-4a6c-a8b7-80db867efaf0"
STRANGE_NEW_WORLDS = "b336544e-bd38-405e-b8c1-3628d5b3c650"

PROJECT_HAIL_MARY = "95cf5952-ce05-4c24-ab3b-db529e4d6e16"
BLADE = "e95f63eb-12e3-402b-936e-ceaebd78849a"
TWENTY_EIGHT_YEARS_LATER = "8d08947c-caa3-4625-b1e8-5d69fa6fc91d"
JURASSIC_WORLD = "b614cf9b-e403-4e34-b97a-25c436dd4ec3"
BLADE_TRINITY = "79e224e2-5dd6-447f-9ebc-c9e798300128"


def records(name="tracearr_history_window.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["data"]


# --- the vocabulary -----------------------------------------------------------


def test_the_two_library_types_and_the_media_types_they_rank():
    assert MEDIA_KINDS == {"Movie": "movie", "Show": "show"}
    assert HISTORY_MEDIA_TYPE == {"movie": "movie", "show": "episode"}
    assert METRICS == ("plays", "watch_time")


def test_an_unknown_media_kind_or_metric_is_refused():
    with pytest.raises(ValueError, match="media kind"):
        rank(records(), media_kind="album", metric="plays", limit=5)
    with pytest.raises(ValueError, match="metric"):
        rank(records(), media_kind="show", metric="unique_users", limit=5)


# --- the oracle: shows --------------------------------------------------------


def test_the_show_ranking_reproduces_the_harvest_example_with_the_merge_rule():
    """The whole oracle, in one assertion.

    The harvest's table has Warehouse 13 at 18 and two records skipped. Both
    skipped records are Warehouse 13 episodes whose ``grandparent_rating_key``
    is "75916" -- the same key the identified Warehouse 13 records carry -- so
    they merge, and the count becomes the 20 the harvest itself calls honest.
    """
    ranked = rank(records(), media_kind="show", metric="plays", limit=100)

    assert [(bucket.media_id, bucket.plays, bucket.watch_time_ms) for bucket in ranked] == [
        (WAREHOUSE_13, 20, 42099000),
        (REACHER, 4, 7549301),
        (COLIN, 3, 5508000),
        (SILO, 3, 4982307),
        (HOUSE_OF_THE_DRAGON, 2, 7638000),
        (TED_LASSO, 2, 5478000),
        (LANTERNS, 2, 3308000),
        (STRANGE_NEW_WORLDS, 1, 3378000),
    ]
    # 37 of the 50 records are episodes, and every one of them is accounted for.
    assert sum(bucket.plays for bucket in ranked) == 37
    assert [bucket.title for bucket in ranked[:2]] == ["Warehouse 13", "Reacher"]


def test_the_two_identity_less_plays_are_merged_and_not_double_counted():
    """The merge rule, isolated: dropping the identity-less records would give
    18 (the harvest's own 10% undercount for this title), counting them as
    their own bucket would give 18 + 2 in two rows, and merging gives 20 in
    one. The banked window is 20 Warehouse 13 records in total."""
    ranked = rank(records(), media_kind="show", metric="plays", limit=100)
    warehouse = next(b for b in ranked if b.media_id == WAREHOUSE_13)

    assert warehouse.plays == 20
    assert warehouse.watch_time_ms == 42099000  # 38004000 identified + 4095000 merged
    assert [b for b in ranked if b.media_id is None] == []
    assert len(ranked) == 8


def test_the_merge_does_not_depend_on_the_order_records_arrive_in():
    """The identity-less pass runs after every identified bucket exists, so a
    page that happened to list the unidentified plays first still merges them.
    Tracearr orders newest-first and a window boundary can put them anywhere."""
    forwards = rank(records(), media_kind="show", metric="plays", limit=100)
    backwards = rank(list(reversed(records())), media_kind="show", metric="plays", limit=100)

    assert [(b.media_id, b.plays) for b in forwards] == [
        (b.media_id, b.plays) for b in backwards
    ]

    # Reversing is not the hard order. In this window the two identity-less
    # records sit at 43/44, so reversal only moves them to 5/6 -- still behind
    # a Warehouse 13 record. The order that actually discriminates a one-pass
    # merge is the one no capture happens to contain: both of them FIRST,
    # before any identified bucket exists.
    page = records()
    lost = [r for r in page if not r.get("media_id") and not r.get("show_media_id")]
    hoisted = lost + [r for r in page if r not in lost]
    ranked = rank(hoisted, media_kind="show", metric="plays", limit=100)

    assert len(lost) == 2
    assert next(b for b in ranked if b.media_id == WAREHOUSE_13).plays == 20
    assert [b for b in ranked if b.rating_key is not None] == []


def test_the_merge_works_for_movies_too__synthetic_records():
    """The movie half of the merge rule, on **synthetic records written for
    this test** -- not banked, not scrubbed capture, not evidence of anything
    Tracearr has ever sent.

    It needs saying because every other fixture in this module is real: all 13
    movie records in the banked window carry a ``media_id``, so the movie
    branch of the identity-less merge has no observed data behind it at all.
    That is a gap in the capture, not proof the case cannot happen -- the
    unidentified-play condition is a property of what the media server
    reported, and nothing about it is episode-specific. The branch is
    implemented symmetrically for both kinds, so it is tested symmetrically,
    with the source of the data stated plainly.

    Movies key off ``rating_key`` rather than ``grandparent_rating_key`` (a
    movie has no grandparent), which is the one thing that differs from the
    show case and the one thing this exercises.
    """
    identified = {
        "media_type": "movie", "media_id": "11111111-1111-4111-8111-000000000001",
        "media_title": "A Synthetic Film", "rating_key": "X", "duration_ms": 1000,
        "imdb_id": "tt0000001", "tmdb_id": 1, "tvdb_id": None,
    }
    same_key = {
        "media_type": "movie", "media_id": None, "media_title": "A Synthetic Film",
        "rating_key": "X", "duration_ms": 500,
        "imdb_id": None, "tmdb_id": None, "tvdb_id": None,
    }
    other_key = {
        "media_type": "movie", "media_id": None, "media_title": "Another One",
        "rating_key": "Y", "duration_ms": 250,
        "imdb_id": None, "tmdb_id": None, "tvdb_id": None,
    }

    ranked = rank(
        [identified, same_key, other_key], media_kind="movie", metric="plays", limit=10
    )

    assert ranked == [
        Bucket(
            media_id="11111111-1111-4111-8111-000000000001",
            rating_key=None,
            title="A Synthetic Film",
            plays=2,
            watch_time_ms=1500,
            imdb_id="tt0000001",
            tmdb_id="1",
            tvdb_id=None,
        ),
        Bucket(
            media_id=None,
            rating_key="Y",
            title="Another One",
            plays=1,
            watch_time_ms=250,
        ),
    ]


def test_an_identity_less_play_with_no_bucket_to_join_becomes_a_plex_bucket():
    """The documented last resort. The two records here are the same two that
    merge in the full window; alone, there is no Warehouse 13 bucket for them
    to join, so they form one bucket keyed by the Plex rating key they share --
    which resolves free against the engine's own index and is dropped, not
    guessed, when it is stale."""
    ranked = rank(
        records("tracearr_history_unidentified.json"),
        media_kind="show", metric="plays", limit=10,
    )

    assert ranked == [
        Bucket(
            media_id=None,
            rating_key="75916",
            title="Warehouse 13",
            plays=2,
            watch_time_ms=4095000,
        )
    ]


# --- the never-an-episode-id invariant ----------------------------------------


def test_a_show_bucket_never_carries_an_external_id():
    """The invariant, made structural rather than remembered.

    On an episode record ``imdb_id``/``tmdb_id``/``tvdb_id`` are the EPISODE's,
    and ``GET /media/show:tvdb:<episode id>`` is a live-verified 404 (harvest
    finding 3). An episode id emitted as a collection member would resolve to
    nothing on a Show library, and "matched nothing" is indistinguishable from
    a correct empty collection -- so the ids are not carried out of the ranking
    at all, and the builder has nothing to accidentally emit.
    """
    for bucket in rank(records(), media_kind="show", metric="plays", limit=100):
        assert bucket.imdb_id is None, bucket.title
        assert bucket.tmdb_id is None, bucket.title
        assert bucket.tvdb_id is None, bucket.title

    # And the ids really are present on the records, so the assertion above is
    # about what the ranking DOES rather than about an empty input.
    episodes = [r for r in records() if r["media_type"] == "episode"]
    assert any(r.get("tvdb_id") for r in episodes)


# --- the oracle: movies -------------------------------------------------------


def test_a_movie_bucket_carries_the_movies_own_ids():
    """The short-circuit that is real for movies and only for movies: a movie
    record's external ids are the MOVIE's, so no ``/media/{ref}`` call is
    needed to reach them."""
    ranked = rank(records(), media_kind="movie", metric="plays", limit=100)
    blade_trinity = next(b for b in ranked if b.media_id == BLADE_TRINITY)

    assert (blade_trinity.tmdb_id, blade_trinity.imdb_id, blade_trinity.tvdb_id) == (
        "36648", "tt0359013", "1700",
    )
    assert blade_trinity.title == "Blade: Trinity"


# --- what a bucket keeps when its records disagree ----------------------------


def test_each_external_id_comes_from_the_first_record_that_has_one__synthetic_records():
    """Per-id coalesce, not first-record-wins -- and the same answer in either
    order.

    **Synthetic records**: all 13 banked movie records carry all three ids, so
    nothing captured can reach this branch. It still matters, because the
    module advertises folding one title across servers and a movie's ids are
    emitted straight off the record with no ``/media`` lookup: a bucket that
    kept an id-less first record's nulls would resolve to nothing downstream,
    which is a silently empty collection rather than a visible failure.
    """
    bare = {
        "media_type": "movie", "media_id": "11111111-1111-4111-8111-000000000002",
        "media_title": "A Synthetic Film", "rating_key": "A", "duration_ms": 1000,
        "imdb_id": None, "tmdb_id": None, "tvdb_id": None,
    }
    bearing = {**bare, "imdb_id": "tt9", "tmdb_id": 9}

    for arrival in ([bare, bearing], [bearing, bare]):
        bucket = rank(arrival, media_kind="movie", metric="plays", limit=10)[0]
        assert bucket.plays == 2
        assert (bucket.imdb_id, bucket.tmdb_id, bucket.tvdb_id) == ("tt9", "9", None)


def test_a_bucket_is_named_by_the_first_record_that_carries_a_title__synthetic_records():
    """Title is first NON-empty, for the same reason and by the same rule: the
    uuid the ranking falls back to is a placeholder, not a name, and a blank
    title on whichever record happened to arrive first must not stick."""
    blank = {
        "media_type": "movie", "media_id": "11111111-1111-4111-8111-000000000003",
        "media_title": "", "rating_key": "B", "duration_ms": 1000,
    }
    named = {**blank, "media_title": "A Synthetic Film"}

    ranked = rank([blank, named], media_kind="movie", metric="plays", limit=10)

    assert ranked[0].title == "A Synthetic Film"


def test_a_zero_external_id_is_absence_in_either_shape__synthetic_records():
    """``0`` and ``"0"`` are the same absence marker one JSON coercion apart,
    and an id of "0" resolves to nothing anywhere -- so neither is carried out
    as an id. Synthetic for the same reason as above."""
    record = {
        "media_type": "movie", "media_id": "11111111-1111-4111-8111-000000000004",
        "media_title": "A Synthetic Film", "rating_key": "C", "duration_ms": 1000,
        "imdb_id": 0, "tmdb_id": "0", "tvdb_id": "",
    }

    bucket = rank([record], media_kind="movie", metric="plays", limit=10)[0]

    assert (bucket.imdb_id, bucket.tmdb_id, bucket.tvdb_id) == (None, None, None)


def test_the_movie_ranking_is_thirteen_single_play_buckets_ordered_by_watch_time():
    """Every movie in this window was played once, so the ``plays`` metric is
    one thirteen-way tie -- which is exactly why the ordering contract needs a
    tiebreak, and why the tiebreak is the other measure rather than the uuid."""
    ranked = rank(records(), media_kind="movie", metric="plays", limit=100)

    assert len(ranked) == 13
    assert {bucket.plays for bucket in ranked} == {1}
    assert [bucket.media_id for bucket in ranked[:5]] == [
        PROJECT_HAIL_MARY,          # 9318000 ms
        BLADE,                      # 6920569 ms
        TWENTY_EIGHT_YEARS_LATER,   # 6863000 ms
        JURASSIC_WORLD,             # 6859000 ms
        BLADE_TRINITY,              # 6535412 ms
    ]


def test_records_of_the_other_media_type_are_not_ranked():
    """The builder asks ``/history`` for one ``media_type``, and this is the
    second half of the same guard: 37 episode records in this window and 13
    movie records, and neither ranking ever sees the other's."""
    shows = rank(records(), media_kind="show", metric="plays", limit=100)
    movies = rank(records(), media_kind="movie", metric="plays", limit=100)

    assert sum(bucket.plays for bucket in shows) == 37
    assert sum(bucket.plays for bucket in movies) == 13


# --- the metric and the cap ---------------------------------------------------


def test_watch_time_ranks_by_summed_duration_and_reorders_the_table():
    """Both metrics come free from one page-through, and they genuinely
    disagree: House of the Dragon has two plays and outranks Reacher's four on
    watch time."""
    ranked = rank(records(), media_kind="show", metric="watch_time", limit=100)

    assert [(b.media_id, b.watch_time_ms) for b in ranked] == [
        (WAREHOUSE_13, 42099000),
        (HOUSE_OF_THE_DRAGON, 7638000),
        (REACHER, 7549301),
        (COLIN, 5508000),
        (TED_LASSO, 5478000),
        (SILO, 4982307),
        (STRANGE_NEW_WORLDS, 3378000),
        (LANTERNS, 3308000),
    ]


def test_the_limit_truncates_after_ranking_and_not_before():
    ranked = rank(records(), media_kind="show", metric="plays", limit=3)

    assert [bucket.media_id for bucket in ranked] == [WAREHOUSE_13, REACHER, COLIN]


def test_a_malformed_duration_costs_the_bucket_its_watch_time_and_not_its_place():
    """``duration_ms`` is a real integer on all 50 banked records, unlike
    ``progress_ms``/``total_duration_ms`` which arrive as strings and which
    nothing here reads. It is coerced anyway: a title dropped from a ranking
    over one malformed field is a silently wrong collection."""
    mangled = [dict(record) for record in records("tracearr_history_silo.json")]
    mangled[0]["duration_ms"] = None
    mangled[1]["duration_ms"] = "547000"

    ranked = rank(mangled, media_kind="show", metric="plays", limit=10)

    assert len(ranked) == 1
    assert ranked[0].plays == 3
    assert ranked[0].watch_time_ms == 547000 + 2811000


def test_a_float_shaped_duration_is_salvaged_and_only_garbage_is_worth_nothing():
    """Salvaging is the whole point of the coercion, so ``"12.5"`` must not be
    thrown away with the rest: ``int("12.5")`` raises, and losing a play's
    entire watch time to a decimal point is the same silently wrong collection
    the previous test guards. ``"junk"`` really has nothing to salvage."""
    mangled = [dict(record) for record in records("tracearr_history_silo.json")]
    mangled[0]["duration_ms"] = "1624307"
    mangled[1]["duration_ms"] = "12.5"
    mangled[2]["duration_ms"] = "junk"

    ranked = rank(mangled, media_kind="show", metric="plays", limit=10)

    assert ranked[0].plays == 3
    assert ranked[0].watch_time_ms == 1624307 + 12


def test_an_empty_window_ranks_to_nothing():
    """Not an error: a deployment nobody watched anything on in the window is
    data. The builder's caller decides what an empty membership means."""
    assert rank([], media_kind="show", metric="plays", limit=10) == []


# --- the window ---------------------------------------------------------------


def test_since_instant_is_an_instant_and_not_a_calendar_day():
    """``/history``'s ``since`` is an instant; ``/media/{ref}/stats``'
    ``last_7``/``last_30`` are UTC calendar-day buckets, and the harvest shows
    the two disagreeing on live data (Silo: 5 plays against 3). Nothing in this
    phase consults the stats windows, and this is the shape that keeps them
    apart. ``now`` is an argument so this is a fixed assertion rather than a
    wall-clock one (roadmap row 119)."""
    now = datetime(2026, 8, 25, 6, 38, 38, tzinfo=UTC)

    assert since_instant(30, now) == "2026-07-26T06:38:38Z"
    assert since_instant(1, now) == "2026-08-24T06:38:38Z"
    assert since_instant(365, now) == "2025-08-25T06:38:38Z"


def test_since_instant_normalises_a_non_utc_now_to_utc():
    """The instant is Z-suffixed, so a process running in a non-UTC timezone
    must not send a local wall-clock time as though it were UTC."""
    from datetime import timedelta, timezone

    helsinki = timezone(timedelta(hours=3))
    now = datetime(2026, 8, 25, 9, 38, 38, tzinfo=helsinki)

    assert since_instant(30, now) == "2026-07-26T06:38:38Z"


def test_since_instant_refuses_a_naive_now_rather_than_guessing_a_timezone():
    """The other half of the same hazard, and the one that has no signal.
    ``.astimezone`` reads a naive datetime as HOST LOCAL time, so the obvious
    caller mistake -- ``datetime.utcnow()`` -- would shift the window by the
    host's offset, silently, and differently per host. The contract is that
    callers pass an aware ``now``; the module reads no clock of its own, so
    there is nothing here that could supply a default."""
    with pytest.raises(ValueError, match="timezone-aware"):
        since_instant(30, datetime(2026, 8, 25, 6, 38, 38))


# --- purity -------------------------------------------------------------------


def test_the_ranking_module_imports_nothing_that_can_do_io():
    """Structural, the ``catalog.py`` precedent: this module is a pure function
    over records, so it can be exercised against the banked oracle without a
    transport, a config or a client -- and so a future edit cannot quietly give
    it a network call."""
    import ast

    source = Path("src/autoposter/collections/activity.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"collections", "dataclasses", "datetime", "logging"}, imported
```

- [ ] **Step 3: Run the tests to verify they fail**

Run:
```
docker compose -p row79t2 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_collection_activity.py -x -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'autoposter.collections.activity'`.

- [ ] **Step 4: Write the ranking module**

Create `src/autoposter/collections/activity.py`:

```python
"""Most-watched, computed from Tracearr's raw watch history.

Tracearr exposes no ranking endpoint of any kind (``docs/research/
tracearr-api-harvest.md``), so this module is the ranking: a pure function over
the records ``providers/tracearr.py`` pages back. No HTTP, no config, no
clients, no database -- which is what lets the 50 banked records of
``tests/fixtures/collections/tracearr_history_window.json`` serve as a real
oracle, and what keeps a ranking recomputable per pass rather than stored.

**A record is a play.** Tracearr groups sessions into resume chains and drops
chains under two minutes before it answers, so ``len(bucket)`` *is* the play
count Tracearr itself reports and nothing here re-filters. Watch time is the
sum of ``duration_ms``, which is a real integer (unlike ``progress_ms`` and
``total_duration_ms``, which arrive as strings and which nothing here reads).

**What a bucket is keyed by.** ``show_media_id`` for shows, ``media_id`` for
movies -- both canonical Tracearr ids and both merge-aware, so the same title
seen on two servers folds into one bucket with no title normalization, and
every episode of a show folds into the show.

**The identity-less plays, and why they are merged rather than dropped.** Some
plays carry no media identity at all: ``media_id``, ``show_media_id``,
``library_id`` and all three external ids null, while ``rating_key`` and
``grandparent_rating_key`` are still present. In the banked window that is 2
records of 50 -- both episodes of one show, whose honest count is therefore 20
and not the 18 a naive grouping reports. So every bucket also records the Plex
rating keys its own records were seen under, and an identity-less play joins
the bucket that has already been seen under its key. Nothing is double counted
(the play lands in exactly one bucket), nothing is split (the show stays one
row), no extra API call is spent, and there is no operator knob: an undercount
nobody can see is not a choice worth offering.

That merge runs as a second pass over the whole page, after every identified
bucket exists, so the answer does not depend on the order records arrive in.

**Multi-server caveat.** Plex rating keys are server-scoped, so on a
multi-server Tracearr two servers could in principle mint the same key for
different titles and an identity-less play could join the wrong bucket. The
deployment this was built for runs one server; the exposure is bounded to the
identity-less records only (2 of 50 here), and the alternative -- one
``/media/{ref}`` call per unidentified play -- buys nothing, because a record
with no media id has nothing to look up.

**Never an episode id.** A show bucket deliberately carries no external ids at
all. On an episode record ``imdb_id``/``tmdb_id``/``tvdb_id`` are the EPISODE's,
and ``GET /media/show:tvdb:<episode id>`` is a live-verified 404 -- so an
episode id emitted as a collection member resolves to nothing on a Show
library, and "matched nothing" looks exactly like a correct empty collection.
The ids are dropped here rather than guarded downstream, so the builder has
nothing to emit by accident. A show's real ids come from its media document,
which is the builder's business.
"""
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

__all__ = [
    "HISTORY_MEDIA_TYPE",
    "MEDIA_KINDS",
    "METRICS",
    "Bucket",
    "rank",
    "since_instant",
]

# Library type -> the kind of thing a most-watched collection of that library
# ranks. Also the ``allowed`` table the builder hands ``require_library_type``,
# the ``CHART_ENDPOINTS`` idiom: one table, so the two cannot disagree.
MEDIA_KINDS: dict[str, str] = {"Movie": "movie", "Show": "show"}

# ...and the Tracearr ``media_type`` whose records carry it. A show is ranked
# from its EPISODES' plays: Tracearr records no play against a show itself.
HISTORY_MEDIA_TYPE: dict[str, str] = {"movie": "movie", "show": "episode"}

# What "most watched" can mean. Both are free from one page-through -- plays is
# the record count, watch time is the summed duration. Deliberately not
# completed-only (``watched``) or unique users: the vocabulary can grow when
# something asks for it, and each new value is a new thing to explain.
METRICS: tuple[str, ...] = ("plays", "watch_time")

# Per media kind: what buckets a record, which Plex key that bucket is seen
# under, and which field holds the name a bucket is reported by.
_GROUP_FIELD = {"movie": "media_id", "show": "show_media_id"}
_RATING_KEY_FIELD = {"movie": "rating_key", "show": "grandparent_rating_key"}
_TITLE_FIELD = {"movie": "media_title", "show": "show_title"}

_EXTERNAL_ID_FIELDS = ("imdb_id", "tmdb_id", "tvdb_id")


@dataclass(frozen=True)
class Bucket:
    """One title's plays in the window.

    ``media_id`` is Tracearr's canonical uuid for the title -- the show's for a
    show, the movie's for a movie -- and is None for the last-resort bucket
    formed from identity-less plays alone. ``rating_key`` is the Plex key such
    a bucket was formed under, and is None otherwise: exactly one of the two is
    set, and which one decides how the builder names the members.

    The three external ids are populated for MOVIE buckets only. See the
    module docstring's never-an-episode-id rule for why a show bucket carries
    none at all.
    """

    media_id: str | None
    rating_key: str | None
    title: str
    plays: int
    watch_time_ms: int
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None


def since_instant(days: int, now: datetime) -> str:
    """The ``since`` value for a ``days``-long window ending at ``now``.

    An instant, and never a calendar day. ``/history``'s ``since``/``until``
    are instants while ``/media/{ref}/stats``' ``last_7``/``last_30`` are UTC
    calendar-day buckets, and the harvest demonstrates the two disagreeing on
    live data -- 5 plays against 3 for the same title in overlapping windows.
    Nothing in this phase consults the stats windows at all, and this shape is
    what keeps the two from being mixed by accident.

    Takes ``now`` rather than reading the clock, so it can be asserted against
    a fixed instant instead of a wall-clock delta (roadmap row 119). ``now``
    must be timezone-aware: ``.astimezone`` reads a naive datetime as HOST
    LOCAL time, which would shift the window by the host's offset silently and
    differently on every differently-configured host.
    """
    if now.tzinfo is None:
        raise ValueError("since_instant needs a timezone-aware now")
    moment = (now - timedelta(days=days)).astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def rank(
    records: Iterable[dict], *, media_kind: str, metric: str, limit: int
) -> list[Bucket]:
    """The ``limit`` most-watched titles in ``records``, best first.

    ``media_kind`` is ``"movie"`` or ``"show"``; ``metric`` is ``"plays"`` or
    ``"watch_time"``. Records of the other media type are ignored -- the
    builder already asks ``/history`` for one kind, and this is the second half
    of the same guard.
    """
    if media_kind not in _GROUP_FIELD:
        raise ValueError(
            f"unknown media kind {media_kind!r}: known media kinds are "
            + ", ".join(sorted(_GROUP_FIELD))
        )
    if metric not in METRICS:
        raise ValueError(
            f"unknown metric {metric!r}: known metrics are " + ", ".join(METRICS)
        )

    wanted = HISTORY_MEDIA_TYPE[media_kind]
    group_field = _GROUP_FIELD[media_kind]
    key_field = _RATING_KEY_FIELD[media_kind]
    title_field = _TITLE_FIELD[media_kind]
    mine = [record for record in records if record.get("media_type") == wanted]

    buckets: dict[str, dict] = {}
    order: list[str] = []
    identity_less: list[dict] = []

    for record in mine:
        group = record.get(group_field)
        if not group:
            identity_less.append(record)
            continue
        entry = buckets.get(group)
        if entry is None:
            entry = buckets[group] = {
                "media_id": group,
                "rating_key": None,
                "title": "",
                "plays": 0,
                "watch_time_ms": 0,
                "keys": set(),
                # Populated for movies only -- the never-an-episode-id rule.
                **{field: None for field in _EXTERNAL_ID_FIELDS},
            }
            order.append(group)
        # First NON-EMPTY name and, per id independently, first NON-NULL id --
        # not whichever record happened to create the bucket. Under the
        # cross-server folding above one server's record can carry a blank
        # title or no ids where another's does, and a movie's ids are emitted
        # straight off the record with no ``/media`` call, so a bucket that
        # kept an id-less first record's nulls would resolve to nothing at all.
        if not entry["title"]:
            entry["title"] = _title(record, title_field)
        if media_kind == "movie":
            for field in _EXTERNAL_ID_FIELDS:
                if entry[field] is None:
                    entry[field] = _external_id(record.get(field))
        entry["plays"] += 1
        entry["watch_time_ms"] += _duration_ms(record)
        seen = record.get(key_field)
        if seen:
            entry["keys"].add(str(seen))

    # The second pass, and it has to be a second pass: an identity-less play
    # can appear before the bucket it belongs to (Tracearr answers newest
    # first, and a window boundary can land anywhere).
    for record in identity_less:
        seen = record.get(key_field) or record.get("rating_key")
        if not seen:
            # No identity and no Plex key: there is nothing to bucket it under
            # and nothing to guess. Logged so a window full of them is
            # visible, and not raised -- one unusable record must not take a
            # whole collection down.
            logger.debug(
                "a Tracearr play of %r carries neither a media id nor a rating key",
                record.get("media_title"),
            )
            continue
        seen = str(seen)
        home = next((key for key in order if seen in buckets[key]["keys"]), None)
        if home is None:
            home = "plex:%s" % seen
            if home not in buckets:
                buckets[home] = {
                    "media_id": None,
                    "rating_key": seen,
                    "title": "",
                    "plays": 0,
                    "watch_time_ms": 0,
                    "keys": {seen},
                    **{field: None for field in _EXTERNAL_ID_FIELDS},
                }
                order.append(home)
        if not buckets[home]["title"]:
            buckets[home]["title"] = _title(record, title_field)
        buckets[home]["plays"] += 1
        buckets[home]["watch_time_ms"] += _duration_ms(record)

    ranked = [
        Bucket(
            media_id=entry["media_id"],
            rating_key=entry["rating_key"],
            # The id is the last-resort placeholder, and only when no record in
            # the whole bucket carried a name.
            title=entry["title"] or entry["media_id"] or entry["rating_key"],
            plays=entry["plays"],
            watch_time_ms=entry["watch_time_ms"],
            imdb_id=entry["imdb_id"],
            tmdb_id=entry["tmdb_id"],
            tvdb_id=entry["tvdb_id"],
        )
        for entry in (buckets[key] for key in order)
    ]
    ranked.sort(key=lambda bucket: _sort_key(bucket, metric))
    return ranked[:limit]


def _sort_key(bucket: Bucket, metric: str):
    """Best first, and deterministic all the way down.

    The tiebreak is the *other* measure, not the id: in a 30-day window every
    movie may well have exactly one play, and breaking that thirteen-way tie by
    uuid would order a collection alphabetically by an identifier no operator
    has ever seen. The id is the last term only so two titles with identical
    measures still order the same way on every pass -- a collection's member
    order is written to Plex, and an order that shuffles is a diff every pass.
    """
    primary, secondary = (
        (bucket.plays, bucket.watch_time_ms)
        if metric == "plays"
        else (bucket.watch_time_ms, bucket.plays)
    )
    return (-primary, -secondary, bucket.media_id or "", bucket.rating_key or "")


def _title(record: dict, title_field: str) -> str:
    """The name one record gives its title, or "" when it gives none."""
    return record.get(title_field) or record.get("media_title") or ""


def _duration_ms(record: dict) -> int:
    """``duration_ms`` as an int, or 0 if it is unusable.

    Observed as a real integer on all 50 banked records -- unlike
    ``progress_ms``/``total_duration_ms``, which arrive as strings and which
    nothing here reads. Coerced anyway, and defaulted rather than raised: a
    title dropped from a ranking over one malformed field is a silently wrong
    collection, which is the outcome this whole module is arranged to avoid.

    Salvaging is the point, so a float-shaped string is salvaged too: losing a
    whole play's watch time to a decimal point would be that same silently
    wrong collection. Only genuine garbage is worth 0.
    """
    value = record.get("duration_ms")
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _external_id(value) -> str | None:
    """One external id as a string, or None when the record carries none.

    ``0`` and ``"0"`` are both read as absence rather than as an id: they are
    the same absence marker one JSON coercion apart, and an id of "0" resolves
    to nothing anywhere.
    """
    if value is None or value == 0 or str(value) in ("", "0"):
        return None
    return str(value)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```
docker compose -p row79t2 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_collection_activity.py -x -q
```
Expected: PASS, 18 passed.

- [ ] **Step 6: Prove the merge rule with a mutation**

Temporarily change the second pass so an identity-less record always forms its own bucket — replace

```python
        home = next((key for key in order if seen in buckets[key]["keys"]), None)
```

with

```python
        home = None
```

Run:
```
docker compose -p row79t2 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_collection_activity.py -q
```
Expected: FAIL — `test_the_show_ranking_reproduces_the_harvest_example_with_the_merge_rule`, `test_the_two_identity_less_plays_are_merged_and_not_double_counted`, `test_the_merge_does_not_depend_on_the_order_records_arrive_in` and `test_the_merge_works_for_movies_too__synthetic_records` all red, with Warehouse 13 at 18 plays and a ninth bucket appearing. Four reds, and the fourth is what says the movie branch is really covered rather than merely written. **Restore the line** and re-run to green before continuing.

- [ ] **Step 7: Golden gate and lint**

Run:
```
docker compose -p row79t2 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_port_golden.py -x -q
docker compose -p row79t2 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test ruff check .
```
Expected: golden green; `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
git add src/autoposter/collections/activity.py tests/test_collection_activity.py \
  tests/fixtures/collections/tracearr_history_window.json \
  tests/fixtures/collections/tracearr_history_end.json \
  tests/fixtures/collections/tracearr_history_page2.json \
  tests/fixtures/collections/tracearr_history_silo.json \
  tests/fixtures/collections/tracearr_history_movies.json \
  tests/fixtures/collections/tracearr_history_unidentified.json
git commit --no-gpg-sign -m "feat(tracearr): rank watch history into buckets, merging the identity-less plays"
```

- [ ] **Step 9: Teardown**

```
docker compose -p row79t2 down
```

---

### Task 3: The builder, its registration, and the two catalog rows

Compose project name for this task: **`row79t3`**.

**Files:**
- Create: `src/autoposter/collections/builders/tracearr.py`
- Modify: `src/autoposter/collections/builders/__init__.py` (the import block `:10-73`, the `register(...)` block `:75-132`)
- Modify: `src/autoposter/collections/catalog.py` (a `TRACEARR_PRESETS` block after `CHART_PRESETS` at `:656-658`; the count-checksum comment at `:1232-1247`; the `CATALOG` tuple at `:1248-1259`)
- Modify: `tests/test_collection_catalog.py:186-196` (`CATALOG_CHECKSUM["charts"]`)
- Create: `tests/test_builder_tracearr.py`

**Interfaces:**
- Consumes: `TracearrClient`, `TracearrRefused`, `API_PREFIX` (Task 1); `rank`, `Bucket`, `since_instant`, `MEDIA_KINDS`, `HISTORY_MEDIA_TYPE`, `METRICS` (Task 2); `BuilderContext`, `BuilderResult`, `require_library_type` (`src/autoposter/collections/builders/base.py`).
- Produces, for Task 4:
  - `autoposter.collections.builders.tracearr.TracearrBuilderRefused(Exception)`
  - `autoposter.collections.builders.tracearr.TracearrMostWatchedParams` — `metric: Literal["plays", "watch_time"] = "plays"`, `days: int = 30` (ge=1, le=365), `limit: int = 20` (ge=1, le=100), `extra="forbid"`
  - `autoposter.collections.builders.tracearr.TracearrMostWatchedBuilder`, `type_name = "tracearr_most_watched"`, registered
  - Catalog keys `chart_tracearr_movies` and `chart_tracearr_shows`, titles `"Most Watched Movies"` and `"Most Watched Shows"`

- [ ] **Step 1: Write the failing builder tests**

Create `tests/test_builder_tracearr.py`:

```python
"""``tracearr_most_watched``: the household's own most-played titles.

Never touches a real instance -- MockTransport only. The history fixtures are
verbatim cuts of the banked payloads (see ``tests/test_collection_activity.py``
for the provenance), with ``meta.nextCursor`` nulled where a fixture exists to
be a single page.

Four things are pinned here, and only the first is transport:

- **the window and the media type reach the request**, so a 30-day movie
  collection is not silently a 25-record default of everything;
- **a movie's ids come off the record and cost no extra call**, which is the
  short-circuit the harvest proves is safe for movies and only for movies;
- **a show's ids come off its media document**, one call per surviving bucket,
  memoised per pass -- and are never the episode-level ids the records carry;
- **one missing title is dropped and counted, one broken source raises.** An
  unconfigured Tracearr, a dead connection or a 404 on the history window all
  fail the definition; a 404 on one ranked title's media document does not,
  because a title deleted between the two calls must not empty a live
  collection.
"""
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.tracearr import TracearrBuilderRefused
from autoposter.providers.tracearr import (
    API_PREFIX,
    TracearrClient,
    TracearrNotFound,
    TracearrRefused,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"

BASE_URL = "http://tracearr.test.invalid"
API_KEY = "trr_pub_test"
SILO_UUID = "faf036e2-8459-4ace-a8ba-19486b6289c6"
REACHER_UUID = "40082858-dfa4-4508-b833-61b9b2654583"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _matched(payload, status=200):
    return httpx.Response(
        status, json=payload, headers={"x-ratelimit-limit": "240"}
    )


def _routed(routes: dict, seen: list | None = None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        entry = routes.get(request.url.path)
        if entry is None:
            return _matched(
                {"statusCode": 404, "error": "NotFoundError", "message": "Not Found"},
                status=404,
            )
        if callable(entry):
            return entry(request)
        return _matched(entry)

    return httpx.MockTransport(handler)


def _sources(http):
    return SourceClients(tracearr=TracearrClient(http, BASE_URL, API_KEY))


def _ctx(sources, library_type="Show", run_cache=None, **params):
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library,
        library_type=library_type,
        config=params,
        sources=sources,
        run_cache={} if run_cache is None else run_cache,
    )


def _build(ctx):
    return REGISTRY["tracearr_most_watched"].build(ctx)


# --- registration and params --------------------------------------------------


def test_the_builder_is_registered_under_its_own_name():
    assert REGISTRY["tracearr_most_watched"].type_name == "tracearr_most_watched"


def test_the_defaults_are_thirty_days_of_plays_capped_at_twenty():
    from autoposter.collections.builders.tracearr import TracearrMostWatchedParams

    params = TracearrMostWatchedParams()

    assert (params.days, params.metric, params.limit) == (30, "plays", 20)


@pytest.mark.parametrize(
    "params",
    [
        {"metric": "unique_users"},
        {"days": 0},
        {"days": 4000},
        {"limit": 0},
        {"limit": 1000},
        {"day": 30},
    ],
    ids=["unknown-metric", "zero-days", "too-many-days", "zero-limit",
         "over-the-call-budget", "misspelled-key"],
)
async def test_a_bad_params_block_is_refused(params):
    """``extra="forbid"``, and bounds on both numbers: ``limit`` is what caps
    the ``/media/{ref}`` calls a Show collection spends out of the shared
    240/min budget, so an unbounded one is a budget hazard rather than a
    preference."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(ValidationError):
            await _build(_ctx(_sources(http), **params))


# --- refusals -----------------------------------------------------------------


async def test_an_unconfigured_tracearr_raises_rather_than_building_nothing():
    with pytest.raises(TracearrBuilderRefused, match="not configured"):
        await _build(_ctx(SourceClients()))


async def test_a_library_of_the_wrong_type_is_refused_by_name():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch, match="Movie or Show"):
            await _build(_ctx(_sources(http), library_type="Artist"))


# --- the request --------------------------------------------------------------


async def test_the_window_and_the_media_type_reach_the_history_request():
    seen: list = []
    routes = {f"{API_PREFIX}/history": load("tracearr_history_movies.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await _build(_ctx(_sources(http), library_type="Movie", days=7))

    assert seen[0].url.path == f"{API_PREFIX}/history"
    assert seen[0].url.params["media_type"] == "movie"
    # The instant itself is the clock's; that it is an instant, and that one
    # was sent at all, is the builder's. ``since_instant`` is asserted exactly
    # in tests/test_collection_activity.py, against a fixed ``now``.
    assert seen[0].url.params["since"].endswith("Z")


async def test_a_show_library_asks_for_episodes_because_shows_have_no_plays():
    seen: list = []
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await _build(_ctx(_sources(http)))

    assert seen[0].url.params["media_type"] == "episode"


# --- movies: the short-circuit ------------------------------------------------


async def test_a_movie_collection_takes_its_ids_off_the_records():
    """TMDb first then IMDb, the ``mdblist_list`` Movie preference: which guid
    a Movie library's items are most likely to carry, and every fallback is a
    member that would otherwise be dropped."""
    seen: list = []
    routes = {f"{API_PREFIX}/history": load("tracearr_history_movies.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await _build(_ctx(_sources(http), library_type="Movie", limit=3))

    assert result.ids == [("tmdb", "36647"), ("tmdb", "36648"), ("tmdb", "36586")]
    # One request, and it was the history page: no ``/media/{ref}`` call is
    # needed or made for movies.
    assert [request.url.path for request in seen] == [f"{API_PREFIX}/history"]


async def test_the_movie_short_circuit_agrees_with_the_media_document():
    """The proof that the short-circuit is *safe*, not merely cheap: the ids on
    Blade: Trinity's history record are the same ids its own media document
    carries. (For an episode they would not be -- see the invariant test.)"""
    record = next(
        r for r in load("tracearr_history_movies.json")["data"]
        if r["media_id"] == "79e224e2-5dd6-447f-9ebc-c9e798300128"
    )
    document = load("tracearr_media_movie.json")

    assert record["tmdb_id"] == document["tmdb_id"]
    assert record["imdb_id"] == document["imdb_id"]
    assert record["tvdb_id"] == document["tvdb_id"]


async def test_a_movie_with_no_usable_id_is_dropped_and_logged(caplog):
    """The ``mdblist_list`` judgement: Tracearr knowing no id for one title is
    the resolver's ordinary "the library does not have this" one step earlier,
    and must not take the collection down."""
    page = load("tracearr_history_movies.json")
    page["data"] = [dict(page["data"][0]) | {"tmdb_id": None, "imdb_id": None}]
    routes = {f"{API_PREFIX}/history": page}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with caplog.at_level(logging.DEBUG):
            result = await _build(_ctx(_sources(http), library_type="Movie"))

    assert result.ids == []
    assert "no usable id" in caplog.text


# --- shows: the media document ------------------------------------------------


async def test_a_show_collection_takes_its_ids_off_the_media_document():
    """TVDb first then TMDb then IMDb, the ``mdblist_list`` Show preference."""
    seen: list = []
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await _build(_ctx(_sources(http)))

    assert result.ids == [("tvdb", "403245")]
    assert [request.url.path for request in seen] == [
        f"{API_PREFIX}/history",
        f"{API_PREFIX}/media/{SILO_UUID}",
    ]


async def test_no_id_a_show_collection_emits_is_an_episode_id():
    """THE INVARIANT (adjudication A-resolution, and its own named test).

    On an episode record ``imdb_id``/``tmdb_id``/``tvdb_id`` are the EPISODE's:
    the three Silo plays in this fixture carry tvdb 11751886 and 11751885,
    while Silo the show is tvdb 403245. ``GET /media/show:tvdb:11751886`` is a
    live-verified 404 (banked as
    ``docs/research/tracearr/payloads/v2-media-show-by-tvdb-ref.json``), so an
    episode id offered to a Show library resolves to nothing -- and "matched
    nothing" is indistinguishable from a correct empty collection, which is
    what makes this failure invisible rather than loud.
    """
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await _build(_ctx(_sources(http)))

    episode_ids = set()
    for record in load("tracearr_history_silo.json")["data"]:
        for field in ("imdb_id", "tmdb_id", "tvdb_id"):
            episode_ids.add(str(record[field]))

    assert episode_ids == {"tt41591872", "7173964", "11751886",
                           "tt39182946", "7173963", "11751885"}
    assert result.ids == [("tvdb", "403245")]
    assert not [value for _, value in result.ids if value in episode_ids]


async def test_the_media_document_is_fetched_once_per_pass_per_show():
    """The shared 240/min v2 budget is the binding constraint, so two
    definitions ranking the same show in one pass spend one call, not two.
    ``ctx.run_cache`` is the ``imdb_award``/``mdblist`` precedent."""
    seen: list = []
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    run_cache: dict = {}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        sources = _sources(http)
        await _build(_ctx(sources, run_cache=run_cache))
        await _build(_ctx(sources, run_cache=run_cache, metric="watch_time"))

    media_calls = [r for r in seen if r.url.path.startswith(f"{API_PREFIX}/media/")]
    assert len(media_calls) == 1


def _two_show_page():
    """Reacher (4 plays) and Silo (3 plays) out of the banked window.

    Records verbatim; only the selection is the test's. Two buckets is the
    smallest page that can show one title dropping while the other builds.
    """
    wanted = {REACHER_UUID, SILO_UUID}
    data = [
        record for record in load("tracearr_history_window.json")["data"]
        if record.get("show_media_id") in wanted
    ]
    assert len(data) == 7, len(data)
    return {"data": data, "meta": {"nextCursor": None, "pageSize": 100}}


async def test_a_media_document_that_404s_drops_that_title_and_builds_the_rest(caplog):
    """One title gone is not the source gone.

    Reacher outranks Silo, and Reacher's uuid 404s here -- which is what a show
    deleted from Tracearr between the history read and this lookup looks like.
    Failing the whole definition on that would empty a live collection over a
    transient fact about one member; dropping it silently would build a
    plausible, quietly-shorter collection. So it is dropped and counted, the
    ``mdblist_list`` shape, and the count is logged with the exception's class
    name and nothing else.
    """
    routes = {
        f"{API_PREFIX}/history": _two_show_page(),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
        # Reacher's uuid is deliberately unrouted: ``_routed``'s default is the
        # matched 404 envelope, rate-limit header included.
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with caplog.at_level(logging.INFO):
            result = await _build(_ctx(_sources(http), limit=10))

    assert result.ids == [("tvdb", "403245")]
    assert "1 ranked title(s) dropped" in caplog.text
    assert "TracearrNotFound" in caplog.text
    assert BASE_URL not in caplog.text


async def test_a_dropped_title_is_not_asked_about_twice_in_one_pass():
    """The failure is memoised too, or a dead id costs one request per
    definition -- ``BuilderContext``'s own rule -- and it stays the same class
    across the memo, so the second definition drops the bucket rather than
    failing on it."""
    seen: list = []
    routes = {
        f"{API_PREFIX}/history": _two_show_page(),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    run_cache: dict = {}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        sources = _sources(http)
        first = await _build(_ctx(sources, run_cache=run_cache, limit=10))
        second = await _build(_ctx(sources, run_cache=run_cache, limit=10))

    assert first.ids == second.ids == [("tvdb", "403245")]
    media_calls = [r for r in seen if r.url.path.startswith(f"{API_PREFIX}/media/")]
    # One per uuid for the whole pass: one 404 and one document, not two each.
    assert len(media_calls) == 2


async def test_a_transport_failure_still_fails_the_whole_definition():
    """The other half of the split. A connection that will not open is the
    SOURCE failing, and every remaining bucket would fail the same way -- so it
    raises, the engine contains it as one dead source, and the collection is
    left exactly as it was rather than being rebuilt from a partial ranking."""

    def boom(request):
        raise httpx.ConnectError("connection refused")

    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": boom,
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _build(_ctx(_sources(http)))

    assert not isinstance(error.value, TracearrNotFound)
    assert "ConnectError" in str(error.value)
    assert BASE_URL not in str(error.value)


async def test_a_history_endpoint_that_404s_still_fails_the_definition():
    """The narrowness of the per-item tolerance, pinned. ``build`` catches
    ``TracearrNotFound`` only around the per-bucket resolution, so the history
    call answering 404 -- the window itself being gone -- is still fatal.
    Reading it as an empty window would mean "remove every member"."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TracearrRefused):
            await _build(_ctx(_sources(http), library_type="Movie"))


# --- the last resort ----------------------------------------------------------


async def test_an_identity_less_only_bucket_becomes_a_plex_rating_key():
    """The documented last resort (adjudication A-resolution). A Plex rating
    key resolves free against the engine's own index and is dropped, not
    guessed, when it is stale -- and no ``/media/{ref}`` call is possible for a
    play that carries no media id."""
    seen: list = []
    routes = {f"{API_PREFIX}/history": load("tracearr_history_unidentified.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await _build(_ctx(_sources(http)))

    assert result.ids == [("plex", "75916")]
    assert [request.url.path for request in seen] == [f"{API_PREFIX}/history"]


# --- what the collection says about itself ------------------------------------


async def test_the_summary_names_the_metric_the_library_and_the_window():
    routes = {f"{API_PREFIX}/history": load("tracearr_history_movies.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        plays = await _build(_ctx(_sources(http), library_type="Movie", days=14))
        watched = await _build(
            _ctx(_sources(http), library_type="Movie", days=14, metric="watch_time")
        )

    assert plays.summary == (
        "The movies played most often on this server over the past 14 days."
    )
    assert watched.summary == (
        "The movies watched for the longest on this server over the past 14 days."
    )
    # No hosted artwork exists for these, so the collection simply keeps none.
    assert (plays.poster_kind, plays.poster_key) == (None, None)


async def test_no_log_line_and_no_error_carries_the_base_url_or_the_key(caplog):
    """On the failing path, which is the one that reaches
    ``logger.exception`` in the engine with a whole traceback."""
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": lambda request: _matched(
            {"statusCode": 500, "error": "InternalServerError", "message": "boom"},
            status=500,
        ),
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(TracearrRefused) as error:
                await _build(_ctx(_sources(http)))

    assert BASE_URL not in caplog.text
    assert API_KEY not in caplog.text
    assert BASE_URL not in str(error.value)
    assert API_KEY not in str(error.value)


# --- the catalog rows ---------------------------------------------------------


def test_the_two_catalog_rows_are_charts_and_opt_in():
    from autoposter.collections.catalog import BY_KEY, READY

    movies = BY_KEY["chart_tracearr_movies"]
    shows = BY_KEY["chart_tracearr_shows"]

    for preset in (movies, shows):
        assert preset.category == "charts"
        assert preset.readiness == READY
        assert preset.gated_row is None
        # No Kometa defaults file reproduces this; saying so is the rule
        # ``_check_kometa_sources`` exists to make impossible to break.
        assert preset.kometa_source.startswith("no Kometa defaults file -- ")
    assert movies.library_types == ("Movie",)
    assert shows.library_types == ("Show",)
    assert movies.titles() == ["Most Watched Movies"]
    assert shows.titles() == ["Most Watched Shows"]


def test_the_rows_expand_to_the_builder_with_an_explicit_window_and_cap():
    from autoposter.collections.catalog import BY_KEY

    [definition] = BY_KEY["chart_tracearr_shows"].definitions("Show")

    assert definition.title == "Most Watched Shows"
    assert definition.builder == "tracearr_most_watched"
    assert definition.params == {"days": 30, "limit": 20, "metric": "plays"}
    # A Movie-only preset asked for its Show definitions has none.
    assert BY_KEY["chart_tracearr_movies"].definitions("Show") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```
docker compose -p row79t3 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_tracearr.py -x -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'autoposter.collections.builders.tracearr'`.

- [ ] **Step 3: Write the builder**

Create `src/autoposter/collections/builders/tracearr.py`:

```python
"""``tracearr_most_watched``: the titles this household actually played.

The transport is ``providers/tracearr.py`` and the ranking is
``collections/activity.py``. What this builder adds is the three decisions
neither of them can make -- how a ranked bucket becomes a namespaced id, what
that costs, and what happens when Tracearr is not configured at all.

**How a bucket becomes an id, and why it differs by library type.**

- *Movies* short-circuit. A movie history record's ``imdb_id``/``tmdb_id``/
  ``tvdb_id`` are the MOVIE's own, verified against the same movie's media
  document, so a Movie collection costs exactly the history pages and nothing
  else.
- *Shows* do not. On an episode record those three ids are the EPISODE's, and
  ``GET /media/show:tvdb:<episode id>`` is a live-verified 404 -- so a show's
  ids come from one ``GET /media/{show_media_id}`` per bucket. That call is
  made AFTER ranking and truncating, so a collection spends at most its own
  ``limit`` calls, and it is preferred over the ``grandparent_rating_key``
  Tracearr also hands over because this codebase runs a pruner precisely
  because rating keys die.
- *A bucket with no canonical id anywhere* -- formed from identity-less plays
  alone -- emits ``("plex", rating_key)`` as the documented last resort. That
  resolves free against the engine's own index and is dropped, not guessed,
  when it is stale.

**The budget.** 240 requests/minute shared across the whole v2 tree. One
collection is one page per 100 plays in its window, plus at most ``limit``
media documents on a Show library. The documents are memoised on
``ctx.run_cache`` -- one library's pass -- so two definitions ranking the same
show spend one call; the FAILURE is memoised too, because otherwise a dead id
costs one request per definition (``BuilderContext``'s own rule, the
``imdb_award``/``mdblist`` precedent). That applies to both kinds of failure:
a uuid that 404s once is not asked about again in this pass, it is dropped
again from the memo.

**A missing media document drops one title; a broken Tracearr fails the
definition.** The split is the ``mdblist_list`` one, and it turns on whether
the failure is about the item or about the source. A ``TracearrNotFound`` --
one uuid the history just handed us that no longer has a media document, which
is what a title deleted between the history read and the lookup looks like --
drops that bucket, counts it, and lets the other nineteen build; that is the
resolver's ordinary "the library does not have this" one step earlier, and a
transient deletion must not empty a collection. Anything else --
``TracearrRefused``: no connection, the SPA fallback, a mangled envelope,
authentication gone -- raises, because the source itself is the thing that
failed and every remaining bucket would fail the same way. The engine contains
that as one dead source and the operator reads "this definition failed".

The dropped-title count is logged once per build, with the exception's CLASS
name and no message, the way ``mdblist_list`` reports the entries it skipped:
it is the same fact ``DefinitionResult.unresolved`` reports one layer down --
"the source named things this collection could not use" -- and a silent drop is
the one outcome that looks identical to a correct, smaller collection.

**No ``server_id`` filter and no per-user variant.** Both are deliberate. The
banked instance runs one server, and the ``user`` block is the PII-bearing part
of a history record -- a username in a collection title would be a new class of
exposure, not a new feature.
"""
import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoposter.collections.activity import (
    HISTORY_MEDIA_TYPE,
    MEDIA_KINDS,
    Bucket,
    rank,
    since_instant,
)
from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.providers.tracearr import TracearrNotFound, TracearrRefused

logger = logging.getLogger(__name__)

__all__ = [
    "TracearrBuilderRefused",
    "TracearrMostWatchedBuilder",
    "TracearrMostWatchedParams",
]

# ``(field on the identity, namespace)`` in preference order, per library type:
# which guid the library's items are most likely to carry, and every fallback
# is a member that would otherwise be dropped. The same orders
# ``builders/mdblist.py`` uses, for the same reason.
_PREFERENCE: dict[str, tuple[tuple[str, str], ...]] = {
    "Movie": (("tmdb_id", "tmdb"), ("imdb_id", "imdb")),
    "Show": (("tvdb_id", "tvdb"), ("tmdb_id", "tmdb"), ("imdb_id", "imdb")),
}

# Where one show's media document is memoised, namespaced by module like
# ``mdblist``'s and ``imdb_award``'s.
_MEDIA_MEMO = "tracearr.media.%s"

# What the collection says about itself when the definition sets no summary.
# Ours to write: no Kometa defaults file describes these collections.
_SUMMARY = {
    "plays": "The %ss played most often on this server over the past %d days.",
    "watch_time": "The %ss watched for the longest on this server over the past %d days.",
}


class TracearrBuilderRefused(Exception):
    """This deployment cannot build from Tracearr.

    Only ever "Tracearr is not configured". Everything Tracearr itself refuses
    is ``TracearrRefused``, raised by the client that knows what was asked.
    """


class TracearrMostWatchedParams(BaseModel):
    """``tracearr_most_watched``'s params: what "most watched" means here.

    ``metric`` is closed because there are exactly two answers and both come
    free from one page-through: ``plays`` counts records (which ARE plays --
    Tracearr groups resume chains and drops anything under two minutes before
    it answers) and ``watch_time`` sums ``duration_ms``.

    ``days`` is the window, converted to an ISO instant at build time. Kometa's
    Tautulli builders take the same knob under the same name. Bounded at both
    ends: 0 days is a collection that can only ever be empty, and a decade is a
    page-through nobody meant to ask for.

    ``limit`` is NOT ``CollectionDefinition.limit`` and the difference matters.
    The definition's cap is applied after resolution, to members; this one caps
    CANDIDATES, before the per-bucket ``/media/{ref}`` calls a Show library
    spends -- so it is what bounds this definition's share of the shared
    240/min budget, which is why it has a ceiling at all.
    """

    model_config = ConfigDict(extra="forbid")

    metric: Literal["plays", "watch_time"] = "plays"
    days: int = Field(30, ge=1, le=365)
    limit: int = Field(20, ge=1, le=100)


class TracearrMostWatchedBuilder:
    """The library's most-watched titles over a window, in rank order."""

    type_name = "tracearr_most_watched"
    params_model = TracearrMostWatchedParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TracearrMostWatchedParams.model_validate(ctx.config)
        require_library_type(
            "the 'tracearr_most_watched' builder", ctx.library_type, MEDIA_KINDS
        )
        client = ctx.sources.tracearr
        if client is None:
            raise TracearrBuilderRefused(
                "Tracearr is not configured for this deployment (tracearr.enabled is "
                "off, tracearr.base_url is blank, or AUTOPOSTER_TRACEARR_APIKEY is "
                "unset), so there is no watch history to rank"
            )

        kind = MEDIA_KINDS[ctx.library_type]
        records = await client.history(
            since=since_instant(params.days, datetime.now(UTC)),
            media_type=HISTORY_MEDIA_TYPE[kind],
        )
        ids = []
        vanished = 0
        for bucket in rank(
            records, media_kind=kind, metric=params.metric, limit=params.limit
        ):
            try:
                external = await _external_id(ctx, client, ctx.library_type, bucket)
            except TracearrNotFound:
                # ONE title, not the source: a uuid the history just named has
                # no media document any more, which is what a title deleted
                # between the two calls looks like. Dropped and counted rather
                # than raised -- see the module docstring. Only this class is
                # caught; a plain TracearrRefused propagates and fails the
                # definition, because every remaining bucket would fail too.
                vanished += 1
                continue
            if external is None:
                # Skipped rather than raised, the ``mdblist_list`` judgement:
                # Tracearr knowing no id for one title is the resolver's
                # ordinary "the library does not have this" one step earlier.
                logger.debug(
                    "%s: no usable id for %r in the Tracearr ranking",
                    ctx.library, bucket.title,
                )
                continue
            ids.append(external)
        if vanished:
            # Class name and a count, no message: the message would carry a
            # path, and what an operator needs is "the ranking was N titles
            # shorter than it looks, and here is the kind of failure".
            logger.info(
                "%s: %d ranked title(s) dropped -- Tracearr has no media document "
                "for them any more (%s)",
                ctx.library, vanished, TracearrNotFound.__name__,
            )
        return BuilderResult(
            ids=ids,
            summary=_SUMMARY[params.metric] % (ctx.library_type.lower(), params.days),
        )


async def _external_id(ctx: BuilderContext, client, library_type: str, bucket: Bucket):
    """The best namespaced id for one bucket, or None if it has none."""
    if bucket.media_id is None:
        # An identity-less-only bucket: no canonical id exists anywhere, so the
        # Plex rating key it was formed under is the only thing there is.
        return ("plex", bucket.rating_key) if bucket.rating_key else None
    identity = (
        {
            "imdb_id": bucket.imdb_id,
            "tmdb_id": bucket.tmdb_id,
            "tvdb_id": bucket.tvdb_id,
        }
        if library_type == "Movie"
        else await _media_document(ctx, client, bucket.media_id)
    )
    for field, namespace in _PREFERENCE[library_type]:
        value = identity.get(field)
        if value is None or value == "" or value == 0:
            continue
        return (namespace, str(value))
    return None


async def _media_document(ctx: BuilderContext, client, media_id: str) -> dict:
    """One show's media document, fetched at most once per library per pass.

    The failure is memoised as well as the answer -- see the module docstring.
    Both classes are memoised and both are re-raised unchanged, so which of
    them it was survives the memo: ``build`` still gets a ``TracearrNotFound``
    to drop the bucket on, or a ``TracearrRefused`` to fail the definition on,
    exactly as it would have on the first attempt.
    """
    memo = _MEDIA_MEMO % media_id
    remembered = ctx.run_cache.get(memo)
    if isinstance(remembered, Exception):
        raise remembered
    if remembered is not None:
        return remembered
    try:
        document = await client.media(media_id)
    except TracearrRefused as error:
        # TracearrNotFound is a subclass, so this arm memoises both.
        ctx.run_cache[memo] = error
        raise
    ctx.run_cache[memo] = document
    return document
```

- [ ] **Step 4: Register the builder**

In `src/autoposter/collections/builders/__init__.py`, add the import (keeping the block's alphabetical order — after the `text_file` import at `:52`, before `tmdb`):

```python
from autoposter.collections.builders.tracearr import TracearrMostWatchedBuilder
```

and the registration, after `register(MdblistListBuilder())` at `:107`:

```python
register(TracearrMostWatchedBuilder())
```

- [ ] **Step 5: Add the two catalog rows**

In `src/autoposter/collections/catalog.py`, immediately after `CHART_PRESETS` (`:656-658`):

```python
# The two Tracearr rows, in the same category and for the same reason the TMDb
# charts are here: they answer "what should I watch, going by what is being
# watched". What makes them different is where the ranking comes from -- TMDb
# publishes its charts and Tracearr publishes no ranking at all, in either API
# version, so these two are computed from the deployment's OWN watch history
# (``collections/activity.py``). That is also why they carry no Kometa
# attribution: Kometa's equivalent is its Tautulli chart family, which reads a
# different service through a different API, and claiming its defaults file
# here would be a citation that does not describe what this builds.
#
# Two rows rather than a tenth category. Two collections do not make a
# taxonomy, and an operator looking for "most watched" looks under Charts.
_TRACEARR_SOURCE = NOT_KOMETA + (
    "the role Kometa's Tautulli chart defaults play, computed from this "
    "deployment's own Tracearr watch history; the title is ours"
)

_TRACEARR_DESCRIPTION = (
    "The %s played most often on this server over the past 30 days, ranked "
    "from Tracearr's own watch history and recomputed on every pass. Needs "
    "tracearr.enabled, tracearr.base_url and AUTOPOSTER_TRACEARR_APIKEY; "
    "without them the collection reports itself failed rather than emptying."
)

TRACEARR_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="chart_tracearr_movies",
        category="charts",
        name="Most Watched Movies",
        description=_TRACEARR_DESCRIPTION % "films",
        kometa_source=_TRACEARR_SOURCE,
        library_types=_MOVIE,
        collections=(
            PresetCollection(
                title="Most Watched Movies",
                builder="tracearr_most_watched",
                # Spelled out rather than left to the params model's defaults:
                # a preset's params should read as the whole answer to "which
                # collection is this", the way the award rows name their event.
                params=(("days", 30), ("limit", 20), ("metric", "plays")),
            ),
        ),
    ),
    Preset(
        key="chart_tracearr_shows",
        category="charts",
        name="Most Watched Shows",
        description=_TRACEARR_DESCRIPTION % "series",
        kometa_source=_TRACEARR_SOURCE,
        library_types=_SHOW,
        collections=(
            PresetCollection(
                title="Most Watched Shows",
                builder="tracearr_most_watched",
                params=(("days", 30), ("limit", 20), ("metric", "plays")),
            ),
        ),
    ),
)
```

Update the count-checksum comment (`:1235-1247`) — the `charts` line and the total:

```
#   awards           15 / 0 / 1     charts           10 / 0 / 1
```

and the closing sentence:

```
# -- 51 rows: 30 presets an operator can switch on today, 18 that name what
# they would build and the roadmap row that would let them, and 3 rendered
# switches for families that already ship behind a boolean.
```

and add the block to `CATALOG` (`:1248-1259`), after `CHART_PRESETS`:

```python
    + TRACEARR_PRESETS
```

- [ ] **Step 6: Update the catalog checksum test**

In `tests/test_collection_catalog.py:186-196`, change the `charts` row of `CATALOG_CHECKSUM`:

```python
    "charts": (10, 0, 1),
```

- [ ] **Step 7: Run the builder and catalog tests to verify they pass**

Run:
```
docker compose -p row79t3 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_tracearr.py tests/test_collection_catalog.py -x -q
```
Expected: PASS. `tests/test_collection_catalog.py` gains two parametrized cases automatically (`READY_PRESETS` is built from `CATALOG`), and both must be green — they are the assertion that each new row expands to a definition the config would accept.

- [ ] **Step 8: Prove the two invariants with mutations**

**Mutation A — the never-an-episode-id rule.** Temporarily change `_external_id` so a Show bucket reads the bucket's own fields rather than the media document: replace the `if library_type == "Movie"` condition with `if True`. Run:

```
docker compose -p row79t3 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_tracearr.py -q
```
Expected: FAIL — `test_a_show_collection_takes_its_ids_off_the_media_document`, `test_no_id_a_show_collection_emits_is_an_episode_id`, `test_a_media_document_that_404s_drops_that_title_and_builds_the_rest` and `test_a_dropped_title_is_not_asked_about_twice_in_one_pass` all red, every Show collection coming back empty. **Restore the condition** and re-run to green.

Note what this mutation demonstrates and what it does not: because `activity.py` refuses to carry episode ids out of the ranking at all, the failure mode here is an *empty* collection rather than a wrongly-populated one. That is the point of dropping the ids at the ranking layer — the worst reachable outcome is loud.

**Mutation B — the blast-radius split.** Temporarily widen the per-bucket catch in `build` from `except TracearrNotFound:` to `except TracearrRefused:`. Run the same command.

Expected: FAIL — `test_a_transport_failure_still_fails_the_whole_definition` red, because a dead connection would now be swallowed one bucket at a time and the build would return an empty, successful-looking result. That is precisely the outcome the narrow catch exists to prevent: "the source is down" and "one title was deleted" must not produce the same collection. **Restore the class** and re-run to green.

(No mutation is needed for the other direction — narrowing the catch to nothing is what `test_a_media_document_that_404s_drops_that_title_and_builds_the_rest` already fails on.)

- [ ] **Step 9: Full suite, golden gate and lint**

Run:
```
docker compose -p row79t3 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest -q
docker compose -p row79t3 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_port_golden.py -x -q
docker compose -p row79t3 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test ruff check .
```
Expected: the full suite green against Task 1's recorded baseline plus the new tests; the golden gate **byte-identical** (the two new rows are opt-in and `collections.presets` is empty in the golden config, so the expansion contributes nothing); `All checks passed!`.

- [ ] **Step 10: Commit**

```bash
git add src/autoposter/collections/builders/tracearr.py \
  src/autoposter/collections/builders/__init__.py src/autoposter/collections/catalog.py \
  tests/test_builder_tracearr.py tests/test_collection_catalog.py
git commit --no-gpg-sign -m "feat(tracearr): the tracearr_most_watched builder and its two catalog rows"
```

- [ ] **Step 11: Teardown**

```
docker compose -p row79t3 down
```

---

### Task 4: Wrap — the operator surface, the docs, and the roadmap

Compose project name for this task: **`row79t4`**.

**Files:**
- Modify: `config/autoposter.example.yaml` (a `tracearr:` block after `sonarr:`, which ends at `:180`)
- Modify: `.env.example`
- Modify: `deploy/README.md` (the Secrets list at `:103-144`; a new subsection near the Radarr/Sonarr one)
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (row 79 at `:175`, row 93 at `:195`, and a new row after `:259`)
- Modify: `tests/test_builder_tracearr.py` (append the two end-to-end/purity tests below)

**Interfaces:**
- Consumes: everything Tasks 1–3 produced. Produces nothing new for later tasks — this is the last one.

- [ ] **Step 1: Write the failing wrap tests**

Append to `tests/test_builder_tracearr.py`:

```python
# --- the operator surface -----------------------------------------------------


def test_the_example_config_carries_a_tracearr_block_that_is_off():
    """The example documents the block and never switches it on: every
    outward-facing integration in this project ships disabled, and an example
    that shipped this one enabled would point a fresh deployment at a hostname
    that does not exist."""
    import pathlib

    import yaml

    example = pathlib.Path("config/autoposter.example.yaml")
    data = yaml.safe_load(example.read_text(encoding="utf-8"))

    assert data["tracearr"]["enabled"] is False
    assert set(data["tracearr"]) == {"enabled", "base_url"}
    # The credential is never a config key. This is the failure
    # tests/test_example_config_matches_schema.py catches from the other side.
    assert "api_key" not in data["tracearr"]


def test_the_secret_is_documented_in_both_places_an_operator_looks():
    """``.env.example`` is the compose path and ``deploy/README.md`` is the
    Kubernetes one. A soft secret documented in neither is a feature nobody can
    turn on."""
    import pathlib

    env_example = pathlib.Path(".env.example").read_text(encoding="utf-8")
    readme = pathlib.Path("deploy/README.md").read_text(encoding="utf-8")

    assert "AUTOPOSTER_TRACEARR_APIKEY" in env_example
    assert "AUTOPOSTER_TRACEARR_APIKEY" in readme
    assert "tracearr_most_watched" in readme


def test_the_roadmap_row_this_phase_closes_says_so_and_names_its_corrections():
    """A closure note that only says "delivered" hides the two places the row's
    own text was wrong -- and the row is what the next reader trusts."""
    import pathlib
    import re

    roadmap = pathlib.Path(
        "docs/superpowers/specs/2026-08-22-full-parity-roadmap.md"
    ).read_text(encoding="utf-8")
    row = next(
        line for line in roadmap.splitlines() if line.startswith("| 79 |")
    )

    assert "**answered row79/tracearr:** delivered" in row
    assert "tracearr_most_watched" in row
    # Correction 1: the short-circuit is movies-only.
    assert "movies only" in row.lower()
    # Correction 2: not every history record carries ids.
    assert "2 of 50" in row

    # And the follow-up row exists, is the LAST row of the table, is numbered
    # one past the previous last, and cites row 79 as its dependency.
    numbered = [int(m.group(1)) for m in re.finditer(r"^\|\s*(\d+)\s*\|", roadmap, re.M)]
    assert numbered[-1] == max(numbered), "the new row is not the last in the table"
    assert numbered[-1] == numbered[-2] + 1, "the new row skipped a number"
    filed = next(
        line for line in roadmap.splitlines()
        if line.startswith("| %d |" % numbered[-1])
    )
    assert "recently-added" in filed
    assert filed.rstrip().endswith("| 79, 17 |")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```
docker compose -p row79t4 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_tracearr.py -x -q -k "example_config or documented or roadmap_row"
```
Expected: FAIL — `KeyError: 'tracearr'` on the example config test, then the other two.

- [ ] **Step 3: Add the example config block**

In `config/autoposter.example.yaml`, after the `sonarr:` block (which ends at `:180`) and before `arr_sync:`:

```yaml
tracearr:
  enabled: false # off until base_url is set and AUTOPOSTER_TRACEARR_APIKEY is exported
  base_url: http://tracearr.media.svc.cluster.local
  # api_key is deliberately not a key here -- it is AUTOPOSTER_TRACEARR_APIKEY,
  # the same posture as radarr/sonarr. Switching this on makes the
  # `chart_tracearr_movies` / `chart_tracearr_shows` presets buildable; it does
  # not switch them on, which is `collections.presets`.
```

- [ ] **Step 4: Add the secret to `.env.example`**

Append to `.env.example`:

```
# Optional: the Tracearr public API key (a `trr_pub_...` value, minted in
# Tracearr's own settings). Unset, the two most-watched collection presets
# report themselves failed and nothing else changes -- every other pass runs
# exactly as before. The base URL is NOT here: it is `tracearr.base_url` in the
# YAML config, the same split radarr/sonarr use.
AUTOPOSTER_TRACEARR_APIKEY=changeme
```

- [ ] **Step 5: Document it in `deploy/README.md`**

Add to the Secrets list, after the `AUTOPOSTER_RADARR_APIKEY` / `AUTOPOSTER_SONARR_APIKEY` entry (`:130-133`):

```markdown
- `AUTOPOSTER_TRACEARR_APIKEY` — optional, same posture as the MDBList key.
  Unset, `tracearr.enabled` defaults to `false` anyway, so the app boots the
  same either way; see "Tracearr watch-history collections" below.
```

and add the subsection immediately after the "Radarr and Sonarr sync" section:

```markdown
### Tracearr watch-history collections

Two collection presets — `chart_tracearr_movies` and `chart_tracearr_shows`,
titled **Most Watched Movies** and **Most Watched Shows** — rank a library by
what this deployment actually played, over a 30-day window. They are opt-in
like every other preset: list the key in `collections.presets`.

Three things have to be true before either builds:

- `tracearr.enabled: true` and `tracearr.base_url` set in the YAML config. The
  base URL may be a cluster-internal hostname; it is never logged, never
  returned by the API and never put in an error message.
- `AUTOPOSTER_TRACEARR_APIKEY` exported. It is a `trr_pub_...` public API key
  minted in Tracearr's own settings, and it is a *soft* secret: with it unset
  the service still boots and every other pass is unaffected, and only these
  definitions report themselves failed.
- The `tracearr_most_watched` builder is what the presets expand to. It takes
  `days` (1–365, default 30), `metric` (`plays` or `watch_time`, default
  `plays`) and `limit` (1–100, default 20), so an operator who wants a
  different window writes their own definition rather than editing the preset.

**Tracearr publishes no most-watched endpoint**, in either API version — the
only top-N-by-play-count endpoint it has is on its internal, session-JWT API
that an API key cannot reach. So this ranking is computed here, by paging
`GET /api/v2/public/history` over the window and grouping the records. Two
consequences worth knowing:

- **The budget is the constraint.** The v2 API allows 240 requests/minute
  *shared across the whole key* — not per route, and spent even by requests
  that fail authentication. One collection costs one page per 100 plays in its
  window, plus (on a Show library only) one `GET /api/v2/public/media/{id}`
  per ranked title, which is what `limit` bounds. Two definitions ranking the
  same show in one pass share one call. Worst case with both presets on is
  2 × (≤10 history pages + ≤25 media lookups) ≈ 70 v2 calls per pass against a
  240/min budget, which is why no client-side rate limiter ships; revisit that
  only if the preset count grows.
- **One missing title does not empty the collection.** If Tracearr no longer
  has a media document for a ranked show — a title deleted between the history
  read and the lookup — that one entry is dropped and the count is logged; the
  collection still builds from the rest. A Tracearr that is unreachable, or
  answering with something other than the API, fails the definition instead and
  leaves the existing collection untouched.
- **These numbers are not Tracearr's dashboard numbers, and are not meant to
  be.** `/history` windows are instants; Tracearr's own per-item stats windows
  are UTC calendar days. The two disagree by design, and mixing them would
  produce a number neither service would recognise.
```

- [ ] **Step 6: Close roadmap row 79 and file the follow-up**

First read the tail of the gap table to get the next free number — rows have been filed by other branches since this plan was written:

```bash
python - <<'PY'
import io, re
path = "docs/superpowers/specs/2026-08-22-full-parity-roadmap.md"
rows = [int(m.group(1)) for m in re.finditer(
    r"^\|\s*(\d+)\s*\|", io.open(path, encoding="utf-8").read(), re.M)]
print("rows:", len(rows), "max:", max(rows), "next free:", max(rows) + 1)
PY
```

Expected at the time of writing: `rows: 165 max: 163 next free: 164`. **Use whatever this prints**, not 164, if the pending branches filed more.

Then, in `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`:

**(a)** Append to row 79's description cell (`:175`), immediately before the closing ` | M — new client + builder | parity-only | 8a, 17 |`:

```
 **answered row79/tracearr:** delivered — `tracearr_most_watched` (`src/autoposter/collections/builders/tracearr.py`) over a new client (`src/autoposter/providers/tracearr.py`) and a pure ranking module (`src/autoposter/collections/activity.py`), plus `TracearrConfig`, the soft secret `AUTOPOSTER_TRACEARR_APIKEY`, and two opt-in Charts presets (`chart_tracearr_movies`, `chart_tracearr_shows`). **Two things this row's own text got wrong, corrected by the harvest and by the shipped code.** (1) The external-id short-circuit is **movies only**. On an episode record `imdb_id`/`tmdb_id`/`tvdb_id` are the EPISODE's, and `GET /api/v2/public/media/show:tvdb:<episode id>` is a live-verified 404 (`docs/research/tracearr/payloads/v2-media-show-by-tvdb-ref.json`) — so a show reaches its ids only through `show_media_id` and one `/media/{uuid}` call per ranked title, made after truncating to `limit`. That call emits the SHOW-LEVEL external ids from the media document rather than this row's original `availability[].rating_key` recipe, because a rating key is exactly the thing that dies when an item is replaced or moved — this repository ships a pruner (row 129) precisely because they do — while a show's tvdb/tmdb/imdb id survives it; the rating key remains the last resort for a bucket that has no canonical id at all. An episode id emitted as a member would resolve to nothing on a Show library and look exactly like a correct empty collection, which is why `collections/activity.py` refuses to carry those ids out of the ranking at all and `tests/test_builder_tracearr.py::test_no_id_a_show_collection_emits_is_an_episode_id` is a named invariant. (2) "ids on every history record" is false: **2 of 50** banked records carry `media_id`, `show_media_id`, `library_id` and all three external ids null while still carrying `rating_key`/`grandparent_rating_key`, and both are episodes of one show whose honest count is therefore 20 rather than the 18 a naive grouping reports. Those plays are MERGED into the bucket already seen under their rating key — no extra call, no operator knob, nothing double-counted — and a bucket formed from such plays alone emits `("plex", rating_key)` as the documented last resort. Failure blast radius is split deliberately: a 404 on one ranked title's media document drops that title and logs a count, while an unreachable or non-API Tracearr fails the definition and leaves the collection untouched. Scope held: `metric` is `plays`/`watch_time` only, `/media/{ref}/stats` calendar-day windows are never consulted or reconciled anywhere, and per-user collections are out (the `user` block is the PII-bearing one). The recently-added family is filed as its own row rather than built here
```

**(b)** In row 93 (`:195`), the catalog gains two rows: recompute the totals from the live `CATALOG` table (rows, READY presets, GATED, setting-backed) rather than copying an absolute — row 93's counts have already drifted once (CR-2 review) and will drift again if a later task's number is trusted instead of the table itself — and update `**N rows** (M READY presets, G GATED, S setting-backed)` to match what `CATALOG` actually reports. Also, in the same row's "READY of this row's own families" sentence change `chart packs (eight TMDb charts, plus the IMDb bundle as a setting-backed row)` to `chart packs (eight TMDb charts, two Tracearr watch-history rows added by row 79, plus the IMDb bundle as a setting-backed row)`.

**(c)** Append the new row after the last row of the gap table (`:259`), using the number the script printed (shown here as `164`):

```
| 164 | Tracearr recently-added builder | row79/tracearr wrap row, filed rather than built — a deliberately different family from the one row 79 delivered. Row 79 had to compute its ranking because Tracearr publishes none; `GET /api/v2/public/recently-added` needs no aggregation at all: one cursor-paginated endpoint ordered by server-reported added date, filterable by `server_id`, `library_id`, `media_type` and `include_removed` (`docs/research/tracearr-api-harvest.md`, the row-79 mapping table calls it the one **direct match**). Strictly cheaper than the history walk it would sit beside, and it inherits row 79's whole transport — `src/autoposter/providers/tracearr.py`, `SourceClients.tracearr`, `TracearrConfig`, `AUTOPOSTER_TRACEARR_APIKEY` — so the new work is one client method, one builder, one params model and the catalog rows. Two traps row 79 already paid for and this row inherits verbatim: `RecentlyAddedRecord` rows for episodes carry EPISODE-level `imdb_id`/`tmdb_id`/`tvdb_id` and reach the show only through `grandparent_rating_key` or a `/media/{uuid}` call, and `media_id` may be null. Kometa's nearest equivalent is its Tautulli/recently-added chart family, so the rows would be NOT_KOMETA for the same reason row 79's two are. Not started: nothing in the shipped code anticipates it beyond the client it would reuse | S — one endpoint, one builder | parity-only | 79, 17 |
```

- [ ] **Step 7: Run the wrap tests to verify they pass**

Run:
```
docker compose -p row79t4 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_tracearr.py tests/test_example_config_matches_schema.py tests/test_collection_catalog.py -x -q
```
Expected: PASS. `tests/test_collection_catalog.py` reads the roadmap's row numbers itself (`_roadmap_rows`), so a malformed new row shows up here rather than being noticed later.

- [ ] **Step 8: Full suite, golden gate and lint**

Run:
```
docker compose -p row79t4 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest -q
docker compose -p row79t4 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test pytest tests/test_builder_port_golden.py -x -q
docker compose -p row79t4 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test ruff check .
```
Expected: the full suite green; the golden gate **byte-identical** with `tests/fixtures/collections/golden_port.json` untouched (confirm with `git status` that the fixture is not modified); `All checks passed!`.

- [ ] **Step 9: Confirm the phase's own claims before saying it is done**

Run and read the output rather than assuming it:

```bash
git status --short
git diff --stat HEAD~3
```

Check, one by one:
- `tests/fixtures/collections/golden_port.json` is **not** in the diff.
- `src/autoposter/config/live.py` is **not** in the diff (`tracearr` is deliberately not frozen).
- No file under `alembic/` is in the diff (this phase adds no migration and needs none).
- Grep the diff for the string `trr_pub_` — every hit is the literal `trr_pub_test` in a test or the words "`trr_pub_...`" in documentation, and nothing else.

- [ ] **Step 10: Commit**

```bash
git add config/autoposter.example.yaml .env.example deploy/README.md \
  docs/superpowers/specs/2026-08-22-full-parity-roadmap.md tests/test_builder_tracearr.py
git commit --no-gpg-sign -m "docs(tracearr): the operator surface, row 79 closed with its corrections, recently-added filed"
```

- [ ] **Step 11: Move the plan into the repository**

The plan draft lives in gitignored staging. Move it (do not copy it) and commit it with the phase:

```bash
mkdir -p docs/superpowers/plans
git mv --force .superpowers/sdd/row79-plan-draft.md docs/superpowers/plans/2026-08-27-row79-tracearr.md 2>/dev/null \
  || mv .superpowers/sdd/row79-plan-draft.md docs/superpowers/plans/2026-08-27-row79-tracearr.md
git add docs/superpowers/plans/2026-08-27-row79-tracearr.md
git commit --no-gpg-sign -m "docs: the row79 tracearr implementation plan, as executed"
```

- [ ] **Step 12: Teardown**

```
docker compose -p row79t4 down
```

---

## Self-review

Run against the adjudications (`.superpowers/sdd/row79-facts.md`) and the harvest.

**Adjudication coverage.**

| Adjudication | Where the plan implements it |
|---|---|
| A1 — always merge identity-less plays by seen rating keys | Task 2, `rank`'s second pass; tests `test_the_two_identity_less_plays_are_merged_and_not_double_counted`, `test_the_merge_does_not_depend_on_the_order_records_arrive_in`, and the Step 6 mutation proof |
| A2 — `metric: plays \| watch_time`, default `plays` | Task 3, `TracearrMostWatchedParams.metric`; Task 2, `METRICS` and `_sort_key` |
| A3 — `days: int = 30`, converted to an ISO instant; stats windows never consulted | Task 2, `since_instant`; Task 3, the `build` call site. No reference to `/media/{ref}/stats` exists anywhere in the plan's code |
| A4 — two rows in the existing Charts category, `NOT_KOMETA` | Task 3 Step 5, `TRACEARR_PRESETS` |
| A5 — recently-added out of scope, filed as a row | Task 4 Step 6(c) |
| A6 — per-user collections out | No `user_id` param, no username anywhere; stated in the builder's module docstring |
| A7 — genre variants out | No genre field is read; `genres` is null on every episode record and nothing depends on it |
| A8 — enabled + no key raises, the `mdblist_list` precedent | Task 3, `TracearrBuilderRefused`; test `test_an_unconfigured_tracearr_raises_rather_than_building_nothing` |
| A9 — plain config field, no `api_key`, not frozen, soft secret | Task 1 Step 5; tests `test_the_config_block_defaults_off_and_carries_no_api_key_field`, `test_the_secret_is_soft_so_a_deployment_without_one_still_boots`, `test_tracearr_is_not_a_frozen_section` |
| A-resolution — shows via `/media/{uuid}` after truncating, movies off the record, plex last resort, never an episode id | Task 3, `_external_id` / `_media_document`; tests `test_a_show_collection_takes_its_ids_off_the_media_document`, `test_a_movie_collection_takes_its_ids_off_the_records`, `test_an_identity_less_only_bucket_becomes_a_plex_rating_key`, `test_no_id_a_show_collection_emits_is_an_episode_id`, `test_a_show_bucket_never_carries_an_external_id` |
| Client mechanics — bearer via `fetch_json`, cursor paging + cap, both error envelopes, 404 raises, SPA guard by header, lenient parsing, `duration_ms` int | Task 1, the whole of `providers/tracearr.py` and `tests/test_tracearr_client.py`; `_duration_ms` in Task 2 |
| Blast radius (controller amendment) — a per-title 404 drops that bucket with a logged, class-name-only count and a memoised failure; every other client error fails the definition | Task 1, `TracearrNotFound`; Task 3, `build`'s narrow `except` and the aggregate log; tests `test_a_media_document_that_404s_drops_that_title_and_builds_the_rest`, `test_a_dropped_title_is_not_asked_about_twice_in_one_pass`, `test_a_transport_failure_still_fails_the_whole_definition`, `test_a_history_endpoint_that_404s_still_fails_the_definition`, and the Step 8 Mutation B proof |
| Movie merge branch (controller amendment) — exercised with labelled synthetic records | Task 2, `test_the_merge_works_for_movies_too__synthetic_records`; no fixture file is written |
| Golden/purity invariants | Every task's golden gate step; Task 2's `test_the_ranking_module_imports_nothing_that_can_do_io`; Task 3 Step 9's byte-identical requirement |

**Type consistency.** `rank(records, *, media_kind, metric, limit) -> list[Bucket]` is called with exactly those keywords in Task 3. `Bucket`'s eight fields are constructed in one place and read in `_external_id` and `_sort_key` only. `TracearrClient.history(*, since, media_type, page_size)` and `.media(media_id)` are the only two methods and both are called with keyword arguments matching their definitions. `MEDIA_KINDS` is both the library-type table and the `require_library_type` `allowed` argument, so the refusal message and the dispatch cannot disagree. `since_instant(days, now)` takes a positional pair in both its test and its call site. `TracearrNotFound` subclasses `TracearrRefused`, so `_media_document`'s `except TracearrRefused` memoises both while `build`'s `except TracearrNotFound` narrows to one — the ordering that makes the split work, and the reason `test_the_spa_and_the_transport_failures_are_not_the_not_found_class` pins the subclass relationship from the other side.

**What the amendments moved, checked one by one.** Three existing tests in Task 3 depended on the old raise-everything behaviour and were rewritten rather than left to fail: the memoised-failure test became `test_a_dropped_title_is_not_asked_about_twice_in_one_pass` (same memo claim, new outcome), `test_no_log_line_and_no_error_carries_the_base_url_or_the_key` now routes a 500 rather than relying on an unrouted uuid to raise (an unrouted uuid no longer raises — it is dropped), and the drop/raise pair is new. Task 2's mutation-proof expectation grew a fourth red. Nothing in Tasks 1's config/bundle half, Task 3's catalog half, or Task 4's docs half is affected.

**Known gaps, stated rather than papered over.**

- The **429 shape is not implemented against**, because the harvest deliberately never captured one (it would have meant exhausting the user's live budget). A 429 falls through `_get`'s `httpx.HTTPError` arm as `TracearrRefused(... HTTPStatusError)` — a dead source for that pass, contained by the engine, with nothing derived from it reaching Plex. That is the correct behaviour; what is untested is the specific status, and there is nothing honest to test it against.
- **`tracearr_history_end.json` is constructed, not banked.** Labelled as such in Task 2 Step 1 and in the fixture-cut script's own comment.
- The **multi-server rating-key caveat** (A1) is documented in `activity.py`'s module docstring and is not otherwise defended against; the deployment runs one server, and the exposure is bounded to identity-less records.
- **No client-side rate limiter**, by decision. The worst case with both presets on is 2 × (≤10 history pages + ≤25 media lookups) ≈ 70 v2 calls per pass against a shared 240/min budget, which is the arithmetic recorded in `deploy/README.md`. A 429 would arrive as `TracearrRefused(... HTTPStatusError)` — one dead source for that pass, not a retry storm. Revisit if the preset count grows.
- **The movie merge branch is covered by synthetic records, not captured ones.** Stated in the test's own name and docstring, and no synthetic record is written to a fixture file, so nothing invented can later be read as evidence of what Tracearr sends.
