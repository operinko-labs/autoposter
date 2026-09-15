# Servers and Settings: configuration in the database, servers from the UI

**Status:** approved design, pre-implementation. Written 2026-09-13.

**Depends on:** the Jellyfin media-server work (`2026-09-12-jellyfin-media-server-design.md`, shipped in v0.3.0). Sibling of `2026-09-13-per-server-outcomes-and-catch-up-design.md`, which adds the Catch up and Retry failed buttons this design's Servers tab carries.

## 0. Summary

Today a deployment is configured by a mounted YAML document, a database row of overrides layered on top of it, and secrets from the environment or the wizard's state file. The result is that a media server cannot be added to a running deployment from the UI at all: `plex` and `jellyfin` are frozen sections the live editor refuses, the editor writes deltas rather than the document, and the wizard that can write a server block only runs before the deployment is configured.

This design makes the database the configuration. The overrides row becomes the whole document; secrets move into an encrypted table; the mounted file seeds the store once and is otherwise a drift report; every setting, servers and secrets included, is set from the Settings page, and a change that needs a restart gets a Restart button rather than a note. The Settings page is re-cut into tabs of accordions, with a Servers tab whose cards add, check and configure Plex and Jellyfin, read their library lists live, and pair libraries between the two servers from dropdowns when both are enabled.

Decisions the operator made during design, in order: secrets may be stored by the UI, with precedence stored → state file → environment; stored secrets are encrypted with one key kept in the state directory; a frozen change is applied by a restart button now and hot-applied later; the "override" pill goes, with no replacement; the mounted file is read only when the store is empty, so the ConfigMap can eventually be removed; the layout is a tab bar with one accordion per section, server cards saving on their own and everything else sharing one pending change with a sticky bar.

## 1. Goals and non-goals

Goals:

- Add, check, configure and remove a media server on a running deployment from the UI, including its credential and its excluded libraries.
- Pair Plex and Jellyfin libraries from the servers' own library lists, only when both servers are enabled.
- Make every setting and every secret settable from the UI, at any time, with the sources shown.
- Let a deployment run with a database URL and a volume and nothing else: no mounted file, no environment secrets.
- Re-cut the Settings page into tabbed sections with collapsible accordions.

Non-goals:

- Hot-applying server changes without a restart (filed as a follow-up; the restart button covers it now).
- Versioned configuration history (snapshots, restore, export and import already exist and stay).
- Emby, or a third server of any kind.
- Any change to what the configuration means: the schema, the defaults and the render version are untouched.

## 2. The configuration store

The `config_overrides` row becomes the configuration document. It stops being a delta: what it holds is what runs. It is validated through the same `build_config` path as today, carries the same revision, the same snapshots, the same drop cap and the same restore, export and import. The table and route names keep the word "overrides" for now; renaming them is churn with no behaviour. The served provenance loses `overridden_paths`, and the page shows no pill of any kind: the operator's question is "what is this set to", and the answer is the field.

Boot with an empty store seeds it once: from the mounted file when one is present, otherwise from the document the wizard staged. After that the file is not read at boot. When a mounted file is present and differs from the store, the System tab shows one notice, "the configuration file on disk differs from the stored configuration", with Import (through the existing import path, drop cap included) and Export. That is how a file kept in git keeps a role without being a second source of truth, and it is what makes the file removable: a deployment whose store is seeded needs no file.

The deployment with no file and an empty store boots into the wizard, which writes its document and its secrets into the store; from then on the same deployment boots into the application.

## 3. Secrets

A new table, `secrets`, holds one row per name (`AUTOPOSTER_TMDB_TOKEN`, `AUTOPOSTER_JELLYFIN_APIKEY`, …): the value encrypted, the time it was set, and nothing else. The key is `secret.key` in the state directory, generated on the first boot that needs it, mode 600, never stored in the database. Losing the volume loses the key and therefore every stored secret, which is the trade the operator chose: the volume already holds the admin password, and re-entering keys is the cost of a lost volume, not of a lost database dump.

Resolution order per name: the stored row, then the state file, then the environment. The result is exported into the process the way boot already does, so nothing downstream changes: the same `Secrets` object, the same rule that no secret ever enters `Config`, the same redaction. Each name reports its source to the UI as one of `stored`, `state file`, `environment`, `unset`; a stored value can be cleared, after which the next source down takes over. Values are never served back.

The Settings page's secrets accordion lists every name with its source and a Set / Replace / Clear control (Rotate for the webhook secret, which is generated). Server credentials live on their server's card, not in that list.

## 4. Boot and restart

Boot keeps its shape: resolve secrets, decide configured or not, run migrations, exec the application. Two changes. "Configured" is answered from the store (the file only when the store is empty), so `missing_server_setup` reads the stored document. The wizard writes to the store, document and secrets, through the same code the Settings page uses; it keeps its separate application and its setup token.

`POST /api/system/restart`, admin only, responds `{"restarting": true}` and re-executes boot exactly as the wizard's finish step does (`os.execv` of `python -m autoposter.boot`; the PID is kept, no supervisor is involved, and the same sequence runs under systemd, a terminal or a container). Boot re-reads the store, so a saved frozen change takes effect. Guards: refused while a full pass, a catch-up or an apply is mid-flight (the response names the run; the UI offers "restart when it finishes"); refused when the process detects it is one worker of several on one port; the listen host and port travel through `AUTOPOSTER_HOST` and `AUTOPOSTER_PORT`, which main reads, so a manual install on another port keeps it across restarts.

The restart list: every saved change whose path is frozen adds the path to a list kept in the document's metadata, so it survives a reload and shows to another admin. One banner, shared by every tab, names the settings waiting for a restart and offers Restart now; a restart clears the list. Hot-applying servers is the follow-up; when a section stops being frozen it simply stops appearing in the list.

## 5. The Servers tab

One card per server. A card shows the server's address, its credential with its source and a Replace control, the libraries the server lists as a tick-list (ticked libraries are managed; unticked ones become that server's `excluded_libraries`), the server-specific switches (Jellyfin's `replace_thumb_with_backdrop`; both servers' `badges.upload_to_<server>` and `operations.write_to_<server>`), a connection pill (connected with the version, refused, unreachable, checked-when), and Check connection, Reload libraries, Catch up, Retry failed (the last two from the sibling design), Remove server and Save. A card saves on its own, as a form, because a server is a unit: address, credential and exclusions belong together.

A fresh deployment shows the Plex card open and the Jellyfin card closed; once any server is configured, cards open only when they need attention (a configured server without its credential). An "Add a server" row lists the servers the schema knows and this deployment has not configured.

Routes, all under the application's normal session auth:

- `GET /api/servers`: every server the schema knows, with `configured`, `credential_source`, `checked` and the health poller's last answer.
- `PUT /api/servers/{name}`: address, excluded libraries and the server's switches, written into the stored document as that server's block; frozen, so it adds to the restart list.
- `DELETE /api/servers/{name}`: removes the block (the drop cap applies) and clears the server's stored credential; refused when it would leave no server configured.
- `POST /api/servers/{name}/check`: the wizard's connection probe, reused: a typed address with a typed credential, or the stored ones; it never sends a stored credential to a typed address.
- `POST /api/servers/{name}/libraries`: the server's library list (`id`, `name`, `type`), read live, same typed-credential rule.
- `PUT /api/servers/{name}/credential` and `DELETE …/credential`: store or clear the credential through the secrets table.

The wizard's own routes stay on the wizard's application; the code behind the probe and the library read moves to a module both applications import.

## 6. The library map

The map pairs a Plex library with the Jellyfin library that holds the same items, so the rest of the configuration, which speaks Plex library names, applies to both. Same-named libraries pair themselves and are shown as such. The accordion appears only when both servers are configured; a single-server deployment never sees it.

Each row is a Plex library (read from Plex's list) and a dropdown of Jellyfin libraries (read from Jellyfin's list), with a clear control. A name typed by hand is not accepted: the value must be one the server lists, which is what makes `absent` (sibling design) decidable. Saving writes `jellyfin.library_map` with only the pairs that differ by name. Changing the map adds nothing to the restart list on its own, but it triggers the sibling design's catch-up for Jellyfin, because it changes which items Jellyfin is expected to carry.

## 7. The Settings page

A tab bar across the top: Servers, Libraries, Artwork, Collections, Metadata, Integrations, System. Each tab holds one accordion per configuration section, collapsed by default except the one last opened (remembered per browser), with the section's field editors inside, unchanged in behaviour. The mapping of sections to tabs:

| Tab | Sections |
|---|---|
| Servers | the server cards, the library map |
| Libraries | per-library overrides, exclusions |
| Artwork | badges, overlays, providers, manual sources, collection posters |
| Collections | collections, playlists, groups |
| Metadata | operations, facts, MDBList, ratings |
| Integrations | Radarr and Sonarr sync, Tracearr, webhooks, notifications |
| System | general, scheduler, workers, secrets, config safety, the API docs switch, the drift notice |

The ordinary tabs share one pending change, as the page does today, with a sticky bar at the bottom that names the tabs holding pending edits and offers Discard, Preview impact, Save, and Save and re-render. Server cards save on their own and never join the pending change. There is no override pill and no per-field reset. Accordion headers carry at most two pills: the restart pill on a frozen section, and the connection pill on a server card.

"Dapper" is defined as: one type scale, one spacing scale, pills for state and nothing else, the same palette in both themes, no colour that is not a state, and no more than three levels of visual nesting (tab, accordion, row). The mockups agreed during design are the reference.

## 8. Migration and safety

On the first boot after this lands, a store that holds a delta is turned into a document: the mounted file (or the wizard's document) merged with the delta, exactly as `load_effective_config` computes it today, written back as the document, with a snapshot of the delta taken first. Every existing snapshot stays restorable: restoring a delta-era snapshot re-runs the same merge. Existing environment and state-file secrets keep working unchanged because they are the lower layers of the new precedence; a deployment that never stores a secret sees no difference, and its sources show as "environment".

The drop cap, the revision check and the confirm flow protect the document as they protected the delta. Import goes through them. Export redacts secrets as it does today; secrets are never part of the document.

## 9. Follow-ups filed, not in scope

- Hot-apply of server changes: rebuild the registry, the health pollers, the job set and the route gates on save.
- Removing the ConfigMap from the operator's own deployment once the store is seeded.
- A CLI to export the key and the stored secrets for a volume migration.

## 10. Testing

Through the real entry points: boot with an empty store and a file seeds the store and never reads the file again; boot with no file and an empty store serves the wizard, and the wizard's finish writes the store; a stored secret wins over the state file and the environment, and clearing it falls through; the encryption key is generated once and a value encrypted with it is unreadable without it; `PUT /api/servers/jellyfin` on a Plex-only deployment adds the block, lists `jellyfin` in the restart list, and after `POST /api/system/restart` the registry holds both servers; `DELETE` of the last configured server is refused; the library-map accordion is served only with both servers configured and refuses a name the server does not list; the restart route is refused during a run; a delta-era snapshot restores correctly after the migration; the Settings page's tabs hold every section the served config has, each exactly once.
