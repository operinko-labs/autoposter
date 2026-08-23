# Full feature parity — gap list and roadmap

**Date:** 2026-08-22
**Status:** Planning document, for review
**Sources:** `.superpowers/sdd/parity-posterizarr-inventory.md` (67 in-scope-or-decidable
gap rows out of ~110 features), `.superpowers/sdd/parity-kometa-inventory.md` (134 out of
~190), `docs/superpowers/specs/2026-08-20-autoposter-design.md` §6/§6a, and the shipped
codebase under `src/autoposter/`.

## Scope decisions (locked)

These bound every row and phase below. They were decided by the user, not inferred.

- **Capability parity, not config compatibility.** autoposter's own config and UI must
  be able to achieve every in-scope outcome. Kometa/Posterizarr YAML is never parsed and
  no importer is planned. Kometa's template/DSL machinery is superseded by design; the
  capabilities its defaults files deliver (overlay families, collection packs) are in
  scope as capabilities.
- **External services: only what the user uses today** — TMDB, IMDb datasets, MDBList,
  TVDB, Fanart.tv, plus the self-hosted stack (Plex, Radarr, Sonarr, and — new —
  Tracearr). TMDB-backed builders (discover, charts, lists) and MDBList lists are in.
  Trakt, Letterboxd, AniDB/AniList/MAL, and anything needing a new account or OAuth are
  out — Appendix A prices what re-scoping each would cost.
- **Playlists: in. Notifications: in.**
- **Cutover-critical, sequenced first:** the user's Posterizarr config POSTs run
  notifications to an n8n webhook (`kometa-trigger`) that chains downstream automation.
  Without an equivalent, that chain dies silently the day the old tools are switched
  off. This is the strongest sequencing constraint in the roadmap: a parity feature that
  gates turning Posterizarr/Kometa off at all.
- **Tautulli is retired by user decision.** The user is deploying
  [Tracearr](https://github.com/connorgallopo/Tracearr) instead in the coming days, so
  the design doc's never-built Tautulli intake and Kometa's Tautulli builders are
  removed from the gap list rather than carried — Tracearr's outbound custom webhooks
  and read-only REST API (both verified from its repo and tracearr.com; nothing beyond
  that is verified) take over both roles.
- **Assumed done:** the full-pass trigger (`POST /api/sweep` + dashboard button),
  in flight on `feat/full-pass-trigger`.

Two spec-level non-goals stay non-goals throughout: Jellyfin/Emby support and Kometa's
`add_missing` family. Where this roadmap includes something the 2026-08-20 spec listed
as a non-goal (clearart, Discord notifications), the full-parity decision supersedes
the spec and the row says so.

## Phase summary

Ordered by: cutover-criticality first, then dependency order, then difficulty ramp.
Sizes are relative to shipped phases (4a: a handful of endpoints; 4c: ~13 commits of
endpoints + two UI pages; phases 2 and 3: the largest shipped units, multi-week each).

| Phase | Title | Size | Notes |
|---|---|---|---|
| 5a | Outbound notifications and webhooks | small (≈ half of 4a) | **Blocks cutover** |
| 5b | Cutover verification sweep | small | **Blocks cutover confidence** |
| 5c | Tracearr webhook intake | small | Harvest-first; needs the live instance |
| — | **Cutover: retire Posterizarr and Kometa** | — | After 5a + 5b + full-pass trigger |
| 6a | Collections UI, search, compare, override actions — **delivered** (rows 22–24; collection stats, diff-now, per-job run-now) | ≈ 4c | Spec §6 debts |
| 6b | WebSocket (dashboard + live log tail) | small | Spec §6 debt |
| 6c | Config editor with hot-reload and impact preview | large (≈ phase 2) | Spec §6 debt |
| 6d | Provider-candidate picker and logo browser | ≈ 4c | Spec §6 debt; feeds 11 |
| 6e | Manual mode and testing mode | ≈ 4c | |
| 7a | Render and selection long tail | small–medium | Many S items, no dependencies |
| 7b | Artwork modes: backup, restore, reset, revert, logo updater | medium | |
| 8a | Builder engine core | large | Gate for 8b–8c, 10, 13 |
| 8b | List builders, wave 1 | medium | |
| 8c | TMDb discover, IMDb search/awards, Tracearr builders | medium | |
| 9a | Filter model, tier 1 | medium | Gate for 9b and dynamic packs |
| 9b | Plex search DSL and sort matrix | **XL — multi-week even parallelised** | |
| 9c | Native smart collections | medium | |
| 10a | Dynamic collections engine | large | |
| 10b | Dynamic packs and defaults equivalents | medium–large | |
| 10c | People: person builders and dynamic person types | medium | |
| 11a | Asset-quality flags and backfill | medium | Action Center, part 1 |
| 11b | Action Center review queue and bulk actions | large | Action Center, part 2 |
| 12a | Overlay families | large | |
| 12b | Custom overlay mechanics | large | |
| 13 | Playlists | medium–large | Reuses 8a builders |
| 14a | Metadata operations completion | medium | |
| 14b | Per-item metadata overrides and per-library config | large | |
| 15 | Operations and observability polish | medium | |

---

## Part 1 — Gap list, easiest first

Every in-scope missing/partial/unknown row from both inventories appears below or in
Appendix A; the completeness pass and counts are in the footer. Rows marked
**(verify)** carry an inventory "unknown" forward as a named verification task instead
of resolving it by assumption. Config impact: **exercised** = the user's real config
exercises it today; **parity-only** = present in the tools but disabled/unused;
**spec §6** = owed by the autoposter design spec itself. Rows carrying
**answered 5b:** were verified in phase 5b; the file:line evidence behind each
one-liner is in the `.superpowers/sdd/p5b-task-{1,2,3}-report.md` and
`p5b-close-report.md` files.

| # | Gap | What it is | Difficulty | Config impact | Depends on |
|---|---|---|---|---|---|
| 1 | Smart vs dumb CS collections (verify) | Confirm whether `buckets.py` creates Plex-native smart collections or reconciled dumb ones. **answered 5b:** Plex-native smart — created with `smart=True` and live `contentRating` filters (`reconcile.py:364-374`); no membership is ever maintained | S — read code + one Plex call | exercised | — |
| 2 | `collection_order: custom` applied (verify) | Confirm `reconcile.py` actually applies builder ordering to Plex. **answered 5b:** yes — created with `sortUpdate("custom")` (`lists.py:145-148`) and source order enforced every pass by `_enforce_order`/`moveItem` (`lists.py:30-59,170`); Oscar year collections deliberately use `release` (`sources.py:82-84`) | S — read code | exercised (charts) | — |
| 3 | Metadata lock semantics (verify) | Confirm whether `plex/writer.py` locks fields the way Kometa's lock/unlock sources do. **answered 5b:** yes — every written field carries `{field}.locked=1` in the same edit (`writer.py:101-104`), genres via `genres.locked=1` and `addGenre/removeGenre(locked=True)` (`writer.py:87,147,156`); unchanged fields are never touched, matching Kometa's lock-on-edit default | S — read code | exercised | — |
| 4 | Collection poster render path (verify) | Styled text-composite (Posterizarr `CollectionTitlePosterPart`) vs hosted-image-only; is `AddCollectionTitle` honoured? **answered 5b:** hosted-image-as-is — `apply_poster` uploads the local-override or hosted-default bytes unchanged (`collections/posters.py:161-238`) and no `AddCollectionTitle` equivalent exists (`config/schema.py:209-254` carries only `collections.posters: bool`). And that *is* live parity: 0 of 325 collection posters on the production Plex carry a Posterizarr provenance stamp, the managed families are sha256-identical to the exact hosted defaults autoposter fetches (IMDb Top 250, Oscars Winners 2026, Age 5+/6+ Movies all verified byte-for-byte), and the unmanaged franchise collections carry heterogeneous community art no single-font pipeline produced — so the `CollectionTitlePosterPart` block is an inert Posterizarr default here, never exercised, and the earlier **exercised** marking was wrong. No fix; the unused compositing capability is tracked as row 105 | S — read code + compare output | parity-only (5b refuted "exercised": the fonts are configured only because Posterizarr writes every config block) | — |
| 5 | `collection_mode` / `sort_title` on creation (verify) | Confirm created collections get display mode and sort-title prefixes the defaults set. **answered 5b:** no on creation — `collection_mode` is set nowhere in the codebase, and a sort title only on the separator (`reconcile.py:254`); adopted Kometa collections keep theirs, but a newly created one (e.g. each year's new Oscars collection) gets neither → follow-up row 104 | S — inspect created collections | exercised (indirect) | — |
| 6 | Download-only mode (verify) | Posterizarr `ImageProcessing: false` — fetch/move art with no compositing; does any autoposter mode match? **answered 5b:** no config switch exists — every provider fetch is resized/re-encoded through the compositor; the only no-compositing path is the per-*item* `source_mode: "verbatim"` (`pipeline.py:438-443`). Per-kind toggles can drop overlay/border/text (`schema.py:94-103`) but the canvas resize still runs. Not exercised: the user runs `ImageProcessing: "true"` (posterizarr-config.txt:167). A switch, if ever wanted, stays with 7a | S — likely a per-kind switch | parity-only | — |
| 7 | Show-vs-movie overlay override (verify) | `showoverlayfile` — a different fade for show posters than movie posters. **answered 5b:** no override — one `ArtKindConfig` per art kind, shared by movies and shows (`schema.py:126-134`; `art_config_for`, `pipeline.py:97-98`). Matches the user's config: `showoverlayfile: ""` (posterizarr-config.txt:99), so shows already share the movies' `overlay.png`. A split stays with 7a | S — schema question | parity-only | — |
| 8 | `AddEPTitleText`/`AddEPText` toggles (verify) | Title-card text lines independently toggleable, or rendered unconditionally? **answered 5b:** two independent blocks with their own `add_text` (`schema.py:117-123`, `pipeline.py:100-119,471-475`), except disabling the title line also suppresses the episode line; the user runs both on (posterizarr config `AddEPTitleText`/`AddEPText` both true), so the asymmetry is not exercised | S — read `render/` | exercised (at defaults) | — |
| 9 | `UseBGLogo` / `TextlessPosterBypass` (verify) | Logo-on-backgrounds and with-text-fallback-despite-textless behaviours. **answered 5b:** neither exists — clearlogo compositing is poster-only (`pipeline.py:398` gates on `art_kind == "poster"`), so no `UseBGLogo`; and while the ladder parks a with-text candidate as the fallback when textless is preferred (`ladder.py:87-96`), the compose path never branches on the base's textlessness — `is_textless` is recorded on the row and nothing more (`pipeline.py:389,508`) — so no `TextlessPosterBypass`. Both `"false"` in the user's config (posterizarr-config.txt:154,164). Stays with 7a (overlaps row 41's with-text branch) | S — read code | parity-only | — |
| 10 | TVDB subscriber PIN (verify) | `apikey#pin` key format support in the TVDB client. **answered 5b:** not supported — the login POSTs `{"apikey": ...}` verbatim with no `pin` field and no `#` splitting (`tvdb.py:108`), so a subscriber `apikey#pin` key would fail login. Not exercised: the user's key is a plain UUID with no `#` (posterizarr-config.txt:3). Stays with 7a | S — read client | parity-only | — |
| 11 | Season/title-card fallback chain (verify) | `ShowFallback`, `UseBackgroundAsTitleCard`, `BackgroundFallback` — what does autoposter do when no season/episode art exists? **answered 5b:** no cross-kind fallback — when no provider offers art for the requested kind the row is marked `no_art` and the render stops (`pipeline.py:380-384`); `select_artwork` walks providers for that kind only (`ladder.py:64-97`), and e.g. TVDB answers `[]` for title cards outright (`tvdb.py:170-173`). Matches the user's config: all three fallbacks are `"false"` (posterizarr-config.txt:207,234,235). A chain stays with 7a | S — read ladder code | parity-only | — |
| 12 | `FollowSymlink` semantics (verify) | Whether security-driven symlink resolution conflicts with opt-in symlinked asset trees. **answered 5b:** no conflict — path derivation is purely lexical (`naming.py:17-36` compares string segments, never resolves), so symlinked library or asset mounts render fine; the two places that do resolve do it to *both* sides symmetrically (artwork serving, `api/artwork.py:89-92`; cleanup scan, `scheduler/jobs.py:344-367`), so a symlinked `assets_root` stays self-consistent. The one deliberate refusal: the serving endpoint rejects an in-tree symlink whose target escapes the resolved root (`api/artwork.py:79-92`) — a security posture, not a render-path behaviour. Not exercised: `FollowSymlink: "false"` (posterizarr-config.txt:128) | S — read `naming.py`/adoption | parity-only | — |
| 13 | `SkipTBA` regex + delete-on-match (verify) | Whether skip words are regex-capable and whether a matching existing card is deleted. **answered 5b:** literal case-insensitive whole-title match, not regex (`pipeline.py:137-141`); an existing card is left in place — the skip returns before any compose/upload and nothing deletes it (`pipeline.py:320-323`); user config: `SkipTBA=true` (covered), `SkipJapTitle=false` (not exercised) | S — read code | exercised | — |
| 14 | Multiple movie/show versions (verify) | Posterizarr covers all versions (theatrical/director's cut); confirm autoposter's per-item model does. **answered 5b:** covered — one row per Plex item is Plex's own versions-share-one-poster granularity; live probe: 50/1,954 movies + 224 episodes have multiple Media (LOTR/Ben-Hur are true theatrical-vs-extended pairs), 0 edition splits. Divergence is cosmetic: badges read media[0], which Plex does not quality-sort, vs Kometa's weight-picked best (values.py:121). Detail: `.superpowers/sdd/p5b-task-3-report.md` → follow-up row 106 | S–M — may reveal a real gap | exercised (implicit) | — |
| 15 | `name_mapping` / illegal characters (verify) | Confirm `render/naming.py` handles characters Plex titles allow but filesystems don't. **answered 5b:** titles never enter paths — asset paths come from the item's on-disk folder name (`naming.py:17-36,67-82`), and resolution raises rather than falling back to the title (`plex/client.py:203-214`); guarded by two tests in `tests/test_naming.py`; the user's Kometa config sets no `name_mapping` | S — read + test | exercised (implicit) | — |
| 16 | Render throughput without a text-size cache (verify) | Posterizarr caches ImageMagick text measurements; confirm fingerprint gating keeps a full re-render tolerable without one. **answered 5b (partial):** enqueue measured — 15,000 items in ~2s (1.83–2.35s set-based insert, PR #34, pinned by a 10s-bound test). Unchanged-item cost on a forced pass: an adopted row short-circuits on local hashes alone — overlay/font/asset SHA-256s plus a fingerprint compare, zero provider calls, zero ImageMagick (`pipeline.py:332-352`); an already-rendered row re-runs provider selection (24h `provider_cache`) and re-downloads the base to re-hash it before the same compare (`pipeline.py:367-389,426-436`) — still no ImageMagick, because text measurement (`textfit.py:92-110`, one magick subprocess per text block) runs only past the fingerprint gate (`pipeline.py:480`), and badge text is Pillow in-process behind its own gate (`badges/compose.py:129-142`, `pipeline.py:658-662`) — skipping the measurement entirely is stronger than caching it. Full changed-library throughput is a cutover-day observation — record it during the first production full pass | S — measure a forced sweep | exercised (implicit) | full-pass trigger |
| 17 | Tracearr payload/API harvest (verify) | Capture real webhook payloads and REST responses from the user's live instance before writing any schema — payload shapes are **unverified** until then | S — harvest, in the project's oracle style | new integration | live Tracearr deploy |
| 18 | **Outbound job/run webhook (n8n)** | POST a completion notification to a configured URL when a run/sweep finishes — the `kometa-trigger` chain's lifeline. The exercised Posterizarr path (`AppriseUrl`) is in fact a plain webhook POST to n8n | S — one POST with retry | **exercised — cutover-critical** | — |
| 19 | Notification event taxonomy | run_start / run_end / error / changes / delete payloads; per-collection changes webhooks (Kometa `changes_webhooks`) | S–M — the contract design is the work | parity-only | 18 |
| 20 | Discord / Apprise formatting | Discord-shaped payloads and Apprise-endpoint support as additional notification targets | S | parity-only | 18, 19 |
| 21 | Tracearr webhook intake | Accept Tracearr's outbound custom webhooks as a trigger source (the role Tautulli's agent held); route into the existing intake/normalize path | S–M — new route + payload mapping | replaces a retired trigger | 17 |
| 22 | Search box | answered 6a: debounced search input on the Library page, wired to the existing escaped-ilike `search` param, offset reset on change | S — one input + wire-up | spec §6 | — |
| 23 | Multi-art-kind compare | answered 6a: item detail renders one labelled base+live pane pair per art kind in `renders` (a movie's poster and background side by side) | S — UI only, endpoints exist | spec §6 | — |
| 24 | Clear-manual-override action | answered 6a: `POST /api/items/{id}/renders/{art_kind}/clear-override` renames the override file to `.disabled`, clears fingerprints, enqueues a re-render; button on `provider: manual` rows | S | spec §6 | — |
| 25 | `sync_mode: append` | Builders currently only sync (diff-to-source); append mode keeps manual additions | S — reconciler flag | parity-only | — |
| 26 | `limit` per collection | Cap builder results | S | parity-only | — |
| 27 | Collection dry-run preview | Show what a diff would do without applying (Kometa `test`) | S — reuse diff, skip apply | parity-only | — |
| 28 | Collection lifecycle ops | `blank_collection`, `delete_collection(s)_named`, `delete_collections` (managed/configured), `mass_collection_mode` | S — plexapi calls | parity-only | — |
| 29 | Collection labels | `label` / `label.remove` / `label.sync` on the collection object | S | parity-only | — |
| 30 | Collection summaries from source | `tmdb_summary`/`tmdb_description`/`tvdb_summary` pulls instead of static text only | S — TMDB/TVDB clients exist | parity-only | — |
| 31 | `sync_to_mdb_list` | Push collection membership out to a MDBList list | S — MDBList client exists | parity-only | — |
| 32 | `mass_user_rating_update` | Overwrite user rating library-wide | S — same shape as shipped ops | parity-only | — |
| 33 | `mass_original_title_update` / `mass_added_at_update` | Two more mass-op fields | S | parity-only | — |
| 34 | `genre_mapper` / `content_rating_mapper` | Normalise values ("Sci-Fi & Fantasy" → "Sci-Fi") before writing | S — a mapping table in config | parity-only | — |
| 35 | Item exemptions | `ignore_ids`/`ignore_imdb_ids`/`ignore_labels` + a `skip_autoposter`-style per-item opt-out label (Posterizarr `skip_posterizarr`) | S — one check in the pipeline | parity-only | — |
| 36 | Plex maintenance toggles | `clean_bundles` / `empty_trash` / `optimize` scheduled ops (user has them off) | S | parity-only | — |
| 37 | `assets_for_all_collections` | Local poster override for collections autoposter doesn't manage | S — extend the existing override lookup | parity-only | — |
| 38 | `LibraryLanguageOverrides` | Per-library language-ladder override (TC keeps `xx` lead) | S — config plumbing into the ladder | parity-only | — |
| 39 | `SkipLocal*TextAdd` per kind | Don't add text to locally supplied assets, as per-art-kind config (today only per-item `source_mode: verbatim`) | S | parity-only | — |
| 40 | `SkipJapTitle` | Skip title cards whose episode title is Japanese/Chinese | S — Unicode-range check | parity-only | — |
| 41 | `SkipAddText` family | Skip text/overlay/border when the chosen provider art is known with-text — the "provider says with-text" branch isn't modelled | S–M — touches selection metadata | parity-only | — |
| 42 | Newline rules | `NewLineOnSpecificSymbols`/`NewLineWords` — forced line breaks and a manual hyphenation map | S — text layout only | parity-only | — |
| 43 | Season name override | `OverrideSeasonName` + specials wording | S | parity-only | — |
| 44 | `UseOriginalTitle` | Original-language title instead of localized | S — field already fetched | parity-only | — |
| 45 | `UseClearart` | Prefer clearart over clearlogo (spec listed it a non-goal; full parity supersedes) | S — Fanart client already sees it | parity-only | — |
| 46 | Logo recolour | `ConvertLogoColor`/`LogoFlatColor` — flatten a logo to one colour | S — one image op | parity-only | — |
| 47 | Local-only switch | `DisableOnlineAssetFetch` globally and per kind — render from local assets, zero provider calls | S — short-circuit the ladder | parity-only | — |
| 48 | Season/episode template assets | One manual image applied to all seasons/episodes of a show (`SeasonTemplate`/`EpisodeTemplate`) | S — manual-assets lookup rule | parity-only | — |
| 49 | Separator collections | Blank "index card" collections for visual grouping | S — cosmetic | parity-only | — |
| 50 | `blur(##)` / `backdrop` overlays | Whole-poster blur and solid-colour layer primitives | S — two Pillow ops | parity-only | — |
| 51 | API-key auth for read endpoints | Key-based access (`X-API-Key`) beyond the webhook secret, for widgets/scripts | S | parity-only | — |
| 52 | Storage stats + homepage endpoints | `/api/assets/stats`-style per-library counts/sizes for gethomepage.dev widgets | S — SQL + a walk | parity-only | 51 |
| 53 | Per-run stats rollup + charts | Per-run posters/seasons/BG/TC/errors counts persisted; duration/success charts in the UI | S–M — events exist, rollup doesn't | parity-only | — |
| 54 | Schedule editing UI | Edit APScheduler cadences from the UI (read-only today) | S–M | parity-only | 6c helps |
| 55 | Overlay/font file management UI | List/upload overlay PNGs and fonts from the UI | S–M | parity-only | — |
| 56 | Trivial Plex builders | `plex_id`/`plex_rating_key`, `plex_pilots`, generic `plex_all` at any level | S — once the engine exists | parity-only | 8a |
| 57 | `text_file` builder | IDs from a local plain-text file | S | parity-only | 8a |
| 58 | `mdblist_list` builder | Any MDBList list with sort/limit — client already exists for ratings | S | parity-only | 8a |
| 59 | IMDb list builders | `imdb_list` / `imdb_id` / `imdb_watchlist` (public), on the IMDb GraphQL surface `charts.py` already uses | S–M | parity-only | 8a |
| 60 | TVDb list builders | `tvdb_list` / `tvdb_movie` / `tvdb_show` — client exists for artwork | S–M | parity-only | 8a |
| 61 | Arr builders | `radarr_all` / `radarr_taglist` / `sonarr_all` / `sonarr_taglist` — `arr/client.py` exists | S | parity-only | 8a |
| 62 | TMDb explicit/simple builders | `tmdb_movie`/`show`/`list`/`collection`/`company`/`network`/`keyword` | S–M — thin API calls | parity-only | 8a |
| 63 | TMDb chart builders | popular / top_rated / trending daily+weekly / now_playing / upcoming / airing_today / on_the_air, region-aware | S–M | parity-only | 8a |
| 64 | `plex_watchlist` builder | Items from the Plex account watchlist. Needs a plex.tv account token — an existing account, not a new one, but a new auth surface; **flag for user confirmation** | S–M | parity-only | 8a |
| 65 | Remove-overlays revert | One-shot "restore clean bases to Plex" — the bases exist in `/assets`; no operation pushes them back | S–M — bulk upload path | parity-only | — |
| 66 | Poster reset mode | Reset a library to Plex default metadata art (`-PosterReset`) | S–M — per-item revert data exists via EXIF | parity-only | — |
| 67 | Logo revert mode | Unlink only fingerprinted logos from Plex | S–M | parity-only | 71 |
| 68 | Hub visibility + priority | `visible_library/home/shared` pinning (Plex Pass) and `hub_priority`/`auto_sort_hubs` | S–M — plexapi hub calls | parity-only | — |
| 69 | Member-item edits | `item_label` and per-member metadata application from a collection definition | S–M | parity-only | — |
| 70 | Per-collection cadence and date windows | Per-collection refresh schedule + range gating — the real capability inside Kometa's schedule DSL that seasonal collections need | S–M — APScheduler already in place | parity-only | — |
| 71 | Logo updater mode | Scan Plex for missing clearlogos, fetch, upload as Plex metadata — a pipeline distinct from posters | M — new upload target | parity-only | — |
| 72 | WebSocket | Replace the dashboard's 5-second polling; add the live log tail view | M — server infra + client swap (client.ts is pre-shaped for it) | spec §6 | — |
| 73 | Provider-candidate picker (Asset Replacer) | Search all providers for an item, browse candidates, pick one; persisted as a manual override. Includes the logo browser | M — designed in spec §6, not built | spec §6 | — |
| 74 | Manual mode | Build one styled artifact (any kind, incl. collection cards) from an arbitrary local file or URL — API + UI | M — reuses the render path | parity-only | 73 helps |
| 75 | Testing mode | Sample renders (short/medium/long text, every artifact kind) against current config — cheap insurance before mass re-renders | M — render path against fixtures | parity-only | — |
| 76 | Backup mode | Download all artwork from Plex into a Kometa-structured backup tree | M — bulk download + naming | parity-only | — |
| 77 | Restore mode | Push backup-tree art back to Plex with type/library/item filters | M | parity-only | 76 |
| 78 | Show title on season posters | `AddShowTitletoSeason` — extra text/logo block above season text | M — new layout region | parity-only | — |
| 79 | Tracearr activity builders | Most-watched / popular collections from Tracearr's read-only REST API (the role of Kometa's Tautulli builders and its `tracearr` chart default) | M — new client + builder | parity-only | 8a, 17 |
| 80 | `tmdb_discover` | The full TMDb discover query surface (genres, dates, votes, keywords, watch providers, …) | M–L — the biggest single TMDb surface | parity-only | 8a |
| 81 | `imdb_search` | IMDb advanced search (type, votes, rating, genre, keyword) via GraphQL | M | parity-only | 8a |
| 82 | Award events beyond Oscars | BAFTA, Cannes, Emmys, Golden Globes, Razzies, … — same shape as `awards.py`, each needs its IMDb event source | M–L — per-event sources | parity-only | 8a |
| 83 | Person builders | `tmdb_actor/director/writer/producer/crew` filmographies + `tmdb_person` summaries/posters + birthday/deathday gating | M | parity-only | 8a |
| 84 | Additional mass-op sources | TVDb-backed sources for genre/dates/studio ops; IMDb-dataset-backed where the TSVs carry the field | M — per-field source matrix | parity-only | — |
| 85 | `mass_imdb_parental_labels` | IMDb parental-guide labels (violence, profanity, …) — needs parental-guide data the TSV datasets don't carry; GraphQL feasibility is a risk | M | parity-only | — |
| 86 | Metadata backup | Export current library metadata to a YAML backup — the undo story for mass ops | M | parity-only | — |
| 87 | Lock/unlock/remove/reset controls | Every mass op accepting lock/unlock/remove/reset as its source | M — after row 3 settles current behaviour | parity-only | 3 |
| 88 | `builder_level` collections | Collections whose members are seasons/episodes (overlay-level support exists; collection-level doesn't) | M | parity-only | 8a |
| 89 | Per-definition Arr overrides | `radarr_*`/`sonarr_*` per collection + `item_radarr_tag`/`item_sonarr_tag` member tagging | M | parity-only | 8a |
| 90 | `smart_filter` / `smart_label` | Plex-native smart collections from a filter, plus the label-then-smart indirection | M — once the DSL exists | exercised (if row 1 says CS buckets should be smart) | 9b, 1 |
| 91 | `plex_collectionless` | Items in no other collection — needs a full collection-membership map | S–M | parity-only | 8a |
| 92 | Per-library config override matrix | Any global block overridable per library (autoposter has it only where the user's config needed it) | M — schema surgery | parity-only | — |
| 93 | Defaults-pack equivalents | Config presets reproducing Kometa's default families in scope: charts (basic/tmdb), genre, studio, streaming, resolution, aspect, year, decade, country, region, continent, franchise, universe/based (via MDBList), seasonal (via row 70), content-rating regionals | M–L — presets on top of engines | parity-only | 8a, 9a, 10a |
| 94 | Config editor with hot-reload + impact preview | Write endpoint with schema validation, hot-reload on save, config-version fingerprint staleness, "this would re-render ~2,400 items" preview with apply-now/later | L — validation + preview machinery | spec §6 | — |
| 95 | Builder engine core | Generic builder abstraction: registry, per-collection config schema, ID-list output with ordering, sync/append, limit, dry-run — everything rows 56–64, 79–83 plug into | L — the gate for all list work | parity-only (three builders exercised today are hardcoded) | — |
| 96 | Filters subsystem | Post-builder filtering on ~60 attributes with `.not/.regex/.gt/…` modifiers, applicable to any definition | L — shares its predicate model with 9b | parity-only | 95 |
| 97 | Custom overlay mechanics | User-supplied overlay images (file/url), templated text overlays with `<<variables>>` and modifiers, configurable positioning/backdrops, generic queue engine, general cross-overlay suppression | L — generalising what `badges/` hardcodes | parity-only | — |
| 98 | Playlists | Playlist definitions from any builder, ordering inherited, `libraries`, `sync_to_users`/`exclude_users`, delete + report, preset playlist file | M–L — new subsystem, but builders do the hard part | parity-only (user never used them; **scoped in**) | 95 |
| 99 | Per-item metadata overrides | The capability of Kometa's metadata files — declarative per-item/season/episode edits (titles, summaries, ratings, dates, tags, images, advanced settings) via autoposter's config/UI, not YAML compatibility | L — whole subsystem | parity-only | 94 helps |
| 100 | Overlay families | aspect, language_count, content-rating regionals, direct_play, network/studio, ribbon, status (TVDb), streaming (TMDB watch providers), versions | L total — each family is S–M render work plus its data source | parity-only | facts plumbing; 8a for ribbon |
| 101 | Plex search DSL | `plex_search` + the full search/sort attribute matrix — a query language against Plex (~60 attributes, modifiers, and/or nesting, limits, sorts) | **XL — multi-week even fully parallelised** | parity-only (exercised only indirectly via CS defaults) | 9a model |
| 102 | Dynamic collections engine | One collection per distinct value: enumeration per type, include/exclude/addons, key-name/title overrides and formats, lifecycle (create, delete-below-minimum), sync | **XL** — only the CS content-rating instance exists | exercised (CS buckets are one instance of it) | 95; 9a for some types |
| 103 | Action Center + asset-quality tracking | Track *why* each chosen asset is imperfect (language rank, provider rank, truncated text, text-fallback, missing), a review queue with resolve/replace/delete and bulk ops | **XL — the largest net-new subsystem**; nothing models "succeeded but suboptimal" | parity-only (but the biggest Posterizarr UI feature) | 73 |
| 104 | Sort-title prefix / `collection_mode` on the create paths | Row 5 follow-up (5b): the create paths (`reconcile.py:364-368`, `lists.py:145-150`) set neither Kometa's `!<section>_` sort-title prefixes nor a display mode, so a collection created fresh (each year's new Oscars collection, a new age bucket) sorts by bare title while its adopted siblings keep Kometa's prefix | S — copy the family's sort-title scheme into the create paths | exercised (indirect) | — |
| 105 | Collection-poster text compositing | Row 4 follow-up (5b): Posterizarr *can* composite a styled title plus a "COLLECTION" line onto collection posters (`CollectionPosterOverlayPart` / `CollectionTitlePosterPart`), a path this deployment never exercised (evidence in row 4 — nothing live carries it). Implementing it would need a new config section, the collection font (`Colus-Regular.ttf`, not in `assets/fonts/`), a compose step between fetch and upload in `collections/posters.py`, and a two-block text render; the phase-1 argv builders are canvas-agnostic (`render/compositor.py:19-45,94-118`, `render/textfit.py:92`) so they reuse, but no Posterizarr-styled oracle exists to prove parity against | M — reuse compositor + textfit; new config, font, tests; no oracle | parity-only | — |
| 106 | Multi-version badge selection & edition splits | Row 14 follow-up (5b): badges describe Media[0], which Plex orders arbitrarily (Ben-Hur: AAC/198min badged while a DTS-HD MA remux sits at media[1]); Kometa surfaces the best matching variant by overlay weight. Separately, an edition-split second item can never be recorded by the GUID-based webhook/safety net (getGuid returns one item; safety net re-enqueues forever, arr/sync.py:341-352) and two splits in one folder would share an asset path | Low — deterministic best-Media pick in media_info_from_plex (~30 lines + tests); split handling larger but exercised by 0 library items | Not cutover-relevant: badge delta is cosmetic on 50 movies/224 episodes; library has no edition splits | — |

---

## Part 2 — Roadmap

Phases continue the project's numbering. Each is a shippable PR-sized unit producing
working software. "Size" compares to shipped phases. Verification rows land inside the
phase that needs their answer, as that phase's first step.

### Phase 5 — cutover

#### 5a — Outbound notifications and webhooks

**Goal:** the n8n chain survives cutover. A configured URL receives a POST when a
run/sweep/job batch completes; the payload contract is versioned and documented.
**Closes:** rows 18, 19, 20 (20 may slip to 15 without harm — Discord is unconfigured
today).
**Size:** small — about half of phase 4a. One config block, one HTTP call with retry,
event hooks at run boundaries.
**Risks:** the payload contract. n8n today receives whatever Apprise sends and the
downstream flow was built against that; the failure this phase must prevent is a
webhook that fires but carries a shape the flow silently drops. First step: read the
n8n flow's trigger node (or ask the user for it) and design the payload against what it
actually consumes. Second: the event taxonomy (row 19) is easy to over-design — ship
run_end first, grow the rest behind the same dispatcher.
**Testable when shipped:** a sweep against a webhook-catcher fixture produces the
documented payload; the real n8n flow fires in a rehearsal.

#### 5b — Cutover verification sweep

**Goal:** every unknown that could bite at cutover is answered with evidence, and any
trivial exercised gap it exposes is closed in the same PR.
**Closes:** rows 1–5, 8, 13, 14, 15 (the exercised-config unknowns). Rows 6, 7, 9–12,
16 are parity-only unknowns and may be answered here cheaply or deferred to phase 7a.
Row 4 matters most: collection posters are the one *exercised* rendering path whose
parity is unconfirmed.
**Size:** small. Mostly reading code and comparing live output; fixes only where an
answer reveals an exercised gap.
**Risks:** an answer might not be small — if row 14 (multiple versions) reveals the
per-item model misses secondary versions, that becomes its own scoped follow-up, not a
rushed patch here.
**Testable when shipped:** each row's answer is recorded in this document's margin (or
a follow-up gap is filed); collection-poster output is pixel-compared against the
Posterizarr oracle.

**Cutover happens here** — with 5a shipped, 5b green, and the full-pass trigger done:
run adoption `--dry-run`, review, repoint webhooks, stop Posterizarr and Kometa. Every
later phase is post-cutover build-out; nothing below blocks turning the old tools off.

#### 5c — Tracearr webhook intake

**Goal:** Tracearr's outbound custom webhooks become a trigger source, replacing the
role Tautulli's notification agent held. Not cutover-blocking: Radarr/Sonarr webhooks
already cover import-driven triggering; this adds watch-activity-driven triggers.
**Closes:** rows 17, 21.
**Size:** small — one route, payload mapping into the existing normalize path.
**Risks:** payload shapes are unverified until the user's instance is live. First step
is row 17: harvest real webhook payloads (and API responses, banked for 8c) from the
deployed instance, in the project's harvest-the-oracle style. Do not write a schema
before that. Note the relationship without conflating: Tracearr's webhooks may
*eventually* also serve the role the n8n chain does, but 5a is what cutover depends on.
**Testable when shipped:** a captured real payload replayed at `/webhook/tracearr`
enqueues the right item.

### Phase 6 — the spec's own UI debts

Sequenced before the parity build-out because the design spec already owes them (§6),
they are moderate in size, and 6d is a hard dependency of phase 11.

#### 6a — Collections UI, search, compare, override actions

**Goal:** the Collections page shows member counts, last diff result (+3/−1), next
refresh, and a "diff now" button; the library browser gets its search box; item detail
compares all art kinds and can clear a manual override.
**Closes:** rows 22, 23, 24, and the collections half of the §6 remainder (member
counts, diff-now — carried in the spec's §6a "still owed" list).
**Size:** about phase 4c — a few endpoints plus UI on two existing pages.
**Risks:** none structural; the endpoints are additive. The known trap is documented in
§6a already: artwork must go through `apiFetchImage`, never a bare `src`.
**Testable when shipped:** diff-now on a chart collection applies deltas and the page
shows the result without a reload.

#### 6b — WebSocket

**Goal:** the dashboard stops polling; a live log tail view exists.
**Closes:** row 72 and the Posterizarr "live log viewer" gap.
**Size:** small — `client.ts` was written so the swap touches one module.
**Risks:** auth for the socket (the session is a bearer header; sockets don't send
headers from browsers uniformly — ticket-based connect is the likely shape). Name it in
the PR rather than discovering it at review.
**Testable when shipped:** events appear on the dashboard without a poll cycle; a
worker log line reaches the tail view.

#### 6c — Config editor with hot-reload and impact preview

**Goal:** schema-validated config editing in the UI, hot-reload on save, config-version
bump marking affected fingerprints stale, and the impact preview ("this change would
re-render ~2,400 items") with apply-now vs regenerate-later.
**Closes:** rows 94 and 54 (schedule editing rides on the same write path).
**Size:** large — comparable to phase 2. The write endpoint is easy; hot-reload
correctness and the preview are not.
**Risks:** three, plainly: (1) hot-reload while workers hold the old config — needs a
config-generation handoff, not in-place mutation; (2) the impact preview must recompute
fingerprints without side effects — a preview that enqueues renders is a data-loss
class bug; (3) partial validity — reject bad config with line-level errors, never
half-apply (spec §7 already promises this).
**Testable when shipped:** editing a badge colour previews the affected-item count,
applying re-renders exactly those items, and a syntactically invalid save changes
nothing.

#### 6d — Provider-candidate picker and logo browser

**Goal:** for any item and art kind, search all providers, browse candidates, pick one;
the choice persists as a manual override. Logos included (Posterizarr's "Browse
Logos").
**Closes:** row 73.
**Size:** about phase 4c.
**Risks:** provider rate budgets — a browse page that fans out to four providers on
every open needs the cache in front of it (the design's Fanart courtesy note applies).
**Testable when shipped:** picking a candidate re-renders with that base and survives a
re-run without being overwritten.

#### 6e — Manual mode and testing mode

**Goal:** build one styled artifact of any kind from an arbitrary file or URL (API +
UI), and render config sample sheets (short/medium/long text, every kind) on demand.
**Closes:** rows 74, 75.
**Size:** about phase 4c — both reuse the render path end to end.
**Risks:** URL fetching is a server-side request forgery surface — allowlist schemes,
no redirects into private ranges; the artwork-endpoint hardening precedent in §6a sets
the bar.
**Testable when shipped:** a URL becomes a styled collection card; sample sheets change
when config changes.

### Phase 7 — render long tail and artwork modes

#### 7a — Render and selection long tail

**Goal:** close the pile of small parity-only rendering/selection toggles in one
sweep, each guarded by a golden-image or unit test.
**Closes:** rows 6, 7, 9–12, 16 (any deferred from 5b), 38–48, 50; plus 25–37, 49, 51,
52 if capacity allows or as a 7a/7b split by review size.
**Size:** small–medium in aggregate; each item is hours, not days. Ship in two or three
PRs rather than one unreviewable one.
**Risks:** none individually; collectively, config-schema sprawl — group new keys under
existing blocks rather than minting one top-level key per toggle.
**Testable when shipped:** each toggle has a test that renders/selects differently with
it on.

#### 7b — Artwork modes

**Goal:** the bulk artwork operations Posterizarr ships as CLI modes, as jobs
triggerable from the UI: backup, restore (with filters), poster reset, remove-overlays
revert, logo updater + revert. A "run modes" UI section fronts them.
**Closes:** rows 65, 66, 67, 71, 76, 77, and the Posterizarr "Run Modes tab" partial.
**Size:** medium — five operations sharing job-queue plumbing.
**Risks:** these are the destructive ops. Every one needs a dry-run and a confirmation
gate; restore must never race the live pipeline (pause-or-fence decision needed);
poster reset interacts with the §6a orphaned-uploads reality — resetting selects
different art but cannot delete uploads, say so in the UI.
**Testable when shipped:** backup → wipe a test library's art → restore round-trips;
revert restores clean bases; all against the fake Plex server.

### Phase 8 — builder engine and list builders

#### 8a — Builder engine core

**Goal:** the generic abstraction the three hardcoded sources (charts, awards, CS
buckets) never needed: a builder produces an ordered external-ID list; the engine
handles config schema, registry, resolution to owned Plex items, sync/append, limit,
ordering, dry-run, and failure containment (failed source ⇒ no changes — the shipped
invariant, preserved).
**Decomposition (the first monster):**
1. Builder interface + registry + typed per-collection config (capability parity:
   config, not YAML compatibility).
2. Port the three shipped sources onto it with zero behaviour change — golden test:
   nightly diff output identical before/after.
3. Add sync/append (row 25), limit (26), dry-run (27), ordering application (per row
   2's answer).
4. Collection-level settings that ride along: rows 28, 29, 30, 68, 69, 70, 92 (per-library
   overrides where builders need them).
**Closes:** rows 95, 25–30, 68–70; 92 partially.
**Size:** large — phase-3 scale. The port-with-zero-change step is the insurance.
**Risks:** over-abstracting. The interface should be exactly what step 2's three real
sources plus row 58's MDBList need — resist designing for Trakt-shaped sources that are
out of scope.
**Testable when shipped:** the three existing collections run through the engine with
byte-identical diffs; one new trivial builder (`plex_id`) works end to end.

#### 8b — List builders, wave 1

**Goal:** the thin builders: TMDb charts and explicit/simple builders, MDBList lists,
TVDb lists, IMDb list/id/watchlist, Plex trivials, text_file, Arr taglists,
plex_watchlist (pending the row-64 confirmation).
**Closes:** rows 56–64.
**Size:** medium — each builder is small once 8a exists; the volume is the size.
**Risks:** IMDb list/watchlist parsing is scraping-adjacent even on GraphQL — pin with
recorded fixtures so upstream drift fails loudly.
**Testable when shipped:** each builder has a recorded-fixture test; a config-defined
TMDb-list collection appears in Plex and diffs nightly.

#### 8c — TMDb discover, IMDb search/awards, Tracearr builders

**Goal:** the query-shaped builders: `tmdb_discover` (full parameter surface),
`imdb_search`, award events beyond Oscars, person filmography builders, and Tracearr
most-watched/popular from its REST API (per the row-17 harvest).
**Closes:** rows 79–83.
**Size:** medium — discover's parameter matrix is wide but mechanical; award events
need per-event source verification.
**Risks:** award events: IMDb's event data varies per event/year — treat each event as
its own vertical slice with an oracle comparison against Kometa's output for that
event, not one generic "awards" claim. Tracearr: the API is read-only and documented
in-instance; anything its docs don't show is out until observed.
**Testable when shipped:** a discover-defined collection matches the same query run
against TMDb by hand; one non-Oscars award collection matches Kometa's members for the
same event/year.

### Phase 9 — the query language (the second monster)

#### 9a — Filter model, tier 1

**Goal:** a typed predicate model (attribute, operator, value, and/or nesting) usable
as post-builder `filters:` — evaluated client-side against facts and Plex metadata.
Tier 1 = the ~15 attributes the defaults and common configs actually use (genre, year,
resolution, rating values, content rating, language, label, added/release dates,
runtime, studio/network, collection membership).
**Closes:** row 96 (tier 1 of it).
**Size:** medium.
**Risks:** the model is shared with 9b — get the operator/modifier semantics
(`.not/.regex/.gt/.before/…`) right here, with a table-driven test per operator, or 9b
inherits the bugs.
**Testable when shipped:** a filtered builder collection excludes exactly the items the
same filter excludes in Kometa (oracle comparison on one library).

#### 9b — Plex search DSL and sort matrix

**Goal:** `plex_search` as a builder: the full attribute matrix translated to Plex
query parameters, sorts, limits, and/or composition.
**Decomposition:**
1. Attribute inventory: enumerate all ~60 from Kometa's search/sort pages into a typed
   table (attribute → Plex field → type → allowed modifiers → item types). The table is
   the deliverable reviewers check; code generates from it.
2. Translation layer: predicate model (from 9a) → Plex `/search` query params, per
   attribute type (tag, number, date, string, boolean).
3. Sort matrix + limits.
4. Tier the delivery: tier 1 attributes first (shippable alone), long tail in follow-up
   PRs against the same table.
**Closes:** rows 101 and the rest of 96 (the two share the attribute table).
**Size:** **XL — this is multi-week even fully parallelised**; the long tail of
attributes is mechanical but each needs a live-Plex verification because Plex's query
params are underdocumented.
**Risks:** silent wrongness — a mistranslated attribute returns a plausible-but-wrong
item set. Mitigation: every attribute's test compares against a hand-verified Plex
query on the dev server, not against expectations written from docs.
**Testable when shipped:** per-attribute round-trip tests; a `plex_search` collection
reproduces an equivalent hand-built Plex filter exactly.

#### 9c — Native smart collections

**Goal:** `smart_filter` (Plex-maintained smart collections from a translated filter)
and `smart_label`; migrate CS buckets to smart if row 1's answer says Kometa's were.
**Closes:** rows 90, 91 (`plex_collectionless` lands here — it needs the membership map
this phase builds).
**Size:** medium.
**Risks:** smart collections are owned by Plex after creation — the reconciler must
treat them as fire-and-forget definitions, not diff targets; mixing the two models on
one collection would fight Plex nightly.
**Testable when shipped:** a smart collection updates itself when a new item matches,
with no autoposter run in between.

### Phase 10 — dynamic collections (the third monster)

#### 10a — Dynamic collections engine

**Goal:** the generic per-value engine that `buckets.py` is one hardcoded instance of.
**Decomposition:**
1. Value enumeration per type (library scan for genre/year/decade/country/resolution/
   studio/network/edition/languages/origin_country — mostly one Plex query each).
2. Key handling: include/exclude, addons merge (generalising the CS addons logic),
   key_name_override.
3. Titling: title_format, remove_prefix/suffix, other_name bucket.
4. Lifecycle: create per key, sync membership (via 8a), delete-below-minimum,
   test mode.
5. Port CS content-rating buckets onto it with zero behaviour change (same insurance as
   8a step 2).
**Closes:** row 102.
**Size:** large — phase-3 scale; the port step keeps it honest.
**Risks:** unbounded fan-out — `year` on a 70-year library creates 70 collections;
per-type defaults (minimum, include lists) must make the default outcome sane, because
the user configures outcomes, not YAML.
**Testable when shipped:** CS buckets byte-identical through the engine; a `decade`
dynamic config creates the expected set on the dev library.

#### 10b — Dynamic packs and defaults equivalents

**Goal:** shipped config presets reproducing the in-scope Kometa default families:
genre, studio, streaming (TMDB watch providers), resolution, aspect, year, decade,
country, region, continent, franchise (tmdb_collection dynamic), universe/based (via
MDBList-hosted lists), seasonal (date-windowed via row 70), content-rating regionals,
separators, chart packs (basic/tmdb).
**Closes:** rows 93, 49, and the `number`/`custom` dynamic types.
**Size:** medium–large — presets are cheap individually; streaming and franchise need
their data plumbing (watch providers, TMDb collection IDs).
**Risks:** presets are opinions — each pack ships disabled by default with a one-line
enable, so parity is available without foisting 40 collections on the library.
**Testable when shipped:** enabling a pack on the dev library produces collections
matching Kometa's for the same library, sampled per pack.

#### 10c — People

**Goal:** person-driven collections: dynamic `actor/director/writer/producer` types
with appearance thresholds, `tmdb_popular_people`, `tmdb_person` summaries/posters,
birthday/deathday gating.
**Closes:** row 83's dynamic half (builders landed in 8c) and the person dynamic types
of row 102.
**Size:** medium.
**Risks:** person enumeration is the expensive scan (every credit of every item) —
needs caching in `item_facts` or a dedicated table, decided at design time, not
discovered at review.
**Testable when shipped:** an actor pack with threshold N creates exactly the actors
with ≥N appearances.

### Phase 11 — Action Center (the fourth monster)

#### 11a — Asset-quality flags and backfill

**Goal:** the pipeline records *why* each chosen asset is imperfect, at selection time:
language rank achieved vs preferred, provider rank, textless-preference miss,
logo-to-text fallback taken, text truncation at render, still-missing. Plus a backfill
job scoring the existing library.
**Closes:** the tracking half of row 103.
**Size:** medium — columns on `renders` + selection-path bookkeeping + one sweep job.
**Risks:** the taxonomy is the design work; changing it later means re-backfilling.
Keep flags factual ("selected language = en, preferred = xx"), not judgemental
("bad") — the queue decides what's worth reviewing, data stays stable.
**Testable when shipped:** a forced en-only render on an xx-preferring config produces
the language flag; backfill counts are visible via the API.

#### 11b — Action Center review queue and bulk actions

**Goal:** the curation UI: filterable queue of imperfect assets (by flag, library,
kind), per-item resolve/replace (via the 6d picker)/delete, bulk operations, and
dismissal that sticks until the underlying facts change.
**Closes:** the rest of row 103.
**Size:** large — the largest single UI unit in the roadmap; bigger than 4c.
**Risks:** bulk operations against providers must respect the same rate budgets as the
pipeline (one "re-search all 400 flagged items" click is a burst); dismissals need a
fingerprint-style identity so a re-render doesn't resurrect dismissed rows.
**Testable when shipped:** a flagged item replaced through the queue re-renders and
leaves the queue; a dismissed one stays dismissed across a sweep.

### Phase 12 — overlays

#### 12a — Overlay families

**Goal:** the missing default families, each as fact-plumbing + a badge spec: aspect,
language_count (Dual/Multi audio-sub), content-rating regionals (US/UK/DE/AU/NZ),
direct_play, network/studio wordmarks, ribbon (list-membership driven, via 8a),
status (Airing/Returning/Canceled/Ended, TVDb), streaming (TMDB watch providers),
versions.
**Closes:** row 100.
**Size:** large in aggregate; each family is S–M and independently shippable — ship in
family-sized PRs against the pixel-oracle harness that validated the shipped nine.
**Risks:** data sources first: status needs a TVDb status fact, streaming needs
watch-provider facts with region, ribbon needs collection membership as a fact. A
family without its fact plumbed is a badge that renders stale data forever.
**Testable when shipped:** per-family pixel comparison against Kometa oracle output,
same as `kometa-overlays.md` did for the first nine.

#### 12b — Custom overlay mechanics

**Goal:** user-defined overlays: arbitrary images (file/url through 55's management
UI), templated text with the `<<variable>>`/modifier set, configurable positioning and
backdrops (generalising `badges/geometry.py`'s fixed values), the generic queue engine,
general cross-overlay suppression.
**Closes:** rows 97, 50 (if not landed in 7a), 41's overlay half.
**Size:** large — this converts `badges/` from a fixed spec into an engine with the
fixed spec as its first consumer.
**Risks:** regression risk to the shipped nine badges is the whole risk — the port must
be fingerprint-neutral (identical output ⇒ identical fingerprint ⇒ zero re-renders on
deploy). Golden images gate the PR.
**Testable when shipped:** shipped badges byte-identical through the engine; a
user-defined text overlay with a runtime variable renders per the Kometa oracle.

### Phase 13 — playlists

**Goal:** playlist definitions on the builder engine: any single builder feeds a
playlist, order inherited; `libraries` selection; `sync_to_users`/`exclude_users`;
delete + report; a starter preset.
**Closes:** row 98.
**Size:** medium–large — the builder engine does the hard part; per-user sync is the
new ground (server users enumeration, per-user copies).
**Risks:** per-user sync multiplies writes (N users × M playlists) and Plex playlist
APIs are per-account — needs the same batching discipline as `plex_bulk_edit_batch_size`.
**Testable when shipped:** a chart-built playlist appears for two users and updates on
the nightly diff.

### Phase 14 — metadata completion

#### 14a — Metadata operations completion

**Goal:** the remaining mass-op surface in scope: TVDb/IMDb-dataset sources (row 84),
user rating/original title/added-at ops (32, 33), mappers (34), lock/unlock/remove/
reset controls (87, after row 3's verification), collection ops (28 remainder),
metadata backup (86), parental labels (85 — with its feasibility risk stated).
**Closes:** rows 32–34, 84–87.
**Size:** medium.
**Risks:** row 85 may be infeasible without scraping beyond the sanctioned IMDb
surface — timebox a feasibility spike; if it fails, the row moves to Appendix A with
that finding rather than silently disappearing.
**Testable when shipped:** each op has a fake-Plex round-trip test; backup exports and
re-imports a test library's metadata.

#### 14b — Per-item metadata overrides and per-library config

**Goal:** the capability of Kometa's metadata files as autoposter-native config/UI:
per-item (and season/episode) declarative overrides — titles, summaries, ratings,
dates, tags with remove/sync, images, advanced settings — persisted, applied by the
pipeline, and surviving fact refreshes; plus the full per-library override matrix.
**Closes:** rows 99, 92 (remainder).
**Size:** large — a subsystem, though the item-detail page and config editor give it
surfaces to grow from.
**Risks:** precedence is the design problem: manual override > mass op > provider
fact, applied idempotently — the failure to prevent is a nightly op silently undoing a
manual edit. The precedence table is written and reviewed before code.
**Testable when shipped:** a manual title override survives a mass-op sweep and a
fact refresh.

### Phase 15 — operations and observability polish

**Goal:** the remaining operational surface: per-run stats rollups and charts (53),
storage stats + homepage widget endpoints (52), API-key read auth (51), overlay/font
management UI (55), Discord/Apprise formats if deferred from 5a (20), and any 7a
leftovers.
**Closes:** rows 20, 51–53, 55, and stragglers.
**Size:** medium.
**Risks:** none structural.
**Testable when shipped:** a homepage widget renders against the stats endpoint with
an API key.

---

## Appendix A — out of scope

### A.1 — Excluded services, and what re-scoping each would cost

One line each, per the scope decision (new accounts/OAuth are out):

- **Trakt** (charts, lists, userlists, recommendations, boxoffice, sync_to_trakt_list,
  trakt_user_lists dynamic type): OAuth app registration + tokens that expire every 7
  days — a refresh daemon and a re-auth UX before the first builder works.
- **Letterboxd** (lists, discovery, user films/reviews): no public API — scraping with
  breakage risk, plus an account for user-scoped pages.
- **AniDB** (id/relation/tag/popular builders, rating/genre sources): account +
  registered client + notoriously strict rate limits; ban risk on bulk use.
- **AniList** (charts, search, userlist): cheapest of the anime trio — public GraphQL,
  no auth for charts; still a new service with no use in this library.
- **MyAnimeList** (full builder surface): registered API app + OAuth2 localhost flow +
  cached refresh tokens.
- **OMDb** (rating/content-rating sources): free API key, heavily rate-limited tier;
  small integration once keyed.
- **Simkl** (dvd/trending charts): client id + pin flow.
- **Serializd, YamTrack, Floppy** (show/tracked lists): service accounts; thin APIs;
  each is small once an account exists.
- **iCheckMovies, BoxOfficeMojo, StevenLu** (list/chart sources): no accounts — plain
  scrape/JSON, trivial-to-small each — excluded purely as services the user doesn't
  use.
- **mediastinger overlay**: needs mediastinger.com data — a new external dependency
  for one badge.
- **Notifiarr / Gotify / ntfy**: no new accounts for the self-hosted two, but unused
  services; each is one dispatcher target (~hours) on top of row 19 if ever wanted.
- **Tautulli** (intake, builders, chart default): not out-of-scope but *superseded* —
  the user is replacing Tautulli with Tracearr; rows 17/21/79 are the successors.

### A.2 — Superseded by the event-driven design (inventory reasoning carried over)

PowerShell-era mechanics and batch-run machinery the inventories marked as candidates,
adopted here as decided-out with their reasoning:

- **Templates/YAML metaprogramming** (`templates`, `external_templates`, `custom_repo`,
  `git` file sourcing, GitHub token, defaults `template_variables` machinery) — exists
  to make one batch YAML reusable; typed config + code replaces it. The *capabilities*
  the defaults deliver are rows 93/100.
- **Schedule DSL and run machinery** (`run_order`, `run_again(_delay)`,
  `schedule_overlays`, `delete_not_scheduled`, CLI/env run flags, `--run` one-shot,
  watcher-directory triggers, running-file lock, `ForceRunningDeletion`) — batch-run
  scaffolding; the queue, APScheduler, and webhooks replace the mechanism. The two
  real user-facing behaviours inside it — per-collection cadence and date windows —
  are row 70.
- **`reapply_overlays`** — fingerprint gating replaces it. (`remove_overlays` as a
  one-shot revert is real and is row 65.)
- **`mass_poster_update` / `mass_background_update`** — the render pipeline owns
  artwork end to end.
- **Reports** (`save_report`, show_missing/filtered/unmanaged/unconfigured) and CSV
  exports — the DB, REST API, and UI are the replacement surface (row 53 covers the
  per-run rollups worth keeping).
- **Asset lookup machinery** (`asset_folders`, `asset_depth`,
  `dimensional_asset_rename`, `download_url_assets`, `show_missing_*` family) —
  autoposter writes assets rather than hunting for them; only manual-override lookup
  survives (and exists).
- **Cache-builder knobs** (`cache_builders`, `ignore_cache`) — Postgres TTL cache.
- **Self-update machinery** (`AutoUpdatePosterizarr`, `AutoUpdateIM`,
  `magickinstalllocation`), version checks, update banners — image-tag upgrades and
  Flux automation.
- **Platform surface** — Windows/macOS/Unraid support, Tautulli Windows-script mode,
  Gather Logs support zips, `/api/system-info`, Uptime Kuma heartbeat, log rotation
  knobs (`maxLogs`), telemetry — one k8s deployment with Prometheus, `/healthz`, and
  the cluster log pipeline covers all of it.
- **ImageMaid / Overlay Reset / Quickstart companion scripts** — ImageMaid is the
  answer to the §6a orphaned-uploads problem, which is recorded there as a deliberate
  operator decision outside this service; Overlay Reset's capability is row 65;
  Quickstart is an installer.
- **Blueprints, onboarding wizard, Danger Zone (factory reset)** — single known
  deployment; the config file and migrations are the interface.
- **`show_skipped` verbose logging** — different logging model.
- **Kometa integration mount guide** — autoposter replaces Kometa itself.
- **`verify_ssl`, `item_refresh_delay`, `missing_only_released`,
  `only_filter_missing`, `tvdb_language` and sibling knobs** — moot in the
  event-driven design or already covered by typed config defaults.

### A.3 — Non-goals reaffirmed

- **Jellyfin/Emby** (server support, sync modes, `ReplaceThumbwithBackdrop`) —
  disabled in the user's config; declared non-goal, unchanged.
- **Kometa `add_missing` family** (`build_collection: false` feeder mode,
  `radarr_add_all`/`radarr_remove_by_tag` and Sonarr equivalents, quality/monitor
  management) — explicit non-goal in the design; Arr *taglist builders* and
  per-definition overrides (rows 61, 89) are in, library-content management is not.
- **RTL font support** — a fi/en library; inventory reasoning adopted.
- **IMDb poster scraping** (Posterizarr's movie last-resort provider) — inventory
  marked candidate-out; four providers plus manual assets cover the ladder.
- **Music dynamic types** (`mood`/`style`/`album_genre`) — no in-scope music library.
- **`server_preroll`** — inventory's words: out of scope by any reading.
- **Multi-instance `-p` mounts, `DISABLE_UI`, `APP_PORT`** — deployment-shape options
  for platforms this project doesn't target.

---

## Completeness pass

Both inventories were re-walked row by row against this document. Method: every
inventory row whose status was missing, partial, or unknown (and the
candidate-out-of-scope rows) was assigned either to a numbered gap-list row, to a
named verification row, or to Appendix A. Covered rows and not-applicable rows
(different-mechanism equivalents the inventories marked covered) were not carried.
Closely related inventory rows collapse into single gap rows (e.g. eight TMDb chart
builders → row 63); the counts below are of *inventory rows*, not gap rows.

| Source | Gap-ish rows in | Placed in Part 1 (gaps + verification) | Out of scope / superseded (Appendix A) |
|---|---|---|---|
| Posterizarr inventory | 67 | 50 | 17 |
| Kometa inventory | 134 | 93 | 41 |
| **Total** | **201** | **143** | **58** |

The 143 placed inventory rows collapse into the 103 numbered rows of Part 1 (17
verification + 86 implementation). Rows 104, 105 and 106 are not inventory rows: they
were added by phase 5b's verification sweep as the scoped follow-ups to rows 5, 4
and 14. One row changed disposition after the inventories
were written: Tautulli (3 inventory rows) moved from "planned/missing" to superseded
by the user's Tracearr decision, and 2 Tracearr rows moved from account-flagged-out to
in-scope for the same reason; both movements are counted in the table above as placed
(Tracearr) and Appendix A (Tautulli, under A.1's superseded note).
