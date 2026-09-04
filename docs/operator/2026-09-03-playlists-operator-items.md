# Playlists — what the operator should know

Written at the close of **98a** and brought up to date at the close of **98c**
(per-user playlist sync). Nothing here blocks what shipped. Section 1 is the
one outstanding action.

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

## 2. What per-user sync does, now that it has shipped

A definition can name other Plex users, by the display title Plex shows for
each of them:

```yaml
playlists:
  sync_to_users_apply: false   # OFF: report what each user would receive
  definitions:
    - title: Marvel Cinematic Universe
      builder: imdb_list
      params: {list: ls539646485}
      sync_to_users: [alice, bob]
```

**Nothing is written into anybody's account until
`playlists.sync_to_users_apply` is switched on.** It is a second switch, not a
rename of `apply_to_plex`: with `apply_to_plex: true` and
`sync_to_users_apply: false` the admin playlists are live and every user's copy
is only reported. That is the recommended way to start.

`sync_to_users: all` needs `playlists.sync_all_users: true` as well, and the
config refuses to load otherwise — the refusal names the definition.
`playlists.exclude_users` is the companion list `all` never resolves to.

**Two caps, both refusing entirely rather than part-way.**
`playlists.max_users` (25) caps how many people one pass may write to;
`playlists.max_user_writes` (50) caps the total write operations across every
copy. Past either, the whole fan-out refuses and reports the numbers, and the
admin playlists are still reconciled. This matters because the expensive shape
is real: seventeen accounts times a hundred-member playlist is a lot of
requests, and "the first fifty" of four hundred is the same accident spread
over eight passes.

**What is skipped, always by name in the report:**

- a **PIN-protected** user — this service never holds a Plex PIN, and the check
  happens before any plex.tv call for that person;
- a user plex.tv mints no access token for — treated as a refusal, never as a
  reason to fall through to some other identity;
- a user the configuration names who does not exist on this account (a renamed
  account reads this way, which is intended: their copy then becomes a
  *reported* sweep candidate rather than a silent deletion);
- **you**, the owner — the admin playlist *is* that definition;
- a user whose account already holds a playlist of that title that this service
  did not create. It is theirs and is never touched, and no second one is made
  beside it.

**Removal.** A user dropped from `sync_to_users`, a definition deleted, and
`playlists.delete_unconfigured` all go through one sweep: off means reported,
on means deleted, and `playlists.max_deletes` counts admin playlists and user
copies **together** — so removing a definition that fanned out to seventeen
people needs a cap that admits eighteen deletions. That is deliberate: it is
the blast radius, made visible. `POST /api/playlists/ops/delete` deletes the
admin playlist and leaves the copies to that sweep, telling you how many remain.

**Two things it does not do.** A user's copy is created in the definition's
order and is **not reordered** afterwards (the cost of enforcing order across
every copy cannot be planned before it is spent, and this phase refuses what it
cannot cap). And it does not pre-check per-library access: if somebody cannot
see a library the playlist draws from, that copy fails and is reported by name.

**Prerequisites.** `AUTOPOSTER_PLEX_ACCOUNT_TOKEN` must be set and must be this
server's **owner's** account token — the pass verifies that before its first
write and refuses the whole stage with a named line if it is not. Section 1's
rotation is still the outstanding action.

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
- Nothing writes to any account but the admin's unless
  `playlists.sync_to_users_apply` is switched on — including the preview
  endpoint, which forces a dry run and therefore cannot reach anybody's
  account whatever the switches say.
- A playlist in somebody else's account is ours only while a
  `managed_playlist_users` row names its rating key. One they made themselves,
  or one Kometa made, is never modified and never deleted, whatever its title.
