# Franchise Grouping & Precedence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop two active presets fighting over one Plex collection title —
the curated definition keeps the title, the enumerated `facts_family` unit
stands down and says so — and move the Universes and DC packs into the
`franchises` band beside the family they were splitting.

**Architecture:** Nothing structural is built and `groups.group_for`'s
precedence is not touched. One new read-only field on `BuilderContext`
carries the titles the rest of the config already manages (the set
`engine.definition_titles_for` already computes for the delete sweep) into
`facts_family.expand`, which extends its existing refuse-and-report idiom by
one filter: a unit whose title is contested is skipped and named in the pass
report. Two `category=` string literals move `content_universes` and
`content_dc` from `content` to `franchises`, and their members' sort prefixes
self-heal through the definition hash on the next real pass.

**Tech Stack:** Python 3.14, pydantic v2, pytest, ruff, Docker Compose. **No
new runtime dependency, no new config key, no Alembic migration, no new
endpoint, no frontend change.**

---

## Read this first — what governs this plan

1. `.superpowers/sdd/p-franchise2-facts.md` **C1–C4** — the controller
   adjudications. They are SETTLED. This plan implements them; the
   "Adjudications this plan makes beyond the facts" section below flags every
   design choice the facts left open, and **CORRECTIONS** flags the two places
   the inputs disagree with the tree.
2. `.superpowers/sdd/p-franchise-band-diagnosis.md` — the line-cited root
   cause. Its citations were taken at `20ac5d9`; **`origin/main` has since
   moved to `94ee181` and this plan re-cites every site at `94ee181`.** Where
   a line number below differs from the diagnosis, this plan's number is the
   current one.
3. `.superpowers/sdd/p-defimg-probe.md` §5–§6 — the `universe/` listing the
   DCEU entry is ratified against.
4. `.superpowers/sdd/p-massops1-task-1-report.md` — the interleaved phase's
   T1 report. Read for the engine.py collision assessment below; its measured
   `4318` is **not** this phase's baseline (see "Branch and cut point").
5. **The tree itself.** Every `file:line` in this plan was read at
   `origin/main` = `94ee181`. The working tree at the time of writing was on
   `feat/mass-ops-1`, which is **behind** `origin/main` by two commits and
   carries four files this phase does not touch — do not read the mechanism
   sites from whatever branch you happen to be standing on.

---

## CORRECTIONS — read before Task 1

**C-1 — the diagnosis' line numbers are stale by two commits.** The diagnosis
cites `origin/main` at `20ac5d9`. `origin/main` is now `94ee181`
(`20ac5d9` + `94ee181`), and `20ac5d9` edited `catalog.py`. The sites this
plan touches, re-read at `94ee181`:

| Diagnosis says | Actually at `94ee181` |
|---|---|
| `catalog.py:968-976` `_UNIVERSE_LISTS` | `catalog.py:968-981` |
| `catalog.py:982-985` `_DC_LISTS` | `catalog.py:983-987` |
| `catalog.py:990,1004` the two `category="content"` | `catalog.py:992` and `catalog.py:1046` |
| `catalog.py:1178` `title="Franchises"` | `catalog.py:1178` (unchanged) |
| `catalog.py:1117-1118` `category="franchises"` | `catalog.py:1117-1118` (unchanged) |
| `groups.py:415-435` `preset_groups` | `groups.py:415-435` (unchanged) |
| `groups.py:438-464` `group_for` | `groups.py:438-464` (unchanged) |
| `engine.py:1311-1358` `definition_titles` | `engine.py:1311-1364` |
| `builders/facts_family.py:434-443` unit construction | `facts_family.py:434-443` (unchanged) |

**C-2 — "the Regions/Continents/Countries latent contest" is only two-thirds
coverable, and the facts' C1.1 overstates it.** `Regions` and `Continents`
are both `builder: facts_family` (`catalog.py:1724`, `catalog.py:1771`), so
this phase's mechanism guards them against each other. **`Countries` is
`builder: dynamic`** (`catalog.py:1665`), a SMART builder
(`builders/dynamic.py:502`, `smart = True`) that defines **no `titles()`
lister** — so `engine.definition_titles` files it under
`titles |= {definition.title}` (`engine.py:1346`) and contributes only the
string `"Countries"`. Its member titles ("Antarctica", "Micronesia") come
from a live Plex tag enumeration and are invisible both to the static half of
the guard and to the `facts_family` run-cache ledger, because a smart builder
never calls `expand`. **Resolution:** the phase guards Regions↔Continents (a
real, previously-silent contest) and does NOT guard either against
`Countries`. Task 3's row records this precisely rather than claiming the
whole class. No extra machinery is built for it: the three packs' own
descriptions already disclose the overlap in prose
(`catalog.py:1647-1654`, `1696-1703`, `1748-1753`).

---

## Branch and cut point

- Branch name: **`fix/franchise-grouping`**, cut from **`origin/main`**.

**Why `origin/main` and not `feat/mass-ops-1`'s tip (the facts' C4 delegates
this decision here):**

1. **`origin/main` is AHEAD of `feat/mass-ops-1`, not behind it.**
   `git merge-base origin/main feat/mass-ops-1` is `322cb3f`; `origin/main`
   carries two commits mass-ops does not: `20ac5d9` (raises the franchise
   family's `max_collections` 250 → 500) and `94ee181` (fixes the
   `test_collection_buckets` `sys.modules` leak that made
   `_assert_no_bucket_collisions` flake under xdist). **Both matter to this
   phase specifically** — `20ac5d9` is the commit that surfaced the contest
   this phase fixes, and `94ee181` is what makes this phase's full-suite runs
   trustworthy. Stacking on mass-ops' tip would build the fix on a base that
   lacks both.
2. **The files are disjoint but for `engine.py`, and there the hunks do not
   collide.** See the collision assessment below.
3. **Independent merge.** Either PR can land first; neither rebases the other.

```bash
git fetch origin
git rev-parse origin/main                 # expect 94ee181... — record it
git show origin/main:src/autoposter/collections/builders/facts_family.py | grep -q "_generated_key" && echo FAMILY-PRESENT
git show origin/main:src/autoposter/collections/engine.py | grep -q "def definition_titles_for" && echo TITLES-PRESENT
git show origin/main:src/autoposter/collections/builders/base.py | grep -q "class BuilderContext" && echo CTX-PRESENT
git show origin/main:src/autoposter/collections/catalog.py | grep -c 'category="content"'   # expect 6
git show origin/main:src/autoposter/collections/default_images.py | grep -q "UNIVERSE_CODES" && echo CODES-PRESENT
git checkout -b fix/franchise-grouping origin/main
git rev-parse HEAD
```

All four markers must print and the count must be `6`. If `FAMILY-PRESENT` or
`TITLES-PRESENT` is missing, the cut point predates the machinery this phase
extends — **STOP and report**; do not cut from an older branch.

### engine.py collision assessment (mass-ops-1 vs this phase)

`feat/mass-ops-1` changed `engine.py` in four places
(`git diff 322cb3f feat/mass-ops-1 -- src/autoposter/collections/engine.py`):

| mass-ops hunk | Line at `322cb3f` | What |
|---|---|---|
| 1 | `61` | one added import of `posters.LOCAL_ASSET_KIND` |
| 2 | `74` | `LOCAL_ASSETS_RESULT_TITLE` constant |
| 3 | `578` | the row-37 unmanaged-poster pass, **after** the delete sweep inside `run_library` |
| 4 | `1121`, `1129` | `_sweep`'s `kind in ("operator", LOCAL_ASSET_KIND)` guard + comment |

This phase changes `engine.py` in two places, both inside `run_library`'s
**setup** block: line `377` (one added assignment beside `group_index`) and
line `379-390` (`context()` gains one keyword). The nearest mass-ops hunk is
#3 at line `578` — **190 lines away, past the entire per-definition loop
(which ends at line 537) and past the separator and sweep blocks.** Hunk #1
at line 61 is 316 lines away. A three-way merge sees no overlapping context
window in either direction. **Verdict: no collision. Merge order is free.**

Every other file is disjoint: mass-ops touched `posters.py`, `schema.py`,
`writer.py`, `pipeline.py`, `jobs.py`, `app.py`, `api/routes.py` and four
test files; this phase touches `catalog.py`, `groups.py`,
`builders/facts_family.py`, `builders/base.py`, `default_images.py` and
five other test files. Zero intersection.

---

## Global Constraints

Every task's requirements implicitly include this section. These are the
facts' C2, verbatim in substance.

1. **The precedence rule is RED-first, and the RED asserts BOTH sides.** A
   fabricated contest where (a) the enumerated unit must NOT be built, and
   (b) the curated definition's collection must be provably untouched — no
   returned unit for that title, and the family's delete-sweep handle making
   no claim on it. A test that asserts only (a) is half the law.
2. **`groups.group_for`'s precedence is NOT flipped.** The index and the
   builder table still outrank `parent`, pinned by
   `tests/test_collection_groups.py:799-813`
   (`test_the_parent_never_outranks_the_index_or_the_builder_table`). That
   test must still pass, unedited, at the end of every task. If a change
   makes it red, the change is wrong.
3. **The category move is pinned by band tests for BOTH units and the
   separator**, at `!050_`, and by a **poster-mapping survival test** —
   `universe/` codes must still resolve for every `content_universes` and
   `content_dc` definition after the move.
4. **The prefix self-heal is pinned by a definition-hash test**, not asserted
   in prose: two different group prefixes over the same definition must
   produce two different `lists._members_hash` values.
5. **The DCEU entry is pinned against the probe doc's own sample**
   (`p-defimg-probe.md` §6 `universe/`, which lists `dcu.jpg`), and the probe
   doc's "left OUT, not guessed" note is **updated to record the operator
   ratification** — never silently contradicted.
6. **No behavior change for a config without the contested presets.** A
   config running `content_franchises` alone, or `location_region` alone,
   must build exactly what it builds today. Every task carries a test that
   says so.
7. **Every skip names the reason and the survivor.** A report line that says
   only "skipped" is half a message: each names the family, the contested
   title, and that the other definition keeps it. It is an expected
   steady-state note (the facts' "INFO not WARNING"), not a failure — it goes
   through the existing `notes()` list, which is the pass report, and
   `facts_family` raises nothing and logs nothing (the module has no logger:
   `grep -c logger src/autoposter/collections/builders/facts_family.py` is 0,
   and this phase does not add one).
8. **Suites stated-then-measured.** Task 1 Step 1 MEASURES the baseline at
   `origin/main`. **~4303 is the hypothesis, not the fact** — mass-ops T1's
   `4318` is `4303 + 15` on a DIFFERENT base (`322cb3f`) and is not this
   phase's number. Every later task states its expected count before running
   and reconciles any gap in its report.
9. **Container discipline.** Unique compose project per task (`pfr21`,
   `pfr22`, `pfr23`), always with the `.superpowers/isolated-db.yml` overlay.
   Tee output to a path under `/app/.superpowers/` inside the container —
   never rely on streamed stdout (`rtk proxy` filters streamed logs and
   `--rm` deletes the container). A long run uses **no `--rm`**, is started
   detached and waited on with a foreground `docker wait`, and the log is read
   back from the host. Teardown is `docker compose -p <project> down` —
   **never** `down -v`.
10. **Commits** are conventional, `--no-gpg-sign`, staged **by name** (never
    `git add -A`), and carry **no AI attribution** of any kind — no
    `Co-Authored-By`, no tool name, nothing. Same for the PR body.
11. **Read to true EOF before appending to a test file.** A limit-truncated
    `Read` hid a file's last line in the default-images phase and the
    implementer then misattributed it to its own edit. `wc -l` the file, then
    read the last 20 lines, before appending.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `src/autoposter/collections/builders/base.py` | `BuilderContext.managed_titles: frozenset[str] = frozenset()` + docstring paragraph | T1 |
| `src/autoposter/collections/builders/facts_family.py` | `_GENERATED_PREFIX`; the contested-unit filter in `expand`; module docstring line | T1 |
| `src/autoposter/collections/engine.py` | `managed_titles` computed once per library beside `group_index`; threaded through `context()` | T1 |
| `tests/test_collection_facts_family.py` | three added tests | T1 |
| `src/autoposter/collections/catalog.py` | two `category=` literals; one comment sentence | T2 |
| `src/autoposter/collections/groups.py` | one comment sentence (no code) | T2 |
| `src/autoposter/collections/default_images.py` | one `UNIVERSE_CODES` entry + its comment | T2 |
| `tests/test_collection_groups.py` | three added tests | T2 |
| `tests/test_collection_catalog.py` | `CATALOG_CHECKSUM` two numbers | T2 |
| `tests/test_collection_default_images.py` | the DCEU assertion flipped | T2 |
| `tests/test_collection_poster_wiring.py` | one added test | T2 |
| `.superpowers/sdd/p-defimg-probe.md` | ratification note under §6 | T2 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | rows 221 and 222 | T3 |
| `.superpowers/sdd/progress.md` | wrap line | T3 |

---

## Adjudications this plan makes beyond the facts

Each is a design choice the facts did not settle. Flagged so a reviewer can
reject one without rejecting the task.

- **A1 — the contested set is TWO unions, not one, and the second is the
  run cache.** The facts say "using the engine's already-computed
  definition-title data". That data (`engine.definition_titles_for`) sees
  every *curated* definition's own title — which is exactly the franchise
  contest (`content_universes`/`content_dc` expand to eight and three real
  `CollectionDefinition`s, each contributing its title at `engine.py:1355`).
  It does **not** see one `facts_family` family's member titles, because a
  family contributes only its placeholder. So Regions↔Continents needs a
  second source: the pass's own `facts_family:generated:*` run-cache records,
  which **already exist** (`facts_family.py:398`) and are written by every
  family for the delete sweep. Reading them back is five lines and no new
  state. Ordering is `config.collections.presets` order — deterministic, and
  a strict improvement over the current silent every-pass fight.
- **A2 — the filter sits AFTER the `max_collections` check, not before**
  (`facts_family.py:379-389`, then the new filter, then line 397's
  `generated`). The cap is a runaway-*enumeration* guard, so it should judge
  what the family enumerated, not what survived a contest. This is also the
  strictly smaller diff.
- **A3 — an all-contested family returns `[]` and writes no `generated`
  record.** That makes `generated_titles` answer `None`, the module's
  documented FAIL-CLOSED value (`facts_family.py:104-119`), so the delete
  sweep does not treat "every title went to somebody else" as "the operator
  narrowed the family" and take the collections. Consistent with the four
  existing family-level refusals.
- **A4 — the "curated side untouched" assertion is made through the two
  handles by which this family could otherwise reach that Plex object**: the
  returned units (no unit ⇒ no `_run_one` ⇒ no `reconcile_list_collection`
  against that title) and the sweep's `generated` set (no claim ⇒ no delete).
  An engine-level integration would need a database, a Plex section and a
  TMDb triple-fake for no coverage those two do not already give.
- **A5 — the facts' C1.4 guard is SATISFIED by the skip lines; no extra
  machinery is built.** The lines name the family, the title and the
  survivor, and land in the same pass report an operator already reads for
  over-cap and unnamed-franchise refusals. A config-time check cannot see the
  enumerated titles at all (`engine.py:1348-1355` — `facts_family` defines no
  `TITLE_PATTERN`, so the validator sees only `"Franchises"`), which is the
  diagnosis' own §3 finding. YAGNI.
- **A6 — `engine.definition_titles_for` is called with an EMPTY
  `collections` argument.** Its only use of that argument is the
  `TITLE_PATTERN` branch (`engine.py:1348-1353`), which matches against
  Plex's live listing. Calling `listing()` in `run_library`'s setup block
  would force the section listing before the first definition ran, on every
  pass including narrow test callers. No `TITLE_PATTERN` builder
  (`imdb_award_years` alone) is in the contest class, so nothing is lost. The
  code carries a comment saying exactly this.
- **A7 — this phase takes roadmap rows 221 and 222.** It wraps before
  mass-ops-1 does, so its rows are next in sequence. The wrap's `progress.md`
  line records that mass-ops-1's two carried rows (the refused-family frozen
  members; `credits_family`'s pre-searchability top-N) shift to **223 and
  224**, so no gap or collision is left in the table.

---

## Task 1: The precedence rule — a contested unit stands down

**Files:**
- Modify: `src/autoposter/collections/builders/base.py:170` (one field), and the `BuilderContext` docstring at `:146-153`
- Modify: `src/autoposter/collections/builders/facts_family.py:88-89` (a prefix constant) and `:391-398` (the filter)
- Modify: `src/autoposter/collections/engine.py:372-390` (compute once, thread through `context()`)
- Test: `tests/test_collection_facts_family.py` (three tests appended)

**Interfaces:**
- Produces: `BuilderContext.managed_titles: frozenset[str]` — every collection
  title the REST of this library's config already manages, defaulting to
  `frozenset()` (a direct caller contests nothing).
  `facts_family._GENERATED_PREFIX: str = "facts_family:generated:"`.
- Consumes: `engine.definition_titles_for(definitions, collections, library,
  library_type, config) -> set[str]` (already exists, `engine.py:1290-1308`);
  `facts_family._generated_key(label) -> str` (already exists, `:88-89`);
  `facts_family.notes(run_cache) -> list[str]` (already exists, `:92-101`).

---

- [ ] **Step 1: Cut the branch and MEASURE the baseline**

Run the whole "Branch and cut point" block above. Paste `git rev-parse
origin/main`, the four markers, the `grep -c` count and `git rev-parse HEAD`
into the task report. If any probe fails, STOP.

Then commit this plan file as-is (it is untracked at task start; do not
rewrite it):

```bash
git add docs/superpowers/plans/2026-09-01-franchise-grouping.md
git commit --no-gpg-sign -m "docs(plan): franchise grouping and precedence implementation plan"
```

Then measure — do not assume the number:

```bash
docker compose -p pfr21 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pfr21-base test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-fg-t1-baseline.log'
docker wait pfr21-base
docker cp pfr21-base:/app/.superpowers/run-fg-t1-baseline.log .superpowers/run-fg-t1-baseline.log
docker rm pfr21-base
tail -5 .superpowers/run-fg-t1-baseline.log
```

Expected: a green summary line. **~4303 is the hypothesis, not the fact** —
record the measured number verbatim in the task report as `BASELINE=<n>`.
Note explicitly in the report that this is NOT mass-ops T1's 4318, which was
measured on a different base.

- [ ] **Step 2: Read the tail of the test file before appending**

```bash
wc -l tests/test_collection_facts_family.py
tail -20 tests/test_collection_facts_family.py
```

Global Constraint 11. Record the line count in the report; every test below is
appended after the true last line.

- [ ] **Step 3: Write the failing tests (all three)**

Append to `tests/test_collection_facts_family.py`:

```python
async def test_a_contested_unit_is_skipped_and_reported(session):
    """The precedence rule. A curated definition elsewhere in this config
    already manages 'Fast & Furious' (``catalog._UNIVERSE_LISTS``), and the
    enumeration finds the same franchise through TMDb. Curated wins: the
    family builds the rest and names what it left alone, the same way it
    already names an over-cap fan-out or a franchise TMDb cannot title."""
    await _item(session, "1", tmdb_origin_country=["US"])
    await _item(session, "2", tmdb_origin_country=["FI"])
    definition = _definition(
        title="Regions", params={"type": "origin_country"},
    )
    ctx = _ctx(
        session, definition,
        managed_titles=frozenset({iso_names.COUNTRY_NAMES["US"]}),
    )
    units = await FactsFamilyBuilder().expand(ctx)

    # The contested unit is not built...
    assert [unit.title for unit in units] == [iso_names.COUNTRY_NAMES["FI"]]
    # ...and the pass report says which one, and who kept it.
    reported = "\n".join(facts_family_module.notes(ctx.run_cache))
    assert iso_names.COUNTRY_NAMES["US"] in reported
    assert "already managed by another definition" in reported


async def test_the_contested_title_stays_the_curated_definitions_alone(session):
    """The other half of the law, asserted through the only two handles by
    which this family could reach that Plex collection: a returned unit (which
    would drive ``engine._run_one`` and rewrite its membership) and the delete
    sweep's ``generated`` record (which would let ``_sweep`` delete it as a
    narrowed family's leftover). Neither names the contested title."""
    await _item(session, "1", tmdb_origin_country=["US"])
    await _item(session, "2", tmdb_origin_country=["FI"])
    definition = _definition(
        title="Regions", params={"type": "origin_country"},
    )
    contested = iso_names.COUNTRY_NAMES["US"]
    managed = frozenset({contested})
    ctx = _ctx(session, definition, managed_titles=managed)
    units = await FactsFamilyBuilder().expand(ctx)

    assert contested not in {unit.title for unit in units}
    assert contested not in generated_titles(ctx.run_cache, definition)
    assert generated_titles(ctx.run_cache, definition) == {
        iso_names.COUNTRY_NAMES["FI"]
    }
    # Read, never edited: the rest of the config is not this builder's to touch.
    assert ctx.managed_titles == managed


async def test_two_enumerated_families_in_one_pass_do_not_contest_each_other(
    session,
):
    """The location latent contest, guarded. ``Regions`` and ``Continents``
    are both ``facts_family`` and both title with the bare name, so the same
    country name can be a bucket in each -- and neither is visible to
    ``engine.definition_titles`` (a family contributes only its placeholder
    title). The pass's own ``generated`` records are the ledger: whichever
    family runs first keeps the title, the second stands down and reports.
    """
    await _item(session, "1", tmdb_origin_country=["US"])
    first = _definition(title="Regions", params={"type": "origin_country"})
    second = _definition(title="Continents", params={"type": "origin_country"})
    run_cache: dict = {}

    first_units = await FactsFamilyBuilder().expand(
        _ctx(session, first, run_cache=run_cache)
    )
    second_units = await FactsFamilyBuilder().expand(
        _ctx(session, second, run_cache=run_cache)
    )

    assert [unit.title for unit in first_units] == [iso_names.COUNTRY_NAMES["US"]]
    assert second_units == []
    reported = "\n".join(facts_family_module.notes(run_cache))
    assert iso_names.COUNTRY_NAMES["US"] in reported
    # Fail-closed: the second family never decided, so the sweep must not read
    # its empty output as "the operator narrowed it" and delete the first
    # family's collection.
    assert generated_titles(run_cache, second) is None


def test_the_engine_computes_the_contested_set_from_the_definitions_it_has():
    """The wiring, at the seam. ``definition_titles_for`` is the set the delete
    sweep and the leftovers report already share; a curated preset's own
    collections are in it by their own titles (``engine.py:1355``), which is
    exactly what makes the franchise contest visible. The empty ``collections``
    argument is deliberate -- see the call site's comment."""
    from types import SimpleNamespace

    from autoposter.collections.engine import definition_titles_for
    from autoposter.config.schema import CollectionsConfig

    curated = CollectionDefinition(
        title="Fast & Furious", builder="imdb_list",
        params={"list": "ls4102351575"},
    )
    placeholder = CollectionDefinition(
        title="Franchises", builder="facts_family",
        params={"type": "tmdb_collection"},
    )
    config = SimpleNamespace(collections=CollectionsConfig())
    titles = definition_titles_for(
        [curated, placeholder], [], "Movies", "Movie", config,
    )
    assert "Fast & Furious" in titles
    assert "Franchises" in titles
```

- [ ] **Step 4: Run the tests to verify they fail**

```bash
docker compose -p pfr21 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_facts_family.py 2>&1 | tee /app/.superpowers/run-fg-t1-red.log'
```

Expected: FAIL. The first three fail with
`TypeError: BuilderContext.__init__() got an unexpected keyword argument
'managed_titles'` (tests 1 and 2) and an assertion failure on
`second_units == []` (test 3, which passes no new kwarg and fails on
behaviour). The fourth passes already — it pins existing behaviour the rest
depends on, and its RED is not required.

Record the exact failure text in the report.

- [ ] **Step 5: Add the `BuilderContext` field**

In `src/autoposter/collections/builders/base.py`, after `definition: Any =
None` (line 170), add:

```python
    managed_titles: frozenset[str] = frozenset()
```

And in the `BuilderContext` docstring, after the `definition` paragraph that
ends `"...and for every builder that does not expand."` (line 153), insert a
blank line and this paragraph:

```
    ``managed_titles`` is every collection title the REST of this library's
    config already manages -- ``engine.definition_titles_for``'s set, the same
    one the delete sweep and the leftovers report share. Only a family builder
    that ENUMERATES its titles needs it, and it needs it for one thing: two
    independent presets may name the same real-world collection with two
    different membership rules (``content_franchises``' TMDb enumeration and
    ``content_universes``' curated list both reach "Fast & Furious"), and
    without this the two overwrite one Plex object every pass. Read-only, and
    empty for a direct caller: contesting nothing is the honest answer when
    there is no config around the call.
```

- [ ] **Step 6: Thread it through the engine**

In `src/autoposter/collections/engine.py`, after the `group_index` assignment
(line 377) and before the blank line preceding `def context(`, add:

```python
# The titles the REST of this config already manages, resolved once per
# library beside the group index above and for the same reason. A family
# builder that ENUMERATES its collections checks its units against this,
# which is what stops two presets rebuilding one Plex collection from two
# membership rules every pass (``builders/facts_family.py``).
#
# The empty ``collections`` argument is deliberate: that argument feeds only
# the ``TITLE_PATTERN`` branch, which matches against Plex's own listing, and
# calling ``listing()`` here would buy the section listing before the first
# definition ran -- on every pass, including the narrow callers that build one
# definition. No pattern-titled builder is in the contest class.
managed_titles = frozenset(
    definition_titles_for(definitions, [], library, library_type, config)
)
```

Then in the `context()` factory (lines 379-390), add one keyword after
`definition=definition,`:

```python
            managed_titles=managed_titles,
```

so the factory reads:

```python
    def context(definition: CollectionDefinition) -> BuilderContext:
        return BuilderContext(
            library=library,
            library_type=library_type,
            http=http,
            config=definition.params,
            cache=cache,
            run_cache=run_cache,
            sources=bound_sources,
            session=session,
            definition=definition,
            managed_titles=managed_titles,
        )
```

`definition_titles_for` is defined in this same module (line 1290); no import
is needed.

- [ ] **Step 7: Add the prefix constant to `facts_family`**

In `src/autoposter/collections/builders/facts_family.py`, replace lines 88-89:

```python
def _generated_key(label: str) -> str:
    return "facts_family:generated:%s" % label
```

with:

```python
# Written out as a constant because it is now READ as well as written: a family
# scans the pass's records for the titles the families BEFORE it claimed, which
# is the only place a second enumerated family's member titles exist.
_GENERATED_PREFIX = "facts_family:generated:"


def _generated_key(label: str) -> str:
    return _GENERATED_PREFIX + label
```

- [ ] **Step 8: Add the contested-unit filter**

In the same file, between the `max_collections` refusal's `return []`
(line 389) and the `# The sweep's input, written HERE` comment (line 391),
insert:

```python
        # Curated wins. Two independent presets may name one real-world
        # collection -- ``content_universes``' hand-written "Fast & Furious"
        # and this family's TMDb enumeration of the same franchise -- with two
        # different membership rules, and before this filter whichever
        # definition ran last in ``config.collections.presets`` order
        # overwrote the other's Plex object every pass (and mis-banded it on
        # the way, since ``groups.group_for`` resolves a unit's title through
        # the OTHER preset's index entry). ``group_for``'s precedence is not
        # the defect and is not touched: the defect is that this family was
        # allowed to claim a title somebody else already manages.
        #
        # Two sources, because one is blind to the other. ``managed_titles``
        # is every CURATED definition's own title, which is the franchise
        # case. The pass's own generated records are the second: a family
        # contributes only its PLACEHOLDER title to ``definition_titles``
        # (``engine.py:1348-1355``), so "Regions" and "Continents" both
        # enumerating one country name is invisible until both have run.
        # Whichever ran first keeps it; ordering is preset order, which is
        # deterministic and is a strict improvement on the silent every-pass
        # fight it replaces.
        own_key = _generated_key(family_label(definition))
        contested = set(ctx.managed_titles)
        for key, claimed in ctx.run_cache.items():
            if key.startswith(_GENERATED_PREFIX) and key != own_key:
                contested |= claimed

        kept = []
        for unit in titled:
            if unit.title in contested:
                # Reported, not raised, and not a warning: with a curated pack
                # and this family both switched on, this is the expected
                # steady state rather than anything going wrong.
                report.append(
                    "%r: %r is already managed by another definition in this "
                    "config, so this family left it alone -- that collection "
                    "keeps its own membership. Expected when a curated pack "
                    "covers something the enumeration also finds; switch the "
                    "other definition off if you want this family to build it "
                    "instead" % (definition.title, unit.title)
                )
                continue
            kept.append(unit)
        titled = kept

        if not titled:
            # Every title went to somebody else. Returning here rather than
            # falling through is what leaves ``generated`` UNWRITTEN, so
            # ``generated_titles`` answers None -- the fail-closed value -- and
            # the delete sweep does not read this as "the operator narrowed the
            # family" and take collections another definition now owns.
            report.append(
                "%r built nothing: every %s value it enumerated is already "
                "managed by another definition in this config"
                % (definition.title, params.type)
            )
            return []
```

- [ ] **Step 9: Record the new refusal in the module docstring**

In the same file, in the `**Every refusal RETURNS**` paragraph (lines 39-44),
change:

```
an empty enumeration, an all-excluded family, an over-cap fan-out, a
duplicate title and a franchise TMDb cannot name are all reported rather than
raised.
```

to:

```
an empty enumeration, an all-excluded family, an over-cap fan-out, a
duplicate title, a franchise TMDb cannot name and a title another definition
in the config already manages are all reported rather than raised.
```

- [ ] **Step 10: Run the tests to verify they pass**

```bash
docker compose -p pfr21 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_facts_family.py 2>&1 | tee /app/.superpowers/run-fg-t1-green1.log'
```

Expected: PASS, all four new tests plus every pre-existing test in the file.
A pre-existing test going red means the filter is firing where it must not —
`BuilderContext`'s default is `frozenset()` and every existing `_ctx` call
passes a fresh `run_cache={}`, so nothing should be contested.

- [ ] **Step 11: Run the no-behavior-change regression**

Global Constraint 6, and the mass-ops T1 lesson about `SimpleNamespace`
config doubles calling `run_library` directly:

```bash
docker compose -p pfr21 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_builder_engine.py tests/test_builder_knobs.py tests/test_collection_main.py tests/test_collection_leftovers.py tests/test_collection_reconcile.py tests/test_collection_groups.py tests/test_smart_filter_engine.py 2>&1 | tee /app/.superpowers/run-fg-t1-green2.log'
```

Expected: PASS, all of them. `test_the_parent_never_outranks_the_index_or_the_builder_table`
(Global Constraint 2) is in `test_collection_groups.py` and must be green and
unedited.

If any test fails with `AttributeError` reaching `definitions`, `library` or
`config` in `run_library`'s setup block, STOP and report — the new
`definition_titles_for` call would be reading something a hand-rolled double
does not carry, and the fix is a decision (not an edit to dozens of
fixtures).

- [ ] **Step 12: Lint**

```bash
docker compose -p pfr21 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'ruff check .'
```

Expected: `All checks passed.`

- [ ] **Step 13: Run the full suite and commit Task 1**

```bash
docker compose -p pfr21 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pfr21-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-fg-t1-full.log'
docker wait pfr21-full
docker cp pfr21-full:/app/.superpowers/run-fg-t1-full.log .superpowers/run-fg-t1-full.log
docker rm pfr21-full
tail -5 .superpowers/run-fg-t1-full.log
docker compose -p pfr21 down
```

Expected: green, `BASELINE + 4`. State that number before running; reconcile
any gap in the report.

```bash
git add src/autoposter/collections/builders/base.py \
  src/autoposter/collections/builders/facts_family.py \
  src/autoposter/collections/engine.py \
  tests/test_collection_facts_family.py
git commit --no-gpg-sign -m "fix(collections): an enumerated unit stands down for a title another definition manages"
```

---

## Task 2: The category move, the DCEU art, and the survival pins

**Files:**
- Modify: `src/autoposter/collections/catalog.py:79` (comment), `:992`, `:1046`
- Modify: `src/autoposter/collections/groups.py:69-70` (comment only, no code)
- Modify: `src/autoposter/collections/default_images.py:214-228` (one entry)
- Modify: `tests/test_collection_catalog.py:271`, `:273`
- Modify: `tests/test_collection_default_images.py:306-308`
- Test: `tests/test_collection_groups.py` (three tests appended), `tests/test_collection_poster_wiring.py` (one test appended)
- Modify: `.superpowers/sdd/p-defimg-probe.md` (§6 ratification note)

**Interfaces:**
- Consumes: nothing from Task 1 — this task is independent of the precedence
  rule and could be reviewed on its own.
- Produces: `catalog.BY_KEY["content_universes"].category == "franchises"` and
  `catalog.BY_KEY["content_dc"].category == "franchises"`, which
  `groups.preset_groups` turns into `index[<each of their eleven titles>] ==
  "franchises"`; `default_images.UNIVERSE_CODES["fa11en82/dc-extended-universe"] == "dcu"`.

---

- [ ] **Step 14: Read the tails of the two test files before appending**

```bash
wc -l tests/test_collection_groups.py tests/test_collection_poster_wiring.py
tail -20 tests/test_collection_groups.py
tail -20 tests/test_collection_poster_wiring.py
```

Global Constraint 11.

- [ ] **Step 15: Write the failing band tests**

Append to `tests/test_collection_groups.py`:

```python
def test_the_universe_and_dc_units_file_under_franchises():
    """The operator directive's first half. Both packs' collections are
    curated LIST definitions carrying their own titles, so they resolve
    through ``group_for``'s route 1 -- the preset index -- and the index is
    built from the preset's ``category``. Moving the category moves the band,
    with no per-definition edit."""
    index = groups.preset_groups(
        config(presets=["content_universes", "content_dc"]), "Movie",
    )
    assert index["Fast & Furious"] == "franchises"
    assert index["Marvel Cinematic Universe"] == "franchises"
    assert index["Star Wars Universe"] == "franchises"
    assert index["DC Universe"] == "franchises"
    assert index["DC Extended Universe"] == "franchises"
    assert index["In Association With DC"] == "franchises"

    unit = CollectionDefinition(
        title="Fast & Furious", builder="imdb_list",
        params={"list": "ls4102351575"},
    )
    order = groups.CANONICAL_ORDER
    assert groups.group_for(unit, index) == "franchises"
    assert groups.sort_prefix_for(unit, index, order) == "!050_"


def test_the_moved_packs_activate_the_franchise_separator_at_050():
    """The separator half, asserted beside the units: a band whose members
    moved but whose divider did not would be the exact disagreement the
    !040 split was. The universes packs alone -- no ``content_franchises`` --
    must now raise the franchises divider on their own."""
    cfg = config(
        presets=["content_universes", "content_dc"], separators=True,
    )
    definitions = [
        *catalog_definitions("content_universes"),
        *catalog_definitions("content_dc"),
    ]
    specs = groups.separator_specs(definitions, "Movie", cfg)
    franchises = next(spec for spec in specs if spec.group == "franchises")
    assert franchises.title == "Franchise Collections"
    assert franchises.sort_title == "!050_!Franchise Collections"
    # ...and nothing of theirs is left behind in the content band.
    assert "content" not in {spec.group for spec in specs}


def test_a_moved_category_changes_the_definition_hash_so_the_prefix_self_heals():
    """No migration. ``sort_prefix`` reaches the stored hash through
    ``with_derived_sort_title`` -> ``lists._settings_parts`` (which is why
    that wrapper exists at all, ``groups._DerivedSortTitle``'s docstring), so
    a collection whose band changed is not short-circuited as already-current
    on the next real pass -- it is re-reconciled and its sort title rewritten.
    This pins the mechanism the phase relies on rather than trusting it."""
    from autoposter.collections import lists

    definition = CollectionDefinition(
        title="Fast & Furious", builder="imdb_list",
        params={"list": "ls4102351575"},
    )
    items = [SimpleNamespace(ratingKey=1), SimpleNamespace(ratingKey=2)]
    order = groups.CANONICAL_ORDER
    assert groups.sort_prefix("content", order) == "!040_"
    assert groups.sort_prefix("franchises", order) == "!050_"

    def hashed(group):
        return lists._members_hash(items, None, "sync", groups.with_derived_sort_title(
            definition, groups.sort_prefix(group, order), definition.title, None,
        ))

    assert hashed("content") != hashed("franchises")
```

`catalog_definitions` does not exist yet; add this helper immediately below
the `config()` helper at the top of the same file (line 12):

```python
def catalog_definitions(key, library_type="Movie"):
    """A catalog preset's own definitions -- what the engine would be handed
    for a config that ticked that key."""
    from autoposter.collections.catalog import BY_KEY

    return BY_KEY[key].definitions(library_type)
```

- [ ] **Step 16: Write the failing DCEU and poster-survival tests**

In `tests/test_collection_default_images.py`, replace lines 306-308:

```python
    # 'In Association With DC' has no upstream entry and must not borrow one:
    # §5 names `dca` as DC ANIMATED, a different continuity.
    assert "fa11en82/in-association-with-dc" not in UNIVERSE_CODES
```

with:

```python
    # The DCEU wears `dcu`. p-defimg-probe.md §6's full `universe/` listing
    # holds no third DC code, and T1 of the posters phase therefore left this
    # ref OUT rather than guess one. The operator has since RATIFIED the reuse
    # (2026-09-01) -- an explicit call to share DC Universe's art, not an
    # inference -- and §6 of the probe doc records the ratification beside its
    # original finding.
    assert UNIVERSE_CODES["fa11en82/dc-extended-universe"] == "dcu"
    # 'In Association With DC' still has no entry and must not borrow one:
    # §5 names `dca` as DC ANIMATED, a different continuity, and the operator
    # ratified the DCEU alone.
    assert "fa11en82/in-association-with-dc" not in UNIVERSE_CODES
```

And append to `tests/test_collection_poster_wiring.py`:

```python
def test_the_universe_poster_keys_survive_the_category_move():
    """The design note's own watch item. Universe art is keyed by the LIST REF
    a definition carries (`UNIVERSE_CODES`, resolved inside the three generic
    list builders), never by the preset's tab -- so moving both packs into the
    `franchises` category cannot change which poster any of them gets. Pinned
    because 'franchise/' art is keyed by DISPLAY NAME and these collections
    must NOT start resolving through it."""
    from autoposter.collections.catalog import BY_KEY
    from autoposter.collections.default_images import UNIVERSE_CODES

    universes = BY_KEY["content_universes"]
    dc = BY_KEY["content_dc"]
    assert universes.category == "franchises"
    assert dc.category == "franchises"

    refs = [d.params["list"] for d in universes.definitions("Movie")]
    refs += [
        str(d.params["list"]) if "list" in d.params else str(d.params["id"])
        for d in dc.definitions("Movie")
    ]
    assert len(refs) == 11
    unmapped = [ref for ref in refs if ref not in UNIVERSE_CODES]
    assert unmapped == ["fa11en82/in-association-with-dc"]
```

- [ ] **Step 17: Run the four tests to verify they fail**

```bash
docker compose -p pfr22 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_groups.py tests/test_collection_default_images.py tests/test_collection_poster_wiring.py 2>&1 | tee /app/.superpowers/run-fg-t2-red.log'
```

Expected: FAIL. `test_the_universe_and_dc_units_file_under_franchises` fails
on `assert index["Fast & Furious"] == "franchises"` (it is `"content"`);
`test_the_moved_packs_activate_the_franchise_separator_at_050` fails on the
`next(...)` with `StopIteration`; the DCEU test fails with `KeyError:
'fa11en82/dc-extended-universe'`; the poster-survival test fails on
`assert universes.category == "franchises"`.
`test_a_moved_category_changes_the_definition_hash_so_the_prefix_self_heals`
passes already — it pins an existing mechanism this phase depends on, and its
RED is not required.

Record the exact failure text in the report.

- [ ] **Step 18: Move the two categories**

In `src/autoposter/collections/catalog.py`, line 992 (inside
`Preset(key="content_universes", ...)`):

```python
        category="franchises",
```

and line 1046 (inside `Preset(key="content_dc", ...)`):

```python
        category="franchises",
```

- [ ] **Step 19: Fix the two comments that now say the opposite**

Both modules state in prose that universes stay in content. Leaving either
would be a comment that contradicts the code it sits on.

In `src/autoposter/collections/catalog.py`, line 79, change:

```
# ``.superpowers/sdd/p-dividers-facts.md``. Universes stay in content.
```

to:

```
# ``.superpowers/sdd/p-dividers-facts.md``. The Universes and DC packs joined
# it on 2026-09-01 (operator directive): they are franchise blocks by every
# reading an operator does, and keeping them a tab away from the enumerated
# family they overlap was the thing that made the overlap hard to see.
```

In `src/autoposter/collections/groups.py`, lines 69-70, change:

```
# C2, p-dividers-facts.md. Universes STAY in content (upstream's own separate
# franchise/universe art agrees).
```

to:

```
# C2, p-dividers-facts.md. Universes were kept in content on the strength of
# upstream's separate franchise/universe ART; on 2026-09-01 an operator
# directive moved them and the DC pack here anyway -- the art keys are
# unaffected (universe posters resolve from the definition's LIST REF through
# ``default_images.UNIVERSE_CODES``, never from the tab), and the tab is what
# an operator navigates.
```

- [ ] **Step 20: Add the DCEU entry**

In `src/autoposter/collections/default_images.py`, replace lines 224-227 (the
four-line "no upstream code" comment inside `UNIVERSE_CODES`) with:

```python
    # DC Extended Universe reuses DC Universe's `dcu`. §6's full `universe/`
    # listing holds only `dca` (DC Animated) and `dcu`, no third DC entry, and
    # T1 of the posters phase therefore left this ref out under its own
    # "a code the listing does not show is LEFT OUT, not guessed" rule. This
    # entry is not a guess overturning that rule -- it is an operator
    # RATIFICATION (2026-09-01) of the reuse, recorded in p-defimg-probe.md §6
    # beside the original finding. 'In Association With DC' is NOT ratified
    # and stays out.
    "fa11en82/dc-extended-universe": "dcu",
```

- [ ] **Step 21: Update the catalog checksum**

In `tests/test_collection_catalog.py`, change line 271 from
`"content": (3, 1, 0),` to:

```python
    # 3/1/0 until the franchise-grouping phase: `content_universes` and
    # `content_dc` followed `content_franchises` into the `franchises` tab on
    # an operator directive. The rows themselves did not change -- only their
    # tab.
    "content": (1, 1, 0),
```

and line 273 from `"franchises": (1, 0, 0),` to:

```python
    "franchises": (3, 0, 0),
```

Leave the existing `4/1/0 until the divider-polish phase` comment above
`"content"` in place — it is the previous entry in the same history and this
adds to it rather than replacing it.

- [ ] **Step 22: Run the tests to verify they pass**

```bash
docker compose -p pfr22 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'pytest -q tests/test_collection_groups.py tests/test_collection_catalog.py tests/test_collection_default_images.py tests/test_collection_poster_wiring.py 2>&1 | tee /app/.superpowers/run-fg-t2-green1.log'
```

Expected: PASS. `test_the_universes_transcription`,
`test_the_dc_transcription` and both `_have_not_drifted_by_one_entry` digest
tests assert titles, builders, params and ids — none asserts a category, so
none should need an edit. If a digest test goes red, STOP: nothing in this
task edits `_UNIVERSE_LISTS` or `_DC_LISTS`, so a red digest means an
unintended edit.

- [ ] **Step 23: Record the ratification in the probe doc**

In `.superpowers/sdd/p-defimg-probe.md`, in §6's `universe/` reading, the
fourth bullet currently ends:

```
so DC Extended Universe is left OUT of `UNIVERSE_CODES` -- mapping it to `dca`
or `dcu` would be a plausible wrong poster, which the plan calls worse than
none.
```

Append, as a new paragraph immediately after that bullet (do not edit the
bullet itself — the finding was correct when it was made):

```
  **Superseded by operator ratification, 2026-09-01.** The finding above
  stands as evidence: the listing genuinely holds no third DC code, and
  nothing here inferred one. What changed is the decision, not the data --
  the operator explicitly called for DC Extended Universe to wear `dcu`'s
  art, which is a ratified reuse rather than a guess, and
  `UNIVERSE_CODES["fa11en82/dc-extended-universe"] = "dcu"` ships with the
  franchise-grouping phase. `In Association With DC` was NOT ratified and
  remains absent, for this bullet's original reason: `dca` is DC ANIMATED, a
  different continuity.
```

- [ ] **Step 24: Lint**

```bash
docker compose -p pfr22 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'ruff check .'
```

Expected: `All checks passed.`

- [ ] **Step 25: Run the full suite and commit Task 2**

```bash
docker compose -p pfr22 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pfr22-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-fg-t2-full.log'
docker wait pfr22-full
docker cp pfr22-full:/app/.superpowers/run-fg-t2-full.log .superpowers/run-fg-t2-full.log
docker rm pfr22-full
tail -5 .superpowers/run-fg-t2-full.log
docker compose -p pfr22 down
```

Expected: green, `BASELINE + 8` (Task 1's 4, plus 3 group tests and 1 poster
test here — the DCEU change and the checksum change edit existing tests and
add no count). State that number before running; reconcile any gap in the
report.

```bash
git add src/autoposter/collections/catalog.py \
  src/autoposter/collections/groups.py \
  src/autoposter/collections/default_images.py \
  tests/test_collection_groups.py tests/test_collection_catalog.py \
  tests/test_collection_default_images.py tests/test_collection_poster_wiring.py \
  .superpowers/sdd/p-defimg-probe.md
git commit --no-gpg-sign -m "feat(collections): universes and DC join the franchises band, DCEU wears dcu art"
```

---

## Task 3: Wrap — the rows, the ancestry, the measured suites

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (rows 221, 222)
- Modify: `.superpowers/sdd/progress.md` (one appended line)

**Interfaces:**
- Consumes: Task 1's and Task 2's commits and their measured suite numbers.
- Produces: nothing code-facing.

---

- [ ] **Step 26: Verify the ancestry and the diff surface**

```bash
git fetch origin
git merge-base --is-ancestor origin/main HEAD && echo ANCESTRY-OK
git log --oneline origin/main..HEAD
git diff --stat origin/main..HEAD
```

Expected: `ANCESTRY-OK`, three commits (the plan, T1, T2), and a diff
touching exactly the fourteen files the File Structure table names. Anything
else in the diff is out of scope — STOP and report rather than committing it.

Then confirm the collision assessment held, now that the code exists:

```bash
git diff origin/main..HEAD -- src/autoposter/collections/engine.py
```

Expected: two hunks, both inside `run_library`'s setup block (around lines
372-391), nothing near `_sweep` and nothing near line 578. Paste it into the
report so the mass-ops reviewer can see it.

- [ ] **Step 27: File row 221 — the phase's own row, filed and closed**

Append to `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, after
row 220's paragraph and before the `---` that follows it, matching the
neighbouring rows' three-column shape:

```markdown
| 221 | Two active presets can claim one Plex collection title with two different membership rules, and nothing detects it (CLOSED — the franchise-grouping phase) | Filed and closed by the same phase (`fix/franchise-grouping`), out of a live-run symptom an operator reported: franchise collections split across the `!040_` content band and the `!050_` franchise band on a freshly-rebuilt library. **The mechanism, diagnosed at `origin/main` before any fix** (`.superpowers/sdd/p-franchise-band-diagnosis.md`): `groups.preset_groups` (`groups.py:415-435`) builds ONE title-keyed index across every active preset, and `groups.group_for` (`:438-464`) resolves a unit through that index BEFORE its inherited parent — deliberately, pinned by `test_the_parent_never_outranks_the_index_or_the_builder_table` (`tests/test_collection_groups.py:799-813`). `content_franchises` (category `franchises`) enumerates TMDb franchises through `facts_family`; `content_universes` and `content_dc` (both category `content`) name several of the SAME franchises by the SAME titles in static lists (`catalog.py:968-981`, `:983-987`). With both switched on, each colliding member resolved to `content` through the other preset's index entry. `20ac5d9` (franchise `max_collections` 250 → 500) is what surfaced it: the enumeration could suddenly reach franchises it had been excluding. **The band split was the visible half; the invisible half was worse** — two definitions rebuilding one Plex collection from two membership rules on every pass, whichever ran last in preset order winning (`reconcile.resolve_collision`, `reconcile.py:320-349`, guards a title only against an EXTERNALLY-labelled collection; it has no concept of two of our own definitions contesting one). **No validator could catch it in advance:** `CollectionsConfig._titles_must_not_collide` → `engine.definition_titles` sees only `facts_family`'s PLACEHOLDER title (`"Franchises"`), because the builder defines no `TITLE_PATTERN` and its member titles genuinely do not exist until the facts pipeline and TMDb enumeration run. **The fix does not touch `group_for`.** `facts_family` extends its own refuse-and-report idiom by one filter: a unit whose title is already managed elsewhere in this config is SKIPPED and named in the pass report, curated keeping the collection and the enumerated family filling the rest. The contested set has two sources — `engine.definition_titles_for`'s set (every curated definition's own title, threaded in on the new read-only `BuilderContext.managed_titles`) and the pass's own `facts_family:generated:*` run-cache records (one enumerated family's titles, which the static set cannot see). The facts' separate "guard for the contest class" is satisfied by these report lines rather than by new machinery. **The same phase moved `content_universes` and `content_dc` from `content` to `franchises`** on an operator directive — the catalog tab, the `!050` band and the franchise separator — with the members' sort prefixes self-healing through the definition hash (`groups._DerivedSortTitle` → `lists._settings_parts`, pinned by `test_a_moved_category_changes_the_definition_hash_so_the_prefix_self_heals`), so no migration. **The latent location contest, precisely:** `Regions` and `Continents` are both `facts_family` and both title with the bare name, so "Antarctica" is a bucket in each — now guarded by the run-cache half. `Countries` is `builder: dynamic`, a SMART builder with no `titles()` lister, so `definition_titles` files it under its own title alone (`engine.py:1346`) and its member titles come from a live Plex enumeration this guard never sees; Regions-vs-Countries and Continents-vs-Countries remain UNGUARDED and remain disclosed in those three packs' own descriptions (`catalog.py:1647-1654`, `:1696-1703`, `:1748-1753`), which is where they were already honest. Closing that third pairing would mean teaching `dynamic` a `titles()` lister that enumerates Plex at config time, which is a different row nobody has asked for. |
```

- [ ] **Step 28: File row 222 — the `url_poster` parity row, filed and OPEN**

Immediately after row 221, append:

```markdown
| 222 | No per-definition poster URL — an operator cannot point one collection at arbitrary artwork without dropping a file on disk | Filed by the franchise-grouping phase's wrap, out of an operator's own workaround rather than out of a gap in the code: asked for specific ThePosterDB art on `DC Extended Universe` and `In Association With DC`, the answer was the EXISTING local-override path (`<assets_root>/<library>/<title>/poster.webp`), which outranks every hosted default and survives everything. That works and is what shipped. What it costs is a file per collection on the box, which is the only reason this row exists. **The gap, in Kometa's terms:** `url_poster` is Kometa's per-collection artwork override (`library.py:453`, above `tmdb_person` at `:462`), and this service has no equivalent — `collections/posters.py` resolves artwork from three sources in a fixed priority (local override, then the hosted default the builder names through `BuilderResult.poster_kind`/`poster_key`, then nothing), none of them operator-addressable per definition. **The shape:** a `poster_url:` field on `CollectionDefinition`, fetched through the same provider cache and hash-comparison the hosted defaults already use, slotted BETWEEN the local override and the hosted defaults — so a file on disk still wins (an operator who put one there meant it) and a URL still beats a generic default. **Pairs with row 138** (the definitions editor), which is what would make it reachable without hand-editing YAML: `poster_url` on a list-of-objects config section has exactly row 138's problem, the one the franchise `max_collections` incident already demonstrated. Not urgent: the DCEU now has a shipped default (`UNIVERSE_CODES` → `dcu`, operator-ratified) and the operator's own override sits above it either way. |
```

- [ ] **Step 29: Append the wrap line to the ledger**

Append one line to `.superpowers/sdd/progress.md`:

```
- FRANCHISE GROUPING SHIPPED: branch fix/franchise-grouping from origin/main (94ee181 — NOT stacked on mass-ops-1, which is two commits behind main and lacks both the 500-cap and the buckets-leak fix; engine.py hunks are 190 lines apart from mass-ops' nearest, verified in T3). T1 the precedence rule (facts_family skips a unit whose title another definition manages; two sources — BuilderContext.managed_titles from definition_titles_for, plus the pass's own generated records for family-vs-family; group_for untouched, its pinned precedence test green and unedited). T2 the category move (content_universes + content_dc -> franchises; band tests for units AND separator at !050; prefix self-heal pinned by a definition-hash test; DCEU->dcu with the probe doc's §6 ratification note; poster-survival test — universe art keys off the LIST REF, not the tab). CORRECTION TO THE FACTS: the location contest is only two-thirds guarded — Regions<->Continents yes (both facts_family), anything-vs-Countries no (dynamic/smart, no titles() lister, member titles live only in Plex); row 221 says so precisely rather than claiming the class. ROWS: 221 filed-and-closed (the contest class + the band diagnosis + the location note), 222 filed-and-open (per-definition poster_url, Kometa url_poster parity, pairs with row 138) — which means MASS-OPS-1's two carried rows shift to 223 (a refused family's members frozen out of re-sort/prune) and 224 (credits_family top-N before searchability). Suites measured, not assumed.
```

- [ ] **Step 30: Run the full suite one last time and commit Task 3**

```bash
docker compose -p pfr23 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pfr23-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-fg-t3-full.log'
docker wait pfr23-full
docker cp pfr23-full:/app/.superpowers/run-fg-t3-full.log .superpowers/run-fg-t3-full.log
docker rm pfr23-full
tail -5 .superpowers/run-fg-t3-full.log
docker compose -p pfr23 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c 'ruff check .'
docker compose -p pfr23 down
```

Expected: green at `BASELINE + 8` (unchanged from Task 2 — this task is
documentation only), and `All checks passed.` State the number before
running. Report every measured number in one block: `BASELINE`, T1's, T2's,
T3's.

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md \
  .superpowers/sdd/progress.md
git commit --no-gpg-sign -m "docs(roadmap): row 221 filed-and-closed, row 222 filed for per-definition poster URLs"
```

- [ ] **Step 31: Report**

Write `.superpowers/sdd/p-franchise2-task-3-report.md` (or hand the report
back inline if the executor's convention is inline). It must carry:

1. `BASELINE=<n>` and the three later suite numbers, each stated-then-measured.
2. The exact RED text from Step 4 and Step 17.
3. The `git diff origin/main..HEAD -- src/autoposter/collections/engine.py`
   output from Step 26, so the mass-ops reviewer can confirm the two branches
   do not collide.
4. Any adjudication A1–A7 the implementation had to depart from, with why.
5. The PR body note: **this branch is independent of `feat/mass-ops-1` and
   either may merge first**; its base is `origin/main` at `94ee181`.

---

## Self-review

**Spec coverage** — every clause of the facts' C1–C4 maps to a step:

| Facts clause | Where |
|---|---|
| C1.1 the precedence rule, refuse-and-report, INFO not WARNING, engine's definition-title data, `group_for` untouched | T1 Steps 5-9; Global Constraints 2 and 7 |
| C1.1 "also covers the location latent contest" | T1 Step 3's third test + Step 8's run-cache half — **partially**, see CORRECTIONS C-2 and A1 |
| C1.2 the category move (tab, `!050` band, separator) | T2 Steps 15, 18, 21 |
| C1.2 "verify the hash actually covers the category-derived prefix" | T2 Step 15's third test |
| C1.3 DCEU → `dcu`, probe-doc note updated not contradicted | T2 Steps 16, 20, 23 |
| C1.3 "In Association With DC stays unmapped" | T2 Step 16's second assertion, Step 20's comment |
| C1.4 the guard for the contest class | A5 — satisfied by the skip lines, no machinery |
| C1.5 OUT: UI work, manual exclude lists, `group_for` changes | nothing in this plan touches any of the three |
| C2 RED-first with both sides asserted | T1 Steps 3-4, Global Constraint 1, A4 |
| C2 band tests for units AND separator | T2 Step 15's first two tests |
| C2 poster-mapping survival | T2 Step 16's second test |
| C2 no behavior change without the contested presets | T1 Step 11 |
| C2 suites stated-then-measured | Steps 1, 13, 25, 30 |
| C3 a row filed-and-closed, band diagnosis + contest class recorded, location contest noted as guarded | T3 Step 27 (which records it as PARTLY guarded — C-2) |
| C4 three tasks, the branch decision stated, `pfr2*` projects, `p-franchise2-` artifacts | the whole structure; Step 31's report path |
| The ledger's `url_poster` carry | T3 Step 28 |

**Placeholder scan** — no `TBD`, no "add error handling", no "similar to Task
N", no test described without its code, no reference to a function this plan
does not define or cite by file and line.

**Type consistency** — `managed_titles` is `frozenset[str]` at its
declaration (Step 5), its construction site (Step 6, `frozenset(...)`), its
read (Step 8, `set(ctx.managed_titles)`) and its test (Step 3,
`frozenset({...})`). `_GENERATED_PREFIX` is defined once (Step 7) and read
once (Step 8). `definition_titles_for`'s five positional arguments match
`engine.py:1290-1296` exactly at both the call site (Step 6) and the wiring
test (Step 3). `catalog_definitions` is defined in Step 15 before its two
uses in the same step.
