# Definitions Editor — row 138 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `CustomCollectionsPanel` a third verb — **Edit** — that rewrites
one stored `collections.definitions` entry over a curated, loss-free field
subset, with the file-provenance hard guard inherited *and* enforced API-side.

**Architecture:** Nothing generic is built. Row 138's literal scope is closed
on the panel that already owns the plumbing: `overrideList` /
`documentForCreate` / `documentForRemove` / `overrideOrdinal` gain one sibling,
`documentForEdit`, which splices a replacement entry at an ordinal into the
same wholesale-replaced array. The replacement entry is built as
`{...storedEntry, ...curatedEdits}` — the eleven curated fields are the only
keys the form may add, change or delete, and every other key of the stored
entry rides through untouched. That single rule is what makes the round-trip
loss-free and it is the plan's law. Server-side, one new guard in
`api/routes.py::_validated_generation` refuses the **first** stored
`collections.definitions` override while the mounted YAML still lists
definitions — the same refusal the panel's `FILE_ROWS_NOTE` makes in copy,
now enforced by the API for all three writers (PUT, preview, apply).

**Tech Stack:** Python 3.14, FastAPI, pydantic v2, pytest (compose `test`
service + `.superpowers/isolated-db.yml`); React 19 + TypeScript + vitest +
@testing-library/react (compose `web` service). **No new runtime dependency,
no Alembic migration, no new endpoint, no schema field.**

---

## Read this first — what governs this plan

1. `.superpowers/sdd/p-defedit-facts.md` **C1–C4** — the controller
   adjudications. SETTLED. This plan implements them. Where this plan decides
   something the facts did not, **ADJUDICATIONS BEYOND THE FACTS** below says
   so by name.
2. `.superpowers/sdd/p-defedit-recon.md` — the surface map. Its §5
   recommendation (option **b**, folded into the existing panel) is what C1.1
   adopted. Two of its statements are corrected below (**F1**, **F2**).
3. `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` row 138 (line
   240) — the requirement text T3's close is written against; row 222 (line
   320) and row 223 (line 321) for the pointers.
4. **The tree itself.** Every `file:line` in this plan was read from
   `origin/main` at **`ca473d8`** ("docs(roadmap): row 38's close text names
   the right call-site count") *after* `git fetch origin`. #122 and #123 are
   already IN that tip. Do not read the mechanism sites from whatever branch
   you happen to be standing on.

---

## CONTRADICTION FLAGS — read before Task 1

**F1 — the recon's "21 fields" is right, and the recon's worry about `#123`
moving `CollectionDefinition` is resolved: it did not.** The class at
`origin/main:src/autoposter/config/schema.py:829-1005` carries exactly
twenty-one fields, enumerated in **The curated subset** below. `poster_url`
(row 222) is **not** among them and is not added by this phase. `changes_webhook`
(row 19 / notif-1) **is** among them and is the last one declared. Every one
of the twenty-one carries a `Field(description=...)`, so
`config/descriptions.py::_walk` already publishes
`collections.definitions[].<name>` for all twenty-one through
`GET /api/config`'s `field_descriptions` — which row 138's own text promised
"the moment there is a row to hang it on". **T2 hangs them on.**

**F2 — the recon's option (b) con ("still blocked by the same hard guard for
file-provenance definitions") is true but not a constraint the Edit UI has to
implement.** Provenance is *uniform in value* by construction
(`api/collections_builders.py:196-224`): when the stored overrides document
carries `collections.definitions`, **every** listed row is `"override"`; when
it does not, **every** row is `"file"`. So a listing can never contain both,
and an Edit control gated on `row.provenance === "override"` — the exact
condition Remove already uses — is unreachable in the file state without a
single extra check. The guard is inherited **structurally**. T2 pins that with
a test rather than adding a second `fileRows` condition, and T1 puts the real
enforcement where a UI cannot be bypassed: the API.

---

## Branch and cut point

- Branch name: **`feat/definitions-editor`**.
- **Cut from `origin/main` after a fetch, verified with a CONTENT probe, never
  a sha probe.** #122 and #123 are merged; the probes below prove the tip
  carries what this phase builds on.

```bash
git fetch origin
git show origin/main:src/autoposter/config/schema.py | grep -q "changes_webhook: str = Field" && echo WEBHOOK-FIELD-PRESENT
git show origin/main:src/autoposter/config/descriptions.py | grep -q 'f"{path}\[\]\."' && echo LIST-DESCRIPTIONS-PRESENT
git show origin/main:frontend/src/pages/CustomCollectionsPanel.tsx | grep -q "documentForRemove" && echo PANEL-PRESENT
git show origin/main:frontend/src/api/overrides.ts | grep -q "export function withPath" && echo OVERRIDES-HELPERS-PRESENT
git show origin/main:src/autoposter/api/routes.py | grep -q "async def _validated_generation" && echo SEAM-PRESENT
git checkout -b feat/definitions-editor origin/main
git rev-parse HEAD
```

All five markers must print. If `LIST-DESCRIPTIONS-PRESENT` is missing the tip
predates row 217's descriptions walk and T2's hover text has nothing to read —
**STOP and report**; do not cut from an older ref and do not stack. At the time
of writing `origin/main` is `ca473d8` and all five pass. Record the resolved
`git rev-parse HEAD` in the Task 1 report.

---

## Global Constraints

Every task's requirements implicitly include this section.

1. **The loss-free law.** Every write this phase produces is built as
   `{...storedEntry, ...curatedEdits}` from the **stored overrides array**.
   Never from `GET /api/collections/definitions` (a seven-field projection of
   a twenty-one-field model) and never from the whole config (the freezing
   hazard `frontend/src/api/overrides.ts:13-21` opens with). The ten
   non-curated fields of an edited entry must come out of a save **byte-
   identical** to what went in.
2. **The negative assertion is law (facts C2).** Editing one definition must
   not perturb its siblings. Every task that writes an array carries a test
   asserting the *untouched* entries are byte-identical after the write — the
   assertion that must fail if the splice is ever rewritten as a rebuild.
3. **The hard guard is API-enforced, not just UI-enforced (facts C1.2, C2).**
   The refusal lives in `_validated_generation`, so PUT `/api/config/overrides`,
   POST `/api/config/preview` and POST `/api/config/apply` all carry it. The
   UI's missing control is the second line of defence, not the first.
4. **`changes_webhook` is never rendered in full (facts C1.3).** Any surface
   this phase adds that shows it shows its **host only** — the discipline
   `notifications.url` gets (`api/routes.py:1131-1153`). It is **not** an
   editable field this phase (see the curated subset's rationale), so the
   stored value round-trips untouched under constraint 1.
5. **RED before GREEN for every behavior this phase adds.** The one deliberate
   exception is T1 Steps 8–10, a characterization block pinning
   *pre-existing* wholesale-replace semantics that cannot go RED; it is
   labelled as such and its steps say plainly that a PASS on first run is the
   expected result.
6. **Suites stated-then-measured.** T1 Step 1 MEASURES the post-#122/#123
   baseline (backend pytest total, vitest total). No number in this plan is a
   fact until that step writes it down; every later task states its expected
   count before running and reconciles any difference in its report.
7. **No Settings.tsx change, of any kind (facts C1.6).** Not the `Field`
   dispatch, not a new export, not a comment. `StringListField`
   (`Settings.tsx:187-231`) is module-private and stays that way; T2 writes its
   own list widget in the editor component.
8. **No schema change.** `config/schema.py` is not touched. `poster_url` (row
   222) stays a follow-up (facts C1.5); the param-override layer files as row
   225 and is not built (facts C1.4).
9. **Field descriptions on any new key.** No new config key is added by this
   phase, so `tests/test_config_descriptions.py` is a regression gate here
   rather than a task gate — it must stay green.
10. **Container discipline.** Unique compose project per task (`pde1`, `pde2`,
    `pde3`), backend runs always with the `.superpowers/isolated-db.yml`
    overlay. Tee backend output to a path under `/app/.superpowers/` inside the
    container; redirect frontend output host-side. Never trust an exit code or
    `docker wait` alone — **read the log** (roadmap row 193). Long runs start
    detached (`run -d --name …`) and are waited on with a foreground
    `docker wait`. Teardown per task is `docker compose -p <project> down` —
    **never** `down -v`.
11. **Commits** are conventional, `--no-gpg-sign`, staged **by name** (never
    `git add -A`, `-u` or `.`), and carry **no AI attribution** of any kind —
    no `Co-Authored-By`, no "Generated with", nothing. Same for the PR body.
12. **Artifacts are `p-defedit-` prefixed** and live in `.superpowers/` /
    `.superpowers/sdd/` (gitignored). The PR body at
    `.superpowers/sdd/p-defedit-pr-body.md` is **never committed**.
13. **This plan file is committed by Task 1, Step 1.** It is an untracked
    working-tree file until then.

---

## The curated subset — what Edit reaches, and why

The rule that draws the line: **a field is editable here only if its stored
value has exactly one representation reachable from a plain widget, and
exactly one unambiguous "unset" state.** Under that rule an unset field is
written as *the key being absent* (the schema default), never as `null` and
never as an explicit copy of the default — the same sparseness the Create path
already produces (`CustomCollectionsPanel.tsx:258-268`).

`libraries` is the one field that does **not** follow the default-equality
rule, and it has its own control for a reason given in its row.

### Editable — eleven fields

| Field | Type (`schema.py`) | Widget | Unset state | Loss-free because |
|---|---|---|---|---|
| `title` | `str` (required) | text input | none — required | A string round-trips as itself. Always written. Renaming is a real consequence, not a lossy one: the collection already in Plex keeps its old title and becomes an orphan under `collections.delete_unconfigured` — disclosed in `EDIT_NOTE`, and the duplicate-title refusal (`CollectionsConfig._titles_must_not_collide`) already arrives as a rendered 422. |
| `libraries` | `list[str] \| None` | "every configured library" checkbox + one checkbox per name | key absent (`None`) | `None` ≠ an explicit list of today's library names: `None` means "every library in `collections.libraries`", which changes when a library is added. Collapsing an explicit full list to `None` would be a silent semantic change, so the every-library state is its OWN control, seeded from whether the stored entry had the key — not inferred from all-boxes-checked. The name roster is the union of `listing.libraries` and the stored entry's own names, so a library the config no longer lists survives an edit that does not touch scope. |
| `summary` | `str \| None` | text input | `""` → key absent | `None` and `""` are the same request ("no hand-written summary"); one blank state, one meaning. |
| `sort` | `str`, default `"custom"` | text input | `""` or `"custom"` → key absent | A plain string with a fixed default; an explicit `"custom"` and an absent key are the same order. |
| `sync_mode` | `Literal["sync","append"]`, default `"sync"` | select | `"sync"` → key absent | Two named values, both reachable, default identical to absent. |
| `limit` | `int \| None`, `ge=1` | number input, `min=1` | `""` → key absent | `None` is "no cap"; `ge=1` means no in-range value collides with the unset state. |
| `labels` | `list[str]`, default `[]` | add/remove string list | `[]` → key absent | `[]` and absent are the same set. Per-item inputs, not a comma-joined string — a joined field cannot represent a label containing the separator. |
| `label_sync` | `bool`, default `False` | checkbox | `false` → key absent | A two-state field whose default is one of the two states; absent ≡ `false` exactly. |
| `item_label` | `list[str]`, default `[]` | add/remove string list | `[]` → key absent | Same as `labels`. |
| `sort_title` | `str \| None` | text input | `""` → key absent | Same as `summary`: one blank state, one meaning. |
| `collection_mode` | `Literal["default","hide","hideItems","showItems"] \| None` | select with an explicit "leave Plex's setting alone" option | that option → key absent | The unset state gets its own named option, so `None` is not confused with the *value* `"default"` — two different requests, two different options. |

### Read-only in the editor — three fields, shown and preserved

| Field | Shown as | Why not editable |
|---|---|---|
| `builder` | plain text | A registry key. Free text lets an operator name a builder that does not exist, refused only at save (`CollectionDefinition._must_be_a_registered_builder`); a select would need the registry served, which is a different phase's endpoint. The Create flow resolves it from a URL, which is the honest way to change it. |
| `params` | `JSON.stringify`, monospace | Facts C1.1: exposed only if a loss-free widget exists **per builder type**. None does — each builder validates its own `params` model — so the read-only render plus the remove-and-recreate note is what stays honest. A raw JSON textarea was considered and rejected: it is loss-free only in the sense that it round-trips *text*, and it hands an operator a way to write a params dict the panel then cannot re-render meaningfully. |
| `changes_webhook` | **host only**, via `hostOnly()` | Facts C1.3/C2 and the field's own schema comment: it may embed a token in its path, so it is never rendered in full. It is not editable because there is no honest way to edit a value the page was not shown: the keep-sentinel machinery that solves exactly this for `notifications.url` (`routes.py:1166-1265`) refuses a sentinel buried inside a list *by design* (`_sentinel_occurrences` returns `replaceable=False` for any list) — so a per-entry keep marker is new machinery, not a reuse. Under the loss-free law the stored value passes through the save untouched. |

### Excluded — seven fields, with the reason each

- `schedule` (`ScheduleGate | None`) — a nested object (`{every_n_runs, months}`). Needs its own sub-form; no plain widget represents it.
- `filters` (`dict | None`) — its own mini-DSL (`attribute[.modifier]: value` plus nested `any:`/`all:`). Same reason as `params`, one level worse.
- `tmdb_summary` (`int | None`, `gt=0`) — reachable by the same widget as `limit`, excluded for economy: it is `summary`'s alternative and no row asks for it. The obvious next increment.
- `visible_library`, `visible_home`, `visible_shared` (`bool | None`) — three coupled tri-state fields. A checkbox is **lossy** here (it collapses `None`, "leave Plex's setting alone", into `false`, "unpin"), and a three-option select each would ship three controls whose real operator intent is one "pin to hubs" decision. Deferred as a unit rather than shipped as three lossy checkboxes.
- `hub_priority` (`int | None`, `ge=0`) — only meaningful once a `visible_*` flag promotes the collection, so it defers with them.

Every excluded field, and every field of a **smart** definition that its
builder refuses (`_SMART_REFUSABLE_DEFAULTS`, `schema.py:815-822`), rides
through a save untouched under the loss-free law — which is precisely what
T1 Step 8 and T2's `preserves every field the form does not reach` pin.

---

## ADJUDICATIONS BEYOND THE FACTS

Recorded so no implementer re-derives them differently, and so the controller
can overrule any of them before T1 starts.

- **A1 — the API guard's predicate is "the FIRST definitions override, while
  the file lists definitions", not "the file lists definitions".** The naive
  predicate would refuse every save after a legitimate migration, because the
  mounted YAML keeps its `definitions:` block while the stored override
  shadows it — which is exactly the state the panel is *designed* to work in
  (provenance reads `"override"` there). The guard therefore fires only on the
  transition from file-supplied to override-supplied. This mirrors the UI's
  `fileRows` condition exactly: `fileRows` is true only while no override is
  stored.
- **A2 — `title` is editable.** The facts name a floor (`sync_mode`, `labels`,
  `sort`, `libraries`) and leave the rest to the loss-free rule. Renaming is
  the single most likely edit; it round-trips losslessly; its one consequence
  (an orphaned Plex collection) is already the subject of the panel's existing
  orphan sentence, which `EDIT_NOTE` reuses verbatim rather than inventing a
  second explanation.
- **A3 — the editor renders the schema's own per-field descriptions as hover
  text**, read from `GET /api/config`'s `field_descriptions` at
  `collections.definitions[].<name>`. No new plumbing: the panel already
  fetches `/api/config`, and row 138's own text says the `[]` descriptions were
  waiting for exactly this. Degrades silently when absent (an older server, or
  the panel's own older test fixtures).
- **A4 — the Edit form has no "Check" button.** Create has one because a
  pasted URL can resolve to params the builder refuses. An edit's refusals
  arrive from Save with nothing stored, by `_validated_generation`'s
  never-half-apply ordering (`routes.py:1425-1470`) — a second button offering
  the same answer would be ceremony. Stated in the plan so its absence reads as
  a decision, not an omission.
- **A5 — `changes_webhook`'s redaction here is DISPLAY discipline, not a
  secrecy boundary, and the plan says so.** `GET /api/config` already serves
  every definition's `changes_webhook` in full (`_REDACTORS` is a dotted-path
  map and `_read_path` walks dicts only — no list index reaches inside
  `collections.definitions`), and that predates this phase. Redacting it
  server-side would need the keep-sentinel's list case, which is refused by
  design. The residual is disclosed in row 138's close rather than papered
  over, and **no new row is filed for it** — the facts authorize row 225 only.
  Flagged to the controller.
- **A6 — the editor is a new component file**, `DefinitionEditor.tsx`, not
  more inline JSX in a 574-line panel. The panel keeps the plumbing (stored
  document, ordinal, PUT, guard); the editor owns the form and the entry
  builder. Its two pure functions (`draftFrom`, `entryFromDraft`) are exported
  so the loss-free law is testable without rendering.

---

## File Structure

**Created:**

- `frontend/src/pages/DefinitionEditor.tsx` — the Edit form: the eleven
  curated controls, the three read-only rows, `draftFrom`/`entryFromDraft`
  (the loss-free law, as pure functions), `hostOnly`.
- `frontend/src/pages/DefinitionEditor.test.tsx` — its vitest suite: the
  round-trip, the preservation of unreached fields, the redaction, the
  scope control's two states.

**Modified:**

- `src/autoposter/api/routes.py` — `_definitions_guard` (new, before
  `_validated_generation` at `:1425`) and one call inside
  `_validated_generation` after the `base = …` read (`:1460`).
- `tests/test_api_config_editor.py` — appended: the guard's five cases and the
  wholesale-replace characterization block.
- `frontend/src/pages/CustomCollectionsPanel.tsx` — `documentForEdit`, the
  editing state, the per-row Edit button, `EDIT_NOTE`, `REMOVE_NOTE` corrected.
- `frontend/src/pages/CustomCollectionsPanel.test.tsx` — the Edit flow's
  panel-level tests (guard, splice, siblings untouched, 422 rendering).
- `frontend/src/pages/custom-collections.css` — the editor's classes.
- `src/autoposter/config/descriptions.py` — the module docstring's row-138
  sentence, corrected (T3).
- `deploy/README.md` — an "Editing a custom collection" subsection (T3).
- `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` — row 138 closed,
  row 225 filed (T3).

**Explicitly NOT touched:** `src/autoposter/config/schema.py`,
`src/autoposter/config/overrides.py`, `src/autoposter/api/collections_builders.py`
(the listing is unchanged — the editor reads the STORED array, never the
projection), `frontend/src/api/overrides.ts`, `frontend/src/api/types.ts`,
`frontend/src/pages/Settings.tsx`, `frontend/package.json`.

---

## Task 1: The API edit seam — the guard, and the wholesale-replace law

**Files:**
- Modify: `src/autoposter/api/routes.py:1406-1470` (new function before
  `_validated_generation`; one call inside it)
- Test: `tests/test_api_config_editor.py` (appended at the end of the file)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the 422 shape T2's UI renders —
  `{"detail": [{"path": "collections.definitions", "message": DEFINITIONS_GUARD_REFUSAL}]}`,
  where `DEFINITIONS_GUARD_REFUSAL` is the module-level string constant
  defined in Step 4. `frontend/src/api/overrides.ts::fieldErrors` maps that to
  `{"collections.definitions": "<message>"}`, which the panel already renders.

- [ ] **Step 1: Cut the branch, commit the plan, and MEASURE the baseline**

```bash
git fetch origin
git show origin/main:src/autoposter/config/schema.py | grep -q "changes_webhook: str = Field" && echo WEBHOOK-FIELD-PRESENT
git show origin/main:src/autoposter/config/descriptions.py | grep -q 'f"{path}\[\]\."' && echo LIST-DESCRIPTIONS-PRESENT
git show origin/main:frontend/src/pages/CustomCollectionsPanel.tsx | grep -q "documentForRemove" && echo PANEL-PRESENT
git show origin/main:frontend/src/api/overrides.ts | grep -q "export function withPath" && echo OVERRIDES-HELPERS-PRESENT
git show origin/main:src/autoposter/api/routes.py | grep -q "async def _validated_generation" && echo SEAM-PRESENT
git checkout -b feat/definitions-editor origin/main
git rev-parse HEAD
git add docs/superpowers/plans/2026-09-02-definitions-editor.md
git commit --no-gpg-sign -m "docs(plans): the definitions editor plan, row 138"
```

All five markers must print before the checkout. Then measure both suites —
these two numbers are the baseline every later task reconciles against:

```bash
docker compose -p pde1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pde1-base test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-p-defedit-base.log"
docker wait pde1-base
docker rm pde1-base
docker compose -p pde1 run --name pde1-base-vitest web npm test > .superpowers/run-p-defedit-base-vitest.log 2>&1
docker rm pde1-base-vitest
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-base.log` and
`D:\Sites\autoposter\.superpowers\run-p-defedit-base-vitest.log` — never trust
the wait code alone** (roadmap row 193). Record both totals ("N passed" for
pytest, "Tests  N passed" for vitest) in the Task 1 report. Both must be **0
failed** before any work starts; a red baseline is a STOP-and-report, not
something to build on. A `tests/test_worker.py` / `tests/test_pipeline.py`
one-off is rows 193/195's known flake — re-run that file alone before treating
it as real.

- [ ] **Step 2: Write the failing guard tests**

Append to the end of `tests/test_api_config_editor.py`:

```python
# --- The definitions guard (row 138) -------------------------------------
#
# The panel refuses to CREATE while any listed definition comes from the
# mounted file, because an overrides list replaces the file's WHOLESALE: the
# first stored list would silently stop every file row from being built, and
# copying the file's rows in to "preserve" them is the freezing hazard. That
# refusal was UI-only. These pin it in the API, where a UI cannot be bypassed.
#
# The predicate is the FIRST such store, not "the file lists definitions":
# once an override is stored the file's list is already shadowed, which is the
# state the panel edits in, and refusing there would brick the editor.


def _with_file_definitions(config_file: Path) -> None:
    """Give the mounted file a non-empty ``collections.definitions``."""
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    document["collections"]["definitions"] = [
        {"title": "Hand Picked", "builder": "plex_id", "params": {"ids": ["12345"]}}
    ]
    config_file.write_text(yaml.safe_dump(document), encoding="utf-8")


AN_OVERRIDE_LIST = {
    "collections": {
        "definitions": [
            {"title": "Star Wars", "builder": "tmdb_collection", "params": {"id": 10}}
        ]
    }
}


async def _store(session_factory, document: dict) -> None:
    """Put a stored overrides document in place, the way a prior save would."""
    async with session_factory() as session:
        stmt = insert(ConfigOverride).values(id=1, document=document)
        stmt = stmt.on_conflict_do_update(index_elements=["id"], set_={"document": document})
        await session.execute(stmt)
        await session.commit()


async def test_the_first_definitions_override_is_refused_while_the_file_lists_some(
    client, auth_headers, config_file, session_factory
):
    """The freezing guard, API-side. The refusal names the path so the panel's
    `fieldErrors` renders it against `collections.definitions`."""
    _with_file_definitions(config_file)

    response = await client.put(
        "/api/config/overrides", json={"document": AN_OVERRIDE_LIST}, headers=auth_headers
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [item["path"] for item in detail] == ["collections.definitions"]
    assert "replaces" in detail[0]["message"]

    # Never half-apply: no row was written.
    async with session_factory() as session:
        assert (await session.execute(select(ConfigOverride))).scalars().first() is None


async def test_the_preview_refuses_the_same_document_the_save_does(
    client, auth_headers, config_file
):
    """Check must not answer "the server would accept this" about a document
    the server refuses -- the panel's Check button offers exactly that."""
    _with_file_definitions(config_file)

    response = await client.post(
        "/api/config/preview", json={"document": AN_OVERRIDE_LIST}, headers=auth_headers
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["path"] == "collections.definitions"


async def test_editing_a_stored_definitions_override_is_allowed_while_the_file_lists_some(
    client, auth_headers, config_file, session_factory
):
    """The state the editor lives in. The file's list is already shadowed by
    the stored one, so the destructive transition has already happened and
    refusing here would brick every subsequent edit."""
    _with_file_definitions(config_file)
    await _store(session_factory, AN_OVERRIDE_LIST)
    edited = {
        "collections": {
            "definitions": [
                {
                    "title": "Star Wars",
                    "builder": "tmdb_collection",
                    "params": {"id": 10},
                    "limit": 25,
                }
            ]
        }
    }

    response = await client.put(
        "/api/config/overrides", json={"document": edited}, headers=auth_headers
    )

    assert response.status_code == 200


async def test_the_first_definitions_override_is_allowed_when_the_file_lists_none(
    client, auth_headers
):
    """The migrated deployment: an empty `definitions:` in the YAML is the
    second way forward the panel's guard note names."""
    response = await client.put(
        "/api/config/overrides", json={"document": AN_OVERRIDE_LIST}, headers=auth_headers
    )

    assert response.status_code == 200


async def test_an_unrelated_save_is_untouched_while_the_file_lists_definitions(
    client, auth_headers, config_file
):
    """The guard is about one key. An operator editing a text setting must not
    be refused because their YAML happens to define collections."""
    _with_file_definitions(config_file)

    response = await client.put(
        "/api/config/overrides", json={"document": TEXT_EDIT}, headers=auth_headers
    )

    assert response.status_code == 200
```

- [ ] **Step 3: Run the guard tests to verify they fail**

Explicit node ids, not a `-k` expression: a `-k` needs its own quoting inside
the already-quoted `sh -c` and silently selects nothing when the quoting is
lost, which reads as a green run.

```bash
docker compose -p pde1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pde1-red1 test sh -c \
  "set -o pipefail; pytest -q tests/test_api_config_editor.py::test_the_first_definitions_override_is_refused_while_the_file_lists_some tests/test_api_config_editor.py::test_the_preview_refuses_the_same_document_the_save_does tests/test_api_config_editor.py::test_editing_a_stored_definitions_override_is_allowed_while_the_file_lists_some tests/test_api_config_editor.py::test_the_first_definitions_override_is_allowed_when_the_file_lists_none tests/test_api_config_editor.py::test_an_unrelated_save_is_untouched_while_the_file_lists_definitions 2>&1 | tee /app/.superpowers/run-p-defedit-t1-red1.log"
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-t1-red1.log`.**
Expected: **2 failed, 3 passed**. The two failures are
`test_the_first_definitions_override_is_refused_while_the_file_lists_some` and
`test_the_preview_refuses_the_same_document_the_save_does`, both with
`assert 200 == 422` — there is no guard yet. The other three pass because they
assert the behavior that already holds; they are the guard's *blast-radius*
pins and must stay green through Step 5. Then `docker rm pde1-red1`.

- [ ] **Step 4: Write the guard**

In `src/autoposter/api/routes.py`, insert this immediately **before**
`async def _validated_generation` (currently line 1425, right after
`_render_affecting`'s closing `return`):

```python
# The refusal the Custom collections panel makes in copy (`FILE_ROWS_NOTE`),
# said once here so the API and the UI cannot drift apart on the wording.
DEFINITIONS_GUARD_REFUSAL = (
    "the mounted config file lists collections.definitions, and an overrides "
    "list replaces the file's WHOLESALE -- storing this one would stop every "
    "file-defined definition being built. Either keep managing definitions in "
    "the config file, or move those rows into the overrides once and empty the "
    "file's definitions list"
)


async def _definitions_guard(request: Request, base: dict, document: dict) -> None:
    """Refuse the FIRST stored ``collections.definitions`` override while the
    mounted file still lists definitions.

    The freezing guard, moved from the panel into the API. It was UI-only:
    `CustomCollectionsPanel` disables Create while any listed row has
    ``provenance: "file"``, which is a control an operator can bypass with one
    `curl`. What the bypass costs is not a validation error -- the document is
    perfectly valid -- it is every file-defined collection silently ceasing to
    be built, discovered whenever somebody next looks at Plex.

    The predicate is the FIRST such store, not "the file lists definitions".
    Once an override is stored the file's list is already shadowed, and that
    is the state the definitions EDITOR lives in (the listing reads
    ``"override"`` there, and the panel offers Edit and Remove) -- refusing
    there would brick the editor on every deployment whose YAML kept a
    ``definitions:`` block. So this fires exactly on the transition, which is
    exactly when ``fileRows`` is true in the panel.

    Reached before anything is written, like every other refusal on this path.
    """
    incoming = document.get("collections")
    if not isinstance(incoming, dict) or "definitions" not in incoming:
        return
    section = base.get("collections")
    file_rows = section.get("definitions") if isinstance(section, dict) else None
    if not isinstance(file_rows, list) or not file_rows:
        return
    async with request.app.state.session_factory() as session:
        try:
            held = await load_overrides_document(session)
        except ValueError as exc:
            # The same answer `GET /api/config` gives the same corrupt row: an
            # operator needs to know what to fix, not an AttributeError.
            raise HTTPException(
                status_code=500,
                detail="config overrides row is corrupt (not a JSON object); fix or delete it",
            ) from exc
    stored_section = held.get("collections")
    if isinstance(stored_section, dict) and "definitions" in stored_section:
        return
    raise HTTPException(
        status_code=422,
        detail=[_error("collections.definitions", DEFINITIONS_GUARD_REFUSAL)],
    )
```

Then, inside `_validated_generation`, immediately after the `base = …` line
(currently line 1460):

```python
    base = await asyncio.to_thread(read_config_document, request.app.state.config_path)
    await _definitions_guard(request, base, document)
```

- [ ] **Step 5: Run the guard tests to verify they pass**

```bash
docker compose -p pde1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pde1-green1 test sh -c \
  "set -o pipefail; pytest -q tests/test_api_config_editor.py 2>&1 | tee /app/.superpowers/run-p-defedit-t1-green1.log"
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-t1-green1.log`.**
Expected: **0 failed** — the five new tests plus every pre-existing test in the
file. Then `docker rm pde1-green1`.

- [ ] **Step 6: Commit the guard**

```bash
git add src/autoposter/api/routes.py tests/test_api_config_editor.py
git commit --no-gpg-sign -m "feat(config): the definitions freezing guard is enforced API-side, not only in the panel"
```

- [ ] **Step 7: Read this before Step 8 — the characterization block**

Steps 8–10 pin the wholesale-replace semantics the editor is built on:
splicing one entry of the stored array must leave its siblings and its own
unreached fields byte-identical. **These tests cannot go RED**: they assert
behavior `merge_overrides` already has (`config/overrides.py:69-93` replaces a
list wholesale) and that `GET /api/config` already round-trips (`model_dump`
serves every field of every definition). Writing them anyway is the point of
constraint 2 — they are the assertion that fails the day someone "improves"
the write path into a rebuild. A PASS on the first run is the expected and
correct result here; do not manufacture a failure to satisfy RED-first.

- [ ] **Step 8: Write the wholesale-replace characterization tests**

Append to `tests/test_api_config_editor.py`:

```python
# --- The wholesale-replace law (row 138's editor is built on it) ----------
#
# CHARACTERIZATION, not RED-first: these pin behavior `merge_overrides` and
# `GET /api/config` already have. They exist so the day the panel's splice is
# rewritten as a rebuild, something says so out loud.

THREE_DEFINITIONS = {
    "collections": {
        "definitions": [
            {"title": "First", "builder": "tmdb_collection", "params": {"id": 1}},
            {
                "title": "Second",
                "builder": "mdblist_list",
                "params": {"list": "someone/weekly"},
                "summary": "What the household watched.",
                "schedule": {"every_n_runs": 3},
                "changes_webhook": "https://hooks.example/T0K3N/path",
                "item_label": ["Weekly"],
            },
            {"title": "Third", "builder": "tmdb_collection", "params": {"id": 3}},
        ]
    }
}


async def test_editing_one_definition_does_not_perturb_its_siblings(
    client, auth_headers
):
    """The negative assertion (facts C2). A whole-list write that changed
    ONLY the entry the operator edited is the whole contract; entries either
    side must come back byte-identical."""
    await client.put(
        "/api/config/overrides", json={"document": THREE_DEFINITIONS}, headers=auth_headers
    )
    entries = deepcopy(THREE_DEFINITIONS["collections"]["definitions"])
    entries[1] = {**entries[1], "limit": 25}

    response = await client.put(
        "/api/config/overrides",
        json={"document": {"collections": {"definitions": entries}}},
        headers=auth_headers,
    )
    assert response.status_code == 200

    served = (await client.get("/api/config", headers=auth_headers)).json()
    definitions = served["collections"]["definitions"]
    assert definitions[0]["title"] == "First"
    assert definitions[0]["limit"] is None
    assert definitions[2]["title"] == "Third"
    assert definitions[2]["limit"] is None
    assert definitions[1]["limit"] == 25


async def test_an_edited_definition_keeps_every_field_the_edit_did_not_reach(
    client, auth_headers
):
    """The loss-free law. `summary`, `schedule`, `item_label` and
    `changes_webhook` are none of them in the editor's curated subset -- an
    entry rewritten around them has to bring them through untouched, which is
    what `{...storedEntry, ...edits}` buys and what a rebuild from the
    seven-field listing would destroy."""
    await client.put(
        "/api/config/overrides", json={"document": THREE_DEFINITIONS}, headers=auth_headers
    )
    entries = deepcopy(THREE_DEFINITIONS["collections"]["definitions"])
    entries[1] = {**entries[1], "limit": 25}

    await client.put(
        "/api/config/overrides",
        json={"document": {"collections": {"definitions": entries}}},
        headers=auth_headers,
    )

    served = (await client.get("/api/config", headers=auth_headers)).json()
    second = served["collections"]["definitions"][1]
    assert second["summary"] == "What the household watched."
    assert second["schedule"] == {"every_n_runs": 3, "months": None}
    assert second["item_label"] == ["Weekly"]
    assert second["changes_webhook"] == "https://hooks.example/T0K3N/path"


async def test_the_served_config_round_trips_a_definition_the_editor_reads_back(
    client, auth_headers
):
    """What the panel's `documentFromConfig` seeds from. The served
    `collections.definitions` IS the stored array (an override wins the
    merge), so an editor seeded from the GET writes back what it was given --
    every key, at full depth."""
    await client.put(
        "/api/config/overrides", json={"document": THREE_DEFINITIONS}, headers=auth_headers
    )

    served = (await client.get("/api/config", headers=auth_headers)).json()

    assert "collections.definitions" in served["overridden_paths"]
    stored_second = THREE_DEFINITIONS["collections"]["definitions"][1]
    served_second = served["collections"]["definitions"][1]
    for key, value in stored_second.items():
        if key == "schedule":
            continue  # pydantic fills the model's own unset field, checked above
        assert served_second[key] == value
```

`deepcopy` is imported at the top of the file if it is not already — check
with `grep -n "^from copy import" tests/test_api_config_editor.py` and add
`from copy import deepcopy` beside the other stdlib imports if it is missing.

- [ ] **Step 9: Run the characterization tests**

```bash
docker compose -p pde1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pde1-green2 test sh -c \
  "set -o pipefail; pytest -q tests/test_api_config_editor.py 2>&1 | tee /app/.superpowers/run-p-defedit-t1-green2.log"
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-t1-green2.log`.**
Expected: **0 failed**, all three new tests PASSING on their first run — see
Step 7 for why that is correct here. If
`test_an_edited_definition_keeps_every_field_the_edit_did_not_reach` fails on
the `schedule` assertion, read the actual served value and correct the
expectation to pydantic's full dump of `ScheduleGate` (both fields); do not
weaken the assertion to a subset — the whole point is depth. Then
`docker rm pde1-green2`.

- [ ] **Step 10: Ruff, then commit**

```bash
docker compose -p pde1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pde1-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-p-defedit-t1-ruff.log"
```

**Read the log.** Expected: `All checks passed!`. Then:

```bash
docker rm pde1-ruff
git add tests/test_api_config_editor.py
git commit --no-gpg-sign -m "test(config): pin the wholesale-replace law the definitions editor is built on"
docker compose -p pde1 down
```

**Task 1 report must record:** the resolved `git rev-parse HEAD` of the cut,
the two baseline totals from Step 1, and the new backend total (baseline + 8).

---

## Task 2: The panel's Edit UI, the curated form, and the redaction

**Files:**
- Create: `frontend/src/pages/DefinitionEditor.tsx`
- Create: `frontend/src/pages/DefinitionEditor.test.tsx`
- Modify: `frontend/src/pages/CustomCollectionsPanel.tsx` (`REMOVE_NOTE` at
  :49-53, a new `EDIT_NOTE`, `documentForEdit` after `documentForRemove` at
  :101-109, `editing` state, the row's action cell at :410-433, the note
  paragraph at :374)
- Modify: `frontend/src/pages/CustomCollectionsPanel.test.tsx`
- Modify: `frontend/src/pages/custom-collections.css`

**Interfaces:**
- Consumes (from Task 1): the 422 body
  `{"detail": [{"path": "collections.definitions", "message": …}]}`, already
  rendered by the panel's existing `errors` list via `fieldErrors`.
- Produces (for Task 3): the component
  `DefinitionEditor({ entry, libraries, descriptions, busy, onSave, onCancel })`
  where `entry: Record<string, unknown>` is a STORED override entry,
  `libraries: string[]` is the listing's `collections.libraries`,
  `descriptions: Record<string, string>` maps
  `"collections.definitions[].<field>"` to its hover text, `busy: boolean`
  disables the actions, `onSave(entry: Record<string, unknown>): void` receives
  the rebuilt entry, `onCancel(): void` closes the form. Plus the two exported
  pure functions `draftFrom(entry, libraries)` and
  `entryFromDraft(entry, draft, roster)`, and the exported `hostOnly(value)`.

- [ ] **Step 1: Write the failing editor tests**

Create `frontend/src/pages/DefinitionEditor.test.tsx`:

```tsx
/** The definitions editor: row 138's literal scope, and the one rule that
 * makes it safe.
 *
 * The rule is that an edited entry is `{...storedEntry, ...curatedEdits}` --
 * the eleven curated fields are the only keys the form may add, change or
 * delete, and every other key of the stored entry rides through untouched.
 * The seven-field listing is never a source; the stored overrides array is.
 *
 * Two more things are pinned here. `libraries` has its own "every configured
 * library" control rather than being inferred from all-boxes-checked, because
 * an explicit list of today's names and the absent key are DIFFERENT requests.
 * And `changes_webhook` is rendered host-only: it may embed a token, so it
 * gets the discipline `notifications.url` gets. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DefinitionEditor, draftFrom, entryFromDraft, hostOnly } from "./DefinitionEditor";

const LIBRARIES = ["Movies", "TV Shows"];

/** A stored entry carrying five fields the form does not reach. */
const RICH_ENTRY = {
  title: "Weekly Watched",
  builder: "mdblist_list",
  params: { list: "someone/weekly" },
  summary: "What the household watched.",
  schedule: { every_n_runs: 3 },
  filters: { "year.gte": 2000 },
  tmdb_summary: 12345,
  changes_webhook: "https://hooks.example/T0K3N/path",
};

const DESCRIPTIONS = {
  "collections.definitions[].limit":
    "A cap on the collection's member count, applied after resolution.",
};

function renderEditor(entry: Record<string, unknown>, onSave = vi.fn()) {
  render(
    <DefinitionEditor
      entry={entry}
      libraries={LIBRARIES}
      descriptions={DESCRIPTIONS}
      busy={false}
      onSave={onSave}
      onCancel={vi.fn()}
    />,
  );
  return onSave;
}

describe("the definition editor", () => {
  it("round-trips an untouched entry byte-for-byte", () => {
    const onSave = renderEditor(RICH_ENTRY);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith(RICH_ENTRY);
  });

  it("preserves every field the form does not reach", () => {
    const onSave = renderEditor(RICH_ENTRY);

    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({ ...RICH_ENTRY, limit: 25 });
  });

  it("writes a key only when its value differs from the schema default", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
    });

    // Typing the default back into a field that never had the key must not
    // add it: an explicit "custom" and an absent `sort` are the same order.
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "custom" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
    });
  });

  it("clears an optional field by dropping its key, never by writing null", () => {
    const onSave = renderEditor({ ...RICH_ENTRY, limit: 25 });

    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith(RICH_ENTRY);
    expect(Object.keys(onSave.mock.calls[0][0])).not.toContain("limit");
  });

  it("keeps an explicit library list explicit when the edit is about something else", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      libraries: ["Movies", "TV Shows"],
    });

    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // All boxes are checked -- but the stored entry SAID these two names, and
    // dropping the key would silently re-scope it to whatever
    // collections.libraries lists next month.
    expect(onSave).toHaveBeenCalledWith({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      libraries: ["Movies", "TV Shows"],
      limit: 5,
    });
  });

  it("drops the libraries key when the every-library control is turned on", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      libraries: ["Movies"],
    });

    fireEvent.click(screen.getByLabelText("Every configured library"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
    });
  });

  it("offers a library the config no longer lists rather than dropping it", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      libraries: ["Movies", "Retired Library"],
    });

    expect(screen.getByLabelText("Retired Library")).toBeChecked();

    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave.mock.calls[0][0].libraries).toEqual(["Movies", "Retired Library"]);
  });

  it("refuses to save an empty scope, naming why", () => {
    renderEditor({ title: "Star Wars", builder: "tmdb_collection", params: { id: 10 } });

    fireEvent.click(screen.getByLabelText("Every configured library"));
    fireEvent.click(screen.getByLabelText("Movies"));
    fireEvent.click(screen.getByLabelText("TV Shows"));

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText(/no library at all/)).toBeInTheDocument();
  });

  it("renders the change webhook host-only and never its path", () => {
    renderEditor(RICH_ENTRY);

    expect(screen.getByText("hooks.example")).toBeInTheDocument();
    expect(screen.queryByText(/T0K3N/)).toBeNull();
  });

  it("shows the builder and params without offering to edit them", () => {
    renderEditor(RICH_ENTRY);

    expect(screen.getByText("mdblist_list")).toBeInTheDocument();
    expect(screen.getByText('{"list":"someone/weekly"}')).toBeInTheDocument();
    expect(screen.queryByLabelText("Builder")).toBeNull();
    expect(screen.queryByLabelText("Params")).toBeNull();
  });

  it("hangs the schema's own description on the field as hover text", () => {
    renderEditor(RICH_ENTRY);

    expect(screen.getByText("Limit").closest("label")).toHaveAttribute(
      "title",
      DESCRIPTIONS["collections.definitions[].limit"],
    );
  });

  it("edits labels as a list, so a label containing a comma survives", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      labels: ["Space, Opera"],
    });

    fireEvent.click(screen.getByRole("button", { name: "Add label" }));
    fireEvent.change(screen.getByLabelText("labels[1]"), { target: { value: "Sci-Fi" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave.mock.calls[0][0].labels).toEqual(["Space, Opera", "Sci-Fi"]);
  });
});

describe("the editor's pure halves", () => {
  it("seeds the every-library control from whether the key was there", () => {
    expect(draftFrom({ title: "A", builder: "b", params: {} }, LIBRARIES).everyLibrary)
      .toBe(true);
    expect(
      draftFrom({ title: "A", builder: "b", params: {}, libraries: ["Movies"] }, LIBRARIES)
        .everyLibrary,
    ).toBe(false);
  });

  it("never invents a key the draft did not set", () => {
    const entry = { title: "A", builder: "b", params: {} };
    const roster = LIBRARIES;
    expect(entryFromDraft(entry, draftFrom(entry, LIBRARIES), roster)).toEqual(entry);
  });

  it("reduces a URL to its host and says so when it cannot", () => {
    expect(hostOnly("https://hooks.example/T0K3N/path")).toBe("hooks.example");
    expect(hostOnly("not a url")).toBe("(unreadable URL)");
  });
});
```

- [ ] **Step 2: Run the editor tests to verify they fail**

```bash
docker compose -p pde2 run --name pde2-red1 web npm test -- DefinitionEditor > .superpowers/run-p-defedit-t2-red1.log 2>&1
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-t2-red1.log`.**
Expected: the whole file fails to collect —
`Failed to resolve import "./DefinitionEditor"`. That is the RED. Then
`docker rm pde2-red1`.

- [ ] **Step 3: Write the editor**

Create `frontend/src/pages/DefinitionEditor.tsx`:

```tsx
/** The Edit form for one stored `collections.definitions` entry — roadmap row
 * 138, at its literal scope.
 *
 * One rule makes this safe, and it is the whole component: the edited entry is
 * `{...storedEntry, ...curatedEdits}`. The eleven fields below are the only
 * keys this form may add, change or delete; every other key of the stored
 * entry — `schedule`, `filters`, `params`, `changes_webhook`, the `visible_*`
 * flags, the ten others — rides through untouched. That is why an edit here
 * cannot lose a field the operator wrote in YAML and migrated, and it is why
 * the entry handed in must be the STORED overrides array's own entry and never
 * `GET /api/collections/definitions`' seven-field projection.
 *
 * A curated field is written only when its value differs from the schema's own
 * default; a value equal to the default drops the key, which is the overrides
 * document's revert (`api/overrides.ts`: `null` is a value, absence is the
 * revert). That is semantics-preserving for every field here EXCEPT
 * `libraries`, whose default `None` means "every library in
 * collections.libraries" — a moving target, not a fixed value — so scope has
 * its own explicit control rather than being inferred from all-boxes-checked.
 *
 * Three fields are shown and not edited. `builder` is a registry key (free
 * text would name builders that do not exist). `params` is per-builder shaped,
 * so no generic widget is loss-free — changing it is still remove-and-recreate.
 * `changes_webhook` may embed a token in its path, so it is rendered HOST ONLY,
 * the discipline `notifications.url` gets; it is not editable because a value
 * the page was never shown cannot honestly be edited, and the keep-sentinel
 * that solves that for `notifications.url` refuses to resolve inside a list by
 * design. */
import { useState } from "react";

/** The path prefix the schema's descriptions are published under
 * (`config/descriptions.py` walks `list[Model]` into a `[]` segment). */
const DESCRIPTION_PREFIX = "collections.definitions[].";

/** Plex's own collection display modes, plus the unset state as its own named
 * option — `None` ("leave Plex's setting alone") and `"default"` are two
 * different requests and a single select must not blur them. */
const COLLECTION_MODES = [
  { value: "", label: "leave Plex's setting alone" },
  { value: "default", label: "default" },
  { value: "hide", label: "hide" },
  { value: "hideItems", label: "hideItems" },
  { value: "showItems", label: "showItems" },
];

const EMPTY_SCOPE_NOTE =
  "An empty scope means no library at all, which builds nothing — check a " +
  "library, or turn on every configured library.";

export interface Draft {
  title: string;
  everyLibrary: boolean;
  libraries: Record<string, boolean>;
  summary: string;
  sort: string;
  sync_mode: string;
  limit: string;
  labels: string[];
  label_sync: boolean;
  item_label: string[];
  sort_title: string;
  collection_mode: string;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** The scope checkboxes to offer: the config's own library names, plus any
 * name the stored entry carries that the config no longer lists. Without the
 * union, editing anything at all would silently drop a scope the operator
 * wrote for a library they renamed. */
export function libraryRoster(
  entry: Record<string, unknown>,
  libraries: string[],
): string[] {
  const stored = stringList(entry.libraries);
  return [...libraries, ...stored.filter((name) => !libraries.includes(name))];
}

/** The form's state, seeded from the stored entry. */
export function draftFrom(
  entry: Record<string, unknown>,
  libraries: string[],
): Draft {
  const roster = libraryRoster(entry, libraries);
  const stored = Array.isArray(entry.libraries) ? stringList(entry.libraries) : null;
  return {
    title: text(entry.title),
    // The KEY's presence, not the checkbox pattern: an explicit list naming
    // every library today is a different request from "every library".
    everyLibrary: stored === null,
    libraries: Object.fromEntries(
      roster.map((name) => [name, stored === null ? true : stored.includes(name)]),
    ),
    summary: text(entry.summary),
    sort: text(entry.sort),
    sync_mode: entry.sync_mode === "append" ? "append" : "sync",
    limit: typeof entry.limit === "number" ? String(entry.limit) : "",
    labels: stringList(entry.labels),
    label_sync: entry.label_sync === true,
    item_label: stringList(entry.item_label),
    sort_title: text(entry.sort_title),
    collection_mode: text(entry.collection_mode),
  };
}

/** The stored entry with the curated fields applied — the loss-free law.
 *
 * `next` starts as a copy of the stored entry, so every key this form does not
 * name survives by construction rather than by being listed somewhere. */
export function entryFromDraft(
  entry: Record<string, unknown>,
  draft: Draft,
  roster: string[],
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...entry };
  const put = (key: string, value: unknown, isDefault: boolean) => {
    if (isDefault) delete next[key];
    else next[key] = value;
  };

  next.title = draft.title.trim();

  if (draft.everyLibrary) delete next.libraries;
  else next.libraries = roster.filter((name) => draft.libraries[name]);

  put("summary", draft.summary, draft.summary === "");
  const sort = draft.sort.trim();
  put("sort", sort, sort === "" || sort === "custom");
  put("sync_mode", draft.sync_mode, draft.sync_mode === "sync");
  put("limit", Number(draft.limit), draft.limit.trim() === "");
  const labels = draft.labels.filter((item) => item.trim() !== "");
  put("labels", labels, labels.length === 0);
  put("label_sync", true, !draft.label_sync);
  const itemLabels = draft.item_label.filter((item) => item.trim() !== "");
  put("item_label", itemLabels, itemLabels.length === 0);
  put("sort_title", draft.sort_title, draft.sort_title === "");
  put("collection_mode", draft.collection_mode, draft.collection_mode === "");

  return next;
}

/** A URL reduced to its host — never its path, which is where a token hides
 * (the `notifications.url` rule, `api/routes.py`'s `_host_only`). A value that
 * is not a URL says so rather than being echoed: echoing it would defeat the
 * whole point on the one input shape most likely to be malformed. */
export function hostOnly(value: string): string {
  try {
    const host = new URL(value).host;
    return host === "" ? "(unreadable URL)" : host;
  } catch {
    return "(unreadable URL)";
  }
}

/** Per-item inputs, not a comma-joined string: a joined field cannot
 * represent a label that contains the separator. */
function StringList({
  name,
  addLabel,
  value,
  onChange,
}: {
  name: string;
  addLabel: string;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <span className="definition-list-edit">
      {value.map((item, index) => (
        <span className="definition-list-item" key={index}>
          <input
            type="text"
            aria-label={`${name}[${index}]`}
            value={item}
            onChange={(event) =>
              onChange(value.map((v, i) => (i === index ? event.target.value : v)))
            }
          />
          <button
            type="button"
            aria-label={`Remove ${name}[${index}]`}
            onClick={() => onChange(value.filter((_, i) => i !== index))}
          >
            ×
          </button>
        </span>
      ))}
      <button type="button" onClick={() => onChange([...value, ""])}>
        {addLabel}
      </button>
    </span>
  );
}

export function DefinitionEditor({
  entry,
  libraries,
  descriptions,
  busy,
  onSave,
  onCancel,
}: {
  entry: Record<string, unknown>;
  libraries: string[];
  descriptions: Record<string, string>;
  busy: boolean;
  onSave: (entry: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const roster = libraryRoster(entry, libraries);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(entry, libraries));
  const set = (patch: Partial<Draft>) =>
    setDraft((previous) => ({ ...previous, ...patch }));

  // Row 138's own promise: the twenty-one fields are already described and
  // already served under a `[]` segment, waiting for a row to hang on.
  const hint = (field: string) => descriptions[DESCRIPTION_PREFIX + field] || undefined;

  const scopeEmpty =
    !draft.everyLibrary && roster.every((name) => !draft.libraries[name]);
  const ready = draft.title.trim() !== "" && !scopeEmpty;

  const webhook = text(entry.changes_webhook);

  return (
    <div className="definition-editor">
      <h4>Edit definition</h4>

      <label className="definition-field" title={hint("title")}>
        <span>Title</span>
        <input
          type="text"
          aria-label="Title"
          value={draft.title}
          onChange={(event) => set({ title: event.target.value })}
        />
      </label>

      <fieldset className="definition-scope">
        <legend className="muted">Libraries</legend>
        <label className="definition-library">
          <input
            type="checkbox"
            aria-label="Every configured library"
            checked={draft.everyLibrary}
            onChange={(event) => set({ everyLibrary: event.target.checked })}
          />
          <span>Every configured library</span>
        </label>
        {roster.map((name) => (
          <label key={name} className="definition-library">
            <input
              type="checkbox"
              aria-label={name}
              disabled={draft.everyLibrary}
              checked={draft.everyLibrary || (draft.libraries[name] ?? false)}
              onChange={(event) =>
                set({ libraries: { ...draft.libraries, [name]: event.target.checked } })
              }
            />
            <span>{name}</span>
          </label>
        ))}
        {scopeEmpty && <p className="definition-refusal">{EMPTY_SCOPE_NOTE}</p>}
      </fieldset>

      <label className="definition-field" title={hint("summary")}>
        <span>Summary</span>
        <input
          type="text"
          aria-label="Summary"
          value={draft.summary}
          onChange={(event) => set({ summary: event.target.value })}
        />
      </label>

      <label className="definition-field" title={hint("sort")}>
        <span>Sort</span>
        <input
          type="text"
          aria-label="Sort"
          value={draft.sort}
          onChange={(event) => set({ sort: event.target.value })}
        />
      </label>

      <label className="definition-field" title={hint("sync_mode")}>
        <span>Sync mode</span>
        <select
          aria-label="Sync mode"
          value={draft.sync_mode}
          onChange={(event) => set({ sync_mode: event.target.value })}
        >
          <option value="sync">sync</option>
          <option value="append">append</option>
        </select>
      </label>

      <label className="definition-field" title={hint("limit")}>
        <span>Limit</span>
        <input
          type="number"
          min={1}
          aria-label="Limit"
          value={draft.limit}
          onChange={(event) => set({ limit: event.target.value })}
        />
      </label>

      <label className="definition-field" title={hint("sort_title")}>
        <span>Sort title</span>
        <input
          type="text"
          aria-label="Sort title"
          value={draft.sort_title}
          onChange={(event) => set({ sort_title: event.target.value })}
        />
      </label>

      <label className="definition-field" title={hint("collection_mode")}>
        <span>Collection mode</span>
        <select
          aria-label="Collection mode"
          value={draft.collection_mode}
          onChange={(event) => set({ collection_mode: event.target.value })}
        >
          {COLLECTION_MODES.map((mode) => (
            <option key={mode.value} value={mode.value}>
              {mode.label}
            </option>
          ))}
        </select>
      </label>

      <div className="definition-field" title={hint("labels")}>
        <span>Labels</span>
        <StringList
          name="labels"
          addLabel="Add label"
          value={draft.labels}
          onChange={(next) => set({ labels: next })}
        />
      </div>

      <label className="definition-field" title={hint("label_sync")}>
        <span>Label sync</span>
        <input
          type="checkbox"
          aria-label="Label sync"
          checked={draft.label_sync}
          onChange={(event) => set({ label_sync: event.target.checked })}
        />
      </label>

      <div className="definition-field" title={hint("item_label")}>
        <span>Member labels</span>
        <StringList
          name="item_label"
          addLabel="Add member label"
          value={draft.item_label}
          onChange={(next) => set({ item_label: next })}
        />
      </div>

      <dl className="definition-readonly">
        <dt title={hint("builder")}>Builder</dt>
        <dd className="mono">{text(entry.builder)}</dd>
        <dt title={hint("params")}>Params</dt>
        <dd className="mono">{JSON.stringify(entry.params)}</dd>
        {webhook !== "" && (
          <>
            <dt title={hint("changes_webhook")}>Change webhook</dt>
            <dd className="mono">{hostOnly(webhook)}</dd>
          </>
        )}
      </dl>

      <div className="definition-actions">
        <button
          type="button"
          disabled={busy || !ready}
          onClick={() => onSave(entryFromDraft(entry, draft, roster))}
        >
          Save
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the editor tests to verify they pass**

```bash
docker compose -p pde2 run --name pde2-green1 web npm test -- DefinitionEditor > .superpowers/run-p-defedit-t2-green1.log 2>&1
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-t2-green1.log`.**
Expected: **15 passed, 0 failed** (twelve in "the definition editor", three in
"the editor's pure halves"). Then `docker rm pde2-green1`.

- [ ] **Step 5: Commit the editor**

```bash
git add frontend/src/pages/DefinitionEditor.tsx frontend/src/pages/DefinitionEditor.test.tsx
git commit --no-gpg-sign -m "feat(collections): the definition editor form, curated and loss-free"
```

- [ ] **Step 6: Write the failing panel tests**

Append inside the existing `describe("the custom collections panel", …)` block
in `frontend/src/pages/CustomCollectionsPanel.test.tsx`, before its closing
`});`:

```tsx
  it("offers Edit on override rows and never on file rows", async () => {
    await renderPanel({
      definitions: listing([FILE_ROW]),
      config: config(),
    });

    // Provenance is uniform: a listing with a file row has no override rows
    // at all, so the guard is structural -- there is nothing to edit.
    expect(screen.queryByRole("button", { name: "Edit Hand Picked" })).toBeNull();
  });

  it("seeds the edit form from the STORED entry, never from the listing", async () => {
    await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Weekly Watched" }));

    // `summary` is in the stored entry and NOT in the seven-field listing --
    // a form built from the listing would show it empty and then save the
    // blank over the operator's text.
    expect(screen.getByLabelText("Summary")).toHaveValue(
      "What the household watched this week.",
    );
    expect(screen.getByLabelText("Limit")).toHaveValue(25);
  });

  it("writes the whole list back with only the edited entry changed", async () => {
    const { puts } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Weekly Watched" }));
    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/^Saved\./);

    const document = sentDocument(puts);
    // The negative assertion: the sibling is byte-identical.
    expect(document.collections.definitions[0]).toEqual(STORED_ENTRIES[0]);
    expect(document.collections.definitions[1]).toEqual({
      ...STORED_ENTRIES[1],
      limit: 10,
    });
    expect(document.collections.definitions).toHaveLength(2);
    // And an override about something else is still there.
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("renders a 422 from the edit against the path the server named", async () => {
    await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
      save: () =>
        json(
          {
            detail: [
              {
                path: "collections.definitions",
                message: "the mounted config file lists collections.definitions",
              },
            ],
          },
          422,
        ),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Star Wars" }));
    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("The server rejected this change.")).toBeInTheDocument();
    expect(screen.getByText("collections.definitions")).toBeInTheDocument();
  });

  it("closes the form and re-reads after a save", async () => {
    const { fetchMock } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Star Wars" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/^Saved\./);

    expect(screen.queryByText("Edit definition")).toBeNull();
    // Two definitions reads and two config reads: the mount, then the re-read.
    const reads = fetchMock.mock.calls.filter(
      ([path]) => path === "/api/collections/definitions",
    );
    expect(reads).toHaveLength(2);
  });

  it("no longer claims there is no edit", async () => {
    await renderPanel({ definitions: listing(OVERRIDE_ROWS), config: overriddenConfig() });

    expect(screen.queryByText(/There is no edit/)).toBeNull();
  });
```

- [ ] **Step 7: Run the panel tests to verify they fail**

```bash
docker compose -p pde2 run --name pde2-red2 web npm test -- CustomCollectionsPanel > .superpowers/run-p-defedit-t2-red2.log 2>&1
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-t2-red2.log`.**
Expected: **5 failed** — every test that clicks `Edit …` fails with
`Unable to find an accessible element with the role "button" and name "Edit …"`,
and `no longer claims there is no edit` fails because `REMOVE_NOTE` still says
it. `offers Edit on override rows and never on file rows` PASSES already
(nothing renders an Edit button yet) — that is expected and it must stay green
through Step 9. Then `docker rm pde2-red2`.

- [ ] **Step 8: Wire the editor into the panel**

In `frontend/src/pages/CustomCollectionsPanel.tsx`:

**8a.** Add the import beside the existing ones (after the `overrides` import
block, before the `types` import):

```tsx
import { DefinitionEditor } from "./DefinitionEditor";
```

**8b.** Replace `REMOVE_NOTE` (lines 47-53) with the corrected note plus the
edit note:

```tsx
/** Facts C3: the orphan story on remove, one honest sentence. */
const REMOVE_NOTE =
  "Removing a definition only stops the pass building it — the collection " +
  "already in Plex follows collections.delete_unconfigured: reported as an " +
  "orphan by default, deleted only when that setting says so.";

/** What Edit reaches, and what it deliberately does not (roadmap row 138).
 *
 * The builder and its params are shown and not edited: a builder is a registry
 * key, and a params dict is shaped by the builder that reads it, so no generic
 * widget round-trips one safely. Renaming gets the same sentence Remove gets,
 * because it has the same consequence. */
const EDIT_NOTE =
  "Edit changes a definition's own fields. The builder and its parameters are " +
  "not editable — change those by removing the definition and creating it " +
  "again from a URL. Renaming leaves the collection already in Plex under its " +
  "old title, which collections.delete_unconfigured then treats as an orphan.";
```

**8c.** Add `documentForEdit` immediately after `documentForRemove` (after
line 109):

```tsx
/** The stored document with the entry at `ordinal` REPLACED.
 *
 * A splice, never a rebuild: the entries either side are the same object
 * references that came out of `overrideList`, so an edit cannot perturb a
 * sibling even by accident. The key is never dropped — an edit always leaves
 * at least the entry it edited. */
function documentForEdit(
  stored: OverridesDocument,
  ordinal: number,
  entry: Record<string, unknown>,
): OverridesDocument {
  const next = overrideList(stored).map((item, at) => (at === ordinal ? entry : item));
  return withPath(stored, DEFINITIONS_PATH, next);
}
```

**8d.** Add the editing state beside the other `useState` calls (after
`const [result, setResult] = useState<ConfigSaveResponse | null>(null);`,
line 180):

```tsx
  // Which override ordinal is open in the edit form, or null. The ORDINAL, not
  // the entry: the entry is read from `stored` at render time, so a re-read
  // after somebody else's save cannot leave the form editing a stale copy.
  const [editing, setEditing] = useState<number | null>(null);
```

**8e.** Close the form on every re-read. In `adopt` (line 193-204), add as the
last statement of the callback body, after `setChosen(...)`:

```tsx
      // A fresh listing may renumber the ordinals; an open form pointing at
      // the old numbering would write the edit into the wrong entry.
      setEditing(null);
```

**8f.** Render the note. Replace the `<p className="muted custom-note">{REMOVE_NOTE}</p>`
line (line 374) with:

```tsx
      <p className="muted custom-note">{REMOVE_NOTE}</p>
      <p className="muted custom-note">{EDIT_NOTE}</p>
```

**8g.** Add the Edit button and the form. Replace the row's action cell — the
whole `<td>` block at lines 410-433 — with:

```tsx
                  <td className="custom-row-actions">
                    {/* Edit and Remove exist only for rows the overrides
                        document supplies. A file row's missing controls ARE
                        the freezing guard made visible: this panel never
                        writes file entries anywhere. Provenance is uniform, so
                        a listing carrying a file row has no override row at
                        all and neither control can appear. */}
                    {row.provenance === "override" && (
                      <>
                        <button
                          type="button"
                          aria-label={`Edit ${row.title}`}
                          disabled={busy !== null}
                          onClick={() => {
                            touch();
                            setEditing(overrideOrdinal(listing.definitions, index));
                          }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          aria-label={`Remove ${row.title}`}
                          disabled={busy !== null}
                          onClick={() =>
                            void put(
                              documentForRemove(
                                stored,
                                overrideOrdinal(listing.definitions, index),
                              ),
                              `removing-${index}`,
                            )
                          }
                        >
                          {busy === `removing-${index}` ? "Removing…" : "Remove"}
                        </button>
                      </>
                    )}
                  </td>
```

**8h.** Render the form itself. Insert immediately after the closing
`</div>` of the `table-scroll` block and before
`<h3 className="custom-form-title">Create from a list URL</h3>` (line 441):

```tsx
      {editing !== null && overrideList(stored)[editing] !== undefined && (
        <DefinitionEditor
          // Remounts when the ordinal changes, so the draft is re-seeded from
          // the entry being edited rather than carrying the previous one's.
          key={editing}
          entry={overrideList(stored)[editing] as Record<string, unknown>}
          libraries={listing.libraries}
          descriptions={descriptions}
          busy={busy !== null}
          onSave={(entry) =>
            void put(documentForEdit(stored, editing, entry), "editing")
          }
          onCancel={() => setEditing(null)}
        />
      )}
```

**8i.** Serve the descriptions to the form. The panel already holds the served
config only as the seeded overrides document, so keep the map beside it. Add a
state hook after the `stored` declaration (line 160):

```tsx
  // The schema's own per-field text, served by `GET /api/config` under a `[]`
  // segment (`collections.definitions[].limit`). Row 138's own text said these
  // were waiting for a row to hang on; the edit form is that row.
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
```

and populate it inside `adopt`, immediately after `setStored(...)`:

```tsx
      const described = config.field_descriptions;
      setDescriptions(
        described !== null && typeof described === "object" && !Array.isArray(described)
          ? Object.fromEntries(
              Object.entries(described as Record<string, unknown>).map(([key, value]) => [
                key,
                String(value),
              ]),
            )
          : {},
      );
```

**8j.** The save's form-clearing arm keys on `"saving"` (line 342), so an
`"editing"` save leaves the create form alone — which is correct and needs no
change. The re-read closes the form through `adopt` (8e).

- [ ] **Step 9: Run the panel tests to verify they pass**

```bash
docker compose -p pde2 run --name pde2-green2 web npm test -- CustomCollectionsPanel > .superpowers/run-p-defedit-t2-green2.log 2>&1
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-t2-green2.log`.**
Expected: **0 failed** — the six new tests plus the panel's thirteen existing
ones. If an existing test asserted the old `REMOVE_NOTE` sentence ("There is no
edit"), it fails here: fix the test to assert the new copy, do not restore the
sentence — the panel would then be lying. Then `docker rm pde2-green2`.

- [ ] **Step 10: Add the editor's styles**

Append to `frontend/src/pages/custom-collections.css`:

```css
/* The edit form (roadmap row 138). Its own block rather than reusing the
   create form's classes: the two forms sit on the same page and a shared
   class would make a change to one silently move the other. */
.definition-editor {
  margin: 1rem 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.definition-editor h4 {
  margin: 0 0 0.5rem;
}

.definition-field {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.35rem 0;
}

.definition-field > span:first-child {
  flex: 0 0 9rem;
}

.definition-scope {
  margin: 0.5rem 0;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.5rem;
}

.definition-library {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-right: 0.75rem;
}

.definition-list-edit,
.definition-list-item {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  flex-wrap: wrap;
}

.definition-readonly {
  display: grid;
  grid-template-columns: 9rem 1fr;
  gap: 0.25rem 0.5rem;
  margin: 0.75rem 0 0;
}

.definition-readonly dt {
  color: var(--muted);
}

.definition-readonly dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.definition-refusal {
  margin: 0.4rem 0 0;
  color: var(--warning);
}

.definition-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.custom-row-actions {
  display: flex;
  gap: 0.4rem;
}
```

Check the two custom properties resolve — `grep -n "\-\-warning\|--border\|--muted" frontend/src/index.css`.
If `--warning` is not defined in this project's palette, use the value the
panel's existing `.custom-parse-error` rule uses (read it from
`custom-collections.css`) rather than inventing a colour.

- [ ] **Step 11: Full frontend suite and type check**

```bash
docker compose -p pde2 run --name pde2-vitest web npm test > .superpowers/run-p-defedit-t2-vitest.log 2>&1
docker compose -p pde2 run --name pde2-tsc web npx tsc --noEmit > .superpowers/run-p-defedit-t2-tsc.log 2>&1
```

**Read both logs.** Expected: vitest **0 failed**, at the T1 baseline total
plus 21 (15 editor + 6 panel); `tsc` produces no output at all. Then:

```bash
docker rm pde2-vitest pde2-tsc
```

- [ ] **Step 12: Commit the panel wiring**

```bash
git add frontend/src/pages/CustomCollectionsPanel.tsx frontend/src/pages/CustomCollectionsPanel.test.tsx frontend/src/pages/custom-collections.css
git commit --no-gpg-sign -m "feat(collections): Edit beside Create and Remove on the custom collections panel"
docker compose -p pde2 down
```

**Task 2 report must record:** the new vitest total and its delta from the T1
baseline, plus any existing panel test whose copy assertion had to change.

---

## Task 3: The wrap — the docs, the rows, the suites

**Files:**
- Modify: `src/autoposter/config/descriptions.py:20-25` (the module docstring's
  row-138 sentence)
- Modify: `deploy/README.md` (a subsection inside the custom-collections
  material)
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (row 138
  at line 240 closed; row 225 appended after row 224 at line 322)
- Create (uncommitted): `.superpowers/sdd/p-defedit-pr-body.md`

**Interfaces:**
- Consumes: T1's `DEFINITIONS_GUARD_REFUSAL` and T2's shipped editor — both
  described in the row's close.
- Produces: nothing code depends on.

- [ ] **Step 1: Correct the descriptions module's stale sentence**

`src/autoposter/config/descriptions.py`'s module docstring (lines 19-25) says
those paths "address no row today". After Task 2 that is false. Replace exactly
this text:

```
* A field whose annotation is a model gets both an entry of its own -- an
  unset optional submodel renders as one leaf row, and a set one renders as a
  section heading -- and a recursion beneath it. A ``list`` of models gets an
  entry plus a recursion under a ``[]`` segment: those paths address no row
  today, because the editor renders a list of objects read-only (roadmap row
  138), and they are served so that they do the day that row lands. ``[]`` is
  a marker in this map, never a path the API accepts.
```

with:

```
* A field whose annotation is a model gets both an entry of its own -- an
  unset optional submodel renders as one leaf row, and a set one renders as a
  section heading -- and a recursion beneath it. A ``list`` of models gets an
  entry plus a recursion under a ``[]`` segment. The generic Settings editor
  still renders a list of objects read-only, so the paths it addresses are the
  scalar ones; the ``[]`` paths are read by the Custom collections panel's
  edit form (roadmap row 138), for the eleven ``CollectionDefinition`` fields
  it edits -- which is what those entries were served for before there was a
  row to hang them on. ``[]`` is a marker in this map, never a path the API
  accepts.
```

Nothing else in the docstring changes — this is a correction of one claim, not
a rewrite.

- [ ] **Step 2: Document the editor for operators**

In `deploy/README.md`, find the "Creating custom collections from the web UI"
section (added by the custom-collections-ui phase) and append this subsection
at its end, before whatever heading follows:

```markdown
### Editing a custom collection

Rows marked **override** carry an **Edit** button. The form edits a curated
set of the definition's fields — title, library scope, summary, sort, sync
mode, limit, sort title, collection mode, labels, label sync and member
labels — and leaves every other field of that definition exactly as stored.

Three things are shown and not editable:

- the **builder** and its **parameters**, because a builder is a registry key
  and each builder defines its own parameter shape; change those by removing
  the definition and creating it again from a URL;
- the **change webhook**, shown as its host only, because it may carry a token
  in its path. It is preserved across an edit untouched — edit it in the config
  file.

Renaming a definition leaves the collection already in Plex under its old
title, which `collections.delete_unconfigured` then treats as an orphan — the
same rule Remove follows.

Rows marked **config file** carry no Edit button, for the reason they carry no
Remove button: an overrides list replaces the file's wholesale, so the first
one stored would stop every file-defined definition being built. The API
refuses that write too, not only the page.
```

- [ ] **Step 3: Close row 138 and file row 225**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, replace line 240
(row 138) with:

```markdown
| 138 | Settings editor cannot edit list-of-object config (CLOSED — the definitions-editor phase) | Filed by 8a Task 6 out of `frontend/src/pages/Settings.tsx`'s own comment: the generic config editor renders a list of objects read-only, which blocked UI-side editing of `collections.definitions`. **Closed at its literal scope, and NOT by building a generic array-of-object widget.** `Settings.tsx` is deliberately unchanged — its `Field` dispatch still renders a list of objects read-only, which stays true, because `CollectionDefinition` is the only list-of-object field anybody has asked to edit and a generic widget would have had to recurse into `builder`, `params`, `filters` and `schedule`'s four different shapes for one consumer. **What shipped** is a third verb on the panel that already owns the plumbing: `CustomCollectionsPanel` gains **Edit** beside Create and Remove, and a new `DefinitionEditor.tsx` renders a CURATED subset of the twenty-one fields — `title`, `libraries`, `summary`, `sort`, `sync_mode`, `limit`, `labels`, `label_sync`, `item_label`, `sort_title`, `collection_mode`. The subset's rule is loss-freeness: a field is editable only where its stored value has one representation reachable from a plain widget and one unambiguous unset state, and an unset field is written as its key being ABSENT (the overrides document's own revert), never as `null` and never as an explicit copy of the default. **The law the whole thing rests on** is that an edited entry is `{...storedEntry, ...curatedEdits}` spliced into the stored array at its ordinal — so `schedule`, `filters`, `tmdb_summary`, the three `visible_*` flags, `hub_priority`, `changes_webhook`, `builder` and `params` all ride through a save byte-identical, and the entries either side of the edited one are untouched object references. Both halves are pinned (`test_editing_one_definition_does_not_perturb_its_siblings`, `test_an_edited_definition_keeps_every_field_the_edit_did_not_reach`, and the panel's own whole-list write test). **`libraries` is the one field that does not follow the default-equality rule**, deliberately: `None` means "every library in `collections.libraries`", a moving target rather than a fixed value, so scope gets its own explicit "every configured library" control instead of being inferred from all-boxes-checked — otherwise editing a limit would silently re-scope a definition that named its two libraries by hand. The checkbox roster is the union of the config's names and the entry's own, so a library the config no longer lists survives an edit that never touched scope. **`builder` and `params` are shown, not edited** — a registry key typed by hand names builders that do not exist, and a params dict is shaped by the builder that reads it, so the remove-and-recreate route stays the honest one and the panel's note still says so. **`changes_webhook` renders HOST ONLY** (roadmap row 19's field may embed a token), the discipline `notifications.url` gets. **The hard guard moved into the API.** It was UI-only — a disabled Create button — which one `curl` bypasses, and what the bypass costs is not a validation error but every file-defined collection silently ceasing to be built. `api/routes.py::_definitions_guard` now refuses the FIRST stored `collections.definitions` override while the mounted file still lists definitions, from inside `_validated_generation`, so PUT, preview and apply all carry it. The predicate is the transition, not "the file lists definitions": once an override is stored the file's list is already shadowed, and that is the state the editor lives in — refusing there would brick it. Edit needs no UI guard of its own: provenance is uniform in value (`api/collections_builders.py`), so a listing carrying a file row has no override row to edit. **Row 138's own promise about descriptions is kept**: `CollectionDefinition`'s twenty-one `Field(description=...)` strings, served under the `[]` segment since row 217, are the edit form's hover text — no new plumbing, the panel already fetched `/api/config`. **Two residuals, disclosed rather than papered over.** (1) `GET /api/config` still serves every definition's `changes_webhook` IN FULL — `_REDACTORS` is a dotted-path map and `_read_path` walks dicts only, so no list index reaches inside `collections.definitions`. That predates this phase; redacting it server-side would need a per-entry keep marker, and the keep sentinel refuses to resolve inside a list by design (`_sentinel_occurrences` returns `replaceable=False` for any list). The redaction here is display discipline on the surface this phase added, which is what facts C1.3 asked for. (2) **The franchise-cap incident's class is NOT closed by this row** — `max_collections` lives in a preset's `PresetCollection.params` tuple in `collections/packs.py`, not in `collections.definitions` at all, so no editor over definition entries can ever reach it. That is **row 225**. Adjudications C1–C4 in `.superpowers/sdd/p-defedit-facts.md` | M — one form component, one splice helper, one API guard | yes — an operator changes a definition's title, scope, limit or labels without removing and re-creating it | 222 (open — `poster_url` becomes one more field in this form), 225 (filed by this phase) |
```

Then append immediately after row 224 (line 322):

```markdown
| 225 | Preset params are unreachable by any operator surface — the franchise-cap incident's real cure | Filed by the definitions-editor phase (row 138), out of the gap between what row 138 literally promised and what row 223's incident actually needed. Row 138 closed by making `collections.definitions` ENTRIES editable. The franchise cap that froze a live family at ~360 buckets against a pinned `max_collections: 250` was never in a definition: it is a Python literal in a `PresetCollection.params` tuple (`collections/packs.py`), and the fix that shipped was a code commit (`20ac5d9`, 250→500). No config path names it, so no config editor can reach it and no overrides document can override it. **The shape of the cure is a param-override LAYER, not another widget:** preset (and file-defined) definitions stay canonical where they are, and an operator overrides individual params on top. That needs three things this project does not have — (i) a stable per-preset/per-definition key that does not imply ownership of the whole entry (title? a synthetic id? the catalog key?), (ii) a merge rule applied at config-load or engine-build time that lays a small override map over the derived definition instead of replacing the array entry, which is a SECOND merge path beside `config/overrides.merge_overrides`' whole-document one, and (iii) validation that an override key names a real field of that specific builder's params model — `CollectionDefinition._params_must_satisfy_the_builders_own_model` already knows how, against the whole dict rather than one key. It also needs a restart-posture answer (`config/live.FROZEN_SECTIONS`) and a UI that does not pretend a preset is a definition. **Deliberately not folded into row 138:** the two problems only look alike. Both are "a value inside a list is unreachable", but one is a config array an operator already writes and the other is code-literal preset data, and the fixes share no machinery. **Related but distinct from row 223**, which asks whether a refused family should still reconcile its existing members — 223 is about the guard's behavior, this is about reaching the number the guard compares against | M–L — a second merge path, a key scheme, a validation rule and a UI; not a widget | yes — an operator retunes a preset's cap without a code change and a deploy | 223 (open — the same incident, the other half), 138 (closed — the row this was carved out of) |
```

- [ ] **Step 4: Write the PR body (uncommitted)**

Create `.superpowers/sdd/p-defedit-pr-body.md`:

```markdown
## Definitions editor — roadmap row 138

`CustomCollectionsPanel` gains a third verb. Rows supplied by the overrides
document carry an **Edit** button that opens a form over a curated subset of
`CollectionDefinition`'s twenty-one fields: title, library scope, summary,
sort, sync mode, limit, sort title, collection mode, labels, label sync and
member labels.

### The rule the whole thing rests on

An edited entry is `{...storedEntry, ...curatedEdits}`, spliced into the
stored array at its ordinal. Two consequences, both pinned by tests:

- every field the form does not reach — `schedule`, `filters`, `params`,
  `changes_webhook`, the `visible_*` flags and the rest — comes out of a save
  byte-identical;
- the entries either side of the edited one are untouched.

The form is seeded from the STORED overrides array, never from
`GET /api/collections/definitions` — that listing projects seven of twenty-one
fields, and a form built from it would show `summary` empty and then save the
blank over the operator's text.

A field is written only when its value differs from the schema default;
equal-to-default drops the key, which is the overrides document's revert.
`libraries` is the exception and has its own "every configured library"
control, because `None` there means "every library in `collections.libraries`"
— a moving target, not a fixed value.

### Not editable, and shown as such

`builder` (a registry key), `params` (shaped by the builder that reads it) and
`changes_webhook` (rendered **host only** — it may carry a token in its path).
Changing a builder or its params is still remove-and-recreate, and the panel's
note says so.

### The hard guard is now enforced by the API

The refusal to store the first `collections.definitions` override while the
mounted YAML still lists definitions was a disabled button. It is now
`_definitions_guard`, called from `_validated_generation`, so PUT, preview and
apply all carry it. The predicate is the transition, not "the file lists
definitions" — once an override is stored the file's list is already shadowed,
and that is the state the editor works in.

### Scope

No schema change, no new endpoint, no migration, no new dependency.
`Settings.tsx` is untouched: its generic editor still renders a list of objects
read-only, which stays true.

Row 138 closes. Row 225 is filed for the franchise-cap incident's actual cure —
preset params live in a Python literal in `collections/packs.py` and no editor
over definition entries can reach them. `poster_url` (row 222) stays a
follow-up and becomes one more field in this form when it ships.
```

- [ ] **Step 5: Run everything**

```bash
docker compose -p pde3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pde3-full test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-p-defedit-full.log"
docker wait pde3-full
```

**Read `D:\Sites\autoposter\.superpowers\run-p-defedit-full.log` — never trust
the wait code alone** (roadmap row 193). Expected: **0 failed**, total = the
Task 1 baseline **+ 8**. A `tests/test_worker.py` / `tests/test_pipeline.py`
one-off is rows 193/195's known flake: re-run that file alone, then the full
suite once, before treating it as this branch's.

```bash
docker rm pde3-full
docker compose -p pde3 run --name pde3-vitest web npm test > .superpowers/run-p-defedit-t3-vitest.log 2>&1
docker compose -p pde3 run --name pde3-tsc web npx tsc --noEmit > .superpowers/run-p-defedit-t3-tsc.log 2>&1
docker compose -p pde3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pde3-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-p-defedit-t3-ruff.log"
```

**Read all three logs.** Expected: vitest **0 failed** at baseline + 21; `tsc`
silent; ruff `All checks passed!`. Then `docker rm pde3-vitest pde3-tsc pde3-ruff`.

- [ ] **Step 6: Verify the untouched files really are untouched**

```bash
git diff --stat origin/main -- src/autoposter/config/schema.py src/autoposter/config/overrides.py src/autoposter/api/collections_builders.py frontend/src/api/overrides.ts frontend/src/api/types.ts frontend/src/pages/Settings.tsx frontend/package.json
```

Expected: **empty output**. Any line here is a Global Constraints 7/8
violation — revert that file rather than explaining it.

- [ ] **Step 7: Commit the docs and tear down — and stop**

```bash
git add src/autoposter/config/descriptions.py deploy/README.md docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(collections): the definitions editor documented; row 138 closed, row 225 filed"
docker compose -p pde3 down
git status
```

`git status` must show a clean tree apart from `.superpowers/` (gitignored —
the PR body stays uncommitted). **Do not push. Do not open a PR.** The user
gates both; hand them `.superpowers/sdd/p-defedit-pr-body.md` when they ask.

### Operator acceptance

Open Collections → Custom collections on a deployment whose definitions are
overrides. An **Edit** button sits beside Remove on every row. Click it: the
form opens seeded with that definition's stored values, including fields the
table never showed. Change the limit, Save — the panel re-reads, the row is
unchanged apart from what was edited, and the sibling rows are exactly as they
were. Open a definition carrying a change webhook: its host is shown, its path
is not. On a deployment whose definitions come from the mounted file, no row
carries an Edit button, and a hand-rolled `curl` PUT of a definitions override
comes back 422.

### Testing

- `pytest tests/test_api_config_editor.py` — the guard's five cases (refused
  first store, preview refused identically, edit-after-migration allowed,
  empty file list allowed, unrelated save untouched) and the three
  wholesale-replace characterization tests.
- `npm test` — `DefinitionEditor.test.tsx` (round-trip, unreached-field
  preservation, default-equality, key-dropping revert, both scope states, the
  retired-library union, the empty-scope refusal, the host-only webhook, the
  read-only builder/params, the hover text, the comma-bearing label) and the
  panel's six new tests (no Edit on file rows, seeded from stored, whole-list
  write with siblings intact, 422 rendering, close-and-re-read, the retired
  copy).
- Full backend suite, full frontend suite, `tsc --noEmit`, `ruff check` — all
  green (logs under `.superpowers/`).

---

## Self-review (performed while writing)

- **Spec coverage.** Facts **C1.1** → T2's curated subset (the eleven fields,
  the floor `sync_mode`/`labels`/`sort`/`libraries` all present; `params`
  read-only with the remove-and-recreate note kept honest, per the "else"
  branch of C1.1). **C1.2** → the guard inherited structurally in T2 (uniform
  provenance, pinned) and enforced in T1 (`_definitions_guard`). **C1.3** →
  `hostOnly` in T2, tested, with the server-side residual disclosed in T3's
  close. **C1.4** → row 225 filed in T3, nothing built. **C1.5** → `poster_url`
  absent from every task; named as a follow-up in the close and the PR body.
  **C1.6** → Global Constraint 7 plus T3 Step 6's diff check. **C2** → the
  round-trip (T1 Step 8's third test, T2's first two editor tests), the
  negative assertion (T1 Step 8's first test, T2's whole-list write test), the
  guard API-side (T1 Steps 2-5), the redaction (T2's host-only test), RED-first
  everywhere except the labelled characterization block, both suites measured
  from T1 Step 1's baseline. **C3** → T3 Steps 1-3. **C4** → three tasks in the
  stated order, `feat/definitions-editor` cut from post-merge `origin/main` by
  content probe, `pde1`-`pde3`, `p-defedit-` artifacts.
- **Placeholder scan.** Every code step carries complete code; every run step
  carries the exact command and its expected outcome. Two steps deliberately
  say "read the current wording before replacing" (T3 Step 1's docstring
  sentence, T2 Step 10's `--warning` custom property) — those are instructions
  to verify against the tree, not TBDs, and each names exactly what to check
  and what to do with either answer.
- **Type consistency.** `DefinitionEditor`'s six props are declared once in T2
  Step 3 and consumed with those exact names in T2 Step 8h.
  `draftFrom(entry, libraries)` / `entryFromDraft(entry, draft, roster)` /
  `libraryRoster(entry, libraries)` / `hostOnly(value)` are spelled identically
  in the component, its tests and the Interfaces block.
  `documentForEdit(stored, ordinal, entry)` matches its call site.
  `DEFINITIONS_GUARD_REFUSAL` and `_definitions_guard(request, base, document)`
  match between T1 Step 4's definition, its call site and T1's Interfaces
  block. `DESCRIPTION_PREFIX` (`"collections.definitions[]."`) matches
  `config/descriptions.py::_walk`'s `f"{path}[]."`.
- **Known accepted risk.** `GET /api/config` serving `changes_webhook` in full
  is a pre-existing exposure this phase narrows on its own surface but does not
  close; it is stated in row 138's close, in A5, and to the controller, and no
  row is filed for it because the facts authorize row 225 only.
