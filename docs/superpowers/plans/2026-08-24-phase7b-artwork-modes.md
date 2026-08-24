# Phase 7b: Artwork Modes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The bulk artwork operations Posterizarr ships as CLI modes, as
operator-triggered jobs: backup, restore (filtered), poster reset,
remove-overlays revert, logo updater + logo revert. A "Run modes" UI section
fronts them. Closes roadmap rows 65, 66, 67, 71, 76, 77.

**Architecture:** Each mode is an on-demand job fanned to the worker pool via
a NEW `job.kind` (the worker's `process_item`-only gate opens to a dispatch
map). Backup reads Plex art (existing `fetch_artwork`) and writes a
Kometa-structured tree under a NEW `plex_backup_root`. Restore reads that
tree and pushes to Plex (existing `upload_artwork`), with type/library/item
filters, after **pausing the worker pool** so it can't race the live
pipeline. Revert pushes the un-badged `/assets` base back to Plex. Reset
unlocks and re-selects Plex's own agent art. Logo updater fetches clearlogos
to a NEW Plex-logo upload target; logo revert unlinks only fingerprinted
logos. Every Plex-writing mode is dry-run-by-default with a plausibility cap,
the `cleanup.apply` precedent.

**User decisions (2026-08-24, binding):**
- Backup target: a NEW `plex_backup_root` config key + mount (NOT the
  overloaded `backup_root`).
- Restore race: PAUSE the worker pool during restore (global flag; pool
  drains in-flight then idles until restore completes).
- Reset semantics: UNLOCK + RESTORE PLEX DEFAULT (unlock the field, then set
  Plex's agent-provided art). The UI states it cannot delete the orphaned
  upload. **Must never call `.refresh()`** (the AST guard forbids it
  project-wide — a refresh reverts locked fields; use `item.posters()` /
  `setPoster` and `reload()` only).
- Logo modes INCLUDED in 7b (new Plex-logo upload target; 67 depends on 71).

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds — including the
  no-`.refresh()` AST guard (`tests/test_plex_writer.py::test_no_refresh_calls_project_wide`)
  and the isolated-compose recipe.
- Container-only testing; zero skips; ruff clean; frontend build clean.
- Baselines at branch start (main b36d9dc): backend ~1563 under
  `-m "not imagemagick"`, frontend 173 (record actual — 6e is NOT on this
  branch).
- **Destructive-op posture (every Plex-writing mode):** dry-run default
  (`apply` bool read off the holder per run), a plausibility cap that
  refuses an implausibly large operation with the real numbers (the
  `cleanup` `max_orphans`/`max_orphan_share` precedent), and no operation
  runs against an empty `media_items`/`renders` (the cleanup empty-table
  guard precedent).
- **No `.refresh()` anywhere** — the AST guard will fail the build; reset in
  particular must select agent art without it.
- New job kinds: the worker gate (`queue/worker.py`, `job.kind !=
  "process_item"` → park) opens to a dispatch map; the `is_healthy` Plex
  gate still applies (all modes touch Plex). Trigger endpoints carry inline
  `Depends(require_session)`; the trigger-allowlist coupling (row 107,
  `SCHEDULED_JOB_NAMES`) — if these reuse that path, keep both lists in sync,
  or use a separate mode-trigger surface.
- Mutation proofs for every guard (the caps, the pause-fence, the
  fingerprint-only logo filter); copy+cmp restores; stage by name;
  `--no-gpg-sign`; no AI attribution; no new deps.
- **Fake-Plex tests only** — restore/reset/logo push to Plex via a FakeItem
  carrying `uploadPoster`/`uploadArt`/`lockPoster`/`unlockPoster`/`posters()`
  spies; backup writes to tmp dirs. The roadmap's "all against the fake Plex
  server."

## Verified facts (fact sheet 2026-08-24; spot-check before relying)

- Read primitive: `plex/artwork.py::fetch_artwork(http, plex_item, base_url,
  headers, art_kind, timeout) -> (bytes, content_type)` (:96); `_artwork_url`
  (:79) via `PLEX_ART_FIELDS` (:26-31). Write primitive:
  `upload_artwork(plex_item, data, art_kind, lock=True)` (:47-76) — routes
  background→uploadArt+lockArt else uploadPoster+lockPoster; temp file +
  unlink in finally.
- Naming = the Kometa tree: `naming.asset_path(config, library, root_folder,
  art_kind, season, episode)` (:67-82); nested vs flat on
  `config.library_folders`. Backup roots this layout at `plex_backup_root`.
- `/assets` holds the UN-badged base (`_publish` writes the styled base;
  badges never persisted — `pipeline.py:789-802`, `api/artwork.py:207-211`).
  Revert pushes that file. `_read_asset` (`api/artwork.py:76-98`) is the
  contained read.
- NO unlock/setPoster/refresh path exists anywhere (grep confirms) —
  reset/revert/logo push are net-new writes. EXIF provenance
  (`plex/exif.py::PROVENANCE_TAG` :61, `parse_provenance` :70) identifies
  OUR uploads, so reset/revert can act on only our art.
- Metadata writer (`plex/writer.py`) is metadata-only — NOT artwork; don't
  confuse the two.
- Bulk enqueue precedent: `full-pass` set-based `enqueue_batch`
  (`routes.py:744-819`). Trigger precedent: `run_scheduled_job_now`
  (`routes.py:695-741`), `SCHEDULED_JOB_NAMES` allowlist (:66-71).
- Safety precedents: `cleanup.apply=False` + `max_orphans=500` /
  `max_orphan_share=0.25` refuse-with-numbers (`schema.py:265-276`,
  `jobs.py:456-481`), empty-table guard (`jobs.py:513-518`);
  `badges.upload_to_plex`/`collections.apply_to_plex`/`adopt.apply` all
  dry-run-default.
- Config pattern: per-feature `*Config` BaseModel on `Config` via
  `Field(default_factory=...)` (`schema.py:428-437`); a single
  `ArtworkModesConfig` holds the mode flags/caps/filters + `plex_backup_root`.
  6c overrides layer makes new keys hot-reloadable/editable for free.
- Logos today: render-time only, composited into the poster, hashed as
  `logo_sha` in the poster fingerprint (`pipeline.py:556,585`;
  `find_logo_override` :174). No Plex-logo target, no logo render row — the
  updater creates the target.
- UI: Dashboard's "Run full pass" (`Dashboard.tsx:50,156-157`) + per-job
  run-now (:74) are the bulk-trigger precedent; a "Run modes" section is new.
  Artwork via `apiFetchImage`, never bare src.
- Tests: `test_plex_writer.py` FakeItem idiom; the no-`.refresh()` AST guard
  (:296-354) + the behavioural `refreshed is False` fake in
  `test_api_artwork.py`.

---

## Task 1: Config + the mode-job dispatch spine

**Files:**
- Modify: `src/autoposter/config/schema.py` (`ArtworkModesConfig` +
  `plex_backup_root`), `config/autoposter.example.yaml`,
  `src/autoposter/queue/worker.py` (dispatch map), `src/autoposter/app.py`
  (register handlers)
- Create: `src/autoposter/artwork_modes/__init__.py`,
  `src/autoposter/artwork_modes/base.py` (the shared job scaffolding: a
  `ModeJob` protocol, the dry-run/cap/empty-table guard helpers, the
  worker-pause primitive)
- Test: `tests/test_artwork_modes_base.py`, `tests/test_worker.py` (new-kind
  dispatch), `tests/test_example_config_matches_schema.py`

**Interfaces:**
- `ArtworkModesConfig`: `plex_backup_root: Path`, per-mode `*_apply: bool`
  (default False), a shared `max_changes` plausibility cap (default e.g.
  500) and `max_change_share` (0.25), plus restore/reset filter defaults.
  Placed on `Config` via default_factory; participates in the overrides
  layer automatically.
- The worker dispatch: `run_once` looks up `job.kind` in a handler map
  (`process_item` stays the default; each mode registers its handler); an
  unknown kind still parks with the existing message.
- `pause_workers()` / `resume_workers()` primitive (an `asyncio.Event` or a
  shared flag on `app.state`): `run_worker` checks it before claiming a job
  and idles (not busy-spins) while paused; restore sets it, `finally`s the
  resume. Pinned by a test: a paused pool claims nothing, resumes on clear.
- The plausibility-cap + empty-table guards factored so every mode calls
  them identically.

- [ ] Failing tests: config round-trips + example matches schema; a new
      job kind dispatches to its handler (not parked); an unknown kind still
      parks; the pause primitive stops claiming and resumes; the cap helper
      refuses-with-numbers past the threshold. Mutation proof: drop the
      pause check in `run_worker` → the paused-claims-nothing test reds.
- [ ] Full backend suite; ruff. Commit.

## Task 2: Backup + Restore (rows 76 → 77) — PR-pair A

**Files:**
- Create: `src/autoposter/artwork_modes/backup.py`,
  `src/autoposter/artwork_modes/restore.py`
- Modify: `src/autoposter/api/routes.py` (trigger endpoints)
- Test: `tests/test_artwork_backup.py`, `tests/test_artwork_restore.py`,
  `tests/test_api_artwork_modes.py`

**Interfaces:**
- Backup: traverses `media_items` (the full-pass precedent), for each item ×
  its art kinds `fetch_artwork` the current Plex bytes and writes them at
  `naming.asset_path` rooted at `plex_backup_root` (atomic write; skip an
  item Plex serves nothing for, counted). Read-only w.r.t. Plex and the DB;
  reports `{items, written, skipped, failed}`. Fan-out as per-item jobs OR a
  single walk — implementer's call, argue it (per-item gives retry/resume;
  a single job is simpler and backup is idempotent).
- Restore: `POST /api/artwork-modes/restore` body `{type?, library?,
  item_id?, apply?}` — filters map onto the `/api/items` filter idiom;
  **pauses the pool**, reads the backup tree, `upload_artwork`s each present
  file to its Plex item, resumes in `finally`. Dry-run (default) reports what
  it WOULD push without pushing; apply pushes. Plausibility cap applies.
  Never `.refresh()`.
- Trigger endpoints require_session; the response names dry-run vs applied
  and the counts.

- [ ] Failing tests (FakeItem/FakeSection with upload spies, tmp
      `plex_backup_root`): backup writes the tree at the right paths, skips
      no-art items, is Plex-read-only; restore dry-run pushes nothing,
      apply pushes exactly the filtered set, pauses the pool during (assert
      the pool claimed nothing mid-restore), respects the cap. Mutation
      proofs: (1) drop the pause in restore → the pool-idle-during test
      reds; (2) drop a filter → the wrong-set-pushed test reds.
- [ ] Full backend suite; ruff. Commit. **This pair is PR-able on its own.**

## Task 3: Remove-overlays revert + Poster reset (rows 65, 66) — PR-pair B

**Files:**
- Create: `src/autoposter/artwork_modes/revert.py`,
  `src/autoposter/artwork_modes/reset.py`
- Modify: `api/routes.py`, possibly `plex/artwork.py` (an `unlock`/`setPoster`
  helper — net-new, next to `upload_artwork`)
- Test: `tests/test_artwork_revert.py`, `tests/test_artwork_reset.py`,
  extend `tests/test_api_artwork_modes.py`

**Interfaces:**
- Revert (65): for filtered items, read the un-badged base from `/assets`
  (`_read_asset`) and `upload_artwork` it to Plex — removing the badge
  overlay by replacing the badged upload with the clean base. Dry-run
  default + cap. Only acts on items with a base on disk (skip + count
  otherwise). This is the smaller, decision-free mode.
- Reset (66): UNLOCK + RESTORE PLEX DEFAULT. A net-new `plex/artwork.py`
  helper unlocks the poster/art field (`unlockPoster`/`unlockArt`) and
  selects Plex's own agent art (`item.posters()` → the agent-default entry →
  `setPoster`), **never `.refresh()`**. Reset only our art (identify via EXIF
  provenance so a hand-set poster is left alone — read `parse_provenance`).
  The response/UI states the orphaned upload is not deleted. Dry-run default
  + cap.
- [ ] Failing tests: revert pushes the clean base for filtered items, skips
      base-less ones; reset unlocks + setPoster's the agent default, acts
      only on EXIF-provenanced-ours items, NEVER calls refresh (the AST
      guard covers the source; add a behavioural fake asserting
      `refreshed is False`). Mutation proofs: (1) reset acting on a
      non-ours item → the leave-hand-set-alone test reds; (2) revert's
      base-exists guard dropped → the base-less-skip test reds.
- [ ] The no-`.refresh()` AST guard passes (it will fail the build if reset
      reaches for refresh). Full suite; ruff. Commit. **PR-able pair.**

## Task 4: Logo updater + Logo revert (rows 71 → 67) — PR-pair C

**Files:**
- Create: `src/autoposter/artwork_modes/logo.py`
- Modify: `api/routes.py`, `plex/artwork.py` (logo upload target),
  `config/schema.py` (logo-mode config if needed)
- Test: `tests/test_artwork_logo.py`, extend `tests/test_api_artwork_modes.py`

**Interfaces:**
- Logo updater (71): scan filtered items for a missing Plex clearlogo, fetch
  the best clearlogo via the existing provider ladder
  (`select_artwork(..., art.LOGO, ...)` — the pipeline's logo path), and
  upload it to Plex's LOGO metadata target (net-new — plexapi's clearlogo
  upload; read the plexapi docs for the field, and pin the contract in
  `test_plexapi_*` style since it's a new API surface). Records which items
  got a logo (a fingerprint marker so revert can find "only fingerprinted
  logos"). Dry-run default + cap. Distinct from the poster composite.
- Logo revert (67): unlink only the logos THIS updater set (the fingerprint
  marker from 71 — not hand-set logos), via the unlock/clear path. Depends
  on 71's target + marker existing.
- [ ] Failing tests: updater fetches + uploads a logo for a missing-logo
      item, skips items that have one, records the marker; revert unlinks
      only marked logos, leaves hand-set ones. Mutation proofs: (1) revert
      unlinking an unmarked logo → the leave-hand-set test reds; (2)
      updater's has-logo skip dropped → the double-upload test reds. plexapi
      contract pin for the logo field.
- [ ] Full suite; ruff. Commit. **PR-able pair (heaviest, most net-new).**

## Task 5: The "Run modes" UI

**Files:**
- Create: `frontend/src/pages/Modes.tsx` (+ css + test) OR a Dashboard
  section — implementer's call on which reads better; a dedicated page if
  the mode set + filters + dry-run/apply controls are too much for a card
- Modify: `App.tsx`, `Sidebar.tsx` (nav), `api/client.ts`/`types.ts`
- Test: the new page's test + Sidebar/App

Behavior: a section/page listing the modes; each with its filter inputs
(type/library/item where applicable — reuse the `/api/items/filters`
values), a **dry-run** button (shows the counts it WOULD change) and an
**Apply** button behind a confirmation (these are destructive), the result
counts, and honest copy per mode (reset can't delete the orphaned upload;
restore pauses the pipeline while it runs). Follows the Run-full-pass
button idiom; mobile-clean (the interlude conventions).

- [ ] Failing tests: dry-run fires the trigger with `apply:false` and shows
      counts; Apply requires confirmation then fires `apply:true`; per-mode
      copy present; filter inputs wired. Mutation proof: Apply firing
      without the confirmation gate → its test reds.
- [ ] Frontend suite + build. Commit.

## Task 6: Roadmap, review, PRs

- Rows 65, 66, 67, 71, 76, 77 `answered 7b:`; 7b entry delivered; the
  reset-can't-delete-uploads and pause-during-restore realities noted;
  follow-ups filed.
- Whole-branch review (most capable model; the destructive Plex-write paths
  + the pause-fence + the no-refresh reset are the load-bearing targets),
  fix round, rebase onto main.
- **Ship as the three PR pairs (A backup+restore, B revert+reset, C logo),
  not one** — the roadmap says medium/"two or three PRs," and each pair is
  independently reviewable and mergeable. Sequence them bottom-up if
  stacked, or cut each from main if independent (A and B share only Task 1's
  spine; keep Task 1 in the first PR and rebase the others on it, OR land
  Task 1 + all modes as one branch and split the PRs by commit range — pick
  the cleaner and note it). Every guard mutation-proven; no AI attribution.
