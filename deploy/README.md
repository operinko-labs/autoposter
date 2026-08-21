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
  studio, release date) still runs (see `app.py`'s `_build_mdblist`).
- `AUTOPOSTER_RADARR_APIKEY` / `AUTOPOSTER_SONARR_APIKEY` — optional, same
  posture as the MDBList key. Unset, `radarr.enabled`/`sonarr.enabled`
  default to `false` anyway, so the app boots the same either way; see
  "Radarr and Sonarr sync" below.
- `AUTOPOSTER_ADMIN_PASSWORD_HASH` — the bcrypt hash of the Web UI's admin
  password. See "Web UI authentication" below: unlike the keys above, an
  unset value does not mean "no auth required", it means every login attempt
  401s.

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

`/healthz` and `/metrics` are the only routes not behind this session check —
they stay open so Kubernetes probes and Prometheus scraping keep working
without credentials. Every other route, including everything under `/api`
except `/api/login`, requires a valid session.

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

The `scheduler:` block in `autoposter.yaml` controls three periodic passes,
each run by a single background task (the same `run(stop_event)` shape as
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

- `enabled` (default `true`) — master switch for all three passes. Off means
  none of them run at all, including as a dry run.
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
which is now managed as part of the Common Sense family rather than left
over). TV Shows holds 20 collections, of which 19 would be touched. 49
collections across both libraries carry the `Kometa` label, and — with the
separator now managed — **all 49 have titles this service manages**; none
of them is expected to appear in the leftovers report. Two collections are
deliberately never touched: Movies' `Deleted Soon` carries the `Collection
managed by Maintainerr` label, and TV Shows' `Deleted Soon` carries no
label at all (plausibly stripped by the previous tool at some point) —
neither title collides with anything this service manages, and both are
additionally covered by `protect_labels` and by the
never-adopt-an-unlabelled-collection rule.

**Unverified: creating a separator in a fresh library.** The blank
`Ratings Collections` divider cannot be made through plexapi
(`createCollection` rejects an empty item list), so it is created with a
raw `POST /library/collections` carrying a `uri` that names no item keys —
the same call Kometa makes. On this server both separators already exist,
so only the update path ever runs and the create path is untested against
live Plex; the test suite cannot cover it either, since no test may make a
real outbound request. If you point `collections.libraries` at a library
that has no `Ratings Collections` collection yet, **check its member count
in Plex after the first run**: it must be empty. If it instead contains the
whole library, remove it and report it — nothing in this service ever adds
members to it, so the only way that can happen is the POST itself.

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

Configure Radarr and Sonarr with a webhook notification pointing at this
service's webhook URL (`/webhook/radarr` and `/webhook/sonarr` respectively),
with a custom header `X-Autoposter-Token` set to the same value as
`AUTOPOSTER_WEBHOOK_SECRET`.

Enable these triggers:

- **Radarr:** On Import Complete, On Rename, On Movie Add
- **Sonarr:** On Import Complete, On Rename, On Series Add

## Recovering parked jobs

A job moves to `state='parked'` once it has been retried
`config.plex.resolve_max_attempts` times (Plex-connectivity failures and
"Plex hasn't scanned this yet") or `MAX_ATTEMPTS` times (everything else)
without succeeding. Parked jobs are not retried automatically, so a Plex
outage longer than the attempt budget silently drops the affected webhooks
unless someone requeues them.

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
