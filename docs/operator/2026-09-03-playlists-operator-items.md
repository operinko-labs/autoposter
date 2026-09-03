# Playlists — what the operator should know

Written at the close of **98a** (admin playlists). Nothing here blocks what
shipped; sections 1 and 2 record the state 98c (per-user playlist sync)
builds on.

## 1. `AUTOPOSTER_PLEX_ACCOUNT_TOKEN` — set in production; rotate it

`config/schema.py`'s `Secrets.plex_account_token` (env
`AUTOPOSTER_PLEX_ACCOUNT_TOKEN`) is a **plex.tv account** token. It is a
separate setting from `plex_token` because a server-scoped token is valid
for everything else this service does while being rejected by plex.tv.

The production external-secret template
(`kubernetes/apps/media/autoposter/app/external-secret.yaml`) templates
**both** `AUTOPOSTER_PLEX_TOKEN` and `AUTOPOSTER_PLEX_ACCOUNT_TOKEN` from the
same Bitwarden value (`plextoken`), and that value is account-scoped (the
operator verified plex.tv `/api/v2/user` answers 200 with it). The
`plex_account` factory is therefore live, and the shipped `plex_watchlist`
builder (roadmap row 64) works in production.

**The one action:** that token was exposed in a chat transcript on
2026-09-03. Rotate it and update the Bitwarden item; both env vars follow
automatically. The service ships a PIN login CLI that mints a fresh
account token without touching the browser session:

```
python -m autoposter.plex.auth
```

(`src/autoposter/plex/auth.py` — plex.tv's `/api/v2/pins` flow.) A UI-based
Plex login on top of the same flow is filed under the first-start wizard
(roadmap row 121).

## 2. What 98c will do, and for whom — decided

Verified end to end in plexapi 4.18.2: `Playlist.copyToUser(user)` →
`PlexServer.switchUser(user)` → `MyPlexUser.get_token(machineIdentifier)`,
which reads `https://plex.tv/api/servers/{machineId}/shared_servers`.
`switchUser` requires the admin account; `PlexServer.myPlexAccount()` builds
the account from the server object's own token and requires the owner.

The operator answered the three questions this hinged on:

 - The token is the **owner's** account token (above), so `shared_servers`
   and `myPlexAccount()` both work. 98c still verifies ownership **before**
   its first write rather than discovering a 401 mid-pass.
 - The audience is **both** kinds: about fifteen **shared** accounts (two
   libraries each) reachable through `shared_servers`, plus three **Plex
   Home** users (the owner, one adult, one "Kids" account), reachable through
   `MyPlexAccount.switchHomeUser(user)`.
 - **None of the Home users is PIN-protected.** 98c syncs to PIN-less Home
   users with the admin token; a PIN-protected Home user, should one appear,
   is skipped with a named report line — this service never holds PINs.

Per-playlist `sync_to_users` decides who receives what (which playlists the
Kids account gets is the operator's call, per playlist). 98c ships with a
dry-run/report pass before any per-user write.

## 3. Not an ask, a disclosure: what 98a will and will not touch

- A pass writes nothing to Plex until `playlists.apply_to_plex` is switched
  on. With it off, a pass reports what it would do. The one write outside the
  pass is the explicit `POST /api/playlists/ops/delete` with `confirm: true`,
  which deletes a single playlist we own on request, exactly as the
  collections delete does, whatever the two switches say.
- A playlist this service did not create is never modified and never deleted,
  whatever its title. Ownership is a `managed_playlists` row naming the
  playlist's rating key, and there is no adoption path — deliberately, because
  a Kometa-made playlist carries no marker to adopt from.
- The pass deletes no playlist unless `playlists.delete_unconfigured` is
  switched on, and even then never more than `playlists.max_deletes` in one
  pass: past that the sweep refuses entirely and reports the numbers.
- Nothing in 98a writes to any account but the admin's. Per-user sync is 98c
  (section 2 above).
