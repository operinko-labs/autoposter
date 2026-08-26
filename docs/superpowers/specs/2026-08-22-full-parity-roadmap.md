# Full feature parity — gap list and roadmap

**Date:** 2026-08-22
**Status:** Planning document, for review
**Sources:** `.superpowers/sdd/parity-posterizarr-inventory.md` (67 in-scope-or-decidable
gap rows out of ~110 features), `.superpowers/sdd/parity-kometa-inventory.md` (134 out of
~190), `docs/superpowers/specs/2026-08-20-autoposter-design.md` §6/§6a, and the shipped
codebase under `src/autoposter/`.

## Scope decisions (locked)

These bound every row and phase below. They were decided by the user, not inferred.

- **Capability parity, not config compatibility.** autoposter's own config and UI must
  be able to achieve every in-scope outcome. Kometa/Posterizarr YAML is never parsed and
  no importer is planned. Kometa's template/DSL machinery is superseded by design; the
  capabilities its defaults files deliver (overlay families, collection packs) are in
  scope as capabilities.
- **External services: only what the user uses today** — TMDB, IMDb datasets, MDBList,
  TVDB, Fanart.tv, plus the self-hosted stack (Plex, Radarr, Sonarr, and — new —
  Tracearr). TMDB-backed builders (discover, charts, lists) and MDBList lists are in.
  Trakt, Letterboxd, AniDB/AniList/MAL, and anything needing a new account or OAuth are
  out — Appendix A prices what re-scoping each would cost.
- **Playlists: in. Notifications: in.**
- **Cutover-critical, sequenced first:** the user's Posterizarr config POSTs run
  notifications to an n8n webhook (`kometa-trigger`) that chains downstream automation.
  Without an equivalent, that chain dies silently the day the old tools are switched
  off. This is the strongest sequencing constraint in the roadmap: a parity feature that
  gates turning Posterizarr/Kometa off at all.
- **Tautulli is retired by user decision.** The user is deploying
  [Tracearr](https://github.com/connorgallopo/Tracearr) instead in the coming days, so
  the design doc's never-built Tautulli intake and Kometa's Tautulli builders are
  removed from the gap list rather than carried — Tracearr's outbound custom webhooks
  and read-only REST API (both verified from its repo and tracearr.com; nothing beyond
  that is verified) take over both roles.
- **Assumed done:** the full-pass trigger (`POST /api/sweep` + dashboard button),
  in flight on `feat/full-pass-trigger`.

Two spec-level non-goals stay non-goals throughout: Jellyfin/Emby support and Kometa's
`add_missing` family. Where this roadmap includes something the 2026-08-20 spec listed
as a non-goal (clearart, Discord notifications), the full-parity decision supersedes
the spec and the row says so.

## Phase summary

Ordered by: cutover-criticality first, then dependency order, then difficulty ramp.
Sizes are relative to shipped phases (4a: a handful of endpoints; 4c: ~13 commits of
endpoints + two UI pages; phases 2 and 3: the largest shipped units, multi-week each).

| Phase | Title | Size | Notes |
|---|---|---|---|
| 5a | Outbound notifications and webhooks | small (≈ half of 4a) | **Blocks cutover** |
| 5b | Cutover verification sweep | small | **Blocks cutover confidence** |
| 5c | Tracearr webhook intake | small | Harvest-first; needs the live instance |
| — | **Cutover: retire Posterizarr and Kometa** | — | After 5a + 5b + full-pass trigger |
| 6a | Collections UI, search, compare, override actions — **delivered** (rows 22–24; collection stats, diff-now, per-job run-now) | ≈ 4c | Spec §6 debts |
| 6b | WebSocket (dashboard + live log tail) — **delivered** (as NDJSON streams; row 72) | small | Spec §6 debt |
| 6c | Config editor with hot-reload and impact preview — **delivered** (rows 94, 54; DB-overrides layer, generation-swap reload, side-effect-free preview) | large (≈ phase 2) | Spec §6 debt |
| 6d | Provider-candidate picker and logo browser — **delivered** (row 73; URL-as-claim pick validation, logo-override mechanism) | ≈ 4c | Spec §6 debt; feeds 11 |
| 6e | Manual mode and testing mode — **delivered** (rows 74, 75; SSRF guard, compose_styled seam) | ≈ 4c | |
| 7a | Render and selection long tail | small–medium | Many S items, no dependencies |
| 7b | Artwork modes: backup, restore, reset, revert, logo updater — **delivered** (rows 65–67, 71, 76–77; six modes inline-in-endpoint with dry-run default + plausibility caps, worker-pause fence for restore, per-field reset, logo upload-key marker, Run-modes UI) | medium | |
| 8a | Builder engine core | large | **delivered** — the golden byte-identical port and the opt-in guarded delete sweep are the phase's spine. Gate for 8b–8c, 10, 13 |
| 8b | List builders, wave 1 — **delivered with a note** (rows 57–64; eight of the nine builder families, `plex_pilots` deferred to row 143 on a premise failure) | medium | Row 56 closes only partially |
| 8c | TMDb discover, IMDb search/awards, Tracearr builders — **delivered with a note** (rows 80–83; row 79 deferred to row 17) | medium | |
| 9a | Filter model, tier 1 | medium | Gate for 9b and dynamic packs |
| 9b | Plex search DSL and sort matrix | **XL — multi-week even parallelised** | |
| 9c | Native smart collections | medium | |
| 10a | Dynamic collections engine | large | |
| 10b | Dynamic packs and defaults equivalents | medium–large | |
| 10c | People: person builders and dynamic person types | medium | |
| 11a | Asset-quality flags and backfill | medium | Action Center, part 1 |
| 11b | Action Center review queue and bulk actions | large | Action Center, part 2 |
| 12a | Overlay families | large | |
| 12b | Custom overlay mechanics | large | |
| 13 | Playlists | medium–large | Reuses 8a builders |
| 14a | Metadata operations completion | medium | |
| 14b | Per-item metadata overrides and per-library config | large | |
| 15 | Operations and observability polish | medium | |

---

## Part 1 — Gap list, easiest first

Every in-scope missing/partial/unknown row from both inventories appears below or in
Appendix A; the completeness pass and counts are in the footer. Rows marked
**(verify)** carry an inventory "unknown" forward as a named verification task instead
of resolving it by assumption. Config impact: **exercised** = the user's real config
exercises it today; **parity-only** = present in the tools but disabled/unused;
**spec §6** = owed by the autoposter design spec itself. Rows carrying
**answered 5b:** were verified in phase 5b; the file:line evidence behind each
one-liner is in the `.superpowers/sdd/p5b-task-{1,2,3}-report.md` and
`p5b-close-report.md` files.

| # | Gap | What it is | Difficulty | Config impact | Depends on |
|---|---|---|---|---|---|
| 1 | Smart vs dumb CS collections (verify) | Confirm whether `buckets.py` creates Plex-native smart collections or reconciled dumb ones. **answered 5b:** Plex-native smart — created with `smart=True` and live `contentRating` filters (`reconcile.py:364-374`); no membership is ever maintained. **4c/defaults-catalog note, recorded because a picker made it visible:** the five US certification buckets have **no master boolean** — there is no `collections.cs_buckets` field or anything like it, and `sources.default_definitions` builds the family unconditionally (`config/schema.py`, `collections/sources.py`). The only switch near them is `collections.separators`, which owns the blank `"Ratings Collections"` divider and nothing else, so that is what the catalog's Content Ratings row is honestly backed by and what its name says (`Preset(key="content_ratings_divider", setting="collections.separators")`). Deliberate, not an oversight: inventing a `cs_buckets` setting to give the picker a tidier row would have been exactly the fabricated path the catalog's provenance checks exist to catch. Making the family toggleable is new config surface and a decision nobody has asked for yet (`.superpowers/sdd/task-3-report.md` §2) | S — read code + one Plex call | exercised | — |
| 2 | `collection_order: custom` applied (verify) | Confirm `reconcile.py` actually applies builder ordering to Plex. **answered 5b:** yes — created with `sortUpdate("custom")` (`lists.py:145-148`) and source order enforced every pass by `_enforce_order`/`moveItem` (`lists.py:30-59,170`); Oscar year collections deliberately use `release` (`sources.py:82-84`) | S — read code | exercised (charts) | — |
| 3 | Metadata lock semantics (verify) | Confirm whether `plex/writer.py` locks fields the way Kometa's lock/unlock sources do. **answered 5b:** yes — every written field carries `{field}.locked=1` in the same edit (`writer.py:101-104`), genres via `genres.locked=1` and `addGenre/removeGenre(locked=True)` (`writer.py:87,147,156`); unchanged fields are never touched, matching Kometa's lock-on-edit default | S — read code | exercised | — |
| 4 | Collection poster render path (verify) | Styled text-composite (Posterizarr `CollectionTitlePosterPart`) vs hosted-image-only; is `AddCollectionTitle` honoured? **answered 5b:** hosted-image-as-is — `apply_poster` uploads the local-override or hosted-default bytes unchanged (`collections/posters.py:161-238`) and no `AddCollectionTitle` equivalent exists (`config/schema.py:209-254` carries only `collections.posters: bool`). And that *is* live parity: 0 of 325 collection posters on the production Plex carry a Posterizarr provenance stamp, the managed families are sha256-identical to the exact hosted defaults autoposter fetches (IMDb Top 250, Oscars Winners 2026, Age 5+/6+ Movies all verified byte-for-byte), and the unmanaged franchise collections carry heterogeneous community art no single-font pipeline produced — so the `CollectionTitlePosterPart` block is an inert Posterizarr default here, never exercised, and the earlier **exercised** marking was wrong. No fix; the unused compositing capability is tracked as row 105 | S — read code + compare output | parity-only (5b refuted "exercised": the fonts are configured only because Posterizarr writes every config block) | — |
| 5 | `collection_mode` / `sort_title` on creation (verify) | Confirm created collections get display mode and sort-title prefixes the defaults set. **answered 5b:** no on creation — `collection_mode` is set nowhere in the codebase, and a sort title only on the separator (`reconcile.py:254`); adopted Kometa collections keep theirs, but a newly created one (e.g. each year's new Oscars collection) gets neither → follow-up row 104 | S — inspect created collections | exercised (indirect) | — |
| 6 | Download-only mode (verify) | Posterizarr `ImageProcessing: false` — fetch/move art with no compositing; does any autoposter mode match? **answered 5b:** no config switch exists — every provider fetch is resized/re-encoded through the compositor; the only no-compositing path is the per-*item* `source_mode: "verbatim"` (`pipeline.py:438-443`). Per-kind toggles can drop overlay/border/text (`schema.py:94-103`) but the canvas resize still runs. Not exercised: the user runs `ImageProcessing: "true"` (posterizarr-config.txt:167). A switch, if ever wanted, stays with 7a | S — likely a per-kind switch | parity-only | — |
| 7 | Show-vs-movie overlay override (verify) | `showoverlayfile` — a different fade for show posters than movie posters. **answered 5b:** no override — one `ArtKindConfig` per art kind, shared by movies and shows (`schema.py:126-134`; `art_config_for`, `pipeline.py:97-98`). Matches the user's config: `showoverlayfile: ""` (posterizarr-config.txt:99), so shows already share the movies' `overlay.png`. A split stays with 7a | S — schema question | parity-only | — |
| 8 | `AddEPTitleText`/`AddEPText` toggles (verify) | Title-card text lines independently toggleable, or rendered unconditionally? **answered 5b:** two independent blocks with their own `add_text` (`schema.py:117-123`, `pipeline.py:100-119,471-475`), except disabling the title line also suppresses the episode line; the user runs both on (posterizarr config `AddEPTitleText`/`AddEPText` both true), so the asymmetry is not exercised | S — read `render/` | exercised (at defaults) | — |
| 9 | `UseBGLogo` / `TextlessPosterBypass` (verify) | Logo-on-backgrounds and with-text-fallback-despite-textless behaviours. **answered 5b:** neither exists — clearlogo compositing is poster-only (`pipeline.py:398` gates on `art_kind == "poster"`), so no `UseBGLogo`; and while the ladder parks a with-text candidate as the fallback when textless is preferred (`ladder.py:87-96`), the compose path never branches on the base's textlessness — `is_textless` is recorded on the row and nothing more (`pipeline.py:389,508`) — so no `TextlessPosterBypass`. Both `"false"` in the user's config (posterizarr-config.txt:154,164). Stays with 7a (overlaps row 41's with-text branch) | S — read code | parity-only | — |
| 10 | TVDB subscriber PIN (verify) | `apikey#pin` key format support in the TVDB client. **answered 5b:** not supported — the login POSTs `{"apikey": ...}` verbatim with no `pin` field and no `#` splitting (`tvdb.py:108`), so a subscriber `apikey#pin` key would fail login. Not exercised: the user's key is a plain UUID with no `#` (posterizarr-config.txt:3). Stays with 7a | S — read client | parity-only | — |
| 11 | Season/title-card fallback chain (verify) | `ShowFallback`, `UseBackgroundAsTitleCard`, `BackgroundFallback` — what does autoposter do when no season/episode art exists? **answered 5b:** no cross-kind fallback — when no provider offers art for the requested kind the row is marked `no_art` and the render stops (`pipeline.py:380-384`); `select_artwork` walks providers for that kind only (`ladder.py:64-97`), and e.g. TVDB answers `[]` for title cards outright (`tvdb.py:170-173`). Matches the user's config: all three fallbacks are `"false"` (posterizarr-config.txt:207,234,235). A chain stays with 7a | S — read ladder code | parity-only | — |
| 12 | `FollowSymlink` semantics (verify) | Whether security-driven symlink resolution conflicts with opt-in symlinked asset trees. **answered 5b:** no conflict — path derivation is purely lexical (`naming.py:17-36` compares string segments, never resolves), so symlinked library or asset mounts render fine; the two places that do resolve do it to *both* sides symmetrically (artwork serving, `api/artwork.py:89-92`; cleanup scan, `scheduler/jobs.py:344-367`), so a symlinked `assets_root` stays self-consistent. The one deliberate refusal: the serving endpoint rejects an in-tree symlink whose target escapes the resolved root (`api/artwork.py:79-92`) — a security posture, not a render-path behaviour. Not exercised: `FollowSymlink: "false"` (posterizarr-config.txt:128) | S — read `naming.py`/adoption | parity-only | — |
| 13 | `SkipTBA` regex + delete-on-match (verify) | Whether skip words are regex-capable and whether a matching existing card is deleted. **answered 5b:** literal case-insensitive whole-title match, not regex (`pipeline.py:137-141`); an existing card is left in place — the skip returns before any compose/upload and nothing deletes it (`pipeline.py:320-323`); user config: `SkipTBA=true` (covered), `SkipJapTitle=false` (not exercised) | S — read code | exercised | — |
| 14 | Multiple movie/show versions (verify) | Posterizarr covers all versions (theatrical/director's cut); confirm autoposter's per-item model does. **answered 5b:** covered — one row per Plex item is Plex's own versions-share-one-poster granularity; live probe: 50/1,954 movies + 224 episodes have multiple Media (LOTR/Ben-Hur are true theatrical-vs-extended pairs), 0 edition splits. Divergence is cosmetic: badges read media[0], which Plex does not quality-sort, vs Kometa's weight-picked best (values.py:121). Detail: `.superpowers/sdd/p5b-task-3-report.md` → follow-up row 106 | S–M — may reveal a real gap | exercised (implicit) | — |
| 15 | `name_mapping` / illegal characters (verify) | Confirm `render/naming.py` handles characters Plex titles allow but filesystems don't. **answered 5b:** titles never enter paths — asset paths come from the item's on-disk folder name (`naming.py:17-36,67-82`), and resolution raises rather than falling back to the title (`plex/client.py:203-214`); guarded by two tests in `tests/test_naming.py`; the user's Kometa config sets no `name_mapping` | S — read + test | exercised (implicit) | — |
| 16 | Render throughput without a text-size cache (verify) | Posterizarr caches ImageMagick text measurements; confirm fingerprint gating keeps a full re-render tolerable without one. **answered 5b (partial):** enqueue measured — 15,000 items in ~2s (1.83–2.35s set-based insert, PR #34, pinned by a 10s-bound test). Unchanged-item cost on a forced pass: an adopted row short-circuits on local hashes alone — overlay/font/asset SHA-256s plus a fingerprint compare, zero provider calls, zero ImageMagick (`pipeline.py:332-352`); an already-rendered row re-runs provider selection (24h `provider_cache`) and re-downloads the base to re-hash it before the same compare (`pipeline.py:367-389,426-436`) — still no ImageMagick, because text measurement (`textfit.py:92-110`, one magick subprocess per text block) runs only past the fingerprint gate (`pipeline.py:480`), and badge text is Pillow in-process behind its own gate (`badges/compose.py:129-142`, `pipeline.py:658-662`) — skipping the measurement entirely is stronger than caching it. Full changed-library throughput is a cutover-day observation — record it during the first production full pass | S — measure a forced sweep | exercised (implicit) | full-pass trigger |
| 17 | Tracearr payload/API harvest (verify) | Capture real webhook payloads and REST responses from the user's live instance before writing any schema — payload shapes are **unverified** until then. **answered (REST half — spec pass):** harvested against the live instance (`media/tracearr`, `ghcr.io/connorgallopo/tracearr:2.1.0`, ClusterIP :3000) and banked at `docs/research/tracearr-api-harvest.md` + raw JSON under `docs/research/tracearr/` — the full endpoint inventory (23 public paths across **two** versions: v1 `/api/v1/public/*` offset-paged, v2 `/api/v2/public/*` cursor-paged and read-only), both complete OpenAPI 3.0 documents (obtained by calling the server's own `generateOpenAPIDocument`/`generateOpenAPIDocumentV2` in-container — the `/docs` routes are themselves auth-gated), pagination shapes (`PaginationMeta` vs `CursorMeta`), live-captured error envelopes (**two** distinct shapes: `{statusCode,error,message}` from handlers, bare `{"error":"Not Found"}` from the not-found fallback), and rate-limit tiers (v1 1000/min, v2 **240/min shared across the whole key**, operator-configurable). Headline finding for row 79: **the public API exposes no top-N media ranking in either version** — verified, not assumed (no `sort`/`order`/`top`/`limit` param on any of the 23 paths); `GET /api/v1/stats/top-content` returns exactly the top-10 by play count but is on the **internal** session-JWT API an API key cannot reach, so most-watched must be aggregated client-side from `GET /api/v2/public/history` (records are already resume-chain-deduped and ≥2-min-filtered, and carry `media_id`/`show_media_id`/`imdb_id`/`tmdb_id`/`tvdb_id`/`genres`), then joined to Plex via `GET /api/v2/public/media/{ref}` → `availability[].rating_key`. **answered (REST half, FULLY — spec *and* live payloads):** the user provisioned an owner key as `AUTOPOSTER_TRACEARR_APIKEY` in `autoposter-secret`, and a second pass on 2026-08-25 captured **33 real responses** from inside the autoposter pod (key referenced only by env var; never in a command line, log, report or fixture) — every public v2 path, a representative v1 set, and every error shape — banked scrubbed under `docs/research/tracearr/payloads/` with a LIVE-CAPTURE section in `docs/research/tracearr-api-harvest.md`. Scrub verified in-container: 133 mapped real values, **0 occurrences** in the banked output, including inside decoded base64 cursors. **Seven observed-vs-spec deltas, all reproducible from the banked files, three of which invalidate first-pass guidance for row 79:** (1) `progress_ms`/`total_duration_ms` arrive as **JSON strings** though the spec says integer (`duration_ms` is a real int); (2) `genres` is declared required-non-nullable but is **null on every episode record** (37/37 in the sample; populated only on movies) — so genre-scoped most-watched for *shows* costs one `/media/{ref}` call each, contradicting the first pass; (3) on episode records `imdb_id`/`tmdb_id`/`tvdb_id` are the **episode's** ids, not the show's — proven by a live 404 on a `show:tvdb:<episode tvdb_id>` lookup, so the first pass's "short-circuit on external ids" is movie-only and shows must go through `show_media_id`; (4) some plays carry **no media identity at all** (2/50, both the same show — a 10% undercount for that title if dropped silently); (5) `poster_url` points at the session-JWT **internal** image proxy, unusable by an API-key client (`thumb_path` is the usable Plex path); (6) the v2 cursor is base64 of `{"t","id"}` and **embeds a session uuid**; (7) undocumented fields are present (`availability[].replaces`, four v1 `transcodeInfo` keys) while documented ones are absent (`transcode_info.hwRequested`) — parse leniently. Also corrected: **v1 `/history` is not a camelCase mirror of v2** — it returns raw sessions (no resume-chain grouping, no ≥2-min filter, a 15-second `live` row in the first five) and carries **no media identity fields whatsoever**, so it cannot be joined to Plex and is useless for row 79. Confirmed live: both error envelopes, the SPA-200 trap, and a cheap reliable tell — **`x-ratelimit-*` headers are present iff a route matched**, absent on both the bare-envelope 404 and the SPA 200; the limiter also runs **before** auth, so a 401 still spends budget; the v2 240/min budget is demonstrably **one shared counter across all v2 routes** (monotonic decrement across six different paths); the whole harvest cost ~26 of 240. The live `/docs` body was diffed against the banked spec in-container: **exactly 8 differences**, all four `serverId` enum/description injections, and **no `servers[]` key in either** (correcting the first pass) — so `openapi-v2.json` is a faithful offline stand-in. The `/history` aggregation recipe is now grounded in a worked example on 50 real records (`show_media_id or media_id` → 21 buckets → top bucket resolved through `/media/{uuid}` to Plex `rating_key` "63856"). Residual REST gaps, all deliberate: 429 (would exhaust the user's shared budget), 403 (needs a second non-owner key), the one public write (`streams/{id}/terminate`), and multi-server/`merged_ids`/non-Plex/`track`-`photo`-`trailer` distributions (single-server Plex-only instance). **One gap remains.** *The WEBHOOK half is untouched* and is **not** a defect of this row: capturing real Tracearr outbound webhook payloads needs autoposter's 5c intake endpoint to exist and be reachable **plus** the user pointing Tracearr's outbound custom-webhook config at it — both are writes to live systems, outside this row's read-only remit. Tracks with row 21 | S — harvest, in the project's oracle style | new integration | live Tracearr deploy |
| 18 | **Outbound job/run webhook (n8n)** | POST a completion notification to a configured URL when a run/sweep finishes — the `kometa-trigger` chain's lifeline. The exercised Posterizarr path (`AppriseUrl`) is in fact a plain webhook POST to n8n | S — one POST with retry | **exercised — cutover-critical** | — |
| 19 | Notification event taxonomy | run_start / run_end / error / changes / delete payloads; per-collection changes webhooks (Kometa `changes_webhooks`) | S–M — the contract design is the work | parity-only | 18 |
| 20 | Discord / Apprise formatting | Discord-shaped payloads and Apprise-endpoint support as additional notification targets | S | parity-only | 18, 19 |
| 21 | Tracearr webhook intake | Accept Tracearr's outbound custom webhooks as a trigger source (the role Tautulli's agent held); route into the existing intake/normalize path | S–M — new route + payload mapping | replaces a retired trigger | 17 |
| 22 | Search box | answered 6a: debounced search input on the Library page, wired to the existing escaped-ilike `search` param, offset reset on change | S — one input + wire-up | spec §6 | — |
| 23 | Multi-art-kind compare | answered 6a: item detail renders one labelled base+live pane pair per art kind in `renders` (a movie's poster and background side by side) | S — UI only, endpoints exist | spec §6 | — |
| 24 | Clear-manual-override action | answered 6a: `POST /api/items/{id}/renders/{art_kind}/clear-override` renames the override file to `.disabled`, clears fingerprints, enqueues a re-render; button on `provider: manual` rows | S | spec §6 | — |
| 25 | `sync_mode: append` | Builders currently only sync (diff-to-source); append mode keeps manual additions. **answered 8a:** delivered — `CollectionDefinition.sync_mode: sync` or `append`; under `append`, `member_diff`/`_enforce_order` skip removal and reordering entirely, so manual additions and their positions survive (`collections/lists.py:87,124,308`) | S — reconciler flag | parity-only | — |
| 26 | `limit` per collection | Cap builder results. **answered 8a:** delivered — `CollectionDefinition.limit` caps AFTER resolution, not the candidate id list, so a short source can't be masked into a full-looking collection (`collections/engine.py:355-359`) | S | parity-only | — |
| 27 | Collection dry-run preview | Show what a diff would do without applying (Kometa `test`). **answered 8a:** delivered as `POST /api/collections/preview` — runs the real engine with `dry_run` forced on regardless of `collections.apply_to_plex`, no Plex or DB writes, per-definition +/- counts and action strings, fronted by a Definitions panel on the Collections page (`api/collections_builders.py`) | S — reuse diff, skip apply | parity-only | — |
| 28 | Collection lifecycle ops | `blank_collection`, `delete_collection(s)_named`, `delete_collections` (managed/configured), `mass_collection_mode`. **answered 8a:** delivered — `POST /api/collections/ops/{blank,delete,mass-mode}`; delete requires `confirm: true` plus the same ownership-label + `managed_collections`-row + protected-label guards the automatic sweep uses (`api/collections_builders.py`); the automatic `delete_unconfigured` sweep is a separate, off-by-default path capped by `max_deletes` (`collections/engine.py::_sweep`) | S — plexapi calls | parity-only | — |
| 29 | Collection labels | `label` / `label.remove` / `label.sync` on the collection object. **answered 8a:** delivered — `CollectionDefinition.labels` plus `label_sync` (off by default); a protected label and an `adopt_from` label are never stripped even when sync is on (`collections/reconcile.py::_apply_labels`) | S | parity-only | — |
| 30 | Collection summaries from source | `tmdb_summary`/`tmdb_description`/`tvdb_summary` pulls instead of static text only. **answered 8a:** delivered for TMDB — `CollectionDefinition.tmdb_summary` is the TMDB *collection* id (an int, not a bool toggle — the Kometa-parity argument), pulled through the facts client and always losing to an explicit `summary:` (`collections/engine.py::_summary_for`); no TVDb equivalent | S — TMDB/TVDB clients exist | parity-only | — |
| 31 | `sync_to_mdb_list` | Push collection membership out to a MDBList list | S — MDBList client exists | parity-only | — |
| 32 | `mass_user_rating_update` | Overwrite user rating library-wide | S — same shape as shipped ops | parity-only | — |
| 33 | `mass_original_title_update` / `mass_added_at_update` | Two more mass-op fields | S | parity-only | — |
| 34 | `genre_mapper` / `content_rating_mapper` | Normalise values ("Sci-Fi & Fantasy" → "Sci-Fi") before writing | S — a mapping table in config | parity-only | — |
| 35 | Item exemptions | `ignore_ids`/`ignore_imdb_ids`/`ignore_labels` + a `skip_autoposter`-style per-item opt-out label (Posterizarr `skip_posterizarr`) | S — one check in the pipeline | parity-only | — |
| 36 | Plex maintenance toggles | `clean_bundles` / `empty_trash` / `optimize` scheduled ops (user has them off) | S | parity-only | — |
| 37 | `assets_for_all_collections` | Local poster override for collections autoposter doesn't manage | S — extend the existing override lookup | parity-only | — |
| 38 | `LibraryLanguageOverrides` | Per-library language-ladder override (TC keeps `xx` lead) | S — config plumbing into the ladder | parity-only | — |
| 39 | `SkipLocal*TextAdd` per kind | Don't add text to locally supplied assets, as per-art-kind config (today only per-item `source_mode: verbatim`) | S | parity-only | — |
| 40 | `SkipJapTitle` | Skip title cards whose episode title is Japanese/Chinese | S — Unicode-range check | parity-only | — |
| 41 | `SkipAddText` family | Skip text/overlay/border when the chosen provider art is known with-text — the "provider says with-text" branch isn't modelled | S–M — touches selection metadata | parity-only | — |
| 42 | Newline rules | `NewLineOnSpecificSymbols`/`NewLineWords` — forced line breaks and a manual hyphenation map | S — text layout only | parity-only | — |
| 43 | Season name override | `OverrideSeasonName` + specials wording | S | parity-only | — |
| 44 | `UseOriginalTitle` | Original-language title instead of localized | S — field already fetched | parity-only | — |
| 45 | `UseClearart` | Prefer clearart over clearlogo (spec listed it a non-goal; full parity supersedes) | S — Fanart client already sees it | parity-only | — |
| 46 | Logo recolour | `ConvertLogoColor`/`LogoFlatColor` — flatten a logo to one colour | S — one image op | parity-only | — |
| 47 | Local-only switch | `DisableOnlineAssetFetch` globally and per kind — render from local assets, zero provider calls | S — short-circuit the ladder | parity-only | — |
| 48 | Season/episode template assets | One manual image applied to all seasons/episodes of a show (`SeasonTemplate`/`EpisodeTemplate`) | S — manual-assets lookup rule | parity-only | — |
| 49 | Separator collections | Blank "index card" collections for visual grouping | S — cosmetic | parity-only | — |
| 50 | `blur(##)` / `backdrop` overlays | Whole-poster blur and solid-colour layer primitives | S — two Pillow ops | parity-only | — |
| 51 | API-key auth for read endpoints | Key-based access (`X-API-Key`) beyond the webhook secret, for widgets/scripts | S | parity-only | — |
| 52 | Storage stats + homepage endpoints | `/api/assets/stats`-style per-library counts/sizes for gethomepage.dev widgets | S — SQL + a walk | parity-only | 51 |
| 53 | Per-run stats rollup + charts | Per-run posters/seasons/BG/TC/errors counts persisted; duration/success charts in the UI | S–M — events exist, rollup doesn't | parity-only | — |
| 54 | Schedule editing UI | answered 6c: cadences are editable via the config editor and take effect LIVE without a restart — `Job.interval_seconds` resolves through the ConfigHolder per poll, so a saved `scheduler.*_hours/_days` override applies at the next scheduler tick (`poll_seconds` itself stays restart-flagged) | S–M | parity-only | 6c helps |
| 55 | Overlay/font file management UI | List/upload overlay PNGs and fonts from the UI | S–M | parity-only | — |
| 56 | Trivial Plex builders | `plex_id`/`plex_rating_key`, `plex_pilots`, generic `plex_all` at any level. **answered 8b (partial):** `plex_id` (`collections/builders/base.py`), `plex_rating_key` (`collections/builders/simple_ids.py`, the same builder under the name Kometa spells it) and `plex_all` (`collections/builders/plex_trivial.py`) delivered; `plex_pilots` **not** — premise failure, not effort: the owned index and `BuilderResult` name library *items*, so there is no way to hand an episode back as a member. See the new episode-aware-resolution row | S — once the engine exists | parity-only | 8a |
| 57 | `text_file` builder | IDs from a local plain-text file. **answered 8b:** delivered — one operator-maintained file per definition, read from the manualassets mount, with the path realpath-contained to that mount on both sides (a symlink *inside* it pointing out is refused on its target, which a lexical check would wave through) and absolute paths refused a layer earlier at params validation. `imdb:`/`tmdb:`/`tvdb:` prefixes, a bare `tt…` read as IMDb, `#` comments anywhere on a line. An unparseable line, a non-UTF-8 file, and a file that yields no ids at all each raise naming the file (and the line number) — a silent skip is one title missing from a collection forever with nothing anywhere to say so | S | parity-only | 8a |
| 58 | `mdblist_list` builder | Any MDBList list with sort/limit — client already exists for ratings. **answered 8b:** delivered — both of MDBList's addressing forms (`user/list-slug` and the numeric id; a pasted URL refused at config load), `sort` open and `order` closed to asc/desc, and per-library-type id preference (Movie `tmdb_id`→`imdb_id`; Show `tvdb_id`→`tmdb_id`→`imdb_id`, the IMDb tail appended because show entries carrying nothing else still resolve against Plex's `imdb://` guids). The other media type is dropped (TMDb's two id spaces share one namespace) and a `mediatype` outside movie/show **raises** rather than being guessed. The 200-with-`{"error": "API Limit Reached!"}` budget response is memoised as the exception on the library's `run_cache`, so every later MDBList definition short-circuits without spending a call that would fail anyway | S | parity-only | 8a |
| 59 | IMDb list builders | `imdb_list` / `imdb_id` / `imdb_watchlist` (public), on the IMDb GraphQL surface `charts.py` already uses. answered 8b: delivered — imdb_list + imdb_watchlist on the same GraphQL endpoint/header/raw-POST transport as charts.py (imdb_id shipped 8a); queries live-verified 2026-08-25 and fixture-pinned, drift raises naming the found shape, errors-on-200 refused. Public watchlists only, permanently — Query.user takes no id, so predefinedList(classType: WATCH_LIST, userId:) is the only anonymous route and a private form would need an IMDb login. | S–M | parity-only | 8a |
| 60 | TVDb list builders | `tvdb_list` / `tvdb_movie` / `tvdb_show` — client exists for artwork. **answered 8b:** delivered on the existing artwork client's `_authenticated_get` (login dance and one-shot 401 re-login inherited, nothing restated). `tvdb_list` is addressed by **id XOR slug** — both or neither is a config-load error, because a definition where the two disagreed would silently build whichever the code checked first — and the slug is restricted to unreserved characters so it needs no escaping and a pasted URL is refused. A v4 list entity carries `seriesId` or `movieId`, never both, so a TVDb list is inherently mixed: it is split per entry and the other kind dropped, since TVDb's series and movie ids are different id spaces in one namespace and passing one through can resolve to the *wrong title* rather than to nothing | S–M | parity-only | 8a |
| 61 | Arr builders | `radarr_all` / `radarr_taglist` / `sonarr_all` / `sonarr_taglist` — `arr/client.py` exists. **answered 8b:** delivered, all four — the `_all` forms are the service's whole inventory in the service's own order; the taglist forms take `tags:` plus `match: any|all` (`any` by default, the reading where adding a tag can only grow the collection). Tag names are matched case-insensitively and an unknown one raises naming the vocabulary that does exist, because a tag that matches nothing is indistinguishable from a correct empty answer. The tag lookup is memoised per service per pass on `ctx.run_cache`, failure included, so a dozen taglist definitions cost one `/api/v3/tag` round trip and a dead service is not re-asked once per definition | S | parity-only | 8a |
| 62 | TMDb explicit/simple builders | `tmdb_movie`/`show`/`list`/`collection`/`company`/`network`/`keyword`. **answered 8b:** delivered, all seven, via the new `providers/tmdb_lists.TmdbListClient` — `tmdb_movie`/`tmdb_show` do no network at all and are library-type-guarded; `tmdb_list` is paged and filtered to this library's media type (a v3 list holds both, and the two id spaces share one namespace); `tmdb_collection` is Movie-only, since a franchise's `parts` are films; and company/network/keyword go through `/discover` rather than the deprecated per-entity listing endpoints — which is also the *only* form TMDb has for a network. A 404 raises naming what was asked for, never an empty membership | S–M — thin API calls | parity-only | 8a |
| 63 | TMDb chart builders | popular / top_rated / trending daily+weekly / now_playing / upcoming / airing_today / on_the_air, region-aware. **answered 8b:** delivered — all eight keys through the same client, one `chart:` param resolving to the `/movie/*` or `/tv/*` endpoint by library type so a single definition serves both, and a key with no form for this library type refused before any request. `region` is honoured only where TMDb really applies it: shape-checked at config load *and* refused at build on the endpoints that accept the parameter over HTTP and silently drop it (every `/tv/*` and `/trending/*` form), leaving the four `/movie/*` list endpoints as the ones that pass. `language` threaded the same way; both omitted entirely when unset, so an unconfigured chart keeps its existing cache key | S–M | parity-only | 8a |
| 64 | `plex_watchlist` builder | Items from the Plex account watchlist. Needs a plex.tv account token — an existing account, not a new one, but a new auth surface; **flag for user confirmation**. **answered 8b:** delivered — the account surface is reached with its own soft secret, `AUTOPOSTER_PLEX_ACCOUNT_TOKEN`, deliberately not the configured `plex_token`, which may be server-scoped (fine for everything else this service does, rejected by plex.tv). Unset is graceful: `SourceClients.plex_account` is None, only `plex_watchlist` definitions report themselves failed, and the rest of the pass is untouched. Minted with the existing PIN CLI (`python -m autoposter.plex.auth`); see `deploy/README.md` | S–M | parity-only | 8a |
| 65 | Remove-overlays revert | One-shot "restore clean bases to Plex" — the bases exist in `/assets`; no operation pushes them back. answered 7b: delivered as the revert mode — `POST /api/artwork-modes/revert` pushes the un-badged `/assets` base back to Plex for filtered items, driven by the `renders` table and gated on `base_sha256 IS NOT NULL` so a Kometa-era file sitting at a never-rendered row's path is never pushed as ours; containment re-proven per file; base-less items skip+count; dry-run default + plausibility cap | S–M — bulk upload path | delivered 7b | — |
| 66 | Poster reset mode | Reset a library to Plex default metadata art (`-PosterReset`). answered 7b: delivered as reset — unlock + restore the Plex agent default for BOTH poster and background fields (user decision), selection skips any listing entry it cannot prove is agent art, gated on OUR EXIF provenance so hand-set art is untouched, never `.refresh()`; counts are per (item, field); the orphaned `upload://` image cannot be deleted by this mode and the response + UI say so | S–M — per-item revert data exists via EXIF | delivered 7b | — |
| 67 | Logo revert mode | Unlink only fingerprinted logos from Plex. answered 7b: delivered — unlinks only logos the updater itself set AND that Plex still shows selected (the `media_items.logo_upload_key` marker cross-checked against the live selection), via `unlockLogo`+`deleteLogo`; hand-set and operator-replaced logos are never touched, both guards mutation-proven | S–M | delivered 7b | 71 |
| 68 | Hub visibility + priority | `visible_library/home/shared` pinning (Plex Pass) and `hub_priority`/`auto_sort_hubs`. **answered 8a:** delivered for `visible_*`/`hub_priority` — via plexapi's `ManagedHub` (`collections/reconcile.py::_apply_hub`/`_move_hub`); config load refuses `hub_priority` set without at least one `visible_*` flag, since moving an unpromoted hub raises `BadRequest` (`config/schema.py::_hub_priority_needs_a_promotion`); no `auto_sort_hubs` | S–M — plexapi hub calls | parity-only | — |
| 69 | Member-item edits | `item_label` and per-member metadata application from a collection definition. **answered 8a:** delivered for `item_label` only — `CollectionDefinition.item_label` labels every resolved member, add-only (a member a source stops naming keeps its label rather than losing it); no other per-member metadata application | S–M | parity-only | — |
| 70 | Per-collection cadence and date windows | Per-collection refresh schedule + range gating — the real capability inside Kometa's schedule DSL that seasonal collections need. **answered 8a:** delivered — `CollectionDefinition.schedule: {every_n_runs, months}` (`ScheduleGate`, `config/schema.py`), gated inside the existing `collections_reconcile` cadence rather than a second scheduler; a gated-off definition is skipped, its collection left untouched (`collections/engine.py::_due`) | S–M — APScheduler already in place | parity-only | — |
| 71 | Logo updater mode | Scan Plex for missing clearlogos, fetch, upload as Plex metadata — a pipeline distinct from posters. answered 7b: delivered — provider-ladder clearlogo fetch (`select_artwork` LOGO path), uploaded + locked via plexapi `LogoMixin` (floor bumped to >=4.16, contract pinned offline); the revert marker is the `upload://` key Plex files our upload under, recorded in `media_items.logo_upload_key` and committed per successful upload so an interrupted run keeps a truthful partial marker set; movie/show only; the first real run on a large library expectedly refuses at the plausibility cap (UI frames it as the normal next step) | M — new upload target | delivered 7b | — |
| 72 | WebSocket | answered 6b: delivered as NDJSON streams, not WebSocket — the anticipated auth risk (bearer header, no browser WS headers) is exactly what the logs stream's fetch-based NDJSON already solved, and no WS infra existed to reuse. Log tail shipped with the logs page; dashboard now consumes `GET /api/dashboard/stream` fed by a per-process broadcaster that polls the DB only while subscribers exist (replica-correct; cheaper than per-tab polling) | M — server infra + client swap (client.ts is pre-shaped for it) | spec §6 | — |
| 73 | Provider-candidate picker (Asset Replacer) | answered 6d: browse endpoint fans out every provider's full candidate list (cache-fronted, ladder-ranked with UNRANKED shown last), picker panel on item detail with hot-linked thumbnails and provider attribution; a pick is validated against a server-side re-fetch (no arbitrary URL is ever fetched), downloaded, decode-verified, transcoded to the .jpg mirror path, fingerprints nulled and a re-render enqueued. Logo browser included via a dedicated logo-override file feeding logo_sha (poster fingerprint coupling) | M — designed in spec §6, not built | spec §6 | — |
| 74 | Manual mode | answered 6e: `POST …/renders/{art_kind}/manual` and `POST /api/collections/{id}/poster` take a URL (behind a real SSRF guard — scheme allowlist, per-hop redirect revalidation, private/loopback/reserved rejection on every resolved address) or a manualassets mount path (realpath-contained), install it via 6d's verify/transcode/override tail, re-render. UI on item detail + collections. Collection posters apply as-is (title compositing stays row 105); "local file" = the mount path (browser upload is a follow-up row) | M — reuses the render path | parity-only | 73 helps |
| 75 | Testing mode | answered 6e: `POST /api/testing/sample {art_kind,length}` renders a sample on a generated pink canvas via the extracted `compose_styled` seam, returns the JPEG inline or a truncation outcome; reads the live config holder, so an edit + hot-reload shows in the next sample. A Testing page fronts a kind×length grid | M — render path against fixtures | parity-only | — |
| 76 | Backup mode | Download all artwork from Plex into a Kometa-structured backup tree. answered 7b: delivered — walks `media_items`, `fetch_artwork` per art kind into the Kometa layout rooted at the NEW `plex_backup_root` (new mount; deploy needs the volume), Plex- and DB-read-only, per-(item, kind) counts with an all-fail run reading `backup failed` not success; a re-run unconditionally overwrites the tree (`os.replace`), which the UI gates behind its confirm | M — bulk download + naming | delivered 7b | — |
| 77 | Restore mode | Push backup-tree art back to Plex with type/library/item filters. answered 7b: delivered — `POST /api/artwork-modes/restore` with the `/api/items` filter idiom; PAUSES the worker pool during apply (in-flight jobs drain, pool idles, resume in `finally` — mutation-proven at the instant of write), dry-run default + cap, refusals carry real counts | M | delivered 7b | 76 |
| 78 | Show title on season posters | `AddShowTitletoSeason` — extra text/logo block above season text | M — new layout region | parity-only | — |
| 79 | Tracearr activity builders | Most-watched / popular collections from Tracearr's read-only REST API (the role of Kometa's Tautulli builders and its `tracearr` chart default). **deferred:** blocked by open row 17 (live Tracearr deploy + harvest, unverified until the instance exists) — phase 8c shipped without it. **row 17 update:** the REST surface is now harvested (`docs/research/tracearr-api-harvest.md`) and the design question is settled — **there is no most-watched/popular endpoint to call**; the only top-10-by-play-count endpoint is on Tracearr's internal session-JWT API, unreachable with an API key, so this row builds its own aggregation by paging `GET /api/v2/public/history` over the collection's `since` window, grouping on `show_media_id` (shows) / `media_id` (movies) — both canonical and merge-aware, so a title on two servers folds into one bucket without title normalization — counting records as plays and summing `duration_ms`, then resolving each id through `GET /api/v2/public/media/{ref}` → `availability[].rating_key` (or short-circuiting on the `imdb_id`/`tmdb_id`/`tvdb_id` already on every history record). Budget is the binding constraint: **240 req/min shared across the whole v2 key**, so prefer aggregate-from-history over a per-item `/media/{ref}/stats` fan-out. Do not mix window semantics — `/history` `since`/`until` are instants, `/media/{ref}/stats` `last_7`/`last_30` are UTC calendar-day buckets, and the numbers will not agree. Still needs a key (row 17 gap (a)) before any fixture is real. **answered row79/tracearr:** delivered — `tracearr_most_watched` (`src/autoposter/collections/builders/tracearr.py`) over a new client (`src/autoposter/providers/tracearr.py`) and a pure ranking module (`src/autoposter/collections/activity.py`), plus `TracearrConfig`, the soft secret `AUTOPOSTER_TRACEARR_APIKEY`, and two opt-in Charts presets (`chart_tracearr_movies`, `chart_tracearr_shows`). **Two things this row's own text got wrong, corrected by the harvest and by the shipped code.** (1) The external-id short-circuit is **movies only**. On an episode record `imdb_id`/`tmdb_id`/`tvdb_id` are the EPISODE's, and `GET /api/v2/public/media/show:tvdb:<episode id>` is a live-verified 404 (`docs/research/tracearr/payloads/v2-media-show-by-tvdb-ref.json`) — so a show reaches its ids only through `show_media_id` and one `/media/{uuid}` call per ranked title, made after truncating to `limit`. That call emits the SHOW-LEVEL external ids from the media document rather than this row's original `availability[].rating_key` recipe, because a rating key is exactly the thing that dies when an item is replaced or moved — this repository ships a pruner (row 129) precisely because they do — while a show's tvdb/tmdb/imdb id survives it; the rating key remains the last resort for a bucket that has no canonical id at all. An episode id emitted as a member would resolve to nothing on a Show library and look exactly like a correct empty collection, which is why `collections/activity.py` refuses to carry those ids out of the ranking at all and `tests/test_builder_tracearr.py::test_no_id_a_show_collection_emits_is_an_episode_id` is a named invariant. (2) "ids on every history record" is false: **2 of 50** banked records carry `media_id`, `show_media_id`, `library_id` and all three external ids null while still carrying `rating_key`/`grandparent_rating_key`, and both are episodes of one show whose honest count is therefore 20 rather than the 18 a naive grouping reports. Those plays are MERGED into the bucket already seen under their rating key — no extra call, no operator knob, nothing double-counted — and a bucket formed from such plays alone emits `("plex", rating_key)` as the documented last resort. Failure blast radius is split deliberately: a 404 on one ranked title's media document drops that title and logs a count, while an unreachable or non-API Tracearr fails the definition and leaves the collection untouched. Scope held: `metric` is `plays`/`watch_time` only, `/media/{ref}/stats` calendar-day windows are never consulted or reconciled anywhere, and per-user collections are out (the `user` block is the PII-bearing one). The recently-added family is filed as its own row rather than built here | M — new client + builder | parity-only | 8a, 17 |
| 80 | `tmdb_discover` | The full TMDb discover query surface (genres, dates, votes, keywords, watch providers, …). **answered 8c:** delivered — `tmdb_discover` (`src/autoposter/collections/builders/tmdb_discover.py`) built on a transcribed 48-row attribute matrix (21 shared/16 movie-only/11 tv-only = TMDb's documented 37-param `/discover/movie` and 32-param `/discover/tv` surfaces minus `page`), with the params model *generated* from the table (`pydantic.create_model`) so the matrix cannot drift from what actually validates; `region` is movie-only (TMDb silently ignores it on `/discover/tv`), while `language` is accepted here even though the narrow `tmdb_movie`/`tmdb_show` entity builders refuse it — discover can sort on localized title and order is this builder's output contract, a genuinely different question, so the entity builders stay narrow on purpose. A companion-rule guard refuses a watch-provider or certification filter missing its required partner at load; a definition with no genuine filtering attribute is refused (an unfiltered discover is `tmdb_chart`'s popularity list under another name), via a table-derived filtering-fields set so a matrix row added later is filtering by default without anyone remembering to list it; the page-cap WARNING (`providers/tmdb_lists.py::_paged`) fires for every paged caller, not discover alone. Detail: `.superpowers/sdd/task-1-report.md` | M–L — the biggest single TMDb surface | parity-only | 8a |
| 81 | `imdb_search` | IMDb advanced search (type, votes, rating, genre, keyword) via GraphQL. **answered 8c:** delivered — `imdb_search` (`src/autoposter/collections/builders/imdb_search.py`) on IMDb's `advancedTitleSearch`, walked live 2026-08-25 (71 probes, since introspection is refused there) against the 8b transport generalized for a second root; all four constraint objects existed and all six operator-facing filters (`type`, `genres`, `rating_gte`/`rating_lte`, `votes_gte`, `released_after`/`released_before`, `sort`) mapped onto them, matching the plan's minimal tier exactly — no BLOCKED. IMDb validates constraint *shape*, not *values*: a misspelled genre id returns HTTP 200 with `total: 0`, indistinguishable on the wire from a real empty result, so the vocabulary is pinned and validated in the builder rather than passed through — 28 genre ids and 11 title-type ids, each proven real by a non-zero total in a live probe, since a fake id returns exactly zero. `POPULARITY` sorts a *rank* where 1 is most popular, so most-popular-first is `sortBy: POPULARITY, sortOrder: ASC`; the plausible `DESC` reading returns the *least* popular matches with no error at all, and is guarded by its own mutation proof. The wider constraint surface (keyword/credit/country/language/certificate/runtime/award/list-membership) is Kometa-complete territory the live walk banked but did not build — filed as new row 148. Detail: `.superpowers/sdd/task-3-report.md` | M | parity-only | 8a |
| 82 | Award events beyond Oscars | BAFTA, Cannes, Emmys, Golden Globes, Razzies, … — same shape as `awards.py`, each needs its IMDb event source. **answered 8c:** delivered — the award machinery generalized from Oscars-only to a per-ceremony `EVENTS` registry (`src/autoposter/collections/builders/imdb_award.py`) plus an `event_validation.yml` lookup (`collections/awards.py::fetch_event_validation`/`require_known_event`) that refuses an event id the community dataset does not carry, in words — never a scrape fallback, the refusal this service has held to since 8a. One non-Oscars ceremony shipped proven: Golden Globes (`ev0000292`), its year collection and both static winner collections compared id-for-id, in order, against an oracle — Kometa's own `_award` resolution algorithm (`Kometa-Team/Kometa` `modules/imdb.py`), transcribed and run over Kometa's own `golden.yml` config and the same upstream IMDb-Awards data files, with no autoposter code imported into the oracle script. **Exact match, including order, on the first run**, across all three collections. Further ceremonies are opt-in via one more `EVENTS` row plus one more year-collection registration each — a registry-not-engine addressing decision, because `definition_titles`/the delete sweep read a title pattern off the *registry entry* with no definition in hand, so one class serving every ceremony could only offer one (wrong-either-way) pattern. Oscars defaults are byte-unchanged (golden gate green, fixture untouched); which ceremonies (if any) become defaults is unchanged — still a pending user decision. Detail: `.superpowers/sdd/task-4-report.md`. **which-events question RESOLVED (4c/defaults-catalog, user decision): ALL SIXTEEN.** `Kometa-Team/Kometa`'s `defaults/award/` ships sixteen ceremony ymls (plus `separator_award.yml`, which builds a section divider rather than an event and gets no row), and all sixteen are registered in `EVENTS` — the fifteen further dataset events Kometa has no default for stay unregistered. But *supported* is not *defaulted*: **exposure is picker-gated**. None of the fifteen non-Oscars ceremonies is on out of the box; each is one opt-in `collections.presets` key, built from the registry rather than written out (`src/autoposter/collections/catalog.py`, `AWARD_PRESETS`), so an untouched config expands `presets: []` to nothing and produces byte-for-byte the collections it produced before. The Oscars are deliberately **not** a preset row — they stay `collections.awards`, because a second switch for the same four collections would put two identically-titled built-in definitions in one library's list, the one collision the title validator cannot catch. Every ceremony's strings (filters, `allowed_libraries`, titles, summaries, year formats, poster folders) were transcribed from its own defaults yml and Translations file, and three further Kometa oracles — BAFTA, the Emmys, Cannes — matched id-for-id and in order on all six comparisons. Detail: `.superpowers/sdd/task-1-report.md`, `.superpowers/sdd/task-2-report.md`, `.superpowers/sdd/task-3-report.md` | M–L — per-event sources | parity-only | 8a |
| 83 | Person builders | `tmdb_actor/director/writer/producer/crew` filmographies + `tmdb_person` summaries/posters + birthday/deathday gating. **answered 8c (static half):** delivered — five filmography builders (`tmdb_actor`/`tmdb_director`/`tmdb_writer`/`tmdb_producer`/`tmdb_crew`, `src/autoposter/collections/builders/tmdb_person.py`) as five registrations of one build path over `/person/{id}/movie_credits` and `/person/{id}/tv_credits` (not `/discover`'s credit filters, which are movie-only and paged), through the new `person_credits(person_id, media_type)` transport (`providers/tmdb_lists.py`). The role table is pinned to TMDb's own job/department spelling, each of the three non-obvious rows argued in the opposite direction from what looks consistent: `tmdb_director` matches `job == "Director"` (the `Directing` *department* also holds First/Second Assistant Director and Script Supervisor); `tmdb_producer` matches `job == "Producer"` exactly, not Executive/Co-/Associate/Line/Supervising Producer, and the `Production` *department* is worse — it also holds Casting and Production Manager; `tmdb_writer` matches `department == "Writing"`, not a job — TMDb spreads writing credits across Screenplay/Story/Teleplay/Novel/Book/Adaptation and more, all genuinely writing credits an exact `job` match would drop. The dynamic half — `tmdb_person` bio/poster, `tmdb_popular_people`, birthday/deathday gating, appearance-threshold dynamic types, library-wide credit scans — stays with 10c, per an explicit hard stop stated in the module's own docstring. **Row-83-vs-10c contradiction resolved:** this row and the `#### 10c — People` section below both list summaries/birthday-gating against the person builders; 10c's later, more specific entry governs, and the adjudication is now recorded in `tmdb_person.py`'s docstring as well as here. Detail: `.superpowers/sdd/task-2-report.md` | M | parity-only | 8a |
| 148 | `imdb_search` Kometa-complete constraint surface | `AdvancedTitleSearchConstraints` also carries keyword, credit, country, language, certificate, runtime, award and list-membership constraints beyond row 81's minimal tier; none were built, and the live walk that would answer most of it is already banked (`.superpowers/sdd/task-3-report.md` §1, §6.6) | S–M — the walk is largely done; each is a few more fields | parity-only | 81 |
| 149 | `award_filter` for multi-medium events | Neither ceremony shipped in row 82 (Oscars, Golden Globes) needed it, so nothing in `imdb_award.py`'s `EVENTS` registry supports Kometa's `award_filter`; BAFTA (`ev0000123`) splits its awards across film/TV/games and cannot be registered as a third ceremony without it (`.superpowers/sdd/task-4-report.md` §6.5). **answered 4c/defaults-catalog:** delivered — the filter lives on the **award**, not the event: the `awards` dict's 4-tuple became a named `Award` record carrying `award_filter` last, and `winners_for_categories`/`winners_for_year` take it, with `None` meaning every group and byte-for-byte what both functions did before. Per-event was rejected because the split BAFTA needs is per *collection*: one event id, one filter per winners collection, where an `AwardEvent.award_filter` could only hold one answer for the whole ceremony and expressing the film/TV split would need two `EVENTS` rows sharing `ev0000123` — two rows that would both want the same year titles, which the pairwise `year_pattern` isolation invariant (`test_every_events_year_pattern_only_matches_its_own_year_title`) forbids. Two corrections the row's premise did not survive, both from Task 2's transcription of all sixteen `defaults/award/*.yml`: (a) `award_filter` appears there only under `collections:`, never under `dynamic_collections:`, so `winners_for_year` **lost** the parameter again — no caller, and no ceremony that would want one; (b) BAFTA itself carries `allowed_libraries: movie` on both of Kometa's blocks and **no** `award_filter` on either (its two category names are film-award names, so reading every group costs nothing), so BAFTA shipped registered, Movie-only, and not using this mechanism. Net: the machinery exists, is proven both ways (`tests/test_collection_awards.py::test_baftas_film_categories_belong_to_the_film_group_alone`), and **six shipped ceremonies configure one** — Berlinale, Cannes, César, Sundance, TIFF and Venice each pass a `*_AWARDS` constant as the fifth **positional** `Award` field (`src/autoposter/collections/builders/imdb_award.py:282,300,323,497,515,538`; `awards.py`'s own comment: "Six ceremonies use one; the other ten read every group"), and the Cannes full oracle exercises one end to end. What is true of BAFTA specifically — the ceremony this row was filed for — is that it configures **none**, because Kometa's own `bafta.yml` configures none. And no ceremony's *year* collections filter by group, per (a) above: `winners_for_year` carries no such parameter. Detail: `.superpowers/sdd/task-1-report.md` §1, `.superpowers/sdd/task-2-report.md` | S–M — one more registry field + a filter step | parity-only (delivered and configured: six festival ceremonies set one, Cannes exercised by full oracle; BAFTA and the year collections deliberately do not) | 82 |
| 150 | Award year collections resolve TV winners on Show libraries unguarded | Golden Globes' year collections carry television title ids because the ceremony awards TV and Kometa's own dynamic year block sets no `allowed_libraries` restriction (only the two *static* winner collections do); today's `imdb_award` builders apply no `require_library_type` gate to either kind. Kometa gates statics only — a controller decision is needed on whether to gate the year collections the same way or simply document the difference (`.superpowers/sdd/task-4-report.md` §6.3). **answered 4c/defaults-catalog:** delivered, and the decision went the **other way from Kometa** — both kinds are gated, not statics only. `AwardEvent.library_types` (default `("Movie",)`, transcribed per ceremony from each defaults file's `allowed_libraries`) plus `require_library_type` in `ImdbAwardBuilder.build` **and** in `ImdbAwardYearsBuilder.expand`; the expand gate is the earliest point that knows the library type and the only path to that builder's `build`, so a refusal costs no fetch. The failure it closes is the one this row names: a definition with no `libraries:` key runs against every library in the pass, and an ungated award definition on a Show library resolves every id to nothing — indistinguishable from a ceremony that had no winners. The Golden Globes stay a Movie ceremony under this rule: their year collections do carry television ids, because Kometa's dynamic year block sets no restriction, and those ids simply resolve to nothing in a Movie library. Task 3 added the per-collection narrowing the event level cannot express (`Preset.award_library_types`, `collections/catalog.py::_AWARD_NARROWING`, keys validated against the event's own awards at import): Critics Choice awards film and television, so its year collections belong in both libraries while its Best Picture collection is Movie-only. `test_each_ceremonys_library_types_are_the_media_it_awards` checksums the whole table, so a ceremony that took the `("Movie",)` default because nobody read its `allowed_libraries` shows up there. Detail: `.superpowers/sdd/task-1-report.md` | S — a decision, then a guard or a doc line | parity-only (still not exercised by default: the Oscars remain the only ceremony on without an opt-in preset) | 82 |
| 151 | `event_validation.yml`'s 398 KB fetch has no cache-TTL home | Memoised per-pass on `run_cache` only (row 82's design), so every pass re-fetches and re-parses the same 398 KB YAML file even though `ProviderCache` exists for exactly this kind of cost; it is wired for `fetch_json`/JSON-shaped responses, and this file is YAML, so it does not fit the existing cache without extension (`.superpowers/sdd/task-4-report.md` §6.1). **4c/defaults-catalog note — the number attached to this got bigger.** Nothing here changed, but the ceiling did: sixteen ceremonies are registered now, fifteen of them one opt-in preset key each, so a full pass with the whole award catalog switched on re-fetches the 398 KB validation file **once** plus **fifteen award event files** (sixteen with the defaulted-on Oscars) — each memoised per pass, per event, and every one of them fetched again on the next pass. The per-event cost is unchanged; what changed is how many events a single pass can want, from two to sixteen (`.superpowers/sdd/task-2-report.md` §7.5) | S–M — extend the provider cache to a YAML/text shape, or add a parallel one | parity-only | 82 |
| 152 | `imdb_lists.py` module naming under-describes itself | The module now holds three roots — list, watchlist, and (since 8c) advanced search — but keeps the name of the first; renaming would move 8b's import path and break the "green unmodified" requirement both 8b and 8c held themselves to, so the docstring carries the correction instead of a rename (`.superpowers/sdd/task-3-report.md` §6.1) | S — rename + import-path updates, once both phases are merged | cosmetic | 8b, 8c |
| 153 | Award category vocabularies have no drift failure | Every other pinned vocabulary in 8c fails loudly when upstream drifts: the `tmdb_discover` matrix self-checksums against TMDb's documented parameter count (row 80), and `imdb_search` refuses an unknown genre/type id as a load error (row 81). An `imdb_award` ceremony's category tuples (`EVENTS[...].awards[key] -> (title, categories, ...)`, matched via `winners_for_categories`, `src/autoposter/collections/builders/imdb_award.py`) have no such check: if IMDb renames a category upstream, the tuple matches zero entries in the fetched dataset and that year's winner silently drops from the collection — no error, no warning, indistinguishable from a year that genuinely had no winner. Candidate mechanism: a per-event category-coverage assertion against `event_validation.yml`'s fetched data — a category tuple matching zero entries across every year raises, rather than degrading to a quiet miss. **answered 4c/defaults-catalog, with the honest half stated:** delivered as a **test-time** coverage gate rather than the runtime assertion sketched above — `tests/test_collection_awards.py::test_every_awards_filters_resolve_winners_in_its_own_dataset`, parametrized over `EVENTS` (16 cases, derived from the registry rather than listed), builds each ceremony end to end and fails when a category tuple resolves zero winners, so the event id, the award group, the category vocabulary, the library gate and the poster key all have to be right *together*. Its data is fourteen new per-ceremony fixtures under `tests/fixtures/collections/`, each cut verbatim from the upstream event file on the trim rule "the three most recent years, in the file's own descending order, in which that ceremony's own Kometa filters resolve at least one winner" (the winner clause skips four years that would otherwise have been cut carrying nothing). **What this does not catch:** the fixtures are pinned, so the gate fails on a transcription error or on a ceremony edited into `EVENTS` wrong — not on IMDb renaming a category upstream *after* the cut, which is the drift this row was filed for. Closing that half still wants live data: either the per-event assertion against the fetched `event_validation.yml` sketched above, or a scheduled re-cut of the fixtures. Detail: `.superpowers/sdd/task-2-report.md` §4, §5 | S–M — one coverage assertion, checked once per event rather than per pass | parity-only (transcription drift covered; upstream drift after the fixture cut is still silent) | 82 |
| 84 | Additional mass-op sources | TVDb-backed sources for genre/dates/studio ops; IMDb-dataset-backed where the TSVs carry the field | M — per-field source matrix | parity-only | — |
| 85 | `mass_imdb_parental_labels` | IMDb parental-guide labels (violence, profanity, …) — needs parental-guide data the TSV datasets don't carry; GraphQL feasibility is a risk | M | parity-only | — |
| 86 | Metadata backup | Export current library metadata to a YAML backup — the undo story for mass ops | M | parity-only | — |
| 87 | Lock/unlock/remove/reset controls | Every mass op accepting lock/unlock/remove/reset as its source | M — after row 3 settles current behaviour | parity-only | 3 |
| 88 | `builder_level` collections | Collections whose members are seasons/episodes (overlay-level support exists; collection-level doesn't) | M | parity-only | 8a |
| 89 | Per-definition Arr overrides | `radarr_*`/`sonarr_*` per collection + `item_radarr_tag`/`item_sonarr_tag` member tagging | M | parity-only | 8a |
| 90 | `smart_filter` / `smart_label` | Plex-native smart collections from a filter, plus the label-then-smart indirection | M — once the DSL exists | exercised (if row 1 says CS buckets should be smart) | 9b, 1 |
| 91 | `plex_collectionless` | Items in no other collection — needs a full collection-membership map | S–M | parity-only | 8a |
| 92 | Per-library config override matrix | Any global block overridable per library (autoposter has it only where the user's config needed it). **8a note:** PARTIAL delivery is pinned to exactly per-definition `libraries:` targeting plus each definition's own `params:` block — nothing broader; no general per-library override of a global config block exists | M — schema surgery | parity-only | — |
| 93 | Defaults-pack equivalents | Config presets reproducing Kometa's default families in scope: charts (basic/tmdb), genre, studio, streaming, resolution, aspect, year, decade, country, region, continent, franchise, universe/based (via MDBList), seasonal (via row 70), content-rating regionals. **HALF-ANSWERED 4c/defaults-catalog — the catalog half shipped early, ahead of 10b.** What this row needs first is a *mechanism*: a named preset table an operator selects from, expanded server-side. That is delivered — `src/autoposter/collections/catalog.py` holds **57 rows** (36 READY presets, 18 GATED, 3 setting-backed), `CollectionsConfig.presets` selects from it **by key** (a preset is a key, never a copy of the definitions it stands for), and `sources.default_definitions` expands the selection as a third term appended after the two that shipped — `presets: []` expands to nothing, so an untouched config builds byte-for-byte what it built before (verified across the charts/awards/separators toggle matrix for both library types, sha256 `358dbccc`). The expansion is **pure** and has to be, because the collision validator runs it for every library type on every config write; that is pinned structurally (the module's import list, read with `ast`) and at run time under the suite's no-outbound-network guard. `GET /api/collections/catalog` serves the table plus which keys are active off the live config, touching neither Plex nor the database, and a tabbed picker on the Collections page (`frontend/src/pages/CatalogPanel.tsx`) is its UI. Provenance is strict: `_KOMETA_DEFAULTS` pins every row's upstream defaults path and `_check_kometa_sources` refuses at import a row citing anything else, so a mistyped path is a startup failure rather than a citation nobody can follow — and a row with no upstream file (the six directors) is marked NOT_KOMETA in its own words. **READY of this row's own families:** chart packs (eight TMDb charts, two Tracearr watch-history rows added by row 79, plus the IMDb bundle as a setting-backed row), streaming (fifteen services off `streaming.yml`'s own watch-provider ids, region US), resolution (four buckets, 8k folded into 4k and 576/360/240/144/sd into 480), universe (nine cross-franchise public IMDb lists), content rating (**this row's regionals are CLOSED** — all seven of Kometa's certification families transcribed verbatim from their own defaults files: US movie, US show, and UK/DE/AU/NZ/MAL, each a preset of its own with Kometa's addon tables; the five regional families carry a country prefix on every bucket title — `UK 12 Movies`, `AU MA15+ Shows` — following upstream's own convention for their separators, because verbatim titles collide eight ways once all six co-enabled families are switched on at once, and the all-presets-on test proves the whole table co-enables), and the fifteen award ceremonies of row 82. **The packs-content half REMAINS with 10b**, and each remaining family now names the row that actually blocks it rather than this one: genre, franchise, based-on, country, region, continent, studio, best-of-year and best-of-decade wait on the per-value engine (row 102); aspect on a tier-2 filter attribute (row 96, listed as 155); seasonal on row 160; TMDb keyword name→id on row 161; audio language, subtitle language and network are STRANDED, not queued (row 155). Detail: `.superpowers/sdd/task-3-report.md` | M–L — presets on top of engines | parity-only | 8a, 9a, 10a |
| 94 | Config editor with hot-reload + impact preview | answered 6c: delivered as a DB-overrides layer (user-decided; the ConfigMap stays git-owned, the app owns deltas, the mounted file is never written) with full-depth schema validation (never half-applied), a generation-swap hot-reload covering everything read per-use plus live scheduler cadences (frozen startup captures get honest "restart to apply" flags), a side-effect-free fingerprint-recompute preview, and apply-now (enqueues exactly the affected items) vs save-only. One honest correction: `config.version` hashes the whole artwork section, so any artwork edit re-renders the whole library — per-art-kind granularity needs a per-kind version (row 111) | L — validation + preview machinery | spec §6 | — |
| 95 | Builder engine core | Generic builder abstraction: registry, per-collection config schema, ID-list output with ordering, sync/append, limit, dry-run — everything rows 56–64, 79–83 plug into. **answered 8a:** delivered — `Builder`/`SmartBuilder` protocols + `REGISTRY` (`collections/builders/base.py`), `CollectionDefinition` config schema, multi-namespace `resolve_external`/`build_owned_index` over one `section.all()` (`collections/resolve.py`), the three shipped sources (CS buckets, IMDb charts, Oscars) golden-ported with a byte-identical-output gate (`tests/test_builder_port_golden.py`), and an opt-in guarded delete sweep (`collections/engine.py::_sweep` — off by default, capped, protected-label-aware) | L — the gate for all list work | parity-only (three builders exercised today are hardcoded) | — |
| 96 | Filters subsystem | Post-builder filtering on ~60 attributes with `.not/.regex/.gt/…` modifiers, applicable to any definition. **answered 9a (tier 1):** delivered — a typed predicate model (`src/autoposter/collections/filters.py`) whose deliverable is THE TABLE, `FILTER_ATTRIBUTES`: fifteen tier-1 rows, each carrying Kometa's own attribute name, a value type, the item kinds, a source tier and a note on every non-obvious cell. **Nine ship**, all listing-resident and therefore free: `year`, `resolution`, `audience_rating`, `critic_rating`, `content_rating`, `added`, `release`, `duration`, `studio` (the accessors are `src/autoposter/collections/filter_values.py`, which reads through `object.__getattribute__` so no path in the module can reach plexapi's partial-object reload; a 120-item library evaluates with zero Plex requests, asserted). **Six DROP to tier 2 rather than ship a silent request-per-item**, each on the read-only production probe's own data (`.superpowers/sdd/task-2-report.md` §1.7; 1955 movies / 284 shows, Plex 1.43.4.10903): `genre` — the listing DOES carry `<Genre>` but TRUNCATED to at most two per item (histograms `{1:175, 2:1780}` movies and `{1:43, 2:241}` shows, never three, against a metadata endpoint returning three and four; 20 sampled movies had 38 listed tags against 58 real), so a listing-backed genre filter would not fail, it would answer wrong; `label` — zero `<Label>` across all 1955 movies and all 284 shows while metadata carried one for 30/30 and 19/20 sampled; `collection` — present for only 257/1955 movies and disagreeing with metadata on 6/20 sampled; `audio_language`/`subtitle_language` — zero `<Stream>` elements across 200 listing movies (the listing stops at `<Part>`); `network` — **stranded**, Plex 1.43.4 emits it nowhere, 0/284 in the listing AND absent from `/library/metadata`, so even paying a reload per show returns `None`. Twenty-two listing parameters were tried; none changed any of it. Filed as row 155. **The operator set** is per value type, not per attribute: tag `eq(bare)/.not/.regex`; str `contains(bare)/.not/.is/.isnot/.begins/.ends/.regex`; int, float and duration `eq(bare)/.not/.gt/.gte/.lt/.lte`; date `within-the-last-N-days(bare)/.not/.before/.after`. **The 9b equivalence table is written down**: `PLEXAPI_EQUIVALENT` names, per `(type, operator)`, the `plexapi.base.OPERATORS` key 9b translates into — or `None`, explicitly, for the two relative-date forms plexapi has no equivalent for — with a structural test pinning that every negative operator maps to its POSITIVE counterpart's key (the negation is ours, so a 9b translator that also negated the key would double-negate). **Config surface:** `filters:` on any list definition, parsed and validated at LOAD naming definition title + field, REFUSED on a smart definition (the `_membership_knobs_need_a_membership` class) and refused when it names an attribute with no accessor; inherited by expansion alongside `limit`; the engine stage sits between resolution and the cap and reports a `filtered` count. No default definition gains a filter — the golden gate is byte-identical. **Proven against Kometa, not against itself:** 120 listing-shaped items and two filter configs (six attribute families, `.not`, two range operators, a relative date, a regex, an OR of AND-blocks) evaluated by our model and by **Kometa 2.4.8's own filter code**, fetched and transcribed standalone — exact match, order included (`tests/test_collection_filter_oracle.py`). It was RED first, in four places, and every one was adjudicated in Kometa's favour with OUR code changed: the bare date window had an invented upper bound at today (Kometa's is one-sided, so future-dated releases stay); `duration` was rounded to whole minutes (Kometa keeps the exact quotient, so a 149.7-minute film passes `.lt: 150`); `.regex` compiled with `IGNORECASE` (Kometa compiles bare at all three of its call sites); and dates were compared as calendar dates (Kometa compares the moment, so an item added at 09:15 passes `added.after:` that same day). A fifth came from reading the source: Kometa accepts `.gt/.gte/.lt/.lte` on a date and silently rewrites all four to the STRICT `.after`/`.before`, so our inclusive `.gte`/`.lte` were the same spelling with a different meaning — removed, and a real inclusive form is filed as row 157. Detail: `.superpowers/sdd/task-4-report.md`. The remaining ~45 attributes and the modifier long tail stay with 9b, which owns the same table | L — shares its predicate model with 9b | parity-only | 95 |
| 97 | Custom overlay mechanics | User-supplied overlay images (file/url), templated text overlays with `<<variables>>` and modifiers, configurable positioning/backdrops, generic queue engine, general cross-overlay suppression | L — generalising what `badges/` hardcodes | parity-only | — |
| 98 | Playlists | Playlist definitions from any builder, ordering inherited, `libraries`, `sync_to_users`/`exclude_users`, delete + report, preset playlist file | M–L — new subsystem, but builders do the hard part | parity-only (user never used them; **scoped in**) | 95 |
| 99 | Per-item metadata overrides | The capability of Kometa's metadata files — declarative per-item/season/episode edits (titles, summaries, ratings, dates, tags, images, advanced settings) via autoposter's config/UI, not YAML compatibility | L — whole subsystem | parity-only | 94 helps |
| 100 | Overlay families | aspect, language_count, content-rating regionals, direct_play, network/studio, ribbon, status (TVDb), streaming (TMDB watch providers), versions | L total — each family is S–M render work plus its data source | parity-only | facts plumbing; 8a for ribbon |
| 101 | Plex search DSL | `plex_search` + the full search/sort attribute matrix — a query language against Plex (~60 attributes, modifiers, and/or nesting, limits, sorts) | **XL — multi-week even fully parallelised** | parity-only (exercised only indirectly via CS defaults) | 9a model |
| 102 | Dynamic collections engine | One collection per distinct value: enumeration per type, include/exclude/addons, key-name/title overrides and formats, lifecycle (create, delete-below-minimum), sync | **XL** — only the CS content-rating instance exists | exercised (CS buckets are one instance of it) | 95; 9a for some types |
| 103 | Action Center + asset-quality tracking | Track *why* each chosen asset is imperfect (language rank, provider rank, truncated text, text-fallback, missing), a review queue with resolve/replace/delete and bulk ops | **XL — the largest net-new subsystem**; nothing models "succeeded but suboptimal" | parity-only (but the biggest Posterizarr UI feature) | 73 |
| 104 | Sort-title prefix / `collection_mode` on the create paths | Row 5 follow-up (5b): the create paths (`reconcile.py:364-368`, `lists.py:145-150`) set neither Kometa's `!<section>_` sort-title prefixes nor a display mode, so a collection created fresh (each year's new Oscars collection, a new age bucket) sorts by bare title while its adopted siblings keep Kometa's prefix. **answered 8a:** delivered — `CollectionDefinition.sort_title`/`collection_mode` applied by `apply_collection_settings`, shared by both the list-collection path (`lists.py`) and the Common Sense path (`reconcile.py`), so both create paths set them the same way (`collections/reconcile.py::_apply_sort_title`/`_apply_mode`) | S — copy the family's sort-title scheme into the create paths | exercised (indirect) | — |
| 105 | Collection-poster text compositing | Row 4 follow-up (5b): Posterizarr *can* composite a styled title plus a "COLLECTION" line onto collection posters (`CollectionPosterOverlayPart` / `CollectionTitlePosterPart`), a path this deployment never exercised (evidence in row 4 — nothing live carries it). Implementing it would need a new config section, the collection font (`Colus-Regular.ttf`, not in `assets/fonts/`), a compose step between fetch and upload in `collections/posters.py`, and a two-block text render; the phase-1 argv builders are canvas-agnostic (`render/compositor.py:19-45,94-118`, `render/textfit.py:92`) so they reuse, but no Posterizarr-styled oracle exists to prove parity against | M — reuse compositor + textfit; new config, font, tests; no oracle | parity-only | — |
| 106 | Multi-version badge selection & edition splits | Row 14 follow-up (5b): badges describe Media[0], which Plex orders arbitrarily (Ben-Hur: AAC/198min badged while a DTS-HD MA remux sits at media[1]); Kometa surfaces the best matching variant by overlay weight. Separately, an edition-split second item can never be recorded by the GUID-based webhook/safety net (getGuid returns one item; safety net re-enqueues forever, arr/sync.py:341-352) and two splits in one folder would share an asset path | Low — deterministic best-Media pick in media_info_from_plex (~30 lines + tests); split handling larger but exercised by 0 library items | Not cutover-relevant: badge delta is cosmetic on 50 movies/224 episodes; library has no edition splits | — |
| 107 | SCHEDULED_JOB_NAMES agreement guard | 6a follow-up (final review): the run-now allowlist in `api/routes.py` spells the four job names by hand because importing the factories would pull plexapi/httpx into the route module; nothing ties the two lists together, so a fifth job added in `scheduler/jobs.py` silently cannot be hand-triggered. A regex-grep test over `scheduler/jobs.py`'s `name="…"` literals (or a shared constants module) closes it | S — one test | — | — |
| 108 | `.row-actions` has no CSS | Pre-existing, surfaced in 6a review: `Failures.tsx` renders its retry/dismiss buttons in a `.row-actions` div no stylesheet defines, so they are unstyled. **answered (mobile sweep):** the class now has real styles in `shell.css` — `display: flex; flex-wrap: wrap; align-items: center; gap: 8px` — and the buttons moved off the `<td>` into the `.row-actions` div the row description already assumed, so the cell keeps `display: table-cell`. Found in the same sweep to be more than cosmetic: at 500px the unwrapped reason column beside it widened the table past the content column and carried Retry/Dismiss off screen entirely, so the wrap is what makes them reachable, not merely tidy. Guarded by a declaration assertion in `frontend/src/styles.test.ts` and a class assertion in `Failures.test.tsx` | S — a few CSS lines | — | — |
| 109 | Search+filter composition assertion | 6a follow-up (final review): Library composes `search` with library/kind/status in one URLSearchParams and the code is correct, but no test pins the composition, so a dep-list regression in the listing effect would go unseen | S — one test | — | — |
| 110 | Dashboard broadcaster hardening | 6b follow-up (final review): (a) `StatusBroadcaster.subscribe` never restarts a loop whose task died with `_task` still set (only a spurious external cancel can cause it; viewers would get heartbeats but no data) — `if self._task is None or self._task.done():` closes it; (b) a poll query that hangs without raising is unbounded — wrap `_build_snapshot` in `asyncio.timeout` | S — two guarded lines + tests | — | — |
| 111 | Per-art-kind render version | 6c follow-up: `render_version` hashes the whole `artwork:` section, so editing one kind's text setting invalidates every fingerprint and a full-library re-render is the honest answer. A per-art-kind version (hash only that kind's settings + the shared roots) would confine invalidation — and the impact preview's counts — to the kinds an edit actually touches. Changes the fingerprint contract: touches `compute_fingerprint` callers, `adopt/`, and stored fingerprints (one-time global invalidation on upgrade, or a dual-read migration) | M — contract change, own phase | — | — |
| 112 | Config-contract cleanup: computed paths + live exceptions | 6c follow-up (final review folded two findings in): (a) `version` is derived but schema-plain, so the editor renders it editable and an override on it is inert noise — reject it server-side like `secrets`, or serve a computed-paths list; (b) GET serves `frozen_paths` but not `LIVE_EXCEPTIONS`, so the client marks `plex.resolve_max_attempts` restart-to-apply while the save response correctly omits it — serve the exceptions so both sides agree | S | — | — |
| 113 | Cross-replica config-swap propagation | 6c follow-up: a saved override swaps generations in the serving process only; sibling replicas keep the old config until restart (they load the persisted overrides at boot, so nothing diverges durably). If multi-replica ever matters: replicas poll `config_overrides.updated_at` (or NOTIFY) and re-load. Single-operator single-replica today — deliberately deferred | S–M | — | — |
| 114 | One default-config-path spelling | 6c follow-up: `DEFAULT_CONFIG_PATH` now lives in `config/loader.py` but the two CLIs (`collections/__main__.py`, `adopt/__main__.py`) still spell `/config/autoposter.yaml` out; point them at the constant | S | — | — |
| 115 | Reconcile failure must not report `ok` | Live-pass finding (2026-08-23): both libraries failed at the separator summary edit, yet `scheduled_runs.last_status` stayed `ok` and the Collections page showed a green pill — `reconcile_libraries` contains per-library failures in its summary string and never raises, and the scheduler marks `failed` only on an exception. The collections job should raise (recording `failed` + the summary as detail) at least when EVERY library failed; a partial failure policy needs deciding. **answered 8a:** delivered, and stricter than "every library failed" — `ReconcileResult`/`LibraryOutcome.ok` is false if EITHER the pass raised OR any definition's source failed (contained, but not hidden); `collections_reconcile` re-raises `CollectionsPassFailed` so `last_status` goes `failed` with the real detail (`collections/service.py`, `scheduler/jobs.py`) | S — a few lines + tests; the status vocabulary is pinned to ok/failed | — | — |
| 116 | Overrides-layer edge hardening | 6c follow-up (final review): (a) an empty-object override (`{"artwork": {}}` — hand-crafted only, the UI cannot produce it) is reported as a leaf by `document_paths`, making the editor seed the whole served section as an override on the next save; treat `{}` as no-path or reject it. (b) `_render_affecting` in the impact walk is a hand-maintained enumeration verified exact today (version-covered fields + `skip_tba`); re-verify whenever the fingerprint-input walk gains a new config read — no non-tautological drift test exists | S | — | — |
| 117 | Credential-bearing tracebacks reach the logs surface | Pre-existing, surfaced by 6d review: Fanart's `api_key` rides the query string, httpx embeds full URLs in `HTTPStatusError` messages, and `ladder.py:81` (and now the candidates fan-out) logs provider failures with `exc_info=True` — the traceback's last line lands in the in-memory log buffer and is served by `/api/logs` and the stream. Behind auth and operator-only, but the repo's own host-only rule deserves to hold here: scrub `api_key`-style query params from logged exception text, or log without exc_info at these sites | S | — | — |
| 118 | Root-level ruff is not a usable gate | Two pre-existing F401s in the 6c merge migration (`alembic/versions/6c515e89a1f0_*.py`) mean `ruff check .` fails while the CI gate (`ruff check src tests`) stays green. Fix the imports or extend the gate's paths so the two commands agree. **answered 4c/defaults-catalog — stale, closing:** the premise no longer holds. `ruff check .` runs clean at this branch's HEAD, and the two F401s the row cites do not exist: `alembic/versions/6c515e89a1f0_*.py` imports `Sequence, Union` and uses both in its own annotations, so neither is unused. Root-level ruff and the CI gate already agree; nothing to fix, and no evidence of when it was fixed (the row was filed against a state some later edit resolved without citing it) | S | — | — |
| 119 | Time-based test flakes under shared-container load | Four distinct one-off failures this cycle, each passing in isolation and on rerun: test_facts_gather (DB now() went backwards ~15s — host clock step), test_queue::test_two_workers_claim_distinct_jobs_independently, test_queue::test_reclaim_stale_resets_run_after (~16s timestamp drift under load), test_queue::test_job_parks_after_max_attempts. Pattern: wall-clock assumptions vs a loaded shared container. Audit the timestamp assertions for tolerance or DB-clock pinning. **8b tally (2026-08-24/25):** four more one-off instances of the same pattern, each green on rerun — `test_facts_gather` again (`updated_at`), a worker-pause `TimeoutError` twice, `test_api_jobs` matching on the substring `"999"`, and a pipeline test whose clock ran backwards 17s. Eight known instances now; the audit is overdue. **8c tally (2026-08-25, Task 2):** one more — `test_item_facts.py::test_fetched_at_comes_from_the_database_clock` failed at task start under container contention (the session fixture's transaction had begun 18s before the row it compared `fetched_at` against, past the check's 1s tolerance), passed immediately on an unmodified rerun and again in the task's own final full run (`.superpowers/sdd/task-2-report.md`). Nine known instances now. **9a tally (2026-08-25):** one more of the same pattern — `test_facts_gather.py::test_updated_at_advances_on_second_persist_facts` failed in Task 2's clean-tree BASELINE, before any edit, with the second persist stamped an *earlier* microsecond than the first, and passed in every later run (`.superpowers/sdd/task-2-report.md`). **Ten known instances.** And one COUSIN, filed here rather than as its own row because it is the same symptom class — a one-off failure under shared-container load that reruns green — but a DIFFERENT mechanism, so the "audit the timestamp assertions" fix will not touch it: `tests/test_worker.py::test_unexpected_errors_also_reschedule` failed once during 9a Task 3 (`assert "exploded" in job.last_error`, with `last_error` `None`), and `tests/test_worker.py` alone then passed 20/20. Nothing in that phase is on its path. The shape — one `Job` row left `pending`, never claimed by `run_once` — reads like a leaked worker task from another module claiming it first, i.e. cross-module contamination rather than a wall-clock assumption (`.superpowers/sdd/task-3-report.md` §9.1). If the audit ever runs, this one needs its own look. **4c/defaults-catalog tally (2026-08-25):** one more, and the first in this family that is not a database-clock comparison at all — `tests/test_scheduler_core.py::test_a_failing_job_is_recorded_and_the_scheduler_survives` starts the scheduler at `poll_seconds=0.01`, sleeps `0.1` s, stops it, and asserts `len(calls) > 1` (ten polls' worth of budget for two). Observed flaking under full-suite container load; passes standalone. **Eleven known instances.** It widens the audit's shape rather than its length: a sleep-then-count assertion has no timestamp to pin or tolerance to widen — what it wants is to wait on the *event* (poll twice, then stop) instead of on a duration. **row129 tally (2026-08-25, recorded by the pruner phase's Task 4):** two more, both observed during the defaults-catalog phase's own runs rather than this branch's — `tests/test_scheduler_core.py::test_a_failing_job_is_recorded_and_the_scheduler_survives` again, the same `assert len(calls) > 1` after a `0.1` s sleep at `poll_seconds=0.01` this row already names above, observed under full-suite container load during a defaults-catalog-phase fix-round run, passed standalone; and `tests/test_pipeline.py::test_second_upsert_of_the_same_rating_key_refreshes_updated_at`, whose DB clock ran backwards ~1.5s between two upserts, observed in a later defaults-catalog-phase full-suite run, with an unmodified rerun going 42/42 green. **Thirteen known instances.** | S–M — a few tests, plus one leaked-task question | — | — |
| 120 | Picker polish: post-pick honesty + unpickable SVG tiles | 6d final-review minors deferred: (a) between a pick and its queued re-render landing, the panel's current-candidate mark and the replaces-override warning reflect pre-pick state (a second pick in that window shows no warning though an override file now exists); a client-side picked-this-session flag closes it. (b) TMDB serves some logos as SVG — the browse lists them but a pick correctly 502s (outside the content-type allowlist, Pillow can't decode); filter them from the grid or mark unpickable | S | — | — |
| 121 | First-start setup wizard | End-of-roadmap (user-placed: after the app is otherwise done). Booting with required env absent enters a setup mode that walks the operator through (a) database details, (b) master password, (c) provider API keys. Design constraints recorded at filing: the app cannot set its own env, so collected values need a persistence target (a writable secrets file the loader falls back to is the likely shape — GitOps/ExternalSecrets deployments never enter setup mode and are unaffected); the wizard is a classic unauthenticated attack surface, so it must exist only while unconfigured, demand the master password as its first act, and exit setup mode atomically | M–L — new boot mode + persistence + UI | usability (new deployments) | — |
| 122 | Adoption report: distinguish newly-adopted from re-confirmed | Live finding (2026-08-23): a rerun reports identical `renders`/`skipped` counts to the first run because already-adopted rows are re-hashed and re-stamped into the same `renders` counter — the operator cannot tell a resumed run made progress (and reasonably suspects it made none). Split the counter (`adopted` vs `re-confirmed`) in `_Counters`/`AdoptionReport`/summary lines | S | — | — |
| 123 | Browser-upload source for manual mode | 6e follow-up: manual mode takes a URL or a manualassets mount path; a browser file-upload (multipart) source is net-new — no upload endpoint or FormData path exists in the API or SPA today (`apiFetch` sets JSON content-type). Add a multipart endpoint sharing the same verify/transcode/install tail, and a file picker beside the URL input | S–M — first multipart surface | parity-only | — |
| 124 | Collection-poster path containment | 6e follow-up (whole-branch review): `install_collection_poster` writes `<assets_root>/<library>/<title>/poster.jpg` with `ManagedCollection.title` interpolated unsanitized; a title carrying separators or `..` could steer the write outside assets_root (`_install` creates parents). Exploitability low (titles come from operator config/reconcilers + require an operator click; the read side predates 6e), but the write should be realpath-contained to assets_root like the manualassets read path is. **answered 8a:** delivered — every poster path candidate is realpath-contained to `assets_root` before use (`collections/posters.py::_poster_candidates`, the same double-realpath idiom as the manualassets mount), raising `PosterPathRefused` for a title that escapes it; both the read (`local_poster_path`) and the write (`poster_override_target`) go through this one spelling | S | — | — |
| 125 | Render-side skip for unnumbered items | Live finding (2026-08-24, recurring each full pass): ~63 title-card jobs for year-grouped specials (TMNT 2003, Top Gear) whose `episode_number` is NULL fail with `title_card requires episode_number` out of `naming._file_name` (via `render_artifact` → `naming.asset_path`) and park with a WARNING traceback. The adoption walk guards this shape (`_missing_number`, `walk.py`), but a queued render job bypasses the walk and reaches the naming call unguarded. Apply the same missing-number skip in the render pipeline before `asset_path`: record `status='skipped'` naming the missing number, no exception, no artifact; covers `season_poster` with NULL `season_number` the same way — **delivered** (guard consolidated as `naming.missing_number`, shared by the walk and `render_artifact`) | S | — | — |
| 126 | Live pass over the 7b artwork modes | 7b shipped fake-Plex-only by design. Two logo-mode assumptions are unverified against a live server (both fail safe): (a) Plex auto-selects a freshly uploaded clearlogo — if it doesn't, every marker is NULL and logo revert is a permanent no-op; (b) `DELETE /library/metadata/{rk}/clearLogo` removes the selected upload rather than the whole listing. The Run-modes UI's response rendering is likewise fixture-built, never exercised against a running server. One supervised session on the live deployment: dry-run every mode, one small filtered apply per mode, confirm the two logo behaviors | S — operator session | — | 7b merge |
| 127 | Mode-run count granularity | 7b review-deferred coarseness, resolve once rather than piecemeal: logo updater's `uploaded` includes items whose marker write failed (revert won't claim them; only logs distinguish); logo `failed` folds SVG-only picks with no-provider-logo; a failed Plex probe silently shrinks the updater/reset candidate sets (a dry run against an unreachable Plex reads as "nothing to do" — a `probe_failed` count would let the UI tell them apart) | S | — | — |
| 128 | Accept Sonarr ImportComplete events | Live webhook finding (2026-08-24): Sonarr v4's On Import Complete sends `eventType: ImportComplete`, which SONARR_EVENTS drops (0 intents). It fires once per completed release (vs per-file Download) — the better shape for season packs. Add it to the accepted set; verify the payload's `episodes` array against a captured real delivery before implementing | S | — | — |
| 129 | Prune media_items gone from Plex | Live finding (2026-08-24, DVR-library move): items moved out of Plex leave media_items rows that every full pass re-enqueues into no-Plex-item retries → re-parks, forever. No prune exists (cleanup is file-level). Add a drift-style sweep (or full-pass side channel) that detects rating_keys no longer resolvable and retires the rows (soft-delete or delete + asset handling decision). **delivered:** the row129 pruner phase (`docs/superpowers/plans/2026-08-26-row129-pruner.md`) shipped the `plex_prune` scheduled job — hard delete with a full-identity `events_log` audit row per row (`scheduler/prune.py::retire`), a dry-run default (`prune.apply`), safety caps on implausible volume, health and empty-table refusals, and hand-trigger support via `SCHEDULED_JOB_NAMES` (`app.py`, `api/routes.py`); documented in `deploy/README.md`'s "Pruning `media_items` rows Plex can no longer resolve" subsection | M | — | — |
| 130 | Failures page: job payload diagnostics | Live finding (2026-08-24, user request): diagnosing the stale Friends jobs took raw SQL — the UI shows only the error string. Expose the job's identity fields (kind, title, S/E, external ids, rating_key present or not) on the Failures page, ideally with an expected-vs-Plex check so a stale-payload job (pre-#57, no rating_key) is distinguishable from a real matching gap | M | — | — |
| 131 | Test-suite runtime outgrew timeout guards | The full backend suite no longer fits `timeout -s KILL 300` on at least one dev host (row-125 session had to run it in two halves; SIGKILL at 68%/91%). Audit the timeout constants in CI and docs/recipes (600 is the current facts-file value) and bump or split per suite growth (~1800 tests) | S | — | — |
| 132 | Season-poster fallback to show art | User request (2026-08-24, from the 19 season_poster no_art items): when the provider ladder yields no season-specific art, fall back to the SHOW's poster as the base (Kometa's effective behavior — season text styling still applies). Mark the render's provenance as fallback-from-show so a later season-art appearance is distinguishable; ladder still prefers real season art when present. **delivered:** merged as PR #65 — the season-scope ladder miss re-queries the show's own poster ladder and styles that instead of recording `no_art`; `source_mode` becomes `"show_fallback"` and is cleared again once real season art renders | S–M — render path | parity-plus | — |
| 133 | Plex↔arr ID-mismatch view | User request (2026-08-24, from the Brooklyn mis-match): a view listing items whose Radarr/Sonarr-declared ids disagree with Plex's guids (or with media_items), so mis-matches can be verified by hand instead of surfacing as mysterious no_art/no-item failures. Needs an arr-vs-Plex comparison sweep + a UI page with per-item links to both. **delivered:** merged as PR #67 — an arr-vs-Plex comparison sweep plus a Mismatches UI page with per-item links to both sides | M | — | — |
| 134 | Sidebar version checker vs Harbor | In-flight implementation-tracking item, not a backlog gap: a sidebar indicator comparing the running image against what Harbor (the self-hosted registry) currently holds, in progress on `feat/version-checker` | S–M | usability | — |
| 135 | Award-year title collisions not catchable at config load | 8a Task 4 finding, documented in the validator (`config/schema.py:480-489`): the built-in-title-collision check enumerates every builder's titles except the dynamic Oscars year collections ("Oscars Winners 2026"), because doing so needs the ceremony dataset the validator does not fetch. A definition accidentally reusing a live award-year title is not caught until the two definitions fight over the same collection at run time | S–M — needs the ceremony dataset at validation time | parity-only | 95 |
| 136 | `CollectionsPassFailed` detail is not redacted | 8a Task 4 review finding: `service.reconcile_libraries` stores a bare `str(error)` on `LibraryOutcome.error` (`collections/service.py:274`) when a library's pass raises outright (a Plex or provider failure, which commonly carries the URL it failed on), and that string flows un-redacted through `ReconcileResult.detail` → `CollectionsPassFailed` → the job's `last_detail` (served by `/api/snapshots`) and into notifications. Pre-existing exposure (row 117's class), widened by row 115's richer failure reporting actually surfacing it. Wants the same `_redact`/class-name-only treatment the preview endpoint (`api/collections_builders.py::_redact`, `_library_failure`) already applies | S | — | 117 |
| 137 | Collection definitions listing endpoint | 8a Task 6 finding: the Collections page's Definitions panel has nothing to show until an operator clicks Preview-all, because only `POST /api/collections/preview` exists — there is no plain `GET` that lists the configured definitions themselves (title, builder, library scope) without running the engine | S | — | 95 |
| 138 | Settings editor cannot edit list-of-object config | Page-wide, pre-existing limitation surfaced by 8a Task 6: `frontend/src/pages/Settings.tsx:342` documents that a list of objects has no editor in the generic config UI (alongside null and redacted values). Blocks UI-side editing of `collections.definitions`, which is exactly a list of objects | M — generic editor work, not collections-specific | — | 94 |
| 139 | Job row doesn't persist the retry budget `fail()` used | PR #66 review finding: the Jobs page's "waiting for Plex" badge (`job.waiting_for_plex`) is computed by `api/jobs.py` matching `job.last_error` against a fixed string prefix at serialize time (`WAITING_FOR_PLEX_PREFIX`), not by reading back which retry budget `queue/jobs.py::fail()` actually applied to that attempt. A future change to the prefix, or a second reason for the same wider budget, silently desyncs the badge from reality | S | — | — |
| 140 | Smart-definition `summary`/`sort` accepted but ignored | 8a final review finding: `_membership_knobs_need_a_membership` (`config/schema.py:326-360`) rejects `limit`/`sync_mode`/`item_label`/`tmdb_summary` on a smart (cs_bucket) definition but not `summary` or `sort` -- neither is ever read for one (`reconcile.py` applies only the builder-derived `bucket.summary`; a smart collection has no explicit membership to order), so both silently no-op. Extending the same validator to reject them is the fix, deliberately filed rather than hotfixed here: it is load-time-breaking for any existing config that already sets either on a smart definition | S — extend one existing validator | breaking for configs setting either today | 135 |
| 141 | `imdb_award_years` definitions drop every ride-along at expand | 8a final review finding: `ImdbAwardYearsBuilder.expand` (`collections/builders/imdb_award.py:141-152`) rebuilds one bare `CollectionDefinition` per ceremony year carrying only `title`/`builder`/`params`/`summary`/`sort` -- every other field an operator set on the base definition (`labels`, `label_sync`, `item_label`, `sort_title`, `collection_mode`, `visible_*`, `hub_priority`, `tmdb_summary`) is dropped, so a ride-along setting that reads as applied in config silently never reaches any of the expanded year collections | S–M — carry the base definition's fields forward in `expand` | parity-only | 95 |
| 142 | Cleanups: dead hash helper, pre-resolution preview counts, hub-failure hash | 8a final review, bundled: (a) `definition_config_hash` (`config/schema.py:388`) is defined but never called anywhere in `src/autoposter` -- production-dead; (b) preview's per-definition `adding`/`removing` counts (`collections/engine.py:362-368`) are computed against the current Plex listing before `reconcile_list_collection`'s ownership resolution runs, so a collision-blocked collection's preview counts read as if the write would apply when it will not; (c) a failed `_apply_hub` (`collections/reconcile.py:284`) still lets `definition_hash` store as current, so a Plex-Pass-required hub failure is never retried until the operator edits the definition again | S — three small, independent fixes | — | — |
| 143 | Episode-aware resolution for episode-level builders | 8b Task 5 finding, and `plex_pilots`' blocker (row 56): the owned index and the builder contract both name library *items*, so no builder can hand an episode back as a member — `plex_pilots` has no premise to build on rather than being unimplemented. The traversal answer is already banked in `.superpowers/sdd/task-5-report.md`: `section.search(libtype="episode", parentIndex__exact=1, index__exact=1)` returns every series' first episode in one call, so what is missing is the *contract*, not the query. `collections/resolve.py::build_owned_index` needs an episode-level key, `BuilderResult` an episode-level id form, and `collections/lists.py::reconcile_list_collection` has to apply one. Also the gate for row 88 | M | parity-only | 95, 56, 88 |
| 144 | TMDb paged-list repeat-page hardening | 8b wrap finding: `providers/tmdb_lists._paged`'s `item_count` stop condition compares ids *kept* against members *listed*, so it can never be satisfied once a media-type filter drops anything — and `tmdb_list` now always filters. The same comparison was already unreliable for a list whose upstream entries were deleted, where the count never arrives either. It exists only for the pathological "TMDb ignored `page`" case, which now degrades to `max_pages` identical requests (bounded, but wasteful and duplicated in the result). Fix by counting entries *seen* rather than ids kept, or by stopping when a page is identical to the previous one. Separately open: whether TMDb's cap here should warn the way `imdb_award`'s equivalent cap does, or stay silent as it does now — a posture-alignment question, not part of this row's fix | S | — | 145 |
| 145 | Drop the `page` param from `/list/{id}` if v3 ignores it | 8b wrap: the client sends `page` on the v3 list-details endpoint on the assumption that it pages the way the chart endpoints do. The recorded fixtures pin the response *shape*, not that behaviour, and no live run has happened yet. If the first live run shows v3 returns the whole list and ignores `page`, drop the parameter — it buys nothing and costs a distinct cache key per page — and collapse the loop to the single request `collection_parts` already is, which also retires row 144 | S — one live observation, then a few lines | — | — |
| 146 | TMDb chart summaries and posters | 8b deliberate gap, filed rather than guessed: `imdb_chart` sets a title and summary only because Kometa's translation strings for those charts are transcribed verbatim in `docs/research/kometa-collections.md` §5. No such transcription exists for TMDb's charts, so `tmdb_chart` sets neither — an invented summary would not be parity, and a guessed poster key is a hosted URL that 404s and leaves the collection quietly without artwork. Closing this means transcribing the strings from Kometa's own defaults, not writing copy. Until then a definition's own `summary:` is the way to set one | S — transcription, then two fields | parity-only | — |
| 147 | MDBList error-body caching delays budget recovery | 8b wrap finding (behaviour already documented in `facts/mdblist.py::list_items`, consequence not yet accepted): MDBList answers a spent daily budget with HTTP 200 and `{"error": "API Limit Reached!"}`, which `fetch_json` stores in the provider cache like any other successful response. The `run_cache` memo stops the *spending* within a pass, but a later pass — including one after midnight, when the allowance has already rolled over — is served the cached refusal and re-raises it until the entry ages out, up to a full TTL late. Either do not cache a limit-error body at all, or add `ProviderCache.delete` and drop the key when the error is recognised | S | — | — |
| 154 | `added` filter is timezone-dependent on the runner, not the Plex server | 9a Task 2 fix-round finding (`src/autoposter/collections/filter_values.py`, `filters.py::_as_calendar_date`): Plex sends `addedAt` as a unix epoch, and plexapi's `toDatetime` converts it with `datetime.fromtimestamp(value)` — no `tz`, because plexapi's own `DATETIME_TIMEZONE` is `None` — so the value lands as a naive datetime in the RUNNER's local clock, not the Plex server's, verified directly against the installed plexapi 4.18.2 (`plexapi/utils.py::_parseTimestamp`, `plexapi/video.py:44`). `_as_moment` (renamed from `_as_calendar_date` in Task 4) compares at the MOMENT, so the same item's `added` can fall on either side of an `added.after`/`added.before` boundary depending on which timezone the pass ran in. **9a Task 4 update, and it widens the row:** the comparison was date-granular when this was filed, which confined the effect to items added within a few hours of midnight. Task 4's Kometa oracle moved it to the moment (Kometa compares the plexapi datetime as it stands against a midnight `validate_date` result and against `current_time = datetime.now()`), so a runner-clock offset now shifts EVERY `added` comparison by that offset, not only the ones near midnight — and `added.after`/`added.before` DO ship as of 9a, so the row is exercised. Note also that fixing it is not simply "convert to UTC": the Plex server's own zone is the value that would make `added` mean what an operator reads it as, and the listing does not carry it. Open behavioral question, not yet decided: normalize to a fixed zone before the comparison, or document the runner-dependence and accept it (today's posture) | S — a decision, then either a one-line conversion or leaving the doc note as the answer | parity-only (exercised since 9a: `added` filters ship) | 96 |
| 155 | Six tier-1 filter attributes are stranded by the section listing | 9a Task 2's probe verdict, filed rather than shipped as a silent N+1: `genre`, `label`, `collection`, `audio_language`, `subtitle_language` and `network` are named tier-1 by 9a's own goal but have no accessor, because the one `section.all()` walk the engine pays for does not carry them **completely enough to filter on** — and the failure mode of shipping them anyway is a full, plausible, wrong collection rather than a visibly empty one. Two distinct classes, and they want different answers. (a) **Readable, just not for free** — `genre` (listing truncates to two tags per item), `label` (absent from the listing, present in metadata), `collection` (present for 257/1955 movies and wrong where absent), `audio_language`/`subtitle_language` (the listing stops at `<Part>`; streams never reach it). Each needs either a per-definition opt-in reload budget (the plan's documented exception path, never taken) or a batched `/library/metadata?ratingKey=a,b,c` prefetch — Task 2's probe already measured the batched shape at **10 calls / 16.90s for all 1955 movies at chunk=200**, so the cost is known, not guessed. (b) **Stranded outright** — `network` is emitted by Plex 1.43.4 nowhere at all (0/284 in the listing AND absent from `/library/metadata` for the shows checked), so no budget buys it; what those shows carry is a `studio` naming the network (E4, Hulu, Paramount+), which is a different attribute with different (string, substring-by-default) semantics, and conflating the two silently is exactly what 9a refused to do. Answering (b) means either sourcing it from TVDb/TMDb under a distinct name or accepting that Kometa's `network` filter has no meaning on this server version (`.superpowers/sdd/task-2-report.md` §2) | M — a reload-budget mechanism plus the batched fetch; (b) is a decision first | parity-only (all six refuse at config load today, naming the field) | 96, 9b |
| 156 | Facts-backed filter attributes under distinct names | The plan's own adjudication, filed so it is not re-litigated by whoever adds the next attribute: `item_facts` is NOT a tier-1 filter source, because its columns carry Kometa's names with different meanings — `ItemFacts.content_rating` is a **Common Sense age rating** (`src/autoposter/facts/gather.py:98-116`), not the Plex certification Kometa's `content_rating` filter means, and shipping it under that name would be the same-name-different-filter parity bug. So 9a's `content_rating` reads Plex's own attrib and the facts data is unreachable from a filter. Closing this means giving the facts-backed values **their own names** (`common_sense_rating`, `mdblist_score`, …) as their own table rows with their own notes, plus a source tier the table does not have yet (`facts`, which is a DB read rather than a listing read and is render-history-SPARSE — `src/autoposter/render/pipeline.py:272-319` is the only writer, so most of the library has no row at all and the missing-value rule would do most of the work) | M — new source tier, new rows, and a sparsity story | parity-only | 96 |
| 157 | An inclusive-boundary date operator, under a name Kometa does not use | 9a Task 4 oracle fallout. `date.gte`/`date.lte` shipped in 9a as inclusive-boundary forms on the stated (and wrong) grounds that Kometa had no spelling for them; Kometa's `Plex.split` (`modules/plex.py:2735-2747`) accepts `.gt`, `.gte`, `.lt` and `.lte` on every date attribute and rewrites all four to the STRICT `.after`/`.before`, so the same config key meant two different memberships in the two systems. Both were removed and now refuse at load with a message saying what Kometa does with them. That leaves a real gap: "released on or after 2000-01-01" is only writable as `release.after: 1999-12-31`, which is correct but reads like an off-by-one. The fix is a spelling Kometa does not use — `release.from`/`release.to`, or `release.on_or_after` — so that no config key can ever mean one thing here and another there. Needs a name decision before code (`.superpowers/sdd/task-4-report.md`) | S — a name, then two operator rows | parity-only | 96 |
| 158 | Filter tag values are not validated against the library's vocabulary | 9a Task 4, surfaced while transcribing Kometa's side of the oracle. Kometa resolves every tag filter's written values against the library's OWN tag list at validation time (`Plex.get_search_choices`, `modules/plex.py:1300-1316`, keyed on both `title` and `title.lower()`; `Builder.validate_attribute`, `modules/builder.py:4400-4440`) and RAISES `Plex Error: {attribute}: {value} not found` when a value is not in it — so `genre: Horrror` is a config error there. This service has no library-vocabulary lookup at all: it compares case-insensitively at evaluation time instead, which reaches the same answer for a value that exists and answers "no members" for one that does not. Two consequences worth deciding on rather than inheriting: a typo produces an empty collection instead of a refusal, and the two systems' case-insensitivity lives in different places (validation vs comparison), which 9b's translation to a Plex *search* will have to reconcile because a search sends the value to the server. Note the cost: the lookup is a per-library, per-attribute Plex call, which is why 9a did not add one speculatively | S–M — one cached lookup per (library, attribute), then a load-time check | parity-only | 96, 9b |
| 159 | `year.not` disagrees with Kometa on an item that has no year | 9a Task 4, found by reading Kometa's source rather than by the oracle run, and recorded here with both behaviours because the oracle's library cannot reach it (every item in it carries a `year`, as every item in the probe's 1955-movie section does). Kometa routes a **bare or `.not`** `year` filter through its tag/set-intersection branch, not its number branch (`modules/plex.py:2895`, whose condition begins `filter_attr != "year"`), so for an item with `year is None` the intersection is empty and `year.not: 2000` KEEPS it — while `year.gte: 2000` on the same item goes to `is_number_filter`, hits the unconditional `value is None`, and DROPS it. Ours applies one rule to the whole `int` family: a missing value is excluded under every operator, `.not` included, so `year.not: 2000` drops it. Kometa's split is emergent (the `year` special-case exists so `year: [1990, 1991]` works as a list membership test), not an intent anyone wrote down, which is why 9a did not copy it. Decide: match the emergent behaviour for parity, or keep the uniform rule and say so on the row | S — one branch, or one sentence | parity-only (unmeasured: the probe found `year` on 1955/1955 movies and 284/284 shows, so it may be unreachable on real libraries) | 96 |
| 160 | Day-level seasonal windows, and a collection fed by several builders | 4c/defaults-catalog Task 5 fix-round row, filed out of row 70 the way 155 and 157 were filed out of 96 — row 70 is *delivered* in this document's own words, so citing it as the seasonal pack's blocker told an operator the opposite of what the cited row says. What row 70 delivered is `CollectionDefinition.schedule: {every_n_runs, months}` (`ScheduleGate`, `config/schema.py`), whose date gating is whole CALENDAR MONTHS. Kometa windows its seasonal collections by DAY — `defaults/movie/seasonal.yml` gives Easter `range(03/20-04/30)` — so a months-only gate cannot express one without showing the collection outside its own window. The second half is the harder one: most of those collections are fed by SEVERAL sources at once (Halloween is three IMDb lists, ten TMDb franchise collections and one film), and a definition is one builder, so there is no shape for a multi-source collection at all. Both together are what the catalog's `time_seasonal` preset waits on (`src/autoposter/collections/catalog.py`, `SEASONAL_WINDOW_ROW`) | M — a day-granular window on `ScheduleGate`, then a multi-builder definition shape | parity-only | 70, 93 |
| 161 | TMDb keyword name → id resolution | 4c/defaults-catalog Task 5 fix-round row, filed because no existing row owns this: row 62 delivered `tmdb_keyword` taking an ID, while Kometa's `defaults/both/based.yml` carries keyword NAMES (`based on book`, `based on novel`, …) and resolves them to ids by searching TMDb at run time. Note what this is *not*: `based.yml` is a FIXED four-collection pack (books, comics, true story, video games), not a per-value enumeration, so the dynamic engine (row 102) is not its blocker and delivering 102 would not close it — which is what the citation said before this row existed. Closing it means a name→id step (TMDb's keyword search, cached, at expansion or resolution time) or transcribing the four id sets from a source that was actually read; a keyword id written from memory resolves to a different keyword and builds a plausible, wrong collection, which is why the pack shipped GATED rather than guessed. The catalog's `content_based_on` preset waits on exactly this (`src/autoposter/collections/catalog.py`, `KEYWORD_RESOLUTION_ROW`) | S — one cached search call, or a verified id transcription | parity-only | 62, 93 |
| 162 | The award year-title space is unaudited at sixteen-ceremony scale | 4c/defaults-catalog wrap row, filed out of 135 rather than duplicating it — row 135 owns the *mechanism*, this owns the *size*. The mechanism is unchanged: `_titles_must_not_collide` (`config/schema.py`) enumerates every built-in title except the dynamic award-year ones, because enumerating those needs the ceremony dataset the validator does not fetch, so an operator definition titled "Oscars Winners 2026" still is not refused at load. What this phase changed is the surface that gap covers. The validator's own docstring now reads "true of the Oscars and, now that the award presets ship, of all sixteen ceremonies alike": each registered ceremony mints one unenumerable year title per year its dataset carries, so the space an operator title can silently land on grew roughly sixteen-fold with no new code and no new test. State what IS covered, so this row is not read as wider than it is: **ceremony-versus-ceremony collisions cannot happen** — `tests/test_collection_awards.py::test_every_events_year_pattern_only_matches_its_own_year_title` asserts pairwise over `EVENTS` that each ceremony's `year_pattern` matches its own year title and no other's, which is also what keeps the guarded delete sweep from eating a neighbour's collections. The audit this row wants is of the year-title space itself: how many titles the sixteen datasets actually mint, whether any two ceremonies' titles are one string apart under an operator's plausible spelling, and whether the config-time enumeration row 135 asks for is affordable at that size (sixteen event fetches inside config validation is what row 151 already says it costs) | S–M — enumerate the sixteen datasets once, then decide 135 with a number in hand | parity-only (nothing observed; the Oscars are the only ceremony on without an opt-in preset) | 135, 82 |
| 163 | Per-chart opt-ins for the IMDb chart bundle | 4c/defaults-catalog wrap row, filed as an **option, not a commitment**. The catalog is asymmetric on charts and says so: the eight TMDb charts are eight separate presets, while the three IMDb charts (Popular, Top 250, Lowest Rated) are a single setting-backed row keyed on `collections.charts` — the boolean that predates the catalog and already builds all three together. Splitting them was considered and refused at the time: a preset key sitting beside `collections.charts` would be a *second* way to build "IMDb Popular", and two built-in definitions sharing a title in one library is precisely the collision `_titles_must_not_collide` cannot catch, since it compares operator definitions against the built-ins and never the built-ins against each other (`tests/test_collection_catalog.py::test_every_ready_preset_at_once_never_builds_one_title_twice` is what makes that reasoning enforced rather than remembered). So this is not "add three catalog rows": it is three per-chart settings replacing one boolean, a migration for every config carrying `collections.charts: true` today, and only then the rows. Worth doing if an operator ever wants two of the three and not the third; there is no evidence yet that anyone does | S–M — three settings plus a migration, then three catalog rows | exercised (`collections.charts` is on and all three are built) | 93 |
| 164 | Tracearr recently-added builder | row79/tracearr wrap row, filed rather than built — a deliberately different family from the one row 79 delivered. Row 79 had to compute its ranking because Tracearr publishes none; `GET /api/v2/public/recently-added` needs no aggregation at all: one cursor-paginated endpoint ordered by server-reported added date, filterable by `server_id`, `library_id`, `media_type` and `include_removed` (`docs/research/tracearr-api-harvest.md`, the row-79 mapping table calls it the one **direct match**). Strictly cheaper than the history walk it would sit beside, and it inherits row 79's whole transport — `src/autoposter/providers/tracearr.py`, `SourceClients.tracearr`, `TracearrConfig`, `AUTOPOSTER_TRACEARR_APIKEY` — so the new work is one client method, one builder, one params model and the catalog rows. Two traps row 79 already paid for and this row inherits verbatim: `RecentlyAddedRecord` rows for episodes carry EPISODE-level `imdb_id`/`tmdb_id`/`tvdb_id` and reach the show only through `grandparent_rating_key` or a `/media/{uuid}` call, and `media_id` may be null. Kometa's nearest equivalent is its Tautulli/recently-added chart family, so the rows would be NOT_KOMETA for the same reason row 79's two are. Not started: nothing in the shipped code anticipates it beyond the client it would reuse | S — one endpoint, one builder | parity-only | 79, 17 |
| 165 | Scheduler head-of-line blocking and the Plex walk's timeout discipline | Filed by the row79/tracearr wrap from two production incidents on 2026-08-25, both of which this branch answered with *visibility* rather than with containment. (1) **The scheduler loop is sequential.** `Scheduler.run` (`src/autoposter/scheduler/core.py:132-143`) awaits `_maybe_run` for each job in turn before it sleeps, so a job that hangs inside `job.run` never returns and every other job's *claim* is never even attempted — no error, no log line, no `scheduled_runs` update, and a dashboard that reads idle rather than stuck. The claim is not a lease either (`claim_due`'s own docstring says so), so the row it holds falls due again while it is still running. The production trigger was a pod whose scheduler stopped claiming anything and stayed that way; **that stall is still unexplained** — the pod was replaced before anything was captured from it and the evidence died with it, so this row starts from the mechanism rather than from a diagnosis. (2) **The Plex walk has no per-request budget of its own.** `PlexServer` is constructed with no `timeout` argument in both `main.py` and `collections/__main__.py`, so every probe falls back to `plexapi.TIMEOUT` (30 s) and the prune's ~15k-row walk carries no overall deadline at all — one wedged socket extends the pass without bound, which is exactly the input the sequential loop above turns into a dead scheduler. What already shipped in response, and why it is not enough: a claim-time `scheduler: %s started` INFO line (`scheduler/core.py:161`), the derived `running` / `interrupted` states on the dashboard's scheduled-job rows (`api/snapshots.py::_run_status`, decided against the process boot instant rather than guessed from a duration), and the boot-time database-vs-process clock delta line. All three make a stuck pass *visible*; none of them bound it. The work here is the bounding: a per-job watchdog or timeout that lets the loop move on, an explicit request timeout on the Plex session, and a decision about whether the loop should run its jobs concurrently at all | M — a watchdog, a timeout, and one decision about the loop | exercised (the scheduler runs every pass in this deployment) | — |
| 166 | Settings editor cannot type a null-valued config field | Filed by the row79/tracearr wrap after an attempted copy-only fix turned out to need the API to change. `Field` (`frontend/src/pages/Settings.tsx:217-288`) picks its widget off the *served* value's runtime type, which is deliberate — it keeps a schema the page has never seen safe, and stops a cleared number input turning into a text one mid-edit. The cost is that a field whose value is `null` has no type to read, so it falls through to the read-only rendering and shows a bare muted `(not set)` beside siblings that are editable: `version_check.harbor_url` is the live example, sitting between an editable `enabled`, `project` and `repository`. Nothing on the page says why, and the row is **not** redacted — `GET /api/config`'s `redacted_paths` names only `notifications.url` — so a hint reading "set in the YAML, not editable here" would be false the moment an operator sets the value and the same row starts rendering as a text input. The honest fix is a type the response carries: have `GET /api/config` advertise each scalar path's schema type (the Pydantic model already knows it) and have `Field` pick the widget from that rather than from the value, which also retires the "a list of objects has no editor" fallback. Not started | S–M — one API field plus the widget switch | usability (an operator who can set every neighbouring field but not this one) | — |
| 167 | Example config has no REVERSE schema check | Filed by the row79/tracearr wrap's Task 4 review (concern 1). `tests/test_example_config_matches_schema.py` has three tests and all three walk example → schema — each of the example's keys is looked up in `Config.model_fields` — so a brand-new top-level config section can ship with no line in `config/autoposter.example.yaml` and the suite stays green; nothing asserts the reverse. The motivating near-miss is this branch's own: the mutation proof that removed the whole `tracearr:` block reddened exactly one test in the entire suite, and only because that test names `tracearr` by hand. The reviewer's sizing datum: a section-level reverse test — `Config`'s top-level fields minus the example's top-level keys must be empty — would pass **today** with one documented exemption: that difference is exactly `{version}` (`config/schema.py:1042-1079` vs the example's top-level keys), the derived render-version hash, never operator-set. Keep it scoped to *section* level only; a subkey-level version would red immediately on the many optional subkeys the example deliberately omits, which is a different, larger decision. Not started | S — ~8 lines plus one documented exemption | hygiene (a new top-level section can ship undocumented and the suite stays green) | — |
| 168 | `imdb_watchlist` still emits unfiltered ids, including episodes | Filed by the uni-fix review (minor 3) after the universe fix gave `imdb_list` a keep-set filter and left `imdb_watchlist` alone. `builders/imdb_lists.py`'s module docstring says why: "a watchlist is one person's own shortlist, not a curated bulk list, and it has never been the shape this fixes" — a claim about whose data it is, not about what an id *can* mean, which is the principle the `imdb_list` fix is built on. An episode's `tt` id is unresolvable everywhere Plex looks (episodes are filed under their show's guid, never their own), regardless of which list carried it, so a watchlist that comes to hold an episode reproduces exactly the "500 unresolved ids, Show-library row resolves nothing" shape row 164's sibling fix closed for `imdb_list` — the two halves of one module answering the same question differently. Nothing observed forces this today: a personal watchlist built by hand rarely holds a bare episode the way a public "complete series" list does, which is why this is filed rather than fixed. The fix, when a watchlist is observed carrying one, is the same keep-set filter `imdb_list` already has: `fetch_watchlist` (`collections/imdb_lists.py:366`) would need to request `titleType { id }` the way `fetch_list` does, and `ImdbWatchlistBuilder.build` would filter through `_KEEP[ctx.library_type]` the same way `ImdbListBuilder.build` does | S — the same keep-set filter, once a watchlist is observed carrying episodes | parity-only | 164 |

---

## Part 2 — Roadmap

Phases continue the project's numbering. Each is a shippable PR-sized unit producing
working software. "Size" compares to shipped phases. Verification rows land inside the
phase that needs their answer, as that phase's first step.

### Phase 5 — cutover

#### 5a — Outbound notifications and webhooks

**Goal:** the n8n chain survives cutover. A configured URL receives a POST when a
run/sweep/job batch completes; the payload contract is versioned and documented.
**Closes:** rows 18, 19, 20 (20 may slip to 15 without harm — Discord is unconfigured
today).
**Size:** small — about half of phase 4a. One config block, one HTTP call with retry,
event hooks at run boundaries.
**Risks:** the payload contract. n8n today receives whatever Apprise sends and the
downstream flow was built against that; the failure this phase must prevent is a
webhook that fires but carries a shape the flow silently drops. First step: read the
n8n flow's trigger node (or ask the user for it) and design the payload against what it
actually consumes. Second: the event taxonomy (row 19) is easy to over-design — ship
run_end first, grow the rest behind the same dispatcher.
**Testable when shipped:** a sweep against a webhook-catcher fixture produces the
documented payload; the real n8n flow fires in a rehearsal.

#### 5b — Cutover verification sweep

**Goal:** every unknown that could bite at cutover is answered with evidence, and any
trivial exercised gap it exposes is closed in the same PR.
**Closes:** rows 1–5, 8, 13, 14, 15 (the exercised-config unknowns). Rows 6, 7, 9–12,
16 are parity-only unknowns and may be answered here cheaply or deferred to phase 7a.
Row 4 matters most: collection posters are the one *exercised* rendering path whose
parity is unconfirmed.
**Size:** small. Mostly reading code and comparing live output; fixes only where an
answer reveals an exercised gap.
**Risks:** an answer might not be small — if row 14 (multiple versions) reveals the
per-item model misses secondary versions, that becomes its own scoped follow-up, not a
rushed patch here.
**Testable when shipped:** each row's answer is recorded in this document's margin (or
a follow-up gap is filed); collection-poster output is pixel-compared against the
Posterizarr oracle.

**Cutover happens here** — with 5a shipped, 5b green, and the full-pass trigger done:
run adoption `--dry-run`, review, repoint webhooks, stop Posterizarr and Kometa. Every
later phase is post-cutover build-out; nothing below blocks turning the old tools off.

#### 5c — Tracearr webhook intake

**Goal:** Tracearr's outbound custom webhooks become a trigger source, replacing the
role Tautulli's notification agent held. Not cutover-blocking: Radarr/Sonarr webhooks
already cover import-driven triggering; this adds watch-activity-driven triggers.
**Closes:** rows 17, 21.
**Size:** small — one route, payload mapping into the existing normalize path.
**Risks:** payload shapes are unverified until the user's instance is live. First step
is row 17: harvest real webhook payloads (and API responses, banked for 8c) from the
deployed instance, in the project's harvest-the-oracle style. Do not write a schema
before that. Note the relationship without conflating: Tracearr's webhooks may
*eventually* also serve the role the n8n chain does, but 5a is what cutover depends on.
**Testable when shipped:** a captured real payload replayed at `/webhook/tracearr`
enqueues the right item.

### Phase 6 — the spec's own UI debts

Sequenced before the parity build-out because the design spec already owes them (§6),
they are moderate in size, and 6d is a hard dependency of phase 11.

#### 6a — Collections UI, search, compare, override actions

**Goal:** the Collections page shows member counts, last diff result (+3/−1), next
refresh, and a "diff now" button; the library browser gets its search box; item detail
compares all art kinds and can clear a manual override.
**Closes:** rows 22, 23, 24, and the collections half of the §6 remainder (member
counts, diff-now — carried in the spec's §6a "still owed" list).
**Size:** about phase 4c — a few endpoints plus UI on two existing pages.
**Risks:** none structural; the endpoints are additive. The known trap is documented in
§6a already: artwork must go through `apiFetchImage`, never a bare `src`.
**Testable when shipped:** diff-now on a chart collection applies deltas and the page
shows the result without a reload.

#### 6b — WebSocket

**Goal:** the dashboard stops polling; a live log tail view exists.
**Closes:** row 72 and the Posterizarr "live log viewer" gap.
**Size:** small — `client.ts` was written so the swap touches one module.
**Risks:** auth for the socket (the session is a bearer header; sockets don't send
headers from browsers uniformly — ticket-based connect is the likely shape). Name it in
the PR rather than discovering it at review.
**Testable when shipped:** events appear on the dashboard without a poll cycle; a
worker log line reaches the tail view.
**Delivered (6b):** as fetch-based NDJSON, not WebSocket — the named auth risk only
exists for a browser `WebSocket`, and the logs page had already established the
authenticated NDJSON pattern this reuses. The dashboard consumes
`GET /api/dashboard/stream`; a per-process broadcaster polls the database only while
subscribers exist (replica-correct, unlike in-process fanout) and pushes on change.
The log tail half shipped earlier with the logs page.

#### 6c — Config editor with hot-reload and impact preview

**Goal:** schema-validated config editing in the UI, hot-reload on save, config-version
bump marking affected fingerprints stale, and the impact preview ("this change would
re-render ~2,400 items") with apply-now vs regenerate-later.
**Closes:** rows 94 and 54 (schedule editing rides on the same write path).
**Size:** large — comparable to phase 2. The write endpoint is easy; hot-reload
correctness and the preview are not.
**Risks:** three, plainly: (1) hot-reload while workers hold the old config — needs a
config-generation handoff, not in-place mutation; (2) the impact preview must recompute
fingerprints without side effects — a preview that enqueues renders is a data-loss
class bug; (3) partial validity — reject bad config with line-level errors, never
half-apply (spec §7 already promises this).
**Testable when shipped:** editing a badge colour previews the affected-item count,
applying re-renders exactly those items, and a syntactically invalid save changes
nothing.
**Delivered (6c):** as a DB-overrides layer over the git-owned ConfigMap (user-decided;
the mounted file is never written), with a ConfigHolder generation swap — every
per-use-read setting hot-reloads, scheduler cadences resolve live per poll, and the
genuinely-frozen startup captures carry "restart to apply" flags. All three risks
closed as prescribed: one-deref-per-job handoff, a read-only fingerprint-recompute
preview (mutation-proven side-effect-free), and full-depth validate-before-anything
saves. Two acceptance substitutions, both honest: badge colours are not configurable
yet (row 97), so the previewed edit is an artwork text setting; and "exactly those
items" is the whole fingerprinted library for any artwork edit, because
`config.version` hashes the artwork section whole — per-kind confinement is row 111.

#### 6d — Provider-candidate picker and logo browser

**Goal:** for any item and art kind, search all providers, browse candidates, pick one;
the choice persists as a manual override. Logos included (Posterizarr's "Browse
Logos").
**Closes:** row 73.
**Size:** about phase 4c.
**Risks:** provider rate budgets — a browse page that fans out to four providers on
every open needs the cache in front of it (the design's Fanart courtesy note applies).
**Testable when shipped:** picking a candidate re-renders with that base and survives a
re-run without being overwritten.

#### 6e — Manual mode and testing mode

**Goal:** build one styled artifact of any kind from an arbitrary file or URL (API +
UI), and render config sample sheets (short/medium/long text, every kind) on demand.
**Closes:** rows 74, 75.
**Size:** about phase 4c — both reuse the render path end to end.
**Risks:** URL fetching is a server-side request forgery surface — allowlist schemes,
no redirects into private ranges; the artwork-endpoint hardening precedent in §6a sets
the bar.
**Testable when shipped:** a URL becomes a styled collection card; sample sheets change
when config changes.
**Delivered (6e):** the SSRF guard (`net/guard.py`) is the phase's security core —
scheme allowlist, redirects off with ≤3 manually re-validated hops, every resolved
address checked against private/loopback/link-local/reserved/multicast, the 6d body
cap + decode verify; an adversarial review confirmed no bypass (decimal/octal/mapped
IP forms fold through `getaddrinfo`, the same resolver httpx connects with); DNS
rebinding and NAT64/6to4 literals are the documented residuals. Operator-supplied
URLs never reach a response, event row, or log line. `compose_styled` was extracted
byte-parity-proven (identical golden node-ids before/after). Honest scope: collection
posters apply as-is (row 105 not smuggled in); local source is the mount path, not an
upload endpoint (follow-up row).

### Phase 7 — render long tail and artwork modes

#### 7a — Render and selection long tail

**Goal:** close the pile of small parity-only rendering/selection toggles in one
sweep, each guarded by a golden-image or unit test.
**Closes:** rows 6, 7, 9–12, 16 (any deferred from 5b), 38–48, 50; plus 25–37, 49, 51,
52 if capacity allows or as a 7a/7b split by review size.
**Size:** small–medium in aggregate; each item is hours, not days. Ship in two or three
PRs rather than one unreviewable one.
**Risks:** none individually; collectively, config-schema sprawl — group new keys under
existing blocks rather than minting one top-level key per toggle.
**Testable when shipped:** each toggle has a test that renders/selects differently with
it on.

#### 7b — Artwork modes

**Goal:** the bulk artwork operations Posterizarr ships as CLI modes, as jobs
triggerable from the UI: backup, restore (with filters), poster reset, remove-overlays
revert, logo updater + revert. A "run modes" UI section fronts them.
**Closes:** rows 65, 66, 67, 71, 76, 77, and the Posterizarr "Run Modes tab" partial.
**Size:** medium — five operations sharing job-queue plumbing.
**Risks:** these are the destructive ops. Every one needs a dry-run and a confirmation
gate; restore must never race the live pipeline (pause-or-fence decision needed);
poster reset interacts with the §6a orphaned-uploads reality — resetting selects
different art but cannot delete uploads, say so in the UI.
**Testable when shipped:** backup → wipe a test library's art → restore round-trips;
revert restores clean bases; all against the fake Plex server.

### Phase 8 — builder engine and list builders

#### 8a — Builder engine core

**Goal:** the generic abstraction the three hardcoded sources (charts, awards, CS
buckets) never needed: a builder produces an ordered external-ID list; the engine
handles config schema, registry, resolution to owned Plex items, sync/append, limit,
ordering, dry-run, and failure containment (failed source ⇒ no changes — the shipped
invariant, preserved).
**Decomposition (the first monster):**
1. Builder interface + registry + typed per-collection config (capability parity:
   config, not YAML compatibility).
2. Port the three shipped sources onto it with zero behaviour change — golden test:
   nightly diff output identical before/after.
3. Add sync/append (row 25), limit (26), dry-run (27), ordering application (per row
   2's answer).
4. Collection-level settings that ride along: rows 28, 29, 30, 68, 69, 70, 92 (per-library
   overrides where builders need them).
**Closes:** rows 95, 25–30, 68–70; 92 partially.
**Size:** large — phase-3 scale. The port-with-zero-change step is the insurance.
**Risks:** over-abstracting. The interface should be exactly what step 2's three real
sources plus row 58's MDBList need — resist designing for Trakt-shaped sources that are
out of scope.
**Testable when shipped:** the three existing collections run through the engine with
byte-identical diffs; one new trivial builder (`plex_id`) works end to end.
**Delivered (8a):** the golden gate is the phase's spine — the three shipped sources
(Common Sense buckets, IMDb charts, Oscars) were ported to the engine behind a
byte-identical-output test (`tests/test_builder_port_golden.py`) pinning every action
string and the resulting section state, captured against the pre-port code, before any
of the rewrite landed. The other spine piece is opt-in, guarded deletes: the automatic
sweep (`collections/engine.py::_sweep`) only removes a collection that carries the
ownership label AND a `managed_collections` row, never over a protected label, off by
default (`delete_unconfigured`) and refused outright past `max_deletes`; the same guards
front the new operator-triggered `POST /api/collections/ops/delete`, which additionally
requires `confirm: true`. Closes rows 95, 25–30, 68–70, 104, 115, 124; row 92 partially
(per-definition `libraries:` targeting plus each definition's own `params:` — nothing
broader). Six follow-ups filed: rows 134–139.

#### 8b — List builders, wave 1

**Goal:** the thin builders: TMDb charts and explicit/simple builders, MDBList lists,
TVDb lists, IMDb list/id/watchlist, Plex trivials, text_file, Arr taglists,
plex_watchlist (pending the row-64 confirmation).
**Closes:** rows 56–64.
**Size:** medium — each builder is small once 8a exists; the volume is the size.
**Risks:** IMDb list/watchlist parsing is scraping-adjacent even on GraphQL — pin with
recorded fixtures so upstream drift fails loudly.
**Testable when shipped:** each builder has a recorded-fixture test; a config-defined
TMDb-list collection appears in Plex and diffs nightly.

**Delivered, with one note.** Eight of the nine builder families shipped: TMDb charts
and explicit/simple builders, MDBList lists, TVDb lists, IMDb list/watchlist, the Plex
trivials, `text_file`, the Arr all/taglist forms, and `plex_watchlist`. Rows 57–64 are
answered; row 56 is answered **partially**. The ninth, `plex_pilots`, is deferred to
row 143 — not for effort but because its premise fails against the shipped contract:
the owned index and `BuilderResult` name library items, so an episode cannot be handed
back as a member at all. The traversal that would answer it is banked in
`.superpowers/sdd/task-5-report.md`; the missing piece is an episode-level resolution
contract, which is also what row 88 needs. Five follow-ups filed: rows 143–147.

Two things worth carrying forward from the phase. The IMDb queries were live-verified
on 2026-08-25 and fixture-pinned, and public watchlists are the permanent ceiling —
`Query.user` takes no id, so there is no anonymous route to a private one. And every
mixed-media list source (MDBList, TVDb, TMDb) had to drop the other media type rather
than pass it through, because each pair of id spaces shares one namespace: the failure
mode is not a missing member but a plausible wrong one.

#### 8c — TMDb discover, IMDb search/awards, Tracearr builders

**Goal:** the query-shaped builders: `tmdb_discover` (full parameter surface),
`imdb_search`, award events beyond Oscars, person filmography builders, and Tracearr
most-watched/popular from its REST API (per the row-17 harvest).
**Closes:** rows 79–83.
**Size:** medium — discover's parameter matrix is wide but mechanical; award events
need per-event source verification.
**Risks:** award events: IMDb's event data varies per event/year — treat each event as
its own vertical slice with an oracle comparison against Kometa's output for that
event, not one generic "awards" claim. Tracearr: the API is read-only and documented
in-instance; anything its docs don't show is out until observed.
**Testable when shipped:** a discover-defined collection matches the same query run
against TMDb by hand; one non-Oscars award collection matches Kometa's members for the
same event/year.

**Delivered (8c), with one deferral.** Four of the five closed: `tmdb_discover` (row
80), `imdb_search` (row 81), award events beyond Oscars (row 82), and the static half
of person builders (row 83). Tracearr most-watched/popular (row 79) is **deferred** —
blocked by row 17's live-instance harvest, which was never going to complete inside
this phase — and the phase shipped without it. Each of the four closed rows carries a
genuine oracle rather than a documentation claim: row 80's 48-row matrix asserts its
own row-count checksum (21/16/11) rather than only being documented; row 81's 39
vocabulary entries are each proven real by a non-zero total from a live probe, not
assumed from IMDb's docs, and the `POPULARITY` sort direction was caught by the same
live walk rather than shipped on the plausible-but-wrong reading; row 82's Golden
Globes members matched Kometa's own transcribed resolution algorithm exactly, in
order, on the first run, over Kometa's own upstream data. Five follow-ups filed: rows
148–152.

### Phase 9 — the query language (the second monster)

#### 9a — Filter model, tier 1

**Goal:** a typed predicate model (attribute, operator, value, and/or nesting) usable
as post-builder `filters:` — evaluated client-side against facts and Plex metadata.
Tier 1 = the ~15 attributes the defaults and common configs actually use (genre, year,
resolution, rating values, content rating, language, label, added/release dates,
runtime, studio/network, collection membership).
**Closes:** row 96 (tier 1 of it).
**Size:** medium.
**Risks:** the model is shared with 9b — get the operator/modifier semantics
(`.not/.regex/.gt/.before/…`) right here, with a table-driven test per operator, or 9b
inherits the bugs.
**Testable when shipped:** a filtered builder collection excludes exactly the items the
same filter excludes in Kometa (oracle comparison on one library).

**DELIVERED, with notes (2026-08-25).** The acceptance criterion above was met
literally: `tests/test_collection_filter_oracle.py` evaluates two filter configs
over a 120-item library and asserts our members are, exactly and in order, the
members **Kometa 2.4.8's own filter code** produces for the same configs over the
same items — its `is_date_filter`/`is_number_filter`/`is_string_filter`,
`check_filter` and `check_filters` fetched and transcribed standalone, with
nothing from this repository imported into the oracle. The notes, all of which
row 96 carries in full:

- **Nine of the fifteen tier-1 attributes ship**, not fifteen. Six drop to tier 2
  (row 155) on the read-only production probe's data, because the Plex section
  listing does not carry them completely enough to filter on — `genre` most
  surprisingly of all, since the listing *does* send `<Genre>` and simply
  truncates it to two tags per item. The plan's no-silent-N+1 rule governed;
  the roadmap's tier-1 naming yielded to it, deliberately and with the numbers
  recorded.
- **The oracle was red first, in four places**, each an unmarked transcription
  judgement, each adjudicated in Kometa's favour with our code changed: the
  relative date window's invented upper bound, `duration` rounding, a
  case-insensitive `.regex`, and date-granular comparison. One of the four had
  been marked `SETTLED-BY-REVIEW` in a previous fix round on a *recollection* of
  Kometa's comparison that turns out not to exist in its source — which is the
  strongest argument this phase produced for fetching the file.
- **`.gte`/`.lte` on dates were removed** rather than kept: Kometa accepts them
  and silently rewrites them to the strict forms, so the same spelling meant two
  memberships. Row 157 files a real inclusive operator under a name Kometa does
  not use.
- **Row 154 widened** as a consequence of the moment-granular fix, and is now
  exercised rather than theoretical.

## Notes for 9b

Banked here by 9a rather than left in a task report, because 9b re-reads this
section and not those:

1. **`includeCollections=1` is a result-set footgun, not an enrichment.** The
   name reads as "also send each item's `<Collection>` children"; what it
   actually does is MIX Collection objects into the result set, changing what
   `/library/sections/N/all` returns. 9a's probe hit this while trying to rescue
   the `collection` attribute (`.superpowers/sdd/task-2-report.md`). Any 9b
   search that reaches for it to enrich a listing will silently change the
   thing it is enriching.
2. **Plex search's own relative-date syntax narrows 9a's two `None`s.**
   `PLEXAPI_EQUIVALENT` maps every tier-1 operator onto a `plexapi.base.OPERATORS`
   key except `(date, eq)` and `(date, not)` — the "in the last N days" window —
   which are `None` because plexapi's client-side table is all absolute
   comparisons. That is true of the *table* and not of the *server*: Kometa's own
   `validate_attribute` (`modules/builder.py:4446-4452`) passes a bare date value
   to a Plex search as `f"{n}{mod}"` where `mod` is one of `s/m/h/d/w/o/y`
   (seconds…years, `modules/plex.py:307`), i.e. Plex accepts `30d` natively on a
   date field. So 9b's translation of a relative window is a server-side
   spelling, not an unsupported case, and the two `None`s should be read as
   "plexapi's client-side table has no key", not "Plex cannot do this".
3. **The three-root transport generalisation is already done.** 8b's IMDb
   transport was generalised for a second root in 8c and now serves three
   (list, watchlist, advanced search) under
   `src/autoposter/collections/imdb_lists.py` — the
   module name under-describes it, which is row 152. 9b's Plex-search work needs
   no equivalent groundwork on that side; what it does need is 9a's
   `FILTER_ATTRIBUTES` extended rather than duplicated, since rows 96 and 101
   explicitly share one table.

#### 9b — Plex search DSL and sort matrix

**Goal:** `plex_search` as a builder: the full attribute matrix translated to Plex
query parameters, sorts, limits, and/or composition.
**Decomposition:**
1. Attribute inventory: enumerate all ~60 from Kometa's search/sort pages into a typed
   table (attribute → Plex field → type → allowed modifiers → item types). The table is
   the deliverable reviewers check; code generates from it.
2. Translation layer: predicate model (from 9a) → Plex `/search` query params, per
   attribute type (tag, number, date, string, boolean).
3. Sort matrix + limits.
4. Tier the delivery: tier 1 attributes first (shippable alone), long tail in follow-up
   PRs against the same table.
**Closes:** rows 101 and the rest of 96 (the two share the attribute table).
**Size:** **XL — this is multi-week even fully parallelised**; the long tail of
attributes is mechanical but each needs a live-Plex verification because Plex's query
params are underdocumented.
**Risks:** silent wrongness — a mistranslated attribute returns a plausible-but-wrong
item set. Mitigation: every attribute's test compares against a hand-verified Plex
query on the dev server, not against expectations written from docs.
**Testable when shipped:** per-attribute round-trip tests; a `plex_search` collection
reproduces an equivalent hand-built Plex filter exactly.

#### 9c — Native smart collections

**Goal:** `smart_filter` (Plex-maintained smart collections from a translated filter)
and `smart_label`; migrate CS buckets to smart if row 1's answer says Kometa's were.
**Closes:** rows 90, 91 (`plex_collectionless` lands here — it needs the membership map
this phase builds).
**Size:** medium.
**Risks:** smart collections are owned by Plex after creation — the reconciler must
treat them as fire-and-forget definitions, not diff targets; mixing the two models on
one collection would fight Plex nightly.
**Testable when shipped:** a smart collection updates itself when a new item matches,
with no autoposter run in between.

### Phase 10 — dynamic collections (the third monster)

#### 10a — Dynamic collections engine

**Goal:** the generic per-value engine that `buckets.py` is one hardcoded instance of.
**Decomposition:**
1. Value enumeration per type (library scan for genre/year/decade/country/resolution/
   studio/network/edition/languages/origin_country — mostly one Plex query each).
2. Key handling: include/exclude, addons merge (generalising the CS addons logic),
   key_name_override.
3. Titling: title_format, remove_prefix/suffix, other_name bucket.
4. Lifecycle: create per key, sync membership (via 8a), delete-below-minimum,
   test mode.
5. Port CS content-rating buckets onto it with zero behaviour change (same insurance as
   8a step 2).
**Closes:** row 102.
**Size:** large — phase-3 scale; the port step keeps it honest.
**Risks:** unbounded fan-out — `year` on a 70-year library creates 70 collections;
per-type defaults (minimum, include lists) must make the default outcome sane, because
the user configures outcomes, not YAML.
**Testable when shipped:** CS buckets byte-identical through the engine; a `decade`
dynamic config creates the expected set on the dev library.

#### 10b — Dynamic packs and defaults equivalents

**Goal:** shipped config presets reproducing the in-scope Kometa default families:
genre, studio, streaming (TMDB watch providers), resolution, aspect, year, decade,
country, region, continent, franchise (tmdb_collection dynamic), universe/based (via
MDBList-hosted lists), seasonal (date-windowed via row 70), content-rating regionals,
separators, chart packs (basic/tmdb).
**Closes:** rows 93, 49, and the `number`/`custom` dynamic types.
**Scope narrowed by 4c/defaults-catalog:** the *catalog half* of row 93 shipped early —
the preset table (`collections/catalog.py`), the `collections.presets` selection, the
pure server-side expansion in `default_definitions`, the `GET /api/collections/catalog`
endpoint and the tabbed picker all exist, with 28 families READY and 18 GATED rows that
already name the roadmap row each waits on. 10b inherits that machinery and owns the
**packs-content half alone**: filling in the eighteen gated families once their blockers
(102, 96/155, 160, 161) land, plus the regionals and separators this section lists. A
gated row becoming ready is one catalog row flipped from GATED to READY plus its
definitions — not new picker, config or endpoint work.
**Size:** medium–large — presets are cheap individually; streaming and franchise need
their data plumbing (watch providers, TMDb collection IDs). Streaming and the eight TMDb
chart packs are already done, which takes their plumbing off this phase's bill.
**Risks:** presets are opinions — each pack ships disabled by default with a one-line
enable, so parity is available without foisting 40 collections on the library. (The
catalog holds that line: `presets:` defaults to empty and every row is opt-in.)
**Testable when shipped:** enabling a pack on the dev library produces collections
matching Kometa's for the same library, sampled per pack.

#### 10c — People

**Goal:** person-driven collections: dynamic `actor/director/writer/producer` types
with appearance thresholds, `tmdb_popular_people`, `tmdb_person` summaries/posters,
birthday/deathday gating.
**Closes:** row 83's dynamic half (builders landed in 8c) and the person dynamic types
of row 102.
**Size:** medium.
**Risks:** person enumeration is the expensive scan (every credit of every item) —
needs caching in `item_facts` or a dedicated table, decided at design time, not
discovered at review.
**Testable when shipped:** an actor pack with threshold N creates exactly the actors
with ≥N appearances.

### Phase 11 — Action Center (the fourth monster)

#### 11a — Asset-quality flags and backfill

**Goal:** the pipeline records *why* each chosen asset is imperfect, at selection time:
language rank achieved vs preferred, provider rank, textless-preference miss,
logo-to-text fallback taken, text truncation at render, still-missing. Plus a backfill
job scoring the existing library.
**Closes:** the tracking half of row 103.
**Size:** medium — columns on `renders` + selection-path bookkeeping + one sweep job.
**Risks:** the taxonomy is the design work; changing it later means re-backfilling.
Keep flags factual ("selected language = en, preferred = xx"), not judgemental
("bad") — the queue decides what's worth reviewing, data stays stable.
**Testable when shipped:** a forced en-only render on an xx-preferring config produces
the language flag; backfill counts are visible via the API.

#### 11b — Action Center review queue and bulk actions

**Goal:** the curation UI: filterable queue of imperfect assets (by flag, library,
kind), per-item resolve/replace (via the 6d picker)/delete, bulk operations, and
dismissal that sticks until the underlying facts change.
**Closes:** the rest of row 103.
**Size:** large — the largest single UI unit in the roadmap; bigger than 4c.
**Risks:** bulk operations against providers must respect the same rate budgets as the
pipeline (one "re-search all 400 flagged items" click is a burst); dismissals need a
fingerprint-style identity so a re-render doesn't resurrect dismissed rows.
**Testable when shipped:** a flagged item replaced through the queue re-renders and
leaves the queue; a dismissed one stays dismissed across a sweep.

### Phase 12 — overlays

#### 12a — Overlay families

**Goal:** the missing default families, each as fact-plumbing + a badge spec: aspect,
language_count (Dual/Multi audio-sub), content-rating regionals (US/UK/DE/AU/NZ),
direct_play, network/studio wordmarks, ribbon (list-membership driven, via 8a),
status (Airing/Returning/Canceled/Ended, TVDb), streaming (TMDB watch providers),
versions.
**Closes:** row 100.
**Size:** large in aggregate; each family is S–M and independently shippable — ship in
family-sized PRs against the pixel-oracle harness that validated the shipped nine.
**Risks:** data sources first: status needs a TVDb status fact, streaming needs
watch-provider facts with region, ribbon needs collection membership as a fact. A
family without its fact plumbed is a badge that renders stale data forever.
**Testable when shipped:** per-family pixel comparison against Kometa oracle output,
same as `kometa-overlays.md` did for the first nine.

#### 12b — Custom overlay mechanics

**Goal:** user-defined overlays: arbitrary images (file/url through 55's management
UI), templated text with the `<<variable>>`/modifier set, configurable positioning and
backdrops (generalising `badges/geometry.py`'s fixed values), the generic queue engine,
general cross-overlay suppression.
**Closes:** rows 97, 50 (if not landed in 7a), 41's overlay half.
**Size:** large — this converts `badges/` from a fixed spec into an engine with the
fixed spec as its first consumer.
**Risks:** regression risk to the shipped nine badges is the whole risk — the port must
be fingerprint-neutral (identical output ⇒ identical fingerprint ⇒ zero re-renders on
deploy). Golden images gate the PR.
**Testable when shipped:** shipped badges byte-identical through the engine; a
user-defined text overlay with a runtime variable renders per the Kometa oracle.

### Phase 13 — playlists

**Goal:** playlist definitions on the builder engine: any single builder feeds a
playlist, order inherited; `libraries` selection; `sync_to_users`/`exclude_users`;
delete + report; a starter preset.
**Closes:** row 98.
**Size:** medium–large — the builder engine does the hard part; per-user sync is the
new ground (server users enumeration, per-user copies).
**Risks:** per-user sync multiplies writes (N users × M playlists) and Plex playlist
APIs are per-account — needs the same batching discipline as `plex_bulk_edit_batch_size`.
**Testable when shipped:** a chart-built playlist appears for two users and updates on
the nightly diff.

### Phase 14 — metadata completion

#### 14a — Metadata operations completion

**Goal:** the remaining mass-op surface in scope: TVDb/IMDb-dataset sources (row 84),
user rating/original title/added-at ops (32, 33), mappers (34), lock/unlock/remove/
reset controls (87, after row 3's verification), collection ops (28 remainder),
metadata backup (86), parental labels (85 — with its feasibility risk stated).
**Closes:** rows 32–34, 84–87.
**Size:** medium.
**Risks:** row 85 may be infeasible without scraping beyond the sanctioned IMDb
surface — timebox a feasibility spike; if it fails, the row moves to Appendix A with
that finding rather than silently disappearing.
**Testable when shipped:** each op has a fake-Plex round-trip test; backup exports and
re-imports a test library's metadata.

#### 14b — Per-item metadata overrides and per-library config

**Goal:** the capability of Kometa's metadata files as autoposter-native config/UI:
per-item (and season/episode) declarative overrides — titles, summaries, ratings,
dates, tags with remove/sync, images, advanced settings — persisted, applied by the
pipeline, and surviving fact refreshes; plus the full per-library override matrix.
**Closes:** rows 99, 92 (remainder).
**Size:** large — a subsystem, though the item-detail page and config editor give it
surfaces to grow from.
**Risks:** precedence is the design problem: manual override > mass op > provider
fact, applied idempotently — the failure to prevent is a nightly op silently undoing a
manual edit. The precedence table is written and reviewed before code.
**Testable when shipped:** a manual title override survives a mass-op sweep and a
fact refresh.

### Phase 15 — operations and observability polish

**Goal:** the remaining operational surface: per-run stats rollups and charts (53),
storage stats + homepage widget endpoints (52), API-key read auth (51), overlay/font
management UI (55), Discord/Apprise formats if deferred from 5a (20), and any 7a
leftovers.
**Closes:** rows 20, 51–53, 55, and stragglers.
**Size:** medium.
**Risks:** none structural.
**Testable when shipped:** a homepage widget renders against the stats endpoint with
an API key.

---

## Appendix A — out of scope

### A.1 — Excluded services, and what re-scoping each would cost

One line each, per the scope decision (new accounts/OAuth are out):

- **Trakt** (charts, lists, userlists, recommendations, boxoffice, sync_to_trakt_list,
  trakt_user_lists dynamic type): OAuth app registration + tokens that expire every 7
  days — a refresh daemon and a re-auth UX before the first builder works.
- **Letterboxd** (lists, discovery, user films/reviews): no public API — scraping with
  breakage risk, plus an account for user-scoped pages.
- **AniDB** (id/relation/tag/popular builders, rating/genre sources): account +
  registered client + notoriously strict rate limits; ban risk on bulk use.
- **AniList** (charts, search, userlist): cheapest of the anime trio — public GraphQL,
  no auth for charts; still a new service with no use in this library.
- **MyAnimeList** (full builder surface): registered API app + OAuth2 localhost flow +
  cached refresh tokens.
- **OMDb** (rating/content-rating sources): free API key, heavily rate-limited tier;
  small integration once keyed.
- **Simkl** (dvd/trending charts): client id + pin flow.
- **Serializd, YamTrack, Floppy** (show/tracked lists): service accounts; thin APIs;
  each is small once an account exists.
- **iCheckMovies, BoxOfficeMojo, StevenLu** (list/chart sources): no accounts — plain
  scrape/JSON, trivial-to-small each — excluded purely as services the user doesn't
  use.
- **mediastinger overlay**: needs mediastinger.com data — a new external dependency
  for one badge.
- **Notifiarr / Gotify / ntfy**: no new accounts for the self-hosted two, but unused
  services; each is one dispatcher target (~hours) on top of row 19 if ever wanted.
- **Tautulli** (intake, builders, chart default): not out-of-scope but *superseded* —
  the user is replacing Tautulli with Tracearr; rows 17/21/79 are the successors.

### A.2 — Superseded by the event-driven design (inventory reasoning carried over)

PowerShell-era mechanics and batch-run machinery the inventories marked as candidates,
adopted here as decided-out with their reasoning:

- **Templates/YAML metaprogramming** (`templates`, `external_templates`, `custom_repo`,
  `git` file sourcing, GitHub token, defaults `template_variables` machinery) — exists
  to make one batch YAML reusable; typed config + code replaces it. The *capabilities*
  the defaults deliver are rows 93/100.
- **Schedule DSL and run machinery** (`run_order`, `run_again(_delay)`,
  `schedule_overlays`, `delete_not_scheduled`, CLI/env run flags, `--run` one-shot,
  watcher-directory triggers, running-file lock, `ForceRunningDeletion`) — batch-run
  scaffolding; the queue, APScheduler, and webhooks replace the mechanism. The two
  real user-facing behaviours inside it — per-collection cadence and date windows —
  are row 70.
- **`reapply_overlays`** — fingerprint gating replaces it. (`remove_overlays` as a
  one-shot revert is real and is row 65.)
- **`mass_poster_update` / `mass_background_update`** — the render pipeline owns
  artwork end to end.
- **Reports** (`save_report`, show_missing/filtered/unmanaged/unconfigured) and CSV
  exports — the DB, REST API, and UI are the replacement surface (row 53 covers the
  per-run rollups worth keeping).
- **Asset lookup machinery** (`asset_folders`, `asset_depth`,
  `dimensional_asset_rename`, `download_url_assets`, `show_missing_*` family) —
  autoposter writes assets rather than hunting for them; only manual-override lookup
  survives (and exists).
- **Cache-builder knobs** (`cache_builders`, `ignore_cache`) — Postgres TTL cache.
- **Self-update machinery** (`AutoUpdatePosterizarr`, `AutoUpdateIM`,
  `magickinstalllocation`), version checks, update banners — image-tag upgrades and
  Flux automation.
- **Platform surface** — Windows/macOS/Unraid support, Tautulli Windows-script mode,
  Gather Logs support zips, `/api/system-info`, Uptime Kuma heartbeat, log rotation
  knobs (`maxLogs`), telemetry — one k8s deployment with Prometheus, `/healthz`, and
  the cluster log pipeline covers all of it.
- **ImageMaid / Overlay Reset / Quickstart companion scripts** — ImageMaid is the
  answer to the §6a orphaned-uploads problem, which is recorded there as a deliberate
  operator decision outside this service; Overlay Reset's capability is row 65;
  Quickstart is an installer.
- **Blueprints, onboarding wizard, Danger Zone (factory reset)** — single known
  deployment; the config file and migrations are the interface.
- **`show_skipped` verbose logging** — different logging model.
- **Kometa integration mount guide** — autoposter replaces Kometa itself.
- **`verify_ssl`, `item_refresh_delay`, `missing_only_released`,
  `only_filter_missing`, `tvdb_language` and sibling knobs** — moot in the
  event-driven design or already covered by typed config defaults.

### A.3 — Non-goals reaffirmed

- **Jellyfin/Emby** (server support, sync modes, `ReplaceThumbwithBackdrop`) —
  disabled in the user's config; declared non-goal, unchanged.
- **Kometa `add_missing` family** (`build_collection: false` feeder mode,
  `radarr_add_all`/`radarr_remove_by_tag` and Sonarr equivalents, quality/monitor
  management) — explicit non-goal in the design; Arr *taglist builders* and
  per-definition overrides (rows 61, 89) are in, library-content management is not.
- **RTL font support** — a fi/en library; inventory reasoning adopted.
- **IMDb poster scraping** (Posterizarr's movie last-resort provider) — inventory
  marked candidate-out; four providers plus manual assets cover the ladder.
- **Music dynamic types** (`mood`/`style`/`album_genre`) — no in-scope music library.
- **`server_preroll`** — inventory's words: out of scope by any reading.
- **Multi-instance `-p` mounts, `DISABLE_UI`, `APP_PORT`** — deployment-shape options
  for platforms this project doesn't target.

---

## Completeness pass

Both inventories were re-walked row by row against this document. Method: every
inventory row whose status was missing, partial, or unknown (and the
candidate-out-of-scope rows) was assigned either to a numbered gap-list row, to a
named verification row, or to Appendix A. Covered rows and not-applicable rows
(different-mechanism equivalents the inventories marked covered) were not carried.
Closely related inventory rows collapse into single gap rows (e.g. eight TMDb chart
builders → row 63); the counts below are of *inventory rows*, not gap rows.

| Source | Gap-ish rows in | Placed in Part 1 (gaps + verification) | Out of scope / superseded (Appendix A) |
|---|---|---|---|
| Posterizarr inventory | 67 | 50 | 17 |
| Kometa inventory | 134 | 93 | 41 |
| **Total** | **201** | **143** | **58** |

The 143 placed inventory rows collapse into the 103 numbered rows of Part 1 (17
verification + 86 implementation). Rows 104, 105 and 106 are not inventory rows: they
were added by phase 5b's verification sweep as the scoped follow-ups to rows 5, 4
and 14. One row changed disposition after the inventories
were written: Tautulli (3 inventory rows) moved from "planned/missing" to superseded
by the user's Tracearr decision, and 2 Tracearr rows moved from account-flagged-out to
in-scope for the same reason; both movements are counted in the table above as placed
(Tracearr) and Appendix A (Tautulli, under A.1's superseded note).
