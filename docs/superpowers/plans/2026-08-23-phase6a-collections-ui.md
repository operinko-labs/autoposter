# Phase 6a: Collections UI, Search, Compare, Override Actions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Collections page shows member counts, last diff result (+3/−1),
next refresh, and a "diff now" button; every scheduled job gets a "Run now"
button on the dashboard; the library browser gets its search box; item detail
compares all art kinds side by side and can clear a manual override. Closes
roadmap rows 22, 23, 24 and the collections half of the spec §6 remainder.

**Architecture:** One migration adds per-collection stat columns that the
list-collection reconcile path stamps at the single point where structured
diff data exists (`lists.py:161-174`); the collections endpoint exposes them.
Run-now is one generic endpoint exploiting `claim_due`'s existing
`last_started_at IS NULL → due` semantics — "diff now" is just its
collections_reconcile case. Search is frontend-only (`GET /api/items` already
accepts `search`). Clear-manual-override renames the override file on the
manualassets mount (the override IS a file — nothing in the DB can suppress
it) and clears the render fingerprint so the next pass regenerates.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds throughout — including the
  corrected container-timeout form recorded at its end.
- Container-only testing from `D:\Sites\autoposter`: backend
  `docker compose run --rm test pytest …` (this checkout, plain `-p`), one
  pytest session at a time; frontend `docker compose run --rm web npm test`
  and `npm run build`. Zero skips.
- Baselines on this branch's parent (origin/main 727bad9): backend **1271
  passed** under `-m "not imagemagick"`, frontend **57 passed**. (Do not
  confuse with other branches' counts.) Record your own at branch start.
- Every guard test mutation-proven (mutate → red → copy+cmp restore → green;
  never `git checkout -- <file>`); transcripts in the task report.
- The structural auth sweep (`tests/test_api_login.py`) walks
  `app.openapi()`: every new endpoint carries `require_session`. Never weaken
  the sweep.
- Alembic: exactly one head after the migration
  (`tests/test_migrations.py:89` guards it; current head `6c515e89a1f0`).
  Follow `alembic/versions/61c977285fa7_collection_poster_hash.py` as the
  add-column template.
- Never call `.refresh()` on plexapi objects (AST-walk guard,
  `tests/test_plex_writer.py`). Read python-plexapi docs before using any
  new plexapi function.
- No new dependencies. Stage by name; `--no-gpg-sign`; no AI attribution.
- Frontend artwork always via `apiFetchImage`/object URLs, never bare
  `<img src>` (bearer-only auth — the roadmap's known 6a trap).

## Verified facts (hand these to every implementer; file:line checked 2026-08-23)

- `reconcile_list_collection` (`src/autoposter/collections/lists.py:62-196`)
  computes `adding`/`removing` (plexapi item lists) and `moves` (int) at
  :161-165 and flattens them at :171-174
  (`"updated %r: +%d -%d, %d move(s)"`); `len(items)` is the desired member
  count (:69). ManagedCollection row create/update: `lists.py:177-186`
  (kind="manual", inside `if not dry_run:`); unchanged-hash short-circuit
  ABOVE it at :131-136. Smart-bucket rows: `reconcile.py:379-388`; separator:
  `reconcile.py:256-265`. Smart collections have NO member count (Plex
  evaluates the filter live) — their stats stay NULL.
- `ManagedCollection` model: `src/autoposter/db/models.py:250-285`; unique
  `(library, title)`.
- `GET /api/collections` (`routes.py`, `list_collections`): returns only
  `{id, library, title, kind}` today.
- `GET /api/status` `scheduled_jobs` (`routes.py:180-226`): `{name,
  last_started_at, last_finished_at, last_status, last_detail}` from
  `ScheduledRun` rows ordered by name. `interval_seconds` is NOT exposed —
  it lives only on the in-memory scheduler `Job` dataclass
  (`scheduler/core.py:34-38`), built in `app.py:128-136` (registration is
  config-conditional).
- `claim_due` (`scheduler/core.py:41-84`): `last_started_at IS NULL` is due
  unconditionally; scheduler polls every `poll_seconds` (default 60). Job
  names: `collections_reconcile`, `ratings_drift_sweep`, `arr_sync`,
  `asset_cleanup` (`scheduler/jobs.py:63,157,291,517`).
- Manual override = a file at `manual_override_path(config, item, art_kind)`
  (`render/pipeline.py:123-134`): the assets layout rooted at
  `config.manual_assets_root` (example `/manualassets`). Checked every pass
  at `pipeline.py:356-390`; stamps `provider="manual"`,
  `source_url=str(override)`; the fingerprint (:426-436) is what
  short-circuits an unchanged pass. `provider` is the only DB trace. No
  clear endpoint exists.
- `item_detail` `renders` array (`routes.py:414-426`): art_kind, status,
  fingerprint, badge_fingerprint, upload_status, adopted, rendered_at,
  uploaded_at — NOT provider/source_mode/asset_path.
- ItemDetail.tsx shows ONE art kind (`artKindFor(item.kind)`, :315) as
  base+live panes (`BasePane`/`LivePane`, `useArtwork()` → `apiFetchImage`).
  `artKind.ts` exports `ART_KIND` map + `artKindFor` (fallback "poster").
- Library.tsx filter idiom: three selects + `choose()` helper that resets
  `offset` (:174-179); query assembly :145-169; backend `search` param
  already exists (`routes.py:262-271`, escaped ilike).
- Collections.tsx: 65 lines, one fetch, table Title/Library/Kind, empty
  state `No managed collections yet.`; no test file exists for it.
- Test idioms: backend API tests per `tests/test_api_library.py` (:244
  collections section); scheduler time manipulated with raw SQL
  (`test_scheduler_core.py:30-32`); reconcile tests use hand-rolled plexapi
  doubles (`test_collection_reconcile.py:14-53`); frontend per
  `ItemDetail.test.tsx` / `Library.test.tsx`.

---

## Task 1: Migration + reconcile stat capture

**Files:**
- Create: `alembic/versions/<rev>_collection_reconcile_stats.py`
- Modify: `src/autoposter/db/models.py` (ManagedCollection),
  `src/autoposter/collections/lists.py`
- Test: `tests/test_collection_lists.py`, `tests/test_managed_collections.py`

**Interfaces:**
- Produces on `ManagedCollection`: `member_count: int | None`,
  `last_added: int | None`, `last_removed: int | None`,
  `last_reconciled_at: datetime | None` (all nullable — NULL means "not a
  list collection or never reconciled since upgrade"; smart/separator rows
  never get values).

Stamping rules (all inside `reconcile_list_collection`, never in dry-run —
dry-run creates no rows today and must stay write-free):
- On the create and update paths (`lists.py:177-186` region):
  `member_count = len(items)`, `last_added = len(adding)`,
  `last_removed = len(removing)`, `last_reconciled_at = func.now()`.
- On the unchanged-hash short-circuit (:131-136): the pass still confirmed
  membership is as desired, so stamp `member_count = len(items)`,
  `last_added = 0`, `last_removed = 0`, `last_reconciled_at = func.now()`
  on the existing row BEFORE returning. (The row exists — the short-circuit
  compares against its stored hash.)
- The empty-source guard (:91-93) stamps nothing (the collection was left
  untouched on purpose).

- [ ] Failing tests first: create-path stamps count/adds/removes;
      update-path stamps the delta (+N/−M matching the action string's
      numbers — assert both agree); unchanged-hash path refreshes
      member_count and zeroes the delta; dry-run stamps nothing; smart
      bucket rows keep NULLs (extend a `test_collection_reconcile.py` case
      or assert via `test_managed_collections.py`).
- [ ] Migration via the 61c977285fa7 template (down_revision =
      `6c515e89a1f0`); `test_alembic_has_a_single_head` and
      `test_alembic_head_matches_models` pass.
- [ ] Red → implement → green; mutation proof: break the unchanged-hash
      stamp (skip it) — its test reds.
- [ ] Commit.

## Task 2: API — collections stats, run-now, intervals, render provenance

**Files:**
- Modify: `src/autoposter/api/routes.py`, `src/autoposter/app.py`,
  `frontend/src/api/types.ts` (type mirrors only — UI tasks consume them)
- Test: `tests/test_api_library.py` (collections section),
  `tests/test_api_dashboard.py`, new `tests/test_api_scheduled_runs.py`

**Interfaces (produces):**
- `GET /api/collections` rows gain `member_count`, `last_added`,
  `last_removed`, `last_reconciled_at` (nullable passthroughs).
- `POST /api/scheduled-runs/{name}/run` (require_session): 404 unless
  `name` is one of the four known job names (import the literal set; do not
  invent a registry); upserts the `scheduled_runs` row with
  `last_started_at = NULL` (INSERT ... ON CONFLICT (name) DO UPDATE SET
  last_started_at = NULL); returns
  `{"status": "requested", "poll_seconds": <int>}` so the UI can say "picks
  up within Ns". It does NOT wait for the run.
- `GET /api/status` scheduled_jobs rows gain `interval_seconds: int | None`:
  `create_app` sets `app.state.scheduler_intervals = {}` unconditionally;
  the lifespan's background branch fills `{job.name: job.interval_seconds}`
  from the jobs it registers (`app.py:128-136`); the endpoint merges by
  name, `None` when absent. Frontend computes next-run client-side.
- `item_detail` renders rows gain `provider` (`render.provider`) — the DB
  trace of a manual override (`"manual"`); the UI shows the clear button on
  it.

- [ ] Failing tests: enriched collections row (stats echoed; NULLs for a
      smart row); run-now 401 / 404-on-unknown-name / row upserted with
      NULL last_started_at when absent AND when present (two cases);
      status carries interval_seconds when state dict is filled and None
      when not; item_detail echoes provider. Auth sweep still green
      (run-now must appear in `app.openapi()` with require_session).
- [ ] Red → implement → green; mutation proof: drop the name allowlist —
      the 404 test reds.
- [ ] Commit.

## Task 3: Clear-manual-override endpoint

**Files:**
- Modify: `src/autoposter/api/routes.py`, `src/autoposter/render/pipeline.py`
  (only if a helper needs exporting — prefer importing `manual_override_path`
  as-is)
- Test: new `tests/test_api_clear_override.py`

**Interfaces:**
- Produces: `POST /api/items/{item_id}/renders/{art_kind}/clear-override`
  (require_session). Semantics:
  1. 404 if the item does not exist.
  2. Resolve `manual_override_path(config, item, art_kind)` — the config is
     `request.app.state.config`. If no override file exists: 409 with detail
     `"no manual override for this art kind"` (the button should not have
     been shown; do not silently succeed).
  3. Rename the file to `<name>.disabled` (same directory, suffix appended
     — NOT delete: the operator's file survives and restoring it is a
     rename back). A `.disabled` name that already exists gets overwritten
     (`os.replace`). Rename failure (read-only mount) → 503 with the OS
     error's message; nothing else changed.
  4. Clear `fingerprint` (and `badge_fingerprint`) on the `(item_id,
     art_kind)` render row if one exists, so the next pass cannot
     short-circuit (`pipeline.py:426-436`).
  5. Enqueue a reprocess job exactly the way
     `POST /api/items/{item_id}/reprocess` does (read that handler,
     `routes.py:552-580`, and reuse its enqueue path — same dedupe
     semantics).
  6. Return `{"status": "cleared", "queued": <bool from the enqueue>}`.
- File operations under `asyncio.to_thread` (they run on a network mount).
- The rename target stays inside `manual_assets_root` by construction
  (`manual_override_path` builds it from config + the item's own fields);
  do not accept any path input from the client beyond `art_kind`, and
  validate `art_kind` against the render row / `ART_KINDS_FOR` so an
  arbitrary string cannot probe the mount.

- [ ] Failing tests (tmp_path as manual_assets_root, config via
      `model_copy(update=...)` on the example config): 401; 404 unknown
      item; 409 when no override file; success renames (old gone,
      `.disabled` present), clears both fingerprints, enqueues (job row
      visible with the reprocess dedupe key); invalid art_kind 422/404 (not
      a mount probe); rename-failure path → 503 and fingerprints untouched
      (make the parent dir read-only or monkeypatch `os.replace` to raise).
- [ ] Red → implement → green; mutation proofs: (1) skip the fingerprint
      clear — the regeneration test reds; (2) drop the art_kind validation
      — the probe test reds.
- [ ] Commit.

## Task 4: Frontend — Collections page + dashboard Run now

**Files:**
- Modify: `frontend/src/pages/Collections.tsx`, `frontend/src/pages/Dashboard.tsx`,
  `frontend/src/api/types.ts` (if Task 2 left gaps), CSS file per existing
  page conventions (extend `dashboard.css` or add `collections.css`)
- Test: new `frontend/src/pages/Collections.test.tsx`, extend
  `frontend/src/pages/Dashboard.test.tsx`

**Interfaces (consumes Task 2):** enriched `/api/collections`,
`POST /api/scheduled-runs/{name}/run`, `interval_seconds` on status rows.

Collections page: table gains Members (`member_count` or "—"), Last diff
(`+{last_added} −{last_removed}` when non-null, else "—"; title attr shows
`last_reconciled_at`), and a header area showing the collections_reconcile
job's last result + computed next refresh (`last_started_at +
interval_seconds`, "—" when interval unknown) from `/api/status`, plus a
**Diff now** button → `POST /api/scheduled-runs/collections_reconcile/run`,
then optimistic "requested — picks up within {poll_seconds}s" note and a
status re-poll. Keep the existing empty/loading/error states.

Dashboard: each scheduled-runs row gains a **Run now** button with the same
behavior (disabled while the request is in flight; error surfaces in the
row). Follow the existing retry/dismiss button idiom from Failures.tsx.

- [ ] Failing tests first (fetch stubs per existing page tests): stats
      render with the exact `+N −M` format; NULL stats render "—"; Diff now
      fires the right POST and shows the requested note; Run now per job
      row fires `/api/scheduled-runs/{name}/run`; error path renders.
- [ ] Red → implement → green; build clean; mutation proof: swap
      added/removed in the format — the exact-text test reds.
- [ ] Commit.

## Task 5: Frontend — Library search + ItemDetail compare & clear-override

**Files:**
- Modify: `frontend/src/pages/Library.tsx`, `frontend/src/pages/ItemDetail.tsx`,
  `frontend/src/pages/library.css` / `item.css` as needed
- Test: extend `frontend/src/pages/Library.test.tsx`,
  `frontend/src/pages/ItemDetail.test.tsx`

**Interfaces (consumes):** `search` param on `/api/items` (exists);
`provider` on item_detail renders (Task 2); clear-override endpoint (Task 3).

Library: a search `<input type="search">` in the existing `library-filters`
row; debounce 300ms before it lands in the query (`query.set("search", …)`
only when non-empty); changing it resets `offset` like `choose()` does.

ItemDetail: replace the single `artKindFor` pane pair with one base+live
pane pair PER art kind present in `item.renders` (ordered by art_kind;
falls back to `artKindFor(item.kind)` when renders is empty), each labelled
with the art kind; `ratioFor` keeps poster kinds upright. Renders table
gains a Provider column; rows with `provider === "manual"` get a **Clear
override** button → POST clear-override, on success re-fetch the item and
show the queued state; 409/503 detail surfaces inline.

- [ ] Failing tests first: typing in search issues a request containing
      `search=` after debounce (fake timers) and resets offset; a movie
      with poster+background renders renders two labelled pane pairs; the
      Clear override button appears only for provider "manual", fires the
      POST, and surfaces a 409 detail.
- [ ] Red → implement → green; build clean; mutation proof: drop the
      offset reset on search change — its test reds.
- [ ] Commit.

## Task 6: Whole-branch review, roadmap bookkeeping, PR

- Update the roadmap (`docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`):
  rows 22, 23, 24 get `answered 6a:` notes; the 6a phase entry marked
  delivered.
- Whole-branch review package from the merge-base; most capable model; one
  batched fix round if findings.
- Rebase onto current main if it moved (PRs #44/#45 may land mid-phase);
  re-run both suites after any rebase.
- PR `feat/phase6a-collections-ui` → `main` via `tea`. No AI attribution.
