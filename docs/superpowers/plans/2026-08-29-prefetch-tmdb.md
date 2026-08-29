# Prefetch Phase A (TMDb-side widening + budget) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the facts pipeline's existing `/movie/{id}` and `/tv/{id}` reads
with `origin_country`, `original_language` and `tmdb_collection_id`, give TMDb a
DB-clock rate budget and the pipeline a "we looked" record, then build the
DB-backed enumeration seam and the facts-enumerated collection families that
close roadmap rows 189 and 192 and flip the packs that can ship honestly.

**Architecture:** Three new scalar/array columns ride reads the pipeline
already pays for (zero new requests); `persist_facts` gains an unconditional
`facts_attempted_at` stamp so "we looked and TMDb had nothing" is a recordable
state; a single-row `tmdb_rate_state` table carries a shared backoff window on
the database clock, exactly as `imdb_miss_refresh_state` already does. On top of
that sits a new enumeration seam (`collections/facts_enumeration.py`, a pure-SQL
read over `item_facts` × `media_items`) consumed by **list** builders — a third
family shape that reuses phase 10a's `derive_keys`/`family_titles` unchanged and
does not touch the ten shipped dynamic types at all.

**Tech Stack:** Python 3.14, SQLAlchemy 2 (async, Postgres 18), alembic,
pydantic v2, httpx, pytest, Docker Compose. **No new runtime dependency.**

---

## Read this first — what governs this plan

1. `.superpowers/sdd/p-prefetch-facts.md` **C1–C10** — the controller
   adjudications. They are **SETTLED**. This plan implements them; where the
   evidence disagrees with itself or with the tree, the CONTRADICTION FLAGS
   section below says so and **the adjudicated version is what gets built
   anyway**.
2. The recon's tree citations. Every `file:line` in this plan was read at
   `bf7a01b` and is re-verifiable there.
3. `.superpowers/sdd/p-prefetch-capture.md` — **does not exist yet.** Task 1
   writes it, and Tasks 2–5 read their payload shapes and their transcribed
   tables out of it. A step that says "from §3 of the capture record" names its
   source and its verification; it is not a placeholder.

**How the data-carrying steps work.** Two tasks consume data this plan
deliberately does not carry: the real TMDb payload shapes (T2's fixtures and
parser expectations) and Kometa's franchise/region/continent tables (T4's
packs). Both come from Task 1's capture record, fetched and captured rather than
recalled — `catalog.py:522-533`'s provenance rule and `packs.py`'s module
docstring are the discipline, and a recalled include list is exactly what they
refuse.

---

## What this phase is NOT

- **Not Phase B** (C1). The batched `/library/metadata/{k1,k2,…}` read, the
  credits cache, rows 169/194, row 91's collectionless builder, the 180/175
  verdict walk and the 155 tier-2 accessors are **out**. Task 5 re-points
  anything this phase touches, honestly, and nothing else.
- **No `filters:` surface for the new columns** (row 156, C3). The three columns
  are named OURS and are **enumeration-only**. Nothing in this phase adds a
  `FILTER_ATTRIBUTES` row, a `filters.BY_NAME` entry or a `facts` source tier.
  That is row 156's own future work.
- **No change to the ten shipped dynamic types.** `collections/dynamic_types.py`,
  `collections/builders/dynamic.py`, `dynamic_keys.py` and `dynamic_titles.py`
  are **read-only for the whole phase** except for one appended module-docstring
  paragraph in `dynamic_types.py` (Task 3b, Step 9), which changes no row and no
  behaviour. `DYNAMIC_TYPES` keeps exactly its ten keys, and a test asserts it.
- **No fourth ItemFacts column for the franchise NAME.** The name is a property
  of the collection, not of the item; it is read once per franchise from
  `/collection/{id}` — the same URL, and therefore the same provider-cache
  entry, that `collection_parts` already reads for that franchise's membership
  (Task 3b, Step 4 states the decision).
- **No credits/people work of any kind** (C1). Row 194 stays open.
- **No golden-gate movement.** `tests/fixtures/collections/golden_port.json` is
  byte-identical at every task's end.

---

## CONTRADICTION FLAGS

Three places where the evidence disagrees with itself or with the tree. Each is
flagged here and **planned as the controller adjudicated it**.

**FLAG 1 — C6 says the enumeration seam is "a per-type enumeration strategy,
the DynamicType row declaring which"; the tree makes that unbuildable, and C7
(the later, more specific adjudication about these very types) says why.**
`DynamicBuilder` is a **smart** builder end to end: it enumerates through
`LibraryTagResolver.choices` and then builds each key with
`parse_filters` → `build_search_url` → `reconcile_smart_collection`
(`builders/dynamic.py:572-576`, `:762-879`). C7 establishes that a
facts-enumerated type's MEMBERSHIP cannot be a Plex smart filter at all — Plex
has no `origin_country` field — so these are LIST families. A `DYNAMIC_TYPES`
row whose type the only consumer of that table cannot build would be a row that
does not work, and shipping it would also break the "zero behaviour change to
the ten existing types" law by putting an unbuildable eleventh, twelfth and
thirteenth row in front of `DynamicParams._the_type_must_be_one_this_service_enumerates`
(`builders/dynamic.py:361-369`), which advertises every key of that dict to an
operator as an option.
**Planned:** C7's shape, which is C6's substance in the only table that can hold
it. The seam IS a per-type enumeration strategy declared by a type row — the row
lives in the new `collections/facts_family.py` table rather than in
`DYNAMIC_TYPES`, its docstring cross-references `dynamic_types.py`'s scope note,
and `dynamic_types.py`'s scope note gains one paragraph pointing back (Task 3b,
Step 9). The ten dynamic rows and their builder are untouched, which is the
strongest possible form of the law C6 also states.

**FLAG 2 — C7 calls these "LIST-builder families" and the phase brief calls the
shape "a third family shape", but the engine's family SWEEP protocol
(`engine._family_state`, `engine.py:674-707`) was written for smart builders and
the engine's leftovers report (`definition_titles`, `engine.py:946-987`) offers
a list builder only a `TITLE_PATTERN` regex.** A facts family's titles come from
`title_format` over library values, which no fixed regex can match — so without
the family-label protocol these collections would be swept on the very next pass
(they carry the ownership label and a `managed_collections` row, and their
titles are not in `definition_titles_for`'s set).
**Planned:** the new builder implements the protocol `builders/dynamic.py`'s
docstring already names as the joining condition — "Growing these two methods is
the whole protocol a future family builder needs to join the sweep" (`:542-551`)
— `family_label(definition)` and `generated_titles(run_cache, definition)`, with
its own label prefix. `_family_state` reads them off the registry entry by
`getattr` and does not care that this one is a list builder, which is checked by
a real test rather than assumed (Task 3b, Step 8).

**FLAG 3 — C10 sizes the phase at "5-6 tasks" with T3 as one task; the tree says
T3 is two.** T3 as written contains a new DB read seam, a new field on
`BuilderContext` (an engine change), a new membership builder, a three-row type
table, name resolution against TMDb, the expansion, and the sweep protocol.
C10 licenses the split explicitly ("The plan may split T3") and C7 carries the
honesty valve.
**Planned:** **six tasks**, T3 split into **T3a** (the seam + the single-purpose
membership builder, independently useful and independently reviewable) and
**T3b** (the family). C7's valve is written into T3b's own header: if T3b's
review finds the family exceeds the phase, T3a's single-purpose builder ships
alone, T4 re-files all three packs with that reason, and the family integration
becomes its own roadmap row — **honesty over force**, and Task 5 records which
branch was taken either way.

---

## Branch and cut point

- Branch name: **`feat/prefetch-tmdb`**.
- **Cut from `origin/main` after a fetch, verified with a CONTENT probe, never a
  sha probe:**

```bash
git fetch origin
git cat-file -e origin/main:src/autoposter/facts/tmdb_facts.py
git cat-file -e origin/main:src/autoposter/collections/packs.py
git cat-file -e origin/main:src/autoposter/collections/dynamic_types.py
git show origin/main:src/autoposter/facts/gather.py | grep -q "facts.is_empty()" && echo SHORTCIRCUIT-PRESENT
git show origin/main:src/autoposter/facts/imdb.py | grep -q "_miss_refresh_due" && echo DBCLOCK-PRESENT
git show origin/main:src/autoposter/collections/engine.py | grep -q "_family_state" && echo FAMILYSWEEP-PRESENT
git checkout -b feat/prefetch-tmdb origin/main
git rev-parse HEAD
```

All three probes must print. `SHORTCIRCUIT-PRESENT` is the C4 break site,
`DBCLOCK-PRESENT` is the C5 pattern this phase copies, and
`FAMILYSWEEP-PRESENT` is the 10a-2 protocol FLAG 2 joins. If any is missing,
`origin/main` predates the phase this plan was written against — **STOP and
report**; do not stack on a phase branch. At the time this plan was written
`origin/main` is `bf7a01b`. Record the resolved `git rev-parse HEAD` in the Task
1 report.

## Execution

Executed via **superpowers:subagent-driven-development**: one fresh subagent per
task, two-stage review between tasks, this plan **live-synced** — a fix that
changes a signature, a column name, a message or a table edits the corresponding
step here in the same commit, so a later task's implementer reads the truth and
not the original guess.

---

## Global Constraints

Every task's requirements implicitly include this section. Copied verbatim from
the phase brief.

1. **Row 156's naming law.** The new columns are named OURS
   (`tmdb_origin_country`, `tmdb_original_language`, `tmdb_collection_id`) —
   never Kometa's filter names — and NOTHING in this phase makes them
   `filters:`-writable. Enumeration-only use does not trigger the naming
   collision with the Plex `country` row; a `filters:` surface is row 156's own
   future work with its own `facts` source tier.
2. **Zero behaviour change to the ten existing dynamic types.** Their tests are
   the gate (`tests/test_collection_dynamic_types.py`,
   `tests/test_collection_dynamic.py`, `tests/test_collection_dynamic_oracle.py`,
   `tests/test_collection_cs_equivalence.py`). `git diff --stat origin/main --
   src/autoposter/collections/dynamic_keys.py src/autoposter/collections/dynamic_titles.py
   src/autoposter/collections/builders/dynamic.py` must be **empty** at every
   task's end, and `dynamic_types.py`'s diff must be docstring-only.
3. **Refusals RETURN.** A builder reports an operator-visible problem as an
   action string, never as an exception that escapes into the engine. New
   exception classes are for the PROVIDER layer (`TmdbRateLimited`), where the
   shipped precedent is `MDBListLimitReached` being caught in
   `facts/gather.py:104-107`.
4. **Transport failures are logged class-name-only.** `logger.warning("…: %s",
   type(exc).__name__)` — never `str(exc)` and never the response body, because
   an httpx error's `str()` carries the full URL.
5. **Never cache a 429 or any other refusal body.** Row 147's MDBList defect is
   the anti-pattern and is cited by name in the code comment that prevents it. A
   test asserts `provider_cache` is EMPTY after a 429.
6. **No URLs or tokens in tracked files** — not in code, comments, fixtures,
   logs, reports or the PR body. TMDb API paths (`/movie/{id}`) and public TMDb
   ids are fine and are the point; an operator's Plex URL, an API key or a
   bearer token is not, in any form.
7. **The golden gate is byte-identical.**
   `tests/fixtures/collections/golden_port.json` must not change in any task.
   Prove it with a real run, not an assumption.
8. **Container discipline.** A unique compose project per task (`pprefetcht1` …
   `pprefetcht6`), always with the `.superpowers/isolated-db.yml` overlay. Tee
   output to a path under `/app/.superpowers/` inside the container — never rely
   on streamed stdout. A long run uses **no `--rm`**, is started detached and
   waited on with a foreground `docker wait`, and the log is read back from the
   host. Teardown is `docker compose -p <project> down` — **never** `down -v`.
9. **Mutation proofs are backup + cmp.** Copy the file, edit the copy's source
   in place, run the test to see it RED, restore from the backup, `cmp` the
   restored file against the backup to prove the restore was exact, re-run to
   see GREEN. Paste real output, both halves.
10. **Commits** are conventional, `--no-gpg-sign`, staged **by name** (never
    `git add -A`), and carry **no AI attribution** of any kind.
11. **Artifact names are `p-prefetch`-prefixed**: `p-prefetch-capture.md`,
    `p-prefetch-task-N-report.md`, `p-prefetch-pr-body.md`.
12. **Every refusal names the way out.** A message that says only what is wrong
    is half a message.

---

## The container commands, once

Every task's test runs use these two shapes. `<proj>` is that task's project
name.

```bash
# a short run (a file or a node), foreground, output teed inside the container
docker compose -p <proj> -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_x.py -v 2>&1 | tee /app/.superpowers/run-<proj>-x.log"

# a long run (the full suite): detached, NO --rm, waited on in the foreground
docker compose -p <proj> -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name <proj>-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-<proj>-full.log"
docker wait <proj>-full
# then read .superpowers/run-<proj>-full.log from the HOST
docker compose -p <proj> -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `.superpowers/sdd/p-prefetch-capture.md` | **new.** The capture record: two live TMDb payloads, the C2 gate verdict, the three upstream pack files, the NOT-KOMETA list, the row-155 endpoint spelling | T1 |
| `tests/fixtures/facts/tmdb_movie.json`, `tmdb_show.json` | replaced with real, scrubbed, provenance-dated captures | T1 |
| `src/autoposter/facts/models.py` | `GatheredFacts` gains the three fields and their `is_empty` clauses | T2 |
| `src/autoposter/facts/tmdb_facts.py` | `parse_movie_facts`/`parse_show_facts` read the three fields; `_get` gates on the budget and notes a 429 | T2 |
| `src/autoposter/facts/tmdb_budget.py` | **new.** `TmdbRateLimited`, `TmdbRateBudget` (DB clock, asyncio.Lock, Retry-After) | T2 |
| `src/autoposter/db/models.py` | `ItemFacts` gains three columns; new `TmdbRateState` | T2 |
| `alembic/versions/<rev>_tmdb_facts_widening.py` | **new.** The three columns and the state table | T2 |
| `src/autoposter/facts/gather.py` | the three fields into `values`; the unconditional "we looked" stamp; `TmdbRateLimited` caught like `MDBListLimitReached` | T2 |
| `src/autoposter/config/schema.py` | `OperationsConfig.tmdb_backoff_seconds` | T2 |
| `src/autoposter/app.py` | builds the budget and hands it to `TMDBFactsClient` | T2 |
| `src/autoposter/collections/facts_enumeration.py` | **new.** The DB-backed enumeration + membership reads, and the field table they key on | T3a |
| `src/autoposter/collections/builders/base.py` | `BuilderContext.session` (T3a), `BuilderContext.definition` (T3b) | T3a, T3b |
| `src/autoposter/collections/engine.py` | passes both through to `BuilderContext` | T3a, T3b |
| `src/autoposter/collections/builders/facts_value.py` | **new.** `facts_value`: one collection, membership from a facts column | T3a |
| `src/autoposter/collections/facts_family.py` | **new.** The three family type rows | T3b |
| `src/autoposter/collections/builders/facts_family.py` | **new.** `facts_family`: enumerate → derive → title → expand, with the sweep protocol | T3b |
| `src/autoposter/providers/tmdb_lists.py` | `collection_name`, sharing `collection_parts`' cache entry | T3b |
| `src/autoposter/collections/packs.py` | the franchise, region and continent tables | T4 |
| `src/autoposter/collections/catalog.py` | three rows flip; the gate constants and the checksum move | T4 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | rows 189/192 close; 155's spelling; the fifth carry filed; the Phase-B carries re-pointed | T5 |
| `.superpowers/sdd/p-prefetch-pr-body.md` | **new.** The PR body and the acceptance procedure | T5 |

---

## Task 1: The capture probe

**Files:**
- Create: `.superpowers/sdd/p-prefetch-capture.md`
- Modify: `tests/fixtures/facts/tmdb_movie.json`, `tests/fixtures/facts/tmdb_show.json`
- Read only: `src/autoposter/facts/tmdb_facts.py`, `.env`

**Interfaces:**
- Produces: the capture record every later task reads. Its section numbers are
  fixed and later tasks cite them: **§0** the capture record (what was asked,
  when, byte counts), **§1** the movie payload's field inventory, **§2** the tv
  payload's, **§3** the **C2 GATE VERDICT**, **§4** the three upstream pack
  files transcribed whole, **§5** the NOT-KOMETA list, **§6** the row-155
  endpoint spelling. Also the two rewritten fixtures.
- Consumes: nothing. **No `src/` changes in this task.**

**Background.** `tests/fixtures/facts/tmdb_movie.json` is 302 bytes of
hand-trimmed JSON with six keys. Nothing in it says whether TMDb's real movie
payload carries `origin_country` — a field TMDb added to `/movie/{id}` later
than the others, which is the recon's stated caution — and a parser written
against a hand-trimmed fixture proves only that the parser reads the fixture.
This task replaces both fixtures with real captures and **adjudicates C2's gate
before Task 2 plans around it**.

**The credential.** The TMDb v4 read token is `secrets.tmdb_token`, from
`AUTOPOSTER_TMDB_TOKEN` (`config/schema.py:16`), and it is present in the repo's
`.env`. It is sent as `Authorization: Bearer <token>`
(`tmdb_facts.py:137-138`). **It is never printed, never pasted into the record,
never written into a fixture, and never included in any command whose output is
quoted.** Read it from the environment inside the container; quote only the
response.

- [ ] **Step 1: Cut the branch**

Run the whole "Branch and cut point" block above. Paste the three probe results
and `git rev-parse HEAD` into the task report. If any probe fails, STOP.

- [ ] **Step 2: Pick the two ids from the library, not from memory**

The capture must be of titles this library actually holds, so the payload shape
is the one the pipeline meets. Read two `tmdb_id`s out of the database — one
movie, one show — rather than choosing famous ids:

```bash
docker compose -p pprefetcht1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test python - <<'PY'
import asyncio, os
from sqlalchemy import select
from autoposter.db.base import make_engine, make_session_factory
from autoposter.db.models import MediaItem

async def main():
    engine = make_engine(os.environ["AUTOPOSTER_TEST_DATABASE_URL"])
    factory = make_session_factory(engine)
    async with factory() as session:
        for kind in ("movie", "show"):
            row = (await session.execute(
                select(MediaItem.tmdb_id, MediaItem.title)
                .where(MediaItem.kind == kind, MediaItem.tmdb_id.isnot(None))
                .limit(1)
            )).first()
            print(kind, row)
    await engine.dispose()

asyncio.run(main())
PY
```

If the isolated test database is empty (it will be, on a fresh volume), fall
back to **two ids named in the repo's own fixtures**, which are public TMDb ids
and already tracked: movie `940143` (`tests/fixtures/facts/tmdb_movie.json`) and
show `95396` (`tests/fixtures/facts/tmdb_show.json`). Record in §0 which route
was taken and why. Either way the ids are public and go in the record.

- [ ] **Step 3: Capture the two payloads**

One read-only GET each, through the same client the pipeline uses, so the
capture is of the same request:

```bash
docker compose -p pprefetcht1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'python - <<PY 2>&1 | tee /app/.superpowers/p-prefetch-capture.json
import asyncio, json, os, httpx

async def main():
    token = os.environ["AUTOPOSTER_TMDB_TOKEN"]
    headers = {"Authorization": "Bearer %s" % token, "accept": "application/json"}
    async with httpx.AsyncClient() as http:
        out = {}
        for label, path in (("movie", "/movie/940143"), ("tv", "/tv/95396")):
            r = await http.get("https://api.themoviedb.org/3" + path, headers=headers)
            out[label] = {"status": r.status_code, "bytes": len(r.content),
                          "payload": r.json()}
        print(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))

asyncio.run(main())
PY'
```

Substitute the ids Step 2 chose. `.env` is loaded by compose for the `app`
service only, so pass the token in explicitly if the `test` service does not
have it:
`docker compose … run --rm -e AUTOPOSTER_TMDB_TOKEN="$AUTOPOSTER_TMDB_TOKEN" test …`
— and note that this is the ONE command whose full text must not be pasted into
the report with the value expanded.

If either response is not `200`, STOP and report the status code and nothing
else from the body.

- [ ] **Step 4: Write §0, §1 and §2 of the capture record**

`§0` is one row per capture: the path asked, the date (2026-08-29), the HTTP
status, the byte count, and the TMDb id. No token, no full URL with query
string.

`§1` (movie) and `§2` (tv) are **field inventories**, one line per top-level key
the payload carries, with its JSON type and — for the four fields this phase
cares about — its literal value:

| key | type | present | value (only for the four) |
| --- | --- | --- | --- |
| `origin_country` | array of string | yes/no | e.g. `["US"]` |
| `original_language` | string | yes/no | e.g. `"en"` |
| `belongs_to_collection` | object or null | yes/no | e.g. `{"id": 1241, "name": "Harry Potter Collection", …}` |
| `production_countries` | array of object | yes/no | (the fallback candidate) |

Every other top-level key is listed by name and type only. Write out the full
`belongs_to_collection` object when present — its `id` and `name` are what
Task 3b keys and titles on.

- [ ] **Step 5: Write §3 — the C2 GATE VERDICT**

This section is what gates Task 2. Answer exactly three questions, each with the
§1/§2 line that answers it:

1. **Does `/movie/{id}` carry `origin_country`?**
   - **YES** → write `GATE: PASS (movie origin_country present)`. Task 2 reads
     it straight off the details payload, as planned.
   - **NO** → write `GATE: STOP` and **STOP THE TASK HERE**. Report it, and do
     not begin Task 2. The record must then also carry the evidence for the
     fallback decision, so whoever adjudicates it has what they need: whether
     `production_countries` is present and what it holds (it is a list of
     `{iso_3166_1, name}` and is **not** the same field — it is the production
     country, which is what Plex's own `<Country>` already is, the very
     substitution roadmap row 189 rules out); whether
     `append_to_response=…` can add it in the same request (it cannot add a
     field that the details endpoint does not have — say so if that is what the
     evidence shows); and what a second endpoint would cost. **Adjudicating the
     fallback is not this task's job** — capturing the evidence for it is.
2. **Does `/tv/{id}` carry `origin_country` and `original_language`?** Same
   PASS/STOP shape, one line each.
3. **Does `/movie/{id}` carry `belongs_to_collection`, and is it `null` for a
   film in no franchise?** The second half matters: `null` is the normal case
   and Task 2's parser must treat it as absent rather than as a shape error.
   If the captured movie IS in a franchise, capture one more movie that is not
   (or note that the code path is proven by Task 2's unit test instead) and say
   which.

- [ ] **Step 6: Rewrite the two fixtures from the captures**

Replace `tests/fixtures/facts/tmdb_movie.json` and `tmdb_show.json` with real
payloads, **scrubbed to the fields and structure the parsers need plus the
fields this phase adds**, keeping the existing tests' expectations working. Two
rules:

- **Keep every key the existing tests assert on**, with the values they assert.
  `tests/test_tmdb_facts.py` pins `vote_average == 6.3`, `genres == ["Horror",
  "Drama"]`, `release_date == "2023-05-12"`, `production_companies[0].name ==
  "First Studio"` for the movie, and `networks[0].name == "Apple TV+"`,
  `first_air_date == "2022-02-18"` for the show. If the live payload's values
  differ, **keep the fixture's asserted values and take only the SHAPE from the
  capture** — this task must not turn eleven green tests red on a rating that
  moved. Say in §0 exactly which values were kept rather than captured.
- **Add the real captured values of the four fields in §1/§2's table**, verbatim.

Head both files with a provenance comment. JSON has no comments, so it goes in
a `"_provenance"` key, which every parser here ignores (they read named keys):

```json
{
  "_provenance": "shape captured from TMDb /movie/940143 on 2026-08-29; rating/genre/company values retained from the hand-trimmed fixture this replaced so the pinned assertions in tests/test_tmdb_facts.py keep meaning what they meant",
  "id": 940143,
  "title": "All Souls",
  "original_language": "en",
  "origin_country": ["US"],
  "belongs_to_collection": null,
  "vote_average": 6.3,
  "vote_count": 41,
  "release_date": "2023-05-12",
  "genres": [{"id": 27, "name": "Horror"}, {"id": 18, "name": "Drama"}],
  "production_companies": [
    {"id": 1, "name": "First Studio"},
    {"id": 2, "name": "Second Studio"}
  ]
}
```

Substitute the captured values for `original_language`, `origin_country` and
`belongs_to_collection`. Do the same for `tmdb_show.json` (`origin_country`,
`original_language`; a show has no `belongs_to_collection`).

**Add a third fixture** `tests/fixtures/facts/tmdb_movie_franchise.json` — a
real captured movie that IS in a franchise — so Task 2 and Task 3b have a real
`belongs_to_collection` object to parse. Same provenance key, same scrubbing.

- [ ] **Step 7: Prove the fixture rewrite changed nothing that was green**

```bash
docker compose -p pprefetcht1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_tmdb_facts.py tests/test_facts_gather.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht1-facts.log"
```

Expected: all pass, same count as before the edit. Paste the tail. If anything
went red, the fixture kept a shape it should have kept a value for — fix the
fixture, not the test.

- [ ] **Step 8: Fetch the three upstream pack files and write §4**

`WebFetch` is a deferred tool — load it first:

```
ToolSearch with query "select:WebFetch"
```

Then fetch each, at the pinned tag, raw:

```
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/movie/franchise.yml
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/movie/region.yml
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/movie/continent.yml
```

`§4` is one subsection per file, carrying **every** key the family builder can
consume, with `(absent)` written out where the file has none — an absent key is
a finding, not a gap:

- `type` (upstream's `dynamic_collections` key)
- `title_format`
- `include` (the whole list, in file order)
- `exclude`
- `addons` (the whole merge table, in file order)
- `key_name_override` / `title_override`
- `remove_prefix` / `remove_suffix`
- `other_name`
- `template_variables` — specifically any `sort_by` and `limit` hidden there
- the pack's own library scope

Nothing is paraphrased. A 200-entry addons table is written out as 200 entries.

**And one question §4 must answer explicitly, because Task 3b's key column
depends on it:** for `franchise.yml`, are `include`/`exclude`/`addons` keyed by
the TMDb collection **ID** (numeric) or by its **NAME** (string)? Quote three
entries verbatim. Same question for `region.yml`/`continent.yml`: are their
`addons` members ISO country **codes** (`us`, `fi`) or country **names**
(`United States`)? Quote three. Write the answer as a one-line verdict each:

```
FRANCHISE KEY: id | name
REGION MEMBERS: code | name
CONTINENT MEMBERS: code | name
```

- [ ] **Step 9: Write §5 (the NOT-KOMETA list) and §6 (row 155's spelling)**

`§5`: everything this phase will ship that no fetched file supplies — each
`max_collections` pin, each divergent `title_format`, the fact that an
`origin_country` bucket is titled from the raw ISO code because neither this
service nor the pack files carry a code→name table (the same posture roadmap row
190 records for languages), and the franchise family's name-resolution route.
One line each, naming who decided and why. This is the list Task 4 reproduces as
`# NOT KOMETA:` comments.

`§6`: row 155's endpoint spelling, corrected against the probe. The row
currently writes the batched read as
`/library/metadata?ratingKey=a,b,c` (query-parameter form). Record the
**path-segment form** — `/library/metadata/{k1},{k2},{k3}` — as the spelling
Task 5 will write into the row, and cite THIS capture record as the reason the
correction is being made now (the phase touched the neighbourhood) rather than
claiming a live Plex probe this phase did not run. **Do not claim a measurement
this task did not take.** If no evidence in this record supports the correction,
say so and Task 5 leaves the row's spelling alone — an unsupported "correction"
is worse than a stale one.

- [ ] **Step 10: Commit**

The record lives under `.superpowers/`, which is gitignored (`.gitignore`), so
only the fixtures are staged:

```bash
git add tests/fixtures/facts/tmdb_movie.json tests/fixtures/facts/tmdb_show.json \
        tests/fixtures/facts/tmdb_movie_franchise.json
git commit --no-gpg-sign -m "test(facts): real captured TMDb payloads behind the facts fixtures"
```

Paste §3, §4's three verdict lines and §6 **verbatim** into the task report:
they are the reviewable deliverable and Tasks 2–5 read them from there.

- [ ] **Step 11: Tear down**

```bash
docker compose -p pprefetcht1 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

Never `down -v`.

---

## Task 2: The widening, the "we looked" fix, and the rate budget

**Files:**
- Modify: `src/autoposter/facts/models.py`
- Modify: `src/autoposter/facts/tmdb_facts.py:52-92`, `:140-157`
- Create: `src/autoposter/facts/tmdb_budget.py`
- Modify: `src/autoposter/db/models.py:199-227` (and a new class at the end)
- Create: `alembic/versions/<rev>_tmdb_facts_widening.py`
- Modify: `src/autoposter/facts/gather.py:67-119`, `:122-192`
- Modify: `src/autoposter/config/schema.py:188-210`
- Modify: `src/autoposter/app.py:173-176`
- Test: `tests/test_tmdb_facts.py`, `tests/test_facts_gather.py`,
  `tests/test_item_facts.py`, new `tests/test_tmdb_budget.py`

**Interfaces:**
- Consumes: Task 1's §3 gate verdict (must be PASS) and the three rewritten
  fixtures.
- Produces, for Tasks 3a/3b:
  - `GatheredFacts.tmdb_origin_country: list[str]`,
    `GatheredFacts.tmdb_original_language: str | None`,
    `GatheredFacts.tmdb_collection_id: int | None`
  - `ItemFacts.tmdb_origin_country` (JSONB, NOT NULL, default `[]`),
    `ItemFacts.tmdb_original_language` (`String(16)`, nullable),
    `ItemFacts.tmdb_collection_id` (`Integer`, nullable)
  - `MediaItem.facts_attempted_at` now stamped by **every** `persist_facts`
    call, empty gathers included
  - `autoposter.facts.tmdb_budget.TmdbRateLimited` (Exception) and
    `TmdbRateBudget(session_factory, backoff_seconds)` with
    `async blocked() -> bool` and `async note_refusal(retry_after: float | None) -> None`

**Gate.** If Task 1's §3 says `GATE: STOP`, **do not start this task.** Report
that the gate blocked it.

- [ ] **Step 1: Write the failing parser tests**

Append to `tests/test_tmdb_facts.py`:

```python
def test_movie_facts_carry_the_three_prefetch_fields():
    """The widening, read off the same payload the pipeline already fetches.

    Zero new requests: these three fields ride the ``/movie/{id}`` read that
    already pays for the rating, the genres and the studio. Roadmap rows 189
    and 192 are what they are for.
    """
    facts = parse_movie_facts(load("tmdb_movie.json"))
    assert facts.tmdb_origin_country == ["US"]
    assert facts.tmdb_original_language == "en"
    assert facts.tmdb_collection_id is None
    assert facts.sources["tmdb_origin_country"] == "tmdb"
    assert facts.sources["tmdb_original_language"] == "tmdb"


def test_a_movie_in_a_franchise_carries_its_collection_id():
    """``belongs_to_collection`` is an object or ``null``; the id is what row
    192's enumeration keys on."""
    facts = parse_movie_facts(load("tmdb_movie_franchise.json"))
    assert isinstance(facts.tmdb_collection_id, int)
    assert facts.sources["tmdb_collection_id"] == "tmdb"


def test_show_facts_carry_the_two_fields_a_show_has():
    """A show has no ``belongs_to_collection`` -- TMDb collections are movie
    franchises (``builders/tmdb.py``'s ``TmdbCollectionBuilder``)."""
    facts = parse_show_facts(load("tmdb_show.json"))
    assert facts.tmdb_origin_country == ["US"]
    assert facts.tmdb_original_language == "en"
    assert facts.tmdb_collection_id is None


def test_the_three_fields_are_absent_rather_than_empty_when_tmdb_has_none():
    """Absent must never be written as a value -- ``persist_facts``' Finding 4.
    An empty ``origin_country`` list is the same statement as no key at all."""
    facts = parse_movie_facts({"id": 1})
    assert facts.tmdb_origin_country == []
    assert facts.tmdb_original_language is None
    assert facts.tmdb_collection_id is None
    assert "tmdb_origin_country" not in facts.sources


@pytest.mark.parametrize("payload", [
    {"origin_country": "US"},
    {"origin_country": [None, 5, "US"]},
    {"belongs_to_collection": []},
    {"belongs_to_collection": {"name": "no id here"}},
    {"belongs_to_collection": {"id": "not-a-number"}},
    {"original_language": 7},
])
def test_malformed_prefetch_fields_are_ignored_rather_than_raising(payload):
    """Every other parser here degrades on malformed input rather than taking
    the whole gather down (``_genres``, ``_first_name``, ``_as_date``); these
    three do the same. ``origin_country: "US"`` -- a bare string where TMDb
    documents a list -- is the one that would otherwise iterate to
    ``["U", "S"]``, which is a plausible wrong value, not an absent one."""
    facts = parse_movie_facts(payload)
    assert facts.tmdb_origin_country in ([], ["US"])
    if payload.get("origin_country") == [None, 5, "US"]:
        assert facts.tmdb_origin_country == ["US"]
    else:
        assert facts.tmdb_collection_id is None or isinstance(
            facts.tmdb_collection_id, int
        )
```

- [ ] **Step 2: Run them to see them fail**

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_tmdb_facts.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht2-red1.log"
```

Expected: FAIL with `AttributeError: 'GatheredFacts' object has no attribute
'tmdb_origin_country'`.

- [ ] **Step 3: Widen `GatheredFacts`**

In `src/autoposter/facts/models.py`, add three fields **after**
`originally_available` and **before** `sources` (so the dataclass's existing
positional construction in tests keeps working), and extend `is_empty`:

```python
    critic_rating: float | None = None
    audience_rating: float | None = None
    content_rating: str | None = None
    genres: list[str] = field(default_factory=list)
    studio: str | None = None
    originally_available: date | None = None
    # The three prefetch fields (roadmap rows 189/192). Named OURS, never
    # Kometa's filter names, and enumeration-only: row 156 owns the question of
    # ever making a facts-backed value `filters:`-writable, and it needs a
    # `facts` source tier this project does not have. `tmdb_origin_country` is
    # a LIST because TMDb's is (a co-production carries several); the other two
    # are scalars.
    tmdb_origin_country: list[str] = field(default_factory=list)
    tmdb_original_language: str | None = None
    tmdb_collection_id: int | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            (
                self.critic_rating is not None,
                self.audience_rating is not None,
                self.content_rating,
                self.genres,
                self.studio,
                self.originally_available,
                self.tmdb_origin_country,
                self.tmdb_original_language,
                self.tmdb_collection_id is not None,
            )
        )
```

- [ ] **Step 4: Widen the two parsers**

In `src/autoposter/facts/tmdb_facts.py`, add three readers above
`parse_movie_facts`:

```python
def _countries(payload: dict) -> list[str]:
    """``origin_country``, which TMDb sends as a list of ISO-3166-1 codes.

    A bare string is refused rather than iterated: ``"US"`` would otherwise
    become ``["U", "S"]``, which is a plausible wrong value and therefore worse
    than an absent one -- the same reasoning ``_genres`` applies to a
    ``genres`` that is not a list.
    """
    countries = payload.get("origin_country")
    if isinstance(countries, list):
        return [one for one in countries if isinstance(one, str) and one]
    return []


def _language(payload: dict) -> str | None:
    """``original_language``, an ISO-639-1 code. Titled from the code itself
    downstream: neither this service nor Kometa's pack files carry a code->name
    table, which is the divergence roadmap row 190 already records for the
    audio/subtitle language families."""
    value = payload.get("original_language")
    return value if isinstance(value, str) and value else None


def _collection_id(payload: dict) -> int | None:
    """``belongs_to_collection.id``, or None.

    ``null`` is the normal case -- most films are in no franchise -- so it is
    the absent case and not a shape error. The NAME is deliberately not read:
    it is a property of the collection rather than of the item, and the
    franchise family reads it once per franchise from ``/collection/{id}``,
    which is the same URL (and therefore the same provider-cache entry) its
    membership already comes from.
    """
    entry = payload.get("belongs_to_collection")
    if not isinstance(entry, dict):
        return None
    try:
        return int(entry["id"])
    except (KeyError, TypeError, ValueError):
        return None
```

Then, in `parse_movie_facts`, between `released = …` and `sources = {}`:

```python
    countries = _countries(payload)
    language = _language(payload)
    collection_id = _collection_id(payload)
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", released),
                       ("tmdb_origin_country", countries),
                       ("tmdb_original_language", language)):
        if value:
            sources[key] = "tmdb"
    if collection_id is not None:
        sources["tmdb_collection_id"] = "tmdb"
    return GatheredFacts(
        audience_rating=rating,
        genres=genres,
        studio=studio,
        originally_available=released,
        tmdb_origin_country=countries,
        tmdb_original_language=language,
        tmdb_collection_id=collection_id,
        sources=sources,
    )
```

And the same in `parse_show_facts`, **without** `collection_id` (a show has no
`belongs_to_collection`):

```python
    countries = _countries(payload)
    language = _language(payload)
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", aired),
                       ("tmdb_origin_country", countries),
                       ("tmdb_original_language", language)):
        if value:
            sources[key] = "tmdb"
    return GatheredFacts(
        audience_rating=rating,
        genres=genres,
        studio=studio,
        originally_available=aired,
        tmdb_origin_country=countries,
        tmdb_original_language=language,
        sources=sources,
    )
```

- [ ] **Step 5: Run the parser tests to see them pass**

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_tmdb_facts.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht2-green1.log"
```

Expected: PASS, and the eleven pre-existing tests in the file still pass.

- [ ] **Step 6: Commit the parser half**

```bash
git add src/autoposter/facts/models.py src/autoposter/facts/tmdb_facts.py tests/test_tmdb_facts.py
git commit --no-gpg-sign -m "feat(facts): read origin_country, original_language and the collection id off the TMDb details payload"
```

- [ ] **Step 7: Write the failing column + persistence tests**

Append to `tests/test_facts_gather.py`:

```python
async def test_persist_writes_the_three_prefetch_columns(session):
    media = MediaItem(rating_key="pf1", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(
        tmdb_origin_country=["US", "GB"],
        tmdb_original_language="en",
        tmdb_collection_id=1241,
        sources={"tmdb_origin_country": "tmdb"},
    ))
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_origin_country == ["US", "GB"]
    assert row.tmdb_original_language == "en"
    assert row.tmdb_collection_id == 1241


async def test_a_later_gather_without_them_does_not_blank_them(session):
    """Finding 4, one field along: a pass with no ``tmdb_id`` must not erase
    what a complete pass stored. ``tmdb_origin_country`` is the interesting one
    -- it is a NOT NULL JSONB defaulting to ``[]``, so a plain SQL COALESCE
    could not tell 'found nothing' from 'honestly empty', which is exactly why
    ``persist_facts`` builds its SET clause from populated fields only."""
    media = MediaItem(rating_key="pf2", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(
        tmdb_origin_country=["FI"], tmdb_original_language="fi",
        tmdb_collection_id=7,
    ))
    await persist_facts(session, media.id, GatheredFacts(critic_rating=4.9))
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.tmdb_origin_country == ["FI"]
    assert row.tmdb_original_language == "fi"
    assert row.tmdb_collection_id == 7
    assert row.critic_rating == pytest.approx(4.9)


async def test_an_empty_gather_still_records_that_we_looked(session):
    """C4, and the whole reason rows 189/192 can tell 'TMDb has nothing for
    this item' from 'nobody has asked yet'.

    ``persist_facts`` still writes NO ``item_facts`` row for an empty gather --
    an all-NULL row is pure noise and that rule is unchanged -- but the ATTEMPT
    is now stamped on ``media_items`` regardless, which is the shape
    ``facts_attempted_at`` already had for the drift sweep
    (``scheduler/jobs.py:142-151``). Before this, an item TMDb has never heard
    of and an item nothing ever fetched were the same two NULLs.
    """
    media = MediaItem(rating_key="pf3", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    assert media.facts_attempted_at is None

    result = await persist_facts(session, media.id, GatheredFacts())

    assert result is None, "an empty gather must still write no facts row"
    assert (await session.execute(
        select(func.count()).select_from(ItemFacts)
    )).scalar_one() == 0
    await session.refresh(media)
    assert media.facts_attempted_at is not None


async def test_a_non_empty_gather_stamps_the_attempt_too(session):
    """The stamp is unconditional -- one statement on both paths, not two
    statements that can drift apart."""
    media = MediaItem(rating_key="pf4", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    await persist_facts(session, media.id, GatheredFacts(critic_rating=4.9))
    await session.refresh(media)
    assert media.facts_attempted_at is not None
```

The file already imports `MediaItem`, `ItemFacts`, `GatheredFacts`,
`persist_facts` and `select`; add `func` to the sqlalchemy import if it is not
there.

- [ ] **Step 8: Run them to see them fail**

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_facts_gather.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht2-red2.log"
```

Expected: FAIL — the first three on `ItemFacts` having no such attribute /
column, `test_an_empty_gather_still_records_that_we_looked` on
`facts_attempted_at is not None`.

- [ ] **Step 9: Add the three columns and the budget's state table**

In `src/autoposter/db/models.py`, inside `ItemFacts`, after
`originally_available`:

```python
    originally_available: Mapped[date | None] = mapped_column(Date)
    # The three prefetch fields (roadmap rows 189/192), named OURS rather than
    # Kometa's -- row 156's law. They are ENUMERATION-only: nothing in this
    # service makes them `filters:`-writable, because a facts-backed filter
    # needs the `facts` source tier and the sparsity story row 156 owns.
    #
    # NOT NULL with a server-side default for the array, exactly like
    # ``genres``: an item TMDb reports no origin country for is honestly `[]`,
    # and ``persist_facts`` never writes a field the gather did not populate,
    # so "found nothing" is a row that keeps whatever it had.
    tmdb_origin_country: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # ISO-639-1 is two characters; 16 leaves room for the locale variants TMDb
    # occasionally sends (``pt-BR``) without inviting a free-text column.
    tmdb_original_language: Mapped[str | None] = mapped_column(String(16))
    # ``belongs_to_collection.id``. The NAME is not stored: see
    # ``facts/tmdb_facts._collection_id``.
    tmdb_collection_id: Mapped[int | None] = mapped_column(Integer, index=True)
```

And a new class at the end of the file, modelled on `ImdbMissRefreshState`
(`db/models.py:343-358`):

```python
class TmdbRateState(Base):
    """The shared TMDb backoff window (see ``facts/tmdb_budget.py``).

    A single row, pinned to ``id=1``, holding the moment past which TMDb may be
    asked again, on the database clock. Held here rather than in a process
    variable for ``ImdbMissRefreshState``'s reason: every pod behind one
    database then shares one window instead of each discovering the 429 for
    itself.

    ``blocked_until`` is NULL when nothing is known -- the state before the
    first refusal -- and is never cleared afterwards, only moved: a window in
    the past is the same statement as no window at all, and comparing against
    ``now()`` is cheaper than deleting a row on a schedule nobody runs.
    """

    __tablename__ = "tmdb_rate_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the last refusal was seen, for an operator reading the table. Never
    # compared against anything: the window is ``blocked_until``.
    refused_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

`text` and `String`, `Integer`, `JSONB`, `DateTime`, `func` are all already
imported at the top of that module.

- [ ] **Step 10: Write the migration**

First learn the real head — do not trust this plan's copy of it:

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test alembic heads
```

At the time this plan was written that is `e2c7a4b91d05` (job cancel_requested).
If it reports something else, use what it reports; if it reports **more than
one** head, STOP and report — this project has been bitten by multiple heads
before (`tests/test_migrations.py` exists because of it).

Create `alembic/versions/<rev>_tmdb_facts_widening.py` (generate `<rev>` with
`python -c "import uuid; print(uuid.uuid4().hex[:12])"`):

```python
"""tmdb facts widening and the rate-limit state

Revision ID: <rev>
Revises: e2c7a4b91d05
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '<rev>'
down_revision: Union[str, Sequence[str], None] = 'e2c7a4b91d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default, not just a Python-side default: ADD COLUMN NOT NULL would
    # otherwise fail against the populated item_facts table a deployed instance
    # already has -- the reasoning `renders.upload_status` records.
    op.add_column(
        'item_facts',
        sa.Column(
            'tmdb_origin_country', postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Nullable with no default: NULL means "TMDb has not told us", which is a
    # different statement from any string, and is what the enumeration skips.
    op.add_column(
        'item_facts', sa.Column('tmdb_original_language', sa.String(length=16), nullable=True)
    )
    op.add_column(
        'item_facts', sa.Column('tmdb_collection_id', sa.Integer(), nullable=True)
    )
    # The franchise enumeration groups by this column across the whole table.
    op.create_index(
        'ix_item_facts_tmdb_collection_id', 'item_facts', ['tmdb_collection_id']
    )
    op.create_table(
        'tmdb_rate_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('blocked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'refused_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('tmdb_rate_state')
    op.drop_index('ix_item_facts_tmdb_collection_id', table_name='item_facts')
    op.drop_column('item_facts', 'tmdb_collection_id')
    op.drop_column('item_facts', 'tmdb_original_language')
    op.drop_column('item_facts', 'tmdb_origin_country')
```

- [ ] **Step 11: Persist the three fields and stamp the attempt**

In `src/autoposter/facts/gather.py`, add `MediaItem` to the models import and
`update` to the sqlalchemy import:

```python
from sqlalchemy import func, select, update
...
from autoposter.db.models import ItemFacts, MediaItem
```

Then, in `persist_facts`, **above** the `is_empty` branch:

```python
    # C4, and the whole point of it: "we looked and found nothing" and "nobody
    # has looked" were the same two NULLs before this, and rows 189/192 need to
    # tell them apart -- an enumeration that cannot say how much of the library
    # it has actually visited cannot state its own coverage honestly.
    #
    # Unconditional, above the short-circuit, on the DATABASE clock like every
    # other timestamp here. It rides ``facts_attempted_at``'s existing shape:
    # the drift sweep already stamps it at selection time for exactly this
    # reason (``scheduler/jobs.py:142-151``), and stamping it again at persist
    # time is the same statement made by the path that actually did the work --
    # so an item reached by a webhook rather than by the sweep is recorded too.
    await session.execute(
        update(MediaItem)
        .where(MediaItem.id == media_item_id)
        .values(facts_attempted_at=func.now())
    )
    await session.commit()
    if facts.is_empty():
        return (
            await session.execute(
                select(ItemFacts).where(ItemFacts.item_id == media_item_id)
            )
        ).scalar_one_or_none()
```

And in the `values` block, after `originally_available`:

```python
    if facts.originally_available:
        values["originally_available"] = facts.originally_available
    if facts.tmdb_origin_country:
        values["tmdb_origin_country"] = facts.tmdb_origin_country
    if facts.tmdb_original_language:
        values["tmdb_original_language"] = facts.tmdb_original_language
    if facts.tmdb_collection_id is not None:
        values["tmdb_collection_id"] = facts.tmdb_collection_id
    if facts.sources:
        values["sources"] = facts.sources
```

Extend the docstring's Finding-4 paragraph with one sentence naming the new
array column:

```
    ``tmdb_origin_country`` joins ``genres``/``sources`` in the group a plain
    SQL ``COALESCE`` could not serve: it is ``NOT NULL`` JSONB defaulting to
    ``[]``, so "this round found no origin country" and "this item honestly has
    none" are the same value and only the populated-fields rule can tell them
    apart.
```

- [ ] **Step 12: Run the persistence tests to see them pass**

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_facts_gather.py tests/test_item_facts.py tests/test_migrations.py tests/test_scheduler_drift_job.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht2-green2.log"
```

Expected: PASS. `test_migrations.py` is included deliberately — it is what
catches a second alembic head, which is the defect that once left the shipped
image unable to boot.

- [ ] **Step 13: Commit the persistence half**

```bash
git add src/autoposter/db/models.py src/autoposter/facts/gather.py \
        alembic/versions/<rev>_tmdb_facts_widening.py tests/test_facts_gather.py
git commit --no-gpg-sign -m "feat(facts): persist the three prefetch fields and record every attempt"
```

- [ ] **Step 14: Write the failing budget tests**

Create `tests/test_tmdb_budget.py`:

```python
"""The TMDb rate budget: a shared backoff window on the database clock.

Built on ``ImdbMissRefresh``'s pattern (``facts/imdb.py:444-513``) -- the DB
clock so multiple pods share one window, an ``asyncio.Lock`` for the
intra-process race, and a state row written whether or not anything succeeds.
The difference is what triggers it: IMDb's cooldown is triggered by a MISS, and
this one by TMDb's own 429.
"""
import httpx
import pytest
from sqlalchemy import select

from conftest import session_factory_for
from autoposter.db.models import ProviderCache as ProviderCacheRow, TmdbRateState
from autoposter.facts.tmdb_budget import TmdbRateBudget, TmdbRateLimited
from autoposter.facts.tmdb_facts import TMDBFactsClient
from autoposter.providers.cache import ProviderCache


async def test_a_fresh_budget_is_not_blocked(session):
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    assert await budget.blocked() is False


async def test_a_refusal_blocks_the_next_ask(session):
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    await budget.note_refusal(None)
    assert await budget.blocked() is True


async def test_a_window_that_has_passed_does_not_block(session):
    """The window is compared against the DATABASE clock, not this process's --
    the rule ``facts/imdb.py``'s ``_is_stale``/``_miss_refresh_due`` state and
    this module keeps."""
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=0)
    await budget.note_refusal(None)
    assert await budget.blocked() is False


async def test_retry_after_wins_over_the_configured_backoff(session):
    """TMDb said how long; believe it rather than the local default."""
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=1)
    await budget.note_refusal(3600)
    row = (await session.execute(select(TmdbRateState))).scalar_one()
    assert row.blocked_until is not None
    assert await budget.blocked() is True


async def test_a_429_notes_the_refusal_and_raises_its_own_class(session):
    """Its own class, like ``MDBListLimitReached``: the gather catches it and
    keeps everything else it found, rather than losing a whole item's facts to
    one provider's budget."""
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"status": 25})

    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, budget=budget)
        with pytest.raises(TmdbRateLimited):
            await client.movie(940143)

    assert await budget.blocked() is True


async def test_a_refusal_body_is_never_cached(session):
    """Roadmap row 147's MDBList defect, refused here by construction.

    A cached 429 body would be served as an ANSWER for the whole TTL -- the
    provider cache stores ``{"found": …, "payload": …}`` and knows nothing
    about status codes. ``fetch_json`` writes the cache only on a 404 and on a
    2xx, and ``raise_for_status`` fires before either; this is the test that
    keeps it that way.
    """
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"status": 25})

    cache = ProviderCache(session_factory_for(session))
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, cache=cache, cache_ttl_seconds=3600,
                                 budget=budget)
        with pytest.raises(TmdbRateLimited):
            await client.movie(940143)

    rows = (await session.execute(select(ProviderCacheRow))).scalars().all()
    assert rows == [], "a refusal must never become a cached answer"


async def test_a_blocked_budget_costs_no_request_at_all(session):
    """The point of the window: the second item in the queue must not re-ask."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, json={"status": 25})

    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, budget=budget)
        with pytest.raises(TmdbRateLimited):
            await client.movie(1)
        with pytest.raises(TmdbRateLimited):
            await client.movie(2)

    assert len(calls) == 1, "the second ask should not have reached the network"


async def test_no_budget_configured_is_todays_behaviour_exactly(session):
    """``budget=None`` must leave the client as it shipped: a 429 raises
    httpx's own error out of ``raise_for_status``. A knob that changes
    behaviour when it is switched off is not a knob."""
    def handler(request):
        return httpx.Response(429, json={"status": 25})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await TMDBFactsClient("tok", http).movie(1)
```

- [ ] **Step 15: Run them to see them fail**

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_tmdb_budget.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht2-red3.log"
```

Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.facts.tmdb_budget'`.

- [ ] **Step 16: Write the budget**

Create `src/autoposter/facts/tmdb_budget.py`:

```python
"""A shared backoff window for TMDb, on the database clock.

TMDb has no daily cap of the kind MDBList meters (``facts/mdblist.py``'s
``MDBListLimitReached``), but it does answer 429 when a burst is too fast -- and
the drift sweep's whole job is to produce bursts: 500 items a week, each one a
``/movie/{id}`` or ``/tv/{id}`` read (``scheduler/jobs.py:124-195``). Without a
shared window, every worker in every pod discovers the same 429 independently
and keeps discovering it.

**The pattern is ``ImdbMissRefresh``'s**, deliberately and line for line
(``facts/imdb.py:444-513``):

- the window lives in a single database row, so pods behind one database share
  it -- a process variable would give each pod its own;
- both sides of every comparison come from the DATABASE clock (``func.now()``
  and the stored timestamp), never this host's, because the two can disagree;
- the ``asyncio.Lock`` prevents only THIS process from writing two refusals at
  once, and the state is re-read once the lock is held;
- the state row is written whether or not anything else succeeds.

**What this deliberately does NOT do.** It does not retry, sleep or queue. A
blocked ask raises ``TmdbRateLimited`` immediately and the caller keeps
everything else it gathered -- the same shape ``MDBListLimitReached`` already
has at ``facts/gather.py:104-107``. The item comes round again on the drift
sweep, which is a scheduled pass with a batch size, not a hot loop.

**And it never caches the refusal.** Roadmap row 147 records what caching one
costs: MDBList's error body was written into ``provider_cache`` and then served
as an ANSWER for the whole TTL, because the cache stores ``{"found", "payload"}``
and knows nothing about status codes. Here the 429 never reaches a cache write
at all -- ``providers/fetch.fetch_json`` writes only on a 404 and on a 2xx, and
``raise_for_status`` fires before either -- and
``tests/test_tmdb_budget.py::test_a_refusal_body_is_never_cached`` is what keeps
that true.
"""
import asyncio
import logging
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from autoposter.db.models import TmdbRateState

logger = logging.getLogger(__name__)

__all__ = ["TmdbRateBudget", "TmdbRateLimited", "retry_after_seconds"]

_STATE_ROW_ID = 1


class TmdbRateLimited(Exception):
    """TMDb refused, or is inside the window a refusal opened.

    Its own class rather than an ``httpx`` error, for ``MDBListLimitReached``'s
    reason: the gather catches it and keeps every other field it found. An
    item's critic rating must not be lost because TMDb was busy.
    """


def retry_after_seconds(response: httpx.Response) -> float | None:
    """``Retry-After`` in seconds, or None when it is absent or unreadable.

    Only the delta-seconds form is read. The HTTP-date form is legal and TMDb
    does not send it; parsing a date here would mean trusting a REMOTE clock to
    set a window this module keeps on the DATABASE clock, which is the one
    mixing of clocks the pattern exists to avoid. An unreadable value falls
    back to the configured backoff, which is the safe direction.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class TmdbRateBudget:
    """The window, read and written.

    ``backoff_seconds`` is the fallback used when TMDb's refusal carries no
    ``Retry-After``. Zero disables the window entirely -- the same off switch
    ``operations.imdb_miss_refresh_minutes`` already has, and the same meaning:
    with no window, each refusal is simply that one request's failure.
    """

    def __init__(self, session_factory, backoff_seconds: int = 60):
        self._session_factory = session_factory
        self._backoff_seconds = backoff_seconds
        self._lock = asyncio.Lock()

    async def blocked(self) -> bool:
        """Whether TMDb is inside a backoff window, per the database clock."""
        if self._backoff_seconds <= 0:
            return False
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(TmdbRateState.blocked_until, func.now()).where(
                        TmdbRateState.id == _STATE_ROW_ID
                    )
                )
            ).one_or_none()
        if row is None:
            return False
        blocked_until, now = row
        return blocked_until is not None and blocked_until > now

    async def note_refusal(self, retry_after: float | None) -> None:
        """Open (or extend) the window after a 429.

        Written unconditionally, ``_record_miss_refresh_attempt``'s reasoning:
        a provider that keeps refusing must not turn into a retry storm, so the
        window is recorded even when the caller is about to raise.
        """
        if self._backoff_seconds <= 0:
            return
        seconds = retry_after if retry_after is not None else self._backoff_seconds
        async with self._lock:
            async with self._session_factory() as session:
                # Database clock on both sides, and the interval built by
                # Postgres rather than by Python's ``timedelta``: the value has
                # to be added to ``now()`` inside the statement.
                until = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, seconds)
                stmt = insert(TmdbRateState).values(
                    id=_STATE_ROW_ID, blocked_until=until
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={"blocked_until": until, "refused_at": func.now()},
                )
                await session.execute(stmt)
                await session.commit()
        logger.warning(
            "tmdb refused with 429; not asking again for %ss", seconds
        )
```

`timedelta` is imported for symmetry with `facts/imdb.py` but is not used —
**drop the import** rather than leaving it; ruff will say so anyway.

- [ ] **Step 17: Gate the client on the budget**

In `src/autoposter/facts/tmdb_facts.py`, add the import and the constructor
argument, and rewrite `_get`:

```python
from autoposter.facts.tmdb_budget import TmdbRateBudget, TmdbRateLimited, retry_after_seconds
```

```python
    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
        budget: TmdbRateBudget | None = None,
    ):
        self._token = token
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        # None means "no shared window": every 429 is then that one request's
        # own failure, out of ``raise_for_status`` exactly as it shipped. The
        # collections CLI (``collections/__main__.py``) constructs this client
        # without one deliberately -- its single use is a `tmdb_summary:`
        # definition's overview, where a refusal is one definition's problem
        # and there is no pipeline behind it to protect.
        self._budget = budget

    async def _get(self, path: str) -> dict | None:
        url = f"{BASE_URL}{path}"
        if self._budget is not None and await self._budget.blocked():
            raise TmdbRateLimited(
                "tmdb is inside a backoff window a 429 opened; this read was "
                "not attempted. It comes round again on the next drift sweep "
                "(scheduler.drift_days), or sooner via a re-queued item; lower "
                "operations.tmdb_backoff_seconds to shorten the window"
            )
        try:
            return await fetch_json(
                method="GET",
                url=url,
                params=None,
                request=lambda: self._client.get(url, headers=self._headers()),
                cache=self._cache,
                ttl_seconds=self._cache_ttl_seconds,
            )
        except httpx.HTTPStatusError as exc:
            # 429 ONLY. Everything else keeps raising exactly as it did -- a
            # 500 is not a budget and must not open a window that stops the
            # whole library being read.
            #
            # Nothing is cached on this path, and that is the row-147 promise:
            # ``fetch_json`` writes the cache on a 404 and on a 2xx, both
            # AFTER ``raise_for_status`` would have fired, so a refusal body
            # cannot become an answer with a TTL.
            if exc.response.status_code != 429 or self._budget is None:
                raise
            await self._budget.note_refusal(retry_after_seconds(exc.response))
            # Class name only: an httpx error's ``str()`` carries the full URL.
            logger.warning("tmdb refused a read: %s", type(exc).__name__)
            raise TmdbRateLimited(
                "tmdb answered 429. The window is now open and further reads "
                "are skipped until it closes; lower "
                "operations.tmdb_backoff_seconds to shorten it"
            ) from exc
```

Add `import logging` and `logger = logging.getLogger(__name__)` at the top of
the module (it has neither today).

- [ ] **Step 18: Catch it in the gather, beside MDBList's**

In `src/autoposter/facts/gather.py`, wrap the TMDb branch of `gather_facts`:

```python
    if item.kind == "episode":
        audience = None
        try:
            if item.tmdb_id and item.season_number is not None:
                ratings = await tmdb.season_episode_ratings(item.tmdb_id, item.season_number)
                audience = ratings.get(item.episode_number)
        except TmdbRateLimited as exc:
            # The MDBList precedent, one provider along: the budget is spent,
            # and everything else this pass gathers is still good. The item is
            # re-queued by the drift sweep like any other.
            logger.warning("tmdb rate budget reached; skipping tmdb facts: %s", exc)
        if audience is not None:
            sources["audience_rating"] = "tmdb"
        facts = GatheredFacts(audience_rating=audience)
    elif item.tmdb_id:
        try:
            facts = await (tmdb.movie(item.tmdb_id) if item.kind == "movie"
                           else tmdb.show(item.tmdb_id))
            sources.update(facts.sources)
        except TmdbRateLimited as exc:
            logger.warning("tmdb rate budget reached; skipping tmdb facts: %s", exc)
```

with `from autoposter.facts.tmdb_budget import TmdbRateLimited` added to the
imports.

- [ ] **Step 19: Add the config knob and wire it up**

In `src/autoposter/config/schema.py`, inside `OperationsConfig`, after
`imdb_miss_refresh_minutes`:

```python
    # How long TMDb is left alone after it answers 429, when its own
    # ``Retry-After`` says nothing. The window lives in the database
    # (``tmdb_rate_state``) so every pod shares it -- see
    # ``facts/tmdb_budget.py``. 0 disables the shared window entirely, exactly
    # as ``imdb_miss_refresh_minutes`` above does for its cooldown, and means
    # each 429 is simply that one request's failure.
    #
    # This section owns WHEN, not WHETHER, in the split ``SchedulerConfig``'s
    # docstring states: there is no ``tmdb_budget_enabled`` beside this,
    # because 0 already means that and two spellings of one setting is one
    # spelling too many.
    tmdb_backoff_seconds: int = 60
```

In `src/autoposter/app.py`, replace the `TMDBFactsClient` construction at
`:173-176`:

```python
        app.state.tmdb_facts = TMDBFactsClient(
            secrets.tmdb_token, http, cache=cache,
            cache_ttl_seconds=config.providers.cache_ttl_seconds,
            # The shared 429 window. Built here rather than inside the client
            # because it needs a session factory and the client holds none --
            # the same reason ProviderCache is constructed here.
            budget=TmdbRateBudget(
                session_factory, config.operations.tmdb_backoff_seconds
            ),
        )
```

with `from autoposter.facts.tmdb_budget import TmdbRateBudget` added to the
imports. **`collections/__main__.py` is deliberately not changed** — the reason
is in the constructor comment written in Step 17.

- [ ] **Step 20: Run the budget tests to see them pass**

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_tmdb_budget.py tests/test_facts_gather.py tests/test_tmdb_facts.py tests/test_config_schema.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht2-green3.log"
```

Expected: PASS.

- [ ] **Step 21: Mutation proof — the never-cache-a-refusal rule**

Global constraint 9, on the rule that matters most here:

```bash
cp src/autoposter/providers/fetch.py /tmp/fetch.py.bak
# edit fetch.py: move the cache.set() call ABOVE response.raise_for_status(),
# so a 429 body is written into provider_cache
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_tmdb_budget.py::test_a_refusal_body_is_never_cached -q 2>&1 | tee /app/.superpowers/run-pprefetcht2-mutate.log"
# expected: RED
cp /tmp/fetch.py.bak src/autoposter/providers/fetch.py
cmp /tmp/fetch.py.bak src/autoposter/providers/fetch.py && echo RESTORED-EXACT
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_tmdb_budget.py -q"
# expected: GREEN
```

Paste both halves and the `RESTORED-EXACT` line.

- [ ] **Step 22: Full suite, golden gate, ruff**

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pprefetcht2-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pprefetcht2-full.log"
docker wait pprefetcht2-full
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "ruff check src tests 2>&1 | tee /app/.superpowers/run-pprefetcht2-ruff.log"
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
git diff --stat origin/main -- src/autoposter/collections/
```

Expected: suite green; ruff clean; **both `git diff --stat` outputs empty**. If
`tests/test_worker.py` fails, re-run that file alone before doing anything else
— it is roadmap row 193's known intermittent, with five sightings — and record
the sighting in the task report either way.

- [ ] **Step 23: Commit**

```bash
git add src/autoposter/facts/tmdb_budget.py src/autoposter/facts/tmdb_facts.py \
        src/autoposter/facts/gather.py src/autoposter/config/schema.py \
        src/autoposter/app.py tests/test_tmdb_budget.py
git commit --no-gpg-sign -m "feat(facts): a shared TMDb backoff window on the database clock"
```

- [ ] **Step 24: Tear down**

```bash
docker compose -p pprefetcht2 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## Task 3a: The facts enumeration seam, and one collection built from it

**Files:**
- Create: `src/autoposter/collections/facts_enumeration.py`
- Create: `src/autoposter/collections/builders/facts_value.py`
- Modify: `src/autoposter/collections/builders/base.py:112-149` (`BuilderContext`)
- Modify: `src/autoposter/collections/engine.py:325-334`
- Modify: `src/autoposter/collections/builders/__init__.py`
- Test: new `tests/test_collection_facts_enumeration.py`, new
  `tests/test_collection_builder_facts_value.py`

**Interfaces:**
- Consumes: Task 2's three `ItemFacts` columns and the `facts_attempted_at`
  stamp.
- Produces, for Task 3b:
  - `collections/facts_enumeration.py`:
    - `FactsField` (frozen dataclass): `name: str`, `column: str`,
      `multi: bool`, `kinds: tuple[str, ...]`, `note: str`
    - `FACTS_FIELDS: dict[str, FactsField]` with keys
      `"origin_country"`, `"original_language"`, `"tmdb_collection"`
    - `async enumerate_values(session, field: FactsField, *, library: str,
      library_type: str) -> list[tuple[str, int]]` — `(value, item_count)`,
      most-populated first, ties broken by value ascending
    - `async items_with_values(session, field: FactsField, values:
      Sequence[str], *, library: str, library_type: str) -> list[str]` — Plex
      rating keys, title-ascending
    - `async coverage(session, *, library: str, library_type: str) ->
      tuple[int, int]` — `(items whose facts were attempted, items in the
      library)`
  - `collections/builders/facts_value.py`: builder `type_name = "facts_value"`,
    `params_model = FactsValueParams(field: str, values: list[str])`
  - `BuilderContext.session: AsyncSession | None = None`

**Background — why the session is a new field and not a workaround.** A list
builder answers with external ids and reaches nothing but its own client
(`builders/base.py:112-141`). Every facts-enumerated family is a DB read: the
values live in `item_facts` and the membership is the join back to
`media_items`. Smart builders already get a session (`SmartContext.session`,
`:201`) — that asymmetry is the gap, not a rule, and the engine has the session
in hand at the construction site (`engine.py:325-334`, inside `run_library`
whose first parameter it is). One optional field, defaulted to `None`, changes
nothing for the twenty-odd builders that ignore it.

**Why `("plex", rating_key)` and not a TMDb id.** `media_items.rating_key` IS
the identity of an owned item, which is what makes `plex` a namespace at all
(`collections/ids.py:13-16`) and `plex_id` the trivial builder. Producing tmdb
ids here would mean the resolver looking up, by external id, rows this query
already holds — and would silently drop every item whose `tmdb_id` is NULL,
which is a plausible wrong membership rather than a visible failure.

- [ ] **Step 1: Write the failing enumeration tests**

Create `tests/test_collection_facts_enumeration.py`:

```python
"""The DB-backed enumeration seam (adjudication C6).

The ten shipped dynamic types enumerate through Plex's ``listFilterChoices``
(``builders/plex_search.LibraryTagResolver``). These values are not in Plex at
all -- ``origin_country`` is TMDb's field and Plex has no equivalent, which is
the whole of roadmap row 189 -- so they are enumerated from the facts this
service already stores.

The missing-value rule row 156 predicted would "do most of the work" is the
shape of every query here: an item with no ``item_facts`` row, or with a NULL /
empty value in the column, is simply ABSENT from the enumeration. It is never a
bucket, never a zero and never an error.
"""
import pytest
from sqlalchemy import select

from autoposter.collections.facts_enumeration import (
    FACTS_FIELDS,
    coverage,
    enumerate_values,
    items_with_values,
)
from autoposter.db.models import ItemFacts, MediaItem


async def _item(session, rating_key, *, library="Movies", kind="movie",
                title="X", **facts):
    item = MediaItem(rating_key=rating_key, library=library, kind=kind, title=title)
    session.add(item)
    await session.flush()
    if facts:
        session.add(ItemFacts(item_id=item.id, **facts))
        await session.flush()
    return item


async def test_a_scalar_column_enumerates_its_distinct_values(session):
    await _item(session, "a", tmdb_original_language="en")
    await _item(session, "b", tmdb_original_language="en")
    await _item(session, "c", tmdb_original_language="fi")
    values = await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="Movies", library_type="Movie",
    )
    assert values == [("en", 2), ("fi", 1)]


async def test_an_array_column_enumerates_each_element(session):
    """A co-production carries several origin countries and belongs in each
    bucket -- which is why the column is a JSONB array and the query unnests
    it rather than grouping on the whole array."""
    await _item(session, "a", tmdb_origin_country=["US", "GB"])
    await _item(session, "b", tmdb_origin_country=["US"])
    values = await enumerate_values(
        session, FACTS_FIELDS["origin_country"],
        library="Movies", library_type="Movie",
    )
    assert values == [("US", 2), ("GB", 1)]


async def test_an_item_with_no_facts_row_is_simply_absent(session):
    """The missing-value rule. Not a bucket, not a zero, not an error -- the
    enumeration reflects what the facts pipeline has VISITED, and the pack
    descriptions state that convergence story rather than hiding it."""
    await _item(session, "seen", tmdb_original_language="en")
    await _item(session, "never-fetched")
    values = await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="Movies", library_type="Movie",
    )
    assert values == [("en", 1)]


async def test_a_null_or_empty_value_is_absent_too(session):
    await _item(session, "null", tmdb_original_language=None)
    await _item(session, "empty-array", tmdb_origin_country=[])
    assert await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="Movies", library_type="Movie",
    ) == []
    assert await enumerate_values(
        session, FACTS_FIELDS["origin_country"],
        library="Movies", library_type="Movie",
    ) == []


async def test_another_librarys_items_are_not_enumerated(session):
    """``media_items.library`` is the Plex section title (``plex/client.py:265``)
    and so is the engine's ``library`` -- a second Movie library must not
    contribute values to this one's family."""
    await _item(session, "here", tmdb_original_language="en")
    await _item(session, "there", library="4K Movies", tmdb_original_language="fi")
    values = await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="Movies", library_type="Movie",
    )
    assert values == [("en", 1)]


async def test_a_show_library_enumerates_shows_and_not_episodes(session):
    """Episodes carry a rating and nothing else -- ``gather_facts`` gives an
    episode only the two ratings and a season nothing at all -- so a family on
    a Show library is a family of SHOWS."""
    await _item(session, "s", library="TV", kind="show", tmdb_original_language="ja")
    await _item(session, "e", library="TV", kind="episode", tmdb_original_language="ja")
    values = await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="TV", library_type="Show",
    )
    assert values == [("ja", 1)]


async def test_the_franchise_field_enumerates_ids_as_strings(session):
    """``derive_keys`` matches ``include``/``exclude``/``addons`` as strings
    (its ``_strlist``), so the key column is a string here too -- an operator's
    ``exclude: [1241]`` is YAML integers and would otherwise never meet it."""
    await _item(session, "a", tmdb_collection_id=1241)
    await _item(session, "b", tmdb_collection_id=1241)
    await _item(session, "c", tmdb_collection_id=87096)
    values = await enumerate_values(
        session, FACTS_FIELDS["tmdb_collection"],
        library="Movies", library_type="Movie",
    )
    assert values == [("1241", 2), ("87096", 1)]


async def test_membership_is_every_item_carrying_any_of_the_values(session):
    """``DynamicKey.values`` is the key plus every addon member the library
    carries, so membership is an OR over the list -- the same ``any:`` base the
    smart families emit."""
    await _item(session, "us", tmdb_origin_country=["US"], title="A")
    await _item(session, "gb", tmdb_origin_country=["GB"], title="B")
    await _item(session, "fi", tmdb_origin_country=["FI"], title="C")
    keys = await items_with_values(
        session, FACTS_FIELDS["origin_country"], ["US", "GB"],
        library="Movies", library_type="Movie",
    )
    assert keys == ["us", "gb"]


async def test_membership_of_no_values_is_no_items(session):
    """A filter with no terms matching the whole library is the failure
    ``builders/dynamic.py`` refuses by name; here it is refused by returning
    nothing, and the builder turns that into its own refusal."""
    await _item(session, "us", tmdb_origin_country=["US"])
    assert await items_with_values(
        session, FACTS_FIELDS["origin_country"], [],
        library="Movies", library_type="Movie",
    ) == []


async def test_coverage_counts_attempts_and_not_rows(session):
    """The convergence story, measurable. ``facts_attempted_at`` is stamped by
    every ``persist_facts`` call (C4), so an item TMDb had nothing for counts
    as VISITED -- which is exactly the honesty the pack descriptions need."""
    seen = await _item(session, "a", tmdb_original_language="en")
    looked = await _item(session, "b")
    await _item(session, "c")
    from sqlalchemy import func, update
    await session.execute(
        update(MediaItem)
        .where(MediaItem.id.in_([seen.id, looked.id]))
        .values(facts_attempted_at=func.now())
    )
    assert await coverage(session, library="Movies", library_type="Movie") == (2, 3)
```

- [ ] **Step 2: Run them to see them fail**

```bash
docker compose -p pprefetcht3a -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_facts_enumeration.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht3a-red1.log"
```

Expected: FAIL — `ModuleNotFoundError: No module named
'autoposter.collections.facts_enumeration'`.

- [ ] **Step 3: Write the enumeration module**

Create `src/autoposter/collections/facts_enumeration.py`:

```python
"""What the facts pipeline knows, as an enumeration a collection family can use.

**Why this exists at all.** Phase 10a's dynamic engine enumerates through
Plex's ``listFilterChoices`` -- one round trip, and the values are the
library's own tags. Three of Kometa's dynamic types cannot be served that way,
and roadmap rows 189 and 192 say why in the same words: ``origin_country`` and
``original_language`` are TMDb's fields and Plex has no equivalent (its own
``<Country>`` is the PRODUCTION country, which the shipped ``country`` type
already enumerates and which disagrees with TMDb's on exactly the
co-productions an operator would notice), and ``tmdb_collection`` is a walk
over every item's ``belongs_to_collection``. Upstream answers all three with a
full-library TMDb walk. This service already HAS that walk -- the facts
pipeline reads ``/movie/{id}`` and ``/tv/{id}`` for every item and, since this
phase, keeps the three fields off the payload it was fetching anyway. So the
enumeration is a database read, not a network one.

**The missing-value rule, which is most of the design.** Row 156 predicted it:
"the missing-value rule would do most of the work". An item with no
``item_facts`` row, or with a NULL scalar or an empty array in the column, is
ABSENT from every query here. It is never a bucket, never a zero, never an
error. That single rule is what makes a partially-visited library produce a
correct-but-incomplete family rather than a wrong one.

**Coverage is honest, not hidden.** The enumeration reflects only the items the
facts pipeline has visited. ``coverage`` measures exactly that -- attempts
against library size -- so a pack description can state the convergence story
in numbers an operator can check, and the drift sweep (500 items a week by
default, ``scheduler.drift_batch_size``) is what closes the gap over time.

Pure SQL and nothing else: no Plex, no HTTP, no config. Everything here takes a
session and returns data, which is what lets the whole of it be proven against
a real Postgres in ``tests/test_collection_facts_enumeration.py``.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemFacts, MediaItem

__all__ = [
    "FACTS_FIELDS",
    "FactsField",
    "coverage",
    "enumerate_values",
    "items_with_values",
]

# Our library-type spelling to the ``media_items.kind`` rows a family is built
# from. A Show family is a family of SHOWS: ``gather_facts`` gives an episode
# only its two ratings and a season nothing at all, so no other kind carries
# the columns below.
_KINDS: dict[str, tuple[str, ...]] = {"Movie": ("movie",), "Show": ("show",)}


@dataclass(frozen=True)
class FactsField:
    """One ``item_facts`` column a family can be enumerated from.

    ``column`` is the attribute name on ``ItemFacts`` -- ours, never Kometa's
    (row 156's naming law) -- and ``name`` is the short word this service uses
    for the field in a params block and in a type row.

    ``multi`` says the column is a JSONB ARRAY rather than a scalar, which
    changes both queries: the enumeration unnests it and the membership test is
    ``jsonb_exists`` rather than ``=``. One flag rather than two field classes,
    because everything else about the two shapes is identical.

    ``kinds`` is which library types the field can be enumerated on, and it is
    the field's own property rather than a type row's: TMDb has no
    ``belongs_to_collection`` for a series, so ``tmdb_collection`` is
    movie-only wherever it is used.
    """

    name: str
    column: str
    multi: bool
    kinds: tuple[str, ...]
    note: str


FACTS_FIELDS: dict[str, FactsField] = {
    field.name: field
    for field in (
        FactsField(
            "origin_country", "tmdb_origin_country", True, ("Movie", "Show"),
            "TMDb's own origin country, an ISO-3166-1 code, and a LIST -- a "
            "co-production carries several and belongs in each bucket. NOT "
            "Plex's `<Country>`, which is the production country the shipped "
            "`country` dynamic type already enumerates: roadmap row 189 rules "
            "the substitution out by name, because the two disagree on exactly "
            "the co-productions an operator would notice.",
        ),
        FactsField(
            "original_language", "tmdb_original_language", False, ("Movie", "Show"),
            "TMDb's `original_language`, an ISO-639-1 code. Titled from the "
            "CODE: neither this service nor Kometa's pack files carry a "
            "code->name table (roadmap row 190 records the same absence for the "
            "audio/subtitle language families, where Plex at least supplies a "
            "display title and here nothing does).",
        ),
        FactsField(
            "tmdb_collection", "tmdb_collection_id", False, ("Movie",),
            "`belongs_to_collection.id`. Movie-only, and not by policy: TMDb "
            "collections ARE movie franchises and their `parts` are movies "
            "(`builders/tmdb.TmdbCollectionBuilder`). Enumerated as the id "
            "stringified, because `dynamic_keys._strlist` matches every "
            "narrowing entry as a string.",
        ),
    )
}


def _column(field: FactsField):
    return getattr(ItemFacts, field.column)


def _scope(field: FactsField, library: str, library_type: str):
    """The joins and predicates every query here shares.

    Library-scoped because ``media_items`` holds every library at once and a
    second Movie section's values are not this one's -- the same narrowing
    ``engine.definition_titles_for`` applies for the same reason.
    """
    return (
        MediaItem.library == library,
        MediaItem.kind.in_(_KINDS.get(library_type, ())),
    )


async def enumerate_values(
    session: AsyncSession, field: FactsField, *, library: str, library_type: str
) -> list[tuple[str, int]]:
    """``(value, item_count)`` for one field, most-populated first.

    Ties break on the value ascending so the order is total and a family's
    collections are created in a stable order between passes -- unlike the
    Plex-backed enumeration, whose order is the server's own and is preserved
    for that reason, this order is ours and has to be decided.

    The COUNT rides along because it is free here (the GROUP BY is already
    running) and because it is the only thing a caller could use to bound a
    family without a second query. Nothing in this phase uses it to filter;
    it is reported.
    """
    column = _column(field)
    if field.multi:
        # jsonb_array_elements_text as a FROM item. Postgres allows a
        # set-returning function in FROM to reference an earlier FROM item
        # without the LATERAL keyword, so no `.lateral()` is needed.
        elements = func.jsonb_array_elements_text(column).table_valued("value")
        value = elements.c.value
        stmt = (
            select(value, func.count())
            .select_from(ItemFacts)
            .join(MediaItem, MediaItem.id == ItemFacts.item_id)
            .join(elements, true())
            .where(*_scope(field, library, library_type))
            .group_by(value)
            .order_by(func.count().desc(), value.asc())
        )
    else:
        stmt = (
            select(column, func.count())
            .select_from(ItemFacts)
            .join(MediaItem, MediaItem.id == ItemFacts.item_id)
            .where(*_scope(field, library, library_type), column.isnot(None))
            .group_by(column)
            .order_by(func.count().desc(), column.asc())
        )
    rows = (await session.execute(stmt)).all()
    return [(str(value), int(count)) for value, count in rows if str(value)]


async def items_with_values(
    session: AsyncSession,
    field: FactsField,
    values: Sequence[str],
    *,
    library: str,
    library_type: str,
) -> list[str]:
    """The Plex rating keys of every item carrying ANY of ``values``.

    An OR over the list, because a bucket's values are its own key plus every
    addon member the library carries (``dynamic_keys.DynamicKey.values``) --
    the same ``any:`` base the smart families emit, and for the same reason:
    ``all:`` would ask for an item that is at once American and British.

    Ordered by title so a collection's custom order is stable and readable.
    An empty ``values`` returns nothing rather than everything: a membership
    query with no terms would match the whole library, which is the accident
    ``builders/dynamic.py`` refuses by name.
    """
    if not values:
        return []
    column = _column(field)
    if field.multi:
        matches = func.jsonb_exists_any(column, list(values))
    else:
        matches = column.in_([str(one) for one in values])
    stmt = (
        select(MediaItem.rating_key)
        .select_from(ItemFacts)
        .join(MediaItem, MediaItem.id == ItemFacts.item_id)
        .where(*_scope(field, library, library_type), matches)
        .order_by(MediaItem.title.asc(), MediaItem.rating_key.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def coverage(
    session: AsyncSession, *, library: str, library_type: str
) -> tuple[int, int]:
    """``(items whose facts have been attempted, items in the library)``.

    The attempt, not the row: ``persist_facts`` stamps
    ``media_items.facts_attempted_at`` on every call including an empty gather
    (adjudication C4), so an item TMDb has nothing for counts as VISITED. That
    is the whole difference between "this family has enumerated 40% of the
    library" and "60% of the library has no origin country", which are very
    different sentences and only one of them is true.
    """
    kinds = _KINDS.get(library_type, ())
    total = (
        await session.execute(
            select(func.count())
            .select_from(MediaItem)
            .where(MediaItem.library == library, MediaItem.kind.in_(kinds))
        )
    ).scalar_one()
    attempted = (
        await session.execute(
            select(func.count())
            .select_from(MediaItem)
            .where(
                MediaItem.library == library,
                MediaItem.kind.in_(kinds),
                MediaItem.facts_attempted_at.isnot(None),
            )
        )
    ).scalar_one()
    return int(attempted), int(total)
```

**One implementation note for whoever writes this.**

`func.jsonb_exists_any(column, list(values))` renders
   `jsonb_exists_any(col, ARRAY[…])`, which is Postgres's function form of the
   `?|` operator. The function form is used deliberately: `?` and `?|` collide
   with parameter markers in some drivers, and a function call has no such
   ambiguity. If the generated SQL does not bind the list as a text array,
   write it as
   `column.op("?|")(sqlalchemy.cast(list(values), postgresql.ARRAY(sqlalchemy.Text)))`
   and record which form was used in the task report — but try the function
   form first and prove it with the test above.

- [ ] **Step 4: Run the enumeration tests to see them pass**

```bash
docker compose -p pprefetcht3a -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_facts_enumeration.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht3a-green1.log"
```

Expected: PASS, all eleven.

- [ ] **Step 5: Commit the seam**

```bash
git add src/autoposter/collections/facts_enumeration.py tests/test_collection_facts_enumeration.py
git commit --no-gpg-sign -m "feat(collections): a DB-backed enumeration over the facts pipeline's TMDb fields"
```

- [ ] **Step 6: Write the failing builder tests**

Create `tests/test_collection_builder_facts_value.py`:

```python
"""``facts_value``: one collection whose membership is a facts column.

The single-purpose half of the facts families -- one collection, one field, one
or more values -- and the builder each expanded unit of an ``origin_country``
or ``original_language`` family runs. Useful on its own: an operator who wants
exactly "the Finnish films" writes this and nothing else.
"""
import pytest

from autoposter.collections.builders.base import BuilderContext
from autoposter.collections.builders.facts_value import FactsValueBuilder, FactsValueParams
from autoposter.db.models import ItemFacts, MediaItem


async def _item(session, rating_key, *, library="Movies", kind="movie", title="X",
                **facts):
    item = MediaItem(rating_key=rating_key, library=library, kind=kind, title=title)
    session.add(item)
    await session.flush()
    if facts:
        session.add(ItemFacts(item_id=item.id, **facts))
        await session.flush()
    return item


async def test_it_builds_the_items_carrying_the_value(session):
    await _item(session, "10", tmdb_origin_country=["FI"], title="A")
    await _item(session, "11", tmdb_origin_country=["US"], title="B")
    result = await FactsValueBuilder().build(BuilderContext(
        library="Movies", library_type="Movie", session=session,
        config={"field": "origin_country", "values": ["FI"]},
    ))
    assert result.ids == [("plex", "10")]


async def test_a_rating_key_is_the_identity_and_needs_no_lookup(session):
    """``plex`` is a namespace exactly because a rating key IS an owned item's
    identity (``collections/ids.py:13-16``). Producing tmdb ids here would ask
    the resolver to look up rows this query already has -- and would silently
    drop every item whose ``tmdb_id`` is NULL, which is a plausible wrong
    membership rather than a visible failure."""
    await _item(session, "42", tmdb_original_language="fi", title="A")
    result = await FactsValueBuilder().build(BuilderContext(
        library="Movies", library_type="Movie", session=session,
        config={"field": "original_language", "values": ["fi"]},
    ))
    assert result.ids == [("plex", "42")]


async def test_several_values_are_an_or(session):
    await _item(session, "1", tmdb_origin_country=["FI"], title="A")
    await _item(session, "2", tmdb_origin_country=["SE"], title="B")
    await _item(session, "3", tmdb_origin_country=["US"], title="C")
    result = await FactsValueBuilder().build(BuilderContext(
        library="Movies", library_type="Movie", session=session,
        config={"field": "origin_country", "values": ["FI", "SE"]},
    ))
    assert result.ids == [("plex", "1"), ("plex", "2")]


async def test_a_field_this_service_does_not_enumerate_is_refused_at_config_load():
    with pytest.raises(ValueError) as caught:
        FactsValueParams.model_validate({"field": "nonsense", "values": ["x"]})
    assert "origin_country" in str(caught.value)


async def test_a_field_the_library_type_cannot_carry_is_refused(session):
    """A ``tmdb_collection`` family on a Show library would match nothing at
    all, and "matched nothing" looks exactly like a correct collection of
    titles the library does not own -- ``require_library_type``'s whole
    reason."""
    from autoposter.collections.builders.base import LibraryTypeMismatch

    with pytest.raises(LibraryTypeMismatch):
        await FactsValueBuilder().build(BuilderContext(
            library="TV", library_type="Show", session=session,
            config={"field": "tmdb_collection", "values": ["1241"]},
        ))


async def test_no_session_raises_rather_than_building_an_empty_collection(session):
    """``build`` RAISES on failure -- returning an empty list would be read one
    layer down as "make no changes" (``builders/base.py``'s first rule), so a
    context with no session must not look like a library with no Finnish
    films."""
    with pytest.raises(ValueError):
        await FactsValueBuilder().build(BuilderContext(
            library="Movies", library_type="Movie",
            config={"field": "origin_country", "values": ["FI"]},
        ))


async def test_an_empty_values_list_is_refused_at_config_load():
    """A membership query with no terms matches the whole library."""
    with pytest.raises(ValueError):
        FactsValueParams.model_validate({"field": "origin_country", "values": []})
```

- [ ] **Step 7: Run them to see them fail**

```bash
docker compose -p pprefetcht3a -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_builder_facts_value.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht3a-red2.log"
```

Expected: FAIL — no such module, and `BuilderContext` has no `session`.

- [ ] **Step 8: Give `BuilderContext` a session**

In `src/autoposter/collections/builders/base.py`, add the field at the end of
`BuilderContext` and one paragraph to its docstring:

```python
    library: str
    library_type: str
    http: httpx.AsyncClient | None = None
    config: dict[str, Any] = field(default_factory=dict)
    cache: ProviderCache | None = None
    run_cache: dict[str, Any] = field(default_factory=dict)
    sources: SourceClients = field(default_factory=SourceClients)
    session: AsyncSession | None = None
```

and, above "Still absent: application config, raw secrets…":

```
    ``session`` is this service's own database, read-only by convention and by
    every use of it: the facts builders' membership IS a query over
    ``item_facts`` joined back to ``media_items``, because the values they build
    on are TMDb's and Plex holds none of them (roadmap rows 189/192). A smart
    builder has had one since 9c (``SmartContext.session``); the asymmetry was a
    gap rather than a rule, and the engine has the session in hand at the
    construction site. None for a direct caller with no database, and a builder
    that needs one says so by raising -- ``build`` raises on failure, so a
    missing session must never be answered with an empty membership.
```

`AsyncSession` is already imported in that module (`:41`).

In `src/autoposter/collections/engine.py`, at the `BuilderContext` construction
(`:326-334`), add the one line:

```python
        return BuilderContext(
            library=library,
            library_type=library_type,
            http=http,
            config=definition.params,
            cache=cache,
            run_cache=run_cache,
            sources=bound_sources,
            session=session,
        )
```

- [ ] **Step 9: Write the builder**

Create `src/autoposter/collections/builders/facts_value.py`:

```python
"""``facts_value``: the items whose stored TMDb facts carry a given value.

The narrowest possible use of the enumeration seam, and the membership half of
every ``origin_country``/``original_language`` family: one collection, one
field, one or more values. An operator who wants exactly "the Finnish films"
writes this directly; a family writes one of these per key.

**Why a LIST builder and not a smart one.** A smart collection's membership is
a Plex filter Plex evaluates live, and Plex has no ``origin_country`` field at
all -- that absence IS roadmap row 189. So there is no query to store on the
collection, and the membership has to be resolved here and applied as a list.
That is the whole of the "third family shape": ``cs_bucket`` is a smart family,
``dynamic`` is a smart family, and this is a list family.

**What that costs, stated rather than discovered.** A list collection's
membership is refreshed when a pass runs, not continuously -- so a film whose
facts arrive after this pass joins its collection on the next one. The drift
sweep is what makes that converge (``scheduler.drift_days``, 500 items a week
by default), and the pack descriptions say so in the operator's own words.
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.collections.facts_enumeration import FACTS_FIELDS, items_with_values

__all__ = ["FactsValueBuilder", "FactsValueParams"]


class FactsValueParams(BaseModel):
    """Which stored field, and which of its values.

    ``extra="forbid"`` so ``value:`` for ``values:`` is an error rather than a
    silently-ignored key, and ``coerce_numbers_to_str`` because a TMDb
    collection id is written as a YAML integer and every value here is matched
    as a string.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    field: str
    values: list[str] = Field(min_length=1)

    @field_validator("field")
    @classmethod
    def _a_field_this_service_enumerates(cls, value: str) -> str:
        if value not in FACTS_FIELDS:
            raise ValueError(
                "%r is not a facts field this service enumerates. Options: %s. "
                "These are the TMDb fields the facts pipeline stores; a Plex "
                "tag is a `smart_filter` or a `dynamic` family instead -- see "
                "`collections/facts_enumeration.py`'s module docstring"
                % (value, ", ".join(sorted(FACTS_FIELDS)))
            )
        return value


class FactsValueBuilder:
    """One collection, from one facts column."""

    type_name = "facts_value"
    params_model = FactsValueParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = FactsValueParams.model_validate(ctx.config)
        field = FACTS_FIELDS[params.field]
        require_library_type(
            "the 'facts_value' builder's %r field" % field.name,
            ctx.library_type, field.kinds,
        )
        if ctx.session is None:
            # Raise, never return empty: an empty membership is read one layer
            # down as "make no changes" (``builders/base.py``'s first rule), so
            # a context with no database would silently look like a library
            # with no matching items.
            raise ValueError(
                "the 'facts_value' builder reads this service's own database "
                "and was given no session. Every engine path supplies one; a "
                "direct caller has to pass `session=` on the BuilderContext"
            )
        keys = await items_with_values(
            ctx.session, field, params.values,
            library=ctx.library, library_type=ctx.library_type,
        )
        return BuilderResult(ids=[("plex", key) for key in keys])
```

Register it in `src/autoposter/collections/builders/__init__.py` beside the
others, following that file's existing shape exactly (import the class, call
`register(FactsValueBuilder())`, add the name to whatever `__all__` or ordering
list the module keeps).

- [ ] **Step 10: Run the builder tests to see them pass**

```bash
docker compose -p pprefetcht3a -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_builder_facts_value.py tests/test_collection_builders.py tests/test_collection_engine.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht3a-green2.log"
```

Expected: PASS. The registry and engine tests are included because a new
registry entry is exactly what a duplicate-`type_name` or a builder-count
assertion would catch.

- [ ] **Step 11: Prove the ten dynamic types did not move**

```bash
docker compose -p pprefetcht3a -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_dynamic_types.py tests/test_collection_dynamic.py tests/test_collection_dynamic_oracle.py tests/test_collection_cs_equivalence.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht3a-dynamic.log"
git diff --stat origin/main -- src/autoposter/collections/dynamic_types.py \
  src/autoposter/collections/dynamic_keys.py src/autoposter/collections/dynamic_titles.py \
  src/autoposter/collections/builders/dynamic.py
```

Expected: green, and the diff **empty**. Global constraint 2, checked rather
than assumed.

- [ ] **Step 12: Full suite, ruff, golden gate, commit**

```bash
docker compose -p pprefetcht3a -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pprefetcht3a-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pprefetcht3a-full.log"
docker wait pprefetcht3a-full
docker compose -p pprefetcht3a -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "ruff check src tests"
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
git add src/autoposter/collections/builders/facts_value.py \
        src/autoposter/collections/builders/base.py \
        src/autoposter/collections/builders/__init__.py \
        src/autoposter/collections/engine.py \
        tests/test_collection_builder_facts_value.py
git commit --no-gpg-sign -m "feat(collections): facts_value, a collection built from the stored TMDb facts"
docker compose -p pprefetcht3a -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

The golden diff must be empty.

---

## Task 3b: The facts families

> **C7's honesty valve, and it is this task's own header rather than a footnote.**
> This task builds a THIRD family shape. If it does not fit — if the sweep
> protocol, the label inheritance or the name resolution turns out to need
> engine surgery beyond the two additive fields this plan specifies — then
> **stop, report, and ship Task 3a's `facts_value` builder alone**. Task 4 then
> re-files all three packs with that reason in their rows, Task 5 files the
> family as its own roadmap row pointing at what was learned, and rows 189/192
> stay open with an honest note instead of being closed by something that
> half-works. Forcing it is the failure mode; re-filing is not.

**Files:**
- Create: `src/autoposter/collections/facts_family.py`
- Create: `src/autoposter/collections/builders/facts_family.py`
- Modify: `src/autoposter/collections/builders/base.py` (`BuilderContext.definition`)
- Modify: `src/autoposter/collections/engine.py:325-334`
- Modify: `src/autoposter/providers/tmdb_lists.py` (add `collection_name`)
- Modify: `src/autoposter/collections/dynamic_types.py` (module docstring only)
- Modify: `src/autoposter/collections/builders/__init__.py`
- Test: new `tests/test_collection_facts_family.py`, and
  `tests/test_tmdb_lists_client.py`

**Interfaces:**
- Consumes: `FACTS_FIELDS`, `enumerate_values`, `coverage`,
  `FactsValueBuilder`'s `type_name` and params shape (all Task 3a);
  `TmdbCollectionBuilder`'s `type_name` and its `{"id": int}` params
  (`builders/tmdb.py:257-269`, shipped); `derive_keys` and `family_titles`
  (phase 10a, unchanged); Task 1 §4's three verdict lines.
- Produces, for Task 4:
  - `collections/facts_family.py`: `FactsFamilyType` (frozen dataclass:
    `name`, `field`, `member_builder`, `key_from`, `title_format`, `note`) and
    `FACTS_FAMILY_TYPES: dict[str, FactsFamilyType]` with keys
    `"origin_country"`, `"original_language"`, `"tmdb_collection"`
  - `collections/builders/facts_family.py`: builder `type_name =
    "facts_family"`, `params_model = FactsFamilyParams`, module constant
    `FAMILY_LABEL_PREFIX = "autoposter-facts: "`, module functions
    `family_label(definition)` and `generated_titles(run_cache, definition)`
  - `FactsFamilyParams` fields, which are the ones a pack's `params` tuple
    fills: `type`, `include`, `exclude`, `addons`, `custom_keys`,
    `key_name_override`, `title_override`, `remove_prefix`, `remove_suffix`,
    `title_format`, `other_name`, `max_collections`
  - `BuilderContext.definition: Any = None`

**Background — the shape, in one paragraph.** `expand` enumerates the field
from the database, runs the enumeration through `derive_keys` (unchanged) and
`family_titles` (unchanged), and returns one `CollectionDefinition` per surviving
key. Each unit names a MEMBERSHIP builder: `facts_value` for the two
facts-backed fields, and the already-shipped `tmdb_collection` for franchises,
whose `collection_parts` is a better membership than "the items we happen to
have fetched" because it is the franchise's own part list. The family joins the
delete sweep through the two methods `builders/dynamic.py:533-551` names as the
protocol. Nothing about phase 10a's key derivation or title machinery changes:
`derive_keys` takes the enumeration as data and `family_titles` takes
`DerivedKeys`, and neither has ever known where the values came from.

- [ ] **Step 1: Read Task 1 §4's three verdict lines**

`FRANCHISE KEY`, `REGION MEMBERS`, `CONTINENT MEMBERS`. They decide one line in
this task (`FactsFamilyType.key_from` for the franchise row) and the tables in
Task 4. Paste them into this task's report before writing any code. If §4 is
missing or its verdicts are absent, STOP — this task cannot invent them.

- [ ] **Step 2: Write the failing family tests**

Create `tests/test_collection_facts_family.py`:

```python
"""``facts_family``: one collection per value the STORED FACTS hold.

The third family shape. ``cs_bucket`` manages a family of SMART collections
from a static table; ``dynamic`` manages a family of SMART collections from a
Plex enumeration; this manages a family of LIST collections from a database
enumeration -- because the values are TMDb's and Plex holds none of them
(roadmap rows 189/192).

Everything about WHICH keys become collections and what each is CALLED is phase
10a's, unchanged and untouched: ``dynamic_keys.derive_keys`` takes the
enumeration as data, and ``dynamic_titles.family_titles`` takes what it
returns. Neither has ever known where the values came from, which is exactly
what makes this family shape cheap.
"""
import httpx
import pytest

from autoposter.collections.builders.base import BuilderContext
from autoposter.collections.builders.facts_family import (
    FactsFamilyBuilder,
    FactsFamilyParams,
    family_label,
    generated_titles,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ItemFacts, MediaItem


async def _item(session, rating_key, *, library="Movies", kind="movie", title="X",
                **facts):
    item = MediaItem(rating_key=rating_key, library=library, kind=kind, title=title)
    session.add(item)
    await session.flush()
    if facts:
        session.add(ItemFacts(item_id=item.id, **facts))
        await session.flush()
    return item


def _definition(**kwargs):
    base = dict(
        title="Countries of origin", builder="facts_family",
        params={"type": "origin_country"},
    )
    base.update(kwargs)
    return CollectionDefinition(**base)


def _ctx(session, definition, **kwargs):
    base = dict(
        library="Movies", library_type="Movie", session=session,
        definition=definition, config=definition.params, run_cache={},
    )
    base.update(kwargs)
    return BuilderContext(**base)


async def test_one_unit_per_enumerated_value(session):
    await _item(session, "1", tmdb_origin_country=["US"])
    await _item(session, "2", tmdb_origin_country=["FI"])
    definition = _definition()
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert [unit.title for unit in units] == ["US", "FI"]
    assert {unit.builder for unit in units} == {"facts_value"}
    assert units[0].params == {"field": "origin_country", "values": ["US"]}


async def test_addons_merge_exactly_as_they_do_for_a_smart_family(session):
    """``derive_keys`` is phase 10a's, unchanged: an addon key the library does
    not itself hold becomes a synthetic bucket over the members it does hold,
    which is what makes ``region.yml``'s 'Nordic' a collection at all."""
    await _item(session, "1", tmdb_origin_country=["FI"])
    await _item(session, "2", tmdb_origin_country=["SE"])
    definition = _definition(params={
        "type": "origin_country",
        "include": ["Nordic"],
        "addons": {"Nordic": ["FI", "SE", "NO"]},
    })
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert [unit.title for unit in units] == ["Nordic"]
    # NO is not in the library, so it is not asked for -- ``derive_keys``
    # drops an absent addon member at :144 and again at :163.
    assert units[0].params["values"] == ["FI", "SE"]


async def test_a_franchise_family_delegates_membership_to_the_shipped_builder(session):
    """The franchise family's members are the FRANCHISE's parts, not the items
    this service happens to have fetched -- ``tmdb_collection`` shipped in 8b
    and reads ``/collection/{id}``'s own ``parts``. The enumeration says WHICH
    franchises the library is in; the builder says what is in each."""
    await _item(session, "1", tmdb_collection_id=1241)

    def handler(request):
        return httpx.Response(200, json={
            "id": 1241, "name": "Harry Potter Collection", "parts": [{"id": 671}],
        })

    definition = _definition(title="Franchises", params={"type": "tmdb_collection"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        from autoposter.collections.builders.sources_bundle import SourceClients
        from autoposter.providers.tmdb_lists import TmdbListClient
        sources = SourceClients(tmdb=TmdbListClient("tok", http))
        units = await FactsFamilyBuilder().expand(
            _ctx(session, definition, sources=sources, http=http)
        )

    assert [unit.title for unit in units] == ["Harry Potter"]
    assert units[0].builder == "tmdb_collection"
    assert units[0].params == {"id": 1241}


async def test_a_franchise_tmdb_cannot_name_is_dropped_and_reported(session):
    """A franchise whose name will not resolve has no title, and a collection
    titled from an id is nobody's ask. Dropped, and named in the report -- the
    silent variant is what an operator cannot see."""
    await _item(session, "1", tmdb_collection_id=1241)

    def handler(request):
        return httpx.Response(404, json={})

    definition = _definition(title="Franchises", params={"type": "tmdb_collection"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        from autoposter.collections.builders.sources_bundle import SourceClients
        from autoposter.providers.tmdb_lists import TmdbListClient
        sources = SourceClients(tmdb=TmdbListClient("tok", http))
        ctx = _ctx(session, definition, sources=sources, http=http)
        units = await FactsFamilyBuilder().expand(ctx)

    assert units == []
    assert any("1241" in note for note in ctx.run_cache.get("facts_family:notes", []))


async def test_an_empty_enumeration_builds_nothing_and_protects_everything(session):
    """The fail-closed state ``generated_titles`` documents: a family that did
    not enumerate has NOT narrowed, and the sweep must not treat its
    collections as candidates. ``None``, not ``set()``."""
    definition = _definition()
    ctx = _ctx(session, definition)
    units = await FactsFamilyBuilder().expand(ctx)
    assert units == []
    assert generated_titles(ctx.run_cache, definition) is None


async def test_a_family_that_did_enumerate_records_every_title_it_derived(session):
    await _item(session, "1", tmdb_origin_country=["US"])
    definition = _definition()
    ctx = _ctx(session, definition)
    await FactsFamilyBuilder().expand(ctx)
    assert generated_titles(ctx.run_cache, definition) == {"US"}


async def test_every_unit_carries_the_family_label_and_the_operators_own(session):
    """The sweep's handle. ``_family_state`` finds a member by the family
    label, and the operator's own labels must survive the expansion -- the
    engine fills ``labels`` from the placeholder only when the expander set
    none (``engine._completed``), so an expander that sets it has to carry both.
    """
    await _item(session, "1", tmdb_origin_country=["US"])
    definition = _definition(labels=["mine"])
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert units[0].labels == ["mine", family_label(definition)]


async def test_an_over_cap_family_creates_nothing_and_says_both_numbers(session):
    """``max_collections``, the same refusal ``builders/dynamic.py`` makes and
    for the same reason: 300 collections an operator then deletes one at a time
    is worse than a refusal that names the way out."""
    for index in range(5):
        await _item(session, str(index), tmdb_origin_country=["C%d" % index])
    definition = _definition(params={"type": "origin_country", "max_collections": 3})
    ctx = _ctx(session, definition)
    units = await FactsFamilyBuilder().expand(ctx)
    assert units == []
    assert generated_titles(ctx.run_cache, definition) is None
    note = " ".join(ctx.run_cache.get("facts_family:notes", []))
    assert "5" in note and "3" in note


async def test_a_type_this_service_does_not_enumerate_is_refused_at_config_load():
    with pytest.raises(ValueError) as caught:
        FactsFamilyParams.model_validate({"type": "actor"})
    assert "origin_country" in str(caught.value)


def test_the_family_label_is_prefixed_and_not_the_bare_title():
    """A family called 'Countries' must not claim a plain ``Countries`` label
    an operator may already use -- ``builders/dynamic.py``'s reasoning, one
    prefix along, and the two prefixes must differ so the two sweeps cannot
    enumerate each other's members."""
    from autoposter.collections.builders.dynamic import (
        FAMILY_LABEL_PREFIX as DYNAMIC_PREFIX,
    )
    from autoposter.collections.builders.facts_family import FAMILY_LABEL_PREFIX

    assert FAMILY_LABEL_PREFIX != DYNAMIC_PREFIX
    assert family_label(_definition()) == FAMILY_LABEL_PREFIX + "Countries of origin"


def test_the_ten_dynamic_types_are_still_exactly_ten():
    """Global constraint 2, as a test rather than as a promise. This phase adds
    THREE family types and none of them is a ``DYNAMIC_TYPES`` row: that table's
    only consumer is a smart builder that cannot build a list family, and a row
    it cannot build would be advertised to operators by
    ``DynamicParams``' own error message."""
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    assert len(DYNAMIC_TYPES) == 10
    assert "origin_country" not in DYNAMIC_TYPES
    assert "original_language" not in DYNAMIC_TYPES
    assert "tmdb_collection" not in DYNAMIC_TYPES
```

- [ ] **Step 3: Run them to see them fail**

```bash
docker compose -p pprefetcht3b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_facts_family.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht3b-red1.log"
```

Expected: FAIL — no such module. `test_the_ten_dynamic_types_are_still_exactly_ten`
should be the one test that already passes; note that in the report.

- [ ] **Step 4: Add `collection_name` to the TMDb list client**

In `src/autoposter/providers/tmdb_lists.py`, beside `collection_parts`
(`:322-333`):

```python
    async def collection_name(self, collection_id: int) -> str | None:
        """A franchise collection's own name, or None if TMDb does not know it.

        The SAME request ``collection_parts`` makes -- same path, same (empty)
        params, therefore the same ``build_cache_key`` and the same
        ``provider_cache`` row. So a franchise family that titles a collection
        here and builds its membership one layer down pays for ONE ``/collection/
        {id}`` read per franchise per TTL, not two.

        That is also why the name is not stored on ``item_facts``: it is a
        property of the collection rather than of the item, storing it per item
        would invite drift between rows, and the read it would save is a read
        the membership already pays for.

        ``None`` rather than a raise for an unknown id, unlike
        ``collection_parts``: a family drops a key it cannot name and reports
        it, where a single ``tmdb_collection`` definition naming a dead id is
        one collection that genuinely cannot be built.
        """
        try:
            payload = await self._get(
                f"/collection/{collection_id}", {}, f"TMDb collection {collection_id}"
            )
        except TmdbListRefused:
            return None
        name = payload.get("name")
        return name if isinstance(name, str) and name else None
```

Check `_get`'s actual signature and 404 behaviour in that module before writing
this — the module docstring says 404 RAISES here rather than returning None
(`tmdb_lists.py:10`), which is why the `try` is written. If `_get` returns
`None` on 404 instead, drop the `try` and guard on `payload`.

Add two tests to `tests/test_tmdb_lists_client.py`:

```python
async def test_a_collection_name_is_read_from_the_same_response_as_its_parts():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={
            "id": 1241, "name": "Harry Potter Collection", "parts": [{"id": 671}],
        })

    from autoposter.providers.cache import ProviderCache
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TmdbListClient("tok", http)
        assert await client.collection_name(1241) == "Harry Potter Collection"


async def test_a_collection_tmdb_does_not_know_has_no_name():
    def handler(request):
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await TmdbListClient("tok", http).collection_name(999) is None
```

(match the file's own import and construction conventions).

- [ ] **Step 5: Write the family type table**

Create `src/autoposter/collections/facts_family.py`:

```python
"""Which facts-enumerated families this service ships, and how each is built.

The sibling of ``dynamic_types.py``, for the families whose values come from
this service's own database rather than from Plex. The two tables are separate
DELIBERATELY, and the reason is structural rather than stylistic:
``DYNAMIC_TYPES``' only consumer is ``builders/dynamic.DynamicBuilder``, which
is smart end to end -- it enumerates through ``listFilterChoices`` and builds
each key with ``parse_filters`` -> ``build_search_url`` ->
``reconcile_smart_collection``. A facts-enumerated type's MEMBERSHIP cannot be a
Plex smart filter at all, because Plex has no ``origin_country`` field; that
absence is roadmap row 189 in one sentence. A row in that table would therefore
be a row its builder cannot build, and worse, one
``DynamicParams._the_type_must_be_one_this_service_enumerates`` would advertise
to an operator as an option.

**What is shared instead, which is nearly everything that matters.**
``dynamic_keys.derive_keys`` decides which keys become collections and what each
one asks for, and ``dynamic_titles.family_titles`` names them. Both are pure and
both take the enumeration as DATA -- neither has ever known where the values
came from -- so this family shape reuses Kometa's key derivation and title
lifecycle unchanged, including the addon merges that make a synthetic 'Nordic'
bucket out of five country codes, the ``include``-applied-last whitelist, the
``other`` leftovers bucket, and the duplicate-title refusal.

**Three rows.** Kometa builds all three as PLAIN collections from a full-library
TMDb walk (``meta.py:36-37``, ``:971-993``, ``builder.py:360-374``), which is
what this service's facts pipeline already is.
"""
from dataclasses import dataclass

from autoposter.collections.facts_enumeration import FACTS_FIELDS, FactsField

__all__ = ["FACTS_FAMILY_TYPES", "FACTS_FAMILY_TYPE_NAMES", "FactsFamilyType"]


@dataclass(frozen=True)
class FactsFamilyType:
    """One row: a family an operator can write as ``params.type``.

    ``field`` is the ``FACTS_FIELDS`` entry the family enumerates, and it also
    supplies the library types the family can serve -- one authority, so a row
    cannot enumerate one field and build another.

    ``member_builder`` is the ``type_name`` each expanded unit is built by.
    Two answers ship: ``facts_value`` for a family whose members are "the items
    whose facts carry this value", and ``tmdb_collection`` for franchises,
    whose members are better answered by the franchise's own ``parts`` than by
    the items this service happens to have fetched.

    ``key_from`` is which half of the enumeration is the KEY that
    ``include``/``exclude``/``addons`` and the override tables are matched
    against -- ``"value"`` when the enumerated value is itself the key (a
    country code, a language code), and ``"id"`` for the franchise family,
    whose enumeration is ids and whose display name is fetched. It is the same
    distinction ``DynamicType.key_from`` draws, and it decides whether
    ``exclude: [1241]`` works or does nothing.
    """

    name: str
    field: FactsField
    member_builder: str
    key_from: str
    title_format: str
    note: str


# The key's own name and nothing else -- the format Kometa gives `country`,
# `network` and the location packs. Shared by all three rows here because none
# of them has a better default: a pack that wants more says so with its own
# `title_format`, which is where every collision this catalog refuses is
# resolved (`tests/test_collection_catalog.py`'s format-collision test).
_BARE_KEY_TITLE = "<<key_name>>"


FACTS_FAMILY_TYPES: dict[str, FactsFamilyType] = {
    row.name: row
    for row in (
        FactsFamilyType(
            "origin_country", FACTS_FIELDS["origin_country"], "facts_value",
            "value", _BARE_KEY_TITLE,
            "Roadmap row 189's first half. Upstream builds this as a PLAIN "
            "collection from a full-library TMDb walk (meta.py:36-37, "
            "builder.py:360-374) and so does this, off the walk the facts "
            "pipeline already makes. The keys are ISO-3166-1 codes and the "
            "titles are those codes: no code->name table exists here or in "
            "Kometa's own pack files, which is the same absence roadmap row 190 "
            "records for the language families. A pack that wants readable "
            "names supplies `key_name_override`, and `region.yml`'s grouping "
            "table makes the question moot for the packs that ship -- their "
            "titles are the region names.",
        ),
        FactsFamilyType(
            "original_language", FACTS_FIELDS["original_language"], "facts_value",
            "value", _BARE_KEY_TITLE,
            "Roadmap row 189's second half, and the same shape as its sibling "
            "above in every respect: ISO-639-1 keys, titled from the code, and "
            "no name table to do better with. NOT the same thing as the shipped "
            "`audio_language` dynamic type, which enumerates the audio STREAMS "
            "a Plex item carries -- a dubbed film has several and one original "
            "language, and conflating them is the class of same-name-different-"
            "meaning bug row 156 exists to refuse.",
        ),
        FactsFamilyType(
            "tmdb_collection", FACTS_FIELDS["tmdb_collection"], "tmdb_collection",
            "id", _BARE_KEY_TITLE,
            "Roadmap row 192. The single-collection `tmdb_collection` builder "
            "shipped in phase 8b and is what each unit here runs, so the "
            "membership is the franchise's own `parts` rather than the items "
            "this service has fetched -- which also means a franchise film the "
            "library owns but has never gathered facts for still joins its "
            "collection. The enumeration decides WHICH franchises the library "
            "is in; the builder decides what is in each. Movie-only, from the "
            "field: TMDb collections are movie franchises.",
        ),
    )
}

FACTS_FAMILY_TYPE_NAMES: tuple[str, ...] = tuple(FACTS_FAMILY_TYPES)
```

**If Task 1 §4's `FRANCHISE KEY` verdict is `name`**, change that row's
`key_from` to `"value"` and say so in the row's note plus the task report — the
enumeration then keys on the resolved name and the ids become an implementation
detail. Everything else is unchanged.

- [ ] **Step 6: Give `BuilderContext` the definition**

In `src/autoposter/collections/builders/base.py`, one more field on
`BuilderContext`:

```python
    session: AsyncSession | None = None
    definition: Any = None
```

and a paragraph in the docstring:

```
    ``definition`` is the definition this context was built for, and only a
    FAMILY builder needs it: an expanding builder returns whole
    ``CollectionDefinition``s, and a family's units have to carry both the
    operator's own labels and the family label the delete sweep finds them by.
    ``engine._completed`` fills an unset field from the placeholder, so a
    builder that sets ``labels`` at all has to set both halves -- and it cannot
    read the first half from anywhere else. None for a direct caller, and for
    every builder that does not expand.
```

And in `src/autoposter/collections/engine.py`, one more line in the
`BuilderContext` construction:

```python
            session=session,
            definition=definition,
```

- [ ] **Step 7: Write the family builder**

Create `src/autoposter/collections/builders/facts_family.py`:

```python
"""``facts_family``: one LIST collection per value this service's facts hold.

The third family shape, and the sentence that distinguishes it from its two
siblings: ``cs_bucket`` manages a family of SMART collections from a static
table, ``dynamic`` manages a family of SMART collections from a Plex
enumeration, and this manages a family of LIST collections from a DATABASE
enumeration. The difference is forced rather than chosen -- Plex has no
``origin_country`` field, so there is no filter to store on a collection and
the membership has to be resolved and applied as a list.

**Four pieces, three of them phase 10a's.** ``facts_enumeration`` does the one
database read; ``dynamic_keys.derive_keys`` decides which keys become
collections and what each asks for; ``dynamic_titles.family_titles`` names
them; and this module is the seam between them and the engine's ordinary list
path. Nothing in the first three is modified, and their tests are the gate.

**How it reaches the engine.** Through ``expand`` (``engine._expand``), the
same hook ``imdb_award_years`` uses for the Oscars years -- the definition an
operator writes is a placeholder that never becomes a collection, and the units
it returns are ordinary list definitions the engine resolves and applies.

**How it joins the delete sweep.** Through ``family_label`` and
``generated_titles``, which is exactly the protocol ``builders/dynamic.py``
names ("Growing these two methods is the whole protocol a future family builder
needs to join the sweep"). ``engine._family_state`` reads them off the registry
entry with ``getattr`` and does not care that this one is a list builder. The
prefix differs from the dynamic families' so the two sweeps cannot enumerate
each other's members.

**Coverage, said out loud rather than discovered.** This family enumerates what
the facts pipeline has VISITED. On a library the pipeline has half worked
through, the family is half the size it will be -- correct, incomplete, and
converging as the drift sweep works the rest (``scheduler.drift_days``,
``scheduler.drift_batch_size``: 500 items a week by default). Every pack built
on this says so in its own description, and the builder reports the coverage
numbers in its actions so an operator can see where they are.

**Every refusal RETURNS**, as a note in the pass's run cache that the engine
surfaces: an empty enumeration, an all-excluded family, an over-cap fan-out, a
duplicate title and a franchise TMDb cannot name are all reported rather than
raised. ``expand`` returning ``[]`` is a family that built nothing, and
``generated_titles`` answering ``None`` is what stops the sweep treating that as
narrowing.
"""
import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoposter.collections.builders.base import BuilderContext, require_library_type
from autoposter.collections.dynamic_keys import derive_keys
from autoposter.collections.dynamic_titles import (
    DuplicateFamilyTitle,
    family_titles,
    title_format_names_the_key,
)
from autoposter.collections.facts_enumeration import coverage, enumerate_values
from autoposter.collections.facts_family import (
    FACTS_FAMILY_TYPE_NAMES,
    FACTS_FAMILY_TYPES,
)
from autoposter.config.schema import CollectionDefinition

logger = logging.getLogger(__name__)

__all__ = [
    "FAMILY_LABEL_PREFIX", "FactsFamilyBuilder", "FactsFamilyParams",
    "family_label", "generated_titles", "notes",
]

# The label every collection in one family carries, beside the ownership label.
# Distinct from ``builders/dynamic.FAMILY_LABEL_PREFIX`` so the two family
# sweeps cannot enumerate each other's members, and prefixed rather than the
# bare definition title for that module's reason: a family called "Countries"
# would otherwise claim a plain ``Countries`` label an operator may already use,
# and this label is a DELETE handle.
FAMILY_LABEL_PREFIX = "autoposter-facts: "

_NOTES_KEY = "facts_family:notes"


def family_label(definition) -> str:
    """The label every collection in ``definition``'s family carries."""
    return "%s%s" % (FAMILY_LABEL_PREFIX, definition.title)


def _generated_key(label: str) -> str:
    return "facts_family:generated:%s" % label


def notes(run_cache: dict) -> list[str]:
    """Everything the families in this pass reported, for the engine to surface.

    A list on the run cache rather than a return value, because ``expand`` has
    to answer with definitions and the engine logs an expansion failure without
    a place to put an operator-facing note (``engine.py:378-385``). The engine
    surfaces these beside the pass's other actions.
    """
    return run_cache.setdefault(_NOTES_KEY, [])


def generated_titles(run_cache: dict, definition) -> set[str] | None:
    """What ``definition``'s family built this pass, or ``None``.

    ``None`` is the FAIL-CLOSED answer, verbatim from ``builders/dynamic.py``'s
    of the same name and for the same reason: it means this family did not get
    as far as deciding -- outside its schedule, or refused at the family level
    (an empty enumeration, an all-excluded family, an over-cap fan-out, a
    duplicate title). A sweep must not delete a family's collections on a pass
    that never enumerated: one transient failure would otherwise read as "the
    operator narrowed the family" and take every collection in it.

    A set is the family's own answer, and it is every title the family DERIVED
    -- written before a single collection is created, so a key whose apply
    failed is still a key this family builds.
    """
    return run_cache.get(_generated_key(family_label(definition)))


class FactsFamilyParams(BaseModel):
    """A facts family: which stored field to enumerate, and how to name it.

    Deliberately the same vocabulary ``DynamicParams`` uses -- ``include``,
    ``exclude``, ``addons``, ``custom_keys``, ``key_name_override``,
    ``title_override``, ``remove_prefix``/``remove_suffix``, ``title_format``,
    ``other_name``, ``max_collections`` -- because the keys are consumed by the
    SAME two pure modules and an operator who has learned one family's knobs has
    learned this one's.

    Three of ``DynamicParams``' knobs are deliberately absent. ``sort_by`` and
    ``limit`` cap a SEARCH Plex evaluates and there is none here -- a list
    definition's own ``sort:`` and ``limit:`` fields do that job, and the engine
    already inherits both into every expanded unit
    (``engine._INHERITED_BY_EXPANSION``). ``minimum_items`` is absent because
    the thing it would count does not exist at this layer: a unit's membership
    is resolved by the unit's OWN builder one layer down, so a per-key minimum
    here would mean resolving every key's membership twice. A shipped knob whose
    cost is a second full pass, to enforce a floor upstream does not have for
    any of these types, is not worth a line -- and a knob an operator sets that
    nothing reads is exactly what ``DynamicParams``' refusal of ``data:``
    exists to prevent.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    type: str
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    addons: dict[str, list[str]] = Field(default_factory=dict)
    custom_keys: bool = True
    key_name_override: dict[str, str] = Field(default_factory=dict)
    title_override: dict[str, str] = Field(default_factory=dict)
    remove_prefix: list[str] = Field(default_factory=list)
    remove_suffix: list[str] = Field(default_factory=list)
    title_format: str | None = None
    other_name: str | None = None
    # The same refuse-over-surprise floor ``DynamicParams`` pins, and the same
    # number: a family that would create more collections than an operator can
    # hold in their head refuses with both numbers and creates nothing.
    max_collections: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def _the_type_must_be_one_this_service_enumerates(self) -> "FactsFamilyParams":
        if self.type not in FACTS_FAMILY_TYPES:
            raise ValueError(
                "%r is not a facts family this service enumerates. Options: %s. "
                "A family built on a PLEX tag is `builder: dynamic` instead -- "
                "see `collections/facts_family.py`'s module docstring for why "
                "the two tables are separate"
                % (self.type, ", ".join(FACTS_FAMILY_TYPE_NAMES))
            )
        return self

    @model_validator(mode="after")
    def _a_title_format_must_name_the_key(self) -> "FactsFamilyParams":
        if self.title_format is not None and not title_format_names_the_key(
            self.title_format
        ):
            raise ValueError(
                "`title_format` has to carry `<<key_name>>` (or `<<title>>`, "
                "which means the same value): without one, every collection in "
                "the family would be given the same name"
            )
        return self


class FactsFamilyBuilder:
    """A family of list collections, one per value the stored facts hold."""

    type_name = "facts_family"
    params_model = FactsFamilyParams

    def family_label(self, definition) -> str:
        """The label this definition's collections carry -- a method as well as
        a module function so ``engine._family_state`` can ask the REGISTRY
        entry rather than importing this module by name."""
        return family_label(definition)

    def generated_titles(self, run_cache: dict, definition) -> set[str] | None:
        """What this definition's family built this pass; see the module
        function of the same name for what ``None`` means."""
        return generated_titles(run_cache, definition)

    async def expand(self, ctx: BuilderContext) -> list[CollectionDefinition]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'facts_family' builder expands the family its definition "
                "names, so it cannot run without one"
            )
        params = FactsFamilyParams.model_validate(ctx.config)
        row = FACTS_FAMILY_TYPES[params.type]
        report = notes(ctx.run_cache)

        # Above the enumeration deliberately: a movie-only family on a show
        # library must cost zero database work.
        require_library_type(
            "the 'facts_family' builder's %r type" % params.type,
            ctx.library_type, row.field.kinds,
        )
        if ctx.session is None:
            raise ValueError(
                "the 'facts_family' builder reads this service's own database "
                "and was given no session"
            )

        counted = await enumerate_values(
            ctx.session, row.field,
            library=ctx.library, library_type=ctx.library_type,
        )
        attempted, total = await coverage(
            ctx.session, library=ctx.library, library_type=ctx.library_type,
        )
        if not counted:
            report.append(
                "%r built nothing: no %s values are stored for %r yet. The "
                "facts pipeline has visited %d of %d item(s) there; this "
                "family fills in as the ratings-drift sweep works through the "
                "rest (scheduler.drift_days, scheduler.drift_batch_size)"
                % (definition.title, params.type, ctx.library, attempted, total)
            )
            return []

        # The franchise family is the one that needs a name before it can title
        # anything: the enumeration is ids. One `/collection/{id}` read each,
        # through the provider cache, and the SAME cache entry the membership
        # builder reads one layer down -- so the family pays once per franchise
        # per TTL, not twice.
        enumerated: list[tuple[str, str]] = []
        if row.key_from == "id":
            client = getattr(ctx.sources, "tmdb", None)
            if client is None:
                report.append(
                    "%r built nothing: a %r family names each collection from "
                    "TMDb and no TMDb read access token is configured"
                    % (definition.title, params.type)
                )
                return []
            for value, _count in counted:
                name = await client.collection_name(int(value))
                if not name:
                    report.append(
                        "%r: TMDb could not name collection %s, so no "
                        "collection was built for it. A collection titled from "
                        "an id is nobody's ask; `title_override: {%s: …}` "
                        "names it by hand"
                        % (definition.title, value, value)
                    )
                    continue
                enumerated.append((value, name))
        else:
            enumerated = [(value, value) for value, _count in counted]

        if not enumerated:
            report.append(
                "%r built nothing: none of the %d enumerated %s value(s) could "
                "be named" % (definition.title, len(counted), params.type)
            )
            return []

        derived = derive_keys(
            enumerated,
            include=params.include,
            exclude=params.exclude,
            addons=params.addons,
            custom_keys=params.custom_keys,
        )
        try:
            titled = family_titles(
                derived,
                library_type=ctx.library_type,
                title_format=params.title_format or row.title_format,
                key_name_override=params.key_name_override,
                title_override=params.title_override,
                remove_prefix=params.remove_prefix,
                remove_suffix=params.remove_suffix,
                # Upstream gates the leftovers collection on ``include``
                # (meta.py:1301-1311) and so does this: with no include list
                # nothing is left over.
                other_name=params.other_name if params.include else None,
            )
        except DuplicateFamilyTitle as refusal:
            report.append("%r built nothing: %s" % (definition.title, refusal))
            return []

        if not titled:
            report.append(
                "%r built nothing: every %s value %r holds was excluded. Widen "
                "`include:`, or remove the definition"
                % (definition.title, params.type, ctx.library)
            )
            return []
        if len(titled) > params.max_collections:
            report.append(
                "%r built nothing: this would create %d collections in %r -- "
                "%r reports %d value(s) there, which `include:`, `exclude:` and "
                "`addons:` narrow to that many buckets -- and `max_collections` "
                "is %d. Narrow the family, or raise `max_collections` past %d "
                "if that is really what you want"
                % (definition.title, len(titled), ctx.library, params.type,
                   len(enumerated), params.max_collections, len(titled))
            )
            return []

        # The sweep's input, written HERE -- after every family-level refusal,
        # before a single unit is returned. Seeded with every title the family
        # derived, so a unit whose apply fails is still a title this family
        # builds and its collection survives the sweep. Never ``set()``: the
        # engine reads absence and emptiness as opposites, and an empty record
        # would silently delete the family.
        generated = {unit.title for unit in titled}
        ctx.run_cache[_generated_key(family_label(definition))] = generated

        label = family_label(definition)
        units: list[CollectionDefinition] = []
        for unit in titled:
            if not unit.values:
                report.append(
                    "%r: %r resolved to no values and was not built"
                    % (definition.title, unit.title)
                )
                generated.discard(unit.title)
                continue
            if row.member_builder == "tmdb_collection":
                # One franchise per collection: the shipped builder takes a
                # single id, and an addon merge over franchises is upstream's
                # own way of saying "Prometheus belongs with Alien", which is
                # two ids and one collection. Not expressible through a builder
                # that takes one id, so a merged bucket takes its FIRST value
                # and the rest are reported -- honest, and visible.
                if len(unit.values) > 1:
                    report.append(
                        "%r: %r merges %d franchises and the `tmdb_collection` "
                        "builder takes one id, so only %s was built. Write the "
                        "extra franchises as their own definitions if you want "
                        "them: %s"
                        % (definition.title, unit.title, len(unit.values),
                           unit.values[0], ", ".join(unit.values[1:]))
                    )
                unit_params = {"id": int(unit.values[0])}
            else:
                unit_params = {"field": row.field.name, "values": list(unit.values)}
            units.append(CollectionDefinition(
                title=unit.title,
                builder=row.member_builder,
                params=unit_params,
                # Both halves, and it has to be both: ``engine._completed``
                # fills ``labels`` from the placeholder only when the expander
                # set none, so setting the family label alone would silently
                # drop the operator's own.
                labels=[*definition.labels, label],
            ))

        report.append(
            "%r enumerated %d %s value(s) in %r and built %d collection(s); the "
            "facts pipeline has visited %d of %d item(s) there"
            % (definition.title, len(enumerated), row.name, ctx.library,
               len(units), attempted, total)
        )
        return units
```

**One implementation note.** Register the builder in `builders/__init__.py`
beside `facts_value`, following that module's own shape.

- [ ] **Step 8: Prove the sweep protocol actually engages**

The engine reads `family_label`/`generated_titles` off the registry entry by
`getattr` (`engine.py:698-706`); nothing asserts a LIST builder can join. Add to
`tests/test_collection_facts_family.py`:

```python
def test_the_engine_finds_this_familys_label_through_the_registry():
    """FLAG 2, as a test: ``_family_state`` was written for smart builders and
    reads the protocol off the registry entry with ``getattr``. A list family
    that did not join it would have its collections deleted on the next pass --
    they carry the ownership label and a managed row, and their titles are not
    in ``definition_titles_for``'s set."""
    from autoposter.collections.builders.base import REGISTRY
    from autoposter.collections.engine import _family_state

    definition = _definition()
    labels, generated = _family_state([definition], "Movies", {})
    assert labels == {family_label(definition): definition.title}
    assert generated == {}, "a family that has not run protects everything"

    ran = {_generated_key_for(definition): {"US"}}
    labels, generated = _family_state([definition], "Movies", ran)
    assert generated[family_label(definition)] == {"US"}
```

with a small helper beside it:

```python
def _generated_key_for(definition):
    from autoposter.collections.builders.facts_family import _generated_key, family_label
    return _generated_key(family_label(definition))
```

- [ ] **Step 9: One paragraph in `dynamic_types.py`'s scope note**

The scope note currently names `original_language`/`origin_country` and
`tmdb_collection` among the deliberate absences, each pointing at the
metadata-prefetch budget. That is now stale by half. **Change no row and no
code** — append to the list item text and add one closing paragraph:

```
- ``original_language``/``origin_country`` -- upstream builds these as PLAIN
  collections whose membership comes from a full-library TMDb walk
  (meta.py:36-37, :971-993, builder.py:360-374), which is the metadata-prefetch
  budget family (rows 155/180), not an enumeration. **They ship, as of the
  prefetch phase, as a different family shape:** ``builder: facts_family``,
  enumerated from this service's own ``item_facts`` and built as LIST
  collections, because a Plex smart filter cannot express membership Plex has no
  field for. See ``collections/facts_family.py``.
```

(and the same one-sentence addition on the `tmdb_collection` bullet). Then, at
the end of the docstring:

```
**Why the facts families are not rows here.** ``DYNAMIC_TYPES``' only consumer
is ``DynamicBuilder``, which is smart end to end -- ``listFilterChoices`` in,
``reconcile_smart_collection`` out. A row it cannot build would still be
advertised to operators by ``DynamicParams``' own error message, so the three
facts-enumerated families live in ``collections/facts_family.py`` and share what
is actually shareable: ``dynamic_keys`` and ``dynamic_titles``, both of which
take the enumeration as data and neither of which changed to accommodate them.
```

`git diff src/autoposter/collections/dynamic_types.py` must show **docstring
lines only**.

- [ ] **Step 10: Run everything and commit**

```bash
docker compose -p pprefetcht3b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_facts_family.py tests/test_collection_facts_enumeration.py tests/test_tmdb_lists_client.py tests/test_collection_dynamic_types.py tests/test_collection_dynamic.py tests/test_collection_dynamic_oracle.py tests/test_collection_engine.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht3b-green.log"
docker compose -p pprefetcht3b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pprefetcht3b-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pprefetcht3b-full.log"
docker wait pprefetcht3b-full
docker compose -p pprefetcht3b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "ruff check src tests"
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
git diff --stat origin/main -- src/autoposter/collections/dynamic_keys.py \
  src/autoposter/collections/dynamic_titles.py src/autoposter/collections/builders/dynamic.py
```

Both `git diff --stat` outputs must be empty.

```bash
git add src/autoposter/collections/facts_family.py \
        src/autoposter/collections/builders/facts_family.py \
        src/autoposter/collections/builders/base.py \
        src/autoposter/collections/builders/__init__.py \
        src/autoposter/collections/engine.py \
        src/autoposter/collections/dynamic_types.py \
        src/autoposter/providers/tmdb_lists.py \
        tests/test_collection_facts_family.py tests/test_tmdb_lists_client.py
git commit --no-gpg-sign -m "feat(collections): facts_family, one list collection per stored TMDb value"
docker compose -p pprefetcht3b -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

- [ ] **Step 11: Mutation proof — the fail-closed sweep record**

```bash
cp src/autoposter/collections/builders/facts_family.py /tmp/ff.py.bak
# edit: in the empty-enumeration branch, write
#   ctx.run_cache[_generated_key(family_label(definition))] = set()
# before returning [] -- the "silently delete the family" mistake
docker compose -p pprefetcht3b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_facts_family.py::test_an_empty_enumeration_builds_nothing_and_protects_everything -q"
# expected: RED
cp /tmp/ff.py.bak src/autoposter/collections/builders/facts_family.py
cmp /tmp/ff.py.bak src/autoposter/collections/builders/facts_family.py && echo RESTORED-EXACT
docker compose -p pprefetcht3b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_facts_family.py -q"
# expected: GREEN
```

Paste both halves and the `RESTORED-EXACT` line.

---

## Task 4: The packs

**Files:**
- Modify: `src/autoposter/collections/packs.py`
- Modify: `src/autoposter/collections/catalog.py:936-964` (content_franchises),
  `:1400-1480` (the location packs), `:653-691` (the gate constants)
- Modify: `tests/test_collection_catalog.py:265-275` (the checksum),
  and new pack-contract tests
- Modify: `config/autoposter.example.yaml` (the presets note, if the pack list
  is enumerated there)

**Interfaces:**
- Consumes: Task 1 §4 (the three transcribed files and their verdict lines),
  Task 1 §5 (the NOT-KOMETA list), Task 3b's `FactsFamilyParams` field names
  and `FACTS_FAMILY_TYPES` keys.
- Produces: `packs.FRANCHISE_PARAMS`, `packs.REGION_PARAMS`,
  `packs.CONTINENT_PARAMS` (each a `tuple[tuple[str, object], ...]`, the shape
  `PresetCollection.params` takes) — **only for the packs that can ship
  honestly**; a pack that cannot re-files instead.

**The re-file rule, first, because it governs the whole task.** C8 flips
`content_franchises` and flips `location_region`/`location_continent` **IF their
grouping tables transcribe cleanly**. "Cleanly" has a test, and it is Task 1
§4's `REGION MEMBERS` / `CONTINENT MEMBERS` verdict:

- If the members are **ISO country codes**, they meet `origin_country`'s keys
  directly and the pack transcribes. Ship it.
- If the members are **country NAMES**, they do not meet ISO-code keys at all,
  and the pack would need a name→code table nobody has fetched. **Do not invent
  one.** Re-file: keep the row `GATED`, re-point it at a NEW roadmap row (Task 5
  files it) that says exactly this, and record the finding. That is the 10b
  discipline verbatim — "a pack that can't ship honestly re-files".

The same rule applies to `content_franchises` if Task 1 §4 finds something that
does not transcribe. Say so and stop; do not approximate.

- [ ] **Step 1: Write the pack tables**

Append to `src/autoposter/collections/packs.py`, following that module's own
conventions exactly: a `# --- <pack>: defaults/movie/<file>.yml ----` banner, a
prose paragraph naming what the file does, a `# Transcribed from … in file
order -- record §4 (…)` comment above each table, `# NOT KOMETA:` on anything
this service decided, and the `*_PARAMS` tuple last. Add the three new names to
`__all__` in alphabetical order.

The tables themselves come from Task 1 §4 and **are not in this plan**: a
recalled include list is exactly what `catalog.py`'s provenance rule refuses.
What this plan specifies is their SHAPE:

```python
# --- franchises: defaults/movie/franchise.yml ---------------------------------
#
# One collection per TMDb franchise the library's items belong to. The
# enumeration is `item_facts.tmdb_collection_id`, which the facts pipeline
# stores off the `/movie/{id}` read it already makes (roadmap row 192), and
# each collection's MEMBERSHIP is the franchise's own `parts` through the
# shipped `tmdb_collection` builder -- so a franchise film the library owns but
# has never gathered facts for still joins its collection.

_FRANCHISE_ADDONS: dict[str, list[str]] = {
    # record §4.1, in file order. Keyed by <id|name>, per §4's FRANCHISE KEY
    # verdict.
}

FRANCHISE_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "tmdb_collection"),
    ("title_format", "<from §4.1; upstream's own>"),
    ("remove_suffix", ["Collection"]),   # from §4.1 if present, else omit
    ("addons", _FRANCHISE_ADDONS),
    # NOT KOMETA: upstream has no cap concept at all. <the number and its
    # arithmetic, from §5>
    ("max_collections", ...),
)
```

Two shape rules the franchise pack must respect, both established in Task 3b:

- `sort_by` and `limit` are **not** `FactsFamilyParams` fields (a list
  collection has no search to cap). If §4.1 records a `template_variables`
  `sort_by`/`limit`, it is expressed as the definition's own `sort:`/`limit:`
  fields on the `PresetCollection`, or it is recorded in §5 as a divergence and
  not shipped. Do not add a params field to make a transcription fit.
- An `addons` bucket that merges two franchises builds ONE of them and reports
  the rest (Task 3b's `tmdb_collection` route takes one id). If §4.1's addon
  table is large, say so in the row's description in the operator's own words —
  it is an opinion this catalog ships and constraint 10 requires it in the row.

`REGION_PARAMS` and `CONTINENT_PARAMS` take the same shape with
`("type", "origin_country")`, their own `include` (the region/continent names,
which are the synthetic bucket keys) and their own `addons` (name → member
codes).

- [ ] **Step 2: Write the failing pack-contract tests**

`_dynamic_rows()` at `tests/test_collection_catalog.py:550-558` filters
`collection.builder == "dynamic"`, so the shipped seven are unaffected by
anything here. Add a sibling and its contract tests beside them:

```python
def _facts_family_rows():
    return [
        (preset, collection, dict(collection.params))
        for preset in READY_PRESETS
        for collection in preset.collections
        if collection.builder == "facts_family"
    ]


def test_there_are_facts_family_packs_to_hold_to_the_contract():
    """The guard the tests below need: an empty list is a pass that proves
    nothing, and pytest reports it as a pass.

    UPDATE THIS SET when a pack re-files instead of shipping -- and if all
    three re-file, delete these tests with the packs rather than leaving a
    green suite asserting over nothing.
    """
    assert {preset.key for preset, _, _ in _facts_family_rows()} == {
        "content_franchises", "location_region", "location_continent",
    }


def test_a_facts_pack_is_one_definition_whose_type_this_service_enumerates():
    from autoposter.collections.facts_family import FACTS_FAMILY_TYPES

    for preset, collection, params in _facts_family_rows():
        assert params["type"] in FACTS_FAMILY_TYPES, preset.key
        row = FACTS_FAMILY_TYPES[params["type"]]
        # The pack's library types are the FIELD's -- a movie-only field under
        # a both-libraries preset would build nothing on half its libraries and
        # say nothing about it.
        assert set(preset.library_types) <= set(row.field.kinds), preset.key
        for library_type in preset.library_types:
            definitions = preset.definitions(library_type)
            assert len(definitions) == 1, (preset.key, library_type)
            assert definitions[0].builder == "facts_family"
            assert definitions[0].title == collection.title


def test_every_facts_pack_says_which_type_it_is_in_words():
    for preset, _collection, params in _facts_family_rows():
        assert "`type: %s`" % params["type"] in preset.description, preset.key


def test_every_facts_pack_states_its_coverage_story():
    """The one thing these packs must say that the Plex-enumerated ones need
    not: their values come from what the facts pipeline has VISITED, so a
    freshly-deployed library gets a small, correct, growing family. An
    operator who is not told that reads a half-built family as a bug.
    """
    for preset, _collection, _params in _facts_family_rows():
        assert "drift" in preset.description.lower(), preset.key


def test_every_opinion_a_facts_pack_pins_is_stated_in_the_row():
    for preset, _collection, params in _facts_family_rows():
        cap = params.get("max_collections")
        if cap is not None:
            assert str(cap) in preset.description, (preset.key, cap)
        if params.get("include"):
            assert "include" in preset.description, preset.key
        if params.get("title_format"):
            assert "title" in preset.description.lower(), preset.key


def test_the_new_pack_tables_have_not_drifted_by_one_entry():
    """The same digest discipline ``test_the_big_pack_tables_have_not_drifted_
    by_one_entry`` applies to the shipped seven: the record these tables
    transcribe is gitignored, so ``packs.py`` is the only in-repo copy.

    Recomputing a digest after a DELIBERATE table edit: re-verify against the
    record's §4 FIRST, then regenerate with ``_table_checksum``. Never adjust a
    digest to make a red test green without that check.
    """
    assert len(packs._FRANCHISE_ADDONS) == ...      # from §4.1
    assert _table_checksum(packs._FRANCHISE_ADDONS) == "..."
    assert len(packs._REGION_INCLUDE) == ...        # from §4.2
    assert _table_checksum(packs._REGION_INCLUDE) == "..."
    assert len(packs._REGION_ADDONS) == ...
    assert _table_checksum(packs._REGION_ADDONS) == "..."
    assert len(packs._CONTINENT_INCLUDE) == ...     # from §4.3
    assert _table_checksum(packs._CONTINENT_INCLUDE) == "..."
    assert len(packs._CONTINENT_ADDONS) == ...
    assert _table_checksum(packs._CONTINENT_ADDONS) == "..."
```

The `...` and `"..."` above are filled from the transcribed tables themselves:
write the table, run `_table_checksum` on it in a one-off python call inside the
container, paste the digest. That is the same procedure the shipped digests
document, and it is not a placeholder — the numbers do not exist until the
tables do.

- [ ] **Step 3: Run them to see them fail**

```bash
docker compose -p pprefetcht4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_catalog.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht4-red.log"
```

Expected: FAIL on `test_there_are_facts_family_packs_to_hold_to_the_contract`
(the three presets are still GATED and build nothing).

- [ ] **Step 4: Flip `content_franchises`**

Replace the row at `catalog.py:936-965`. It loses `readiness=GATED` and
`gated_row`, and gains `collections`. Its description must carry, in the
operator's own words: what it builds, that the enumeration is the facts
pipeline's and converges with the drift sweep, the `type:` to write, every
pinned opinion, and the customisation path. The shape:

```python
    Preset(
        key="content_franchises",
        category="content",
        name="Franchises",
        description=(
            "One collection per TMDb franchise collection the library holds, "
            "with Kometa's addon merges (Prometheus into Alien, Minions into "
            "Despicable Me) and its 'Collection' suffix removed, transcribed "
            "from `defaults/movie/franchise.yml`. Movie libraries only, as "
            "upstream has it: TMDb collections are movie franchises. Built by "
            "`builder: facts_family`, `type: tmdb_collection` -- the family "
            "enumerates the franchises this library's items belong to from the "
            "`belongs_to_collection` id the facts pipeline stores off the "
            "`/movie/{id}` read it already makes, and each collection's MEMBERS "
            "are that franchise's own parts through the `tmdb_collection` "
            "builder, so a film the library owns but has not been gathered yet "
            "still joins its collection. "
            "What that costs, said plainly: the family is as complete as the "
            "facts pipeline's coverage. A freshly-deployed library starts small "
            "and grows as the ratings-drift sweep works through it "
            "(`scheduler.drift_days`, `scheduler.drift_batch_size` -- 500 items "
            "a week by default), and each pass reports how many items it has "
            "visited. `max_collections` is pinned at %d, <the arithmetic>. "
            "To build something other than what this pack builds, copy it into "
            "a `definitions:` entry of your own and edit it there -- a preset "
            "is a key, not a copy of the definitions it stands for."
            % ...
        ),
        kometa_source="defaults/movie/franchise.yml",
        library_types=_MOVIE,
        collections=(
            PresetCollection(
                title="Franchises", builder="facts_family",
                params=packs.FRANCHISE_PARAMS,
            ),
        ),
    ),
```

- [ ] **Step 5: Flip (or re-file) the two location packs**

`_LOCATION_PACKS` at `:1408-1415` is a comprehension producing two GATED rows
from one shared description template. If both ship, they stop being one of a
kind with each other in the same way `location_country` did (`:1417-1418`):
each now has a producer and its own pack table. Write them out as two explicit
`Preset(...)` rows beside `_COUNTRY_PRESET`, delete `_LOCATION_PACKS` and the
comprehension, and update the section's leading comment — which currently says
"Two packs, one blocker, plus one that shipped" — to what is now true.

Each description must additionally carry the sentence that distinguishes these
from `location_country`, because an operator can enable both and would
reasonably expect them to agree:

```
"The values here are TMDb's own origin countries, which is a DIFFERENT value "
"set from the `Countries` pack beside this one: that one enumerates Plex's own "
"`country` tag -- the production country as Plex's agent recorded it -- and "
"the two disagree on exactly the co-productions you would notice. Kometa "
"groups TMDb's origin countries and so does this."
```

**If Task 1 §4's verdict says the members are country NAMES rather than ISO
codes**, do not write these rows. Leave both GATED, re-point `gated_row` at the
new row Task 5 files, and rewrite their shared description to say precisely
what is missing (a name→code table nobody has fetched) instead of what it says
today. Record the branch taken in the task report — Task 5 needs it.

- [ ] **Step 6: Move the gate constants and the checksum**

`TMDB_ORIGIN_COUNTRY_ROW` (189) and `TMDB_COLLECTION_TYPE_ROW` (192)
(`catalog.py:676-685`) are no longer blockers for the rows that flipped. Two
edits, and the second is the one that is easy to get wrong:

- If a constant is no longer referenced by any `gated_row=`, it may still be
  CITED in prose (as `TMDB_LANGUAGE_NAME_ROW` already is, `:686-690`). Keep it
  and rewrite its comment to say it is cited rather than waited on, in that
  constant's own established shape. Delete it only if nothing references it at
  all — and `test_a_gated_row_names_a_real_roadmap_row`-style tests read these,
  so run the catalog suite after either choice.
- `CATALOG_CHECKSUM` (`tests/test_collection_catalog.py:265-275`) moves by
  exactly the number of rows that flipped: `content` `(2, 2, 0)` → `(3, 1, 0)`
  and `location` `(1, 2, 0)` → `(3, 0, 0)` if all three ship; adjust for
  whatever actually shipped. The comment above it says why one number moving is
  the whole point — do not weaken it.

- [ ] **Step 7: The picker's shape line**

`Preset.years_title()` (`catalog.py:370-388`) answers for an award ceremony and
for a `builder == "dynamic"` collection, and returns `None` for anything else —
so a facts pack would show no shape line at all. Add the third branch, reusing
`dynamic_shape`'s structure:

```python
        for collection in self.collections:
            if collection.builder == "dynamic":
                return dynamic_shape(dict(collection.params), collection.title)
            if collection.builder == "facts_family":
                return facts_family_shape(dict(collection.params), collection.title)
        return None
```

and a `facts_family_shape` beside `dynamic_shape` (`catalog.py:124-160`),
written to that function's own contract: derived rather than restated, rendered
through the same `dynamic_titles.render_title` the family names collections
with, so the sentence in the picker cannot describe a shape the builder will not
produce. Read `dynamic_shape` whole before writing it and mirror it, including
`DYNAMIC_KEY_PLACEHOLDER` and `DYNAMIC_LIBRARY_TYPE`.

**Zero frontend changes:** `years_title` is an existing payload key the picker
already renders (10b decision C3), so there is nothing to add in
`frontend/src/pages/CatalogPanel.tsx`. Verify by reading it; if it turns out the
key is not rendered for a non-award preset, STOP and report rather than
redesigning the picker in this task.

- [ ] **Step 8: Run the catalog suite to see it pass**

```bash
docker compose -p pprefetcht4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_catalog.py tests/test_collection_facts_family.py tests/test_config_schema.py -q 2>&1 | tee /app/.superpowers/run-pprefetcht4-green.log"
```

Expected: PASS, including
`test_every_ready_preset_at_once_never_builds_one_title_twice` (`:522`) and
`test_a_ready_presets_expansion_is_definitions_the_config_would_accept`
(`:471`) — the two that would catch a pack whose definitions the config
refuses, or whose titles collide with another shipped pack's.

- [ ] **Step 9: Mutation proof — the coverage sentence**

```bash
cp src/autoposter/collections/catalog.py /tmp/catalog.py.bak
# edit: delete the word "drift" from one new pack's description
docker compose -p pprefetcht4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_catalog.py::test_every_facts_pack_states_its_coverage_story -q"
# expected: RED
cp /tmp/catalog.py.bak src/autoposter/collections/catalog.py
cmp /tmp/catalog.py.bak src/autoposter/collections/catalog.py && echo RESTORED-EXACT
docker compose -p pprefetcht4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_catalog.py -q"
# expected: GREEN
```

- [ ] **Step 10: Full suite, golden gate, commit, tear down**

```bash
docker compose -p pprefetcht4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pprefetcht4-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pprefetcht4-full.log"
docker wait pprefetcht4-full
docker compose -p pprefetcht4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "ruff check src tests"
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
```

The golden diff must be **empty** — `collections.presets` defaults to empty, so
every pack this phase ships is invisible to the golden gate. Assert it with the
real run rather than assuming it.

```bash
git add src/autoposter/collections/packs.py src/autoposter/collections/catalog.py \
        tests/test_collection_catalog.py
git commit --no-gpg-sign -m "feat(collections): the franchise and location packs, on the facts enumeration"
docker compose -p pprefetcht4 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## Task 5: The wrap

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (rows 155,
  189, 192, 193; new rows for the Phase-B carries)
- Create: `.superpowers/sdd/p-prefetch-pr-body.md`
- Modify: `README.md` / `config/autoposter.example.yaml` **only if** a shipped
  setting or preset key is documented there and is now wrong

**Interfaces:**
- Consumes: every prior task's report — specifically Task 1 §3 (the gate
  verdict), §6 (the endpoint spelling and whether it is supported), Task 3b's
  valve outcome, and Task 4's ship-or-re-file branch.
- Produces: nothing any task reads. This is the last task.

**The rule this task exists to keep.** A roadmap row is closed when the work is
DONE, not when a branch is merged, and a row that stays open says what is
actually left. Every claim written here must be traceable to a task report or a
run log in this branch. **Do not write a measurement no task took.**

- [ ] **Step 1: Re-measure coverage before writing any coverage claim**

C6's closing instruction: the wrap re-checks live coverage before writing the
convergence claim into anything permanent.

```bash
docker compose -p pprefetcht5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test python - <<'PY'
import asyncio, os
from sqlalchemy import func, select
from autoposter.db.base import make_engine, make_session_factory
from autoposter.db.models import ItemFacts, MediaItem

async def main():
    engine = make_engine(os.environ["AUTOPOSTER_TEST_DATABASE_URL"])
    factory = make_session_factory(engine)
    async with factory() as session:
        for kind in ("movie", "show"):
            total = (await session.execute(
                select(func.count()).select_from(MediaItem)
                .where(MediaItem.kind == kind)
            )).scalar_one()
            attempted = (await session.execute(
                select(func.count()).select_from(MediaItem)
                .where(MediaItem.kind == kind,
                       MediaItem.facts_attempted_at.isnot(None))
            )).scalar_one()
            with_facts = (await session.execute(
                select(func.count()).select_from(ItemFacts)
                .join(MediaItem, MediaItem.id == ItemFacts.item_id)
                .where(MediaItem.kind == kind,
                       ItemFacts.tmdb_original_language.isnot(None))
            )).scalar_one()
            print(kind, "total", total, "attempted", attempted, "with lang", with_facts)
    await engine.dispose()

asyncio.run(main())
PY
```

**If the database this runs against is the empty isolated test database** — it
will be — say so and write **no live coverage number** anywhere. The honest
sentence is the mechanism ("the family reflects what the drift sweep has
visited; at 500 items a week a 16,000-item library converges in about eight
months"), which is arithmetic over a shipped default and not a measurement. The
sentence that would be dishonest is "coverage is currently N%". Record which was
written.

- [ ] **Step 2: Close rows 189 and 192**

Row 189 (`origin_country`/`original_language`) and row 192 (`tmdb_collection`)
both say the same thing in different words: the types need "the metadata-prefetch
budget the same rows 155/180 keep running into — a per-item external fetch across
the whole library, with its own cache, its own rate limit and its own failure
mode when TMDb does not know an item". Every clause of that is now true and
shipped, so each row closes with **what shipped, in that row's own terms**:

- the per-item fetch: unchanged, and that is the point — the three fields ride
  the `/movie/{id}` and `/tv/{id}` reads the facts pipeline already made, so the
  budget bought zero new requests;
- the cache: `provider_cache`, already there, and a refusal never enters it;
- the rate limit: `tmdb_rate_state` and `operations.tmdb_backoff_seconds`, on
  the database clock so pods share one window;
- the not-found behaviour: `facts_attempted_at` is stamped on every attempt, so
  "TMDb has nothing for this item" and "nobody has looked" are now distinct
  states, and the enumeration's missing-value rule reads the difference;
- what shipped ON that budget: `builder: facts_family` and its three types, as
  a LIST family rather than a dynamic-type row — with one sentence saying why,
  pointing at `collections/facts_family.py`'s module docstring.

**Close each row only for what actually shipped.** If Task 3b took the valve,
neither row closes: both are rewritten to say the seam and the budget shipped,
the family did not, and the family's own row (Step 4) is what they now wait on.

- [ ] **Step 3: Correct row 155's endpoint spelling**

Only if Task 1 §6 supports it. The row writes the batched read as
`/library/metadata?ratingKey=a,b,c`; the path-segment form is
`/library/metadata/{k1},{k2},{k3}`. Correct the spelling **and say in the row
where the correction came from** — this phase's capture record, because the
phase was in the neighbourhood — rather than implying a live Plex probe nobody
ran. If §6 says the evidence does not support it, leave the row alone and say
so in the task report.

Row 155 otherwise **stays open**: the batched read itself, the reload budget and
the six stranded attributes are Phase B.

- [ ] **Step 4: File the Phase-B carries, the fifth carry included**

New rows, each in the roadmap's own row format (number, title, evidence,
size, parity impact, blockers). At minimum:

1. **The Plex-side batched read family** — Phase B's own scope, out of C1: the
   batched `/library/metadata/{k1,k2,…}` read, the credits cache, and the
   decision (recorded as a decision, not discovered later) of whether that cache
   is `item_facts` or a table of its own. Rows 155/169/194 point at it.
2. **The fifth carry, counted this time**: 9c's T2 Minor 7, the unbounded
   `require_matches` fetch (`.superpowers/sdd/branch-review-9c.md:144`), whose
   filed destination was "a follow-up row beside the metadata-prefetch/budget
   family (155/180)". That family is this phase, and the carry has now been
   deferred five times without a row. File it as a row: what it is, that it
   matches `plex_search.build`'s own precedent, that it fires only on definition
   change, and that this is its fifth deferral.
3. **The facts-family row**, only if Task 3b took C7's valve — what shipped
   (the seam and `facts_value`), what did not (the family), and what was
   learned about why.
4. **The region/continent name→code row**, only if Task 4's §4 verdict forced
   the re-file — what table is missing and where it would come from.

- [ ] **Step 5: Leave 180, 175, 91, 155, 169 and 194 honestly open**

Each of these rows currently points at "the metadata-prefetch budget" as its
blocker, and that phrase now means something narrower than it did. Edit each
row's blocker clause so it points at **Phase B's row** rather than at a budget
that has shipped — one clause each, no rewrites. A row that reads "waiting on
the prefetch budget" after the prefetch budget shipped is a row nobody can act
on.

Row 193 (`tests/test_worker.py`'s intermittent) gains a **sixth sighting** only
if a task actually saw one; each task was told to record it. If none did, say so
— "no sighting across six full-suite runs on this branch" is itself evidence for
that row and belongs in it.

- [ ] **Step 6: Write the PR body**

Create `.superpowers/sdd/p-prefetch-pr-body.md`. It carries:

- what shipped, in the order it shipped, one paragraph per task;
- the **acceptance procedure** an operator follows to see it work: enable a
  pack, run a collections pass in preview, read the family's coverage line, then
  enable `apply_to_plex`. Written as commands, not as prose;
- what did NOT ship and why (Phase B, and anything the valve deferred);
- the migration note: one alembic revision, three columns and one table, all
  additive, `server_default` on the NOT NULL one so an ADD COLUMN against a
  populated `item_facts` cannot fail;
- the settings added: `operations.tmdb_backoff_seconds`, with its default, its
  off switch, and what 0 means;
- **no AI attribution of any kind, and no URLs or tokens.**

- [ ] **Step 7: Final gate**

```bash
docker compose -p pprefetcht5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pprefetcht5-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pprefetcht5-full.log"
docker wait pprefetcht5-full
docker compose -p pprefetcht5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "ruff check src tests"
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
git diff --stat origin/main -- src/autoposter/collections/dynamic_keys.py \
  src/autoposter/collections/dynamic_titles.py src/autoposter/collections/builders/dynamic.py
git diff --stat origin/main
git log --oneline origin/main..HEAD
```

The first two diffs must be empty. Paste the last two into the task report:
the full diffstat and the commit list are what a reviewer reads first.

- [ ] **Step 8: Commit and tear down**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): rows 189 and 192 close on the prefetch budget; Phase B's carries filed"
docker compose -p pprefetcht5 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

The PR body lives under `.superpowers/` and is gitignored — nothing to stage.

---

## Self-review

Run against the adjudications with fresh eyes, as the writing-plans skill
requires.

**Spec coverage.**

| adjudication | where it is implemented |
| --- | --- |
| C1 (scope: Phase A ships, Phase B does not) | "What this phase is NOT"; T5 Steps 4–5 file and re-point Phase B |
| C2 (probe first, and it gates) | T1 Steps 3–5; the GATE at T2's header |
| C3 (three fields, our names, `ItemFacts`) | T2 Steps 3, 4, 9, 11; constraint 1 |
| C4 (break the empty-gather short-circuit) | T2 Steps 7, 11 — the stamp above the short-circuit, red-firsted |
| C5 (DB clock, multi-pod, refusals never cached) | T2 Steps 14–19, 21; constraint 5 |
| C6 (the enumeration seam) | T3a whole; FLAG 1 records the table it lives in and why |
| C7 (the three types, list families, the valve) | T3b whole; the valve is T3b's own header |
| C8 (packs flip, or re-file) | T4, and its re-file rule is the task's first paragraph |
| C9 (row bookkeeping, the fifth carry counted) | T5 Steps 2–5 |
| C10 (5–6 tasks) | six, with T3 split; FLAG 3 records why |

**Placeholder scan.** Three places carry `...` deliberately, and each names its
source and its procedure rather than deferring a decision: T4 Step 1's pack
tables (from capture record §4 — the same discipline `packs.py`'s own docstring
states), T4 Step 2's checksums (computed from the tables once they exist, the
procedure the shipped digests document), and T4 Steps 4–5's cap arithmetic (from
§5). Nothing else in the plan says TBD, "handle edge cases", or "similar to Task
N".

**Type consistency.** Checked across tasks:
`GatheredFacts.tmdb_origin_country` / `ItemFacts.tmdb_origin_country` /
`FactsField.column` agree; `FACTS_FIELDS` keys (`origin_country`,
`original_language`, `tmdb_collection`) are the same three strings as
`FACTS_FAMILY_TYPES` keys and as `FactsValueParams.field`'s accepted values;
`FactsFamilyType.member_builder` values (`facts_value`, `tmdb_collection`) are
both real `type_name`s — the first from T3a, the second shipped at
`builders/tmdb.py:261`; `family_label`/`generated_titles` match the names
`engine._family_state` reads by `getattr` (`engine.py:698`, `:703`);
`FAMILY_LABEL_PREFIX` is defined twice in the codebase on purpose and the test
at T3b Step 2 asserts the two differ.

**Two things a reviewer should push on**, named here rather than discovered:

1. **T3b is the biggest task in the phase** and it is the one the valve exists
   for. If its review finds the label inheritance or the sweep protocol needs
   more than the two additive `BuilderContext` fields, take the valve — that is
   what it is for, and Task 4's re-file rule and Task 5's Step 4 already have
   somewhere to put the outcome.
2. **The franchise family's merged buckets build one franchise and report the
   rest** (T3b, Step 7). That is a real narrowing relative to upstream, whose
   `addons` genuinely merge two collections' parts into one — and it is
   forced by the shipped `tmdb_collection` builder taking a single id. The plan
   reports it per bucket rather than hiding it, and Task 4 must state it in the
   franchise row's description as the opinion it is (constraint 10). If Task 1
   §4 finds the addon table is large enough that most buckets are merges, that
   is a re-file signal for the pack, not a reason to widen the builder inside
   this phase.
