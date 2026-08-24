# Phase 8c: Query-Shaped Builders — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The query-shaped builders on the 8b plumbing: `tmdb_discover`
(row 80), the five TMDb person filmography builders (row 83's static half),
`imdb_search` minimal-viable tier (row 81), and award events generalized
beyond Oscars with one ceremony proven end-to-end (row 82). Row 79
(Tracearr) is DEFERRED — blocked by open row 17 (live-deploy harvest).

**Architecture:** Everything rides existing surfaces. `tmdb_discover` is a
params-model problem — the client's `discover()` already passes arbitrary
filter maps; the deliverable is a reviewable attribute matrix the model
generates from. Person builders add one not-paged credits method and
collapse five Kometa names onto one build path + a job-filter table.
`imdb_search` extends the 8b GraphQL module (generalized over root/edge
path) after a live schema walk. Awards parameterize `awards.py` on event
id with a per-event registry; one non-Oscars ceremony ships proven against
a Kometa oracle; no new defaults.

**Controller adjudications (2026-08-25 overnight — recorded in the ledger;
morning questions noted):**
- Row 79 OUT (harvest-first rule; roadmap:302 "Do not write a schema
  before that"). Filed as a follow-up row at wrap. Morning question:
  Tracearr deploy status.
- Awards: opt-in operator definitions ONLY (the "presets are opinions,
  disabled by default" posture); ONE event end-to-end with the oracle
  comparison; WHICH events become defaults/definitions = morning question
  (recommendation: Golden Globes or BAFTA first). `collections.awards`
  bool stays Oscars-only-defaults for now.
- `imdb_search`: minimal-viable constraint tier — title type, genre,
  user rating, vote count, release-date window, sort. Kometa-complete =
  follow-up row. Bounds the un-introspectable live walk.
- Person builders: FILMOGRAPHY ONLY (params `{id}`); 10c's later, more
  specific line wins the roadmap's row-83 self-contradiction (annotate at
  wrap). No tmdb_person summaries/posters, no popular-people, no
  birthday/deathday gating, no library-wide credit scans.
- Person route: `/person/{id}/movie_credits` + `/tv_credits` (single
  request each, covers Show libraries) — NOT discover-with_cast (paged,
  movie-only). One new not-paged client method pair modeled on
  `collection_parts`.
- `tmdb_discover` ACCEPTS `region`/`language`/`sort_by`/`watch_region`
  (they genuinely change discover results); the three narrow entity
  builders (`tmdb_company`/`network`/`keyword`) KEEP their minimal model
  (no widening, no cache-key churn).
- Discover paging: cap stays `MAX_PAGES = 10`, but discover gains the
  IMDb-style WARNING at the cap (rows 144/145 stay filed for the deeper
  hardening).

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds — full repo-relative
  paths in every brief/report; isolated compose per task (`-p 8ct<N>`),
  timeout 1800 under contention, **foreground `docker wait` only — the
  blocking IS the wait; never write "standing by"**, teardown after.
- Baseline at branch start (feat/phase8b-list-builders b361211): full
  suite **2370 passed / 0 skipped** (verify and record the
  `-m "not imagemagick"` number at Task 1 start).
- The 8a golden gate stays byte-identical (no default definitions
  change — awards defaults remain Oscars-only).
- Builders raise on failure; order = source order; params models
  load-enforced (automatic); registry invariants zero-edit; every network
  builder fixture-pinned with MockTransport request-shape assertions.
- IMDb GraphQL: the 8b drift posture verbatim — level-by-level shape
  validation, bounded excerpts, Refused-vs-Drift classes, errors-on-200
  checked first, NO caching (raw POST), warning at page cap. Any live
  schema walk documents its queries + responses in the task report and
  scrubs personal identifiers from fixtures.
- Awards: per-event work is a vertical slice with an ORACLE comparison
  (the shipped-event fixture vs Kometa's members for the same event/year —
  document the oracle procedure in the report); never invent
  titles/summaries/poster paths — transcribe or omit (row-146 posture).
- Credential discipline unchanged across all surfaces; no new secrets;
  no new deps.
- Mutation proofs for every guard; red-first; copy+cmp restores.
- Stage by name; `--no-gpg-sign`; no AI attribution.

## Verified facts (8c fact sheet 2026-08-25 @ b361211; spot-check before relying)

- Discover transport EXISTS: `src/autoposter/providers/tmdb_lists.py:258-266`
  `discover(media_type, filters)` passes an arbitrary Mapping through
  `_paged` (:156-201, `MAX_PAGES = 10` :57, silent break — add the
  warning). `chart()` takes region/language (:203-221) but `discover()`
  does not yet. `collection_parts` (:245-256) is the not-paged method
  precedent for credits.
- Exposed discover surface today: ONE param (`id`) via `TmdbEntityParams`
  (`src/autoposter/collections/builders/tmdb.py:143-157`, extra=forbid);
  `_DiscoverBuilder` (:271-293) hardcodes with_companies/with_networks/
  with_keywords (:301,310,318). Region/language deliberately refused
  there (:145-149) — that judgment REVERSES for generic discover only.
- NO transcribed discover-attr table exists in the repo — Task 1 (T0)
  produces it; the dotted TMDb names (`vote_average.gte`) need an alias
  map (pydantic field names can't carry dots).
- Shape validators reusable verbatim: region/language
  (`src/autoposter/collections/builders/tmdb.py:58-60,118-140`).
  `require_library_type` + `media_types` maps (`src/autoposter/collections/
  builders/base.py:229-252`).
- IMDb: transport constants `src/autoposter/collections/charts.py:13-14`;
  the 8b module to generalize: `src/autoposter/collections/imdb_lists.py` —
  `_connection`/`_ids`/`_excerpt` (:115-188), PAGE_SIZE 250 (:62),
  MAX_PAGES 10 (:67), cap warning (:215-218), variable-carrying POSTs
  (:201-203), Refused/Drift split (:32-37), errors-before-status
  (:127-133). Introspection REFUSED — the advancedTitleSearch constraint
  schema must be walked live via validator errors (the row's real cost;
  budget it separately). Its edges are `{node: {title: {id}}}` — a second
  shape, so generalize `_connection`/`_ids` over root + edge path first.
- Awards: `src/autoposter/collections/awards.py` — `EVENT_ID="ev0000003"`
  default param (:11,30-37), BASE_URL Kometa-Team/IMDb-Awards (:12),
  category variant tuples (:16-27), winners_for_categories (:69-79),
  recent_years (:52-60). Builder: `src/autoposter/collections/builders/
  imdb_award.py` — AWARDS registry (:38-51), Oscars summary prose
  (:30-34), YEAR_TITLE/TITLE_PATTERN (:53,62), run_cache key
  `"imdb_award.event"` (:64) — MUST become event-scoped
  (`f"imdb_award.event:{event_id}"`), and TITLE_PATTERN is per-CLASS —
  two ceremonies need two classes or a safely-scoped pattern (leftovers
  mis-attribution risk via `engine.definition_titles` :724-730). Posters:
  `src/autoposter/collections/posters.py:62-65` hardcodes `award/oscars/`
  — needs an event→segment map; each path must EXIST in
  Kometa-Team/Default-Images or be omitted. `event_validation.yml`
  (`docs/research/kometa-collections.md:127-134`): Kometa validates event
  ids against it; we fetch only the event file — ADD the validation
  lookup (load-time-adjacent refusal beats a 404 mid-pass; never the
  scrape fallback).
- Person credits: `/person/{id}/movie_credits` + `/tv_credits` return
  cast[] + crew[] (job/department), NOT paged. Kometa's five names =
  one path + role filter: actor=cast; director/writer/producer=crew by
  job; crew=all crew. The alias-registration precedent:
  `src/autoposter/collections/builders/__init__.py:67-69`.
- The 10c boundary (roadmap:607-618): dynamic person types, tmdb_person
  summaries/posters, popular-people, birthday gating, credit scans — ALL
  out of 8c.
- Inheritance from 8b available to all: SourceClients fields, ctx.cache,
  run_cache memo precedents (`src/autoposter/collections/builders/
  imdb_award.py:64-85`, `arr.py:26,94-99`, `mdblist.py:70,134-145`),
  fixture idioms (`tests/test_builder_tmdb.py:36-62`,
  `tests/test_builder_imdb_lists.py:37-58`), 26 registered builders.
- Kometa award event candidates (inventory:145): bafta, berlinale,
  cannes, cesar, choice, emmy, golden, nfr, pca, razzie, sag, spirit,
  sundance, tiff, venice.

---

## Task 1: The discover attribute matrix + `tmdb_discover` (row 80)

**Files:**
- Create: `src/autoposter/collections/builders/tmdb_discover.py` (the
  matrix as a typed table + the params model generated/validated from it +
  the builder), fixtures
- Modify: `src/autoposter/providers/tmdb_lists.py` (plumb
  language/region/sort_by/watch_region into `discover()`; the cap
  WARNING), `src/autoposter/collections/builders/__init__.py`
- Test: `tests/test_builder_tmdb_discover.py`

**Interfaces:**
- THE MATRIX IS THE DELIVERABLE reviewers check (the 9b pattern a phase
  early): a module-level table — TMDb param name → field name (alias for
  dotted), type, validator, movie-only/tv-only/shared. Code generates the
  pydantic model fields (or validates params against the table — pick the
  clearer mechanism and argue it). Cover the ~40 documented discover
  params; a param in neither the table nor pydantic's model = load error
  (extra=forbid as everywhere).
- `tmdb_discover` params: the matrix fields + `sort_by` + `region`/
  `language`/`watch_region`; media type follows library_type
  (`media_types = {"Movie": "movie", "Show": "tv"}`); movie-only fields
  on a Show-library definition = BUILD-time refusal naming the field
  (mirror require_library_type's message style; load-time impossible —
  library binding is per-pass).
- Discover cap: warning at MAX_PAGES like `src/autoposter/collections/
  imdb_lists.py:215-218`.
- [ ] Red-first: matrix-driven load rejection (bad name, movie-only field
      on tv), dotted-alias round-trip (`vote_average.gte` in YAML → the
      request param), region/language reach the wire, order across pages,
      cap warning fires. Mutation proofs: drop the movie-only check →
      red; drop the alias map → red. Full suite + ruff + golden. Commit.

## Task 2: Person filmography builders (row 83 static half)

**Files:**
- Create: `src/autoposter/collections/builders/tmdb_person.py`
- Modify: `src/autoposter/providers/tmdb_lists.py` (`person_credits(
  person_id, media_type)` — not-paged, `collection_parts` shape),
  `src/autoposter/collections/builders/__init__.py`
- Test: `tests/test_builder_tmdb_person.py` + fixtures (movie_credits +
  tv_credits recordings-shaped)

**Interfaces:** five registrations (`tmdb_actor`, `tmdb_director`,
`tmdb_writer`, `tmdb_producer`, `tmdb_crew`) on one class + a role table
(actor=cast; director=crew job "Director"; writer=crew department
"Writing"; producer=crew job "Producer"; crew=all crew — verify the
job/department strings against real TMDb credits shapes and pin them);
params `{id: int>0}`; works on BOTH library types via the media-typed
credits endpoint; ordering: TMDb credit order preserved (document what
that is); 404 person → raise naming the id. HARD STOP at the 10c line
(a docstring states what is deliberately absent and points to 10c).
- [ ] Red-first per role filter (a crew-job fixture proving director ≠
      producer filtering); both library types; 404. Mutation proof: role
      filter dropped → red. Full suite + ruff + golden. Commit.

## Task 3: `imdb_search` minimal-viable (row 81)

**Files:**
- Modify: `src/autoposter/collections/imdb_lists.py` (generalize
  `_connection`/`_ids` over root + edge path — 8b tests stay green
  unmodified) — or a sibling module if cleaner; argue it
- Create: `src/autoposter/collections/builders/imdb_search.py`, fixtures
  (RECORDINGS of the live walk, identifiers scrubbed)
- Test: `tests/test_builder_imdb_search.py`

**Interfaces:** params (the minimal tier): `type` (movie/tv →
titleTypeConstraint, cross-checked against library_type), `genres`,
`rating_gte`/`rating_lte` (userRatingsConstraint), `votes_gte`
(vote count), `released_after`/`released_before` (releaseDateConstraint),
`sort` (a small validated enum). The LIVE WALK is step one: probe
`advancedTitleSearch`'s constraint/sort input objects via validator
errors (introspection is refused), record every query+response pair used,
scrub identifiers, pin the working query verbatim in fixtures. Drift
posture verbatim from 8b (Refused/Drift, bounded excerpts, loud raises).
Kometa-complete surface = follow-up row at wrap.
- [ ] Red-first: each constraint reaches the wire (request-shape
      asserts); type-vs-library mismatch refused; drift shape raises;
      cursor paging + cap warning. Mutation proofs: drift-degraded-to-
      empty → red; constraint dropped from the query → red. Full suite +
      ruff + golden. Commit. If the live walk reveals the surface is
      materially different from the plan's tier, STOP and report BLOCKED
      with what was found — do not improvise a different tier.

## Task 4: Award events generalized + one proven ceremony (row 82)

**Files:**
- Modify: `src/autoposter/collections/awards.py` (event-parameterized
  helpers; `event_validation.yml` lookup — refuse unknown event ids,
  NEVER a scrape fallback), `src/autoposter/collections/builders/
  imdb_award.py` (per-event registry: event id → categories + poster
  segment + transcribed title/summary strings + year-title pattern;
  event-scoped run_cache keys; TITLE_PATTERN scoping decision — two
  classes or safely-scoped patterns, argued vs the leftovers
  mis-attribution risk), `src/autoposter/collections/posters.py` (the
  event→segment map; a missing Default-Images path = omit the poster,
  never guess)
- Test: `tests/test_collection_awards.py` (extend), fixtures: ONE
  non-Oscars event YAML (recommendation: ev0000123 Golden Globes or
  ev0000123-equivalent BAFTA — use the REAL event id from
  event_validation.yml; record the fixture from the actual dataset) +
  the oracle-comparison test
- [ ] The ORACLE: build the chosen event's winners collection from the
      recorded fixture and compare members against Kometa's output for
      the same event/year (document how the Kometa member list was
      obtained in the report — the roadmap requires the comparison, not a
      generic claim). Oscars defaults BYTE-UNCHANGED (golden). New events
      are opt-in operator definitions only — no default_definitions
      change. Red-first: unknown event id refused at the validation
      lookup; event-scoped memo (two events, two fetches, one each);
      category matching on the new event's vocabulary. Mutation proofs:
      validation lookup dropped → red; memo collision (shared key) → red.
      Full suite + ruff + golden. Commit.

## Task 5: Roadmap, whole-branch review, PR (stacked)

- Rows 80/81/82/83 `answered 8c:` (81 notes the minimal tier + follow-up
  row for Kometa-complete; 82 notes one-proven-event + morning-question
  pending for defaults; 83 notes filmography-only, annotates the
  row-83/10c contradiction resolution); row 79 deferred-with-row (the
  143 precedent); 8c phase entry delivered-with-notes; follow-up rows
  discovered en route.
- Whole-branch review (most capable model): the discover matrix's
  honesty (params that silently no-op = the phase's enemy), the live-walk
  fixtures' fidelity, the award oracle's genuineness, cross-builder
  consistency with the 8b family. One batched fix round.
- Both suites; PR `feat/phase8c-query-builders` → base
  `feat/phase8b-list-builders` (STACKED — the description MUST carry:
  "merge #69 first, then retarget this PR's base to main before merging;
  merging into the source branch after it merged delivers nothing").
  No AI attribution.
