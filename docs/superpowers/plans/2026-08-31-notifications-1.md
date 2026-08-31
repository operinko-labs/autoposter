# Notifications 1 — the event taxonomy and per-collection webhooks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close roadmap rows 18 and 19 — row 18 as a documentation correction (it shipped on 2026-08-22 and the doc was never updated), row 19 by growing the shipped dispatcher's two events into the taxonomy the row asks for: `run_start` and a first-class `error` at the scheduler boundary, and per-collection `changes`/`delete` webhooks configured on the definition that owns the collection.

**Architecture:** Three tasks. Task 1 adds the two global events at the one boundary that already knows a run started and a run failed (`scheduler/core.py:_maybe_run`) — no new machinery, one generalised `_start_notification`, and row 18's doc close folded in. Task 2 is the design task: a `changes_webhook` field on `CollectionDefinition` (the `labels`/`sync_mode` precedent), the applied member deltas surfaced out of the reconciler through a small out-param, the engine *collecting* per-collection notifications rather than sending them, and `service.reconcile_libraries` dispatching them after its per-library commit — the same after-the-commit rule both shipped call sites follow. `Notifier.send` gains an optional `url` so one notifier serves the global target and every per-collection target. Task 3 wraps: full suites, row closes, PR body, no push.

**Tech Stack:** Python 3.14 + FastAPI + SQLAlchemy + pytest (compose `test` service), vitest via the compose `web` service, ruff.

**Binding requirements:** `.superpowers/sdd/p-notif-facts.md` (C1–C4, adjudicated 2026-08-31). Where this plan and that file could ever disagree, that file wins. Evidence base: `.superpowers/sdd/p-notif-recon.md`; every line number below was re-verified against `6718640` (chore/sweep-5's tip) at plan time.

## Global Constraints

Every task's requirements implicitly include this section.

1. **Scope (facts C1).** Rows IN: **18** (closes as a doc correction, citing `dcdaf0f`/`d233ce7`/`ff6b17c`/`93cd3c9`) and **19** (the taxonomy plus per-collection webhooks). Rows OUT: **20** (Discord/Apprise formatting — the roadmap's own phase-5a note and phase 15 both sanction the deferral; row 20 stays open with a note), **17's webhook half** and **21** (both blocked on the 5c live round-trip: the intake endpoint must exist before the user can point Tracearr at it). No Discord payload mode, no `/webhook/tracearr` route, no notifications settings UI in this phase.
2. **The trusted-sink doctrine (rows 117/207/209/213).** The pod log (stdout) keeps full detail; every served surface is redacted or class-name-only. A notification payload is a served surface: it carries `scheduled_runs.last_detail`, which is already class-name-only for an unmarked exception (`scheduler/core.py:183-187`). **A webhook URL is never logged or stored in full — host only** (`notify/dispatch.py:88-92,134-140,158-171`), and that rule extends unchanged to the per-collection URLs this phase adds.
3. **RED before GREEN, at the dispatch seam (facts C2 — the law).** Every new event is pinned by a test that drives a **real** `build_notifier` over an `httpx.MockTransport` and asserts the **exact POSTed JSON body**, not a fake notifier's recorded arguments. Each such test is seen failing before the implementation exists. Suite totals are stated before they are measured, then measured.
4. **No live targets, no operator URLs.** No test may reach n8n, Discord, or any real host (`conftest`'s `no_outbound_network` fails a test that escapes the mock transport). Fixture URLs are obvious fakes on the `.test` TLD (`hooks.example.test`), following `tests/test_notify_dispatch.py:33-35`; the token in a fixture URL stays the existing obvious fake shape (`tok-SECRET123`). Nothing that could pass for a real token, host or key ever enters a test or the example config.
5. **The frozen-section split (facts adjudication 3).** The global `notifications` block stays a `FROZEN_SECTIONS` entry (`config/live.py:39-42`) with its wording **unchanged** — the notifier is still built once at startup. The per-collection URLs live on `collections.definitions[].changes_webhook`, which is **not** frozen: the engine reads the definition off the live config on every pass, so an edited webhook applies at the next pass. This split is pinned by a test (Task 2), not merely asserted in prose.
6. **Never round-trip a config section into overrides** (`frontend/src/api/overrides.ts:16-18`). `changes_webhook` is a field on `CollectionDefinition`, so it inherits `collections.definitions`' existing wholesale-list-replace semantics via `config/overrides.py` — the precedent `labels`, `sync_mode` and `filters` already have. No new overrides plumbing, and no UI in this phase.
7. **Notification failure never fails the work it reports on.** `Notifier.send` never raises by contract; every new call site is fire-and-forget with a strong task reference and a done-callback, exactly as `scheduler/core.py:215-234` and `api/routes.py:100-130` already do it.
8. **Container discipline (the standing recipe).** Unique compose project per task (`pnf1`, `pnf2`, `pnf3`), always the `.superpowers/isolated-db.yml` overlay for the `test` service, always `sh -c` with `tee` to a path under `/app/.superpowers/` **inside** the container — never rely on streamed stdout (it is filtered). **No `--rm` anywhere**: use `run --name <project>-<label>`, read the log from the host, then `docker rm <project>-<label>`. The full suite runs detached (`run -d --name`), is waited on with `docker wait`, and its log is read from the host. Teardown is `docker compose -p <project> down` — **never** `down -v`. Two pytest commands against the same compose project are unsafe — keep the project names unique per task.
9. **Frontend tests** (Task 3 only) via the compose `web` service with a host-side redirect: `docker compose -p pnf3 run --name pnf3-web web npm test > .superpowers/run-p-notif-web.log 2>&1`, then read the log from the host and `docker rm pnf3-web`.
10. **Timeouts (row 131's derivation):** full backend suite `timeout -s KILL 2700`; targeted files `timeout -s KILL 600`.
11. **Commits.** Conventional messages, `--no-gpg-sign`, staged **by name** (never `git add -A`). No `Co-Authored-By` and no AI attribution of any kind, in commits or the PR body.
12. **Branch (facts C4).** `feat/notifications-1` cut from `chore/sweep-5`'s tip — **SECOND in a two-deep stack** (PR #111 is pending below it). Cut is verified with a **content probe, never a sha probe**: `tests/test_citation_anchors.py` must exist on the cut branch (it is sweep-5's Task 4 deliverable and exists nowhere else). When #111 rebases, this branch rebases onto it; Task 3's PR body says so.
13. **Artifacts.** Phase artifacts under `.superpowers/sdd/` use the `p-notif-` prefix; run logs are `.superpowers/run-p-notif-*.log`. The PR body goes to `.superpowers/sdd/p-notif-pr-body.md` — gitignored, never committed. **Push and PR creation are NOT in this plan**: the user gates the PR.
14. **Baselines (facts C4):** 4198 backend / 358 frontend, ruff clean. Stated deltas: **+3 after Task 1 (4201)**, **+9 after Task 2 (4210)**; frontend unchanged at 358.
15. **Sequencing.** Task 2 depends on Task 1 only for the branch and the plan commit, but the tasks run **sequentially** — shared tree, the standing rule.

## Adjudications Made by This Plan

Decisions the facts file left open, settled here so no executor guesses. Each is disclosed again in row 19's close (Task 3).

- **Wire names follow the shipped naming style, and the plan states the mapping.** The shipped events are `scheduled_run_completed` and `full_pass_enqueued` — subject-first, past-tense. Row 19's taxonomy names map onto that style: `run_start` → **`scheduled_run_started`**, `error` → **`scheduled_run_failed`**, `changes` → **`collection_changed`**, `delete` → **`collection_deleted`**. The last one is deliberately the *exact* string the unrouted `EventLog(event_type="collection_deleted")` already uses (`collections/engine.py:1089`), per facts adjudication 4: one name for one fact, whether it lands in the events log or on a webhook.
- **`error` is ADDITIVE, not a replacement.** A failed run emits `scheduled_run_completed` (unchanged, with `status: "failed"`) **and then** `scheduled_run_failed`. Removing or renaming a shipped event would break the user's n8n flow, which is row 18's whole point; two POSTs for a failed run is the cheap side of that trade. Both carry `type: "failure"` in apprise-json mode, so an existing `body.type === "success"` gate is unaffected.
- **No per-event on/off toggles.** The facts ask for the events, not a switchboard; new `notifications.*` keys would each need a frozen-section justification. YAGNI. `run_start` fires once per *due* job run (jobs run on hour-scale cadences, not per poll), so the volume is the same as the completion event that already ships.
- **`changes` is opt-in per definition and NEVER falls back to the global URL.** A membership change fires only when that definition sets `changes_webhook`. The global target must not start receiving a POST per changed collection per pass — that is an unbounded volume change to a shipped integration.
- **`delete` DOES fall back to the global URL.** A swept delete routes to the family definition's `changes_webhook` when the deleted collection belongs to a definition that sets one, and to the global target otherwise. Deletes are rare, destructive and hard-capped by `collections.max_deletes` (default 5, `config/schema.py:743`), and a collection deleted because *no definition builds it any more* has no per-collection webhook to route to by construction. Disclosed in row 19's close as the one new event an existing global consumer will see.
- **Per-collection webhooks are gated on `notifications.enabled`.** `build_notifier` is untouched: a disabled config yields `NullNotifier` and nothing sends, and enabled-without-a-global-`url` still yields `NullNotifier` with its one build-time warning (`notify/dispatch.py:45-53`, pinned by `tests/test_notify_dispatch.py:249-266`). An operator who wants only per-collection webhooks sets a global `url` too. Named in the example config's comment so it is not a surprise.
- **The engine collects, the service dispatches.** `run_library` returns the notifications it *would* send on `LibraryRun.notifications`; `service.reconcile_libraries` sends them after its per-library `session.commit()` (`collections/service.py:450`). This keeps the notifier out of the engine's eleven call sites, and it is the only placement that honours the shipped "never describe a run the database does not yet show" rule. The preview endpoints call `run_library` with `dry_run=True` and never see the notifier at all.
- **A dry run never notifies.** Both collection events are gated on `not dry_run`, so a periodic dry-run pass (`apply_to_plex: false`) stays silent — it changed nothing to report.
- **Applied deltas come from a small out-param, not from the preview counts.** `DefinitionResult.adding/removing` are documented as *preview* counts and are zero on a real pass (`collections/engine.py:80-84,647`). The applied numbers exist only as locals in `lists.py:357`. Rather than change `reconcile_list_collection`'s return shape (many callers), it gains an optional `deltas: dict | None` it fills with the applied `added`/`removed`; `_run_one` stamps them onto **new** `DefinitionResult.added/removed` fields, kept distinct from `adding/removing` — "what happened" vs "what would happen". The preview API builds its response dict field by field (`api/collections_builders.py:362-363`), so the new fields reach no served surface and no frontend type changes.
- **Smart (Plex smart-collection) definitions emit no `changes` event.** Plex evaluates their membership itself, so this service has no member diff to report (`collections/engine.py:410-416` says the same about the preview counts). Stated as a known limitation in row 19's close.
- **`changes_webhook` rides along on expansion.** It is added to `_INHERITED_BY_EXPANSION` (`collections/engine.py:165-179`) for row 141's reason exactly: the placeholder is the only definition an operator writes for a family, so a webhook on it is a webhook for every unit. Pinned by extending the existing `_RIDE_ALONGS` fixture rather than by a new test.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `docs/superpowers/plans/2026-08-31-notifications-1.md` | **Commit as-is** (this plan) | T1 |
| `src/autoposter/scheduler/core.py` | **Modify** (`:161`, `:204-224`). The `scheduled_run_started` send, the `scheduled_run_failed` send, `_start_notification` generalised | T1 |
| `tests/test_scheduler_core.py` | **Modify.** A webhook-catcher harness + 3 new tests | T1 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modify.** Row 18's close (T1); rows 19/20/17/21 (T3) | T1, T3 |
| `src/autoposter/config/schema.py` | **Modify** (`CollectionDefinition`, after `filters` at `:375`). `changes_webhook` | T2 |
| `src/autoposter/notify/dispatch.py` | **Modify** (`:65-66`, `:94-140`, plus a module-level helper). Optional `url` per send; `send_in_background` | T2 |
| `src/autoposter/collections/lists.py` | **Modify** (`:165-189` signature, after `:362`). The `deltas` out-param | T2 |
| `src/autoposter/collections/engine.py` | **Modify** (`:77-125`, `:165-179`, `:259-518`, `:712-735`, `:919-930`, `:1087-1103`). `CollectionNotification`; collection of both events | T2 |
| `src/autoposter/collections/service.py` | **Modify** (`:390-399`, after `:450`). `notifier` param; dispatch after the commit | T2 |
| `src/autoposter/scheduler/jobs.py` | **Modify** (`:47-54`, `:105-108`). `notifier` threaded into the pass | T2 |
| `src/autoposter/app.py` | **Modify** (`:291-294`). Hand the job the notifier built at `:195` | T2 |
| `config/autoposter.example.yaml` | **Modify** (the commented `definitions:` example, `:95-161`). Document `changes_webhook` | T2 |
| `tests/test_notify_dispatch.py` | **Modify.** 2 new tests for the per-send URL | T2 |
| `tests/test_builder_knobs.py` | **Modify.** 5 new tests: the two events end to end through `reconcile_libraries` | T2 |
| `tests/test_builder_engine.py` | **Modify** (`:644-658`). `_RIDE_ALONGS` gains the field | T2 |
| `tests/test_collection_config.py` | **Modify.** The schema default | T2 |
| `tests/test_config_live.py` | **Modify.** The frozen split | T2 |
| `.superpowers/sdd/p-notif-pr-body.md` | **Create** (gitignored, NEVER committed) | T3 |

---

### Task 1: The global taxonomy — `run_start`, first-class `error`, and row 18's close

**Files:**
- Commit: `docs/superpowers/plans/2026-08-31-notifications-1.md`
- Modify: `src/autoposter/scheduler/core.py:161`, `:204-224`
- Modify: `tests/test_scheduler_core.py` (harness after `_RecordingNotifier`, `:303-332`; 3 tests after `test_a_failed_job_notifies_with_failed_status`, `:356-...`)
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (row 18 — file line 114 at plan time; **locate it by its row number**, not by line)

**Interfaces:**
- Consumes: `Notifier.send(event: str, summary: str, detail: dict) -> bool` and `NullNotifier.send` (`notify/dispatch.py:65,94`); `build_notifier(config, http, session_factory)` (`:39`); `NotificationsConfig` (`config/schema.py:1187-1209`); `Scheduler(session_factory, jobs, poll_seconds=60, notifier=None)` (`scheduler/core.py:116-131`).
- Produces: two new wire events, `scheduled_run_started` (detail `{"job": name}`) and `scheduled_run_failed` (detail `{"job": name, "status": "failed", "detail": <truncated>}`); a generalised `Scheduler._start_notification(event: str, summary: str, detail: dict) -> None`. Task 3 counts **+3** backend tests from here.

**Readers of the changed surface, enumerated (constraint 2):** the only new outbound data is the job *name* (already in the shipped `scheduled_run_completed` payload) and `scheduled_runs.last_detail`'s truncated copy (already in that same payload, and already class-name-only for an unmarked exception — `scheduler/core.py:176-187`). No new field, column or endpoint is exposed. `_start_notification`'s only caller is `_maybe_run`; verified with `grep -rn "_start_notification" src tests` at plan time (one definition, one call).

- [ ] **Step 1: Cut the branch and commit this plan**

```bash
git checkout chore/sweep-5
test -f tests/test_citation_anchors.py || { echo "WRONG BASE: this is not sweep-5's content"; exit 1; }
git checkout -b feat/notifications-1
git add docs/superpowers/plans/2026-08-31-notifications-1.md
git commit --no-gpg-sign -m "docs(plans): the notifications-1 plan"
```

Expected: the content probe prints nothing and the branch is created. If the probe fails, STOP — the base is wrong and every line number in this plan is unverified against it.

- [ ] **Step 2: Add the webhook-catcher harness to `tests/test_scheduler_core.py`**

Append these imports to the existing import block at the top of the file (keep the existing ones; add only what is missing):

```python
import json

import httpx

from autoposter.config.schema import NotificationsConfig
from autoposter.notify.dispatch import build_notifier
```

Then, directly below `_RecordingNotifier` (which ends at `:332`), add:

```python
HOOK_HOST = "hooks.example.test"
HOOK_URL = f"http://{HOOK_HOST}/notify/tok-SECRET123"


def _refuse_db():
    raise AssertionError("a successful send must never touch the events log")


class _Catcher:
    """A webhook catcher: the real dispatcher POSTs into this.

    These tests assert the payload the operator's endpoint actually receives,
    not the arguments a fake notifier recorded -- the dispatch seam is where a
    wrong shape would reach n8n. ``seen`` lets a test await one fire-and-forget
    send instead of sleeping: the events it will wait for are named at
    construction, because a waiter created after the POST landed would wait
    forever.
    """

    def __init__(self, *events: str):
        self.bodies: list[dict] = []
        self._waiters = {
            f"autoposter: {event}": asyncio.Event() for event in events
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        waiter = self._waiters.get(body["title"])
        if waiter is not None:
            waiter.set()
        return httpx.Response(200)

    async def seen(self, event: str) -> dict:
        """Wait for one event and return its body."""
        await asyncio.wait_for(self._waiters[f"autoposter: {event}"].wait(), timeout=5)
        return self.body(event)

    def body(self, event: str) -> dict:
        (found,) = [b for b in self.bodies if b["title"] == f"autoposter: {event}"]
        return found

    @property
    def titles(self) -> list[str]:
        return [body["title"] for body in self.bodies]
```

- [ ] **Step 3: Write the three failing tests**

Append to `tests/test_scheduler_core.py`, below `test_a_failed_job_notifies_with_failed_status`:

```python
# --- roadmap row 19: the run_start and error events -------------------------


async def test_a_due_job_posts_a_run_start_payload_before_its_completion(
    session_factory,
):
    """Row 19's ``run_start``, asserted at the dispatch seam: this is the exact
    body the operator's endpoint receives. It fires after the claim commits --
    the row already says the run is in flight -- and before the completion
    payload, which is the ordering a consumer pairing the two depends on."""

    async def body(session):
        return "did the thing"

    catcher = _Catcher("scheduled_run_started", "scheduled_run_completed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        notifier = build_notifier(
            NotificationsConfig(enabled=True, url=HOOK_URL), http, _refuse_db
        )
        stop = asyncio.Event()
        scheduler = Scheduler(
            session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
        )
        task = asyncio.create_task(scheduler.run(stop))
        started = await catcher.seen("scheduled_run_started")
        await catcher.seen("scheduled_run_completed")
        stop.set()
        await task

    assert started == {
        "version": "1.0",
        "title": "autoposter: scheduled_run_started",
        "message": "scheduled run demo started",
        "attachments": [],
        # No status field, so an n8n gate on `body.type === "success"` passes
        # for a start too -- the shipped derivation (payload.py), unchanged.
        "type": "success",
    }
    assert catcher.titles.index("autoposter: scheduled_run_started") < (
        catcher.titles.index("autoposter: scheduled_run_completed")
    )


async def test_a_failed_job_posts_the_error_event_as_well_as_the_completion(
    session_factory,
):
    """Row 19's ``error``: one call site, the same boundary that writes the
    failed status. ADDITIVE -- ``scheduled_run_completed`` still fires, because
    removing a shipped event would break the n8n flow row 18 exists for. The
    message carries the class name only: an unmarked exception's str() is not
    a served surface (rows 209/213)."""

    async def body(session):
        raise RuntimeError("job exploded")

    catcher = _Catcher("scheduled_run_failed", "scheduled_run_completed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        notifier = build_notifier(
            NotificationsConfig(enabled=True, url=HOOK_URL), http, _refuse_db
        )
        stop = asyncio.Event()
        scheduler = Scheduler(
            session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
        )
        task = asyncio.create_task(scheduler.run(stop))
        failed = await catcher.seen("scheduled_run_failed")
        completed = await catcher.seen("scheduled_run_completed")
        stop.set()
        await task

    assert failed == {
        "version": "1.0",
        "title": "autoposter: scheduled_run_failed",
        "message": "scheduled run demo failed: RuntimeError",
        "attachments": [],
        "type": "failure",
    }
    assert completed["type"] == "failure", "the shipped event is unchanged"
    assert "job exploded" not in json.dumps(catcher.bodies), (
        "the exception's own message never reaches a served surface"
    )


async def test_a_successful_run_posts_no_error_event(session_factory):
    """The error event is a failure signal, not a run marker: a consumer
    routing on it must never be woken by a run that worked."""

    async def body(session):
        return "did the thing"

    catcher = _Catcher("scheduled_run_completed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        notifier = build_notifier(
            NotificationsConfig(enabled=True, url=HOOK_URL), http, _refuse_db
        )
        stop = asyncio.Event()
        scheduler = Scheduler(
            session_factory, [_job(run=body)], poll_seconds=0.01, notifier=notifier
        )
        task = asyncio.create_task(scheduler.run(stop))
        await catcher.seen("scheduled_run_completed")
        stop.set()
        await task

    assert "autoposter: scheduled_run_failed" not in catcher.titles
```

- [ ] **Step 4: Run the file — see the REDs at the dispatch seam**

```bash
docker compose -p pnf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pnf1-red test sh -c "timeout -s KILL 600 pytest tests/test_scheduler_core.py -q 2>&1 | tee /app/.superpowers/run-p-notif-t1-red.log; echo EXIT=\$?"
```

Read `.superpowers/run-p-notif-t1-red.log` from the host, then `docker rm pnf1-red`.

Expected: **2 failed** — `test_a_due_job_posts_a_run_start_payload_before_its_completion` and `test_a_failed_job_posts_the_error_event_as_well_as_the_completion`, both timing out in `catcher.seen(...)` with `asyncio.TimeoutError` because no such event is ever POSTed. `test_a_successful_run_posts_no_error_event` PASSES already — it pins the absence, so it cannot go red before the feature exists; say so in the notes rather than counting it as a red. Every pre-existing test in the file passes.

- [ ] **Step 5: Implement — the two sends in `scheduler/core.py`**

(a) Insert the start send directly after the existing `logger.info("scheduler: %s started", job.name)` at `:161`, before the `status, detail = "ok", ""` line:

```python
        # Roadmap row 19's run_start, from the same after-the-commit position
        # the completion send uses: the claim above is committed, so the
        # scheduled_runs row already shows this run in flight and the payload
        # never describes a run the database does not. Fire-and-forget for the
        # completion send's reason -- an awaited send's worst case is ~31.5s on
        # the default retry config (notify/dispatch.py) and this loop runs
        # every job sequentially, so awaiting it here would delay the work the
        # notification is announcing.
        self._start_notification(
            "scheduled_run_started",
            f"scheduled run {job.name} started",
            {"job": job.name},
        )
```

(b) Replace the block from the comment at `:204` through `_start_notification`'s body ending at `:224` with:

```python
        # After the commit above, never before: the notification must not
        # describe a run the database does not yet show. And on a task of its
        # own, not awaited: one send's worst case is ~31.5s on the default
        # retry config (see notify/dispatch.py), while this loop runs every
        # job sequentially -- an awaited send would stall every job behind it
        # and the poll cadence. The truncated detail matches what the row
        # recorded. send's boolean is deliberately ignored: the Notifier does
        # its own outcome logging, and a disabled notifier's vacuous True
        # must not be reported as a delivery.
        recorded = detail[:2000]
        self._start_notification(
            "scheduled_run_completed",
            f"scheduled run {job.name} finished: {status}",
            {"job": job.name, "status": status, "detail": recorded},
        )
        if status == "failed":
            # Row 19's `error` event, and its ONE call site: this is the only
            # boundary that knows a scheduled run failed, so emitting it here
            # rather than per job body keeps one event with one shape. Additive
            # on purpose -- scheduled_run_completed still fires for every run,
            # because dropping or renaming a shipped event would break the n8n
            # flow row 18 exists to keep alive. `recorded` is the same
            # class-name-only string the row holds (see the handler above).
            self._start_notification(
                "scheduled_run_failed",
                f"scheduled run {job.name} failed: {recorded}",
                {"job": job.name, "status": "failed", "detail": recorded},
            )

    def _start_notification(self, event: str, summary: str, detail: dict) -> None:
        task = asyncio.create_task(self._notifier.send(event, summary, detail))
        self._notify_tasks.add(task)
        task.add_done_callback(self._notification_done)
```

(Leave `_notification_done` at `:226-234` exactly as it is.)

- [ ] **Step 6: Run the file — GREEN, including every pre-existing pin**

```bash
docker compose -p pnf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pnf1-green test sh -c "timeout -s KILL 600 pytest tests/test_scheduler_core.py -q 2>&1 | tee /app/.superpowers/run-p-notif-t1-green.log; echo EXIT=\$?"
```

Read the log, then `docker rm pnf1-green`.

Expected: all pass, `EXIT=0`. The pre-existing pins matter as much as the new ones: `test_a_completed_job_notifies_with_the_job_name_and_ok_status` (`:334-356`) and `test_a_failed_job_notifies_with_failed_status` (`:358-...`) read `notifier.calls[0]`, which is now the **start** event — if either fails, the fix is in those tests' indexing, not in the implementation: change `notifier.calls[0]` to `notifier.calls[1]` and add `assert notifier.calls[0][0] == "scheduled_run_started"`. Note in the report which of them needed it. The `_RecordingNotifier.done` event is set by the FIRST send, so any test awaiting it must now also await the completion; if one flakes, await `notifier.calls` reaching the expected length instead.

- [ ] **Step 7: Close row 18 in the roadmap**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, find the table row beginning `| 18 | **Outbound job/run webhook (n8n)** |` (file line 114 at plan time) and append to its requirement cell, immediately before the closing ` | S — one POST with retry |`:

```
. **answered — shipped 2026-08-22, this row's marker was simply never updated.** Four commits on `main`, all ancestors of every current branch: `dcdaf0f` (the `notifications` config block and the payload contract), `d233ce7` (the dispatcher and HTTP sender), `ff6b17c` (the notifier wired into the run boundaries), `93cd3c9` (the dispatcher review findings). What shipped: `src/autoposter/notify/payload.py` (`apprise-json`, verified line by line against Apprise's own `custom_json.py`, and the native `autoposter-v1`), `src/autoposter/notify/dispatch.py` (bounded retries with exponential backoff, no retry on 4xx, host-only logging, an `events_log` row on terminal failure, and a `NullNotifier` so callers never branch), `NotificationsConfig` (`config/schema.py:1187-1209`, frozen at `config/live.py:39-42`), and two call sites — `scheduled_run_completed` (`scheduler/core.py`) and `full_pass_enqueued` (`api/routes.py:873-879`), both fired after their commit and never awaited. Pinned by `tests/test_notify_payload.py` and `tests/test_notify_dispatch.py`. The n8n rehearsal against the live flow is the one thing left, and it is a cutover-day action, not code
```

- [ ] **Step 8: Commit**

```bash
docker compose -p pnf1 down
git add src/autoposter/scheduler/core.py tests/test_scheduler_core.py docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "feat(notify): the run_start and error events, and row 18's overdue close"
```

---

### Task 2: Per-collection `changes` and `delete` webhooks

**Files:**
- Modify: `src/autoposter/config/schema.py` (`CollectionDefinition`, after the `filters` field at `:375`)
- Modify: `src/autoposter/notify/dispatch.py:65-66`, `:94-140`, plus a new module-level helper below `build_notifier`
- Modify: `src/autoposter/collections/lists.py:165-189`, after `:362`
- Modify: `src/autoposter/collections/engine.py:98-110`, `:113-125`, `:165-179`, `:259-277`, `:462-481`, `:495-516`, `:518`, `:712-735`, `:919-930`, `:1087-1103`
- Modify: `src/autoposter/collections/service.py:390-399`, after `:450`
- Modify: `src/autoposter/scheduler/jobs.py:47-54`, `:105-108`
- Modify: `src/autoposter/app.py:291-294`
- Modify: `config/autoposter.example.yaml` (the commented `definitions:` block, `:95-161`)
- Modify: `tests/test_notify_dispatch.py` (append), `tests/test_builder_knobs.py` (append), `tests/test_builder_engine.py:644-658`, `tests/test_collection_config.py` (append), `tests/test_config_live.py` (append)

**Interfaces:**
- Consumes: from Task 1 nothing but the branch. From the tree: `Notifier.send`/`NullNotifier.send` (`notify/dispatch.py:65,94`); `build_notifier` (`:39`); `member_diff` and `reconcile_list_collection` (`collections/lists.py:114,165`); `run_library(...) -> LibraryRun` (`collections/engine.py:259`); `reconcile_libraries(session, server, config, http, run_index=0, summaries=None, sources=None, cache=None) -> ReconcileResult` (`collections/service.py:390`); `make_collections_job(holder, server_factory, http, summaries=None, secrets=None, cache=None) -> Job` (`scheduler/jobs.py:47`); `_INHERITED_BY_EXPANSION` (`collections/engine.py:165`); `_why(family_title)` (`:907`).
- Produces:
  - `CollectionDefinition.changes_webhook: str = ""`.
  - `Notifier.send(event, summary, detail, url: str | None = None)` and the matching `NullNotifier.send` signature — `url=None` means the configured global target.
  - `send_in_background(coroutine) -> None` in `notify/dispatch.py`.
  - `reconcile_list_collection(..., deltas: dict | None = None)`, filling `{"added": int, "removed": int}` on a non-dry-run write.
  - `DefinitionResult.added: int = 0` / `.removed: int = 0` (applied, not previewed).
  - `CollectionNotification(event: str, summary: str, detail: dict, url: str)` and `LibraryRun.notifications: list[CollectionNotification]`.
  - `reconcile_libraries(..., notifier=None)` and `make_collections_job(..., notifier=None)`.
  - Two wire events: `collection_changed` (detail `{"library", "collection", "added", "removed"}`) and `collection_deleted` (detail `{"library", "collection", "rating_key", "reason"}`).
  - Task 3 counts **+9** backend tests from here.

**Readers of the changed surfaces, enumerated (constraint 2):** `DefinitionResult`'s new fields reach no served surface — the preview endpoint builds its dict field by field (`api/collections_builders.py:128`, `:362-363`), so nothing in `frontend/src/api` or `types.ts` changes; verified with `grep -rn "DefinitionResult\|asdict(result)" src/autoposter/api` (no matches) at plan time, re-verify at dispatch. `reconcile_list_collection`'s callers are `engine._run_one` only for the list path — verify with `grep -rn "reconcile_list_collection" src tests` before editing and confirm every other caller is unaffected by an added keyword-only-in-practice parameter with a default. `LibraryRun`'s new field is defaulted, so `service.py` and the preview endpoint compile unchanged.

- [ ] **Step 1: Write the failing dispatcher tests (the per-send URL)**

Append to `tests/test_notify_dispatch.py`:

```python
# --- roadmap row 19: a per-collection target on one notifier ----------------

COLLECTION_HOOK_HOST = "collections.example.test"
COLLECTION_HOOK_URL = f"http://{COLLECTION_HOOK_HOST}/hook/tok-SECRET456"


async def test_an_explicit_url_overrides_the_configured_target(make_client):
    """Row 19's per-collection webhooks read their URL from the definition at
    dispatch time, so one notifier -- built once, from the frozen config --
    serves the global target and every per-collection one."""
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    notifier = build_notifier(_config(), make_client(handler), _refuse_db)

    ok = await notifier.send(
        "collection_changed",
        "Movies: 'Hand Picked' changed: +2 -1",
        {"library": "Movies", "collection": "Hand Picked", "added": 2, "removed": 1},
        url=COLLECTION_HOOK_URL,
    )

    assert ok is True
    assert str(seen[0].url) == COLLECTION_HOOK_URL, "never the configured target"
    assert json.loads(seen[0].content) == {
        "version": "1.0",
        "title": "autoposter: collection_changed",
        "message": "Movies: 'Hand Picked' changed: +2 -1",
        "attachments": [],
        "type": "success",
    }


async def test_a_failure_against_an_explicit_url_names_that_host_only(
    make_client, session_factory, session, sleeps, caplog
):
    """The host-only rule follows the URL, not the config: a per-collection
    hook can embed a token in its path exactly as the global one can."""

    def handler(request):
        raise httpx.ConnectError("connection refused")

    notifier = build_notifier(_config(), make_client(handler), session_factory)

    with caplog.at_level(logging.DEBUG):
        ok = await notifier.send(
            "collection_changed", "s", {"library": "Movies"}, url=COLLECTION_HOOK_URL
        )

    assert ok is False
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert COLLECTION_HOOK_HOST in warnings[0].getMessage()
    assert HOOK_HOST not in warnings[0].getMessage(), "the global host is not this one"
    assert COLLECTION_HOOK_URL not in caplog.text
    assert "tok-SECRET456" not in caplog.text

    rows = (await session.execute(select(EventLog))).scalars().all()
    assert "tok-SECRET456" not in json.dumps(rows[0].payload)
```

- [ ] **Step 2: Write the failing schema, inheritance and frozen-split tests**

(a) In `tests/test_builder_engine.py`, add one entry to `_RIDE_ALONGS` (`:644-658`), keeping the rest of the dict as it is:

```python
    "changes_webhook": "http://hooks.example.test/changes/fake-1",
```

(b) Append to `tests/test_collection_config.py`:

```python
def test_a_definition_defaults_to_no_changes_webhook():
    """Row 19's per-collection webhook is opt-in per definition: an operator
    who configures none gets exactly the notifications they get today."""
    definition = CollectionDefinition(
        title="Hand Picked", builder="plex_id", params={"ids": ["1"]}
    )
    assert definition.changes_webhook == ""
```

If `CollectionDefinition` is not already imported in that file, add `from autoposter.config.schema import CollectionDefinition` to its import block.

(c) Append to `tests/test_config_live.py`:

```python
def test_per_collection_webhooks_are_not_frozen():
    """Facts adjudication 3, the frozen split. The global notifications block
    is frozen -- the notifier is built once at startup -- but the
    per-collection URLs are fields on a definition the engine reads off the
    live config on every pass, so an edited one applies at the next pass and
    the settings editor must not claim otherwise."""
    assert "notifications" in FROZEN_SECTIONS
    assert not any(
        prefix == "collections" or prefix.startswith("collections.")
        for prefix in FROZEN_SECTIONS
    )
```

If `FROZEN_SECTIONS` is not already imported there, add `from autoposter.config.live import FROZEN_SECTIONS`.

- [ ] **Step 3: Write the failing end-to-end webhook tests**

Append to `tests/test_builder_knobs.py`. First, extend its import block with what is missing:

```python
import json

from autoposter.config.schema import NotificationsConfig
from autoposter.notify.dispatch import build_notifier
```

Then append the harness and the five tests:

```python
# --- roadmap row 19: per-collection changes and delete webhooks -------------

GLOBAL_HOOK_URL = "http://hooks.example.test/notify/tok-SECRET123"
COLLECTION_HOOK_URL = "http://collections.example.test/hook/tok-SECRET456"


class _Catcher:
    """A webhook catcher over a MockTransport: what the operator's endpoint
    actually receives. ``seen`` awaits one fire-and-forget send rather than
    sleeping on it; the events waited for are named at construction, because a
    waiter created after the POST landed would wait forever."""

    def __init__(self, *events: str):
        self.posts: list[tuple[str, dict]] = []
        self._waiters = {
            f"autoposter: {event}": asyncio.Event() for event in events
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.posts.append((str(request.url), body))
        waiter = self._waiters.get(body["title"])
        if waiter is not None:
            waiter.set()
        return httpx.Response(200)

    async def seen(self, event: str) -> tuple[str, dict]:
        await asyncio.wait_for(self._waiters[f"autoposter: {event}"].wait(), timeout=5)
        (found,) = [
            post for post in self.posts if post[1]["title"] == f"autoposter: {event}"
        ]
        return found


def _refuse_db():
    raise AssertionError("a successful send must never touch the events log")


def _notifier_for(catcher, http):
    return build_notifier(
        NotificationsConfig(enabled=True, url=GLOBAL_HOOK_URL), http, _refuse_db
    )


async def test_a_changed_collection_posts_to_its_own_webhook(session, registry_entry):
    """Kometa's ``changes_webhooks``, as adjudicated: the URL is a field on the
    definition, read live at dispatch time, and the payload carries the deltas
    the pass actually applied -- not the preview counts, which a real pass
    never fills."""
    registry_entry(_Listing("knobs_hooked", [("imdb", "tt1"), ("imdb", "tt2")]))
    section = FakeSection([("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])])
    config = _service_config()
    config.collections.definitions = [
        CollectionDefinition(
            title="Hooked", builder="knobs_hooked",
            changes_webhook=COLLECTION_HOOK_URL,
        )
    ]

    catcher = _Catcher("collection_changed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        await reconcile_libraries(
            session, FakeServer({"Movies": section}), config, http,
            notifier=_notifier_for(catcher, http),
        )
        url, body = await catcher.seen("collection_changed")

    assert url == COLLECTION_HOOK_URL
    assert body == {
        "version": "1.0",
        "title": "autoposter: collection_changed",
        "message": "Movies: 'Hooked' changed: +2 -0",
        "attachments": [],
        "type": "success",
    }
    assert [post[0] for post in catcher.posts] == [COLLECTION_HOOK_URL], (
        "a membership change never reaches the global target"
    )


async def test_a_definition_without_a_webhook_posts_nothing(session, registry_entry):
    """Opt-in, and never a fallback to the global URL: a POST per changed
    collection per pass would be an unbounded volume change to the shipped
    integration row 18 exists to keep alive."""
    registry_entry(_Listing("knobs_unhooked", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"])])
    config = _service_config()
    config.collections.definitions = [
        CollectionDefinition(title="Unhooked", builder="knobs_unhooked")
    ]

    catcher = _Catcher()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        await reconcile_libraries(
            session, FakeServer({"Movies": section}), config, http,
            notifier=_notifier_for(catcher, http),
        )
        # Nothing to await: assert the absence after the pass has returned and
        # any task it started has had the loop to itself.
        await asyncio.sleep(0)

    assert catcher.posts == []


async def test_a_dry_run_posts_nothing(session, registry_entry):
    """``apply_to_plex: false`` is a periodic dry run -- it changed nothing, so
    it has nothing to announce."""
    registry_entry(_Listing("knobs_dry", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"])])
    config = _service_config(apply_to_plex=False)
    config.collections.definitions = [
        CollectionDefinition(
            title="Dry", builder="knobs_dry", changes_webhook=COLLECTION_HOOK_URL
        )
    ]

    catcher = _Catcher()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        await reconcile_libraries(
            session, FakeServer({"Movies": section}), config, http,
            notifier=_notifier_for(catcher, http),
        )
        await asyncio.sleep(0)

    assert catcher.posts == []


async def test_a_swept_family_delete_is_routed_to_the_family_webhook(session):
    """Facts adjudication 4: the sweep's existing ``collection_deleted``
    EventLog site is ROUTED rather than a second delete detection invented, and
    it keeps the same event name on the wire that it writes to the log.

    A collection with a definition that still owns it is the DYNAMIC family
    case -- an ordinary definition's title is never a sweep candidate, because
    a configured title is managed whether or not this pass built it. The
    routing is asserted on what the engine collected; the payload itself is
    asserted at the dispatch seam by the global-fallback test below."""
    from autoposter.collections.builders.dynamic import (
        _generated_key, family_label,
    )

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"},
        changes_webhook=COLLECTION_HOOK_URL,
    )
    gone = FakeCollection(
        "Top Western movies", [FakeItem("m1")],
        labels=[LABEL, family_label(definition)],
    )
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[gone])
    session.add(ManagedCollection(
        library="Movies", title="Top Western movies", kind="smart",
        plex_rating_key="c-Top Western movies", definition_hash="seed",
    ))
    await session.flush()

    run = await run_library(
        session, section, "Movies", "Movie", [definition],
        _config(delete_unconfigured=True), sweep=True,
        run_cache_seed={_generated_key(family_label(definition)): set()},
    )

    assert gone.deleted is True
    (note,) = run.notifications
    assert note.event == "collection_deleted"
    assert note.url == COLLECTION_HOOK_URL, "the family's hook, not the global one"
    assert note.summary == "deleted collection 'Top Western movies' in Movies"
    assert note.detail == {
        "library": "Movies",
        "collection": "Top Western movies",
        "rating_key": "c-Top Western movies",
        "reason": "the 'Genres' family no longer builds it",
    }


async def test_a_swept_delete_without_a_family_webhook_posts_to_the_global_url(
    session, registry_entry
):
    """The one new event an existing global consumer sees. A collection swept
    because NO definition builds it any more has no per-collection webhook to
    route to by construction, and a delete is destructive, rare and capped by
    ``max_deletes`` -- so it goes to the global target rather than nowhere."""
    registry_entry(_Listing("knobs_sweep_global", [("imdb", "tt1")]))
    orphan = FakeCollection("Retired Chart", [FakeItem("m1")], labels=[LABEL])
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[orphan])
    await _managed_row(session, "Movies", "Retired Chart")
    config = _service_config(delete_unconfigured=True)
    config.collections.definitions = [
        CollectionDefinition(title="Kept", builder="knobs_sweep_global")
    ]

    catcher = _Catcher("collection_deleted")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catcher.handler)
    ) as http:
        await reconcile_libraries(
            session, FakeServer({"Movies": section}), config, http,
            notifier=_notifier_for(catcher, http),
        )
        url, body = await catcher.seen("collection_deleted")

    assert orphan.deleted is True
    assert url == GLOBAL_HOOK_URL
    assert body["message"] == "deleted collection 'Retired Chart' in Movies"
```

- [ ] **Step 4: Run the four test files — see the REDs**

```bash
docker compose -p pnf2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pnf2-red test sh -c "timeout -s KILL 600 pytest tests/test_notify_dispatch.py tests/test_builder_knobs.py tests/test_builder_engine.py tests/test_collection_config.py tests/test_config_live.py -q 2>&1 | tee /app/.superpowers/run-p-notif-t2-red.log; echo EXIT=\$?"
```

Read `.superpowers/run-p-notif-t2-red.log` from the host, then `docker rm pnf2-red`.

Expected reds, all for the right reason — check each against this list before implementing:
- `test_an_explicit_url_overrides_the_configured_target`, `test_a_failure_against_an_explicit_url_names_that_host_only` — `TypeError: send() got an unexpected keyword argument 'url'`.
- `test_expanded_definitions_inherit_the_placeholders_settings` (`test_builder_engine.py:681`) — `ValidationError`, `changes_webhook` is not a field on `CollectionDefinition`.
- `test_a_definition_defaults_to_no_changes_webhook` — `AttributeError`/`ValidationError`, same cause.
- `test_a_changed_collection_posts_to_its_own_webhook`, `test_a_swept_delete_without_a_family_webhook_posts_to_the_global_url` — `TypeError` on `reconcile_libraries(..., notifier=...)`.
- `test_a_swept_family_delete_is_routed_to_the_family_webhook` — `ValidationError` on `changes_webhook`, and behind it `AttributeError: 'LibraryRun' object has no attribute 'notifications'`.
- `test_per_collection_webhooks_are_not_frozen`, `test_a_definition_without_a_webhook_posts_nothing`, `test_a_dry_run_posts_nothing` — these PASS or fail only on the `notifier=` kwarg; the first pins current behaviour and cannot go red before the feature. Say so in the notes rather than counting them as reds.

- [ ] **Step 5: Implement (a) — the config field**

In `src/autoposter/config/schema.py`, immediately after the `filters` field (ending `:375`) and before the `@field_validator("builder")` at `:377`, add:

```python
    # Row 19 (Kometa's ``changes_webhooks``): a webhook this collection's
    # membership changes are POSTed to, in addition to whatever the global
    # ``notifications`` block does. A field on the definition rather than a
    # separate pattern list, for the reason ``labels`` and ``sync_mode`` are:
    # this is a property of one collection, and the operator already writes
    # that collection here. Deliberately NOT in ``config/live.FROZEN_SECTIONS``
    # -- the engine reads the definition off the live config on every pass, so
    # an edited URL applies at the next pass rather than the next restart. Only
    # ever sent to when ``notifications.enabled`` is true: the notifier itself
    # is still built once at startup. May embed a token in its path, so it is
    # never logged in full -- host only, the same rule the global URL has.
    changes_webhook: str = ""
```

- [ ] **Step 6: Implement (b) — the per-send URL and the background helper**

In `src/autoposter/notify/dispatch.py`:

(1) Replace `NullNotifier.send` (`:65-66`) with:

```python
    async def send(
        self, event: str, summary: str, detail: dict, url: str | None = None
    ) -> bool:
        return True
```

(2) Replace `Notifier.send` and `Notifier._send` (`:94-151`) with:

```python
    async def send(
        self, event: str, summary: str, detail: dict, url: str | None = None
    ) -> bool:
        """Deliver one notification. Never raises: ``False`` means failed.

        ``url`` overrides the configured target for this send alone -- row
        19's per-collection webhooks, which are read off the collection's
        definition at dispatch time and so are not part of the frozen config
        this notifier was built from. Everything else -- the mode, the
        timeout, the retry policy, the host-only rule -- is the same for
        every target.
        """
        try:
            return await self._send(event, summary, detail, url or self._url)
        except Exception:
            # Unexpected -- _send already contains transport failures, so
            # reaching here means a bug in OUR code: error, not warning, so it
            # outranks webhook noise in log filtering. The work this
            # notification describes is done; do not fail it.
            logger.error("notification %r failed unexpectedly", event, exc_info=True)
            return False

    async def _send(self, event: str, summary: str, detail: dict, url: str) -> bool:
        payload = build_payload(self._mode, event, summary, detail)
        host = self._host if url == self._url else _host_of(url)
        attempts = 0
        failure = "not attempted"
        for attempt in range(self._retry_count):
            if attempt:
                await _sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
            attempts = attempt + 1
            try:
                response = await self._http.post(
                    url, json=payload, timeout=self._timeout
                )
            except httpx.HTTPError as exc:
                # Transport-level: the next attempt may find the host back.
                # Leak-safety of logging/storing str(exc) rests on the shared
                # client having no event hooks and this method never calling
                # raise_for_status() -- HTTPStatusError's message embeds the
                # full URL, token and all.
                failure = f"{type(exc).__name__}: {exc}"
                continue
            if response.is_success:
                logger.debug("notification %r delivered to %s", event, host)
                return True
            failure = f"HTTP {response.status_code}"
            if response.status_code < 500:
                # A 4xx is a misconfiguration (wrong path, revoked token):
                # retrying cannot help, so fail now.
                break
        logger.warning(
            "notification %r to %s failed after %d attempt(s): %s",
            event,
            host,
            attempts,
            failure,
        )
        try:
            await self._record_failure(event, summary, host, attempts, failure)
        except Exception:
            # The events-log write is best-effort bookkeeping: if the DB is
            # down too, keep the invariant of exactly one WARNING per failed
            # send -- this secondary problem gets a secondary (info) line.
            logger.info(
                "could not record notification failure in events_log",
                exc_info=True,
            )
        return False

    async def _record_failure(
        self, event: str, summary: str, host: str, attempts: int, failure: str
    ) -> None:
        # Host only, never the URL: events_log payloads reach the operator
        # through the API and must not carry an embedded token. The host is
        # the one this send actually used, which for a per-collection webhook
        # is not the configured target's.
        async with self._session_factory() as session:
            session.add(
                EventLog(
                    source="notifier",
                    event_type=event,
                    payload={
                        "summary": summary,
                        "host": host,
                        "attempts": attempts,
                        "error": failure,
                    },
                    outcome=f"notification failed after {attempts} attempt(s): {failure}",
                )
            )
            await session.commit()
```

(3) Replace the host derivation in `Notifier.__init__` (`:88-92`) with a call to the shared helper:

```python
        # The only URL component that may ever be logged or stored.
        self._host = _host_of(config.url)
```

(4) Add these module-level definitions directly below `build_notifier` (after `:53`):

```python
def _host_of(url: str) -> str:
    """The one URL component that may ever be logged or stored."""
    try:
        return httpx.URL(url).host or "(unknown host)"
    except Exception:  # a malformed URL must not break a send or construction
        return "(unknown host)"


# Strong references to in-flight sends: asyncio holds only a weak reference to
# a created task, so a fire-and-forget send nothing else references could be
# garbage-collected mid-flight. The done-callback drops each reference.
_background_tasks: set = set()


def send_in_background(coroutine) -> None:
    """Fire one ``Notifier.send`` without awaiting it.

    The collections pass must not wait on a webhook: one send's worst case is
    ``retry_count * timeout_seconds`` plus backoff (~31.5s on the defaults),
    and a pass can have several collections to report. ``send`` never raises
    and does its own outcome logging, so the result is deliberately dropped --
    in particular a disabled notifier's vacuous ``True`` is never reported as
    a delivery.
    """
    task = asyncio.create_task(coroutine)
    _background_tasks.add(task)
    task.add_done_callback(_background_done)


def _background_done(task) -> None:
    _background_tasks.discard(task)
    # send never raises by contract, but an exception a task holds unretrieved
    # becomes a GC-time warning; retrieve and log it here so a misbehaving
    # notifier is named, not leaked.
    if not task.cancelled() and task.exception() is not None:
        logger.warning("notification task failed", exc_info=task.exception())
```

(`scheduler/core.py` and `api/routes.py` keep their own copies of this idiom — this plan does not refactor code it is not otherwise touching.)

- [ ] **Step 7: Implement (c) — the applied deltas out of the reconciler**

In `src/autoposter/collections/lists.py`:

(1) Add one parameter to `reconcile_list_collection`, after `sort_order: str | None = None` (`:188`) and before the closing `) -> list[str]:`:

```python
    deltas: dict | None = None,
```

(2) Extend the function's docstring with one paragraph, after the `existing` paragraph (`:192-196`):

```
    ``deltas``, when given, is filled with the members this call actually
    added and removed (``{"added": int, "removed": int}``) on a pass that
    wrote. It is an out-param rather than a second return value because every
    caller wants the action strings and only one wants the numbers -- row 19's
    per-collection webhook, which reports what changed. A dry run, an
    unchanged membership and a refused claim all leave it untouched, which is
    exactly the "nothing to announce" case.
```

(3) Directly after the `if not definition_current:` block ends — that is, after the update branch's closing `)` at `:362` and before the comment block starting `# Below both write branches` at `:364` — insert, at the same indentation as the `if not definition_current:` statement (`:308`):

```python
        if deltas is not None and not dry_run:
            # After both write paths, so a create (every item added) and an
            # update (its own diff) report through one statement. `added_count`
            # and `removed_count` are initialised to 0 above, so a branch that
            # wrote nothing reports nothing.
            deltas["added"] = added_count
            deltas["removed"] = removed_count
```

- [ ] **Step 8: Implement (d) — the engine collects the notifications**

In `src/autoposter/collections/engine.py`:

(1) Add two fields to `DefinitionResult`, after `filtered: int = 0` (`:107`):

```python
    # What the pass ACTUALLY applied, as opposed to ``adding``/``removing``
    # above, which are preview counts and stay zero on a real pass. Filled
    # from ``reconcile_list_collection``'s ``deltas`` out-param, and read by
    # row 19's per-collection ``changes`` webhook -- which must report what
    # happened, not what a preview would have said.
    added: int = 0
    removed: int = 0
```

(2) Add the notification record and the `LibraryRun` field. Replace `LibraryRun` (`:113-125`) with:

```python
@dataclass
class CollectionNotification:
    """One outbound webhook this pass owes, collected but not yet sent.

    The engine decides WHAT to announce and WHERE; ``service.reconcile_libraries``
    decides WHEN, which is after its per-library commit -- the same
    "never describe work the database does not yet show" rule both shipped
    call sites follow. Collecting rather than sending also keeps the notifier
    out of ``run_library``'s signature, which every preview and CLI caller
    would otherwise have to learn about.

    ``url`` empty means the globally configured target (a swept delete of a
    collection no definition owns any more has no per-collection webhook to
    route to).
    """

    event: str
    summary: str
    detail: dict
    url: str = ""


@dataclass
class LibraryRun:
    """One library's pass: every action string, how each definition fared, and
    the per-collection webhooks the pass owes."""

    actions: list[str]
    definitions: list[DefinitionResult]
    notifications: list[CollectionNotification] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        """The titles whose builder failed, or whose filter stage could not be
        evaluated. A pass with any of these is not a success, whatever the
        action count says (roadmap row 115)."""
        return [result.title for result in self.definitions if result.failed]
```

(Confirm `field` is imported from `dataclasses` at the top of the file — it is, since `DefinitionResult.actions` uses `field(default_factory=list)`.)

(3) Add the webhook to the expansion ride-alongs. In `_INHERITED_BY_EXPANSION` (`:165-179`), add after `"tmdb_summary",`:

```python
    "changes_webhook",
```

and extend the comment block above it (`:158-164`) with one sentence at its end:

```
# ``changes_webhook`` rides along for the same reason, one step further out:
# the placeholder is the only definition an operator writes for the family, so
# a webhook on it is a webhook for every collection in it.
```

(4) In `run_library`, declare the list next to the other accumulators. After `results: list[DefinitionResult] = []` (`:308`) add:

```python
    notifications: list[CollectionNotification] = []
```

(5) In the unit loop, after `results.append(result)` (`:481`), add:

```python
            if not dry_run and unit.changes_webhook and (result.added or result.removed):
                # Row 19's ``changes``: opt-in per definition and never a
                # fallback to the global target -- a POST per changed
                # collection per pass would be an unbounded volume change to
                # the shipped integration. A dry run announces nothing because
                # it changed nothing.
                notifications.append(CollectionNotification(
                    event="collection_changed",
                    summary="%s: %r changed: +%d -%d" % (
                        library, unit.title, result.added, result.removed
                    ),
                    detail={
                        "library": library,
                        "collection": unit.title,
                        "added": result.added,
                        "removed": result.removed,
                    },
                    url=unit.changes_webhook,
                ))
```

(6) Pass the list to the sweep. In the `if sweep:` block (`:495-501`), add one argument to the `_sweep(...)` call:

```python
                    run_cache=run_cache, notifications=notifications,
```

(7) Return them. Replace `:518` with:

```python
    return LibraryRun(
        actions=actions, definitions=results, notifications=notifications
    )
```

(8) In `_run_one`, fill the new fields. Replace the `else:` branch's reconcile call (`:711-734`) so it passes and reads the out-param — that is, insert `deltas: dict = {}` immediately above `outcome.actions += await reconcile_list_collection(`, add `deltas=deltas,` to the call's keyword arguments after `sort_order=sort_order,`, and add the two assignments directly below the call:

```python
    else:
        deltas: dict = {}
        outcome.actions += await reconcile_list_collection(
            ...  # every existing argument unchanged
            sort_order=sort_order,
            deltas=deltas,
        )
        # What the pass applied, for row 19's per-collection webhook. Absent
        # keys mean a dry run or an unchanged membership: nothing to announce.
        outcome.added = deltas.get("added", 0)
        outcome.removed = deltas.get("removed", 0)
    return outcome
```

(9) In `_sweep`, take the list and route the delete. Add one parameter to the signature (`:919-930`), after `run_cache: dict,`:

```python
    notifications: list | None = None,
```

and add a module-level helper directly above `_sweep` (after `_why` ends at `:917`):

```python
def _webhook_for(definitions: list[CollectionDefinition], title: str | None) -> str:
    """The per-collection webhook of the definition that owns ``title``.

    Empty when no definition names it -- which is the ordinary case for a
    swept collection: it is being deleted precisely because nothing builds it
    any more, so there is no per-collection target and the delete goes to the
    global one.
    """
    if title is None:
        return ""
    for definition in definitions:
        if definition.title == title:
            return definition.changes_webhook
    return ""
```

Then, in `_sweep`'s delete loop, directly after the `await session.flush()` at `:1103` and before `results.append(...)`, add:

```python
        if notifications is not None:
            # Facts adjudication 4: the EventLog row above is the ready hook,
            # so this is the same fact reaching a second sink under the same
            # event name -- not a second delete detection. Routed to the
            # family's webhook when a definition still owns the title, and to
            # the global target otherwise: a delete is destructive, rare and
            # capped by max_deletes, so "nowhere" is the wrong answer for it.
            notifications.append(CollectionNotification(
                event="collection_deleted",
                summary="deleted collection %r in %s" % (title, library),
                detail={
                    "library": library,
                    "collection": title,
                    "rating_key": str(getattr(collection, "ratingKey", "") or ""),
                    "reason": why,
                },
                url=_webhook_for(definitions, family_title),
            ))
```

(The `dry_run` branch at `:1068-1072` returns before this, so a dry run announces nothing.)

- [ ] **Step 9: Implement (e) — the service dispatches, and the notifier reaches it**

(1) In `src/autoposter/collections/service.py`, add the import next to the existing engine import (`:34`):

```python
from autoposter.notify.dispatch import NullNotifier, send_in_background
```

(2) Add one parameter to `reconcile_libraries` (after `cache: ProviderCache | None = None,`, `:398`):

```python
    notifier=None,
```

and one paragraph at the end of its docstring (before the closing `"""` at `:423`):

```
    ``notifier`` receives the per-collection webhooks the pass collected
    (row 19). A ``NullNotifier`` stand-in when the caller has none -- the
    ``app._build_mdblist`` precedent -- so the dispatch below is
    unconditional. Sends are fired below the per-library commit and never
    awaited, for the two reasons every other send site here has: a
    notification must not describe work the database does not yet show, and a
    pass must not wait on a webhook.
```

(3) Directly below `await session.commit()` (`:450`) — above the leftovers scan and its comment — add:

```python
            notifier = notifier if notifier is not None else NullNotifier()
            for note in run.notifications:
                send_in_background(
                    notifier.send(
                        note.event, note.summary, note.detail, url=note.url or None
                    )
                )
```

Hoist the `NullNotifier` fallback out of the loop body by assigning it once at the top of the function instead, immediately below `result = ReconcileResult()` (`:424`):

```python
    notifier = notifier if notifier is not None else NullNotifier()
```

and keep only the `for note in run.notifications:` loop at the commit site.

(4) In `src/autoposter/scheduler/jobs.py`, add `notifier=None` to `make_collections_job`'s signature (after `cache=None,`, `:53`), one docstring sentence at the end of its docstring:

```
    ``notifier`` is the process's notifier, forwarded to the pass for row 19's
    per-collection webhooks. Absent, the pass sends nothing.
```

and pass it through at the `reconcile_libraries` call (`:105-108`):

```python
        result = await reconcile_libraries(
            session, server, config, http, run_index=run_index, summaries=summaries,
            sources=sources, cache=cache, notifier=notifier,
        )
```

(5) In `src/autoposter/app.py`, extend the `make_collections_job` call (`:291-294`):

```python
                scheduler_jobs.append(make_collections_job(
                    holder, server_factory, http, summaries=app.state.tmdb_facts,
                    secrets=secrets, cache=cache, notifier=notifier,
                ))
```

(`notifier` is in scope: it is built at `:195` and published as `app.state.notifier` at `:196`.)

- [ ] **Step 10: Document the field in the example config**

In `config/autoposter.example.yaml`, inside the commented `definitions:` example, directly below the `limit: 50` line (`:103`), add:

```yaml
  #       # Row 19: POST this collection's membership changes here, on top of
  #       # whatever `notifications` does. Read live -- an edit applies at the
  #       # next pass, not the next restart -- but only ever sent to when
  #       # `notifications.enabled` is true. May embed a token; never logged
  #       # in full. A family definition's hook covers every collection in it.
  #       changes_webhook: "" # e.g. http://n8n.n8n-prod.svc.cluster.local:5678/webhook/collection
```

- [ ] **Step 11: Run the five files — GREEN**

```bash
docker compose -p pnf2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pnf2-green test sh -c "timeout -s KILL 600 pytest tests/test_notify_dispatch.py tests/test_builder_knobs.py tests/test_builder_engine.py tests/test_collection_config.py tests/test_config_live.py tests/test_scheduler_collections_job.py tests/test_example_config_matches_schema.py -q 2>&1 | tee /app/.superpowers/run-p-notif-t2-green.log; echo EXIT=\$?"
```

Read the log, then `docker rm pnf2-green`.

Expected: all pass, `EXIT=0`. Two pre-existing files are in this run on purpose: `test_scheduler_collections_job.py` builds `make_collections_job` positionally in several places (`:202`, `:217`, `:235`, `:297`, `:341`) and must be unaffected by the new keyword parameter, and `test_example_config_matches_schema.py` guards the example config's shape against the schema. If the reconcile-driven tests hang rather than fail, the cause is a `catcher.seen(...)` for an event the pass never produced — read `catcher.posts` in the failure output before changing any production code.

- [ ] **Step 12: Teardown and commit**

```bash
docker compose -p pnf2 down
git add src/autoposter/config/schema.py src/autoposter/notify/dispatch.py src/autoposter/collections/lists.py src/autoposter/collections/engine.py src/autoposter/collections/service.py src/autoposter/scheduler/jobs.py src/autoposter/app.py config/autoposter.example.yaml tests/test_notify_dispatch.py tests/test_builder_knobs.py tests/test_builder_engine.py tests/test_collection_config.py tests/test_config_live.py
git commit --no-gpg-sign -m "feat(notify): per-collection changes and delete webhooks (row 19)"
```

---

### Task 3: The wrap — full suites, the row closes, the PR body

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (rows 19, 20, 17, 21 — **locate each by its row number**; file lines 113-117 at plan time)
- Create: `.superpowers/sdd/p-notif-pr-body.md` (gitignored — NEVER `git add` it)

**Interfaces:**
- Consumes: everything Tasks 1 and 2 produced; the stated totals from constraint 14.
- Produces: measured suite numbers, four updated roadmap rows, a PR body on disk. No push, no PR.

- [ ] **Step 1: State the expected totals, then run the full backend suite**

State in the report, before running: **4198 baseline + 3 (T1) + 9 (T2) = 4210 backend**, frontend unchanged at **358**.

```bash
docker compose -p pnf3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pnf3-full test sh -c "timeout -s KILL 2700 pytest -q 2>&1 | tee /app/.superpowers/run-p-notif-full.log; echo EXIT=\$?"
docker wait pnf3-full
```

Read `.superpowers/run-p-notif-full.log` from the host, then `docker rm pnf3-full`.

Expected: `4210 passed` (plus whatever skips the baseline already has), `EXIT=0`. If the number differs from 4210, report the measured number and account for the difference before continuing — a mismatch means a test was added or lost that this plan did not intend.

- [ ] **Step 2: Ruff**

```bash
docker compose -p pnf3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pnf3-ruff test sh -c "ruff check . 2>&1 | tee /app/.superpowers/run-p-notif-ruff.log; echo EXIT=\$?"
```

Read the log, then `docker rm pnf3-ruff`. Expected: `All checks passed!`, `EXIT=0`.

- [ ] **Step 3: The frontend suite (unchanged, run as a guard)**

```bash
docker compose -p pnf3 run --name pnf3-web web npm test > .superpowers/run-p-notif-web.log 2>&1
```

Read the log, then `docker rm pnf3-web`. Expected: **358 passed** — this phase touches no frontend file, and a change here would mean an unintended served-surface change.

- [ ] **Step 4: Close row 19**

In the roadmap, find the row beginning `| 19 | Notification event taxonomy |` and append to its requirement cell, before the closing ` | S–M — the contract design is the work |`:

```
. **answered — the taxonomy shipped.** Five events now exist behind the one dispatcher. Global, at the scheduler boundary (`scheduler/core.py`): `scheduled_run_started` (row 19's `run_start`, fired after the claim commits, detail `{"job"}`), `scheduled_run_completed` (the shipped `run_end`, unchanged) and `scheduled_run_failed` (row 19's `error`, one call site — the same boundary that writes the failed status — and **additive**: the completion event still fires for a failed run, because renaming or dropping a shipped event would break the n8n flow row 18 exists for; the detail is the same class-name-only string the `scheduled_runs` row holds). Per collection: `collection_changed` (row 19's `changes` = Kometa's `changes_webhooks`) and `collection_deleted` (row 19's `delete`), configured as `collections.definitions[].changes_webhook` — a field on the definition, the `labels`/`sync_mode` precedent, inherited by every unit an expanding family produces. Deliberate decisions, each pinned by a test: `changes` is opt-in per definition and NEVER falls back to the global target (a POST per changed collection per pass would be an unbounded volume change to the shipped integration), while `delete` DOES fall back to it (a collection swept because no definition builds it any more has no per-collection target by construction, and deletes are capped by `max_deletes`); a dry run announces nothing; the per-collection URL is read LIVE from the definition at dispatch time and is not a `FROZEN_SECTIONS` entry, while the global `notifications` block stays frozen because the notifier is still built once at startup (pinned by `test_per_collection_webhooks_are_not_frozen`); per-collection webhooks are still gated on `notifications.enabled`. The delete event routes the ALREADY-EXISTING `EventLog(event_type="collection_deleted")` hook in the sweep rather than inventing a second delete detection, and keeps the same event name on both sinks. Known limitation, deliberate: a **smart** (Plex-evaluated) definition emits no `changes` event — Plex owns that membership, so this service has no diff to report, the same reason its preview counts are blank. Every event is pinned by a test that asserts the exact JSON body POSTed through a real dispatcher over a mock transport
```

- [ ] **Step 5: Update rows 20, 17 and 21's notes**

(a) Row 20 (`| 20 | Discord / Apprise formatting |`), append to the requirement cell:

```
. **deferred to phase 15, deliberately — not attempted in notifications-1.** The phase-5a note and phase 15's own goal line both sanction it, and the shape is unchanged by the taxonomy landing: a third value on `payload.py`'s `mode` Literal plus a builder beside `apprise-json`/`autoposter-v1`, no dependency on any of row 19's work. Still S; still unconfigured today
```

(b) Row 17 (`| 17 | Tracearr payload/API harvest (verify) |`), append to the requirement cell:

```
. **Webhook half re-confirmed open (notifications-1, outbound-only phase — it touched no intake code).** Exactly two things are needed, in this order, and both are writes to live systems: (1) row 21's `/webhook/tracearr` intake endpoint must exist and be reachable from the Tracearr container — there is nothing to point Tracearr at until it does; (2) the user must then point Tracearr's outbound custom-webhook config at that URL and trigger real events, so a payload lands and can be banked and scrubbed the way the 33 REST responses were. Nothing else about this row is open
```

(c) Row 21 (`| 21 | Tracearr webhook intake |`), append to the requirement cell:

```
. **Still blocked on the same live round-trip as row 17's webhook half, and out of notifications-1's scope (that phase was outbound-only).** The route itself is precedented and buildable now — `intake/routes.py`'s `radarr_webhook`/`sonarr_webhook` behind the shared `webhook_secret` check are the shape — but the payload MAPPING cannot be written until a real Tracearr webhook body has been captured, which needs the route deployed first. A plan taking this row should name the two steps explicitly (build the authenticated route; wire the mapping after the live capture) rather than presenting the row as closeable in one pass
```

- [ ] **Step 6: Commit the roadmap closes**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): row 19 closes with the taxonomy; 17/20/21 notes updated"
```

- [ ] **Step 7: Write the PR body (gitignored)**

Write `.superpowers/sdd/p-notif-pr-body.md`. Plain prose, no AI attribution, no `Co-Authored-By`. It must contain:

- **What this is:** rows 18 and 19 of the full-parity roadmap. Row 18 was already shipped on 2026-08-22 and closes here as a documentation correction naming its four commits; row 19 is the work.
- **The five events**, with their wire names, their call sites and their payload details, and the row-19 taxonomy mapping (`run_start`/`run_end`/`error`/`changes`/`delete`).
- **The decisions a reviewer should check**, each in one line: `error` is additive rather than a replacement; `changes` is opt-in and never global; `delete` falls back to global; dry runs are silent; per-collection URLs are live while the global block stays frozen; the sweep's existing `collection_deleted` EventLog hook is routed rather than duplicated; smart definitions emit no `changes`; no per-event toggles.
- **What is NOT here:** row 20 (deferred to phase 15), row 17's webhook half and row 21 (both blocked on the 5c live round-trip — say precisely what the user must supply: the intake endpoint first, then Tracearr's outbound webhook pointed at it).
- **Stack position:** `feat/notifications-1` is SECOND in a two-deep stack, cut from `chore/sweep-5`'s tip (PR #111). **#111 merges first; this branch is then rebased onto the updated base before it merges.**
- **Verification:** the measured backend and frontend totals from Steps 1-3 against the 4198/358 baseline, and ruff clean. No live webhook target was contacted at any point — every assertion runs against a mock transport.

- [ ] **Step 8: Teardown and confirm the tree is clean**

```bash
docker compose -p pnf3 down
git status --short
git log --oneline chore/sweep-5..HEAD
```

Expected: `git status` shows nothing but (optionally) the gitignored run logs and the PR body; the log shows exactly four commits (the plan, T1, T2, the roadmap closes). **Do not push and do not open a PR** — the user gates that.

---

## Self-Review

**1. Spec coverage.** Facts C1: row 18 closes as a doc correction with the four commits cited (T1 Step 7); row 19 closes with the taxonomy (T1) and per-collection webhooks (T2), closed in T3 Step 4. Adjudication 1 (fields on `CollectionDefinition`) — T2 Step 5, with the `labels`/`sync_mode` precedent and the wholesale-replace overrides semantics cited. Adjudication 2 (`error` one call site; `run_start` one site) — T1 Step 5, both in `_maybe_run`. Adjudication 3 (frozen split) — `live.py` untouched, pinned by `test_per_collection_webhooks_are_not_frozen`. Adjudication 4 (the ready hook routed) — T2 Step 8(9), appended at the existing EventLog site under the same event name. Adjudication 5 (OUT) — rows 20/17/21 get notes in T3 Step 5 and no code. C2: every new event is pinned at the dispatch seam through a real notifier over a mock transport, seen RED in T1 Step 4 and T2 Step 4; no live targets; fixture URLs are `.test` fakes; transport errors stay class-name-only and host-only. C3: rows 18/19 close, 17/21 keep updated blocked-on notes, 20's deferral noted, PR body plain. C4: three tasks in the stated order, `pnf1`/`pnf2`/`pnf3`, `p-notif-` artifacts, baselines 4198/358 stated per task, branch cut by content probe, no push.

**2. Placeholder scan.** Every code step carries the actual code. The one abbreviated block is T2 Step 8(8), where the existing 22-argument `reconcile_list_collection` call is shown with `...  # every existing argument unchanged` — the three edits to it (a `deltas: dict = {}` line above, a `deltas=deltas,` argument, two assignments below) are each spelled out, and reproducing twenty unchanged argument lines would invite a transcription error rather than prevent one. No TBD, no "add error handling", no "similar to Task N".

**3. Type consistency.** `send(event, summary, detail, url=None)` is the one signature used by `NullNotifier`, `Notifier`, the scheduler (which never passes `url`) and the service (which passes `url=note.url or None`). `CollectionNotification(event, summary, detail, url)` is constructed in two places and read in one, with the same field names. `deltas` is `{"added", "removed"}` at the fill site (`lists.py`) and the read site (`engine._run_one`). `DefinitionResult.added`/`.removed` (applied) stay distinct from `.adding`/`.removing` (preview) everywhere. `changes_webhook` is spelled identically in the schema, `_INHERITED_BY_EXPANSION`, `_webhook_for`, the unit loop, the example config and all five tests. The wire names `scheduled_run_started`, `scheduled_run_failed`, `collection_changed`, `collection_deleted` appear identically in implementation, tests and the roadmap close.
