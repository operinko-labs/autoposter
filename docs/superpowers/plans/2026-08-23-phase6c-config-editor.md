# Phase 6c: Config Editor with Hot-Reload and Impact Preview — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The operator edits configuration from the UI with schema validation,
sees "this change would re-render ~N items" before committing, chooses
apply-now vs regenerate-later, and the running service picks the change up
without a restart for everything read per-use — with an honest "restart to
apply" marker on what is not. Closes roadmap rows 94 and 54.

**Architecture (user-decided 2026-08-23):** Edits live in a **database
overrides layer** — a single JSONB document deep-merged over the
ConfigMap-delivered YAML at load time. The ConfigMap stays git/Flux-owned for
defaults; the app owns deltas; nothing ever writes the mounted file (it is
read-only in the pod, and an in-app writer would diverge from git). Reload is
a **config-generation swap**: a new immutable `Config` is built from
base+overrides, validated whole (never half-applied, spec §7), and swapped
in; consumers that read per-use see it immediately, consumers frozen at
startup are restart-flagged in the UI. The impact preview recomputes
fingerprints offline from stored render rows — provably side-effect-free.

**Scope guards:** Secrets remain env-only — the overrides document must never
contain a `secrets` key and the editor never renders secret inputs. Badge
appearance is NOT configurable today (roadmap row 97), so the roadmap's
"editing a badge colour" acceptance line is delivered as "editing an artwork
text setting" — stated in the PR. `api_docs_enabled`, `workers`,
`providers.*`, `notifications.*`, `plex.*`, `operations.imdb_refresh_*` are
restart-flagged, not rebuilt.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds throughout (note the
  cancelled-task/idle-in-transaction trap and the timeout wrapper form).
- Container-only testing from `D:\Sites\autoposter` (PowerShell); one pytest
  session at a time; zero skips.
- Baselines at branch start (verify): backend **1333 passed** under
  `-m "not imagemagick"`, frontend **97 passed** — parent is the 6b branch
  tip (or main if #48 merged; record actual).
- Alembic single head (`tests/test_migrations.py:89`); current head is the
  6a stats migration `3f9b2ad74c81` — verify before writing `down_revision`.
- Mutation proofs for every guard; copy+cmp restores.
- **Never half-apply** (spec §7:543): an invalid merged config must change
  nothing — no overrides row write, no swap, structured errors back.
- **The preview must be side-effect-free** (roadmap risk 2): its tests
  assert zero `jobs` rows and zero `renders` mutations after a preview.
- The structural auth sweep covers every new endpoint (inline
  `Depends(require_session)`).
- `GET /api/config` keeps redacting: secrets wholesale, `notifications.url`
  host-only. New endpoints must not leak either.
- Stage by name; `--no-gpg-sign`; no AI attribution; no new dependencies.

## Verified facts (file:line checked 2026-08-23 by controller; authoritative)

- Loading: `load_config` (`config/loader.py:52-64`) = read → `yaml.safe_load`
  → `Config(**data)` → `config.version = render_version(config)` (:63).
  `render_version` (:10-49) hashes ONLY render-affecting fields: `artwork`
  dump, `library_folders`, `assets_root`, `manual_assets_root`,
  `fonts_root`, `overlays_root`. `model_copy(update=...)` does NOT re-derive
  version — only `load_config` does; `pipeline.py:130` relies on that.
- Config path: `AUTOPOSTER_CONFIG` default `/config/autoposter.yaml`
  (`main.py:16`), duplicated in `collections/__main__.py:29` and
  `adopt/__main__.py:26` — both CLIs must gain overrides too (they already
  have DB access).
- No reload/write machinery exists anywhere; `GET /api/config`
  (`routes.py:766-795`) is the only config endpoint.
- `app.state.config` set once (`app.py:197`). Read per-use (live under a
  rebind): `intake/routes.py:29,89`; `api/routes.py:208,597,692,785`;
  `api/artwork.py:176,278`.
- Frozen-at-startup captures (the restart-flag list, from `app.py`
  lifespan): providers (:68-79,:252-270), notifier (:84), PlexHealth
  (:87-94), artwork_probe partial (:108-112), **worker handler partial
  `config=config` (:113-118)**, imdb refresh (:119-126), scheduler job set +
  factories closing over config (:136-143, `scheduler/jobs.py`),
  `Scheduler(poll_seconds=...)` (:157-160), `run_workers(config.workers)`
  (:164-168), PlexClient (`main.py:70-73`), `api_docs_enabled`
  (:189-196), StatusBroadcaster `self._config` (`dashboard_stream.py:103`).
- BUT within worker/scheduler bodies these settings are read per-item off
  the closured object: `badges.*` (`pipeline.py:602,628,679,686`),
  `artwork.*`/roots/`magick_binary`/`skip_tba` (`pipeline.py:306-504`),
  `operations.enabled/.write_to_plex` (:566-572,736),
  `collections.apply_to_plex/.charts/...` (`collections/service.py:74-116`),
  `plex.resolve_max_attempts` (`app.py:316`). So handing these consumers a
  HOLDER instead of the instance makes all of them live in one move.
- Scheduler cadence: `Job.interval_seconds` computed at factory time
  (`jobs.py:63` etc.) but READ each poll by `claim_due`
  (`scheduler/core.py:73-76` via the SQL parameter) — making it a per-poll
  deref of the holder livens cadence edits (closes row 54 honestly).
  `app.state.scheduler_intervals` is filled in place (`app.py:154-156`,
  `.update` — aliasing pinned by test) and the dashboard broadcaster holds
  that dict; cadence liveness must keep it current on swap.
- `compute_fingerprint(config_version, art_kind, source_url, base_sha256,
  text_inputs, asset_hashes)` (`pipeline.py:56-76`, `\x1f`-join sha256).
  `gather_fingerprint_inputs` (:144-189): text_inputs from `title_text_for`
  (:101-120, config-gated); asset_hashes = overlay sha + font shas +
  logo_sha; **logo_sha and draw_text are render-time facts** — the adopted
  approximation (`adopted_fingerprint` :192-207) defaults
  `draw_text=True, logo_sha=""` for exactly this reason. `source_url` and
  `base_sha256` are stored per render row (`db/models.py:72,74`).
  `badge_fingerprint` takes config only transitively via base fingerprint
  (`badges/compose.py:106-122`).
- `settings.enabled` and `_should_skip_title` short-circuit before
  fingerprinting (`pipeline.py:314-324`) — the preview must apply the same
  gates or overcount.
- Full-pass enqueue precedent: `enqueue_batch` with the pending-dedupe
  ON CONFLICT arbiter (`api/routes.py:696-763`) — apply-now reuses it,
  filtered to affected items.
- Example-config guard: `tests/test_example_config_matches_schema.py`
  (walks one nesting level; unknown keys are the failure).
- Settings page (`frontend/src/pages/Settings.tsx`, 225 lines):
  `ScalarValue`/`ListValue`/`ConfigNode`/`ConfigSections`, shape-driven, no
  schema knowledge, `secrets` pinned last, placeholder text at :216-219
  promising this phase. `REDACTED_MARKER` duplicated client-side (:26).
- Pydantic models carry no descriptions in `model_json_schema()` — the
  editor derives widget types from the VALUES' JSON types plus a small
  server-sent constraint map (below); no schema-description work in scope.
- Config test idioms: `model_copy(update=...)`
  (`test_api_clear_override.py:59`), YAML text-substitution `_variant`
  (`test_config.py:137-142`), version tests (`test_config.py:118-188`).

---

## Task 1: Overrides store, merged loading, ConfigHolder generation swap

**Files:**
- Create: `alembic/versions/<rev>_config_overrides.py`,
  `src/autoposter/config/overrides.py`, `src/autoposter/config/holder.py`
- Modify: `src/autoposter/config/loader.py`, `src/autoposter/db/models.py`,
  `src/autoposter/main.py`, `src/autoposter/collections/__main__.py`,
  `src/autoposter/adopt/__main__.py`
- Test: `tests/test_config_overrides.py`, extend `tests/test_config.py`

**Interfaces:**
- `ConfigOverride` model: single-document table `config_overrides`
  (`id` PK, `document JSONB NOT NULL`, `updated_at timestamptz`) — at most
  one row (enforce: fixed `id=1` upsert).
- `merge_overrides(base: dict, overrides: dict) -> dict`: recursive
  dict-deep-merge (override scalars/lists replace; dicts merge). A
  `secrets` key anywhere in overrides is a `ValueError` — tested.
- `load_effective_config(path, session) -> Config`: yaml base dict +
  overrides document → merged dict → `Config(**merged)` →
  `version = render_version(...)` (same derivation as `load_config`; factor
  the tail of `load_config` so there is ONE construction path).
- `ConfigHolder(initial: Config)`: `.current` (property), `swap(new)`.
  Plain attribute read — no locks needed (asyncio, single assignment).
- `main.build()` and both CLIs load via `load_effective_config`; the app
  publishes `app.state.config_holder` AND keeps `app.state.config` rebound
  on every swap (per-request readers stay correct with zero changes).

- [ ] Failing tests: merge semantics (scalar/list replace, dict merge,
      secrets rejection); effective load applies overrides and re-derives
      version (an artwork override changes `version`, a scheduler override
      does not — mirror `test_config.py`'s style); no overrides row ⇒
      byte-identical behavior to `load_config`; holder swap visible through
      `.current`; CLIs load effective config (unit-test the loader call
      they make, not the whole CLI).
- [ ] Migration (down_revision = current single head — verify it), both
      alembic guards green.
- [ ] Mutation proof: break the secrets rejection — its test reds.
- [ ] Full backend suite; ruff; commit.

## Task 2: Generation handoff — closured consumers read the holder

**Files:**
- Modify: `src/autoposter/app.py`, `src/autoposter/scheduler/core.py`,
  `src/autoposter/scheduler/jobs.py`, `src/autoposter/api/dashboard_stream.py`
- Test: extend `tests/test_scheduler_core.py`, `tests/test_worker.py` (or
  the actual handler-path test file — read the tree),
  `tests/test_api_dashboard_stream.py`

**Interfaces:**
- The worker handler partial carries `config_holder=holder`;
  `_handle_intent` derefs `holder.current` per job (`process_item`'s own
  signature is untouched — it already takes config per call).
- Scheduler `Job` gains live cadence: `interval_seconds` becomes a
  zero-arg callable (or the Job keeps a holder reference and a
  `current_interval()` method — pick the smaller diff and justify);
  `claim_due` reads it per poll; the factories build it from
  `holder.current.scheduler.*`; job run-bodies deref the holder instead of
  the closured config. After a swap, `app.state.scheduler_intervals` is
  refreshed in place (`.update` + delete-stale-keys — the broadcaster and
  /api/status pick it up by aliasing, already pinned by test).
- `StatusBroadcaster` takes the holder (derefs per poll).
- The restart-flag map lives here as data:
  `FROZEN_SECTIONS: dict[str, str]` (dotted-path prefix → one-line reason),
  covering: `workers`, `providers`, `notifications`, `plex` (except
  `resolve_max_attempts`), `operations.imdb_refresh_enabled/_hours`,
  `api_docs_enabled`, `scheduler.enabled`, `collections.enabled`/
  `arr_sync.enabled` (job SET membership; cadences are live). Exported for
  Task 3's endpoint.

- [ ] Failing tests: a holder swap changes the config a subsequently
      processed job sees (drive the handler with a fake intent);
      a cadence override + swap changes when `claim_due` fires next (SQL
      time-manipulation idiom from `test_scheduler_core.py:30-32`);
      the broadcaster's next snapshot reflects a swapped
      `workers` value... (`workers` is frozen for the pool but the
      broadcaster reports `config.workers` — report the HOLDER's value with
      a comment that the pool needs restart; assert the flag map covers it).
- [ ] Mutation proof: revert the handler partial to a config instance —
      the swap-visibility test reds.
- [ ] Full backend suite; ruff; commit.

## Task 3: Save, preview and impact endpoints

**Files:**
- Create: `src/autoposter/config/impact.py`
- Modify: `src/autoposter/api/routes.py`
- Test: `tests/test_api_config_editor.py`, `tests/test_config_impact.py`

**Interfaces:**
- `GET /api/config` gains, alongside the existing redacted dump:
  `"overridden_paths": [dotted paths present in the overrides document]`,
  `"frozen_paths": FROZEN_SECTIONS`, `"version": <current>` (already
  present as a field — keep).
- `PUT /api/config/overrides` body `{document: {...}}`: validate the MERGED
  result (`Config(**merged)`); on pydantic failure → 422 with
  `[{path: "artwork.poster.text.font_size", message: ...}]` mapped from
  `e.errors()` (loc→dotted path) and NOTHING persisted or swapped
  (never-half-apply, mutation-prove it); on success → upsert the document,
  build the new Config, `holder.swap`, rebind `app.state.config`, refresh
  `scheduler_intervals`, write one `events_log` row
  (source="config", event_type="overrides_updated", outcome=
  "version <old> -> <new>"; NO document contents in the payload), respond
  `{version_before, version_after, restart_required: [paths in the
  overrides delta that hit FROZEN_SECTIONS]}`.
- `POST /api/config/preview` body `{document: {...}}`: same validation
  path (422 identical shape); on success computes WITHOUT persisting:
  `{version_before, version_after, restart_required: [...],
  impact: {affected: N, by_art_kind: {...}, of_total: M} | null}` —
  `impact` is null when `version_after == version_before` AND no
  text/asset-affecting field changed (cheap short-circuit).
- `impact.py`: `count_affected(session, new_config) -> Impact`. Per render
  row (join media_items): apply the same gates the pipeline applies
  (`settings.enabled`, `skip_tba`/`_should_skip_title`); rebuild
  text_inputs via the real `title_text_for`; hash overlay/font files under
  the new config's roots ONCE per distinct file (cache dict); reuse stored
  `source_url`/`base_sha256`; approximate render-time facts exactly as
  `adopted_fingerprint` does (`draw_text=True, logo_sha=""` — cite
  pipeline.py:161-163 in a comment); compare to the stored `fingerprint`.
  Count mismatches. READ-ONLY: takes a session, never flushes; tests
  assert no `jobs` rows and unchanged `renders` after a run. Honest
  caveat in the docstring: rows whose real render used a logo will show
  as affected under the approximation — an overcount, never an undercount
  of text/version changes; state it in the UI copy ("~").
- `POST /api/config/apply` body `{document: {...}}`: the apply-now arm —
  validates+saves+swaps exactly like PUT (share the implementation), then
  enqueues `process_item` for exactly the affected items via
  `enqueue_batch` (the full-pass dedupe arbiter), returns the PUT response
  plus `{queued, skipped}`. Regenerate-later is simply PUT (drift/full-pass
  picks changes up naturally) — no third endpoint.

- [ ] Failing tests: 401s ×3 (sweep covers, plus explicit); 422 path shape
      on a bad merged config with nothing persisted (then GET shows no
      override) — mutation-prove the never-half-apply by making the
      handler persist before validating (test reds); PUT persists + swaps
      (subsequent GET /api/config reflects it; version delta correct;
      restart_required lists a frozen path when touched and is empty
      otherwise); events row written, secrets never in it; preview
      counts: a text-setting change affects exactly the title-card rows
      of the fixture set, a poster-only overlay change affects poster
      rows only, a no-op documents `impact: null`; preview is
      side-effect-free (no jobs, renders unchanged — mutation-prove by
      making impact flush a change); apply enqueues exactly the affected
      items (dedupe respected).
- [ ] Full backend suite; ruff; commit.

## Task 4: Editor UI — editing, provenance, restart flags

**Files:**
- Modify: `frontend/src/pages/Settings.tsx`,
  `frontend/src/pages/settings.css`, `frontend/src/api/types.ts`,
  `frontend/src/api/client.ts` (only if a helper is genuinely missing)
- Test: extend `frontend/src/pages/Settings.test.tsx`

**Interfaces (consumes Task 3):** enriched GET; PUT; the editor builds an
overrides DOCUMENT (only touched fields), never round-trips the whole
config.

Behavior: fields become editable in place — widget by value type (boolean
pill → toggle, number → number input, string → text input, string list →
editable list); `secrets` panel stays read-only pills (never editable);
fields under `frozen_paths` show a "restart to apply" marker when edited;
fields in `overridden_paths` show an "overridden" badge with a
clear-override control (removing the path from the document); a dirty-state
bar shows the pending document with Save (PUT) and Preview buttons; 422
errors render inline at the field via the dotted path; on save success show
`version_before → version_after` and any restart_required list. The page's
"Read-only" placeholder (:216-219) is replaced.

- [ ] Failing tests first: editing a value produces a minimal overrides
      document (only the touched path); save PUTs it and re-renders from
      the response; a 422 lands on the right field; frozen-path edit shows
      the restart marker; overridden badge + clear control round-trips;
      secrets stay uneditable. Mutation proofs: (1) make the document
      builder send the full config — the minimal-document test reds;
      (2) drop the 422 path mapping — the inline-error test reds.
- [ ] Frontend suite ≥ baseline+new, 0 skips; build clean; commit.

## Task 5: Preview & apply UI

**Files:**
- Modify: `frontend/src/pages/Settings.tsx`, `settings.css`, `types.ts`
- Test: extend `frontend/src/pages/Settings.test.tsx`

Behavior: Preview posts the pending document; renders
"~N of M items would re-render" with the by-art-kind breakdown (the `~` and
a tooltip carrying the logo-approximation caveat verbatim from the API
docstring); then two actions: **Apply now** (POST /api/config/apply →
show `{queued, skipped}`) and **Save only** (PUT — regenerate-later, with
copy saying drift/full-pass will pick it up). `impact: null` renders as
"no re-renders — this change does not affect rendered artwork".
Buttons disabled in flight; errors inline.

- [ ] Failing tests: preview renders count + breakdown from a stubbed
      response; apply fires /api/config/apply and surfaces queued/skipped;
      save-only fires PUT and not apply; null impact renders the no-op
      copy. Mutation proof: swap apply/save endpoints — both action tests
      red.
- [ ] Frontend suite, 0 skips; build clean; commit.

## Task 6: Docs, roadmap, final review, PR

- `deploy/README.md`: a new section on the overrides layer — precedence
  (DB over ConfigMap), where edits live, how to inspect/clear them
  (SQL one-liner), the restart-to-apply list, and that the ConfigMap
  remains the git-owned base. Update the cutover note's "edit the
  ConfigMap" framing where it now has a UI alternative.
- Roadmap: rows 94, 54 `answered 6c:`; 6c phase entry **delivered**, with
  the badge-colour acceptance substitution and the DB-overrides decision
  noted; any revealed follow-ups as new rows.
- Whole-branch review (most capable model) with the accumulated minors
  list; one batched fix round; rebase onto main (#48 will have merged);
  both suites post-rebase; PR via `tea` (design decisions, the honest
  restart-flag list, impact-approximation caveat, suite deltas).
