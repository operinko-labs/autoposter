# Jellyfin as a media server — design

**Date:** 2026-09-12
**Status:** Approved design, implemented over 2026-09-12/13 — §15 records what implementation amended
**Roadmap:** row 267 (`docs/design/2026-08-22-full-parity-roadmap.md`)
**Scope of this document:** sub-projects 1 and 2 of row 267 — the server
abstraction, the Jellyfin 12.x client, artwork delivery, and server-neutral item
identity. Sub-project 3 (the builder-availability tag-set) and sub-project 4
(collections and metadata on Jellyfin) each get their own spec when reached.

## 0. Decisions this design rests on

Every one of these was decided by the operator during design, not inferred.

| Decision | Choice | Why |
|---|---|---|
| Server model | **Any combination of Plex and Jellyfin, minimum one.** Jellyfin-only, Plex-only and both-at-once are all valid deployments. | Both reasons the work exists need Jellyfin-only: a community user with no Plex server, and the operator's own possible migration. "Plex always present" was the current codebase's assumption leaking into the design and was rejected. |
| Identity | **Approach B: server-neutral re-key now.** `media_items.rating_key` is removed; per-server ids live in a refs table; items are keyed by a derived identity. | Jellyfin-only cannot coexist with a required, unique Plex key. A nullable middle ground was offered and declined in favour of paying the whole identity cost once. |
| Ordering | Sub-projects 1 and 2 together, in this spec. | Artwork delivery needs per-event resolution on every server; making that stable needs the identity spine. Splitting them would build the same resolution path twice. |
| Target | **Jellyfin 12.x only.** | 12.0 left prerelease 2026-09-08 after seven RCs. Operator's assessment: no breaking changes to the request pipeline; the legacy `/emby/` and `/mediabrowser/` prefixes are dropped (confirmed: no such path in the 12.0 spec) and the OpenAPI is stricter. |
| Source of truth | **Jellyfin's own OpenAPI**, `https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json` (3.0.4, reports 12.0.0), captured into `docs/reference/` and cited by path and operation, as `arr/client.py` cites the Arr capture. | Primary source, citable by line, kept honest by the existing citation guard. |
| Prior art | `4lx69/jellyfin-collection` (MIT, Python) is **inspiration only. Do not copy its code.** | It is Kometa-YAML-compatible by design, the inverse of this project's locked capability-parity decision; lifting from it imports a config model this project does not have. Its builder set is evidence for the gating model (a Jellyfin search covering only genres/year/limits; nothing resembling smart collections). |
| Wizard | The first-start wizard gains a **media server phase**. | Once servers are optional, a wizard whose first act is a Plex PIN flow is wrong for exactly the Jellyfin-only user this exists for. |
| Emby | **Not in this spec.** | Posterizarr supports it; it shares lineage with Jellyfin but not its API. It earns its own row once Jellyfin is real. |

## 1. Verified facts the design depends on

### 1.1 Jellyfin 12.0 API (from the captured spec)

- **There is no provider-id lookup query.** `anyProviderIdEquals` from 10.x is absent from `GET /Items`; the only provider-related parameters anywhere are `hasTmdbId`/`hasTvdbId`/`hasImdbId` (booleans) and `fields=ProviderIds` on results. Resolution must therefore be an index, not a query (§4.4).
- `GET /Items` supports `recursive`, `includeItemTypes`, `parentId`, `fields`, `searchTerm`, `years`, `ids`.
- `GET /Shows/{seriesId}/Seasons` and `GET /Shows/{seriesId}/Episodes`; `BaseItemDto` carries `IndexNumber`, `ParentIndexNumber`, `SeriesId`, `Path`, `ProviderIds`, `SortName`, `ForcedSortName`, `LockedFields`.
- `POST /Items/{itemId}/Images/{imageType}` (`SetItemImage`), request body content type `image/*`; `ImageType` includes `Primary`, `Backdrop`, `Logo`, `Thumb`, `Banner`, `Art`.
- `POST /Items/{itemId}` (`UpdateItem`) takes the full `BaseItemDto` — it replaces, so metadata writes are read-modify-write.
- `POST /Collections`, `POST|DELETE /Collections/{id}/Items`, `POST /Playlists`, `/Playlists/{id}/Items` exist (sub-project 4).
- `GET /Library/VirtualFolders` (libraries and their `Locations`), `POST /Library/Refresh`, `POST /Items/{itemId}/Refresh`, `GET /System/Info`, `GET /Users/Me`.
- Security scheme: `apiKey` in header `Authorization`, described only as "API key header parameter". The known format is `Authorization: MediaBrowser Token="<key>", Client="…", Device="…", DeviceId="…", Version="…"`. **This is verification item V1** (§11).

### 1.2 The codebase (from the two coupling maps, 2026-09-12)

- `PlexClient` (`src/autoposter/plex/client.py:338`) is already a de-facto boundary: it returns plain dataclasses (`ResolvedItem` `:64`, `SectionItem` `:95`) and hides `plexapi`. Its public surface: `fetch_item` `:607`, `list_items` `:679`, `exists_many` `:694`, `keys_resolve` `:738`, `resolve` `:764`.
- The boundary leaks in one place: `fetch_item` returns a raw `plexapi` object, and `plex/artwork.py`'s functions (`upload_artwork` `:61`, `upload_logo` `:93`, `selected_uploaded_logo_key` `:130`, `clear_logo` `:149`, `has_clearlogo` `:167`, `generated_title_card_url` `:230`, `reset_artwork_to_agent_default` `:251`, `fetch_artwork` `:308`, `artwork_provenance` `:339`) consume that object directly. `plex/writer.py:763 apply_facts` likewise.
- Collections, playlists and adopt go *around* the client to raw `plexapi` (`collections/lists.py`, `reconcile.py`, `smart.py`, `engine.py`, `playlists.py`, `playlist_users.py`, `posters.py`, `adopt/walk.py`). They are Plex-only and out of scope here (§9).
- Exactly one Plex server per deployment today: `PlexConfig` (`config/schema.py:898-923`), `Secrets.plex_token` (`:151`, hard), constructed in `main.py:89-103`, published as `app.state.plex` (`app.py:159`), `app.state.plex_health` (`:222`), `app.state.plex_server_factory` (`:314`).
- Artwork write path: the unbadged base is written to the asset tree (`render/pipeline.py:913-924`); the **badged image exists only as bytes** (`badges/compose.py:276`) and is uploaded to Plex; nothing writes it to disk. Upload is gated by `config.badges.upload_to_plex` (`pipeline.py:1962`) with a per-library override (`schema.py:1705`); `Render.upload_status` takes `uploaded|skipped|failed`.
- Worker error model (`queue/worker.py:107-127`): `PlexPathMismatch` fails the job; `ItemNotFound` defers it with no finite cap, on the stated grounds that no cap is the right one.
- The artwork full pass enumerates from **Radarr/Sonarr** (`arr/sync.py:447-505`), not from Plex. The enumeration source is already server-neutral.
- Identity today: `media_items.rating_key` `String(64) NOT NULL UNIQUE` (`db/models.py:26-40`), target of the pipeline's `ON CONFLICT (rating_key)` upsert (`pipeline.py:899-901`); `tmdb_id`, `tvdb_id`, `imdb_id` are already columns. The inventory's fourteen load-bearing uses are enumerated in §4.6.
- Wizard: steps are `["password","url","database","systems","finish"]` (`frontend/src/pages/setupSteps.ts:35`); Plex is an accordion body inside `systems`, not a step (`Setup.tsx:632-650`); "required" is the hard-secret list (`config/schema.py:18-25`) and `AUTOPOSTER_PLEX_TOKEN` is on it; `stage_config_document` demands a Plex URL unconditionally (`api/setup.py:1409`).

## 2. Goals and non-goals

**Goals**

1. A deployment with Jellyfin only, Plex only, or both boots, renders, and delivers artwork to every configured server.
2. One render per item, delivered to N servers, with the outcome recorded per server and never held hostage to the slowest server.
3. Item identity that does not depend on any server: an item is the same row whether it was first seen on Plex, on Jellyfin, or both.
4. Existing Plex-only deployments upgrade with no config change and no behavioural change, apart from the identity migration running at boot.
5. The first-start wizard can set up a Jellyfin-only deployment end to end.
6. Plex-only features refuse cleanly, by name, on a deployment without Plex.

**Non-goals (this spec)**

- Collections, playlists, adopt, mismatches, metadata backup and field locks on Jellyfin (sub-project 4).
- The builder-availability tag-set (sub-project 3). §9 builds its first, coarsest instance and nothing more.
- Emby.
- Emulating Plex smart collections or the Plex search DSL on Jellyfin. Both upstreams gate these on Plex; so does this project.
- A Jellyfin container in CI (follow-up row; §10.3).
- Retiring any Plex-only column or feature. They stay, keyed on the Plex ref, behind the gate.

## 3. Architecture

### 3.1 Packages

```
src/autoposter/servers/base.py     MediaServer protocol, ServerItemRef, ResolvedItem, capabilities, errors
src/autoposter/plex/…              unchanged location; PlexClient conforms to the protocol
src/autoposter/jellyfin/client.py  JellyfinClient (httpx, no SDK)
src/autoposter/jellyfin/index.py   per-library owned index (§4.4)
src/autoposter/jellyfin/artwork.py image upload / read-back
src/autoposter/jellyfin/writer.py  metadata read-modify-write
src/autoposter/jellyfin/health.py  JellyfinHealth
```

### 3.2 The `MediaServer` protocol

Lifted from what the pipeline, scheduler, artwork modes and metadata writer call today. Method names keep their current spelling so call sites change shape, not vocabulary.

```
name: str                                   # "plex" | "jellyfin"
capabilities: frozenset[str]                # §3.4

# resolution
resolve(intent: RenderIntent) -> ResolvedItem          # raises ItemNotFound / PathMismatch
fetch_ref(native_id: str) -> ServerItemRef | None
exists_many(intents) -> list[bool]
keys_resolve(intents) -> list[bool]
list_items(kind: str) -> list[SectionItem]

# artwork (every one takes a ServerItemRef, never a client-native object)
upload_artwork(ref, data: bytes, art_kind: str, lock: bool) -> None
upload_logo(ref, data: bytes, suffix: str) -> str | None
clear_logo(ref) -> None
has_clearlogo(ref) -> bool
fetch_artwork(ref, art_kind) -> bytes | None
artwork_provenance(ref, art_kind) -> Provenance | None
reset_artwork_to_agent_default(ref, art_kind) -> bool

# metadata
apply_facts(ref, facts, verbs) -> AppliedFacts        # same WRITABLE_BY_KIND vocabulary

# health
check_liveness() -> bool
```

`ServerItemRef(server: str, native_id: str, library: str, kind: str)` is the only handle that crosses the boundary. Each client resolves the ref to its own object internally (Plex: `fetchItem`; Jellyfin: `GET /Items/{id}`), so the leak in §1.2 closes.

`ResolvedItem` gains `server: str` and `native_id: str`, plus `parent_native_id`. The field `rating_key` is **removed** from `ResolvedItem` in this spec (Approach B); `native_id` replaces it everywhere. `SectionItem` likewise.

### 3.3 `PlexClient` conformance

`PlexClient` implements the protocol by wrapping the functions it already has. The artwork and writer functions move from module-level functions taking a `plexapi` item to methods taking a `ServerItemRef`; the existing bodies are kept, preceded by a `fetchItem`. Consumers in `render/pipeline.py`, `artwork_modes/*`, `api/artwork.py`, `api/item_overrides.py`, `metadata_backup.py` change from passing an item to passing a ref. `plex/client.py:223,395,634` drop their `int(key)` casts (§4.6).

### 3.4 Capabilities

A frozenset on each client, consulted by callers before calling; never discovered by catching. Minimal, concrete set for this spec:

| Capability | Plex | Jellyfin | Meaning |
|---|---|---|---|
| `lock_artwork` | yes | no | `upload_artwork(lock=True)` has an effect |
| `logo_upload` | yes | yes | `upload_logo`/`clear_logo`/`has_clearlogo` are meaningful |
| `logo_upload_key` | yes | no | the server returns a per-upload key (`upload://…`) that `logo_upload_key` can store; without it, logo revert is unavailable |
| `field_locks` | yes | partial | `apply_facts` verbs `lock`/`unlock`; on Jellyfin mapped to `LockedFields` where a field name exists there |
| `artwork_provenance` | yes | yes | EXIF read-back of what the server serves |
| `title_card_url` | yes | no | `generated_title_card_url` |
| `reset_to_agent_default` | yes | no | `reset_artwork_to_agent_default` (Plex cannot delete a single `upload://` entry; Jellyfin has no analogue) |

Calling a method whose capability is absent raises `UnsupportedOnServer(server, capability)` with the fixed sentence `"<label> does not support <capability>"`. The pipeline never reaches it because it checks first; the gate (§9) covers passes and routes.

### 3.5 Application state

- `app.state.servers: dict[str, MediaServer]` — every configured server, insertion order Plex then Jellyfin, **minimum one** (boot refuses otherwise, §7.2).
- `app.state.plex: PlexClient | None` — kept for the Plex-only code this spec does not port.
- `app.state.server_health: dict[str, Health]` — `PlexHealth` and `JellyfinHealth` by name; `app.state.plex_health` kept as an alias while the Plex-only code reads it.

### 3.6 The Jellyfin client

httpx `AsyncClient`, base URL from config, no third-party SDK. Every request carries `Authorization: MediaBrowser Token="<key>", Client="autoposter", Device="autoposter", DeviceId="<stable per deployment>", Version="<AUTOPOSTER_RELEASE or AUTOPOSTER_VERSION>"` (format subject to V1). Libraries from `/Library/VirtualFolders` (`Name`, `CollectionType`, `Locations`), filtered by `excluded_libraries` after the optional `library_map` (§7.1). Images via `SetItemImage` into `Primary` (poster, season poster, title card), `Backdrop` (background), `Logo` (clearlogo), and `Thumb` (a second upload of the background, only when `replace_thumb_with_backdrop` is on). Metadata via `GET /Items/{id}` → mutate → `POST /Items/{id}`. Liveness via `/System/Info`.

## 4. Identity

### 4.1 `media_item_server_refs`

```
media_item_server_refs
  item_id     BIGINT   NOT NULL  FK media_items.id ON DELETE CASCADE
  server      TEXT     NOT NULL           -- "plex" | "jellyfin"
  native_id   TEXT     NOT NULL           -- Plex ratingKey (numeric string) or Jellyfin Id (GUID string)
  library     TEXT     NOT NULL           -- the library name on that server
  created_at, updated_at
  UNIQUE (server, native_id)
  INDEX  (item_id)
```

The only home for any server's id. An item may have zero, one or several rows per server (several only for Plex duplicates the merge pass has not yet reconciled).

### 4.2 `media_items.identity_key`

`TEXT NOT NULL UNIQUE`, the new target of the pipeline's upsert. Derived, never chosen, by this rule and no other:

```
kind := movie | show | season | episode
provider := first present of ("tmdb", tmdb_id), ("tvdb", tvdb_id), ("imdb", imdb_id)
coords := "" for movie/show; f"s{season}" for season; f"s{season}e{episode}" for episode
file := basename(file_path) for movie and episode (file-bearing kinds); "" for show and season

if provider:      identity_key = f"{kind}:{provider.ns}:{provider.value}:{coords}:{file}"
elif file:        identity_key = f"{kind}:path::{coords}:{file}"
else:             identity_key = f"{kind}:legacy:plex:{old_rating_key}"     # migration only
```

**Why the basename.** A 4K and an HD copy are distinct files with identical provider ids; today they are two items, two renders and two resolution badges (`collections/resolve.py:153` documents the same distinction for members). Provider ids alone would collapse them. The basename keeps them apart and still matches across servers, whose mount prefixes differ but whose filenames do not. Shows and seasons carry no file and key on ids and coordinates alone.

**Episode parents.** `parent_id` resolves through the parent's identity key (the show's, computed from the show's provider ids the resolver returns as `parent_*`), replacing today's `rating_key == parent_rating_key` lookup (`pipeline.py:878-881`). The resolver on each server must therefore return the parent's provider ids, not only its native id.

### 4.3 `RenderIntent`

Gains `refs: dict[str, str]` (server → native id) as an optional hint. The persisted field `rating_key` is **still accepted by the decoder** (`app.py:280 RenderIntent(**job.payload)`) and mapped to `refs["plex"]`, then dropped, so every in-flight job payload survives without a payload migration. `dedupe_key` (`intake/arr.py:194-213`) already excludes it. The prune query (`scheduler/prune.py:537`) moves from `payload["rating_key"]` to `payload["refs"]["plex"]` with the legacy path consulted for old rows.

### 4.4 Jellyfin resolution: index first

Because 12.0 cannot answer "which item has TMDB 123" (§1.1):

1. **Build.** Per library, one `GET /Items?parentId=<library>&recursive=true&includeItemTypes=Movie,Series&fields=ProviderIds,Path`. Index by `("tmdb", id)`, `("tvdb", id)`, `("imdb", id)` and by `("path", basename)`. Seasons and episodes are fetched per series on demand via `/Shows/{seriesId}/Seasons` and `/Episodes` (with `fields=ProviderIds,Path`) and cached under the series.
2. **Lookup.** By provider namespaces in the §4.2 precedence, then by path basename. For seasons and episodes: the series first, then `ParentIndexNumber`/`IndexNumber`.
3. **Miss.** One narrow `GET /Items?searchTerm=<title>&years=<year>&includeItemTypes=<kind>&recursive=true&fields=ProviderIds,Path`; every hit is verified against the intent's provider ids before use and inserted into the index.
4. **Second miss.** `ItemNotFound`. The item is not on this server yet (typically: Jellyfin has not scanned the new file). The delivery goes pending (§5.3).
5. **Root folder.** The item's `Path` is matched against the library's `Locations`, exactly as `plex/client.py:793` matches against `section.locations`; a miss raises `PathMismatch` (the generalisation of `PlexPathMismatch`; the Plex class stays as a subclass).
6. **Refresh.** The index is rebuilt at the start of each full pass and on a fixed interval otherwise; a build failure marks the server unreachable for resolution and never leaves a partial index in use.

Held in memory; not persisted.

### 4.5 Persisted strings and contracts

- `Render.source_url` for generated title cards becomes `f"{server}://{native_id}/title_card"` (`pipeline.py:1353`). Existing `plex://…` rows stay valid: their ref row exists.
- API responses: `rating_key` on `/items` (`api/routes.py:466`), item detail (`:557`) and `/api/id-mismatches` (`api/mismatches.py:184`) become `refs: {plex?: str, jellyfin?: str}`; `frontend/src/api/types.ts:192,264,617` follow; `ItemDetail.tsx:1100` shows one id per server. `RebuildResponse.rating_keys` (`api/action_center.py:813`, `types.ts:1027`) becomes `items: [{id, refs}]`.
- **Webhook and EventLog payloads keep `rating_key`** for the Plex id and gain `refs` (`collections/engine.py:1662,1684`, `collections/playlists.py:1193,1244`, `api/playlists.py:473`, `pipeline.py:813-814`, `merge.py:942-944`, `prune.py:480`). They are an external contract; nothing is removed from them.
- Config vocabulary `plex_id` / `plex_rating_key` builders (`builders/simple_ids.py:141-155`) is unchanged: Plex-only, behind the gate.

### 4.6 The fourteen load-bearing uses, disposed

From the inventory. **Changed in this spec:**

1. `media_items.rating_key` UNIQUE + `ON CONFLICT (rating_key)` (`pipeline.py:899-901`) → `identity_key` upsert; refs upserted beside it.
2. `parent_id` via parent rating key (`pipeline.py:878-881`) → via parent identity key (§4.2).
3. `jobs.payload["rating_key"]` (`prune.py:537`, `app.py:280`) → `refs`, legacy accepted (§4.3).
7. `plex://{rating_key}/title_card` (`pipeline.py:1353`) → `{server}://{native_id}` (§4.5).
10. Numeric-string assumptions: `merge.py:500,507` tie-break → by `updated_at` then `id`; `plex/client.py:223,395,634` `int(key)` → the Plex client parses its own ids internally and never exposes an int.
11. API fields the UI reads → `refs` (§4.5).
12. EventLog/webhook fields → `rating_key` kept, `refs` added (§4.5).
14. `collections/resolve.py:58 _identity` → **unchanged**: it operates on `plexapi` objects inside the Plex-only collections engine, not on `MediaItem`.

**Unchanged, Plex-only, behind the gate:** 4 (`managed_playlists.plex_rating_key`, `managed_playlist_users.plex_rating_key`), 5 (`managed_collections.plex_rating_key`), 6 (raw Plex collection URLs in `reconcile.py`, `smart.py`), 8 (`logo_upload_key` / `upload://`, capability `logo_upload_key`), 9 (metadata backup files keyed by Plex id), 13 (`plex_id` builders).

### 4.7 Migration

One Alembic revision, in this order, each step idempotent:

1. Create `media_item_server_refs`; backfill `(item_id, "plex", rating_key, library)` from every row.
2. Add `identity_key` nullable; compute it in Python over every row by the §4.2 rule (the `legacy:plex:` form only when a row has no provider id and no file path); **detect collisions** (§6.5); set `NOT NULL` and `UNIQUE`.
3. Create `render_deliveries` (§5.2); backfill one `plex` row per render from `upload_status`/`uploaded_at`.
4. Drop `ix_media_items_rating_key` and the `rating_key` column.

Down reverses through the Plex ref rows: re-add the column, fill from `refs WHERE server='plex'` (first row per item), drop deliveries, drop refs, drop `identity_key`. Proven up, down and up against a scratch database (`compose exec postgres psql` or asyncpg; the test image has no `psql`) before merge, plus the seeded fixture in §10.4.

## 5. Data flow

### 5.1 Event path

1. Webhook → `RenderIntent` (provider ids, coordinates; `refs` empty). Unchanged.
2. Job claimed. For each server in `app.state.servers`: `server.resolve(intent)`. Each success upserts a refs row; the first success establishes or updates the `MediaItem` by identity key. A server that raises `ItemNotFound` or a transport error does **not** block the others; its outcome is recorded as a pending delivery (§5.3).
3. Render once per art kind: base to the asset tree, badged bytes in memory. Unchanged.
4. Deliver: for each server with a ref for this item and `upload_to_<server>` true (global or per-library), `server.upload_artwork(ref, data, art_kind, lock=config.badges.lock_artwork and "lock_artwork" in server.capabilities)`.
5. Record per server (§5.2).

### 5.2 Recording

```
render_deliveries
  render_id   FK renders.id ON DELETE CASCADE
  server      TEXT NOT NULL
  status      TEXT NOT NULL   -- uploaded | skipped | failed | pending
  attempted_at, uploaded_at, next_attempt_at
  detail      TEXT            -- category + class name, never a URL
  UNIQUE (render_id, server)
```

`Render.upload_status` stays as the roll-up so every existing query and dashboard keeps working: `uploaded` when every attempted delivery is uploaded, `failed` when any is failed, `skipped` when none was attempted, `pending` while any is pending and none failed. `uploaded_at` = the latest per-server `uploaded_at`.

### 5.3 The unresolved server

Today `ItemNotFound` defers the whole job with no finite cap (`worker.py:124`). That stance is kept, applied at the delivery: the job completes for the servers that resolved; the missing server gets a delivery row with `status=pending` and `next_attempt_at = now + DEFER_INTERVAL_SECONDS`. A scheduler pass, `retry_pending_deliveries`, selects due rows, re-resolves on that server, uploads, and records. No new job type; the deliveries table is the retry queue. Plex artwork is never held to Jellyfin's scan.

### 5.4 Full pass

Enumeration is already server-neutral: `arr/sync.py` walks Radarr and Sonarr and enqueues intents. Its `known` skip-set (`:447-505`) moves from rating keys to identity keys. Every intent then follows §5.1, so the full pass delivers to every server without a new walker. `adopt/walk.py` walks Plex sections and stays Plex-only.

### 5.5 Scheduler passes

- **Prune** (`scheduler/prune.py`, `exists_many` per server): a ref is pruned when its server no longer has the item; the `MediaItem` only when no ref remains.
- **Merge** (`scheduler/merge.py`, `keys_resolve` per server): clusters by identity key; survivor by `updated_at` then `id`.
- **Metadata** (`apply_facts` per server with a ref): Jellyfin is read-modify-write; `lock`/`unlock` verbs map to `LockedFields`, whose member type `MetadataField` is exactly `Cast, Genres, ProductionLocations, Studios, Tags, Name, Overview, Runtime, OfficialRating` (verified in the 12.0 spec). Against the writer's vocabulary that gives: `title`→`Name`, `summary`→`Overview`, `genres`→`Genres`, `studio`→`Studios`, `content_rating`→`OfficialRating`. `sort_title`, `ratings`, `originally_available`, `original_title`, `tagline` and `added_at` are **not lockable on Jellyfin**; their `lock`/`unlock` verbs are no-ops with a debug line, and the `field_locks` capability is therefore `partial` (§3.4).
- **Retry pending deliveries** (new, §5.3).

## 6. Error handling

### 6.1 Isolation
Every server call is caught at the delivery or ref it belongs to and never propagates into another server's path. Logged as `category: ClassName` (the `api/version.py:_failure_reason` discipline), never a URL.

### 6.2 Four outcomes per server on resolve
| Outcome | Delivery | Retried | Notes |
|---|---|---|---|
| Resolved | proceeds | — | |
| `ItemNotFound` | `pending` | yes, no cap | not scanned yet |
| `PathMismatch` | `failed`, fixed sentence | no | a scan will not change a mount |
| transport error | `pending` | yes | health poller shows the server down |

### 6.3 Credential rejected (401)
Jellyfin API keys do not expire; a 401 is misconfiguration. The health record for that server carries `credential_rejected`; the status endpoint surfaces it by name; deliveries wait rather than hammer. The existing settings-page secret rotation (state-file names, `boot.py:196`) fixes it without a restart.

### 6.4 Health and index
`JellyfinHealth` polls `/System/Info` on `liveness_interval_seconds`. A down server queues its deliveries and stops nothing else. An index build that fails leaves the server unreachable for resolution; a partial index is never used.

### 6.5 Migration collisions
Two rows can compute the same identity key (Plex re-key leftovers the merge pass has not reconciled). Boot runs migrations, so a refusal would crash-loop the pod. The migration therefore **merges deterministically**: the row with the newest `updated_at` (then highest `id`) survives; the other's refs, renders, facts, credits and overrides are re-pointed; one log line per merge naming both old ids. A management command `autoposter migrate-preview` reports the collisions without changing anything, so the operator can run the existing merge pass first if preferred.

## 7. Configuration

### 7.1 Schema
```yaml
plex:                       # now OPTIONAL as a block; unchanged fields
  url: …
  excluded_libraries: []
  …
jellyfin:                   # new, optional
  url: https://jellyfin.example
  excluded_libraries: []
  library_map: {}           # Plex library name -> Jellyfin library name; only when names differ
  replace_thumb_with_backdrop: false
  liveness_interval_seconds: 60
badges:
  upload_to_plex: true      # unchanged
  upload_to_jellyfin: true  # new, same per-library override
operations:
  write_to_plex: true       # unchanged
  write_to_jellyfin: true   # new
```
Twinned keys rather than a list-shaped setting: it matches the existing style and keeps every current config valid unchanged. Plex's `resolve_max_attempts` keeps its meaning for the Plex path; Jellyfin has no copy (attempts belong to deliveries, §5.3). Live-versus-restart follows each key's Plex twin (`config/live.py`).

### 7.2 Secrets and boot
`AUTOPOSTER_JELLYFIN_APIKEY` is added. `AUTOPOSTER_PLEX_TOKEN` **leaves** `_SECRET_ENV` (`schema.py:20`). A new category, **server credentials**, holds both, with the rule: *configured* = the five remaining hard secrets present **and** at least one server with both an address (config) and a credential (secret). `missing_hard_secret_names` keeps its meaning for the five; a new `missing_server_setup()` answers the server half; `boot.is_configured` (`boot.py:83`) requires both. A configured server whose credential is missing is a boot refusal naming the variable; zero configured servers is a boot refusal with a fixed sentence.

### 7.3 Documents
`config/autoposter.example.yaml` gains the block (its schema test walks every key). `deploy/README.md` gains "Media servers" and the ExternalSecret line. `.env.example` gains the key.

## 8. The wizard's media-server phase

- **Step algebra:** `ALL_STEPS = ["password","url","database","servers","systems","finish"]` (`setupSteps.ts:35`); `farthestStep` gains the `servers` gate: unmet while `missing_server_setup()` is non-empty (`setupSteps.ts:78-86`).
- **Servers pane:** one card per server. The Plex card is today's `SetupPlexPane` moved out of the systems accordion (`Setup.tsx:632-650, 696-770`). The Jellyfin card takes an address and API key, runs `/check` with system `jellyfin`, then lists libraries via a new `POST /api/setup/jellyfin/libraries` mirroring `/plex/libraries` (`api/setup.py:1097`) and stages `document["jellyfin"]`.
- **Progress:** `/progress` gains `servers: {plex: {configured, checked}, jellyfin: {…}}` — ten keys. `required` no longer lists the Plex token.
- **Staging:** `stage_config_document` (`setup.py:1409`) requires at least one server URL whose credential is staged or resolved, not a Plex URL unconditionally; `_apply_staged_urls` (`:1450-1478`) treats `jellyfin` as it treats `plex`.
- **Check table:** `CHECK_SYSTEMS` gains `jellyfin` (typed address; `GET /System/Info`; header auth) — ten systems; `SYSTEMS_WITH_AN_ADDRESS` gains it (`Setup.tsx:113`, `setup_checks.py` host=None).
- **Frontend tables:** `PROVIDER_LABELS` gains the key; `REQUIRED_PROVIDER_NAMES` drops the Plex token (`Setup.tsx:87-93`); `SYSTEM_FOR_CREDENTIAL` gains `AUTOPOSTER_JELLYFIN_APIKEY: "jellyfin"`.
- **Finish pane:** summarises each configured server and which were checked; "left for later" covers the un-configured server.
- **Pinned assertions that move (deliberately):** `tests/test_api_setup.py:59-66` (HARD tuple), `:516-519` and `:1347-1350` (nine-key progress shape), `:526`/`:1901` (`required`); `tests/test_api_setup_check.py:89-103` (nine-key check set); `frontend/src/pages/setupSteps.test.ts:6-17, 46-108`; `Setup.test.tsx:519, 236, 244, 261`; `SetupFinishPane.test.tsx:36-76`.

## 9. The gate (first instance of the tag-set)

One predicate, `plex_configured(app) -> bool`, and one fixed sentence: `"This needs Plex, and no Plex server is configured."` Applied at:

- **Scheduler passes** that need Plex: collections reconcile (`collections/engine.py:316 run_library`), playlists (`collections/playlists.py`), adopt, metadata backup — skip with a single `INFO` line per pass.
- **API routes** that need Plex: `/api/setup/plex/*`, mismatches, adopt, playlists, collections builders, item overrides' lock semantics, metadata backup — refuse with `409` and the sentence, via one FastAPI dependency.
- **UI:** the status endpoint gains `capabilities: {plex: bool, jellyfin: bool}`; pages that need Plex are hidden from the sidebar and render the sentence if reached directly.

Built as a single dependency and a single predicate so the tag-set generalises it (per-builder declarations checked against configured systems) rather than replaces it.

## 10. Testing and verification

### 10.1 One fake
`tests/media_server_doubles.py: FakeMediaServer` satisfies the protocol. The eight `FakePlexClient` shapes (`test_api_artwork_modes.py:171`, `test_api_mismatches.py:41`, `test_artwork_{reset:103,logo:116,backup:42,restore:50,revert:51}.py`, `test_api_item_overrides.py:58`, `test_app.py:610`) collapse onto it as their files are touched, not in a sweep.

### 10.2 Conformance
One behavioural suite parametrised over three implementations: the fake; `PlexClient` over `tests/plex_doubles.py: FakeSection`; `JellyfinClient` over an httpx `MockTransport`. A method that behaves differently on one server fails by name.

### 10.3 Jellyfin contract, cited
`docs/reference/2026-09-jellyfin-openapi-12.md`: a curated capture of only the endpoints used (§1.1), each cited by path and operation; the raw 1.9 MB spec stays out of the tree. Tests assert the client's requests against it: paths, the auth header, `fields=ProviderIds,Path`, the image content type. A `jellyfin` pytest marker (declared beside `imagemagick` and `deep` in `pyproject.toml`) marks tests that need a live server; they read `AUTOPOSTER_TEST_JELLYFIN_URL`/`_KEY` and are deselected without them. First among them: V1. A Jellyfin container as a CI service is a follow-up row.

### 10.4 Migration
Up, down, up on a scratch database. Plus a seeded fixture: items with every provider id, with none, two sharing a basename with different ids, a 4K/HD pair, a duplicate pair that must merge, episodes with parents, and existing renders. Assertions on refs, identity keys, `parent_id`, deliveries backfill, the roll-up, and the collision log line. `migrate-preview` reports the same fixture's collisions.

### 10.5 Gate, through the real entry point
A Plex-less app boots; `run_library` skips with its log line; each Plex-only route refuses with the sentence; the status endpoint's `capabilities` says so. Through the real ASGI app in the deep lane; every new `test_api_*.py` assigned a lane (`tests/test_ci_path_filters.py` refuses an unassigned one).

### 10.6 End to end, through the real pipeline
- **Dual:** webhook → Plex resolves, Jellyfin `ItemNotFound` → Plex delivered, Jellyfin `pending` → the retry pass → both `uploaded` → roll-up `uploaded`.
- **Jellyfin-only:** the same webhook, no Plex configured → one ref, one delivery, nothing refused.
- **Path mismatch on one server:** that delivery `failed`, the other `uploaded`, roll-up `failed`.

### 10.7 Wizard and frontend
Jellyfin-only and dual paths through the wizard's real routes to `POST /finish`. Vitest: servers pane, Jellyfin card, finish summary, per-server ids on the item page, capability-hidden pages, and the moved assertions in §8.

## 11. Verification items (resolve on the throwaway instance before the Jellyfin client is built)

| # | Question | Why it matters |
|---|---|---|
| V1 | Exact `Authorization` header format accepted by 12.0 (`MediaBrowser Token="…"` with which of Client/Device/DeviceId/Version required). | Every request. |
| V2 | `ProviderIds` key casing as served (`Tmdb`/`Tvdb`/`Imdb`?) and whether Season items carry any. | The index (§4.4) and season resolution. |
| V3 | Whether `SetItemImage` accepts raw image bytes with `Content-Type: image/jpeg`, or requires a base64-encoded body (a known behaviour of earlier versions). | Artwork delivery. The spec says `image/*`; the wire behaviour must be observed. |
| V4 | Whether an uploaded image survives (a) a routine library scan and (b) `POST /Items/{id}/Refresh` with `imageRefreshMode=FullRefresh` and `replaceAllImages=true`. The spec shows replacement is opt-in per refresh call, which suggests a routine scan leaves uploads alone; `LockedFields` names no image field at all. | Whether "no lock" on Jellyfin means "may be overwritten by a refresh", and what to tell the operator about scheduled scans. |
| V5 | `VirtualFolders.Locations` shape and whether episode `Path` is absolute as mounted. | Root-folder derivation and `PathMismatch`. |
| V6 | Whether `searchTerm` with `years` returns seasons/episodes or only top-level items. | The miss path (§4.4 step 3). |

## 12. Compatibility and rollout

- Existing configs are valid unchanged; `plex:` present and `jellyfin:` absent is a Plex-only deployment identical to today.
- The migration runs at boot (`autoposter.boot` → `alembic upgrade head`) on the next Flux deploy; the collision rule (§6.5) is what makes that safe. `migrate-preview` before merging is the operator's check.
- Feature visibility: nothing Jellyfin-related appears in the UI until a `jellyfin` block exists.
- Webhook consumers see an added `refs` field and an unchanged `rating_key`.
- The operator follow-up after merge: none for Plex-only; for a new Jellyfin server, the wizard or the `jellyfin:` block plus the key.

## 13. Follow-ups filed by this spec

- Row for sub-project 3 (tag-set), grounded in the capability set and the gate this spec introduces; the `GATED` naming hazard carries over.
- Row for sub-project 4 (collections, playlists, metadata and adopt on Jellyfin).
- Row for Emby.
- Row for a Jellyfin container in CI.
- Row for the sort-order feature (TMDB collection order → sort titles): a builder feeding `apply_facts` with `sort_title`, server-agnostic by construction. **Coordination:** it must write through `apply_facts` and never touch `plexapi` directly, or it will conflict with §3.3.
- Rename the residual `rating_key` in webhook payloads to a server-neutral shape in a future major, once consumers are known.

## 14. Suggested implementation phases (not binding)

1. Protocol, `ServerItemRef`, capabilities, `FakeMediaServer`; `PlexClient` conforms; call sites take refs. No behaviour change; full suite green.
2. Identity migration: refs, identity key, deliveries, collision merge, `migrate-preview`; `RenderIntent.refs`; the fourteen dispositions. Full suite green; up/down/up proven.
3. Servers as a set: optional `plex:`, secrets category, boot rule, `app.state.servers`, the gate (§9), `capabilities` on status. A Plex-less app boots.
4. Jellyfin client, index, artwork, writer, health; contract capture; V1–V6 resolved on the throwaway instance first.
5. Delivery fan-out, deliveries recording, pending retry pass, roll-up; the three end-to-end runs.
6. Wizard media-server phase; moved assertions.
7. Docs: example config, deploy README, `.env.example`, roadmap row 267 closed against this spec.

## 15. Amendments recorded during implementation

What implementation decided that this document did not, or decided differently.
Each entry supersedes the section it names; the body above is left as approved
so that what changed, and why, stays readable. Dated by the day the change
landed on the branch.

### The protocol, identity and the migration (§3, §4, §6.5)

- **2026-09-12, §3.** `MediaServer.fetch_artwork` returns `tuple[bytes, str] | None` — the bytes and the upstream content type — not `bytes | None`. `api/artwork.py` builds its HTTP response's media type from that content type and now routes through `server.fetch_artwork`, `plex_artwork.fetch_artwork` already returned the pair, and Jellyfin's image GET carries a `Content-Type`. Applies to the protocol, `FakeMediaServer`, `PlexClient` and the conformance case.
- **2026-09-12, §6.5.** The collision merge re-points **every** child table of `media_items` — `item_facts`, `item_credits`, `item_metadata_overrides`, `action_dismissals`, `renders` — deduping on each table's own real unique key rather than on `item_id` alone. §6.5's enumeration omitted `action_dismissals`, and its "overrides re-pointed" was not what the migration actually does.
- **2026-09-12, §4.2.** A show or season with no provider id keys on the path branch with the file slot filled by its root folder (`show:path:::<root_folder>`, `season:path::s2:<root_folder>`): the folder is shared storage and therefore server-neutral. The legacy key stays migration-only, and the raise remains for an item with truly nothing to key on.
- **2026-09-12, §4.2.** `adopt/walk._resolved_episode` fills `file_path` from the plexapi episode's media parts exactly as the resolver does, so an adopted episode and a resolved one key identically.
- **2026-09-12, §4.2 ("Episode parents").** The parent model is unchanged: an episode's `parent_id` is its **season** row and a season's is its show. `parent_identity_key_for(episode)` returns the season's key (the show's provider ids plus `s{n}`), and `adopt/walk._resolved_season` takes the show's guids so adopted and resolved seasons key identically. An earlier "episode parents to the show" test was a slip, not a design change.
- **2026-09-12, §4.2 (second amendment, superseding the first).** The file slot is the basename of `file_path` for a **movie only**; it is empty for show, season and episode, because no producer supplies an episode file — one Plex episode is one item. A provider-less episode therefore keys as `episode:path::s2e3:<root_folder>`. Adopted episodes revert to `file_path=None`; adopted seasons and episodes carry the show's guids as their parent ids. The bare-key promotion introduced above is removed (nothing produces the shape it caught); the legacy-key promotion is kept and gated on `server == "plex"`.
- **2026-09-13, §4.1.** **One ref per `(item, server)` is an invariant**, not an allowance: `upsert_server_ref` deletes the item's other refs for that server, and `_merge_into` drops the stale row's refs for servers the survivor already has. The "several rows per server" wording is withdrawn.
- **2026-09-13, §4.2/§4.6.** The same Plex ref plus the same `kind:provider:id:coords` prefix with a **different** file slot is a file replacement: the existing row is re-keyed in place, keeping its overrides, dismissals and renders. A *different* ref with the same prefix stays a separate item — that is the 4K/HD pair. Legacy promotion is this same mechanism's special case.
- **2026-09-13, §4.4 step 2.** The basename lookup is **deferred**. `RenderIntent` carries no path, so nothing can drive it; the index entries it would have needed are removed. Revisit if intents ever carry a path.
- **2026-09-13, §4.4 step 6.** `LibraryIndex` auto-invalidates once `built_at` is `max_age_seconds=3600` old (monotonic), and `JellyfinClient` exposes `invalidate()`. The full pass invalidates at pass start — iterating every server in the registry and duck-typing the method (`getattr(server, "invalidate", None)`) rather than naming Jellyfin, because a server with no index to go stale simply has nothing to call. An earlier note's `servers.jellyfin.invalidate()` was the shape, not the call.
- **2026-09-13, §4.7.** `migrate_preview.preview()` selects `root_folder` and passes it to `identity_key`, so its report matches what the migration computes. The planned "run it against the dev database before the migration" step was not performed here: the development databases are per-worker scratch, and the operator's production database is the real target — the command is named in the Phase 2 PR body instead.

### The Jellyfin client (§1.1, §11)

- **2026-09-13, §1.1 / V1.** `Authorization: MediaBrowser Token="<key>"` is accepted on 12.0 with or without the `Client`/`Device`/`DeviceId`/`Version` fields; the legacy `X-Emby-Token` header answers `401` and is not used. Verified live against a 12.0.0 instance.
- **2026-09-13, §1.1 / V3.** `POST /Items/{itemId}/Images/{imageType}` answers `500` for the raw image bytes the spec's `requestBody` describes. The body must be **base64-encoded**, with the real image `Content-Type` unchanged; `JellyfinApi.set_image` encodes accordingly. This is a live-server behaviour the spec does not state anywhere.

### Delivery (§5)

- **2026-09-13, §5.** **One intent is one row.** The first server that resolves the intent — Plex when this deployment has one — sets the item's identity; every other server adds a ref to that row and never a row of its own.
- **2026-09-13, §5.2.** **Render once.** The unchanged-work gate keys on the render fingerprint alone, not on the fingerprint plus an `uploaded` status. A `failed` delivery is re-armed `pending` once per full pass — it costs no ImageMagick work for the servers that already hold the bytes — while `uploaded` and `skipped` are left alone.
- **2026-09-13, §5.3.** The retry pass composes from the **identity server** (Plex when the item has a Plex ref) and never writes the fingerprint; the fingerprint stays the full pass's to write.
- **2026-09-13, §5.3.** On retry, a resolution miss stays `pending` with backoff, while a compose or upload exception records `failed` — the distinction the pipeline itself already draws. One row's unexpected exception never aborts the pass.
- **2026-09-13, §5.3.** On an **unchanged** fingerprint, any configured server with no delivery row for that render (or a `pending` one) is recorded `pending` with `retry_in=0`, so the pending-deliveries pass delivers it from the already-badged asset. A server enabled after the fact catches up without anything being re-badged.
- **2026-09-13, §5.2.** `uploaded_at` survives a later failed delivery: a `failed`, `skipped` or `pending` outcome never erases what that server did deliver last time, and the roll-up carries the column forward rather than overwriting it with `NULL`.
- **2026-09-13, §5.4.** Adoption ignores the delivery rows the identity migration backfilled.
- **2026-09-13, §5.5.** The prune sweep retires refs per server only in an **apply** pass, and only past the cap. Its job registration is still gated on Plex, so a Jellyfin-only deployment runs no prune yet; that is a follow-up row, not a decision.

### The wizard (§8)

- **2026-09-13, §8.** The media-server step's gate is `config/schema.missing_server_setup` itself — at least one server configured, and every configured server's credential held — asked by the step rather than restated as a second expression that happens to agree with `boot.is_configured` today.
- **2026-09-13, §8.** `checked` is a per-system **session** fact (`checks_passed`), reported beside `configured` and `credential` and never persisted. A successful check is not a second way to answer the step: a probe is not a decision to run a server.
- **2026-09-13, §8.** A card saved on its own keeps the other server's block. The two cards may be submitted in one body or one at a time, and a server the submitted body does not name keeps whatever an earlier submit of the same session staged for it — while losing the example document's placeholder block, and losing a block for a server that was only checked.
- **2026-09-13, §8.** The `jellyfin` check is `GET /System/Info` — authenticated, not `/System/Info/Public` — for the same reason Plex's is `/library/sections`: the probe has to prove the credential, not merely that the host answered.
