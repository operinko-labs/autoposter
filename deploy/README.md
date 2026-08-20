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

See `config/autoposter.example.yaml` for the full block.

## Loading IMDb ratings

`critic_rating` (IMDb) is populated from IMDb's bulk datasets, not a live API
call. Nothing in this phase schedules that load automatically — a scheduler is
Phase 3's job — so it must be run by hand:

```
python -m autoposter.facts.imdb
```

This needs only `AUTOPOSTER_DATABASE_URL` in the environment. It selects the
IMDb ids the library actually needs from `media_items`, downloads and parses
both datasets, upserts `imdb_ratings`/`imdb_episodes`, and prints how many
rating and episode rows were stored.

**Run this at least once before metadata operations can produce a non-NULL
`critic_rating`** — without it, `imdb_ratings`/`imdb_episodes` stay empty
forever, `get_rating`/`get_episode_rating` return `None` for everything, and
Plex's critic rating is never written, with no error or warning. Re-run
periodically afterwards, since IMDb republishes both datasets daily.

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
