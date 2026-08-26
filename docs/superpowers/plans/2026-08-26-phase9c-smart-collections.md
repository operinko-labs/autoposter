# Phase 9c: Native Smart Collections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `smart_filter` as a registered smart builder — an operator writes the
same query 9b's `plex_search` takes, and Plex owns the resulting collection's
membership forever after, evaluated live from a filter this service wrote once.

**Architecture:** Five layers, each its own reviewer gate. (1) THE SMART-ENVELOPE
ORACLE — the 9b oracle extended so Kometa's `build_smart_filter` /
`create_smart_collection` / `update_smart_collection` produce the POST and PUT
strings as pinned data, sixteen configs deep, before a byte of ours exists.
(2) The reconciler (`collections/smart.py`) — a raw POST/PUT over a URL string,
with the empty-result refusal, the shape-conflict refusal and the desired-state
hash, testable with no builder in sight. (3) The builder + params + registration
+ the load-time refusal split. (4) The definition end-to-end: engine dispatch,
leftovers enumeration, delete sweep, `managed_collections`. (5) The wrap:
roadmap rows, the operator acceptance procedure, branch prep.

**Tech Stack:** Python 3.14, pydantic v2, plexapi 4.18.2, SQLAlchemy 2 (async),
pytest, ruff, Docker Compose. **No new runtime dependency.**

---

## What this phase is NOT

Settled in `.superpowers/sdd/p9c-facts.md` (adjudications C1–C11) and **not
reopened by an implementer**:

- **No plexapi `filters=` translation.** The URI is 9b's oracle-proven string,
  sent by a raw POST (C1). plexapi is still used for the re-read
  (`section.collection(title)`), the labels, the sort title, the mode, the
  summary and the delete.
- **No CS migration.** `cs_bucket` keeps `section.createCollection(smart=True)`
  and its plexapi grammar (C2). Two grammars coexist; T5 files the optional
  migration as a roadmap row.
- **No `smart_label`, no `smart_url`, no `plex_collectionless`** (C3, C4). T5
  rewrites the roadmap rows that assumed them.
- **No catalog rows, no preset, no default definition** (C9). The golden gate
  proves it.
- **No live-server probe, no dev-time write** (C10, T5). Acceptance is the
  oracle plus a documented post-deploy operator procedure.

---

## Task order — one deviation from the facts file, stated once

The facts file lists the task split as *T1 oracle, T2 builder+params, T3
reconciler*. This plan runs **T2 = reconciler, T3 = builder** — the same two
scopes, swapped. The reason is mergeability, not preference: the builder's
`apply()` is four statements plus a call to `reconcile_smart_collection`, so a
builder task that lands first either ships a placeholder body (forbidden) or
registers a builder whose `apply` does not exist, leaving the engine's smart
dispatch on a live `AttributeError` at the task boundary. The reconciler takes
a **URL string**, not a builder, so it is complete and independently testable
first. Nothing else about either scope moves.

---

## Branch and cut point

- Branch name: **`feat/smart-collections`**.
- **Cut from `origin/main` after a fetch.** The 9b merge landed on the remote
  via a rebase-merge, so its commits carry NEW shas there (`b994e1f` names the
  branch-side commit and is an ancestor of nothing on the remote) and a stale
  local `main` is the trap this line originally fell into. Verify by CONTENT,
  not by sha: `git fetch origin` then confirm
  `git cat-file -e origin/main:src/autoposter/collections/search_url.py` —
  that file exists only if 9b shipped. Record the actual cut commit in the
  Task 1 report. If the probe fails, STOP and report — this plan's every seam
  assumes 9b shipped.
- No stacking. If a PR touching `src/autoposter/config/schema.py` or
  `src/autoposter/collections/engine.py` is open when this starts, say so in
  the Task 1 report and note the retarget instruction in the PR description
  (the 9a/9b precedent), but still cut from `origin/main`.

## Execution

Executed via **superpowers:subagent-driven-development**: one fresh subagent per
task, two-stage review between tasks, this plan document **live-synced** through
every fix round (a fix that changes a signature, a message or a golden edits the
corresponding step here in the same commit, so a later task's implementer reads
the truth and not the original guess).

---

## Global Constraints

Every task's requirements implicitly include this section.

**Testing is container-only.**

- A bare host `pytest` fails at collection by design (`tests/conftest.py`
  requires `AUTOPOSTER_TEST_DATABASE_URL` with no fallback). That is intended.
- Each task runs on its **own isolated compose project**, named `p9ct<N>`
  (`p9ct1`, `p9ct2`, `p9ct3`, `p9ct4`, `p9ct5`), so a sibling agent's run on the
  shared `-p autoposter` database is never contended:

  ```bash
  docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
      run --rm test sh -c 'timeout -s KILL 1800 pytest -q tests/test_smart_collection_oracle.py; echo EXIT=$?'
  ```

- **`timeout -s KILL N` inside `sh -c`, never a bare `timeout` as the container
  command.** BusyBox `timeout` execs in the parent, pytest becomes PID 1, and
  PID 1 ignores default SIGTERM — that produced 10-hour zombie containers
  squatting on the shared postgres.
- Teardown after each task: `docker compose -p p9ct<N> down`. **Never `down -v`**
  — that destroys the dev database volume. Never `docker compose down` on the
  shared `-p autoposter` project.
- **The repeated-foreground-docker-wait recipe**, for any run that outlives a
  single tool call's cap. Start the run detached with a name, then block in the
  FOREGROUND on `docker wait`, repeatedly, until it returns:

  ```bash
  # 1. start it, named, detached
  docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
      run --rm -d --name p9ct1-run test \
      sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
  # 2. block in the FOREGROUND (repeat this exact call until it prints an exit code)
  docker wait p9ct1-run
  # 3. read the result
  docker logs p9ct1-run | tail -40
  ```

  After starting ANY container run, the agent's next action MUST be one of
  (a) foreground `docker wait <name>`, (b) reading the finished container's
  logs, or (c) a different piece of work followed by (a)/(b). **Ending a turn
  while a container runs and expecting to be resumed is the failure**, whatever
  it is called — there is no notification mechanism. Nine recorded stalls; no
  phrasing grants an exception.
- If zombie `autoposter*run*` or `p9ct*` containers are found holding the DB,
  remove with `docker rm -f <id>` — never `docker compose down` on the shared
  project.

**Every task ends with golden + ruff, both green.**

```bash
docker compose -p p9ct<N> -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 1800 pytest -q tests/test_builder_port_golden.py tests/test_collection_catalog.py; echo EXIT=$?'
docker compose -p p9ct<N> -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
```

- **Golden gate byte-identical throughout.** `tests/test_builder_port_golden.py`
  reproduces `tests/fixtures/collections/golden_port.json` byte for byte, and
  **no default definition and no catalog preset gains a `smart_filter` in this
  phase** (C9). If the golden file changes, the change is a defect until proven
  otherwise; never re-capture it (re-capture is only ever legal on the pre-port
  commit).
- `ruff check src tests` — the lint select set is `["E4", "E7", "E9", "F"]`
  (`pyproject.toml:73`), pinned deliberately; do not broaden it.

**The full suite runs where a task's scope warrants it.** T2, T3 and T4 each
touch shipped `src/` files, so each ends with the **full suite**. T1 touches
`tests/` only and runs the full suite too (it edits a driver another test file
already reads). T5 is **evidence-and-docs only** — its diff touches zero `src/`
and zero `tests/` files, so it gates on `ruff check src tests` plus its own
evidence checks and does NOT run the full suite (the 9b T5 lesson: eleven
minutes proving that nothing it had not changed still worked). This is a rule
about the *diff*, not the task number: the moment T5 touches one shipped file,
the full suite is back.

**Paths.** Every file reference in a dispatch prompt, task brief, task report or
commit message uses the FULL repo-relative path (`src/autoposter/...`,
`tests/...`, `docs/...`, `.superpowers/...`). Shorthand is acceptable only inside
a sentence that already gave the full path.

**No timestamp-flake assertions (roadmap row 119).** Nothing in this phase
asserts on `datetime.now()`, elapsed time, or a database clock. Every
date-sensitive test pins its moment explicitly and passes it in. If a run hits a
*pre-existing* instance of the pattern, the task report records it for T5's
row-119 tally and reruns; it does not "fix" an unrelated test.

**Async-SQLAlchemy capture-ids-before-expiry rule — LIVE in this phase.** T2 and
T4 both write `managed_collections` rows. After a commit an ORM attribute is
expired, and touching it issues a lazy load that raises under async. **Capture
`record.id`, `record.title`, `record.definition_hash` into locals BEFORE the
commit that expires them**, and re-`select()` rather than reusing a stale
instance across a commit boundary. `reconcile_smart_collection` itself only
`flush()`es (never commits), exactly as `reconcile_content_ratings` and
`reconcile_list_collection` do — the commit belongs to the caller, and that is
deliberate: a per-library rollback has to be able to take the Plex-side writes'
rows with it.

**Secrets and URL hygiene.**

- No test fixture, no report and no committed file carries the Plex server
  address, its token, or an `X-Plex-Token` query parameter. The oracle's machine
  identifier is the literal `abc123`; the section key is `2`.
- **Class-name-only errors from Plex.** Any exception raised out of a plexapi
  call is caught and re-raised as this phase's own named exception carrying the
  exception's **class name and nothing else** — never `str(error)`, which can
  carry a tokenised URL.
  `src/autoposter/collections/builders/plex_search.py:351-359` is the shape this
  phase copies (`PlexSearchUnavailable`).

**Oracle discipline.**

- Kometa is pinned at **v2.4.8**; plexapi at the installed **4.18.2**. Every
  transcribed function carries `file:line` provenance in a comment —
  `modules/plex.py:1615-1616`, not "Kometa's smart filter builder".
- The oracle drivers import **NOTHING** from `src/`. They may import stdlib and
  nothing else. `tests/oracle/9c/kometa_smart_collection.py` also imports
  nothing from `tests/oracle/9b/` — the test file drives both.
- Goldens are pinned **as data** in the test file. **Never edit a golden to make
  a test pass: if ours differs, ours is wrong.**

**Purity.** Nothing in config load, in expansion, or in any parse/URL function
does I/O. `parse_filters`, `build_search_url` and `smart_filter_uri` are pure.
The tag resolver is **injected**. The one argued exception is unchanged from 9b:
build-time tag validation makes Plex calls, memoised per
`(library, libtype-scope, field)` in the pass's `run_cache` — which is exactly
why `SmartContext` grows `run_cache` in T3.

**Conventions.**

- `git commit --no-gpg-sign`. **No `Co-Authored-By` and no AI attribution** in
  any commit message or PR description.
- Stage files **by name**. Never `git add -A`, `git add .`, `git commit -a`.
- **Never restore a file with `git checkout -- <file>`.** Copy it aside first
  and restore from the copy, verified with `cmp`.
- `.gitattributes` forces LF. If editing with a Python script use `write_bytes`
  or `newline="\n"`; `write_text` emits CRLF on Windows and turns a small edit
  into a whole-file diff.
- Use PowerShell for docker commands; Git Bash mangles container paths.

**The bar for tests.** This repository has shipped tests that could not fail six
times. Every guard test must be proven falsifiable: break the thing it guards,
show it red, restore (from the copy, verified with `cmp`), show it green, and
paste **both real outputs**. Never present output from one run as evidence for
another.

**Mutation proofs, named per task.** Each task's final step names the exact
mutation its reviewer must see fail:

| Task | Mutation | Test that must go red |
| --- | --- | --- |
| T1 | delete `default_sort` from the driver's `sort.append(...)` line | `test_the_smart_driver_still_produces_the_pinned_strings` (config 16) |
| T1 | change one `%25` to `%` in one pinned POST golden | `test_the_envelope_is_byte_identical_to_kometas` for that config |
| T2 | drop the `if not items:` guard in `require_matches` | `test_a_filter_matching_nothing_refuses_at_create` |
| T2 | change `"smart": 1` to `"smart": 0` in `create_smart_collection` | `test_the_create_post_is_byte_identical_to_the_oracles` |
| T3 | change the default sort from `("random",)` to `()` | `test_a_definition_with_no_sort_by_sorts_random` |
| T3 | delete the `sort` row from `SmartFilterBuilder.refused_definition_fields` | `test_sort_is_refused_on_a_smart_filter_definition` |
| T4 | revert `definition_titles`' fallthrough to `builder.titles(...)` | `test_a_smart_filter_definition_enumerates_its_own_title` (AttributeError) |
| T4 | make `_sweep` skip rows whose `kind == "smart"` | `test_an_orphaned_smart_filter_collection_is_deleted_when_opted_in` |

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
| `tests/oracle/9c/kometa_smart_collection.py` | Kometa's four smart-envelope functions (`modules/plex.py:1580-1620`) plus plexapi's `joinArgs`, transcribed standalone. Imports stdlib only. |
| `tests/test_smart_collection_oracle.py` | The sixteen envelope goldens, pinned as data, plus the plexapi cross-check and the driver-drift guard. |
| `src/autoposter/collections/smart.py` | The third reconciler: one Plex-native smart collection, from a built URL string. Raw POST/PUT, the empty-result refusal, the desired-state hash. |
| `tests/test_collection_smart.py` | That reconciler's unit surface. |
| `src/autoposter/collections/builders/smart_filter.py` | The `smart_filter` builder: params → URL → the reconciler. |
| `tests/test_builder_smart_filter.py` | The builder's params, refusals and URL. |
| `tests/test_smart_filter_engine.py` | The definition end-to-end: engine dispatch, leftovers enumeration, delete sweep, the DB row. |

**Modified**

| Path | Change |
| --- | --- |
| `tests/oracle/9b/kometa_build_filter.py` | `build_filter` gains upstream's `default_sort` parameter (`modules/builder.py:4140-4141`). Additive; the fifteen 9b goldens are unchanged. |
| `src/autoposter/collections/reconcile.py` | Gains `shape_conflict()` (C11), shared by both membership-owning reconcilers. |
| `src/autoposter/collections/lists.py` | Calls `shape_conflict(..., want_smart=False)` before touching an existing collection. |
| `src/autoposter/collections/builders/plex_search.py` | `_Resolver` → `LibraryTagResolver`, exported: `smart_filter` needs the same per-pass tag vocabulary and a second copy would drift. |
| `src/autoposter/collections/builders/base.py` | `SmartContext` grows `run_cache`; `SmartBuilder.titles` becomes an optional extra (the `params_model` convention). |
| `src/autoposter/collections/builders/cs_bucket.py` | Declares `refused_definition_fields` (row 140's refusals for the family builder). |
| `src/autoposter/config/schema.py` | `_membership_knobs_need_a_membership` becomes the per-builder refusal split (C7). |
| `src/autoposter/collections/builders/__init__.py` | Imports and registers `SmartFilterBuilder`. |
| `src/autoposter/collections/engine.py` | Smart dispatch passes `run_cache`; `definition_titles` gains the no-`titles()` fallthrough (C6). |
| `src/autoposter/db/models.py` | `ManagedCollection.kind`'s comment: what `"smart"` now covers. |
| `tests/test_plexapi_collection_contract.py` | Pins `Collection.smart` and `LibrarySection.collection`. |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | T5: rows 90, 91, 140, the 9c section, and the new rows. |

---

### Task 1: The smart-envelope oracle

**Files:**

- Modify: `tests/oracle/9b/kometa_build_filter.py` (the `build_filter` signature and one line of its sort block)
- Create: `tests/oracle/9c/kometa_smart_collection.py`
- Create: `tests/test_smart_collection_oracle.py`

**Interfaces:**

- Consumes: `tests/oracle/9b/kometa_build_filter.py::build_filter(method, plex_filter, sort_type, default_sort=None) -> (type_key, url)`; `src/autoposter/collections/search_url.py::build_search_url`; `src/autoposter/collections/filters.py::parse_filters`.
- Produces: sixteen pinned `POST` strings and two pinned `PUT` strings. Task 2 re-pins the same literals in `tests/test_collection_smart.py`, shared **by value** — for the reason `test_the_oracles_vocabulary_fixture_matches_this_files_copy` already gives about `CHOICES`: an oracle and the code it judges must not share a symbol.

**Why this task exists.** 9b proved the *query*. It did not prove the *envelope*,
and the envelope is not a formality: `joinArgs` percent-encodes the whole `uri`
value, so every `%3A` inside a 9b golden becomes `%253A`, every `%20` becomes
`%2520`, and every `!` becomes `%21`. An envelope missing one level of encoding
is a URL Plex still accepts and answers with a different set — the exact
silent-wrongness class this phase's risk section names.

- [ ] **Step 1: Confirm the cut point**

```bash
git fetch origin
git cat-file -e origin/main:src/autoposter/collections/search_url.py && echo "9b IS in origin/main"
git switch -c feat/smart-collections origin/main
git log --oneline -1
```

Expected: the second command prints `9b IS in origin/main`. (The probe is by
content, not sha — the rebase-merge gave 9b's commits new shas on the remote,
and a stale local `main` must not be consulted.) Record the actual cut commit
sha in the Task 1 report. If it does not print, STOP and report.

- [ ] **Step 2: Give the 9b driver upstream's `default_sort` parameter**

This is a transcription **correction**, not a feature: Kometa's real signature is
`build_filter(self, method, plex_filter, display=False, default_sort=None)`
(`modules/builder.py:4093`) and its sort block ends
`sort.append(default_sort if default_sort else type_default_sort)`
(`:4140-4141`). The 9b driver flattened both because no 9b caller passed a
default. The `smart_filter` call site does: `default_sort="random"`
(`modules/builder.py:1478`).

In `tests/oracle/9b/kometa_build_filter.py`, change the signature line:

```python
def build_filter(method, plex_filter, sort_type):
```

to:

```python
# ``default_sort`` is upstream's own parameter (modules/builder.py:4093). 9b's
# transcription dropped it because ``plex_search`` never passes one; the
# ``smart_filter`` call site does -- ``default_sort="random"``
# (modules/builder.py:1478) -- so it is restored here, defaulting to None
# exactly as upstream does, which leaves all fifteen 9b goldens untouched.
def build_filter(method, plex_filter, sort_type, default_sort=None):
```

and change the sort fallback:

```python
    if not sort:
        sort.append(type_default_sort)
```

to:

```python
    if not sort:
        # modules/builder.py:4140-4141, restored verbatim.
        sort.append(default_sort if default_sort else type_default_sort)
```

- [ ] **Step 3: Prove the fifteen 9b goldens did not move**

```bash
docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_search_oracle.py; echo EXIT=$?'
```

Expected: `EXIT=0`, everything green. `test_the_driver_still_produces_the_pinned_strings`
is the regression proof for Step 2 — it re-runs the edited driver against all
fifteen pinned strings. Record the real pass count in the report.

- [ ] **Step 4: Write the 9c oracle driver**

Create `tests/oracle/9c/kometa_smart_collection.py`:

```python
"""THE SMART-ENVELOPE ORACLE -- phase 9c's acceptance.

9b proved the QUERY. This proves the ENVELOPE around it: the ``uri`` Kometa
hands Plex when it creates or updates a smart collection, and the two request
keys that carry it. The envelope matters on its own because ``joinArgs``
percent-encodes the whole ``uri`` VALUE, so every ``%3A`` inside a 9b golden
becomes ``%253A``, every ``%20`` becomes ``%2520`` and every ``!`` becomes
``%21``. An envelope missing one level of encoding is a URL Plex still answers,
with a different set -- the silent wrongness this phase exists to exclude.

Provenance (fetched 2026-08-26):

  Kometa v2.4.8, modules/plex.py:1580-1584     Plex.test_smart_filter
  Kometa v2.4.8, modules/plex.py:1592-1600     Plex.create_smart_collection
  Kometa v2.4.8, modules/plex.py:1615-1616     Plex.build_smart_filter
  Kometa v2.4.8, modules/plex.py:1618-1620     Plex.update_smart_collection
  Kometa v2.4.8, modules/builder.py:1476-1483  the smart_filter call site
  plexapi 4.18.2, plexapi/utils.py             joinArgs
  plexapi 4.18.2, plexapi/server.py            PlexServer._uriRoot

``joinArgs`` and ``_uriRoot`` are plexapi's, not Kometa's -- Kometa reaches for
both and defines neither (``utils.joinArgs``, ``self.PlexServer._uriRoot()``).
They are transcribed here rather than imported so this file imports nothing but
the standard library, and ``test_the_transcribed_joinArgs_matches_plexapis``
holds the transcription against the installed plexapi by value.

NOTHING from the autoposter repository is imported. Nothing from
``tests/oracle/9b/`` is imported either: the ``uri_args`` this file wraps are
supplied by its caller, so the two oracles stay independent and the test file is
the only thing that knows about both.

Run:  python kometa_smart_collection.py
      -> prints the three pinned shapes for one demo query
"""
from urllib.parse import quote as _urllib_quote

# The server identity, fixed. A real ``_uriRoot()`` interpolates the server's
# machineIdentifier; pinning a literal is what keeps this comparison about
# ENCODING rather than about one server, and it is also why no committed file
# in this phase carries a real server address.
MACHINE_IDENTIFIER = "abc123"
SECTION_KEY = "2"


class Failed(Exception):
    """Kometa's own control-flow exception (modules/util.py). Its message text
    is Kometa's, verbatim, because the refusal wording is part of what this
    oracle records -- ours is deliberately different, and Task 2 says why."""


def quote(value):
    """plexapi's ``joinArgs`` quotes with ``safe=''`` -- every reserved
    character encoded, ``/`` and ``:`` included, and ``%`` itself encoded to
    ``%25``, which is where the double encoding comes from."""
    return _urllib_quote(str(value), safe="")


def joinArgs(args):
    """plexapi 4.18.2 ``plexapi/utils.py::joinArgs``, transcribed.

    Two details are load-bearing and neither is guessable from the output: the
    keys are sorted **case-insensitively** (``sectionId``, ``smart``, ``title``,
    ``type``, ``uri``), and only the VALUE is encoded, never the key.
    """
    if not args:
        return ""
    arglist = []
    for key in sorted(args, key=lambda x: x.lower()):
        value = str(args[key])
        arglist.append(f"{key}={quote(value)}")
    return f"?{'&'.join(arglist)}"


def uri_root():
    """plexapi 4.18.2 ``plexapi/server.py::PlexServer._uriRoot``."""
    return f"server://{MACHINE_IDENTIFIER}/com.plexapp.plugins.library"


def build_smart_filter(uri_args, section_key=SECTION_KEY):
    """modules/plex.py:1615-1616, verbatim.

    Note what it is NOT: it is not a URL a client fetches. It is a ``server://``
    uri Plex STORES and evaluates itself, which is the whole difference between
    a smart collection and a list one.
    """
    return f"{uri_root()}/library/sections/{section_key}/all{uri_args}"


def test_smart_filter(item_count, uri_args):
    """modules/plex.py:1580-1584.

    REMOVED: ``self.fetchItems(uri_args)``, the network read -- the caller
    supplies the count it would have returned. What is transcribed is the
    VERDICT, because the verdict is the behaviour 9c adopts (C8): fewer than one
    item is a refusal, not an empty collection.
    """
    if item_count < 1:
        raise Failed(f"Plex Error: No items for smart filter: {uri_args}")


def create_smart_collection(
    title, smart_type, uri_args, item_count, ignore_blank_results=False,
    section_key=SECTION_KEY,
):
    """modules/plex.py:1592-1600, as the request KEY it would POST.

    REMOVED: ``self._collection_by_title(title)`` and its "already exists;
    skipping creation" warning (:1593-1596) -- a Plex read, and this service
    resolves a title collision through ``reconcile.resolve_collision`` instead,
    which is a strictly stronger rule (ownership label, protected label,
    adoption) rather than a skip. REMOVED: ``self._query(..., post=True)``, the
    transport; the key is returned so it can be compared as a string.

    ``ignore_blank_results`` is transcribed because it is what Kometa's flow
    DOES, and 9c refuses to offer it (C8) -- recording the switch here is what
    makes that refusal a deliberate divergence rather than an omission.
    """
    if not ignore_blank_results:
        test_smart_filter(item_count, uri_args)
    args = {
        "type": smart_type,
        "title": title,
        "smart": 1,
        "sectionId": section_key,
        "uri": build_smart_filter(uri_args, section_key),
    }
    return f"/library/collections{joinArgs(args)}"


def update_smart_collection(rating_key, uri_args, item_count, section_key=SECTION_KEY):
    """modules/plex.py:1618-1620, as the request KEY it would PUT.

    Upstream calls ``test_smart_filter`` UNCONDITIONALLY here -- the
    ``ignore_blank_results`` escape hatch exists only on the create path. That
    is why 9c refusing at both is the stricter reading of upstream rather than a
    departure from it.
    """
    test_smart_filter(item_count, uri_args)
    args = {"uri": build_smart_filter(uri_args, section_key)}
    return f"/library/collections/{rating_key}/items{joinArgs(args)}"


def main():
    demo = "?type=1&sort=titleSort&contentRating=5&and=1&contentRating=7"
    print("uri  ", build_smart_filter(demo))
    print("post ", create_smart_collection("Oracle Collection", 1, demo, 7))
    print("put  ", update_smart_collection("12345", demo, 7))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Write the oracle test file's head**

Create `tests/test_smart_collection_oracle.py`:

```python
"""THE SMART-ENVELOPE ORACLE'S ASSERTIONS -- phase 9c's acceptance gate.

Sixteen configs. For each one, the ``POST /library/collections?...`` key Kometa
would issue to create the smart collection, produced by
``tests/oracle/9c/kometa_smart_collection.py`` and pinned below as data. Ours
must reproduce them byte for byte.

Fifteen of the sixteen are 9b's own configs, reused deliberately: their
``uri_args`` are already pinned in ``tests/test_collection_search_oracle.py``,
so anything that differs HERE is the envelope and nothing else. The sixteenth is
config 1's block with no ``sort_by`` at all, driven with
``default_sort="random"`` -- Kometa's ``smart_filter`` default
(modules/builder.py:1478), and the one behaviour 9c's builder has that
``plex_search`` does not.

## What the envelope adds, and why sixteen pins rather than one

``joinArgs`` encodes the whole ``uri`` VALUE with ``safe=''``. Every byte the 9b
query already encoded is encoded a second time: ``%3A`` -> ``%253A``, ``%2C`` ->
``%252C``, ``%20`` -> ``%2520``, ``%26`` -> ``%2526``; and ``!``, which 9b's
queries carry raw, becomes ``%21``. Which of those a config exercises depends
entirely on the config, so one pin would prove one encoding path.

| Config | The envelope byte it is the only pin for |
| --- | --- |
| 2, 12 | ``%253A`` -- a directional sort through both layers |
| 11 | ``%252C`` -- two sorts, joined |
| 8, 9 | ``%21`` -- a raw ``!`` in the query, encoded by the envelope alone |
| 10 | ``%2520`` and ``%2526`` -- a quoted string, quoted again |
| 15 | ``%21%253D`` -- ``!%3D`` through both layers |
| 16 | ``sort=random``, which only ``default_sort`` can produce |

## Do not edit a golden to make a test pass

If ours differs from a pinned string, ours is wrong. The strings came from the
driver, and ``test_the_smart_driver_still_produces_the_pinned_strings`` re-runs
it, so a driver that drifted from them is caught here too.
"""
import importlib.util
from pathlib import Path

import pytest

from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import build_search_url

SMART_DRIVER = Path(__file__).parent / "oracle" / "9c" / "kometa_smart_collection.py"
FILTER_DRIVER = Path(__file__).parent / "oracle" / "9b" / "kometa_build_filter.py"

# Shared BY VALUE with tests/test_collection_search_oracle.py and with the 9b
# driver, for the reason that file's docstring gives: the driver imports nothing
# from this repository and must not import anything into it either.
CHOICES = {
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
    ("label", "Overlay"): ("3",),
    ("collection", "The Fast and the Furious Collection"): ("77",),
}

MACHINE_IDENTIFIER = "abc123"
SECTION_KEY = "2"
TITLE = "Oracle Collection"
RATING_KEY = "12345"


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


def load(path):
    """Load an oracle driver by PATH, never by import: they live outside the
    package and must stay unimportable from ``src``. The idiom is
    ``tests/test_collection_search_oracle.py``'s own."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 6: Add the configs and the goldens to the same file**

Append to `tests/test_smart_collection_oracle.py`:

```python
# (id, libtype, params, default_sort) -- OUR spelling. The first fifteen are
# tests/test_collection_search_oracle.py's CONFIGS verbatim; config 7's ``2:30``
# is 9a's written duration form, which the driver is given as ``150``.
CONFIGS = [
    ("1-multi-value-tag", "movie", {"all": {"content_rating": ["PG-13", "R"]}}, None),
    ("2-any-base", "movie", {
        "any": {"studio": "A24", "year.gte": 2020},
        "sort_by": "critic_rating.desc", "limit": 25,
    }, None),
    ("3-list-nesting", "movie", {"all": {
        "content_rating": "PG-13",
        "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}],
    }}, None),
    ("4-mapping-nesting", "movie", {"all": {
        "year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8},
    }}, None),
    ("5-relative-dates", "movie", {"all": {
        "added": 30, "release.not": "6o", "last_played.not": "2y",
    }}, None),
    ("6-absolute-dates", "movie", {"all": {
        "release.after": "2000-01-01", "added.before": "12/25/2020",
    }}, None),
    ("7-duration", "movie", {"all": {"duration.gt": 90, "duration.lte": "2:30"}}, None),
    ("8-rated", "movie", {"all": {
        "critic_rating.rated": True, "audience_rating.rated": False,
    }}, None),
    ("9-booleans", "movie", {"all": {"unplayed": True, "progress": False}}, None),
    ("10-string-quoting", "movie", {"all": {
        "studio.begins": "Warner Bros",
        "studio.not": "Hallmark & Co",
        "studio.is": "A24",
    }}, None),
    ("11-several-sorts", "movie", {
        "all": {"year.gte": 2010},
        "sort_by": ["critic_rating.desc", "title.asc"], "limit": 100,
    }, None),
    ("12-show-rescoping", "show", {
        "all": {
            "genre": "Drama", "resolution": "1080", "audio_language": "en",
            "network": "HBO", "added.after": "2024-01-01",
        },
        "sort_by": "episode_added.desc", "limit": 10,
    }, None),
    ("13-language-expansion", "movie", {"all": {"audio_language": "es"}}, None),
    ("14-multi-value-under-any", "movie", {"any": {"content_rating": ["PG-13", "R"]}}, None),
    ("15-unreached-renders-and-rows", "movie", {"all": {
        "genre.not": "Horror",
        "studio.isnot": "A24",
        "studio.ends": "Pictures & Co",
        "label": "Overlay",
        "collection": "The Fast and the Furious Collection",
        "plays.gt": 3,
        "plays.lte": 10,
    }}, None),
    # 16: the ONLY thing a smart_filter does that a plex_search does not. No
    # ``sort_by`` is written, and Kometa's smart_filter call site passes
    # ``default_sort="random"`` (modules/builder.py:1478), so the query sorts
    # ``random`` rather than ``titleSort``. Config 1's block, unchanged, so the
    # diff against config 1's golden is exactly the sort and nothing else.
    ("16-random-default-sort", "movie", {"all": {"content_rating": ["PG-13", "R"]}}, "random"),
]

# The libtype key Kometa sends as ``type`` in BOTH the query and the POST args:
# 1 for movie, 2 for show (modules/plex.py:779-787). Kometa reads it off
# ``build_filter``'s first return value, which is what the driver returns too.
SMART_TYPE = {"movie": 1, "show": 2}

# KOMETA'S OWN ANSWERS, pinned as data. Produced by
# ``tests/oracle/9c/kometa_smart_collection.py`` wrapping
# ``tests/oracle/9b/kometa_build_filter.py``. Do not edit a string here to make
# a test pass: if ours differs, ours is wrong.
KOMETA_POST = {
    "1-multi-value-tag": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26contentRating%3D7",
    "2-any-base": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26limit%3D25%26sort%3Drating%253Adesc%26push%3D1%26studio%3DA24%26or%3D1%26year%253E%3D2020%26pop%3D1",
    "3-list-nesting": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26push%3D1%26studio%3DA24%26or%3D1%26year%253E%3D2020%26pop%3D1%26and%3D1%26push%3D1%26genre%3D1138%26pop%3D1",
    "4-mapping-nesting": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26year%253E%3D2000%26and%3D1%26push%3D1%26studio%3DA24%26and%3D1%26rating%253E%3D8.0%26pop%3D1",
    "5-relative-dates": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26addedAt%253E%253E%3D-30d%26and%3D1%26originallyAvailableAt%253C%253C%3D-6mon%26and%3D1%26lastViewedAt%253C%253C%3D-2y",
    "6-absolute-dates": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26originallyAvailableAt%253E%253E%3D2000-01-01%26and%3D1%26addedAt%253C%253C%3D2020-12-25",
    "7-duration": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26duration%253E%253E%3D5400000.0%26and%3D1%26duration%253C%3D9000000.0",
    "8-rated": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26rating%21%3D-1%26and%3D1%26audienceRating%3D-1",
    "9-booleans": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26unwatched%3D1%26and%3D1%26inProgress%21%3D1",
    "10-string-quoting": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26studio%253C%3DWarner%2520Bros%26and%3D1%26studio%21%3DHallmark%2520%2526%2520Co%26and%3D1%26studio%253D%3DA24",
    "11-several-sorts": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26limit%3D100%26sort%3Drating%253Adesc%252CtitleSort%26year%253E%3D2010",
    "12-show-rescoping": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=2&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D2%26limit%3D10%26sort%3Depisode.addedAt%253Adesc%26show.genre%3D9%26and%3D1%26episode.resolution%3D1080%26and%3D1%26episode.audioLanguage%3Den%26and%3D1%26show.network%3D42%26and%3D1%26show.addedAt%253E%253E%3D2024-01-01",
    "13-language-expansion": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26audioLanguage%3Des-419%26and%3D1%26audioLanguage%3Des-MX%26and%3D1%26audioLanguage%3Dspa",
    "14-multi-value-under-any": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26push%3D1%26contentRating%3D5%26or%3D1%26contentRating%3D7%26pop%3D1",
    "15-unreached-renders-and-rows": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26genre%21%3D1138%26and%3D1%26studio%21%253D%3DA24%26and%3D1%26studio%253E%3DPictures%2520%2526%2520Co%26and%3D1%26label%3D3%26and%3D1%26collection%3D77%26and%3D1%26viewCount%253E%253E%3D3%26and%3D1%26viewCount%253C%3D10",
    "16-random-default-sort": "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3Drandom%26contentRating%3D5%26and%3D1%26contentRating%3D7",
}

# The UPDATE key, pinned for the two configs whose difference is the whole point
# of having a second shape at all: the same ``uri`` value, a different path, and
# no ``type``/``title``/``smart``/``sectionId`` at all.
KOMETA_PUT = {
    "1-multi-value-tag": "/library/collections/12345/items?uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26contentRating%3D7",
    "16-random-default-sort": "/library/collections/12345/items?uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3Drandom%26contentRating%3D5%26and%3D1%26contentRating%3D7",
}


def our_query(libtype, params, default_sort):
    """OUR query string for one config -- 9b's builder, with 9c's default sort.

    ``sort_by or (default_sort,)`` is the whole of C5's sort delta, written here
    the same way Task 3's builder writes it, so this file proves the exact
    expression that ships rather than an equivalent one.
    """
    base = "all" if "all" in params else "any"
    group = parse_filters(params[base], field="params", searching=True, base=base)
    sort_by = params.get("sort_by") or ()
    if isinstance(sort_by, str):
        sort_by = [sort_by]
    if not sort_by and default_sort:
        sort_by = (default_sort,)
    return build_search_url(
        group, libtype=libtype, sort_by=sort_by,
        limit=params.get("limit"), resolve_tag=resolve,
    )
```

- [ ] **Step 7: Add the assertions to the same file**

Append to `tests/test_smart_collection_oracle.py`:

```python
@pytest.mark.parametrize(
    ("name", "libtype", "params", "default_sort"), CONFIGS, ids=[c[0] for c in CONFIGS]
)
def test_the_envelope_is_byte_identical_to_kometas(name, libtype, params, default_sort):
    """OUR query, wrapped by the driver's envelope, against Kometa's own key.

    The envelope here is the DRIVER's, because Task 2 has not been written yet:
    this task's subject is the query reaching the envelope unchanged. Task 2's
    ``test_the_create_post_is_byte_identical_to_the_oracles`` then asserts the
    SHIPPED envelope against these same pinned strings, which is what makes the
    pair a gate rather than a round trip.
    """
    driver = load(SMART_DRIVER)
    key = driver.create_smart_collection(
        TITLE, SMART_TYPE[libtype], our_query(libtype, params, default_sort),
        item_count=7,
    )
    assert key == KOMETA_POST[name]


@pytest.mark.parametrize("name", sorted(KOMETA_PUT), ids=sorted(KOMETA_PUT))
def test_the_update_key_is_byte_identical_to_kometas(name):
    driver = load(SMART_DRIVER)
    libtype, params, default_sort = next(
        (c[1], c[2], c[3]) for c in CONFIGS if c[0] == name
    )
    key = driver.update_smart_collection(
        RATING_KEY, our_query(libtype, params, default_sort), item_count=7,
    )
    assert key == KOMETA_PUT[name]


def test_the_smart_driver_still_produces_the_pinned_strings():
    """The goldens above are data, and the drivers that produced them are
    tracked, reviewable and editable -- so they can drift from them with nothing
    noticing. This runs BOTH drivers end to end, ours nowhere in sight, and is
    the only place ``default_sort="random"`` is exercised through Kometa's own
    ``build_filter`` rather than through our sort table.

    Both drivers import only the standard library, open no socket and read no
    clock on any path these configs reach, so this runs in-process rather than
    behind a marker: a skipped guard is not a guard.
    """
    smart = load(SMART_DRIVER)
    build = load(FILTER_DRIVER)
    kometa_configs = {
        name: (libtype, params, default_sort)
        for name, libtype, params, default_sort in CONFIGS
    }
    # Kometa's own spelling of config 7's second duration -- ``2:30`` is 9a's
    # written form and has no Kometa equivalent; the MINUTE VALUE is identical,
    # which is the point of the comparison.
    kometa_configs["7-duration"] = (
        "movie", {"all": {"duration.gt": 90, "duration.lte": 150}}, None,
    )
    for name, pinned in KOMETA_POST.items():
        libtype, params, default_sort = kometa_configs[name]
        type_key, uri_args = build.build_filter(
            "smart_filter", params, libtype, default_sort=default_sort
        )
        assert smart.create_smart_collection(
            TITLE, type_key, uri_args, item_count=7
        ) == pinned, name
        if name in KOMETA_PUT:
            assert smart.update_smart_collection(
                RATING_KEY, uri_args, item_count=7
            ) == KOMETA_PUT[name], name


def test_the_transcribed_joinArgs_matches_plexapis():
    """The driver transcribes plexapi's ``joinArgs`` rather than importing it,
    so the two are held equal here -- by value, on the exact argument dict the
    create path builds. Case-insensitive key ordering and value-only encoding
    would each survive a wrong transcription of the other, which is why the
    assertion is on the whole string.
    """
    from plexapi.utils import joinArgs

    driver = load(SMART_DRIVER)
    args = {
        "type": 1, "title": TITLE, "smart": 1, "sectionId": SECTION_KEY,
        "uri": driver.build_smart_filter("?type=1&sort=rating%3Adesc&studio=A24"),
    }
    assert driver.joinArgs(args) == joinArgs(args)


def test_the_transcribed_uri_root_matches_plexapis_shape():
    """``_uriRoot`` is a PRIVATE plexapi method the create path depends on.
    ``tests/test_plexapi_collection_contract.py`` pins that it still exists and
    still interpolates the machine identifier; this pins that the driver spells
    the same string, so an upstream change fails in both places rather than
    silently leaving the oracle describing a server nobody has.
    """
    import inspect

    from plexapi.server import PlexServer

    driver = load(SMART_DRIVER)
    source = inspect.getsource(PlexServer._uriRoot)
    assert "com.plexapp.plugins.library" in source
    assert driver.uri_root() == (
        "server://%s/com.plexapp.plugins.library" % driver.MACHINE_IDENTIFIER
    )


def test_a_filter_matching_nothing_refuses_in_kometa_too():
    """C8 adopts this verdict, so it is pinned rather than assumed. Both paths:
    create refuses unless ``ignore_blank_results`` is set, update refuses
    unconditionally -- which is why 9c refusing at both is the stricter reading
    of upstream and not a departure from it.
    """
    driver = load(SMART_DRIVER)
    with pytest.raises(driver.Failed):
        driver.create_smart_collection(TITLE, 1, "?type=1&sort=titleSort&year=1900", 0)
    with pytest.raises(driver.Failed):
        driver.update_smart_collection(RATING_KEY, "?type=1&sort=titleSort&year=1900", 0)
    # The escape hatch upstream offers and 9c refuses to offer (C8), recorded so
    # the refusal reads as a decision rather than as something nobody noticed.
    assert driver.create_smart_collection(
        TITLE, 1, "?type=1&sort=titleSort&year=1900", 0, ignore_blank_results=True,
    ).startswith("/library/collections?")


def test_no_envelope_carries_a_token_or_includeCollections():
    """The two things that must never appear in a stored smart filter: a token
    (the uri is persisted BY PLEX and would outlive any rotation) and
    ``includeCollections``, which does not enrich a listing -- it MIXES
    Collection objects into the result set (roadmap Notes-for-9b item 1)."""
    for key in list(KOMETA_POST.values()) + list(KOMETA_PUT.values()):
        assert "X-Plex-Token" not in key
        assert "includeCollections" not in key


def test_config_16_differs_from_config_1_only_in_the_sort():
    """The default-sort case is worth pinning as a DIFFERENCE, not only as a
    string: config 16 is config 1's block with no ``sort_by``, so any byte that
    moves other than ``titleSort`` -> ``random`` is a defect in the parameter
    rather than in the sort table."""
    assert KOMETA_POST["1-multi-value-tag"].replace(
        "sort%3DtitleSort", "sort%3Drandom"
    ) == KOMETA_POST["16-random-default-sort"]
```

- [ ] **Step 8: Run the new file**

```bash
docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_smart_collection_oracle.py; echo EXIT=$?'
```

Expected: `EXIT=0`, 24 passed (16 envelope + 2 update + 6 file-level). Record the
real count. If any config is red, **ours is wrong**: report the diff, do not
touch the golden.

- [ ] **Step 9: Prove the gate can fail (mutation 1 — the parameter)**

```bash
cp tests/oracle/9b/kometa_build_filter.py /tmp/kbf.bak
python - <<'PY'
from pathlib import Path
p = Path("tests/oracle/9b/kometa_build_filter.py")
s = p.read_text()
s = s.replace("sort.append(default_sort if default_sort else type_default_sort)",
              "sort.append(type_default_sort)")
p.write_bytes(s.encode())
PY
docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_smart_collection_oracle.py::test_the_smart_driver_still_produces_the_pinned_strings; echo EXIT=$?'
```

Expected: **FAIL**, with `16-random-default-sort` named by the assertion.
Restore and re-verify:

```bash
cp /tmp/kbf.bak tests/oracle/9b/kometa_build_filter.py
cmp /tmp/kbf.bak tests/oracle/9b/kometa_build_filter.py && echo RESTORED
docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_smart_collection_oracle.py; echo EXIT=$?'
```

Expected: `RESTORED`, then `EXIT=0`. Paste **both** real outputs into the report.

- [ ] **Step 10: Prove the gate can fail (mutation 2 — one encoding byte)**

```bash
cp tests/test_smart_collection_oracle.py /tmp/tsco.bak
python - <<'PY'
from pathlib import Path
p = Path("tests/test_smart_collection_oracle.py")
s = p.read_text()
s = s.replace("sort%3Drating%253Adesc%26push", "sort%3Drating%3Adesc%26push", 1)
p.write_bytes(s.encode())
PY
docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q "tests/test_smart_collection_oracle.py::test_the_envelope_is_byte_identical_to_kometas[2-any-base]"; echo EXIT=$?'
```

Expected: **FAIL** — single-vs-double encoding of `:` in the sort is exactly the
defect these pins exist for. Restore:

```bash
cp /tmp/tsco.bak tests/test_smart_collection_oracle.py
cmp /tmp/tsco.bak tests/test_smart_collection_oracle.py && echo RESTORED
```

- [ ] **Step 11: Full suite, golden and ruff**

```bash
docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9ct1-full test \
    sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9ct1-full
docker logs p9ct1-full | tail -40
docker compose -p p9ct1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9ct1 down
```

Expected: `EXIT=0` on the suite (the pre-existing count plus this file's) and
`All checks passed!` from ruff. The golden test runs inside the full suite;
quote its line in the report.

- [ ] **Step 12: Commit**

```bash
git add tests/oracle/9b/kometa_build_filter.py tests/oracle/9c/kometa_smart_collection.py tests/test_smart_collection_oracle.py
git commit --no-gpg-sign -m "test(smart): the smart-collection envelope oracle

Sixteen POST keys and two PUT keys, produced by a standalone transcription of
Kometa v2.4.8's build_smart_filter/create_smart_collection/
update_smart_collection and plexapi 4.18.2's joinArgs, pinned as data. The 9b
driver regains upstream's default_sort parameter (modules/builder.py:4093,
:4140-4141) so the smart_filter call site's default_sort=random is reachable;
all fifteen 9b goldens are unchanged."
```

---

### Task 2: The reconciler — one smart collection, from a URL string

**Files:**

- Create: `src/autoposter/collections/smart.py`
- Create: `tests/test_collection_smart.py`
- Modify: `src/autoposter/collections/reconcile.py` (add `shape_conflict`, after `resolve_collision` at `:183`)
- Modify: `src/autoposter/collections/lists.py` (call it, in `reconcile_list_collection`)
- Modify: `tests/test_plexapi_collection_contract.py` (pin `Collection.smart` and `LibrarySection.collection`)

**Interfaces:**

- Consumes: `reconcile.resolve_collision`, `reconcile.apply_collection_settings`, `reconcile._edit_collection_summary`, `reconcile.LIBTYPES`, `posters.apply_poster`, `posters.posters_enabled`, `lists._settings_parts`, `db.models.ManagedCollection`, `plexapi.utils.joinArgs`.
- Produces, for Task 3:
  - `smart.reconcile_smart_collection(session, section, library, library_type, title, url, label, *, summary=None, dry_run=True, existing=None, adopt=False, adopt_from=None, adopt_removes_prior_label=False, protect_labels=None, http=None, config=None, settings=None) -> list[str]`
  - `smart.SmartFilterMatchedNothing` (C8), `smart.SmartCollectionUnavailable` (transport, class-name-only)
  - `smart.smart_filter_uri(server, section_key, url) -> str`
  - `smart.smart_definition_hash(url, summary, settings=None) -> str`
- Produces, for Task 4: rows with `kind="smart"` in `managed_collections`.

**Why the reconciler comes before the builder.** It takes a URL **string**, not a
builder and not a definition's params, so its whole surface is testable with a
fake section and no registry entry at all. The builder is then four statements
and a call.

- [ ] **Step 1: Write the failing shape-conflict test (C11)**

Add to `tests/test_collection_reconcile.py`, at the end of the file:

```python
# --- C11: a definition that changes shape under an existing collection -------


def test_shape_conflict_is_silent_when_the_shapes_agree():
    from autoposter.collections.reconcile import shape_conflict

    smart = FakeCollection("Recent Horror")
    smart.smart = True
    assert shape_conflict(smart, "Recent Horror", want_smart=True) is None

    dumb = FakeCollection("Hand Picked")
    dumb.smart = False
    assert shape_conflict(dumb, "Hand Picked", want_smart=False) is None


def test_shape_conflict_names_both_shapes_and_the_manual_path():
    """Kometa deletes and recreates here (modules/builder.py:1768-1772). This
    service refuses, because a silent delete crosses every guard the delete
    sweep is built out of -- and the message has to leave the operator able to
    act, which means naming what the collection IS, what the definition BUILDS,
    and the two ways out."""
    from autoposter.collections.reconcile import shape_conflict

    smart = FakeCollection("Recent Horror")
    smart.smart = True
    message = shape_conflict(smart, "Recent Horror", want_smart=False)
    assert message is not None
    assert "Recent Horror" in message
    assert "smart" in message and "list" in message
    assert "delete" in message


def test_shape_conflict_treats_a_missing_attribute_as_a_list_collection():
    """``Collection.smart`` is cast from an XML attribute that defaults to
    ``'0'`` (pinned in tests/test_plexapi_collection_contract.py), and a fake or
    a partially-loaded object may not carry it at all. Absent means NOT smart --
    the same defensiveness ``has_label`` uses for ``labels``."""
    from autoposter.collections.reconcile import shape_conflict

    bare = FakeCollection("Hand Picked")
    assert shape_conflict(bare, "Hand Picked", want_smart=False) is None
    assert shape_conflict(bare, "Hand Picked", want_smart=True) is not None
```

- [ ] **Step 2: Run it — it must fail**

```bash
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_reconcile.py -k shape_conflict; echo EXIT=$?'
```

Expected: FAIL — `ImportError: cannot import name 'shape_conflict'`.

- [ ] **Step 3: Add `shape_conflict` to `src/autoposter/collections/reconcile.py`**

Insert immediately after `resolve_collision` (which ends at `:183`) and before
the `COLLECTION_MODES` comment block:

```python
def shape_conflict(collection, title: str, want_smart: bool) -> str | None:
    """Roadmap 9c / C11: an existing collection whose SHAPE the definition changed.

    A Plex collection is either smart -- Plex evaluates a stored filter and owns
    the membership -- or it is a list, whose membership this service maintains
    item by item. A definition that switches ``builder:`` between the two kinds
    is asking for the second thing to happen to a collection that is the first,
    and there is no edit that converts one into the other.

    Kometa's answer is to DELETE the collection and recreate it
    (modules/builder.py:1768-1772), silently, in the middle of a pass. That
    crosses the whole design of this service's delete guards: nothing is deleted
    without an ownership label, a ``managed_collections`` row, a
    ``delete_unconfigured`` opt-in and a ``max_deletes`` cap, and a conversion
    would bypass all four. So this refuses, and names the two manual paths --
    which are also the two the operator would want: keep the collection and
    rename the definition, or delete the collection and let the definition
    rebuild it.

    Returns the refusal message, or ``None`` when the shapes agree -- the
    ``(ok, message)`` idiom ``resolve_collision`` above already uses, and not an
    exception, deliberately: a definition's own configuration error must cost
    that definition its pass and nothing else, and an exception out of either
    reconciler reaches ``reconcile_libraries``' per-library rollback, which
    would undo the OTHER definitions' work in the same library.

    A missing ``smart`` attribute reads as a list collection. plexapi casts it
    from an XML attribute that defaults to ``'0'`` (pinned in
    ``tests/test_plexapi_collection_contract.py``), so absence is the same
    defensiveness ``has_label`` applies to ``labels``.
    """
    is_smart = bool(getattr(collection, "smart", False))
    if is_smart == want_smart:
        return None
    have, wanted = ("smart", "list") if is_smart else ("list", "smart")
    return (
        "shape conflict: %r already exists in Plex as a %s collection and this "
        "definition builds a %s one. Plex cannot convert one into the other, and "
        "this service will not delete and recreate it. Either rename the "
        "definition so it builds a new collection, or delete %r in Plex and let "
        "the next pass create it." % (title, have, wanted, title)
    )
```

- [ ] **Step 4: Run it — green**

```bash
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_reconcile.py -k shape_conflict; echo EXIT=$?'
```

Expected: `EXIT=0`, 3 passed.

- [ ] **Step 5: Wire the list side of the refusal**

In `src/autoposter/collections/lists.py`, extend the existing import:

```python
from autoposter.collections.reconcile import (
    _edit_collection_summary,
    apply_collection_settings,
    resolve_collision,
)
```

to:

```python
from autoposter.collections.reconcile import (
    _edit_collection_summary,
    apply_collection_settings,
    resolve_collision,
    shape_conflict,
)
```

and, inside `reconcile_list_collection`, immediately **before** the existing
`if collection is not None:` block that calls `resolve_collision`, insert:

```python
    # C11, the list half. Checked BEFORE ``resolve_collision`` because a smart
    # collection this service already owns would otherwise pass the ownership
    # check and go on to ``addItems``, which Plex answers for a smart collection
    # by doing nothing useful and reporting success.
    if collection is not None:
        conflict = shape_conflict(collection, title, want_smart=False)
        if conflict is not None:
            return [conflict]
```

- [ ] **Step 6: Write the failing reconciler tests**

Create `tests/test_collection_smart.py`:

```python
"""Reconciling ONE Plex-native smart collection from a built search URL.

The fakes mirror plexapi's real signatures, which are pinned separately in
``tests/test_plexapi_collection_contract.py``. The pinned request strings are a
COPY of ``tests/test_smart_collection_oracle.py``'s, shared by value and not by
import -- an oracle and the code it judges must not share a symbol, which is the
rule ``test_the_oracles_vocabulary_fixture_matches_this_files_copy`` already
states about ``CHOICES``.
"""
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from autoposter.collections.smart import (
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    reconcile_smart_collection,
    smart_definition_hash,
    smart_filter_uri,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"
MACHINE_IDENTIFIER = "abc123"
SECTION_KEY = "2"
TITLE = "Oracle Collection"

# Config 1 of the oracle: the query, and the two request keys its envelope
# produces. Copied from tests/test_smart_collection_oracle.py by value.
URL = "?type=1&sort=titleSort&contentRating=5&and=1&contentRating=7"
KOMETA_POST = "/library/collections?sectionId=2&smart=1&title=Oracle%20Collection&type=1&uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26contentRating%3D7"
KOMETA_PUT = "/library/collections/12345/items?uri=server%3A%2F%2Fabc123%2Fcom.plexapp.plugins.library%2Flibrary%2Fsections%2F2%2Fall%3Ftype%3D1%26sort%3DtitleSort%26contentRating%3D5%26and%3D1%26contentRating%3D7"


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeServer:
    """``section._server``: the three internals the raw POST/PUT reach for."""

    def __init__(self):
        self.queries = []
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://%s/com.plexapp.plugins.library" % MACHINE_IDENTIFIER

    def query(self, key, method=None, **kwargs):
        self.queries.append((key, method))


class FakeCollection:
    """Mirrors plexapi's lazy ``labels``/``fields``: empty until ``reload()``."""

    def __init__(self, title, labels=(), rating_key="12345", smart=True, summary=None):
        self.title = title
        self.ratingKey = rating_key
        self.smart = smart
        self.summary = summary
        self.titleSort = None
        self.collectionMode = None
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": False})()]
        self._fields = []
        self.labels_added = []
        self.sort_titles = []
        # ``_edit_collection_summary`` writes through ``collection._server``.
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
        self.sort_titles.append(value)
        self.titleSort = value

    def query(self, key, method=None, **kwargs):
        """The item-level summary PUT, through this collection's own server."""
        self._server.query(key, method)
        self.summary = parse_qs(urlsplit(key).query)["summary.value"][0]
        self._real_fields[0].locked = True


class FakeSection:
    def __init__(self, matches=1, existing=(), key=SECTION_KEY):
        self.key = key
        self._server = FakeServer()
        self._existing = {c.title: c for c in existing}
        self._matches = matches
        self.fetched = []

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        """plexapi's re-read after the raw POST. The fake stands in for the
        collection Plex created, so it comes back SMART."""
        if title not in self._existing:
            self._existing[title] = FakeCollection(title, smart=True)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        self.fetched.append(path)
        if isinstance(self._matches, Exception):
            raise self._matches
        return [FakeItem(str(i)) for i in range(self._matches)]


async def _row(session, library, title):
    return (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()
```

- [ ] **Step 7: Add the assertions to the same file**

Append to `tests/test_collection_smart.py`:

```python
def test_the_uri_is_the_one_kometa_stores():
    """``build_smart_filter``, modules/plex.py:1615-1616. A ``server://`` uri,
    not a URL -- Plex stores this and evaluates it itself."""
    assert smart_filter_uri(FakeServer(), SECTION_KEY, URL) == (
        "server://abc123/com.plexapp.plugins.library/library/sections/2/all" + URL
    )


async def test_the_create_post_is_byte_identical_to_the_oracles(session):
    """THE gate this task exists for. The shipped envelope, against the string
    Kometa's own ``create_smart_collection`` produces for the same query
    (tests/test_smart_collection_oracle.py, config 1)."""
    section = FakeSection(matches=7)
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert [key for key, _ in section._server.queries] == [KOMETA_POST]
    assert section._server.queries[0][1] == "POST"
    assert any("created" in action for action in actions)


async def test_the_update_put_is_byte_identical_to_the_oracles(session):
    """The second shape: same uri value, different path, and none of the
    create-only arguments."""
    existing = FakeCollection(TITLE, labels=[LABEL], rating_key="12345", smart=True)
    section = FakeSection(matches=7, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash="stale",
    ))
    await session.flush()

    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert [key for key, _ in section._server.queries] == [KOMETA_PUT]
    assert section._server.queries[0][1] == "PUT"
    assert any("updated" in action for action in actions)


async def test_a_filter_matching_nothing_refuses_at_create(session):
    """C8, without ``ignore_blank_results``. Kometa offers the switch; an
    error-downgrade switch is the ``validate:`` class 9b already refused."""
    section = FakeSection(matches=0)
    with pytest.raises(SmartFilterMatchedNothing) as caught:
        await reconcile_smart_collection(
            session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
        )
    assert "widen" in str(caught.value).lower()
    assert section._server.queries == [], "nothing may be written after the refusal"
    assert await _row(session, "Movies", TITLE) is None


async def test_a_filter_matching_nothing_refuses_at_update_too(session):
    """The update path refuses on zero as well -- which is upstream's own shape
    (modules/plex.py:1618-1620 calls test_smart_filter unconditionally) and the
    one that matters more: a library that lost every match would otherwise have
    its filter quietly rewritten to one that finds nothing."""
    existing = FakeCollection(TITLE, labels=[LABEL], smart=True)
    section = FakeSection(matches=0, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash="stale",
    ))
    await session.flush()

    with pytest.raises(SmartFilterMatchedNothing):
        await reconcile_smart_collection(
            session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
        )
    assert section._server.queries == []


async def test_the_match_probe_wraps_a_plex_failure_by_class_name_only(session):
    """A tokenised URL can ride in a plexapi exception's message. The engine's
    log line prints the class name and nothing else, so this must too --
    ``plex_search.py:351-359`` is the shape."""
    boom = RuntimeError("https://plex.example:32400/library?X-Plex-Token=SECRET")
    section = FakeSection(matches=boom)
    with pytest.raises(SmartCollectionUnavailable) as caught:
        await reconcile_smart_collection(
            session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
        )
    assert "RuntimeError" in str(caught.value)
    assert "SECRET" not in str(caught.value)
    assert "X-Plex-Token" not in str(caught.value)


async def test_a_dry_run_probes_but_writes_nothing(session):
    """The probe is a READ, so it runs under dry_run too: an operator seeing the
    preview before enabling apply_to_plex should learn that the filter matches
    nothing THEN, not on the first applied pass."""
    section = FakeSection(matches=3)
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=True,
    )
    assert section.fetched == ["/library/sections/2/all" + URL]
    assert section._server.queries == []
    assert any("would create" in action and "3" in action for action in actions)
    assert await _row(session, "Movies", TITLE) is None


async def test_an_unchanged_definition_writes_nothing_and_probes_nothing(session):
    """C10: the hash is over the BUILT URI, so a pass whose URI is unchanged
    short-circuits before the probe. That is what keeps a smart definition from
    costing one Plex query per pass forever."""
    existing = FakeCollection(TITLE, labels=[LABEL], smart=True)
    section = FakeSection(matches=7, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash=smart_definition_hash(URL, None),
    ))
    await session.flush()

    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert actions == []
    assert section.fetched == []
    assert section._server.queries == []


async def test_the_hash_is_over_the_uri_and_not_over_plexs_echo(session):
    """C10 in one assertion: two different URIs hash differently and the same
    URI hashes identically, and nothing anywhere reads ``Collection.content``.
    Plex-side manual edits to a smart filter therefore go undetected -- exactly
    as they do for the Common Sense family today, whose hash is over the desired
    state too."""
    assert smart_definition_hash(URL, None) == smart_definition_hash(URL, None)
    assert smart_definition_hash(URL, None) != smart_definition_hash(
        URL.replace("titleSort", "random"), None
    )
    assert smart_definition_hash(URL, "a summary") != smart_definition_hash(URL, None)


async def test_a_changed_summary_alone_re_applies(session):
    """The summary is in the hash for the reason ``lists._settings_parts``
    exists: a pass short-circuits on the hash, so a definition whose only edit
    was the summary would otherwise be recognised as current and the edit would
    never be applied."""
    existing = FakeCollection(TITLE, labels=[LABEL], smart=True, summary="old")
    section = FakeSection(matches=7, existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title=TITLE, kind="smart", plex_rating_key="12345",
        definition_hash=smart_definition_hash(URL, "old"),
    ))
    await session.flush()

    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL,
        summary="new", dry_run=False,
    )
    assert existing.summary == "new"


async def test_the_ride_along_settings_are_applied_on_create(session):
    """Row 104's half that lives on the create path: the definition's labels and
    sort title reach the collection object, through the SAME
    ``apply_collection_settings`` both other reconcilers call."""
    section = FakeSection(matches=7)
    definition = CollectionDefinition(
        title=TITLE, builder="plex_id", params={"ids": ["1"]},
        labels=["Curated"], sort_title="!300_Recent",
    )
    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL,
        dry_run=False, settings=definition,
    )
    created = section.collection(TITLE)
    assert LABEL in created.labels_added
    assert "Curated" in created.labels_added
    assert created.sort_titles == ["!300_Recent"]


async def test_a_row_is_written_with_kind_smart(session):
    section = FakeSection(matches=7)
    await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    row = await _row(session, "Movies", TITLE)
    # Read into locals before anything can expire them -- the async-SQLAlchemy
    # rule in this plan's Global Constraints.
    kind, rating_key, digest = row.kind, row.plex_rating_key, row.definition_hash
    assert kind == "smart"
    assert rating_key == "12345"
    assert digest == smart_definition_hash(URL, None)


async def test_a_collection_that_is_not_ours_is_left_alone(session):
    """The ownership rule, unchanged and shared: ``resolve_collision`` is the
    one place it lives, so it cannot drift between the three reconcilers."""
    stranger = FakeCollection(TITLE, labels=["SomeoneElse"], smart=True)
    section = FakeSection(matches=7, existing=[stranger])
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert section._server.queries == []
    assert any("conflict" in action for action in actions)
    assert await _row(session, "Movies", TITLE) is None


async def test_a_list_collection_under_a_smart_definition_refuses(session):
    """C11, the smart half. The collection exists and is ours, and it is a LIST
    collection -- so the definition changed shape and this refuses rather than
    deleting it the way Kometa does (modules/builder.py:1768-1772)."""
    dumb = FakeCollection(TITLE, labels=[LABEL], smart=False)
    section = FakeSection(matches=7, existing=[dumb])
    actions = await reconcile_smart_collection(
        session, section, "Movies", "Movie", TITLE, URL, LABEL, dry_run=False,
    )
    assert section._server.queries == []
    assert len(actions) == 1
    assert "shape conflict" in actions[0]
    assert await _row(session, "Movies", TITLE) is None
```

- [ ] **Step 8: Run them — they must fail**

```bash
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_smart.py; echo EXIT=$?'
```

Expected: FAIL at collection — `ModuleNotFoundError: No module named
'autoposter.collections.smart'`.

- [ ] **Step 9: Write `src/autoposter/collections/smart.py`**

```python
"""One Plex-native smart collection, reconciled from a built search URL.

The third reconciler in this package, and the one that owns the least. Its
siblings:

- ``reconcile.py`` -- the Common Sense age buckets, a FAMILY of smart
  collections whose filters are derived from the library's own ratings and
  written through plexapi's ``createCollection(smart=True)``.
- ``lists.py`` -- every collection with a membership this service maintains.
- here -- ONE smart collection per definition, whose filter is the operator's
  own ``smart_filter`` query, written as a RAW POST.

**Why a raw POST rather than plexapi's ``filters=`` (9c decision C1).** The
phase chain made exactly one grammar byte-provable: 9b's ``build_search_url``,
gated by an oracle against Kometa's own ``build_filter``, and 9c's
``tests/test_smart_collection_oracle.py`` extends that gate through the
envelope. Handing plexapi a ``filters`` dict instead would add a second
translation layer with its own correctness surface AND different semantics --
plexapi comma-joins a multi-value tag, which Plex reads as OR, where 9b's
grammar emits one term per value joined by the block's own conjunction, which
under ``all:`` is an AND. The same config would build a different collection.
So the URI this module sends is the oracle-proven string, and plexapi is used
for everything that is not the URI: the re-read, the labels, the sort title, the
display mode, the summary.

**What is deliberately NOT here.**

- No comparison against the ``content`` attribute Plex echoes back on a smart
  collection (C10) -- spelled without the leading dot here on purpose, because
  ``test_no_reconciler_reads_a_collections_content_echo`` greps this file for
  exactly that read. Drift is detected the way the Common Sense family does --
  a ``definition_hash`` over the DESIRED state, stored in
  ``managed_collections`` -- which means a Plex-side manual edit to a smart
  filter goes undetected, exactly as it does for that family today. That is a
  documented consequence, not an oversight: hashing the echo would make every
  pass depend on a byte-for-byte round trip nobody has verified.
- No ``ignore_blank_results`` (C8). Kometa's switch downgrades "this filter
  matches nothing" from an error to a log line; an error-downgrade switch is the
  ``validate:`` class 9b refused.
- No delete-and-recreate on a shape change (C11). See
  ``reconcile.shape_conflict``.
"""
import hashlib
import logging

import httpx
from plexapi.utils import joinArgs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.posters import apply_poster, posters_enabled
from autoposter.collections.reconcile import (
    LIBTYPES,
    _edit_collection_summary,
    apply_collection_settings,
    resolve_collision,
    shape_conflict,
)
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)

__all__ = [
    "SmartCollectionUnavailable",
    "SmartFilterMatchedNothing",
    "create_smart_collection",
    "reconcile_smart_collection",
    "smart_definition_hash",
    "smart_filter_uri",
    "update_smart_collection",
]


class SmartFilterMatchedNothing(Exception):
    """The query answered with zero items, at create or at update (C8).

    Its own class so the caller can turn it into one definition's refusal rather
    than a dead pass -- and so the engine's class-name-only log line says which
    kind of failure this was.
    """


class SmartCollectionUnavailable(Exception):
    """Plex would not answer at all.

    Never carries a Plex exception's message: those can contain a tokenised URL.
    The same shape as ``plex_search.PlexSearchUnavailable``.
    """


def smart_filter_uri(server, section_key, url: str) -> str:
    """Kometa's ``build_smart_filter`` (modules/plex.py:1615-1616).

    Not a URL a client fetches -- a ``server://`` uri Plex STORES and evaluates
    itself, which is the whole difference between a smart collection and a list
    one. ``server._uriRoot()`` is private plexapi and is pinned in
    ``tests/test_plexapi_collection_contract.py`` for exactly that reason.
    """
    return "%s/library/sections/%s/all%s" % (server._uriRoot(), section_key, url)


def require_matches(section, url: str) -> int:
    """Kometa's ``test_smart_filter`` (modules/plex.py:1580-1584), C8's half.

    Returns how many items the filter matches right now, and refuses at zero.
    The count is not stored anywhere -- a smart collection's membership is
    Plex's and changes without us -- it exists only so the action string can say
    what the operator's filter actually found.

    The catch is blanket and class-name-only, which is ``plex_search.build``'s
    own reasoning (:342-359): this call returns the collection's whole reason to
    exist, so any failure ends the reconcile the same way, and nothing is
    memoised here that a coding bug could be mistaken for a library fact.
    """
    try:
        items = section.fetchItems("/library/sections/%s/all%s" % (section.key, url))
    except Exception as error:  # class name only, never the message
        raise SmartCollectionUnavailable(
            "Plex would not answer this smart filter: %s" % type(error).__name__
        ) from None
    if not items:
        raise SmartFilterMatchedNothing(
            "this search matches nothing in this library right now, and a smart "
            "collection built from it would be permanently empty. Widen the "
            "filter, or narrow the definition with `libraries:` so it only "
            "targets libraries that can answer it. (Kometa refuses here too -- "
            "modules/plex.py:1580-1584 -- behind an `ignore_blank_results` "
            "switch this service deliberately does not offer.)"
        )
    return len(items)


def create_smart_collection(section, libtype: str, title: str, url: str):
    """Kometa's ``create_smart_collection`` (modules/plex.py:1592-1600).

    The same raw-POST idiom ``reconcile.create_blank_collection`` needs and for
    an adjacent reason: plexapi's ``createCollection(smart=True)`` takes a
    ``filters`` dict, and there is no entry point that accepts a uri. The three
    plexapi internals this depends on -- ``PlexServer._uriRoot``,
    ``PlexServer.query``'s ``method`` override and ``server._session.post`` --
    are pinned in ``tests/test_plexapi_collection_contract.py``.

    Returns the created collection, re-read through plexapi: the POST answers
    with the collection's XML but not through a route plexapi will build an
    object from, and every step after this one is an ordinary plexapi edit.
    """
    server = section._server
    args = {
        "type": 1 if libtype == "movie" else 2,
        "title": title,
        "smart": 1,
        "sectionId": section.key,
        "uri": smart_filter_uri(server, section.key, url),
    }
    server.query("/library/collections%s" % joinArgs(args), method=server._session.post)
    return section.collection(title)


def update_smart_collection(section, collection, url: str) -> None:
    """Kometa's ``update_smart_collection`` (modules/plex.py:1618-1620).

    ``PUT {collection}/items?uri=...`` -- the same uri value the create POST
    carries, on the collection's own items route. Replacing the stored filter is
    the only edit a smart collection has; there is no partial one.
    """
    server = section._server
    args = {"uri": smart_filter_uri(server, section.key, url)}
    server.query(
        "/library/collections/%s/items%s" % (collection.ratingKey, joinArgs(args)),
        method=server._session.put,
    )


def smart_definition_hash(url: str, summary: str | None, settings=None) -> str:
    """The desired state, hashed -- what an unchanged pass short-circuits on.

    Over the BUILT URI (C10), never over ``Collection.content``. Three parts,
    and each is in it because a pass that skipped on this hash would otherwise
    silently drop an edit: the uri decides membership, the summary is written by
    this module, and ``settings`` folds in exactly the way
    ``lists._settings_parts`` folds it into the members hash -- a definition
    whose only edit was a new label or sort title would otherwise be recognised
    as already current.

    ``_settings_parts`` is imported locally for the reason
    ``reconcile.definition_hash`` imports it locally: ``lists.py`` imports from
    ``reconcile.py`` at load time, and a module-level import here would build a
    second edge into that same cycle for no gain. Settings contribute nothing at
    their defaults, so a definition that sets none hashes to the same string it
    would have without this term.
    """
    from autoposter.collections.lists import _settings_parts

    payload = "\x1f".join([url, summary or "", *_settings_parts(settings)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def reconcile_smart_collection(
    session: AsyncSession,
    section,
    library: str,
    library_type: str,
    title: str,
    url: str,
    label: str,
    summary: str | None = None,
    dry_run: bool = True,
    existing: dict | None = None,
    adopt: bool = False,
    adopt_from: list[str] | None = None,
    adopt_removes_prior_label: bool = False,
    protect_labels: list[str] | None = None,
    http: httpx.AsyncClient | None = None,
    config=None,
    settings=None,
) -> list[str]:
    """Bring one smart collection in line with ``url``.

    ``url`` is a query string from ``?`` onward -- ``build_search_url``'s
    output, unmodified. This module never builds one; taking it as a string is
    what lets the whole reconcile be tested without a builder, a registry entry
    or a params model.

    ``settings`` is the ``CollectionDefinition``, supplying the per-definition
    collection settings (labels, sort title, display mode, hub visibility) the
    shared ``apply_collection_settings`` applies. ``summary`` is the definition's
    own; there is no builder-derived one here, because the builder derives no
    membership either.

    Returns a description of every action taken -- or, under ``dry_run``, every
    action that would be taken. ``flush``, never ``commit``: the commit belongs
    to the caller, so a per-library rollback can take these rows with it.
    """
    libtype = LIBTYPES[library_type]
    listing = existing if existing is not None else {
        collection.title: collection for collection in section.collections()
    }
    collection = listing.get(title)
    actions: list[str] = []

    if collection is not None:
        # C11 first: a list collection under a smart definition must never reach
        # the ownership check, because passing it would send the pass on to an
        # update route that cannot mean anything for that collection.
        conflict = shape_conflict(collection, title, want_smart=True)
        if conflict is not None:
            logger.warning("%s: %s", library, conflict)
            return [conflict]

        ok, message = resolve_collision(
            collection, label, adopt, adopt_from or [], adopt_removes_prior_label,
            dry_run, protect_labels or [],
        )
        if message:
            actions.append(message)
        if not ok:
            return actions

    record = (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()

    wanted = smart_definition_hash(url, summary, settings)
    posters_on = posters_enabled(config, http)
    definition_current = (
        collection is not None and record is not None and record.definition_hash == wanted
    )
    # The same exception the Common Sense family makes: an unchanged definition
    # is not on its own a reason to skip, because a collection whose poster fetch
    # failed on the pass that created it still carries a NULL ``poster_sha256``
    # and would otherwise never be revisited.
    if definition_current and not (posters_on and record.poster_sha256 is None):
        return actions

    if not definition_current:
        # C8, before anything is written, on BOTH paths and under dry_run too:
        # the probe is a read, and an operator previewing a pass should learn
        # that their filter matches nothing then rather than on the first
        # applied one.
        matched = require_matches(section, url)

        if dry_run:
            actions.append(
                "%s %r from a smart filter matching %d item(s)"
                % ("would update" if collection else "would create", title, matched)
            )
        else:
            if collection is None:
                collection = create_smart_collection(section, libtype, title, url)
                collection.addLabel(label)
                actions.append(
                    "created %r as a smart collection (%d item(s) match now)"
                    % (title, matched)
                )
            else:
                update_smart_collection(section, collection, url)
                actions.append(
                    "updated the smart filter of %r (%d item(s) match now)"
                    % (title, matched)
                )

            if summary is not None:
                _edit_collection_summary(collection, summary)
            actions += apply_collection_settings(
                section, collection, settings, label, config
            )

            if record is None:
                record = ManagedCollection(
                    library=library, title=title, kind="smart",
                    plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                    definition_hash=wanted,
                )
                session.add(record)
            else:
                record.definition_hash = wanted
                record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")

    if posters_on and collection is not None and record is not None:
        # ``kind``/``key`` are None: a smart_filter definition has no builder to
        # derive default artwork from, so ``hosted_poster_url`` has nothing to
        # offer and only the operator's LOCAL override (keyed on library+title)
        # can supply one. That is the same shape a ``plex_id`` collection
        # already has -- ``BuilderResult.poster_kind`` is None there too -- and
        # it means the action string says "no poster source" on every pass until
        # a local file exists, which is the honest report.
        message = await apply_poster(
            session, http, config, collection, record, library, None, None,
            dry_run=dry_run,
        )
        if message:
            actions.append(message)

    await session.flush()
    return actions
```

- [ ] **Step 10: Run the reconciler tests — green**

```bash
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_smart.py tests/test_collection_reconcile.py tests/test_collection_lists.py; echo EXIT=$?'
```

Expected: `EXIT=0`. If `tests/test_collection_lists.py` does not exist under
that name, run `tests/ -k list_collection` instead and record what ran.

- [ ] **Step 11: Pin the two plexapi facts this module now depends on**

Append to `tests/test_plexapi_collection_contract.py`:

```python
# --- What the smart reconciler depends on (collections/smart.py) -------------


def test_collection_exposes_the_smart_flag_the_shape_check_reads():
    """``reconcile.shape_conflict`` branches on ``Collection.smart``. plexapi
    casts it from an XML attribute that DEFAULTS TO '0', which is what makes
    "absent means not smart" the correct defensive reading rather than a
    guess."""
    source = inspect.getsource(Collection._loadData)
    assert "self.smart = utils.cast(bool, data.attrib.get('smart', '0'))" in source


def test_library_section_can_re_read_a_collection_by_title():
    """The raw POST creates the collection; ``section.collection(title)`` is how
    an object comes back for every plexapi edit after it. Pinned because both
    raw-POST helpers (``create_blank_collection`` and
    ``smart.create_smart_collection``) end on this call."""
    from plexapi.library import LibrarySection

    assert callable(LibrarySection.collection)
    params = inspect.signature(LibrarySection.collection).parameters
    assert "title" in params


def test_no_reconciler_reads_a_collections_content_echo():
    """9c decision C10, asserted rather than promised.

    ``Collection.content`` is Plex's echo of a smart collection's stored uri,
    and Kometa compares against it on every pass
    (``check_url != self.library.smart_filter(self.obj)``,
    modules/builder.py:1774-1776). This service hashes its own DESIRED state
    instead, which is the Common Sense precedent and which makes the unverified
    "does Plex echo these bytes back unchanged" question moot. If a reconciler
    ever reads ``.content``, that is a decision to re-make deliberately, not a
    line to slip in.

    Scoped to the three reconcilers and matched with a word boundary on purpose:
    ``\\.content\\b`` does not match ``.content_rating`` (an underscore is a word
    character), and ``collections/posters.py`` legitimately reads
    ``response.content`` off an httpx response, which is a different ``.content``
    entirely.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "autoposter" / "collections"
    offenders = [
        name for name in ("smart.py", "lists.py", "reconcile.py")
        if re.search(r"\.content\b", (root / name).read_text(encoding="utf-8"))
    ]
    assert offenders == [], offenders
```

- [ ] **Step 12: Run the contract test**

```bash
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_plexapi_collection_contract.py; echo EXIT=$?'
```

Expected: `EXIT=0`. If `test_no_reconciler_reads_a_collections_content_echo` is
red, the offender list names the file: the fix is to remove the read (or, if it
is prose rather than code, to reword the comment so it does not spell `.content`
— `smart.py`'s own module docstring is written that way for this exact reason).
Never delete the test.

- [ ] **Step 13: Prove the two gates can fail**

```bash
cp src/autoposter/collections/smart.py /tmp/smart.bak
python - <<'PY'
from pathlib import Path
p = Path("src/autoposter/collections/smart.py")
s = p.read_text()
s = s.replace('        "smart": 1,', '        "smart": 0,')
p.write_bytes(s.encode())
PY
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_smart.py::test_the_create_post_is_byte_identical_to_the_oracles; echo EXIT=$?'
cp /tmp/smart.bak src/autoposter/collections/smart.py
cmp /tmp/smart.bak src/autoposter/collections/smart.py && echo RESTORED
python - <<'PY'
from pathlib import Path
p = Path("src/autoposter/collections/smart.py")
s = p.read_text()
s = s.replace("    if not items:\n", "    if False:\n")
p.write_bytes(s.encode())
PY
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_smart.py -k matching_nothing; echo EXIT=$?'
cp /tmp/smart.bak src/autoposter/collections/smart.py
cmp /tmp/smart.bak src/autoposter/collections/smart.py && echo RESTORED
```

Expected: both mutated runs **FAIL**, both restores print `RESTORED`. Paste all
four real outputs into the task report.

- [ ] **Step 14: Full suite, golden and ruff**

```bash
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9ct2-full test \
    sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9ct2-full
docker logs p9ct2-full | tail -40
docker compose -p p9ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9ct2 down
```

Expected: `EXIT=0` and `All checks passed!`. The golden test is inside the full
suite — quote its line. **`lists.py` changed in this task**, so any red in the
list-collection tests is this task's and must be fixed here, not deferred.

- [ ] **Step 15: Commit**

```bash
git add src/autoposter/collections/smart.py src/autoposter/collections/reconcile.py src/autoposter/collections/lists.py tests/test_collection_smart.py tests/test_collection_reconcile.py tests/test_plexapi_collection_contract.py
git commit --no-gpg-sign -m "feat(collections): reconcile one Plex-native smart collection

A third reconciler beside the Common Sense family and the list collections:
raw POST /library/collections?smart=1 and PUT {key}/items?uri=, byte-identical
to Kometa v2.4.8's own envelope, over 9b's oracle-proven query string. Refuses
a filter matching nothing at create AND at update, with no ignore_blank_results
escape hatch; refuses a smart/list shape change instead of deleting and
recreating; drift is a definition_hash over the built URI, never Plex's echo."
```

---

### Task 3: The builder, the params, and the per-builder refusal split

**Files:**

- Create: `src/autoposter/collections/builders/smart_filter.py`
- Create: `tests/test_builder_smart_filter.py`
- Modify: `src/autoposter/collections/builders/plex_search.py` (`_Resolver` → `LibraryTagResolver`, exported)
- Modify: `src/autoposter/collections/builders/base.py` (`SmartContext.run_cache`; `SmartBuilder.titles` becomes an optional extra)
- Modify: `src/autoposter/collections/builders/cs_bucket.py` (declare `refused_definition_fields`)
- Modify: `src/autoposter/collections/builders/__init__.py` (import + register)
- Modify: `src/autoposter/config/schema.py` (the per-builder refusal split; closes roadmap row 140)
- Modify: `src/autoposter/collections/engine.py` (`run_cache` into `SmartContext`; `definition_titles` fallthrough)
- Modify: `tests/test_collection_config.py:552` (the renamed validator's name in a docstring)

**Interfaces:**

- Consumes: `smart.reconcile_smart_collection` and the two refusal classes from Task 2; `search_url.build_search_url`; `filters.parse_filters` (through `PlexSearchParams`); `plex_search.PlexSearchParams`, `plex_search.LibraryTagResolver`, `plex_search.PlexSearchUnavailable`.
- Produces, for Task 4:
  - `REGISTRY["smart_filter"]` — a `SmartBuilder` with `smart = True`, `params_model = PlexSearchParams`, **no `titles` method**, and `refused_definition_fields: dict[str, str]`.
  - `SmartFilterBuilder.search_url(ctx: SmartContext) -> str`
  - `SmartContext(..., run_cache: dict[str, Any])`
  - `engine.definition_titles` falling through to `{definition.title}` for a smart builder with no `titles`.

- [ ] **Step 1: Rename the tag resolver so two builders can share one**

`smart_filter` needs exactly the tag vocabulary `plex_search` already caches —
one `listFilterChoices` per `(library, libtype-scope, field)` per pass, failures
memoised. A second copy would drift, and drift here means the same written
`Horror` resolving to different keys in two builders, which is a difference in
*membership* nothing downstream could report. So the class is renamed rather
than copied, and the rename is mechanical: four sites in
`src/autoposter/collections/builders/plex_search.py`, no test references.

In `src/autoposter/collections/builders/plex_search.py`:

1. Extend `__all__`:

```python
__all__ = [
    "LibraryTagResolver",
    "PlexSearchBuilder",
    "PlexSearchParams",
    "PlexSearchUnavailable",
]
```

2. At `:339`, change the call:

```python
            resolve_tag=_Resolver(ctx, section, libtype),
```

to:

```python
            resolve_tag=LibraryTagResolver(ctx, section, libtype),
```

3. At `:343`, in the comment, change ```` ``_Resolver._raw_choices`` ```` to
   ```` ``LibraryTagResolver._raw_choices`` ````.

4. At `:365-372`, change the sentinel comment and the class statement:

```python
# Above ``_Resolver``, its only user, rather than at the bottom of the file:
```

to:

```python
# Above ``LibraryTagResolver``, its only user, rather than at the bottom of the
# file:
```

and:

```python
class _Resolver:
    """The library's tag vocabulary, cached per pass.
```

to:

```python
class LibraryTagResolver:
    """The library's tag vocabulary, cached per pass.

    Public (and exported) since 9c, because ``smart_filter`` needs the same
    vocabulary and the same per-pass cache. Two copies of this would drift, and
    a drift here is the same written word resolving to two different Plex keys
    in two builders -- a difference in MEMBERSHIP that nothing downstream could
    report. It takes any context object carrying ``library`` and ``run_cache``,
    which is what ``SmartContext`` grew in 9c.
```

- [ ] **Step 2: Run the plex_search suite — nothing may move**

```bash
docker compose -p p9ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_plex_search.py tests/test_collection_search_url.py tests/test_collection_search_oracle.py; echo EXIT=$?'
```

Expected: `EXIT=0`. A rename that changed behaviour would show here.

- [ ] **Step 3: Write the failing builder tests**

Create `tests/test_builder_smart_filter.py`:

```python
"""``smart_filter``: the same query ``plex_search`` takes, owned by Plex.

One builder, one behaviour (9c decision C5): the params model is
``PlexSearchParams`` ITSELF, not a copy and not a subclass, so the vocabulary,
the refusals and the error messages cannot drift between the two builders. Two
deltas live inside that shared model rather than beside it -- the default sort
is ``random`` where ``plex_search``'s is the libtype's ``title.asc``, and the
definition's own ``sort`` is refused because on a smart collection the URI's
sort IS the display order.

A consequence worth knowing before reading a refusal: because the model is
literally ``plex_search``'s, its messages name ``plex_search`` ("a plex_search
has one base", "the plex_search vocabulary is ..."). That is deliberate -- the
vocabulary IS that one -- and the module docstring of
``src/autoposter/collections/builders/smart_filter.py`` says so.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import SmartContext
from autoposter.collections.builders.smart_filter import SmartFilterBuilder
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"
SECTION_KEY = "2"


class FakeChoice:
    def __init__(self, title, key):
        self.title = title
        self.key = key


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
    def __init__(self, title, labels=(), rating_key="12345", smart=True):
        self.title = title
        self.ratingKey = rating_key
        self.smart = smart
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
    def __init__(self, matches=3, existing=()):
        self.key = SECTION_KEY
        self._server = FakeServer()
        self._existing = {c.title: c for c in existing}
        self._matches = matches
        self.choice_calls = []

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        if title not in self._existing:
            self._existing[title] = FakeCollection(title, smart=True)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        return [FakeItem(str(i)) for i in range(self._matches)]

    def listFilterChoices(self, field, libtype=None):
        self.choice_calls.append((field, libtype))
        return [FakeChoice("Horror", "1138"), FakeChoice("Drama", "9")]


def _config(**overrides):
    options = {
        "adopt": False, "adopt_from": [], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "separators": False,
        "ownership_label": LABEL,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


def _ctx(session, section, definition, *, dry_run=False, library_type="Movie", config=None):
    return SmartContext(
        session=session, section=section, library="Movies",
        library_type=library_type, label=LABEL, config=config or _config(),
        http=None, dry_run=dry_run, definition=definition, run_cache={},
    )


def _definition(**overrides):
    options = {
        "title": "Recent Horror",
        "builder": "smart_filter",
        "params": {"all": {"genre": "Horror"}},
    }
    options.update(overrides)
    return CollectionDefinition(**options)
```

- [ ] **Step 4: Add the assertions to the same file**

Append to `tests/test_builder_smart_filter.py`:

```python
# --- registration and the protocol ------------------------------------------


def test_it_is_registered_as_a_smart_builder():
    builder = REGISTRY["smart_filter"]
    assert builder.smart is True
    assert builder.params_model.__name__ == "PlexSearchParams"


def test_it_declares_no_titles_method():
    """C6's degenerate case. A ``cs_bucket`` definition names a FAMILY and has
    to enumerate it; a ``smart_filter`` definition names exactly one collection,
    its own title, so it declares no ``titles`` and the engine falls through --
    which is a smaller diff than a method that restates the definition's title
    back to the engine that already has it."""
    assert not hasattr(REGISTRY["smart_filter"], "titles")


# --- the sort delta (C5) ----------------------------------------------------


def test_a_definition_with_no_sort_by_sorts_random(session):
    """Kometa's smart_filter passes ``default_sort="random"``
    (modules/builder.py:1478) where a plex_search takes the libtype's
    ``title.asc``. Pinned through the oracle as config 16."""
    url = SmartFilterBuilder().search_url(
        _ctx(session, FakeSection(), _definition())
    )
    assert url == "?type=1&sort=random&genre=1138"


def test_a_written_sort_by_still_wins(session):
    url = SmartFilterBuilder().search_url(
        _ctx(session, FakeSection(), _definition(
            params={"all": {"genre": "Horror"}, "sort_by": "year.desc"}
        ))
    )
    assert url == "?type=1&sort=year%3Adesc&genre=1138"


def test_the_tag_vocabulary_is_read_once_per_pass(session):
    """The resolver is ``plex_search``'s own, so the per-pass memo is too: two
    definitions in one pass share ``run_cache`` and cost ONE
    ``listFilterChoices``."""
    section = FakeSection()
    ctx = _ctx(session, section, _definition())
    SmartFilterBuilder().search_url(ctx)
    SmartFilterBuilder().search_url(
        SmartContext(
            session=ctx.session, section=section, library=ctx.library,
            library_type=ctx.library_type, label=ctx.label, config=ctx.config,
            http=None, dry_run=False,
            definition=_definition(title="More Horror"), run_cache=ctx.run_cache,
        )
    )
    assert section.choice_calls == [("genre", "movie")]


# --- the load-time refusals (C7, and roadmap row 140) -----------------------


def test_sort_is_refused_on_a_smart_filter_definition():
    """C7. On a smart collection the URI's sort IS the display order, so a
    definition-level ``sort`` and ``params.sort_by`` would be two knobs steering
    one behaviour. The message has to point at the one that works."""
    with pytest.raises(ValueError) as caught:
        _definition(sort="release")
    assert "sort_by" in str(caught.value)


def test_summary_is_accepted_on_a_smart_filter_definition():
    """The other half of C7: a smart_filter definition names ONE collection, and
    that collection's summary is written by the reconciler exactly as the
    Common Sense family's is. Refusing it would be refusing something that
    works."""
    assert _definition(summary="Everything that scared us lately").summary


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("limit", 10),
        ("sync_mode", "append"),
        ("item_label", ["Scary"]),
        ("tmdb_summary", 10),
        ("filters", {"year.gte": 2000}),
    ],
)
def test_the_membership_knobs_are_refused(field, value):
    with pytest.raises(ValueError) as caught:
        _definition(**{field: value})
    assert field in str(caught.value)
    assert "smart_filter" in str(caught.value)


def test_cs_bucket_now_refuses_summary_and_sort_too():
    """Roadmap row 140, closed by the per-builder split. Neither was ever read
    for a ``cs_bucket`` definition -- the reconciler applies only the
    builder-derived per-bucket summary, and a family of smart collections has no
    single membership to order -- so both silently no-opped. This is load-time
    breaking for a config that sets either today, which is what the row says."""
    with pytest.raises(ValueError):
        CollectionDefinition(title="Ages", builder="cs_bucket", summary="hi")
    with pytest.raises(ValueError):
        CollectionDefinition(title="Ages", builder="cs_bucket", sort="release")


def test_the_shipped_default_definition_still_loads():
    """The refusal above is only safe because nothing shipped sets either field:
    ``sources.default_definitions`` builds the family with title and builder and
    nothing else (``src/autoposter/collections/sources.py:87``)."""
    assert CollectionDefinition(
        title="Common Sense age ratings", builder="cs_bucket"
    ).sort == "custom"


# --- apply ------------------------------------------------------------------


async def test_apply_creates_the_collection_and_writes_a_row(session):
    section = FakeSection(matches=3)
    definition = _definition()
    actions = await SmartFilterBuilder().apply(_ctx(session, section, definition))

    assert any("created" in action for action in actions)
    key, method = section._server.queries[0]
    assert key.startswith("/library/collections?sectionId=2&smart=1")
    assert "genre%3D1138" in key
    assert method == "POST"
    row = (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none()
    kind = row.kind
    assert kind == "smart"


async def test_a_filter_matching_nothing_costs_this_definition_and_no_other(session):
    """C8's refusal reaches the operator as this definition's action string, not
    as an exception. The engine does NOT wrap a smart builder (engine.py:341-344,
    on the grounds that anything it raises is a Plex write failing), so an
    escaping refusal would reach ``reconcile_libraries``' per-library rollback
    and undo the OTHER definitions' work in the same library. A too-narrow
    filter is this definition's problem alone."""
    section = FakeSection(matches=0)
    actions = await SmartFilterBuilder().apply(_ctx(session, section, _definition()))
    assert len(actions) == 1
    assert actions[0].startswith("refused 'Recent Horror'")
    assert "widen" in actions[0].lower()
    assert section._server.queries == []


async def test_an_unknown_tag_value_costs_this_definition_and_no_other(session):
    """The same containment for the other refusal an operator can cause: a
    genre this library does not have. 9b refuses rather than building a query
    that matches nothing (roadmap row 158)."""
    section = FakeSection()
    actions = await SmartFilterBuilder().apply(
        _ctx(session, section, _definition(params={"all": {"genre": "Polka"}}))
    )
    assert len(actions) == 1
    assert actions[0].startswith("refused 'Recent Horror'")
    assert "Polka" in actions[0]


async def test_a_show_only_sort_against_a_movie_library_is_contained(session):
    actions = await SmartFilterBuilder().apply(
        _ctx(session, FakeSection(), _definition(
            params={"all": {"genre": "Horror"}, "sort_by": "episode_added.desc"}
        ))
    )
    assert len(actions) == 1
    assert actions[0].startswith("refused 'Recent Horror'")
    assert "libraries:" in actions[0]


async def test_no_refusal_string_can_carry_a_plex_token(session):
    """Every refusal this builder reports is one it constructed, or one wrapped
    class-name-only. The action strings go to the run report, the logs page and
    the notifier, so a tokenised URL reaching one of them is a leak with three
    audiences."""
    class Exploding(FakeSection):
        def fetchItems(self, path, **kw):
            raise RuntimeError("https://plex.example:32400/x?X-Plex-Token=SECRET")

    actions = await SmartFilterBuilder().apply(
        _ctx(session, Exploding(), _definition())
    )
    assert len(actions) == 1
    assert "SECRET" not in actions[0]
    assert "X-Plex-Token" not in actions[0]
    assert "RuntimeError" in actions[0]
```

- [ ] **Step 5: Run them — they must fail**

```bash
docker compose -p p9ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_builder_smart_filter.py; echo EXIT=$?'
```

Expected: FAIL at collection — `ModuleNotFoundError: No module named
'autoposter.collections.builders.smart_filter'`.

- [ ] **Step 6: Grow `SmartContext` and relax the protocol**

In `src/autoposter/collections/builders/base.py`, replace the `SmartContext`
docstring's fourth paragraph and add the field. The class becomes:

```python
@dataclass(frozen=True)
class SmartContext:
    """What a *smart* builder gets instead of ``BuilderContext``.

    The deliberate exception to "builders never touch Plex". A Plex-native smart
    collection has no membership to produce -- Plex evaluates its filter live --
    so there is no id list for the engine to resolve and apply, and the whole
    reconcile is the builder's.

    Two shapes now use it, and the difference between them is the only thing a
    reader has to hold: ``cs_bucket`` manages a FAMILY of collections whose
    titles it derives itself, and ``smart_filter`` (9c) manages exactly ONE, the
    collection its definition names. That is why ``titles`` below is an optional
    extra rather than a protocol member.

    A smart builder returns action strings from ``apply`` and ignores the
    membership knobs -- which of them are rejected on its definitions at config
    load is the builder's own ``refused_definition_fields`` table
    (``config/schema.py``), because the two shapes cannot apply the same set:
    a family has no single summary, a single collection does.

    ``definition`` is the definition itself, which a smart builder needs (and a
    list builder does not) because it applies its own collections: the
    per-definition collection settings the engine hands to
    ``reconcile_list_collection`` have to reach the smart reconciler the same
    way. None for a direct caller that has no definition.

    ``run_cache`` is the pass's scratch, the same dict ``BuilderContext`` gets
    and for the same reason: ``smart_filter`` resolves the library's tag
    vocabulary through ``plex_search.LibraryTagResolver``, which memoises one
    ``listFilterChoices`` per (library, libtype-scope, field) per pass --
    failures included. Sharing the dict with the list builders is the point: two
    definitions naming ``genre: Horror`` cost one round trip whichever builders
    they use. ``cs_bucket`` ignores it.
    """

    session: AsyncSession
    section: object
    library: str
    library_type: str
    label: str
    config: Any
    http: httpx.AsyncClient | None = None
    dry_run: bool = True
    definition: Any = None
    run_cache: dict[str, Any] = field(default_factory=dict)
```

and the protocol becomes:

```python
@runtime_checkable
class SmartBuilder(Protocol):
    """The other kind of registry entry: one that applies itself.

    Marked by ``smart = True``, which is what the engine dispatches on. It
    produces no ids -- see ``SmartContext`` for why -- so it gets the reconcile
    context instead and returns the action strings itself.

    Two optional extras, left off the protocol itself for the reason
    ``params_model`` is left off ``Builder``: not every implementation has one.

    - ``titles(library_type, config) -> set[str]`` -- for a builder that manages
      a FAMILY of collections (``cs_bucket``), so the leftovers report and the
      delete sweep can enumerate what it owns. A builder that manages exactly
      the collection its definition names declares none, and
      ``engine.definition_titles`` falls through to ``{definition.title}``.
    - ``refused_definition_fields: dict[str, str]`` -- which
      ``CollectionDefinition`` fields this builder cannot apply, mapped to the
      reason. **Required in practice**: ``config/schema.py`` refuses to validate
      a smart definition whose builder declares none, because silently accepting
      a field that never applies is the failure the table exists to prevent.
    """

    type_name: str
    smart: bool

    async def apply(self, ctx: SmartContext) -> list[str]: ...
```

- [ ] **Step 7: Write the builder**

Create `src/autoposter/collections/builders/smart_filter.py`:

```python
"""``smart_filter``: a query the operator writes once and PLEX evaluates forever.

The same search 9b's ``plex_search`` takes, pointed at a different destination.
``plex_search`` asks Plex the question on every pass and hands the answers to the
engine, which resolves them, diffs them and writes the membership. ``smart_filter``
asks the question ONCE -- to check it matches something -- and then stores it on
the server, after which Plex answers it live and this service never touches the
membership again. A new item that matches appears in the collection with no pass
in between, which is the whole point and is also the acceptance criterion the
roadmap wrote for this phase.

**One builder, one behaviour (C5).** ``params_model`` is ``PlexSearchParams``
ITSELF -- not a copy, not a subclass -- so the vocabulary, the refusals and the
messages cannot drift between the two builders. Two deltas are parameterised
inside that shared model rather than duplicated beside it:

- the default sort is ``random`` when the config names none, which is Kometa's
  for this builder and only this builder (``default_sort="random"``,
  modules/builder.py:1478), against ``plex_search``'s ``title.asc``;
- the definition's own ``sort`` is refused (C7) -- see
  ``refused_definition_fields`` below.

A consequence, stated here because an operator will meet it: the shared model's
messages name ``plex_search`` ("a plex_search has one base", "the plex_search
vocabulary is ..."). That is accurate -- the vocabulary IS that one -- and the
alternative, a second set of messages differing only in a word, is exactly the
drift the shared model exists to prevent.

**Every refusal is contained to this definition.** ``engine.py:341-344``
deliberately does not wrap a smart builder's ``apply``, on the grounds that
anything it raises is a Plex WRITE failing, which belongs to the per-library
rollback. That reasoning is still right, and it is why this module catches its
own refusals: a filter matching nothing, a genre the library does not have, a
show-only sort against a movie library and a shape conflict are the OPERATOR's
configuration meeting this library, not a write failing, and each must cost this
one definition its pass and nothing else. What is not caught -- a failing label
write, a failing summary PUT -- still reaches the rollback, unchanged.
"""
import logging

from autoposter.collections.builders.base import (
    LibraryTypeMismatch,
    SmartContext,
    require_library_type,
)
from autoposter.collections.builders.plex_search import (
    LibraryTagResolver,
    PlexSearchParams,
    PlexSearchUnavailable,
)
from autoposter.collections.search_sorts import SortNotAvailable
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

__all__ = ["SmartFilterBuilder"]

# Kometa's default for this builder and no other (modules/builder.py:1478).
# Passed as a one-element ``sort_by`` rather than through a new argument to
# ``sort_argument``: the sort tables already hold ``random`` for both libtypes,
# so the delta is a value, not a signature.
DEFAULT_SORT = "random"

# Every refusal an operator's CONFIGURATION can cause once it meets a real
# library. Held as a tuple so ``apply`` has one catch rather than six, and named
# exhaustively rather than as ``Exception`` so a genuine bug -- a TypeError in
# this module -- still reaches the engine as a failure instead of being reported
# to the operator as something about their filter.
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


class SmartFilterBuilder:
    """One Plex-native smart collection, from the operator's own query."""

    type_name = "smart_filter"
    # The engine's marker for "this one applies itself" -- see SmartContext.
    smart = True
    params_model = PlexSearchParams

    # C7, and the closing half of roadmap row 140: which definition fields this
    # builder cannot apply, and why. Read at config load
    # (``config/schema.py``), so an operator learns at the moment of the edit
    # rather than from a setting that reads as applied and never is.
    #
    # ``summary`` is deliberately ABSENT -- a smart_filter definition names one
    # collection and the reconciler writes its summary, exactly as the Common
    # Sense reconciler writes each bucket's. ``sort_title``, ``collection_mode``
    # and the ``visible_*`` flags are absent for the reason they always were:
    # they are properties of the collection OBJECT, not of its membership, and
    # the create path applies them.
    refused_definition_fields = {
        "sort": (
            "on a smart collection the search's own order IS the display order, "
            "so `sort` here and `params.sort_by` inside the search would be two "
            "knobs steering one behaviour. Write the order as `params.sort_by`"
        ),
        "limit": (
            "Plex evaluates this collection's membership live, so there is no "
            "resolved list to cap. Cap the SEARCH instead, with `params.limit` "
            "-- which, with a `params.sort_by`, is what 'the 50 highest-rated' "
            "means"
        ),
        "sync_mode": (
            "Plex owns this collection's membership; there is nothing for this "
            "service to sync or append to"
        ),
        "item_label": (
            "this service never resolves this collection's members -- Plex "
            "does -- so there is no list of items to label"
        ),
        "tmdb_summary": (
            "not supported on a smart definition. Write the summary out with "
            "`summary:`, which this builder does apply"
        ),
        "filters": (
            "a `filters:` block narrows a membership this service resolved, and "
            "this one is never resolved here. Put the narrowing INSIDE the "
            "search, where Plex will evaluate it live along with the rest"
        ),
    }

    def search_url(self, ctx: SmartContext) -> str:
        """The query string for this definition, from ``?`` onward.

        Separate from ``apply`` because it is the half that has an answer
        without a database, a collection or a write -- which is what makes the
        default sort and the tag resolution testable on their own, and what the
        oracle's ``our_query`` mirrors.
        """
        if ctx.definition is None:
            raise ValueError(
                "the 'smart_filter' builder builds the collection its definition "
                "names, so it cannot run without one"
            )
        params = PlexSearchParams.model_validate(ctx.definition.params)
        require_library_type(
            "the 'smart_filter' builder", ctx.library_type, ("Movie", "Show")
        )
        libtype = ctx.library_type.lower()
        return build_search_url(
            params.group,
            libtype=libtype,
            sort_by=params.sort_by or (DEFAULT_SORT,),
            limit=params.limit,
            resolve_tag=LibraryTagResolver(ctx, ctx.section, libtype),
        )

    async def apply(self, ctx: SmartContext) -> list[str]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'smart_filter' builder builds the collection its definition "
                "names, so it cannot run without one"
            )
        collections = ctx.config.collections
        try:
            url = self.search_url(ctx)
            logger.debug("smart_filter: %s -> %s", definition.title, url)
            return await reconcile_smart_collection(
                ctx.session,
                ctx.section,
                ctx.library,
                ctx.library_type,
                definition.title,
                url,
                ctx.label,
                summary=definition.summary,
                dry_run=ctx.dry_run,
                adopt=collections.adopt,
                adopt_from=collections.adopt_from,
                adopt_removes_prior_label=collections.adopt_removes_prior_label,
                protect_labels=collections.protect_labels,
                http=ctx.http,
                config=ctx.config,
                settings=definition,
            )
        except REFUSALS as refusal:
            # Contained deliberately -- see the module docstring. Logged as well
            # as reported, because the action string reaches a run report an
            # operator may not read and the logs page is where they look when a
            # collection stops updating.
            logger.warning(
                "%s: %r was not built: %s", ctx.library, definition.title, refusal
            )
            return ["refused %r: %s" % (definition.title, refusal)]
```

- [ ] **Step 8: Register it**

In `src/autoposter/collections/builders/__init__.py`, add the import (keeping
the alphabetical block's order — after `simple_ids`, before `text_file`):

```python
from autoposter.collections.builders.smart_filter import SmartFilterBuilder
```

and add the registration immediately after `register(PlexSearchBuilder())`:

```python
# The same query, with Plex owning the answer instead of this service: the
# SmartBuilder half of the pair, and the second implementation of that escape
# hatch (``cs_bucket`` is the first). See ``builders/smart_filter.py``.
register(SmartFilterBuilder())
```

- [ ] **Step 9: Declare `cs_bucket`'s refusals**

In `src/autoposter/collections/builders/cs_bucket.py`, add inside the class,
immediately after the `smart = True` line and its comment:

```python
    # Which ``CollectionDefinition`` fields this builder cannot apply, and why
    # (read at config load by ``config/schema.py``). ``summary`` and ``sort``
    # close roadmap row 140: neither was ever read for this family -- the
    # reconciler applies each bucket's OWN derived summary, and a family of
    # smart collections has no single membership to order -- so both silently
    # no-opped. This is load-time breaking for a config that sets either today,
    # which is what row 140 says it is.
    refused_definition_fields = {
        "summary": (
            "this definition names a FAMILY of collections and each one derives "
            "its own summary from the ratings it covers, so a single summary "
            "could not be the summary of any particular one of them"
        ),
        "sort": (
            "Plex evaluates each bucket's membership live, so there is no "
            "resolved order to set. The family's own ordering is what "
            "`sort_title` is for"
        ),
        "limit": (
            "Plex evaluates each bucket's membership from a filter, so there is "
            "no resolved list to cap"
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
            "this definition names a family of collections whose summaries the "
            "builder derives per collection, so one borrowed overview could not "
            "be the summary of any particular one of them"
        ),
        "filters": (
            "a `filters:` block narrows a membership this service resolved, and "
            "these are never resolved here -- the items are chosen inside Plex, "
            "by the bucket's own filter"
        ),
    }
```

- [ ] **Step 10: Make the schema validator per-builder**

In `src/autoposter/config/schema.py`, add above `class CollectionDefinition`
(next to the other module-level constants):

```python
# What each refusable field looks like when the operator did NOT write it. One
# table rather than one per builder, because the check is "did they write it",
# which needs the default -- and a per-builder list carrying its own defaults
# would be seven chances for two builders to disagree about what `sync` means.
_SMART_REFUSABLE_DEFAULTS: dict[str, object] = {
    "summary": None,
    "sort": "custom",
    "limit": None,
    "sync_mode": "sync",
    "item_label": [],
    "tmdb_summary": None,
    "filters": None,
}
```

and replace the whole `_membership_knobs_need_a_membership` validator
(`:420-463`) with:

```python
    @model_validator(mode="after")
    def _smart_definitions_refuse_what_they_cannot_apply(self) -> "CollectionDefinition":
        """A smart builder is held to its OWN table of inapplicable fields.

        Until 9c this was one hard-coded list, which was right while
        ``cs_bucket`` was the only smart builder and wrong the moment a second
        one arrived with a different shape: ``cs_bucket`` names a FAMILY of
        collections, so it can apply no single ``summary``; ``smart_filter``
        names exactly one, so it can. A shared list would have to refuse the
        union (a setting that works, refused) or accept the intersection (a
        setting that reads as applied and is not) -- and the second is the
        failure roadmap row 140 filed.

        So the table moves onto the builder, as ``refused_definition_fields``,
        and this validator is the mechanism. A smart builder that declares none
        is refused outright rather than defaulted to empty: the default that
        matters here is "refuse nothing", and reaching it by forgetting a class
        attribute is exactly how a silently-ignored setting ships.

        ``sort_title``, ``collection_mode`` and the ``visible_*`` flags are in
        no builder's table, deliberately. They are properties of the collection
        OBJECT rather than of its membership, and every smart create path
        applies them (roadmap row 104).
        """
        from autoposter.collections.builders import REGISTRY

        builder = REGISTRY.get(self.builder)
        if not getattr(builder, "smart", False):
            return self
        refused = getattr(builder, "refused_definition_fields", None)
        if refused is None:
            raise ValueError(
                f"{self.builder!r} is registered as a smart builder but declares "
                "no 'refused_definition_fields'. Every smart builder has to say "
                "which definition fields it cannot apply, because the failure "
                "mode of not saying is a setting that reads as applied and "
                "never is"
            )
        for field_name, why in refused.items():
            if getattr(self, field_name) != _SMART_REFUSABLE_DEFAULTS[field_name]:
                raise ValueError(
                    f"{field_name!r} does not apply to {self.builder!r}: {why}"
                )
        return self
```

Then update the stale name in `tests/test_collection_config.py:552`, changing:

```python
    """The `_membership_knobs_need_a_membership` class. Plex evaluates a smart
```

to:

```python
    """The `_smart_definitions_refuse_what_they_cannot_apply` class. Plex
    evaluates a smart
```

(keep the rest of that docstring exactly as it is; re-wrap only if ruff's line
length complains, which at `E501` off it will not).

- [ ] **Step 11: Thread `run_cache` and the titles fallthrough through the engine**

In `src/autoposter/collections/engine.py`, in the smart dispatch (`:345-351`),
add the field to the constructed context:

```python
            smart_actions = await builder.apply(
                SmartContext(
                    session=session, section=section, library=library,
                    library_type=library_type, label=label, config=config,
                    http=http, dry_run=dry_run, definition=definition,
                    run_cache=run_cache,
                )
            )
```

and in `definition_titles` (`:852-856`), replace:

```python
        builder = REGISTRY[definition.builder]
        if getattr(builder, "smart", False):
            titles |= builder.titles(library_type, config)
            continue
```

with:

```python
        builder = REGISTRY[definition.builder]
        if getattr(builder, "smart", False):
            # Two shapes of smart builder, and the difference is exactly this.
            # ``cs_bucket`` manages a FAMILY whose titles it derives itself, so
            # it lists them. ``smart_filter`` manages the one collection its
            # definition names, so there is nothing to derive -- and a
            # ``titles`` method that handed the definition's own title back to
            # the caller that already has it would be ceremony, not
            # information. Falling through is the smaller diff and keeps the
            # engine's smart dispatch the single seam (9c decision C6).
            lister = getattr(builder, "titles", None)
            titles |= lister(library_type, config) if lister else {definition.title}
            continue
```

Also update `definition_titles`' docstring, changing:

```python
    Three cases, because three kinds of definition name their collections
    differently: a smart builder owns a family of titles and lists them itself;
```

to:

```python
    Four cases, because four kinds of definition name their collections
    differently: a smart builder that owns a family of titles lists them itself;
    a smart builder that owns exactly the collection its definition names lists
    nothing and is recognised by its own title;
```

- [ ] **Step 12: Run the builder tests — green**

```bash
docker compose -p p9ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_smart_filter.py tests/test_collection_config.py tests/test_builder_settings.py; echo EXIT=$?'
```

Expected: `EXIT=0`. `tests/test_builder_settings.py` holds the existing
`cs_bucket` refusal tests (`:688-698`, `:798-805`) — they must still pass,
because the split preserved every refusal that was there and added two.

- [ ] **Step 13: Prove the two gates can fail**

```bash
cp src/autoposter/collections/builders/smart_filter.py /tmp/sf.bak
python - <<'PY'
from pathlib import Path
p = Path("src/autoposter/collections/builders/smart_filter.py")
s = p.read_text()
s = s.replace("sort_by=params.sort_by or (DEFAULT_SORT,),", "sort_by=params.sort_by or (),")
p.write_bytes(s.encode())
PY
docker compose -p p9ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_builder_smart_filter.py::test_a_definition_with_no_sort_by_sorts_random; echo EXIT=$?'
cp /tmp/sf.bak src/autoposter/collections/builders/smart_filter.py
cmp /tmp/sf.bak src/autoposter/collections/builders/smart_filter.py && echo RESTORED
python - <<'PY'
from pathlib import Path
p = Path("src/autoposter/collections/builders/smart_filter.py")
s = p.read_text()
start = s.index('        "sort": (\n            "on a smart collection')
end = s.index('        "limit": (')
p.write_bytes((s[:start] + s[end:]).encode())
PY
docker compose -p p9ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_builder_smart_filter.py::test_sort_is_refused_on_a_smart_filter_definition; echo EXIT=$?'
cp /tmp/sf.bak src/autoposter/collections/builders/smart_filter.py
cmp /tmp/sf.bak src/autoposter/collections/builders/smart_filter.py && echo RESTORED
```

Expected: both mutated runs **FAIL** (the first with `sort=titleSort`, the
second with "DID NOT RAISE"), both restores print `RESTORED`. Paste all four
real outputs.

- [ ] **Step 14: Full suite, golden and ruff**

```bash
docker compose -p p9ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9ct3-full test \
    sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9ct3-full
docker logs p9ct3-full | tail -40
docker compose -p p9ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9ct3 down
```

Expected: `EXIT=0` and `All checks passed!`. **The golden matters most in this
task**: a new registry entry is the one change that could alter what
`sources.default_definitions` or the catalog expands to. It must not — nothing
selects `smart_filter` (C9). If `tests/test_builder_port_golden.py` moves by one
byte, STOP and report.

- [ ] **Step 15: Commit**

```bash
git add src/autoposter/collections/builders/smart_filter.py src/autoposter/collections/builders/__init__.py src/autoposter/collections/builders/base.py src/autoposter/collections/builders/cs_bucket.py src/autoposter/collections/builders/plex_search.py src/autoposter/config/schema.py src/autoposter/collections/engine.py tests/test_builder_smart_filter.py tests/test_collection_config.py
git commit --no-gpg-sign -m "feat(builders): smart_filter

The plex_search query, with Plex owning the answer. One builder, one behaviour:
the params model IS PlexSearchParams, with Kometa's default_sort=random for
this builder (modules/builder.py:1478) and the definition-level sort refused,
because on a smart collection the search's order is the display order.

Smart definitions' refusals move onto the builder as
refused_definition_fields, which closes roadmap row 140: cs_bucket now refuses
summary and sort, which it never applied, while smart_filter accepts the
summary it does apply. SmartContext grows the pass's run_cache so the tag
vocabulary is read once per (library, field); definition_titles falls through
to the definition's own title for a smart builder that manages one collection."
```

---

### Task 4: The definition end to end — engine, report, sweep, row

**Files:**

- Create: `tests/test_smart_filter_engine.py`
- Modify: `src/autoposter/db/models.py:289-295` (what `kind="smart"` now covers)

**Interfaces:**

- Consumes: everything Tasks 2 and 3 produced, plus `engine.run_library`, `engine.definition_titles`, `engine.definition_titles_for`.
- Produces: nothing new. This task adds no `src` behaviour beyond one comment — it proves that the three seams Task 3 touched (dispatch, enumeration, sweep) behave, and that a `smart_filter` collection is deletable exactly like any other when its definition goes away.

**Why this is its own task.** Task 3 could be green with a definition that the
leftovers report over-claims and the sweep can never delete, or one the sweep
deletes while its definition still exists — neither shows up in a builder unit
test, and both are the kind of defect that is only visible once. A reviewer can
meaningfully reject this while approving Task 3.

**No migration.** `ManagedCollection.kind` is already `String(16)` and `"smart"`
is already a value it carries (the Common Sense buckets write it). Nothing in
this task touches the schema — if an implementer finds themselves reaching for
Alembic, the design drifted; stop and ask.

- [ ] **Step 1: Write the failing end-to-end tests**

Create `tests/test_smart_filter_engine.py`:

```python
"""A ``smart_filter`` definition, through the engine and the delete sweep.

Three seams, each of which a builder unit test cannot see:

1. the engine's smart dispatch reaches this builder with the pass's
   ``run_cache`` and reports one ``DefinitionResult`` for it;
2. ``definition_titles`` enumerates the definition's own title, so the
   leftovers report never invites an operator to delete a collection this
   service is actively maintaining;
3. the delete sweep treats the collection as an ordinary managed collection --
   never a candidate while a definition builds it, and deletable under
   ``delete_unconfigured`` once none does.

Seam 3 is the one worth stating out loud, because a smart collection LOOKS
fire-and-forget: Plex owns its membership and no pass touches it. It is still a
collection this service created, labelled and holds a row for, so when its
definition is deleted from the config it becomes an orphan exactly like a
retired chart, and the same four guards decide its fate -- ownership label,
managed row, ``delete_unconfigured``, ``max_deletes``. A ``cs_bucket``
definition's collections behave differently only because that builder ENUMERATES
its family, so the sweep never sees them while the definition exists.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from autoposter.collections.engine import (
    definition_titles,
    definition_titles_for,
    run_library,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"
SECTION_KEY = "2"


class FakeChoice:
    def __init__(self, title, key):
        self.title = title
        self.key = key


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
    def __init__(self, title, labels=(), rating_key="12345", smart=True):
        self.title = title
        self.ratingKey = rating_key
        self.smart = smart
        self.summary = None
        self.titleSort = None
        self.collectionMode = None
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": False})()]
        self._fields = []
        self.deleted = False
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
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def editSortTitle(self, value, locked=True):
        self.titleSort = value

    def delete(self):
        self.deleted = True

    def query(self, key, method=None, **kwargs):
        self._server.query(key, method)
        self._real_fields[0].locked = True


class FakeSection:
    def __init__(self, matches=3, existing=()):
        self.key = SECTION_KEY
        self._server = FakeServer()
        self._existing = {c.title: c for c in existing}
        self._matches = matches

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        if title not in self._existing:
            self._existing[title] = FakeCollection(title, smart=True)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        return [FakeItem(str(i)) for i in range(self._matches)]

    def listFilterChoices(self, field, libtype=None):
        return [FakeChoice("Horror", "1138"), FakeChoice("Drama", "9")]


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": False, "awards": False,
        "separators": False, "definitions": [], "presets": [], "libraries": ["Movies"],
        "delete_unconfigured": False, "max_deletes": 5, "enabled": True,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


def _definition(**overrides):
    options = {
        "title": "Recent Horror",
        "builder": "smart_filter",
        "params": {"all": {"genre": "Horror"}},
    }
    options.update(overrides)
    return CollectionDefinition(**options)


async def _managed_row(session, library, title, kind="smart"):
    session.add(ManagedCollection(
        library=library, title=title, kind=kind, plex_rating_key="12345",
        definition_hash="stale",
    ))
    await session.flush()
```

- [ ] **Step 2: Add the assertions to the same file**

Append to `tests/test_smart_filter_engine.py`:

```python
# --- seam 1: the engine's smart dispatch ------------------------------------


async def test_a_smart_filter_definition_runs_through_the_engine(session):
    section = FakeSection(matches=3)
    run = await run_library(
        session, section, "Movies", "Movie", [_definition()], _config(),
    )
    assert any("created" in action for action in run.actions)
    assert [result.title for result in run.definitions] == ["Recent Horror"]
    key, method = section._server.queries[0]
    assert key.startswith("/library/collections?sectionId=2&smart=1")
    assert method == "POST"


async def test_the_row_it_writes_is_a_smart_row(session):
    section = FakeSection(matches=3)
    await run_library(
        session, section, "Movies", "Movie", [_definition()], _config(),
    )
    row = (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none()
    # Captured into locals before anything can expire them.
    kind, member_count, last_reconciled = row.kind, row.member_count, row.last_reconciled_at
    assert kind == "smart"
    # Permanently NULL for a smart row, which is what ``ManagedCollection``'s
    # own comment says: Plex evaluates the filter live, so there is no
    # membership this service could count or stamp a time against.
    assert member_count is None
    assert last_reconciled is None


async def test_a_second_pass_writes_nothing(session):
    """The hash short-circuit, end to end. A smart definition that costs a Plex
    write on every pass would be the 'fire-and-forget' promise broken in the
    most expensive direction."""
    section = FakeSection(matches=3)
    config = _config()
    await run_library(session, section, "Movies", "Movie", [_definition()], config)
    before = len(section._server.queries)
    run = await run_library(session, section, "Movies", "Movie", [_definition()], config)
    assert len(section._server.queries) == before
    assert run.actions == []


async def test_the_pass_run_cache_reaches_the_builder(session):
    """C6's plumbing, observed rather than asserted on the dataclass: two
    smart_filter definitions naming the same genre in one pass cost ONE
    ``listFilterChoices``."""
    calls = []

    class Counting(FakeSection):
        def listFilterChoices(self, field, libtype=None):
            calls.append((field, libtype))
            return super().listFilterChoices(field, libtype)

    await run_library(
        session, Counting(matches=3), "Movies", "Movie",
        [_definition(), _definition(title="More Horror")], _config(),
    )
    assert calls == [("genre", "movie")]


# --- seam 2: what the definition is understood to manage --------------------


def test_a_smart_filter_definition_enumerates_its_own_title():
    """C6's degenerate case. The builder declares no ``titles``, so the engine
    falls through to the definition's title -- and this is the assertion that
    goes red if that fallthrough is ever reverted to ``builder.titles(...)``."""
    assert definition_titles(
        [_definition()], [], "Movie", _config()
    ) == {"Recent Horror"}


def test_a_definition_aimed_elsewhere_does_not_claim_this_librarys_titles():
    """``definition_titles_for`` is the sweep's narrower enumeration: a
    definition targeting another library must not make this library's collection
    of the same name look managed here."""
    elsewhere = _definition(libraries=["Kids Movies"])
    assert definition_titles_for([elsewhere], [], "Movies", "Movie", _config()) == set()
    assert definition_titles([elsewhere], [], "Movie", _config()) == {"Recent Horror"}


def test_a_gated_off_definition_still_counts_as_managing_its_title():
    """Deliberate, and inherited: a collection skipped this pass is still
    managed, and reporting it as a leftover would invite an operator to delete
    it."""
    gated = _definition(schedule={"every_n_runs": 12})
    assert definition_titles([gated], [], "Movie", _config()) == {"Recent Horror"}


# --- seam 3: the delete sweep -----------------------------------------------


async def test_the_sweep_never_touches_a_collection_its_definition_builds(session):
    existing = FakeCollection("Recent Horror", labels=[LABEL], smart=True)
    section = FakeSection(matches=3, existing=[existing])
    await _managed_row(session, "Movies", "Recent Horror")

    run = await run_library(
        session, section, "Movies", "Movie", [_definition()], _config(),
        sweep=True,
    )
    assert existing.deleted is False
    assert not any("no definition builds it" in action for action in run.actions)


async def test_an_orphaned_smart_filter_collection_is_only_reported_by_default(session):
    """The definition was deleted from the config. The collection is ours -- our
    label, our row -- so it is a sweep candidate like any other, and
    ``delete_unconfigured`` off means REPORTED."""
    orphan = FakeCollection("Recent Horror", labels=[LABEL], smart=True)
    section = FakeSection(matches=3, existing=[orphan])
    await _managed_row(session, "Movies", "Recent Horror")

    run = await run_library(
        session, section, "Movies", "Movie", [], _config(), sweep=True,
    )
    assert orphan.deleted is False
    assert any("delete_unconfigured" in action for action in run.actions)
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none() is not None


async def test_an_orphaned_smart_filter_collection_is_deleted_when_opted_in(session):
    orphan = FakeCollection("Recent Horror", labels=[LABEL], smart=True)
    section = FakeSection(matches=3, existing=[orphan])
    await _managed_row(session, "Movies", "Recent Horror")

    run = await run_library(
        session, section, "Movies", "Movie", [], _config(delete_unconfigured=True),
        sweep=True,
    )
    assert orphan.deleted is True
    assert any("deleted 'Recent Horror'" in action for action in run.actions)
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none() is None


async def test_a_protected_label_still_wins_over_the_sweep(session):
    """Checked before ownership everywhere else, and here too. A smart
    collection carrying a protected label is reported and left alone even though
    it also carries ours."""
    orphan = FakeCollection("Recent Horror", labels=[LABEL, "Maintainerr"], smart=True)
    section = FakeSection(matches=3, existing=[orphan])
    await _managed_row(session, "Movies", "Recent Horror")

    run = await run_library(
        session, section, "Movies", "Movie", [],
        _config(delete_unconfigured=True, protect_labels=["Maintainerr"]),
        sweep=True,
    )
    assert orphan.deleted is False
    assert any("protected" in action for action in run.actions)


# --- the shape refusal, end to end ------------------------------------------


async def test_a_list_collection_under_a_smart_definition_costs_one_definition(session):
    """C11 through the engine: the refusal is one definition's action string,
    and the pass carries on. An exception here would reach
    ``reconcile_libraries``' per-library rollback and undo the rest of the
    library's work over one config edit."""
    dumb = FakeCollection("Recent Horror", labels=[LABEL], smart=False)
    section = FakeSection(matches=3, existing=[dumb])

    run = await run_library(
        session, section, "Movies", "Movie", [_definition()], _config(),
    )
    assert any("shape conflict" in action for action in run.actions)
    assert section._server.queries == []
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none() is None
```

- [ ] **Step 3: Run them**

```bash
docker compose -p p9ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_smart_filter_engine.py; echo EXIT=$?'
```

Expected: `EXIT=0` **if Task 3 wired all three seams**. This task's tests are
written to pass against Task 3's code — that is deliberate and is what makes
them a *gate on Task 3* rather than a driver for new behaviour. Any red here is
a Task 3 defect surfacing, and the fix belongs in the file it belongs in, synced
back into this plan.

Two failures to expect and how to read them:

- `AttributeError: 'SmartFilterBuilder' object has no attribute 'titles'` in
  `test_a_smart_filter_definition_enumerates_its_own_title` — the
  `definition_titles` fallthrough (Task 3 Step 11) was not applied.
- `TypeError: SmartContext.__init__() got an unexpected keyword argument
  'run_cache'` in `test_the_pass_run_cache_reaches_the_builder` — the dataclass
  field (Task 3 Step 6) or the dispatch (Step 11) was not applied.

- [ ] **Step 4: Say what `kind="smart"` covers now**

In `src/autoposter/db/models.py`, replace the `kind` comment (`:289-294`):

```python
    # smart | manual | separator | operator
    # "operator" is a row an operator created directly through a lifecycle
    # endpoint (``api/collections_builders.py::blank_collection``) rather than
    # a definition -- no definition enumerates its title, so it needs its own
    # durable marker to stay out of ``engine._sweep``'s "no definition builds
    # this any more" candidates. Never written except by that endpoint.
```

with:

```python
    # smart | manual | separator | operator
    # "smart" is a Plex-native smart collection -- Plex evaluates a stored
    # filter and owns the membership. Two builders write it and the row does
    # not distinguish them, deliberately: a Common Sense age bucket
    # (``collections/reconcile.py``) and a ``smart_filter`` definition's
    # collection (``collections/smart.py``) differ in who derived the filter,
    # not in what the row has to remember about the result. Both leave
    # ``member_count`` and the reconcile stamps NULL for the same reason.
    # "operator" is a row an operator created directly through a lifecycle
    # endpoint (``api/collections_builders.py::blank_collection``) rather than
    # a definition -- no definition enumerates its title, so it needs its own
    # durable marker to stay out of ``engine._sweep``'s "no definition builds
    # this any more" candidates. Never written except by that endpoint.
```

- [ ] **Step 5: Prove the sweep gate can fail**

```bash
cp src/autoposter/collections/engine.py /tmp/engine.bak
python - <<'PY'
from pathlib import Path
p = Path("src/autoposter/collections/engine.py")
s = p.read_text()
s = s.replace(
    '        if rows[title].kind == "operator":',
    '        if rows[title].kind in ("operator", "smart"):',
)
p.write_bytes(s.encode())
PY
docker compose -p p9ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_smart_filter_engine.py::test_an_orphaned_smart_filter_collection_is_deleted_when_opted_in; echo EXIT=$?'
cp /tmp/engine.bak src/autoposter/collections/engine.py
cmp /tmp/engine.bak src/autoposter/collections/engine.py && echo RESTORED
```

Expected: **FAIL** (`assert orphan.deleted is True`), then `RESTORED`. Paste both
real outputs.

- [ ] **Step 6: Prove the enumeration gate can fail**

```bash
cp src/autoposter/collections/engine.py /tmp/engine.bak
python - <<'PY'
from pathlib import Path
p = Path("src/autoposter/collections/engine.py")
s = p.read_text()
s = s.replace(
    "            titles |= lister(library_type, config) if lister else {definition.title}",
    "            titles |= builder.titles(library_type, config)",
)
p.write_bytes(s.encode())
PY
docker compose -p p9ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_smart_filter_engine.py::test_a_smart_filter_definition_enumerates_its_own_title; echo EXIT=$?'
cp /tmp/engine.bak src/autoposter/collections/engine.py
cmp /tmp/engine.bak src/autoposter/collections/engine.py && echo RESTORED
```

Expected: **FAIL** with `AttributeError: ... has no attribute 'titles'`, then
`RESTORED`.

- [ ] **Step 7: Full suite, golden and ruff**

```bash
docker compose -p p9ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9ct4-full test \
    sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9ct4-full
docker logs p9ct4-full | tail -40
docker compose -p p9ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9ct4 down
```

Expected: `EXIT=0` and `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
git add tests/test_smart_filter_engine.py src/autoposter/db/models.py
git commit --no-gpg-sign -m "test(smart): a smart_filter definition, end to end

The three seams a builder unit test cannot see: the engine's smart dispatch
reaching it with the pass's run_cache, definition_titles enumerating exactly
the definition's own title, and the delete sweep treating the collection as an
ordinary managed one -- never a candidate while a definition builds it,
deletable under delete_unconfigured once none does, and still losing to a
protected label."
```

---

### Task 5: The wrap — roadmap, acceptance procedure, branch prep

**Files:**

- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` — rows 90, 91, 140; the 9c section; three new rows appended to the gap table.

**Interfaces:** none. This task writes prose and opens a PR.

**Gate:** this task's diff touches **zero `src/` and zero `tests/` files**, so it
does NOT run the full suite (the 9b T5 lesson). It gates on `ruff check src tests`
plus its own evidence checks: the row-number lookups, the plan-vs-shipped sync
check, and the row-119 tally. **If this task ends up touching one shipped file,
the full suite is back** — say so in the report and run it.

**Row numbers are looked up, never guessed.** Before writing a new row, read the
table and take the next free number:

```bash
grep -oE "^\| [0-9]+ \|" docs/superpowers/specs/2026-08-22-full-parity-roadmap.md \
  | grep -oE "[0-9]+" | sort -n | tail -3
```

At the time this plan was written that printed `181 182 183`, so the new rows
below are numbered **184, 185, 186**. If it prints something else, renumber them
in order and say so in the report — and update every cross-reference written in
this task to match.

**Do not touch the Completeness pass footer.** Its counts are of *inventory
rows*, and every follow-up row filed since (139–183) left them alone. Adding
three more follow-ups does not change how many inventory rows were placed.

- [ ] **Step 1: Rewrite row 90**

Find the line beginning `| 90 | \`smart_filter\` / \`smart_label\` |` and replace
the whole row with:

```markdown
| 90 | `smart_filter` | Plex-native smart collections from a translated filter. **SHIPPED 9c.** `builder: smart_filter` takes the same params as `plex_search` (`all:`/`any:`, `sort_by`, `limit`) and, instead of resolving a membership, writes the query to Plex as a stored filter Plex evaluates live — a raw `POST /library/collections?smart=1&…&uri=` whose envelope is byte-identical to Kometa v2.4.8's `create_smart_collection`, over the query string 9b's oracle already pinned (`tests/test_smart_collection_oracle.py`, sixteen configs). Kometa's `default_sort="random"` for this builder (`modules/builder.py:1478`) is reproduced. Drift is a `definition_hash` over the BUILT URI, stored in `managed_collections` exactly as the Common Sense family's is — **never** a comparison against `Collection.content`, Plex's echo, which Kometa does compare (`modules/builder.py:1774-1776`); the consequence, documented rather than fixed, is that a Plex-side manual edit to a smart filter goes undetected, as it already does for that family. Refuses a filter matching nothing at create AND at update, with no `ignore_blank_results` escape hatch (Kometa's switch is an error-downgrade of the `validate:` class 9b refused). Refuses a smart↔list shape change on an existing collection instead of Kometa's silent delete-and-recreate. `smart_label` and `smart_url` are **not** here — see row 184 | M | parity-only | 9b, 1 |
```

- [ ] **Step 2: File row 184 — what row 90 no longer claims**

Append after row 183 in the gap table:

```markdown
| 184 | `smart_label` / `smart_url` | The two Kometa builders 9c split out of row 90, each for its own reason. **`smart_label`** is a *list builder wearing a smart hat*: Kometa resolves a membership from the definition's other builders, writes a label onto every resolved ITEM, and then creates a smart collection whose filter is `label: <name>` (`modules/builder.py:1200-1221`). So membership is maintained by label WRITES, not by Plex — which contradicts the phase thesis 9c was built on, would require un-refusing `item_label` for exactly one builder, and cannot create the collection before the members are labelled (Kometa's own `smart_label_check` gates on the label existing first). **`smart_url`** is the no-DSL convenience form: paste a Plex Web URL, and Kometa extracts the query out of it (`get_smart_filter_from_uri`, `modules/plex.py:1608-1613`). Cheap to build on 9c's reconciler, and orthogonal to it — the URL is a query nobody validated, so it needs its own refusal surface. Neither has a caller today | S–M each | parity-only | 90 |
```

- [ ] **Step 3: Re-scope row 91 with the premise correction**

Find the line beginning `| 91 | \`plex_collectionless\` |` and replace the whole
row with:

```markdown
| 91 | `plex_collectionless` | Items in no other collection. **Premise corrected 9c.** This row and the 9c section both said it "needs the membership map this phase builds" — it does not, and 9c builds no such map. Kometa's `plex_collectionless` is a plain list builder: it walks the library and reads `item.collections` per item (`modules/plex.py`, the collectionless branch), which is one FORCED reload per library item per pass — the exact reload trap phase 9a was written to avoid. Nothing about a smart collection is involved. Re-scoped: the real dependency is the metadata-prefetch budget (rows 155/180), and it ships there as an ordinary `Builder` once a bulk read exists that can answer "which collections is this item in" without a reload per item | S–M once the prefetch budget exists | parity-only | 155, 180 |
```

- [ ] **Step 4: Close row 140**

Find the line beginning `| 140 | Smart-definition \`summary\`/\`sort\` accepted but ignored |`
and append to that row's description cell, immediately before the ` | S — extend one existing validator |`
that ends it:

```markdown
. **CLOSED 9c.** Not by extending the one validator, but by splitting it per builder: each smart builder now declares `refused_definition_fields` (`src/autoposter/collections/builders/cs_bucket.py`, `.../smart_filter.py`) and `config/schema.py`'s `_smart_definitions_refuse_what_they_cannot_apply` enforces the builder's own table. The split was forced by the second smart builder arriving: `cs_bucket` names a FAMILY, so it can apply no single `summary` and both fields are refused there as this row asked; `smart_filter` names exactly one collection, so its `summary` IS applied and only `sort` is refused — with a message pointing at `params.sort_by`, because on a smart collection the search's order is the display order. A shared list would have had to refuse the union (a setting that works, refused) or accept the intersection (this row's own bug, kept). Load-time breaking as predicted, and verified harmless for what ships: `sources.default_definitions` builds the family with title and builder and nothing else
```

- [ ] **Step 5: Rewrite the 9c section**

Replace the whole `#### 9c — Native smart collections` block (its Goal, Closes,
Size, Risks and Testable-when-shipped lines) with:

```markdown
#### 9c — Native smart collections — **SHIPPED**

**Goal:** `smart_filter` — Plex-maintained smart collections from a translated
filter.
**Closes:** row 90 (`smart_filter` half), row 140.
**Split out, with the premise corrections that forced it:** `smart_label` and
`smart_url` to row 184 (`smart_label` maintains membership by item-label writes,
which contradicts this phase's thesis); `plex_collectionless` to rows 155/180
(row 91's "needs the membership map this phase builds" was false — this phase
builds no membership map, and Kometa's implementation is a per-item forced
reload). CS-bucket migration NOT done and filed as optional (row 185).
**Size:** medium. Delivered as five tasks: the envelope oracle, the reconciler,
the builder, the end-to-end seams, this wrap.
**Risks, as they turned out:** the risk was never "Plex owns it after creation"
— that is the feature. It was the *envelope*: `joinArgs` percent-encodes the
whole `uri` value, so every byte the query already encoded is encoded again, and
a URL missing one level is one Plex still answers with a different set. Pinned
byte for byte against Kometa's own envelope, sixteen configs deep
(`tests/test_smart_collection_oracle.py`). The second risk was the one the
roadmap did not name: a definition switching between the smart and list shapes
over an existing collection. Kometa deletes and recreates
(`modules/builder.py:1768-1772`); this refuses, because a silent delete crosses
every guard the delete sweep is built out of.

**Acceptance — the operator procedure, post-deploy.** The roadmap's criterion
("a smart collection updates itself when a new item matches, with no autoposter
run in between") cannot be met by any test in this repository: it is a claim
about the Plex server's own behaviour after this service stops touching the
collection. It is deliberately NOT a dev-time write probe against the operator's
server. Deploys are Flux-automated, so this is a natural post-merge step:

1. Add one definition to the config and save it:

   ```yaml
   collections:
     definitions:
       - title: "Smart Filter Acceptance"
         builder: smart_filter
         libraries: ["Movies"]
         params:
           all:
             genre: Horror
             year.gte: 2020
   ```

2. Run a collections pass with `collections.apply_to_plex` **off** first. The
   report must say `would create 'Smart Filter Acceptance' from a smart filter
   matching N item(s)` with N ≥ 1. If N is 0 the definition is refused rather
   than created — widen it and repeat.
3. Turn `apply_to_plex` on and run again. The report says `created 'Smart Filter
   Acceptance' as a smart collection (N item(s) match now)`.
4. In Plex, confirm the collection exists, is marked **smart**, and holds N
   items.
5. Add or edit ONE library item so it newly matches the filter (a 2021 film's
   genre set to Horror is the cheapest edit). **Run nothing.**
6. Re-open the collection in Plex. The item is there. That is the criterion,
   met by Plex and not by this service, which is the point.
7. Run a third pass. The report is EMPTY for this definition — the URI is
   unchanged, so the hash short-circuits before any read of the filter. A pass
   that reports something here means the hash is unstable and is a defect.
8. Delete the definition from the config and run once more. The report says
   `'Smart Filter Acceptance' is no longer built by any definition; set
   collections.delete_unconfigured to delete it`. Delete the collection by hand,
   or set that flag for one pass.

Record the run of this procedure — or the fact that it has not been run yet — in
the PR description. It is the only thing in this phase no test covers.
```

- [ ] **Step 6: File rows 185 and 186**

Append after row 184:

```markdown
| 185 | Migrate the CS buckets onto `build_search_url` (optional) | 9c decision C2: the Common Sense family still creates its collections through plexapi's `section.createCollection(smart=True, filters=…)`, a SECOND query grammar beside the one 9b proved. The two differ in eight enumerated ways, and one of them changes membership: plexapi comma-joins a multi-value tag, which Plex reads as OR, where the 9b grammar emits one term per value joined by the block's conjunction — an AND under `all:`. The CS family relies on the OR reading, so a migration is not a swap: it needs the buckets rewritten onto an `any:` base. Worth doing only to retire the second grammar; the golden port test is the instrument that would make it safe, and there is zero operator-visible value today. Filed so the coexistence is a recorded decision rather than an accident | M — rewrite onto an `any:` base, gate on the golden | none (invisible) | 90 |
| 186 | `tmdb_summary` on a `smart_filter` definition | 9c refuses it, and the refusal is the conservative reading rather than an adjudicated one. The original reason — "a smart definition names a FAMILY of collections whose summaries the builder derives per collection, so one borrowed overview could not be the summary of any particular one of them" — is true of `cs_bucket` and false of `smart_filter`, which names exactly one collection whose summary the reconciler writes. Mechanically there is nothing in the way. It was refused because C7 adjudicated `sort` and `summary` and not this, and shipping an unadjudicated acceptance is the direction that cannot be taken back. Accepting it is one row deleted from `SmartFilterBuilder.refused_definition_fields` plus a summary source in `apply` | XS | parity-only | 90, 30 |
```

- [ ] **Step 7: Verify every roadmap edit landed and nothing else moved**

```bash
git diff --stat docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
grep -c "SHIPPED 9c" docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
grep -oE "^\| 18[0-9] \|" docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
grep -n "needs the membership map this phase builds" docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
```

Expected: one file changed; `SHIPPED 9c` appears at least twice (row 90 and the
9c section); the row list ends `| 186 |`; and the "needs the membership map"
grep returns **nothing** — the false premise is corrected in both places it
appeared (row 91 and the old 9c "Closes" line). If that grep still hits, the
9c section rewrite in Step 5 missed a line.

- [ ] **Step 8: The plan-versus-shipped sync check**

The plan document is live-synced through fix rounds, so at the end it must
describe what actually shipped. Walk it and confirm, quoting the real values:

```bash
grep -n "def reconcile_smart_collection" -A 20 src/autoposter/collections/smart.py
grep -n "refused_definition_fields" src/autoposter/collections/builders/*.py
grep -n "DEFAULT_SORT" src/autoposter/collections/builders/smart_filter.py
grep -n "run_cache" src/autoposter/collections/builders/base.py src/autoposter/collections/engine.py
```

For each: does the signature, the field list, the constant and the plumbing match
what Tasks 2–4 of this document say? Any mismatch is either a fix round that was
not synced (sync it now, in this task's commit, and say which task it belonged
to) or a defect. List every divergence found in the report even if the answer is
"none".

- [ ] **Step 9: The row-119 tally and the branch's own numbers**

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

Report: the commit list, the file/line totals, any pre-existing row-119
(wall-clock) instance a task's run hit, and the final full-suite pass count from
Task 4's run.

- [ ] **Step 10: Commit the wrap**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): 9c shipped -- rows 90 and 140 closed, 91 re-scoped

Row 90 keeps the smart_filter half and records what shipped, including the two
deliberate divergences from Kometa: no ignore_blank_results, and no comparison
against Collection.content. Row 91's premise is corrected -- plex_collectionless
needs a per-item reload budget, not a membership map this phase never built --
and moves to rows 155/180. Row 140 is closed by the per-builder refusal split.
New rows 184 (smart_label/smart_url), 185 (optional CS migration) and 186
(tmdb_summary on a smart_filter definition, refused conservatively)."
```

- [ ] **Step 11: Ruff, and only ruff**

```bash
docker compose -p p9ct5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9ct5 down
```

Expected: `All checks passed!`. No pytest run in this task — see the Gate note
above.

- [ ] **Step 12: Open the PR**

```bash
git push -u origin feat/smart-collections
gh pr create --base main --title "Phase 9c: native smart collections (smart_filter)" --body "$(cat <<'BODY'
`builder: smart_filter` — the same query `plex_search` takes, with Plex owning
the answer. The definition's search is written to the server once, as a stored
filter Plex evaluates live; a new item that matches appears in the collection
with no pass in between.

## What is in here

- **The envelope oracle** (`tests/test_smart_collection_oracle.py`): sixteen
  configs' `POST /library/collections?…` keys and two `PUT {key}/items?uri=`
  keys, produced by a standalone transcription of Kometa v2.4.8's
  `build_smart_filter` / `create_smart_collection` / `update_smart_collection`
  and plexapi 4.18.2's `joinArgs`, pinned as data. This is the gate: `joinArgs`
  encodes the whole `uri` value, so every byte the 9b query already encoded is
  encoded again, and a URL missing one level is one Plex still answers with a
  different set.
- **The reconciler** (`src/autoposter/collections/smart.py`): raw POST/PUT over
  9b's oracle-proven query string. Refuses a filter matching nothing at create
  AND at update, with no `ignore_blank_results` switch. Refuses a smart↔list
  shape change instead of deleting and recreating the collection the way Kometa
  does. Drift is a `definition_hash` over the built URI, never Plex's echo.
- **The builder** (`src/autoposter/collections/builders/smart_filter.py`): the
  params model IS `PlexSearchParams`, so the vocabulary cannot drift between the
  two builders. Kometa's `default_sort="random"` for this builder is reproduced.
- **The refusal split** (`src/autoposter/config/schema.py`): each smart builder
  declares which definition fields it cannot apply. Closes roadmap row 140 —
  `cs_bucket` now refuses `summary` and `sort`, which it never applied.
- **The seams** (`tests/test_smart_filter_engine.py`): engine dispatch,
  leftovers enumeration, delete sweep.

## Breaking

A config that sets `summary:` or `sort:` on a `cs_bucket` definition no longer
loads. Nothing shipped does — `sources.default_definitions` builds the family
with a title and a builder and nothing else — and roadmap row 140 filed this
break deliberately rather than hotfixing it.

## Nothing opts in

No default definition and no catalog preset gains a `smart_filter`. The golden
port fixture is byte-identical.

## Acceptance

The roadmap's criterion — a smart collection updating itself with no run in
between — is a claim about the Plex server, not about this code, and there is no
dev-time write probe here. The post-deploy operator procedure is written out in
the roadmap's 9c section.
BODY
)"
```

Do not merge. Report the PR URL.

---

## Self-review

Run against the facts file (`.superpowers/sdd/p9c-facts.md`) with fresh eyes.

**Adjudication coverage**

| Adjudication | Where it is implemented |
| --- | --- |
| C1 raw POST of the oracle-proven string | T1 (the pins), T2 Steps 9–10 (`create_smart_collection`, `update_smart_collection`) |
| C2 no CS migration | Not done, by construction; filed as row 185 in T5 Step 6 |
| C3 smart_label / smart_url out | Not built; row 90 rewritten and row 184 filed, T5 Steps 1–2 |
| C4 plex_collectionless out, premise corrected | T5 Step 3, and the "needs the membership map" grep in Step 7 |
| C5 `builder: smart_filter`, params identical, `random` default, `type:` still refused | T3 Step 7 (`params_model = PlexSearchParams`, `DEFAULT_SORT`); `type:` refusal is `_REFUSED_KEYS`', inherited unchanged |
| C6 minimal plumbing: `titles` fallthrough, `SmartContext` grows `run_cache` | T3 Steps 6 and 11; proved in T4 Steps 2 and 6 |
| C7 per-builder refusal split; `sort` refused, `summary` kept | T3 Steps 9–10; row 140 closed in T5 Step 4 |
| C8 refuse on zero at create and update, no escape hatch | T2 Step 9 (`require_matches`), tested T2 Step 7, pinned upstream T1 Step 7 |
| C9 no catalog work | The golden gate in every task's Step "full suite, golden and ruff" |
| C10 hash over the built URI, never the echo | T2 Step 9 (`smart_definition_hash`), enforced by `test_no_reconciler_reads_a_collections_content_echo` |
| C11 conversion refused, not delete-recreate | T2 Steps 1–5 (`shape_conflict`, both directions), T4 Step 2 end-to-end |
| T5 acceptance: no dev-time write probe | T5 Step 5, the operator procedure |

**Placeholder scan.** No step says "TBD", "add error handling", "similar to Task
N", or "write tests for the above". Every code step carries the code; every run
step carries the command and the expected output.

**Type and name consistency.** `reconcile_smart_collection`,
`smart_definition_hash`, `smart_filter_uri`, `create_smart_collection`,
`update_smart_collection`, `require_matches`, `SmartFilterMatchedNothing`,
`SmartCollectionUnavailable`, `shape_conflict`, `LibraryTagResolver`,
`SmartFilterBuilder.search_url`, `refused_definition_fields`,
`_SMART_REFUSABLE_DEFAULTS`, `_smart_definitions_refuse_what_they_cannot_apply`,
`DEFAULT_SORT`, `REFUSALS` — each is defined in exactly one task and spelled the
same everywhere it is used. `smart.create_smart_collection` (ours) and the
oracle driver's `create_smart_collection` share a name across two files that
never import each other, deliberately: they are the same upstream function, and
naming them differently would hide that.

**Known gaps, stated rather than hidden**

1. **The shared params model's messages name `plex_search`.** An operator who
   mis-writes a `smart_filter` block is told "a plex_search has one base". C5
   forced the shared model and the alternative is two message sets differing in
   one word. Documented in `smart_filter.py`'s module docstring; not filed as a
   row, because filing it invites the fix that reintroduces the drift.
2. **A `smart_filter` collection with no local poster reports "no poster source"
   on every pass.** Inherited, not introduced: every list builder with no
   `poster_kind` already behaves this way. Noted at the `apply_poster` call site.
3. **`tmdb_summary` is refused conservatively**, not adjudicated. Row 186.
4. **A refused definition is not marked `failed` in its `DefinitionResult`.**
   The engine's smart branch never sets that flag, and `cs_bucket` has the same
   property today. The refusal reaches the report as an action string and the
   logs as a warning. Left alone deliberately — changing it is an engine-wide
   decision about what "failed" means for a builder that applies itself.
