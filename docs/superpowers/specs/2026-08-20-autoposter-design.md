# Autoposter — Design Spec

**Date:** 2026-08-20
**Status:** Approved design, pre-implementation

## 1. Purpose

Replace Posterizarr and Kometa with a single application that handles the full
poster/overlay/collection/metadata pipeline **atomically per media item**,
driven by Radarr/Sonarr/Tautulli webhooks, instead of Kometa's full-library
scans (~3k movies, ~13k episodes per run today).

**Goals**

- Event-driven per-item processing: art fetch → base composite → badge
  overlays → asset write → Plex upload → per-item metadata ops.
- List-based collections (inherently non-atomic) refreshed on a schedule via
  cheap diffs — never a full-library crawl.
- Rendered output visually indistinguishable from today's
  Posterizarr + Kometa output; existing assets adopted in place with zero
  re-renders at cutover.
- Full replacement: both existing tools are retired after phased cutover.

**Non-goals**

- Jellyfin/Emby support (disabled in current config).
- Multi-user/multi-server support.
- Features of either tool not present in the current configs (e.g. resolution
  overlays on posters, clearart, Discord notifications, Kometa `add_missing`).

**Prior art:** [Kometizarr](https://github.com/P2Chill/kometizarr) covers a
small subset (rating badges, preset collections, Plex-webhook trigger,
SQLite). It validates the FastAPI + React + webhook/cron shape but lacks the
base-art pipeline, title cards/season posters/backgrounds, episode-level
processing, 7 of the 8 badge types in use, metadata operations, Arr-driven
eventing, and Postgres persistence. Nothing is vendored from it.

## 2. Architecture

Approach: **modular monolith** — one container, one FastAPI process, four
modules communicating only through the database and typed interfaces. A future
split into backend + worker (if ever needed) is a refactor, not a rewrite,
because the queue and pipeline are decoupled.

```
Radarr ─┐                                    ┌─→ TMDB/TVDB/Fanart (art + metadata)
Sonarr ─┼─ webhooks ─→ [Intake] → job queue ─┼─→ [Pipeline workers ×5]
Tautulli┘                  ↑       (Postgres)│      ├─ fetch → composite → badges
                           │                 │      ├─ write /assets
        [Scheduler] ───────┘                 │      └─ upload to Plex + metadata ops
         (cron: collections diff,            └─→ MDBList / IMDb datasets (ratings)
          ratings drift, arr sync, cleanup)
[Web UI + REST API] ─ reads/writes the same DB, enqueues manual jobs
```

**Technology choices**

| Concern | Choice | Rationale |
|---|---|---|
| Language/runtime | Python 3.12+, FastAPI, asyncio | Ecosystem parity with Kometa; `python-plexapi` is what Kometa itself uses; Kometa source is readable as reference implementation |
| Persistence | PostgreSQL 18 (async SQLAlchemy + asyncpg, Alembic migrations) | User requirement — SQLite ruled out (Volsync snapshot corruption). App expects `DATABASE_URL`, owns its schema |
| Job queue | Postgres table; claim via `SELECT … FOR UPDATE SKIP LOCKED`; `LISTEN/NOTIFY` wakeups | Durable across restarts, no extra broker, instant worker wakeup |
| Workers | asyncio tasks, configurable parallelism (default 5, matches current `ParallelJobs`) | Peak load is "one season pack" — a single process handles it today |
| Base composite | ImageMagick via Wand | Posterizarr renders bases with ImageMagick; same engine ⇒ same text auto-sizing/gravity/stroke semantics |
| Badge overlays | Pillow | Kometa renders badges with Pillow; same engine ⇒ same output |
| Plex | `python-plexapi` | Battle-tested |
| Arr | Radarr/Sonarr v3 APIs | Webhook source + `add_existing` sync |
| IMDb ratings | Free IMDb TSV datasets, loaded into Postgres daily | Kometa's approach; no per-item scraping |
| Scheduler | APScheduler, in-process | No external cron needed |
| Config | One YAML file (Secret/ConfigMap mount), schema-validated | Both old configs map into it 1:1; documented mapping is a deliverable |
| Web UI | React SPA (Vite + TypeScript), served at `/` by the same app; REST + WebSocket | Single deployment |
| Observability | Structured logs, Prometheus `/metrics`, `/healthz` | k8s-native |
| Provider caching | Postgres table with TTL (semantics of Kometa's `cache_expiration: 60`) | No SQLite anywhere |

**Core mechanism — render fingerprint.** Every artifact carries a hash of all
inputs affecting its final image: base art hash, resolution, video format,
audio codec, ratings values (as displayed, post-rounding), runtime, language
flags, config version, badge asset hashes. Any event or scheduled pass
recomputes fingerprints and re-renders **only** items whose fingerprint
changed. A "full run" becomes a cheap fingerprint sweep, not 16k image
operations.

## 3. Data model

Names illustrative; final schema in migrations.

- **`media_items`** — one row per movie/show/season/episode: Plex
  `rating_key`, library, type, parent linkage, external IDs
  (tmdb/tvdb/imdb), title, year, file path. The spine.
- **`item_facts`** — current observed truth per item: resolution, video
  format (DoVi/HDR10), audio codec, runtime, audio/subtitle languages, IMDb
  critic rating, TMDB audience rating, Common Sense rating, genres, studio,
  originally-available date. Each fact records source and fetch time.
- **`renders`** — one row per artifact (poster / season poster / background /
  title card) per item: `source_mode` (`generate` = fetch textless art and
  composite our own text/fade; `verbatim` = apply supplied art as-is), base
  art provenance (provider, source URL, textless or not), base file hash,
  render fingerprint, `/assets` path, Plex upload time, status.
- **`jobs`** — the queue: type (`process_item`, `refresh_collections`,
  `ratings_sweep`, …), payload, state, attempts, scheduled time,
  claimed-by/claimed-at.
- **`collections`** + **`collection_members`** — per configured collection:
  last-fetched source list snapshot and believed Plex membership. Nightly
  diff edits only deltas.
- **`provider_cache`** — keyed API responses with TTL.
- **`events_log`** — every webhook received and its resolution; feeds the UI
  activity feed and debugging.

**Schema principles:** facts are separate from renders so metadata ops and
image ops are independently triggerable; nothing lives only in memory — any
pod restart resumes exactly where it stopped.

## 4. Per-item pipeline

Triggered by Radarr ("On Import Complete", "On Rename", "On Movie Add"),
Sonarr (same + "On Series Add"), Tautulli, the scheduler, or the UI.

1. **Intake & normalize.** Webhook logged to `events_log`, normalized into
   "process item X" intents. A Sonarr episode event fans out to: episode
   (title card), season (season poster), show (poster + background) — a new
   episode can change season/show-level facts. Radarr events map to the
   movie.
2. **Debounce.** Enqueueing `process_item(X)` while one is pending is a
   no-op; jobs start after a settle delay (configurable, ~30s) so a season
   pack import becomes one pass over affected items.
3. **Resolve.** Map external ID (tmdbId from Radarr, tvdbId from Sonarr) to
   the Plex item. If Plex hasn't scanned yet: retry with backoff, bounded,
   then park as "waiting for Plex" (visible in UI). Replaces
   `FileTestOnTrigger`.
4. **Gather facts.** Media facts from Plex (resolution, codecs, languages,
   runtime); provider facts from TMDB/MDBList/IMDb-dataset (ratings, genres,
   studio, Common Sense, dates) — through the cache. Update `item_facts`.
5. **Base art** — only if no existing base render or the manual override
   changed. Provider ladder TMDB → TVDB → Fanart → Plex, textless-first per
   language ladders (`xx` → `en` → `fi`; logos `en` → `fi`). Composite with
   ImageMagick per current Posterizarr settings: fade PNGs
   (`overlay.png`, `bottom-up-fade-background.png`, `bottom-up-fade.png`),
   Comfortaa title text with auto point-size, gravity south, configured
   offsets, clearlogo handling, `SkipTBA`. Write clean base to
   `/assets/<Library>/<Folder>/…` with **today's naming scheme**
   (`poster.jpg`, `SeasonXX.jpg`, `SXXEYY.jpg`, `background.jpg`) so
   existing assets are adopted in place. `/manualassets` overrides beat
   fetched art.
6. **Fingerprint & badge.** Compute fingerprint from facts + base hash +
   config version. Unchanged → done. Changed → composite badges onto a copy
   of the base with Pillow using Kometa's default overlay assets and layout
   math. Badge set in use: resolution, audio codec, ratings (IMDb critic +
   TMDB audience, right-aligned), video format, runtimes, Common Sense,
   languages (flag variant); TV adds episode-info and episode-level variants
   of resolution/audio/ratings/video-format/runtimes, plus season-level
   Common Sense.
7. **Upload & metadata.** Upload badged image to Plex as the item's art;
   apply per-item metadata ops (genre ← TMDB, content rating ←
   MDBList/Common Sense, studio ← TMDB, originally-available ← TMDB, critic
   rating ← IMDb, audience rating ← TMDB; episode-level ratings for TV).
   Record in `renders`.

Every step is idempotent; re-running an item is always safe.

**Artifact model:** `/assets` holds the clean base (art + fade + title text);
Plex holds base + badges. Same as today — "remove overlays" remains possible.

## 5. Scheduler

All cadences configurable.

- **Collections diff (nightly).** For each configured collection — Oscars,
  IMDb Popular/Top 250/Lowest Rated, Common Sense buckets, for both
  libraries as per current config — fetch the source list (TMDB/IMDb
  datasets/MDBList, mirroring Kometa's default builders), resolve to owned
  Plex items, diff against `collection_members`, apply only deltas
  (add/remove/sort). Collection poster + summary set once at creation using
  current collection styling. `minimum_items: 1`, no delete-below-minimum.
- **Ratings drift sweep (weekly).** Refresh ratings for all items (batched,
  cached, rate-limited), update facts, recompute fingerprints. Re-render
  only items whose *displayed* (rounded) value changed. Runtimes/resolution
  don't drift without a file event — not swept.
- **IMDb dataset refresh (daily).** Download TSVs, load into Postgres. Feeds
  ratings badges and IMDb chart collections.
- **Arr sync (daily).** Kometa `add_existing` parity: Plex items missing
  from Radarr/Sonarr get registered in place, with `/mnt/media` ↔
  `/mnt/Media` path mapping. Safety net: any Plex item with no `media_items`
  row is enqueued — eventual consistency even if webhooks fail.
**Provider request budget.** Fanart.tv asks that a client not "perform more
requests than are necessary for each user" and forbids downloading all of their
content.

For scale: a complete sweep of this library is about **2,236 requests** — 1,953
movies plus 283 shows. Fanart is queried once per *title*, and a single response
carries every artwork type for it, so the 12,598 episode assets generate no
Fanart traffic at all (it has no episode artwork). Against a corpus of roughly
950,000 images, that is a rounding error and nowhere near the clause about bulk
downloading. This is a courtesy and efficiency concern, not a compliance risk,
and it should not be used to justify avoiding otherwise sensible designs.

What follows from it is ordinary good behaviour rather than a hard limit: the
scheduled passes read from `provider_cache` first, skip items whose fingerprint
is unchanged, and rate-limit per provider so a burst does not arrive as a spike.
Adoption resolves nothing from the artwork providers at all — it hashes what is
already on disk — because that is faster and correct, not because the traffic
would be objectionable. The same courtesy applies to TMDB and TVDB.

- **Asset cleanup (weekly).** `AssetCleanup` parity: assets whose item no
  longer exists move to `/assetsbackup`.

**One-time adoption (first boot).** Walk Plex + `/assets`: build
`media_items`/`renders` for everything, hash existing bases, compute
fingerprints, mark current Plex art as already badged. Zero re-renders — the
system starts believing the world is correct; only genuine future changes
trigger work. This is the cutover moment: repoint webhooks, stop both old
tools. `--dry-run` mode produces an adoption report for review before
cutover.

## 6. Web UI

React SPA served by the app; REST API + WebSocket for live updates.

- **Dashboard** — queue depth, worker status, items processed today,
  failures, next scheduled runs, live activity feed from `events_log`.
- **Library browser** — art grid, filter by library/type/status. Item detail:
  base vs. badged side-by-side, facts feeding badges, fingerprint status,
  render history; actions: re-run, refetch base art (provider-candidate
  picker for manual choice), clear manual override.
- **Collections** — member counts, last diff result (+3/−1), next refresh,
  "diff now".
- **Config editor** — schema-validated YAML editing, hot-reload on save.
  Config version bump marks affected fingerprints stale; UI previews impact
  ("this change would re-render ~2,400 items") with apply-now vs.
  regenerate-later choice.
- **Failures** — parked jobs with reasons; retry/dismiss.

Auth: single admin password (bcrypt), behind the cluster ingress.

**Attribution (required, not optional).** The UI is a user-facing surface
displaying metadata and artwork from third-party APIs whose terms mandate
attribution, so every view that shows provider-sourced artwork or metadata must
carry it:

- **TheTVDB** — attribution with a **direct link to TheTVDB.com**. Their terms:
  *"Unless approved by TheTVDB, attribution with a direct link to TheTVDB.com
  must be displayed to end users viewing metadata from our API. Command line
  products or development libraries may display attribution on your about or
  readme pages."* The readme exemption applies only while there is no UI; it
  lapses when this ships.
- **TMDB** — the exact notice *"This product uses TMDB and the TMDB APIs but is
  not endorsed, certified, or otherwise approved by TMDB."*, displayed
  prominently, **plus the TMDB logo**, which must be less prominent than this
  application's own branding. The logo asset is a deliverable of this phase.
- **Fanart.tv** — **confirmed: no attribution requirement.** The attribution
  clause belongs to their *project* API key terms; this deployment uses a
  personal API key, which carries no such condition. Nothing to display.

**Where it goes (decided).** Both notices live on the Web UI's **API settings
page**, alongside the key fields for each provider — the place a user is
already looking at that provider. TheTVDB publishes a ready-made attribution
image in their brand assets ("Metadata provided by TheTVDB. Please consider
adding missing information or subscribing.") with light and dark logo
variants; use their asset rather than reproducing the wording by hand, and
pick the variant matching the active theme.

Treat this as an acceptance criterion for the UI phase, not a documentation
task.

## 6a. Decisions carried out of phases 1-3

Recorded here so they are not rediscovered as open questions.

**The `Overlay` Plex label: deliberately not applied.** The tool being
replaced labels every item it overlays, and that label is its *state*: it
reads it back to decide whether an item still needs overlaying
(`overlays.py:104` — no label means "not overlaid yet"), and it uses it as the
index of items to **restore original artwork to** when overlays are removed or
unconfigured (`overlays.py:63-74`). This service keeps that state in
`renders.badge_fingerprint`/`upload_status` and, since phase 3d, in the
uploaded image's own EXIF — so the label is redundant. Applying it would also
be a small hazard: if the old tool were ever run again, the label is exactly
what it would use to decide our artwork should be reverted. The config flag
exists and defaults off; leave it off unless the label is wanted for
filtering in Plex's own UI.

**Orphaned uploads: reap them.** The previous tool uploads a fresh poster on
every run and never removes the old ones, leaving roughly five orphaned
`upload://` entries per item — on the order of 11,000 across the library.
Fingerprint-gated upload stops it growing; the accumulated ones are to be
cleaned up, dry-run first.

**Collection posters: implement them.** The previous tool downloads a static
hosted image per collection, preferring a local `/assets/<collection>/poster.*`
when one exists. Adopted collections keep whatever poster they already have,
so nothing regressed — but a newly created collection has none, so this is a
real gap to close rather than a deferral.

## 7. Error handling

- **At-least-once, idempotent jobs.** Any job can crash at any line and
  retry safely. Exponential backoff, per-type attempt caps, then park in
  failures view. A webhook always becomes either a completed render or a
  visible failure — never silently dropped.
- **Provider resilience.** Per-provider rate limiters and circuit breakers;
  a down provider is skipped and the ladder falls through. If all art
  providers fail, the job parks as retryable rather than rendering degraded
  output. Fact updates are transactional.
- **Plex outages.** Uploads/metadata ops queue and drain on recovery; the
  Arr-sync job catches anything missed.
- **Asset safety.** Temp-file write + atomic rename; previous asset version
  retained (one generation) for one-click rollback. The app never deletes an
  asset without a replacement in hand.
- **Config safety.** Schema-validated on boot and on save; bad config is
  rejected with line-level errors, never half-applied.

## 8. Testing

- **Unit:** fingerprint computation, debounce/coalescing, collection
  diffing, language-ladder selection, Arr payload parsing.
- **Golden-image:** fixed inputs through the real ImageMagick/Pillow path,
  compared against checked-in references with perceptual-hash tolerance.
  Initial references are harvested from actual Posterizarr/Kometa outputs in
  the existing asset library — parity is proven, not assumed.
- **Integration:** recorded HTTP fixtures for TMDB/TVDB/Fanart/MDBList; fake
  Plex server for upload/metadata; real Postgres via docker-compose (also
  the dev environment).
- **Adoption rehearsal:** dry-run adoption against production Plex + assets
  before cutover; review the unmatched list.

## 9. Delivery phases

Each phase independently shippable; old tools retired piecemeal.

1. **Posterizarr parity** — intake, resolve, base art pipeline, `/assets`
   write. Retire Posterizarr (Kometa temporarily remains, still reading
   `/assets`).
2. **Badges + metadata ops** — fingerprinting, badge rendering, Plex upload,
   per-item operations. Disable Kometa overlays/operations.
3. **Collections + scheduler** — diff engine, drift sweep, Arr sync,
   adoption run. Retire Kometa fully.
4. **Web UI** — dashboard, browser, collections, config editor, failures.
   (A minimal status endpoint exists from phase 1.)

## 10. Configuration mapping

The new YAML config carries over, 1:1 where sensible:

- **From Posterizarr config:** provider keys, `FavProvider`, language ladders
  (`xx`/`en`/`fi` per artifact type), artifact toggles (posters, season
  posters, backgrounds, title cards), fade overlay files, fonts
  (Comfortaa/Colus), all text-styling blocks (point sizes, offsets, gravity,
  stroke), `SkipTBA`, library excludes (`Muskarit`, `Photos`),
  `AssetCleanup`, parallelism (5), `LibraryFolders: true` naming.
- **From Kometa config:** overlay set + template variables (ratings sources
  and alignment, episode-level builders, season-level Common Sense),
  collection definitions (oscars, imdb, content_rating_cs per library),
  operations set (`mass_*_update` sources incl. `mdb_commonsense` caveat),
  `plex_bulk_edit_batch_size: 250` for any batched Plex writes,
  Radarr/Sonarr connection + `add_existing` + path mappings, cache TTL 60,
  `minimum_items: 1`.
- **Secrets** (Plex token, TMDB/TVDB/Fanart/MDBList/Arr keys) injected via
  env vars, ExternalSecret-friendly — never in the YAML.

Settings from either tool not listed in Non-goals but absent here default to
current behavior; the implementation plan enumerates the full mapping table.

## 11. MediUX (deferred, seam built in Phase 1)

MediUX (mediux.pro) hosts curated designer **sets** — a matching poster,
backdrop, every season poster, and every episode title card for one title by
a single designer, optionally grouped into franchise-wide boxsets. It is the
only source that supplies coherent title cards across a whole show, which is
otherwise the weakest part of the art ladder.

**It cannot join the provider ladder.** MediUX art ships with the title
treatment already composited in, and the API exposes no language field at
all, so the `xx`/null-language textless inference used for TMDB/Fanart/TVDB
is structurally impossible. The handful of textless sets that exist are
discoverable only by string-matching creator-authored set titles such as
"(Textless)". MediUX is therefore modelled as `source_mode: verbatim` — apply
the set's images as-is and **skip the text/fade compositing entirely** —
selected per item, never as a fallback inside the generate path.

**Status: deferred.** The API is real and fully mapped (a Directus GraphQL
endpoint at `https://images.mediux.io/graphql`; the AURA project embeds the
complete query set, and image bytes at
`https://api.mediux.io/assets/{uuid}?v={modified_on}` are public and need no
auth). The blocker is the metadata bearer token, which is allowlist-gated via
the MediUX Discord rather than self-service — the same blocker recorded in
Posterizarr issue #111. Anonymous GraphQL introspection returns an empty
schema.

**What Phase 1 builds for it:** the `source_mode` column on `renders` and a
render pipeline that branches on it. Nothing else. Adding MediUX later is a
new provider module plus a `mediux_sets`/`item_set_bindings` table tracking
which set is bound to which item and each image's `modified_on`, so designer
updates and newly-aired episodes pull matching art. No rewrite of the render
model is required.
