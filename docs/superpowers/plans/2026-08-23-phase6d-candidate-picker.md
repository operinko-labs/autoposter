# Phase 6d: Provider-Candidate Picker and Logo Browser — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For any item and art kind, browse every provider's candidates and
pick one; the choice persists as a manual override, re-renders with that
base, and survives a re-run without being overwritten. Logos included.
Closes roadmap row 73; feeds phase 11.

**Architecture:** One browse endpoint fans out the EXISTING provider clients
(`provider.fetch` returns full candidate lists already; the JSON cache fronts
it, answering the roadmap's rate-budget risk) and merges with the ladder's
own `rank_key` for default order. The SPA hot-links candidate thumbnails
directly (provider image URLs are public and unauthenticated; our own images
need bearer auth, these don't). The pick endpoint accepts `{provider, url}`
but treats the URL as a CLAIM, not an instruction: it re-fetches the
candidate list server-side (cache hit) and refuses any URL not in it — no
arbitrary-URL fetch ever happens, which closes the SSRF question without
building §6e's allowlist machinery early. A pick downloads, verifies,
transcodes to `.jpg`, and atomically writes the manual-override mirror path
(the pipeline only ever stats the `.jpg` name), then nulls fingerprints and
enqueues a reprocess exactly as clear-override does. Logos — which have no
art-kind downstream — get the minimal mechanism: a dedicated logo-override
file consulted before logo selection in the poster render, feeding the
existing `logo_sha` fingerprint input, so a picked logo invalidates the
POSTER row.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds throughout.
- Container-only testing from `D:\Sites\autoposter` (PowerShell); one pytest
  session at a time; zero skips; ruff clean; `npm run build` clean.
- Baselines at branch start: record them (branch is stacked on 6c/main —
  backend ~1461, frontend ~135; verify).
- Structural auth sweep covers every new endpoint (inline
  `Depends(require_session)`).
- Mutation proofs for every guard; copy+cmp restores; never
  `git checkout --`.
- The pick endpoint must NEVER fetch a caller-supplied URL that was not
  first found in a server-side provider response — this is the phase's
  security invariant; mutation-prove it.
- **Attribution is not optional** (design spec :234-259): the candidate
  browser is the first view showing TMDB/TVDB artwork as theirs — the
  panel must carry TMDB's verbatim notice + logo (less prominent than an
  adjacent app brand mark) and TheTVDB's linked attribution. Reuse the
  Settings constants/logo asset via a shared component; the
  `frontend/dist` attribution test must keep passing.
- Stage by name; `--no-gpg-sign`; no AI attribution; no new dependencies
  (Pillow is already a dependency — verify before using it for transcode;
  if absent, transcode via the existing ImageMagick path instead and say so).

## Verified facts (file:line checked 2026-08-23; authoritative)

- `ArtCandidate` (`providers/base.py:32-56`): provider, url, language,
  width, height, score, includes_text (+`is_textless`). `ArtRequest`
  (:13-29). Kinds incl. `LOGO` (:7).
- Every client: `async fetch(request) -> list[ArtCandidate]` — full lists.
  TMDB (`tmdb.py:94-120`; **`_params()` :77-80 narrows via
  `include_image_language` from config — browse needs a wider variant**;
  logo/still arrays :13-19). TVDB (`tvdb.py:167-192`; title_card → `[]`
  always :170-173; season-id round trip :155-165; only provider with
  `includesText` :51). Fanart (`fanart.py:88-107`; no title cards :13;
  logo keys :21-22).
- Ranking: `providers/ladder.py` — `rank_key` :40-44
  (language, text, −score, −pixels); `UNRANKED = 99` candidates are
  DISCARDED by the ladder (:50-53) but a browse must SHOW them (sorted
  last), or the picker hides exactly what the ladder refused.
- `app.state.providers` published at `app.py:71` (`[]` on no-lifespan
  apps :248) — handlers reach clients with no new wiring.
- Cache (`providers/cache.py` + `fetch.py:17-51`): raw JSON payloads keyed
  by method+url+params (credentials stripped); a wider TMDB param is a
  DIFFERENT cache key (one extra upstream call per item per TTL — fine).
- Manual override: `manual_override_path` (`pipeline.py:123-134`) stats
  ONLY the `.jpg` mirror name from `naming.asset_path`
  (`naming.py:49-82`; extension hardcoded `.jpg`; raises for `"logo"`).
  Pipeline prefers it unconditionally (:362-365). Fingerprint
  short-circuit sits ABOVE it (:426-436) → after writing the file, null
  `fingerprint` + `badge_fingerprint` (clear-override does this at
  `routes.py:653-656`) or the next pass reports "unchanged".
- Download helper `_download` (`pipeline.py:210-219`): streams + sha256, NO
  content-type/size checks, follows redirects. Atomic publish `_publish`
  (:522-549) is keyed to `assets_root` — reuse with the `model_copy` trick
  or write a small local staging+`os.replace` (the `.tmp`-beside-target
  idiom) for the manual root.
- Clear-override leaves `<name>.jpg.disabled` behind (`routes.py:641-646`,
  `os.replace`); a new pick must simply write the live name (the stale
  `.disabled` stays, harmless).
- `_enqueue_reprocess(session, item)` (`routes.py:543-563`) is the shared
  re-render trigger — docstring says it exists to be shared; reuse it.
- Rendered rows stamp `provider="manual"`, `source_url=str(override)` under
  an override (`pipeline.py:506-518`) — the picked provider/URL provenance
  is otherwise lost: the pick endpoint writes ONE events_log row
  (source="picker", outcome naming item, art kind, provider; NEVER the
  full URL — host is fine) as the provenance trace. No new columns.
- `item_detail` does NOT expose `source_url`/`textless` (`routes.py:399-402`;
  model has them, `db/models.py:73-74`) — the picker UI wants them to show
  the current base; add to the response + `ItemRender` type.
- Logos: first-class at the provider layer (all three serve them), absent
  downstream — no naming path, no ART_KINDS_FOR entry, no renders row, no
  config sub-model (`schema.py:126-133` has only the three logo scalars).
  Render-time only: fetched to tmpdir per poster render
  (`pipeline.py:395-418`, suffix from URL defaulting `.png` :414-416),
  hash rides `logo_sha` into the poster fingerprint
  (`gather_fingerprint_inputs` :144-189).
- Artwork-endpoint hardening idioms to copy where relevant
  (`api/artwork.py`): art_kind validated against `ART_KINDS_FOR[kind]`
  before anything (:239-242), content-type allowlist never derived from
  caller input (:40-51,:67), nosniff (:58).
- Frontend: ItemDetail structure (`ItemDetail.tsx` — `useArtwork` :45-101,
  `Provider` :225-252 with the Clear button, `Renders` :254-323, per-kind
  panes :442-476); NO modal/overlay precedent exists in the SPA — use an
  inline expanding panel (the existing idiom). `apiFetchImage` returns
  Blob|null-on-404 (`client.ts:108-129`); provider thumbnails need no auth
  (plain `<img src>` is fine for THEM and only them; our own endpoints
  stay on `apiFetchImage`). TMDB serves size variants by URL prefix
  (`image.tmdb.org/t/p/original/...` — a `w342` variant for grids); TVDB
  and Fanart URLs are served full-size as returned.
- Attribution shipped surface: `Settings.tsx:772-809` (brand-mark,
  TMDB logo asset :17, notice constants, TVDB link);
  `tests/test_attribution_present.py` pins the BUILT BUNDLE.
- Test idioms: provider clients via httpx.MockTransport handlers
  (`test_providers.py`); ladder pure-unit (`test_ladder.py`); override
  endpoint template `test_api_clear_override.py` (tmp_path manual root,
  `_plant`, monkeypatched os.replace for 503); ItemDetail via
  `stubFetch(RouteMap)` + object-URL stubbing (`ItemDetail.test.tsx`).

---

## Task 1: Browse endpoint — cross-provider candidate lists

**Files:**
- Create: `src/autoposter/api/candidates.py`
- Modify: `src/autoposter/providers/tmdb.py` (wider-language browse param),
  `src/autoposter/api/routes.py` (include router; expose
  `source_url`/`textless` on item_detail), `frontend/src/api/types.ts`
  (mirrors only)
- Test: `tests/test_api_candidates.py`, extend `tests/test_providers.py`,
  `tests/test_api_library.py` (item_detail additions)

**Interfaces:**
- `GET /api/items/{item_id}/candidates/{art_kind}` (require_session).
  `art_kind` ∈ `ART_KINDS_FOR[item.kind]` PLUS `"logo"` when the item kind
  is movie or show (the logo browser; 404 otherwise — same validation
  idiom as clear-override). Builds the `ArtRequest` the render path builds
  (read how `process_item` does it — tmdb/tvdb/imdb ids, season/episode
  numbers), fans out `provider.fetch` over `app.state.providers`
  concurrently (`asyncio.gather`, exceptions per-provider → that
  provider's `error` string in the response, never a 500), merges, sorts
  by the ladder's `rank_key` with UNRANKED last, returns
  `{candidates: [{provider, url, thumb_url, language, width, height,
  score, includes_text}], errors: {provider: msg}, current:
  {source_url, provider} | null}` — `thumb_url` is the TMDB `w342`
  variant for TMDB URLs, else the url itself; `current` from the render
  row so the UI can mark the in-use base.
- TMDB browse widening: `fetch` gains a keyword-only
  `all_languages: bool = False` that drops/widens
  `include_image_language` — default preserves render behavior
  byte-for-byte (its cache keys must not change; test this).
- item_detail renders rows gain `source_url` and `textless`.

- [ ] Failing tests: 401; unknown item 404; invalid kind 404 (probe idiom);
      logo kind allowed for movie/show only; fan-out merges three fake
      providers with ladder ordering and UNRANKED-last; one provider
      raising lands in `errors` while others' candidates return
      (mutation-prove: let the exception propagate → test reds); TMDB
      widened param present only under `all_languages=True` and absent by
      default (cache-key stability pinned); thumb_url variant for TMDB
      only; `current` populated from the render row.
- [ ] Red → implement → green; full backend suite; ruff. Commit.

## Task 2: Pick endpoint + logo override mechanism

**Files:**
- Create: none (extends `api/candidates.py`), plus
  `src/autoposter/render/logo_override.py` if pipeline.py would bloat —
  implementer's call
- Modify: `src/autoposter/api/candidates.py`, `src/autoposter/render/pipeline.py`
  (logo-override consult), `src/autoposter/render/naming.py` ONLY if the
  logo path helper genuinely belongs there
- Test: `tests/test_api_pick.py`, extend `tests/test_pipeline.py` (logo
  override consult + fingerprint coupling)

**Interfaces:**
- `POST /api/items/{item_id}/candidates/{art_kind}/pick` body
  `{provider: str, url: str}` (require_session):
  1. Same item/kind validation as browse (logo included).
  2. **The URL is a claim**: re-run the browse fan-out server-side (cache
     hit) and 422 unless `(provider, url)` matches a returned candidate
     EXACTLY. No other URL is ever fetched — the phase's security
     invariant, mutation-proven (drop the membership check → the
     arbitrary-URL test reds).
  3. Download via the `_download` idiom to a tmpdir, with what it lacks:
     response content-type must be in the artwork allowlist
     (image/jpeg|png|webp), size > 0; failure → 502 with the provider
     named, nothing written.
  4. Non-logo kinds: transcode/re-encode to JPEG when not already JPEG
     (Pillow, RGB convert, quality 95 — verify Pillow is a declared
     dependency first; the repo's dependency test will tell you), then
     atomic write (`.tmp` + `os.replace`) to the manual-override mirror
     path (`naming.asset_path` under `model_copy(update=
     {"assets_root": manual_assets_root})`), creating parents. Then null
     `fingerprint`+`badge_fingerprint` on the `(item, art_kind)` row if
     present and `_enqueue_reprocess` — the clear-override ordering,
     file-success-first.
  5. Logo kind: write the ORIGINAL bytes (no transcode — logos need alpha;
     keep the URL's suffix defaulting `.png`) to the logo-override path:
     the poster's mirror directory with file name `logo.<ext>` under
     library_folders, `<root_folder>_logo.<ext>` flat — a small dedicated
     helper NEXT TO `manual_override_path`, not inside `naming._file_name`
     (which stays poster-kinds-only). Then null the POSTER row's
     fingerprints and enqueue — a logo is a poster input.
  6. Response `{"status": "picked", "queued": bool}`; one events_log row
     (source="picker", event_type="candidate_picked", outcome
     "<library>/<title> <art_kind> from <provider>", payload WITHOUT the
     URL).
- Pipeline: in the logo block (`pipeline.py:395-418`), consult the
  logo-override helper FIRST — if the file exists, stage it and use its
  sha as `logo_sha` instead of calling `select_artwork` for LOGO. The
  poster fingerprint then changes exactly when the logo file's bytes
  change. `use_logo: false` still suppresses everything (config wins).

- [ ] Failing tests: 401/404s; the arbitrary-URL 422 (server never issues
      the request — assert via the mock transport's call log, and
      mutation-prove); happy pick writes the mirror `.jpg` (PNG source
      transcoded — check magic bytes), nulls fingerprints, enqueues
      (dedupe respected), events row without URL; download failure → 502,
      no file, fingerprints intact; logo pick writes `logo.png`, nulls
      the POSTER row, enqueues; pipeline consult: with the override file
      present the LOGO provider ladder is never called (fake providers
      assert no fetch) and `logo_sha` equals the file's sha
      (fingerprint changes when the file changes — extend the pipeline
      test); `use_logo=false` ignores the file.
- [ ] Red → implement → green; full backend suite; ruff. Commit.

## Task 3: The picker UI

**Files:**
- Modify: `frontend/src/pages/ItemDetail.tsx`, `frontend/src/pages/item.css`,
  `frontend/src/api/types.ts`; Create: `frontend/src/ProviderAttribution.tsx`
  (extracted shared block; Settings imports it too — Settings' own tests
  and the dist attribution test must keep passing)
- Test: extend `frontend/src/pages/ItemDetail.test.tsx`,
  `frontend/src/pages/Settings.test.tsx` (only if the extraction moves
  markup)

**Interfaces (consumes Tasks 1–2):** browse + pick endpoints; the enriched
renders rows (`source_url`, `textless`).

Behavior: each per-kind pane section gains a **Browse candidates** button
(plus a **Browse logos** button on the poster section for movies/shows);
it expands an inline panel (no modal — the SPA has no such precedent):
a thumbnail grid hot-linking `thumb_url` via plain `<img src>` (loading
lazy; our OWN endpoints stay on `apiFetchImage` — never mix), each tile
showing provider, language, dimensions, textless badge; the currently-used
candidate (matching `current.source_url`) highlighted; per-provider errors
listed non-fatally; a Pick button per tile → POST, disabled in flight, on
success show queued state and re-fetch the item (provider flips to
"manual" and the Clear-override button appears — the existing machinery).
The panel carries the extracted `ProviderAttribution` block (TMDB notice +
logo beside the app brand mark, TVDB link). Renders table shows
`source_url` host + textless where present (compact).

- [ ] Failing tests: browse expands and renders tiles from a stubbed
      response (provider/language/dims/textless shown); current-candidate
      highlight; provider error listed while tiles render; pick fires the
      POST with exactly the tile's `{provider, url}` and re-fetches; the
      attribution block is INSIDE the panel markup (and Settings still
      carries its own). Mutation proofs: (1) send `thumb_url` instead of
      `url` in the pick body → the pick-payload test reds; (2) drop the
      attribution from the panel → its test reds.
- [ ] Frontend suite ≥ baseline+new, 0 skips; build clean (dist
      attribution test still green in the container suite). Commit.

## Task 4: Roadmap, final review, PR

- Roadmap: row 73 `answered 6d:`; 6d phase entry **delivered** (note the
  hot-link-thumbnails + URL-as-claim decisions and the logo-override
  mechanism); follow-up rows for anything revealed.
- Whole-branch review (most capable model) with accumulated minors; one
  batched fix round; rebase onto main (#51 will have merged); both suites
  post-rebase; PR via `tea`. No AI attribution.
