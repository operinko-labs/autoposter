# Settings Clarity — every setting says what it does — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close roadmap rows 217 and 112 — every field the Settings page renders gains an operator-facing description, condensed out of the schema comment that already documents it, served on `GET /config` as a dotted-path map and rendered as a hover tooltip on the row's label; and the same endpoint stops lying about two things (a computed `version` it offers as editable, and a live path it flags "restart to apply").

**Architecture:** Four tasks. Task 1 builds the *wire* and the *safety net* before any of the grind: a `config/descriptions.py` walk that turns `Field(description=...)` into the dotted-path map the API speaks, one new top-level key on `GET /config` beside `frozen_paths`, and a MECHANICAL completeness guard — every field of every config model must carry a non-empty description, every path the endpoint serves must have an entry — so a field added in a year's time without a description goes RED rather than shipping silent. Task 1 then fills the first batch (the 11 classes that already call `Field()`, 108 fields), with the guard's pending-list covering the rest. Task 2 fills the second batch (the 12 bare classes, 83 fields) and deletes the pending list, at which point the guard covers the whole schema unconditionally. Task 3 is the UI: `ConfigRow` hangs the description off the row label as a native `title=` hover — the page's own idiom, three existing uses, no new component — plus row 112's two additive keys and the two small render rules that make them mean something. Task 4 wraps: suites, row closes, PR body, no push.

**Tech Stack:** Python 3.14 + FastAPI + Pydantic v2 + pytest (compose `test` service), React + vitest via the compose `web` service, ruff.

**Binding requirements:** `.superpowers/sdd/p-settings-facts.md` (C1–C4, adjudicated 2026-08-31). Where this plan and that file could ever disagree, that file wins. Evidence base: `.superpowers/sdd/p-settings-recon.md`; every line number below was re-verified against `9b64239` (`feat/notifications-1`'s tip) at plan time.

## Global Constraints

Every task's requirements implicitly include this section.

1. **Scope (facts C1).** Rows IN: **217** (every field the settings page renders gains a description; the text is condensed from the existing comments and docstrings, never invented) and **112** (both halves: serve the computed paths, serve the live exceptions). Row **138 is OUT** and stays open, untouched — no list-of-object editor is built here, and no file this phase edits may grow one.
2. **Descriptions are SOURCED, NOT INVENTED (facts C2 — the law).** The text of every description comes from the comment or docstring already sitting on that field or its model, condensed to one or two operator-facing sentences. Where a field's comment carries no operator-relevant content, the description is a short *factual* statement of what the field is, derived from the code that reads it — never padded, never a guess about behaviour. If a field's purpose cannot be established from the comment, the docstring, its validators and its type, STOP and report it rather than writing a plausible sentence. A wrong description is worse than no description: it is a lie the operator has no way to check.
3. **A description says WHAT, never WHEN (facts C1.4).** No description mentions restarting, "takes effect", "applies at the next", or any other timing claim. That fact has an owner already — `config/live.FROZEN_SECTIONS`, served as `frozen_paths` and rendered as the restart pill's own tooltip. Two spellings of one fact is one spelling too many, and the second one drifts. Mechanically pinned by `test_a_description_never_says_when_a_setting_takes_effect` (Task 1).
4. **Comment retention (facts C1.6).** A schema comment is **deleted** only when the description now says the same thing in full — otherwise it is a drift twin. A comment is **kept, unchanged**, when it carries anything the description does not: provenance (roadmap row numbers, dates, decisions), tradeoffs, rejected alternatives, cross-file citations, or reasoning aimed at a maintainer rather than an operator. When in doubt, keep it. Never rewrite a comment you are keeping; this phase's diff on a kept comment is zero lines.
5. **`Field()` must not change a field's contract.** Adding `description=` may not change a default, a validator bound or whether a field is required. A bare required annotation becomes `x: T = Field(description=...)` — with **no** `default=`, which keeps it required; a field with a default becomes `Field(default=<the same default>, description=...)` or keeps `default_factory=`; every existing `ge=`/`gt=` stays exactly as it was. A changed default is a silent production change, and the suite is the only thing that would catch it.
6. **RED before GREEN, at the guard.** The completeness guard is seen failing for a descriptionless field before it is trusted — Task 1 Step 16 blanks one description transiently, runs the guard, reads the RED naming that exact path, and restores it. A guard nobody has seen fail is a guard nobody knows works.
7. **Baselines are MEASURED, not carried (facts C2).** The roadmap ledger's "4215 backend" is pre-merge and unmeasured — this plan does **not** carry it as fact. Task 1 Step 2 measures the backend and frontend totals at the cut commit, calls them **M** and **F**, and records them in the task report. Every later total in this plan is stated as a delta on M/F and is stated *before* it is measured.
8. **Container discipline (the standing recipe).** Unique compose project per task (`pst1`, `pst2`, `pst3`, `pst4`), always the `.superpowers/isolated-db.yml` overlay for the `test` service, always `sh -c` with `tee` to a path under `/app/.superpowers/` **inside** the container — never rely on streamed stdout (it is filtered). **No `--rm` anywhere**: use `run --name <project>-<label>`, read the log from the host, then `docker rm <project>-<label>`. The full suite runs detached (`run -d --name`), is waited on with `docker wait`, and its log is read from the host. Teardown is `docker compose -p <project> down` — **never** `down -v`. Two pytest commands against the same compose project are unsafe — keep the project names unique per task.
9. **Frontend tests** via the compose `web` service with a host-side redirect: `docker compose -p pstN run --name pstN-web web npm test > .superpowers/run-p-settings-<label>.log 2>&1`, then read the log from the host and `docker rm pstN-web`.
10. **Timeouts (row 131's derivation):** full backend suite `timeout -s KILL 2700`; targeted files `timeout -s KILL 600`.
11. **Commits.** Conventional messages, `--no-gpg-sign`, staged **by name** (never `git add -A`). No `Co-Authored-By` and no AI attribution of any kind, in commits or the PR body.
12. **Branch (facts C4).** `feat/settings-clarity`, cut from `feat/notifications-1`'s tip — **SECOND in a stack** if PR #113 is still pending at cut time; if #113 has merged, cut from `origin/main` instead. Task 1 Step 1 checks which, and probes the cut by **content, never by sha**.
13. **Artifacts.** Phase artifacts under `.superpowers/sdd/` use the `p-settings-` prefix; run logs are `.superpowers/run-p-settings-*.log`. The PR body goes to `.superpowers/sdd/p-settings-pr-body.md` — gitignored, never committed. **Push and PR creation are NOT in this plan**: the user gates the PR.
14. **Ruff.** `line-length = 100` (`pyproject.toml:88-89`). Long descriptions are written as parenthesised implicit-concatenation strings, one sentence fragment per line, as every example below does.
15. **Sequencing.** The tasks share one tree and run **sequentially**. Task 2 depends on Task 1's guard and pending list; Task 3 depends on Task 1's wire; Task 4 depends on all three.

## Adjudications Made by This Plan

Decisions the facts file left open, settled here so no executor guesses. Each is disclosed again in the row closes (Task 4).

- **The recon's model table is off by one class, and the counts below are the re-measured ones.** `TitleCardConfig` (`config/schema.py:148-154`) subclasses `ArtKindConfig`, not `BaseModel`, so the recon's "22 models" (a count of direct `BaseModel` subclasses) does not list it; it is a real 23rd class with 4 fields of its own. The field total is **191 annotated fields across 23 classes** (measured by AST walk at plan time), not the recon's regex estimate of 194. The per-class counts in each batch task's table are authoritative.
- **EVERY annotated field gets a description, including the ones that hold a submodel.** `Config.plex`, `ArtworkConfig.poster`, `ArtKindConfig.text` and their kind render as a panel heading or — when an optional submodel is unset — as a single leaf row. Describing them is what lets the completeness guard be a plain "every field, no exceptions" rule instead of a rule with a carve-out, and carve-outs are where the next descriptionless field hides. Their text comes from the referenced model's class docstring.
- **`Secrets` is walked even though it is no field of `Config`.** The endpoint *injects* a redacted `secrets` block (`api/routes.py:1289`), so the served body has `secrets.*` leaves that the model walk would never reach. The map mirrors what is served. A secret's description says what the credential is for and which environment variable sets it — never anything about its value.
- **List-of-model item fields are served under a `[]` segment** — `collections.definitions[].title`. Nothing renders them today (the settings page shows a list of objects read-only; that is row 138), and they are authored and served anyway because they are the entire payoff of row 138 landing later: descriptions are per schema field, not per widget. The `[]` is a marker, not a path the API accepts, and the endpoint docstring says so.
- **The completeness guard is two tests, not one.** A *structural* one (every path in the map resolves to a real schema field; every leaf the endpoint serves has an entry in the map) which is true from Task 1 onward, and a *content* one (every field's description is non-empty) which carries a `PENDING_MODELS` list of the 12 classes Task 2 fills. Task 2 deletes that list. Splitting them is what lets Task 1 end green without weakening what ships: the structural half already refuses a field the map cannot address, and the content half refuses a blank the moment its class leaves the list.
- **`field_descriptions` joins `PROVENANCE_KEYS` in Task 1, not Task 3.** The frontend renders every top-level object key as a settings panel, so shipping the backend key without that one-line frontend change would put a panel titled "Field descriptions" on the page for the length of two tasks. The tooltip work still belongs to Task 3; only the "this key is not configuration" line moves earlier.
- **Row 112(a) is closed by serving a computed-paths list, not by rejecting the override server-side.** The row offers both; the facts call both of its fixes "small additive dict keys". Serving is additive and reversible, and the page can then render `version` read-only — which is what actually removes the inert edit the row complains about. The save endpoint is untouched: an override on `version` remains accepted-and-inert exactly as today, and nothing about that behaviour is claimed to change.
- **Row 112(b) is closed on both sides.** `live_paths` is served, and the frontend's `frozenReason` gains the same "a live exception wins over every frozen prefix" rule the backend's `frozen_reason` already has (`config/live.py:120-121`). Serving it without honouring it would leave the client still marking `plex.resolve_max_attempts` "restart to apply", which is the exact complaint.
- **`.config-key[title] { cursor: help; }`** — one CSS line, no new markup, no new component. The recon notes that a `title=` attribute has no discovery affordance; the cursor is the platform's own answer to that and costs nothing. This is the only visual change in the phase.
- **Descriptions are threaded to `ConfigRow` as their own prop, not on the `Editor` object.** The secrets panel is rendered with `editor={null}` deliberately (`Settings.tsx:432`), and a secret's description must still show. A prop with a `{}` default keeps the secrets panel described and leaves the "no editor" meaning of `null` alone.
- **Payload size is accepted.** The map is **277 keys** (measured at plan time by running the walk in this plan against the schema at `9b64239`) — perhaps 30KB of text added to a `GET /config` response an operator loads once per visit to one page. No caching, no lazy second endpoint, no gzip work: YAGNI until someone measures a problem.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `docs/superpowers/plans/2026-08-31-settings-clarity.md` | **Commit as-is** (this plan) | T1 |
| `src/autoposter/config/descriptions.py` | **Create.** The schema walk; `FIELD_DESCRIPTIONS` | T1 |
| `tests/test_config_descriptions.py` | **Create.** The completeness guard (structural + content + the WHAT-not-WHEN rule) | T1, T2 |
| `src/autoposter/api/routes.py` | **Modify** (`:1146` for `COMPUTED_PATHS`; `get_config`, `:1252-1305`). The three new keys | T1, T3 |
| `tests/test_api_config_editor.py` | **Modify** (after `:224`). Endpoint coverage of the new keys | T1, T3 |
| `src/autoposter/config/schema.py` | **Modify.** Batch 1: the 11 classes that already call `Field()` (108 fields) | T1 |
| `src/autoposter/config/schema.py` | **Modify.** Batch 2: the 12 bare classes (83 fields) | T2 |
| `frontend/src/pages/Settings.tsx` | **Modify** (`:89-94` `PROVENANCE_KEYS`) | T1 |
| `frontend/src/pages/Settings.tsx` | **Modify** (`:96-107`, `:109-122`, `:290-350`, `:356-386`, `:396-438`, `:529-561`). Tooltip, computed, live | T3 |
| `frontend/src/pages/settings.css` | **Modify** (`:23`). The `cursor: help` line | T3 |
| `frontend/src/pages/Settings.test.tsx` | **Modify** (`:205-214` fixture, `:443-457` provenance test; 3 new tests) | T1, T3 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modify.** Rows 217 and 112 close; row 138's note | T4 |
| `.superpowers/sdd/p-settings-pr-body.md` | **Create** (gitignored, NEVER committed) | T4 |

---

### Task 1: The wire, the completeness guard, and batch 1 (the 11 classes that already call `Field()`)

**Files:**
- Commit: `docs/superpowers/plans/2026-08-31-settings-clarity.md`
- Create: `src/autoposter/config/descriptions.py`
- Create: `tests/test_config_descriptions.py`
- Modify: `src/autoposter/api/routes.py` (import block near `:42-52`; `get_config`'s docstring `:1270-1281` and body `:1302`)
- Modify: `tests/test_api_config_editor.py` (after `test_get_config_still_redacts`, `:221-224`)
- Modify: `src/autoposter/config/schema.py` (batch 1, the classes listed in Step 9's table)
- Modify: `frontend/src/pages/Settings.tsx:89-94`
- Modify: `frontend/src/pages/Settings.test.tsx:205-214`, `:443-457`

**Interfaces:**
- Consumes: `Config` and `Secrets` (`config/schema.py:1232`, `:21`); `FROZEN_SECTIONS` and the `dict(FROZEN_SECTIONS)` precedent (`config/live.py:30`, `api/routes.py:1302`); pydantic v2's `BaseModel.model_fields` (name → `FieldInfo`, with `.description` and `.annotation`).
- Produces, and every later task depends on these exact names:
  - `autoposter.config.descriptions.build_field_descriptions() -> dict[str, str]` — the walk, recomputed on call.
  - `autoposter.config.descriptions.FIELD_DESCRIPTIONS: dict[str, str]` — the module-level result, what the endpoint serves.
  - The `GET /config` response key **`field_descriptions`**: `dict[str, str]`, dotted path → description, siblings `frozen_paths`/`redacted_paths`.
  - `tests/test_config_descriptions.py::PENDING_MODELS: frozenset[str]` — class names Task 2 fills and then deletes.
  - Frontend: `field_descriptions` present in `PROVENANCE_KEYS` (`Settings.tsx:89-94`).
- Task 4 counts **+5** backend tests from this task and **+0** frontend tests.

- [ ] **Step 1: Cut the branch and commit this plan**

First establish which base applies (Global Constraint 12):

```bash
gh pr view 113 --json state,title 2>&1 | head -5
```

If that prints `"state": "MERGED"`, the base is `origin/main`; anything else (including `gh` failing to answer, which this environment has done before) means #113 is still pending and the base is `feat/notifications-1`. Then:

```bash
git fetch origin
git checkout feat/notifications-1   # or: git checkout -B settings-base origin/main
grep -q "changes_webhook" src/autoposter/config/schema.py || { echo "WRONG BASE: notifications-1's content is not here"; exit 1; }
grep -q "def get_config" src/autoposter/api/routes.py || { echo "WRONG BASE: no get_config"; exit 1; }
git checkout -b feat/settings-clarity
git add docs/superpowers/plans/2026-08-31-settings-clarity.md
git commit --no-gpg-sign -m "docs(plans): the settings-clarity plan"
```

Expected: both probes print nothing and the branch is created. If either fails, STOP — the base is wrong and every line number in this plan is unverified against it. Record in the task report which base was used.

- [ ] **Step 2: MEASURE the baseline — do not carry the ledger's number**

The roadmap ledger says 4215 backend; that figure is pre-merge and unmeasured (facts C2). Measure it here and use the measured number everywhere afterwards.

```bash
docker compose -p pst1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pst1-base test sh -c "timeout -s KILL 2700 pytest -q 2>&1 | tee /app/.superpowers/run-p-settings-baseline.log; echo EXIT=\$?"
docker wait pst1-base
```

Read `.superpowers/run-p-settings-baseline.log` from the host, then `docker rm pst1-base`.

```bash
docker compose -p pst1 run --name pst1-web-base web npm test > .superpowers/run-p-settings-web-baseline.log 2>&1
```

Read that log, then `docker rm pst1-web-base`.

Expected: a passing run in both. Record the two totals in the task report as **M** (backend) and **F** (frontend), with the sentence "the ledger's 4215 was unmeasured; the measured baseline at this cut is M". Every later expectation in this plan is written as `M + n` / `F + n`.

- [ ] **Step 3: Write the failing guard — `tests/test_config_descriptions.py`**

Create the file with exactly this content:

```python
"""Every config field says what it does, and the map can address every one.

Roadmap row 217. The settings page has no place to put documentation that
lives in a ``#`` comment, so the operator-facing half of each comment moves
onto the field as ``Field(description=...)`` and is served as a dotted-path
map. These are the guards that keep that true for fields nobody has written
yet: a new setting added in a year with no description fails here, at the
cheapest possible moment, instead of shipping as a silent row on a page whose
whole point is that nothing on it is silent.

Three rules, three tests:

1. every path in the map addresses a real field, and every leaf the endpoint
   serves has an entry in the map (the endpoint half lives in
   ``test_api_config_editor.py``, where the client fixture is);
2. every field's description is non-empty;
3. no description says WHEN a setting takes effect -- ``frozen_paths`` owns
   that fact and the editor already renders it on the restart pill, so a
   second copy here would be a copy that drifts.
"""
from pydantic import BaseModel

from autoposter.config.descriptions import (
    FIELD_DESCRIPTIONS,
    build_field_descriptions,
)
from autoposter.config.schema import Config, Secrets

# Classes whose descriptions the second batch of this phase writes. Rule 2
# skips them and nothing else does -- rule 1 already holds every field in
# them to being addressable. DELETED by that batch, along with the skip: a
# permanent exemption list is how a field stays undescribed forever.
PENDING_MODELS: frozenset[str] = frozenset({
    "Secrets",
    "TextStyle",
    "OperationsConfig",
    "BadgesConfig",
    "CleanupConfig",
    "PruneConfig",
    "ArtworkModesConfig",
    "SchedulerConfig",
    "RadarrConfig",
    "SonarrConfig",
    "TracearrConfig",
    "ArrSyncConfig",
})


def _described_models() -> list[tuple[str, type[BaseModel], str]]:
    """Every model the served config reaches, with the path prefix it sits at.

    ``Secrets`` is not a field of ``Config`` -- the endpoint injects a redacted
    block for it -- so it is named here for the same reason the walk names it:
    the map mirrors what is served, not what the model holds.
    """
    found: dict[str, tuple[str, type[BaseModel], str]] = {}

    def visit(model: type[BaseModel], prefix: str) -> None:
        if model.__name__ in found:
            return
        found[model.__name__] = (model.__name__, model, prefix)
        for name, field in model.model_fields.items():
            for nested, segment in _nested_models(field.annotation):
                visit(nested, f"{prefix}{name}{segment}.")

    visit(Config, "")
    visit(Secrets, "secrets.")
    return list(found.values())


def _nested_models(annotation):
    """The models an annotation reaches, each with its path segment."""
    from autoposter.config.descriptions import _item_model_of, _model_of

    direct = _model_of(annotation)
    if direct is not None:
        return [(direct, "")]
    item = _item_model_of(annotation)
    return [] if item is None else [(item, "[]")]


def test_every_path_in_the_map_addresses_a_real_schema_field():
    """A path the schema cannot resolve is a description no row can ever show."""
    unresolved = []
    for path in FIELD_DESCRIPTIONS:
        model: type[BaseModel] | None = Config
        for segment in path.split("."):
            name = segment[:-2] if segment.endswith("[]") else segment
            if path.startswith("secrets.") and model is Config:
                model = Secrets
                if name == "secrets":
                    continue
            if model is None or name not in model.model_fields:
                unresolved.append(path)
                break
            field = model.model_fields[name]
            nested = _nested_models(field.annotation)
            model = nested[0][0] if nested else None
    assert unresolved == [], f"paths that address no field: {unresolved}"


def test_every_config_field_carries_a_description():
    """The completeness guard: a field with no description is a silent row."""
    missing = []
    for class_name, model, prefix in _described_models():
        if class_name in PENDING_MODELS:
            continue
        for name, field in model.model_fields.items():
            if not (field.description or "").strip():
                missing.append(f"{class_name}.{name} (serves {prefix}{name})")
    assert missing == [], (
        "every setting the page renders has to say what it does; these do not: "
        f"{missing}"
    )


def test_a_description_never_says_when_a_setting_takes_effect():
    """WHAT, not WHEN. ``frozen_paths`` owns the restart requirement and the
    editor already renders it as the restart pill's own tooltip; a second copy
    inside a description is a copy that drifts out of date silently."""
    timing = ("restart", "takes effect", "at the next pass", "until the process")
    offenders = [
        (path, word)
        for path, description in build_field_descriptions().items()
        for word in timing
        if word in description.lower()
    ]
    assert offenders == [], f"descriptions that describe timing: {offenders}"
```

- [ ] **Step 4: Run the guard — see it RED for the missing module**

```bash
docker compose -p pst1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst1-red1 test sh -c "timeout -s KILL 600 pytest tests/test_config_descriptions.py -q 2>&1 | tee /app/.superpowers/run-p-settings-t1-red1.log; echo EXIT=\$?"
```

Read `.superpowers/run-p-settings-t1-red1.log` from the host, then `docker rm pst1-red1`.

Expected: a **collection error** — `ModuleNotFoundError: No module named 'autoposter.config.descriptions'`. Nothing is described yet and the module the map comes from does not exist.

- [ ] **Step 5: Write the walk — `src/autoposter/config/descriptions.py`**

Create the file with exactly this content:

```python
"""Every config field's operator-facing description, as dotted paths.

The schema documents its fields in ``#`` comments and class docstrings, which
no served surface can read: the settings page renders a label and a widget and
has nowhere to put the sentence the comment carries (roadmap row 217). So the
operator-facing half of each comment moves onto the field itself, as
``Field(description=...)``, and this module is the single walk that turns those
into the dotted-path map ``GET /config`` serves beside ``frozen_paths``.

Three rules the walk encodes:

* A description says WHAT a setting does, never WHEN it takes effect. The
  restart requirement is ``config/live.FROZEN_SECTIONS``' job, is already
  served as ``frozen_paths`` and is already rendered on the restart pill. One
  fact, one owner.
* ``Secrets`` is walked even though it is no field of ``Config``: the endpoint
  injects a redacted ``secrets`` block into the response, so the map has to
  mirror what is *served* rather than what the model holds.
* A field whose annotation is a model gets both an entry of its own -- an
  unset optional submodel renders as one leaf row, and a set one renders as a
  section heading -- and a recursion beneath it. A ``list`` of models gets an
  entry plus a recursion under a ``[]`` segment: those paths address no row
  today, because the editor renders a list of objects read-only (roadmap row
  138), and they are served so that they do the day that row lands. ``[]`` is
  a marker in this map, never a path the API accepts.
"""
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel

from autoposter.config.schema import Config, Secrets


def _model_of(annotation) -> type[BaseModel] | None:
    """The model an annotation holds, unwrapping ``X | None``."""
    if (
        get_origin(annotation) is None
        and isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
    ):
        return annotation
    if get_origin(annotation) in (Union, UnionType):
        for arg in get_args(annotation):
            found = _model_of(arg)
            if found is not None:
                return found
    return None


def _item_model_of(annotation) -> type[BaseModel] | None:
    """The model a ``list[Model]`` annotation holds, unwrapping ``X | None``."""
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return _model_of(args[0]) if args else None
    if origin in (Union, UnionType):
        for arg in get_args(annotation):
            found = _item_model_of(arg)
            if found is not None:
                return found
    return None


def _walk(model: type[BaseModel], prefix: str = "") -> dict[str, str]:
    described: dict[str, str] = {}
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        described[path] = field.description or ""
        nested = _model_of(field.annotation)
        if nested is not None:
            described.update(_walk(nested, f"{path}."))
            continue
        item = _item_model_of(field.annotation)
        if item is not None:
            described.update(_walk(item, f"{path}[]."))
    return described


def build_field_descriptions() -> dict[str, str]:
    """The whole map, freshly walked. ``FIELD_DESCRIPTIONS`` is the cached one."""
    described = _walk(Config)
    described.update(_walk(Secrets, "secrets."))
    return described


# Computed once: the schema is a set of classes, not a runtime value, so this
# cannot change between requests -- the same reasoning that makes
# ``FROZEN_SECTIONS`` a module-level dict.
FIELD_DESCRIPTIONS: dict[str, str] = build_field_descriptions()
```

- [ ] **Step 6: Run the guard — see the completeness half RED for all 191 fields**

```bash
docker compose -p pst1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst1-red2 test sh -c "timeout -s KILL 600 pytest tests/test_config_descriptions.py -q 2>&1 | tee /app/.superpowers/run-p-settings-t1-red2.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst1-red2`.

Expected: **1 failed, 2 passed**. `test_every_config_field_carries_a_description` fails, listing every field of the 11 non-pending classes (108 of them, `ArtKindConfig.enabled` first). The other two pass: the map addresses real fields already, and an empty description says nothing about timing. That is the guard proving it can see a missing description.

- [ ] **Step 7: Write the endpoint's failing tests**

In `tests/test_api_config_editor.py`, directly below `test_get_config_still_redacts` (`:221-224`), add:

```python
def _leaf_paths(body: dict, prefix: str = "") -> list[str]:
    """Every dotted path the settings page renders as a row.

    A dict recurses; anything else -- scalar, list, null -- is a leaf, which is
    exactly the rule ``Settings.tsx``'s ``ConfigNode`` applies to decide
    between a subsection and a row.
    """
    leaves: list[str] = []
    for key, value in body.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            leaves.extend(_leaf_paths(value, f"{path}."))
        else:
            leaves.append(path)
    return leaves


PROVENANCE_KEYS = {
    "overridden_paths",
    "frozen_paths",
    "redacted_paths",
    "keep_sentinel",
    "field_descriptions",
}


async def test_get_config_describes_every_path_it_serves(client, auth_headers):
    """The completeness guard's endpoint half: a row the page renders with no
    description is a setting whose only documentation is the YAML file the
    operator does not have open."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    descriptions = body["field_descriptions"]
    served = _leaf_paths({
        key: value for key, value in body.items() if key not in PROVENANCE_KEYS
    })
    assert served, "the config response serves no settings at all"
    undescribed = [path for path in served if path not in descriptions]
    assert undescribed == [], f"served with no description: {undescribed}"


async def test_get_config_describes_a_sample_of_settings_in_words(client, auth_headers):
    """Presence is checked wholesale above; this is the spot-check that the
    entries are sentences rather than placeholders."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    descriptions = body["field_descriptions"]
    for path in ("workers", "plex.url", "collections.max_deletes"):
        assert descriptions[path].strip(), f"{path} is described by nothing"
        assert len(descriptions[path]) > 20, f"{path}'s description is a stub"
```

- [ ] **Step 8: Run the endpoint tests — see them RED**

```bash
docker compose -p pst1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst1-red3 test sh -c "timeout -s KILL 600 pytest tests/test_api_config_editor.py -q -k describes 2>&1 | tee /app/.superpowers/run-p-settings-t1-red3.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst1-red3`.

Expected: **2 failed**, both with `KeyError: 'field_descriptions'` — the endpoint serves no such key yet.

- [ ] **Step 9: Serve the map from `get_config`**

(a) In `src/autoposter/api/routes.py`, add to the import block (it is alphabetical by module; this goes directly above the `from autoposter.config.impact import ...` line at `:42`):

```python
from autoposter.config.descriptions import FIELD_DESCRIPTIONS
```

(b) In `get_config`'s docstring, replace the sentence at `:1270` that begins `Carries four things the editor needs beyond the values themselves.` with:

```
    Carries five things the editor needs beyond the values themselves.
    ``overridden_paths`` is the provenance: which of these values come from
    the database overrides rather than the mounted YAML, so the UI can mark
    them and offer "revert to base". ``frozen_paths`` maps each restart-only
    path to the reason a live swap cannot reach it (see config/live.py) --
    sent as data so the editor can flag a field without duplicating this
    project's startup wiring in TypeScript. ``redacted_paths`` and
    ``keep_sentinel`` are the other half of the same honesty: they name the
    values this response is *not* telling the truth about, and give the
    editor the one token it can send back for them without either destroying
    the stored value or dropping it (see ``KEEP_SENTINEL``).
    ``field_descriptions`` maps each setting's dotted path to what that
    setting does, condensed from the schema's own comments (roadmap row 217)
    -- what the page renders as the row's hover text. It deliberately says
    nothing about *when* a change applies: that is ``frozen_paths``' fact and
    the editor renders it separately. A few of its keys carry a ``[]``
    segment (``collections.definitions[].title``): those describe the fields
    of the objects inside a list, which this editor cannot edit yet (roadmap
    row 138). ``[]`` is a marker in that map only -- no endpoint here accepts
    a path containing it.
```

(c) Directly below `body["frozen_paths"] = dict(FROZEN_SECTIONS)` (`:1302`), add:

```python
    body["field_descriptions"] = dict(FIELD_DESCRIPTIONS)
```

- [ ] **Step 10: Run both test files — the endpoint tests go GREEN, the completeness one stays RED**

```bash
docker compose -p pst1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst1-wire test sh -c "timeout -s KILL 600 pytest tests/test_config_descriptions.py tests/test_api_config_editor.py -q 2>&1 | tee /app/.superpowers/run-p-settings-t1-wire.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst1-wire`.

Expected: exactly **1 failed** — `test_every_config_field_carries_a_description`, still listing the 108 fields batch 1 is about to fill. Both new endpoint tests pass: the map addresses every served leaf even while its values are empty strings, which is the structural half doing its job. If any *other* test in `test_api_config_editor.py` fails, STOP: the new key has disturbed something and that must be understood before any description is written.

- [ ] **Step 11: Teach the frontend that `field_descriptions` is not a settings section**

(a) In `frontend/src/pages/Settings.tsx`, replace `PROVENANCE_KEYS` (`:89-94`) with:

```tsx
const PROVENANCE_KEYS = [
  "overridden_paths",
  "frozen_paths",
  "redacted_paths",
  "keep_sentinel",
  "field_descriptions",
];
```

(b) In `frontend/src/pages/Settings.test.tsx`, add `field_descriptions` to the `EDITOR_CONFIG` fixture (`:205-214`), directly after the `frozen_paths` line:

```tsx
  field_descriptions: {
    workers: "How many render workers run in parallel.",
    "plex.url": "The base URL of the Plex server this service manages.",
  },
```

(c) In the same file, in `it("does not render the provenance keys as configuration", ...)` (`:443-457`), add after the `expect(screen.queryByText("Overridden paths")).toBeNull();` line:

```tsx
    expect(screen.queryByRole("heading", { name: "Field descriptions" })).toBeNull();
```

- [ ] **Step 12: Run the frontend suite**

```bash
docker compose -p pst1 run --name pst1-web web npm test > .superpowers/run-p-settings-t1-web.log 2>&1
```

Read the log, then `docker rm pst1-web`.

Expected: **F passed** — the same total measured in Step 2. No frontend test is added here; the fixture and one assertion changed.

- [ ] **Step 13: Commit the wire and the guard**

```bash
git add src/autoposter/config/descriptions.py tests/test_config_descriptions.py src/autoposter/api/routes.py tests/test_api_config_editor.py frontend/src/pages/Settings.tsx frontend/src/pages/Settings.test.tsx
git commit --no-gpg-sign -m "feat(config): serve a description map, and guard that every field has one"
```

- [ ] **Step 14: Write batch 1's descriptions — the 11 classes that already call `Field()`**

This is the grind. Work class by class in this order and commit after each group of roughly 30 fields (Step 16 has the commands). The counts are the AST-measured field totals per class; every one of them gets a description.

| Class | Lines | Fields | `Field()` today |
| --- | --- | --- | --- |
| `ArtKindConfig` | 122-145 | 10 | 1 |
| `TitleCardConfig` | 148-154 | 4 (its own; it inherits `ArtKindConfig`'s 10) | 1 |
| `ArtworkConfig` | 157-165 | 8 | 1 |
| `ProvidersConfig` | 168-174 | 4 | 1 |
| `PlexConfig` | 177-183 | 6 | 1 |
| `ScheduleGate` | 252-275 | 2 | 1 |
| `CollectionDefinition` | 296-659 | 21 | 6 |
| `CollectionsConfig` | 662-964 | 18 | 6 |
| `AdoptConfig` | 1040-1053 | 2 | 1 |
| `NotificationsConfig` | 1207-1229 | 5 | 2 |
| `Config` | 1232-1288 | 28 | 13 |
| **Total** | | **108** | |

**One thing about the guard's output that will look wrong and is not.** `TitleCardConfig` subclasses `ArtKindConfig`, and pydantic's `model_fields` includes inherited fields, so the guard reports 118 missing *slots* for these 108 distinct fields — `TitleCardConfig.enabled` appears beside `ArtKindConfig.enabled`. Describing the base class's field describes the subclass's too (verified: an inherited `FieldInfo` carries the parent's `description`), so the pair clears together and nothing extra is authored for the ten inherited names.

**Where the text comes from, per field, in this order:**

1. the `#` comment directly above the field (most fields have one);
2. failing that, the class docstring, for what this field contributes to it;
3. failing that, for a field whose annotation is a model (`Config.plex`, `ArtworkConfig.poster`, `ArtKindConfig.text`, `CollectionDefinition.schedule`): the *referenced model's* class docstring;
4. failing that, the field's own validators and type, stated factually.

**The condensation rule.** One or two sentences, present tense, addressed to an operator reading a settings page — what the setting does, and what its notable values mean (what `0`, `""`, `None` or an empty list do, when the comment says). A validator bound may be stated because it is in the code (`ge=1`, `gt=0`). Never a timing claim (Global Constraint 3). Never a fact the comment, docstring, validators and type do not carry (Global Constraint 2). Never padding: a genuinely simple field gets a genuinely short sentence.

**The comment-retention rule (Global Constraint 4).** Delete the comment only when the description now says the whole of it; keep it, unchanged, when it carries provenance, tradeoffs, rejected alternatives or cross-file citations.

**Four fully worked examples. Follow this shape exactly.**

*(a) A comment that is entirely operator-facing — condensed, and the comment DELETED as a drift twin.* `ProvidersConfig.cache_ttl_seconds`, `:172-174`. Replace those three lines and the field with:

```python
    cache_ttl_seconds: int = Field(
        default=24 * 3600,
        description=(
            'How long a provider\'s answer -- including "nothing found" -- stays in '
            "the provider cache. 0 disables provider caching entirely."
        ),
    )
```

*(b) A comment carrying reasoning beyond the operator's sentence — condensed, and the comment KEPT, unchanged.* `CollectionsConfig.max_deletes`, `:759-763`. The comment explains the `cleanup.max_orphans` precedent and why `ge=0` is meaningful — a maintainer's fact, not an operator's. Leave lines 759-762 exactly as they are and replace only the field:

```python
    max_deletes: int = Field(
        default=5,
        ge=0,
        description=(
            "The most collections one delete sweep may remove. Past this the sweep "
            "refuses entirely and reports the numbers instead; 0 means the sweep is "
            "opted in but deletes nothing."
        ),
    )
```

*(c) A long comment (19 lines) whose operator-facing content is three facts.* `CollectionDefinition.changes_webhook`, `:378-395`. The comment carries roadmap provenance, the frozen-sections decision and the family fan-out warning — KEPT, unchanged. Replace only the field line:

```python
    changes_webhook: str = Field(
        default="",
        description=(
            "A webhook this collection's membership changes are POSTed to, beside "
            "whatever the global notifications block does. Sent only while "
            "notifications.enabled is on and a global notifications.url is "
            "configured; a Plex-evaluated smart collection never fires it."
        ),
    )
```

*(d) A field whose annotation is a model, and the required-field trap.* `Config.plex`, `:1248`. `PlexConfig` has no docstring, so the description is a factual statement of what the section holds, read off its own fields (`url`, the excluded libraries, the resolve and refresh cadences). Note there is **no** `default=`: adding one would silently make a required section optional (Global Constraint 5).

```python
    plex: PlexConfig = Field(
        description=(
            "How this service reaches the Plex server: its address, which libraries "
            "are left alone, and how often it re-checks the server and its token."
        ),
    )
```

- [ ] **Step 15: Run the guard after each class — watch the missing list shrink**

After each class, run:

```bash
docker compose -p pst1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst1-batch test sh -c "timeout -s KILL 600 pytest tests/test_config_descriptions.py tests/test_collection_config.py -q 2>&1 | tee /app/.superpowers/run-p-settings-t1-batch.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst1-batch` (the container name is reused, so it must be removed each time).

Expected while batch 1 is in progress: `test_every_config_field_carries_a_description` still fails, with a *shorter* list each time. `tests/test_collection_config.py` runs alongside it as the early-warning for Global Constraint 5: it exercises the schema's defaults and validators, so a `Field()` rewrite that changed a default or dropped a bound fails there immediately rather than in Task 4's full run. When the last class in the table is done, all three description tests pass.

- [ ] **Step 16: SEE THE GUARD GO RED — the transient omission (Global Constraint 6)**

With batch 1 complete and green, blank exactly one description to prove the guard is real. In `ProvidersConfig.cache_ttl_seconds`, temporarily change `description=(` ... `)` to `description=""`, then:

```bash
docker compose -p pst1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst1-proof test sh -c "timeout -s KILL 600 pytest tests/test_config_descriptions.py -q 2>&1 | tee /app/.superpowers/run-p-settings-t1-proof.log; echo EXIT=\$?"
```

Read `.superpowers/run-p-settings-t1-proof.log`, then `docker rm pst1-proof`.

Expected: **1 failed**, the message naming `ProvidersConfig.cache_ttl_seconds (serves providers.cache_ttl_seconds)` and nothing else. Quote that line in the task report — it is the evidence that the completeness guard fails for a descriptionless field.

Then **restore the description exactly** and re-run the same command (with a fresh container name `pst1-proof2`, removed afterwards). Expected: **3 passed**. `git diff` must show no trace of the blanking.

- [ ] **Step 17: Ruff, then commit batch 1**

```bash
docker compose -p pst1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst1-ruff test sh -c "ruff check . 2>&1 | tee /app/.superpowers/run-p-settings-t1-ruff.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst1-ruff`. Expected: `All checks passed!`, `EXIT=0`.

```bash
git add src/autoposter/config/schema.py
git commit --no-gpg-sign -m "docs(config): describe the fields that already carry a Field() wrapper"
docker compose -p pst1 down
```

**Task 1 report must state:** the base branch used and which #113 case applied; **M** and **F** as measured, with the note that the ledger's 4215 was unmeasured; the RED evidence line from Step 16; and the running backend total, expected **M + 5**.

---

### Task 2: Batch 2 — the 12 classes with no `Field()` wrapper at all

**Files:**
- Modify: `src/autoposter/config/schema.py` (the classes in Step 1's table)
- Modify: `tests/test_config_descriptions.py` (delete `PENDING_MODELS` and its two uses)

**Interfaces:**
- Consumes: `Field` (already imported at `config/schema.py:7`); `PENDING_MODELS` and `test_every_config_field_carries_a_description` from Task 1's `tests/test_config_descriptions.py`; the walk's rule that every field of every reachable model gets an entry.
- Produces: no new names. After this task `PENDING_MODELS` **does not exist** — the completeness guard covers the whole schema unconditionally, which is the state Task 3 and Task 4 assume. Task 4 counts **+0** backend tests from this task (the guard is the same three tests, now unrestricted).

- [ ] **Step 1: The batch, class by class**

Every field of these 12 classes gets a description. The rules are **exactly** those in Task 1 Step 14 — source order (comment, then class docstring, then referenced model's docstring, then validators and type), the condensation rule (one or two operator-facing sentences, notable values spelled out, no timing claim, nothing invented), the comment-retention rule (delete only a comment the description now says in full; otherwise keep it unchanged), and the contract rule (a `Field()` rewrite changes no default, no bound and no required-ness). They are repeated here in full rather than cross-referenced because the implementer of this task may not have read Task 1.

| Class | Lines | Fields |
| --- | --- | --- |
| `Secrets` | 21-90 | 13 |
| `TextStyle` | 93-119 | 14 |
| `OperationsConfig` | 186-220 | 6 |
| `BadgesConfig` | 223-249 | 5 |
| `CleanupConfig` | 986-1003 | 3 |
| `PruneConfig` | 1006-1037 | 3 |
| `ArtworkModesConfig` | 1056-1088 | 8 |
| `SchedulerConfig` | 1091-1123 | 9 |
| `RadarrConfig` | 1126-1148 | 8 |
| `SonarrConfig` | 1151-1167 | 9 |
| `TracearrConfig` | 1170-1187 | 2 |
| `ArrSyncConfig` | 1190-1204 | 3 |
| **Total** | | **83** |

Every field in these classes is a bare `name: type = default` annotation today, so each one gains a whole `Field(...)` wrapper. Two shapes, and getting them wrong is the one way this task can break production:

```python
    # a field with a default:
    write_to_plex: bool = Field(default=True, description="...")
    # a REQUIRED field (no default today) -- no `default=`, or it stops being required:
    plex_token: str = Field(description="...")
```

**Three fully worked examples. Follow this shape exactly.**

*(a) A comment that is entirely operator-facing — condensed, and the comment DELETED as a drift twin.* `OperationsConfig.write_to_plex`, `:190-192`. Replace those two comment lines and the field with:

```python
    write_to_plex: bool = Field(
        default=True,
        description=(
            "Write the gathered metadata to Plex. Off gathers and stores the facts "
            "but leaves Plex untouched -- the safe setting while another tool still "
            "owns these fields."
        ),
    )
```

*(b) An 11-line comment whose operator-facing content is two sentences — the comment KEPT, unchanged.* `BadgesConfig.adopt_from_plex`, `:239-249`. The comment carries the EXIF mechanism, the "artwork not the database is the source of truth" argument and the cutover arithmetic — maintainer's facts. Leave `:239-248` exactly as they are and replace only the field line:

```python
    adopt_from_plex: bool = Field(
        default=True,
        description=(
            "Before uploading a render this service has no fingerprint for, read the "
            "provenance out of the artwork Plex is already serving and skip the "
            "upload when it is already that exact image. Best-effort: any failure "
            "falls through to a normal upload."
        ),
    )
```

*(c) A safety valve whose comment is one operator sentence and one cross-reference — condensed, comment DELETED (the cross-reference is to a sibling field the description names anyway).* `SchedulerConfig.drift_batch_size`, `:1111-1114`. Replace the three comment lines and the field with:

```python
    drift_batch_size: int = Field(
        default=500,
        description=(
            "The most stale items one ratings-drift sweep enqueues, so a large "
            "library is worked through gradually rather than all at once."
        ),
    )
```

**`Secrets` needs its own note.** Its six hard secrets (`database_url` through `webhook_secret`, `:24-29`) carry no comments at all; their facts are in the class docstring ("Runtime secrets. Never read from the YAML config file.") and in `_SECRET_ENV` (`:11-18`), which names the environment variable each one comes from. That is enough for a factual description and nothing more may be added. The page renders every one of these as `***REDACTED***`, so the description is the only thing on the row that says anything — and it must say nothing about the value:

```python
    plex_token: str = Field(
        description=(
            "The Plex server token every request to Plex authenticates with. Set "
            "from AUTOPOSTER_PLEX_TOKEN; never read from the config file."
        ),
    )
```

The seven soft secrets (`:30-73`) each have a long comment explaining why an empty value must still boot — condense the operator half ("a deployment without one still runs, with X unavailable"), and KEEP the comments: they carry the per-secret reasoning and, for `plex_account_token`, the reason it is deliberately not `plex_token`.

- [ ] **Step 2: Delete the pending list**

In `tests/test_config_descriptions.py`:

(a) delete the whole `PENDING_MODELS` block (the comment and the `frozenset`);

(b) in `test_every_config_field_carries_a_description`, delete these two lines from the loop:

```python
        if class_name in PENDING_MODELS:
            continue
```

(c) in the module docstring, replace the sentence `2. every field's description is non-empty;` with:

```
2. every field's description is non-empty -- every model, no exemptions (the
   list of classes awaiting their batch is gone, and a new one must never be
   added: a permanent exemption list is how a field stays undescribed
   forever);
```

- [ ] **Step 3: Run the guard — the whole schema, no exemptions**

```bash
docker compose -p pst2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst2-guard test sh -c "timeout -s KILL 600 pytest tests/test_config_descriptions.py tests/test_collection_config.py tests/test_api_config_editor.py -q 2>&1 | tee /app/.superpowers/run-p-settings-t2-guard.log; echo EXIT=\$?"
```

Read `.superpowers/run-p-settings-t2-guard.log` from the host, then `docker rm pst2-guard`.

Expected: all pass. If `test_every_config_field_carries_a_description` still fails, the message names exactly which fields were missed — finish those and re-run (with a fresh container name). If `test_collection_config.py` or `test_api_config_editor.py` fails, a `Field()` rewrite changed a default, a bound or a required-ness: fix that before anything else.

- [ ] **Step 4: Ruff**

```bash
docker compose -p pst2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst2-ruff test sh -c "ruff check . 2>&1 | tee /app/.superpowers/run-p-settings-t2-ruff.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst2-ruff`. Expected: `All checks passed!`, `EXIT=0`.

- [ ] **Step 5: Commit and tear down**

```bash
git add src/autoposter/config/schema.py tests/test_config_descriptions.py
git commit --no-gpg-sign -m "docs(config): describe the remaining schema fields; the guard now covers all of them"
docker compose -p pst2 down
```

**Task 2 report must state:** the running backend total, expected **M + 5** (this task adds no tests), and the confirmation that `PENDING_MODELS` no longer exists anywhere in the tree (`grep -rn PENDING_MODELS tests src` returns nothing).

---

### Task 3: The tooltip, and row 112's two keys

**Files:**
- Modify: `src/autoposter/api/routes.py` (`:1146` area for `COMPUTED_PATHS`; the import at `:43`; `get_config`'s docstring and body)
- Modify: `tests/test_api_config_editor.py` (after the two tests Task 1 added)
- Modify: `frontend/src/pages/Settings.tsx` (`:89-94`, `:96-107`, `:109-122`, `:290-350`, `:356-386`, `:396-438`, `:529-561`)
- Modify: `frontend/src/pages/settings.css:23`
- Modify: `frontend/src/pages/Settings.test.tsx` (fixture `:205-214`; 3 new tests)

**Interfaces:**
- Consumes: `FIELD_DESCRIPTIONS` and the `field_descriptions` response key (Task 1); `LIVE_EXCEPTIONS: frozenset[str]` (`config/live.py:94`, currently `{"plex.resolve_max_attempts"}`); `REDACTED_PATHS`/`_REDACTORS` as the precedent for a module-level path tuple (`api/routes.py:1145-1146`); `Editor` (`Settings.tsx:111-122`), `frozenReason` (`:99-107`), `ConfigRow` (`:290`), `ConfigNode` (`:356`), `ConfigSections` (`:396`), `rowOf` (`Settings.test.tsx:258`).
- Produces:
  - `api.routes.COMPUTED_PATHS: tuple[str, ...]` = `("version",)`.
  - Two more `GET /config` keys: **`computed_paths`** (`list[str]`) and **`live_paths`** (`list[str]`).
  - Frontend: `Editor` gains `computed: string[]` and `live: string[]`; `frozenReason(frozen, live, path)` takes the live list as its second argument; `ConfigNode` and `ConfigRow` take a `descriptions: Record<string, string>` prop.
- Task 4 counts **+2** backend tests and **+3** frontend tests from this task.

- [ ] **Step 1: Write the failing backend tests**

In `tests/test_api_config_editor.py`, below `test_get_config_describes_a_sample_of_settings_in_words` (added in Task 1), add — and extend the `PROVENANCE_KEYS` set that Task 1 added in the same file to include the two new keys:

```python
async def test_get_config_names_the_paths_it_computes(client, auth_headers):
    """``version`` is derived from the settings, not set by the operator, so an
    override on it is inert. Served as data (roadmap row 112) rather than left
    for the editor to hard-code, which is how the two sides drift."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["computed_paths"] == ["version"]


async def test_get_config_names_the_live_exceptions(client, auth_headers):
    """A path inside a frozen section that is nonetheless read per use. Without
    it the editor marks ``plex.resolve_max_attempts`` "restart to apply" while
    the save response correctly omits it -- the two halves of one endpoint
    disagreeing about the same path (roadmap row 112)."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["live_paths"] == ["plex.resolve_max_attempts"]
    # It is inside a frozen prefix: that is the whole reason it has to be said.
    assert "plex" in body["frozen_paths"]
```

Update the `PROVENANCE_KEYS` set in that file to:

```python
PROVENANCE_KEYS = {
    "overridden_paths",
    "frozen_paths",
    "redacted_paths",
    "keep_sentinel",
    "field_descriptions",
    "computed_paths",
    "live_paths",
}
```

- [ ] **Step 2: Run them — see the REDs**

```bash
docker compose -p pst3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst3-red test sh -c "timeout -s KILL 600 pytest tests/test_api_config_editor.py -q 2>&1 | tee /app/.superpowers/run-p-settings-t3-red.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst3-red`.

Expected: **2 failed**, both `KeyError` — `'computed_paths'` and `'live_paths'`. Everything else in the file passes.

- [ ] **Step 3: Serve the two keys**

(a) In `src/autoposter/api/routes.py`, directly below `REDACTED_PATHS: tuple[str, ...] = tuple(_REDACTORS)` (`:1146`), add:

```python
# Paths the service computes rather than the operator setting. ``version`` is
# the render-settings hash (config/loader.py's render_version), stored on each
# Render row so a settings change is detectable as staleness -- writing one by
# hand overrides it with a value the next load recomputes away. Served so the
# editor can render it read-only instead of offering an edit that does nothing
# (roadmap row 112). Not a refusal: an override on it is still accepted and
# still inert, exactly as before.
COMPUTED_PATHS: tuple[str, ...] = ("version",)
```

(b) Extend the `config.live` import at `:43` to bring in the exceptions:

```python
from autoposter.config.live import (
    FROZEN_SECTIONS,
    LIVE_EXCEPTIONS,
    frozen_reason,
    is_inert,
)
```

(the existing line imports `FROZEN_SECTIONS, frozen_reason, is_inert` plus whatever else it already names — keep every existing name and add `LIVE_EXCEPTIONS`.)

(c) In `get_config`, directly below the `body["field_descriptions"]` line Task 1 added, add:

```python
    body["computed_paths"] = list(COMPUTED_PATHS)
    body["live_paths"] = sorted(LIVE_EXCEPTIONS)
```

(d) In `get_config`'s docstring, change `Carries five things the editor needs` to `Carries seven things the editor needs`, and append this paragraph after the `field_descriptions` paragraph Task 1 added:

```
    ``computed_paths`` and ``live_paths`` are the last two, and both exist so
    the editor never contradicts this service about a path it already knows
    the answer for. The first names the values this process derives rather
    than reads (``version``), which the editor renders read-only instead of
    offering an inert edit. The second names the paths a broader frozen
    prefix would otherwise swallow but which are genuinely read per use
    (``config/live.LIVE_EXCEPTIONS``) -- without it the editor flags
    ``plex.resolve_max_attempts`` as needing a restart while ``frozen_reason``
    here correctly says it does not.
```

- [ ] **Step 4: Run the backend tests — GREEN**

```bash
docker compose -p pst3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst3-green test sh -c "timeout -s KILL 600 pytest tests/test_api_config_editor.py tests/test_config_descriptions.py -q 2>&1 | tee /app/.superpowers/run-p-settings-t3-green.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst3-green`. Expected: all pass, including the two new ones.

- [ ] **Step 5: Write the three failing frontend tests**

In `frontend/src/pages/Settings.test.tsx`, first extend the `EDITOR_CONFIG` fixture (`:205-214`) so it carries the new keys — replace the `field_descriptions` block Task 1 added, and add two more keys, so the fixture's tail reads:

```tsx
  overridden_paths: [] as string[],
  frozen_paths: {
    workers: "the worker pool is sized once, at startup",
    plex: "the Plex client is built once at startup",
  },
  field_descriptions: {
    workers: "How many render workers run in parallel.",
    version: "The hash of every setting that changes what a render produces.",
    "plex.url": "The base URL of the Plex server this service manages.",
    "plex.resolve_max_attempts": "How many times a failed Plex lookup is retried.",
  },
  computed_paths: ["version"],
  live_paths: ["plex.resolve_max_attempts"],
};
```

and add `resolve_max_attempts: 10` to the fixture's `plex` object so there is a row at that path:

```tsx
  plex: {
    url: "http://plex:32400",
    excluded_libraries: ["Muskarit", "Photos"],
    resolve_max_attempts: 10,
  },
```

Then add these three tests inside the `describe("Settings editor", ...)` block, after `it("marks an edited frozen field as needing a restart, with the server's reason", ...)` (which ends at `:404`):

```tsx
  it("hangs each setting's description off its label as hover text", async () => {
    stubApi();
    await renderSettings();

    const label = within(rowOf(screen.getByLabelText("workers"))).getByText(
      "Workers",
    );
    expect(label).toHaveAttribute(
      "title",
      "How many render workers run in parallel.",
    );
    // A path the server described nothing for gets no empty tooltip: an empty
    // `title` is a hover that opens onto nothing.
    expect(
      within(rowOf(screen.getByLabelText("badges.enabled"))).getByText("Enabled"),
    ).not.toHaveAttribute("title");
  });

  it("renders a computed path read-only rather than offering an inert edit", async () => {
    stubApi();
    await renderSettings();

    // `version` is derived from the other settings; an override on it is
    // recomputed away, so the editor must not present it as a field.
    expect(screen.queryByLabelText("version")).toBeNull();
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("does not demand a restart for a live path inside a frozen section", async () => {
    stubApi();
    await renderSettings();

    // `plex` is frozen as a whole, but this one path is read per use -- the
    // server says so in `live_paths`, and the save response already omits it.
    fireEvent.change(screen.getByLabelText("plex.resolve_max_attempts"), {
      target: { value: "12" },
    });
    expect(
      within(rowOf(screen.getByLabelText("plex.resolve_max_attempts")))
        .queryByText("restart to apply"),
    ).toBeNull();
    // A sibling under the same frozen prefix still says it.
    fireEvent.change(screen.getByLabelText("plex.url"), {
      target: { value: "http://plex:32401" },
    });
    expect(
      within(rowOf(screen.getByLabelText("plex.url"))).getByText("restart to apply"),
    ).toBeInTheDocument();
  });
```

- [ ] **Step 6: Run the frontend suite — see the three REDs**

```bash
docker compose -p pst3 run --name pst3-web-red web npm test > .superpowers/run-p-settings-t3-web-red.log 2>&1
```

Read the log, then `docker rm pst3-web-red`.

Expected: **3 failed** — the label carries no `title`; `version` still renders as an editable input; and the live path still shows "restart to apply". Note that the existing test at `:443` (`does not render the provenance keys as configuration`) must still pass: `computed_paths` and `live_paths` are arrays, so they fold into the "General" panel rather than becoming sections — which is exactly why Step 7(a) adds them to `PROVENANCE_KEYS`.

- [ ] **Step 7: Implement the frontend**

(a) `PROVENANCE_KEYS` (`:89-94`) gains the two new keys:

```tsx
const PROVENANCE_KEYS = [
  "overridden_paths",
  "frozen_paths",
  "redacted_paths",
  "keep_sentinel",
  "field_descriptions",
  "computed_paths",
  "live_paths",
];
```

(b) `frozenReason` (`:96-107`) learns the live exceptions, mirroring `config/live.py:114-125`:

```tsx
/** The reason a restart is needed for `path`, or undefined if it is live.
 * `frozen_paths` keys are prefixes: `notifications` freezes everything under
 * it. `live` wins over all of them -- a path the server reads per use is live
 * however broad the prefix above it is, which is the same precedence
 * `config/live.py`'s `frozen_reason` applies server-side. */
function frozenReason(
  frozen: Record<string, string>,
  live: string[],
  path: string,
): string | undefined {
  if (live.some((prefix) => path === prefix || path.startsWith(`${prefix}.`))) {
    return undefined;
  }
  for (const [prefix, reason] of Object.entries(frozen)) {
    if (path === prefix || path.startsWith(`${prefix}.`)) return reason;
  }
  return undefined;
}
```

(c) The `Editor` interface (`:109-122`) gains two fields — insert them directly after `frozen`:

```tsx
  /** Paths this service derives rather than the operator setting, and paths a
   * frozen prefix covers but which are read per use. Both come from the
   * server: the editor must not carry a second copy of either judgement. */
  computed: string[];
  live: string[];
```

(d) `ConfigRow` (`:290-350`) takes the description, hangs it off the label, and renders a computed path read-only. Replace the whole function with:

```tsx
function ConfigRow({
  name,
  path,
  value,
  editor,
  description,
}: {
  name: string;
  path: string;
  value: unknown;
  editor: Editor | null;
  description?: string;
}) {
  // A computed path is the server's to set: an override on it is recomputed
  // away, so offering an input would be offering an edit that does nothing.
  // Handled by dropping the editor for this row alone, which is the same
  // mechanism the secrets panel already uses.
  const rowEditor =
    editor !== null && editor.computed.includes(path) ? null : editor;
  const edited = rowEditor !== null && hasPath(rowEditor.document, path);
  const pending =
    edited && rowEditor !== null ? readPath(rowEditor.document, path) : value;
  const isRedacted = rowEditor !== null && rowEditor.redacted.includes(path);
  // The sentinel is a state, not a value: it says "the stored setting stays as
  // it is", so the field shows the server's redacted rendering of that setting
  // rather than the marker. Typing replaces the marker with what was typed,
  // and from then on the typed value is what shows.
  const current = isRedacted && pending === rowEditor.sentinel ? value : pending;
  const restart =
    edited && rowEditor !== null
      ? frozenReason(rowEditor.frozen, rowEditor.live, path)
      : undefined;
  const error = rowEditor?.errors[path];

  return (
    <div className="config-row">
      {/* The description is the row's whole documentation, and it hangs off
          the label as a native hover -- the page's existing idiom for
          secondary text (the redaction note, the restart reason, the impact
          caveat) rather than a new component. An absent or empty description
          passes `undefined`, because an empty `title` is a hover that opens
          onto nothing. */}
      <span className="config-key" title={description || undefined}>
        {labelFor(name)}
      </span>
      <span className="config-value">
        <Field
          path={path}
          base={value}
          current={current}
          editor={rowEditor}
          title={isRedacted ? REDACTED_EDIT_NOTE : undefined}
        />
        {rowEditor !== null && rowEditor.overridden.includes(path) && (
          <>
            <span className="config-pill overridden">overridden</span>
            <button
              type="button"
              className="link-button"
              aria-label={`Clear override for ${path}`}
              onClick={() => rowEditor.clear(path)}
            >
              Clear
            </button>
          </>
        )}
        {restart !== undefined && (
          <span className="config-pill restart" title={restart}>
            restart to apply
          </span>
        )}
        {error !== undefined && (
          <span className="config-field-error" role="alert">
            {error}
          </span>
        )}
      </span>
    </div>
  );
}
```

(e) `ConfigNode` (`:356-386`) threads the map down. Replace the whole function with:

```tsx
/** Recursive renderer driven entirely by the response's shape: scalars and
 * lists become label/value rows, nested objects become indented subsections.
 * Nothing here names a config field, so a new key appears without a frontend
 * change. `path` accumulates the dotted path the API speaks in, and
 * `descriptions` is keyed on exactly that path. */
function ConfigNode({
  value,
  path = "",
  editor = null,
  descriptions = {},
}: {
  value: Record<string, unknown>;
  path?: string;
  editor?: Editor | null;
  descriptions?: Record<string, string>;
}) {
  return (
    <div className="config-node">
      {Object.entries(value).map(([key, entry]) => {
        const childPath = path === "" ? key : `${path}.${key}`;
        return isPlainObject(entry) ? (
          <div className="config-subsection" key={key}>
            <h3>{labelFor(key)}</h3>
            <ConfigNode
              value={entry}
              path={childPath}
              editor={editor}
              descriptions={descriptions}
            />
          </div>
        ) : (
          <ConfigRow
            key={key}
            name={key}
            path={childPath}
            value={entry}
            editor={editor}
            description={descriptions[childPath]}
          />
        );
      })}
    </div>
  );
}
```

(f) `ConfigSections` (`:396-438`) reads the map off the response and passes it to both calls. Change its props and its two `ConfigNode` usages:

```tsx
function ConfigSections({
  config,
  editor,
}: {
  config: ConfigResponse;
  editor: Editor;
}) {
  // Descriptions reach every row, including the secrets panel's -- those
  // render `***REDACTED***` and nothing else, so the description is the only
  // thing on the row that says anything. That is why this is a prop of its own
  // rather than a field of `Editor`, which the secrets panel deliberately does
  // not get.
  const descriptions = isPlainObject(config.field_descriptions)
    ? Object.fromEntries(
        Object.entries(config.field_descriptions).map(([key, text]) => [
          key,
          String(text),
        ]),
      )
    : {};
  const entries = Object.entries(config).filter(
    ([key]) => !PROVENANCE_KEYS.includes(key),
  );
  const general = entries.filter(([, value]) => !isPlainObject(value));
  const sections = entries
    .filter((entry): entry is [string, Record<string, unknown>] =>
      isPlainObject(entry[1]),
    )
    // Array.prototype.sort is stable, so everything but `secrets` keeps the
    // server's order.
    .sort(([a], [b]) => Number(a === "secrets") - Number(b === "secrets"));

  return (
    <>
      {general.length > 0 && (
        <section className="panel config-section">
          <h2>General</h2>
          <ConfigNode
            value={Object.fromEntries(general)}
            editor={editor}
            descriptions={descriptions}
          />
        </section>
      )}
      {sections.map(([key, value]) => (
        <section className="panel config-section" key={key}>
          <h2>{labelFor(key)}</h2>
          {/* Secrets are environment-only and the API refuses a document
              carrying one at any depth, so the panel gets no editor at all
              rather than disabled inputs. */}
          <ConfigNode
            value={value}
            path={key}
            editor={key === "secrets" ? null : editor}
            descriptions={descriptions}
          />
        </section>
      ))}
    </>
  );
}
```

(g) The `editor` object in `Settings` (`:530-561`) gains the two lists — insert directly after the `frozen: ...` block and before `errors,`:

```tsx
    computed: Array.isArray(config?.computed_paths)
      ? config.computed_paths.filter(
          (path): path is string => typeof path === "string",
        )
      : [],
    live: Array.isArray(config?.live_paths)
      ? config.live_paths.filter(
          (path): path is string => typeof path === "string",
        )
      : [],
```

(h) In `frontend/src/pages/settings.css`, directly below the `.config-key {` block that starts at `:23`, add:

```css
/* A described row is hoverable, and nothing else on the page says so. The
   platform's own affordance for "there is text behind this", at the cost of
   one line and no markup. */
.config-key[title] {
  cursor: help;
}
```

- [ ] **Step 8: Run the frontend suite — GREEN**

```bash
docker compose -p pst3 run --name pst3-web web npm test > .superpowers/run-p-settings-t3-web.log 2>&1
```

Read the log, then `docker rm pst3-web`.

Expected: **F + 3 passed** — the three new tests and every pre-existing one. If the restart-pill test at `:382` fails, `frozenReason`'s new signature was not updated at one of its call sites: `grep -n "frozenReason(" frontend/src/pages/Settings.tsx` must show exactly two lines, the definition and the one call inside `ConfigRow`.

- [ ] **Step 9: Commit and tear down**

```bash
git add src/autoposter/api/routes.py tests/test_api_config_editor.py frontend/src/pages/Settings.tsx frontend/src/pages/Settings.test.tsx frontend/src/pages/settings.css
git commit --no-gpg-sign -m "feat(settings): describe every row on hover, and stop lying about computed and live paths"
docker compose -p pst3 down
```

**Task 3 report must state:** the running totals, expected **M + 7** backend and **F + 3** frontend.

---

### Task 4: Wrap — suites, row closes, PR body

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (rows 217, 112, 138 — **locate each by its row number**, not by line)
- Create: `.superpowers/sdd/p-settings-pr-body.md` (gitignored, never committed)

**Interfaces:**
- Consumes: everything the three previous tasks produced; **M** and **F** as measured in Task 1 Step 2.
- Produces: no code. Rows 217 and 112 close; row 138 stays open with one added sentence.

- [ ] **Step 1: State the expected totals, then run the full backend suite**

State in the report, before running: **M + 5 (T1) + 0 (T2) + 2 (T3) = M + 7 backend**, and **F + 3 frontend**, with M and F quoted from Task 1's measurement.

```bash
docker compose -p pst4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pst4-full test sh -c "timeout -s KILL 2700 pytest -q 2>&1 | tee /app/.superpowers/run-p-settings-full.log; echo EXIT=\$?"
docker wait pst4-full
```

Read `.superpowers/run-p-settings-full.log` from the host, then `docker rm pst4-full`.

Expected: `M + 7 passed` (plus whatever skips the baseline already has), `EXIT=0`. If the number differs, report the measured number and account for the difference before continuing — a mismatch means a test was added or lost that this plan did not intend. Note for the reader of a failure: this phase rewrites 191 field declarations, so a failure anywhere in the suite is most likely a changed default or a dropped validator bound (Global Constraint 5), not a test-count problem.

- [ ] **Step 2: Ruff**

```bash
docker compose -p pst4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pst4-ruff test sh -c "ruff check . 2>&1 | tee /app/.superpowers/run-p-settings-ruff.log; echo EXIT=\$?"
```

Read the log, then `docker rm pst4-ruff`. Expected: `All checks passed!`, `EXIT=0`.

- [ ] **Step 3: The frontend suite**

```bash
docker compose -p pst4 run --name pst4-web web npm test > .superpowers/run-p-settings-web.log 2>&1
```

Read the log, then `docker rm pst4-web`. Expected: **F + 3 passed**.

- [ ] **Step 4: Close row 217**

In the roadmap, find the row beginning `| 217 | The Settings page has no per-setting descriptions` and append to its requirement cell, before the closing ` | M — 34 field descriptions is the bulk;`:

```
. **answered — every setting the page renders now describes itself.** All three steps shipped together. (1) The schema: **191 fields across 23 model classes** carry `Field(description=...)`, condensed from the `#` comments and class docstrings that already documented them — the row's "34" was the count of pre-existing `Field(` wrappers, not of fields, and twelve of the classes had no wrapper at all, so most of these fields gained one. Descriptions are sourced, never invented: where a comment carried maintainer reasoning beyond the operator's sentence (provenance, tradeoffs, rejected alternatives, cross-file citations) the comment stays, unchanged, beside the description; where it said only what the description now says, it went, because a drift twin is worse than either half alone. (2) The endpoint: `GET /config` serves `field_descriptions`, a dotted-path map beside `frozen_paths` (`config/descriptions.py` is the one walk that builds it, and it walks `Secrets` too because the endpoint injects a redacted block the model does not hold). (3) The render: `ConfigRow` hangs the description off the row's label as a native `title=` hover — the page's own idiom for secondary text, used three times already (the redaction note, the restart reason, the impact caveat), so no new component and no new visual pattern; `.config-key[title]` gets `cursor: help` as the affordance a bare `title` otherwise lacks. **Two deliberate scoping decisions.** A description says WHAT a setting does and never WHEN it takes effect: the restart requirement is `config/live.FROZEN_SECTIONS`' fact, already served as `frozen_paths` and already rendered on the restart pill, and a second copy inside 191 strings is a second copy that drifts — pinned by `test_a_description_never_says_when_a_setting_takes_effect`. And the fields of `CollectionDefinition` are described and served under a `[]` segment (`collections.definitions[].title`) although no row renders them yet: that is row 138's gap, and the descriptions are ready the day it lands, because a description belongs to a schema field rather than to a widget. **What keeps this closed:** `tests/test_config_descriptions.py` is a MECHANICAL completeness guard — every field of every reachable model must carry a non-empty description, and every path in the map must address a real field — so a setting added in a year with no description fails a test rather than shipping as a silent row. Its endpoint half (`test_get_config_describes_every_path_it_serves`) holds the same line for every leaf the response actually serves
```

- [ ] **Step 5: Close row 112**

Find the row beginning `| 112 | Config-contract cleanup: computed paths + live exceptions` and append to its requirement cell, before the closing ` | S |`:

```
. **answered — both halves, in settings-clarity.** (a) `GET /config` serves `computed_paths` (`("version",)`, `api/routes.py`'s `COMPUTED_PATHS`), and the editor renders a computed path read-only by dropping the editor for that row alone — the same mechanism the secrets panel already uses. The row offered two fixes and this is the serve-a-list one: the save endpoint is deliberately unchanged, so an override on `version` is still accepted and still recomputed away, exactly as before; what changed is that the page no longer *offers* the edit. (b) `GET /config` serves `live_paths` (`sorted(config.live.LIVE_EXCEPTIONS)`), and the frontend's `frozenReason` now applies the same precedence the backend's does — a live exception wins over every frozen prefix above it — so `plex.resolve_max_attempts` no longer shows "restart to apply" for a change that takes effect immediately, and the GET and the save response finally agree about it. Both are pinned by tests on each side
```

- [ ] **Step 6: Note row 138 (stays open)**

Find the row beginning `| 138 | Settings editor cannot edit list-of-object config` and append to its requirement cell, before the closing ` | M — generic editor work, not collections-specific |`:

```
. **Still open, deliberately untouched by settings-clarity (row 217), which was scoped around it rather than into it** — descriptions attach to `ConfigRow`'s label, `Field`'s array-of-object branch is a different code path, and bundling a new generic widget into a description-authoring phase would have been two phases in one. One thing is now waiting for it: `CollectionDefinition`'s 21 fields are already described and already served, under a `[]` path segment (`collections.definitions[].title`), so whoever builds the editor gets the per-field hover text for free the moment there is a row to hang it on
```

- [ ] **Step 7: Commit the roadmap closes**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): rows 217 and 112 close; 138 notes what now waits on it"
```

- [ ] **Step 8: Write the PR body (gitignored)**

Write `.superpowers/sdd/p-settings-pr-body.md`. Plain prose, no AI attribution, no `Co-Authored-By`. It must contain:

- **What this is:** rows 217 and 112 of the full-parity roadmap. Every setting the page renders now says what it does, on hover; the config endpoint stops offering an inert edit and stops demanding a restart it does not need.
- **The three moving parts:** the schema (191 fields across 23 classes, descriptions condensed from the comments already there), `config/descriptions.py` plus the `field_descriptions` key on `GET /config`, and `ConfigRow`'s `title=` hover.
- **The decisions a reviewer should check**, one line each: descriptions are sourced from the existing comments and never invented; a comment stays when it carries more than its description does and goes when it does not; a description says WHAT and never WHEN, because `frozen_paths` already owns WHEN; `CollectionDefinition`'s fields are described and served under a `[]` segment although row 138 has not built the editor that renders them; row 112(a) is closed by serving a computed-paths list rather than by refusing the override server-side, so no save behaviour changed.
- **The guard**, stated as the reason this row stays closed: `tests/test_config_descriptions.py` fails for any field added later without a description, and the endpoint half fails for any served leaf with no entry.
- **What is NOT here:** row 138 (the list-of-object editor) — still open, and the reason 217 was scoped around it.
- **Stack position:** whichever base Task 1 Step 1 established — either `feat/settings-clarity` cut from `feat/notifications-1` (SECOND behind PR #113, which merges first, after which this branch is rebased), or cut from `origin/main` if #113 had already merged. State which, plainly.
- **Verification:** the measured backend and frontend totals against the baseline measured at the cut (`M`/`F`), and ruff clean. Say explicitly that the ledger's "4215" was an unmeasured pre-merge figure and that the baseline used here was measured at the cut commit.

- [ ] **Step 9: Teardown and confirm the tree is clean**

```bash
docker compose -p pst4 down
git status --short
git log --oneline feat/notifications-1..HEAD
```

Expected: `git status --short` empty; six commits (the plan, the wire and guard, batch 1, batch 2, the frontend and row 112's keys, the roadmap) — one more for each extra commit batch 1 was split into. **No push, no PR**: the user gates that.
</content>
</invoke>
