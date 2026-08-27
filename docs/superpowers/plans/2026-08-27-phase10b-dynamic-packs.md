# Phase 10b: Dynamic Packs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the seven Kometa dynamic packs the engine can actually express —
`content_genres`, `time_decade`, `media_audio_language`, `location_country`,
`media_subtitle_language`, `production_studio`, `production_network` — as
catalog rows an operator switches on by key, and re-point the four packs that
stay gated at the roadmap row that actually blocks each.

**Architecture:** Nothing new is built. Phase 10a shipped the engine
(`builder: dynamic`, ten types, include/exclude/addons, title formats, the
fan-out cap, the family sweep); this phase adds **data**: one transcribed pack
table per family in a new pure module `collections/packs.py`, one
`PresetCollection` row per pack pointing at it, and seven `readiness` flips.
Three small seams move with them: the test helper that calls
`builder.titles()` unconditionally gains the fall-through the engine already
has (it is what a dynamic preset breaks); `Preset.years_title()` gains a
dynamic branch so the picker reports the family's SHAPE instead of a lie; and a
whole-table test proves no two co-enablable families share a title format.

**Tech Stack:** Python 3.14, pydantic v2, pytest, ruff, React 19 + vitest,
Docker Compose. **No new runtime dependency, no new engine code, no new config
field, no new endpoint.**

---

## Read this first — what governs this plan

1. `.superpowers/sdd/p10b-facts.md` **C1–C12** — the controller adjudications.
   They are SETTLED. This plan implements them; where the evidence disagrees
   with itself, the CONTRADICTION FLAGS section below says so and the
   adjudicated version is what gets built anyway.
2. `.superpowers/sdd/p10b-recon-pointer.md` — the recon's key citations, every
   one of them re-verifiable from the tree at `8786393` (`== origin/main`).
3. The tree itself. Every file:line in this plan was read at `8786393`.

**How the data-carrying steps work.** Four tasks write TRANSCRIBED TABLES —
Kometa's own include lists, addon merges and name overrides. Those values are
not in this plan and must not be invented: Task 1 fetches the seven upstream
files and records every table in
`.superpowers/sdd/p10b-upstream-packs.md`, and Tasks 3 and 4 copy from that
record into `src/autoposter/collections/packs.py`. This plan specifies the
exact SHAPE of every table, the exact module and row it goes in, the naming,
the provenance comment and the tests — the only thing it does not carry is
somebody else's data, because a recalled include list is precisely what
`catalog.py:522-533` refuses. A step that says "from §3 of the record" is not a
placeholder; it names its source and its verification.

---

## What this phase is NOT

- **No new dynamic type, no new enumeration seam** (C1). `tmdb_collection`,
  `origin_country`, `original_language` and the people types stay out; each is
  the metadata-prefetch-budget family, not an enumeration. Adding one is
  rows 189/192/83 work, not this phase's.
- **No new config shape** (C9). `collections.presets` stays `list[str]`. There
  is no per-preset override key, no `presets:` mapping form, no new endpoint
  field. An operator who wants a pack changed copies it into their own
  `definitions:` entry — which every pack description says in its own words.
- **No engine `src/` changes** (Global Constraint 2). `builders/dynamic.py`,
  `dynamic_types.py`, `dynamic_keys.py`, `dynamic_titles.py`, `engine.py`,
  `smart.py`, `search_url.py`, `filters.py` are READ-ONLY for the whole phase.
  If a pack cannot be expressed without touching one of them, the pack does not
  ship — say so in the task report and stop.
- **No preset flip for the four blocked packs.** `content_franchises`,
  `location_region`, `location_continent` and `time_year` stay `GATED`. They
  get a truthful blocker, not a builder.
- **No "one per year the library holds" year pack** (C1). It is not the pack
  Kometa ships (`defaults/both/year.yml` is "Best of the last ten years") and
  87 unbounded years is not that pack wearing its name. REFUSED, and the
  refusal is recorded in the row.
- **No `catalog.py` split** (C11). Third triage, deferred again, reasoning
  recorded in the wrap.
- **No picker redesign.** One line of `CatalogPanel.tsx` changes. Nothing else
  in `frontend/` does.

---

## CONTRADICTION FLAGS

Three places where the evidence disagrees with itself or with the tree. Each is
flagged here and **planned as the controller adjudicated it**.

**FLAG 1 — C3 says both that the placeholder stays out of `titles()` and that
`titles()` returns it.** C3's sentence reads "The placeholder title itself
stays out of `titles()` for dynamic rows … instead `titles()` returns the
placeholder AND the shape line explains it". The operative clause is the
second, and it is also what the tree does for free: a `PresetCollection` with a
title is listed by `Preset.titles()` (`catalog.py:294-302`) with no code
change, and that title is genuinely reserved — `engine.definition_titles`
falls through to `{definition.title}` for a smart builder that lists none
(`engine.py:976-977`), so the placeholder IS a title this service manages.
**Planned:** `titles()` is not touched; each pack's placeholder title appears
there, and `years_title()`'s shape line explains what the family really builds.
An empty `titles()` was also checked against the READY contract and would not
have failed it — `test_every_ready_preset_actually_builds_something`
(`tests/test_collection_catalog.py:347-365`) asserts DEFINITIONS, not titles —
so the parenthetical's stated reason is wrong even though its conclusion is
right. Recorded, not acted on.

**FLAG 2 — C4's format-collision test can fire on packs that cannot actually
collide.** The test compares rendered title FORMATS with the key token
replaced by a sentinel. Kometa's own packs reuse one format
(`<<key_name>> <<library_typeU>>s`) across several families, and two of those
families can be co-enabled while being incapable of producing the same string
— `location_country`'s key names carry flag emoji, so "🇫🇷 France Movies" can
never equal a genre's title. So a literal transcription of upstream's formats
makes this test RED for a pair that is safe, while catching the pair that is
genuinely unsafe (audio and subtitle languages share a value vocabulary almost
exactly, so identical formats there are a guaranteed mass collision).
**Planned:** implement C4 exactly as adjudicated and resolve every collision by
pinning a distinguishing `title_format` on the pack that has to move, with the
divergence stated in the row (Task 4, Step 5 carries the decision table and the
rule). The rendered-format test is a coarse instrument on purpose: it refuses
the class an operator can create, not only the instances that bite today.

**FLAG 3 — C8 assumes Kometa's language NAMES are transcribable from a defaults
file.** The tree says otherwise: upstream titles a language bucket from TMDb's
`_iso_639_1` table in `meta.py:928-929`, not from
`defaults/both/audio_language.yml`, and roadmap row 190 already files
"vendor the ISO table" as unbuilt work with a cost. If Task 1's fetch finds no
name table in the two pack files, there is nothing to transcribe and inventing
one is exactly the fabrication `NOT_KOMETA` exists to prevent.
**Planned:** Task 1 records which is true. Task 3/4 transcribe a name table if
and only if the pack file carries one; otherwise the packs ship Plex's own
`choice.title` names — the branch upstream itself falls back to — with the
mixed provenance stated in the row. Row 182 closes either way (C8's
"documented remainder" clause); row 190 stays open and the wrap says which
branch was taken.

---

## Branch and cut point

- Branch name: **`feat/dynamic-packs`**.
- **Cut from `origin/main` after a fetch, verified with a CONTENT probe, never
  a sha probe:**

  ```bash
  git fetch origin
  git cat-file -e origin/main:src/autoposter/collections/builders/dynamic.py
  git cat-file -e origin/main:src/autoposter/collections/dynamic_types.py
  git cat-file -e origin/main:tests/test_collection_cs_equivalence.py
  git show origin/main:src/autoposter/collections/engine.py | grep -q generated_titles && echo SWEEP-PRESENT
  git checkout -b feat/dynamic-packs origin/main
  git rev-parse HEAD
  ```

  All four probes must succeed and `SWEEP-PRESENT` must print. The third and
  fourth are the **10a-2 artifacts** specifically: `test_collection_cs_equivalence.py`
  is 10a-2's mandatory equivalence proof and `generated_titles` in `engine.py`
  is its family sweep. If either is missing, `origin/main` predates 10a-2 —
  **STOP and report**; do not cut from a phase branch and do not stack.
  At the time this plan was written `origin/main` is `8786393` and all four
  pass. Record the resolved `git rev-parse HEAD` in the Task 1 report.

## Execution

Executed via **superpowers:subagent-driven-development**: one fresh subagent per
task, two-stage review between tasks, this plan **live-synced** — a fix that
changes a table, a message or a signature edits the corresponding step here in
the same commit, so a later task's implementer reads the truth and not the
original guess.

---

## Global Constraints

Every task's requirements implicitly include this section. Carried verbatim
from the phase brief and the 9b/9c/10a plans.

1. **Presets are keys, not definitions.** Zero new config, picker or endpoint
   shapes. `collections.presets` stays `list[str]`; `catalog_listing`'s payload
   keys stay exactly the ten it serves today. C3's reuse of the existing
   `years_title` payload key is the **one** allowed picker-adjacent move, and
   renaming that key is allowed **only** if the picker change is a one-line
   rename — otherwise live with the name. (This plan lives with it.)
2. **Zero changes to the dynamic engine's `src/`.** The packs are DATA
   (`collections/packs.py`) plus catalog rows plus the one test-helper fix.
   `builders/dynamic.py`, `dynamic_types.py`, `dynamic_keys.py`,
   `dynamic_titles.py`, `engine.py`, `smart.py`, `search_url.py`,
   `search_sorts.py` and `filters.py` are read-only. `git diff --stat
   origin/main -- src/autoposter/collections/builders/ src/autoposter/collections/engine.py
   src/autoposter/collections/dynamic_*.py` must be EMPTY at every task's end.
3. **Transcriptions are fetched, not recalled.** Every table in `packs.py`
   comes from a file fetched at Kometa `v2.4.8` and recorded in
   `.superpowers/sdd/p10b-upstream-packs.md` with its path. Anything this
   service invents carries the `NOT_KOMETA` prefix
   (`catalog.py:534`) or, inside a pack table, an explicit
   `# NOT KOMETA:` comment naming who decided it and why. A row's
   `kometa_source` must stay a path `_KOMETA_DEFAULTS` pins
   (`catalog.py:536-572`) — all seven already are.
4. **Refusals RETURN.** Nothing in this phase adds a refusal, and nothing may
   convert one into a raise. The engine's existing refusals (empty
   enumeration, over-cap fan-out, duplicate family title, dead lookup) are what
   a pack meets on a real library, and they are already action strings.
5. **No operator URLs or tokens anywhere** — not in code, comments, tables,
   logs, reports or the PR body. Upstream GitHub URLs at a pinned tag are fine
   and are the point.
6. **The golden gate is byte-identical.**
   `tests/fixtures/collections/golden_port.json` must not change in any task.
   `presets:` defaults to empty, so every pack this phase ships is invisible to
   it — assert that with a real run rather than assuming it.
7. **Container discipline.** Unique compose project per task (`p10bt1` …
   `p10bt6`), always with the `.superpowers/isolated-db.yml` overlay. Tee
   output to a path under `/app/.superpowers/` inside the container — never
   rely on streamed stdout. A long run uses **no `--rm`**, is started detached
   and waited on with a foreground `docker wait`, and the log is read back from
   the host. Teardown is `docker compose -p <project> down` — **never**
   `down -v`.
8. **Mutation proofs are backup + cmp.** Copy the file, edit the copy's source
   in place, run the test to see it RED, restore from the backup, `cmp` the
   restored file against the backup to prove the restore was exact, re-run to
   see GREEN. Paste real output, both halves.
9. **Commits** are conventional, `--no-gpg-sign`, staged **by name** (never
   `git add -A`), and carry **no AI attribution** of any kind.
10. **Every opinion states its number and its reasoning in the row.** A pinned
    `max_collections`, an `include:` list, a divergent `title_format` and a
    divergent value set are opinions (`catalog.py:34-37`); each one is written
    into the preset's own `description`, in the words an operator reads in the
    picker. A test enforces this rather than trusting it.
11. **Every refusal names the way out.** A message that says only what is wrong
    is half a message.

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `.superpowers/sdd/p10b-upstream-packs.md` | the fetch record: seven files at v2.4.8, every table, the cap arithmetic, the language-naming finding, the format-collision table | T1 |
| `tests/test_collection_catalog.py` | `_managed_titles`' fall-through fix; the pack contract tests; the format-collision test; the checksum | T2, T3, T4 |
| `src/autoposter/collections/packs.py` | **new.** The transcribed pack tables and the `params` tuple each preset row points at. Pure data: no imports beyond typing, no I/O, no Plex | T3, T4 |
| `src/autoposter/collections/catalog.py` | `dynamic_shape()` + `years_title()`'s dynamic branch; seven rows gain a `collections` table and lose `GATED`; four rows re-point; the count checksum | T3, T4, T5 |
| `frontend/src/pages/CatalogPanel.tsx:136-141` | one line: the shape clause is served whole instead of being assembled in the browser | T3 |
| `config/autoposter.example.yaml:69-77` | the operator note on what un-ticking a pack means | T4 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | new row 192; rows 93, 182, 190, 49; the 10b section re-scoped | T5, T6 |
| `.superpowers/sdd/p10b-pr-body.md` | the PR body and the C12 acceptance procedure | T6 |

---

## Task 1: The upstream transcription

**Files:**
- Create: `.superpowers/sdd/p10b-upstream-packs.md`
- Read only: `docs/research/plex-dynamic-probe/README.md`,
  `src/autoposter/collections/dynamic_types.py`,
  `src/autoposter/collections/dynamic_keys.py`

**Interfaces:**
- Produces: the record every later task transcribes FROM. Its section numbers
  are fixed and later tasks cite them: **§1** per-pack tables, **§2** the cap
  table, **§3** the language-naming finding, **§4** the rendered-format
  collision table, **§5** the NOT-KOMETA list.
- Consumes: nothing. No code changes in this task.

**Background.** `_KOMETA_DEFAULTS` (`catalog.py:536-572`) already pins all
eleven pack paths, and `p10a-upstream-dynamic.md` transcribed the ENGINE from
`modules/meta.py` — but no pack CONTENT has ever been transcribed. That is this
task. The seven files, all at tag `v2.4.8`:

| pack | preset key | file |
| --- | --- | --- |
| genre | `content_genres` | `defaults/both/genre.yml` |
| decade | `time_decade` | `defaults/movie/decade.yml` |
| audio language | `media_audio_language` | `defaults/both/audio_language.yml` |
| subtitle language | `media_subtitle_language` | `defaults/both/subtitle_language.yml` |
| studio | `production_studio` | `defaults/both/studio.yml` |
| country | `location_country` | `defaults/movie/country.yml` |
| network | `production_network` | `defaults/show/network.yml` |

The four blocked packs' files (`franchise.yml`, `region.yml`,
`continent.yml`, `year.yml`) are **not** fetched: nothing ships from them and
their rows already characterise them (C7).

- [ ] **Step 1: Cut the branch**

Run the whole "Branch and cut point" block above. Paste the four probe results
and `git rev-parse HEAD` into the task report. If any probe fails, STOP.

- [ ] **Step 2: Fetch the seven files**

`WebFetch` is a deferred tool — load it first:

```
ToolSearch with query "select:WebFetch"
```

Then fetch each of the seven, at the pinned tag, raw:

```
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/both/genre.yml
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/movie/decade.yml
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/both/audio_language.yml
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/both/subtitle_language.yml
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/both/studio.yml
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/movie/country.yml
https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/defaults/show/network.yml
```

If a path 404s, the file has moved between versions: report the 404, try the
same path at the repository's `defaults/` listing for `v2.4.8`, and if it is
genuinely absent say so in §5 and mark that pack's tables NOT KOMETA rather
than substituting a neighbouring file's content.

- [ ] **Step 3: Write §0 (the fetch record) and §1 (the per-pack tables)**

`§0` is one row per file: the URL fetched, the date, the byte count and the
first non-comment line, so a later reader can tell whether they are looking at
the same file.

`§1` is one subsection per pack, and each carries **every** key the engine can
consume, with "(absent)" written out where the file has none — an absent key is
a finding, not a gap:

- `type` (upstream's `dynamic_collections` key)
- `title_format`
- `include` (the whole list, in file order)
- `exclude`
- `addons` (the whole merge table)
- `key_name_override`
- `title_override`
- `remove_prefix` / `remove_suffix`
- `other_name`
- `template_variables` — specifically any per-key `sort_by` and `limit`
  upstream hides there (this is where "the ten highest-rated" lives)
- `allowed_libraries` / the pack's own library scope

Nothing is paraphrased. A list of 300 studios is written out as 300 entries.

- [ ] **Step 4: Write §2 — the cap table, with the arithmetic**

C5 pins a per-pack `max_collections` opinion and licenses this task to revise
any of its numbers **with the reasoning recorded**. The rule to apply, and the
reason it is a better rule than a guessed number:

> `include` is a whitelist applied LAST (`dynamic_keys.py`'s module docstring,
> meta.py:1348-1351), and the cap is checked against the DERIVED titles, not
> the raw enumeration (`builders/dynamic.py`, the `len(titled) >
> params.max_collections` branch). So a pack with an include list can never
> build more collections than that list is long, whatever the library holds —
> and a pack without one is bounded only by the measured enumeration.

Fill this table, one row per pack, and mark each pin REVISED or CONFIRMED
against C5's defaults-of-record:

| pack | measured enumeration (probe README) | `include` length | pin | reasoning |
| --- | --- | --- | --- | --- |
| genre | ~25 movie | — | none (default 50 stands) | C5 |
| decade | ~12 movie | — | none | C5 |
| audio_language | 46 | — | 80 | measured + headroom so one new variant cannot refuse the family |
| country | 63 | — | 100 | measured + headroom |
| subtitle_language | 115 | — | 150 | the pack IS one-per-language; narrowing it is not what Kometa ships |
| network | 91 show | ? | 120, or `len(include)` if the file ships one | C5 |
| studio | 824 movie / 74 show | ? | `len(include)` | the whitelist bounds the family; a cap below it would refuse on a library that simply holds a lot of Kometa's studios |

Where the file settles a `?`, the pin is written as `len(<TABLE>)` in code
rather than as a literal — Task 4 does that — so the number cannot drift from
the list. Record here what it evaluates to today.

- [ ] **Step 5: Write §3 — the language-naming finding (C8, FLAG 3)**

Answer exactly one question, with a citation: **do
`audio_language.yml`/`subtitle_language.yml` carry a code→name table
(`title_override`, `key_name_override` or a `template_variables` mapping), or
do upstream's language names come from `meta.py:928-929`'s TMDb `_iso_639_1`
lookup instead?**

- If the files carry a table: transcribe it whole. It becomes
  `title_override` in the two packs, and the remainder (Plex `choice.title`s
  with no upstream entry) is listed here by count with three examples.
- If they do not: say so in one sentence with the citation, list nothing, and
  record that the packs ship Plex's own names — the fallback branch upstream
  itself has — and that vendoring the ISO table is roadmap row 190's filed
  work, deliberately not done here.

Either way, close with the vocabulary note row 182 needs: the production
libraries answer 46 `audioLanguage` and 115 `subtitleLanguage` values spanning
2-letter, locale, 3-letter, script-qualified and one literal `english`, so no
single normalisation target is correct.

- [ ] **Step 6: Write §4 — the rendered-format collision table**

For each of the seven packs, for each library type it serves, write the
`title_format` from §1 (or the type's default from `dynamic_types.py` where the
file pins none) with `<<key_name>>` replaced by `KEY` and `<<library_type>>` /
`<<library_typeU>>` substituted for that library type. Then mark every pair
that renders IDENTICALLY on the same library type. That set is what Task 4's
collision test will refuse and what its decision table has to break.

- [ ] **Step 7: Write §5 — the NOT-KOMETA list**

Everything this phase will ship that no fetched file supplies: each divergent
`title_format`, each cap pin, `location_country`'s value-set divergence (Plex
`country` tag vs TMDb origin-country, C6), and the language names if §3 took
the fallback branch. One line each, naming who decided and why. This is the
list Tasks 3 and 4 must reproduce as `# NOT KOMETA:` comments in `packs.py`.

- [ ] **Step 8: Commit**

The record lives under `.superpowers/`, which is gitignored — so there is
nothing to stage and **no commit for this task**. Instead, paste §2, §3 and §4
verbatim into the task report: they are the reviewable deliverable, and Tasks
3–5 read them from there.

---

## Task 2: The break site

**Files:**
- Modify: `tests/test_collection_catalog.py:94-125` (`_managed_titles`)
- Test: `tests/test_collection_catalog.py` (same file, a new test above it)

**Interfaces:**
- Produces: `_managed_titles(config, library_type) -> list[str]` — unchanged
  signature, one branch changed: a smart builder with no `titles` attribute
  now contributes `definition.title` instead of raising.
- Consumes: nothing from Task 1.

**Background — why this is first.** `_managed_titles` is the helper behind
`test_every_ready_preset_at_once_never_builds_one_title_twice`
(`:443-461`), the test that proves no two built-in definitions claim one title.
It calls `builder.titles(library_type, config)` unconditionally for any smart
builder (`:119-120`). `DynamicBuilder` declares **no** `titles` — deliberately,
and the module docstring's "No `titles()`, deliberately" section says why: a
dynamic family's titles are the library's, and enumerating them offline is
impossible while enumerating them online would put a Plex call inside
`_titles_must_not_collide`, which runs on every config write. The engine
already answers correctly for that shape (`engine.py:976-977`); this helper
does not. The first dynamic preset to reach it raises `AttributeError`. Row 93
noted this break site; 10a-1 and 10a-2 both left it standing because no preset
reached it. Task 3 makes one reach it, so it is fixed here, red first, in its
own commit.

The lost-flag guard at `:112-118` **stays exactly as it is**. It asserts that a
builder which is neither smart nor pattern-owning has no `titles` — that is
about a builder that LOST its `smart` flag, a different failure, and the fix
touches only the `if smart:` branch.

- [ ] **Step 1: Write the failing test**

Insert immediately above `_config` in `tests/test_collection_catalog.py`
(after `_managed_titles`):

```python
def test_the_titles_helper_answers_for_a_smart_builder_that_lists_nothing(
    monkeypatch,
):
    """THE break site row 93 noted, proven before it is fixed.

    ``engine.definition_titles`` falls through to the definition's own title
    for a smart builder that declares no ``titles`` (``engine.py:976-977``) --
    ``smart_filter`` manages the one collection its definition names, and
    ``DynamicBuilder`` declares none at all because a dynamic family's titles
    are the LIBRARY's (``builders/dynamic.py``, "No ``titles()``,
    deliberately"). This helper called ``titles`` unconditionally, so the first
    dynamic preset to reach it raised ``AttributeError`` instead of reporting
    the placeholder title the engine reserves -- and the whole-table collision
    test below is the one that would have raised.

    A double rather than a shipped row, because at this commit no shipped
    preset builds with ``dynamic`` yet; Task 3's packs are what make this
    branch load-bearing.
    """
    double = Preset(
        key="content_dynamic_double",
        category="content",
        name="Dynamic double",
        description="test double",
        kometa_source=catalog.NOT_KOMETA + "written for this test",
        library_types=("Movie",),
        collections=(
            catalog.PresetCollection(
                title="Genre double",
                builder="dynamic",
                params=(("type", "genre"),),
            ),
        ),
    )
    monkeypatch.setattr(catalog, "CATALOG", catalog.CATALOG + (double,))

    titles = _managed_titles(_config(["content_dynamic_double"]), "Movie")

    assert "Genre double" in titles
    assert titles.count("Genre double") == 1
```

- [ ] **Step 2: Run it to see it fail**

```bash
docker compose -p p10bt2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q "tests/test_collection_catalog.py::test_the_titles_helper_answers_for_a_smart_builder_that_lists_nothing" 2>&1 | tee /app/.superpowers/run-t2-red.log; echo EXIT=$?'
```

Expected: FAIL with `AttributeError: 'DynamicBuilder' object has no attribute
'titles'`. Paste the traceback into the report — that traceback IS the break
site, and it is the evidence row 93 asked for.

- [ ] **Step 3: Fix the helper**

In `tests/test_collection_catalog.py`, replace lines 119-120:

```python
        if smart:
            titles += sorted(builder.titles(library_type, config))
```

with:

```python
        if smart:
            # ``engine.definition_titles`` (engine.py:968-978) is the shape
            # this mirrors, and it has TWO branches for a smart builder: one
            # that lists a family it derives itself (``cs_bucket``), and a
            # fall-through to the definition's own title for one that lists
            # nothing. ``smart_filter`` manages exactly the collection its
            # definition names; ``DynamicBuilder`` declares no ``titles`` at
            # all because a dynamic family's titles are the library's and
            # offline enumeration is impossible (9c decision C6). Both reserve
            # the definition's title and nothing else, so that is what this
            # helper counts for them.
            lister = getattr(builder, "titles", None)
            titles += (
                sorted(lister(library_type, config))
                if lister
                else [definition.title]
            )
```

- [ ] **Step 4: Run it to see it pass, then the whole module**

```bash
docker compose -p p10bt2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py 2>&1 | tee /app/.superpowers/run-t2-green.log; echo EXIT=$?'
```

Expected: every test passes, `EXIT=0`. The pre-existing count is the baseline
for later tasks — record it.

- [ ] **Step 5: Pin the lost-flag guard, so the fix cannot have weakened it**

The guard at `:112-118` is the one thing this change could silently weaken: it
catches a builder that owns a FAMILY of titles but has lost its `smart` flag,
which would otherwise be probed as a single title and could hide a collision.
The new fall-through touches only the `if smart:` branch, and this test says so
permanently rather than a mutation saying it once. Append it directly under the
test from Step 1:

```python
def test_the_titles_helper_still_refuses_a_family_builder_that_lost_its_flag(
    monkeypatch,
):
    """The guard beside the branch Task 2 changed, pinned.

    Both reads in the helper have a default, so a builder that owns a family of
    titles and has lost its ``smart`` flag would be probed as a single title
    and could hide a collision. A family builder is recognisable without the
    flag -- it carries ``titles`` -- and the guard is what says so. ``imdb_award``
    is an ordinary non-smart builder the example config already builds with, so
    giving it a ``titles`` attribute is exactly the shape the guard describes.
    """
    monkeypatch.setattr(
        REGISTRY["imdb_award"], "titles", lambda *a, **k: set(), raising=False
    )

    with pytest.raises(AssertionError, match="imdb_award"):
        _managed_titles(_config([]), "Movie")
```

Run it, and run it once with the guard's `assert` commented out to see it go
red (backup + `cmp` restore, global constraint 8):

```bash
cp tests/test_collection_catalog.py /tmp/tcc.bak
# comment out ONLY the `assert not hasattr(builder, "titles")` line
docker compose -p p10bt2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py -k lost_its_flag 2>&1 | tail -20'
cp /tmp/tcc.bak tests/test_collection_catalog.py
cmp /tmp/tcc.bak tests/test_collection_catalog.py && echo RESTORED-EXACT
docker compose -p p10bt2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py -k lost_its_flag 2>&1 | tail -5'
```

Paste both halves. If the example config's `awards:` default ever makes
`imdb_award` absent from `default_definitions`, use whichever non-smart builder
IS present — the test asserts on the builder name it monkeypatches, so switching
it is a two-line change, not a redesign.

- [ ] **Step 6: Commit**

```bash
git add tests/test_collection_catalog.py
git commit --no-gpg-sign -m "fix(tests): the titles helper answers for a smart builder that lists none

The helper behind the whole-table collision test called builder.titles()
for every smart builder. DynamicBuilder declares none, deliberately -- a
dynamic family's titles are the library's -- so the first dynamic preset to
reach it raised instead of reporting the placeholder title the engine
reserves. Mirrors engine.definition_titles' own fall-through; the lost-flag
guard beside it is untouched."
```

```bash
docker compose -p p10bt2 down
```

---

## Task 3: The four straightforward packs

**Files:**
- Create: `src/autoposter/collections/packs.py`
- Modify: `src/autoposter/collections/catalog.py` — the `dynamic_titles` /
  `dynamic_types` imports, `dynamic_shape()`, `years_title()`, the
  `content_genres` row (`:787-807`), the `time_decade` row (`:1692-1713`),
  the `media_audio_language` row (`:1383-1417`), the `LOCATION_PRESETS` block
  (`:1276-1313`), the count checksum comment (`:1738-1753`)
- Modify: `frontend/src/pages/CatalogPanel.tsx:136-141`
- Modify: `tests/test_collection_catalog.py` — `CATALOG_CHECKSUM` (`:188-198`),
  the rewrite of `test_the_three_presets_9b_readjudicated_stay_gated_on_the_engine_row`
  (`:1389-1416`), the new pack contract tests
- Test: `tests/test_collection_catalog.py`

**Interfaces:**
- Produces, in `packs.py`:
  - `GENRE_PARAMS`, `DECADE_PARAMS`, `AUDIO_LANGUAGE_PARAMS`,
    `COUNTRY_PARAMS` — each `tuple[tuple[str, object], ...]`, the exact
    `params` pairs a `PresetCollection` carries. Task 4 adds
    `SUBTITLE_LANGUAGE_PARAMS`, `STUDIO_PARAMS`, `NETWORK_PARAMS` in the same
    shape.
- Produces, in `catalog.py`:
  - `DYNAMIC_KEY_PLACEHOLDER = "<%s>"`, `DYNAMIC_LIBRARY_TYPE = "<Library type>"`
  - `def dynamic_shape(params: dict) -> str` — the family-shape clause for one
    pack, derived from the pinned `title_format` (or the type's default)
    through the engine's own `render_title`.
  - `def _shape_line(what: str, shape: str) -> str` — `'one per %s, named
    "%s"'`, used by BOTH families.
  - `Preset.years_title()` — same signature, now returns the whole clause.
- Produces, in `tests/test_collection_catalog.py`:
  - `def _dynamic_rows() -> list[tuple[Preset, PresetCollection, dict]]` —
    every READY row that ships a dynamic pack. Task 4 reuses it.
- Consumes: Task 1 §1 (the four packs' tables), §2 (their pins), §3 (the
  language names), §5 (the NOT-KOMETA list); Task 2's helper fix.

**Background.** Each pack is ONE definition, not many: `builder: dynamic` with
a `type:` expands into a family at run time, against the library. So a preset
row's `collections` table holds exactly one `PresetCollection`, whose `title`
is the family's placeholder — reserved by the engine, never created as a
collection (`builders/dynamic.py`'s "No `titles()`, deliberately"), and used as
the family LABEL (`FAMILY_LABEL_PREFIX + definition.title`) the sweep
enumerates by.

**The one shape decision, and its measured reason.** `PresetCollection.params`
is a tuple of pairs "so a row stays hashable" (`catalog.py:136-138`), and its
values have so far been scalars and tuples. Three dynamic params
(`addons`, `key_name_override`, `title_override`) are MAPPINGS, and pydantic v2
will not build a `dict[str, list[str]]` from a tuple of pairs — measured:
`Input should be a valid dictionary [type=dict_type]`. So those values are real
dicts, defined in `packs.py`, and a row that carries one is not hashable.
Nothing hashes a `PresetCollection` today (the whole suite and module were
grepped: `set()`/`frozenset()` are only ever built over keys, titles and
library types), and the docstring gains the exception rather than the claim
being left false.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_collection_catalog.py`, in the "the table" section after
`test_every_ready_preset_at_once_never_builds_one_title_twice`:

```python
# --- the dynamic packs -------------------------------------------------------


def _dynamic_rows() -> list[tuple[Preset, catalog.PresetCollection, dict]]:
    """Every READY row that ships a Kometa dynamic pack, with its params.

    A list built from the table rather than a hand-kept key list: a pack added
    to the catalog is held to the contract below without anybody remembering to
    add it here, which is the failure mode a literal tuple of seven keys has.
    """
    return [
        (preset, collection, dict(collection.params))
        for preset in READY_PRESETS
        for collection in preset.collections
        if collection.builder == "dynamic"
    ]


def test_there_are_dynamic_packs_to_hold_to_the_contract():
    """The guard the tests below need: an empty list is a pass that proves
    nothing, and pytest reports it as a pass."""
    assert {preset.key for preset, _, _ in _dynamic_rows()} == {
        "content_genres",
        "time_decade",
        "media_audio_language",
        "location_country",
    }


def test_a_pack_is_exactly_one_definition_whose_type_the_engine_enumerates():
    """A pack is ONE definition that expands at run time, not a table of many.

    The definition validates against ``DynamicParams`` as it is constructed
    (``CollectionDefinition._params_must_satisfy_the_builders_own_model``), so
    a pack naming a type this service does not enumerate, or a param the
    builder does not take, cannot even be expanded -- this asserts the shape
    around that, which construction alone does not say.
    """
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    for preset, collection, params in _dynamic_rows():
        assert params["type"] in DYNAMIC_TYPES, preset.key
        row = DYNAMIC_TYPES[params["type"]]
        # The pack's library types are the TYPE's -- a movie-only type under a
        # both-libraries preset would build nothing on half its libraries and
        # say nothing about it.
        assert set(preset.library_types) <= set(row.kinds), preset.key
        for library_type in preset.library_types:
            definitions = preset.definitions(library_type)
            assert len(definitions) == 1, (preset.key, library_type)
            assert definitions[0].builder == "dynamic"
            assert definitions[0].title == collection.title


def test_every_pack_says_which_dynamic_type_it_is_in_words():
    """The row an operator READS names the type they would write themselves.

    Every pack description already tells them the customisation path is a
    ``definitions:`` entry of their own; that path is worthless without the
    ``type:`` to write in it.
    """
    for preset, _collection, params in _dynamic_rows():
        assert "`type: %s`" % params["type"] in preset.description, preset.key


def test_every_opinion_a_pack_pins_is_stated_in_the_row():
    """Global constraint 10, enforced rather than trusted.

    A cap, an include list or a divergent title format is an OPINION
    (``catalog.py``'s "opinion-free until asked"), and an opinion that ships
    without appearing in the description is one the operator cannot find. The
    numbers are asserted as strings because that is how they appear in the
    sentence an operator reads.
    """
    for preset, _collection, params in _dynamic_rows():
        cap = params.get("max_collections")
        if cap is not None:
            assert str(cap) in preset.description, (preset.key, cap)
        if params.get("include"):
            assert "include" in preset.description, preset.key
        if params.get("title_format"):
            assert "title" in preset.description.lower(), preset.key


def test_a_packs_placeholder_title_is_listed_and_reserved():
    """The pair C3 asks to read honestly.

    ``titles()`` reports the placeholder -- which IS a title this service
    manages, because the engine reserves ``{definition.title}`` for a smart
    builder that lists none (``engine.py:976-977``) -- and ``years_title()``
    reports the SHAPE of what the family really builds. Neither claims a
    collection title the library has not been asked about.
    """
    for preset, collection, _params in _dynamic_rows():
        assert collection.title in preset.titles(), preset.key
        shape = preset.years_title()
        assert shape is not None, preset.key
        assert shape.startswith("one per "), (preset.key, shape)
        assert "named " in shape, (preset.key, shape)


def test_a_packs_shape_line_comes_from_the_renderer_the_builder_uses():
    """Derived, not restated -- the module's second property, applied to the
    picker's copy. If the shape line were written by hand it could name a title
    format the engine does not use; here it comes out of the same
    ``render_title`` the builder titles collections with, so it cannot.
    """
    from autoposter.collections.dynamic_titles import render_title
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    for preset, _collection, params in _dynamic_rows():
        row = DYNAMIC_TYPES[params["type"]]
        noun = row.name.replace("_", " ")
        key_name = catalog.DYNAMIC_KEY_PLACEHOLDER % noun
        expected = render_title(
            params.get("title_format") or row.title_format,
            key_name,
            catalog.DYNAMIC_LIBRARY_TYPE,
            key=key_name,
            values=(),
            auto_type=row.name,
        )
        assert expected in preset.years_title(), preset.key


def test_a_packs_placeholder_reaches_the_managed_titles_helper():
    """Task 2's fix, exercised by a shipped row rather than a double: the
    placeholder is what the collision test counts for this family, and it is
    what the engine reserves on a real pass."""
    keys = [preset.key for preset, _, _ in _dynamic_rows()]
    config = build_config(_document(keys))

    for library_type in LIBRARY_TYPES:
        titles = _managed_titles(config, library_type)
        for preset, collection, _params in _dynamic_rows():
            if library_type in preset.library_types:
                assert collection.title in titles, (preset.key, library_type)
```

Then update `CATALOG_CHECKSUM` (`:188-198`) to what this task ships:

```python
CATALOG_CHECKSUM: dict[str, tuple[int, int, int]] = {
    "awards": (15, 0, 1),
    "charts": (10, 0, 1),
    "content": (2, 2, 0),
    "content_ratings": (7, 0, 1),
    "location": (1, 2, 0),
    "media": (2, 2, 0),
    "people": (1, 4, 0),
    "production": (1, 2, 0),
    "time": (1, 2, 0),
}
```

And replace `test_the_three_presets_9b_readjudicated_stay_gated_on_the_engine_row`
(`:1389-1416`) — two of its three subjects ship in this phase, so the test it
was cannot stand — with the test it becomes:

```python
def test_the_presets_9b_readjudicated_carry_their_evidence_into_the_pack():
    """Phase 9b proved the DATA path for three presets and none of the shapes;
    10a shipped the enumerator; 10b ships the packs. What must not be lost in
    that sequence is the evidence -- each of these rows names the live probe
    that measured its value count, so the next reader does not re-run it.

    ``media_audio_language`` ships here; ``production_network`` and
    ``media_subtitle_language`` ship in the task after this one. The assertion
    is written over whichever of them is READY, so it holds in both states
    rather than needing an edit between two commits of one phase.
    """
    for key in ("production_network", "media_audio_language",
                "media_subtitle_language"):
        preset = catalog.BY_KEY[key]
        assert "live probe" in preset.description, key
        assert "enumerat" in preset.description, key
        if preset.readiness == GATED:
            assert preset.gated_row == catalog.DYNAMIC_ENGINE_ROW, key

    # The client-side strand is still named -- it is why these cannot be built
    # out of a library walk -- but it was never what they waited on.
    assert "row %d" % catalog.STRANDED_FILTER_ROW in catalog.BY_KEY[
        "media_audio_language"
    ].description
```

- [ ] **Step 2: Run the new tests to see them fail**

```bash
docker compose -p p10bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py 2>&1 | tee /app/.superpowers/run-t3-red.log; echo EXIT=$?'
```

Expected: `test_there_are_dynamic_packs_to_hold_to_the_contract` fails on an
empty set, `test_the_catalog_table_checksum` fails on the counts, and the
`_dynamic_rows()`-driven tests pass vacuously (which is exactly why the guard
test exists). Paste the failure list.

- [ ] **Step 3: Write `src/autoposter/collections/packs.py`**

Data only. No imports beyond the standard library's typing needs — the module
is imported by `catalog.py`, whose purity is pinned structurally
(`test_the_catalog_module_imports_nothing_that_could_reach_the_world`), so a
`pathlib` or `httpx` here would be a network call inside config validation one
import removed.

```python
"""Kometa's dynamic packs, transcribed.

``catalog.py`` says what a preset IS; this module is what four of them are made
of. A dynamic pack is one ``builder: dynamic`` definition whose ``params``
carry Kometa's own answer to "which values get a collection, and what is each
one called" -- the include list, the addon merges, the name overrides and the
title format that live in a ``defaults/`` YAML file upstream. The engine that
consumes them shipped in phase 10a and is not touched by this module.

Separate from ``catalog.py`` for one reason: these are TABLES, several hundred
rows of somebody else's data, and ``catalog.py`` is already 1900 lines of
prose-heavy rows. Nothing here is logic. Every constant below is either a
transcription of a file named in its own comment or is marked ``NOT KOMETA:``
with the decision and the reason.

**Provenance.** Every file cited here was fetched from
github.com/Kometa-Team/Kometa at tag ``v2.4.8`` and read while these tables
were written; the fetch record, with byte counts and the full extracted tables,
is the phase's transcription document. The ids, keys, names and filter values
below are transcriptions of those files, not recollections of them -- the
discipline ``catalog.py``'s provenance section states for the whole catalog.

**Shape.** Each pack exports one ``*_PARAMS`` tuple of pairs, which is exactly
what a ``PresetCollection.params`` field takes and what
``PresetCollection.definition`` hands to ``DynamicParams``. Three of the params
are MAPPINGS (``addons``, ``key_name_override``, ``title_override``) and are
real dicts: pydantic will not build a ``dict[str, list[str]]`` from a tuple of
pairs (measured -- ``Input should be a valid dictionary``), so the pairs
convention stops at the value.
"""

__all__ = [
    "AUDIO_LANGUAGE_PARAMS",
    "COUNTRY_PARAMS",
    "DECADE_PARAMS",
    "GENRE_PARAMS",
]


# --- genre: defaults/both/genre.yml -------------------------------------------
#
# <one sentence: what the file's own include/exclude/addons do, from §1>

_GENRE_ADDONS: dict[str, list[str]] = {
    # transcribed from defaults/both/genre.yml, `addons:`, in file order
}

_GENRE_EXCLUDE: tuple[str, ...] = (
    # transcribed from defaults/both/genre.yml, `exclude:`
)

GENRE_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "genre"),
    ("title_format", <the file's own `title_format` string, verbatim>),
    ("addons", _GENRE_ADDONS),
    ("exclude", _GENRE_EXCLUDE),
)
```

**Three rules for filling those blanks, so none of them is a judgement call.**
(a) A pair is written only for a key the file actually carries — a pack whose
file pins no `title_format` omits the pair entirely and inherits the type's
default from `dynamic_types.py`, which is what an absent key means to the
builder. (b) An empty table is not written as `{}` or `()`; the pair is left
out. (c) `title_format` may be overridden by Task 4 Step 5 if it collides with
another pack's — that is the only value in this module a later task may change,
and it changes with a `# NOT KOMETA:` comment beside it.

Repeat that block shape for `DECADE_PARAMS`, `AUDIO_LANGUAGE_PARAMS` and
`COUNTRY_PARAMS`, with these pack-specific rules:

- **decade** — keys on `choice.key` (`1980`) and titles from `choice.title`
  (`1980s`); `include`/`exclude`/`addons` entries are matched against the KEY,
  so a transcribed `exclude: ["1980s"]` is written as upstream wrote it and
  `derive_keys` matches it against key OR value (`dynamic_keys.py:129-138`).
  Movie-only. If §1 shows a `limit` in `template_variables` (the "hundred
  highest-rated films" the catalog row already claims), pin it as
  `("limit", <n>)`; a `sort_by` is pinned ONLY if its name is in
  `search_sorts.KNOWN_SORT_NAMES`, and if it is not, keep the type's default
  and record the divergence in §5 and in the row.
- **audio_language** — `("max_collections", 80)`. `title_override` is the §3
  table if §3 found one, and is ABSENT if it did not (FLAG 3): an empty
  `title_override` is not written at all rather than written as `{}`.
- **country** — `("max_collections", 100)`. This is the pack with a value-set
  divergence (C6): the shipped `type: country` enumerates the Plex `country`
  TAG (63 values measured), while Kometa keys on TMDb origin-country. The
  `key_name_override`/`title_override` transcription therefore applies only
  where the Plex tag vocabulary overlaps Kometa's table; unmapped values keep
  the Plex name. Write that as a `# NOT KOMETA:` comment above the table AND in
  the row's description.

Every pin that §2 revised carries a one-line comment with the arithmetic.

- [ ] **Step 4: Add the shape-line derivation to `catalog.py`**

Imports, beside the existing ones at `:57-61`:

```python
from autoposter.collections.dynamic_titles import render_title
from autoposter.collections.dynamic_types import DYNAMIC_TYPES
```

(Both are pure — `dynamic_types` imports only `dataclasses`, `dynamic_titles`
only `collections.abc`, `dataclasses` and `dynamic_keys` — so the import-list
purity test is unaffected. Neither imports `catalog`, so there is no cycle.)

Then, replacing the `YEARS_PLACEHOLDER` neighbourhood's end (after
`award_years_title`, `:93-101`):

```python
# What goes where the KEY does in a dynamic pack's shape line, and where the
# library type does. Both are placeholders for the same reason
# ``YEARS_PLACEHOLDER`` is one: a family's real titles are decided by something
# this table cannot see -- the dataset's years, or the library's own values --
# and a shape is the honest thing to show instead of a guess.
DYNAMIC_KEY_PLACEHOLDER = "<%s>"
DYNAMIC_LIBRARY_TYPE = "<Library type>"


def _shape_line(what: str, shape: str) -> str:
    """One line for a family this table can name the SHAPE of and not the
    members of. Shared by the two families that have one -- an award
    ceremony's year collections and a dynamic pack -- because the picker
    renders one string and there is no reason for it to be assembled two
    ways."""
    return 'one per %s, named "%s"' % (what, shape)


def dynamic_shape(params: dict) -> str:
    """The family-shape line for one dynamic pack.

    Derived, not restated. The format is the pack's own pinned
    ``title_format`` when it has one and the TYPE's default when it does not
    (``dynamic_types.DYNAMIC_TYPES``), and it is rendered by the same
    ``dynamic_titles.render_title`` the builder names collections with -- so
    the sentence in the picker cannot describe a shape the builder will not
    produce. The two unknowns are written as placeholders: the key the library
    will supply, and the library type, which differs between a preset's
    libraries and would be a lie if one of them were picked.
    """
    row = DYNAMIC_TYPES[params["type"]]
    noun = row.name.replace("_", " ")
    key_name = DYNAMIC_KEY_PLACEHOLDER % noun
    shape = render_title(
        params.get("title_format") or row.title_format,
        key_name,
        DYNAMIC_LIBRARY_TYPE,
        key=key_name,
        values=(),
        auto_type=row.name,
    )
    return _shape_line("%s the library holds" % noun, shape)
```

Replace `Preset.years_title` (`:304-308`) with:

```python
    def years_title(self) -> str | None:
        """The shape of this preset's dynamic titles, or None if it has none.

        Two families build collections this table cannot name. An award
        ceremony's year collections are named by the years the dataset carries;
        a dynamic pack's family is named by the values the library holds.
        Neither is knowable here, and both answer with the same one-line SHAPE
        through the same payload key the picker already renders -- one field,
        one UI branch, no second shape for the second family to drift from
        (10b decision C3).
        """
        if self.award_event is not None:
            return _shape_line(
                "ceremony", EVENTS[self.award_event].year_title % "<year>"
            )
        for collection in self.collections:
            if collection.builder == "dynamic":
                return dynamic_shape(dict(collection.params))
        return None
```

Add the measured exception to `PresetCollection`'s docstring, after the
"pairs rather than dicts so a row stays hashable" sentence (`:136-138`):

```
    One exception, measured rather than assumed: a dynamic pack's ``addons``,
    ``key_name_override`` and ``title_override`` params are MAPPINGS, and
    pydantic will not build a ``dict[str, list[str]]`` from a tuple of pairs
    (``Input should be a valid dictionary``). Those values are dicts, so a row
    carrying one is not hashable -- nothing hashes these rows today, and the
    alternative (a pairs-to-dict conversion keyed on param names) would put
    one builder's vocabulary inside a generic row.
```

- [ ] **Step 5: Flip the four rows**

`content_genres` (`:787-807`) — the description is rewritten from "what is
missing is the PRESET" into the shipped record:

```python
    Preset(
        key="content_genres",
        category="content",
        name="Genres",
        description=(
            "One collection per genre the library actually holds -- Kometa's "
            "largest pack, transcribed from `defaults/both/genre.yml` "
            "(its exclusions and addon merges are "
            "`collections/packs.py`'s table). Built by the per-value engine "
            "phase 10a shipped: `builder: dynamic`, `type: genre`, one "
            "definition that expands against the library on every pass. "
            "Roughly 25 genres on the production movie library, inside the "
            "engine's default `max_collections` of 50, so this pack pins no "
            "cap. The genre attribute's own caveat stands and is why the "
            "family is built by SEARCH rather than from the section listing, "
            "which phase 9a's probe found truncates to two tags per item (row "
            "%d). To build something other than what this pack builds, copy it "
            "into a `definitions:` entry of your own and edit it there -- a "
            "preset is a key, not a copy of the definitions it stands for."
            % STRANDED_FILTER_ROW
        ),
        kometa_source="defaults/both/genre.yml",
        library_types=_BOTH,
        collections=(
            PresetCollection(
                title="Genres", builder="dynamic", params=packs.GENRE_PARAMS,
            ),
        ),
    ),
```

with `from autoposter.collections import packs` added to the import block.

`time_decade` (`:1692-1713`) — same treatment: keep the sentences that are
still true ("'Best of the 1980s' and its neighbours … movie-only, because
Plex's decade filter is"), delete the "What is missing is the PRESET" sentence,
add `type: decade`, the ~12-decades-inside-the-default-cap sentence, the pinned
`limit` if §1 has one, and the `definitions:` customisation sentence. The row's
collection is `PresetCollection(title="Decades", builder="dynamic",
params=packs.DECADE_PARAMS)`.

`media_audio_language` (`:1383-1417`) — keep the 9a/9b evidence sentences
verbatim (the rewritten test above asserts "live probe" and "enumerat" are
still there), keep the `any:`-conjunction sentence and the five-shape
vocabulary sentence (they are row 182's finding and this pack is what
discharges it), delete "What is still missing is the PRESET", and add:

- `` `builder: dynamic`, `type: audio_language` ``;
- the cap sentence naming **80** and its reasoning ("46 measured, pinned at 80
  so one new locale variant in the library cannot make the whole family
  refuse");
- the naming sentence, whichever branch §3 settled — either "Kometa's own
  language names, transcribed into `title_override`, with the N Plex values
  that table does not cover keeping the name Plex gives them" or "named from
  Plex's own `choice.title`, which is the fallback branch upstream itself
  ships; vendoring TMDb's ISO-639-1 name table is roadmap row %d" %
  TMDB_LANGUAGE_NAME_ROW. That constant does not exist yet; add it to the
  blocker constants block (`:581-596`) in the same shape as its neighbours:

  ```python
  TMDB_LANGUAGE_NAME_ROW = 190  # upstream names a language bucket from TMDb's
                                # ISO-639-1 table and we name it from Plex's
                                # own choice.title -- names only, never
                                # membership, and the two agree on the common
                                # codes
  ```

  It is a CITED row, not a blocker: no preset's `gated_row` may be set to it
  (`test_every_preset_row_is_internally_consistent` holds READY rows to
  `gated_row is None`);
- the `definitions:` customisation sentence.

Collection: `PresetCollection(title="Audio languages", builder="dynamic",
params=packs.AUDIO_LANGUAGE_PARAMS)`.

`LOCATION_PRESETS` (`:1276-1313`) — `location_country` leaves the comprehension
and becomes an explicit row; `location_region` and `location_continent` stay in
the comprehension, still `GATED` on `DYNAMIC_ENGINE_ROW` (Task 5 re-points
them, and doing it here would mix two tasks' reasons in one diff):

```python
# Two packs, one blocker, plus one that shipped. The include/exclude, addon-
# merge and title-format machinery each of these three files configures IS the
# dynamic engine, and that engine shipped in phase 10a. What separates them now
# is where their VALUES come from: `country` is a Plex tag this service can
# enumerate, so that pack ships; `region` and `continent` are groupings of
# TMDb's origin-country data, which is a full-library TMDb walk and not an
# enumeration at all.
_LOCATION_PACKS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("location_region", "Regions", "defaults/movie/region.yml",
     "One collection per world region (Nordic, Balkan, Southeast Asia and the "
     "rest of Kometa's grouping).", _MOVIE),
    ("location_continent", "Continents", "defaults/movie/continent.yml",
     "One collection per continent -- the coarsest of the three location "
     "packs.", _MOVIE),
)
```

and, before the comprehension, the shipped country row, whose description
carries the C6 divergence in full:

```python
_COUNTRY_PRESET = Preset(
    key="location_country",
    category="location",
    name="Countries",
    description=(
        "One collection per country the library's own metadata names, with "
        "Kometa's per-country name and flag styling transcribed from "
        "`defaults/movie/country.yml`. Movie libraries only, as upstream has "
        "it. Built by `builder: dynamic`, `type: country` -- 63 values on the "
        "production movie library, measured by phase 10a's probe, and this "
        "pack pins `max_collections: 100` so a library with a wider spread "
        "than that one does not refuse. **One divergence, and it is about "
        "MEMBERSHIP, not names:** the values here are the Plex `country` TAG, "
        "which is the production country Plex's agent recorded, while Kometa "
        "reads TMDb's origin-country field. The two disagree on exactly the "
        "co-productions an operator would notice, and the Kometa-exact "
        "variant needs a full-library TMDb walk, which is row %d. Values that "
        "Kometa's styling table does not name keep the name Plex gives them. "
        "To build something else, copy this pack into a `definitions:` entry "
        "of your own and edit it there."
        % TMDB_ORIGIN_COUNTRY_ROW
    ),
    kometa_source="defaults/movie/country.yml",
    library_types=_MOVIE,
    collections=(
        PresetCollection(
            title="Countries", builder="dynamic", params=packs.COUNTRY_PARAMS,
        ),
    ),
)

LOCATION_PRESETS: tuple[Preset, ...] = (_COUNTRY_PRESET,) + tuple(
    Preset(
        key=key,
        category="location",
        name=name,
        description=(
            # unchanged in this task; Task 5 re-points it to row 189
            "%s The per-value engine SHIPPED in phase 10a, so the machinery "
            "this pack is made of -- enumeration, addon merges, key-name "
            "overrides, title formats -- is here. Two things are not. Its "
            "values are Kometa's, read off TMDb's origin-country data, and "
            "that is a full-library TMDb walk rather than an enumeration, "
            "which is why `origin_country` is not one of the ten dynamic types "
            "that shipped (row %d); the Plex `country` tag IS enumerable (63 "
            "values on the production movie library, measured by phase 10a's "
            "probe) but it is a different value set. And the PRESET itself is "
            "the preset-expansion story of phase 10b, which is what row %d "
            "tracks."
            % (description, TMDB_ORIGIN_COUNTRY_ROW, DYNAMIC_ENGINE_ROW)
        ),
        kometa_source=source,
        library_types=library_types,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
    )
    for key, name, source, description, library_types in _LOCATION_PACKS
)
```

Finally update the count-checksum comment above `CATALOG` (`:1738-1753`) to the
new per-category counts and the new totals — `content 2/2`, `location 1/2`,
`media 2/2`, `time 1/2`, and "57 rows: 40 presets an operator can switch on
today, 14 that name what they would build …". Recount from the table rather
than trusting this sentence; the checksum test is what proves the recount.

- [ ] **Step 6: The one-line picker change**

`frontend/src/pages/CatalogPanel.tsx:136-141`:

```tsx
  // The dynamic half is reported as a SHAPE, never as titles -- which years
  // the dataset carries, or which values the library holds, is not knowable
  // from a table. The whole clause is served by the catalog so the two
  // families that have one (award years, dynamic packs) read the same way and
  // neither is assembled here.
  const dynamic = preset.years_title;
  return titles === ""
    ? `Builds ${dynamic}`
    : `Builds: ${titles}, plus ${dynamic}`;
```

Nothing else in `frontend/` changes: `years_title` keeps its name and its
`string | null` type (`api/types.ts:422`), no test asserts the old rendered
sentence, and the fixtures in `CatalogPanel.test.tsx` / `Collections.test.tsx`
are values, not assertions — update those four fixture strings to whole clauses
(`'one per ceremony, named "Cannes <year>"'`) so they read like real payloads.

Update the two backend payload expectations for the same reason:
`tests/test_collection_catalog.py:1332` becomes
`"years_title": 'one per ceremony, named "Cannes <year>"',` and `:335` becomes

```python
        assert preset.years_title() == (
            'one per ceremony, named "%s"' % (event.year_title % "<year>")
        )
```

- [ ] **Step 7: Run the suite**

```bash
docker compose -p p10bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py tests/test_config_schema.py tests/test_api_collections_builders.py 2>&1 | tee /app/.superpowers/run-t3-green1.log; echo EXIT=$?'
```

Expected: all pass. Then the whole suite, detached (global constraint 7):

```bash
docker compose -p p10bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    up -d postgres
docker compose -p p10bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10bt3-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t3-full.log; echo EXIT=$? >> /app/.superpowers/run-t3-full.log'
docker wait p10bt3-full
tail -30 .superpowers/run-t3-full.log
```

Then the frontend:

```bash
docker compose -p p10bt3 run --rm web npm test > .superpowers/run-t3-vitest.log 2>&1; echo EXIT=$?
tail -20 .superpowers/run-t3-vitest.log
```

- [ ] **Step 8: Prove the golden gate did not move**

```bash
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
git diff --stat origin/main -- src/autoposter/collections/builders/ \
    src/autoposter/collections/engine.py src/autoposter/collections/dynamic_types.py \
    src/autoposter/collections/dynamic_keys.py src/autoposter/collections/dynamic_titles.py
```

Both must print nothing (global constraints 2 and 6). Paste the empty output.

- [ ] **Step 9: Mutation proof — the shape line is derived, not restated**

Backup + cmp (global constraint 8). In `packs.py`, change `GENRE_PARAMS`'
`title_format` to a different string; expected RED:
`test_a_packs_shape_line_comes_from_the_renderer_the_builder_uses` stays GREEN
(it re-derives) while `test_every_opinion_a_pack_pins_is_stated_in_the_row`
goes RED if the format is an opinion the row states. Then, in `catalog.py`,
hardcode `dynamic_shape` to return a literal sentence; expected RED:
`test_a_packs_shape_line_comes_from_the_renderer_the_builder_uses` for all four
packs. Restore, `cmp`, re-run green. Paste all four halves.

- [ ] **Step 10: Lint and commit**

```bash
docker compose -p p10bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
git add src/autoposter/collections/packs.py src/autoposter/collections/catalog.py \
    tests/test_collection_catalog.py frontend/src/pages/CatalogPanel.tsx \
    frontend/src/pages/CatalogPanel.test.tsx frontend/src/pages/Collections.test.tsx
git commit --no-gpg-sign -m "feat(collections): the genre, decade, audio-language and country packs

Four Kometa dynamic packs ship as catalog rows: one transcribed table each in
collections/packs.py, one `builder: dynamic` definition per preset, and the
readiness flip that lets an operator switch them on by key. The picker learns
what a family will be called from the same renderer the builder titles
collections with, through the payload key the award years already use -- no
new config, picker or endpoint shape.

Caps are opinions and each one is stated in the row it ships in: audio
languages pin 80 against 46 measured, countries 100 against 63. The country
pack's value set is the Plex country tag and not TMDb's origin country; the
divergence is in the description and row 189 keeps the exact variant."
docker compose -p p10bt3 down
```

---

## Task 4: The three big packs, and the format-collision proof

**Files:**
- Modify: `src/autoposter/collections/packs.py` (three more tables)
- Modify: `src/autoposter/collections/catalog.py` — `media_subtitle_language`
  (`:1418-1443`), `production_studio` (`:1601-1625`), `production_network`
  (`:1626-1661`), the count checksum comment
- Modify: `config/autoposter.example.yaml:69-77`
- Modify: `tests/test_collection_catalog.py` — `CATALOG_CHECKSUM`, the pack
  guard test's expected set, the new collision test
- Test: `tests/test_collection_catalog.py`

**Interfaces:**
- Produces, in `packs.py`: `SUBTITLE_LANGUAGE_PARAMS`, `STUDIO_PARAMS`,
  `NETWORK_PARAMS` — same `tuple[tuple[str, object], ...]` shape as Task 3's
  four; plus `STUDIO_INCLUDE: tuple[str, ...]` (and `NETWORK_INCLUDE` if §1
  found one), which the cap pin is computed FROM.
- Consumes: Task 3's `_dynamic_rows()`, `dynamic_shape()`,
  `DYNAMIC_KEY_PLACEHOLDER`; Task 1 §1/§2/§4/§5.

**Background.** These three are the packs whose numbers are the reason the
engine has a cap at all: 824 studio values, 115 subtitle languages, 91
networks. Two things follow. First, `include` is a whitelist applied LAST and
the cap is checked against the DERIVED titles, so a pack with an include list
is bounded by that list's length whatever the library holds. Second, these are
the three families where un-ticking the preset is a family-wide narrowing an
operator should be told about before they do it (C10) — the sweep's guards
(`delete_unconfigured` off by default, `max_deletes`, protected labels,
dry-run) all apply and a narrowing past `max_deletes` refuses the whole
library's sweep loudly, but nothing warns them in advance except these three
sentences.

- [ ] **Step 1: Extend the guard test and the checksum**

In `tests/test_collection_catalog.py`,
`test_there_are_dynamic_packs_to_hold_to_the_contract`'s expected set becomes
all seven keys, and `CATALOG_CHECKSUM` becomes:

```python
CATALOG_CHECKSUM: dict[str, tuple[int, int, int]] = {
    "awards": (15, 0, 1),
    "charts": (10, 0, 1),
    "content": (2, 2, 0),
    "content_ratings": (7, 0, 1),
    "location": (1, 2, 0),
    "media": (3, 1, 0),
    "people": (1, 4, 0),
    "production": (3, 0, 0),
    "time": (1, 2, 0),
}
```

- [ ] **Step 2: Write the format-collision test**

Append to the dynamic-packs section:

```python
_FORMAT_SENTINEL = "\x1fKEY\x1f"


def _rendered_formats(preset, params) -> list[tuple[str, str]]:
    """One ``(library_type, rendered format)`` pair per library this pack
    serves, with the KEY token replaced by a sentinel no real value can be."""
    from autoposter.collections.dynamic_titles import render_title
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    row = DYNAMIC_TYPES[params["type"]]
    return [
        (
            library_type,
            render_title(
                params.get("title_format") or row.title_format,
                _FORMAT_SENTINEL,
                library_type,
                key=_FORMAT_SENTINEL,
                values=(),
                auto_type=row.name,
            ),
        )
        for library_type in preset.library_types
    ]


def test_no_two_families_an_operator_can_co_enable_share_a_title_format():
    """The collision the whole-table title test cannot see.

    ``test_every_ready_preset_at_once_never_builds_one_title_twice`` compares
    the titles this table can NAME, and a dynamic pack names only its
    placeholder -- the family's real titles are the library's and appear
    nowhere offline. So two packs with the same ``title_format`` pass that test
    and then create the same collection title on a real library the moment
    their key spaces touch, which for the two language packs is not a
    coincidence but a certainty: they enumerate almost the same vocabulary.

    This is the offline half of the answer: two enabled families must not share
    a rendered format for one library type. It catches the whole class rather
    than the instances that bite today. The residual -- two DIFFERENT formats
    that happen to render the same string for specific runtime values -- is
    rows 135/162's documented gap and is not caught here or anywhere else.

    ``cs_bucket``'s static titles are in the comparison because it is a shipped
    family an operator cannot switch off, so a pack that rendered into one of
    its titles would collide with something always present.
    """
    from autoposter.collections.buckets import derive_buckets

    seen: dict[tuple[str, str], str] = {}
    for preset, _collection, params in _dynamic_rows():
        for library_type, rendered in _rendered_formats(preset, params):
            previous = seen.get((library_type, rendered))
            assert previous is None, (library_type, rendered, previous, preset.key)
            seen[(library_type, rendered)] = preset.key

    for library_type in LIBRARY_TYPES:
        for bucket in derive_buckets(set(), library_type):
            clash = seen.get((library_type, bucket.title))
            assert clash is None, (library_type, bucket.title, clash)


def test_the_collision_test_can_actually_fail():
    """A test that has never been seen red is a test nobody has debugged.

    Two packs are given one format here on purpose, and the check the test
    above performs is run over them directly -- so the assertion's teeth are
    proven without waiting for a real collision to ship.
    """
    seen: dict[tuple[str, str], str] = {}
    collided = []
    for key, rendered in (("pack_a", "Top KEY Movies"), ("pack_b", "Top KEY Movies")):
        if ("Movie", rendered) in seen:
            collided.append((key, seen[("Movie", rendered)]))
        seen[("Movie", rendered)] = key

    assert collided == [("pack_b", "pack_a")]
```

- [ ] **Step 3: Run to see the new tests fail**

```bash
docker compose -p p10bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py 2>&1 | tee /app/.superpowers/run-t4-red.log; echo EXIT=$?'
```

Expected: the guard test fails on the missing three keys and the checksum test
fails on `media`/`production`. The collision test passes at this point (four
packs, and Task 3 already had to make those four distinct); it becomes
load-bearing in Step 5.

- [ ] **Step 4: Write the three tables into `packs.py`**

Same block shape as Task 3, from §1, with these pack-specific rules:

- **subtitle_language** — `("max_collections", 150)`. The pack IS
  one-per-language: narrowing it to an include list is not what Kometa ships,
  and 115 measured against a 150 pin leaves room for a library with a wider
  spread. `title_override` per §3/FLAG 3, exactly as the audio pack.
- **studio** — three module-level tables in Task 3's shape,
  `STUDIO_INCLUDE: tuple[str, ...]` (the file's `include:`, whole, in file
  order), `_STUDIO_ADDONS: dict[str, list[str]]` (its `addons:`) and
  `_STUDIO_KEY_NAMES: dict[str, str]` (its `key_name_override:`), each under
  its own provenance comment, and then:

  ```python
  STUDIO_PARAMS: tuple[tuple[str, object], ...] = (
      ("type", "studio"),
      ("include", STUDIO_INCLUDE),
      # `include` is a whitelist applied LAST (dynamic_keys' module docstring,
      # meta.py:1348-1351) and the engine's cap is checked against the DERIVED
      # titles, so this list is a hard upper bound on the family whatever the
      # library holds -- which is why the pin is computed from it rather than
      # written as a number that could drift from it. The raw enumeration (824
      # values on the production movie library) never reaches the cap.
      ("max_collections", len(STUDIO_INCLUDE)),
      ("addons", _STUDIO_ADDONS),
      ("key_name_override", _STUDIO_KEY_NAMES),
  )
  ```

  Note `key_from` for this type is the TITLE, not `choice.key` — Plex answers
  the studio filter's key double percent-encoded (`20th%2520Century%2520Fox`),
  which the probe measured — so `include`/`addons`/overrides are matched
  against the display name, and the transcribed list must be upstream's
  display names. If §1's list is keyed on something else, say so in the report
  and STOP rather than shipping a whitelist that matches nothing.
- **network** — show-only. If §1 found an `include:`, use it and pin
  `("max_collections", len(NETWORK_INCLUDE))`; if it did not, pin
  `("max_collections", 120)` against 91 measured. Either way the breadth
  caveat goes in the row (the search mechanism is proven at ONE network with
  two shows; 91 exist and nothing has checked they all behave).

- [ ] **Step 5: Break the format collisions**

Run §4's table against what is now in `packs.py`:

```bash
docker compose -p p10bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test python -c "
from autoposter.collections.catalog import CATALOG, READY
from autoposter.collections.dynamic_titles import render_title
from autoposter.collections.dynamic_types import DYNAMIC_TYPES
for p in CATALOG:
    if p.readiness != READY:
        continue
    for c in p.collections:
        if c.builder != 'dynamic':
            continue
        params = dict(c.params)
        row = DYNAMIC_TYPES[params['type']]
        for lt in p.library_types:
            print(lt, '|', p.key, '|', render_title(
                params.get('title_format') or row.title_format,
                'KEY', lt, key='KEY', values=(), auto_type=row.name))
"
```

Paste that table into the report. **The rule:** where two packs an operator can
co-enable render the same format for the same library type, keep Kometa's
format on the pack whose file is the canonical home of that shape and pin a
distinguishing one on the other(s), recorded as a `# NOT KOMETA:` comment in
`packs.py` **and** stated in the row's description. Use these, in this order of
preference, and only where a collision forces it:

| pack | format if it must move |
| --- | --- |
| `content_genres` | `<<key_name>> <<library_typeU>>s` (keeps Kometa's; genre is the canonical home of the plain shape) |
| `production_studio` | `Top <<key_name>> <<library_typeU>>s` |
| `location_country` | `Best <<library_typeU>>s from <<key_name>>` |
| `production_network` | `<<library_typeU>>s on <<key_name>>` |
| `media_audio_language` | `<<key_name>> Audio` |
| `media_subtitle_language` | `<<key_name>> Subtitles` |
| `time_decade` | `Best <<library_typeU>>s of the <<key_name>>` (the type's own default; already distinct) |

Every format must satisfy `dynamic_titles.title_format_names_the_key` — it
must contain `<<key_name>>` or `<<title>>`, or the builder's params model
refuses it at config load. The divergence sentence in the row reads, in the
row's own words: what Kometa's format is, what this pack uses instead, and that
the reason is that two families an operator can switch on together must not
share a title format.

- [ ] **Step 6: Flip the three rows**

Same treatment as Task 3's four. Keep every evidence sentence
(`media_subtitle_language`'s "115 values", `production_network`'s "9b's live
probe read 91 networks" and the New-Plex-TV-Agent gate,
`production_studio`'s "824 studio values"), delete every "What is missing is
the PRESET" sentence, and add to each:

- `` `builder: dynamic`, `type: <name>` ``;
- the cap sentence with its number and reasoning (for studio: "Kometa's
  include list is N studios long and a whitelist is applied last, so the family
  cannot exceed it whatever the library holds — the cap is that number");
- **the C10 un-tick sentence**, one per row, in these words adapted to the
  pack: "Switching this off is a family-wide narrowing: every collection it
  built becomes a sweep candidate at once. That sweep is off unless
  `collections.delete_unconfigured` is on, it never exceeds
  `collections.max_deletes` in a pass, it skips protected labels — and past
  the cap it refuses the whole library's sweep and reports the numbers rather
  than deleting a prefix of the family.";
- the `definitions:` customisation sentence.

Collections: `PresetCollection(title="Subtitle languages", …)`,
`PresetCollection(title="Studios", …)`, `PresetCollection(title="Networks", …)`.

Update the count-checksum comment above `CATALOG` again: `media 3/1`,
`production 3/0`, and "57 rows: 43 presets an operator can switch on today, 11
that name what they would build and the roadmap row that would let them, and 3
rendered switches".

- [ ] **Step 7: The operator note in the example config**

`config/autoposter.example.yaml`, extending the `presets:` comment at `:69-77`:

```yaml
  # Preset collections from the catalog, switched on by key. Empty by default,
  # and an empty list builds nothing extra -- the toggles above are unaffected.
  # The keys are the ones GET /api/collections/catalog lists (the Collections
  # page's picker writes this field); an unknown one is rejected when the
  # config loads. The Oscars are the `awards:` toggle above, not a key here.
  #
  # Some presets are PACKS: one key builds one collection per value the library
  # holds (every genre, every studio, every audio language). Switching a pack
  # ON creates that whole family on the next pass, and switching it OFF makes
  # every one of those collections an orphan at once -- which the sweep only
  # deletes if `delete_unconfigured` above is on, never more than `max_deletes`
  # of them in a pass, and never at all past that cap (it refuses the whole
  # sweep and reports the numbers instead). A pack whose enumeration would
  # exceed its own `max_collections` creates nothing and says so, rather than
  # building a prefix of the family.
  # Example:
  #   presets:
  #     - award_cannes # Cannes Golden Palm Winners + one per recent festival
  #     - content_genres # one collection per genre the library holds
```

- [ ] **Step 8: Run everything**

The same three runs as Task 3 Step 7 (targeted, full detached, vitest), with
project `p10bt4`, plus the golden and engine-diff proofs from Task 3 Step 8.

- [ ] **Step 9: Mutation proof — the collision test has teeth on real data**

Backup + cmp. Set `SUBTITLE_LANGUAGE_PARAMS`' `title_format` equal to
`AUDIO_LANGUAGE_PARAMS`'. Expected RED:
`test_no_two_families_an_operator_can_co_enable_share_a_title_format`, naming
both keys and both library types. Restore, `cmp`, re-run green. Then the second
mutation: drop `("max_collections", len(STUDIO_INCLUDE))` from `STUDIO_PARAMS`.
Expected RED: `test_every_opinion_a_pack_pins_is_stated_in_the_row` — the row
still states a number the params no longer pin. Paste all four halves.

- [ ] **Step 10: Lint and commit**

```bash
docker compose -p p10bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
git add src/autoposter/collections/packs.py src/autoposter/collections/catalog.py \
    tests/test_collection_catalog.py config/autoposter.example.yaml
git commit --no-gpg-sign -m "feat(collections): the studio, subtitle-language and network packs

The three packs whose fan-out is the reason the engine has a cap. Studio ships
Kometa's include list and pins max_collections to its length -- a whitelist is
applied last and the cap is checked against the derived titles, so the list is
a hard upper bound and the 824 raw values never reach it. Subtitle languages
pin 150 against 115 measured; networks pin their own against 91.

Each of the three says in its own description what un-ticking it means: a
family-wide narrowing, through delete_unconfigured, max_deletes and the
refusal past the cap. A new whole-table test refuses two co-enablable families
sharing a rendered title format -- the collision the title test cannot see,
because a pack names only its placeholder offline."
docker compose -p p10bt4 down
```

---

## Task 5: The four re-pointings

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (new row
  192, in the gap table after row 191)
- Modify: `src/autoposter/collections/catalog.py` — the blocker constants
  block (`:581-596`), `content_franchises` (`:808-832`), the
  `LOCATION_PRESETS` comprehension, `time_year` (`:1667-1691`)
- Test: `tests/test_collection_catalog.py` (existing gated-row tests cover it;
  one new assertion)

**Interfaces:**
- Produces: `TMDB_COLLECTION_TYPE_ROW = 192` in `catalog.py`'s constants block.
- Consumes: nothing from Tasks 3/4 beyond the table they left.

**Background.** Eleven presets were gated on row 102. Seven ship. The other
four were never waiting on 102 at all — 10a-2's wrap re-pointed their TEXT at
"10b's preset-expansion story", and 10b is now here and cannot build them. A
row still citing 102 after 102 closed and its preset-expansion phase shipped is
a citation that has stopped meaning anything, which is the failure
`Preset.gated_row`'s docstring exists to prevent. Each now cites what actually
blocks it.

- [ ] **Step 1: File roadmap row 192**

Append after row 191 in the gap table, in the table's own six-column shape
(`| N | title | body | size | category | deps |`):

```
| 192 | `tmdb_collection` is a dynamic type nothing here can enumerate | Filed by phase 10b, out of adjudication C1, because `content_franchises` had been gated on row 102 and row 102 closed without being able to help it. Kometa's franchise pack is `dynamic_collections: {tmdb_collection: ...}` — one collection per TMDb franchise collection the library's items belong to, with its addon merges (Prometheus into Alien, Minions into Despicable Me) and its "Collection" suffix stripped. It is not a library enumeration: `listFilterChoices` has no field for it, and `collections/dynamic_types.py`'s scope note already names it among the deliberate absences (`dynamic_types.py:38-39`). Getting the value set means reading every item's TMDb record and collecting the `belongs_to_collection` ids — the same metadata-prefetch budget rows 155/180/189 keep running into, with its own cache, rate limit and not-found behaviour. The single-collection `tmdb_collection` BUILDER is already shipped (phase 8b), so what is missing is exactly the enumeration and nothing else. Deliberately not built in 10b: adding an enumeration seam was out of that phase's scope by adjudication C1 | M–L — a budgeted per-item TMDb walk, then one dynamic-type row | parity-only | 189 |
```

- [ ] **Step 2: Add the constant**

In `catalog.py`'s blocker constants (`:581-596`), after
`TMDB_ORIGIN_COUNTRY_ROW`:

```python
TMDB_COLLECTION_TYPE_ROW = 192  # the franchise pack's dynamic type: a TMDb
                                # walk over every item's belongs_to_collection,
                                # not a listFilterChoices enumeration -- filed
                                # by phase 10b when row 102 closed without
                                # being able to help it
```

- [ ] **Step 3: Re-point `content_franchises`**

`gated_row=TMDB_COLLECTION_TYPE_ROW`. The description keeps everything up to
and including "so there is no `type:` an operator can write for this family
today." and replaces everything after it with:

```python
            "The single-collection tmdb_collection builder it would call "
            "is already here; what is outstanding is the enumeration, and "
            "that is a TMDb walk over every item's `belongs_to_collection` "
            "rather than a Plex listing -- row %d, filed for exactly this "
            "pack. Phase 10b shipped the seven packs whose values Plex can "
            "enumerate today and deliberately added no new enumeration seam, "
            "so this one waits for the metadata-prefetch budget rows 155, 180 "
            "and 189 also wait on."
            % TMDB_COLLECTION_TYPE_ROW
```

- [ ] **Step 4: Re-point `location_region` and `location_continent`**

In the `LOCATION_PRESETS` comprehension Task 3 left, `gated_row` becomes
`TMDB_ORIGIN_COUNTRY_ROW` and the shared description text becomes:

```python
            "%s The per-value engine shipped in phase 10a and the country pack "
            "beside this one ships today -- so what this row waits on is "
            "neither machinery nor a preset. It is the VALUES: Kometa reads "
            "them off TMDb's origin-country data, which is a full-library TMDb "
            "walk rather than an enumeration (row %d), and the grouping "
            "vocabulary on top of it is upstream's own -- 'Nordic', 'Balkan', "
            "'Southeast Asia' are Kometa's names for sets of those countries, "
            "not values any library holds. The Plex `country` tag this "
            "service CAN enumerate is a different value set and cannot be "
            "grouped into these regions without that same table. Phase 10b "
            "shipped `location_country` on the Plex tag and left this pack "
            "where its data is."
            % (description, TMDB_ORIGIN_COUNTRY_ROW)
```

- [ ] **Step 5: Re-point `time_year`, and record the refusal**

`gated_row=RELATIVE_YEAR_ROW`. The description keeps everything up to and
including "(the probe counted 87 of them on the production movie library, past
the default `max_collections` of 50)." and replaces everything after it with:

```python
            "What did NOT ship is the counting relative to today: the "
            "`current_year`/`current_year-N` value grammar is the half of row "
            "%d that phase 10a left open, and without it 'the last ten years' "
            "has no expression here -- so this pack waits on that row and not "
            "on the engine. Phase 10b considered shipping the pack it COULD "
            "build -- one collection per year the library holds -- and "
            "refused: the probe counted 87 years on the production movie "
            "library, and 87 unbounded years is not 'Best of the last ten "
            "years' wearing its name. A preset that builds something other "
            "than the pack it cites is worse than one that waits."
            % RELATIVE_YEAR_ROW
```

- [ ] **Step 6: Add the assertion that the re-pointings are real**

```python
def test_no_preset_still_waits_on_the_row_the_dynamic_engine_closed():
    """Row 102 closed in phase 10a-2 and its preset-expansion phase (10b) has
    shipped. A row still citing it would be citing work that is DONE, which is
    the same as citing nothing -- so every preset that was gated on it has
    either shipped or now names what actually blocks it.

    ``DYNAMIC_ENGINE_ROW`` stays in the module: the packs' descriptions cite it
    as the phase that shipped their engine, which is a different kind of
    citation from a blocker.
    """
    still_waiting = [
        preset.key for preset in CATALOG
        if preset.gated_row == catalog.DYNAMIC_ENGINE_ROW
    ]

    assert still_waiting == []
    assert {
        catalog.BY_KEY[key].gated_row
        for key in ("content_franchises", "location_region",
                    "location_continent", "time_year")
    } == {
        catalog.TMDB_COLLECTION_TYPE_ROW,
        catalog.TMDB_ORIGIN_COUNTRY_ROW,
        catalog.RELATIVE_YEAR_ROW,
    }
```

`test_every_gated_row_cites_a_roadmap_row_that_exists` (`:467-480`) already
reads the roadmap document, so row 192 must be in it before this passes — that
is Step 1, and the ordering is the point.

- [ ] **Step 7: Run, lint, commit**

```bash
docker compose -p p10bt5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py 2>&1 | tee /app/.superpowers/run-t5-green.log; echo EXIT=$?'
docker compose -p p10bt5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md \
    src/autoposter/collections/catalog.py tests/test_collection_catalog.py
git commit --no-gpg-sign -m "fix(catalog): the four packs 10b cannot build cite what actually blocks them

Row 102 closed and its preset-expansion phase shipped seven packs; the other
four were never waiting on it. Franchises wait on a new row 192 -- the
tmdb_collection dynamic type is a TMDb walk over every item's
belongs_to_collection, not a Plex enumeration. Regions and continents wait on
189, where their values and their grouping vocabulary both live. The year pack
waits on 171's current_year grammar, and the divergent pack it could have
built instead -- one collection per year the library holds, 87 of them -- is
refused in the row, in writing."
docker compose -p p10bt5 down
```

---

## Task 6: The wrap

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` — the
  `#### 10b` section (`:822-849`), row 93 (`:195`), row 182 (`:278`), row 190
  (`:286`), row 49 (`:145`)
- Create: `.superpowers/sdd/p10b-pr-body.md`
- Test: the whole suite, plus the evidence sweep

**Interfaces:**
- Consumes: every earlier task's outcome. Nothing produces code here.

- [ ] **Step 1: Re-scope the 10b section honestly**

Replace the `#### 10b — Dynamic packs and defaults equivalents` block. Its
current text claims a phase that closes rows 93 and 49 and fills in "the
eighteen gated families once their blockers land". That is not what shipped and
the section must say so in its own words. Write:

- **Delivered (2026-08).** Seven packs — genres, decades, audio languages,
  countries, subtitle languages, studios, networks — as catalog rows an
  operator switches on by key. Each is ONE `builder: dynamic` definition whose
  params are a transcription of Kometa's own defaults file
  (`src/autoposter/collections/packs.py`), expanded server-side on every pass.
  Caps are opinions pinned in the row and stated in its description. No new
  config field, no new endpoint, no new picker shape: the whole picker change
  is one line, and the family's SHAPE is reported through the payload key the
  award years already used.
- **What it did not close, with each blocker named.** Franchises → new row 192.
  Regions and continents → 189. The year pack → 171's `current_year` grammar,
  with the divergent alternative refused in the row. `media_aspect` → 96/155.
  `content_based_on` → 161. `time_seasonal` → 160. The people packs → 83.
  Separators (row 49) are untouched by this phase and row 49 stays open.
- **Row 93** is not closed: its packs-content half is 7 of 18. Rows 102 and
  185 were closed by 10a-2 and stay closed.
- **Testable when shipped** becomes the C12 acceptance procedure below rather
  than a dev-time probe: this phase's dev-time verification is the offline
  expansion contract plus the engine's own oracle chain, which 10a already
  proved live.
- **The `catalog.py` split, third triage (C11):** deferred again. The file is
  ~1900 lines and this phase appended seven data rows to a table that is
  data-heavy by design, moving the bulk into `packs.py` rather than growing it;
  a split would be churn against the checksum, provenance and purity tests that
  pin the module, with no behavioural payoff. Revisit only when a phase must
  EDIT the catalog's structure rather than append rows.

- [ ] **Step 2: Update row 93**

Append to row 93's body: the catalog half shipped in 4c; 10b shipped seven of
the eighteen gated families as packs (naming them) and re-pointed the four that
row 102 had been standing in for; the remaining eleven each name their own
blocker in the table itself, so the row's outstanding work is enumerable
without reading this row. Row 93 stays OPEN.

- [ ] **Step 3: Close row 182, and say what happened to row 190**

Row 182 — mark **CLOSED — phase 10b** and say how both halves discharged: the
conjunction half by the engine (a dynamic family emits an `any:` base, so a
language key expanding to three locale variants is an OR and not the `all:`
AND that measured 0 against 442), and the vocabulary half by the two language
packs, which ship with the mixed vocabulary stated in their descriptions and
with whatever naming §3 found. Row 190 stays OPEN and gains one clause: what
10b's two language packs do about names (transcribed table, or Plex's own names
with the ISO table still unvendored), so a reader of 190 knows which.

- [ ] **Step 4: Write the PR body**

`.superpowers/sdd/p10b-pr-body.md`, following `p10a-2-pr-body.md`'s shape:
what shipped, the seven packs and their pinned opinions in a table, the three
seams that moved (the test helper, the shape line, the one picker line), the
four re-pointings and row 192, the divergences (country's value set, any
divergent title format, the language naming branch), what did NOT ship and why,
and — as its own section — **the operator acceptance procedure (C12)**:

1. Deploy. Confirm `collections.presets` is unchanged and no collection moved
   (the golden gate is the offline half of this).
2. Switch on `content_genres` alone, on the dev library, through the picker.
3. On the next pass, compare the family against the library's own genre
   listing: the COUNT first, then three spot titles rendered from the pack's
   format.
4. Un-tick it. With `delete_unconfigured` off — the default — the pass reports
   the orphans and deletes nothing; that report is the expected result.
5. Only if the deployment runs with `delete_unconfigured` on: observe the sweep
   refusing past `max_deletes` and reporting both numbers, rather than deleting
   a prefix of the family.

No URLs, no tokens, no library names that identify a host.

- [ ] **Step 5: The evidence sweep**

```bash
docker compose -p p10bt6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    up -d postgres
docker compose -p p10bt6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10bt6-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t6-full.log; echo EXIT=$? >> /app/.superpowers/run-t6-full.log'
docker wait p10bt6-full
tail -30 .superpowers/run-t6-full.log
docker compose -p p10bt6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10bt6 run --rm web npm test > .superpowers/run-t6-vitest.log 2>&1; echo EXIT=$?
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
git diff --stat origin/main -- src/autoposter/collections/builders/ \
    src/autoposter/collections/engine.py src/autoposter/collections/dynamic_types.py \
    src/autoposter/collections/dynamic_keys.py src/autoposter/collections/dynamic_titles.py \
    src/autoposter/collections/smart.py src/autoposter/collections/filters.py
git diff --stat origin/main -- src/autoposter/config/schema.py
docker compose -p p10bt6 down
```

The last three diffs must be EMPTY: constraint 6 (the golden gate), constraint
2 (the engine is untouched) and constraint 1 (no new config shape) are proven
by output, not by assertion. Paste all of it.

- [ ] **Step 6: Commit and open the PR**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): 10b re-scoped to what shipped, row 182 closed, 192 filed

Seven packs shipped and eleven gated rows remain, each naming its own blocker
rather than a phase that has ended. Row 182 closes -- the any: conjunction by
the engine, the vocabulary by the two language packs and their stated
remainder. Row 190 records which naming branch the packs took. Rows 93 and 49
stay open and say why. The catalog.py split is triaged a third time and
deferred, with the reasoning."
```

Open the PR with `.superpowers/sdd/p10b-pr-body.md` as the body. No AI
attribution anywhere in it.
