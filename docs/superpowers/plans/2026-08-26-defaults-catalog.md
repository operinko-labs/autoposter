# Defaults Catalog + Picker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A browsable catalog of preset collection definitions (Kometa's
defaults equivalent) in nine categories, fronted by a tabbed picker UI
writing through the live config-overrides API. All 15 IMDb award events
supported (rows 149 + 150 as prework). Everything opt-in; an untouched
config produces byte-for-byte today's collections. Closes rows 149, 150,
153; delivers row 93/10b's catalog half early; row 82's "which events"
question resolved as ALL (user decision).

**Architecture (option (b), from the fact sheet — binding):** presets are
KEYS, not expanded definitions. A pure data module
`src/autoposter/collections/catalog.py` holds the preset table;
`CollectionsConfig.presets: list[str]` (live, override-writable) selects;
`sources.default_definitions` expands server-side as a third term after
the existing two. Because preset titles flow through `default_definitions`,
the existing `_titles_must_not_collide` validator covers them for free,
and the golden gate is structurally inviolate (empty presets = no-op
expansion). A Plex-free `GET /api/collections/catalog` serves the table +
readiness to the UI. The picker is a tabbed panel on the Collections page
writing `collections.presets` via the same GET-config → withPath →
PUT-overrides flow Settings uses.

**Adjudications (2026-08-26, recorded in ledger — binding):**
- Row 150: per-event `library_types` on `AwardEvent` (Oscars→Movie,
  Emmys→Show, BAFTA→both once 149 lands) + `require_library_type` in both
  award builders — strictly more expressive than Kometa's statics-only
  gating; no doc-only excuse.
- `charts`/`awards`/`separators` booleans SURVIVE; the catalog renders
  them as entries that read/write those booleans; `presets` covers only
  new material.
- Gated presets: shown in the UI with "needs row N" badges; a gated key
  in config REFUSES AT LOAD naming the row (the 9a deferred-attribute
  precedent, schema.py:479-521 style).
- Oracle bar for the 13 new ceremonies: FULL oracle (the 8c procedure)
  for BAFTA (first award_filter exerciser), Emmys (first Show-gated), and
  ONE festival (Cannes — the shape differs from academy ceremonies);
  every other ceremony gets the row-153 category-coverage assertion (a
  category tuple matching ZERO entries across all fetched years = red)
  + poster-segment existence verification + year-pattern pairwise
  isolation (the existing invariant test extends automatically).
- Row 153 IN-SCOPE (the coverage assertion is the reduced bar's
  backbone). Row 151 DEFERRED (the 398KB validation fetch is once per
  pass regardless of event count; 15 small event files per pass are
  acceptable — noted at wrap).

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds (full repo-relative
  paths; the behavior-class container rule; isolated compose `-p dc<N>` +
  isolated-db.yml; timeout 1800 under contention; teardown).
- Baseline at branch start (main 082e107): verify + record at Task 1
  start (full suite incl. imagemagick + the not-imagemagick number).
- **The three invariants, handed to every task:**
  1. `tests/test_builder_port_golden.py` + its fixture — BYTE-IDENTICAL,
     fixture untouched, with default config (empty presets).
  2. `src/autoposter/collections/sources.py:74-76` — definition ORDER is
     load-bearing; preset expansion APPENDS after the existing two terms.
  3. `src/autoposter/config/schema.py:630-687` — every built-in title
     flows through `default_definitions` so the collision validator sees
     it; nothing bypasses that seam.
- **Expansion purity**: `_titles_must_not_collide` runs
  `default_definitions` for every library type on EVERY config write
  (schema.py:651-666) — the preset expansion and the catalog table are
  pure and I/O-free. No fetches, no Plex, no dataset reads. (Award-years
  expansion stays run-time in the builder, as today.)
- Never invent award strings/posters: transcribe from Kometa's defaults
  or omit (the 8c posture); poster segments verified to exist in
  Default-Images or None.
- Mutation proofs for every guard; red-first; copy+cmp restores; stage
  by name; `--no-gpg-sign`; no AI attribution; no new deps.

## Verified facts (fact sheet 2026-08-26 @ 082e107; spot-check before relying)

- `AwardEvent` dataclass `src/autoposter/collections/builders/
  imdb_award.py:68-94`; `EVENTS` :100-155 (oscars, golden_globes; each
  best_picture + best_director); `ImdbAwardParams` event validation
  :216-232; per-ceremony `ImdbAwardYearsBuilder` registrations
  `src/autoposter/collections/builders/__init__.py:111-117` (the
  TITLE_PATTERN-per-registry-entry reason :112-115, engine.py:825-848).
- A new event row needs SIX things (fact sheet §1): EVENTS row (incl.
  year_pattern pairwise-isolated), category vocabularies transcribed
  from Kometa's `defaults/award/<file>.yml` category_filter (precedent
  `src/autoposter/collections/awards.py:40-56`), an
  `posters.AWARD_SEGMENTS` entry (`src/autoposter/collections/
  posters.py:48`; missing = no poster by design), a registration line,
  presence in event_validation.yml (awards.py:74-105), verification
  tests per the adjudicated bar.
- Row 149 mechanics: Kometa filters at TWO levels (award group + category
  — the transcription at `.superpowers/sdd/archive/p8c-task-4-report.md:
  70-81`); ours discards the group key (`awards.py:147-157`, :160-166 —
  the only two consumers, pure functions); fix = optional award_filter on
  the event/award rows + group-aware winners_for_*; dedupe/order contract
  (awards.py:141-144,152) must not move.
- Row 150 mechanics: no require_library_type anywhere in imdb_award.py;
  the config-level gate is only sources.py:50; the media_types idiom to
  copy: `src/autoposter/collections/builders/tmdb.py:296-310`.
- The 13 remaining events: bafta, berlinale, cannes, cesar, choice,
  emmy, nfr, pca, razzie, sag, spirit, sundance, tiff, venice
  (+ the award separator as a cosmetic entry).
- Defaults seam: `default_definitions` sources.py:71-80; the
  Oscars-only-deliberately comment :51-54 (the posture the catalog
  preserves — presets are opinions, opt-in); `service.library_definitions`
  service.py:128-134 appends operator definitions.
- Overrides write path: GET /api/config (routes.py:1254-1277, editor
  affordances :1273-1276); OverridesBody whole-document :1280-1290; PUT
  :1457-1472 (save+swap); preview :1475-1502 (collections.* edits report
  impact:null by design — the picker states that, not treats it as a
  bug); validation order :1359-1405 (unknown-key walk BEFORE pydantic);
  audit row versions-only :1408-1454; `collections` fully live
  (config/live.py:30-97, schema.py:613-614).
- 8a-T6 findings the picker answers: generic editor list-of-object =
  read-only page-wide (`.superpowers/sdd/archive/p8a-task-6-report.md:
  126-183`); no Plex-free definitions listing exists (:96-110) — the
  catalog endpoint IS that endpoint for presets.
- Frontend: Collections.tsx structure (DefinitionsPanel :267-386, insert
  the catalog panel at :585); Settings' write flow to copy
  (Settings.tsx:99-197 document helpers, :718-741 the three calls sharing
  one pendingDocument); client.ts ApiError-with-detail :53-67;
  styles.test.ts conventions (MOBILE 640px :126, .table-scroll etc.).
  **NO tab component exists anywhere in the app** — the picker is the
  first: accessible tab semantics (role=tablist/tab/tabpanel,
  aria-selected, arrow-key navigation), catalog.css with a 640px block
  (tab strip wraps or collapses), new styles.test.ts assertions.
- Ready-now preset backing per category (fact sheet §2/§6): Charts =
  imdb_chart 5 keys + tmdb_chart 8 keys; People = tmdb_actor/director/
  writer/producer id packs; Production = tmdb_company/tmdb_network
  (Show-only)/tmdb_discover watch-providers; Content = tmdb_discover
  with_genres + tmdb_keyword + tmdb_collection franchise packs (fixed id
  lists — LIBRARY-derived genre is row 155/10a, gated); Media =
  resolution via plex_all+filters (9a); Content Ratings = cs_bucket
  (shipped) — regionals gated 10a; Location/Time = fixed-value presets
  possible but the real Kometa packs gated 10a (badges); seasonal gated
  row 70; aspect NO SOURCE; languages row 155.
- Kometa provenance strings per preset from
  `.superpowers/sdd/parity-kometa-inventory.md:140-152`.

---

## Task 1: Rows 149 + 150 — award_filter and library gating

**Files:**
- Modify: `src/autoposter/collections/awards.py` (group-aware
  `winners_for_categories`/`winners_for_year` — award-group key exposed
  as an optional filter; dedupe/order contract unmoved),
  `src/autoposter/collections/builders/imdb_award.py` (`AwardEvent`
  gains `award_filter: tuple[str,...] | None = None` (per-award where
  the design demands — argue the placement against BAFTA's per-collection
  film/TV split) and `library_types: tuple[str,...] = ("Movie",)`;
  both builders call `require_library_type` with the event's types)
- Test: `tests/test_collection_awards.py` (extend; existing tests green
  UNMODIFIED — the golden + oracle pins must not move)
- [ ] Red-first: group-filtered winners (a fixture with two award groups
      → only the filtered group's winners); Oscars/GG behavior
      byte-identical with the new fields defaulted (oracle pins prove
      it); Show-library award definition refused for Movie-only events
      (mutation: drop the gate → red); Emmys-shaped event allowed on
      Show. Full suite + ruff + GOLDEN. Commit.

## Task 2: The thirteen ceremonies

**Files:**
- Modify: `src/autoposter/collections/builders/imdb_award.py` (13 EVENTS
  rows — categories transcribed from Kometa's defaults/award/*.yml;
  year_title/year_pattern per ceremony, pairwise-isolated),
  `src/autoposter/collections/posters.py` (AWARD_SEGMENTS entries —
  verified against Kometa-Team/Default-Images; missing = None + comment),
  `src/autoposter/collections/builders/__init__.py` (13 registrations)
- Test: `tests/test_collection_awards.py` + fixtures: real dataset cuts
  for the THREE full-oracle ceremonies (BAFTA — exercises award_filter;
  Emmys — exercises Show gating; Cannes — festival shape); the row-153
  COVERAGE ASSERTION for ALL 15 events (each award's category tuple
  matches ≥1 entry in its event's fetched dataset — recorded fixtures
  per event OR a single parametrized live-shaped test against committed
  per-event fixture cuts; argue the fixture strategy: 15 full dataset
  fixtures is heavy — a trimmed per-event cut carrying 2-3 years
  suffices for coverage, state the trim rule)
- The three oracles follow the 8c procedure EXACTLY (standalone Kometa
  transcription, nothing imported, exact match including order,
  provenance documented). If Kometa has no default for a chosen
  ceremony, substitute per the 8c fallback rule and say so.
- [ ] Red-first; the pairwise year-pattern invariant test must pass with
      15 events UNMODIFIED (it derives from EVENTS). Mutation proofs:
      a coverage assertion with a typo'd category → red; a cross-matching
      year pattern → red. Full suite + ruff + GOLDEN (defaults still
      Oscars-only — the new events are NOT in default_definitions).
      Commit. (Splittable into two commits by ceremony group if the diff
      demands.)

## Task 3: The catalog module, presets config, and expansion

**Files:**
- Create: `src/autoposter/collections/catalog.py` — `Preset` frozen
  dataclass (key, category ∈ the nine, title(s)/definition-producing
  fields, builder, params, library_types, readiness (READY |
  GATED(row)), description, kometa_source) + `CATALOG` table + pure
  `preset_definitions(config, library_type) -> list[CollectionDefinition]`
  + `catalog_listing()` (the API's data). Table discipline: derived
  views where a registry exists (the CHART_COLLECTIONS precedent), count
  checksums, a note per non-obvious row.
- Modify: `src/autoposter/config/schema.py` (`CollectionsConfig.presets:
  list[str] = []` — validated at load: unknown key refused naming the
  catalog; GATED key refused naming the gating row; duplicate keys
  refused), `src/autoposter/collections/sources.py`
  (`default_definitions` gains `*preset_definitions(...)` as the THIRD
  term), `src/autoposter/api/collections_builders.py` (or a sibling)
  `GET /api/collections/catalog` — Plex-free: categories, presets,
  readiness badges, active state (from config), descriptions
- Population: the AWARDS category = the 15 events (active = the awards
  bool for Oscars compat + preset keys for the rest — reconcile with the
  adjudication: the booleans survive; award presets beyond Oscars are
  `presets` keys); CHARTS = the 13 chart keys as entries reading/writing
  the charts bool where 1:1 and preset keys otherwise; ready-now entries
  for People/Production/Content/Media-resolution per the facts; GATED
  rows for Location/Time/regional-ratings/languages/aspect with their
  gating row numbers.
- [ ] Red-first: expansion purity (a test importing catalog.py asserts
      no network/fs modules touched — the ast-import structural-test
      precedent); empty presets = default_definitions UNCHANGED
      (byte-compare against the pre-change function on fixture config —
      and the GOLDEN); a preset key produces its definitions (order:
      appended); collision validator sees preset titles (an operator
      definition colliding with an ACTIVE preset title → the existing
      422; inactive → allowed); gated/unknown/duplicate key load
      refusals. Mutation proofs: expansion reordered before the existing
      terms → red; the gated-key refusal dropped → red. Full suite +
      ruff + GOLDEN. Commit.

## Task 4: The tabbed picker UI

**Files:**
- Create: `frontend/src/pages/CatalogPanel.tsx` (or colocated in
  Collections.tsx if under ~200 lines — implementer's call),
  `frontend/src/pages/catalog.css`
- Modify: `frontend/src/pages/Collections.tsx` (insert at :585),
  `frontend/src/api/types.ts` + client usage, `frontend/src/styles.test.ts`
  (new assertions), `frontend/src/pages/Collections.test.tsx`
- Design: the app's FIRST tab component — accessible semantics
  (role=tablist/tab/tabpanel, aria-selected, arrow-key nav, focus
  management); tab per category; checkbox rows with name + description +
  Kometa-provenance hint + "needs row N" badge (disabled checkbox) on
  gated entries; an Active summary strip; Save writes
  `collections.presets` (and the compat booleans where an entry maps to
  one) via GET /api/config → withPath → PUT /api/config/overrides (the
  Settings.tsx:718-741 flow — one pending document, adopt-on-save);
  save surfaces the returned versions + a "applies next reconcile" note
  (collections.* previews report impact:null by design — say that, don't
  imply breakage); 422s render per-field per the ApiError detail idiom.
  Mobile: 640px block (tab strip wraps); conventions per styles.test.ts.
- [ ] Red-first: tabs render all nine categories; checking a preset +
      Save PUTs the right document (captured body asserted — the
      mutation: drop a checked key from the document → red); gated
      checkbox disabled with badge; booleans round-trip; 422 rendering;
      keyboard nav (arrow keys move tabs — testable via fireEvent);
      mobile pins. Frontend suite + build. Commit.

## Task 5: Ready-now presets for the eight other categories

**Files:** `src/autoposter/collections/catalog.py` (populate), fixtures/
tests per preset family (`tests/test_collection_catalog.py`)
- The first population, each entry carrying its Kometa provenance:
  Charts (imdb 5 + tmdb 8), People (curated id packs — director/actor
  sets transcribed from Kometa's defaults where they name people;
  otherwise a small honest starter set marked non-Kometa), Production
  (studio/network/streaming packs), Content (TMDb genre + keyword +
  franchise collection packs), Media (resolution presets via
  plex_all+filters), Content Ratings (the CS entry reading the existing
  smart family). GATED rows present-but-disabled for everything 10a/10c/
  155/70-gated, each citing its row.
- [ ] Red-first: every READY preset's expansion produces load-valid
      definitions (parametrized: expand + CollectionDefinition-validate
      each); every GATED row cites a real roadmap row (test greps the
      roadmap); catalog checksums. Full suite + ruff + GOLDEN. Commit.

## Task 6: Wrap

- Roadmap: rows 149, 150, 153 answered; row 82's which-events note
  resolved (ALL, user decision, picker-gated); row 93/10b annotated
  (catalog half delivered early, packs-content half remains); row 151
  deferred-note updated (15 event files/pass observation); new rows for
  anything discovered. The hostname scrub queued from the Tracearr
  capture (`docs/superpowers/plans/2026-08-20-phase1-posterizarr-parity.md:481`)
  — one line, do it here.
- Whole-branch review (most capable model): the catalog table's honesty
  (readiness cells), the expansion purity, the collision-seam invariant,
  the three oracles' genuineness, the first-tab-component accessibility.
  One batched fix round. Both suites. PR to MAIN (no stack). No AI
  attribution.
