# Config Safety (the overrides-wipe phase) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make `PUT /api/config/overrides` incapable of silently destroying the operator's overrides — by refusing a write that cannot prove it knew what it was replacing — and give the operator a backup, a history and a one-click restore for when something destroys them anyway.

**Architecture:** four layers over one existing write seam. (1) `OverridesBody` becomes `extra="forbid"`, so the *envelope* mistake that started the incident (the overrides document sent unwrapped, at top level) is a 422 instead of a silent bind-to-`{}`. (2) `_persist_and_swap` — the one function both the PUT and the apply funnel through, and which `/config/preview` deliberately does *not* — grows four write-only guards: an empty-document refusal, a drop cap, a content-hash revision compare under `SELECT … FOR UPDATE`, and path counts on the audit row. (3) A new `config_override_snapshots` table captures the *outgoing* document pre-write, inside the same transaction as the upsert, pruned to 20 inline; three endpoints list, read and restore it, with restore routed back through the same validation and the same guards. (4) Export/import is that document in a thin `{"autoposter_overrides": 1, …}` envelope, with import implemented as a save with a file picker in front of it — no second write path. The frontend's four writing pages all carry the revision they seeded from and, on a 409, re-seed and *tell the operator* rather than retrying.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 async + PostgreSQL (JSONB), alembic, pytest + pytest-asyncio + httpx `ASGITransport`; React 19 + TypeScript + vitest + Testing Library.

## Global Constraints

Every task's requirements implicitly include this section.

1. **Branch.** `fix/config-safety`, cut from `origin/main` (`a88f79a`, the Phase 11 finale merge) after the content probe in T1 Step 1 passes. Do not rebase mid-phase.
2. **Sequencing is binding.** T1 → T2 → T3 → T4 → T5. T1 and T2 are the incident fix and ship in the SAME phase behind ONE gate — there is no split PR. T4 consumes T3's restore helper; T5 consumes T3 and T4. T2 and T5 both edit `frontend/src/pages/Settings.tsx`, which is why they are sequenced rather than parallelised.
3. **NO `src/autoposter/config/schema.py` changes, anywhere in this phase.** The drop cap (`OVERRIDE_DROP_CAP = 3`) and the snapshot retention (`SNAPSHOT_RETENTION = 20`) are **module constants, not settings**. This keeps the phase strictly disjoint from the written-but-unstarted overlay era, which adds config sections to that file (`.superpowers/sdd/p-overlay-era-recon.md`, rows 97/105). Every task ends by proving it: `git diff --stat <task-start-sha> -- src/ tests/ frontend/` must not list `src/autoposter/config/schema.py`, and must list only files named in that task's **Files** block.
4. **The guards live on the WRITE path, never on the validation path.** `_validated_generation`'s docstring promises *"Nothing here persists, swaps or enqueues anything"* and `POST /api/config/preview` routes through it. **A preview must never be refused for being destructive** — answering "what would this do" is its entire job. Every destructiveness guard, and the 409, goes in `_persist_and_swap`. Each task that adds one also adds the preview negative.
5. **The envelope forbids extras; the config document does not.** `model_config = ConfigDict(extra="forbid")` goes on `OverridesBody` (and on every new body model in this phase). The *overrides document* stays `extra="ignore"` at pydantic level because `unknown_key_paths` already gives a far better error at full depth. This asymmetry is deliberate; say so in the docstring.
6. **The revision token is a CONTENT HASH, never a timestamp.** `sha256` over `json.dumps(document, sort_keys=True, separators=(",", ":"))`. `ConfigOverride.updated_at` exists (`db/models.py:457`) and is the obvious candidate — **do not use it**: it moves on a no-op rewrite, and this dev machine has a recorded Docker clock that steps ~2.7s *backwards* every ~27s, which would make a timestamp token go backwards.
7. **A stale save 409s LOUDLY and the client NEVER silently retries.** A silent retry re-applies an edit onto a document the operator has not seen, which is a quieter version of the bug being fixed. The client re-fetches, re-seeds, and says so.
8. **Confirm is a flag, not a second endpoint** — the `DeleteRequest.confirm` precedent (`api/collections_builders.py:395-406`, its 422 at `:577-581`): *"A confirmation flag rather than a second endpoint, because the thing being confirmed is this library and this title."* Same argument verbatim.
9. **The audit row records COUNTS, never settings.** `EventLog.payload` gains `paths_before`/`paths_after` integers. The docstring's rule (`routes.py:1576-1583`, "Versions, never the document") is not violated by counts — say why in the comment.
10. **A snapshot and its write commit together or not at all.** The capture is an `session.add` inside the *existing* session and the *existing* single `commit()` in `_persist_and_swap`. A snapshot that commits without its write, or a write without its snapshot, is worse than neither. A test proves a refused write leaves no snapshot orphan.
11. **Restore and import are SAVES, not bypasses.** Both go through `_validated_generation` + `_persist_and_swap`: same five refusal gates, same snapshot (so a restore is itself undoable), same drop cap, same 409.
12. **Export is UNREDACTED, deliberately.** A redacted backup is a broken backup — re-importing one would write the bare host over `notifications.url` and destroy a push token. The endpoint returns real values; the docstring and the UI download copy both state the trade in plain words. (`secrets` is absent by construction — `merge_overrides` refuses the key.)
13. **The gated-feature entry-point law.** Every behaviour here is proven through the REAL endpoint with the real `ASGITransport` client (or, on the frontend, through the real page component with a stubbed `fetch`) — never through a helper alone. Ungated behaviour is byte-identical to pre-phase behaviour: a normal one-field save must be unaffected by every guard, and a client that sends no `expected_revision` must still succeed.
14. **Suites stated-then-measured.** T1 Step 1 MEASURES the post-#127 baseline (backend pytest total, frontend vitest total). `4555/383` from `.superpowers/sdd/progress.md` is a *claim*, not a fact, until that step writes it down. Every later task states its expected total BEFORE running and reconciles any difference in its report.
15. **Container discipline.** Unique compose project per task — **`pcs1`, `pcs2`, `pcs3`, `pcs4`, `pcs5`** — backend runs always with the `.superpowers/isolated-db.yml` overlay. Tee backend output to a path under `/app/.superpowers/` inside the container; frontend output is redirected host-side through the **`web` service**. **Never trust an exit code or `docker wait` alone — read the log** (roadmap row 193). Long runs start detached (`run -d --name …`) and are waited on with a foreground `docker wait`, then `docker rm`. Teardown per task is `docker compose -p <project> down` — **never** `down -v`.
16. **Never assert wall-clock ordering across sleeps in a container test.** The dev machine's Docker clock steps backwards. Snapshot ordering is asserted on `id DESC` (a monotonic identity column), never on `created_at`.
17. **No test in this phase opens a socket.** `tests/conftest.py`'s `no_outbound_network` autouse fixture is not to be bypassed.
18. **Field descriptions.** No new config key is added by this phase (constraint 3), so `tests/test_config_descriptions.py` is a regression gate here, not a task gate. It must stay green.
19. **Commits** are conventional, `--no-gpg-sign`, staged **by name** (never `git add -A`, `-u` or `.`), and carry **no AI attribution** of any kind — no `Co-Authored-By`, no "Generated with", nothing. Same for the PR body.
20. **Artifacts are `p-configsafety-` prefixed** and live in `.superpowers/` / `.superpowers/sdd/` (gitignored). The PR body at `.superpowers/sdd/p-configsafety-pr-body.md` is **never committed**.
21. **This plan file is committed by Task 1, Step 1.** It is an untracked working-tree file until then.
22. **RED before GREEN on every behavioural step:** write the failing test, run it, watch it fail for the RIGHT reason, then implement the minimum that makes it pass.

---

## The incident this plan reproduces

Two independent defects in one family, both proven in `.superpowers/sdd/p-configsafety-recon.md`. The RED tests in T1 and T2 are not analogies for them — they are the observations.

| Observation (`.superpowers/sdd/progress.md:3851-3854`) | Defect | Reproduced by |
|---|---|---|
| A raw `PUT` → 200 + normal version stamps, store went 17 paths → 0 | D1: the unwrapped body binds to `document = {}` via `extra="ignore"` (`api/routes.py:1368-1381`) | T1 Step 2, `test_an_unwrapped_document_body_is_refused_not_silently_emptied` |
| A raw restore `PUT`, 757 real bytes of good JSON, 200, `version_before == version_after`, nothing landed | D1 again — the same discard, the second time over an already-empty row | T1 Step 2, `test_the_incident_restore_body_is_refused_rather_than_written_as_empty` |
| A Settings-UI save of `collections.separator_style: sand` did not stick; the identical second save did | D2: a whole-document lost update from another mounted page's stale mount-time seed (`Settings.tsx:571`, `CatalogPanel.tsx:275`, `GroupsPanel.tsx:148`, `CustomCollectionsPanel.tsx:232`) | T2 Step 2, `test_the_two_page_stale_save_is_refused_instead_of_clobbering` |
| UI saves generally working | Neither defect fires when one page saves against a fresh seed | T1 Step 2 / T2 Step 2 negatives |

**Fixing either defect alone leaves the incident reproducible by the other route.** That is why T1 and T2 ship behind one gate.

---

## The frontend's eight-clause contract

Seven clauses hold today (`p-configsafety-recon.md` §3); this phase adds the eighth. Every page this plan touches must satisfy all eight, and T2 Step 12 asserts clause 8 for all four.

1. **Wrapped.** `{"document": <the deltas>}`. Never the bare document. *This is the single line the raw fetches violated.*
2. **Whole, and a delta** — not a patch, and not the config. Seeded from `documentFromConfig(response)`, so a save of one field re-sends all the others.
3. **Reverting is key removal, never `null`.** `null` is a value and 422s.
4. **Redacted paths carry the keep sentinel**, never the served value.
5. **`Content-Type: application/json` + `Authorization: Bearer <token>`** — `api/client.ts:69-76`.
6. **PUT is not "stage".** It persists, swaps the live config and writes an audit row. `POST /api/config/apply` is that plus a render enqueue; `POST /api/config/preview` is the only non-persisting arm and takes the identical body.
7. **The server owns provenance.** After every write the page re-GETs `/api/config` and re-adopts.
8. **NEW — every writer sends back the revision of the document it seeded from**, and the server refuses a write composed against a document that has since moved.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `src/autoposter/config/overrides.py` | `document_revision`, `EMPTY_DOCUMENT_REVISION`, `load_overrides_document(session, *, for_update=False)`, `_without_migrated_sections` → public `without_migrated_sections` | T1, T2, T3 |
| `src/autoposter/api/routes.py` | `OverridesBody` hardening; `OVERRIDE_DROP_CAP`; the four guards + the audit counts in `_persist_and_swap`; `overrides_revision` on `GET /api/config`; snapshot endpoints; export/import endpoints | T1–T4 |
| `src/autoposter/config/snapshots.py` | **new** — `SNAPSHOT_RETENTION`, `capture_snapshot`, `list_snapshots`, `load_snapshot`. **Not** `api/snapshots.py`, which already means the status/events payload builders | T3 |
| `src/autoposter/db/models.py` | **new** `ConfigOverrideSnapshot` | T3 |
| `alembic/versions/c7e1b93a4d20_config_override_snapshots.py` | **new** — one `create_table`, `down_revision = 'f3a9c41d2b07'` | T3 |
| `tests/test_api_config_editor.py` | the D1 **and** D2 reproductions, the guards, the revision mechanics, the preview negatives. This file owns the fixtures (`config_file`, `app`, `client`, `auth_headers`) and is where the seam's rules are already documented, so the incident tests live here rather than in a new file that would have to rebuild them | T1, T2 |
| `tests/test_config_overrides.py` | the revision hash's unit tests | T2 |
| `tests/test_config_safety.py` | **new** — snapshots and export/import, importing the four fixtures above by name | T3, T4 |
| `frontend/src/api/overrides.ts` | `revisionFromConfig`, `saveBody`, `STALE_SAVE_NOTE` | T2 |
| `frontend/src/api/overrides.test.ts` | their unit tests | T2 |
| `frontend/src/api/types.ts` | `overrides_revision` on the save response; `ConfigSnapshot`, `ConfigExport` | T2, T5 |
| `frontend/src/pages/Settings.tsx` | the revision + 409 flow (T2); the safety panel (T5) | T2, T5 |
| `frontend/src/pages/CatalogPanel.tsx` | the revision + 409 flow | T2 |
| `frontend/src/pages/GroupsPanel.tsx` | the revision + 409 flow | T2 |
| `frontend/src/pages/CustomCollectionsPanel.tsx` | the revision + 409 flow | T2 |
| `frontend/src/pages/{Settings,CatalogPanel,GroupsPanel,CustomCollectionsPanel}.test.tsx` | one 409 test each, plus the clause-8 assertion | T2 |
| `frontend/src/pages/settings.css` | `.config-safety` styles | T5 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | file-and-close row 231 | T5 |
| `.superpowers/sdd/progress.md` | the phase's ship entry | T5 |
| `.superpowers/sdd/p-configsafety-pr-body.md` | **new, never committed** | T5 |

---

## The cross-task interface contract

T1 and T2 produce exactly these names. T3, T4 and T5 consume them and add only what their own rows say.

```python
# autoposter/config/overrides.py                                    (T1, T2)
EMPTY_DOCUMENT_REVISION: str      # "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
def document_revision(document: dict) -> str: ...
def without_migrated_sections(document: dict) -> dict: ...          # renamed from _without_migrated_sections
async def load_overrides_document(session, *, for_update: bool = False) -> dict: ...

# autoposter/api/routes.py                                          (T1, T2)
OVERRIDE_DROP_CAP: int = 3

class OverridesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict = Field(default_factory=dict)
    expected_revision: str | None = None
    confirm: bool = False

async def _persist_and_swap(
    request: Request,
    document: dict,
    after: Config,
    *,
    expected_revision: str | None = None,
    confirm: bool = False,
    reason: str = "save",
) -> dict: ...
# returns {version_before, version_after, restart_required, inert, overrides_revision}

# autoposter/config/snapshots.py                                    (T3)
SNAPSHOT_RETENTION: int = 20
async def capture_snapshot(session, document: dict, reason: str) -> None: ...
async def list_snapshots(session) -> list[dict]: ...      # [{id, created_at, path_count, reason}] newest first
async def load_snapshot(session, snapshot_id: int) -> dict: ...   # raises LookupError when absent
```

```ts
// frontend/src/api/overrides.ts                                    (T2)
export function revisionFromConfig(config: ConfigResponse): string | null;
export function saveBody(document: OverridesDocument, revision: string | null): string;
export const STALE_SAVE_NOTE: string;
```

---

# Task 1: The shape guard, the destructiveness guards, and the audit counts

This task closes the **raw-fetch half** of the incident (defect D1) and puts the two
destructiveness guards on the write path. It is the highest-value change in the phase:
one line (`extra="forbid"`) converts the exact incident from a silent wipe into an
error message the frontend's existing `fieldErrors` already renders.

**Files:**
- Modify: `src/autoposter/api/routes.py:1368-1381` (`OverridesBody`), `:1556-1602` (`_persist_and_swap`), `:11` (imports)
- Modify: `src/autoposter/config/overrides.py:163-185` (`_without_migrated_sections` → `without_migrated_sections`), `:216`
- Test: `tests/test_api_config_editor.py` (modify — append a new section)

**Interfaces:**
- Consumes: `autoposter.config.overrides.{document_paths, load_overrides_document, OVERRIDES_ROW_ID}`; `autoposter.api.routes.{_validated_generation, _error, _persist_and_swap}`; `autoposter.db.models.{ConfigOverride, EventLog}`.
- Produces: `OVERRIDE_DROP_CAP`; `OverridesBody` with `model_config = ConfigDict(extra="forbid")` and `confirm: bool = False`; `_persist_and_swap(request, document, after, *, confirm=False, reason="save")` — **T2 adds `expected_revision` to both**; `without_migrated_sections` (the public rename T3 imports).

- [ ] **Step 1: Probe `origin/main`, cut the branch, commit this plan, and MEASURE the baseline**

The probe first. Every marker must print, in order; if one does not, **STOP and report** — the plan was written against a tree that is not this one.

```bash
git fetch origin
git show origin/main:src/autoposter/api/routes.py | grep -q "class OverridesBody(BaseModel):" && echo BODY-MODEL-PRESENT
git show origin/main:src/autoposter/api/routes.py | grep -q "async def _persist_and_swap" && echo WRITE-SEAM-PRESENT
git show origin/main:src/autoposter/api/routes.py | grep -q "async def _validated_generation" && echo VALIDATION-SEAM-PRESENT
git show origin/main:src/autoposter/config/overrides.py | grep -q "def _without_migrated_sections" && echo MIGRATED-STRIP-PRESENT
git show origin/main:src/autoposter/config/overrides.py | grep -q "def document_paths" && echo DOCUMENT-PATHS-PRESENT
git show origin/main:src/autoposter/api/collections_builders.py | grep -q "confirm: bool = False" && echo CONFIRM-PRECEDENT-PRESENT
git show origin/main:frontend/src/api/overrides.ts | grep -q "export function documentFromConfig" && echo SEEDING-HELPER-PRESENT
```

Then:

```bash
git checkout -b fix/config-safety origin/main
git rev-parse HEAD
git add docs/superpowers/plans/2026-09-03-config-safety.md
git commit --no-gpg-sign -m "docs(plans): the config safety plan — the overrides-wipe incident fix"
```

Now measure both suites. **These two numbers are the baseline every later task reconciles against; no number in this plan is a fact until this step writes it down.**

```bash
docker compose -p pcs1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pcs1-base test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-p-configsafety-base.log"
docker wait pcs1-base
docker rm pcs1-base
docker compose -p pcs1 run --name pcs1-base-vitest web npm test > .superpowers/run-p-configsafety-base-vitest.log 2>&1
docker rm pcs1-base-vitest
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-base.log` and
`D:\Sites\autoposter\.superpowers\run-p-configsafety-base-vitest.log` — never trust
the wait code alone** (roadmap row 193). Record both totals ("N passed" for pytest,
"Tests  N passed" for vitest) in the Task 1 report. Both must be **0 failed** before
any work starts; a red baseline is a STOP-and-report, not something to build on. A
`tests/test_worker.py` / `tests/test_pipeline.py` one-off is rows 193/195's known
flake — re-run that file alone before treating it as real. `progress.md` claims
`4555/383`; treat any difference as the measurement being right and the note being
stale, and say so in the report.

- [ ] **Step 2: Write the failing tests — the D1 reproduction and the two guards**

Append to the end of `tests/test_api_config_editor.py`. `THE_INCIDENT_DOCUMENT` is
the shape the operator's real overrides had: twelve paths across seven sections, the
same kind of body the restore fetch sent as 757 bytes of good JSON.

```python
# --- Config safety: the shape guard and the destructiveness guards -------
#
# These reproduce the 2026-09-01 incident (.superpowers/sdd/progress.md:3851).
# Defect D1: a PUT whose JSON body is a well-formed object that does NOT carry
# a top-level `document` key was silently read as "the operator's complete set
# of deltas is now empty" -- pydantic v2's default extra="ignore" matched it
# against zero declared fields and threw 757 bytes away -- and the unconditional
# upsert wrote {} over seventeen stored overrides with a 200.

THE_INCIDENT_DOCUMENT = {
    "workers": 9,
    "artwork": {
        "title_card": {"season_label": "Kausi"},
        "poster": {"text": {"min_point_size": 22}},
    },
    "plex": {"resolve_max_attempts": 7},
    "badges": {"enabled": False, "upload_to_plex": False},
    "notifications": {"enabled": False},
    "collections": {
        "separator_style": "sand",
        "ownership_label": "Autoposter",
        "delete_unconfigured": False,
        "max_deletes": 100,
    },
    "scheduler": {"enabled": False},
}


async def _store(client, auth_headers, document: dict) -> None:
    """Put a document in the store the ordinary way, and insist it landed."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": document}
    )
    assert response.status_code == 200, response.text


async def test_an_unwrapped_document_body_is_refused_not_silently_emptied(
    client, auth_headers, session
):
    """The incident's first PUT: the document sent bare, at top level.

    Before the fix this answered 200 and wiped the store. `document` defaulted
    to {}, {} is a fully valid document (it is the *documented* revert-
    everything), and _persist_and_swap wrote it unconditionally.
    """
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json=THE_INCIDENT_DOCUMENT
    )

    assert response.status_code == 422, (
        "an unwrapped body was accepted. It binds to document={} and wipes the "
        "store with a 200 -- this is the incident"
    )
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT, "the refused request still wrote"


async def test_the_incident_restore_body_is_refused_rather_than_written_as_empty(
    client, auth_headers, session, app
):
    """The incident's second PUT: the same mistake over an already-empty row.

    757 bytes of perfectly good JSON, a 200, and version_before ==
    version_after *by construction* -- the merged config was the file alone,
    which was already what was running. Nothing landed and nothing said so.
    """
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json=THE_INCIDENT_DOCUMENT
    )

    assert response.status_code == 422
    # FastAPI's request-validation shape, which the frontend's `fieldErrors`
    # already renders: the extras are named by `loc`.
    named = {tuple(entry["loc"]) for entry in response.json()["detail"]}
    assert ("body", "workers") in named, response.text
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []
    assert app.state.config.workers == 5, "a refused body must not swap anything"


async def test_a_correctly_wrapped_body_is_untouched_by_the_shape_guard(
    client, auth_headers, session
):
    """The negative: the SPA's own body shape still saves. It sends exactly one
    key, so forbidding extras on the envelope cannot reach it."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_the_preview_refuses_the_unwrapped_body_the_same_way(
    client, auth_headers
):
    """All three arms share the model, so all three harden together."""
    response = await client.post(
        "/api/config/preview", headers=auth_headers, json=THE_INCIDENT_DOCUMENT
    )
    assert response.status_code == 422


async def test_emptying_a_non_empty_store_needs_confirm(
    client, auth_headers, session, app
):
    """`{}` is a legal document and the documented revert-everything. What was
    missing was any way to tell a deliberate clear-all apart from a request
    that *degenerated* into one."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {}}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "path": "document",
            "message": (
                "this would clear all 12 stored overrides; send confirm: true "
                "to do it deliberately"
            ),
        }
    ]
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT
    assert app.state.config.workers == 9, "a refused write must not swap"


async def test_emptying_a_non_empty_store_is_allowed_with_confirm(
    client, auth_headers, session, app
):
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {}, "confirm": True},
    )

    assert response.status_code == 200
    assert (await session.execute(select(ConfigOverride))).scalar_one().document == {}
    assert app.state.config.workers == 5, "the example config's value did not come back"


async def test_an_empty_document_over_an_empty_store_stays_a_legal_no_op(
    client, auth_headers
):
    """What a fresh deployment's first save looks like. There is nothing to
    destroy, so there is nothing to confirm."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {}}
    )
    assert response.status_code == 200


async def test_a_write_that_drops_more_than_the_cap_is_refused(
    client, auth_headers, session
):
    """The drop cap. A normal edit drops 0 or 1 path; the incident dropped 17.

    `document_paths` is exactly the unit `GET /api/config`'s `overridden_paths`
    reports, so the operator, the API and this refusal all count the same
    things.
    """
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": 9}}
    )

    assert response.status_code == 422
    [detail] = response.json()["detail"]
    assert detail["path"] == "document"
    assert detail["message"].startswith("this would drop 11 stored overrides")
    # Named, not just counted: an operator cannot judge a refusal they cannot see.
    assert "collections.separator_style" in detail["message"]
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_a_drop_over_the_cap_is_allowed_with_confirm(
    client, auth_headers, session
):
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {"workers": 9}, "confirm": True},
    )

    assert response.status_code == 200
    assert (await session.execute(select(ConfigOverride))).scalar_one().document == {
        "workers": 9
    }


async def test_a_normal_one_field_edit_never_trips_the_drop_cap(
    client, auth_headers, session
):
    """The blast-radius negative, and the one that matters most: the guards are
    worthless if the ordinary save has to learn about them."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await _store(client, auth_headers, edited)

    # And clearing exactly one override -- the drop cap's other boundary.
    reduced = deepcopy(edited)
    del reduced["scheduler"]
    await _store(client, auth_headers, reduced)
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert "scheduler" not in stored


async def test_dropping_exactly_the_cap_is_allowed_and_one_more_is_not(
    client, auth_headers
):
    """The boundary, both sides of it. OVERRIDE_DROP_CAP is 3: three dropped
    paths pass, four do not."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    three_fewer = deepcopy(THE_INCIDENT_DOCUMENT)
    del three_fewer["badges"]              # 2 paths
    del three_fewer["scheduler"]           # 1 path
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": three_fewer}
    )
    assert response.status_code == 200, response.text

    four_fewer = deepcopy(three_fewer)
    del four_fewer["notifications"]        # 1 path
    del four_fewer["plex"]                 # 1 path
    del four_fewer["workers"]              # 1 path
    del four_fewer["artwork"]              # 2 paths
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": four_fewer}
    )
    assert response.status_code == 422


async def test_the_preview_is_never_refused_for_being_destructive(
    client, auth_headers
):
    """`_validated_generation` promises every failure mode is reached before
    the first write, and the preview's whole job is answering "what would this
    do". A preview that refused to describe a destructive edit would be
    refusing the one question worth asking about it."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    for document in ({}, {"workers": 9}):
        response = await client.post(
            "/api/config/preview", headers=auth_headers, json={"document": document}
        )
        assert response.status_code == 200, (document, response.text)


async def test_the_preview_ignores_confirm_rather_than_rejecting_it(
    client, auth_headers
):
    """One body shape across all three arms -- the property Settings.tsx's
    `submit()` docstring says the three actions must never drift on."""
    response = await client.post(
        "/api/config/preview",
        headers=auth_headers,
        json={"document": {"workers": 9}, "confirm": True},
    )
    assert response.status_code == 200


async def test_the_audit_row_records_the_path_counts(
    client, auth_headers, session
):
    """The incident would have been visible in the audit log as `12 -> 0`
    rather than requiring an investigation. Counts, not settings: the payload's
    "versions, never the document" rule is about what an operator changed, and
    how many is not that."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {}, "confirm": True},
    )

    rows = (
        (
            await session.execute(
                select(EventLog)
                .where(EventLog.event_type == "overrides_updated")
                .order_by(EventLog.id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.payload["paths_before"] for row in rows] == [0, 12]
    assert [row.payload["paths_after"] for row in rows] == [12, 0]
    assert "document" not in rows[-1].payload
```

- [ ] **Step 3: Run the new tests to verify they fail for the right reasons**

Explicit node ids, not a `-k` expression: a `-k` needs its own quoting inside the
already-quoted `sh -c` and silently selects nothing when the quoting is lost, which
reads as a green run.

```bash
docker compose -p pcs1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs1-red1 test sh -c \
  "set -o pipefail; pytest -q tests/test_api_config_editor.py::test_an_unwrapped_document_body_is_refused_not_silently_emptied tests/test_api_config_editor.py::test_the_incident_restore_body_is_refused_rather_than_written_as_empty tests/test_api_config_editor.py::test_a_correctly_wrapped_body_is_untouched_by_the_shape_guard tests/test_api_config_editor.py::test_the_preview_refuses_the_unwrapped_body_the_same_way tests/test_api_config_editor.py::test_emptying_a_non_empty_store_needs_confirm tests/test_api_config_editor.py::test_emptying_a_non_empty_store_is_allowed_with_confirm tests/test_api_config_editor.py::test_an_empty_document_over_an_empty_store_stays_a_legal_no_op tests/test_api_config_editor.py::test_a_write_that_drops_more_than_the_cap_is_refused tests/test_api_config_editor.py::test_a_drop_over_the_cap_is_allowed_with_confirm tests/test_api_config_editor.py::test_a_normal_one_field_edit_never_trips_the_drop_cap tests/test_api_config_editor.py::test_dropping_exactly_the_cap_is_allowed_and_one_more_is_not tests/test_api_config_editor.py::test_the_preview_is_never_refused_for_being_destructive tests/test_api_config_editor.py::test_the_preview_ignores_confirm_rather_than_rejecting_it tests/test_api_config_editor.py::test_the_audit_row_records_the_path_counts 2>&1 | tee /app/.superpowers/run-p-configsafety-t1-red1.log"
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-t1-red1.log`.**

Expected: **9 failed, 5 passed.** The failures and their mechanisms — check each one,
because a test that fails for the wrong reason is not a RED:

| Test | Expected failure |
|---|---|
| `…unwrapped_document_body_is_refused…` | `assert 200 == 422` — **this is D1** |
| `…incident_restore_body_is_refused…` | `assert 200 == 422` |
| `…preview_refuses_the_unwrapped_body…` | `assert 200 == 422` |
| `…emptying_a_non_empty_store_needs_confirm` | `assert 200 == 422` |
| `…emptying…allowed_with_confirm` | `422` — `confirm` is an unknown field today only after Step 4's `extra="forbid"`; **before** it, this test PASSES. If it fails at this step, read the log: pre-fix it should pass (extras are ignored). |
| `…drops_more_than_the_cap_is_refused` | `assert 200 == 422` |
| `…dropping_exactly_the_cap…one_more_is_not` | `assert 200 == 422` on the second half |
| `…audit_row_records_the_path_counts` | `KeyError: 'paths_before'` |

The five that pass are the negatives (`…untouched_by_the_shape_guard`,
`…legal_no_op`, `…allowed_with_confirm` twice over, `…normal_one_field_edit…`,
`…preview_is_never_refused…`, `…preview_ignores_confirm…`) — they assert behaviour
that already holds and are the guards' blast-radius pins. They must stay green through
Step 7. If the counted split is not 9/5, reconcile it in the report before continuing;
do not adjust a test to make the arithmetic come out.

Then `docker rm pcs1-red1`.

- [ ] **Step 4: Harden the envelope**

`src/autoposter/api/routes.py:11` — add `ConfigDict` to the pydantic import:

```python
from pydantic import BaseModel, ConfigDict, Field, ValidationError
```

Replace `OverridesBody` (`routes.py:1368-1381`) with:

```python
class OverridesBody(BaseModel):
    """The whole overrides document, always sent whole.

    Not a patch: the document *is* the operator's complete set of deltas, and
    "revert this field to the file's value" is expressed by the key being
    absent from it. A patch shape would need an out-of-band way to say
    "delete", and the obvious candidate -- sending ``null`` -- already means
    something else (see ``_validated_generation``).

    The envelope forbids extras and the *document* does not, and the asymmetry
    is deliberate. Forbidding here turns one specific catastrophe into an error
    message: a body of ``{"artwork": ..., "scheduler": ...}`` -- the document
    sent bare, which is what a hand-written fetch produces -- used to match
    zero declared fields under pydantic's default ``extra="ignore"``, bind
    ``document`` to its ``{}`` default, and wipe every stored override with a
    200 (the 2026-09-01 incident). Inside the document, ``unknown_key_paths``
    gives a far better error than pydantic could, at full depth and with the
    dotted path an operator can act on, so nothing is gained by forbidding
    twice.

    ``confirm`` authorises a destructive write -- one that empties the store or
    drops more than ``OVERRIDE_DROP_CAP`` paths. A flag rather than a second
    endpoint, for the reason ``DeleteRequest.confirm`` gives in
    ``api/collections_builders.py``: what is being confirmed is *this*
    document against *this* store, which a separate endpoint could not name.
    ``POST /api/config/preview`` accepts it and ignores it, so all three arms
    keep taking one body shape.
    """

    model_config = ConfigDict(extra="forbid")

    document: dict = Field(default_factory=dict)
    confirm: bool = False
```

Add the cap constant immediately above the class:

```python
# A normal edit drops 0 or 1 override; the 2026-09-01 incident dropped 17. A
# module constant rather than a setting on purpose: a destructiveness cap an
# operator can raise from the same page the destructive write comes from is
# not a cap, and this phase is deliberately disjoint from config/schema.py.
OVERRIDE_DROP_CAP = 3
```

- [ ] **Step 5: Put the two destructiveness guards on the write path**

Replace the body of `_persist_and_swap` (`routes.py:1556-1602`). The signature gains
two keyword-only arguments; the guards run **inside the session and before the
upsert**, preserving the never-half-apply ordering the existing tests reorder to prove.

```python
def _drop_refusal(stored: dict, document: dict) -> str | None:
    """Why this write is too destructive to do unasked, or None.

    Counted in ``document_paths`` units -- exactly what ``GET /api/config``
    reports as ``overridden_paths`` -- so the operator, the API and this
    sentence all count the same things.
    """
    before = set(document_paths(stored))
    if not before:
        # Nothing to destroy. A fresh deployment's first save lands here and
        # must not be asked to confirm anything.
        return None
    if not document:
        return (
            f"this would clear all {len(before)} stored overrides; "
            "send confirm: true to do it deliberately"
        )
    dropped = sorted(before - set(document_paths(document)))
    if len(dropped) <= OVERRIDE_DROP_CAP:
        return None
    return (
        f"this would drop {len(dropped)} stored overrides "
        f"({', '.join(dropped)}); send confirm: true to do it deliberately"
    )


async def _persist_and_swap(
    request: Request,
    document: dict,
    after: Config,
    *,
    confirm: bool = False,
    reason: str = "save",
) -> dict:
    """Store the document, swap the running generation, log the event.

    Only ever called with an ``after`` that ``_validated_generation`` already
    built, so by the time anything is written the config is known to be whole.
    ``swap_config`` is three assignments and a dict refresh with no I/O, so it
    cannot fail after the row is committed either.

    This is also where every *write-only* guard lives, and none of them live in
    ``_validated_generation``. That function promises "nothing here persists,
    swaps or enqueues anything" and ``POST /api/config/preview`` routes through
    it -- a preview must never be refused for being destructive, because
    answering "what would this do" is the whole of its job. Guards that fire on
    a write belong on the write path, which ``put_config_overrides`` and
    ``apply_config_overrides`` both funnel through, so one insertion covers
    both.

    The stored document is read here, in the same session as the upsert, rather
    than in an earlier one: a separate read would be a TOCTOU window inside the
    fix for a TOCTOU bug.

    Crash-consistent by construction: if the process dies between the commit
    and ``swap_config``, requests keep being served by the old generation until
    restart, at which point ``load_effective_config`` reads the persisted
    overrides back off the database and starts on the new one -- correct by
    design, not by luck. ``api_docs_enabled`` is the one exception: a restart
    re-reads the database overrides but not the mounted file, so it lands back
    exactly where the file left it -- which is why it is reported in ``inert``
    below rather than ``restart_required``.
    """
    before = request.app.state.config
    async with request.app.state.session_factory() as session:
        stored = await load_overrides_document(session)
        if not confirm:
            refusal = _drop_refusal(stored, document)
            if refusal is not None:
                raise HTTPException(
                    status_code=422, detail=[_error("document", refusal)]
                )

        stmt = insert(ConfigOverride).values(id=OVERRIDES_ROW_ID, document=document)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"], set_={"document": document, "updated_at": func.now()}
        )
        await session.execute(stmt)
        session.add(
            EventLog(
                source="config",
                event_type="overrides_updated",
                # Versions and counts, never the document. The overrides carry
                # no secrets (merge_overrides refuses a `secrets` key
                # outright), but an audit row is read casually and copied into
                # tickets, and *which* settings an operator changed are not
                # this table's business. How many there were before and after
                # is: the 2026-09-01 wipe would have read `12 -> 0` here
                # instead of costing an investigation.
                payload={
                    "version_before": before.version,
                    "version_after": after.version,
                    "paths_before": len(document_paths(stored)),
                    "paths_after": len(document_paths(document)),
                    "reason": reason,
                },
                outcome=f"version {before.version} -> {after.version}",
            )
        )
        await session.commit()

    restart_required = _restart_required(before, after)
    inert = _inert_changes(before, after)
    swap_config(request.app, after)
    return {
        "version_before": before.version,
        "version_after": after.version,
        "restart_required": restart_required,
        "inert": inert,
    }
```

Then thread `confirm` through the one caller that has it today —
`put_config_overrides` and `apply_config_overrides` both change their
`_persist_and_swap` call:

```python
    document, after = await _validated_generation(request, body.document)
    return await _persist_and_swap(request, document, after, confirm=body.confirm)
```

and, in `apply_config_overrides`:

```python
    saved = await _persist_and_swap(
        request, document, after, confirm=body.confirm, reason="apply"
    )
```

Add to `put_config_overrides`' docstring, after the existing final paragraph:

```
    A destructive save -- one that empties a non-empty store, or drops more
    than ``OVERRIDE_DROP_CAP`` of its paths -- is refused with a 422 naming the
    paths, and needs ``confirm: true``. A body that is not the ``{"document":
    ...}`` envelope is refused by the model before this runs.
```

- [ ] **Step 6: Make the migrated-sections strip public (T3 needs it by name)**

In `src/autoposter/config/overrides.py`, rename `_without_migrated_sections` to
`without_migrated_sections` at its definition (`:163`) and at its one call site
(`:216`). Nothing else in the tree references it — verified by
`grep -rn "_without_migrated_sections" src/ tests/`, which returns those two lines only.
Add one sentence to the docstring saying why it is public now:

```python
def without_migrated_sections(document: dict) -> dict:
    """``document`` minus any ``MIGRATED_SECTIONS`` key, warning once per key.

    Public because the stored document is no longer its only source: a restored
    snapshot is a raw row this function never ran over, and a snapshot taken
    before a section left the schema still holds it (config/snapshots.py).
```

(the rest of the docstring is unchanged.)

- [ ] **Step 7: Run the new tests, then the whole file**

```bash
docker compose -p pcs1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs1-green1 test sh -c \
  "set -o pipefail; pytest -q tests/test_api_config_editor.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t1-green1.log"
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-t1-green1.log`.**
Expected: **0 failed**, and the file's total up by **14**.

Two pre-existing tests in this file are the ones most likely to move and both are
worth checking by name in the log rather than assuming:

- `test_omitting_a_key_reverts_it_to_the_file` (`:398-404`) saves `{"workers": 9}`
  and then `{"document": {}}`. That second call now drops one path from a one-path
  store — **`_drop_refusal` returns the clear-all message**, so it 422s. This test
  pins the *documented* revert-everything and must keep doing so, so add `"confirm":
  True` to its second call and one line saying why:

```python
    # Clearing every override is still the documented revert-everything -- it
    # is now a *deliberate* one (OVERRIDE_DROP_CAP's empty-document arm).
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {}, "confirm": True},
    )
```

- Any test asserting `EventLog.payload == {...}` by equality rather than by key. Search
  with `grep -n "overrides_updated" tests/test_api_config_editor.py` and widen an
  equality assertion to the two version keys it was actually about, leaving the new
  count keys unasserted there (they have their own test).

If any *other* test in the file goes red, **STOP and report** — that is a blast radius
this plan did not predict, and adapting it silently is how a guard becomes a
regression.

- [ ] **Step 8: Run the full backend suite**

```bash
docker compose -p pcs1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pcs1-full test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-p-configsafety-t1-full.log"
docker wait pcs1-full
docker rm pcs1-full
```

**Read the log.** Expected: **0 failed**, total = the Step 1 baseline **+ 14**. State
that expected number before running and reconcile any difference in the report. Other
suites that touch this seam and must stay green: `tests/test_config_overrides.py`
(the rename), `tests/test_adopt_config.py`, `tests/test_migrations.py`.

- [ ] **Step 9: Ruff, prove the disjointness, then commit**

```bash
docker compose -p pcs1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs1-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-p-configsafety-t1-ruff.log"
docker rm pcs1-ruff
git diff --stat HEAD -- src/ tests/ frontend/
```

**Read the ruff log.** Expected: `All checks passed!`. The `git diff --stat` must list
exactly `src/autoposter/api/routes.py`, `src/autoposter/config/overrides.py` and
`tests/test_api_config_editor.py` — **and must NOT list
`src/autoposter/config/schema.py`** (Global Constraint 3). Then:

```bash
git add src/autoposter/api/routes.py src/autoposter/config/overrides.py tests/test_api_config_editor.py
git commit --no-gpg-sign -m "fix(config): refuse a mis-shaped or destructive overrides write

The 2026-09-01 wipe: a PUT whose body was the overrides document sent bare
matched zero declared fields under pydantic's default extra=\"ignore\", bound
document to {}, and the unconditional upsert wrote it over seventeen stored
overrides with a 200. The envelope now forbids extras, an empty or heavily
dropping write needs confirm: true, and the audit row carries path counts."
docker compose -p pcs1 down
```

**Task 1 report must record:** the resolved `git rev-parse HEAD` of the cut, both
Step 1 baseline totals, the new backend total (baseline + 14), the actual RED split
from Step 3, and any pre-existing test that needed the `confirm: True` amendment in
Step 7 (with the reason).

---
# Task 2: The revision token, the loud 409, and the four re-seeding pages

This task closes the **UI half** of the incident (defect D2, the whole-document lost
update) and has the widest frontend blast radius in the phase. Four pages each hold a
mount-time seed of the whole document that nothing invalidates when a *different* page,
tab or raw fetch writes; each then PUTs its stale seed plus one edit over everything
added since, and gets a 200. Guard 4 is the eighth clause the frontend contract needs
and does not have.

**Files:**
- Modify: `src/autoposter/config/overrides.py` (add `document_revision`, `EMPTY_DOCUMENT_REVISION`, `for_update` on `load_overrides_document`)
- Modify: `src/autoposter/api/routes.py` (`OverridesBody.expected_revision`; the compare in `_persist_and_swap`; `overrides_revision` on `GET /api/config` and on the save response)
- Modify: `tests/test_api_config_editor.py`
- Modify: `tests/test_config_overrides.py`
- Modify: `frontend/src/api/overrides.ts`, `frontend/src/api/overrides.test.ts`
- Modify: `frontend/src/api/types.ts` (`ConfigSaveResponse.overrides_revision`)
- Modify: `frontend/src/pages/Settings.tsx`, `frontend/src/pages/CatalogPanel.tsx`, `frontend/src/pages/GroupsPanel.tsx`, `frontend/src/pages/CustomCollectionsPanel.tsx`
- Test: `frontend/src/pages/Settings.test.tsx`, `frontend/src/pages/CatalogPanel.test.tsx`, `frontend/src/pages/GroupsPanel.test.tsx`, `frontend/src/pages/CustomCollectionsPanel.test.tsx`

**Interfaces:**
- Consumes: everything in T1's **Produces** block; `frontend/src/api/overrides.ts`'s `documentFromConfig`, `fieldErrors`; `frontend/src/api/client.ts`'s `ApiError` (`.status`, `.detail`).
- Produces:
  - `autoposter.config.overrides.document_revision(document: dict) -> str`
  - `autoposter.config.overrides.EMPTY_DOCUMENT_REVISION: str` — the sha256 of `{}` under this project's canonical dump, `"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"`
  - `autoposter.config.overrides.load_overrides_document(session, *, for_update: bool = False)`
  - `OverridesBody.expected_revision: str | None = None`
  - `_persist_and_swap(..., *, expected_revision: str | None = None, confirm: bool = False, reason: str = "save")`, returning `{version_before, version_after, restart_required, inert, overrides_revision}`
  - `GET /api/config` → `overrides_revision` (a string, always present)
  - `revisionFromConfig`, `saveBody`, `STALE_SAVE_NOTE` in `frontend/src/api/overrides.ts`

- [ ] **Step 1: Write the failing backend tests — the D2 reproduction and the token mechanics**

Append to the end of `tests/test_api_config_editor.py`. The reproduction is driven
through the real endpoints with two independent clients standing in for two mounted
pages; nothing is stubbed and no helper is called directly.

```python
# --- Config safety: the revision token and the loud 409 ------------------
#
# Defect D2 (.superpowers/sdd/progress.md:3853): four pages each seed the WHOLE
# document at mount and PUT the whole result. The seed is refreshed only by
# that page's own save, so a page holding a mount-time seed writes its stale
# document over everything a different page added since -- with a 200. That is
# the operator's own observation: a Settings save of
# `collections.separator_style: sand` did not stick, and the identical second
# save did, because nothing stale followed it.


async def _revision(client, auth_headers) -> str:
    """What a page seeding from GET /api/config carries away with the seed."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    return body["overrides_revision"]


async def test_the_two_page_stale_save_is_refused_instead_of_clobbering(
    client, auth_headers, session
):
    """T0 page A mounts. T1 page B saves separator_style. T2 page A saves.

    Before the fix, T2 answered 200 and separator_style was gone -- and not
    even listed in overridden_paths, so the page had nothing to show the
    operator. It must now be a 409 that says what happened.
    """
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    # T0: page A mounts and seeds. It is now holding the document as of now.
    page_a_seed = deepcopy(THE_INCIDENT_DOCUMENT)
    page_a_revision = await _revision(client, auth_headers)

    # T1: page B, mounted from the same state, saves one field.
    page_b = deepcopy(THE_INCIDENT_DOCUMENT)
    page_b["collections"]["separator_style"] = "sand"
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": page_b, "expected_revision": page_a_revision},
    )
    assert response.status_code == 200, response.text

    # T2: page A saves anything at all, against the seed it took at T0.
    page_a_seed["workers"] = 11
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": page_a_seed, "expected_revision": page_a_revision},
    )

    assert response.status_code == 409, (
        "a stale whole-document write was accepted. It silently deletes every "
        "override added since the writer mounted -- this is the incident's UI half"
    )
    detail = response.json()["detail"]
    assert detail["current_revision"] == await _revision(client, auth_headers)
    assert "collections.separator_style" in detail["changed_paths"]

    # T3: and the field is still there, which is the whole point.
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored["collections"]["separator_style"] == "sand"


async def test_a_save_against_a_fresh_seed_is_untouched_by_the_revision_check(
    client, auth_headers
):
    """"UI saves generally working": neither defect fires when one page saves
    against a seed nothing has moved under."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    revision = await _revision(client, auth_headers)

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": edited, "expected_revision": revision},
    )
    assert response.status_code == 200, response.text


async def test_a_client_that_sends_no_revision_is_not_broken_by_the_upgrade(
    client, auth_headers
):
    """A scripted client predates the token. Absent means "proceed" -- the
    frontend is held to sending it by its own tests, not by this endpoint."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": edited}
    )
    assert response.status_code == 200, response.text


async def test_the_revision_is_a_content_hash_not_a_timestamp(
    client, auth_headers
):
    """Two writers that independently produced the same document are not in
    conflict, and a no-op rewrite does not invalidate anybody's seed.

    `updated_at` is the obvious candidate and is wrong twice over: it moves on
    a no-op rewrite, and this project has a recorded environment whose
    container clock steps *backwards*, which would make a timestamp token go
    backwards.
    """
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    first = await _revision(client, auth_headers)

    # The same document written again -- a real write, a new updated_at.
    await _store(client, auth_headers, deepcopy(THE_INCIDENT_DOCUMENT))
    assert await _revision(client, auth_headers) == first

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": edited, "expected_revision": first},
    )
    assert await _revision(client, auth_headers) != first


async def test_the_empty_store_serves_the_empty_document_revision(
    client, auth_headers
):
    """A page that mounts against a fresh deployment gets a real token, not a
    null the four pages would each have to special-case."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["overrides_revision"] == EMPTY_DOCUMENT_REVISION


async def test_the_save_response_carries_the_revision_it_just_wrote(
    client, auth_headers
):
    """So a page can re-seed from its own write even if the follow-up GET
    fails -- and so the two never disagree about what was stored."""
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": THE_INCIDENT_DOCUMENT},
    )
    assert response.json()["overrides_revision"] == await _revision(
        client, auth_headers
    )


async def test_the_apply_arm_checks_the_revision_too(client, auth_headers):
    """Both write arms funnel through _persist_and_swap, so one insertion
    covers both -- and a test says so, because "both" is the claim."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    stale = EMPTY_DOCUMENT_REVISION

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.post(
        "/api/config/apply",
        headers=auth_headers,
        json={"document": edited, "expected_revision": stale},
    )
    assert response.status_code == 409


async def test_the_preview_accepts_the_revision_and_ignores_it(
    client, auth_headers
):
    """All three arms take one body shape. A preview that 409'd would be
    refusing to answer "what would this do" for the one case where the
    operator most needs to know."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.post(
        "/api/config/preview",
        headers=auth_headers,
        json={"document": edited, "expected_revision": EMPTY_DOCUMENT_REVISION},
    )
    assert response.status_code == 200, response.text


async def test_a_stale_save_writes_nothing_at_all(
    client, auth_headers, session, app
):
    """Never half-apply, on the 409 path too: no row change, no swap, no audit
    event for the refused write."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    events_before = len(
        (
            await session.execute(
                select(EventLog).where(EventLog.event_type == "overrides_updated")
            )
        )
        .scalars()
        .all()
    )

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": edited, "expected_revision": EMPTY_DOCUMENT_REVISION},
    )

    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT
    assert app.state.config.workers == 9
    events_after = len(
        (
            await session.execute(
                select(EventLog).where(EventLog.event_type == "overrides_updated")
            )
        )
        .scalars()
        .all()
    )
    assert events_after == events_before


async def test_an_unknown_field_on_the_body_is_still_refused(
    client, auth_headers
):
    """The envelope's forbid-extras survives the two new fields -- a typo'd
    `expected_version` must not be silently ignored, which would put the
    sender straight back in the incident."""
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {"workers": 9}, "expected_version": "whatever"},
    )
    assert response.status_code == 422
```

Add `EMPTY_DOCUMENT_REVISION` to the file's imports, beside the existing
`from autoposter.api.routes import KEEP_SENTINEL`:

```python
from autoposter.config.overrides import EMPTY_DOCUMENT_REVISION
```

- [ ] **Step 2: Run the backend tests to verify they fail**

```bash
docker compose -p pcs2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs2-red1 test sh -c \
  "set -o pipefail; pytest -q tests/test_api_config_editor.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t2-red1.log"
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-t2-red1.log`.**

Expected: a **collection error**, not a run — `ImportError: cannot import name
'EMPTY_DOCUMENT_REVISION'`. That is the correct first RED for a task whose first
deliverable is a new name. Write `document_revision` and `EMPTY_DOCUMENT_REVISION`
(Step 3's first half) and re-run this exact command; the second RED is the one to
read carefully. Expected then: **9 failed, 2 passed** —
`…no_revision_is_not_broken…` and `…preview_accepts_the_revision_and_ignores_it`
pass (nothing reads the field yet, and `extra="forbid"` will reject it until the
field is declared — so if these two ERROR with a 422 instead, declare
`expected_revision` on the model first and re-run). Every other new test fails with
`KeyError: 'overrides_revision'` or `assert 200 == 409`.

Then `docker rm pcs2-red1`.

- [ ] **Step 3: The revision function and the locked read**

In `src/autoposter/config/overrides.py`, add to the imports at the top:

```python
import hashlib
import json
```

and, immediately below `MIGRATED_SECTIONS` (`:46`):

```python
def document_revision(document: dict) -> str:
    """A token identifying exactly this document's *content*.

    Sent to every editor with its seed and sent back with its save, so the
    write path can refuse a document composed against a store that has since
    moved (the 2026-09-01 lost update). The alternative token -- the row's own
    ``updated_at`` -- is wrong twice over. It moves on a no-op rewrite, so an
    unchanged document would invalidate every open page for nothing; and this
    project has a recorded deployment whose container clock steps ~2.7s
    backwards every ~27s, which would make a timestamp token travel backwards.
    A content hash has neither problem and has the right semantics besides:
    two writers that independently produced the same document are not in
    conflict, because there is nothing to lose.

    Canonical dump -- sorted keys, no whitespace -- so the token depends on
    what the document says and not on how it was serialised on the way in.
    """
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: What a fresh deployment serves. A real token rather than null, so an editor
#: seeding against an empty store carries the same shape as every other one.
EMPTY_DOCUMENT_REVISION = document_revision({})
```

Then give `load_overrides_document` its lock (`:187`):

```python
async def load_overrides_document(
    session: AsyncSession, *, for_update: bool = False
) -> dict:
```

and, inside it, replace the `select` with:

```python
    statement = select(ConfigOverride).where(ConfigOverride.id == OVERRIDES_ROW_ID)
    if for_update:
        # The write path reads and compares and writes as one step. Without the
        # lock, two overlapping transactions can both read the same document,
        # both find their expected revision current, and both write -- which is
        # the lost update this whole check exists to stop, just narrower.
        statement = statement.with_for_update()
    row = await session.scalar(statement)
```

Add to the docstring, after the existing final paragraph:

```
    ``for_update`` takes a row lock, and only ``_persist_and_swap`` passes it:
    a reader that locked would serialise ``GET /api/config`` behind every save
    for no benefit. One honest limit: when no row exists yet there is nothing
    to lock, so two *simultaneous* first-ever saves on a fresh deployment can
    still race. The upsert keeps the row consistent either way, both writers
    legitimately saw ``{}``, and the drop cap bounds what the loser can lose --
    so this is a documented residual, not a silent one.
```

- [ ] **Step 4: The token on the body, the compare on the write path, the token on the responses**

`OverridesBody` (from T1) gains one field and one docstring paragraph:

```python
    model_config = ConfigDict(extra="forbid")

    document: dict = Field(default_factory=dict)
    expected_revision: str | None = None
    confirm: bool = False
```

```
    ``expected_revision`` is the token that came with the seed this document
    was composed from (``GET /api/config``'s ``overrides_revision``). When it
    is present and no longer current, the write is refused with a 409 rather
    than overwriting whatever arrived in between. When it is absent the write
    proceeds, so a scripted client is not broken by this upgrade; the four
    pages are held to sending it by their own tests, not by this model.
```

In `_persist_and_swap`, the signature and the first lines of the session block:

```python
async def _persist_and_swap(
    request: Request,
    document: dict,
    after: Config,
    *,
    expected_revision: str | None = None,
    confirm: bool = False,
    reason: str = "save",
) -> dict:
```

```python
    before = request.app.state.config
    async with request.app.state.session_factory() as session:
        stored = await load_overrides_document(session, for_update=True)
        if expected_revision is not None:
            current = document_revision(stored)
            if expected_revision != current:
                # Loudly, and without a suggestion to retry: a client that
                # retried would re-apply an edit onto a document its operator
                # has not seen, which is a quieter version of the bug this
                # check exists to stop.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "these settings changed somewhere else while this "
                            "page was open; nothing was saved"
                        ),
                        "current_revision": current,
                        "changed_paths": sorted(_changed_paths(stored, document)),
                    },
                )
        if not confirm:
            refusal = _drop_refusal(stored, document)
            ...
```

(the rest of the function is exactly as T1 left it, except the return dict:)

```python
    return {
        "version_before": before.version,
        "version_after": after.version,
        "restart_required": restart_required,
        "inert": inert,
        "overrides_revision": document_revision(document),
    }
```

Add the paragraph to the function's docstring, after the "Guards that fire on a write
belong on the write path" paragraph:

```
    The revision compare goes here, under the row lock, for the same reason and
    one more: a check in ``_validated_generation`` would 409 a preview, and a
    check in a separate earlier session would be a TOCTOU window inside the fix
    for a TOCTOU bug. ``SELECT ... FOR UPDATE`` is what makes read-compare-write
    one step; the unconditional upsert it replaced was not.
```

Update the two callers to pass the token:

```python
    document, after = await _validated_generation(request, body.document)
    return await _persist_and_swap(
        request,
        document,
        after,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
    )
```

and in `apply_config_overrides`:

```python
    saved = await _persist_and_swap(
        request,
        document,
        after,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
        reason="apply",
    )
```

Serve the token on `GET /api/config` — in `get_config`, immediately after the existing
`body["overridden_paths"] = sorted(document_paths(document))` (`routes.py:1358`):

```python
    # Computed from the same document `overridden_paths` came from: one extra
    # hash, no extra query, and it arrives with the seed -- which is exactly
    # the invariant a stale-write check needs, because a page that seeded from
    # this response holds this token for what it seeded from.
    body["overrides_revision"] = document_revision(document)
```

Add `document_revision` and `EMPTY_DOCUMENT_REVISION` to the `autoposter.config.overrides`
import block at `routes.py:53-59`:

```python
from autoposter.config.overrides import (
    OVERRIDES_ROW_ID,
    document_paths,
    document_revision,
    load_overrides_document,
    merge_overrides,
    unknown_key_paths,
    without_migrated_sections,
)
```

(`without_migrated_sections` is imported here now so T3's restore endpoint has it in
scope; it is unused until then, so add it in T3 rather than here if ruff's F401 fires.)

Extend `get_config`'s docstring — the paragraph beginning "Carries seven things the
editor needs" becomes "Carries eight things", and add at the end of that paragraph
block:

```
    ``overrides_revision`` is the eighth and the newest: a content hash of the
    stored overrides document, which every editor carries away with its seed
    and sends back with its save. It is what lets the write path tell a save
    composed against *this* document apart from one composed against a document
    that has since moved -- the difference between a save and a silent deletion
    of everything another page added in between.
```

- [ ] **Step 5: Run the backend tests to verify they pass**

```bash
docker compose -p pcs2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs2-green1 test sh -c \
  "set -o pipefail; pytest -q tests/test_api_config_editor.py tests/test_config_overrides.py tests/test_adopt_config.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t2-green1.log"
docker rm pcs2-green1
```

**Read the log.** Expected: **0 failed**, `test_api_config_editor.py` up by **11**
from its Task 1 total. If a `tests/test_config_overrides.py` test asserts
`load_overrides_document`'s signature positionally, it is unaffected — the new
parameter is keyword-only.

- [ ] **Step 6: Add the revision unit test for the hash itself**

Append to `tests/test_config_overrides.py`:

```python
def test_the_document_revision_is_stable_across_key_order_and_whitespace():
    """The token is about what the document says, not how it was serialised.
    A page that rebuilt the same deltas in a different order must not be told
    its seed is stale."""
    from autoposter.config.overrides import document_revision

    one = {"badges": {"enabled": True}, "workers": 9}
    other = {"workers": 9, "badges": {"enabled": True}}
    assert document_revision(one) == document_revision(other)


def test_the_empty_document_revision_is_the_pinned_constant():
    """Pinned by value, not by re-deriving it: a change to the canonical dump
    would invalidate every seed every open page is holding, and that must be a
    deliberate act with a red test in front of it."""
    from autoposter.config.overrides import (
        EMPTY_DOCUMENT_REVISION,
        document_revision,
    )

    assert EMPTY_DOCUMENT_REVISION == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )
    assert document_revision({}) == EMPTY_DOCUMENT_REVISION


def test_a_changed_value_changes_the_revision():
    from autoposter.config.overrides import document_revision

    assert document_revision({"workers": 9}) != document_revision({"workers": 10})
```

Run them:

```bash
docker compose -p pcs2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs2-green2 test sh -c \
  "set -o pipefail; pytest -q tests/test_config_overrides.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t2-green2.log"
docker rm pcs2-green2
```

**Read the log.** Expected: **0 failed**, up by 3.

- [ ] **Step 7: Commit the backend half**

```bash
git add src/autoposter/config/overrides.py src/autoposter/api/routes.py tests/test_api_config_editor.py tests/test_config_overrides.py
git commit --no-gpg-sign -m "fix(config): refuse a save composed against a stale overrides document

The incident's UI half: four pages each hold a mount-time seed of the whole
document, nothing invalidates it when another page writes, and the
unconditional upsert accepted the stale document with a 200 -- silently
deleting every override added in between. GET /api/config now serves a
content-hash revision, the write path compares it under SELECT FOR UPDATE,
and a mismatch is a 409 naming the paths that moved."
```

- [ ] **Step 8: Write the failing frontend helper tests**

Append to `frontend/src/api/overrides.test.ts`:

```ts
describe("the revision a page carries with its seed", () => {
  it("reads the revision the config response served", () => {
    expect(revisionFromConfig({ overrides_revision: "abc" })).toBe("abc");
  });

  it("reads null from a response that has no revision, rather than throwing", () => {
    // A response from before the field existed. The save then omits the key
    // and the server proceeds -- the same posture the endpoint takes.
    expect(revisionFromConfig({})).toBeNull();
    expect(revisionFromConfig({ overrides_revision: "" })).toBeNull();
    expect(revisionFromConfig({ overrides_revision: 7 })).toBeNull();
  });

  it("sends the revision alongside the document, wrapped", () => {
    expect(JSON.parse(saveBody({ workers: 9 }, "abc"))).toEqual({
      document: { workers: 9 },
      expected_revision: "abc",
    });
  });

  it("omits the key entirely when there is no revision to send", () => {
    // Not `expected_revision: null` -- the body model types it as
    // `str | None` and null would read as "I have no seed", which is exactly
    // what it means, but omitting is what the four pages' tests assert and
    // what an older client sends. One shape, not two.
    const body = JSON.parse(saveBody({ workers: 9 }, null));
    expect(body).toEqual({ document: { workers: 9 } });
    expect("expected_revision" in body).toBe(false);
  });

  it("never tells the operator to retry", () => {
    // A retry would re-apply the edit onto a document they have not seen.
    expect(STALE_SAVE_NOTE).not.toMatch(/try again|retry/i);
    expect(STALE_SAVE_NOTE).toMatch(/nothing was saved/i);
  });
});
```

Extend that file's import to `import { …, revisionFromConfig, saveBody, STALE_SAVE_NOTE } from "./overrides";` alongside whatever it already imports.

- [ ] **Step 9: Run the helper tests to verify they fail**

```bash
docker compose -p pcs2 run --name pcs2-red2 web npm test -- overrides > .superpowers/run-p-configsafety-t2-red2.log 2>&1
docker rm pcs2-red2
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-t2-red2.log`.** Expected:
a transform/resolve failure naming `revisionFromConfig`, `saveBody` and
`STALE_SAVE_NOTE` as missing exports. That is the RED for a step whose deliverable is
three new exports.

- [ ] **Step 10: Write the three helpers**

Append to `frontend/src/api/overrides.ts`:

```ts
/** The revision the server served with this seed, or null when it served none.
 *
 * Null rather than a throw for a response that predates the field: an older
 * deployment's `GET /api/config` is a perfectly usable seed, and a page that
 * refused to save against one would be a worse failure than the one this
 * guards. `saveBody` then omits the key and the endpoint proceeds. */
export function revisionFromConfig(config: ConfigResponse): string | null {
  const revision = config.overrides_revision;
  return typeof revision === "string" && revision !== "" ? revision : null;
}

/** The body every writing page sends, built in exactly one place.
 *
 * The four pages that write this document each seed the WHOLE of it at mount
 * and PUT the whole result, so each of them can silently delete what the other
 * three saved. The seed's revision is what makes that a 409 instead. Built
 * here rather than per page for the reason this file exists at all: the page
 * that copied the first one badly is the page that would leave the key off and
 * put its operator straight back in the 2026-09-01 incident. */
export function saveBody(
  document: OverridesDocument,
  revision: string | null,
): string {
  return JSON.stringify(
    revision === null ? { document } : { document, expected_revision: revision },
  );
}

/** What the operator is told when the server refuses a stale save.
 *
 * Three facts and no instruction to retry: what happened, that nothing was
 * stored, and that the page in front of them is now showing the real settings.
 * An automatic retry would re-apply their edit onto a document they have never
 * seen, which is the same lost update wearing a friendlier face. */
export const STALE_SAVE_NOTE =
  "These settings were changed somewhere else while this page was open, so " +
  "nothing was saved. The page now shows the current settings — make your " +
  "change again.";
```

Add `overrides_revision` to `frontend/src/api/types.ts`'s `ConfigSaveResponse`:

```ts
export interface ConfigSaveResponse {
  version_before: string;
  version_after: string;
  restart_required: string[];
  inert?: string[];
  /** The revision of the document this write stored — the token the next save
   * from this page must carry. Optional so a response from before it existed
   * still parses; a page that gets none falls back to the re-read GET. */
  overrides_revision?: string;
}
```

- [ ] **Step 11: Run the helper tests to verify they pass**

```bash
docker compose -p pcs2 run --name pcs2-green3 web npm test -- overrides > .superpowers/run-p-configsafety-t2-green3.log 2>&1
docker rm pcs2-green3
```

**Read the log.** Expected: **0 failed**, up by 5.

- [ ] **Step 12: Write the failing page tests — clause 8 and the 409 recovery, four times**

Four pages, four near-identical pairs. They are written out in full rather than
cross-referenced, because the implementer may be reading them out of order and because
each page's harness differs.

**`frontend/src/pages/Settings.test.tsx`** — append a new `describe` at the end, and
extend the file's imports to include `STALE_SAVE_NOTE` from `../api/overrides`:

```tsx
describe("Settings stale-save recovery", () => {
  const SEEDED = { ...EDITOR_CONFIG, overrides_revision: "rev-1" };

  it("sends the revision it seeded from with every write and every preview", async () => {
    // Clause 8 of the frontend's contract. Without it this page can still
    // delete what CatalogPanel, GroupsPanel or a second tab just saved.
    const fetchMock = stubEditor({
      config: SEEDED,
      responses: {
        "/api/config/overrides": json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
          overrides_revision: "rev-2",
        }),
        "/api/config/preview": previewBody(null),
      },
    });
    await renderSettings();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await click("Preview");
    expect(callTo(fetchMock, "/api/config/preview").body).toEqual({
      document: { workers: 9 },
      expected_revision: "rev-1",
    });

    await save();
    expect(callTo(fetchMock, "/api/config/overrides").body).toEqual({
      document: { workers: 9 },
      expected_revision: "rev-1",
    });
  });

  it("tells the operator and re-seeds when the server refuses a stale save", async () => {
    // The second GET serves the state another page wrote, which is what the
    // page must end up showing -- not the operator's discarded edit.
    const moved = {
      ...EDITOR_CONFIG,
      workers: 5,
      badges: { enabled: false },
      overridden_paths: ["badges.enabled"],
      overrides_revision: "rev-9",
    };
    let served: unknown = SEEDED;
    const fetchMock = vi.fn((input: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") return Promise.resolve(json(served));
      served = moved;
      return Promise.resolve(
        json(
          {
            message: "these settings changed somewhere else",
            current_revision: "rev-9",
            changed_paths: ["badges.enabled"],
          },
          409,
        ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    await renderSettings();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();

    // Said, not swallowed -- and said outside the pending panel, which the
    // re-seed makes disappear.
    expect(screen.getByText(STALE_SAVE_NOTE)).toBeInTheDocument();
    // Re-seeded from the server, not from what was typed.
    expect(screen.getByLabelText("workers")).toHaveValue(5);
    // And NOT retried: exactly one write left this page.
    expect(
      fetchMock.mock.calls.filter(([, init]) => (init?.method ?? "GET") !== "GET"),
    ).toHaveLength(1);
  });

  it("carries the new revision into the next save after a successful one", async () => {
    // Otherwise the page's second save is stale against its own first, and
    // every page would 409 itself on the second click.
    const after = { ...EDITOR_CONFIG, overrides_revision: "rev-2" };
    let served: unknown = SEEDED;
    const fetchMock = vi.fn((input: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") return Promise.resolve(json(served));
      served = after;
      return Promise.resolve(
        json({
          version_before: "abc123",
          version_after: "def456",
          restart_required: [],
          overrides_revision: "rev-2",
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    await renderSettings();

    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "9" } });
    await save();
    fireEvent.change(screen.getByLabelText("workers"), { target: { value: "11" } });
    await save();

    const writes = fetchMock.mock.calls.filter(
      ([, init]) => (init?.method ?? "GET") !== "GET",
    );
    expect(JSON.parse(String(writes[1][1]?.body)).expected_revision).toBe("rev-2");
  });
});
```

**`frontend/src/pages/CatalogPanel.test.tsx`** — append at the end of the file, and
import `STALE_SAVE_NOTE` from `../api/overrides`:

```tsx
describe("CatalogPanel stale-save recovery", () => {
  it("sends the revision it seeded from", async () => {
    const { puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
    });
    await toggleFirstKey();

    expect(JSON.parse(String(puts[0].body)).expected_revision).toBe("rev-1");
  });

  it("tells the operator and re-reads when the server refuses a stale save", async () => {
    const { fetchMock, puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
      save: () =>
        json(
          {
            message: "these settings changed somewhere else",
            current_revision: "rev-9",
            changed_paths: ["collections.separator_style"],
          },
          409,
        ),
    });
    await toggleFirstKey();

    expect(screen.getByText(STALE_SAVE_NOTE)).toBeInTheDocument();
    expect(puts).toHaveLength(1);
    // Re-read, so the panel is showing the settings that actually hold.
    expect(
      fetchMock.mock.calls.filter(([path]) => path === "/api/config"),
    ).toHaveLength(2);
  });
});
```

`toggleFirstKey` is this file's existing "click a category and save" helper — read the
file and use whatever it is actually called (`grep -n "async function" frontend/src/pages/CatalogPanel.test.tsx`),
rather than adding a second one. If no such helper exists, do the click and the save
inline with `fireEvent.click(screen.getByRole("checkbox", …))` followed by the file's
existing save-button click, matching a neighbouring test in the same file.

**`frontend/src/pages/GroupsPanel.test.tsx`** — the same pair, against this panel's own
harness (identical `stubFetch`/`renderPanel`/`sentDocument` shape):

```tsx
describe("GroupsPanel stale-save recovery", () => {
  it("sends the revision it seeded from", async () => {
    const { puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
    });
    await saveOrder();

    expect(JSON.parse(String(puts[0].body)).expected_revision).toBe("rev-1");
  });

  it("tells the operator and re-reads when the server refuses a stale save", async () => {
    const { fetchMock, puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
      save: () =>
        json(
          {
            message: "these settings changed somewhere else",
            current_revision: "rev-9",
            changed_paths: ["collections.separator_style"],
          },
          409,
        ),
    });
    await saveOrder();

    expect(screen.getByText(STALE_SAVE_NOTE)).toBeInTheDocument();
    expect(puts).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter(([path]) => path === "/api/config"),
    ).toHaveLength(2);
  });
});
```

`saveOrder` is this file's existing move-then-save helper; find it the same way.

**`frontend/src/pages/CustomCollectionsPanel.test.tsx`** — the same pair again:

```tsx
describe("CustomCollectionsPanel stale-save recovery", () => {
  it("sends the revision it seeded from", async () => {
    const { puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
    });
    await createDefinition();

    expect(JSON.parse(String(puts[0].body)).expected_revision).toBe("rev-1");
  });

  it("tells the operator and re-reads when the server refuses a stale save", async () => {
    const { fetchMock, puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
      save: () =>
        json(
          {
            message: "these settings changed somewhere else",
            current_revision: "rev-9",
            changed_paths: ["collections.definitions"],
          },
          409,
        ),
    });
    await createDefinition();

    expect(screen.getByText(STALE_SAVE_NOTE)).toBeInTheDocument();
    expect(puts).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter(([path]) => path === "/api/config"),
    ).toHaveLength(2);
  });
});
```

`createDefinition` is this file's existing parse-then-create helper.

- [ ] **Step 13: Run the page tests to verify they fail**

```bash
docker compose -p pcs2 run --name pcs2-red3 web npm test -- Settings CatalogPanel GroupsPanel CustomCollectionsPanel > .superpowers/run-p-configsafety-t2-red3.log 2>&1
docker rm pcs2-red3
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-t2-red3.log`.**
Expected: **11 failed**. The "sends the revision it seeded from" four fail on the
body not carrying `expected_revision` (`undefined` vs `"rev-1"`); the four 409 tests
fail on `STALE_SAVE_NOTE` not being in the document (the pages currently surface a 409
through the generic `(caught as Error).message` arm, which renders the ApiError's
message, not this note); Settings' "carries the new revision" fails on
`undefined` vs `"rev-2"`. Two of the Settings three may fail on the *first*
assertion instead — read the log and confirm each failure names the revision or the
note before implementing.

- [ ] **Step 14: Wire Settings.tsx**

Extend the import from `../api/overrides`:

```tsx
import {
  documentFromConfig,
  fieldErrors,
  hasPath,
  isPlainObject,
  keepContract,
  revisionFromConfig,
  saveBody,
  STALE_SAVE_NOTE,
  withPath,
  withoutPath,
} from "../api/overrides";
```

(keep whatever names the file already imports; add the three new ones.)

Two new pieces of state, beside the existing ones (`Settings.tsx:561-569`):

```tsx
  // The revision of the document `savedDocument` was seeded from. Sent with
  // every write so the server can refuse one composed against a document that
  // has since moved -- the whole of clause 8.
  const [storedRevision, setStoredRevision] = useState<string | null>(null);
  // Deliberately NOT `saveError`: that one lives inside the pending panel,
  // and a stale save is followed by a re-seed that makes the pending panel
  // disappear. The operator would be told nothing at all.
  const [staleNote, setStaleNote] = useState<string | null>(null);
```

`adopt` takes the revision with the seed — the two must never be set apart:

```tsx
  const adopt = useCallback((response: ConfigResponse) => {
    const stored = documentFromConfig(response);
    setConfig(response);
    setSavedDocument(stored);
    setPendingDocument(stored);
    setStoredRevision(revisionFromConfig(response));
  }, []);
```

`submit` clears the note, sends the token, and handles the 409:

```tsx
  async function submit(action: Action) {
    setBusy(action);
    setErrors({});
    setSaveError(null);
    setStaleNote(null);
    setResult(null);
    const body = saveBody(pendingDocument, storedRevision);
    try {
      if (action === "preview") {
        ...unchanged...
      }
      ...unchanged...
      adopt(await apiFetch<ConfigResponse>("/api/config"));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        // Never a retry. Re-seed from the server and hand the operator back a
        // page that tells the truth, with their edit discarded and said so.
        setStaleNote(STALE_SAVE_NOTE);
        try {
          adopt(await apiFetch<ConfigResponse>("/api/config"));
        } catch (reread) {
          setSaveError((reread as Error).message);
        }
      } else if (caught instanceof ApiError && caught.status === 422) {
        setErrors(fieldErrors(caught.detail));
        setSaveError("The server rejected these settings.");
      } else {
        setSaveError((caught as Error).message);
      }
    } finally {
      setBusy(null);
    }
  }
```

Render the note in the **Running configuration** section, immediately after the
`{config === null && <p className="muted">Loading…</p>}` line (`Settings.tsx:720`) —
outside the `dirty` gate, which is the point:

```tsx
        {staleNote !== null && <p className="page-error">{staleNote}</p>}
```

- [ ] **Step 15: Wire the three panels**

All three take the same three changes. They are written out per file because the
surrounding names differ.

**`frontend/src/pages/CatalogPanel.tsx`.** Extend the `../api/overrides` import with
`revisionFromConfig, saveBody, STALE_SAVE_NOTE`. Add the state beside `stored`:

```tsx
  const [storedRevision, setStoredRevision] = useState<string | null>(null);
```

In `adopt` (`:270-279`), beside `setStored(documentFromConfig(config))`:

```tsx
      setStoredRevision(revisionFromConfig(config));
```

In `save()` (`:358-...`), the body and the catch:

```tsx
      const response = await apiFetch<ConfigSaveResponse>("/api/config/overrides", {
        method: "PUT",
        body: saveBody(documentToSave(stored, categories!, chosen), storedRevision),
      });
```

```tsx
    } catch (caught) {
      if (live.current) {
        if (caught instanceof ApiError && caught.status === 409) {
          // Not retried: re-read, and say what happened. `stale` makes the
          // re-read below run even though nothing was saved -- the panel is
          // showing a document that is no longer true.
          setSaveError(STALE_SAVE_NOTE);
          stale = true;
        } else if (caught instanceof ApiError && caught.status === 422) {
          setErrors(fieldErrors(caught.detail));
          setSaveError("The server rejected these choices.");
        } else {
          setSaveError((caught as Error).message);
        }
      }
    }
```

Declare `let stale = false;` beside the existing `let saved = false;`, and widen the
re-read gate:

```tsx
    if (saved || stale) {
```

**`frontend/src/pages/GroupsPanel.tsx`.** The same three changes: the import; the
`storedRevision` state and its `setStoredRevision(revisionFromConfig(config))` inside
`adopt` (`:140-150`); and inside `put(document)` (`:219-...`):

```tsx
      const response = await apiFetch<ConfigSaveResponse>("/api/config/overrides", {
        method: "PUT",
        body: saveBody(document, storedRevision),
      });
```

```tsx
        if (caught instanceof ApiError && caught.status === 409) {
          setSaveError(STALE_SAVE_NOTE);
          stale = true;
        } else if (caught instanceof ApiError && caught.status === 422) {
          setErrors(fieldErrors(caught.detail));
          setSaveError("The server rejected this order.");
        } else {
```

with `let stale = false;` beside `let saved = false;` and `if (saved || stale) {` on
the re-read.

**`frontend/src/pages/CustomCollectionsPanel.tsx`.** The same three changes: the
import; `setStoredRevision(revisionFromConfig(config))` inside `adopt` (`:229-251`);
and in `put(document, key)` (`:365-...`):

```tsx
      const response = await apiFetch<ConfigSaveResponse>("/api/config/overrides", {
        method: "PUT",
        body: saveBody(document, storedRevision),
      });
```

```tsx
        if (caught instanceof ApiError && caught.status === 409) {
          setSaveError(STALE_SAVE_NOTE);
          stale = true;
        } else if (caught instanceof ApiError && caught.status === 422) {
          setErrors(fieldErrors(caught.detail));
          setSaveError("The server rejected this change.");
        } else {
```

with `let stale = false;`, `if (saved || stale) {` on the re-read, and — inside that
block — the existing form-clearing branch stays gated on `saved`, not on `stale`: a
save that did not happen must not clear the form the operator would otherwise have to
re-type.

This panel's `check()` also sends a document to `/api/config/preview`. Change its body
to `saveBody(documentForCreate(stored, entry()), storedRevision)` so the three arms
keep one shape — the preview accepts the token and ignores it.

- [ ] **Step 16: Run the page tests to verify they pass**

```bash
docker compose -p pcs2 run --name pcs2-green4 web npm test -- Settings CatalogPanel GroupsPanel CustomCollectionsPanel > .superpowers/run-p-configsafety-t2-green4.log 2>&1
docker rm pcs2-green4
```

**Read the log.** Expected: **0 failed**, and the four files' totals up by **11**
between them. If a *pre-existing* test in one of these files goes red on the body now
carrying `expected_revision` (a `toEqual({ document: … })` on the whole body rather
than on `.document`), that is a real consequence of clause 8 and the fix is to widen
that assertion to the body's `document` key — never to stop sending the token. Record
each such amendment in the report.

- [ ] **Step 17: Full frontend suite, typecheck, full backend suite**

```bash
docker compose -p pcs2 run --name pcs2-vitest web npm test > .superpowers/run-p-configsafety-t2-vitest.log 2>&1
docker rm pcs2-vitest
docker compose -p pcs2 run --name pcs2-tsc web npx tsc --noEmit > .superpowers/run-p-configsafety-t2-tsc.log 2>&1
docker rm pcs2-tsc
docker compose -p pcs2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pcs2-full test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-p-configsafety-t2-full.log"
docker wait pcs2-full
docker rm pcs2-full
docker compose -p pcs2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs2-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-p-configsafety-t2-ruff.log"
docker rm pcs2-ruff
```

**Read all four logs.** Expected: vitest **0 failed**, up 16 from the T1 baseline
(5 helper + 11 page); `tsc --noEmit` silent; pytest **0 failed**, up 14 from T1's
total; ruff `All checks passed!`.

- [ ] **Step 18: Prove the disjointness, then commit the frontend half**

```bash
git diff --stat HEAD -- src/ tests/ frontend/
```

Must list only the nine frontend files in this task's **Files** block, and **must not
list `src/autoposter/config/schema.py`**. Then:

```bash
git add frontend/src/api/overrides.ts frontend/src/api/overrides.test.ts frontend/src/api/types.ts frontend/src/pages/Settings.tsx frontend/src/pages/Settings.test.tsx frontend/src/pages/CatalogPanel.tsx frontend/src/pages/CatalogPanel.test.tsx frontend/src/pages/GroupsPanel.tsx frontend/src/pages/GroupsPanel.test.tsx frontend/src/pages/CustomCollectionsPanel.tsx frontend/src/pages/CustomCollectionsPanel.test.tsx
git commit --no-gpg-sign -m "fix(settings): carry the seed's revision on every write, re-seed on a 409

All four pages that write the overrides document now send the revision they
seeded from and, when the server refuses, re-read and tell the operator their
edit was not applied. No page retries: a retry would re-apply the edit onto a
document the operator has never seen."
docker compose -p pcs2 down
```

**Task 2 report must record:** the backend and vitest totals before and after, every
pre-existing assertion widened in Step 16 (with the reason), and confirmation that
each of the four pages sends the token on its own real save path — named page by page,
not asserted in aggregate.

---
# Task 3: Pre-write snapshots, and a restore that is itself undoable

The guards stop the wipe; this is what the operator wanted when they said *"there NEEDS
to be an easy config backup/restore"*. The incident cost a hand restoration from a
document that happened to survive in a chat window and in `window.__cfg`. It must
never again depend on that.

**A table, not a file.** The metadata-backup precedent (`src/autoposter/metadata_backup.py`)
writes YAML to a configured mount because its payload is ~16,000 Plex records. The
overrides document is one small JSON object that already lives in Postgres; putting its
history on a mount would make config recovery depend on a mount being present, which is
the one failure that module says it cannot detect.

**Files:**
- Create: `src/autoposter/config/snapshots.py`
- Create: `alembic/versions/c7e1b93a4d20_config_override_snapshots.py`
- Create: `tests/test_config_safety.py`
- Modify: `src/autoposter/db/models.py` (append `ConfigOverrideSnapshot`)
- Modify: `src/autoposter/api/routes.py` (the capture inside `_persist_and_swap`; three endpoints)

**Interfaces:**
- Consumes: T1's `_persist_and_swap(..., *, expected_revision, confirm, reason)`, `_drop_refusal`, `_error`, `_REDACTORS`, `_read_path`, `_set_path`, `REDACTED_PATHS`, `_validated_generation`; T1's `without_migrated_sections`; `document_paths`.
- Produces:
  - `autoposter.db.models.ConfigOverrideSnapshot`
  - `autoposter.config.snapshots.{SNAPSHOT_RETENTION, capture_snapshot, list_snapshots, load_snapshot}`
  - `GET /api/config/snapshots` → `[{id, created_at, path_count, reason}]`, newest first
  - `GET /api/config/snapshots/{snapshot_id}` → `{id, created_at, path_count, reason, document}` with `document` redacted
  - `POST /api/config/snapshots/{snapshot_id}/restore`, body `SnapshotRestoreBody{confirm, expected_revision}` → the `_persist_and_swap` response
- T4 consumes `capture_snapshot` implicitly (via `_persist_and_swap`) and nothing else.

- [ ] **Step 1: The model and the migration**

Append to `src/autoposter/db/models.py`, immediately after `ConfigOverride`:

```python
class ConfigOverrideSnapshot(Base):
    """What the overrides document was, immediately before a write replaced it.

    History for the one row this service lets an operator destroy from a web
    page. Captured pre-write inside the writing transaction, so a snapshot
    without its write -- or a write without its snapshot -- cannot exist.

    In the database rather than on a mount, unlike ``metadata_backup``: that
    module's payload is ~16,000 Plex records and belongs on a volume, while
    this is one small JSON object that already lives here. Putting the
    configuration's own recovery path behind a mount would make it depend on
    exactly the thing an operator cannot check while the pod will not start.

    ``path_count`` is stored rather than derived so the listing endpoint can
    answer "3 settings, 14:22 today" without shipping twenty full config
    documents to a page that only needs to label its rows.
    """

    __tablename__ = "config_override_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    path_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # save | apply | restore | import -- what the write that displaced this
    # document was doing. Not nullable: every writer knows its own reason, and
    # a nullable column would only ever record that somebody forgot.
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
```

Create `alembic/versions/c7e1b93a4d20_config_override_snapshots.py`. `down_revision`
is the single current head — confirm it before writing the file:

```bash
docker compose -p pcs3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs3-heads test sh -c \
  "set -o pipefail; alembic heads 2>&1 | tee /app/.superpowers/run-p-configsafety-t3-heads.log"
docker rm pcs3-heads
```

**Read the log.** Expected: exactly one head, `f3a9c41d2b07` (the `facts_backfill_state`
revision). **If there is more than one head, STOP and report** — a second head means
another branch landed a migration and the chain needs linearising first (the repo has
an `alembic-merge-report.md` precedent). Then:

```python
"""config override snapshots

Revision ID: c7e1b93a4d20
Revises: f3a9c41d2b07
Create Date: 2026-09-03 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7e1b93a4d20'
down_revision: Union[str, Sequence[str], None] = 'f3a9c41d2b07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'config_override_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('path_count', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=16), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_config_override_snapshots_created_at'),
        'config_override_snapshots',
        ['created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_config_override_snapshots_created_at'),
        table_name='config_override_snapshots',
    )
    op.drop_table('config_override_snapshots')
```

- [ ] **Step 2: Verify the migration matches the model**

`tests/test_migrations.py` runs `alembic check` against a scratch database, so a
migration that drifts from the model goes red there. Run it now, before any endpoint
exists:

```bash
docker compose -p pcs3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs3-migrations test sh -c \
  "set -o pipefail; pytest -q tests/test_migrations.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t3-migrations.log"
docker rm pcs3-migrations
```

**Read the log.** Expected: **0 failed**. A failure here says the hand-written DDL and
the model disagree — read what `alembic check` printed and fix the migration to match
the model, never the model to match the migration.

- [ ] **Step 3: Write the failing snapshot tests**

Create `tests/test_config_safety.py`:

```python
"""Snapshots, restore, and export/import for the overrides document.

The 2026-09-01 incident: seventeen stored overrides went to zero through a
200-OK write, and the only copy that survived was one an operator happened to
have in a chat window. Everything here exists so that is never again the
recovery plan.

Two rules are load-bearing and each has a test whose failure message says which
one broke.

1. **A snapshot and its write commit together.** The capture is an insert in
   the same session and the same transaction as the upsert. A snapshot without
   its write, or a write without its snapshot, is worse than neither.
2. **Restore and import are SAVES.** Both run the full validation and every
   write guard. A restore is itself snapshotted first, so it is undoable; and a
   snapshot taken before a config section left the schema must fail loudly or
   be stripped, never brick the pod.

The fixtures are the config editor's own -- one app, one client, one login --
imported rather than rebuilt so the two files cannot drift about what a
deployment looks like.
"""
from copy import deepcopy

import pytest
from sqlalchemy import select

from autoposter.config.overrides import EMPTY_DOCUMENT_REVISION
from autoposter.config.snapshots import SNAPSHOT_RETENTION
from autoposter.db.models import ConfigOverride, ConfigOverrideSnapshot

# Fixtures, re-exported. pytest's default import mode puts `tests/` on
# sys.path, so this is a plain module import.
from test_api_config_editor import (  # noqa: F401
    THE_INCIDENT_DOCUMENT,
    app,
    auth_headers,
    client,
    config_file,
)

pytestmark = pytest.mark.asyncio


async def _store(client, auth_headers, document: dict, **extra) -> None:
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": document, **extra},
    )
    assert response.status_code == 200, response.text


async def _snapshots(session) -> list[ConfigOverrideSnapshot]:
    result = await session.execute(
        select(ConfigOverrideSnapshot).order_by(ConfigOverrideSnapshot.id.desc())
    )
    return list(result.scalars().all())


# --- capture --------------------------------------------------------------


async def test_a_save_snapshots_the_document_it_is_about_to_replace(
    client, auth_headers, session
):
    """The OUTGOING document, so the newest snapshot is always "what you had
    before the last save"."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await _store(client, auth_headers, edited)

    rows = await _snapshots(session)
    assert len(rows) == 1, "the first save had nothing to snapshot; the second did"
    assert rows[0].document == THE_INCIDENT_DOCUMENT
    assert rows[0].path_count == 12
    assert rows[0].reason == "save"


async def test_the_first_save_of_a_fresh_deployment_snapshots_nothing(
    client, auth_headers, session
):
    """There is nothing to restore to, and a fresh deployment would otherwise
    accumulate empty rows for as long as nobody saved anything."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    assert await _snapshots(session) == []


async def test_a_refused_write_leaves_no_snapshot_orphan(
    client, auth_headers, session
):
    """Same transaction, so a write that never happened has no history entry
    claiming it did. Three refusal routes, one for each guard that can fire
    after the capture point is reached."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    # The drop cap.
    await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": 9}}
    )
    # The empty-document guard.
    await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {}}
    )
    # The revision check.
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": edited, "expected_revision": EMPTY_DOCUMENT_REVISION},
    )

    assert await _snapshots(session) == [], (
        "a refused write left a snapshot behind. The capture and the upsert "
        "must commit together or not at all"
    )
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_the_apply_arm_records_its_own_reason(
    client, auth_headers, session
):
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.post(
        "/api/config/apply", headers=auth_headers, json={"document": edited}
    )
    assert response.status_code == 200, response.text

    rows = await _snapshots(session)
    assert [row.reason for row in rows] == ["apply"]


async def test_snapshots_are_pruned_to_the_retention_inline(
    client, auth_headers, session
):
    """Unbounded growth on a config table is not acceptable, and a scheduled
    pruner is more machinery than twenty rows deserve.

    Ordered and asserted on `id`, never on `created_at`: this project has a
    recorded environment whose container clock steps backwards.
    """
    for n in range(SNAPSHOT_RETENTION + 5):
        edited = deepcopy(THE_INCIDENT_DOCUMENT)
        edited["workers"] = n + 1
        await _store(client, auth_headers, edited)

    rows = await _snapshots(session)
    assert len(rows) == SNAPSHOT_RETENTION
    # The survivors are the newest, and the newest holds the document the last
    # save displaced.
    assert rows[0].document["workers"] == SNAPSHOT_RETENTION + 4


# --- the listing and the single read --------------------------------------


async def test_the_listing_carries_metadata_and_no_documents(
    client, auth_headers
):
    """A listing that shipped twenty full config documents is a payload nobody
    asked for, and one of them holds a push token."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await _store(client, auth_headers, edited)

    body = (await client.get("/api/config/snapshots", headers=auth_headers)).json()
    assert len(body) == 1
    assert set(body[0]) == {"id", "created_at", "path_count", "reason"}
    assert body[0]["path_count"] == 12
    assert body[0]["reason"] == "save"


async def test_the_listing_is_newest_first(client, auth_headers):
    for n in range(3):
        edited = deepcopy(THE_INCIDENT_DOCUMENT)
        edited["workers"] = n + 1
        await _store(client, auth_headers, edited)

    body = (await client.get("/api/config/snapshots", headers=auth_headers)).json()
    ids = [row["id"] for row in body]
    assert ids == sorted(ids, reverse=True)


async def test_reading_one_snapshot_redacts_it_exactly_as_the_config_does(
    client, auth_headers
):
    """A snapshot holds notifications.url in full. Serving it raw would leak a
    push token the live config endpoint carefully withholds -- through a new
    endpoint, which is exactly how that kind of hole gets made."""
    await _store(
        client,
        auth_headers,
        {"notifications": {"enabled": True, "url": "https://kuma.example.com/api/push/s3cr3t"}},
    )
    await _store(
        client,
        auth_headers,
        {"notifications": {"enabled": True, "url": "https://kuma.example.com/api/push/s3cr3t"}, "workers": 9},
    )
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    body = (
        await client.get(f"/api/config/snapshots/{row['id']}", headers=auth_headers)
    ).json()
    assert "s3cr3t" not in str(body)
    assert body["document"]["notifications"]["url"] == "kuma.example.com"
    assert body["document"]["notifications"]["enabled"] is True


async def test_reading_a_snapshot_that_is_not_there_is_a_404(client, auth_headers):
    response = await client.get("/api/config/snapshots/999999", headers=auth_headers)
    assert response.status_code == 404


# --- restore --------------------------------------------------------------


async def test_the_restore_round_trip_returns_the_exact_document(
    client, auth_headers, session, app
):
    """Snapshot, wipe, restore, and the stored document is byte-equal to what
    was there before. This is the deliverable the operator asked for."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    # A second save, so there is a snapshot holding the incident document.
    await _store(client, auth_headers, {"workers": 9}, confirm=True)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True},
    )

    assert response.status_code == 200, response.text
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT, "the restore did not round-trip"
    assert app.state.config.workers == 9
    assert app.state.config.collections.separator_style == "sand"


async def test_a_restore_is_itself_snapshotted_first(
    client, auth_headers, session
):
    """So an operator who restores the wrong one can get back. A restore that
    was not undoable would be a second way to lose a configuration."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    await _store(client, auth_headers, {"workers": 9}, confirm=True)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True},
    )

    rows = await _snapshots(session)
    assert [r.reason for r in rows] == ["restore", "save"]
    assert rows[0].document == {"workers": 9}


async def test_a_restore_respects_the_drop_cap_without_confirm(
    client, auth_headers, session
):
    """Restore is a save, not a bypass. Restoring a two-path snapshot over a
    twelve-path store drops ten, which is exactly the thing the cap is for."""
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": False}})
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore", headers=auth_headers, json={}
    )

    assert response.status_code == 422
    assert "confirm: true" in response.json()["detail"][0]["message"]
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_a_restore_re_validates_against_the_config_on_file(
    client, auth_headers, config_file, session
):
    """The mounted YAML may have moved under the snapshot since it was taken,
    and a snapshot from before a schema change must fail loudly rather than
    brick the pod on the next boot."""
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": False}})
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    # Make the snapshot invalid the way a schema change would: a value the
    # merged config can no longer accept.
    stored_row = (
        await session.execute(select(ConfigOverrideSnapshot))
    ).scalar_one()
    stored_row.document = {"workers": "not a number"}
    await session.commit()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["path"] == "workers"


async def test_a_restore_strips_a_migrated_section(
    client, auth_headers, session
):
    """The design trap: `load_overrides_document` strips MIGRATED_SECTIONS on
    read, but a raw snapshot row still holds one. Without the strip, restoring
    an old snapshot 422s on a key the editor can no longer produce -- so the
    one recovery path would be unusable on exactly the old snapshots recovery
    is for."""
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": False}})
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": True}})
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    stored_row = (
        await session.execute(select(ConfigOverrideSnapshot))
    ).scalar_one()
    stored_row.document = {"workers": 9, "version_check": {"enabled": True}}
    await session.commit()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True},
    )

    assert response.status_code == 200, response.text
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == {"workers": 9}


async def test_a_restore_honours_the_revision_check(client, auth_headers):
    """The safety UI sits on the same page as the editor, so a restore can be
    stale for exactly the same reason a save can."""
    await _store(client, auth_headers, {"workers": 9, "badges": {"enabled": False}})
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    [row] = (await client.get("/api/config/snapshots", headers=auth_headers)).json()

    response = await client.post(
        f"/api/config/snapshots/{row['id']}/restore",
        headers=auth_headers,
        json={"confirm": True, "expected_revision": EMPTY_DOCUMENT_REVISION},
    )
    assert response.status_code == 409


async def test_restoring_a_snapshot_that_is_not_there_is_a_404(
    client, auth_headers
):
    response = await client.post(
        "/api/config/snapshots/999999/restore", headers=auth_headers, json={}
    )
    assert response.status_code == 404


async def test_every_snapshot_endpoint_needs_a_session(client):
    """The same gate every other config endpoint sits behind. A history of the
    configuration is not less sensitive than the configuration."""
    assert (await client.get("/api/config/snapshots")).status_code == 401
    assert (await client.get("/api/config/snapshots/1")).status_code == 401
    assert (
        await client.post("/api/config/snapshots/1/restore", json={})
    ).status_code == 401
```

- [ ] **Step 4: Run the new tests to verify they fail**

```bash
docker compose -p pcs3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs3-red1 test sh -c \
  "set -o pipefail; pytest -q tests/test_config_safety.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t3-red1.log"
docker rm pcs3-red1
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-t3-red1.log`.**

Expected: a **collection error** — `ModuleNotFoundError: No module named
'autoposter.config.snapshots'`. That is the RED for a task whose first deliverable is
a new module.

**If instead the error is `ModuleNotFoundError: No module named
'test_api_config_editor'`, STOP and report before adapting anything.** It means this
repo does not run under pytest's prepend import mode. The fallback, and the only one:
move the whole of `tests/test_config_safety.py` into the end of
`tests/test_api_config_editor.py`, drop the fixture import and the `pytestmark` line
(that file already has both), and keep every test body byte-identical. Say in the
report that the split was reverted and why. Do **not** add `tests/__init__.py` — that
changes how the entire 4500-test suite is imported.

- [ ] **Step 5: Write the snapshot module**

Create `src/autoposter/config/snapshots.py`:

```python
"""History for the overrides document, and the read side of restoring it.

Deliberately NOT ``autoposter.api.snapshots``: that name is already taken by
the status/events payload builders behind ``GET /api/status`` and the dashboard
stream, and a second meaning for it would be a trap for every later reader.
This lives beside ``config/overrides.py``, which is where the document's other
readers already live.

The capture itself takes a caller's session and adds to it rather than opening
one, because it has to land in the same transaction as the write it is a
snapshot of. See ``api/routes._persist_and_swap``.
"""
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.overrides import document_paths
from autoposter.db.models import ConfigOverrideSnapshot

logger = logging.getLogger(__name__)

#: How many previous documents to keep. A module constant rather than a
#: setting, for the same reason the drop cap is one: a recovery depth an
#: operator can lower from the page they are about to break is not a recovery
#: depth. Twenty covers a bad afternoon and costs a few kilobytes.
SNAPSHOT_RETENTION = 20


async def capture_snapshot(session: AsyncSession, document: dict, reason: str) -> None:
    """Record ``document`` as the state a write is about to replace.

    Adds to the caller's session and does not commit: the snapshot and the
    write it protects must land together, or the history says something that
    did not happen.

    An empty outgoing document is skipped. There is nothing to restore to, and
    a fresh deployment would otherwise fill the table with empty rows before
    ever having anything worth keeping.

    Pruning runs here, inline, in the same transaction. Unbounded growth on a
    config table is not acceptable and a scheduled pruner is more machinery
    than twenty rows deserve.
    """
    if not document:
        return
    session.add(
        ConfigOverrideSnapshot(
            document=document,
            path_count=len(document_paths(document)),
            reason=reason,
        )
    )
    # So the row about to be inserted is inside the keep set and cannot prune
    # itself out of existence on a table already at the retention limit.
    await session.flush()
    keep = (
        select(ConfigOverrideSnapshot.id)
        .order_by(ConfigOverrideSnapshot.id.desc())
        .limit(SNAPSHOT_RETENTION)
    )
    await session.execute(
        delete(ConfigOverrideSnapshot).where(
            ConfigOverrideSnapshot.id.not_in(keep.scalar_subquery())
        )
    )


async def list_snapshots(session: AsyncSession) -> list[dict]:
    """Every kept snapshot's metadata, newest first -- and no documents.

    The list UI needs to label its rows ("3 settings, 14:22 today"), not to
    hold twenty configurations. One of those configurations holds a push token,
    so shipping them all to render a list would be a leak with no upside.

    Ordered by ``id``, not by ``created_at``: identity is monotonic, and this
    project has a recorded deployment whose clock is not.
    """
    result = await session.execute(
        select(
            ConfigOverrideSnapshot.id,
            ConfigOverrideSnapshot.created_at,
            ConfigOverrideSnapshot.path_count,
            ConfigOverrideSnapshot.reason,
        ).order_by(ConfigOverrideSnapshot.id.desc())
    )
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "path_count": row.path_count,
            "reason": row.reason,
        }
        for row in result
    ]


async def load_snapshot(session: AsyncSession, snapshot_id: int) -> dict:
    """One snapshot's stored document, exactly as it was captured.

    Unredacted, because both callers need it that way for opposite reasons: the
    restore has to write the real ``notifications.url`` back, and the single-
    snapshot GET redacts it itself with the same machinery ``GET /api/config``
    uses. Redacting here would quietly make restore destroy a push token.

    Raises ``LookupError`` when there is no such row -- the caller owns the HTTP
    status, this module does not import fastapi.
    """
    row = await session.get(ConfigOverrideSnapshot, snapshot_id)
    if row is None:
        raise LookupError(f"no config snapshot {snapshot_id}")
    return dict(row.document)
```

- [ ] **Step 6: Capture inside the write transaction**

In `src/autoposter/api/routes.py`, add the import beside the other `autoposter.config`
ones:

```python
from autoposter.config.snapshots import capture_snapshot, list_snapshots, load_snapshot
```

In `_persist_and_swap`, insert the capture between the guards and the upsert — inside
the same `async with` block, before `session.execute(stmt)`:

```python
        # Pre-write, in this session and this transaction. Same transaction is
        # the whole point: a snapshot that commits without its write, or a
        # write that commits without its snapshot, is worse than neither.
        await capture_snapshot(session, stored, reason)
```

Add to the docstring, after the TOCTOU paragraph:

```
    The pre-write snapshot goes in the same block for the same reason the guards
    do -- it is part of the write, not a step beside it.
```

- [ ] **Step 7: The three endpoints**

Append to `src/autoposter/api/routes.py`, immediately after `apply_config_overrides`:

```python
class SnapshotRestoreBody(BaseModel):
    """Restore one previous overrides document. ``confirm`` for a big drop.

    Restore is a save, not a bypass: it re-validates against the config on
    file, snapshots the current document first (so the restore is itself
    undoable), and respects the same drop cap and the same revision check a PUT
    does. Its body is therefore the save body minus the document, which the
    snapshot id supplies.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: str | None = None
    confirm: bool = False


def _redacted_document(document: dict) -> dict:
    """A snapshot as it may be served: the same redaction ``GET /api/config``
    applies, applied to the same paths.

    A stored snapshot holds ``notifications.url`` in full -- it has to, or a
    restore could not put the push token back. Serving that raw from a new
    endpoint would hand out the exact value the config endpoint is careful to
    withhold.
    """
    shown = deepcopy(document)
    for path, redact in _REDACTORS.items():
        value = _read_path(shown, path)
        if isinstance(value, str) and value:
            _set_path(shown, path, redact(value))
    return shown


@router.get("/config/snapshots")
async def get_config_snapshots(
    request: Request, _: SessionModel = Depends(require_session)
) -> list[dict]:
    """Every kept previous overrides document, newest first, metadata only.

    Enough to label a row -- how many settings it held, when it was displaced
    and by what -- and no documents: see ``list_snapshots``.
    """
    async with request.app.state.session_factory() as session:
        return await list_snapshots(session)


@router.get("/config/snapshots/{snapshot_id}")
async def get_config_snapshot(
    snapshot_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """One previous overrides document, redacted the way the live one is."""
    async with request.app.state.session_factory() as session:
        try:
            document = await load_snapshot(session, snapshot_id)
        except LookupError:
            raise HTTPException(
                status_code=404, detail=f"no config snapshot {snapshot_id}"
            ) from None
        rows = {row["id"]: row for row in await list_snapshots(session)}
    meta = rows.get(snapshot_id, {})
    return {
        "id": snapshot_id,
        "created_at": meta.get("created_at"),
        "path_count": meta.get("path_count"),
        "reason": meta.get("reason"),
        "document": _redacted_document(document),
    }


@router.post("/config/snapshots/{snapshot_id}/restore")
async def restore_config_snapshot(
    snapshot_id: int,
    body: SnapshotRestoreBody,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Put a previous overrides document back, as a save.

    Not a bypass and not a second write path: the stored (unredacted) document
    goes through ``_validated_generation`` and ``_persist_and_swap`` exactly as
    a PUT's would, so it is re-validated against the config file as it stands
    *now* -- the mounted YAML may have moved under this snapshot since it was
    taken, and a snapshot from before a schema change must fail loudly here
    rather than brick the pod at the next boot.

    ``without_migrated_sections`` runs first, and it is not optional. A whole
    section that left the schema is stripped from the *stored* document on
    every read, but a raw snapshot row still holds it -- so without this the
    one recovery path would 422 on exactly the old snapshots recovery exists
    for.
    """
    async with request.app.state.session_factory() as session:
        try:
            snapshot = await load_snapshot(session, snapshot_id)
        except LookupError:
            raise HTTPException(
                status_code=404, detail=f"no config snapshot {snapshot_id}"
            ) from None

    document, after = await _validated_generation(
        request, without_migrated_sections(snapshot)
    )
    return await _persist_and_swap(
        request,
        document,
        after,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
        reason="restore",
    )
```

If `without_migrated_sections` is not yet in `routes.py`'s import block from T2, add
it now.

- [ ] **Step 8: Run the tests to verify they pass**

```bash
docker compose -p pcs3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs3-green1 test sh -c \
  "set -o pipefail; pytest -q tests/test_config_safety.py tests/test_api_config_editor.py tests/test_migrations.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t3-green1.log"
docker rm pcs3-green1
```

**Read the log.** Expected: **0 failed**; `test_config_safety.py` contributes **18**.

Two failures are foreseeable and each has one correct fix:

- `test_snapshots_are_pruned_to_the_retention_inline` failing with 25 rows means the
  `not_in` subquery ran before the flush. The fix is the `await session.flush()`, not
  a larger retention.
- A `test_api_config_editor.py` test that counts `EventLog` rows or asserts an exact
  row count on any table may now see snapshot rows. Widen it to the table it was
  actually about; do not remove the capture.

- [ ] **Step 9: Full backend suite, ruff, disjointness, commit**

```bash
docker compose -p pcs3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pcs3-full test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-p-configsafety-t3-full.log"
docker wait pcs3-full
docker rm pcs3-full
docker compose -p pcs3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs3-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-p-configsafety-t3-ruff.log"
docker rm pcs3-ruff
git diff --stat HEAD -- src/ tests/ frontend/ alembic/
```

**Read both logs.** Expected: pytest **0 failed**, up **18** from T2's total; ruff
`All checks passed!`. The diff must list only this task's five files and **must not
list `src/autoposter/config/schema.py`**. Then:

```bash
git add src/autoposter/config/snapshots.py src/autoposter/db/models.py src/autoposter/api/routes.py alembic/versions/c7e1b93a4d20_config_override_snapshots.py tests/test_config_safety.py
git commit --no-gpg-sign -m "feat(config): snapshot the overrides document before every write

Twenty previous documents, captured pre-write in the same transaction as the
upsert and pruned inline, with list/read/restore endpoints. Restore is a save:
re-validated against the config on file, snapshotted first so it is itself
undoable, and subject to the same drop cap and revision check. Migrated
sections are stripped on the way back in, or an old snapshot would be
unrestorable."
docker compose -p pcs3 down
```

**Task 3 report must record:** the confirmed single alembic head from Step 1, the new
backend total, whether the `test_config_safety.py` fixture import worked or the
fallback was taken, and any test widened in Step 8 with the reason.

---
# Task 4: Export and import

Two endpoints over the machinery T1–T3 already built. The whole task is small on
purpose: an export is the stored document in a thin envelope, and an import is *a save
with a file picker in front of it*. Inventing an import-only code path would give the
one write that most needs the guards its own way around them.

**Files:**
- Modify: `src/autoposter/api/routes.py` (two endpoints, one body model, one import)
- Modify: `tests/test_config_safety.py`
- No docs are touched by this task. The phase's paperwork — the roadmap row, the progress entry, the PR body — is all T5's.

**Interfaces:**
- Consumes: T1's `_validated_generation`, `_persist_and_swap`, `_error`; T2's `document_revision`; T3's `without_migrated_sections` import.
- Produces:
  - `GET /api/config/overrides/export` → `{"autoposter_overrides": 1, "exported_at": <iso>, "document": <stored, UNREDACTED>}`
  - `POST /api/config/overrides/import`, body `ConfigImportBody{autoposter_overrides, exported_at?, document, confirm, expected_revision}` → the `_persist_and_swap` response
  - `OVERRIDES_EXPORT_FORMAT = 1`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_safety.py`:

```python
# --- export / import ------------------------------------------------------


async def test_the_export_carries_the_document_under_the_same_key_the_put_takes(
    client, auth_headers
):
    """Deliberately the same key name and the same shape, so an exported
    file's `document` value pastes straight into a PUT body and back."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    body = (
        await client.get("/api/config/overrides/export", headers=auth_headers)
    ).json()

    assert body["autoposter_overrides"] == 1
    assert body["document"] == THE_INCIDENT_DOCUMENT
    assert body["exported_at"].endswith("+00:00") or body["exported_at"].endswith("Z")


async def test_the_export_is_unredacted_by_design(client, auth_headers):
    """A redacted backup is a broken backup: re-importing one would write the
    bare host over notifications.url and destroy the push token. The trade is
    deliberate and is stated in the docstring and in the UI's download copy --
    the file holds the notification URL."""
    await _store(
        client,
        auth_headers,
        {"notifications": {"enabled": True, "url": "https://kuma.example.com/api/push/s3cr3t"}},
    )

    body = (
        await client.get("/api/config/overrides/export", headers=auth_headers)
    ).json()

    assert body["document"]["notifications"]["url"] == (
        "https://kuma.example.com/api/push/s3cr3t"
    )


async def test_an_empty_store_still_exports_a_valid_envelope(client, auth_headers):
    """A backup taken before the first edit is a legitimate backup of nothing;
    an endpoint that 404'd there would make the button lie on a fresh
    deployment."""
    body = (
        await client.get("/api/config/overrides/export", headers=auth_headers)
    ).json()
    assert body == {
        "autoposter_overrides": 1,
        "exported_at": body["exported_at"],
        "document": {},
    }


async def test_an_export_round_trips_through_import(
    client, auth_headers, session
):
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    exported = (
        await client.get("/api/config/overrides/export", headers=auth_headers)
    ).json()
    await _store(client, auth_headers, {"workers": 1}, confirm=True)

    response = await client.post(
        "/api/config/overrides/import",
        headers=auth_headers,
        json={**exported, "confirm": True},
    )

    assert response.status_code == 200, response.text
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_an_import_without_the_format_marker_is_refused(
    client, auth_headers, session
):
    """The marker is what stops somebody importing a whole `GET /api/config`
    dump, which would freeze today's file values as overrides for ever -- the
    exact hazard `documentFromConfig`'s docstring warns about."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.post(
        "/api/config/overrides/import",
        headers=auth_headers,
        json={"document": {"workers": 1}, "confirm": True},
    )

    assert response.status_code == 422
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_an_import_declaring_an_unknown_format_is_refused(
    client, auth_headers
):
    response = await client.post(
        "/api/config/overrides/import",
        headers=auth_headers,
        json={"autoposter_overrides": 2, "document": {"workers": 1}, "confirm": True},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "path": "autoposter_overrides",
            "message": "unsupported export format 2; this service writes and reads 1",
        }
    ]


async def test_an_import_is_refused_when_it_drops_too_much_without_confirm(
    client, auth_headers, session
):
    """Import is the highest-risk drop in the phase: one stale export can drop
    dozens of paths at once. It is a save, so the cap applies unchanged."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.post(
        "/api/config/overrides/import",
        headers=auth_headers,
        json={"autoposter_overrides": 1, "document": {"workers": 1}},
    )

    assert response.status_code == 422
    assert "confirm: true" in response.json()["detail"][0]["message"]
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_an_import_is_snapshotted_and_validated_like_any_other_save(
    client, auth_headers, session
):
    """No import-only code path: the same five refusal gates, the same
    snapshot, the same drop cap."""
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)

    bad = await client.post(
        "/api/config/overrides/import",
        headers=auth_headers,
        json={
            "autoposter_overrides": 1,
            "document": {"workers": "not a number"},
            "confirm": True,
        },
    )
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["path"] == "workers"
    assert await _snapshots(session) == [], "a refused import must snapshot nothing"

    good = await client.post(
        "/api/config/overrides/import",
        headers=auth_headers,
        json={"autoposter_overrides": 1, "document": {"workers": 1}, "confirm": True},
    )
    assert good.status_code == 200, good.text
    rows = await _snapshots(session)
    assert [row.reason for row in rows] == ["import"]
    assert rows[0].document == THE_INCIDENT_DOCUMENT


async def test_an_import_honours_the_revision_check(client, auth_headers):
    await _store(client, auth_headers, THE_INCIDENT_DOCUMENT)
    response = await client.post(
        "/api/config/overrides/import",
        headers=auth_headers,
        json={
            "autoposter_overrides": 1,
            "document": {"workers": 1},
            "confirm": True,
            "expected_revision": EMPTY_DOCUMENT_REVISION,
        },
    )
    assert response.status_code == 409


async def test_an_import_body_with_an_unknown_key_is_refused(client, auth_headers):
    """The envelope forbids extras here too. A hand-edited backup with a typo'd
    key must not be half-applied under a 200."""
    response = await client.post(
        "/api/config/overrides/import",
        headers=auth_headers,
        json={
            "autoposter_overrides": 1,
            "document": {"workers": 1},
            "exported_ad": "2026-09-03T00:00:00+00:00",
        },
    )
    assert response.status_code == 422


async def test_both_export_and_import_need_a_session(client):
    assert (await client.get("/api/config/overrides/export")).status_code == 401
    assert (
        await client.post("/api/config/overrides/import", json={})
    ).status_code == 401
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p pcs4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs4-red1 test sh -c \
  "set -o pipefail; pytest -q tests/test_config_safety.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t4-red1.log"
docker rm pcs4-red1
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-t4-red1.log`.**
Expected: **11 failed, 18 passed** — the eighteen from Task 3 stay green, and every
new one fails with `assert 404 == 200` or `assert 404 == 422` (neither endpoint
exists, so FastAPI answers 404). `…need_a_session` fails on `404 != 401` for the same
reason. If any new test fails with a 500 or an assertion about a *body*, read the log
before continuing — that would mean a route already matched.

- [ ] **Step 3: Write the two endpoints**

In `src/autoposter/api/routes.py`, add the stdlib import beside the others at the top:

```python
from datetime import UTC, datetime
```

(if `datetime` is already imported there, extend that line rather than adding a second.)

Append after `restore_config_snapshot`:

```python
#: The export envelope's format discriminator. Bumped only when the shape of
#: `document` changes in a way an older reader would misread -- not when a
#: config field is added, which the document already absorbs by construction.
OVERRIDES_EXPORT_FORMAT = 1


class ConfigImportBody(BaseModel):
    """An exported overrides file, on its way back in.

    The same envelope ``GET /api/config/overrides/export`` writes, so the two
    are one format rather than two that agree by habit. ``autoposter_overrides``
    is required and is the point of the envelope: without a discriminator,
    somebody would eventually import a whole ``GET /api/config`` dump, which
    would freeze today's file values as permanent overrides -- the exact hazard
    ``documentFromConfig``'s docstring warns about, arriving through a button.

    ``exported_at`` is carried so a hand-inspected file round-trips unchanged;
    nothing reads it.
    """

    model_config = ConfigDict(extra="forbid")

    autoposter_overrides: int
    document: dict = Field(default_factory=dict)
    exported_at: str | None = None
    expected_revision: str | None = None
    confirm: bool = False


@router.get("/config/overrides/export")
async def export_config_overrides(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """The stored overrides document, in a thin envelope, for safekeeping.

    ``document`` is deliberately the same key name and the same shape ``PUT
    /api/config/overrides`` takes, so an exported file's ``document`` value
    pastes straight into a PUT body and back.

    **Unredacted, deliberately, and this is a trade rather than an oversight.**
    ``GET /api/config`` reduces ``notifications.url`` to its host because that
    response is for a page; this response is a backup, and a backup that
    redacts is broken -- re-importing one would write the bare host over the
    real URL and destroy the push token it embeds. So the file holds the
    notification URL, the UI's download button says so in plain words, and the
    operator decides where the file goes. ``secrets`` is absent by
    construction: ``merge_overrides`` refuses the key outright, so there is no
    API token in it either way.

    (The alternative considered and rejected: exporting with the keep sentinel
    at every redacted path. That round-trips correctly on the *same*
    deployment and is useless as a transfer to a different one, which is most
    of what a backup is for.)
    """
    async with request.app.state.session_factory() as session:
        document = await load_overrides_document(session)
    return {
        "autoposter_overrides": OVERRIDES_EXPORT_FORMAT,
        "exported_at": datetime.now(UTC).isoformat(),
        "document": document,
    }


@router.post("/config/overrides/import")
async def import_config_overrides(
    body: ConfigImportBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Restore an exported overrides file. A save with a file picker in front.

    No import-only code path exists on purpose. The document goes through
    ``_validated_generation`` and ``_persist_and_swap`` exactly as a PUT's
    would: the same five refusal gates, the same pre-write snapshot, the same
    drop cap, the same revision check. An import is the highest-risk *drop* in
    this service -- one stale export can drop dozens of paths at once -- which
    is precisely the reason to route it through the guards rather than around
    them.

    Migrated sections are stripped first, for the reason
    ``restore_config_snapshot`` gives: a file exported before a section left the
    schema is exactly the file somebody reaches for a year later.
    """
    if body.autoposter_overrides != OVERRIDES_EXPORT_FORMAT:
        raise HTTPException(
            status_code=422,
            detail=[
                _error(
                    "autoposter_overrides",
                    f"unsupported export format {body.autoposter_overrides}; "
                    f"this service writes and reads {OVERRIDES_EXPORT_FORMAT}",
                )
            ],
        )

    document, after = await _validated_generation(
        request, without_migrated_sections(body.document)
    )
    return await _persist_and_swap(
        request,
        document,
        after,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
        reason="import",
    )
```

`ConfigOverrideSnapshot.reason` is `String(16)`; `"import"` is six characters, well
inside it. No migration change is needed.

- [ ] **Step 4: Run them to verify they pass**

```bash
docker compose -p pcs4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs4-green1 test sh -c \
  "set -o pipefail; pytest -q tests/test_config_safety.py tests/test_api_config_editor.py 2>&1 | tee /app/.superpowers/run-p-configsafety-t4-green1.log"
docker rm pcs4-green1
```

**Read the log.** Expected: **0 failed**; `test_config_safety.py` at **29**.

One foreseeable failure: `test_the_export_carries_the_document_under_the_same_key…`
asserting the `exported_at` suffix. `datetime.now(UTC).isoformat()` ends `+00:00`, so
the first arm of that `or` holds — if it does not, read the actual value in the log and
correct the *assertion* to what a UTC-aware isoformat really produces. Do not switch
the endpoint to a naive timestamp to satisfy a test.

- [ ] **Step 5: Full backend suite, ruff, disjointness, commit**

```bash
docker compose -p pcs4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pcs4-full test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-p-configsafety-t4-full.log"
docker wait pcs4-full
docker rm pcs4-full
docker compose -p pcs4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs4-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-p-configsafety-t4-ruff.log"
docker rm pcs4-ruff
git diff --stat HEAD -- src/ tests/ frontend/ alembic/
```

**Read both logs.** Expected: pytest **0 failed**, up **11** from T3's total; ruff
`All checks passed!`. The diff must list exactly `src/autoposter/api/routes.py` and
`tests/test_config_safety.py`, and **must not list `src/autoposter/config/schema.py`**.

```bash
git add src/autoposter/api/routes.py tests/test_config_safety.py
git commit --no-gpg-sign -m "feat(config): export and import the overrides document

An envelope with a format marker so a whole GET /api/config dump cannot be
imported by mistake, and an import that is a save through the same guarded
path -- same validation, same snapshot, same drop cap, same revision check.
The export is unredacted deliberately: a redacted backup would destroy the
notification URL on the way back in."
docker compose -p pcs4 down
```

**Task 4 report must record:** the new backend total, and the exact `exported_at`
format the endpoint produced.

---
# Task 5: The safety UI, and the phase's paperwork

The operator's directive was *"there NEEDS to be an easy config backup/restore"*. T3 and
T4 built it; this is the part they can reach. It lives in the Settings page's existing
save area as one more panel — not a new page — and it is its own component file rather
than another six hundred lines inside `Settings.tsx`, which is 29 KB already and which
T2 has just edited.

**Files:**
- Create: `frontend/src/pages/ConfigSafetyPanel.tsx`
- Create: `frontend/src/pages/ConfigSafetyPanel.test.tsx`
- Modify: `frontend/src/pages/Settings.tsx` (one import, one element)
- Modify: `frontend/src/pages/Settings.test.tsx` (one mount assertion)
- Modify: `frontend/src/api/types.ts` (`ConfigSnapshot`, `ConfigSnapshotDetail`, `ConfigExport`)
- Modify: `frontend/src/pages/settings.css`
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (file and close row 231)
- Modify: `.superpowers/sdd/progress.md` (the ship entry)
- Create: `.superpowers/sdd/p-configsafety-pr-body.md` (**never committed**)

**Interfaces:**
- Consumes: T3's three endpoints; T4's two; T2's `STALE_SAVE_NOTE`; `apiFetch`, `ApiError` from `../api/client`.
- Produces: `ConfigSafetyPanel({ revision, onChanged })`, rendered by `Settings`.

- [ ] **Step 1: The types**

Append to `frontend/src/api/types.ts`:

```ts
/** One row of GET /api/config/snapshots — metadata only. The documents are
 * not in the listing on purpose: the list needs to label its rows, and one of
 * those documents holds the notification URL. */
export interface ConfigSnapshot {
  id: number;
  created_at: string;
  path_count: number;
  reason: string;
}

/** GET /api/config/snapshots/{id}. `document` is redacted exactly as
 * `GET /api/config` redacts the live one. */
export interface ConfigSnapshotDetail extends ConfigSnapshot {
  document: OverridesDocument;
}

/** GET /api/config/overrides/export. `document` is UNREDACTED — a redacted
 * backup would write the bare notification host back over the real URL on the
 * next import. The download copy tells the operator so. */
export interface ConfigExport {
  autoposter_overrides: number;
  exported_at: string;
  document: OverridesDocument;
}
```

- [ ] **Step 2: Write the failing panel tests**

Create `frontend/src/pages/ConfigSafetyPanel.test.tsx`:

```tsx
/** The panel an operator reaches for after a configuration goes missing.
 *
 * It exists because of the 2026-09-01 incident, where the only surviving copy
 * of seventeen overrides was one that happened to be in a chat window. Three
 * things are load-bearing here and each has a test that says which one broke:
 * a restore is offered per snapshot and re-reads afterwards; a destructive
 * restore or import is gated on the operator ticking a box, not on the page
 * guessing; and the export's warning about what the file contains is not
 * decoration.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { STALE_SAVE_NOTE } from "../api/overrides";
import { ConfigSafetyPanel, EXPORT_WARNING } from "./ConfigSafetyPanel";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SNAPSHOTS = [
  { id: 7, created_at: "2026-09-03T14:22:00+00:00", path_count: 3, reason: "save" },
  { id: 6, created_at: "2026-09-03T11:04:00+00:00", path_count: 12, reason: "apply" },
];

interface StubOptions {
  snapshots?: unknown;
  restore?: () => Response;
  exported?: unknown;
  preview?: () => Response;
  imported?: () => Response;
}

function stubFetch(options: StubOptions = {}) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path === "/api/config/snapshots") {
      return json(options.snapshots ?? SNAPSHOTS);
    }
    if (/^\/api\/config\/snapshots\/\d+\/restore$/.test(path)) {
      return options.restore ? options.restore() : json({ version_before: "a", version_after: "b", restart_required: [] });
    }
    if (path === "/api/config/overrides/export") {
      return json(
        options.exported ?? {
          autoposter_overrides: 1,
          exported_at: "2026-09-03T14:30:00+00:00",
          document: { workers: 9 },
        },
      );
    }
    if (path === "/api/config/preview") {
      return options.preview
        ? options.preview()
        : json({
            version_before: "a",
            version_after: "b",
            restart_required: [],
            inert: [],
            impact: null,
          });
    }
    if (path === "/api/config/overrides/import") {
      return options.imported ? options.imported() : json({ version_before: "a", version_after: "b", restart_required: [] });
    }
    throw new Error(`unexpected fetch: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

async function renderPanel(options: StubOptions = {}, revision: string | null = "rev-1") {
  const stub = stubFetch(options);
  const onChanged = vi.fn(async () => {});
  await act(async () => {
    render(<ConfigSafetyPanel revision={revision} onChanged={onChanged} />);
  });
  return { ...stub, onChanged };
}

async function click(name: string | RegExp) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

beforeEach(() => {
  setToken(null);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:stub"),
    revokeObjectURL: vi.fn(),
  });
});

describe("the previous-versions list", () => {
  it("lists each snapshot by how many settings it held and when", async () => {
    await renderPanel();

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("3 settings");
    expect(rows[1]).toHaveTextContent("12 settings");
  });

  it("says so plainly when there is no history yet", async () => {
    await renderPanel({ snapshots: [] });
    expect(screen.getByText(/No previous versions/i)).toBeInTheDocument();
  });

  it("restores one snapshot, sends the revision, and re-reads afterwards", async () => {
    const { calls, onChanged } = await renderPanel();

    await click(/Restore/);
    const restore = calls.find((call) => call.path.endsWith("/restore"));
    expect(restore?.init?.method).toBe("POST");
    expect(JSON.parse(String(restore?.init?.body))).toEqual({
      confirm: false,
      expected_revision: "rev-1",
    });
    // Provenance is the server's to report -- the same re-read every other
    // write on this page does.
    expect(onChanged).toHaveBeenCalledTimes(1);
    // And the list itself is refreshed: the restore just added a snapshot.
    expect(calls.filter((call) => call.path === "/api/config/snapshots")).toHaveLength(2);
  });

  it("sends confirm only when the operator ticked the box", async () => {
    const { calls } = await renderPanel();

    fireEvent.click(screen.getByLabelText(/Allow this to remove settings/i));
    await click(/Restore/);

    const restore = calls.find((call) => call.path.endsWith("/restore"));
    expect(JSON.parse(String(restore?.init?.body)).confirm).toBe(true);
  });

  it("shows the server's refusal instead of restoring silently", async () => {
    await renderPanel({
      restore: () =>
        json(
          {
            detail: [
              {
                path: "document",
                message:
                  "this would drop 11 stored overrides (badges.enabled); send confirm: true to do it deliberately",
              },
            ],
          },
          422,
        ),
    });

    await click(/Restore/);
    expect(screen.getByText(/would drop 11 stored overrides/)).toBeInTheDocument();
  });

  it("tells the operator and re-reads when a restore is stale", async () => {
    const { onChanged } = await renderPanel({
      restore: () =>
        json({ message: "moved", current_revision: "rev-9", changed_paths: [] }, 409),
    });

    await click(/Restore/);
    expect(screen.getByText(STALE_SAVE_NOTE)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });
});

describe("export", () => {
  it("warns what the file holds before offering it", async () => {
    await renderPanel();
    expect(screen.getByText(EXPORT_WARNING)).toBeInTheDocument();
    // The warning is the trade the endpoint made, stated where the operator
    // makes the decision. A paraphrase would be a different promise.
    expect(EXPORT_WARNING).toMatch(/notification URL/i);
  });

  it("downloads the envelope under a named file", async () => {
    const { calls } = await renderPanel();

    await click(/Download a backup/);

    expect(calls.some((call) => call.path === "/api/config/overrides/export")).toBe(true);
    const link = screen.getByTestId("config-export-link") as HTMLAnchorElement;
    expect(link.getAttribute("download")).toMatch(/^autoposter-overrides-.*\.json$/);
    expect(link.getAttribute("href")).toBe("blob:stub");
  });
});

describe("import", () => {
  const FILE = new File(
    [JSON.stringify({ autoposter_overrides: 1, exported_at: "x", document: { workers: 9 } })],
    "backup.json",
    { type: "application/json" },
  );

  async function choose(file: File) {
    const input = screen.getByLabelText(/Restore from a backup file/i);
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
  }

  it("previews before offering the import, and imports nothing yet", async () => {
    const { calls } = await renderPanel();

    await choose(FILE);

    const preview = calls.find((call) => call.path === "/api/config/preview");
    expect(JSON.parse(String(preview?.init?.body))).toEqual({
      document: { workers: 9 },
      expected_revision: "rev-1",
    });
    expect(calls.some((call) => call.path === "/api/config/overrides/import")).toBe(false);
    expect(screen.getByRole("button", { name: /Import these settings/i })).toBeInTheDocument();
  });

  it("sends the whole envelope on import, and re-reads", async () => {
    const { calls, onChanged } = await renderPanel();

    await choose(FILE);
    await click(/Import these settings/i);

    const imported = calls.find((call) => call.path === "/api/config/overrides/import");
    expect(JSON.parse(String(imported?.init?.body))).toEqual({
      autoposter_overrides: 1,
      exported_at: "x",
      document: { workers: 9 },
      confirm: false,
      expected_revision: "rev-1",
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("refuses a file that is not an export, without calling the server", async () => {
    // The server refuses it too, but a page that posted a Plex library dump
    // and waited for a 422 would be telling the operator the server is fussy
    // rather than that they picked the wrong file.
    const { calls } = await renderPanel();

    await choose(new File(["{\"workers\": 9}"], "config.json", { type: "application/json" }));

    expect(screen.getByText(/not an Autoposter overrides backup/i)).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/config/preview")).toBe(false);
  });

  it("refuses a file that is not JSON at all", async () => {
    const { calls } = await renderPanel();

    await choose(new File(["not json"], "notes.txt", { type: "text/plain" }));

    expect(screen.getByText(/could not be read as JSON/i)).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/config/preview")).toBe(false);
  });

  it("shows the server's refusal of an import rather than claiming success", async () => {
    await renderPanel({
      imported: () =>
        json({ detail: [{ path: "workers", message: "Input should be a valid integer" }] }, 422),
    });

    await choose(FILE);
    await click(/Import these settings/i);

    expect(screen.getByText(/workers: Input should be a valid integer/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

```bash
docker compose -p pcs5 run --name pcs5-red1 web npm test -- ConfigSafetyPanel > .superpowers/run-p-configsafety-t5-red1.log 2>&1
docker rm pcs5-red1
```

**Read `D:\Sites\autoposter\.superpowers\run-p-configsafety-t5-red1.log`.** Expected:
a resolve failure — `Failed to resolve import "./ConfigSafetyPanel"`. That is the RED
for a task whose deliverable is a new component.

- [ ] **Step 4: Write the panel**

Create `frontend/src/pages/ConfigSafetyPanel.tsx`:

```tsx
/** Backup, history and restore for the overrides document.
 *
 * Its own file rather than another section of `Settings.tsx` -- that page is
 * already the largest in the app and this is a self-contained thing with its
 * own fetches, its own errors and its own state. It renders as one more panel
 * inside the settings page, not as a new route: an operator looking for "what
 * did my configuration used to be" is already on the page that changed it.
 *
 * Everything destructive here is gated on one checkbox the operator ticks,
 * which becomes the API's `confirm: true`. The page never sets it on their
 * behalf, and never retries a refusal -- the two habits that turn a guard back
 * into the bug it was added for.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import { fieldErrors, STALE_SAVE_NOTE } from "../api/overrides";
import type {
  ConfigExport,
  ConfigPreviewResponse,
  ConfigSaveResponse,
  ConfigSnapshot,
} from "../api/types";

/** Said where the decision is made, because the endpoint deliberately does not
 * redact: a redacted backup would write the bare notification host back over
 * the real URL on the next import, which is a broken backup wearing a safe
 * one's clothes. */
export const EXPORT_WARNING =
  "The backup file contains your settings in full, including the notification URL. " +
  "Keep it somewhere you would keep a password.";

function refusal(caught: unknown): string {
  if (caught instanceof ApiError && caught.status === 422) {
    const errors = fieldErrors(caught.detail);
    const messages = Object.entries(errors).map(([path, message]) =>
      path === "" || path === "document" ? message : `${path}: ${message}`,
    );
    if (messages.length > 0) return messages.join("; ");
  }
  return (caught as Error).message;
}

function describe(snapshot: ConfigSnapshot): string {
  const when = new Date(snapshot.created_at);
  const stamp = Number.isNaN(when.getTime())
    ? snapshot.created_at
    : when.toLocaleString();
  const settings = snapshot.path_count === 1 ? "1 setting" : `${snapshot.path_count} settings`;
  return `${settings} · ${stamp} · replaced by ${snapshot.reason}`;
}

interface PendingImport {
  envelope: ConfigExport;
  preview: ConfigPreviewResponse;
  name: string;
}

export function ConfigSafetyPanel({
  revision,
  onChanged,
}: {
  /** The revision the settings page seeded from. Sent with every write here
   * for the same reason it is sent with a save: a restore composed against a
   * page that has gone stale is the same lost update. */
  revision: string | null;
  /** Re-read `/api/config` and re-adopt. Provenance is the server's to report
   * after a restore exactly as it is after a save. */
  onChanged: () => Promise<void>;
}) {
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [download, setDownload] = useState<{ href: string; name: string } | null>(null);
  const [pending, setPending] = useState<PendingImport | null>(null);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    const rows = await apiFetch<ConfigSnapshot[]>("/api/config/snapshots");
    if (live.current) setSnapshots(rows);
  }, []);

  useEffect(() => {
    reload().catch((caught: Error) => {
      if (live.current) setError(caught.message);
    });
  }, [reload]);

  /** One body for both writes here, and the same shape the editor's save uses:
   * the confirm the operator ticked, and the revision the page seeded from. */
  function guards(): { confirm: boolean; expected_revision?: string } {
    return revision === null ? { confirm } : { confirm, expected_revision: revision };
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await action();
    } catch (caught) {
      if (!live.current) return;
      if (caught instanceof ApiError && caught.status === 409) {
        // Never retried. Re-read, and say what happened.
        setNote(STALE_SAVE_NOTE);
        await onChanged();
      } else {
        setError(refusal(caught));
      }
    } finally {
      if (live.current) setBusy(false);
    }
  }

  async function restore(id: number) {
    await run(async () => {
      await apiFetch<ConfigSaveResponse>(`/api/config/snapshots/${id}/restore`, {
        method: "POST",
        body: JSON.stringify(guards()),
      });
      if (live.current) setNote("Restored. The settings below are the restored ones.");
      await onChanged();
      await reload();
    });
  }

  async function exportOverrides() {
    await run(async () => {
      const envelope = await apiFetch<ConfigExport>("/api/config/overrides/export");
      const blob = new Blob([JSON.stringify(envelope, null, 2)], {
        type: "application/json",
      });
      const stamp = envelope.exported_at.replace(/[:.]/g, "-");
      if (live.current) {
        setDownload({ href: URL.createObjectURL(blob), name: `autoposter-overrides-${stamp}.json` });
      }
    });
  }

  async function choose(file: File | undefined) {
    if (file === undefined) return;
    setPending(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(await file.text());
    } catch {
      setError(`${file.name} could not be read as JSON.`);
      return;
    }
    const envelope = parsed as ConfigExport;
    if (
      envelope === null ||
      typeof envelope !== "object" ||
      envelope.autoposter_overrides !== 1 ||
      typeof envelope.document !== "object"
    ) {
      // Checked here as well as on the server, because the two refusals say
      // different things: the server's is about a format, and this one is
      // about the file the operator just picked.
      setError(`${file.name} is not an Autoposter overrides backup.`);
      return;
    }
    await run(async () => {
      const preview = await apiFetch<ConfigPreviewResponse>("/api/config/preview", {
        method: "POST",
        body: JSON.stringify(
          revision === null
            ? { document: envelope.document }
            : { document: envelope.document, expected_revision: revision },
        ),
      });
      if (live.current) setPending({ envelope, preview, name: file.name });
    });
  }

  async function importPending() {
    if (pending === null) return;
    await run(async () => {
      await apiFetch<ConfigSaveResponse>("/api/config/overrides/import", {
        method: "POST",
        body: JSON.stringify({ ...pending.envelope, ...guards() }),
      });
      if (live.current) {
        setPending(null);
        setNote(`Imported ${pending.name}.`);
      }
      await onChanged();
      await reload();
    });
  }

  return (
    <section className="panel config-safety">
      <h2>Backup and previous versions</h2>

      {error !== null && <p className="page-error">{error}</p>}
      {note !== null && <p className="config-saved">{note}</p>}

      <label className="config-safety-confirm">
        <input
          type="checkbox"
          checked={confirm}
          onChange={(event) => setConfirm(event.target.checked)}
        />
        Allow this to remove settings I have saved
      </label>
      <p className="muted">
        Restoring or importing an older set of settings removes anything saved
        since. Without this ticked, the server refuses a change that would
        remove more than a few.
      </p>

      <h3>Previous versions</h3>
      {snapshots.length === 0 ? (
        <p className="muted">No previous versions yet — one is kept before every save.</p>
      ) : (
        <ul className="config-safety-list">
          {snapshots.map((snapshot) => (
            <li key={snapshot.id}>
              <span>{describe(snapshot)}</span>
              <button type="button" disabled={busy} onClick={() => void restore(snapshot.id)}>
                Restore
              </button>
            </li>
          ))}
        </ul>
      )}

      <h3>Backup file</h3>
      <p className="config-safety-warning">{EXPORT_WARNING}</p>
      <button type="button" disabled={busy} onClick={() => void exportOverrides()}>
        Download a backup
      </button>
      {download !== null && (
        <a
          className="config-safety-download"
          data-testid="config-export-link"
          href={download.href}
          download={download.name}
        >
          {`Save ${download.name}`}
        </a>
      )}

      <label className="config-safety-import" htmlFor="config-import-file">
        Restore from a backup file
      </label>
      <input
        id="config-import-file"
        type="file"
        accept="application/json,.json"
        disabled={busy}
        onChange={(event) => void choose(event.target.files?.[0])}
      />
      {pending !== null && (
        <>
          <p className="muted">
            {`${pending.name} is a valid backup. ${
              pending.preview.restart_required.length > 0
                ? `Restart required to apply: ${pending.preview.restart_required.join(", ")}.`
                : "Nothing in it needs a restart."
            }`}
          </p>
          <button type="button" disabled={busy} onClick={() => void importPending()}>
            Import these settings
          </button>
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 5: Run the panel tests to verify they pass**

```bash
docker compose -p pcs5 run --name pcs5-green1 web npm test -- ConfigSafetyPanel > .superpowers/run-p-configsafety-t5-green1.log 2>&1
docker rm pcs5-green1
```

**Read the log.** Expected: **0 failed**, **15 passed**.

Two foreseeable failures with one correct fix each:

- `File.prototype.text` missing in this jsdom version → the "refuses a file that is
  not JSON" and the import tests error on `file.text is not a function`. The fix is in
  the *test*: replace `new File([...])` with an object literal carrying a `text()`
  returning a promise and a `name`, and cast it. Do not change the component to use
  `FileReader`.
- `URL.createObjectURL` still undefined despite the `beforeEach` stub → stub it as
  `vi.stubGlobal("URL", Object.assign(Object.create(URL), {...}))` or assign directly
  onto `globalThis.URL.createObjectURL`. Again, the test moves, not the component.

- [ ] **Step 6: Mount it from Settings**

In `frontend/src/pages/Settings.tsx`, add the import beside the other page imports:

```tsx
import { ConfigSafetyPanel } from "./ConfigSafetyPanel";
```

and render it immediately after the "Running configuration" `</section>`
(`Settings.tsx:751`), before the `{dirty && (` block:

```tsx
      {config !== null && (
        <ConfigSafetyPanel
          revision={storedRevision}
          onChanged={async () => {
            adopt(await apiFetch<ConfigResponse>("/api/config"));
          }}
        />
      )}
```

Append to `frontend/src/pages/Settings.test.tsx`, inside the existing
`describe("Settings configuration")`:

```tsx
  it("offers backup and previous versions on the settings page, not a new route", async () => {
    // The panel does its own fetching; `stubConfig` answers every request with
    // the config body, which the panel reads as an empty snapshot list.
    stubConfig();
    await renderSettings();

    expect(
      screen.getByRole("heading", { name: "Backup and previous versions" }),
    ).toBeInTheDocument();
  });
```

- [ ] **Step 7: The styles**

Append to `frontend/src/pages/settings.css`, above the `@media (max-width: 640px)`
block (so the existing responsive rules stay last):

```css
.config-safety-list {
  list-style: none;
  margin: 8px 0;
  padding: 0;
}

.config-safety-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
}

.config-safety-confirm {
  display: flex;
  align-items: center;
  gap: 8px;
}

.config-safety-warning {
  color: var(--warn);
}

.config-safety-import {
  display: block;
  margin-top: 12px;
}

.config-safety-download {
  display: inline-block;
  margin-left: 12px;
}
```

Check `var(--border)` and `var(--warn)` exist before using them:
`grep -n "\-\-border\|\-\-warn" frontend/src/styles.css` (or wherever the tokens live —
`frontend/src/styles.test.ts` pins them). If `--border` is not a real token, use
whichever separator token that file defines; do not invent one, `frontend/src/styles.test.ts`
will go red.

- [ ] **Step 8: Full frontend suite and typecheck**

```bash
docker compose -p pcs5 run --name pcs5-vitest web npm test > .superpowers/run-p-configsafety-t5-vitest.log 2>&1
docker rm pcs5-vitest
docker compose -p pcs5 run --name pcs5-tsc web npx tsc --noEmit > .superpowers/run-p-configsafety-t5-tsc.log 2>&1
docker rm pcs5-tsc
```

**Read both logs.** Expected: vitest **0 failed**, up **16** from T2's total (15 panel
+ 1 Settings mount); `tsc --noEmit` silent. If a pre-existing `Settings.test.tsx` test
now fails on an unstubbed `/api/config/snapshots` fetch, the fix is in that test's stub
(`stubEditor` rejects unstubbed non-GET calls; the snapshot listing is a GET and is
answered by the config body, which the panel tolerates) — read the log and add the
route to `stubEditor`'s GET arm rather than removing the panel from the page.

- [ ] **Step 9: Commit the UI**

```bash
git diff --stat HEAD -- src/ tests/ frontend/ alembic/
git add frontend/src/pages/ConfigSafetyPanel.tsx frontend/src/pages/ConfigSafetyPanel.test.tsx frontend/src/pages/Settings.tsx frontend/src/pages/Settings.test.tsx frontend/src/api/types.ts frontend/src/pages/settings.css
git commit --no-gpg-sign -m "feat(settings): backup, previous versions and one-click restore

A panel in the settings page listing the twenty kept documents with a Restore
button each, a backup download that says what the file contains, and an import
that previews before it writes. Everything destructive is gated on a checkbox
the operator ticks, and a stale restore or import re-reads and says so rather
than retrying."
```

The `git diff --stat` must list only this task's six code files, and **must not list
`src/autoposter/config/schema.py`**.

- [ ] **Step 10: File and close the roadmap row**

Append to the gap table in
`docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, immediately after row 230
(`:328`), matching that table's six-column shape exactly:

```
| 231 | `PUT /api/config/overrides` accepted a whole-document replace with no evidence the sender knew what it was replacing (CLOSED — config-safety) | Two independent defects, one family, and between them they explain every observation of the 2026-09-01 wipe. **D1:** `OverridesBody` was a plain `BaseModel`, so pydantic v2's default `extra="ignore"` matched a body of `{"artwork": ..., "scheduler": ...}` — the overrides document sent bare, which is what a hand-written fetch produces — against zero declared fields, bound `document` to its `{}` default, and the unconditional `ON CONFLICT DO UPDATE` wrote it over seventeen stored overrides with a 200 and two plausible version stamps. Reproduced deterministically. **D2:** four pages (`Settings`, `CatalogPanel`, `GroupsPanel`, `CustomCollectionsPanel`) each seed the WHOLE document at mount and PUT the whole result, and nothing invalidated a seed when a different page wrote — a textbook lost update, and the reason the operator's own `collections.separator_style: sand` save did not stick while the identical second one did. That observation is the operator's, and it is what promoted D2 from a structural argument to a confirmed defect. Closed by: `extra="forbid"` on the envelope (the document stays lenient — `unknown_key_paths` gives a better error at depth); an empty-document refusal and a >3-path drop cap, both `confirm: true`-overridable, on the WRITE path only so a preview is never refused for being destructive; a content-hash `overrides_revision` served on `GET /api/config`, sent back by all four pages, and compared under `SELECT ... FOR UPDATE` inside the writing transaction, with a stale save answering 409 and every page re-seeding rather than retrying; path counts on the audit row, so the next such event reads `12 -> 0` in the log instead of costing an investigation; twenty pre-write snapshots in `config_override_snapshots` captured in the same transaction as the upsert, with list/read/restore endpoints and a restore that is itself snapshotted; and an unredacted export/import envelope whose import is a save through the same guarded path. Deliberately no `config/schema.py` change: the drop cap and the retention are module constants, because a destructiveness cap an operator can raise from the page the destructive write comes from is not a cap | M — shipped | none (module constants, not settings) | — |
```

Then append the closing note beneath the table, after the existing
"Rows 143/173/179's `Depends on` cells…" paragraph:

```
Row 231 was filed and closed by the config-safety phase, which was cut ahead of
the overlay era at the operator's direction after a live configuration wipe.
Both of its defects have reproduction tests that fail against `origin/main`
(`tests/test_api_config_editor.py`), which is the whole reason it is one row
rather than two: fixing either alone leaves the incident reachable by the other
route. Two operator verifications from the phase that preceded it remain open
and are unaffected by this work — the Radarr/Sonarr tag round-trip (row 89b)
and the MDBList list test, which is the test that was running when the wipe
happened and which never completed.
```

- [ ] **Step 11: The progress entry**

Append to `.superpowers/sdd/progress.md`:

```
CONFIG SAFETY SHIPPED (row 231, the overrides-wipe phase): both defects fixed
behind one gate. D1 the unwrapped-body silent discard (`extra="forbid"` on the
envelope) and D2 the four-page whole-document lost update (content-hash
`overrides_revision`, compared under SELECT FOR UPDATE, 409 + re-seed on all
four writing pages), each with a RED reproduction of the real observation. Plus
the empty-document refusal and the >3 drop cap on the write path only, path
counts on the audit row, twenty in-transaction pre-write snapshots with
list/read/restore, and an unredacted export/import whose import is a save
through the same guarded path. NO config/schema.py change — the phase is
strictly disjoint from the queued overlay era. Suites <backend>/<vitest>.
Residual, documented not silent: two *simultaneous* first-ever saves on a
deployment with no overrides row yet can still race, because there is no row to
lock; the drop cap bounds it. Still open and unaffected: row 89b's Arr tag
round-trip and the MDBList list test — the test that was running when the wipe
happened. NEXT per the ratified trajectory: the overlay era (Phase A T1, which
has been holding for this).
```

Replace `<backend>/<vitest>` with the measured totals from Step 8 and Task 4.

- [ ] **Step 12: Write the PR body**

Create `.superpowers/sdd/p-configsafety-pr-body.md` — **not committed**. It must
narrate the incident honestly: both defects, both reproductions, and the operator's own
evidence crediting the second.

```markdown
## Config safety — the overrides-wipe fix (roadmap row 231)

On 2026-09-01 the stored configuration overrides went from seventeen paths to
zero through a request that answered `200 OK`, and the attempted restore also
answered `200 OK` and stored nothing. This is the fix, and the backup the
operator asked for so that a repeat is a click rather than an investigation.

### What was actually wrong — two defects, not one

**D1 — the silent body-shape discard.** `OverridesBody` was a plain
`pydantic.BaseModel` with no `model_config`, so pydantic v2's default
`extra="ignore"` was in force. A body of `{"artwork": ..., "scheduler": ...}` —
the overrides document sent *unwrapped*, which is what a hand-written `fetch`
produces — matched zero declared fields, bound `document` to its `{}` default,
and was written over everything by an unconditional upsert. `{}` is a fully
valid document (it is the *documented* revert-everything), so nothing refused
it. 757 bytes of good JSON were read off the wire and thrown away with a 200.

**D2 — the whole-document lost update.** Four pages write this row —
`Settings`, `CatalogPanel`, `GroupsPanel` and `CustomCollectionsPanel` — and
each of them seeds the *whole* document once at mount and PUTs the whole
result. That is required by the API's shape. What was missing was any way for
the server to tell a save composed against the current document apart from one
composed against a document that had since moved, so a page holding a stale
mount-time seed silently deleted everything another page had saved in between,
and got a 200 for it.

**D2 is the operator's find.** The structural argument for it existed, but what
promoted it from argument to confirmed defect was their own observation during
the restoration: a Settings save of `collections.separator_style: sand` did not
stick, and the identical second save did. No body-shape mistake can produce
that. It is the signature of a lost update, and it is why this PR fixes two
things instead of one — **fixing either alone leaves the incident reachable by
the other route.**

### The reproductions

Both defects have tests that fail against `origin/main` and pass here, driven
through the real endpoints:

- `test_an_unwrapped_document_body_is_refused_not_silently_emptied` — was 200
  and a wipe; is now 422.
- `test_the_incident_restore_body_is_refused_rather_than_written_as_empty` —
  the restore attempt, including why `version_before == version_after`.
- `test_the_two_page_stale_save_is_refused_instead_of_clobbering` — page A
  mounts, page B saves `separator_style: sand`, page A saves anything; was a
  200 that deleted `sand`, is now a 409 that names it.

### What shipped

1. `extra="forbid"` on the request *envelope* only. The config document stays
   lenient at pydantic level because `unknown_key_paths` gives a better error at
   full depth; only the envelope forbids extras.
2. An empty-document refusal and a >3-path drop cap, both overridable with
   `confirm: true` (the `DeleteRequest.confirm` precedent), on the **write**
   path only — `POST /api/config/preview` is never refused for describing a
   destructive edit.
3. A content-hash `overrides_revision` on `GET /api/config`, sent back by all
   four writing pages and compared under `SELECT … FOR UPDATE` inside the
   writing transaction. A stale write is a 409; every page re-seeds and tells
   the operator, and **no page retries** — a retry would re-apply an edit onto
   a document nobody has seen. A content hash rather than `updated_at`, which
   moves on a no-op rewrite and which on one recorded deployment travels
   backwards.
4. Path counts on the audit row. This incident would have read `12 -> 0` in
   `events_log` instead of costing an investigation.
5. Twenty pre-write snapshots in a new `config_override_snapshots` table,
   captured in the *same transaction* as the write and pruned inline, with
   list / read / restore endpoints. A restore is a save: re-validated, drop-
   capped, and snapshotted first so it is itself undoable.
6. Export/import with an `{"autoposter_overrides": 1, …}` envelope. The export
   is **unredacted deliberately** — a redacted backup would write the bare
   notification host back over the real URL on the next import — and the UI
   says so where the download happens. The import is a save with a file picker
   in front of it, not a second code path.
7. A "Backup and previous versions" panel in the settings page.

### Deliberate non-changes

- **No `config/schema.py` change of any kind.** The drop cap and the snapshot
  retention are module constants. A destructiveness cap an operator can raise
  from the page the destructive write comes from is not a cap — and keeping out
  of that file makes this phase strictly disjoint from the queued overlay era.
- **A scripted client that sends no `expected_revision` still works.** The four
  pages are held to sending it by their own tests, not by the endpoint.

### Known residual, stated rather than hidden

When no overrides row exists yet, `SELECT … FOR UPDATE` has nothing to lock, so
two *simultaneous* first-ever saves on a fresh deployment can still race. Both
writers legitimately saw `{}`, the upsert keeps the row consistent, and the
drop cap bounds what the loser can lose. Documented in
`load_overrides_document`'s docstring.

### Operator checkboxes

Both predate this phase and neither is affected by it:

- [ ] Row 89(b): the live Radarr/Sonarr tag round-trip.
- [ ] The MDBList list test — **the test that was running when the wipe
      happened, and which never completed.** Worth re-running now that a
      mis-shaped write is refused and a snapshot exists either way.
```

- [ ] **Step 13: The whole-branch verification**

```bash
docker compose -p pcs5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pcs5-full test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-p-configsafety-final.log"
docker wait pcs5-full
docker rm pcs5-full
docker compose -p pcs5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pcs5-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-p-configsafety-final-ruff.log"
docker rm pcs5-ruff
docker compose -p pcs5 down
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD -- src/autoposter/config/schema.py
```

**Read both logs.** Expected: pytest **0 failed** at the total Task 4 recorded; ruff
`All checks passed!`. `git log` must show seven commits (the plan, T1, T2's two, T3,
T4, T5's two). **`git diff --stat` against `config/schema.py` must print nothing** —
that is Global Constraint 3's proof for the whole branch, and it is the last thing
checked before the PR.

- [ ] **Step 14: Commit the paperwork**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md .superpowers/sdd/progress.md
git commit --no-gpg-sign -m "docs(roadmap): row 231 files and closes — the overrides-wipe incident"
```

`.superpowers/` is gitignored, so the `git add` of `progress.md` will be refused unless
it is already tracked. Check with `git check-ignore -v .superpowers/sdd/progress.md`;
if it is ignored, drop it from the `git add` and leave the entry as a working-tree note
— that is where the other phases' entries live. **Never** `git add -f` it.

- [ ] **Step 15: Hand off**

Use `superpowers:finishing-a-development-branch`. Push, open the PR with
`.superpowers/sdd/p-configsafety-pr-body.md` as the body (no AI attribution anywhere
in it), and arm the CI monitor on the PR's status. Row 231 closes on merge.

**Task 5 report must record:** the final backend and vitest totals, confirmation that
the `config/schema.py` diff is empty for the whole branch, the seven-commit log, and
whether `progress.md` was committable or left as a working-tree note.

---

## Self-review

**Spec coverage.** Facts C1.1 → T1 (envelope, empty refusal, drop cap N=3, `confirm`,
audit counts, all in `_persist_and_swap`). C1.2 → T2 (content hash, `overrides_revision`
on GET, `expected_revision` on the body, `SELECT … FOR UPDATE` inside the upsert's
transaction, loud 409, no silent retry, all four pages re-seed, preview
accepts-and-ignores). C1.3 → T3 (table, `config/snapshots.py` not `api/snapshots.py`,
in-transaction pre-write capture, N=20 pruned inline, list/get/restore, restore through
`_validated_generation` and itself snapshotted, `MIGRATED_SECTIONS` stripped). C1.4 →
T4 (the envelope, import as a save, unredacted with the trade stated in the docstring
and the UI copy). C1.5 → T5 (snapshot list + restore, export/import, the 409 recovery;
T2 and T5 sequential on `Settings.tsx`). C2 → T1 Step 2 and T2 Step 1 (both RED
reproductions), T1 Step 2 (`…normal_one_field_edit…`, the drop cap's negative), T3
Step 3 (`…refused_write_leaves_no_snapshot_orphan`, `…restore_round_trip…`), T5 Steps
2–5 (the entry-point law for the UI flows), T1 Step 1 (the measured baseline), Global
Constraint 3 + every task's `git diff --stat` (no `config/schema.py`). C3 → T5 Steps
10–12. C4 → five tasks, `fix/config-safety` from `origin/main`, `p-configsafety-`
artifacts, `pcs1`–`pcs5`, the alembic head check at T3 Step 1.

**Type consistency.** `_persist_and_swap`'s keyword-only signature is introduced in T1
(`confirm`, `reason`), extended once in T2 (`expected_revision`), and every later call
site in T3 and T4 uses all three. `document_revision`/`EMPTY_DOCUMENT_REVISION`/
`without_migrated_sections`/`load_overrides_document(…, for_update=…)` are defined in
T1–T2 and consumed under those exact names in T3 and T4.
`saveBody`/`revisionFromConfig`/`STALE_SAVE_NOTE` are defined in T2 Step 10 and used
under those names in T2 Steps 14–15 and T5 Step 4. `SNAPSHOT_RETENTION`,
`capture_snapshot`, `list_snapshots`, `load_snapshot` are defined in T3 Step 5 and used
in T3 Steps 6–7. `ConfigSnapshot`/`ConfigExport` are declared in T5 Step 1 and consumed
in T5 Step 4.

**Two adjudications this plan makes beyond the facts, flagged for the reviewer.**
(1) The safety UI is a new component file, `ConfigSafetyPanel.tsx`, rather than more of
`Settings.tsx` — the recon says "a panel in the existing save area, not a new page",
and this is a panel; the split is about file size and the T2/T5 collision, and it
reduces T5's `Settings.tsx` edit to two lines. (2) `ConfigOverrideSnapshot.reason` is
`NOT NULL` where the recon's sketch had it nullable — every writer knows its own
reason, so a nullable column could only ever record that somebody forgot.
