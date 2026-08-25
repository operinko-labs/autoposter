# Phase 9a: Filter Model, Tier 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A typed predicate model (attribute, operator, value, and/or
nesting) usable as post-builder `filters:` on any list definition —
evaluated client-side against resolved Plex items. Tier 1 = the roadmap's
~15 common attributes. Closes row 96 tier 1. This is the shared foundation
9b's search DSL translates, so operator semantics carry table-driven tests
per operator (the roadmap's named risk).

**Architecture:** Four layers. (1) THE ATTRIBUTE TABLE (the reviewable
deliverable, 8c's matrix pattern): attribute → type → allowed operators →
item kinds → data source (listing-resident / include-verified / lazy /
absent). (2) The predicate model: pure, no Plex — parse/validate
`filters:` into typed predicates with and/or nesting. (3) The value-
extraction layer: per-family accessors over resolved plexapi items with an
ENFORCED reload budget (call-counted). (4) The engine seam: one filter
stage between resolution and limit, a `filtered` count on the result, and
load-time config validation matching the params discipline. Proven by a
Kometa oracle on one library.

**Controller adjudications (2026-08-25 overnight; recorded in ledger):**
- Attribute table TRANSCRIBED from Kometa's files/filters documentation
  (the 8c T0 pattern: table is the deliverable, code generates from it);
  fidelity backed by the oracle test, and by the 9b joint-ownership note.
- **Scan budget = probe-then-decide**: Task 3 begins with a READ-ONLY live
  probe against the production Plex server (the cluster-exec pattern used
  all through this project) determining which `include*`/param variants
  enrich `/library/sections/N/all` with the tier-1 child elements (labels,
  collections, genres, media). Attributes the enriched listing covers ship
  scan-free; attributes still lazy after the probe DROP to tier 2 (a filed
  row) rather than shipping a silent N+1 — with ONE documented exception
  path: an explicit per-definition opt-in cap may be proposed at review if
  the probe leaves a roadmap-named tier-1 attribute stranded. No silent
  reload-per-item, ever; call-count tests enforce.
- item_facts is NOT a tier-1 filter source: Plex-only semantics under
  Kometa's names (facts-backed content_rating is Common Sense, not Plex
  certification — same-name-different-filter is a parity bug). Facts-backed
  attributes arrive later under DISTINCT names (filed row).
- `filters:` on a smart definition REFUSED at load (the
  `_membership_knobs_need_a_membership` class — schema.py:399-433).
- `filters` ADDED to `_INHERITED_BY_EXPANSION` (engine.py:134-147) with the
  argued comment — it is membership semantics like limit/sync_mode.
- NO default definition gains a filter (golden stays byte-identical).
- Operator vocabulary chosen to map cleanly onto plexapi's OPERATORS table
  (plexapi/base.py:22-40) so 9b's translation is a mapping, not a
  reinvention: `.not`, `.gt/.gte/.lt/.lte`, `.before/.after` (dates),
  `.regex`, contains-default for strings/tags per Kometa semantics — the
  exact tier-1 operator set is fixed by the T1 table per attribute type.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds — full repo-relative
  paths; isolated compose per task (`-p 9at<N>`); timeout 1800 under
  contention; **foreground `docker wait` only — the blocking IS the wait;
  never write "standing by"**; teardown after.
- Branch `feat/phase9a-filter-model` off feat/phase8c-query-builders
  (6ed88b1 + this plan). STACK DEPTH 3: PRs merge #69 → retarget #70 →
  merge → retarget 9a's PR → merge. Every stacked PR description carries
  the retarget instruction.
- Baseline at branch start: **2560 not-imagemagick / 2571 full, 0 skips**
  (verify + record at Task 1 start).
- Golden gate byte-identical throughout; Oscars/defaults untouched.
- The filter stage NEVER makes a Plex request per item without the probe
  verdict authorizing that family — call-count tests on every accessor.
- Filter evaluation failures follow engine containment: a predicate that
  cannot evaluate (missing attribute on an item) has DEFINED per-table
  semantics (missing = excluded, per Kometa) — never an exception that
  kills the definition; but a CONFIG-invalid filter (unknown attribute,
  wrong operator for the type, malformed value) refuses at LOAD naming
  definition title + field (the params discipline, schema.py:328-397).
- Mutation proofs per operator family + the seam ordering (filter before
  limit) + the reload budget; red-first; copy+cmp restores.
- Stage by name; `--no-gpg-sign`; no AI attribution; no new deps.

## Verified facts (9a fact sheet 2026-08-25 @ 6ed88b1; spot-check before relying)

- Roadmap: 9a goal/tier-1 list roadmap.md:538-551; 9b owns translation +
  the rest of the ~60 attrs (:553-574); 9c owns smart (:576-587). Row 96
  (:198) is the target, dep row 95 delivered.
- NO filter attribute table exists in the repo (inventory:134 names the
  count and modifiers only) — Task 1 transcribes it.
- The engine seam: `src/autoposter/collections/engine.py::_run_one` —
  build :409/:425 → resolve :427 → items :435 → **[seam]** → limit
  :436-440 (deliberately post-resolution; filter must precede it) →
  skipped :442 → preview counts :443-449 → reconcile :455-471.
  `DefinitionResult` (:64-88) gains `filtered: int`. `session` is in scope
  (:390); the section closure/lazy index at :275-282; run_cache :268.
- Resolved items are LIVE plexapi objects (resolve.py:57-84, :87-115).
- The reload trap: plexapi `PlexPartialObject.__getattribute__`
  (base.py:650-668) — any None/[] attribute on a partial object = one GET;
  listing items are always partial (base.py:692-702); pinned in
  `tests/test_plexapi_list_items_contract.py:70-84`. Existing deliberate
  payers: reconcile.py:70-83 (labels, per-collection), badges/values.py:
  111-125 (media, render path).
- Listing-resident (Movie._loadData): audienceRating, contentRating,
  duration, originallyAvailableAt, rating, studio, year, addedAt,
  updatedAt, title (+Video base). Child-element cached properties (present
  IFF the listing XML carries them — THE PROBE DECIDES): genres, labels,
  collections, countries, media, guids (guids pinned present via
  includeGuids).
- DB traps: media_items/item_facts coverage is render-history-sparse
  (pipeline.py:272-319 is the only writer); ItemFacts.content_rating is
  Common Sense (gather.py:98-116) NOT Plex certification — the reason for
  the Plex-only adjudication.
- Config discipline to mirror: schema.py:328-343 (load-time builder
  check), :345-397 (params model, names title+key), :399-433 (smart
  refusals — the class `filters:` joins), extra="forbid" everywhere.
  Expansion inheritance list + comment block: engine.py:134-147.
- plexapi's client-side OPERATORS table: plexapi/base.py:22-40; its search
  validation (library.py:1259-1292) is 9b's raw material — the operator
  mapping argument.
- Stale inventory line to fix at wrap: parity-kometa-inventory.md:328
  still lists CS-buckets-smart as unknown; answered in 5b (roadmap:97).
- Oracle precedent: the 8c award oracle (Kometa logic transcribed
  standalone, driven by Kometa's own config; exact-match member lists
  pinned as data).

---

## Task 1: The tier-1 attribute table + the predicate model

**Files:**
- Create: `src/autoposter/collections/filters.py` (THE TABLE + the typed
  predicate model + the config-facing parser/validator)
- Test: `tests/test_collection_filters.py`

**Interfaces (later tasks rely on these exactly):**
- THE TABLE: `FILTER_ATTRIBUTES` — rows: Kometa attribute name → value
  type (str/tag/int/float/date/duration) → allowed operators → item kinds
  (movie/show/both) → source tier (`listing` / `probe` [pending Task 3's
  verdict] / `tier2-deferred`) → per-row note for every non-obvious cell.
  Tier-1 rows: genre, year, resolution, audience_rating, critic_rating,
  user_rating?, content_rating, audio_language, subtitle_language, label,
  added, release (originally_available), duration (runtime), studio,
  network (show), collection. Transcribed from Kometa's documented
  semantics — including its missing-value rule (item lacking the attribute
  = excluded) and modifier meanings; every transcription judgment gets a
  note.
- The predicate model: `FilterPredicate` (attribute, operator, value —
  typed per the table), `FilterGroup` (all_of/any_of nesting, arbitrary
  depth), parsed from the Kometa-shaped YAML mapping (`genre: Horror`,
  `year.gte: 2000`, `label.not: skip`, nested `any:`/`all:` lists — pin
  the accepted YAML shapes in tests; unknown attribute / wrong operator
  for the type / unparseable value = ValueError naming the field, for
  Task 4's load-time hookup).
- `evaluate(group, view) -> bool` where `view` is Task 3's item-view
  protocol (a mapping-like accessor; Task 1 tests use plain dict fakes) —
  the model NEVER touches plexapi.
- Table-driven operator tests: one parametrized case-set PER OPERATOR per
  value type (eq/not/gt/gte/lt/lte/before/after/regex/contains…), incl.
  the missing-value rule and date/duration parsing edges.
- [ ] Red-first: the operator table; nesting (any-of-all-of); the
      refusal messages. Mutation proofs: flip one operator's comparison →
      exactly its cases red; drop the missing-value rule → red.
      Full suite + ruff + golden. Commit.

## Task 2: The probe + the value-extraction layer

**Files:**
- Create: `src/autoposter/collections/filter_values.py` (the item-view:
  per-family accessors over a resolved plexapi item), the probe REPORT
  section (in the task report, not code)
- Test: `tests/test_collection_filter_values.py` (+ a plexapi contract
  pin for any newly-relied listing behavior)

**Step one — THE PROBE (read-only, production cluster):** via the
established kubectl-exec python pattern, against the real Plex server:
fetch `/library/sections/{movies,shows}/all` with candidate enrichments
(plexapi `section.all()` kwargs / raw params: includeGuids is known;
probe what carries Genre/Label/Collection/Media child elements — e.g.
`includeMeta`, `checkFiles`, raw `X-Plex-Container` params, or whether
they're present by default in this server version). Record: for EACH
tier-1 child-element family, present-in-listing yes/no, and the listing's
time cost with enrichments vs without (the resolve.py:1-8 2.8s budget is
the yardstick — an enrichment that triples it needs saying). The probe
script + raw findings go in the task report VERBATIM. The table's
`probe` cells resolve to `listing` or `tier2-deferred` from this data.
- The accessors: one per family, total-function over the item (missing →
  the table's missing semantics, never a reload — enforced: the accessor
  reads via `_loadData`-safe paths / cached properties ONLY when the probe
  verdict says present; a `tier2-deferred` family gets NO accessor).
  Resolution/language accessors ship ONLY if the probe puts Media/streams
  in the listing (unlikely for streams — expect these to defer; the
  roadmap's tier-1 naming yields to the no-silent-N+1 rule per the
  adjudication).
- Call-count tests: evaluating every shipped accessor over a FakeItem
  library of 100 makes ZERO Plex requests (the plex_trivial.py:70-73
  pattern); a partial-object fake with tripwire `__getattribute__` proves
  no lazy-load path is reachable.
- [ ] Red-first per accessor family; the tripwire test. Mutation proof:
      route one accessor through a lazy attribute → tripwire red.
      Full suite + ruff + golden. Commit. If the probe leaves a
      roadmap-named attribute stranded, the report SAYS SO with the data
      and the filed-row recommendation — do not silently ship less.

## Task 3: Engine seam + config surface

**Files:**
- Modify: `src/autoposter/collections/engine.py` (the stage between :435
  and :436; `DefinitionResult.filtered`; `_INHERITED_BY_EXPANSION` +
  comment), `src/autoposter/config/schema.py` (`filters:` on
  CollectionDefinition — load-time validation via Task 1's parser, error
  names title+field; the smart-definition refusal joining :399-433),
  `src/autoposter/collections/service.py`/`api/collections_builders.py`
  if the preview response schema names the new count
- Test: `tests/test_builder_engine.py` + `tests/test_collection_config.py`
  (extend)
- [ ] Red-first: filter stage runs after resolve, BEFORE limit (ordering
      mutation → red); `filtered` counted + in preview; skipped/preview
      reflect the filtered set; smart+filters refused at load; expansion
      inherits filters (the award-family test); config refusals name
      title+field; a filter evaluation over items never raises out of the
      stage (containment test). Golden byte-identical (no default has
      filters). Full suite + ruff. Commit.

## Task 4: The oracle + wrap

- **The oracle** (roadmap :550-551): one library, one real filter config,
  our evaluation vs Kometa's own filter logic transcribed standalone (the
  8c award-oracle procedure: Kometa's code as the oracle script, nothing
  imported from this repo; the member lists pinned as data; document the
  derivation). Choose a filter exercising ≥3 attribute families + a .not
  + a range operator.
- Roadmap: row 96 `answered 9a (tier 1):` naming shipped attributes AND
  deferred-to-tier-2 ones with the probe data cited; the 9a phase entry;
  new rows: tier-2 stranded attributes (if any), facts-backed distinct-name
  filters, and anything discovered; fix inventory:328 (CS-smart stale
  line); row 119 tally if new instances.
- Whole-branch review (most capable model): the table's transcription
  fidelity, the operator semantics vs Kometa's, the reload-budget
  enforcement, the seam's count honesty. One batched fix round.
- Both suites; PR stacked on feat/phase8c-query-builders with the
  retarget-chain instruction (#69 → #70 → this). No AI attribution.
