# Tracearr API harvest — roadmap row 17 (REST half)

Harvested 2026-08-25 against the user's live instance. Everything below was
**observed**, not recalled: read out of the running container or captured as a
real HTTP response. Nothing here is inferred from upstream documentation or
from memory of what Tracearr "usually" returns.

**Status: the REST half is complete.** The endpoint inventory, request surface,
response schemas, pagination shape, error shapes, and rate-limit tiers were
captured on 2026-08-25 (first pass). **Live response bodies were captured on
2026-08-25 (second pass)**, after the user provisioned an owner API key as
`AUTOPOSTER_TRACEARR_APIKEY` in the autoposter deployment — 33 real requests
against every public v2 path, a representative v1 set, and every error shape.
See [Live capture](#live-capture-2026-08-25). The webhook half of row 17 is
still open by construction.

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

| Category | Found in the live capture? | Treatment |
| --- | --- | --- |
| Usernames / display names / identity names | **yes** — 13 distinct values across `HistoryUser.username`, `UserIdentity.username`, `UserAccount.username`, `WatcherUser.username`/`identity_name`, v1 `displayName` | → `user_1` … `user_13` |
| Email addresses | **yes** — 8 distinct (`UserIdentity.email`) | → `user_N@example.invalid` |
| External account ids (`external_user_id`, `plex_account_id`) | **yes** — 10 distinct | → `extuser_N` |
| Tracearr user / identity / server-user uuids | **yes** — 26 distinct (`user.id`, `user_id`, `server_user_id`) | → `00000000-0000-4000-8000-0000000000NN` |
| Session / play uuids (`HistoryRecord.id`, `reference_id`, and the uuids **inside** cursors) | **yes** — 59 distinct | → `22222222-2222-4222-8222-0000000000NN` |
| Server uuid + server name | **yes** — 1 of each (single-server instance) | → `11111111-1111-4111-8111-000000000001`, `server_1` |
| Avatar / thumb URLs on user objects | **yes** — 10 distinct | → `https://avatar.example.invalid/N.jpg` |
| Device / player strings | **yes** — 6 device, 9 player values | → `device_N` / `player_N` |
| Hostnames that are not the in-cluster service | **yes** — 4, incl. the public Tracearr hostname echoed in `access-control-allow-origin` | → `host-N.example.invalid` |
| IP addresses, geo fields | none present in any public v2/v1 response | n/a (rule was armed anyway) |
| API key / bearer token | **never read.** Referenced only as `$AUTOPOSTER_TRACEARR_APIKEY` inside the cluster; the recorded request field is the literal `Bearer <REDACTED>` written at capture time | never left the pod |
| Secret values (`JWT_SECRET`, `COOKIE_SECRET`, `DB_PASSWORD`) | never read | n/a |
| Media / library titles, media uuids, external media ids, Plex rating keys | **yes — deliberately kept.** Same class as the existing fixtures: the user's own library | kept verbatim |

Two scrub notes worth knowing before using the fixtures:

1. **Pseudonyms are applied by whole-string replacement**, so a sensitive value
   that is a substring of a benign one takes the benign one with it. Real
   example: a device string is also a word inside `product`, so `product` reads
   `"Plex for device_5 (TV)"` and `platform` reads `"device_4"`. Treat
   `device` / `player` / `product` / `platform` values in these fixtures as
   **shape only**, never as the real enum.
2. **Cursors were decoded, re-mapped, and re-encoded**, because a v2 cursor is
   base64 of `{"t":<iso8601>,"id":<session uuid>}` and therefore smuggles a real
   session uuid past a plain substring scan. The banked cursors are structurally
   faithful and **not replayable**.

**Verification, run in-container over the banked output:** every one of the 133
mapped real values was counted across the 33 scrubbed files *and* across the
decoded contents of all 9 base64 tokens found in them — total occurrences **0**.
The API key value is absent, and the literal `trr_pub` does not appear. A
follow-up scan of the exported files found no residual `http(s)://` host other
than `avatar.example.invalid` and `host-1.example.invalid`.

---

## Raw files banked

| File | What it is |
| --- | --- |
| `docs/research/tracearr/openapi-v1.json` | The **complete** OpenAPI 3.0 document for Public API v1 (60,030 bytes), generated in-container by calling the server's own `generateOpenAPIDocument()` |
| `docs/research/tracearr/openapi-v2.json` | The **complete** OpenAPI 3.0 document for Public API v2 (96,042 bytes), from the server's own `generateOpenAPIDocumentV2()` |
| `docs/research/tracearr/live-unauthenticated-probes.json` | 8 real HTTP request/response pairs — the one endpoint that answers without a key, plus every auth-failure and not-found shape |
| `docs/research/tracearr/payloads/*.json` | **33 scrubbed live captures** from the authenticated second pass, one file per endpoint-shape. Each file is `{name, request, status, response_headers, body}` so the headers are banked alongside the body |

The `payloads/` files, by group:

| Prefix | Files |
| --- | --- |
| `v2-history-*` | `page1` (pageSize 10), `page2-cursor` (the same query with `meta.nextCursor` fed back), `since-window` (`since=2026-07-26`, pageSize 50 — the aggregation sample), `movies` (`media_type=movie`) |
| `v2-media-*` | `movie-by-tmdb-ref`, `movie-by-uuid`, `show-by-uuid`, `show-by-tvdb-ref` (a **404** — see below), `show-children`, `movie-stats`, `show-stats`, `movie-watchers`, `show-history` |
| `v2-users-*` | `v2-users` (list), `v2-users-one`, `v2-users-stats`, `v2-users-history` |
| other v2 | `v2-libraries`, `v2-recently-added`, `v2-streams`, `v2-docs-delta` (reduced — see the file's own `note`) |
| v1 | `v1-stats`, `v1-activity-week`, `v1-history`, `v1-users` |
| errors | `err-401-no-auth`, `err-404-unknown-media-ref`, `err-400-malformed-ref` (a 404 in practice), `err-404-unmatched-api-path`, `err-200-spa-trap`, `err-400-bad-cursor`, `err-400-until-before-since`, `err-400-pagesize-over-max` |

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

### Resolved: how the live capture was authenticated

The first pass was blocked because the key lives only in the Postgres `users`
table, in plaintext (step 3 above is a literal `eq(users.apiToken, token)`, not
a hash comparison), and reading it out of the database is forbidden.

The user resolved this by minting an owner key in **Settings → General** and
provisioning it as **`AUTOPOSTER_TRACEARR_APIKEY`** in the `autoposter-secret`
Secret, which the `media/autoposter` deployment loads via `envFrom`. The capture
therefore ran **inside the autoposter pod**, referencing the key only as an
environment variable:

```
kubectl exec -n media deploy/autoposter -- python3 /tmp/cap.py
#   ... headers = {"Authorization": "Bearer " + os.environ["AUTOPOSTER_TRACEARR_APIKEY"]}
```

The pod image has **no `curl`** — it has Python 3.14.7 with `httpx` 0.28.1, which
is what the capture and scrub scripts used. Scrubbing ran in the same pod so the
raw bodies never left it; only the scrubbed files were exported (as a zip over
`kubectl exec` + base64, to avoid shell encoding damage on Windows). The key
value never entered a command line, a log, this document, or any fixture.

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

The **400** shapes (bad cursor, `since > until`, out-of-range `pageSize`) were
captured in the authenticated second pass — see
[Error shapes, confirmed live with a key](#error-shapes-confirmed-live-with-a-key),
which also corrects two guesses made here. Still not captured: **403**
`"API key is not associated with an owner account"` (needs a second, non-owner
key) and **429** (would mean exhausting the user's shared budget).

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
  `library_id`, `genres: string[]` — **but see the live capture**: `genres` is
  in fact null on every episode record, `media_id` can be null, and on episodes
  the three external ids are the *episode's*, not the show's
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
- `GET /api/v1/public/history` → **camelCase** field names (`mediaTitle`, `startedAt`, `durationMs`, …) and offset paging; v2 is snake_case. **This is not a mirror of v2 `/history`** — the live capture showed it returns raw sessions (no resume-chain grouping, no ≥2-minute filter) and carries **no media identity fields at all**, so it cannot be joined to Plex. See [v1 is not a camelCase mirror of v2](#v1-is-not-a-camelcase-mirror-of-v2--do-not-treat-it-as-one).
- `GET /api/v1/public/users` → `User {id, username, displayName, thumbUrl, avatarUrl, role, trustScore, totalViolations, serverId, serverName, lastActivityAt, sessionCount, createdAt}` — one row **per server** for multi-server users.

---

## Live capture (2026-08-25)

33 requests, all from inside the autoposter pod against
`http://tracearr.media.svc.cluster.local:3000`. Everything in this section was
read out of a real response body or header; nothing is inferred from the spec.

The instance at capture time: **one** Plex server, 17 users, 11,079 sessions
(v1 `meta.total`), 3 libraries (movies 1,955 · TV 12,971 items / 284 shows ·
a second movie library 143), ~31 TB total.

### The seven things that will bite an implementer

These are the places where the live payload and the OpenAPI contract disagree,
or where the contract is technically satisfied but misleading. Every one is
reproducible from the banked files.

**1. `progress_ms` and `total_duration_ms` are JSON strings, not integers.**
The spec declares both `integer` (nullable). Observed across all 50 records of
`v2-history-since-window.json`: `duration_ms` is an `int`, while `progress_ms`
and `total_duration_ms` are `str` — `"1619000"`, `"3344132"`. Same in v1
(`progressMs`, `totalDurationMs`). A client that does arithmetic on these
without coercing gets a `TypeError`, or worse, silent string concatenation.
`duration_ms` — the field row 79 actually sums — is a genuine integer.

**2. `genres` is `null` on every episode record.** The spec declares
`genres: array<string>`, **required and not nullable**. Observed in the 50-record
window: 13 populated (all `media_type: "movie"`), **37 null (all
`media_type: "episode"`)**, zero empty arrays. It is null on
`/media/{ref}/children` season rows too. Genres *are* available per show, but
only from `GET /api/v2/public/media/{ref}` — `Silo` returns
`["Drama", "Sci-Fi & Fantasy"]` there. This directly contradicts the first
pass's note that "genre-scoped most-watched collections need no extra calls":
**for shows they need one `/media/{ref}` call each.**

**3. On episode records, `imdb_id`/`tmdb_id`/`tvdb_id` are the *episode's* ids,
not the show's.** Three `Silo` episode records carry
`tvdb_id` 11751886 / 11751885, `tmdb_id` 7173964 / 7173963,
`imdb_id` `tt39182946`. The `Silo` **show** resource carries `tvdb_id` 403245,
`tmdb_id` 125988, `imdb_id` `tt14688458`. Proven live: a
`GET /api/v2/public/media/show:tvdb:11751886` built from an episode record's
`tvdb_id` returns **404** (`v2-media-show-by-tvdb-ref.json`). The first pass's
suggestion to "short-circuit and match on external ids" is safe for **movies
only**. For shows the only correct key is `show_media_id`, resolved through
`GET /api/v2/public/media/{uuid}`. The same trap applies to
`/recently-added`: episode rows carry episode-level tmdb/tvdb ids and reach the
show only via `grandparent_rating_key`.

**4. Some plays have no media identity at all.** 2 of 50 records have
`media_id`, `show_media_id`, `library_id`, `imdb_id`, `tmdb_id` and `tvdb_id`
**all null** while still carrying `media_title`, `show_title`,
`grandparent_rating_key` and `rating_key`. Both are `Warehouse 13` episodes.
Concretely: the window holds **20** `Warehouse 13` records but only **18** are
groupable by `show_media_id` — a 10% undercount for that title if unidentified
records are silently dropped. A ranking builder must decide explicitly whether
to fall back to `grandparent_rating_key` (which is a Plex rating key and is
present) or to accept the undercount.

**5. `poster_url` points at the internal API, not at anything an API-key client
can fetch.** Observed value shape:
`/api/v1/images/proxy?server=<uuid>&url=...`. That path is on the
session-JWT internal API. `thumb_path` is the useful one — it is the raw Plex
path (`/library/metadata/63856/thumb/1787495797`).

**6. The v2 cursor is base64 of `{"t":<iso8601>,"id":<uuid>}`.** Decoded from a
real `meta.nextCursor`: `{"t":"2026-08-22T14:35:06.000Z","id":"<session uuid>"}`,
and `t` equals the `started_at` of the last record on the page. It is still to
be treated as opaque — but note that it **embeds a session identifier**, so a
cursor is not safe to log or to paste into a fixture unscrubbed.

**7. Undocumented fields are present in live responses.** `MediaAvailability`
carries a `replaces` key (null in both samples) that is in no schema.
v1 `transcodeInfo` carries `maxOffsetAvailable`, `progress`, `streamContainer`
and `throttled`; v1 `activity.plays[]` carries `serverId`. Conversely
`transcode_info.hwRequested` is documented but absent from every observed
record. A strict/forbid-extra parser will fail on this API; parse leniently.

### Nullability and shape, as actually observed

From the 50-record `since`-window sample unless noted.

| Field | Spec | Observed |
| --- | --- | --- |
| `state` | enum `playing`/`paused`/`stopped` | **only `stopped`** in history — history is finished plays |
| `media_type` | 7-value enum | `episode` (37), `movie` (13) in v2; v1 `/history` additionally returned **`live`** |
| `watched` | boolean | 46 true / 4 false |
| `segment_count` | integer | 1 for 48, >1 for 2 — resume chains are real but rare |
| `id` vs `reference_id` | both uuid | **identical in all 50 records** — the chain start's own id is the reference |
| `stopped_at` | nullable | non-null in all 50 |
| `percent_complete` | number | mixed `int` and `float` in JSON (43 int / 7 float) |
| `rating_key`, `library_id` | string, nullable | strings (`"164737"`, `"2"`); `library_id` null on the 2 unidentified rows |
| `resolution` | nullable string | only `"1080p"` observed |
| `video_decision` / `audio_decision` | enum incl. `null` | `directplay`, `copy`, `transcode` all seen |
| `source_*_details`, `stream_*_details`, `transcode_info`, `subtitle_info` | objects | sub-keys are **absent**, not null, when unknown (e.g. `source_video_details.colorSpace` present on 3 of 50). The container itself is null on some rows |
| `availability[].versions` | array | populated for the movie (codec/container/resolution/file_size), **empty for the show**; the show's `video_resolution` and `file_size` are null |
| `merged_ids` | array | `[]` on both media samples |
| `plex_account_id` | string, nullable | **null for all 10** identities |
| `email` | nullable | null on 2 of 10 |
| `accounts[].removed_at` | nullable | null on all |
| `libraries[].resolutions` | free-form object | keys observed: `4k`, `1440p`, `1080p`, `720p`, `576p`, `480p`, `sd`, `unknown` — lowercase, as documented |
| `watchers[].distinct_episodes_watched` | nullable | null for a movie |
| v1 `users[].lastActivityAt` | nullable | **null for all 5** despite `sessionCount` in the hundreds |

`GET /api/v2/public/media/{ref}/children` on a show returns **seasons only**
(`media_type: "season"`, 3 rows for a 3-season show), with
`imdb_id`/`tmdb_id`/`tvdb_id`/`genres` all null and `episode_count` per season.
Reaching episodes needs a second `children` call on a season uuid.

`GET /api/v2/public/streams` with nothing playing returns
`{"data": [], "summary": {"total":0, ..., "total_bitrate": "—", "by_server": []}}`
— note `total_bitrate` is a **display string** containing an em dash, not a
number.

### v1 is not a camelCase mirror of v2 — do not treat it as one

The first pass recorded v1 `/history` as "same domain as v2 `/history` but
camelCase". The live payloads show two substantive differences:

1. **v1 `/history` returns raw sessions, not plays.** A 15-second `live` record
   appears in the first 5 rows. There is no resume-chain grouping and no
   ≥2-minute filter. `meta.total` is 11,079 — the session count, not the play
   count.
2. **v1 `/history` records carry no media identity whatsoever.** No `mediaId`,
   no `imdbId`/`tmdbId`/`tvdbId`, no `ratingKey`, no `libraryId`, no `genres`.
   The only art reference is `thumbPath`/`posterUrl`.

**v1 `/history` is therefore useless for row 79** — it cannot be joined to Plex.
v2 is not merely the nicer option, it is the only option.

`GET /api/v1/public/activity` is genuinely useful for charts and returns what
the spec says, with one inconsistency worth guarding: `plays[].date` is
`"2026-08-18 12:00:00"` (space-separated, **no timezone**) while
`concurrent[].date` is `"2026-08-18 12:00:00+00"` (space-separated, **with**
offset) — two different formats in one response, neither of them ISO-8601 `T`
form, unlike every timestamp in v2.

### Error shapes, confirmed live with a key

| Request | Status | Body | Rate-limit headers? |
| --- | --- | --- | --- |
| `/api/v2/public/history`, no `Authorization` | 401 | `{"statusCode":401,"error":"UnauthorizedError","message":"Missing or invalid Authorization header"}` | **yes** |
| `/api/v2/public/history?cursor=garbage-cursor` | 400 | `{"statusCode":400,"error":"BadRequestError","message":"Invalid cursor"}` | yes |
| `/api/v2/public/history?since=2026-08-20&until=2026-08-01` | 400 | `{...,"message":"since must be before or equal to until"}` | yes |
| `/api/v2/public/history?pageSize=101` | 400 | `{...,"message":"Invalid query parameters"}` — **no field detail** | yes |
| `/api/v2/public/media/movie:tmdb:99999999` | 404 | `{"statusCode":404,"error":"NotFoundError","message":"Not Found"}` | yes |
| `/api/v2/public/media/not-a-valid-ref` | **404**, not 400 | same `NotFoundError` envelope | yes |
| `/api/v2/public/does-not-exist` | 404 | `{"error":"Not Found"}` — the bare envelope | **no** |
| `/this-is-not-an-api-path` | **200** `text/html`, 2,463 bytes | the SPA `index.html` | **no** |

Three things follow:

- **A malformed `{ref}` is indistinguishable from an unknown one.** Both are a
  404 `NotFoundError`. There is no 400 for a bad ref and no message detail.
- **The presence of `x-ratelimit-*` headers is the reliable tell that a route
  matched.** Both fallbacks (the bare-envelope 404 and the SPA 200) are outside
  the rate-limit plugin and carry no such headers. This is a cheaper and more
  robust check than sniffing the body, and it settles the SPA-200 trap: a
  200 with no `x-ratelimit-limit` on an `/api/**`-shaped client is a
  misrouted request, not data.
- **The rate limiter runs before authentication.** The unauthenticated 401 came
  back with `x-ratelimit-limit: 240, x-ratelimit-remaining: 239` — a request
  that fails auth still spends budget.

**429 was not captured.** Doing so would mean deliberately exhausting a shared
240/min budget on the user's live instance; not worth it. Its shape remains
inferred from the plugin, not observed.

### Rate limiting, observed

`x-ratelimit-limit` / `x-ratelimit-remaining` / `x-ratelimit-reset` (seconds)
on every matched route. The first pass's claim that the v2 budget is **shared
across the whole key rather than per-route** is now directly evidenced — a
single monotonic counter across *different* v2 paths within one window:

```
v2 /history       remaining 239      v2 /users          remaining 234
v2 /libraries     remaining 235      v2 /recently-added remaining 233
v2 /streams       remaining 232      v2 /docs           remaining 231
v2 /media/../history 222   v2 /users/../history 219   (errors) 218 … 214
```

v1 sits in its own bucket and counts down independently in the same window:
`limit 1000`, remaining 999 → 998 → 997 → 996 across four v1 calls.

The whole 33-request harvest consumed **~26 of 240** v2 and **4 of 1000** v1.

### The live `/docs` response equals the banked spec

Verified by a full recursive diff inside the container, live body vs
`docs/research/tracearr/openapi-v2.json`: **exactly 8 differences**, all four
`serverId`/`server_id` parameters gaining an `enum` of the instance's server
uuids plus a rewritten `description` listing server names. Path count 14 and
schema count 35 match. Correcting the first pass: the live response carries
**no `servers[]` key at all** — it is absent from both documents, so the
handler's post-processing does not add one. The banked specs are therefore a
faithful stand-in for `/docs`, and `openapi-v2.json` can be used offline
without caveat. See `payloads/v2-docs-delta.json`.

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

### Worked example, on real records

Run against `docs/research/tracearr/payloads/v2-history-since-window.json` —
`GET /api/v2/public/history?since=2026-07-26&pageSize=50`, one page, 50 records,
`meta.nextCursor` non-null (so this is the newest slice of the window, roughly
2026-08-14 → 2026-08-24, not the whole 30 days).

Grouping key: `record["show_media_id"] or record["media_id"]` — one line, and it
folds every episode of a show into the show's bucket while leaving movies as
themselves. Play count is `len(bucket)` because the records **are** plays
already (resume-chain-deduped, ≥2 min); watch time is `sum(duration_ms)`.

```
 50 records in
  2 skipped (media_id and show_media_id both null — the unidentified plays)
 48 folded into 21 buckets

 18 plays   633.4 min   Warehouse 13          1626e7f7-6d58-4172-817c-bd56f95a295d
  4 plays   125.8 min   Reacher               40082858-dfa4-4508-b833-61b9b2654583
  3 plays    83.0 min   Silo                  faf036e2-8459-4ace-a8ba-19486b6289c6
  3 plays    91.8 min   Colin from Accounts   e9141b5c-646d-422a-97dc-38e6dd00bd53
  2 plays    55.1 min   Lanterns              dc76248f-854d-4a6c-a8b7-80db867efaf0
  2 plays    91.3 min   Ted Lasso             1193b102-4538-44a7-b7bb-00dbe26dd69c
  2 plays   127.3 min   House of the Dragon   1a87c4a5-44ea-4fc9-899f-932f1b4adfdb
  1 play    108.9 min   Blade: Trinity        79e224e2-5dd6-447f-9ebc-c9e798300128
```

Those uuids are the real, unscrubbed canonical media ids — media identity is
kept in these fixtures. Feeding the top one back in:

```
GET /api/v2/public/media/faf036e2-8459-4ace-a8ba-19486b6289c6
  -> title "Silo", media_type "show", year 2023, season_count 3,
     episode_count 28, genres ["Drama","Sci-Fi & Fantasy"],
     tmdb_id 125988, tvdb_id 403245, imdb_id "tt14688458",
     availability[0] = { server_type: "plex", library_id: "2",
                         rating_key: "63856", added_at: "2024-11-10T02:16:26.000Z" }
```

`availability[0].rating_key = "63856"` is the Plex rating key autoposter builds
collections from. The join works end-to-end, for shows as well as movies, and
`v2-media-show-by-uuid.json` is the banked proof.

**The two skipped records matter.** Both are `Warehouse 13` episodes with a null
`media_id`, so the honest count for that title in this window is **20**, not the
18 above. Whatever row 79 does about this, it should do it deliberately.

Cross-check, and a caution: `GET /media/faf036e2…/stats` reports
`last_30.combined.plays = 5` for Silo while this history page yields 3. There is
no contradiction — the page covers ~10 days, not 30, and the two endpoints use
different window semantics. It is a live demonstration of why the numbers must
not be mixed.

Notes for whoever implements it:

- **Movies only**: a record's `imdb_id`/`tmdb_id`/`tvdb_id` can short-circuit
  step 4. **For episodes those are episode-level ids** and resolving them as a
  show is a live-verified 404 — go through `show_media_id`. See finding 3 above.
- **`genres` is null on every episode record** (finding 2). Genre-scoped
  most-watched works for movies straight from `/history`; for shows it costs one
  `GET /media/{show_media_id}` per candidate — cheap if done *after* ranking and
  truncating to N, expensive if done before.
- **Coerce `progress_ms` / `total_duration_ms`** — they arrive as strings
  (finding 1). `duration_ms`, the field this recipe sums, is a real integer.
- **Parse leniently.** Undocumented keys are present and documented keys are
  absent (finding 7); a strict model will reject live payloads.
- The **240 req/min shared v2 budget** is the real constraint, and it is now
  measured: this entire 33-request harvest cost ~26 of it, and the counter is
  demonstrably one bucket across all v2 routes. A 30-day window at
  `pageSize=100` is a handful of calls; a per-item `/media/{ref}/stats` fan-out
  across 1,955 movies is 20× over budget. Prefer aggregate-from-history.
  Budget the post-ranking `/media/{ref}` resolution calls too — they come out of
  the same 240.
- Recompute rather than cache ranks: the server-side stat endpoints cache 60 s,
  but `/history` does not, and the window semantics (UTC calendar days) differ
  between `/history` (`since`/`until` are instants) and `/media/{ref}/stats`
  (`last_7`/`last_30` are UTC day buckets). **Do not mix the two and expect the
  numbers to agree** — demonstrated above.
- Pagination behaves: page 1 and the `nextCursor` page 2 were **disjoint**
  (0 overlap on record id), strictly newest-first, and page 2 continued from
  page 1's last `started_at`.

---

## What remains unharvested

1. ~~**Live REST payloads.**~~ **Done 2026-08-25** — see
   [Live capture](#live-capture-2026-08-25) and
   `docs/research/tracearr/payloads/`. Residual gaps inside the REST half, all
   deliberate:
   - **429 shape** — not captured; would require exhausting the user's shared
     240/min budget.
   - **403** (`"API key is not associated with an owner account"`) — not
     captured; would require minting a second, non-owner key.
   - **`POST /api/v1/public/streams/{id}/terminate`** — the only write in the
     public API, deliberately not exercised against a live instance.
   - **Multi-server value distributions** — the instance has exactly one Plex
     server, so `per_server` arrays, the `server_id` filter and cross-server
     `merged_ids` were all observed at n=1. `merged_ids` was `[]` in both media
     samples; the merge behaviour is contract-only.
   - **Non-Plex `server_type`, and the `track`/`photo`/`trailer` media types** —
     absent from this library, so their enum members are unobserved.
2. **The webhook half of row 17.** Out of scope here by construction: capturing
   real Tracearr outbound webhook payloads requires autoposter's row 5c intake
   endpoint to exist and be reachable, plus the user pointing Tracearr's
   outbound custom-webhook config at it. Both are writes to live systems and
   sit outside this row's read-only remit. Tracks with row 21 (Tracearr webhook
   intake).
