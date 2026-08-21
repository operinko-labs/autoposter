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

None of these are read from the YAML config file.

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
without succeeding. Parked jobs are not retried automatically — there is no
endpoint, script, or sweep that unparks them in this phase, so a Plex outage
longer than the attempt budget silently and permanently drops the affected
webhooks unless someone requeues them by hand.

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
