# Phase 8b: List Builders, Wave 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The thin builders on the 8a engine: TMDb charts + explicit/simple,
MDBList lists, TVDb lists, IMDb list/id/watchlist, Plex trivials, text_file,
Arr all/taglists, plex_watchlist. Closes rows 56–64.

**Architecture:** One plumbing task widens what builders can reach — a
`SourceClients` bundle threaded through `reconcile_libraries` exactly the way
`summaries` already is (never the app `Config`, never raw `Secrets`), the
real `ProviderCache` on `BuilderContext.cache`, and load-time `params_model`
enforcement. Then builder families land by shared client surface, each a
recorded-fixture-tested registry class following the shipped
`imdb_chart`/`plex_id`/`imdb_award_years` shapes. The golden gate stays
byte-identical throughout (no default definitions change).

**Decisions (2026-08-24, from the fact sheet's flags — binding):**
- Row 64 watchlist INCLUDED (user decision): new optional soft secret
  `AUTOPOSTER_PLEX_ACCOUNT_TOKEN` (`plex_account_token`, defaults "") — the
  configured `plex_token` may be server-scoped (health.py:75-79); never
  assume it works for plex.tv. Unset ⇒ the builder raises a clear
  "account token not configured" (contained as a failed source). The
  existing PIN CLI (`plex/auth.py`) is how the operator mints one.
- Builder access = **`SourceClients` bundle**, not raw secrets/config on the
  context: built once per pass where clients are constructable (app/service
  layer), carried as `BuilderContext.sources`. Contains lazily-usable
  handles: tmdb (fetch_json-based), mdblist, tvdb, radarr/sonarr (or None
  when unconfigured/disabled), plex_account (MyPlexAccount factory or
  None), and the owned-index/section accessor for Plex trivials (see Task 1
  interface). A builder needing an absent client raises — contained per
  definition.
- `BuilderContext.cache` = the real `ProviderCache` (None only when TTL 0).
  List fetches go through `fetch_json` where the API is JSON-over-GET
  (TMDb/MDBList/TVDb) so caching + credential-stripping come free. IMDb
  GraphQL stays raw-POST (uncached, as charts.py) — fixture-pinned.
- **`params_model` enforced at config load** in `CollectionDefinition`
  validation (next to the builder-name validator). Load-time-breaking in
  principle (row-140 class) but there are ZERO operator definitions in any
  real config today — this is the last free moment. Builders keep their
  internal validation too (defense in depth).
- **Row 141 fixed in Task 1**: `_expand` results inherit the placeholder
  definition's ride-along fields (labels/label_sync/item_label/sort_title/
  collection_mode/visible_*/hub_priority/limit/sync_mode/tmdb_summary)
  unless the expanded definition sets its own. Red-first against the
  current dropping behavior; imdb_award_years covered.
- text_file = LOCAL ONLY (roadmap row 57 wording wins over the inventory's
  "local/remote"), rooted at `manual_assets_root` (operator-supplied files
  are what that mount is for), double-realpath containment per
  posters.py:101-124 — a `params.path` escaping the root refuses (raised,
  contained). Deploy docs note the mount.
- MDBList budget: `MDBListLimitReached` short-circuits the REMAINING
  MDBList definitions in the pass via a `run_cache` memo (the `_event`
  precedent, imdb_award.py:64-85) — one limit hit must not burn N more
  calls. Each affected definition reports failed; nothing is touched.
- TMDb 429: acceptable as one dead source in this wave (no special
  handling); noted as a follow-up row at wrap if it bites.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds. Isolated compose per task
  (`-p 8bt<N>`), timeout 1800 under contention, **foreground `docker wait`
  only — the blocking IS the wait; never write "standing by"/"awaiting
  notification"**, teardown after.
- Baselines at branch start (main 1976e2f): backend **2075 passed / 0
  skipped** FULL (≈2064 under `-m "not imagemagick"` — verify the actual
  number at Task 1 start and record it), frontend **267**, ruff clean.
- **Golden gate untouched**: no change to default definitions or their
  action strings. Verify green at every task end.
- Builders RAISE on failure (never return empty); containment is the
  engine's. Every id is a namespaced `ExternalId`; order is source order.
- Every network builder: recorded fixture under
  `tests/fixtures/collections/` + MockTransport tests pinning request
  shape (auth header/params, paging) AND failure-raises AND
  order-preservation. The no-outbound-network conftest guard stays.
- Credentials: reuse existing secrets (`tmdb_token`, `tvdb_apikey`,
  `mdblist_apikey`, `radarr/sonarr_apikey`); the ONLY new secret is
  `plex_account_token` (soft, ""). No credential in a cache key, action
  string, log, response, or fixture. Failure logs: exception class names
  only.
- New config keys: none beyond `plex_account_token` and (if needed)
  text_file's root reuse — builders are configured through `definitions:`
  params, not new top-level blocks.
- Mutation proofs for every guard (containment idioms, params rejection,
  the limit short-circuit, text_file escape). Red-first throughout.
- Stage by name; `--no-gpg-sign`; no AI attribution; no new deps
  (plexapi/httpx/pydantic cover everything; MyPlexAccount is already
  imported in health.py).

## Verified facts (fact sheet 2026-08-24 @ 1976e2f; spot-check before relying)

- Recipe: `Builder` protocol (base.py:147-158), `BuilderResult(ids,
  summary, poster_kind, poster_key)` (:55-77), `BuilderContext` (:79-109 —
  `config` IS the definition's params; cache always None TODAY; no
  secrets/app-config/section BY DESIGN — Task 1 changes this deliberately),
  raise-on-failure rule (:10-14), `SmartBuilder` escape hatch (:112-145).
  Registration: `builders/__init__.py:22-33`, dup-refused (base.py:183-195),
  lazy import from the config validator (schema.py:316-331). Params-model
  gap: declared but NEVER read outside `build` — grep base.py:151/223,
  imdb_chart.py:60, imdb_award.py:107/138.
- `_expand`: engine.py:121-132; expansion failure → failed+skipped, nothing
  touched (:260-271); expanding builders MUST expose `TITLE_PATTERN`
  (definition_titles, engine.py:621-651). Worked example
  imdb_award.py:127-164; **:141-152 rebuilds bare definitions = row 141**.
- Engine call path: `_run_one` engine.py:308-391 (containment :327-344 —
  class-name-only rule stated in its comment; limit post-resolution
  :355-359; summary :370; reconcile :374-390). Context built :215-222.
  `summaries` threading precedent: service.py:194-234,
  api/collections_builders.py:179, collections/__main__.py:62-70.
- Clients: MDBList `facts/mdblist.py` (apikey QUERY param :92 — stripped by
  cache `_CREDENTIAL_PARAMS` cache.py:14; `MDBListLimitReached` :12-18,
  :106-107; Null client app.py:414-429). TMDb providers/tmdb.py +
  facts/tmdb_facts.py (Bearer header; fetch_json; NO paging anywhere yet).
  IMDb charts.py (GraphQL POST, `x-imdb-client-name` header mandatory :13-14,
  raw http NOT fetch_json :35-39). TVDb providers/tvdb.py (login dance
  :100-124, 401-retry :126-137, /login never cached :139-145). Arr
  arr/client.py (X-Api-Key header; `listing` :40-52, `ids_in` :54-66;
  ArrKind.id_field RADARR=tmdbId SONARR=tvdbId :26-27; **NO /api/v3/tag
  call exists; `tags` field unread**). Plex: engine holds lazy
  section/owned-index per library (engine.py:203-213);
  `build_owned_index` = one section.all() (resolve.py:57-84);
  episode traversal exists NOWHERE (plex_pilots is net-new Plex surface).
  MyPlexAccount: imported in plex/health.py:6,89; PIN flow plex/auth.py
  (prints token once, never persists). No watchlist() call anywhere.
- fetch_json (providers/fetch.py:17-51): 404→None cached negative, other
  non-2xx RAISE, cache key credential-stripped, returns dict|None —
  paging loops are the caller's.
- Fixtures/tests: tests/fixtures/collections/ + MockTransport idioms
  (test_collection_charts.py:1-80 the model); no_outbound_network
  conftest.py:104-124; registry-wide invariants auto-apply to new builders
  (test_builder_base.py:66-77); engine fakes test_builder_engine.py:37-118;
  ProviderCache tests via session_factory_for (conftest.py:206-216).
- Config: secrets schema.py:23-69 (soft-secret pattern :32-43 to copy for
  plex_account_token); RadarrConfig/SonarrConfig enabled+base_url
  :663-704; title collisions vs built-ins refused at load :485-542;
  example-config gate test.
- Collision watch: rows 135/137/140/142 touch adjacent code — do NOT fix
  them in 8b (filed follow-ups); row 141 IS fixed here (Task 1).

---

## Task 1: SourceClients plumbing, cache wiring, load-time params, row 141

**Files:**
- Modify: `src/autoposter/collections/builders/base.py` (`BuilderContext.
  sources` + cache doc), `collections/engine.py` (context construction +
  `_expand` ride-along inheritance), `collections/service.py` +
  `api/collections_builders.py` + `collections/__main__.py` (thread the
  bundle like summaries), `src/autoposter/app.py` (build the bundle),
  `config/schema.py` (`plex_account_token` soft secret; params_model
  enforcement in CollectionDefinition validation)
- Create: `src/autoposter/collections/builders/sources_bundle.py` (or in
  base.py — implementer's call) — the `SourceClients` dataclass
- Test: `tests/test_builder_base.py`, `test_builder_engine.py`,
  `test_collection_config.py` (params enforcement), award tests (141)

**Interfaces (later tasks rely on these exactly):**
- `SourceClients`: `tmdb: TmdbListClient | None`, `mdblist: MDBListClient |
  None` (None when Null/keyless), `tvdb: TVDBClient | None`, `radarr:
  ArrClient | None`, `sonarr: ArrClient | None` (None when disabled),
  `plex_account: Callable[[], MyPlexAccount] | None` (factory, None when
  plex_account_token unset), `plex: PlexSectionAccess` — a narrow accessor
  exposing `owned_index()` (the engine's lazy index for the CURRENT
  library) and `section()` (the plexapi section) so Plex trivials don't
  re-walk. Bundle built once per pass; engine injects the per-library plex
  accessor when constructing each context.
- `BuilderContext.sources: SourceClients` (required — engine always
  provides one; tests construct a bundle of Nones via a helper).
- `BuilderContext.cache: ProviderCache | None` — the app's real cache.
- `_expand` inheritance: expanded definitions receive the placeholder's
  ride-along fields via `model_copy(update=...)` unless explicitly set by
  the expander. Red-first: award-years placeholder with labels/sort_title
  → expanded units carry them (currently dropped).
- Load-time params: `CollectionDefinition` validator resolves the builder
  from REGISTRY (existing lazy import) and, when the builder exposes
  `params_model`, validates `params` against it — a stray key or bad type
  is a config load error naming definition title + field.

- [ ] Failing tests first for each: bundle threaded to a probe builder;
      cache present; expand inheritance (141); params load-rejection (bad
      key, bad type, missing required); plex accessor returns the shared
      index (call-counted — no second section.all()). Mutation proofs:
      drop the inheritance → 141 test reds; drop params enforcement → the
      load-rejection test reds.
- [ ] Full backend suite + ruff; golden green. Commit.

## Task 2: No-network + Arr builders (rows 56 partial, 57, 59 partial, 61, 62 partial)

**Files:**
- Create: `builders/simple_ids.py` (`imdb_id`, `tmdb_movie`, `tmdb_show`
  explicit-id forms — params `{ids: [...]}` → namespaced ids, the
  PlexIdBuilder shape; `plex_rating_key` = alias of plex_id via a second
  registration name if Kometa parity wants the name, else document),
  `builders/text_file.py`, `builders/arr.py` (`radarr_all`,
  `radarr_taglist`, `sonarr_all`, `sonarr_taglist`)
- Modify: `arr/client.py` (add `tags()` — GET /api/v3/tag — and expose
  `tags` field parsing on listing entries), `builders/__init__.py`
- Test: `tests/test_builder_simple.py`, `test_builder_text_file.py`,
  `test_builder_arr.py`, `test_arr_client.py` (extend)

**Interfaces:**
- text_file params: `{path: str}` relative to `manual_assets_root`;
  double-realpath containment (escape → raise, contained); file format:
  one id per line, `imdb:tt...`/`tmdb:123`/`tvdb:456`/bare `tt...`
  (imdb-inferred), `#` comments, blank lines skipped, unparseable line →
  raise naming the line number (fail loudly, never silently drop).
- arr builders: `radarr_all` = listing→ids_in→("tmdb", …); taglist params
  `{tags: [names]}` — resolve names via `tags()`, unknown tag name →
  raise; filter listing entries whose `tags` intersect. Sonarr → tvdb ids.
  Clients come from `ctx.sources`; absent (disabled instance) → raise.
- [ ] Red-first per builder incl. containment mutation (text_file escape),
      unknown-tag raise, order preservation. Full suite + ruff + golden.
      Commit.

## Task 3: TMDb charts + explicit/simple (rows 62 remainder, 63)

**Files:**
- Create: `src/autoposter/providers/tmdb_lists.py` (the list-surface
  client: charts `/movie|tv/{popular,top_rated,now_playing,upcoming,
  airing_today,on_the_air}`, `/trending/{movie,tv}/{day,week}`,
  `/list/{id}` (v3 list, paged), `/collection/{id}` parts,
  `/discover` by company/network/keyword (or the direct
  `/company|network|keyword/{id}/movies`-style endpoints — implementer
  reads TMDb docs and picks the stable one, pinning the choice in
  fixtures), all through `fetch_json` with Bearer auth + paging helper
  (page loop, `total_pages` capped by a per-call max), `region`/`language`
  params), `builders/tmdb.py` (chart + list + collection + company +
  network + keyword builders)
- Modify: `builders/__init__.py`
- Test: `tests/test_tmdb_lists_client.py`, `test_builder_tmdb.py` +
  fixtures (recorded JSON shapes incl. a 2-page list)

**Interfaces:** chart params `{chart: <name>, region?: str, limit
  handled by definition.limit}`; media type follows library_type; ids →
  ("tmdb", id). Library-type mismatch (a tv chart on a Movie library) →
  raise at build (clear message). Paging: fetch until definition-agnostic
  client cap (e.g. 10 pages) — definition.limit trims post-resolution as
  everywhere.
- [ ] Red-first: paging loop (2-page fixture, order across pages), region
      param pinned, Bearer never in cache key (existing property — assert
      via cache-key test), 404 list → raise-not-empty... (404 through
      fetch_json returns None → builder must RAISE on None, pin it).
      Full suite + ruff + golden. Commit.

## Task 4: MDBList + TVDb list builders (rows 58, 60)

**Files:**
- Modify: `facts/mdblist.py` (list endpoints: `/lists/{user}/{list}/items`
  and `/lists/{id}/items`, sort params, through fetch_json + the apikey
  query param; the limit short-circuit: on `MDBListLimitReached`, memoise
  in `ctx.run_cache` so subsequent MDBList builds this pass raise
  immediately without a request), `providers/tvdb.py` (list endpoints:
  `/lists/{id}/extended` or slug variant; explicit `tvdb_movie`/`tvdb_show`
  id passthrough needs no fetch)
- Create: `builders/mdblist.py` (`mdblist_list`), `builders/tvdb.py`
  (`tvdb_list`, `tvdb_movie`, `tvdb_show`)
- Test: `tests/test_builder_mdblist.py`, `test_builder_tvdb.py` + fixtures
- [ ] Red-first: the limit short-circuit (call-counted transport — second
      MDBList build makes NO request; mutation: drop the memo → red);
      TVDb login-dance reuse (401-retry still works through the new
      endpoints); mixed-media list entries mapped to the right namespaces.
      Full suite + ruff + golden. Commit.

## Task 5: IMDb lists + Plex trivials + watchlist (rows 56/59 remainders, 64)

**Files:**
- Modify: `collections/charts.py` or create `collections/imdb_lists.py`
  (GraphQL queries for public list + public watchlist — same endpoint,
  same mandatory header, same raw-POST-no-cache transport as charts;
  paged via GraphQL cursors; fixture-pinned STRICTLY — the roadmap names
  upstream drift as the phase risk, so malformed/unexpected shapes raise
  loudly with the response shape named, never a silent empty)
- Create: `builders/imdb_lists.py` (`imdb_list`, `imdb_watchlist` —
  public, by list/user id), `builders/plex_trivial.py` (`plex_all` — every
  key in `ctx.sources.plex.owned_index()["plex"]`, order = section order;
  `plex_pilots` — S1E1 of every show via the section: episode traversal is
  NET-NEW Plex surface, pin the plexapi calls in the contract-pin test
  file, one listing pass, no `.refresh()` — the AST guard applies),
  `builders/plex_watchlist.py` (`plex_watchlist` via
  `ctx.sources.plex_account().watchlist()` — plexapi MyPlexAccount;
  map watchlist items to tmdb/tvdb/imdb ids from their guids; token
  unset → raise "account token not configured"; pin the plexapi
  watchlist contract offline)
- Modify: `builders/__init__.py`, `config/schema.py` docs if needed
- Test: `tests/test_builder_imdb_lists.py`, `test_builder_plex_trivial.py`,
  `test_builder_plex_watchlist.py`, contract pins + fixtures
- [ ] Red-first per builder; plex_pilots respects show ordering and skips
      shows with no S1E1 (counted via raise? no — a show without S1E1 is
      SKIPPED silently-with-log? Decide: skip-and-continue, it's not an
      error; state in docstring); watchlist guid-mapping covers items
      with no usable guid (unresolved downstream, not a crash). Full
      suite + ruff + golden. Commit.

## Task 6: Roadmap, whole-branch review, PRs

- Rows 56–64 `answered 8b:` with delivery notes; 8b phase entry delivered;
  follow-up rows for anything discovered (TMDb 429 handling if warranted;
  the deploy-mount note for text_file).
- Deploy notes gathered for the PR: `AUTOPOSTER_PLEX_ACCOUNT_TOKEN`
  provisioning via the PIN CLI; manualassets mount hosts text files.
- Whole-branch review (most capable model): the containment idioms
  (text_file realpath, credential discipline across five new client
  surfaces, the limit short-circuit) and the params load-enforcement are
  the load-bearing targets. One batched fix round.
- Rebase/merge main if moved; both suites; ship as ONE PR unless the diff
  demands two (if stacked: the retarget-before-merge instruction in every
  description). No AI attribution.
