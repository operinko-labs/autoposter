# Deploying autoposter

## Database

The app needs `AUTOPOSTER_DATABASE_URL` pointing at a PostgreSQL database it owns
(nothing else should write to it). Migrations run automatically at container
startup (`alembic upgrade head`), so no manual migration step is needed.

## Volumes

Mount the same NFS shares Posterizarr and Kometa already use today, at these
paths:

- `/assets`
- `/manualassets`
- `/assetsbackup`

These correspond to `assets_root`, `manual_assets_root` and `backup_root` in
`autoposter.yaml`.

`/manualassets` has a second consumer as of the list builders: a `text_file`
collection definition names its list file **relative to this mount**
(`params: {path: lists/oscars.txt}`), so hand-maintained lists live beside the
manual artwork operators already drop there. Nothing extra needs mounting, and
editing a list takes effect on the next pass with no config change and no
restart. The path is contained the same way the manual-artwork endpoint
contains its own: mount and candidate are *both* resolved before being
compared, so a path that climbs out — or a symlink sitting inside the mount and
pointing at something outside it — is refused on its target rather than read.
An absolute path is refused earlier still, as a config-load error.

One more mount, which Posterizarr and Kometa do not have and which this
deployment has to add:

- `/plexbackup`

This is `artwork_modes.plex_backup_root` — where the Backup mode copies the
artwork Plex is currently serving, and where Restore reads it back from. It is
deliberately **not** `backup_root` (`/assetsbackup`), which holds relocated
orphaned assets and is a different tree with a different lifetime.

Size it for a full copy of the library's artwork: a poster and a background per
movie and per show, a poster per season and per episode. Back it with real
storage — the whole point of the mode is to hold the pre-badge artwork, so
losing it loses the only way back.

The Backup mode **refuses to run** when `/plexbackup` is not mounted, rather
than creating the directory. Without that refusal every file would be written
into the container's own filesystem: the run would report success, fill the
node's disk, and the "backup" would disappear with the pod.

The config file itself is read from the path in `AUTOPOSTER_CONFIG`, which
the image sets to `/config/autoposter.yaml` — so mount `autoposter.yaml`
(e.g. from a ConfigMap) at `/config/autoposter.yaml`. Mounting it anywhere
else without also setting the variable means the app fails at startup
looking for a file that is not there.

## Config overrides (the Settings editor)

The Settings page can edit configuration. Edits do NOT touch the mounted
file — they live in the database, in the single-row `config_overrides`
table, as a partial document deep-merged OVER the file at every load. The
precedence is therefore: **database override, else ConfigMap value**. The
ConfigMap stays the git-owned base; the app owns only the deltas an
operator has explicitly saved, so a Flux sync and a UI edit can never fight
over the same file.

Practical consequences:

- A saved change to anything the app reads per-use (all of `artwork:`,
  badge/collection/operation behaviour, `settle_seconds`, scheduler
  cadences) takes effect immediately in the serving pod — no restart. The
  editor marks the rest ("restart to apply"): worker count, provider
  clients, notification wiring, Plex connection settings, `poll_seconds`,
  and whether a scheduled job is registered at all. A restart also brings
  any other replica up to date; a running sibling replica keeps its old
  configuration until then.
- `api_docs_enabled` is the one exception a restart does NOT fix: it must
  be set in the ConfigMap. FastAPI decides whether `/docs`, `/redoc` and
  `/openapi.json` exist when the application object is built, and that
  happens before the pod has read a single override, so an override on it
  is inert at every boot. The editor says as much in its own reason text.
  To close the docs on a pod whose ConfigMap has them on, edit the
  ConfigMap (or set `AUTOPOSTER_CONFIG` at a file that has them off) and
  restart — a database override will not do it.
- An invalid save changes nothing — the merged result is validated whole
  before anything is persisted or applied, and errors come back
  field-labelled.
- "Revert to base" in the editor removes the key from the override
  document; the ConfigMap value shows through again on the next load/swap.
- To inspect or clear the overrides by hand:
  `SELECT document FROM config_overrides;` /
  `DELETE FROM config_overrides;` (the next boot then runs on the file
  alone). The events feed records every save as
  `config / overrides_updated` with the version movement, never the
  contents.
- Editing `artwork:` settings (or repointing a root) from the UI carries
  the same weight the cutover note below gives the file: `config.version`
  is derived from the render-affecting settings as a whole, so ANY artwork
  edit re-renders the library. The editor's preview says exactly how many
  renders that is before you commit to it.

## Secrets

Secrets come from an ExternalSecret providing the `AUTOPOSTER_*` environment
variables:

- `AUTOPOSTER_DATABASE_URL`
- `AUTOPOSTER_PLEX_TOKEN`
- `AUTOPOSTER_TMDB_TOKEN`
- `AUTOPOSTER_TVDB_APIKEY`
- `AUTOPOSTER_FANART_APIKEY`
- `AUTOPOSTER_WEBHOOK_SECRET`
- `AUTOPOSTER_MDBLIST_APIKEY` — optional. Unset, only the `content_rating`
  metadata field is skipped; every other metadata operation (ratings, genres,
  studio, release date) still runs (see `app.py`'s `_build_mdblist`), and any
  `mdblist_list` collection definition reports itself failed while the rest of
  the pass proceeds.

  **One key, one budget.** The `mdblist_list` collection builder spends the
  *same* daily allowance the content-rating lookups do — 10,000 requests/day
  on this account — so adding list definitions eats into the metadata side and
  vice versa. MDBList signals exhaustion with an HTTP `200` carrying
  `{"error": "API Limit Reached!"}` rather than a `429`, so nothing in the HTTP
  layer slows down: the builder memoises that refusal for the rest of the
  library's pass so the remaining definitions fail without spending further
  calls. Because the response is a successful one as far as the response cache
  is concerned, it is also cached — recovery after the allowance rolls over can
  therefore lag by up to the cache TTL.
- `AUTOPOSTER_RADARR_APIKEY` / `AUTOPOSTER_SONARR_APIKEY` — optional, same
  posture as the MDBList key. Unset, `radarr.enabled`/`sonarr.enabled`
  default to `false` anyway, so the app boots the same either way; see
  "Radarr and Sonarr sync" below.
- `AUTOPOSTER_TRACEARR_APIKEY` — optional, same posture as the MDBList key.
  Unset, `tracearr.enabled` defaults to `false` anyway, so the app boots the
  same either way; see "Tracearr watch-history collections" below.
- `AUTOPOSTER_ADMIN_PASSWORD_HASH` — the bcrypt hash of the Web UI's admin
  password. See "Web UI authentication" below: unlike the keys above, an
  unset value does not mean "no auth required", it means every login attempt
  401s.
- `AUTOPOSTER_HARBOR_TOKEN` — optional, same posture as the MDBList key. See
  "The sidebar's update check" below, which also covers `AUTOPOSTER_IMAGE_REF`
  — not a secret, and so not part of this ExternalSecret, but the other half
  of the same check.
- `AUTOPOSTER_PLEX_ACCOUNT_TOKEN` — optional, same posture as the MDBList key.
  A plex.tv *account* token, for the collection builders whose source is the
  account rather than the server. Deliberately not `AUTOPOSTER_PLEX_TOKEN`:
  that one may be scoped to the server, which is fine for everything else this
  service does and is rejected by plex.tv.

  Mint one with the same PIN flow "Obtaining AUTOPOSTER_PLEX_TOKEN" below
  describes — `python -m autoposter.plex.auth`, which accepts
  `--client-identifier` (reuse the value a previous run printed, to refresh
  that "Authorized Devices" entry instead of adding a new one) and `--timeout`
  (seconds to wait for the browser step; default 300). It prints the token
  exactly once and persists it nowhere. The PIN flow always yields an
  account-wide token, so one run can serve either variable — note that the
  command's closing reminder names `AUTOPOSTER_PLEX_TOKEN`, and it is which
  secret you store the value in that decides the role it plays here. Rotate it
  the same way you rotate the server token.

  Unset, only the definitions that read the account report themselves failed;
  the rest of the pass is unaffected.

### The sidebar's update check

The sidebar shows the version the pod is running, and marks "Update
available" when the Harbor registry holds a newer image. It is off until
`AUTOPOSTER_IMAGE_REF` is provided, and it is off safely — the version line
still shows, with no marker.

The registry, project and repository are **not** operator config — they are a
deployment fact, derivable from the image reference the pod is already
running, so there is nothing to keep in sync with wherever the image actually
lives. `AUTOPOSTER_IMAGE_REF` is the only variable the check strictly needs:

1. **`AUTOPOSTER_IMAGE_REF`**, set to the full reference the pod pulled --
   e.g. `harbor.example.internal/operinko-labs/autoposter:sha-abc1234`. The
   app splits this into the registry host, the Harbor project and the
   repository itself at boot (`config/image_ref.py`); there is no separate
   field for any of the three. In Helm this is one line templated from the
   chart's own image value, e.g.
   `"{{ .Values.image.repository }}:{{ .Values.image.tag }}"`, so it can
   never drift from what the Deployment actually runs.

   The derived registry host is treated as private, the same as the old
   config field was: it never appears in `GET /api/version`'s response, in an
   event row, or in the log — a failed check logs the exception's class name
   (plus, for an HTTP error from Harbor, the status code) and nothing else. An
   `AUTOPOSTER_IMAGE_REF` that does not parse (not enough `/`-separated
   segments, or a first segment that doesn't look like a registry host) logs
   one WARNING naming the reason and switches the check off, the same as
   leaving it unset.

2. **`AUTOPOSTER_HARBOR_TOKEN`, only for a private registry project.** The
   `autoposter` project on Harbor is public and internet-accessible, so the
   check works with no credential at all — unset, requests are anonymous.
   Set this only if your own deployment pushes to a private project: in
   Harbor, under the project → Robot Accounts, create one with `pull` and
   `list` on the `autoposter` repository (nothing more; this account never
   pushes). Harbor shows the secret once. The environment variable is not
   that secret but `base64("robot$<name>:<secret>")` — the value of an
   `Authorization: Basic` header, which is what the app sends verbatim when
   the variable is set:

   ```sh
   printf '%s' 'robot$autoposter-readonly:THE-SECRET' | base64 -w0
   ```

   Put the result in the ExternalSecret as `AUTOPOSTER_HARBOR_TOKEN`.

The check runs in the background, not on request: every six hours (hardcoded
— a deployment fact, not a setting), a poll refreshes the cached answer, with
the first poll firing at startup so the sidebar has something to show within
seconds of boot rather than up to six hours later. `GET /api/version` only
ever reads that cache; it never calls Harbor itself, so however many browser
tabs are open, the registry sees at most one request per pod every six hours.
A failed poll leaves the previous answer standing rather than blanking the
marker, and logs the same class-name-only warning described above.

The comparison only works because the image knows its own tag: CI passes the
commit's short sha to `docker build` as `GIT_SHA`, the Dockerfile stamps it as
`AUTOPOSTER_VERSION=sha-<it>`, and the same job pushes the image under that
exact tag. An image built any other way reports `dev`, and a `dev` pod with a
reachable registry always shows the marker — correctly, since it is running
something that was never published.

**Migrating from the old `version_check:` config block:** remove it from
`autoposter.yaml` — the schema no longer recognises it, and a mounted file
that still has it fails to load — and add `AUTOPOSTER_IMAGE_REF`.
`AUTOPOSTER_HARBOR_TOKEN` is now optional (see step 2 above); a deployment
already carrying one for a private project needs no change.

None of these are read from the YAML config file.

## Web UI authentication

The `/api` routes the Web UI (Phase 4b) talks to sit behind a single admin
password, supplied as a bcrypt *hash* via `AUTOPOSTER_ADMIN_PASSWORD_HASH` —
never the plaintext password itself, following the same pattern as every
other credential in this project.

Generate the hash with `hash_password()` from `autoposter.api.auth`:

```
python -c "from autoposter.api.auth import hash_password; print(hash_password('your password here'))"
```

Store the printed hash (not the password) in the `AUTOPOSTER_ADMIN_PASSWORD_HASH`
ExternalSecret.

**An unset hash means nobody can log in, not that authentication is
skipped.** `POST /api/login` always runs the bcrypt check — even with no hash
configured, against a dummy hash — so response timing cannot reveal whether
the deployment has one set; every attempt still fails with `401` either way.
This fails closed rather than open: a forgotten secret locks operators out of
the Web UI instead of leaving the API open to anyone.

A successful login returns an opaque session token good for 24 hours
(`SESSION_TTL_HOURS` in `api/routes.py`), after which the operator has to log
in again. Sessions are rows in the `sessions` table, not signed cookies, so
`POST /api/logout` can revoke one immediately rather than waiting for it to
expire.

Everything under `/api` except `/api/login` requires a valid session. The
routes outside `/api` are authenticated differently or deliberately open:
`/healthz` and `/metrics` stay open so Kubernetes probes and Prometheus
scraping keep working without credentials; `/webhook/radarr` and
`/webhook/sonarr` are authenticated by the `X-Autoposter-Token` header, not
a session (see "Radarr / Sonarr webhooks" below); and the SPA's static
pages are public by design — they are just the login page and the built
bundle, and every piece of data they show comes through the
session-protected `/api` routes.

## Metadata operations config

The `operations:` block in `autoposter.yaml` controls the per-item metadata
writes added in Phase 2a (ratings, content rating, genres, studio, release
date — replacing Kometa's `mass_*_update`):

- `enabled` (default `true`) — off means metadata operations do not run at
  all; artwork rendering is unaffected either way.
- `write_to_plex` (default `true`) — off means facts are still gathered and
  stored in `item_facts`, but nothing is written to Plex. The safe setting
  while the tool being replaced (Kometa) still owns these fields.
- `imdb_refresh_hours` (default `6`) — how often the IMDb ratings dataset is
  polled in the background; see "Loading IMDb ratings" below.
- `imdb_refresh_enabled` (default `true`) — off disables the automatic
  refresh entirely, for operators who prefer to run it by hand.
- `imdb_miss_refresh_minutes` (default `60`) — rate limit for the
  miss-triggered refresh described below. `0` disables it.
- `tmdb_backoff_seconds` (default `60`) — how long TMDb is left alone after
  it answers `429` without a usable `Retry-After`. `0` disables the shared
  window entirely, restoring the behaviour before it existed: each `429` is
  then simply that one request's failure. **Restart to apply** — the budget
  captures its window length when the facts client is built at startup, so a
  saved override reaches it at the next restart rather than immediately; the
  config editor marks it as such rather than reporting the edit as live.
  Two properties worth knowing before tuning it. The window lives in the
  database, not in the pod, so every replica shares one: two pods do not each
  have to discover the same `429`. And it can only be widened, never
  shortened — a refusal carrying a longer `Retry-After` extends an open
  window, a shorter one leaves it alone, and nothing shortens one that is
  already open. If you need to clear a window early, the way out is to
  restart with `0` rather than to lower this number.

See `config/autoposter.example.yaml` for the full block.

## Badge overlays config

The `badges:` block in `autoposter.yaml` controls the Phase 2b badge stage:
compositing Kometa-parity resolution, audio, rating, content-rating, runtime
and language badges onto the base artwork and uploading the result to Plex.

- `enabled` (default `true`) — off means the badge stage does not run at all;
  base artwork rendering is unaffected either way. Backgrounds are never
  badged regardless of this setting — only posters, season posters and
  episode title cards carry badges.
- `upload_to_plex` (default `false`) — this is the first thing in the project
  that writes images to the live Plex server, across the whole library
  (~16,000 items). Left off, the stage still composes each badged image and
  records its fingerprint, so an operator can inspect what would be uploaded
  before flipping this on. Once enabled, every eligible item in the library
  gets its badged artwork uploaded and locked, not just newly changed ones on
  the first pass.
- `lock_artwork` (default `true`) — locks the Plex poster/art field after
  upload, the same way `plex/artwork.py` always has, so Plex's metadata agent
  cannot reclaim the field and replace what was just uploaded.
- `apply_overlay_label` (default `false`) — a deliberate behaviour change
  from Kometa, which labels every overlaid item with the literal Plex label
  `Overlay`. Badge state is tracked in Postgres (`renders.badge_fingerprint`,
  `upload_status`), so the label is not needed for this project's own
  purposes, but it is visible and filterable in Plex, so it is offered rather
  than silently dropped.
- `adopt_from_plex` (default `true`) — before uploading a render this service
  has no `badge_fingerprint` for (after adoption, after a database restore, or
  anything never badged here), read the EXIF provenance off the artwork Plex is
  already serving. If it records exactly the fingerprint about to be uploaded,
  the correct image is already there: the fingerprint is recorded, the render
  is marked uploaded, and both the composite and the upload are skipped. Costs
  one small HTTP Range request per such render — one, not two, for JPEG artwork
  or for a WebP whose header says it carries no EXIF at all. Entirely
  best-effort: any failure reading provenance falls through to a normal upload.

See `config/autoposter.example.yaml` for the full block.

## Common Sense collections config

The `collections:` block in `autoposter.yaml` controls the Phase 3a Common
Sense age-bucket smart collections, replacing Kometa's. These are Plex-native
smart collections — Plex evaluates the filter live. Reconciliation runs on
its own cadence via the periodic scheduler (see "Periodic scheduler" below),
and can also be run by hand at any time:

```
python -m autoposter.collections
```

- `enabled` (default `true`) — off means reconciliation does not run at all.
- `apply_to_plex` (default `false`) — dry run by default, the same posture as
  `operations.write_to_plex` and `badges.upload_to_plex`: reconciliation
  computes what each bucket's filter should be and reports it, but changes
  nothing in Plex until the operator opts in. Dry-run output looks like:

  ```
  Movies: 2 action(s)
     would create 'Age 17+ Movies' -> 17, R
     would update 'Age 13+ Movies' -> 13, PG-13
  ```

- `ownership_label` (default `autoposter`) — the safety boundary. Only
  collections carrying this label are ever created or modified; anything
  else (Plex/TMDB franchise collections, hand-made operator collections,
  Kometa's own `Kometa`-labelled collections) is left untouched. **Changing
  this after a run orphans every collection created under the old label** —
  they are not renamed or migrated, just no longer recognised as ours.
- `libraries` (default `[Movies, TV Shows]`) — Plex library names to
  reconcile.
- `separators` (default `true`) — maintain a blank "index card" divider
  collection for each GROUP of collections this service manages: `Chart
  Collections`, `Award Collections`, `Ratings Collections` and so on. Each is a
  permanently-empty collection whose sort title floats it above its own block in
  Plex's alphabetised collections tab. Turning it off leaves the sort-title
  prefixes in place and removes only the headings — and an existing heading is
  then an ordinary orphan, reported by the pass and deleted only if
  `delete_unconfigured` is on and `max_deletes` allows it, exactly like any
  other collection this service no longer builds.
- `group_order` (default unset) — reorder those blocks. Unset is the built-in
  order: charts, awards, content ratings, content, location, media, people,
  production, time, and your own `definitions:` entries last. A partial list is
  the normal use — the groups you name lead, in that order, and the rest follow
  behind them:

  ```yaml
  collections:
    group_order: [awards, charts]
  ```

  The valid names are the ten above. An unknown or repeated one is refused at
  config load with the full list, not accepted as a reordering that silently
  did nothing.

**The first pass after this feature ships re-writes one sort title per managed
collection** — one `editSortTitle` PUT each, membership untouched — including
replacing the prefixes on collections adopted from Kometa. Collections this
service does not manage are never touched. The tab reorders once and then
settles. Changing `group_order` later does the same thing again, once.

**No collection is ever deleted by this service**, including ones that are
empty or whose filter currently matches nothing in the library — that is
expected and normal, not a bug to fix.

See `config/autoposter.example.yaml` for the full block.

## IMDb chart and Oscars collections

The same `collections:` block also controls two families of regular (list)
collections, run by the same `python -m autoposter.collections` command
alongside the Common Sense collections:

- `charts` (default `true`) — `IMDb Popular`, `IMDb Top 250` and, for
  movies only, `IMDb Lowest Rated`, sourced from IMDb's public chart API.
  Shows get `IMDb Popular` and `IMDb Top 250` only — IMDb has no
  lowest-rated TV chart.
- `awards` (default `true`) — Oscars winner collections, movies only:
  `Oscars Best Picture Winners`, `Oscars Best Director Winners`, and
  `Oscars Winners <year>` for the five most recent ceremony years with
  data, sourced from the community IMDb-Awards dataset.

Unlike the Common Sense collections, these are regular collections with
explicit, ordered membership — items are added, removed and reordered on
every pass to match the source's own rank order. `apply_to_plex` still gates
every write, the same dry-run-by-default posture as the rest of this block.

**A source that fails to fetch leaves its collection untouched, never
empty.** These collections use sync semantics — anything not re-selected is
normally removed — so a failed IMDb or GitHub request is treated as "make no
changes" rather than "remove everything". One dead chart also cannot block
the others: each source is fetched independently, so an IMDb outage still
lets the Oscars collections (or vice versa) update normally.

IMDb's API response carries a non-commercial-use disclaimer. This deployment
is a private, single-operator install, which is within it; nothing here
redistributes the fetched data.

See `config/autoposter.example.yaml` for the full block.

## Collection posters

The same `collections:` block also controls whether the collections this
service manages get a poster:

- `posters` (default `true`) — give every managed collection (the Common
  Sense buckets and separator, the IMDb charts, the Oscars collections) a
  poster. Applied only after `resolve_collision` has approved the collection,
  so a conflicting or protected collection is never touched.

**Turning this on sets a poster on every collection this service manages,
adopted ones included** — not just newly created ones. The first pass after
enabling it fills in every managed collection whose poster we have never set,
whether or not its definition changed. Adopted collections are already
carrying these same images, set by the tool being replaced, so in practice
this is a visual no-op for them.

A file at `<assets_root>/<library>/<collection title>/poster.{jpg,jpeg,png,webp}`
overrides the hosted default, and is the supported way to pin your own poster
on one managed collection — the same `prioritize_assets`-style override the
badge pipeline uses for item artwork. An unreadable or non-image file there is
ignored and the hosted default used instead. Without one, the poster is
fetched at runtime from Kometa's `Default-Images` repository and never
vendored into this repository (see the module docstring on
`autoposter/collections/posters.py` for the reasoning).

**A failed fetch leaves the collection untouched, not the pass.** A missing
poster is cosmetic; the collection is logged and skipped, and the rest of
the run continues normally. A content hash on each collection's database row
means an unchanged pass uploads nothing.

`apply_to_plex` still gates every write, the same dry-run-by-default posture
as the rest of this block. A dry run resolves and fetches each poster — so it
can tell you whether the source is reachable — and reports the ones it would
set without uploading anything.

See `config/autoposter.example.yaml` for the full block.

## Periodic scheduler

The `scheduler:` block in `autoposter.yaml` controls five periodic passes —
the Common Sense collections reconcile, the ratings-drift sweep, the
orphaned-asset cleanup, the `media_items` prune, and the Radarr/Sonarr sync
with its safety net (see
"Radarr and Sonarr sync" below) — each run by a single background task (the same `run(stop_event)` shape as
the Plex health probe) started from the app lifespan. Their schedule lives in
the database, not process memory: `scheduled_runs` records each job's last
start/finish time and outcome (`last_status`, `last_detail`), so a restart
does not re-run everything, and two replicas coordinate through
`FOR UPDATE SKIP LOCKED` rather than both firing the same pass at once. Query
it directly to check what last happened and when:

```sql
SELECT name, last_started_at, last_finished_at, last_status, last_detail
  FROM scheduled_runs;
```

- `enabled` (default `true`) — master switch for all five passes. Off means
  none of them run at all, including as a dry run — **including the
  Radarr/Sonarr sync and its safety net**: an operator turning the scheduler
  off to silence the collections or cleanup passes also stops the Arr sync,
  and a missed webhook then never converges.
- `poll_seconds` (default `60`) — how often the scheduler checks whether
  anything is due; not the interval of any individual job.
- `collections_hours` (default `24`) — cadence for the Common Sense
  collections reconcile (see above). Only registered at all when
  `collections.enabled` is `true`, so a deployment with collections off does
  not run a pass that immediately returns.
- `drift_days` (default `7`) — cadence for the ratings-drift sweep. Ratings
  change without any file event, so nothing else re-triggers an item; this
  sweep re-enqueues the same `process_item` job the webhook intake path uses
  for anything whose gathered facts are older than `drift_max_age_days`
  (default `7`).
- `drift_batch_size` (default `500`) — the safety valve on that sweep.
  Enqueuing every stale item at once across a ~16,000-item library would
  swamp the worker pool and hammer every provider, so each run only takes the
  oldest `drift_batch_size` candidates and leaves the rest for the next
  run — a sweep works through a backlog gradually over successive runs
  rather than all at once.
- `cleanup_days` (default `7`) — cadence for the orphaned-asset cleanup: a
  walk of `assets_root` moving any directory no `renders` row references to
  `backup_root`. **Whether it writes is not a `scheduler` setting** — see
  `cleanup.apply` above, which already defaults to `false` (dry run: report
  what would move) because this pass moves the operator's files.
- `prune_days` (default `7`) — cadence for the `media_items` prune: a walk of
  every row asking whether the render pipeline can still resolve it, retiring
  the ones it cannot. **Whether it deletes is not a `scheduler` setting** —
  see `prune.apply` below, which defaults to `false` (dry run: report which
  rows would go).

### Orphaned-asset cleanup: what it can and cannot find

The cleanup works at **directory** granularity: it moves an asset *folder*
whose files no `renders` row references. That has one consequence worth
knowing before you enable it.

**With `library_folders: false` the cleanup finds nothing at all.** That
setting puts every asset directly in `assets_root` as a flat pile of files
rather than one folder per item, so there are no per-item directories to
find orphaned, and `assets_root` itself is deliberately never a candidate
(treating it as its own orphan would move the entire tree in one go). The
pass will run on its cadence, report `0 of 0`, and change nothing. This is
not a misconfiguration to fix — it is simply that the feature is inert in
that layout, so do not read a clean cleanup report as evidence that nothing
is orphaned. If you want the cleanup to do anything, run with
`library_folders: true`.

Two safety caps bound what one pass can do, because several ordinary
operational events make *every* directory look orphaned at once — repointing
`assets_root`, remounting the volume somewhere else, toggling
`library_folders` (which changes the whole naming scheme), or restoring only
part of the database:

- `cleanup.max_orphans` (default `500`) — refuse the pass if more than this
  many directories look orphaned.
- `cleanup.max_orphan_share` (default `0.25`) — refuse the pass if more than
  this share of the scanned tree does, which catches the same failure on a
  library too small for the absolute cap to fire. Only applied once the tree
  has at least 20 directories, below which a share means nothing.

A refusal is recorded in `scheduled_runs.last_detail` with the real numbers
(`refused: 11900 of 12000 asset directory(ies) look orphaned …`). If that
count is genuinely correct, raise the cap deliberately for one run rather
than leaving it raised.

Nothing is ever deleted: orphans move to `backup_root` keeping their path
relative to `assets_root`. If a destination already exists there from an
earlier pass, the new copy lands beside it with a `.1`, `.2` … suffix rather
than being moved *inside* it.

### Pruning `media_items` rows Plex can no longer resolve

An item that leaves Plex — deleted, moved into a library you excluded, or
re-matched under a new rating key — leaves its `media_items` row behind.
Nothing else removes it, so every full pass re-enqueues that row, the job
cannot find the item, and it parks. Forever, and once per pass. The
`plex_prune` job retires those rows.

**What "gone" means here is wider than "deleted from Plex."** A row is
prunable when the *render pipeline* cannot resolve it, and the pipeline never
looks inside `plex.excluded_libraries`. So **excluding a library makes its
rows prunable**, and an applied prune after an exclusion retires them. That is
deliberate — you exclude a library to stop processing those items — but know
it before switching `prune.apply` on. No files are touched either way, and
re-including the library re-creates the rows on the next pass.

A row that Plex *has* but has not finished scanning is never pruned: the probe
asks only whether the item can be found, not whether it is usable yet.

**A lost network mount is not a mass deletion — as long as Plex's trash stays
manual.** When a library's storage goes away, a scan marks those items
*unavailable* and moves them to Plex's trash; the items themselves still
resolve, so this prune's probe reads every one of them as present and finds
nothing prunable. What actually removes them from Plex is emptying that trash,
which is why **"Empty trash automatically after every scan" should stay off**
on every library this service manages. With it off, a mount outage is a
non-event at every layer: Plex keeps the items, the prune keeps the rows, and
the next scan after the mount returns un-marks them. With it on, one scan
during an outage deletes the items in Plex, and the next prune pass — capped
and dry-run by default, which is the last line of defence rather than the
first — is then reporting a real absence it cannot tell from a deliberate one.

- `prune.apply` (default `false`) — dry run: report which rows would go and
  delete nothing. Unlike the asset cleanup, whose mistake is a folder that
  moved to `backup_root`, this one's mistake is a row that is gone, so leave
  it off until a dry-run report reads the way you expect.
- `prune.max_prunes` (default `500`) — refuse the pass if more than this many
  rows look unresolvable.
- `prune.max_prune_share` (default `0.25`) — refuse if more than this share of
  the library does, which catches the same failure on a library too small for
  the absolute cap to fire. Only applied once there are at least 20 rows.

Four more things bound what one pass can do:

1. **An unhealthy Plex refuses the whole pass.** A server that answers nothing
   would make every row look gone. The liveness state (the same one that gates
   job claiming) is checked before the table is even read.
2. **Any error while connecting to Plex or probing it refuses the whole
   pass**, and is recorded as `failed` rather than `ok` in `scheduled_runs`.
   The detail names the exception class only, never the server address.
3. **A parent is pruned only when it and every descendant are individually
   gone.** `media_items.parent_id` cascades, so deleting a show removes its
   seasons and episodes; one episode that still resolves holds the whole show.
   The summary reports how many rows were held that way. A gone episode under
   a surviving show is still pruned on its own — that is the re-match case.
4. **The empty-table guard**: an empty `media_items` refuses, because that
   means a restore has not finished.

An applied pass also protects itself against changes made while it runs: a
row that changed since the probe -- or was held because a row underneath it
changed -- is left alone rather than deleted on stale evidence, and the
summary reports how many rows were left that way.

Each deleted row leaves one `events_log` row (`source = 'prune'`,
`event_type = 'media_item_pruned'`) carrying its whole identity — rating key,
kind, library, title, external ids, how many `renders` rows went with it, and
its `logo_upload_key`. That last one matters: a pruned item's uploaded
clearlogo can no longer be reverted by the logo-revert mode, because the marker
that made the revert safe dies with the row. The key is written into the audit
so it is recoverable by hand. `renders` and `item_facts` rows cascade away with
the item; pending and parked `process_item` jobs for the pruned rating keys are
dismissed in the same run (running ones park themselves and are dismissed by
the next pass).

```sql
SELECT payload->>'rating_key', payload->>'title', received_at
  FROM events_log WHERE event_type = 'media_item_pruned' ORDER BY received_at DESC;
```

**Interaction with the asset cleanup.** The prune touches no files. Each pruned
movie or show leaves its asset directory unreferenced — as with the cleanup
above, only the `library_folders: true` layout has a per-item directory to
leave behind; under the flat layout there is nothing there for a prune to
orphan, and the directory counts below refer to the foldered layout — and the
orphaned-asset cleanup above then moves it to `backup_root` on its own
cadence, which is safer than anything the prune could do, since that sweep
never deletes. The prune's summary says how many directories it is handing
over. Watch for one edge: past `cleanup.max_orphans` the cleanup pass refuses
**entirely**, so a large prune can leave the next cleanup pass doing nothing at
all, including the orphans it would otherwise have handled. The prune summary
warns when its own count exceeds that cap; it cannot check
`cleanup.max_orphan_share`, which needs a scan of the whole asset tree, so
consider that one yourself before a large applied prune. Pruning only some
episodes of a surviving show leaves no orphaned directory at all — those
artifacts live under the show's folder, and in the re-match case the new rows
reuse the same paths.

The IMDb dataset refresh (`operations.imdb_refresh_hours`) deliberately does
**not** run on this scheduler — it keeps its own separate background loop.
Its trigger is remote dataset staleness plus a miss-triggered cooldown path
(see "Loading IMDb ratings" below), not a fixed interval, so folding it into
this scheduler would mean either losing that behaviour or bending the
scheduler around one job.

See `config/autoposter.example.yaml` for the full block.

## Loading IMDb ratings

`critic_rating` (IMDb) is populated from IMDb's bulk datasets, not a live API
call. The app polls these automatically in the background: once at startup if
`imdb_ratings` is missing or older than `imdb_refresh_hours` (default 6),
then every `imdb_refresh_hours` after that. The refresh runs as a background
task — like the Plex health probe — so it never blocks startup or the
request/worker loop, even while parsing the ~60 MB datasets.

IMDb rebuilds these datasets once a day, around 00:38–00:39 UTC. Polling
every 6 hours (rather than the previous 24h, which could sit up to a full day
behind a publication) picks up each day's build within 6 hours.

Most of those polls transfer nothing: the request is conditional
(`If-Modified-Since`), and datasets.imdbws.com replies `304 Not Modified` with
no body when the file hasn't changed since the last successful refresh, so
the download and the parse are both skipped. This is tracked per dataset
(ratings and episodes independently, since their id sets differ), and the
skip is safe *only* when this library's set of wanted IMDb ids also hasn't
changed since that refresh — a poll still re-downloads and re-parses an
otherwise-unchanged file if the library gained titles since the last refresh,
because `refresh()` only stores rows for ids it was asked about and would
otherwise silently leave a newly imported title unrateable forever. Check the
`imdb:` log lines to see, per poll, whether each dataset downloaded (and how
many rows it stored) or was skipped and why.

The one behaviour operators will still notice: a title imported since the
last refresh has no IMDb rating until the next poll runs, so a newly added
film's `critic_rating` can lag by up to `imdb_refresh_hours`. This is
expected, not a bug — set `imdb_refresh_hours` lower if that lag is a
problem, or see "Miss-triggered refresh" below for the mechanism that
usually catches this sooner.

### Miss-triggered refresh

To shrink that blind spot, fact gathering also retries once whenever a
rating lookup finds nothing: it attempts an immediate refresh scoped to just
that one IMDb id, then re-checks, so a freshly imported title can get its
`critic_rating` on the same pass instead of waiting up to
`imdb_refresh_hours`. It only pulls what that one lookup needs — a movie or
show miss downloads just `title.ratings.tsv.gz` (8.6 MB); only an *episode*
miss also downloads `title.episode.tsv.gz` (54 MB), to learn the new
episode's own IMDb id first.

This is rate-limited to one attempt per `imdb_miss_refresh_minutes` (default
60), tracked in the database (`imdb_miss_refresh_state`) rather than in
memory, so every pod behind the same database shares one cooldown window —
importing a season pack triggers at most one download, not one per episode.
Set `imdb_miss_refresh_minutes: 0` to disable it entirely.

A title that is genuinely unrated — a same-day release, or an episode that
hasn't aired yet — will still show a blank `critic_rating` after the retry.
That is correct behaviour, not a fault: IMDb has no rating to give it until
it has votes.

To force a refresh immediately (e.g. for a first load before the app has run,
or after changing which titles are in the library) rather than waiting for
the next interval, run the same loader by hand:

```
python -m autoposter.facts.imdb
```

This needs only `AUTOPOSTER_DATABASE_URL` in the environment. It selects the
IMDb ids the library actually needs from `media_items`, downloads and parses
both datasets, upserts `imdb_ratings`/`imdb_episodes`, and prints how many
rating and episode rows were stored.

## Obtaining AUTOPOSTER_PLEX_TOKEN

Run the PIN-based login flow instead of extracting a token from a browser
URL:

```
python -m autoposter.plex.auth
```

This prints an `https://app.plex.tv/auth#?...` URL — open it in a browser and
sign in to authorise autoposter. The command then polls plex.tv and prints
the resulting token once authorisation completes, together with a reminder
that it belongs in `AUTOPOSTER_PLEX_TOKEN`.

The token grants full access to the Plex account it was issued for (not just
this library), so store it only in the secret manager backing the
`AUTOPOSTER_PLEX_TOKEN` ExternalSecret — never in the config file, a
`.env` file, or anywhere else. To rotate it, run the command again; pass
`--client-identifier` with the value printed by the previous run if you want
the new token to update the same "Authorized Devices" entry in the Plex
account rather than adding a new one.

## Adopting an existing library (cutover)

The `adopt:` block in `autoposter.yaml` controls one-time adoption: taking
over a library already populated by Posterizarr and Kometa without
re-rendering any of it. Adoption walks each configured Plex library and the
asset tree beneath it, hashing whatever artwork already exists on disk and
recording it in `media_items`/`renders` as `adopted`, rather than resolving
anything from the artwork providers. The render pipeline then short-circuits
an adopted item's first real pass to a single file hash and no provider
requests, as long as nothing else about it (title, config version, overlay
or font file) has changed since adoption.

- `apply` (default `false`) — dry run by default, the same posture as
  `badges.upload_to_plex`, `collections.apply_to_plex` and `cleanup.apply`:
  the walk computes and reports everything it would adopt, but writes no
  database rows until the operator opts in. This is deliberately the last of
  these settings to be flipped on, not the first.
- `libraries` (default `[Movies, TV Shows]`) — Plex library names to adopt.

Run the cutover in this order:

1. **Run the adoption report with `adopt.apply: false`** (the default):

   ```
   python -m autoposter.adopt
   ```

   This writes nothing. Read the printed report for each library and the
   total, in particular `missing_assets` — every item counted there has no
   artwork on disk for that art kind and will render from scratch on its
   first real pass, same as a brand new item. If that count is far higher
   than expected, stop and find out why (wrong `assets_root`, wrong
   `library_folders` setting, a library not yet fully populated) before
   proceeding — this report is exactly what it exists to catch.

   `not rendered by this config` is a separate count and is *not* a gap: those
   are art kinds this configuration would never produce anyway — a kind with
   `enabled: false` (e.g. `artwork.background`), or a title card whose episode
   title matches a `skip_tba` word. They are excluded from `missing_assets`
   deliberately, so that number stays a number worth acting on.

   `unnumbered` is a third separate count: items Plex gave no season or
   episode number for, so there is no file name their artwork could have. The
   usual cause is a year-grouped special (`index: null`, filed under a
   `parentIndex` like 2024) — a Plex agent quirk, not a fault in the asset
   tree. Each one is also logged individually with its title and rating key.
   They are skipped and the walk continues; a handful is normal, a large count
   means those items need re-matching in Plex.
2. **Set `adopt.apply: true` and run it again.** This time it writes the
   `media_items`/`renders` rows. The numbers in the report should be
   unchanged from the dry run; if they differ, something in the library or
   asset tree changed between the two runs.

   Flipping this setting is safe for the rows just written. `config.version`,
   which is the first component of every render fingerprint, is derived from
   the *render-affecting* settings only — the `artwork:` block,
   `library_folders`, and the asset/font/overlay roots — so editing `adopt`,
   `scheduler`, `cleanup`, `collections`, `operations`, `badges`, worker or
   connection settings, or adding a comment, leaves every adopted fingerprint
   valid. Editing anything under `artwork:` or repointing a root does not, and
   will re-render the library; make those changes *before* adopting, not after.
3. **Repoint the Radarr and Sonarr webhooks** at this service (see below) and
   **stop the old tools** (Posterizarr, Kometa) so they stop writing to the
   same asset tree and Plex fields this service now owns.

**Adoption deliberately does not mark existing Plex artwork as already
badged.** It would be possible to record every adopted item as carrying
current badge overlays too, avoiding a re-upload for the whole library on
cutover — but the overlays currently on the Plex server were produced by the
tool being replaced, not by this one. Marking them current would mean Plex
keeps those overlays until each item happens to change for some other
reason, which could be months. Instead, an adopted item keeps its expensive
*base* artwork — that is what adoption exists to preserve — and re-badges on
its next pass, which costs one composite and one upload rather than a full
re-render, and produces badge artwork this service actually owns and can
track a fingerprint for.

Artwork this service has uploaded before is recognised without a re-upload:
`badges.adopt_from_plex` (default `true`) reads our own EXIF provenance back
off whatever Plex is currently serving and, when it records exactly the
fingerprint about to be composed, marks the render uploaded and skips the
work. That covers a database restore or a re-run against a library this
service already badged; it does **not** cover cutover from the old tools,
whose overlays carry no provenance of ours. Turning it off costs one
redundant upload per affected item, never correctness.

### What the drift sweep will and will not re-badge

`scheduler.drift_batch_size` throttles how fast the ratings-drift sweep works
through the library, but the sweep does **not** reach every adopted artifact:

- `sweep_stale_facts` selects only `media_items` whose `kind` is `movie` or
  `show`, and
- a pass over one item renders only the art kinds that item's own kind
  implies — a show gets its poster and background, and nothing else.

So a show's drift pass never descends into its seasons or episodes. On this
library that leaves roughly **2,800 adopted season posters and 13,000 adopted
title cards that the drift sweep will never re-badge**; they keep the
replaced tool's overlays indefinitely, until a Sonarr webhook (import,
rename, series add) happens to touch that season or episode and put it
through the pipeline itself. Movie posters/backgrounds and show
posters/backgrounds do get re-badged on the sweep's cadence.

If you want the season and episode artwork re-badged sooner than Sonarr
activity will manage, drive it by hand rather than waiting for the sweep.

### What the drift sweep fills in for TMDb-backed collections

Three fields now come off the TMDb payload the facts pipeline was already
fetching — `tmdb_origin_country`, `tmdb_original_language` and
`tmdb_collection_id`, all on `item_facts`. They cost no extra requests: they
ride the `/movie/{id}` and `/tv/{id}` reads a facts gather already makes, so
turning them on added zero calls to any pass.

What they are FOR is collections Plex cannot express. Plex has no
`origin_country` field and no concept of a TMDb franchise, so a collection
family over those values cannot be a Plex smart filter — it is built from
this service's own stored facts instead. The `content_franchises` preset is
the first one shipped on them.

**The consequence an operator should expect, stated up front: such a family
is only as complete as the facts pipeline's coverage of the library.** It
enumerates what has been VISITED, not what exists. A library the pipeline
has half worked through builds a half-sized family — correct, incomplete,
and converging as the sweep works the rest. At the defaults above
(`scheduler.drift_batch_size` 500 every `scheduler.drift_days` 7) a
~16,000-item library is worked through in about 32 weeks; raising the batch
size or shortening the cadence converges it faster, at the usual cost of
more provider traffic per run.

Two things make this visible rather than something to infer:

- A family that builds **nothing** says so in the pass's own actions, with
  both numbers — "the facts pipeline has visited N of M item(s) there" — and
  names the two knobs above. So does a family that refuses for any other
  reason (everything excluded, more collections than its cap allows, a
  franchise TMDb cannot name).
- A family that has enumerated nothing is never treated as a family the
  operator narrowed, so the delete sweep will not remove its collections on
  a pass that could not see them. Coverage gaps make a family smaller; they
  never make it delete.

There is nothing to wait for before enabling such a preset — an incomplete
family is a working family that grows. Enable it in preview first
(`collections.apply_to_plex: false`) and read the coverage line.

### Taking over Kometa's collections

The Common Sense age buckets, the IMDb chart collections and the Oscars
collections all collide by title with what Kometa already created on this
library, so without adoption they are reported as conflicts and left alone
forever. `collections.adopt` (default `false`) takes them over instead.

**Stop Kometa first.** This is the same "stop the old tools" step above,
but it matters more here: with adoption enabled on both sides, this service
and Kometa would claim the same collections and fight over their contents
on every pass. Adoption is a cutover step, run once with Kometa already
stopped — not something to enable while it is still scheduled.

With Kometa stopped:

1. **Set `collections.adopt: true` and run with `apply_to_plex: false`**
   (the default):

   ```
   python -m autoposter.collections
   ```

   This writes nothing. The report names every collection it would claim,
   e.g. `would adopt 'Age 17+ Movies' (currently labelled 'Kometa')`.
2. **Set `collections.apply_to_plex: true` and run it again.** Each named
   collection gains the `autoposter` label — and keeps the `Kometa` label
   unless `adopt_removes_prior_label` is set — then is reconciled
   normally from then on: the Common Sense buckets have their filters
   rewritten, the IMDb chart and Oscars collections have their membership
   diffed against the source.

What adoption never claims, regardless of title:

- **Unlabelled collections** — the operator's own hand-made collections
  and Plex's own franchise collections (269 of them on this library) carry
  no label at all and are never eligible.
- **Collections carrying a protected label** (`protect_labels`, default
  `Collection managed by Maintainerr`) — protected even if the same
  collection also carries an `adopt_from` label.
- **Any collection whose title this service does not manage** —
  adoption only ever happens at the point an existing collection's title
  collides with one this service is about to create or update.

A prior-tool collection genuinely left over after adoption — one that
still carries an `adopt_from` label but whose title this service does not
manage — is named once in the leftovers report appended to that library's
summary line, so it is flagged rather than silently forgotten. Collections
carrying a `protect_labels` label are left out of that report as well:
naming a Maintainerr collection as "left behind" would invite action on the
one collection this service must never touch.

**Numbers for this library, audited against the live server:** Movies
holds 305 collections, of which 30 would be touched by adoption (29
content/chart/award collections plus the `Ratings Collections` separator,
which is now one of the per-group dividers this service manages rather than
left over). TV Shows holds 20 collections, of which 19 would be touched. 49
collections across both libraries carry the `Kometa` label, and — with the
separator now managed — **all 49 have titles this service manages**; none
of them is expected to appear in the leftovers report. Two collections are
deliberately never touched: Movies' `Deleted Soon` carries the `Collection
managed by Maintainerr` label, and TV Shows' `Deleted Soon` carries no
label at all (plausibly stripped by the previous tool at some point) —
neither title collides with anything this service manages, and both are
additionally covered by `protect_labels` and by the
never-adopt-an-unlabelled-collection rule.

**Unverified: creating a separator.** A blank divider collection cannot be
made through plexapi (`createCollection` rejects an empty item list), so it
is created with a raw `POST /library/collections` carrying a `uri` that names
no item keys — the same call Kometa makes. Only the `Ratings Collections`
divider existed on this server before the per-group dividers shipped, so
until now only the update path ever ran and the create path is untested
against live Plex; the test suite cannot cover it either, since no test may
make a real outbound request. **The first pass after this feature ships is
therefore the create path's first live exercise**, once per group that has no
divider yet. **Check each new divider's member count in Plex after that run**:
it must be empty. If it instead contains the whole library, remove it and
report it — nothing in this service ever adds members to it, so the only way
that can happen is the POST itself.

Only the libraries named in `collections.libraries` are touched at all;
everything else on the server is left completely alone. The default is
`Movies` and `TV Shows`. Adding another library there would create a full
set of Common Sense, chart and award collections on it too, the same as any
other configured library — so add one deliberately, and run with
`apply_to_plex: false` first to see what it would create.

See `config/autoposter.example.yaml` for the full block.

## Radarr and Sonarr sync

The `radarr:`, `sonarr:` and `arr_sync:` blocks in `autoposter.yaml` control
Phase 3f: registering Plex movies/shows that Radarr or Sonarr does not know
about yet, in place against the file already on disk, without ever
triggering a search or a download. Neither service is contacted or written
to unless it is explicitly configured — see "Two independent things" below.

### Configuring a service

For each of `radarr:`/`sonarr:`:

- `enabled` (default `false`) — off means this service is never contacted at
  all: no read, no write. Turn on only once `base_url` is set and the
  matching API key is exported.
- `base_url` — the service's URL, e.g. `http://radarr.media.svc.cluster.local`.
- The API key is **not** a config file setting. It comes from the
  environment, the same pattern every other credential in this project
  follows: `AUTOPOSTER_RADARR_APIKEY` / `AUTOPOSTER_SONARR_APIKEY` (see
  "Secrets" above). It is never read from, or written to, the YAML file, and
  never appears in a log line.
- `add_existing` (default `false`) — dry run by default, the same posture as
  `badges.upload_to_plex`, `collections.apply_to_plex` and `cleanup.apply`:
  with this off, a pass still reports what it would register, but issues no
  `POST`. Registering an item **never triggers a search or a download** —
  Radarr's `addOptions.searchForMovie` and Sonarr's
  `addOptions.searchForMissingEpisodes` are always sent `false`, and this is
  not configurable: there is no legitimate reason for this service to start
  a download, and a config key that could set either flag would eventually
  get flipped by accident.
- `quality_profile` — the exact quality profile name to register new items
  under (e.g. `HD Bluray + WEB` for Radarr, `WEB-1080p` for Sonarr). If the
  name does not resolve against the service, the pass for that service fails
  loudly (logged with a full traceback, and named in
  `scheduled_runs.last_detail`) — but it does **not** stop the other
  service's sync or the safety-net enqueue described below.
- `monitor` (Radarr `monitor`, Sonarr `monitor`) and, Radarr-only,
  `minimum_availability` (default `announced`); Sonarr-only, `season_folder`
  (default `true`) and `series_type` (default `standard`) — passed straight
  through on every registration, mirroring how the tool being replaced was
  configured.

**Nothing is ever removed from or modified in either service.** This phase
only ever adds an item the service is missing, registered against the file
already on disk; there is no delete or update path at all, regardless of any
setting above.

### The path mapping is mandatory and case-sensitive

`plex_path` (default `/mnt/Media`) and `arr_path` (default `/mnt/media`) map
a Plex item's file location to the path the service should register.
**Verified live: Plex mounts the library at `/mnt/Media` (capital M);
Radarr and Sonarr see the same files at `/mnt/media` (lowercase).** The
mapping is an exact, case-sensitive path-segment replacement — get it wrong
and a registration points the service at a directory it cannot read (or,
worse, silently matches an unrelated path that merely shares a prefix). A
path that does not fall under `plex_path` is skipped rather than guessed at.

An item whose mapped path *is* a root — `arr_path` itself, or one of the
root folders the service manages (`/mnt/media/Movies`, `/mnt/media/TV`) — is
never registered. That happens when a file sits directly under the library
root with no folder of its own; registering it would tell the service that
one item owns the whole tree, and a later "delete with files" would take the
library with it.

### A service that cannot be the right one is refused, not acted on

Before comparing anything, each pass checks the service's own answers
against the configuration, and refuses the whole pass for that service
(logged with a full traceback, reported as `refused` in
`scheduled_runs.last_detail`, nothing added) when either check fails:

- **Root folders.** `arr_path` must share a tree with one of the root
  folders the service actually manages — verified live, exactly one each:
  `/mnt/media/Movies` for Radarr and `/mnt/media/TV` for Sonarr. A service
  managing something else entirely is not the instance this sync was
  configured for (a wrong `base_url`, the wrong container).
- **An empty listing.** A service answering "I hold nothing at all" for a
  Plex library with more than ten items is treated as a misconfiguration,
  not as a library-sized gap. That answer would make every Plex item look
  missing *and* leave the path-collision guard nothing to compare against —
  under `add_existing` roughly 1,982 POSTs into the wrong instance.

Both refusals are contained the same way a missing quality profile is: the
other service's sync and the safety-net enqueue below still run.

### Two independent things run per Plex library

Every pass over a Plex library (movie or show) does two independent things:

1. **Registration with the matching service** — only when that service's
   own `enabled` is `true`. **Both services being unconfigured is a clean
   no-op for this half, not an error.**
2. **The safety net** — `arr_sync.enabled` (default `true`) — enqueues every
   Plex item in that library with no `media_items` row yet, so the library
   still converges on a missed webhook even with both services above
   disabled entirely. This runs **independently** of `radarr.enabled` /
   `sonarr.enabled`.

`arr_sync.hours` (default `24`) is the registration cadence; `arr_sync.batch_size`
(default `500`) caps how many unknown items the safety net enqueues per
pass — the same safety valve as `scheduler.drift_batch_size`, for the same
reason: a first run against a fresh database can find every item unknown.

This whole pass runs on the periodic scheduler, so it is honoured only while
`scheduler.enabled` is `true` — turning the scheduler off stops the Arr sync
and its safety net too (see "Periodic scheduler" above).

### Expected steady-state outcome for this library

Audited against the live services: Radarr holds 1,982 movies and Sonarr
holds 287 series, with nothing missing and no path collisions on either
side. The expected outcome of a run against a healthy library is that the
sync **adds nothing at all**. The report groups every item into one of
four categories:

- **missing** — genuinely absent from the service. With `add_existing:
  true` this is what gets registered, in place, against the file already
  on disk.
- **misassignment** — the service already holds this folder, but under a
  *different* external id. This service will not touch it; comparing ids
  alone would otherwise try to register a second, colliding entry. It has
  to be fixed by hand in Radarr or Sonarr. Both discrepancies previously
  seen on this library (`Limitless` and `The Moomins`) turned out to be of
  this kind, not missing registrations.
- **already present by path** — Plex has no usable external id for the
  item, but the service already has the folder registered. `Tractor Tom`
  is the current example: its TVDB guid had not yet reached Plex, but
  Sonarr already had the show at the mapped path. Nothing to do.
- **unmatchable** — no external id in Plex and no registered path either.
  This usually means the item is unmatched in Plex itself, so the fix
  belongs in Plex or its metadata source, not here.


## Radarr / Sonarr webhooks

The app listens on port `8080` — point the Kubernetes Service, the probes
(`/healthz`) and these webhook URLs at it.

Configure Radarr and Sonarr with a webhook notification pointing at this
service's webhook URL (`/webhook/radarr` and `/webhook/sonarr` respectively),
with a custom header `X-Autoposter-Token` set to the same value as
`AUTOPOSTER_WEBHOOK_SECRET`.

Enable these triggers:

- **Radarr:** On Import Complete, On Rename, On Movie Add
- **Sonarr:** On Import Complete, On Rename, On Series Add

## Tracearr watch-history collections

Two collection presets — `chart_tracearr_movies` and `chart_tracearr_shows`,
titled **Most Watched Movies** and **Most Watched Shows** — rank a library by
what this deployment actually played, over a 30-day window. They are opt-in
like every other preset: list the key in `collections.presets`.

Three things have to be true before either builds:

- `tracearr.enabled: true` and `tracearr.base_url` set in the YAML config. The
  base URL may be a cluster-internal hostname; it never appears in a log
  line, an error message or an event payload — `providers/tracearr.py`'s
  error-hygiene contract. It is visible in the authenticated config editor,
  like every other section's URL; `_REDACTORS` (`api/routes.py`) redacts only
  `notifications.url`.
- `AUTOPOSTER_TRACEARR_APIKEY` exported. It is a `trr_pub_...` public API key
  minted in Tracearr's own settings, and it is a *soft* secret: with it unset
  the service still boots and every other pass is unaffected, and only these
  definitions report themselves failed.
- The `tracearr_most_watched` builder is what the presets expand to. It takes
  `days` (1–365, default 30), `metric` (`plays` or `watch_time`, default
  `plays`) and `limit` (1–100, default 20), so an operator who wants a
  different window writes their own definition rather than editing the preset.

**Tracearr publishes no most-watched endpoint**, in either API version — the
only top-N-by-play-count endpoint it has is on its internal, session-JWT API
that an API key cannot reach. So this ranking is computed here, by paging
`GET /api/v2/public/history` over the window and grouping the records. Three
consequences worth knowing:

- **The budget is the constraint.** The v2 API allows 240 requests/minute
  *shared across the whole key* — not per route, and spent even by requests
  that fail authentication. One collection costs one page per 100 plays in its
  window, plus (on a Show library only) one `GET /api/v2/public/media/{id}`
  per ranked title, which is what `limit` bounds. Two definitions ranking the
  same show in one pass share one call. Worst case with both presets on is
  ≤10 history pages for the Movie preset plus ≤10 history pages and ≤20 media
  lookups for the Show preset — ≤40 v2 calls per pass against a 240/min
  budget, which is why no client-side rate limiter ships; revisit that only
  if the preset count grows.
- **One missing title does not empty the collection.** If Tracearr no longer
  has a media document for a ranked show — a title deleted between the history
  read and the lookup — that one entry is dropped and the count is logged; the
  collection still builds from the rest. A Tracearr that is unreachable, or
  answering with something other than the API, fails the definition instead and
  leaves the existing collection untouched.
- **These numbers are not Tracearr's dashboard numbers, and are not meant to
  be.** `/history` windows are instants; Tracearr's own per-item stats windows
  are UTC calendar days. The two disagree by design, and mixing them would
  produce a number neither service would recognise.

## Outbound notifications config

The `notifications:` block in `autoposter.yaml` controls the Phase 5a
run-completion webhook: one POST to a configured URL when a run boundary is
crossed, replacing the notification capability Posterizarr's Apprise config
provided. Two events exist in v1, both hooked where completion is already
recorded:

- `scheduled_run_completed` -- a named scheduler job (collections reconcile,
  ratings-drift sweep, asset cleanup, Arr sync) finished, successfully or
  not. Fires only after the `scheduled_runs` row is committed, so a
  notification can never describe a run the database does not yet show.
- `full_pass_enqueued` -- `POST /api/full-pass` enqueued its batch, carrying
  the real `{total, queued, skipped}` counts, after their commit.

Settings:

- `enabled` (default `false`) -- off means no sender is built at all; every
  hook still runs, against a no-op notifier.
- `url` (default empty) -- where the POST goes. Operator-sensitive even
  though it is config rather than a secret: it may embed a token in its path
  (as Uptime-Kuma-style push URLs do), so the full URL is never logged or
  stored -- log lines and events rows name only the host. `enabled: true`
  with an empty URL is a named misconfiguration: one warning at startup and
  a no-op notifier, not one warning per event.
- `mode` (default `apprise-json`) -- the payload shape, below. An unknown
  mode fails config validation at load time; there is no runtime fallback.
- `timeout_seconds` (default `10`) -- per-attempt HTTP timeout.
- `retry_count` (default `3`) -- total attempts per notification, not
  retries after the first.

### The two payload shapes

`apprise-json` (the default) is the body Apprise's `json://` scheme POSTs,
so anything already built to consume Apprise webhooks works unchanged
(verified against Apprise's `custom_json.py`; see
`src/autoposter/notify/payload.py` for the provenance):

```json
{
  "version": "1.0",
  "title": "autoposter: scheduled_run_completed",
  "message": "scheduled run ratings_drift_sweep finished: ok",
  "attachments": [],
  "type": "success"
}
```

`type` is `failure` when the event's detail carries `status: failed`
(a scheduler job that raised); anything else -- including events with no
failure semantics at all, such as `full_pass_enqueued` -- is `success`, so a
consumer gating on `type === "success"` behaves meaningfully. `attachments`
is always present and always `[]` (Apprise sends the key even with nothing
attached; this service never attaches files).

`autoposter-v1` is this service's own versioned contract, for consumers that
want the structured detail the Apprise shape has no field for:

```json
{
  "schema": "autoposter/v1",
  "event": "scheduled_run_completed",
  "at": "2026-08-22T12:35:25.808755+00:00",
  "summary": "scheduled run ratings_drift_sweep finished: ok",
  "detail": {
    "job": "ratings_drift_sweep",
    "status": "ok",
    "detail": "enqueued 0 item(s) with facts older than 7 days"
  }
}
```

`at` is the payload build time, ISO 8601 UTC with an explicit offset.
`detail` for `scheduled_run_completed` is `{job, status, detail}` with
`status` exactly `ok` or `failed`; for `full_pass_enqueued` it is
`{total, queued, skipped}`.

### Retry, timeout, and what failure looks like

A notification describes work that already finished, so its failure never
fails that work: no retry-forever, no parked jobs, no crashed scheduler.
Each send makes up to `retry_count` attempts, each bounded by
`timeout_seconds`, with backoff of 0.5s, 1s, 2s, ... between them. Transport
errors and 5xx responses retry; any other status does not (a 4xx, or a 3xx
-- redirects are not followed) -- a wrong path or revoked
token cannot be fixed by asking again. Worst case for one send on the
defaults: `3 x 10s + 0.5s + 1s = 31.5s`, and that time is spent on a
background task -- neither the scheduler loop nor the `/api/full-pass`
response ever waits on the webhook.

A send that exhausts its attempts logs exactly one warning (naming the
host, the attempt count and the last error) and writes an `events_log` row
with `source: notifier`, so the failure is visible in the Web UI's activity
feed and via `GET /api/events` -- not only in the pod logs. Success logs at
debug only and writes no row.

### Pointing automation at it

The n8n flow that used to fire Kometa on Posterizarr's webhook is retired at
cutover together with the Kometa CronJob -- its live path was a bare
trigger that never read the POST body, and its sole purpose was to run the
tool this service replaces. New automation (a rebuilt n8n flow, a catcher,
anything Apprise-shaped) points at this webhook instead; wiring the real
cluster n8n to it is a cutover-day step. The shapes above are not
hand-written examples: they are bodies captured from a live rehearsal of
this exact wiring (real compose stack, real scheduled runs, real
authenticated full pass, local catcher).

## Recovering parked jobs

**Parked is not the same as deferred.** Parked means a job gave up and needs a
human; deferred means a job is waiting for the library to catch up and needs
nobody. They are separate states, and only the first belongs on this page.

A job moves to `state='parked'` once it has been retried
`config.plex.resolve_max_attempts` times (Plex-connectivity failures) or
`MAX_ATTEMPTS` times (everything else) without succeeding. Parked jobs are not
retried automatically, so a Plex outage longer than the attempt budget
silently drops the affected webhooks unless someone requeues them.

A job moves to `state='deferred'` when Plex has no such item yet — a movie
added to Radarr months before release is the usual cause. There is **no**
attempt budget on this path: the job comes back every six hours
(`DEFER_INTERVAL_SECONDS` in `queue/jobs.py`), indefinitely, and runs by
itself the moment Plex can see the item. Nothing here needs recovering. A
deferred job appears on the Jobs page (state `deferred`, "waiting for Plex")
and on the dashboard's own tile, never on Failures, and the only way to end
one early is the Jobs page's Cancel, which dismisses it. Once the item's real
download webhook queues a fresh job that succeeds, the stranded deferred row
is dismissed automatically.

A webhook-born row carries no Plex rating key (Sonarr and Radarr know nothing
about Plex), so the pruner's retirement sweep can never reach it; if the item
is also never released, no sibling job ever succeeds to dismiss it either. Such
a row lives until it is cancelled by hand — there is no age-based retirement.

    SELECT count(*), state FROM jobs WHERE state = 'deferred' GROUP BY state;

A large and growing deferred count is a library statement, not a fault: that
many items are queued in Radarr/Sonarr that Plex does not hold.

`GET /api/jobs/parked` lists parked jobs with why they parked;
`POST /api/jobs/{id}/retry` resets one to pending with a fresh attempt count;
`POST /api/jobs/{id}/dismiss` marks one dismissed without deleting it, for a
failure nobody intends to requeue. The SQL below remains useful for bulk
recovery (e.g. after an extended outage) that would be tedious one job at a
time through the API.

A Plex outage itself should no longer be the cause, though: job claiming is
gated on a periodic Plex liveness check (`plex/health.py`), so while the
server is known unhealthy jobs stay `pending` and untouched instead of being
claimed and burning attempts. The retry budget above is now only a fallback
for an outage that begins between health checks.

Use this only after confirming the underlying problem (e.g. Plex) is fixed —
requeuing while the cause is still broken just burns through the attempt
budget again and re-parks the same jobs.

Requeue every parked job:

```sql
UPDATE jobs
   SET state = 'pending',
       claimed_by = NULL,
       claimed_at = NULL,
       attempts = 0
 WHERE state = 'parked';
```

Requeue only jobs parked within a time window (e.g. the last 2 hours, to
avoid resurrecting something parked for an unrelated, still-unresolved
reason):

```sql
UPDATE jobs
   SET state = 'pending',
       claimed_by = NULL,
       claimed_at = NULL,
       attempts = 0
 WHERE state = 'parked'
   AND updated_at > now() - interval '2 hours';
```

## ImageMagick build

The image is built on Alpine because its ImageMagick is **Q16-HDRI**, matching
the Posterizarr deployment this service replaces. This is not cosmetic: a Q16
build without HDRI renders the same source 0.077% differently, so new artwork
would no longer be byte-identical to what is already in the asset tree. The
Docker build asserts the flag is present, so swapping the base image fails the
build rather than silently changing output.

HDRI also permits float-format source artwork (`.exr`, `.hdr`) to be dropped
into the manual-assets directory.

## Provider API keys

Each operator supplies their own keys; none are embedded in the image.
`AUTOPOSTER_FANART_APIKEY` is a personal key from your own fanart.tv account —
keep that account's email address current, as their terms require, since it is
how they would contact you about the key.
