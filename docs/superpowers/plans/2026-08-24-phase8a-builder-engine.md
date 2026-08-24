# Phase 8a: Builder Engine Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The generic builder abstraction the three hardcoded collection
sources never needed: a builder produces an ordered external-ID list; the
engine handles config schema, registry, resolution to owned Plex items,
sync/append, limit, ordering, dry-run, and failure containment. Closes
roadmap rows 95, 25–30, 68–70, 92 partial; folds rows 104, 115, 124 (they
sit on the exact lines this phase rewrites).

**Architecture:** `collections/builders/` package: a `Builder` protocol
(`build(ctx) -> BuilderResult` with an ordered list of namespaced external
IDs), a registry keyed by builder type, and typed per-collection
`CollectionDefinition` config models under `CollectionsConfig` (YAML +
hot-reloadable via the 6c overrides layer). A multi-namespace owned-item
index (imdb/tmdb/tvdb) replaces the IMDb-only resolve. The engine slots
between config and the EXISTING apply layer: definitions → builder →
resolve → `lists.reconcile_list_collection` (which keeps its contract).
The three shipped sources (IMDb charts, Oscars awards, CS buckets) become
registry builders with byte-identical output, proven by golden tests on
the returned action strings.

**User decisions (2026-08-24, binding):**
- Definitions live in **YAML (`CollectionsConfig`) + the 6c DB-overrides
  layer** — Flux-owned file, live-editable in Settings, example-config
  test gate applies. NO separate DB definition store.
- **Reconciler may delete, opt-in**: a managed collection whose definition
  disappeared may be deleted by the reconciler ONLY when
  `collections.delete_unconfigured: true` (default false). Guards below.
- **sync is the default** membership mode; `sync_mode: append` per
  definition (row 25).

**Plan-level decisions (from the fact sheet's flags, fixed here):**
- Definition identity stays `(library, title)`; a `definition_hash` OF THE
  DEFINITION CONFIG (distinct from the members hash) detects edits; a
  rename orphans the old title (reported, and deletable under the opt-in).
- Unresolvable IDs are counted and reported per definition (`unresolved:
  N`), not silently dropped; the drop itself remains (no title fallback).
- Failure containment: a failed source ⇒ that collection untouched, run
  continues — preserved exactly; per-definition failures surface in the
  run summary AND `reconcile_libraries` stops lying: any per-library
  failure ⇒ job result is not-ok (row 115 fix).
- Per-collection cadence (row 70) = `schedule` gating INSIDE the one
  `collections_reconcile` job (interval multiplier + optional date
  window), no second scheduler surface.
- Row 92 partial = per-definition `libraries:` targeting + per-library
  parameter overrides on a definition. Nothing broader.
- Smart collections are OUT (9c). `builder_level`, filters, new list
  sources are OUT (8b/9x) — the interface is sized to the three shipped
  sources + row 58's MDBList shape (namespaced IDs), nothing more.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds (no `.refresh()`; isolated
  compose recipe when the shared DB is contended; sibling-wait recipe).
- Container-only testing; zero skips; ruff clean (`ruff check src tests`);
  frontend build clean. Backend baseline on this branch (off main 5fc3fa8
  + fa0352c): **1806 passed / 11 deselected** `-m "not imagemagick"`;
  frontend **230**. Verify at start of every task.
- **The shipped failure-containment invariant is sacred**: empty/failed
  builder result ⇒ that collection is not touched (`lists.py:90-93`
  behavior preserved through the port).
- **Golden port gate**: the three shipped sources run through the engine
  with byte-identical `reconcile_list_collection` action strings
  before/after (same fixtures, same fakes). Any diff = the port is wrong.
- **Delete guards (all mandatory)**: `delete_unconfigured` default False;
  only collections carrying the ownership label AND a `managed_collections`
  row; protected labels always win; dry-run lists would-deletes without
  deleting; a per-pass delete cap — `max_deletes: int = 5` default,
  refuse-with-numbers past it (the cleanup-cap precedent) so a config
  wipe cannot cascade; every delete logged as an action string + events row.
- New endpoints go in a NEW `api/collections_builders.py` router (the
  6d/6e/7b pattern), inline `Depends(require_session)`; the openapi auth
  sweep must stay green.
- Mutation proofs for every guard (containment, delete cap, ownership
  filter, append-keeps-manual, limit) — red first, copy+cmp restore.
- Alembic: single new revision on the current head; run
  `alembic upgrade head` in the test container to prove it.
- Hot-reload: builder definitions are LIVE config (`config/live.py` — the
  collections section already is, except `.enabled`); an edited definition
  applies next reconcile with no restart. Do not add restart flags.
- Stage by name; `--no-gpg-sign`; no AI attribution; no new deps.

## Verified facts (fact sheet 2026-08-24 @ 5fc3fa8; spot-check before relying)

- Sources today: `collections/sources.py:41-84` (`CHART_COLLECTIONS`,
  `CHART_SUMMARIES`, `AWARD_COLLECTIONS`, `YEAR_SUMMARY`, `YEAR_SORT`),
  `buckets.py:39-71` (CS buckets from `assets/collections/content_rating_cs.json`).
  Orchestration `sources.py:103-181` `build_all()`; `_chart_ids`/`_award_event`
  (`:87-100`) log-and-return-empty = the containment wrapper to generalize.
- Fetchers: `charts.py:28-47` `fetch_chart` (IMDb GraphQL, 5 keys, raises);
  `awards.py:29-60` (`fetch_event`, `recent_years`, winners helpers).
- Resolve: `resolve.py:15-25` `build_imdb_index(section)` (one
  `section.all()` → `{imdb_id: item}`); `:28-44` `resolve_ids` order-
  preserving, dedupes, drops unowned silently. IMDb-ONLY — the multi-
  namespace index is Task 2's job. Plex items expose `guids` (see
  `test_collection_sources.py:33-43` FakeGuid idiom: `imdb://`, `tmdb://`,
  `tvdb://` prefixes).
- Apply layer contract (`lists.py:62-81` `reconcile_list_collection`):
  items = resolved Plex objects in source order; empty ⇒ no changes;
  sync-only diff (`:184-192`); dry-run = no writes, no ManagedCollection
  row (`:156-158`); `_members_hash` short-circuit (`:25-27`, `:117-148`);
  ownership via `resolve_collision` (`reconcile.py:118-167`); summary via
  raw PUT (`reconcile.py:198-242` — editSummary 404s live); returns
  `list[str]` action strings; per-library commit (`service.py:176`);
  `_managed_titles()` (`service.py:60-87`) must enumerate every engine-
  produced title or leftovers reporting breaks (`service.py:90-131`).
- Smart family: `reconcile.py:325-454`, fire-and-forget, NOT in scope.
- Separators: `reconcile.py:170-195`, keep as-is.
- Config: `CollectionsConfig` at `schema.py:209-256` (booleans only);
  example YAML `config/autoposter.example.yaml:43-59`;
  `tests/test_example_config_matches_schema.py:26-40` gates every example
  key. Live sections: `config/live.py:76-79`.
- DB: `managed_collections` (`db/models.py:261-306`): `(library,title)`
  unique, `kind`, `plex_rating_key`, `definition_hash` (members hash
  today), `poster_sha256`, stats columns. NO membership table, NO builder
  state. Alembic merge-revision precedent exists (`6c515e89a1f0`).
- Scheduler: `collections_reconcile` (`scheduler/jobs.py:40-77`) reads
  `holder.current` per run; CLI `python -m autoposter.collections`
  (`collections/__main__.py`). Row 115 defect: `reconcile_libraries`
  never raises → `last_status=ok` even when every library failed
  (`service.py:196-203`).
- Provider plumbing to reuse: `providers/fetch.py:17-40` `fetch_json`
  (cache-fronted, credential-stripped keys), `providers/cache.py`
  (Postgres `provider_cache`).
- Test idioms: `test_collection_lists.py:24-138` (FakeCollection with
  `_live` vs `_cache`, editSummary raises NotFound, raw-PUT `query()`);
  `test_collection_sources.py:33-130` (FakeGuid/FakeItem/FakeSection +
  recorded fixtures `tests/fixtures/collections/ev0000003.yml`,
  `imdb_chart.json`); `test_plexapi_collection_contract.py` (pin new
  plexapi surface here); `conftest.py:104-121` blocks outbound network.
- Rows folded in: 104 (`sort_title` prefix + `collection_mode` on the
  create paths `reconcile.py:364-368` / `lists.py:145-150`), 115 (above),
  124 (`ManagedCollection.title` interpolated unsanitized into the poster
  path — contain the WRITE in `collections/posters.py` to assets_root by
  realpath, the manualassets idiom).
- 7b conflict pressure: gone — this branch is off post-merge main.

---

## Task 1: Builder interface, registry, and typed definition config

**Files:**
- Create: `src/autoposter/collections/builders/__init__.py`,
  `src/autoposter/collections/builders/base.py`
- Modify: `src/autoposter/config/schema.py` (definition models on
  `CollectionsConfig`), `config/autoposter.example.yaml`
- Test: `tests/test_builder_base.py`, `tests/test_collection_config.py`
  (extend), `tests/test_example_config_matches_schema.py` stays green

**Interfaces (later tasks rely on these exactly):**
- `ExternalId = tuple[namespace, value]` with `namespace ∈ {"imdb","tmdb","tvdb","plex"}`
  (plex = rating key, for the trivial builder).
- `class BuilderResult: ids: list[ExternalId]` (ordered, may repeat —
  resolver dedupes); `summary: str | None` (builder-derived summary, e.g.
  chart translations).
- `class BuilderContext`: `library: str`, `library_type: str`, `http`,
  `config` (the definition's params), `cache` — everything a fetcher
  needs; NO Plex section (builders never touch Plex; resolution is the
  engine's).
- `Builder` protocol: `type_name: str`; `async build(ctx) -> BuilderResult`
  (raises on failure — the ENGINE contains it).
- `REGISTRY: dict[str, Builder]` + `register(builder)`; unknown type in
  config = validation error at load time, not runtime.
- `CollectionDefinition` (pydantic, on `CollectionsConfig` as
  `definitions: list[CollectionDefinition] = []`): `title: str`,
  `builder: str` (registry key), `params: dict = {}` (builder-validated),
  `libraries: list[str] | None` (None = collections.libraries),
  `summary: str | None`, `sort: str = "custom"`,
  `sync_mode: Literal["sync","append"] = "sync"`, `limit: int | None`,
  `schedule: ScheduleGate | None` (`every_n_runs: int = 1`,
  `months: list[int] | None` date window), `labels: list[str] = []`,
  `sort_title: str | None`, `collection_mode: str | None`.
  `definition_config_hash(defn) -> str` (sha256 of the canonical dump).
- The trivial `plex_id` builder (roadmap's end-to-end proof) lives in
  `builders/base.py`: `params: {ids: [rating keys]}` → plex-namespace ids.

- [ ] Failing tests: registry round-trip + unknown-type load error;
      definition model validates (bad sync_mode, negative limit rejected);
      example YAML gains a commented `definitions:` example and the
      schema-gate passes; `plex_id` builder returns ordered plex ids;
      `definition_config_hash` stable across dict ordering.
- [ ] Full backend suite; ruff. Commit.

## Task 2: Multi-namespace resolution

**Files:**
- Modify: `src/autoposter/collections/resolve.py`
- Test: `tests/test_collection_resolve.py` (extend)

**Interfaces:**
- `build_owned_index(section) -> OwnedIndex` — ONE `section.all()` pass
  building `{namespace: {value: item}}` for imdb/tmdb/tvdb from item
  `guids` plus `{"plex": {ratingKey: item}}`. Replaces `build_imdb_index`
  (keep a thin alias if the sources still call it mid-phase).
- `resolve_external(index, ids: list[ExternalId]) -> ResolvedList`:
  order-preserving, deduping BY ITEM (an item reachable via two
  namespaces appears once, first position wins), returns
  `(items, unresolved_count)`.
- Existing IMDb-only behavior reproduced exactly for imdb-namespace input
  (the golden port depends on it).

- [ ] Failing tests: index built from FakeItems with mixed guids; tmdb-
      and tvdb-namespace resolution; cross-namespace dedupe (same item via
      imdb and tmdb ids = one entry, position of first); unresolved
      counted; order preserved. Mutation proof: break order preservation →
      the order test reds.
- [ ] Full backend suite; ruff. Commit.

## Task 3: Port the three shipped sources — golden gate

**Files:**
- Create: `src/autoposter/collections/builders/imdb_chart.py`,
  `builders/imdb_award.py`, `builders/cs_bucket.py`,
  `src/autoposter/collections/engine.py`
- Modify: `src/autoposter/collections/sources.py` (build_all delegates to
  the engine; the constants become DEFAULT definitions produced by
  `default_definitions(config)` so behavior with an empty `definitions:`
  list is UNCHANGED), `collections/service.py` (`_managed_titles` fed
  from the engine's definition enumeration)
- Test: `tests/test_builder_port_golden.py` (NEW — the gate),
  existing collection tests stay green untouched

**Interfaces:**
- `engine.run_definitions(session, section, library, definitions, config,
  http, dry_run, …) -> list[str]` — for each definition (schedule-gated,
  library-matched): containment wrapper → `builder.build` → resolve →
  apply `limit` → `reconcile_list_collection(...)`; failure ⇒
  `"<title>: source failed (<class name>)"` action string, no changes.
- CS buckets stay SMART (fire-and-forget path) — the cs_bucket builder
  wraps `derive_buckets` + `reconcile_content_ratings` behind the same
  definition surface; it ignores sync/limit knobs (validation rejects
  them on smart-kind definitions).
- The five dynamic Oscars year collections come from `recent_years()`
  inside the imdb_award builder, exactly as today.

- [ ] **Golden test first**: capture the action strings of a full
      `build_all` run over the existing fixtures/fakes AT THE PARENT
      COMMIT (write them to `tests/fixtures/collections/golden_port.json`
      via a one-off script run BEFORE the port lands; document the
      commands in the report); after the port, the same harness through
      the engine must produce byte-identical strings. This test is the
      task's definition of done.
- [ ] Failing tests: engine containment (builder raises → failed string,
      collection untouched — mutation proof: drop the wrapper → red);
      limit applied post-resolve; schedule gate skips (every_n_runs=2
      runs alternate passes; months window); `_managed_titles` includes
      engine definitions (leftovers report stays correct).
- [ ] Full backend suite; ruff. Commit.

## Task 4: sync/append, dry-run preview, opt-in deletes, honest failure

**Files:**
- Modify: `src/autoposter/collections/lists.py` (append mode),
  `collections/service.py` + `scheduler/jobs.py` (row 115: any library
  failure ⇒ job not-ok), `config/schema.py`
  (`delete_unconfigured: bool = False`, `max_deletes: int = 5`),
  `collections/reconcile.py` or `engine.py` (the delete sweep)
- Create: `src/autoposter/api/collections_builders.py` —
  `POST /api/collections/preview` (run engine dry-run for one definition
  or all, return per-definition `{title, adding, removing, deleting,
  unresolved, failed}` counts + action strings)
- Test: `tests/test_builder_knobs.py`, `tests/test_api_collections_builders.py`

**Interfaces:**
- Append: `sync_mode="append"` ⇒ removals list is always empty; adds
  still ordered; members hash accounts for mode so switching modes
  re-reconciles. Mutation proof: append removing a manual member → red.
- Deletes: after reconciling definitions, the engine lists owned+managed
  collections in the library whose titles are NOT enumerated; when
  `delete_unconfigured` off (default) ⇒ report-only (today's leftovers
  behavior); when on ⇒ delete THROUGH the guards (ownership label AND
  managed row AND not protected-label; cap `max_deletes` per pass with
  refuse-with-numbers; dry-run reports would-deletes). Deleting removes
  the Plex collection and the managed row (renders/assets untouched).
  Mutation proofs: drop the ownership filter → red; drop the cap → red.
- Row 115: `reconcile_libraries` returns a structured result; the
  scheduler job maps any-failed → `last_status="failed"` with the detail
  listing which libraries/definitions failed.

- [ ] Failing tests first for every knob above (red transcripts in the
      report); endpoint auth-swept; 409/no-concurrency questions do NOT
      apply (preview is read-only).
- [ ] Full backend suite; ruff. Commit.

## Task 5: Ride-along collection settings (rows 28, 29, 30, 68, 69, 104, 124)

**Files:**
- Modify: `collections/lists.py` + `reconcile.py` (sort_title prefix +
  collection_mode on BOTH create paths — row 104; labels row 29),
  `collections/engine.py` (tmdb/tvdb summary pull — row 30, via
  `facts/tmdb.py`-style fetch through the provider cache; hub visibility
  row 68 — `visible_library/home/shared` + `hub_priority` via plexapi,
  pin the contract in `test_plexapi_collection_contract.py`; member-item
  labels row 69 — `item_label` add/remove/sync on resolved members),
  `collections/posters.py` (row 124: realpath-contain the poster write to
  assets_root; refuse outside), `config/schema.py` (the per-definition
  fields: `label`/`label_sync`, `tmdb_summary: bool`, `visible_*`,
  `hub_priority`, `item_label`), lifecycle ops row 28
  (`blank`, `delete_named`, `mass_collection_mode`) as explicit
  operator endpoints in `api/collections_builders.py` behind
  confirmation-style POST bodies.
- Test: `tests/test_builder_settings.py`, extend the plexapi contract pin,
  `tests/test_collection_posters.py` (containment red-first)

- [ ] Failing tests first; row-124 containment mutation proof (title with
      `..` escapes → refused; drop the check → red). Hub/visibility calls
      against fakes + contract pins. Full backend suite; ruff. Commit.

## Task 6: Definitions UI touch + docs

**Files:**
- Modify: `frontend/src/pages/Collections.tsx` (+css/test): a
  "Definitions" panel listing config definitions with per-definition
  status from the last run (managed / failed / unresolved counts) and a
  Preview button calling `/api/collections/preview` rendering the counts
  + action strings; the Settings editor already edits the YAML overrides
  (nothing new needed there — verify and state it).
- Test: `Collections.test.tsx` (extend)

- [ ] Failing tests: definitions render, preview fires and shows
      adding/removing/deleting counts, failed definitions visibly
      flagged; mobile conventions (`.table-scroll` etc.). Frontend suite
      + build. Commit.

## Task 7: Roadmap, whole-branch review, PRs

- Roadmap: rows 95, 25, 26, 27, 28, 29, 30, 68, 69, 70, 104, 115, 124
  `answered 8a:`; row 92 partial note pinned to exactly what shipped
  (per-definition libraries + per-library params); 8a phase entry
  delivered; follow-up rows for anything discovered.
- Whole-branch review (most capable model): the golden port gate, the
  delete guards, and the containment invariant are the load-bearing
  targets. One batched fix round. Re-review if findings.
- Rebase onto main if moved; both suites; ship as TWO PRs if the diff is
  large (engine+port+knobs → main; ride-alongs+UI stacked) — and if
  stacked, the PR descriptions MUST carry the retarget-before-merge
  instruction (lesson from the 7b stack). Otherwise one PR. No AI
  attribution.
