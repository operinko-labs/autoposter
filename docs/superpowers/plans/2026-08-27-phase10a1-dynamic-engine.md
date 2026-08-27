# Phase 10a-1: The Dynamic Collections Engine (part 1 of 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `builder: dynamic` — one Plex-native smart collection per distinct
value a library actually holds, enumerated from the library itself, titled and
keyed with Kometa's own `include`/`exclude`/`addons`/`custom_keys` and
`title_format` semantics, and written through the 9c reconciler in the
9b-proven query grammar.

**Architecture:** Six layers, each its own reviewer gate. (1) The two
FILTER_ATTRIBUTES vocabulary rows the phase needs (`decade`, `country`), proven
against the vendored Kometa `build_filter` oracle before anything uses them.
(2) The public enumeration seam over `listFilterChoices` (`LibraryTagResolver.choices`)
plus the transcribed per-type table, plus the live read-only probe that decides
whether `network` is enumerable at all. (3) The key machinery — include /
exclude / addons / custom_keys — as a pure module, gated by a new standalone
oracle transcribing Kometa's `meta.py` key derivation. (4) The title machinery —
`title_format`, `key_name_override`, `title_override`, `remove_prefix/suffix`,
the `other` bucket — same module discipline, same oracle. (5) The builder: params,
load-time refusals, the family label, the per-key emission through
`smart.reconcile_smart_collection`, the fan-out cap, and the engine-sweep
protection its collections need on day one. (6) The wrap: the
`docs/research/kometa-collections.md` sync-claim correction, the operator
documentation, the roadmap addendum, the evidence sweep.

**Tech Stack:** Python 3.14, pydantic v2, plexapi 4.18.2, SQLAlchemy 2 (async),
pytest, ruff, Docker Compose. **No new runtime dependency.**

---

## THE SPLIT DECISION — read this first

**Phase 10a is split into 10a-1 and 10a-2, per adjudication C8.** Honest
right-sizing of the whole phase yields ten tasks, well past C8's "~6" trigger:

| | Scope | Tasks |
| --- | --- | --- |
| **10a-1** (this plan, planned in full) | vocabulary rows + enumeration seam + type table + key machinery + title machinery + the `dynamic` builder + its wrap | **6** |
| **10a-2** (outlined at the end; planned in full when 10a-1 ships) | the label-scoped family sweep through our delete guards, delete-below-minimum, the CS port and its equivalence proof, the phase wrap (C7's eleven-preset re-pointing, row 185's verdict, row 102 closure) | **~4** |

Each half is its own branch and its own PR. 10a-1 ships a working, testable
engine an operator can point at a library today; it does **not** delete
anything, and the collections it creates are reported-but-never-swept by the
engine's own sweep until 10a-2 gives the family its own lifecycle. That
boundary is stated in the shipped code, in the refusal messages, and in the
sweep's own report line — never left implicit.

**One CONTRADICTION FLAG is open and it belongs to 10a-2, not to this plan.**
See the final section: the golden fixture pins the CS family's *plexapi call
shape*, not its outcome, so a C1-grammar port cannot leave
`tests/fixtures/collections/golden_port.json` byte-identical. 10a-1 does not
touch the CS path at all, so the gate stays byte-identical throughout this
plan; the adjudication is needed before 10a-2 is written.

---

## What this phase is NOT

Settled in `.superpowers/sdd/p10a-facts.md` (adjudications C1–C8) and **not
reopened by an implementer**:

- **No plexapi `filters=` grammar, no second write path** (C1). Every
  collection this builder creates goes through
  `smart.reconcile_smart_collection` with a `build_search_url` query string.
- **No CS port in 10a-1** (C2, C8). `cs_bucket` is untouched; the golden gate
  is byte-identical from the first commit to the last.
- **No TMDb-walk dynamic types** (C3): no `original_language`, no
  `origin_country`, no `edition`, no people types, no `tmdb_collection`,
  `trakt_*`, `number`, `custom` or music types. No show-decade.
- **No `test:` and no library-type `data:`** (C5). Both are load-time refusals
  naming the upstream reason.
- **No catalog work of any kind** (C7). No preset flips, no new catalog row, no
  default definition. `src/autoposter/collections/catalog.py` and
  `src/autoposter/collections/sources.py` are untouched in 10a-1, which is
  asserted by a `git diff --stat` check in Task 6 — this is what keeps the
  row-93 break site (`tests/test_collection_catalog.py:119-120`) unreached.
- **No deletes** (10a-2 owns the family sweep). No `sync:` key: it is refused
  by name with the reason and the phase that will lift it.

---

## Branch and cut point

- Branch name: **`feat/dynamic-collections`**.
- **Cut from `origin/main` after a fetch, verified with a CONTENT probe, never
  a sha probe:**

  ```bash
  git fetch origin
  git cat-file -e origin/main:src/autoposter/collections/smart.py
  git cat-file -e origin/main:src/autoposter/collections/builders/smart_filter.py
  git cat-file -e origin/main:tests/oracle/9b/kometa_build_filter.py
  git checkout -b feat/dynamic-collections origin/main
  ```

  All three probes must exit 0. If any fails, **STOP and report** — 9b and 9c
  are this plan's every seam, and a cut point without them is not this plan's
  starting tree. Record the resolved commit (`git rev-parse HEAD`) in the Task 1
  report.
- No stacking. If a PR touching `src/autoposter/collections/filters.py`,
  `src/autoposter/collections/engine.py` or
  `src/autoposter/collections/builders/plex_search.py` is open when this starts,
  say so in the Task 1 report and note the retarget instruction in the PR
  description (the 9a/9b/9c precedent), but still cut from `origin/main`.

## Execution

Executed via **superpowers:subagent-driven-development**: one fresh subagent per
task, two-stage review between tasks, this plan document **live-synced** through
every fix round (a fix that changes a signature, a message, a golden or a table
row edits the corresponding step here in the same commit, so a later task's
implementer reads the truth and not the original guess).

---

## Global Constraints

Every task's requirements implicitly include this section. These are project
law, carried verbatim from the phase brief and from the 9b/9c plans they were
established in.

**Secrets and URL hygiene.**

- **Operator URLs and tokens never appear in a log line, an error message, an
  action string, an `EventLog` payload or any committed file.** No test fixture,
  no report and no committed file carries the Plex server address, its token or
  an `X-Plex-Token` query parameter. Probe output is scrubbed at the point of
  PRINTING, not at the point of construction.
- **Transport errors are class-name-only.** Any exception raised out of a
  plexapi or `requests` call is caught and re-raised as this phase's own named
  exception carrying the exception's **class name and nothing else** — never
  `str(error)`, which can carry a tokenised URL.
  `src/autoposter/collections/builders/plex_search.py:436-463` is the shape this
  phase copies, and `LibraryTagResolver` is reused rather than re-written
  precisely so that wrap is not re-implemented.

**Refusals RETURN from `apply`; nothing raises through the engine's smart
dispatch.** `engine.py:341-344` deliberately does not wrap a smart builder's
`apply` — anything that escapes is treated as a Plex WRITE failing and belongs
to the caller's per-library rollback. So every operator-configuration failure
this builder can meet (a library type it cannot serve, a dead
`listFilterChoices`, a tag value the library does not have, a filter matching
nothing, a shape conflict, an over-cap fan-out, a duplicate family title) is
caught inside `DynamicBuilder.apply` and RETURNED as an action string. What is
not caught — a failing label write, a failing summary PUT, a genuine `TypeError`
in our own code — still reaches the rollback, unchanged.

**Drift is the stored hash; `Collection.content` is never read.** Drift
detection is `definition_hash` over the DESIRED state in `managed_collections`
(`smart.smart_definition_hash`). Nothing in this phase reads the `content`
attribute Plex echoes back on a smart collection —
`test_no_reconciler_reads_a_collections_content_echo` greps the reconciler
modules for exactly that read, and any new module that touches a collection
object is written the same way.

**Golden gate byte-identical.** `tests/test_builder_port_golden.py` reproduces
`tests/fixtures/collections/golden_port.json` byte for byte at every commit of
this plan. **10a-1 changes nothing about the CS create path**, so a diff in that
file is a defect, full stop; never re-capture it (re-capture is only ever legal
on the pre-port commit). The CS port is the one place a change would even be
conceivable and it is 10a-2's, governed by C2 and by the CONTRADICTION FLAG at
the end of this document.

**Testing is container-only.**

- A bare host `pytest` fails at collection by design (`tests/conftest.py`
  requires `AUTOPOSTER_TEST_DATABASE_URL` with no fallback). That is intended.
- Each task runs on its **own isolated compose project**, named `p10at<N>`
  (`p10at1` … `p10at6`), so a sibling agent's run on the shared `-p autoposter`
  database is never contended:

  ```bash
  docker compose -p p10at1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
      run --rm test sh -c 'timeout -s KILL 1800 pytest -q tests/test_collection_filters.py; echo EXIT=$?'
  ```

- **`timeout -s KILL N` inside `sh -c`, never a bare `timeout` as the container
  command.** BusyBox `timeout` execs in the parent, pytest becomes PID 1, and
  PID 1 ignores default SIGTERM — that produced 10-hour zombie containers
  squatting on the shared postgres.
- **No `--rm` for a long run**, and the repeated-foreground-`docker wait` recipe
  for anything that outlives a single tool call's cap. Start detached with a
  name, then block in the FOREGROUND on `docker wait`, repeatedly, until it
  returns:

  ```bash
  docker compose -p p10at1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
      run -d --name p10at1-full test \
      sh -c 'timeout -s KILL 1800 pytest -q 2>&1 | tee /app/.superpowers/run-t1-full.log; echo EXIT=$?'
  docker wait p10at1-full        # foreground, repeat until it prints an exit code
  docker logs p10at1-full | tail -40
  docker rm p10at1-full
  ```

  After starting ANY container run, the agent's next action MUST be one of
  (a) foreground `docker wait <name>`, (b) reading the finished container's
  logs, or (c) a different piece of work followed by (a)/(b). **Ending a turn
  while a container runs and expecting to be resumed is the failure**, whatever
  it is called — there is no notification mechanism.
- **Output is tee-captured into `/app/.superpowers/`** (`run-t<N>-<what>.log`),
  which is the repo's gitignored scratch mount, so a reviewer can read the real
  output rather than a summary of it.
- Teardown after each task: `docker compose -p p10at<N> down`. **Never
  `down -v`** — that destroys the dev database volume. Never `docker compose
  down` on the shared `-p autoposter` project. If zombie `autoposter*run*` or
  `p10at*` containers are found holding the DB, remove with `docker rm -f <id>`.

**Every task ends with golden + catalog + ruff, all green.**

```bash
docker compose -p p10at<N> -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 1800 pytest -q tests/test_builder_port_golden.py tests/test_collection_catalog.py; echo EXIT=$?'
docker compose -p p10at<N> -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
```

`ruff check src tests` — the lint select set is `["E4", "E7", "E9", "F"]`
(`pyproject.toml:73`), pinned deliberately; do not broaden it.

**The full suite runs where a task's scope warrants it.** T1–T5 each touch
shipped `src/` files, so each ends with the **full suite**. T6 is
evidence-and-docs only — its diff touches zero `src/` and zero `tests/` files,
so it gates on `ruff check src tests` plus its own evidence checks and does NOT
run the full suite. This is a rule about the *diff*, not the task number: the
moment T6 touches one shipped file, the full suite is back.

**Mutation proofs: backup + `cmp`, never `git checkout --`.** Every guard test
must be proven falsifiable: copy the file aside, break the thing the test
guards, show it RED, restore **from the copy** and verify with `cmp`, show it
GREEN, and paste **both real outputs**. Never present output from one run as
evidence for another. **Never restore a file with `git checkout -- <file>`.**

**Oracle discipline.**

- Kometa is pinned at **v2.4.8**; plexapi at the installed **4.18.2**. Every
  transcribed function carries `file:line` provenance in a comment —
  `modules/meta.py:1217-1228`, not "Kometa's addon merge".
- Oracle drivers import **NOTHING** from `src/`. Stdlib only.
- Goldens are pinned **as data** in the test file. **Never edit a golden to make
  a test pass: if ours differs, ours is wrong.**
- The new 10a driver is transcribed from the line ranges quoted verbatim in
  `.superpowers/sdd/p10a-upstream-dynamic.md`. **If the executing environment
  can reach the network, the implementer MUST fetch
  `https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/modules/meta.py`
  and verify each transcribed block against it line by line**, recording the
  verification (or its impossibility, explicitly) in the task report. A
  transcription nobody checked against the source is a self-agreement, which is
  the failure an oracle exists to escape.

**Purity.** Nothing in config load, in expansion, or in any parse/derive/URL
function does I/O. `derive_keys`, `family_titles`, `render_title`,
`parse_filters` and `build_search_url` are pure and take their data as
arguments. The one argued exception is unchanged from 9b: enumeration makes Plex
calls, memoised per `(library, libtype-scope, field)` in the pass's `run_cache`,
failures included.

**No timestamp-flake assertions (roadmap row 119).** Nothing in this phase
asserts on `datetime.now()`, elapsed time, or a database clock. If a run hits a
*pre-existing* instance of the pattern, the task report records it for T6's
row-119 tally and reruns; it does not "fix" an unrelated test.

**Async-SQLAlchemy capture-ids-before-expiry rule — LIVE in this phase.** T5
writes `managed_collections` rows through `reconcile_smart_collection`. After a
commit an ORM attribute is expired and touching it issues a lazy load that
raises under async. **Capture `record.id`, `record.title`,
`record.definition_hash` into locals BEFORE the commit that expires them**, and
re-`select()` rather than reusing a stale instance across a commit boundary.
`reconcile_smart_collection` itself only `flush()`es, never commits.

**Conventions.**

- Conventional commits. `git commit --no-gpg-sign`. **No `Co-Authored-By` and no
  AI attribution** in any commit message or PR description.
- Stage files **by name**. Never `git add -A`, `git add .`, `git commit -a`.
- `.gitattributes` forces LF. If editing with a Python script use `write_bytes`
  or `newline="\n"`; `write_text` emits CRLF on Windows and turns a small edit
  into a whole-file diff.
- Use PowerShell for docker commands; Git Bash mangles container paths.
- **Paths.** Every file reference in a dispatch prompt, task brief, task report
  or commit message uses the FULL repo-relative path (`src/autoposter/...`,
  `tests/...`, `docs/...`, `.superpowers/...`).

**Pre-authorized known-pattern fixes.** These may be applied without a new
adjudication, and each MUST be reported and MUST be synced into this plan
document in the same commit:

- capture-ids-before-expiry (the async-SQLAlchemy rule above);
- replacing a wall-clock assertion with a pinned moment (row 119);
- widening a refusal message's wording without changing what it refuses;
- adding a `file:line` provenance comment to a transcribed branch.

---

## File Structure

**Created**

| Path | Responsibility |
| --- | --- |
| `src/autoposter/collections/dynamic_types.py` | The transcribed per-type table: which Plex attribute a dynamic type enumerates and queries, which library types it serves, whether its key is the choice's `key` or its `title`, and its upstream default `title_format`, `sort_by` and `limit`. Data plus provenance; no logic. |
| `src/autoposter/collections/dynamic_keys.py` | Pure: enumerated `(key, value)` pairs + `include`/`exclude`/`addons`/`custom_keys` → the family's keys, each key's query values, and the leftover (`other`) keys. Kometa's `meta.py` order of operations, exactly. |
| `src/autoposter/collections/dynamic_titles.py` | Pure: a derived key + the title options → its `key_name` and its collection title, plus the `other` bucket's, plus the duplicate-title refusal. |
| `src/autoposter/collections/builders/dynamic.py` | The `dynamic` smart builder: params, load-time refusals, the family label, enumeration, per-key URL, per-key `reconcile_smart_collection`, the fan-out cap. |
| `tests/oracle/10a/kometa_dynamic.py` | Kometa's `dynamic_collections` key and title derivation (`modules/meta.py`, `modules/util.py`), transcribed standalone. Imports stdlib only. |
| `tests/test_collection_dynamic_oracle.py` | The pinned oracle cases for keys (T3) and titles (T4), plus the driver-drift guard. |
| `tests/test_collection_dynamic_keys.py` | `derive_keys`' own unit surface. |
| `tests/test_collection_dynamic_titles.py` | `family_titles`' own unit surface, including the refusals that deliberately diverge from upstream's warn-and-skip. |
| `tests/test_collection_dynamic_types.py` | The type table's checksum: counts per column, every row's attribute resolvable, every row's `title_format` renderable. |
| `tests/test_builder_dynamic.py` | The builder end-to-end against fakes: params refusals, emitted URLs, the family label, the fan-out cap, the engine sweep's family report. |
| `docs/research/plex-dynamic-probe/README.md` | T2's read-only live probe: what `listFilterChoices` answers per dynamic type on the production libraries, scrubbed. |

**Modified**

| Path | Change |
| --- | --- |
| `src/autoposter/collections/filters.py` | Two `FilterAttribute` rows (`decade`, `country`), one `SEARCH_OPERATORS_EXCLUDED` entry, the table header's column-total comment. |
| `tests/test_collection_filters.py` | The table's checksums: row list, per-column totals, kind splits, the searchable/filterable arithmetic, plus two new rows' own tests. |
| `tests/oracle/9b/kometa_build_filter.py` | Two configs appended to `CONFIGS`; `CHOICES` gains one country entry. No branch of `build_filter` changes. |
| `tests/test_collection_search_oracle.py` | The same two configs and their pinned Kometa strings; `CHOICES` copy kept equal. |
| `src/autoposter/collections/builders/plex_search.py` | `LibraryTagResolver` gains the public `choices(attribute)` seam over the already-memoised `_raw_choices`. |
| `tests/test_builder_plex_search.py` | The seam's tests: shape, per-pass memoisation, failure memoisation, show-libtype rescoping. |
| `src/autoposter/collections/builders/__init__.py` | Imports and registers `DynamicBuilder`. |
| `src/autoposter/collections/engine.py` | `_sweep` recognises a dynamic family's label and REPORTS its members rather than considering them for deletion (10a-2 gives the family its own sweep). |
| `tests/test_builder_engine.py` | That sweep behaviour. |
| `config/autoposter.example.yaml` | The `dynamic` builder documented in the `definitions:` example block. |
| `docs/research/kometa-collections.md` | T6: the false `sync: true` → `sync_mode: sync` claim corrected, cited (C4). |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | T6: row 102's addendum (the 10a-1/10a-2 split and what shipped), rows 171 and 174 marked half-closed by the two table rows. |

---

### Task 1: The two vocabulary rows — `decade` and `country`

**Files:**

- Modify: `src/autoposter/collections/filters.py` (append two rows to `FILTER_ATTRIBUTES` at `:819`, one entry to `SEARCH_OPERATORS_EXCLUDED` at `:271-274`, the header comment at `:507-516`)
- Modify: `tests/test_collection_filters.py` (`:79-99`, `:115-161`, `:164-172`, `:234-254`, `:257-275`)
- Modify: `tests/oracle/9b/kometa_build_filter.py` (`CHOICES` at `:45-56`, `CONFIGS` at `:977`)
- Modify: `tests/test_collection_search_oracle.py` (`CHOICES` at `:98-109`, `CONFIGS` at `:119-170`, `KOMETA` at `:178-194`)

**Interfaces:**

- Consumes: `FilterAttribute` (`src/autoposter/collections/filters.py:419-500`), the vendored driver's `build_filter(method, plex_filter, sort_type, default_sort=None) -> (type_key, url)`.
- Produces: `BY_NAME["decade"]` and `BY_NAME["country"]` — searchable rows every later task depends on. `BY_NAME["decade"].field_for("movie") == "decade"`;
  `BY_NAME["country"].field_for("movie") == "country"`;
  `BY_NAME["country"].field_for("show") == "show.country"`.

**Why this task is first.** Everything downstream builds a query out of these
names. `decade` is the type the roadmap's own acceptance criterion for 10a names
("a `decade` dynamic config creates the expected set on the dev library") and it
is not in the shipped vocabulary at all; `country` is the same, one row along.
Both are cheap table rows per roadmap rows 171 and 174 — and both are proven
against Kometa's own `build_filter` before any engine code exists, because a row
whose operator set or whose show-rescoping is wrong produces a full, plausible,
wrong collection rather than an error.

- [ ] **Step 1: Write the failing table tests**

In `tests/test_collection_filters.py`, edit the four checksum tests and append
two new ones. The row list at `:79-99` gains two entries at the END (table
order, appended not interleaved, exactly as 9b appended its four):

```python
        "plays",
        "last_played",
        "unplayed",
        "progress",
        "decade",
        "country",
    ]
```

`test_the_column_totals_are_the_transcriptions_checksum` (`:115-161`):

```python
    assert {k: len(v) for k, v in by_type.items()} == {
        "tag": 9,
        "str": 1,
        "int": 3,
        "float": 2,
        "date": 3,
        "duration": 1,
        "bool": 2,
    }
```

and, in the same test, the two source-tier lists that move:

```python
    # 10a appended ``country`` for the reason 9b appended ``plays``: Kometa
    # filters on it and 9a's probe never asked whether the section listing
    # carries ``<Country>``, so there is no verdict to cite.
    assert by_source["unprobed"] == ["plays", "last_played", "country"]
    # ``decade`` joins the search-only tier: row 96's own 29-name list names it
    # first, and Kometa has no ``decade`` FILTER at all.
    assert by_source["search-only"] == ["unplayed", "progress", "decade"]
```

`test_item_kinds_are_movie_show_or_both` (`:164-172`):

```python
    assert movie_only == [
        "audio_language", "decade", "progress", "resolution",
        "subtitle_language", "unplayed",
    ]
    assert show_only == ["network"]
    assert len([r for r in FILTER_ATTRIBUTES if r.kinds == ("movie", "show")]) == 14
```

`test_the_search_kinds_column_is_its_own_and_differs_from_kinds` (`:248-250`):

```python
    assert Counter(row.search_kinds for row in FILTER_ATTRIBUTES) == {
        ("movie", "show"): 16, ("movie",): 4, ("show",): 1,
    }
```

`test_every_row_is_searchable_and_seventeen_are_filterable` (`:257-275`) is
renamed and re-numbered — the name carries a count, so leaving it would be a
lie in the test list:

```python
def test_every_row_is_searchable_and_eighteen_are_filterable():
    """The set arithmetic, pinned so it cannot rot silently.

    Kometa's search vocabulary is 55 non-music attributes and its filter
    vocabulary is 70 names; this table covers 21 of the first and 18 of the
    second. The module docstring carries the full derivation. Phase 10a added
    ``decade`` (search-only, so searchable and not filterable) and ``country``
    (in Kometa's 26-name overlap, so both).
    """
    from autoposter.collections.filters import (
        FILTERABLE_ATTRIBUTES,
        FILTER_ATTRIBUTES,
        SEARCHABLE_ATTRIBUTES,
    )

    assert all(row.searchable for row in FILTER_ATTRIBUTES)
    assert len(SEARCHABLE_ATTRIBUTES) == 21
    assert len(FILTERABLE_ATTRIBUTES) == 18
    assert set(SEARCHABLE_ATTRIBUTES) - set(FILTERABLE_ATTRIBUTES) == {
        "unplayed", "progress", "decade",
    }
```

Append the two row-specific tests at the end of the search-half section (after
`test_every_row_searchable_on_show_carries_a_show_search_field`, `:318-326`):

```python
def test_decades_search_operator_set_is_the_bare_form_alone():
    """``decade`` is in Kometa's ``no_not_mods`` (plex.py:593), and that list
    does two things at once: it drops ``.not`` from the tag-modifier half of
    ``searches`` and it drops the attribute from the number-modifier half
    ENTIRELY (plex.py:597-599). So ``decade: 1980`` is the only decade search
    Kometa will build -- not ``decade.gte``, which reads like an int operator
    and is not one. ``resolution`` is the sibling row that only loses ``.not``,
    which is why the two subtractions differ."""
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["decade"].search_operators == ("eq",)
    assert BY_NAME["resolution"].search_operators == ("eq",)
    assert BY_NAME["year"].search_operators == ("eq", "not", "gt", "gte", "lt", "lte")


def test_country_is_rescoped_for_a_show_library_and_decade_refuses_one():
    """The two new rows' libtype behaviour, which is where a wrong
    transcription would silently build the wrong query rather than fail."""
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["country"].field_for("movie") == "country"
    assert BY_NAME["country"].field_for("show") == "show.country"
    assert BY_NAME["decade"].field_for("movie") == "decade"
    with pytest.raises(ValueError, match="not searchable on a show library"):
        BY_NAME["decade"].field_for("show")
```

- [ ] **Step 2: Run them and watch them fail**

```bash
docker compose -p p10at1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_filters.py 2>&1 | tee /app/.superpowers/run-t1-red1.log; echo EXIT=$?'
```

Expected: FAIL. The checksum tests fail on the counts; the two new tests fail
with `KeyError: 'decade'` / `KeyError: 'country'`.

- [ ] **Step 3: Add the two rows and the operator subtraction**

In `src/autoposter/collections/filters.py`, append inside `FILTER_ATTRIBUTES`,
immediately before the closing `)` at `:820`:

```python
    # --- rows 10a added ------------------------------------------------------
    #
    # Both are named by roadmap row 102's own decomposition list, and neither
    # is a new mechanism: ``decade`` is roadmap row 171's single table row (the
    # ``current_year``/``current_year-N`` value grammar that row also carries
    # is NOT built here and stays with it), and ``country`` is roadmap row 174
    # in full. Phase 10a needs them because its acceptance criterion names
    # ``decade`` and because upstream's dynamic type table carries both.
    FilterAttribute(
        "decade", "int", ("movie",), "search-only",
        "Plex's own ``decade`` filter. MOVIE-ONLY -- it is in "
        "``movie_only_searches`` (plex.py:437), which is exactly why Kometa's "
        "show-decade dynamic type falls back to a full library scan "
        "(meta.py:881-898) and why this phase does not ship one. A YEAR "
        "attribute upstream (``year_attributes``, plex.py:591), so its values "
        "are sent as PLAIN NUMBERS -- ``decade=1980`` -- and are never resolved "
        "through the library's tag vocabulary, exactly like ``year``; the "
        "dynamic engine feeds it the enumerated ``choice.key`` (``1980``) and "
        "titles the collection from ``choice.title`` (``1980s``). Its operator "
        "set is NOT the ``int`` set: ``decade`` is in ``no_not_mods`` "
        "(plex.py:593), which subtracts ``.not`` from its tag modifiers AND "
        "removes it from the number-modifier comprehension entirely "
        "(plex.py:597-599), so the bare form is the only decade search Kometa "
        "builds -- ``SEARCH_OPERATORS_EXCLUDED`` subtracts the other five. "
        "``search-only``: Kometa has no ``decade`` FILTER at all (row 96's "
        "29-name search-only list names it first), so a ``filters:`` block "
        "refuses it by naming the block it does belong to.",
        search_field="decade", show_search_field=None,
        search_kinds=("movie",), filterable=False,
    ),
    FilterAttribute(
        "country", "tag", _BOTH, "unprobed",
        "Plex's ``<Country>`` child element -- a tag attribute whose values "
        "resolve through the same ``listFilterChoices`` path every shipped tag "
        "attribute uses, which is roadmap row 174 in full. Re-scoped to "
        "``show.country`` on a show library by ``show_translation`` "
        "(plex.py:168-193). Source tier ``unprobed`` rather than "
        "``tier2-deferred``, for the reason ``plays`` and ``last_played`` carry "
        "that tier: 9a's probe never asked whether ``<Country>`` reaches the "
        "section listing, so there is no verdict to cite and the "
        "tier2-deferred copy -- which cites one -- would be a claim nobody "
        "checked. It IS in both of Kometa's vocabularies (row 96's arithmetic "
        "puts it in the 26-name overlap: it appears in neither the 44 "
        "filter-only names nor the 29 search-only ones), so ``filterable`` is "
        "True and a ``filters:`` block refuses it by naming its tier rather "
        "than by denying the attribute exists. Note that phase 10a's dynamic "
        "``country`` TYPE is movie-only -- that is upstream's dynamic type "
        "table (meta.py:18-19), not this row: the SEARCH answers for both "
        "library types.",
        search_field="country", show_search_field="show.country",
        search_kinds=_BOTH, filterable=True,
    ),
```

Extend `SEARCH_OPERATORS_EXCLUDED` (`:271-274`):

```python
SEARCH_OPERATORS_EXCLUDED: dict[str, tuple[str, ...]] = {
    "resolution": ("not",),
    "plays": ("eq", "not"),
    # ``decade`` is in the same ``no_not_mods`` list as ``resolution``
    # (plex.py:593) and loses MORE than ``resolution`` does, because it is a
    # ``year_attribute`` and therefore reached the number modifiers too: that
    # comprehension is guarded by the same list (plex.py:599), so all four
    # ranges go with the ``.not``. The bare form is what is left.
    "decade": ("not", "gt", "gte", "lt", "lte"),
}
```

Update the table header comment at `:507-516` — the first sentence and the
column totals:

```python
# Twenty-one rows: 9a's fifteen in the order the roadmap names them
# (roadmap.md:538-551), then 9b's four and 10a's two appended rather than
# interleaved so the first fifteen still read against the roadmap line they came
# from. Column totals are asserted in tests/test_collection_filters.py as the
# transcription's checksum: 9 tag / 1 str / 3 int / 2 float / 3 date /
# 1 duration / 2 bool; 9 listing / 6 tier2-deferred (Task 2's probe moved the
# seven ``probe`` rows: resolution in, the other six out) / 3 unprobed /
# 3 search-only; 14 both-kinds / 6 movie-only / 1 show-only for ``kinds``, and
# 16 / 4 / 1 for ``search_kinds``, which is a different split and that is the
# point of the second column.
```

- [ ] **Step 4: Run the table tests green**

```bash
docker compose -p p10at1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_filters.py tests/test_collection_filter_values.py 2>&1 | tee /app/.superpowers/run-t1-green1.log; echo EXIT=$?'
```

Expected: PASS. `tests/test_collection_filter_values.py` is included
deliberately and must be UNCHANGED: `SHIPPED_ATTRIBUTES` and
`DEFERRED_ATTRIBUTES` are derived from the `listing` and `tier2-deferred` tiers,
and neither new row is in either, so neither gains a client-side accessor.

- [ ] **Step 5: Add the two oracle configs to the vendored driver**

In `tests/oracle/9b/kometa_build_filter.py`, `CHOICES` (`:45-56`) gains one
entry, keeping the existing ten unchanged:

```python
    ("collection", "The Fast and the Furious Collection"): ("77",),
    ("country", "France"): ("36",),
}
```

and `CONFIGS` (`:977`) gains two entries at the END:

```python
    # 16: the two rows phase 10a added, on a movie library. ``decade`` is a
    # year_attribute rendered as a plain number with no tag lookup at all
    # (builder.py:4242-4248 takes it through the same multi-term branch as the
    # tag rows, but validate_attribute returned ints); ``country`` is an
    # ordinary tag resolved to its key.
    ("movie", {"all": {"decade": 1980, "country": "France"}}),
    # 17: ``country`` on a SHOW library, which is the only thing about either
    # row that a movie config cannot reach -- show_translation re-scopes it to
    # ``show.country`` (plex.py:164) and a row that forgot the rescoping would
    # build a query Plex answers with the wrong set rather than with an error.
    ("show", {"all": {"country": "France"}}),
```

- [ ] **Step 6: Run the driver and READ its answer**

```bash
docker compose -p p10at1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm --no-deps test sh -c 'cd /app/tests/oracle/9b && python kometa_build_filter.py 2>&1 | tee /app/.superpowers/run-t1-oracle.log; echo EXIT=$?'
```

It prints one line per config, `N <url>`. **Copy lines 16 and 17 verbatim** —
they are the goldens. The shape to expect (a mismatch here is a finding to
report, not something to paper over):

```
16 ?type=1&sort=titleSort&decade=1980&and=1&country=36
17 ?type=2&sort=titleSort&show.country=36
```

- [ ] **Step 7: Pin the goldens and the configs in the oracle test**

In `tests/test_collection_search_oracle.py`: add the same `("country",
"France"): ("36",)` entry to the `CHOICES` copy at `:98-109` (the two copies are
compared literally by
`test_the_oracles_vocabulary_fixture_matches_this_files_copy`), append the two
configs to `CONFIGS` in the same order as the driver's, and add the two printed
strings to `KOMETA`:

```python
    ("16-decade-and-country", "movie", {"all": {"decade": 1980, "country": "France"}}),
    ("17-country-on-a-show", "show", {"all": {"country": "France"}}),
]
```

```python
    "16-decade-and-country": "<line 16, verbatim from Step 6>",
    "17-country-on-a-show": "<line 17, verbatim from Step 6>",
}
```

Add the file's own coverage note to the module docstring, under the existing
"fifteenth config" section:

```
## The sixteenth and seventeenth configs

Phase 10a added two rows to the table -- ``decade`` (roadmap row 171) and
``country`` (row 174) -- and
``test_the_configs_cover_every_shipped_value_type`` only asserts that every
value TYPE is exercised, which both rows' types already were. So neither row
would have been reached by any golden: config 16 pins ``decade``'s plain-number
render beside a tag on the same movie query, and config 17 pins ``country``'s
show-library rescoping, which is the one thing about either row that a movie
config cannot reach. Both goldens came from the same driver in the same way.
```

- [ ] **Step 8: Run the oracle green**

```bash
docker compose -p p10at1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_search_oracle.py 2>&1 | tee /app/.superpowers/run-t1-green2.log; echo EXIT=$?'
```

Expected: PASS, 17 parametrised cases plus the four structural tests.

- [ ] **Step 9: Mutation proof**

```bash
cp src/autoposter/collections/filters.py /tmp/filters.py.bak
```

Mutation A — change the `country` row's `show_search_field` from
`"show.country"` to `"country"`. Run
`pytest -q tests/test_collection_search_oracle.py tests/test_collection_filters.py`.
Expected RED: `17-country-on-a-show` and
`test_country_is_rescoped_for_a_show_library_and_decade_refuses_one`.

Mutation B — restore, then remove `"decade"` from `SEARCH_OPERATORS_EXCLUDED`.
Run the same. Expected RED:
`test_decades_search_operator_set_is_the_bare_form_alone`.

Restore and verify:

```bash
cp /tmp/filters.py.bak src/autoposter/collections/filters.py
cmp /tmp/filters.py.bak src/autoposter/collections/filters.py && echo RESTORED
```

Paste both RED outputs and the restored GREEN run into the task report.

- [ ] **Step 10: Full suite, golden, ruff**

```bash
docker compose -p p10at1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10at1-full test sh -c 'timeout -s KILL 1800 pytest -q 2>&1 | tee /app/.superpowers/run-t1-full.log; echo EXIT=$?'
docker wait p10at1-full
docker logs p10at1-full | tail -40
docker rm p10at1-full
docker compose -p p10at1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10at1 down
```

Expected: full suite green, `golden_port.json` untouched (`git status` shows no
change under `tests/fixtures/`), ruff clean.

- [ ] **Step 11: Commit**

```bash
git add src/autoposter/collections/filters.py tests/test_collection_filters.py \
        tests/oracle/9b/kometa_build_filter.py tests/test_collection_search_oracle.py
git commit --no-gpg-sign -m "feat(filters): decade and country search rows, oracle-proven

Roadmap rows 171 and 174, the two vocabulary rows phase 10a's dynamic types
need. decade is search-only and movie-only with the bare form as its whole
operator set (Kometa's no_not_mods); country is a tag in both vocabularies,
rescoped to show.country on a show library. Two new configs in the vendored
build_filter oracle pin both against Kometa v2.4.8."
```

---

### Task 2: The enumeration seam, the type table, and the live probe

**Files:**

- Modify: `src/autoposter/collections/builders/plex_search.py` (`LibraryTagResolver`, after `__call__` at `:413`)
- Modify: `tests/test_builder_plex_search.py` (append the seam's tests)
- Create: `src/autoposter/collections/dynamic_types.py`
- Create: `tests/test_collection_dynamic_types.py`
- Create: `docs/research/plex-dynamic-probe/README.md`

**Interfaces:**

- Consumes: `BY_NAME` and `FilterAttribute.field_for` (`src/autoposter/collections/filters.py`), `LibraryTagResolver._raw_choices` (already memoised per `(library, scope, field)` per pass, failures included).
- Produces:
  - `LibraryTagResolver.choices(attribute: str) -> tuple[tuple[str, str], ...]` — every `(key, title)` the library reports for that attribute, in Plex's own order, both members `str`.
  - `dynamic_types.DynamicType` (frozen dataclass: `name`, `attribute`, `search_key`, `kinds`, `key_from`, `title_format`, `sort_by`, `limit`, `note`) and `dynamic_types.DYNAMIC_TYPES: dict[str, DynamicType]`.

**Why the seam is a method on the existing resolver.** `_raw_choices` already
IS the enumeration primitive — one `listFilterChoices(field=name, libtype=scope)`
per `(library, scope, field)` per pass, with both failure classes wrapped
class-name-only and memoised. Nothing public exposes it; `__call__` only answers
"which key is this written word". A second implementation would be a second
cache, a second failure policy and a second chance to leak a tokenised URL. So
the seam is four lines on the class that already owns all three.

- [ ] **Step 1: Write the failing seam tests**

Append to `tests/test_builder_plex_search.py`:

```python
# --- the public enumeration seam (phase 10a) ---------------------------------


def test_choices_hands_back_every_key_and_title_the_library_reports():
    """The seam phase 10a's dynamic engine enumerates through. ``__call__``
    answers "which key is this written word"; this answers "what does this
    library HAVE", which is the question one-collection-per-value asks."""
    section = FakeSection()
    ctx = _ctx()
    resolver = LibraryTagResolver(ctx, section, "movie")

    assert resolver.choices("genre") == (("1138", "Horror"), ("9", "Drama"))


def test_choices_and_call_share_one_round_trip_per_pass():
    """The whole reason the seam lives on this class: a pass that enumerates a
    family AND resolves a written value for some other definition pays for one
    ``listFilterChoices`` between them, because both go through the same
    memoised ``_raw_choices``."""
    section = FakeSection()
    ctx = _ctx()
    resolver = LibraryTagResolver(ctx, section, "movie")

    resolver.choices("genre")
    resolver.choices("genre")
    assert resolver("genre", "Horror") == ("1138",)
    assert section.choice_calls == [("genre", "movie")]


def test_choices_asks_a_show_library_at_the_scope_the_row_names():
    """``field_for`` is what decides the libtype scope, so the three media
    attributes are enumerated at the EPISODE libtype on a show library --
    which is Kometa's own ``get_tags(f"episode.{field}")`` (plex.py:920-921)
    and is not a special case here, just the dotted field being split."""
    section = FakeSection()
    resolver = LibraryTagResolver(_ctx(), section, "show")

    resolver.choices("audio_language")
    resolver.choices("genre")
    assert section.choice_calls == [
        ("audioLanguage", "episode"), ("genre", "show"),
    ]


def test_choices_memoises_the_failure_like_every_other_lookup():
    """A dead filter is memoised as a failure, so a family of forty keys does
    not re-ask forty times -- ``BuilderContext.run_cache``'s own docstring
    requires it, and the seam gets it for free by going through
    ``_raw_choices``."""
    section = FakeSection()
    section.raise_on_choices = NotFound("no such filter")
    resolver = LibraryTagResolver(_ctx(), section, "movie")

    with pytest.raises(PlexSearchUnavailable) as first:
        resolver.choices("genre")
    with pytest.raises(PlexSearchUnavailable):
        resolver.choices("genre")
    assert section.choice_calls == [("genre", "movie")]
    assert "NotFound" in str(first.value)
    assert "no such filter" not in str(first.value)
```

If `FakeSection` in that file has no `raise_on_choices` hook, add it to the
existing fake rather than writing a second one:

```python
    def listFilterChoices(self, field, libtype=None):
        self.choice_calls.append((field, libtype))
        if getattr(self, "raise_on_choices", None) is not None:
            raise self.raise_on_choices
        return [FakeChoice("Horror", "1138"), FakeChoice("Drama", "9")]
```

- [ ] **Step 2: Run them and watch them fail**

```bash
docker compose -p p10at2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_plex_search.py 2>&1 | tee /app/.superpowers/run-t2-red1.log; echo EXIT=$?'
```

Expected: FAIL — `AttributeError: 'LibraryTagResolver' object has no attribute 'choices'`.

- [ ] **Step 3: Add the seam**

In `src/autoposter/collections/builders/plex_search.py`, immediately after
`__call__` (`:402-413`):

```python
    def choices(self, attribute: str, /) -> tuple[tuple[str, str], ...]:
        """Every ``(key, title)`` this library reports for ``attribute``.

        The enumeration primitive, public since 10a. ``__call__`` above answers
        "which key does Plex know this written word by"; this answers "what does
        this library HAVE", which is the question one-collection-per-value asks
        and the only question ``_raw_choices`` was already able to answer
        without a second round trip.

        It is a method here rather than a function elsewhere because
        ``_raw_choices`` owns three things a second implementation would have to
        duplicate and could get wrong: the one-call-per
        ``(library, libtype-scope, field)`` memo, the narrow
        ``NotFound``/``BadRequest`` split against the blanket
        ``PlexApiException``/``RequestException`` one, and the class-name-only
        wrap that keeps a tokenised URL out of the message. Kometa's own
        enumeration is the same call through the same table
        (``get_tags``, modules/plex.py:1346-1364).

        Both members are ``str``: a caller comparing an operator's written
        ``include:`` entry against these must compare like with like, and Plex
        answers some keys as integers.
        """
        row = BY_NAME[attribute]
        field = row.field_for(self._libtype)
        scope, _, name = field.rpartition(".")
        scope = scope or self._libtype
        return tuple(
            (str(choice.key), str(choice.title))
            for choice in self._raw_choices(attribute, scope, name)
        )
```

Add `choices` to the class docstring's summary line ("Public (and exported)
since 9c … and since 10a it also exposes the raw vocabulary, which is what the
dynamic engine enumerates").

- [ ] **Step 4: Run the seam tests green**

```bash
docker compose -p p10at2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_plex_search.py tests/test_builder_smart_filter.py 2>&1 | tee /app/.superpowers/run-t2-green1.log; echo EXIT=$?'
```

Expected: PASS.

- [ ] **Step 5: Run the live read-only probe**

This is C3's mandated verification: 9b proved `show.network` answers as a SEARCH
field; that the CHOICES listing answers for it is the same family and is
**unproven**. The probe also captures how many collections each IN type would
create on the real libraries, which is the number the fan-out cap in Task 5 is
chosen against.

Write `p10a_probe.py` at the repo root (deleted in Step 8 — it is reproduced
verbatim in the README, which is the artefact that ships):

```python
"""Phase 10a Task 2 -- the read-only live probe.

READ-ONLY. The ONLY plexapi call this script makes is

    LibrarySection.listFilterChoices  (GET /library/sections/{k}/{field})

Nothing else. No ``edit(``, no ``addLabel``, no ``removeLabel``, no ``upload``,
no ``delete``, no ``refresh``.

Every line printed goes through ``scrub`` first, so neither the server address
nor the token can reach a recorded file. The script contains no literal URL and
no literal token; both come from the environment for the duration of the run.
"""
import os
from urllib.parse import urlsplit

from plexapi.server import PlexServer

# (our attribute name, movie field, show field) -- the movie/show columns are
# ``FilterAttribute.field_for``'s, spelled out here so the probe depends on the
# table only by value.
FIELDS = [
    ("year", "year", "show.year"),
    ("decade", "decade", None),
    ("content_rating", "contentRating", "show.contentRating"),
    ("studio", "studio", "show.studio"),
    ("genre", "genre", "show.genre"),
    ("country", "country", "show.country"),
    ("resolution", "resolution", "episode.resolution"),
    ("audio_language", "audioLanguage", "episode.audioLanguage"),
    ("subtitle_language", "subtitleLanguage", "episode.subtitleLanguage"),
    ("network", None, "show.network"),
]


def scrub(text: str) -> str:
    out = str(text)
    url = os.environ["PROBE_PLEX_URL"]
    for secret in [url, url.rstrip("/"), urlsplit(url).netloc,
                   os.environ["PROBE_PLEX_TOKEN"]]:
        if secret:
            out = out.replace(secret, "<scrubbed>")
    return out


def main() -> None:
    server = PlexServer(os.environ["PROBE_PLEX_URL"], os.environ["PROBE_PLEX_TOKEN"])
    for section in server.library.sections():
        if section.type not in ("movie", "show"):
            continue
        print(scrub("=== section %s (%s)" % (section.title, section.type)))
        for name, movie_field, show_field in FIELDS:
            field = movie_field if section.type == "movie" else show_field
            if field is None:
                print("  %-18s n/a for this library type" % name)
                continue
            scope, _, plain = field.rpartition(".")
            scope = scope or section.type
            try:
                choices = list(section.listFilterChoices(field=plain, libtype=scope))
            except Exception as error:  # class name only, never the message
                print("  %-18s REFUSED (%s)" % (name, type(error).__name__))
                continue
            sample = ", ".join(
                "%s=%s" % (c.key, c.title) for c in choices[:5]
            )
            print(scrub("  %-18s %4d value(s)  %s" % (name, len(choices), sample)))


if __name__ == "__main__":
    main()
```

Run it, with the credentials passed as bare `-e NAME` so neither ever appears on
a command line:

```bash
docker compose -p p10at2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm --no-deps -e PROBE_PLEX_URL -e PROBE_PLEX_TOKEN \
    test python p10a_probe.py 2>&1 | tee .superpowers/run-t2-probe.log
```

- [ ] **Step 6: Record the probe and apply its verdict**

Create `docs/research/plex-dynamic-probe/README.md` with: the headline verdict
table, the read-only proof (the four-line call list plus the grep over the
script), the scrubbed output verbatim, and the script reproduced verbatim —
the same five-section shape `docs/research/plex-search-probe/README.md` uses.

**The verdict rules, decided here and not by the implementer's judgement:**

| Probe outcome for `network` | What Step 7's table does |
| --- | --- |
| answers with ≥1 value | the `network` row ships as written below, and its `note` carries the count |
| REFUSED, or 0 values | the `network` row is **omitted from `DYNAMIC_TYPES`**, the omission is recorded in the module docstring with the probe's own words, and Task 6 files a roadmap row for it |
| the probe cannot be run at all (no reachable server) | same as REFUSED — `network` is omitted, and the README records "unprobed, therefore not shipped" rather than a verdict |

Fail closed, always: a dynamic type that enumerates nothing builds one
collection named after nothing, which is exactly the failure
`test_the_three_presets_9b_readjudicated_stay_gated_on_the_engine_row`
(`tests/test_collection_catalog.py:1389-1416`) was written about.

- [ ] **Step 7: Write the type table's failing test, then the table**

`tests/test_collection_dynamic_types.py`:

```python
"""The dynamic type table -- transcription checksums.

The table is Kometa's ``auto`` type map (meta.py:15-22), its
``auto_type_translation`` (meta.py:24-34), its per-type ``default_template``
(meta.py:947-959) and its per-type ``title_format`` defaults (meta.py:868,
:898, :948-959), reduced to the types phase 10a ships (adjudication C3). These
tests are what a reviewer checks the transcription against.
"""
import pytest

from autoposter.collections.dynamic_types import DYNAMIC_TYPES, DynamicType
from autoposter.collections.filters import BY_NAME
from autoposter.collections.search_sorts import KNOWN_SORT_NAMES


def test_the_table_holds_exactly_the_types_c3_scoped():
    """C3's IN list, and nothing else. Every absence is a decision with a
    reason: ``edition`` waits on roadmap row 170; ``original_language`` and
    ``origin_country`` are PLAIN collections upstream, built from a
    full-library TMDb walk (meta.py:36-37), which is the metadata-prefetch
    budget family and not this phase; the people types are 10c; show-decade
    needs the full scan Kometa falls back to (meta.py:881-898); and
    ``number``/``custom``/``tmdb_collection``/the list types are not library
    enumerations at all."""
    assert list(DYNAMIC_TYPES) == [
        "year",
        "decade",
        "content_rating",
        "studio",
        "genre",
        "country",
        "resolution",
        "audio_language",
        "subtitle_language",
        "network",
    ]


def test_every_row_names_a_searchable_attribute_it_can_enumerate():
    """The table's two columns that can silently disagree: the attribute a row
    enumerates and the key it writes into the query are the same row of
    ``FILTER_ATTRIBUTES``, and that row must be searchable on every library
    type the dynamic type serves -- otherwise ``field_for`` raises mid-pass on
    a library the operator was told this type supports."""
    for name, row in DYNAMIC_TYPES.items():
        attribute = BY_NAME[row.attribute]
        assert attribute.searchable, name
        assert row.search_key.split(".")[0] == row.attribute, name
        for library_type in row.kinds:
            assert library_type in ("Movie", "Show"), name
            assert library_type.lower() in attribute.search_kinds, name
            attribute.field_for(library_type.lower())


def test_every_rows_query_key_is_one_this_vocabulary_accepts():
    """The emitted key goes through ``parse_filters`` with ``searching=True``,
    so an operator never writes it and nothing else would catch a row whose
    modifier the attribute's type does not take in a search. ``studio.is`` is
    the row this exists for: upstream singles it out (meta.py:950) because a
    bare ``studio:`` is a CONTAINS match and the bucket wants the exact
    value."""
    from autoposter.collections.filters import parse_filters

    for name, row in DYNAMIC_TYPES.items():
        parse_filters(
            {row.search_key: ["x"]}, field="params", searching=True, base="any"
        )
        assert isinstance(row.key_from, str) and row.key_from in ("key", "title"), name


def test_every_default_sort_is_a_real_sort_and_every_title_format_is_renderable():
    """Two transcription errors that would only surface against a live library:
    a sort name Plex does not have refuses the whole family at build time, and
    a ``title_format`` with neither ``<<key_name>>`` nor ``<<title>>`` would
    title every collection in the family identically."""
    for name, row in DYNAMIC_TYPES.items():
        for sort_name in row.sort_by:
            assert sort_name in KNOWN_SORT_NAMES, name
        assert "<<key_name>>" in row.title_format or "<<title>>" in row.title_format, name
        assert row.note.strip(), name


def test_the_general_default_is_upstreams_and_resolution_is_the_exception():
    """meta.py:950 gives every tag type ``limit: 50`` and
    ``critic_rating.desc``; meta.py:947 gives ``resolution`` ``title.asc`` and
    NO limit. Pinned because they are the two numbers an operator inherits
    without writing anything."""
    assert DYNAMIC_TYPES["genre"].sort_by == ("critic_rating.desc",)
    assert DYNAMIC_TYPES["genre"].limit == 50
    assert DYNAMIC_TYPES["resolution"].sort_by == ("title.asc",)
    assert DYNAMIC_TYPES["resolution"].limit is None


def test_the_title_formats_are_upstreams_four_shapes():
    """meta.py:868 (the base), :955 (year), :957 (movie decade), :948
    (resolution), :959 (every other tag type)."""
    assert DYNAMIC_TYPES["year"].title_format == "Best <<library_type>>s of <<key_name>>"
    assert DYNAMIC_TYPES["decade"].title_format == "Best <<library_type>>s of the <<key_name>>"
    assert DYNAMIC_TYPES["resolution"].title_format == "<<key_name>> <<library_type>>s"
    assert DYNAMIC_TYPES["genre"].title_format == "Top <<key_name>> <<library_type>>s"
    assert DYNAMIC_TYPES["content_rating"].title_format == "Top <<key_name>> <<library_type>>s"


def test_the_two_key_from_key_types_are_the_ones_upstream_keys_on_choice_key():
    """meta.py:933 vs :936 -- ``resolution`` and movie ``decade`` key on
    ``choice.key`` and title on ``choice.title``; every other tag type uses the
    title as BOTH. The languages key on ``choice.key`` too (:928-929). Getting
    this backwards builds ``decade=1980s``, which Plex answers with nothing."""
    keyed = [name for name, row in DYNAMIC_TYPES.items() if row.key_from == "key"]
    assert keyed == ["decade", "resolution", "audio_language", "subtitle_language"]


def test_country_and_decade_are_movie_only_and_network_is_show_only():
    """Upstream's ``auto`` table (meta.py:15-22): ``country`` is Movie/Artist/
    Video, ``network`` and ``origin_country`` are Show, and movie ``decade`` is
    the only decade this phase ships."""
    assert DYNAMIC_TYPES["country"].kinds == ("Movie",)
    assert DYNAMIC_TYPES["decade"].kinds == ("Movie",)
    assert DYNAMIC_TYPES["network"].kinds == ("Show",)
    assert DYNAMIC_TYPES["genre"].kinds == ("Movie", "Show")
```

**If Step 6's verdict omitted `network`**, drop it from the first test's list
and replace the last test's `network` assertion with:

```python
    assert "network" not in DYNAMIC_TYPES  # see the module docstring's probe verdict
```

- [ ] **Step 8: Write `dynamic_types.py`**

```python
"""Which dynamic types this service ships, and what each one asks Plex.

Kometa's dynamic engine is a config-EXPANSION pass: for each distinct value a
library holds it writes one ordinary collection into the same dict a handwritten
``collections:`` block fills (modules/meta.py:805-1465), whose entire body is a
template call. For the twelve tag/scan library types that template is a
``smart_filter`` -- ``{"smart_filter": {"limit": 50, "sort_by":
"critic_rating.desc", "any": {<type>: "<<value>>"}}}`` (meta.py:950), with
``resolution`` the one exception (``{"sort_by": "title.asc", ...}``,
meta.py:947, no limit) -- which Kometa turns into a query string with
``build_filter`` and POSTs as a smart collection (plex.py:1592-1600).

That is the grammar 9b's oracle transcribes and 9c's reconciler writes, which is
why this engine emits 9b-grammar smart collections through
``smart.reconcile_smart_collection`` and not a second write path (10a decision
C1). Every row below therefore carries the same four things: what to enumerate,
what key to write into the query, how to title the result, and how to order it.

**Scope (decision C3).** Only the types ``listFilterChoices`` can enumerate
TODAY. Deliberately absent, each for its own reason:

- ``edition`` -- roadmap row 170 owns the table row it needs.
- ``original_language``/``origin_country`` -- upstream builds these as PLAIN
  collections whose membership comes from a full-library TMDb walk
  (meta.py:36-37, :971-993, builder.py:360-374), which is the metadata-prefetch
  budget family (rows 155/180), not an enumeration.
- show ``decade`` -- Plex's decade filter is movie-only (plex.py:437), so
  upstream falls back to a whole-library scan and REFUSES ``addons`` while doing
  it (meta.py:881-898). Out until that scan has a budget.
- the people types (10c/row 83), ``tmdb_collection``, ``trakt_*``, ``number``,
  ``custom`` and the music types -- none of them is a library enumeration.

**One documented divergence.** Upstream titles a language bucket from TMDb's
``_iso_639_1`` table -- ``en`` becomes "English" -- and falls back to the Plex
choice's own title when TMDb has no entry (meta.py:928-929). This service has no
such table, so a language family is titled from Plex's own ``choice.title``,
which is the fallback branch upstream already ships. The KEYS are identical
either way, so membership is unaffected; only the collection's name differs, and
``title_override`` sets it by hand.
"""
from dataclasses import dataclass

__all__ = ["DYNAMIC_TYPES", "DYNAMIC_TYPE_NAMES", "DynamicType"]


@dataclass(frozen=True)
class DynamicType:
    """One row: a dynamic type an operator can write as ``params.type``.

    ``attribute`` is the ``FILTER_ATTRIBUTES`` row used for BOTH halves of the
    job -- ``field_for(libtype)`` gives the ``listFilterChoices`` field (and its
    libtype scope, when the field is dotted), and the row's search half renders
    the query. One column rather than two, because two would be two chances to
    enumerate one attribute and query another.

    ``search_key`` is what goes into the emitted ``any:`` block, and it is
    ``attribute`` for every row but ``studio``: upstream writes ``studio.is``
    (meta.py:950) because a bare ``studio:`` is Kometa's CONTAINS match and a
    bucket wants the exact value.

    ``key_from`` is which column of a ``listFilterChoices`` row is the KEY --
    ``"title"`` for the tag types, whose key and display value are the same
    string (meta.py:936), and ``"key"`` for the rows upstream keys on
    ``choice.key`` (meta.py:933 for ``resolution`` and movie ``decade``,
    :928-929 for the languages). It decides what ``include``/``exclude``/
    ``addons`` are matched against, which is the difference between
    ``exclude: [1080]`` working and doing nothing.

    ``kinds`` is our library-type spelling (``Movie``/``Show``), taken from
    upstream's ``auto`` table (meta.py:15-22) and NOT from the attribute's
    ``search_kinds``: ``country`` searches on both and is a movie-only dynamic
    type upstream.
    """

    name: str
    attribute: str
    search_key: str
    kinds: tuple[str, ...]
    key_from: str
    title_format: str
    sort_by: tuple[str, ...]
    limit: int | None
    note: str


_BOTH = ("Movie", "Show")
_GENERAL_SORT = ("critic_rating.desc",)
_GENERAL_LIMIT = 50
_GENERAL_TITLE = "Top <<key_name>> <<library_type>>s"


DYNAMIC_TYPES: dict[str, DynamicType] = {
    row.name: row
    for row in (
        DynamicType(
            "year", "year", "year", _BOTH, "title",
            "Best <<library_type>>s of <<key_name>>", _GENERAL_SORT, _GENERAL_LIMIT,
            "meta.py:955 for the title, :919-923 and :936 for the enumeration. "
            "The type whose fan-out the roadmap's own risk note names: a "
            "70-year library is 70 collections, which is why the builder's "
            "``max_collections`` floor defaults below that.",
        ),
        DynamicType(
            "decade", "decade", "decade", ("Movie",), "key",
            "Best <<library_type>>s of the <<key_name>>", _GENERAL_SORT, _GENERAL_LIMIT,
            "MOVIE-ONLY: Plex's decade filter is movie-only (plex.py:437) and "
            "upstream's show fallback is a full library scan (meta.py:881-898). "
            "Keys on ``choice.key`` (``1980``) and titles from ``choice.title`` "
            "(``1980s``), meta.py:933 -- so the query says ``decade=1980`` and "
            "the collection is called 'Best movies of the 1980s'.",
        ),
        DynamicType(
            "content_rating", "content_rating", "content_rating", _BOTH, "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "The shape the Common Sense family is one hardcoded instance of "
            "(``collections/buckets.py``). Its production block is "
            "``type: content_rating`` with ``include`` == the ``addons`` keys, "
            "which is exactly what ``dynamic_keys.derive_keys`` reproduces.",
        ),
        DynamicType(
            "studio", "studio", "studio.is", _BOTH, "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "The one row whose query key is not its attribute: upstream writes "
            "``f'{auto_type}.is'`` for studio alone (meta.py:950), because a "
            "bare ``studio:`` is a CONTAINS match and a per-studio bucket wants "
            "the exact value.",
        ),
        DynamicType(
            "genre", "genre", "genre", _BOTH, "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "Row 155's listing truncation does NOT reach this type: membership "
            "is decided inside Plex by the stored filter, so the two-tags-per-"
            "item cap that makes a client-side genre FILTER wrong never touches "
            "a genre SEARCH (9b's probe 3, 5/5).",
        ),
        DynamicType(
            "country", "country", "country", ("Movie",), "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "Movie-only as a dynamic type (meta.py:18-19) even though the "
            "search answers for both library types -- upstream's type table is "
            "the authority on which libraries get the family, and the attribute "
            "row is the authority on how to ask.",
        ),
        DynamicType(
            "resolution", "resolution", "resolution", _BOTH, "key",
            "<<key_name>> <<library_type>>s", ("title.asc",), None,
            "The one type with its own template (meta.py:947): ``title.asc`` "
            "and NO limit, because 'every 4k movie' is not a top-50 question. "
            "Enumerated at the EPISODE libtype on a show library "
            "(``episode.resolution``), which is the server doing the traversal "
            "our client-side filter refuses to pay for.",
        ),
        DynamicType(
            "audio_language", "audio_language", "audio_language", _BOTH, "key",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "Keys are Plex's own language keys (meta.py:928-929 keys on "
            "``choice.key``); the emitted term goes through the resolver's "
            "``_language_keys`` expansion, which is Kometa's "
            "``get_language_search_values`` -- and because the emitted block is "
            "``any:``, a base code expanding to three locale variants is an OR, "
            "not the ``all:`` AND that made row 182's probe answer 0 instead of "
            "442. Titled from Plex rather than from TMDb's English name; see the "
            "module docstring's divergence note.",
        ),
        DynamicType(
            "subtitle_language", "subtitle_language", "subtitle_language",
            _BOTH, "key", _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "``audio_language``'s sibling in every respect, including the "
            "``any:``-base reason its terms are OR-ed.",
        ),
        DynamicType(
            "network", "network", "network", ("Show",), "title",
            _GENERAL_TITLE, _GENERAL_SORT, _GENERAL_LIMIT,
            "SHOW-ONLY (meta.py:19), and the row whose enumerability was "
            "PROBED before it shipped rather than assumed: 9b proved the SEARCH "
            "field answers (91 networks) while the client-side listing strands "
            "it outright, and the choices listing is the same family but was "
            "unproven until phase 10a's probe "
            "(``docs/research/plex-dynamic-probe/README.md``). Upstream "
            "additionally gates this type on the New Plex TV Agent "
            "(meta.py:820-821); we have no agent check, so a library whose "
            "agent cannot answer enumerates nothing and the definition refuses "
            "with the count.",
        ),
    )
}

DYNAMIC_TYPE_NAMES: tuple[str, ...] = tuple(DYNAMIC_TYPES)
```

**If Step 6's verdict omitted `network`**, delete that `DynamicType(...)` entry
and add to the module docstring, under the scope list:

```
- ``network`` -- the phase's own probe found the choices listing does not
  answer for it on the production show library (see
  ``docs/research/plex-dynamic-probe/README.md``); shipping the type would
  build a family of nothing. Filed as a roadmap row by Task 6.
```

- [ ] **Step 9: Run the type-table tests green**

```bash
docker compose -p p10at2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_dynamic_types.py 2>&1 | tee /app/.superpowers/run-t2-green2.log; echo EXIT=$?'
```

Expected: PASS.

- [ ] **Step 10: Mutation proof**

```bash
cp src/autoposter/collections/dynamic_types.py /tmp/dynamic_types.py.bak
cp src/autoposter/collections/builders/plex_search.py /tmp/plex_search.py.bak
```

Mutation A — change `studio`'s `search_key` from `"studio.is"` to `"studio"`.
Expected RED: `test_every_rows_query_key_is_one_this_vocabulary_accepts` stays
green (a bare `studio:` parses), so this must be caught by a dedicated
assertion — add it to
`test_the_two_key_from_key_types_are_the_ones_upstream_keys_on_choice_key`'s
neighbourhood:

```python
def test_studio_is_the_one_row_whose_query_key_carries_a_modifier():
    """meta.py:950's ``f'{auto_type}.is' if auto_type == 'studio' else
    auto_type`` -- a bare ``studio:`` is a CONTAINS match, so dropping the
    ``.is`` would put every A24-distributed label into the A24 bucket."""
    assert DYNAMIC_TYPES["studio"].search_key == "studio.is"
    assert [n for n, r in DYNAMIC_TYPES.items() if "." in r.search_key] == ["studio"]
```

Re-run: RED on that test. Restore, `cmp`, GREEN.

Mutation B — in `LibraryTagResolver.choices`, replace `scope = scope or
self._libtype` with `scope = self._libtype`. Expected RED:
`test_choices_asks_a_show_library_at_the_scope_the_row_names`. Restore, `cmp`,
GREEN. Paste both.

- [ ] **Step 11: Full suite, golden, ruff, delete the probe script, commit**

```bash
rm p10a_probe.py
docker compose -p p10at2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10at2-full test sh -c 'timeout -s KILL 1800 pytest -q 2>&1 | tee /app/.superpowers/run-t2-full.log; echo EXIT=$?'
docker wait p10at2-full
docker logs p10at2-full | tail -40
docker rm p10at2-full
docker compose -p p10at2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10at2 down
```

```bash
git add src/autoposter/collections/builders/plex_search.py \
        tests/test_builder_plex_search.py \
        src/autoposter/collections/dynamic_types.py \
        tests/test_collection_dynamic_types.py \
        docs/research/plex-dynamic-probe/README.md
git commit --no-gpg-sign -m "feat(collections): the dynamic enumeration seam and type table

LibraryTagResolver.choices exposes the listFilterChoices vocabulary the dynamic
engine enumerates -- the same memoised, class-name-only-wrapped call the tag
resolver already made. dynamic_types transcribes Kometa's per-type template,
title_format and enumeration key rules for the ten types C3 scoped. The network
row's enumerability was probed read-only against production before it shipped."
```

---

### Task 3: The key machinery — include, exclude, addons, custom_keys

**Files:**

- Create: `tests/oracle/10a/kometa_dynamic.py` (the keys half; Task 4 appends the titles half)
- Create: `tests/test_collection_dynamic_oracle.py` (the keys cases; Task 4 appends the titles cases)
- Create: `src/autoposter/collections/dynamic_keys.py`
- Create: `tests/test_collection_dynamic_keys.py`

**Interfaces:**

- Consumes: nothing from Tasks 1–2 at run time. `derive_keys` takes plain data, which is what makes it testable with no Plex, no builder and no config.
- Produces:
  - `dynamic_keys.DynamicKey` — frozen: `key: str`, `value: str`, `values: tuple[str, ...]`.
  - `dynamic_keys.DerivedKeys` — frozen: `keys: tuple[DynamicKey, ...]`, `other_keys: tuple[str, ...]`, `used_keys: tuple[str, ...]`.
  - `dynamic_keys.derive_keys(enumerated, *, include=(), exclude=(), addons=None, custom_keys=True) -> DerivedKeys`, where `enumerated` is an ordered sequence of `(key, value)` pairs.
- The oracle driver exposes `kometa_dynamic.derive(all_pairs, *, include=None, exclude=None, addons=None, custom_keys=True) -> dict` and a `KEY_CASES` list; running it as a script prints one JSON line per case.

**Why this is its own task.** This is the whole of roadmap decomposition step 2
("include/exclude, addons merge, generalising the CS addons logic") and it is
the part where being subtly wrong is invisible: an addon member that keeps its
own collection, or an excluded key that still lands in the `other` bucket, is a
collection an operator did not ask for with a membership that looks plausible.
It is pure data-in/data-out, so it is provable against a standalone
transcription of Kometa's own loop before anything touches a library.

- [ ] **Step 1: Write the oracle driver (keys half)**

`tests/oracle/10a/kometa_dynamic.py`:

```python
"""THE DYNAMIC-COLLECTIONS ORACLE -- Kometa's own key and title derivation.

Provenance: Kometa v2.4.8. Every function below is transcribed from the line
ranges quoted verbatim in ``.superpowers/sdd/p10a-upstream-dynamic.md`` §5 and
§6, which were read out of:

  modules/meta.py:825-867      the exclude/include/addons reads and the
                               addon-members-become-exclusions extension
  modules/meta.py:1217-1228    the addons -> keys merge (``custom_keys``)
  modules/meta.py:1348-1365    the include whitelist and each key's query values
  modules/util.py:917-961      the ``strlist`` / ``dictliststr`` / ``strdict``
                               coercions the three options are read through

NOTHING from the autoposter repository is imported, and nothing outside the
standard library is either: this half of Kometa reads a dict of enumerated
values and three option lists and returns keys -- there is no Plex object, no
TMDb client and no template engine anywhere in it.

REMOVED throughout: every ``logger.*`` call (they warn, they do not decide),
the ``self.temp_vars`` library-level override layer and its
``append_*``/``remove_*`` variants (meta.py:826-861 -- this service has no
library-level template variables, so the ``elif dynamic[...]`` branch is the
only reachable one), and the per-type enumeration itself (meta.py:869-1211),
whose output IS this driver's ``all_pairs`` argument.

Run:  python kometa_dynamic.py
      -> prints one ``N <json>`` line per case
"""
import json


def strlist(value):
    """modules/util.py:933 -- ``strlist``: every element ``str()``."""
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(v) for v in value]


def dictliststr(value):
    """modules/util.py:959 -- ``dictliststr``: keys AND members both ``str()``."""
    if not value:
        return {}
    out = {}
    for key, members in value.items():
        if not isinstance(members, list):
            members = [members]
        out[str(key)] = [str(m) for m in members]
    return out


def derive(all_pairs, *, include=None, exclude=None, addons=None, custom_keys=True):
    """meta.py:825-867, :1217-1228 and :1348-1365, in upstream's own order.

    ``all_pairs`` is the enumeration: an ordered sequence of ``(key, value)``,
    which is what each type's ``all_keys``/``auto_list`` comprehension produces
    (e.g. :933 keys on ``choice.key``, :936 keys on ``choice.title``).
    """
    # :826-829 / :835-839 / :845-849, with the temp_vars layer removed.
    og_exclude = strlist(exclude)
    include = [i for i in strlist(include) if i not in og_exclude]
    addons = dictliststr(addons)

    # :863-867. EVERY addon member is pushed into ``exclude`` -- that is how an
    # addon member stops getting a collection of its own. Note the asymmetry
    # upstream keeps and this keeps with it: ``exclude`` is str()-coerced and
    # grows, while ``og_exclude`` stays as written and is what the custom-key
    # check below consults.
    exclude = [str(e) for e in og_exclude]
    for k, v in addons.items():
        if k in v:
            pass  # logger.warning(f"{k} cannot be an addon for itself")
        exclude.extend([y for y in v if y != k and y not in exclude])

    # The enumeration comprehension, :928-937: ``all_keys`` is everything the
    # library reported; ``auto_list`` is what survived ``exclude``. A key is
    # dropped when the KEY or the VALUE is excluded.
    all_keys = {}
    auto_list = {}
    for key, value in all_pairs:
        all_keys[key] = value
        if key not in exclude and value not in exclude:
            auto_list[key] = value

    # :1217-1228 -- the addons->keys merge, which is ``custom_keys``' whole job.
    # The guard means this fires ONLY for an addon key the library does not
    # itself carry: a synthetic bucket. An addon key that IS a real library
    # value keeps its own enumerated entry untouched.
    for add_key, combined_keys in addons.items():
        if add_key not in all_keys and add_key not in og_exclude:
            final_keys = [ck for ck in combined_keys if ck in all_keys]
            if custom_keys and final_keys:
                if add_key not in auto_list:
                    auto_list[add_key] = add_key
                addons[add_key] = final_keys
            elif custom_keys:
                pass  # logger.trace(f"{add_key} Custom Key must have at least one Key")
            else:
                for final_key in final_keys:
                    auto_list[final_key] = all_keys[final_key]

    keys = []
    other_keys = []
    used_keys = []
    for key, value in auto_list.items():
        # :1348-1351 -- ``include`` is a whitelist applied LAST. An excluded key
        # is neither built nor swept into ``other``; an unincluded-but-not-
        # excluded key becomes an ``other`` member.
        if include and key not in include:
            if key not in exclude:
                other_keys.append(key)
            continue
        # :1362-1365 -- the bucket's query value: the key itself if the library
        # really has it, plus every addon member the library really has, self
        # excluded. (Upstream's ``or auto_type == "custom"`` disjunct is dropped
        # with the ``custom`` type itself, which this service does not ship.)
        key_value = [key] if key in all_keys else []
        if key in addons:
            key_value.extend([a for a in addons[key] if a in all_keys and a != key])
        used_keys.extend(key_value)
        keys.append({"key": key, "value": value, "values": key_value})
    return {"keys": keys, "other_keys": other_keys, "used_keys": used_keys}


# The enumerations the cases run against. Shaped like a real
# ``listFilterChoices`` answer for the type named, and shared BY VALUE with
# ``tests/test_collection_dynamic_oracle.py`` -- never by import, in either
# direction.
RATINGS = [
    ("G", "G"), ("PG", "PG"), ("PG-13", "PG-13"), ("R", "R"),
    ("NC-17", "NC-17"), ("Unrated", "Unrated"),
]
DECADES = [("1980", "1980s"), ("1990", "1990s"), ("2000", "2000s")]

KEY_CASES = [
    # 1. The plainest family: every value becomes a key, each asking for itself.
    ("plain", RATINGS, {}),
    # 2. The Common Sense shape -- synthetic buckets over addon members, with
    #    ``include`` naming exactly the bucket keys (the production block's own
    #    invariant). Every member is excluded from having its own collection by
    #    :863-867, and the leftovers land in ``other_keys``.
    ("cs-shaped", RATINGS, {
        "include": ["Kids", "Teens"],
        "addons": {"Kids": ["G", "PG"], "Teens": ["PG-13", "R"]},
    }),
    # 3. The same, with ``custom_keys: false``: no synthetic bucket at all, and
    #    each present member is promoted back to a collection of its own
    #    (:1226-1228) -- which is what re-adds the keys :863-867 excluded.
    ("custom-keys-false", RATINGS, {
        "addons": {"Kids": ["G", "PG"]}, "custom_keys": False,
    }),
    # 4. An addon key that IS a real library value: the merge's guard means the
    #    branch never fires, so the key keeps its own enumerated entry and picks
    #    its members up as extra query values.
    ("addon-key-the-library-has", RATINGS, {
        "addons": {"PG": ["G"]},
    }),
    # 5. An addon list containing its own key -- the AU/NZ shape. ``a != key``
    #    is where the de-dupe happens (:1365), not in a prepend.
    ("addon-list-holds-its-own-key", RATINGS, {
        "addons": {"Teens": ["Teens", "PG-13", "R"]},
    }),
    # 6. ``exclude`` matched against the VALUE rather than the key, which is the
    #    only way to exclude a decade by the name an operator can see.
    ("exclude-by-value", DECADES, {"exclude": ["1990s"]}),
    # 7. ``include`` naming a key the library does not have: no collection, and
    #    nothing in ``other`` either.
    ("include-names-a-missing-key", DECADES, {"include": ["1980", "1970"]}),
    # 8. YAML integer keys and integer members -- the DE/UK certification shape.
    #    ``dictliststr`` and ``strlist`` are what make them match.
    ("integer-keys", [("12", "12"), ("16", "16"), ("18", "18")], {
        "include": [12, 16], "addons": {12: [16]},
    }),
]


def main():
    for index, (name, pairs, options) in enumerate(KEY_CASES, start=1):
        print("%d %s %s" % (index, name, json.dumps(derive(pairs, **options))))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the driver and capture its answers**

```bash
docker compose -p p10at3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm --no-deps test sh -c 'cd /app/tests/oracle/10a && python kometa_dynamic.py 2>&1 | tee /app/.superpowers/run-t3-oracle.log; echo EXIT=$?'
```

Copy the eight printed JSON documents verbatim — they are the goldens.

**Before pinning them, verify the transcription against the source if the
environment can reach the network** (Global Constraints, oracle discipline):

```bash
curl -sfL https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/modules/meta.py -o /tmp/meta.py \
    && sed -n '825,867p;1217,1228p;1348,1365p' /tmp/meta.py
```

Diff each block against the transcription line by line and record the result —
including "no network, not verified" if that is the truth — in the task report.

- [ ] **Step 3: Write the failing oracle test**

`tests/test_collection_dynamic_oracle.py`:

```python
"""THE DYNAMIC-COLLECTIONS ORACLE -- phase 10a's acceptance for keys and titles.

``src/autoposter/collections/dynamic_keys.py`` and
``src/autoposter/collections/dynamic_titles.py`` are TRANSCRIPTIONS of Kometa's
``dynamic_collections`` expansion pass, and the danger is the one 9b's search
oracle exists for: an addon member that keeps its own collection, or a key name
whose prefix was stripped when upstream would not have stripped it, produces a
full, plausible, WRONG family. Every other test in this phase asserts the
transcription against itself.

This file asserts it against Kometa. The driver is
``tests/oracle/10a/kometa_dynamic.py`` -- under ``tests/``, not under
``.superpowers/``, because this test READS it and ``.superpowers/`` is
gitignored; a file an assertion depends on has to be in the checkout that runs
the assertion. It imports nothing from this repository, and this file imports
nothing from it: the enumerations both need are shared BY VALUE, and a test
below holds the two copies equal.

Do not edit a golden here to make a test pass: if ours differs, ours is wrong.
"""
import ast
import json
from pathlib import Path

import pytest

from autoposter.collections.dynamic_keys import derive_keys

ORACLE_DRIVER = Path(__file__).parent / "oracle" / "10a" / "kometa_dynamic.py"

# COPIES of the driver's enumerations, shared by value. The next test holds
# them equal.
RATINGS = [
    ("G", "G"), ("PG", "PG"), ("PG-13", "PG-13"), ("R", "R"),
    ("NC-17", "NC-17"), ("Unrated", "Unrated"),
]
DECADES = [("1980", "1980s"), ("1990", "1990s"), ("2000", "2000s")]

KEY_CASES = [
    ("plain", RATINGS, {}),
    ("cs-shaped", RATINGS, {
        "include": ["Kids", "Teens"],
        "addons": {"Kids": ["G", "PG"], "Teens": ["PG-13", "R"]},
    }),
    ("custom-keys-false", RATINGS, {
        "addons": {"Kids": ["G", "PG"]}, "custom_keys": False,
    }),
    ("addon-key-the-library-has", RATINGS, {"addons": {"PG": ["G"]}}),
    ("addon-list-holds-its-own-key", RATINGS, {
        "addons": {"Teens": ["Teens", "PG-13", "R"]},
    }),
    ("exclude-by-value", DECADES, {"exclude": ["1990s"]}),
    ("include-names-a-missing-key", DECADES, {"include": ["1980", "1970"]}),
    ("integer-keys", [("12", "12"), ("16", "16"), ("18", "18")], {
        "include": [12, 16], "addons": {12: [16]},
    }),
]

# KOMETA'S OWN ANSWERS, pinned as data. Produced by
# ``tests/oracle/10a/kometa_dynamic.py``, Kometa v2.4.8's ``meta.py`` key
# derivation transcribed standalone. The raw run is in the Task 3 report.
KOMETA_KEYS = {
    "plain": {...},                       # <- paste line 1's JSON
    "cs-shaped": {...},                   # <- line 2
    "custom-keys-false": {...},           # <- line 3
    "addon-key-the-library-has": {...},   # <- line 4
    "addon-list-holds-its-own-key": {...},# <- line 5
    "exclude-by-value": {...},            # <- line 6
    "include-names-a-missing-key": {...}, # <- line 7
    "integer-keys": {...},                # <- line 8
}


def _ours(pairs, options):
    derived = derive_keys(
        pairs,
        include=options.get("include", ()),
        exclude=options.get("exclude", ()),
        addons=options.get("addons"),
        custom_keys=options.get("custom_keys", True),
    )
    return {
        "keys": [
            {"key": k.key, "value": k.value, "values": list(k.values)}
            for k in derived.keys
        ],
        "other_keys": list(derived.other_keys),
        "used_keys": list(derived.used_keys),
    }


@pytest.mark.parametrize(
    ("name", "pairs", "options"), KEY_CASES, ids=[c[0] for c in KEY_CASES]
)
def test_our_keys_are_kometas(name, pairs, options):
    assert _ours(pairs, options) == KOMETA_KEYS[name]


def test_the_oracles_enumerations_match_this_files_copies():
    """The driver imports nothing from here and this file imports nothing from
    there, so the two enumerations are shared by VALUE. This reads the driver
    as text and compares the literals -- the only coupling that does not break
    the isolation. Anchored to ``__file__``, not to the working directory: a
    guarantee that can quietly stop applying is not a guarantee."""
    tree = ast.parse(ORACLE_DRIVER.read_text(encoding="utf-8"))
    literals = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("RATINGS", "DECADES")
    }
    assert literals["RATINGS"] == [list(pair) for pair in RATINGS] or \
        literals["RATINGS"] == RATINGS
    assert literals["DECADES"] == [list(pair) for pair in DECADES] or \
        literals["DECADES"] == DECADES


def test_the_driver_still_produces_the_pinned_key_answers():
    """The goldens are data and the driver that produced them is tracked,
    reviewable and editable -- so it can drift from them with nothing noticing.

    Running the driver HERE does not violate "never compare ours against the
    driver at test time": the assertion above still runs against the pinned
    text, and this one never touches our code. Run in-process rather than
    marked slow: the driver imports only the standard library, opens no socket
    and reads no clock."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("kometa_dynamic_driver", ORACLE_DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    for (name, pairs, options), (pinned_name, pinned) in zip(
        driver.KEY_CASES, KOMETA_KEYS.items(), strict=True
    ):
        assert name == pinned_name
        assert json.loads(json.dumps(driver.derive(pairs, **options))) == pinned, name
```

- [ ] **Step 4: Run it and watch it fail**

```bash
docker compose -p p10at3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_dynamic_oracle.py 2>&1 | tee /app/.superpowers/run-t3-red1.log; echo EXIT=$?'
```

Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.collections.dynamic_keys'`.

- [ ] **Step 5: Write `dynamic_keys.py`**

```python
"""Which keys a dynamic family builds, and what each one asks Plex for.

The generalisation of ``collections/buckets.py``. That module is one hardcoded
instance of this: a fixed include list, a fixed addon table, "the bucket's own
key when the library carries it plus every addon candidate the library carries",
and the leftovers as a set complement. Here the table is the operator's and the
enumeration is the library's, and the order of operations is Kometa's own
(modules/meta.py:825-867, :1217-1228, :1348-1365) because the CS family is one
input to it and must come out unchanged.

Four rules that look like details and are not:

- **Every addon member is excluded from having its own collection**
  (meta.py:863-867). That is what makes a bucket a bucket rather than a
  duplicate of its members.
- **The merge only fires for an addon key the library does not carry**
  (meta.py:1217-1218). An addon key that IS a real value keeps its own
  enumerated entry and simply gains extra query values -- there is no synthetic
  bucket shadowing a real one.
- **``custom_keys: false`` is not "no addons"**, it is "no synthetic buckets":
  each present member is promoted back to a collection of its own
  (meta.py:1226-1228), which un-does the exclusion above for exactly those
  members.
- **``include`` is a whitelist applied LAST** (meta.py:1348-1351), and an
  unincluded-but-not-excluded key falls into ``other_keys``. With no
  ``include`` the ``other`` bucket is dead, which is why ``other_name`` is
  gated on it upstream and here.

Pure. No Plex, no config, no I/O: it takes the enumeration as data, which is
what lets the whole of it be proven against Kometa's own loop in
``tests/test_collection_dynamic_oracle.py``.
"""
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["DerivedKeys", "DynamicKey", "derive_keys"]


@dataclass(frozen=True)
class DynamicKey:
    """One collection-to-be.

    ``key`` is what ``include``/``exclude``/``addons`` and the overrides are
    matched against; ``value`` is the display string the title is built from
    (the two are the same for every type that keys on ``choice.title``);
    ``values`` is what the query asks Plex for, which is the key itself plus
    every addon member the library actually carries, self excluded.
    """

    key: str
    value: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class DerivedKeys:
    """The whole family: its keys, its leftovers, and what it consumed.

    ``other_keys`` is the ``other`` bucket's membership -- keys the library has
    that no ``include`` entry named. ``used_keys`` is every value the built
    keys consumed, which is upstream's ``used_keys`` (meta.py:1365): the
    complement-shaped reading of the same leftovers, kept because it is the one
    an ``other`` bucket written as "everything not already claimed" needs, and
    because ``buckets.derive_buckets`` computes exactly it today.
    """

    keys: tuple[DynamicKey, ...]
    other_keys: tuple[str, ...]
    used_keys: tuple[str, ...]


def _strlist(value: object) -> list[str]:
    """Kometa's ``strlist`` (modules/util.py:933): every element ``str()``.

    Not decoration: an operator's ``include: [12, 16]`` is YAML integers and
    the enumeration's keys are strings, so without this the two never meet and
    the family silently builds nothing. The DE and UK certification tables are
    written with integer keys upstream.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        value = [value]
    return [str(one) for one in value]


def _dictliststr(value: object) -> dict[str, list[str]]:
    """Kometa's ``dictliststr`` (modules/util.py:959): keys AND members ``str()``."""
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("addons is a mapping of key -> members, not %r" % (value,))
    out: dict[str, list[str]] = {}
    for key, members in value.items():
        if isinstance(members, (str, bytes)) or not isinstance(members, Iterable):
            members = [members]
        out[str(key)] = [str(one) for one in members]
    return out


def derive_keys(
    enumerated: Sequence[tuple[str, str]],
    *,
    include: object = (),
    exclude: object = (),
    addons: object = None,
    custom_keys: bool = True,
) -> DerivedKeys:
    """Kometa's key derivation, whole.

    ``enumerated`` is ``(key, value)`` in the library's own order -- what
    ``LibraryTagResolver.choices`` returns, mapped through the type's
    ``key_from`` rule. Order is preserved end to end, so the family's
    collections are created in the order Plex reported its values, and a
    synthetic bucket appears after every real key.
    """
    written_exclude = _strlist(exclude)
    included = [one for one in _strlist(include) if one not in written_exclude]
    addon_table = _dictliststr(addons)

    # meta.py:863-867. ``excluded`` grows with every addon member; the WRITTEN
    # list stays as it was, because the merge below consults it rather than the
    # grown one -- an addon key an operator explicitly excluded must not come
    # back as a synthetic bucket.
    excluded = list(written_exclude)
    for key, members in addon_table.items():
        excluded.extend([m for m in members if m != key and m not in excluded])

    # meta.py:928-937. ``present`` is everything the library reported;
    # ``surviving`` is what ``exclude`` left, matched against the key OR the
    # display value -- an operator excluding "1990s" means the decade whose key
    # is "1990".
    present: dict[str, str] = {}
    surviving: dict[str, str] = {}
    for key, value in enumerated:
        present[key] = value
        if key not in excluded and value not in excluded:
            surviving[key] = value

    # meta.py:1217-1228.
    for add_key, members in addon_table.items():
        if add_key in present or add_key in written_exclude:
            continue
        real = [m for m in members if m in present]
        if custom_keys and real:
            surviving.setdefault(add_key, add_key)
            addon_table[add_key] = real
        elif not custom_keys:
            for member in real:
                surviving[member] = present[member]

    keys: list[DynamicKey] = []
    other_keys: list[str] = []
    used_keys: list[str] = []
    for key, value in surviving.items():
        # meta.py:1348-1351.
        if included and key not in included:
            if key not in excluded:
                other_keys.append(key)
            continue
        # meta.py:1362-1365.
        values = [key] if key in present else []
        values.extend(
            [m for m in addon_table.get(key, ()) if m in present and m != key]
        )
        used_keys.extend(values)
        keys.append(DynamicKey(key=key, value=value, values=tuple(values)))

    return DerivedKeys(
        keys=tuple(keys),
        other_keys=tuple(other_keys),
        used_keys=tuple(used_keys),
    )
```

- [ ] **Step 6: Run the oracle green**

```bash
docker compose -p p10at3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_dynamic_oracle.py 2>&1 | tee /app/.superpowers/run-t3-green1.log; echo EXIT=$?'
```

Expected: PASS, eight parametrised cases plus the two structural tests. **If a
case is RED, ours is wrong** — adjudicate in Kometa's favour and change
`dynamic_keys.py`, then record the disagreement in the task report the way 9a's
four disagreements were recorded.

- [ ] **Step 7: Add the unit surface the oracle cannot express**

`tests/test_collection_dynamic_keys.py` — the properties that are about OUR
contract rather than about Kometa's answers:

```python
"""``derive_keys``' own surface: ordering, purity and the CS equivalence.

The oracle next door proves the ANSWERS. These prove the promises the rest of
the phase makes about them.
"""
from autoposter.collections.buckets import derive_buckets, load_table
from autoposter.collections.dynamic_keys import derive_keys


def test_the_family_keeps_the_librarys_own_order_and_appends_synthetic_buckets():
    """Order is the collection-creation order and the sort-title order an
    operator sees, so it is a promise, not an accident: enumerated keys in the
    order Plex reported them, then any synthetic bucket."""
    derived = derive_keys(
        [("b", "b"), ("a", "a")], addons={"Zed": ["a"]},
    )
    assert [k.key for k in derived.keys] == ["b", "Zed"]


def test_deriving_twice_from_the_same_input_gives_the_same_answer():
    """No mutation of the caller's data, no hidden state. The builder calls
    this once per pass per definition and the config layer may call it never;
    a function that edited its ``addons`` argument in place would make the
    second pass differ from the first."""
    addons = {"Kids": ["G", "PG"]}
    pairs = [("G", "G"), ("PG", "PG"), ("R", "R")]
    first = derive_keys(pairs, addons=addons, include=["Kids"])
    second = derive_keys(pairs, addons=addons, include=["Kids"])
    assert first == second
    assert addons == {"Kids": ["G", "PG"]}


def test_the_common_sense_table_derives_the_same_values_this_engine_would():
    """The equivalence phase 10a-2's port rests on, asserted now rather than
    discovered then: fed the SHIPPED Common Sense table as
    ``include`` + ``addons``, this engine produces each bucket's values exactly
    as ``buckets.derive_buckets`` does -- same members, same order after
    sorting, and the same leftovers for the ``other`` bucket.

    It is a value-level claim only. Whether the resulting QUERY is the same
    membership is the golden gate's and the port's own equivalence proof, and
    that is 10a-2's task, not this one's.
    """
    table = load_table()
    present = {"G", "PG", "PG-13", "R", "NR", "TV-MA"}
    enumerated = [(one, one) for one in sorted(present)]

    derived = derive_keys(
        enumerated,
        include=list(table["include"]),
        addons={key: list(values) for key, values in table["addons"].items()},
    )
    ours = {k.key: tuple(sorted(k.values)) for k in derived.keys}

    for bucket in derive_buckets(present, "Movie"):
        if bucket.key == "other":
            assert tuple(sorted(derived.other_keys)) == bucket.values
            continue
        assert ours[bucket.key] == bucket.values, bucket.key


def test_an_addon_key_the_library_carries_is_not_shadowed_by_a_synthetic_one():
    """meta.py:1217-1218's guard, stated as behaviour: ``addons: {PG: [G]}`` on
    a library that HAS a PG rating widens the real PG bucket. It does not
    create a second, synthetic 'PG'."""
    derived = derive_keys(
        [("G", "G"), ("PG", "PG")], addons={"PG": ["G"]},
    )
    assert [k.key for k in derived.keys] == ["PG"]
    assert derived.keys[0].values == ("PG", "G")


def test_a_synthetic_bucket_with_no_present_member_builds_nothing():
    """meta.py:1224-1225. The library has none of the members, so there is no
    collection -- rather than an empty one, which on a smart filter would match
    the entire library (``reconcile.py:609-612``)."""
    derived = derive_keys([("R", "R")], addons={"Kids": ["G", "PG"]})
    assert [k.key for k in derived.keys] == ["R"]
```

- [ ] **Step 8: Run the unit tests green**

```bash
docker compose -p p10at3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_dynamic_keys.py tests/test_collection_dynamic_oracle.py tests/test_collection_buckets.py 2>&1 | tee /app/.superpowers/run-t3-green2.log; echo EXIT=$?'
```

Expected: PASS. `tests/test_collection_buckets.py` is included because the CS
equivalence test above reads the shipped table; if the table moved, both go red
together and the cause is obvious.

- [ ] **Step 9: Mutation proof**

```bash
cp src/autoposter/collections/dynamic_keys.py /tmp/dynamic_keys.py.bak
```

Mutation A — delete the `excluded.extend(...)` line (the addon-members-become-
exclusions rule). Expected RED: the `cs-shaped` and
`addon-list-holds-its-own-key` oracle cases, plus
`test_the_common_sense_table_derives_the_same_values_this_engine_would`.

Mutation B — restore, `cmp`, then change `if add_key in present` to
`if False` in the merge guard. Expected RED:
`addon-key-the-library-has` and
`test_an_addon_key_the_library_carries_is_not_shadowed_by_a_synthetic_one`.

Restore, `cmp`, GREEN. Paste both RED outputs.

- [ ] **Step 10: Full suite, golden, ruff, commit**

```bash
docker compose -p p10at3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10at3-full test sh -c 'timeout -s KILL 1800 pytest -q 2>&1 | tee /app/.superpowers/run-t3-full.log; echo EXIT=$?'
docker wait p10at3-full
docker logs p10at3-full | tail -40
docker rm p10at3-full
docker compose -p p10at3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10at3 down
```

```bash
git add tests/oracle/10a/kometa_dynamic.py tests/test_collection_dynamic_oracle.py \
        src/autoposter/collections/dynamic_keys.py tests/test_collection_dynamic_keys.py
git commit --no-gpg-sign -m "feat(collections): dynamic key derivation, proven against Kometa

include/exclude/addons/custom_keys in Kometa v2.4.8's own order of operations
(meta.py:825-867, :1217-1228, :1348-1365), transcribed standalone as an oracle
and pinned as eight goldens. The shipped Common Sense table derives the same
bucket values through this engine as buckets.derive_buckets does today."
```

---

### Task 4: The title machinery

> **CORRECTION (2026-08-27, filed by Task 6's wrap — the plan text below is left
> as written and is WRONG here).** Three places in this task claim that
> `<<library_typeU>>` must be substituted FIRST because replacing
> `<<library_type>>` first would "eat the prefix of the longer token and leave a
> stray `U>>`": the oracle transcription's comment (this section's
> `key_name_and_title`, "typeU FIRST"), `render_title`'s docstring ("substituted
> FIRST and that is load-bearing, not tidiness"), and the test named
> `test_the_library_type_tokens_substitute_in_the_order_that_survives_both`.
> **There is no such prefix relationship.** `<<library_type>>` is not a substring
> of `<<library_typeU>>` — the `U` sits between `type` and the closing `>>`, so
> `"<<library_typeU>>".replace("<<library_type>>", x)` matches nothing and the
> two replacements provably commute. Upstream itself does `<<library_type>>`
> first (meta.py:1268-1271), which the plan's own citation all but points at
> and the plan's prose then contradicts. (Amended 2026-08-27 by phase 10a-2's
> wrap, closing the 10a-1 Task 6 review's M-5: the transcription below writes
> that citation as `:1269-1272`, one line adrift of the `:1268-1271` the
> shipped code and the oracle both use, so "points at" was itself a shade
> generous.) The Task 4 review adjudicated this and the
> SHIPPED code is upstream's order with the reason stated correctly
> (`src/autoposter/collections/dynamic_titles.py::_substitute_library_type`);
> what IS load-bearing is which of the two lowercases, not which runs first.
> Recorded as a dated note rather than edited away, because the plan is the
> record of what was believed when the tasks were dispatched.

**Files:**

- Modify: `tests/oracle/10a/kometa_dynamic.py` (append the titles half and `TITLE_CASES`; `main` prints both)
- Modify: `tests/test_collection_dynamic_oracle.py` (append the titles cases and their goldens)
- Create: `src/autoposter/collections/dynamic_titles.py`
- Create: `tests/test_collection_dynamic_titles.py`

**Interfaces:**

- Consumes: `dynamic_keys.DynamicKey` and `dynamic_keys.DerivedKeys` (Task 3, exact fields `key`, `value`, `values` / `keys`, `other_keys`, `used_keys`).
- Produces:
  - `dynamic_titles.TitledKey` — frozen: `key: str`, `key_name: str`, `title: str`, `values: tuple[str, ...]`.
  - `dynamic_titles.DuplicateFamilyTitle(Exception)`.
  - `dynamic_titles.render_title(title_format: str, key_name: str, library_type: str) -> str`.
  - `dynamic_titles.key_name_for(key, value, *, key_name_override=None, remove_prefix=(), remove_suffix=()) -> str`.
  - `dynamic_titles.family_titles(derived, *, library_type, title_format, key_name_override=None, title_override=None, remove_prefix=(), remove_suffix=(), other_name=None) -> tuple[TitledKey, ...]`.

**Why the title half is separate from the key half.** They fail differently. A
key mistake changes MEMBERSHIP; a title mistake changes IDENTITY — and identity
is `(library, title)`, the uniqueness invariant `managed_collections` is keyed
on and the thing `_titles_must_not_collide` cannot check for a dynamic family
(C4b: the row-135/162 gap widens per type, is documented per type, and the
reconciler's run-time ownership check is the backstop). A reviewer can
meaningfully accept one and reject the other.

**The three deliberate divergences from upstream, all in the same direction.**
Upstream logs and continues in three places; this service refuses, because each
of the three is a setting that reads as applied and is not:

| Upstream | meta.py | Here |
| --- | --- | --- |
| a `title_format` with neither `<<key_name>>` nor `<<title>>` is silently reverted to the type default | `:1234-1236` | refused at load, naming the rule |
| two `key_name_override` values that are equal pop a key mid-iteration (a `RuntimeError` landmine in CPython 3) | `:1251-1257` | refused at load, naming both keys |
| a duplicate generated title is warned and skipped, and the `other` collection bypasses the check entirely | `:1417-1418`, `:1455` | refused, naming both keys and the title — **including** for `other`, which is exactly the "Not Rated Movies" collision `p4c-task-5-review.md:131-134` predicted against `buckets.py:66` (C6) |

- [ ] **Step 1: Append the titles half to the oracle driver**

In `tests/oracle/10a/kometa_dynamic.py`, after `derive` and before `KEY_CASES`:

```python
def strdict(value):
    """modules/util.py:961 -- ``strdict``: keys AND values both ``str()``."""
    if not value:
        return {}
    return {str(k): str(v) for k, v in value.items()}


def commalist(value):
    """modules/util.py -- ``commalist``: comma-split, and NOT str()-coerced.

    The one option of the six that is not coerced, which is upstream's own
    asymmetry and is why a numeric prefix does not strip.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [part.strip() for part in str(value).split(",")]


def key_name_and_title(key, value, *, library_type, title_format,
                       key_name_override=None, title_override=None,
                       remove_prefix=None, remove_suffix=None):
    """meta.py:1268-1273 (the library-type substitution), :1352-1361 (the key
    name) and :1382-1394 (the title).

    ``library_type`` is Kometa's ``library.type``: ``<<library_type>>`` is its
    lowercase form and ``<<library_typeU>>`` is it unchanged (meta.py:1269-1270).
    """
    key_name_override = strdict(key_name_override)
    title_override = strdict(title_override)

    if key in key_name_override:                                   # :1352-1353
        key_name = key_name_override[key]
    else:
        key_name = value
        for prefix in commalist(remove_prefix):                    # :1356-1358
            if key_name.startswith(prefix):
                key_name = key_name[len(prefix):].strip()
        for suffix in commalist(remove_suffix):                    # :1359-1361
            if key_name.endswith(suffix):
                key_name = key_name[:-len(suffix)].strip()

    if key in title_override:                                      # :1382-1383
        return {"key_name": key_name, "title": title_override[key]}

    # :1269-1272 -- typeU FIRST: replacing ``<<library_type>>`` first would
    # eat the prefix of ``<<library_typeU>>`` and leave a stray ``U>>``.
    title = title_format.replace("<<library_typeU>>", library_type)
    title = title.replace("<<library_type>>", library_type.lower())
    title = title.replace("<<title>>", key_name).replace("<<key_name>>", key_name)
    return {"key_name": key_name, "title": title}
```

and append the cases plus the extended `main`:

```python
TITLE_CASES = [
    # 1. The default shape every tag type gets (meta.py:959).
    ("general-default", "Horror", "Horror", "Movie",
     "Top <<key_name>> <<library_type>>s", {}),
    # 2. The show wording of the same, which is the only thing the two
    #    library-type tokens can get wrong.
    ("general-default-show", "Drama", "Drama", "Show",
     "Top <<key_name>> <<library_type>>s", {}),
    # 3. Movie decade: the key and the display value differ (meta.py:933).
    ("decade", "1980", "1980s", "Movie",
     "Best <<library_type>>s of the <<key_name>>", {}),
    # 4. Both library-type tokens in one format -- the CS certification shape
    #    (``<<key_name>> <<library_typeU>>s``) beside the lowercase one.
    ("both-library-type-tokens", "5", "5", "Movie",
     "<<key_name>> <<library_typeU>>s for a <<library_type>> library", {}),
    # 5. ``key_name_override`` rewrites the name BEFORE the format, and
    #    SUPPRESSES the prefix/suffix strip -- the strip is the else branch
    #    (meta.py:1354-1361).
    ("key-name-override-suppresses-the-strip", "BBC One", "BBC One", "Show",
     "Top <<key_name>> <<library_type>>s",
     {"key_name_override": {"BBC One": "the BBC"}, "remove_prefix": ["BBC "]}),
    # 6. Prefixes then suffixes, each stripped in sequence and each ``.strip()``ed.
    ("prefix-and-suffix", "The Studio Ltd", "The Studio Ltd", "Movie",
     "Top <<key_name>> <<library_type>>s",
     {"remove_prefix": ["The "], "remove_suffix": [" Ltd"]}),
    # 7. ``title_override`` replaces the finished title outright, and the
    #    format is never applied to it.
    ("title-override", "R", "R", "Movie", "Top <<key_name>> <<library_type>>s",
     {"title_override": {"R": "Grown-Up Movies"}}),
    # 8. ``<<title>>`` is the other accepted token for the same value
    #    (meta.py:1384-1394).
    ("title-token", "1990", "1990s", "Movie", "<<title>> Cinema", {}),
]


def main():
    for index, (name, pairs, options) in enumerate(KEY_CASES, start=1):
        print("K%d %s %s" % (index, name, json.dumps(derive(pairs, **options))))
    for index, case in enumerate(TITLE_CASES, start=1):
        name, key, value, library_type, title_format, options = case
        print("T%d %s %s" % (index, name, json.dumps(key_name_and_title(
            key, value, library_type=library_type,
            title_format=title_format, **options,
        ))))
```

Note the `main` change renumbers the key lines with a `K` prefix — update the
Task 3 log reference in the task report, not the pinned goldens, which are
unchanged.

- [ ] **Step 2: Run the driver and capture the eight title answers**

```bash
docker compose -p p10at4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm --no-deps test sh -c 'cd /app/tests/oracle/10a && python kometa_dynamic.py 2>&1 | tee /app/.superpowers/run-t4-oracle.log; echo EXIT=$?'
```

Verify the transcription against the source if the network is reachable:

```bash
curl -sfL https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/modules/meta.py -o /tmp/meta.py \
    && sed -n '1234,1257p;1268,1273p;1290,1299p;1352,1361p;1382,1418p' /tmp/meta.py
```

Record the verification (or its impossibility) in the task report.

- [ ] **Step 3: Pin the title cases in the oracle test, and watch them fail**

Append to `tests/test_collection_dynamic_oracle.py`:

```python
# --- the title half ----------------------------------------------------------

TITLE_CASES = [
    ("general-default", "Horror", "Horror", "Movie",
     "Top <<key_name>> <<library_type>>s", {}),
    ("general-default-show", "Drama", "Drama", "Show",
     "Top <<key_name>> <<library_type>>s", {}),
    ("decade", "1980", "1980s", "Movie",
     "Best <<library_type>>s of the <<key_name>>", {}),
    ("both-library-type-tokens", "5", "5", "Movie",
     "<<key_name>> <<library_typeU>>s for a <<library_type>> library", {}),
    ("key-name-override-suppresses-the-strip", "BBC One", "BBC One", "Show",
     "Top <<key_name>> <<library_type>>s",
     {"key_name_override": {"BBC One": "the BBC"}, "remove_prefix": ["BBC "]}),
    ("prefix-and-suffix", "The Studio Ltd", "The Studio Ltd", "Movie",
     "Top <<key_name>> <<library_type>>s",
     {"remove_prefix": ["The "], "remove_suffix": [" Ltd"]}),
    ("title-override", "R", "R", "Movie", "Top <<key_name>> <<library_type>>s",
     {"title_override": {"R": "Grown-Up Movies"}}),
    ("title-token", "1990", "1990s", "Movie", "<<title>> Cinema", {}),
]

# KOMETA'S OWN ANSWERS for the titles, pinned as data. Same driver, same run.
KOMETA_TITLES = {
    "general-default": {...},                          # <- paste T1's JSON
    "general-default-show": {...},                     # <- T2
    "decade": {...},                                   # <- T3
    "both-library-type-tokens": {...},                 # <- T4
    "key-name-override-suppresses-the-strip": {...},   # <- T5
    "prefix-and-suffix": {...},                        # <- T6
    "title-override": {...},                           # <- T7
    "title-token": {...},                              # <- T8
}


@pytest.mark.parametrize(
    ("name", "key", "value", "library_type", "title_format", "options"),
    TITLE_CASES, ids=[c[0] for c in TITLE_CASES],
)
def test_our_titles_are_kometas(name, key, value, library_type, title_format, options):
    from autoposter.collections.dynamic_keys import DerivedKeys, DynamicKey
    from autoposter.collections.dynamic_titles import family_titles

    derived = DerivedKeys(
        keys=(DynamicKey(key=key, value=value, values=(key,)),),
        other_keys=(), used_keys=(key,),
    )
    titled = family_titles(
        derived, library_type=library_type, title_format=title_format, **options
    )
    assert len(titled) == 1
    assert {"key_name": titled[0].key_name, "title": titled[0].title} == \
        KOMETA_TITLES[name]
```

and extend the driver-drift guard to cover both halves — replace
`test_the_driver_still_produces_the_pinned_key_answers`'s body's final loop with
a second loop after it:

```python
    for case, (pinned_name, pinned) in zip(
        driver.TITLE_CASES, KOMETA_TITLES.items(), strict=True
    ):
        name, key, value, library_type, title_format, options = case
        assert name == pinned_name
        assert driver.key_name_and_title(
            key, value, library_type=library_type,
            title_format=title_format, **options,
        ) == pinned, name
```

Run:

```bash
docker compose -p p10at4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_dynamic_oracle.py 2>&1 | tee /app/.superpowers/run-t4-red1.log; echo EXIT=$?'
```

Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.collections.dynamic_titles'`.

- [ ] **Step 4: Write `dynamic_titles.py`**

```python
"""What a dynamic family's collections are CALLED.

Membership is ``dynamic_keys``'; identity is here. The distinction matters
because a collection's identity in this service is ``(library, title)`` -- the
``managed_collections`` uniqueness constraint, the ownership check, and the
sweep's enumeration all key on it -- so a title mistake is not a cosmetic one:
two keys that title the same are one collection whose membership flips every
pass.

Upstream's order, unchanged (modules/meta.py):

1. ``key_name`` starts as the enumerated VALUE, not the key (:1352).
2. ``key_name_override[key]`` replaces it, and SUPPRESSES ``remove_prefix``/
   ``remove_suffix`` -- the strip is the ``else`` branch (:1354-1361).
3. Otherwise every matching prefix is stripped in sequence, then every matching
   suffix, each result ``.strip()``ed (:1356-1361).
4. ``title_override[key]`` is the finished title, verbatim, and ``title_format``
   is never applied to it (:1382-1383).
5. Otherwise ``title_format`` has its two library-type tokens substituted, then
   ``<<title>>`` and ``<<key_name>>`` (:1269-1272, :1384-1394).

**Three places this REFUSES where upstream logs and continues**, each because
the upstream behaviour is a setting that reads as applied and is not:

- a ``title_format`` naming neither ``<<key_name>>`` nor ``<<title>>`` is
  reverted to the default upstream (:1234-1236), which titles every collection
  in the family identically and then skips all but the first as duplicates;
- two ``key_name_override`` values that are equal pop a key while iterating
  upstream (:1251-1257) -- a ``RuntimeError`` in CPython 3, not a graceful skip;
- a duplicate generated title is warned and skipped upstream (:1417-1418), and
  the ``other`` collection bypasses the check entirely (:1455). The second is
  the collision ``p4c-task-5-review.md:131-134`` predicted against
  ``buckets.py:66`` -- "Not Rated Movies" from an ``other_name`` landing on the
  Common Sense family's own catch-all -- so it is refused here rather than
  reproduced (decision C6).

The first two refuse at CONFIG LOAD, in the builder's params model, where an
operator learns at the moment of the edit; the third can only be known once the
library has been enumerated, so it refuses the definition's pass and names both
keys.

Pure: no Plex, no config, no I/O.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from autoposter.collections.dynamic_keys import DerivedKeys, DynamicKey

__all__ = [
    "DuplicateFamilyTitle",
    "TitledKey",
    "family_titles",
    "key_name_for",
    "render_title",
    "title_format_names_the_key",
]

# The literal ``key`` upstream gives the ``other`` bucket (meta.py:1435), so
# ``title_override``/``key_name_override`` can never reach it -- their keys are
# library values. Kept as the same string for the same reason.
OTHER_KEY = "other"


class DuplicateFamilyTitle(Exception):
    """Two keys in one family title the same collection.

    Its own class so the builder can turn it into one definition's refusal
    rather than a dead pass, and so the message can name both keys: an operator
    with a twelve-key family needs to know which two, not that there was a
    clash.
    """


@dataclass(frozen=True)
class TitledKey:
    """One collection-to-be, named.

    ``values`` rides along from ``DynamicKey`` unchanged so the builder has one
    object per collection rather than two lists it has to keep in step.
    """

    key: str
    key_name: str
    title: str
    values: tuple[str, ...]


def title_format_names_the_key(title_format: str) -> bool:
    """meta.py:1234-1236's condition, as a predicate the config layer can ask.

    Upstream reverts a format that fails this to the type's default and logs;
    the builder's params model refuses it instead, naming the rule -- because
    the reverted family builds fine, under names the operator did not write.
    """
    return "<<key_name>>" in title_format or "<<title>>" in title_format


def _strdict(value: object) -> dict[str, str]:
    """Kometa's ``strdict`` (modules/util.py:961): keys AND values ``str()``."""
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("expected a mapping of key -> replacement, not %r" % (value,))
    return {str(k): str(v) for k, v in value.items()}


def _affixes(value: object) -> list[str]:
    """Kometa's ``commalist`` (modules/util.py), and deliberately NOT
    ``str()``-coerced: that asymmetry is upstream's own, and coercing here
    would make ``remove_prefix: 20`` strip the digits out of a year's name."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",")]
    if not isinstance(value, Iterable):
        raise TypeError("expected a list of affixes, not %r" % (value,))
    return [one for one in value]


def key_name_for(
    key: str,
    value: str,
    *,
    key_name_override: object = None,
    remove_prefix: object = (),
    remove_suffix: object = (),
) -> str:
    """meta.py:1352-1361. The override wins outright and suppresses the strip."""
    overrides = _strdict(key_name_override)
    if key in overrides:
        return overrides[key]
    name = value
    for prefix in _affixes(remove_prefix):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
    for suffix in _affixes(remove_suffix):
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


def render_title(title_format: str, key_name: str, library_type: str) -> str:
    """meta.py:1269-1272 and :1384-1394.

    ``<<library_typeU>>`` is substituted FIRST and that is load-bearing, not
    tidiness: replacing ``<<library_type>>`` first would consume the prefix of
    the longer token and leave a stray ``U>>`` in every title.
    """
    rendered = title_format.replace("<<library_typeU>>", library_type)
    rendered = rendered.replace("<<library_type>>", library_type.lower())
    return rendered.replace("<<title>>", key_name).replace("<<key_name>>", key_name)


def family_titles(
    derived: DerivedKeys,
    *,
    library_type: str,
    title_format: str,
    key_name_override: object = None,
    title_override: object = None,
    remove_prefix: object = (),
    remove_suffix: object = (),
    other_name: str | None = None,
) -> tuple[TitledKey, ...]:
    """Every collection this family builds, named, in the family's own order.

    ``other_name`` is the leftovers bucket and the caller passes it only when
    an ``include`` list exists -- upstream gates it the same way
    (meta.py:1301-1311), and without an ``include`` there are no leftovers for
    it to hold. Its key is the literal ``"other"``, so neither override table
    can reach it, and its VALUES are the leftover keys themselves.

    Raises ``DuplicateFamilyTitle`` if two keys name one collection, the
    ``other`` bucket included -- upstream warns and skips, and exempts ``other``
    from even that (decision C6, and the module docstring says why).
    """
    titles = _strdict(title_override)
    titled: list[TitledKey] = []
    seen: dict[str, str] = {}

    units: list[tuple[DynamicKey, str]] = [
        (unit, titles.get(unit.key) or "") for unit in derived.keys
    ]
    if other_name and derived.other_keys:
        units.append((
            DynamicKey(key=OTHER_KEY, value=OTHER_KEY, values=derived.other_keys),
            render_title(other_name, OTHER_KEY, library_type)
            if False else _other_title(other_name, library_type),
        ))

    for unit, overridden in units:
        if unit.key == OTHER_KEY:
            key_name, title = OTHER_KEY, overridden
        else:
            key_name = key_name_for(
                unit.key, unit.value,
                key_name_override=key_name_override,
                remove_prefix=remove_prefix,
                remove_suffix=remove_suffix,
            )
            title = overridden or render_title(title_format, key_name, library_type)
        if title in seen:
            raise DuplicateFamilyTitle(
                "%r and %r both name a collection %r. One collection cannot be "
                "two keys' -- its membership would flip every pass -- so this "
                "definition builds nothing until one of them is renamed with "
                "`title_override`, or one is dropped with `exclude`. (Kometa "
                "warns and skips the second here, modules/meta.py:1417-1418, "
                "and does not check the `other` collection at all, :1455.)"
                % (seen[title], unit.key, title)
            )
        seen[title] = unit.key
        titled.append(TitledKey(
            key=unit.key, key_name=key_name, title=title, values=unit.values,
        ))
    return tuple(titled)


def _other_title(other_name: str, library_type: str) -> str:
    """meta.py:1301-1310: the ``other`` name gets the same library-type
    substitution ``title_format`` gets, and no key-name substitution at all --
    its key is the literal ``"other"``, which is not a name anybody wants in a
    title."""
    rendered = other_name.replace("<<library_typeU>>", library_type)
    return rendered.replace("<<library_type>>", library_type.lower())
```

**Simplify while writing it:** the `render_title(...) if False else ...`
expression above is a plan-writing artefact — write the call as
`_other_title(other_name, library_type)` only. It is spelled out here so the
intent is unambiguous: the `other` title never goes through `render_title`,
because there is no key name to substitute.

- [ ] **Step 5: Run the title oracle green**

```bash
docker compose -p p10at4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_dynamic_oracle.py 2>&1 | tee /app/.superpowers/run-t4-green1.log; echo EXIT=$?'
```

Expected: PASS, sixteen parametrised cases (eight keys, eight titles) plus the
structural tests. If a title case is RED, ours is wrong.

- [ ] **Step 6: Add the unit surface — the refusals and the `other` bucket**

`tests/test_collection_dynamic_titles.py`:

```python
"""``family_titles``: identity, and the three places we refuse where Kometa logs.

The oracle next door proves the NAMES. These prove what happens when two of
them collide, and what the ``other`` bucket is.
"""
import pytest

from autoposter.collections.dynamic_keys import DerivedKeys, DynamicKey
from autoposter.collections.dynamic_titles import (
    DuplicateFamilyTitle,
    family_titles,
    render_title,
    title_format_names_the_key,
)

FORMAT = "Top <<key_name>> <<library_type>>s"


def _derived(*pairs, other=()):
    return DerivedKeys(
        keys=tuple(DynamicKey(key=k, value=v, values=(k,)) for k, v in pairs),
        other_keys=tuple(other),
        used_keys=tuple(k for k, _ in pairs),
    )


def test_two_keys_that_title_the_same_collection_refuse_and_name_both():
    """Kometa warns and skips the second (meta.py:1417-1418). Skipping means
    one of the two keys silently has no collection -- and which one depends on
    the order Plex reported its values, so the same config builds different
    families on different libraries. Refusing names both keys and the title."""
    derived = _derived(("Sci-Fi", "Sci-Fi"), ("SciFi", "SciFi"))
    with pytest.raises(DuplicateFamilyTitle) as refusal:
        family_titles(
            derived, library_type="Movie", title_format=FORMAT,
            key_name_override={"Sci-Fi": "Science Fiction", "SciFi": "Science Fiction"},
        )
    assert "Sci-Fi" in str(refusal.value)
    assert "SciFi" in str(refusal.value)
    assert "Top Science Fiction movies" in str(refusal.value)


def test_the_other_collection_is_held_to_the_same_collision_rule():
    """Upstream writes the ``other`` collection unguarded (meta.py:1455), which
    is exactly how Kometa's ``other_name: Not Rated <<library_typeU>>s`` lands
    on the Common Sense family's own 'Not Rated Movies'
    (``collections/buckets.py:66``) -- predicted in
    ``p4c-task-5-review.md:131-134`` and refused here (decision C6)."""
    derived = _derived(("NR", "NR"), other=["Unrated"])
    with pytest.raises(DuplicateFamilyTitle):
        family_titles(
            derived, library_type="Movie", title_format="<<key_name>>",
            title_override={"NR": "Not Rated Movies"},
            other_name="Not Rated <<library_typeU>>s",
        )


def test_the_other_bucket_holds_the_leftover_keys_and_is_titled_without_one():
    """Its key is the literal ``"other"`` (meta.py:1435), so no override table
    can reach it, and its title takes the library-type substitution and nothing
    else. Its VALUES are the leftovers, which is what makes it a set
    complement rather than a name."""
    derived = _derived(("G", "G"), other=["R", "NC-17"])
    titled = family_titles(
        derived, library_type="Movie", title_format=FORMAT,
        other_name="Everything Else, <<library_typeU>>s",
    )
    assert [one.title for one in titled] == [
        "Top G movies", "Everything Else, Movies",
    ]
    assert titled[-1].key == "other"
    assert titled[-1].values == ("R", "NC-17")


def test_no_other_collection_when_nothing_was_left_over():
    """meta.py:1432-1433 warns "Other Collection not needed" and does not create
    it. An empty ``other`` would be a smart filter with no terms, which
    ``build_search_url`` refuses and which would otherwise match the entire
    library (``reconcile.py:609-612``)."""
    derived = _derived(("G", "G"))
    titled = family_titles(
        derived, library_type="Movie", title_format=FORMAT, other_name="Leftovers",
    )
    assert [one.key for one in titled] == ["G"]


def test_the_library_type_tokens_substitute_in_the_order_that_survives_both():
    """``<<library_typeU>>`` first, or the shorter token eats its prefix and
    every title in the family ends up with a stray ``U>>``."""
    assert render_title("<<key_name>> <<library_typeU>>s in <<library_type>>", "5", "Movie") \
        == "5 Movies in movie"


def test_a_title_format_that_names_no_key_is_recognised_as_such():
    """The predicate the params model refuses on. Upstream reverts to the type
    default and logs (meta.py:1234-1236) -- which builds the family under names
    the operator did not write, and then skips all but the first as
    duplicates."""
    assert title_format_names_the_key("Top <<key_name>> <<library_type>>s")
    assert title_format_names_the_key("<<title>> Cinema")
    assert not title_format_names_the_key("Great <<library_type>>s")
```

- [ ] **Step 7: Run the unit tests green**

```bash
docker compose -p p10at4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_dynamic_titles.py tests/test_collection_dynamic_oracle.py 2>&1 | tee /app/.superpowers/run-t4-green2.log; echo EXIT=$?'
```

Expected: PASS.

- [ ] **Step 8: Mutation proof**

```bash
cp src/autoposter/collections/dynamic_titles.py /tmp/dynamic_titles.py.bak
```

Mutation A — in `render_title`, swap the two library-type replacements so
`<<library_type>>` goes first. Expected RED: the oracle's
`both-library-type-tokens` case and
`test_the_library_type_tokens_substitute_in_the_order_that_survives_both`.

Mutation B — restore, `cmp`, then in `key_name_for` move the prefix/suffix strip
above the override check (so an override is stripped too). Expected RED: the
oracle's `key-name-override-suppresses-the-strip` case.

Mutation C — restore, `cmp`, then replace the `raise DuplicateFamilyTitle` with
`continue`. Expected RED:
`test_two_keys_that_title_the_same_collection_refuse_and_name_both` and
`test_the_other_collection_is_held_to_the_same_collision_rule`.

Restore, `cmp`, GREEN. Paste all three RED outputs.

- [ ] **Step 9: Full suite, golden, ruff, commit**

```bash
docker compose -p p10at4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10at4-full test sh -c 'timeout -s KILL 1800 pytest -q 2>&1 | tee /app/.superpowers/run-t4-full.log; echo EXIT=$?'
docker wait p10at4-full
docker logs p10at4-full | tail -40
docker rm p10at4-full
docker compose -p p10at4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10at4 down
```

```bash
git add tests/oracle/10a/kometa_dynamic.py tests/test_collection_dynamic_oracle.py \
        src/autoposter/collections/dynamic_titles.py tests/test_collection_dynamic_titles.py
git commit --no-gpg-sign -m "feat(collections): dynamic title derivation, proven against Kometa

title_format, key_name_override (which suppresses remove_prefix/suffix),
title_override and the other bucket, in Kometa v2.4.8's own order
(meta.py:1352-1361, :1382-1394), pinned as eight more oracle goldens. Three
upstream log-and-continue paths refuse here instead, the other-collection
duplicate-title bypass among them."
```

---

### Task 5: The `dynamic` builder

**Files:**

- Create: `src/autoposter/collections/builders/dynamic.py`
- Create: `tests/test_builder_dynamic.py`
- Modify: `src/autoposter/collections/builders/__init__.py` (import + `register`)
- Modify: `src/autoposter/collections/engine.py` (`_sweep`: recognise a dynamic family's label and report rather than consider)
- Modify: `tests/test_builder_engine.py` (that sweep behaviour)
- Modify: `config/autoposter.example.yaml` (the `definitions:` example block)

**Interfaces:**

- Consumes:
  - `dynamic_types.DYNAMIC_TYPES` (Task 2) — `.attribute`, `.search_key`, `.kinds`, `.key_from`, `.title_format`, `.sort_by`, `.limit`.
  - `LibraryTagResolver(ctx, section, libtype)` with `.choices(attribute)` (Task 2) and `__call__(attribute, value)` (9b, used as `resolve_tag`).
  - `dynamic_keys.derive_keys` (Task 3), `dynamic_titles.family_titles` / `DuplicateFamilyTitle` / `title_format_names_the_key` (Task 4).
  - `parse_filters(block, field=..., searching=True, base="any")`, `build_search_url(group, libtype=..., sort_by=..., limit=..., resolve_tag=...)`.
  - `smart.reconcile_smart_collection(session, section, library, library_type, title, url, label, summary=..., dry_run=..., existing=..., adopt=..., adopt_from=..., adopt_removes_prior_label=..., protect_labels=..., http=..., config=..., settings=...) -> list[str]`.
  - `SmartContext` (`session`, `section`, `library`, `library_type`, `label`, `config`, `http`, `dry_run`, `definition`, `run_cache`, `listing`).
- Produces:
  - `builders.dynamic.DynamicBuilder` — `type_name = "dynamic"`, `smart = True`, `params_model = DynamicParams`, `refused_definition_fields`, `async apply(ctx) -> list[str]`, `family_label(definition) -> str`. **No `titles()` method** — see the note below.
  - `builders.dynamic.FAMILY_LABEL_PREFIX` and the module-level `family_label(definition)`.
  - `engine._family_labels(definitions, library) -> set[str]`.

**Two design points a reviewer will ask about, decided here.**

**(a) Why no `titles()`.** `engine.definition_titles` offers a smart builder a
`titles(library_type, config)` hook, and `cs_bucket` uses it because its titles
come from a static table with no Plex round trip. A dynamic family's titles are
the LIBRARY's — enumerating them offline is impossible, and enumerating them
online would put a Plex call inside `_titles_must_not_collide`, which runs on
every config write (`config/schema.py:760-819`). So the builder declares none and
`definition_titles` falls through to `{definition.title}` (9c decision C6),
which means the placeholder's own title. Two consequences, both deliberate and
both documented in the module docstring:

- the placeholder's title is reserved in the collision check although no
  collection is ever created under it — the same reservation an expanding
  builder's placeholder gets;
- the family's real titles are invisible to the sweep, which is exactly why (b)
  exists. This is the row-135/162 gap widening per dynamic type (C4b); the
  reconciler's own ownership check (`resolve_collision`) is the run-time
  backstop, and it already refuses to touch a collection carrying somebody
  else's label.

**(b) Why the engine's sweep must learn about the family in this task.** The
sweep deletes what it owns and cannot enumerate (`engine.py:696-698`, `:710-739`).
Family members carry the ownership label and get `managed_collections` rows, and
no definition's title set contains them — so without this change the FIRST
sweep-enabled pass after a family is created would treat every one of them as an
orphan. 10a-2 gives the family its own sweep; until then the engine REPORTS them
and never considers them for deletion. Reporting rather than silently skipping
is the honest half: an operator who narrows `include:` sees the leftovers named,
with the phase that will remove them.

- [ ] **Step 1: Write the failing builder tests**

`tests/test_builder_dynamic.py`:

```python
"""``dynamic``: one smart collection per distinct value the library holds.

The engine ``collections/buckets.py`` is one hardcoded instance of. It
enumerates through ``LibraryTagResolver.choices``, derives its keys and titles
with the two pure modules beside it, and writes each collection through the SAME
reconciler ``smart_filter`` uses -- so there is one write path, one query
grammar and one drift hash for every smart collection this service manages
(10a decision C1).
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import SmartContext
from autoposter.collections.builders.dynamic import (
    DynamicBuilder,
    DynamicParams,
    family_label,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"
SECTION_KEY = "2"


class FakeChoice:
    def __init__(self, key, title):
        self.key = key
        self.title = title


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeServer:
    def __init__(self):
        self.queries = []
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://abc123/com.plexapp.plugins.library"

    def query(self, key, method=None, **kwargs):
        self.queries.append((key, method))


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self.smart = True
        self.summary = None
        self.titleSort = None
        self.collectionMode = None
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": False})()]
        self._fields = []
        self.labels_added = []
        self._server = FakeServer()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return self._fields

    def reload(self, **kw):
        self._labels = self._real_labels
        self._fields = self._real_fields

    def addLabel(self, label, locked=True):
        self.labels_added.append(label)
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def editSortTitle(self, value, locked=True):
        self.titleSort = value

    def query(self, key, method=None, **kwargs):
        self._server.query(key, method)
        self._real_fields[0].locked = True


class FakeSection:
    """Answers ``listFilterChoices`` with one vocabulary and every search with
    three items, which is all a family needs to be created."""

    def __init__(self, choices=None, section_type="movie"):
        self.key = SECTION_KEY
        self.type = section_type
        self._server = FakeServer()
        self._existing = {}
        self._choices = choices if choices is not None else [
            FakeChoice("1138", "Horror"), FakeChoice("9", "Drama"),
        ]
        self.choice_calls = []
        self.fetched = []

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        if title not in self._existing:
            self._existing[title] = FakeCollection(title)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        self.fetched.append(path)
        return [FakeItem("1"), FakeItem("2"), FakeItem("3")]

    def listFilterChoices(self, field, libtype=None):
        self.choice_calls.append((field, libtype))
        return list(self._choices)


def _config(**overrides):
    options = {
        "adopt": False, "adopt_from": [], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "separators": False,
        "ownership_label": LABEL,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


def _ctx(session, section, definition, *, dry_run=False, library_type="Movie",
         config=None, listing=None):
    return SmartContext(
        session=session, section=section, library="Movies",
        library_type=library_type, label=LABEL, config=config or _config(),
        http=None, dry_run=dry_run, definition=definition, run_cache={},
        listing=listing,
    )


def _definition(**overrides):
    options = {
        "title": "Genres",
        "builder": "dynamic",
        "params": {"type": "genre"},
    }
    options.update(overrides)
    return CollectionDefinition(**options)


# --- registration and the protocol -------------------------------------------


def test_the_builder_is_registered_as_a_smart_builder():
    builder = REGISTRY["dynamic"]
    assert builder.smart is True
    assert isinstance(builder, DynamicBuilder)
    assert not hasattr(builder, "titles"), (
        "a dynamic family's titles are the LIBRARY's -- enumerating them "
        "offline is impossible and enumerating them online would put a Plex "
        "call inside _titles_must_not_collide, which runs on every config write"
    )


# --- the params, at config load ----------------------------------------------


def test_an_unknown_type_refuses_at_load_and_lists_the_ones_that_exist():
    with pytest.raises(ValueError) as refusal:
        _definition(params={"type": "edition"})
    assert "edition" in str(refusal.value)
    assert "genre" in str(refusal.value)


@pytest.mark.parametrize("key", ["test", "data", "sync", "other_template",
                                 "template", "template_variables"])
def test_the_dead_upstream_knobs_refuse_by_name_with_the_reason(key):
    """C5, plus the two this phase cannot mean anything by. Every one of them
    is a key Kometa accepts, so an operator porting a config meets it -- and
    accepting it silently is the failure the refusal table exists to prevent."""
    with pytest.raises(ValueError) as refusal:
        _definition(params={"type": "genre", key: True})
    assert key in str(refusal.value)


def test_a_title_format_that_names_no_key_refuses_at_load():
    """Upstream reverts it to the default and logs (meta.py:1234-1236), which
    builds the family under names the operator did not write."""
    with pytest.raises(ValueError, match="key_name"):
        _definition(params={"type": "genre", "title_format": "Great <<library_type>>s"})


def test_two_key_name_overrides_with_the_same_value_refuse_at_load():
    """Upstream pops a key while iterating (meta.py:1251-1257), which is a
    RuntimeError in CPython 3 rather than the graceful skip it reads as."""
    with pytest.raises(ValueError) as refusal:
        _definition(params={
            "type": "genre",
            "key_name_override": {"Sci-Fi": "Science Fiction", "SciFi": "Science Fiction"},
        })
    assert "Science Fiction" in str(refusal.value)


def test_an_unknown_sort_refuses_at_load():
    with pytest.raises(ValueError, match="not a Plex sort"):
        _definition(params={"type": "genre", "sort_by": ["nonsense.desc"]})


def test_the_definition_fields_a_family_cannot_apply_refuse_at_load():
    """Every one of the seven ``cs_bucket`` refuses, for the same reasons: this
    definition names a FAMILY, and Plex owns each member's membership."""
    for field, value in [
        ("summary", "one summary"), ("sort", "alpha"), ("limit", 5),
        ("sync_mode", "append"), ("item_label", ["x"]), ("tmdb_summary", 10),
        ("filters", {"year.gte": 2000}),
    ]:
        with pytest.raises(ValueError, match=field):
            _definition(**{field: value})


# --- what it builds -----------------------------------------------------------


async def test_it_creates_one_smart_collection_per_enumerated_value(session):
    section = FakeSection()
    definition = _definition()
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    assert sorted(section._existing) == ["Top Drama movies", "Top Horror movies"]
    assert any("created 'Top Horror movies'" in one for one in actions)
    # One listFilterChoices for the whole family, not one per key.
    assert section.choice_calls == [("genre", "movie")]


async def test_each_collection_asks_plex_for_its_own_value_under_an_any_base(session):
    """The emitted query is the 9b grammar and the base is ``any:`` -- which is
    upstream's (meta.py:950) and is what makes a bucket's several values an OR
    rather than the ``all:`` AND that would match nothing (10a decision C2)."""
    section = FakeSection()
    definition = _definition()
    await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    posted = [key for key, _ in section._server.queries]
    assert any("genre%3D1138" in one or "genre=1138" in one for one in posted) or \
        any("genre=1138" in one for one in section.fetched)
    assert all("push=1" in one for one in section.fetched)


async def test_a_family_labels_every_collection_it_creates(session):
    """C4's mechanism: family membership is a LABEL, which is Kometa's own
    handle for the same job (``append_label: str(map_name)``, meta.py:1421) and
    is what 10a-2's sweep will enumerate. Never an offline title list."""
    section = FakeSection()
    definition = _definition()
    await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    for collection in section._existing.values():
        assert family_label(definition) in collection.labels_added
        assert LABEL in collection.labels_added


async def test_the_family_writes_one_managed_row_per_collection(session):
    section = FakeSection()
    definition = _definition()
    await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert sorted(row.title for row in rows) == [
        "Top Drama movies", "Top Horror movies",
    ]
    assert {row.kind for row in rows} == {"smart"}


async def test_an_unchanged_second_pass_writes_nothing(session):
    section = FakeSection()
    definition = _definition()
    ctx = _ctx(session, section, definition)
    await REGISTRY["dynamic"].apply(ctx)
    before = len(section._server.queries)
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    assert len(section._server.queries) == before
    assert actions == []


async def test_a_dry_run_reports_the_whole_family_and_writes_nothing(session):
    section = FakeSection()
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(), dry_run=True)
    )
    assert len(actions) == 2
    assert all(one.startswith("would create") for one in actions)
    assert section._existing == {}


# --- the refusals, which RETURN ----------------------------------------------


async def test_a_library_type_the_type_does_not_serve_refuses_without_enumerating(session):
    """``network`` is show-only upstream (meta.py:19). The refusal returns, and
    it costs zero Plex round trips -- the gate is above the enumeration."""
    section = FakeSection()
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(params={"type": "network"}))
    )
    assert len(actions) == 1
    assert "refused" in actions[0]
    assert section.choice_calls == []


async def test_a_type_that_enumerates_nothing_refuses_rather_than_building_one(session):
    """The failure ``test_the_three_presets_9b_readjudicated_stay_gated_on_the_engine_row``
    is about: a per-value family with no values must not quietly build one
    collection named after nothing."""
    section = FakeSection(choices=[])
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition())
    )
    assert len(actions) == 1
    assert "enumerate" in actions[0]
    assert section._existing == {}


async def test_a_dead_filter_lookup_refuses_class_name_only(session):
    """The resolver wraps both Plex failure classes with the class name and
    nothing else, because either can carry a tokenised URL. The refusal
    RETURNS -- nothing raises through the engine's unwrapped smart dispatch."""
    from plexapi.exceptions import BadRequest

    class Refusing(FakeSection):
        def listFilterChoices(self, field, libtype=None):
            self.choice_calls.append((field, libtype))
            raise BadRequest("bad request; https://plex.example/library?X-Plex-Token=SECRET")

    section = Refusing()
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition()))
    assert len(actions) == 1
    assert "BadRequest" in actions[0]
    assert "SECRET" not in actions[0]
    assert "X-Plex-Token" not in actions[0]


async def test_a_fan_out_past_the_cap_refuses_with_both_numbers(session):
    """C8's refuse-over-surprise floor. A 70-year library is 70 collections;
    the operator raises the cap deliberately or narrows the family, and either
    way nothing is created behind their back."""
    section = FakeSection(choices=[FakeChoice(str(n), str(n)) for n in range(60)])
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(params={"type": "genre"}))
    )
    assert len(actions) == 1
    assert "60" in actions[0] and "50" in actions[0]
    assert "max_collections" in actions[0]
    assert section._existing == {}


async def test_raising_the_cap_lets_the_same_family_build(session):
    section = FakeSection(choices=[FakeChoice(str(n), str(n)) for n in range(60)])
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(
            params={"type": "genre", "max_collections": 100}
        ))
    )
    assert len(section._existing) == 60
    assert len(actions) == 60


async def test_two_keys_that_title_the_same_collection_refuse_the_whole_family(session):
    section = FakeSection(choices=[FakeChoice("1", "Sci-Fi"), FakeChoice("2", "SciFi")])
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        params={
            "type": "genre",
            "key_name_override": {"Sci-Fi": "Science Fiction"},
            "title_override": {"SciFi": "Top Science Fiction movies"},
        },
    )))
    assert len(actions) == 1
    assert "Sci-Fi" in actions[0] and "SciFi" in actions[0]
    assert section._existing == {}


async def test_one_key_whose_value_plex_cannot_resolve_costs_only_that_key(session):
    """A per-key refusal is contained to its key: the rest of the family is
    still built, because one unresolvable value is not a reason to stop
    managing eleven working collections."""

    class HalfResolving(FakeSection):
        def listFilterChoices(self, field, libtype=None):
            self.choice_calls.append((field, libtype))
            return [FakeChoice("1138", "Horror"), FakeChoice("9", "Drama")]

    section = HalfResolving()
    definition = _definition(params={
        "type": "genre", "addons": {"Horror": ["Nothing The Library Has"]},
    })
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, definition))
    assert sorted(section._existing) == ["Top Drama movies", "Top Horror movies"]
```

- [ ] **Step 2: Run them and watch them fail**

```bash
docker compose -p p10at5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_dynamic.py 2>&1 | tee /app/.superpowers/run-t5-red1.log; echo EXIT=$?'
```

Expected: FAIL — `ModuleNotFoundError: No module named 'autoposter.collections.builders.dynamic'`.

- [ ] **Step 3: Write the builder**

`src/autoposter/collections/builders/dynamic.py`:

```python
"""``dynamic``: one smart collection per distinct value the library holds.

The generic engine ``collections/buckets.py`` is one hardcoded instance of, and
the third smart builder. Its siblings mark the two shapes it sits between:
``cs_bucket`` manages a FAMILY whose titles come from a static table, and
``smart_filter`` manages exactly ONE collection whose query the operator wrote.
This one manages a family whose titles and queries are both derived from what
the LIBRARY turns out to hold.

**One write path, one grammar (decision C1).** Upstream's twelve tag/scan
dynamic types generate ``smart_filter`` collections created by a raw POST with
the ``build_filter`` query (meta.py:950, builder.py:1476-1478, plex.py:1592-1600)
-- the grammar 9b's oracle transcribes and 9c's reconciler already writes. So
every collection here goes through ``smart.reconcile_smart_collection`` with a
``build_search_url`` string: no plexapi ``filters=``, no second translation
layer, no second drift hash.

**Four pieces, three of them pure.** ``dynamic_types`` says what to enumerate
and how to title it; ``LibraryTagResolver.choices`` does the one Plex read;
``dynamic_keys`` decides which keys become collections and what each asks for;
``dynamic_titles`` names them. This module is the seam between them and the
reconciler, and it holds exactly two decisions of its own: the fan-out cap and
the family label.

**The fan-out cap.** ``year`` on a seventy-year library is seventy collections,
which the roadmap files as this phase's own risk. Upstream has no cap at all. So
a family whose enumeration exceeds ``max_collections`` (default 50) refuses with
both numbers and creates nothing, rather than creating seventy collections an
operator then has to delete one at a time. Raising it is one key, and the
refusal says so.

**The family label.** Every collection this builder creates carries
``FAMILY_LABEL_PREFIX + definition.title`` beside the ownership label. That is
Kometa's own handle for the same job -- it labels each generated collection with
the map name (``append_label``, meta.py:1421) and its ``sync:`` sweep deletes
labelled collections no key regenerated (meta.py:1300, :1456-1461) -- and it is
what phase 10a-2's family sweep will enumerate, through this service's own
delete guards. Until then the engine's sweep REPORTS family members and never
considers them for deletion (``engine._sweep``).

**No ``titles()``, deliberately.** ``engine.definition_titles`` offers a smart
builder that hook, and ``cs_bucket`` uses it because its titles come from a
static table. A dynamic family's titles are the library's: offline enumeration
is impossible, and online enumeration would put a Plex call inside
``_titles_must_not_collide``, which runs on every config write
(``config/schema.py:760-819``). So this builder declares none, the engine falls
through to ``{definition.title}`` (9c decision C6), and two things follow --
the placeholder's title is reserved although no collection is created under it,
and the family's real titles are invisible to the leftovers report and the
sweep. That is roadmap rows 135/162's gap widening per dynamic type, recorded
here and answered at run time by the reconciler's own ownership check: a
collision with somebody else's collection is refused by ``resolve_collision``
rather than silently adopted.

**Every refusal RETURNS.** ``engine.py:341-344`` does not wrap a smart
builder's ``apply``, on the grounds that anything escaping it is a Plex WRITE
failing. So a library type this type cannot serve, a dead filter lookup, an
empty enumeration, an over-cap fan-out, a duplicate title and a value Plex
cannot resolve are all caught here and returned as action strings. A failing
label write still reaches the engine's per-library rollback, unchanged.
"""
import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoposter.collections.builders.base import (
    LibraryTypeMismatch,
    SmartContext,
    require_library_type,
)
from autoposter.collections.builders.plex_search import (
    LibraryTagResolver,
    PlexSearchUnavailable,
)
from autoposter.collections.dynamic_keys import derive_keys
from autoposter.collections.dynamic_titles import (
    DuplicateFamilyTitle,
    family_titles,
    title_format_names_the_key,
)
from autoposter.collections.dynamic_types import DYNAMIC_TYPES, DYNAMIC_TYPE_NAMES
from autoposter.collections.filters import parse_filters
from autoposter.collections.search_sorts import KNOWN_SORT_NAMES, SortNotAvailable
from autoposter.collections.search_url import (
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    TagValueNotFound,
    build_search_url,
)
from autoposter.collections.smart import (
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    reconcile_smart_collection,
)

logger = logging.getLogger(__name__)

__all__ = ["FAMILY_LABEL_PREFIX", "DynamicBuilder", "DynamicParams", "family_label"]

# The label every collection in one family carries, beside the ownership label.
# Prefixed rather than the bare definition title (which is what Kometa uses,
# meta.py:1421): a family called "Genres" would otherwise claim a plain
# ``Genres`` label an operator may already use for something else, and this
# label is a delete handle -- 10a-2's sweep reads it.
FAMILY_LABEL_PREFIX = "autoposter-dynamic: "

# Kometa's keys this builder refuses, each with the reason. Held as data so the
# refusals cannot drift apart in wording, and so adding one is a row rather than
# a branch -- ``plex_search._REFUSED_KEYS`` is the shape.
_REFUSED_KEYS: dict[str, str] = {
    "test": (
        "`test: true` configures NOTHING on a collection upstream -- it is in "
        "`ignored_details` (modules/builder.py:137) and the only thing that "
        "reads it is Kometa's own `--run-tests` CLI flag, which skips every "
        "collection WITHOUT the marker (kometa.py:1233-1248). Accepting it "
        "here would be a knob that does nothing. Preview a definition with the "
        "collections preview instead, which reports what a pass would do "
        "without writing"
    ),
    "data": (
        "`data:` is never read for any of Kometa's thirteen LIBRARY dynamic "
        "types (meta.py:23 makes it a valid key, and nothing parses it unless "
        "the type is one of the people, award, number, custom or list types "
        "this service does not ship). Accepting it would be a block an "
        "operator writes and nothing reads"
    ),
    "sync": (
        "`sync: true` upstream is a DELETE sweep -- it labels each generated "
        "collection and deletes labelled collections no key regenerated "
        "(meta.py:1300, :1456-1461) -- not a sync mode. This service labels the "
        "family already, and its own delete sweep for it ships in phase 10a-2, "
        "through the `delete_unconfigured` and `max_deletes` guards every other "
        "delete goes through. Until then this definition never deletes anything"
    ),
    "other_template": (
        "`other_template:` names another Kometa TEMPLATE for the leftovers "
        "collection (meta.py:1311-1317), and this service has no template "
        "system -- the leftovers collection is built by the same type query "
        "over the keys no `include:` entry named. Write `other_name:` for its "
        "title; there is nothing else about it to point elsewhere"
    ),
    "template": (
        "Kometa's dynamic engine emits nothing but variable bindings and puts "
        "the whole query in a `template:` (meta.py:1287-1289). Here the query "
        "IS the type, so there is no template to name"
    ),
    "template_variables": (
        "the same: this engine has no templates, so there are no variables to "
        "bind. The per-key knobs are `key_name_override`, `title_override` and "
        "`title_format`"
    ),
}

# Every refusal an operator's CONFIGURATION can cause once it meets a real
# library. A tuple so the per-key path has one catch rather than six, and named
# exhaustively rather than as ``Exception`` so a genuine bug in this module
# still reaches the engine as a failure instead of being reported to the
# operator as something about their family.
REFUSALS = (
    LibraryTypeMismatch,
    PlexSearchUnavailable,
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    SortNotAvailable,
    TagValueNotFound,
)


def family_label(definition) -> str:
    """The label every collection in ``definition``'s family carries."""
    return "%s%s" % (FAMILY_LABEL_PREFIX, definition.title)


class DynamicParams(BaseModel):
    """A dynamic family: which values to enumerate, and how to name them.

    ``type`` is the only required key. Everything else narrows the family
    (``include``/``exclude``/``addons``/``custom_keys``), names it
    (``title_format``, ``key_name_override``, ``title_override``,
    ``remove_prefix``/``remove_suffix``, ``other_name``) or bounds it
    (``sort_by``, ``limit``, ``max_collections``).

    ``extra="forbid"`` so ``includes:`` for ``include:`` is an error rather than
    a silently-ignored key, and ``coerce_numbers_to_str`` because certification
    tables are written with integer YAML keys upstream and an operator porting
    one writes ``include: [12, 16]``.
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
    sort_by: list[str] | None = None
    limit: int | None = Field(default=None, ge=1)
    # C8's refuse-over-surprise floor. 50 is chosen against the production
    # library: content ratings enumerate to ~7, decades to ~12, genres to ~25,
    # networks to 91 and years to ~70 -- so the two families that would create
    # more collections than an operator can hold in their head are exactly the
    # two that refuse until the cap is raised on purpose.
    max_collections: int = Field(default=50, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _the_refused_keys(cls, data: object) -> object:
        """Everything that has to beat ``extra="forbid"``.

        A ``mode="before"`` validator for ``plex_search``'s own reason: these
        are keys Kometa accepts, so an operator porting a config writes them,
        and pydantic's generic "Extra inputs are not permitted" would tell them
        nothing about WHY a key that works upstream does not work here. Matched
        exactly, never case-insensitively, so this validator's verdict and
        pydantic's field lookup agree on every input.
        """
        if not isinstance(data, dict):
            return data
        for refused, why in _REFUSED_KEYS.items():
            if refused in {str(key) for key in data}:
                raise ValueError("%r is not accepted here. %s" % (refused, why))
        return data

    @model_validator(mode="after")
    def _the_type_must_be_one_this_service_enumerates(self) -> "DynamicParams":
        if self.type not in DYNAMIC_TYPES:
            raise ValueError(
                "%r is not a dynamic type this service enumerates. Options: %s. "
                "Kometa has more, and each absence is a decision with a reason "
                "-- see `collections/dynamic_types.py`'s module docstring"
                % (self.type, ", ".join(DYNAMIC_TYPE_NAMES))
            )
        return self

    @model_validator(mode="after")
    def _a_title_format_must_name_the_key(self) -> "DynamicParams":
        """meta.py:1234-1236 reverts such a format to the type's default and
        logs. Reverting builds the whole family under names the operator did not
        write -- and then skips all but the first as duplicates."""
        if self.title_format is not None and not title_format_names_the_key(
            self.title_format
        ):
            raise ValueError(
                "`title_format` has to carry `<<key_name>>` (or `<<title>>`, "
                "which means the same value): without one, every collection in "
                "the family would be given the same name. Kometa silently "
                "reverts to its own default here (modules/meta.py:1234-1236)"
            )
        return self

    @model_validator(mode="after")
    def _key_name_overrides_must_be_unique(self) -> "DynamicParams":
        """meta.py:1251-1257 pops a key mid-iteration when two values match --
        a ``RuntimeError`` in CPython 3, not the graceful skip it reads as. And
        two keys renamed to one name would title one collection twice."""
        seen: dict[str, str] = {}
        for key, name in self.key_name_override.items():
            if name in seen:
                raise ValueError(
                    "`key_name_override` renames both %r and %r to %r, which "
                    "would give one collection two keys. Rename one of them, or "
                    "merge them with `addons` so they are one key to begin with"
                    % (seen[name], key, name)
                )
            seen[name] = key
        return self

    @model_validator(mode="after")
    def _sort_names_must_exist_somewhere(self) -> "DynamicParams":
        for name in self.sort_by or []:
            if name not in KNOWN_SORT_NAMES:
                raise ValueError(
                    "sort_by %r is not a Plex sort. Options: %s"
                    % (name, ", ".join(sorted(KNOWN_SORT_NAMES)))
                )
        return self


class DynamicBuilder:
    """A family of Plex-native smart collections, one per value in the library."""

    type_name = "dynamic"
    # The engine's marker for "this one applies itself" -- see SmartContext.
    smart = True
    params_model = DynamicParams

    # The same seven ``cs_bucket`` refuses and for the same reasons: this
    # definition names a FAMILY, and Plex evaluates each member's membership
    # live. ``sort_title``, ``collection_mode`` and the ``visible_*`` flags are
    # deliberately absent -- they are properties of the collection OBJECT rather
    # than of its membership, they apply to every member of the family, and the
    # create path applies them (roadmap row 104).
    refused_definition_fields = {
        "summary": (
            "this definition names a FAMILY of collections, one per value the "
            "library holds, so a single summary could not be the summary of any "
            "particular one of them. Kometa's dynamic engine writes no summary "
            "either -- it emits variable bindings and lets the template decide"
        ),
        "sort": (
            "Plex evaluates each collection's membership live, so there is no "
            "resolved order to set. The order the search returns is "
            "`params.sort_by`, and the family's own ordering is what "
            "`sort_title` is for"
        ),
        "limit": (
            "Plex evaluates each collection's membership from a filter, so "
            "there is no resolved list to cap. Cap the SEARCH instead, with "
            "`params.limit` -- which, with `params.sort_by`, is what 'the 50 "
            "highest-rated in each genre' means. To cap how many COLLECTIONS "
            "the family builds, use `params.max_collections`"
        ),
        "sync_mode": (
            "Plex owns these collections' membership; there is nothing for this "
            "service to sync or append to"
        ),
        "item_label": (
            "this service never resolves these collections' members -- Plex "
            "does -- so there is no list of items to label"
        ),
        "tmdb_summary": (
            "this definition names a family of collections, so one borrowed "
            "overview could not be the summary of any particular one of them"
        ),
        "filters": (
            "a `filters:` block narrows a membership this service resolved, and "
            "these are never resolved here -- the items are chosen inside Plex, "
            "by each key's own filter. Narrow the family with `include:`, "
            "`exclude:` or `params.limit` instead"
        ),
    }

    def family_label(self, definition) -> str:
        """The label this definition's collections carry.

        A method as well as a module function so the engine can ask the
        REGISTRY entry -- ``_sweep`` has a builder and a definition, and no
        reason to import this module by name.
        """
        return family_label(definition)

    async def apply(self, ctx: SmartContext) -> list[str]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'dynamic' builder builds the family its definition names, "
                "so it cannot run without one"
            )
        params = DynamicParams.model_validate(definition.params)
        row = DYNAMIC_TYPES[params.type]
        libtype = ctx.library_type.lower()
        collections = ctx.config.collections

        try:
            # Above the enumeration, deliberately: a show-only type against a
            # movie library must cost zero Plex round trips.
            require_library_type(
                "the 'dynamic' builder's %r type" % params.type,
                ctx.library_type, row.kinds,
            )
            resolver = LibraryTagResolver(ctx, ctx.section, libtype)
            enumerated = [
                (choice_key if row.key_from == "key" else choice_title, choice_title)
                for choice_key, choice_title in resolver.choices(row.attribute)
            ]
        except REFUSALS as refusal:
            return self._refused(ctx, definition.title, refusal)

        if not enumerated:
            return self._refused(ctx, definition.title, (
                "this library reports no %r values at all, so this definition "
                "would build no collections. A family with nothing to enumerate "
                "is not an empty family -- it is a library that cannot answer "
                "the question" % params.type
            ))

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
                # nothing is left over, so an ``other_name`` would name a
                # collection that could never have a member.
                other_name=params.other_name if params.include else None,
            )
        except DuplicateFamilyTitle as refusal:
            return self._refused(ctx, definition.title, refusal)

        if not titled:
            return self._refused(ctx, definition.title, (
                "every %r value this library holds was excluded, so this "
                "definition builds nothing. Widen `include:`, or remove the "
                "definition" % params.type
            ))
        if len(titled) > params.max_collections:
            return self._refused(ctx, definition.title, (
                "this would create %d collections in %r and `max_collections` "
                "is %d, so nothing was created. A family this size is usually a "
                "type with more values than expected -- narrow it with "
                "`include:` or `exclude:`, or raise `max_collections` if %d is "
                "really what you want"
                % (len(titled), ctx.library, params.max_collections, len(titled))
            ))

        # The definition's own settings, plus the family label. ``model_copy``
        # rather than a re-validated construction, for ``engine._completed``'s
        # reason: both halves are already validated, and re-running the
        # definition validators here would hold this copy to rules that were
        # checked at config load.
        settings = definition.model_copy(
            update={"labels": [*definition.labels, family_label(definition)]}
        )
        # The pass's one listing, fetched here rather than at context
        # construction so a definition that refuses above costs nothing and a
        # pass with no dynamic definition never fetches it at all.
        listing = ctx.listing() if ctx.listing is not None else None

        actions: list[str] = []
        for unit in titled:
            if not unit.values:
                actions.append(
                    "refused %r: it resolved to no values, and a smart filter "
                    "with no terms matches the entire library" % unit.title
                )
                continue
            try:
                url = build_search_url(
                    parse_filters(
                        {row.search_key: list(unit.values)},
                        field="params", searching=True, base="any",
                    ),
                    libtype=libtype,
                    sort_by=params.sort_by or row.sort_by,
                    limit=row.limit if params.limit is None else params.limit,
                    resolve_tag=resolver,
                )
                logger.debug("dynamic: %s -> %s", unit.title, url)
                actions += await reconcile_smart_collection(
                    ctx.session,
                    ctx.section,
                    ctx.library,
                    ctx.library_type,
                    unit.title,
                    url,
                    ctx.label,
                    # No summary: upstream's dynamic engine writes none either
                    # (its generated config is a template call and a label), and
                    # a family has no single summary to write.
                    summary=None,
                    dry_run=ctx.dry_run,
                    existing=listing,
                    adopt=collections.adopt,
                    adopt_from=collections.adopt_from,
                    adopt_removes_prior_label=collections.adopt_removes_prior_label,
                    protect_labels=collections.protect_labels,
                    http=ctx.http,
                    config=ctx.config,
                    settings=settings,
                )
            except REFUSALS as refusal:
                # Contained to ONE key: an unresolvable value or a filter that
                # matches nothing is that key's problem, and eleven working
                # collections must not stop being managed because a twelfth
                # cannot be built.
                logger.warning(
                    "%s: %r was not built: %s", ctx.library, unit.title, refusal
                )
                actions.append("refused %r: %s" % (unit.title, refusal))
        return actions

    def _refused(self, ctx: SmartContext, title: str, why: object) -> list[str]:
        """One definition-level refusal, logged and returned.

        Logged as well as reported because the action string reaches a run
        report an operator may not read, and the logs page is where they look
        when a family stops updating.
        """
        logger.warning("%s: %r was not built: %s", ctx.library, title, why)
        return ["refused %r: %s" % (title, why)]
```

- [ ] **Step 4: Register it**

In `src/autoposter/collections/builders/__init__.py`, add the import beside the
other builder imports (alphabetical, after `cs_bucket`):

```python
from autoposter.collections.builders.dynamic import DynamicBuilder
```

and the registration beside `CsBucketBuilder`'s at the end of the register
block:

```python
register(CsBucketBuilder())
register(DynamicBuilder())
```

- [ ] **Step 5: Run the builder tests green**

```bash
docker compose -p p10at5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_dynamic.py tests/test_collection_config.py 2>&1 | tee /app/.superpowers/run-t5-green1.log; echo EXIT=$?'
```

Expected: PASS. `tests/test_collection_config.py` is in the same run because the
new builder goes through `_smart_definitions_refuse_what_they_cannot_apply` and
`_params_must_satisfy_the_builders_own_model` — a missing
`refused_definition_fields` or an unlisted refusable field fails there first,
with a message that says so.

- [ ] **Step 6: Write the failing sweep test**

Append to `tests/test_builder_engine.py`, beside the existing sweep tests:

```python
async def test_a_dynamic_familys_collections_are_reported_by_the_sweep_never_deleted(
    session, config_factory
):
    """A dynamic family's titles are the LIBRARY's, so no definition's title set
    contains them and the sweep would otherwise see every one of them as an
    orphan on the first pass after they were created.

    The family is recognised by its LABEL -- which is how Kometa tracks the same
    membership (``append_label``, meta.py:1421) and how phase 10a-2's own family
    sweep will enumerate it, through this service's delete guards. Reported
    rather than silently skipped: an operator who narrows ``include:`` has to
    see the leftovers named, and the report says which phase removes them.
    """
    from autoposter.collections.builders.dynamic import family_label

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"},
    )
    orphan = FakeCollection(
        "Top Horror movies", labels=[LABEL, family_label(definition)],
    )
    section = FakeSection(existing=[orphan])
    session.add(ManagedCollection(
        library="Movies", title="Top Horror movies", kind="smart",
    ))
    await session.flush()

    config = config_factory()
    config.collections.ownership_label = LABEL
    config.collections.delete_unconfigured = True
    config.collections.apply_to_plex = True

    run = await run_library(
        session, section, "Movies", "Movie", [definition], config, sweep=True,
    )

    reported = [a for result in run.definitions for a in result.actions]
    assert any("Top Horror movies" in one and "10a-2" in one for one in reported)
    assert orphan.deleted is False
```

Adapt the fakes to whatever `tests/test_builder_engine.py` already uses (that
file has a `FakeSection`/`FakeCollection` pair and a `LABEL` constant); do not
add a second pair. If its `FakeCollection` has no `deleted` flag, assert on the
absence of a `collection_deleted` `EventLog` row instead — the file already has
that idiom in its sweep tests.

- [ ] **Step 7: Run it and watch it fail**

```bash
docker compose -p p10at5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_engine.py -k dynamic_family 2>&1 | tee /app/.superpowers/run-t5-red2.log; echo EXIT=$?'
```

Expected: FAIL — the collection is deleted, so the `10a-2` report is absent.

- [ ] **Step 8: Teach `_sweep` about the family**

In `src/autoposter/collections/engine.py`, add above `_sweep`:

```python
def _family_labels(definitions: list[CollectionDefinition], library: str) -> dict[str, str]:
    """``{label: definition title}`` for every dynamic family this library builds.

    Pure -- it reads the registry and the definitions, never Plex -- and
    library-scoped for ``definition_titles_for``'s reason: a family aimed at
    another library must not protect this one's collections.

    The builder is asked rather than the module imported, so the engine keeps
    knowing only the protocol: a builder that manages a family whose titles it
    cannot enumerate offline says so by having a ``family_label``.
    """
    labels: dict[str, str] = {}
    for definition in definitions:
        if not _targets(definition, library):
            continue
        builder = REGISTRY[definition.builder]
        namer = getattr(builder, "family_label", None)
        if namer is not None:
            labels[namer(definition)] = definition.title
    return labels
```

and, inside `_sweep`'s candidate loop, immediately after the `protected_label`
check (`engine.py:727-732`) and before the ownership check at `:733`:

```python
        family = next(
            (label for label in families if has_label(collection, label)), None
        )
        if family is not None:
            # A dynamic family's titles are the library's, so no definition
            # enumerates them and every member lands here on the first
            # sweep-enabled pass after it was created. The family owns its own
            # lifecycle -- phase 10a-2 gives it a sweep that runs through these
            # same guards -- so this one reports and never deletes. Reported
            # rather than skipped: an operator who narrowed the family has to
            # see what is left behind.
            results.append(_swept(title, library, (
                "%r belongs to the dynamic family %r, whose own delete sweep "
                "ships in phase 10a-2; nothing was deleted"
                % (title, families[family])
            )))
            continue
```

with `families` computed once at the top of `_sweep`, beside `managed`:

```python
    families = _family_labels(definitions, library)
```

Update `_sweep`'s docstring guard list with a fifth bullet:

```
    - a **dynamic family's member** is reported and never deleted. Its titles
      are the library's, so ``definition_titles_for`` cannot contain them and
      every one of them would otherwise look like an orphan; the family's own
      sweep (phase 10a-2) is what decides its lifecycle, through these same
      guards.
```

- [ ] **Step 9: Run the sweep test green**

```bash
docker compose -p p10at5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_engine.py tests/test_collection_leftovers.py tests/test_smart_filter_engine.py 2>&1 | tee /app/.superpowers/run-t5-green2.log; echo EXIT=$?'
```

Expected: PASS.

- [ ] **Step 10: Document the builder in the example config**

In `config/autoposter.example.yaml`, inside the commented `definitions:`
example, after the `plex_id` block:

```yaml
  #   # One collection per distinct value the library holds (Kometa's
  #   # dynamic_collections). The definition's own title names the FAMILY and
  #   # never becomes a collection; each collection is named by title_format.
  #   definitions:
  #     - title: Decades
  #       builder: dynamic
  #       params:
  #         type: decade # year | decade | content_rating | studio | genre |
  #                      # country | resolution | audio_language |
  #                      # subtitle_language | network -- an unknown one is
  #                      # rejected when the config loads, listing these
  #         # Which values become collections. include: is a whitelist applied
  #         # last; every addon member stops getting a collection of its own.
  #         exclude: [1930s]
  #         addons:
  #           1980s: [1970s] # one collection asking for both
  #         # How they are named. <<key_name>> is the value, <<library_type>>
  #         # is `movie`/`show` and <<library_typeU>> is `Movie`/`Show`.
  #         title_format: Best <<library_type>>s of the <<key_name>>
  #         key_name_override: {2000: The Noughties} # renamed BEFORE the format
  #         title_override: {1980: Eighties Classics} # replaces the whole title
  #         remove_prefix: ["The "] # ...unless key_name_override named the key
  #         remove_suffix: [" Ltd"]
  #         other_name: Everything Else # only ever built when include: is set
  #         # What each collection asks Plex for.
  #         sort_by: [critic_rating.desc] # the search's order IS the display order
  #         limit: 50 # cap on the SEARCH, so with a sort_by it picks the top 50
  #         max_collections: 50 # refuse (with the count) rather than create more
  #       libraries: [Movies]
  #       labels: [Decades] # every collection in the family carries them
  #       sort_title: "!050_Decades" # ...and this, so the family sorts as a block
```

- [ ] **Step 11: Mutation proof**

```bash
cp src/autoposter/collections/builders/dynamic.py /tmp/dynamic.py.bak
cp src/autoposter/collections/engine.py /tmp/engine.py.bak
```

Mutation A — change `base="any"` to `base="all"` in the `parse_filters` call.
Expected RED: `test_each_collection_asks_plex_for_its_own_value_under_an_any_base`.
This is the mutation that matters most in the phase: it is the exact difference
between a bucket matching any of its values and a bucket matching all of them at
once, which is 0 items against 24 on the production library (row 182).

Mutation B — restore, `cmp`, then delete the `if len(titled) > params.max_collections`
branch. Expected RED: `test_a_fan_out_past_the_cap_refuses_with_both_numbers`.

Mutation C — restore, `cmp`, then delete the `family` branch from
`engine._sweep`. Expected RED:
`test_a_dynamic_familys_collections_are_reported_by_the_sweep_never_deleted`.

Restore both files, `cmp` both, GREEN. Paste all three RED outputs.

- [ ] **Step 12: Full suite, golden, ruff, commit**

```bash
docker compose -p p10at5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10at5-full test sh -c 'timeout -s KILL 1800 pytest -q 2>&1 | tee /app/.superpowers/run-t5-full.log; echo EXIT=$?'
docker wait p10at5-full
docker logs p10at5-full | tail -40
docker rm p10at5-full
docker compose -p p10at5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10at5 down
```

The golden gate is in that run and must be byte-identical: no default
definition and no catalog preset gains a `dynamic` builder in this phase (C7),
so `git status` must show nothing under `tests/fixtures/`.

```bash
git add src/autoposter/collections/builders/dynamic.py tests/test_builder_dynamic.py \
        src/autoposter/collections/builders/__init__.py \
        src/autoposter/collections/engine.py tests/test_builder_engine.py \
        config/autoposter.example.yaml
git commit --no-gpg-sign -m "feat(builders): dynamic

One Plex-native smart collection per distinct value a library holds, written
through the same reconciler and the same 9b query grammar smart_filter uses
(decision C1). Enumerates through LibraryTagResolver.choices, derives keys and
titles with the two pure modules beside it, labels the family the way Kometa
does, and refuses -- with both numbers -- rather than fanning out past
max_collections. The engine's sweep reports a family's collections and never
deletes them; the family's own sweep is phase 10a-2."
```

---

### Task 6: The 10a-1 wrap — the research correction, the roadmap, the evidence

**Files:**

- Modify: `docs/research/kometa-collections.md` (the false `sync:` claim, `:122-123`)
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (row 102's addendum, rows 171 and 174, the phase-10a section)
- Create: `.superpowers/sdd/p10a-1-pr-body.md` (the PR description, gitignored — pasted into the PR, not committed)

**Interfaces:** none. This task's diff touches **zero `src/` and zero `tests/`
files**, which is what puts it on the ruff-plus-evidence gate rather than the
full suite (Global Constraints). The moment it touches one shipped file, the
full suite is back.

- [ ] **Step 1: Correct the research file's `sync:` claim (C4)**

`docs/research/kometa-collections.md:122-123` currently reads:

```
`sync: true` makes each year-collection a `sync_mode: sync` collection
(diffed and pruned every run — see §6).
```

That is **wrong**, and phase 10a's upstream read is what establishes it:
`meta.py:1263-1267` reads the boolean and `meta.py:1300` immediately overwrites
the variable with a label lookup; each generated title pops itself out
(`:1428-1429`, and `:1453-1454` for the other collection) and whatever remains
is deleted (`:1456-1461`). Replace with:

```
`sync: true` does **not** set `sync_mode: sync` on the generated collections
— a claim this file made until phase 10a read the code. It is a **delete
sweep**: `meta.py:1263-1267` reads the boolean, `meta.py:1300` immediately
replaces it with `{str(i.title).casefold(): i for i in
library.get_all_collections(label=str(map_name))}`, every generated title pops
itself out of that map (`:1428-1429`, and `:1453-1454` for the `other`
collection), and whatever is left is deleted (`:1456-1461`). So it means
"delete any collection carrying this map's label that this pass did not
regenerate" — Kometa's equivalent of this service's own `_sweep` /
`delete_unconfigured`, not a membership mode. The label is applied
unconditionally (`append_label: str(map_name)`, `:1421`/`:1450`), independent
of `sync`. Matching is `casefold()`-insensitive, so a title that changes only
in case survives and any other title change makes the old collection a delete
target. (Phase 10a-1, `.superpowers/sdd/p10a-upstream-dynamic.md` §6.)
```

Check whether §6 of the same file repeats the claim (`grep -n "sync_mode"
docs/research/kometa-collections.md`) and correct every instance, each carrying
the same citation.

- [ ] **Step 2: Verify the correction is complete**

```bash
grep -n "sync: true" docs/research/kometa-collections.md
grep -n "sync_mode" docs/research/kometa-collections.md
```

Expected: no line claims `sync:` sets a sync mode. Paste both outputs into the
task report.

- [ ] **Step 3: Roadmap — row 102's addendum**

Append to row 102's cell (`docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:204`),
inside the same table cell, keeping the row's existing text intact:

> **Phase 10a split into 10a-1 and 10a-2 (adjudication C8); 10a-1 SHIPPED.**
> 10a-1 delivers the engine and the operator `definitions:` surface: `builder:
> dynamic`, ten types enumerated through the public `LibraryTagResolver.choices`
> seam over `listFilterChoices`, Kometa's own `include`/`exclude`/`addons`/
> `custom_keys` key derivation and its `title_format`/`key_name_override`/
> `title_override`/`remove_prefix|suffix`/`other_name` titling — both halves
> proven against a standalone transcription of `meta.py` in
> `tests/test_collection_dynamic_oracle.py`, sixteen goldens. Every collection
> is written through `smart.reconcile_smart_collection` in the 9b query
> grammar, so there is ONE smart write path rather than a second one (decision
> C1). Two `FILTER_ATTRIBUTES` rows shipped with it — `decade` (row 171) and
> `country` (row 174) — each proven against the vendored `build_filter` oracle.
> The fan-out risk this row names is answered by a refusal, not a cap that
> truncates: a family enumerating past `params.max_collections` (default 50)
> creates nothing and reports both numbers. **Still open, and 10a-2's:** the
> family's own delete sweep through our guards, delete-below-minimum, and the
> CS port (roadmap decomposition step 5 / row 185). Until then the engine's
> `_sweep` REPORTS a family's collections and never deletes them, naming the
> phase. **Zero catalog work in 10a-1 (decision C7)** — the eleven
> `DYNAMIC_ENGINE_ROW` presets stay GATED, and their blocker text is re-pointed
> to 10b by 10a-2's wrap.

- [ ] **Step 4: Roadmap — rows 171 and 174**

Row 171 (`:267`) gains, at the end of its cell:

> **Half-closed by phase 10a-1:** the `decade` table row shipped
> (`src/autoposter/collections/filters.py`, search-only, movie-only, the bare
> form as its whole operator set per `no_not_mods`), proven by oracle config
> `16-decade-and-country`. The `current_year` / `current_year-N` value grammar
> — the other half of this row — did NOT ship and still blocks nothing else.

Row 174 (`:270`) gains:

> **CLOSED by phase 10a-1:** the `country` row shipped
> (`src/autoposter/collections/filters.py`, tag, both vocabularies, rescoped to
> `show.country` on a show library), proven by oracle configs
> `16-decade-and-country` and `17-country-on-a-show`. Its source tier is
> `unprobed`, not `listing`: whether `<Country>` reaches the section listing was
> never probed, so a `filters:` block still refuses it by naming that tier.

- [ ] **Step 5: Roadmap — the phase 10a section**

At `:747-766`, insert after the existing `**Testable when shipped:**` line:

> **Delivered as 10a-1 (2026-08):** decomposition steps 1–3 in full and step 4
> in part (create; the lifecycle half is 10a-2). Step 5 — the CS port — is
> 10a-2's, and it carries an OPEN adjudication: `golden_port.json` records the
> Common Sense family's plexapi **call shape** (`"filters": {"contentRating":
> ["G"]}`, one cell per bucket), not its outcome, so a port onto the 9b grammar
> cannot leave that file byte-identical. See `.superpowers/sdd/p10a-plan-draft.md`'s
> CONTRADICTION FLAG section: the equivalence proof and the fixture's one
> changed cell need a controller ruling before 10a-2 is planned.

**If Task 2's probe omitted `network`**, add a new roadmap row in the same edit,
numbered after the current highest, with: the probe's own numbers, the
`listFilterChoices` call that refused, the note that 9b proved the SEARCH field
answers (91 networks) so the strand is specifically the CHOICES listing, and
`production_network`'s dependency on it.

- [ ] **Step 6: Re-verify the C7 claim against the shipped diff**

C7 requires that no preset ships a smart builder in this phase, which is what
keeps the row-93 break site (`tests/test_collection_catalog.py:119-120`,
`builder.titles(...)` called unconditionally for every smart builder a preset
expands into) unreached. Verify it against the diff rather than against the
intention:

```bash
git diff --stat origin/main -- src/autoposter/collections/catalog.py \
    src/autoposter/collections/sources.py
git diff origin/main -- tests/fixtures/collections/golden_port.json
grep -n "dynamic" src/autoposter/collections/catalog.py src/autoposter/collections/sources.py
```

Expected: the first two produce **no output at all**, and the third finds no
`builder="dynamic"` (a prose occurrence of the word in a gated preset's
description is fine — read each hit). Paste all three into the task report. If
any is non-empty, the phase has done catalog work and C7 is violated: STOP and
report rather than adjusting the expectation.

- [ ] **Step 7: The evidence sweep**

Assemble in the task report, each with the run it came from:

1. Every task's mutation proof, RED and GREEN, real output only.
2. The oracle provenance statement: whether `meta.py` v2.4.8 was fetched and
   diffed against `tests/oracle/10a/kometa_dynamic.py` (Tasks 3 and 4), or
   explicitly not, and why.
3. The probe verdict for `network`, and which branch of Task 2 Step 6 was taken.
4. The row-119 tally: any pre-existing wall-clock assertion a run tripped over.
5. The final full-suite run, the golden gate and `ruff check src tests`.
6. The three C7 checks from Step 6.

- [ ] **Step 8: Write the PR body**

`.superpowers/sdd/p10a-1-pr-body.md` — gitignored, pasted into the PR rather
than committed. It must state, in this order:

- what shipped (the six task deliverables, one line each);
- what it does NOT do: no deletes, no CS port, no catalog work, no preset flips,
  ten types and not thirteen;
- the two documented divergences from upstream: language collection names come
  from Plex rather than TMDb's `_iso_639_1` table, and three upstream
  log-and-continue paths refuse here instead (with the table from Task 4);
- the row-135/162 gap widening: a dynamic family's titles are invisible to
  `_titles_must_not_collide`, and what backstops it;
- the corrected `sync:` claim, with the citation;
- the open CONTRADICTION FLAG that 10a-2 needs adjudicated;
- **no AI attribution, no mention of how the work was produced.**

- [ ] **Step 9: Gate and commit**

```bash
docker compose -p p10at6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10at6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_catalog.py tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-t6-gate.log; echo EXIT=$?'
docker compose -p p10at6 down
```

`tests/test_collection_catalog.py` is in that run for a specific reason: its
gating tests read the roadmap `.md` and assert that every gated row cites a row
that exists (`:467`), so a roadmap edit is the one docs change that can redden
the suite.

```bash
git add docs/research/kometa-collections.md \
        docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs: correct Kometa's sync: semantics and record phase 10a-1

sync: true is a label-scoped DELETE sweep upstream (meta.py:1300, :1456-1461),
not a sync_mode -- the research file claimed the opposite. Roadmap row 102
records the 10a-1/10a-2 split and what shipped; rows 171 and 174 are half- and
fully closed by the two new table rows."
```

---

## Phase 10a-2 — outline

**Planned in full when 10a-1 ships**, per C8. This is the scope, not the plan;
each bullet becomes a task with its own TDD cycle and reviewer gate.

**Branch:** `feat/dynamic-collections-lifecycle`, cut from `origin/main` after
10a-1 merges, with the same content probe plus
`git cat-file -e origin/main:src/autoposter/collections/builders/dynamic.py`.

**T1 — Establish what the golden fixture records about the CS create path
(C2's mandated first step). ALREADY ANSWERED by this plan's research; the task
re-verifies it and acts on the ruling.** The finding, recorded here so 10a-2
starts from evidence:
`tests/test_builder_port_golden.py:308-326`'s `_state` records
`"filters": collection.updated_filters`, and `updated_filters` is set only by
the fake's `createCollection(..., filters=...)` / `updateFilters(...)`. The
fixture therefore holds, per bucket, `"filters": {"contentRating": ["G"]}` — a
**plexapi call shape**, not an outcome. A port onto the C1 grammar writes a raw
POST with a `uri` and leaves `updated_filters` at `None`, so the fixture's
`filters` cell changes for every Common Sense collection in every scenario.
**Consequence:** the golden gate is necessary but NOT sufficient, exactly as C2
anticipated, and it cannot stay byte-identical either. See the CONTRADICTION
FLAG below — this needs a controller ruling before T2 is written.

**T2 — The equivalence proof.** Old query versus new query producing identical
member sets, at the level the fixture cannot reach: for every bucket in the
shipped `content_rating_cs.json` table, against a vocabulary fixture, assert
that plexapi's `filters={"contentRating": [...]}` (comma-joined, read by Plex as
OR) and this engine's `any: {content_rating: [...]}` (one term per value, joined
by `or=1`) select the same values. Upstream is the authority that the two agree
(`builder.py:4242-4248`, `:4174`, and the `any:` base at `meta.py:950`), and
`docs/research/kometa-collections.md:295` records the production family using
exactly that block. Oracle-grade: the driver produces the plexapi filter string
and the `build_filter` URL side by side.

**T3 — The CS port.** `cs_bucket` becomes a `dynamic` definition (`type:
content_rating`, `include` + `addons` from the shipped table, `title_format:
Age <<key_name>>+ <<library_typeU>>s`, the `other` bucket) or keeps its builder
and switches its write path — the ruling from T1 decides which. Graded by the
golden gate plus T2's proof, plus the summary and separator behaviour the
fixture also records. Row 185 (grammar unification) is ADVANCED here; whether
it CLOSES depends on what ships, and the wrap records which.

**T4 — The family sweep and delete-below-minimum (C4).** The family enumerates
its own members by label (`section.collections()` filtered on
`FAMILY_LABEL_PREFIX + title`, or the server-side label filter
`service.py:158-175` already uses), and deletes the ones this pass did not
regenerate — through OUR guards, never a bare `library.delete`:
`delete_unconfigured` off means reported, `protect_labels` wins first,
`max_deletes` refuses the whole sweep with both numbers, `dry_run` reports. The
`engine._sweep` family branch from 10a-1 Task 5 Step 8 is replaced by a call
into it. Delete-below-minimum (roadmap decomposition step 4) rides the same
path: a key whose search matches fewer than `params.minimum_items` items is not
created, and an existing collection that falls below it is deleted through the
same guards. **Note for the planner:** upstream has NO per-key minimum for the
library types (`p10a-upstream-dynamic.md` §7.2) — the nearest thing is the
people types' `data: minimum` credit threshold, which is an enumeration filter.
So this is a divergence by design and its default must be "off" (`None`), not a
number.

**T5 — The wrap.** C7's re-pointing: each of the eleven `DYNAMIC_ENGINE_ROW`
presets' blocker text moves from "the engine is missing" to "10b: the
preset-expansion story", in `src/autoposter/collections/catalog.py` — which
moves `CATALOG_CHECKSUM` not at all (readiness and gated row are unchanged) but
does touch the descriptions
`test_the_three_presets_9b_readjudicated_stay_gated_on_the_engine_row`
(`tests/test_collection_catalog.py:1389-1416`) asserts on, so its `"live probe"`
/ `"enumerat"` substring assertions must survive the rewording. Plus: row 102
closure, row 185's verdict, the roadmap's 10a section marked delivered, and the
follow-up rows this phase filed (the TMDb-walk types, the language-name table,
`network` if the probe stranded it).

---

## Self-Review

**1. Spec coverage.** Every adjudication mapped to the task that implements it:

| Adjudication | Where |
| --- | --- |
| C1 — a third smart builder emitting 9b-grammar smart collections through `reconcile_smart_collection` | T5 (`DynamicBuilder.apply`), asserted by `test_each_collection_asks_plex_for_its_own_value_under_an_any_base` and by the absence of any `createCollection` call in the builder |
| C2 — the `any:` base reproduces CS membership; the golden gate grades the port; establish first what the fixture records | Established in this plan (10a-2 outline T1 + the CONTRADICTION FLAG); the port itself is 10a-2. 10a-1's `test_the_common_sense_table_derives_the_same_values_this_engine_would` (T3) proves the VALUE half now |
| C3 — types scoped exactly; the two vocabulary rows with oracle coverage; the network probe | T1 (rows + oracle), T2 (type table + probe with a fail-closed rule for both outcomes) |
| C4 — lifecycle by label, our guards, the row-135/162 gap documented, the `sync:` doc correction | T5 (the label + the sweep's family report + the module docstring's gap note), T6 (the doc correction); the sweep itself is 10a-2 |
| C5 — `test:` and `data:` refused, not ported | T5 (`_REFUSED_KEYS`), asserted per key by `test_the_dead_upstream_knobs_refuse_by_name_with_the_reason` |
| C6 — the four live options with upstream's exact precedence; oracle-style tests; the collision bypass NOT reproduced | T3 (keys), T4 (titles, the three refusals, `other` held to the collision rule) |
| C7 — zero catalog work | Asserted in T6 Step 6 against the diff, not the intention |
| C8 — the split, and the fan-out refusal cap | The split decision at the top; the cap in T5 with both numbers in the message |

Two roadmap decomposition items are deliberately NOT in 10a-1 and are named as
10a-2's: "sync membership" (there is none — Plex owns it; what remains is the
delete sweep) and "delete-below-minimum".

**2. Placeholder scan.** The `{...}` cells in the two oracle tests
(`KOMETA_KEYS`, `KOMETA_TITLES`) are the one intentional exception and they are
not placeholders in the forbidden sense: the plan cannot invent an oracle's
answer, so each carries the exact command that produces it (T3 Step 2, T4
Step 2) and the line to copy verbatim. The same is true of T1's two `KOMETA`
strings, which additionally carry the expected shape so a mismatch is caught
rather than pinned. The `render_title(...) if False else ...` in T4 Step 4 is
flagged in the step itself as a plan-writing artefact with the exact line to
write instead. Both `network` branches in T2 are fully specified — neither
leaves a decision to the implementer.

**3. Type consistency.** `DynamicKey(key, value, values)` and
`DerivedKeys(keys, other_keys, used_keys)` are produced in T3 and consumed by
name in T4's `family_titles` and in T4's oracle test's hand-built `DerivedKeys`.
`TitledKey(key, key_name, title, values)` is produced in T4 and consumed in T5's
loop (`unit.title`, `unit.values`). `DynamicType`'s nine fields are produced in
T2 and every one is read in T5 (`attribute`, `search_key`, `kinds`, `key_from`,
`title_format`, `sort_by`, `limit`) or in T2's own tests (`name`, `note`).
`LibraryTagResolver.choices` is produced in T2 and consumed in T5;
`family_label` is produced in T5 as both a module function and a method, and the
engine reads the METHOD off the registry entry (`_family_labels`) while the
tests read the module function — deliberate, and both are the same string.
`title_format_names_the_key` is produced in T4 and consumed in T5's params
validator.

---

## CONTRADICTION FLAG

Two findings that a controller must rule on. **Both are planned the adjudicated
way regardless**, per the brief.

### FLAG 1 — the golden gate cannot stay byte-identical through the CS port (C2 vs. project law)

**The fact.** `tests/test_builder_port_golden.py:308-326`'s `_state` records
`"filters": collection.updated_filters` for every collection, and the fake sets
`updated_filters` only from plexapi's `createCollection(..., filters=...)` and
`updateFilters(...)` (`:230-237`, `:166-167`). The committed fixture therefore
holds, for every Common Sense bucket in every scenario, a cell like
`"filters": {"contentRating": ["G"]}` — verified directly:

```
Age 1+ Movies {"summary": "...", "sort": null, "sort_title": null,
               "filters": {"contentRating": ["G"]}, "labels": ["autoposter"], ...}
```

That is a **plexapi call shape**, not an outcome. Under C1 the ported family
writes a raw POST carrying a `uri` and never calls `createCollection`, so
`updated_filters` stays `None` and that cell changes for every CS collection in
the file.

**The contradiction.** C2 says the port "proceeds on that basis" and that if the
fixture pins call shapes "the golden gate is necessary but not sufficient" —
*necessary* meaning it must still pass. It cannot: the fixture's own harness
records the call shape the port removes. Global project law says the golden gate
is byte-identical, with the parenthetical that the CS port is the one place a
change would even be conceivable and that C2 governs — but C2 does not actually
say a change is permitted, only that the gate is insufficient. The two readings
cannot both hold.

**What the plan does about it.** Nothing in 10a-1 — the CS path is untouched and
the gate is byte-identical at every commit, which is why this flag does not
block this plan. It blocks 10a-2's T1, and the outline says so. The three
options a ruling would choose between, stated so the ruling is a choice and not
an invention:

1. **Amend the harness, not the recording.** Teach the fake to record the
   POSTed `uri` into the same `filters` cell (or a new `uri` cell) and re-capture
   only the CS rows — which requires lifting "never re-capture from a post-port
   tree" for one documented cell, under the equivalence proof.
2. **Port the family without changing its write path.** Keep
   `createCollection(smart=True, filters=...)` for CS alone, and the gate stays
   byte-identical — at the cost of two grammars surviving, which is the thing
   row 185 and C1 exist to end.
3. **Split the fixture's claim.** Freeze the current file as the pre-port
   record, capture a second post-port fixture, and make the gate a comparison of
   the two through a documented translation of the one cell.

The plan does not choose. 10a-2's T1 is written after the ruling.

### FLAG 2 — C6's `other_template` cannot be ported as written

**The fact.** C6 says to port the `other_name`/`other_template` gate ("both
require `include:`"). `other_template` names another Kometa **template** for the
leftovers collection (`meta.py:1311-1317`), and its whole purpose is to let the
`other` bucket be built by a different query — upstream's own richer variable
set for it (`used_keys`, `included_keys`, `meta.py:1435`) exists so an
`other_template` can be written as a set complement rather than an enumeration.
This service has **no template system at all**: the query is a property of the
dynamic type, not of a template the definition points at. There is nothing for
`other_template` to name.

**What the plan does about it.** The GATE is ported in full — `other_name` is
dead unless `include:` is set (T5's `other_name=params.other_name if
params.include else None`, and T4's
`test_no_other_collection_when_nothing_was_left_over`) — and `other_template`
is a load-time refusal naming exactly this reason (`_REFUSED_KEYS`), which is
the C5 discipline applied one key along rather than a silent omission. If the
controller intended something narrower by "port that gate", the refusal message
is the one line to change.

**Not flagged, recorded here for completeness** (each is a documented
divergence, not a contradiction): language collection names come from Plex's own
`choice.title` rather than TMDb's `_iso_639_1` English name, which is upstream's
own fallback branch and affects names only, never membership; and three upstream
log-and-continue paths (`title_format` revert, duplicate `key_name_override`
value, duplicate generated title) refuse here instead, per the refuse-over-
silent-ignore rule this project applies everywhere else.
