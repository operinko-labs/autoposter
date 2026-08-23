# Phase 6e: Manual Mode and Testing Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one styled artifact of any kind from an operator-supplied
source (a URL, or a path on the manualassets mount) — including collection
posters — and render config sample sheets (short/medium/long text, every
artifact kind) on demand. Closes roadmap rows 74 and 75; the last phase of
the 6-series.

**Architecture:** Manual mode for items IS the 6d pick flow with an
operator-supplied source: the source becomes the manual override (verified,
transcoded, atomically installed by the machinery 6d built), fingerprints
null, a re-render enqueues, and the normal pipeline styles and badges it.
What 6e adds on that path is the thing 6d deliberately deferred: a real
SSRF guard, because the URL is now arbitrary. Collection posters ride the
existing `local_poster_path` override (write the file; the next reconcile —
or Diff now — applies it). Testing mode extracts the pipeline's compositing
block into a reusable styled-bytes seam and drives it synchronously against
a generated sample canvas, returning the image (or the truncation outcome)
inline — no storage, no new serving surface, no queue.

**Honest scope notes (from the fact sheet, stated up front):**
- "Collection cards" get the poster **applied as-is** — collection-title
  COMPOSITING is roadmap row 105 (parity-only, unbuilt, no oracle, no
  Colus font) and is NOT smuggled in here.
- "Local file" means a path under `manual_assets_root` — Posterizarr's
  `-PicturePath` was a path on the box, and no upload endpoint exists
  anywhere in the design or the SPA. A browser-upload flow is a follow-up
  row, not this phase.
- Testing-mode samples render like Posterizarr's: on a generated solid
  sample canvas (theirs is pink), because no title-card/season base
  fixture exists and shipping one real poster as a sample base would be
  arbitrary.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds throughout — including
  the isolated-compose-project recipe if the shared DB is contended.
- Container-only testing from `D:\Sites\autoposter`; zero skips (the main
  checkout has frontend/dist); ruff clean; `npm run build` clean.
- Baselines at branch start (main b36d9dc): record them yourself (expect
  backend ~1563+dist-tests under `-m "not imagemagick"`, frontend ~173+).
- **Byte-parity is load-bearing**: Task 1's refactor must not change one
  byte of rendered output. The imagemagick-marked golden tests RUN in the
  dev container (magick is in the image; they are deselected by `-m`, not
  impossible) — run them explicitly before AND after the refactor:
  `docker compose run --rm test pytest -m imagemagick -v`. Byte-identical
  or the refactor is wrong.
- **The SSRF guard is the phase's security invariant** (roadmap §6e names
  it): scheme allowlist, private/loopback/link-local/reserved address
  rejection AFTER resolution, no automatic redirects (bounded manual
  redirect handling with re-validation per hop), the 6d size cap and
  decode verification on the body. Mutation-prove the address rejection
  with a literal metadata-service URL the way `test_api_pick.py` does.
- Mount-path sources: containment via the artwork endpoints' double-
  realpath idiom against `manual_assets_root` — a path outside it is 422,
  never read.
- Every new endpoint carries inline `Depends(require_session)` (openapi
  sweep covers).
- Mutation proofs for every guard; copy+cmp restores; stage by name;
  `--no-gpg-sign`; no AI attribution; no new dependencies.

## Verified facts (fact sheet 2026-08-23/24; spot-check anchors before relying)

- The compositing block to extract: `render/pipeline.py:504-564` (stamp →
  base → optional logo → text blocks → publish), all already
  `to_thread`-wrapped; argv builders in `render/compositor.py` (signatures
  at :14,:19-28,:94-102,:121-123, `run` :146), `textfit.fit_point_size`
  :92 (returns `FitResult(point_size, truncated)`; truncated ⇒ the
  pipeline writes NO file, :543-554 — testing mode must render that as an
  OUTCOME). Canvases: `_CANVAS` map `pipeline.py:46-51`
  (`POSTER_SIZE="2000x3000"`, `BACKGROUND_SIZE="3840x2160"`).
- `tests/test_golden.py:88-136` drives the argv builders directly — the
  refactor's oracle.
- Text inputs: `title_text_for(art_kind, item, config)`
  (`pipeline.py:101-120`) needs only a synthetic `ResolvedItem` (title +
  season/episode numbers) — no DB, no Plex.
- 6d machinery, all URL-source-agnostic (`api/candidates.py`):
  `_download_artwork` (:267, cap+content-type+empty guards),
  `_prepare_jpeg` (:288), `_verify_image` (:321), `_install` (:351),
  `manual_override_target` (`pipeline.py:123-135`), logo override helpers
  (:153-183), `_clear_stale_logo_overrides` (:337), `_enqueue_reprocess`
  (`routes.py:556-576`). The pick endpoint's offered-set validation does
  NOT transfer — that is what the SSRF guard replaces.
- NO ssrf machinery exists (`ipaddress` unused repo-wide); httpx
  per-request `follow_redirects` defaults False; the shared client
  (`app.py:63`) must not be reconfigured — pass per-request.
- Collections: `local_poster_path(config, library, title)`
  (`collections/posters.py:68`, `<assets_root>/<library>/<title>/poster.{jpg,jpeg,png,webp}`),
  `apply_poster` (:161-171) prefers it, `_is_image`-validates (:204-213),
  uploads on reconcile. ManagedCollection rows carry library/title
  (`db/models.py:250+`).
- Serving: no transient-image endpoint exists; artwork endpoints serve
  assets_root/Plex only. Testing mode returns bytes INLINE from its POST.
- No sync-render precedent in handlers; a sample = one magick pipeline on
  one canvas via `to_thread` — bounded, acceptable; do NOT add a page-wide
  parallel fan-out client-side (render sequentially or small batches).
- Frontend seams: ItemDetail's `.browse-controls` (:749-767 region) is
  where the manual-source control joins the 6d buttons; Sidebar nav is a
  hardcoded array (`shell/Sidebar.tsx:22-29`, icons :9-20) — a Testing
  page is a 7th entry; `apiFetch` sets JSON content-type when body
  present; blob-from-POST needs a small client helper (apiFetchImage is
  GET-only — extend with an optional init rather than forking).
- Test idioms: `test_api_pick.py` (request-log SSRF assertions,
  `manual_root` tmp fixture, PIL byte factories), `test_api_candidates.py`,
  golden/imagemagick conftest mechanics (`tests/conftest.py:44-99`).

---

## Task 1: Extract the styled-bytes seam (byte-parity-proven refactor)

**Files:**
- Modify: `src/autoposter/render/pipeline.py`
- Test: `tests/test_pipeline.py` (only if assertions reference moved
  internals), golden tests untouched

**Interfaces:**
- Produces: `compose_styled(config, art_kind, base_path, working_dir, *,
  title_text, secondary_text, logo_path=None) -> ComposeResult` (exact
  signature is the implementer's on reading the block — it must carry
  everything :504-564 uses and NOTHING more; `ComposeResult` names the
  output path and `truncated: bool`). `render_artifact` calls it and keeps
  its own publish/DB behavior byte-identically.

- [ ] Run `pytest -m imagemagick -v` BEFORE (record the pass list),
      refactor, run again — identical results, plus the full suite.
- [ ] Commit.

## Task 2: The SSRF guard + manual-source endpoints

**Files:**
- Create: `src/autoposter/net/guard.py` (or `providers/…` — implementer's
  call with reasoning), extend `api/candidates.py` or a new
  `api/manual.py`
- Modify: `src/autoposter/collections/posters.py` only if a helper needs
  exporting
- Test: `tests/test_fetch_guard.py`, `tests/test_api_manual.py`

**Interfaces:**
- `guarded_download(http, url, destination, *, max_bytes, content_types)
  -> None`: scheme ∈ {http, https}; resolve the host and reject
  private/loopback/link-local/reserved/multicast targets (every resolved
  address must pass, not just the first); redirects OFF at httpx level,
  followed manually up to 3 hops with full re-validation per hop; then the
  6d body guards (cap, content-type, non-empty, decode-verify by caller).
  A rejection is a typed exception carrying the REASON, never the URL, in
  its message.
- `POST /api/items/{item_id}/renders/{art_kind}/manual` body
  `{source: str}` (require_session): source starting with `http(s)://` →
  guarded download; otherwise treated as a path RELATIVE to
  `manual_assets_root` → double-realpath containment, must exist and be a
  file. Then exactly the pick tail: verify → transcode (non-logo) or
  verbatim (logo) → `_install` to the override target → null fingerprints
  → `_enqueue_reprocess` → `{"status": "installed", "queued": bool}`.
  Same art_kind validation and logo coupling as 6d (logo → poster row).
- `POST /api/collections/{collection_id}/poster` body `{source: str}`:
  same source handling; writes `local_poster_path`'s `poster.jpg`
  (transcoded) for that ManagedCollection's library/title (creating the
  directory); response `{"status": "installed", "applies": "next
  reconcile"}` — no upload to Plex here; Diff now covers immediacy.
- [ ] Guard tests red-first incl. the metadata-URL mutation proof
      (request-log style: the forged request must actually fire when the
      guard is dropped); redirect-hop re-validation (a public URL
      redirecting to 169.254.169.254 is refused at hop 2); DNS-resolved
      private rejection (fake resolver); mount-path escape 422
      (`..`, absolute-outside, symlink via realpath); both endpoints'
      happy paths, failure table (nothing written on refusal), logo
      coupling, collection path creation.
- [ ] Full backend suite; ruff. Commit.

## Task 3: Testing mode — sample renders

**Files:**
- Create: `src/autoposter/api/testing.py`
- Test: `tests/test_api_testing.py`

**Interfaces:**
- `POST /api/testing/sample` body `{art_kind, length}` (require_session);
  `length` ∈ {short, medium, long} with fixed sample titles (e.g. "Up",
  a median-length title, and a deliberately over-long one; title cards get
  season/episode numbers so the secondary line renders). Flow: generate
  the sample canvas at the kind's `_CANVAS` size via magick (solid
  Posterizarr-pink, one `run(...)` argv — no fixture file), build a
  synthetic `ResolvedItem`, `title_text_for` + `compose_styled` under
  `to_thread`, then EITHER the styled JPEG bytes inline
  (`image/jpeg`, `Cache-Control: no-store`, nosniff) OR — when
  `ComposeResult.truncated` — a 200 JSON body
  `{"truncated": true, "art_kind":…, "length":…}` (the pipeline's rule is
  truncated ⇒ no artifact; testing mode's job is to SHOW that, not to
  pretend). Config comes from the live holder, so edits + hot-reload are
  visible in the next sample — the roadmap's acceptance ("sample sheets
  change when config changes").
- These tests are imagemagick-marked where they invoke real compositing
  (they run in the dev container; CI's dedicated step runs them too —
  confirm the marker mechanics from conftest) with a thin unmarked layer
  for auth/validation/JSON paths.
- [ ] Red-first; a config-change test (different text setting via
      `model_copy` → different bytes/point size); the truncation outcome
      test (long text + tiny max box); full suite + explicit
      `-m imagemagick` run; ruff. Commit.

## Task 4: UI — manual source controls + the Testing page

**Files:**
- Modify: `frontend/src/pages/ItemDetail.tsx`, `Collections.tsx`,
  `frontend/src/shell/Sidebar.tsx`, `App.tsx`, `api/client.ts`
  (blob-POST helper), `api/types.ts`, CSS per page conventions
- Create: `frontend/src/pages/Testing.tsx` (+ css + test)
- Test: extend ItemDetail/Collections/Sidebar tests, new `Testing.test.tsx`

Behavior:
- ItemDetail: a "Use file or URL" control beside Browse candidates (and
  the logo variant beside Browse logos): one text input + Install button →
  the manual endpoint; helper copy states the two source forms ("https://…
  or a path under /manualassets"); errors inline (guard reasons arrive as
  detail — no URLs in them); success = the existing queued state +
  item re-read (provider flips to manual; Clear override appears).
- Collections: a per-row "Set poster…" action → the collection endpoint;
  success copy names Diff now for immediacy.
- Testing page (`/testing`, new nav icon): a kind × length grid; each cell
  a Render button → blob-POST → `<img>` from an object URL (own-endpoint
  bytes = authed fetch, NEVER a bare src); truncated outcomes render as a
  labelled state, not an error; a note that samples reflect the running
  config (edit in Settings, re-render here).
- [ ] Red-first per page; mutation proofs: (1) the manual control sending
      the wrong field name → payload test reds; (2) truncated-state
      rendering coerced to error → its test reds. Suite + build. Commit.

## Task 5: Roadmap, review, PR

- Rows 74, 75 `answered 6e:`; 6e entry **delivered** (with the honest
  scope notes); follow-up rows: browser-upload source for manual mode;
  drift-sweep + arr-sync intents' available-but-unpassed rating keys
  (from the #57 review); collection-title compositing stays row 105
  unchanged. Row 119's flake batch: note it is now seven instances and
  worth scheduling.
- Whole-branch review (most capable model; the SSRF guard and the
  byte-parity refactor are the load-bearing targets), one batched fix
  round, rebase onto main if moved, both suites, PR via `tea`.
