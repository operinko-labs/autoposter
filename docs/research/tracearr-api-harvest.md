# Tracearr API harvest — roadmap row 17 (REST half)

Harvested 2026-08-25 against the user's live instance. Everything below was
**observed**, not recalled: read out of the running container or captured as a
real HTTP response. Nothing here is inferred from upstream documentation or
from memory of what Tracearr "usually" returns.

**Status: partial harvest.** The endpoint inventory, request surface, response
schemas, pagination shape, error shapes, and rate-limit tiers are all captured
and verbatim. **Live response bodies for the data endpoints are NOT captured** —
every one of them is behind an owner API key that does not exist in any
Kubernetes secret or environment variable and can only be minted through the
web UI. See [Blocked: live payload capture](#blocked-live-payload-capture).

---

## The instance

| Fact | Value | How observed |
| --- | --- | --- |
| Namespace / deployment | `media` / `tracearr` | `kubectl get deploy -n media tracearr` |
| Image | `ghcr.io/connorgallopo/tracearr:2.1.0` | deployment spec |
| Service | `media/tracearr` ClusterIP, port **3000** | `kubectl get svc -n media` |
| In-cluster base URL | `http://tracearr.media.svc.cluster.local:3000` | service name + port |
| Entrypoint | `node apps/server/dist/index.js` | `/proc/1/cmdline` |
| Framework | Fastify (plugin tree, `reply.unauthorized`, `@fastify/rate-limit`) | `apps/server/dist/` |
| Datastores | `tracearr-timescale` (PostgreSQL/TimescaleDB), `tracearr-redis` | services + `/health` |
| Public API versions | **v1** (`1.0.0`, offset paging) and **v2** (`2.0.0`, cursor paging) | generated OpenAPI docs |

Deployment environment variables (**names only** — no values read):
`CORS_ORIGIN`, `HOST`, `LOG_LEVEL`, `NODE_ENV`, `PORT`, `TZ`, `REDIS_URL`,
`DATABASE_URL`, and three secret-backed refs from secret `tracearr`:
`JWT_SECRET`, `COOKIE_SECRET`, `DB_PASSWORD`.

**There is no API-key environment variable.** This matters — see below.

---

## Scrub log

Per the harvest discipline, the following categories were checked for and
handled before banking:

| Category | Found? | Treatment |
| --- | --- | --- |
| Usernames / display names | **none captured** — no authenticated response was obtained | n/a |
| Tracearr user ids / identity uuids | **none captured** | n/a |
| IP addresses | **none captured** | n/a |
| Session / device / player identifiers | **none captured** | n/a |
| Server uuids (`server_id`) | **none captured** — the `/docs` handler injects the live server list into the spec, but that handler is auth-gated; the specs banked here were generated from the static registry and carry **no** server ids | n/a |
| API key / bearer token | never read; the two probe requests that sent an `Authorization` header used deliberately invalid throwaway strings, and the recorded header value is `Bearer <REDACTED>` | redacted at capture time |
| Secret values (`JWT_SECRET`, `COOKIE_SECRET`, `DB_PASSWORD`) | never read | n/a |
| External hostname | the deployment's `CORS_ORIGIN` names the user's public Tracearr hostname | **not reproduced** in this document or the banked JSON |
| Media / library titles | none captured (no authenticated response) | would have been kept — same class as existing fixtures |

The banked JSON files were re-read after writing and contain none of the above.

---

## Raw files banked

| File | What it is |
| --- | --- |
| `docs/research/tracearr/openapi-v1.json` | The **complete** OpenAPI 3.0 document for Public API v1 (60,030 bytes), generated in-container by calling the server's own `generateOpenAPIDocument()` |
| `docs/research/tracearr/openapi-v2.json` | The **complete** OpenAPI 3.0 document for Public API v2 (96,042 bytes), from the server's own `generateOpenAPIDocumentV2()` |
| `docs/research/tracearr/live-unauthenticated-probes.json` | 8 real HTTP request/response pairs — the one endpoint that answers without a key, plus every auth-failure and not-found shape |

### How the OpenAPI documents were obtained

Tracearr serves its spec at `GET /api/v1/public/docs` and
`GET /api/v2/public/docs` — but **both require the API key**, so they could not
be fetched over HTTP. The specs are generated at request time from a static
`@asteasolutions/zod-to-openapi` registry by two pure, side-effect-free
exported functions. Those were invoked directly in the container:

```
node --input-type=module -e '
  import {generateOpenAPIDocumentV2} from "/app/apps/server/dist/routes/publicV2.openapi.js";
  ...JSON.stringify(generateOpenAPIDocumentV2(), null, 2)...'
```

This is byte-for-byte what the `/docs` endpoint would return, with **one**
difference, documented in the handler itself
(`apps/server/dist/routes/publicV2/index.js:32-71`): the live endpoint
post-processes the spec to (a) set `spec.servers` to the request's base path
and (b) fill the `enum` of every `serverId`/`server_id` query param with the
instance's real server uuids and names. The banked specs therefore have
**empty `serverId` enums and no server names** — which is exactly the scrub we
would have applied anyway.

The schemas in these documents are the same zod schemas the handlers validate
their output against, so they are an authoritative statement of payload shape —
but they are a *contract*, not a *sample*. Field nullability, key casing, and
enum membership are trustworthy; real-world value distributions are not
represented.

---

## Authentication

Both public API versions use one scheme, verbatim from the spec:

```json
"securitySchemes": {
  "bearerAuth": {
    "type": "http",
    "scheme": "bearer",
    "description": "API key format: trr_pub_<token>. Generate in Settings > General."
  }
}
```

The verifier is `authenticatePublicApi` (`apps/server/dist/plugins/auth.js:193`):

1. `Authorization` header must start with `Bearer ` → else 401 *"Missing or invalid Authorization header"*.
2. Token must start with `trr_pub_` → else 401 *"Invalid API key format"*.
3. Token is looked up against the `users.api_token` column → no row means 401 *"Invalid API key"*.
4. The matched user's `role` must be `owner` → else **403** *"API key is not associated with an owner account"*.

So: one key per owner account, minted from the UI, no scopes, no expiry field
in the check path. A non-owner key authenticates but is refused at every route.

### Blocked: live payload capture

The mission anticipated an API key readable in-cluster by environment-variable
reference. **There is none.** The key lives only in the Postgres `users` table,
and it is written and compared **in plaintext** (step 3 above is a literal
`eq(users.apiToken, token)` — not a hash comparison).

Reading that column would mean extracting a live credential from the database,
which is exactly the thing the project's credential discipline forbids and
which the sandbox correctly refused. No workaround was attempted.

**To finish the REST harvest, the user must:**

1. Open Tracearr → **Settings → General** → generate a Public API key.
2. Put it in the existing `tracearr` secret in namespace `media` (e.g. key
   `PUBLIC_API_KEY`), so a follow-up harvest can reference it in-cluster
   without the value ever crossing into a transcript, a report, or a fixture.

With that in place, capturing the ~14 v2 and ~9 v1 live payloads is
a short, mechanical follow-up.

---

## Endpoint inventory

Route prefixes resolve as `API_BASE_PATH = /api/v1` and
`API_V2_BASE_PATH = /api/v2` (`apps/server/dist/index.js:325-356`).

### Unauthenticated

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | The **only** endpoint that answers without a key. Liveness + dependency status. |

Any unmatched `/api/**` path returns `404 {"error":"Not Found"}`. Any unmatched
non-`/api` path returns the SPA's `index.html` (200, `text/html`, 2,463 bytes) —
so a client must not treat "got a 200" as "endpoint exists".

### Public API v1 — `/api/v1/public/*` (offset pagination)

| Method | Path | Summary |
| --- | --- | --- |
| GET | `/health` | Check server connectivity |
| GET | `/stats` | Dashboard statistics |
| GET | `/stats/today` | Today's dashboard statistics |
| GET | `/activity` | Playback activity trends |
| GET | `/streams` | Active playback sessions |
| POST | `/streams/{id}/terminate` | Terminate active stream **(the only write in the public API)** |
| GET | `/users` | User list with activity metrics |
| GET | `/violations` | Rule violations |
| GET | `/history` | Session history |
| GET | `/docs` | OpenAPI 3.0 spec (auth-gated) |

### Public API v2 — `/api/v2/public/*` (cursor pagination)

| Method | Path | Summary |
| --- | --- | --- |
| GET | `/docs` | OpenAPI specification |
| GET | `/history` | Watch history as plays |
| GET | `/streams` | Active streams |
| GET | `/media/{ref}` | Media identity and availability |
| GET | `/media/{ref}/children` | Media children |
| GET | `/media/{ref}/stats` | Media play statistics |
| GET | `/media/{ref}/watchers` | Media watchers |
| GET | `/media/{ref}/history` | Media watch history |
| GET | `/users` | Identities with account correlation |
| GET | `/users/{id}` | One identity |
| GET | `/users/{id}/stats` | Identity play statistics |
| GET | `/users/{id}/history` | Identity watch history |
| GET | `/recently-added` | Recently added library items |
| GET | `/libraries` | Per-library rollups |

v2 is entirely read-only. It is the version a new integration should target.

### Internal API — `/api/v1/*` (session JWT, **not** the public contract)

Registered alongside the public routes but guarded by `app.authenticate`
(cookie/JWT session), not `authenticatePublicApi`. Recorded here because one of
these is the endpoint row 79 would most want, and it is **not reachable with an
API key**:

`/api/v1/` + `setup`, `auth`, `servers`, `users`, `server-users`, `sessions`,
`rules`, `violations`, `stats`, `settings`, `settings/notifications`, `import`,
`images`, `debug`, `mobile`, `notifications`, `version`, `maintenance`,
`tailscale`, `tasks`, `library`, `backup`.

Within those, the ranking endpoints are:

| Path | Handler |
| --- | --- |
| `GET /api/v1/stats/top-content` | `routes/stats/content.js` — top movies **and** top shows by play count |
| `GET /api/v1/library/top-movies` | `routes/library/topContent.js` |
| `GET /api/v1/library/top-shows` | `routes/library/topContent.js` |

`/api/v1/stats/top-content` accepts `period`, `startDate`, `endDate`,
`serverId`/`serverIds`, and returns, per row: `media_title`, `year`,
`play_count`, `total_watch_ms`, `thumb_path`, `server_id`, `rating_key`
(shows additionally carry `grandparent_title` and `episode_count`).
**`LIMIT 10` is hardcoded in the SQL** and there is no limit parameter.

---

## Pagination

Two different shapes; a client must not share code between them.

**v1 — offset.** `page` (default 1) and `pageSize` (default 25) query params;
response carries `meta: PaginationMeta`:

```
PaginationMeta { total: integer, page: integer, pageSize: integer }
```

**v2 — opaque cursor.** `cursor` and `pageSize` (default 25, **min 0, max
100**) query params; response carries `meta: CursorMeta`:

```
CursorMeta { nextCursor: string|null, pageSize: integer }
```

Pass `meta.nextCursor` back as `cursor`; `null` means the end. Two documented
properties worth relying on:

- `/history` cursors operate on **whole plays**, so a resume chain never splits
  across a page boundary.
- `/recently-added` cursors page on the `(added_at, id)` tuple, so items sharing
  an added timestamp — the common case after a bulk sync — page without skips
  or duplicates.

An unreadable cursor returns **400**.

---

## Error and rate-limit shapes (live, verbatim)

All captured as real responses; full set in
`docs/research/tracearr/live-unauthenticated-probes.json`.

**401, missing header** — `GET /api/v1/public/health`, no `Authorization`:

```json
{"statusCode":401,"error":"UnauthorizedError","message":"Missing or invalid Authorization header"}
```

**401, wrong prefix** — `Authorization: Bearer <string not starting trr_pub_>`:

```json
{"statusCode":401,"error":"UnauthorizedError","message":"Invalid API key format"}
```

**401, unknown key** — `Authorization: Bearer trr_pub_<invalid>`:

```json
{"statusCode":401,"error":"UnauthorizedError","message":"Invalid API key"}
```

**404, unmatched API path** — `GET /api/v1/public/does-not-exist` and
`GET /api/v3/public/history` both:

```json
{"error":"Not Found"}
```

Note the **two different error envelopes**: route handlers produce the
four-field Fastify-sensible shape (`statusCode`/`error`/`message`), while the
not-found fallback produces a bare `{"error": ...}`. A client parsing errors
must tolerate both.

Documented but not captured (would require a valid key): **403**
`"API key is not associated with an owner account"`, **400** on a bad cursor or
`since > until`, **429** on rate-limit exhaustion.

**Rate limits** are advertised on every response, and the two API versions sit
in different buckets (observed on live responses):

| Surface | `x-ratelimit-limit` | Window |
| --- | --- | --- |
| `/health`, `/api/v1/public/*` | **1000** | 60 s |
| `/api/v2/public/*` | **240** | 60 s |

Headers returned: `x-ratelimit-limit`, `x-ratelimit-remaining`,
`x-ratelimit-reset` (seconds). The v2 budget is **shared across the whole key**,
not per-route — the plugin registers one limiter for the entire v2 tree
(`routes/publicV2/index.js:23-27`), with a comment stating that per-route config
would multiply the per-token budget by route count. The v2 limit is also
resolved lazily per request from settings, so it is **operator-configurable**
and 240 should be read as "the current value", not a constant.

**`/health` live response** (the one real data payload obtained):

```json
{
  "status": "ok",
  "mode": "ready",
  "db": true,
  "redis": true,
  "geoip": true,
  "tailscale": "disabled",
  "timescale": {
    "installed": true,
    "hypertable": true,
    "compression": true,
    "aggregates": 3,
    "chunks": 58,
    "compressionDegraded": false
  }
}
```

`mode` is the field to gate on — the server answers `/health` while still
booting or in maintenance, and the routes can be registered before the DB is
reachable by design.

---

## Response shapes for the builder-relevant surface

Field lists below are transcribed from the banked specs. `?` marks nullable.

### `GET /api/v2/public/history` — the workhorse

Cursor-paginated watch history, **newest first**, one record per *play*.
Verbatim semantics from the spec:

> A play is one resume chain: sessions are grouped by
> `COALESCE(reference_id, id)`, where `reference_id IS NULL` marks the chain
> start. Chains where no session reaches 2 minutes are excluded
> (`COALESCE(duration_ms, 0) >= 120000`). Rating keys the media server never
> provided are returned as null.

Query params: `cursor`, `pageSize` (0–100, default 25), `user_id`, `server_id`,
`media_id`, `rating_key`, `imdb_id`, `tmdb_id`, `tvdb_id`,
`media_type` (`movie|episode|track|live|photo|unknown`), `watched` (bool),
`since`, `until` (date or ISO datetime; `until` must not precede `since`).

`media_id` is hierarchical: *"A show id matches all of its episodes and a season
id matches that season"*, and ids merged into the given id are matched too.

`HistoryResponse { data: HistoryRecord[], meta: CursorMeta }`

`HistoryRecord` — 70 fields. The ones that matter for collection building:

- **identity:** `media_id`, `show_media_id?`, `imdb_id?`, `tmdb_id?`,
  `tvdb_id?`, `rating_key?`, `parent_rating_key?`, `grandparent_rating_key?`,
  `library_id`, `genres: string[]`
- **titles:** `media_title`, `show_title?`, `year?`, `season_number?`,
  `episode_number?`, `artist_name?`, `album_name?`
- **play measures:** `duration_ms`, `progress_ms`, `total_duration_ms`,
  `percent_complete`, `watched: boolean`, `segment_count`, `reference_id?`
- **time:** `started_at`, `stopped_at?`
- **who/where:** `user: HistoryUser`, `server_id`, `server_name`, `server_type`
- **art:** `thumb_path?`, `poster_url?`
- the remaining ~30 are stream-technical (codecs, transcode decisions,
  bitrate, resolution, subtitle info) — irrelevant to collection building

`HistoryUser { id, server_user_id, username?, thumb_url?, avatar_url? }`
— **this is the PII-bearing block** any future fixture must scrub.

### `GET /api/v2/public/media/{ref}/stats`

`ref` is a canonical media uuid **or** a type-qualified provider ref:
`{movie|show|episode}:{imdb|tmdb|tvdb}:{id}` — e.g. `movie:tmdb:584`,
`show:tvdb:81189`. Seasons have no provider ref; reach a season uuid through a
show's `children`.

```
MediaStatsResponse {
  media_id, media_type,
  windows: { all_time: StatWindow, last_30: StatWindow, last_7: StatWindow }
}
StatWindow { combined: {plays, watch_time_ms, unique_users}, per_server: StatServerMeasures[] }
StatServerMeasures { server_id, server_name?, plays, watch_time_ms, unique_users }
```

Windows are **UTC calendar days** (`day >= current UTC date - N + 1`), so
`last_7` includes today. Cached 60 s. Movies and episodes roll up by canonical
media id; shows roll up their episodes; **seasons compute live from raw
sessions** and count a chain when *any* segment reaches 2 minutes, so — per the
spec's own warning — a season total can exceed the sum of its episodes.

`plays` is defined as *"Resume chains whose first session reached 2 minutes,
from the daily rollup. Plays on media Tracearr could not identify are not
counted."*

### `GET /api/v2/public/media/{ref}/watchers`

Params: `ref`, `window` (`all_time|last_30|last_7`), `server_id`.

```
MediaWatchersResponse { media_id, media_type, window, watchers: Watcher[] }
Watcher { user: WatcherUser, plays, watch_time_ms, completion_pct,
          last_watched_day?, distinct_episodes_watched? }
WatcherUser { server_user_id, user_id, username?, identity_name? }
```

### `GET /api/v2/public/recently-added`

Params: `cursor`, `pageSize`, `server_id`, `library_id`,
`media_type` (`movie|episode|season|show|artist|album|track|photo`),
`include_removed`. Ordered by **server-reported** added date, newest first.

```
RecentlyAddedRecord { id, server_id, server_type, library_id, media_type,
                      title, year?, added_at, removed_at?, media_id?,
                      imdb_id?, tmdb_id?, tvdb_id?, rating_key?,
                      parent_rating_key?, grandparent_rating_key? }
```

### `GET /api/v2/public/media/{ref}`

```
MediaResource { id, media_type, title, year?, imdb_id?, tmdb_id?, tvdb_id?,
                genres: string[], show_media_id?, merged_ids: string[],
                availability: MediaAvailability[], season_count?, episode_count? }
MediaAvailability { server_id (uuid), server_type (plex|jellyfin|emby),
                    library_id, rating_key, added_at, removed_at?,
                    video_resolution? }
```

`availability` is how a Tracearr media identity maps back to a **Plex rating
key** — the join autoposter needs to turn a Tracearr ranking into Plex items.

### `GET /api/v2/public/libraries`

```
LibraryRollup { server_id, server_type, library_id, item_count, movie_count,
                episode_count, show_count, track_count, total_file_size,
                resolutions: { [token: string]: integer } }
```

`resolutions` counts each title once at its best version (a 4K+1080p pair lands
only in `4k`); keys are lowercase tokens, unknown resolution keys as
`"unknown"`. Cached 60 s.

### `GET /api/v2/public/users/{id}/stats`

```
UserStatsResponse { user_id,
                    windows: { all_time, last_30, last_7 }   // each {plays, watch_time_ms}
                    top_genres: UserGenre[] }
UserGenre { genre, plays }
```

`top_genres` is the **only** "top N" list anywhere in the public API — and it
ranks genres for one user, not media.

### v1 equivalents (for completeness)

- `GET /api/v1/public/stats` → `{activeStreams, totalUsers, totalSessions, recentViolations, timestamp}`
- `GET /api/v1/public/activity` → `{period, range, plays[], concurrent[], byDayOfWeek[], byHourOfDay[], platforms[], quality}`; params `period` (`week|month|year`, default `month`), `serverId`, `timezone` (IANA, default UTC). Time-series bucketed by 6 h (week) or 1 day (month/year); play counts use the same ≥2-minute engagement filter.
- `GET /api/v1/public/history` → same domain as v2 `/history` but **camelCase** field names (`mediaTitle`, `startedAt`, `durationMs`, …) and offset paging. v2 is snake_case. Do not mix.
- `GET /api/v1/public/users` → `User {id, username, displayName, thumbUrl, avatarUrl, role, trustScore, totalViolations, serverId, serverName, lastActivityAt, sessionCount, createdAt}` — one row **per server** for multi-server users.

---

## What the builders need — row 79 mapping

Row 79 wants "most-watched / popular collections from Tracearr's read-only REST
API (the role of Kometa's Tautulli builders)". Mapping those concepts onto what
actually exists:

| Row 79 concept | Endpoint that provides it | Verdict |
| --- | --- | --- |
| **Most-watched movies (top N)** | *none* | **NO ENDPOINT.** Must be computed client-side. |
| **Most-watched shows (top N)** | *none* | **NO ENDPOINT.** Must be computed client-side. |
| **"Popular" (trending / rising)** | *none* | **NO ENDPOINT.** No trending, no rank, no popularity score. |
| Play count for a **known** item | `GET /api/v2/public/media/{ref}/stats` | Direct — but requires knowing the item first, and is one HTTP call per item. |
| Recently added | `GET /api/v2/public/recently-added` | **Direct match**, cursor-paginated, filterable by library and media type. |
| Activity trends (charts) | `GET /api/v1/public/activity` | Direct — plays over time, day-of-week, hour-of-day, platforms. Not a media ranking. |
| Per-library totals | `GET /api/v2/public/libraries` | Direct. |
| Who watched a title | `GET /api/v2/public/media/{ref}/watchers` | Direct. |
| Tracearr identity → **Plex rating key** | `GET /api/v2/public/media/{ref}` → `availability[].rating_key` | Direct. **This is the join autoposter needs.** |

### The headline finding

**The public API exposes no top-N ranking of media, in either version.** This
was verified, not assumed: no `sort`, `order`, `top`, or `limit` query parameter
exists on any of the 23 public paths, and the only `top_*` field in either spec
is `top_genres` on the per-user stats response.

Meanwhile `GET /api/v1/stats/top-content` — which returns exactly the top-10
movies and shows by play count that row 79 describes — **exists but is on the
internal API**, guarded by the browser session JWT rather than
`authenticatePublicApi`. An API key cannot reach it. Building against it would
mean simulating a browser login against an undocumented, unversioned surface;
that is not a contract autoposter should depend on.

### So row 79 must aggregate `/api/v2/public/history` itself

This is entirely feasible and is the recommended design:

1. Page `GET /api/v2/public/history` with `since` set to the collection's window
   (`pageSize=100`, follow `meta.nextCursor` until null), optionally filtered by
   `media_type` and `server_id`.
2. Group by `show_media_id` for shows (it is present on every episode record)
   or `media_id` for movies. Both are canonical and merge-aware, so the same
   title across two servers folds into one bucket without title normalization.
3. Count plays — the records are already de-duplicated into resume chains and
   already filtered to ≥2 minutes, so a plain count of records **is** the play
   count Tracearr itself reports. Sum `duration_ms` for watch time. Filter on
   `watched` for completion-based rankings.
4. Sort, take N, then resolve each `media_id` through
   `GET /api/v2/public/media/{ref}` → `availability[].rating_key` to reach the
   Plex items autoposter builds collections from.

Notes for whoever implements it:

- Every record already carries `imdb_id`/`tmdb_id`/`tvdb_id`, so a builder can
  short-circuit step 4 and match on external ids where autoposter already does.
- `genres: string[]` is on every history record, so genre-scoped most-watched
  collections need no extra calls.
- The **240 req/min shared v2 budget** is the real constraint. A 30-day window
  at `pageSize=100` is a handful of calls; a naive per-item `/media/{ref}/stats`
  fan-out across a large library is not. Prefer the aggregate-from-history
  approach over per-item stats calls.
- Recompute rather than cache ranks: the server-side stat endpoints cache 60 s,
  but `/history` does not, and the window semantics (UTC calendar days) differ
  between `/history` (`since`/`until` are instants) and `/media/{ref}/stats`
  (`last_7`/`last_30` are UTC day buckets). **Do not mix the two and expect the
  numbers to agree.**

---

## What remains unharvested

1. **Live REST payloads.** Blocked on the owner API key — see
   [Blocked: live payload capture](#blocked-live-payload-capture). Schemas are
   banked; real bodies are not. Nothing should be treated as verified against
   real value distributions until this is done.
2. **The webhook half of row 17.** Out of scope here by construction: capturing
   real Tracearr outbound webhook payloads requires autoposter's row 5c intake
   endpoint to exist and be reachable, plus the user pointing Tracearr's
   outbound custom-webhook config at it. Both are writes to live systems and
   sit outside this row's read-only remit. Tracks with row 21 (Tracearr webhook
   intake).
