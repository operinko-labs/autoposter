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

Secrets come from an ExternalSecret providing the six `AUTOPOSTER_*`
environment variables:

- `AUTOPOSTER_DATABASE_URL`
- `AUTOPOSTER_PLEX_TOKEN`
- `AUTOPOSTER_TMDB_TOKEN`
- `AUTOPOSTER_TVDB_APIKEY`
- `AUTOPOSTER_FANART_APIKEY`
- `AUTOPOSTER_WEBHOOK_SECRET`

None of these are read from the YAML config file.

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
