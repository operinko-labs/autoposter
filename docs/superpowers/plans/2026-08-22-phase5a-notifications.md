# Phase 5a: Outbound Notifications and Webhooks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Autoposter can notify. A configured URL receives a POST when a run
completes, with a versioned, documented payload contract — replacing the
notification capability Posterizarr's Apprise config provided, so the user can
point new automation at it after the old Kometa-triggering chain retires.

**Architecture:** One config block, one dispatcher module with an in-process
event API, one HTTP sender with retry that never blocks the event loop. Hooks
at the run boundaries that already exist (`ScheduledRun` completion, full-pass
trigger). Ship `run_end` first; every later event type goes through the same
dispatcher — the roadmap explicitly warns against over-designing the taxonomy.

**Cutover framing, corrected against the real n8n flow (user supplied the
export):** the flow's live path — webhook → list kometa jobs → dedup → create
an ad-hoc `--overlays-only` Kometa Job — **never reads the POST body** (the
only body-reading node, an `Is Success` check on `body.type`, is orphaned:
nothing connects to it). And its sole purpose is to trigger Kometa, the tool
autoposter replaces. So the chain is not something 5a must keep firing; it is
something the cutover checklist RETIRES, together with the `kometa` CronJob in
namespace `media`. What 5a still owes, per the approved scope: a notification
capability the user can point at anything (a rebuilt n8n flow, a catcher,
Discord later), with a documented, versioned payload — and an Apprise-shaped
`type: success|failure` field, because the orphaned node shows exactly the
gating pattern the user reaches for and it should work if reconnected.

## Global Constraints

All of `.superpowers/sdd/p4c-verified-facts.md` still binds. Additionally:

- **The payload's compatibility mode is Apprise `json://`'s shape** — the
  documented body Apprise POSTs for that scheme: `version`, `title`,
  `message`, `type` (and `attachments` when present). **Verify the exact field
  set against Apprise's current source/docs, not this plan.** The failure this
  phase exists to prevent is a webhook that fires but carries a shape the flow
  silently drops.
- **Answered: the flow is a bare trigger** (live path reads no body; see the
  corrected framing above). Apprise-json mode stays the default anyway — it is
  cheap, it matches what any Apprise-trained consumer expects, and its `type`
  field supports the success-gating pattern the flow's orphaned node shows.
- **Notification failure must never fail the work.** A run that completed but
  could not notify logs a warning and records the failure visibly (events log);
  it does not retry forever, does not park jobs, does not crash the scheduler.
- **The webhook URL is config, not a secret** — but treat it as
  operator-sensitive: never log the full URL at info level (it may embed
  tokens in the path, as Uptime-Kuma-style URLs do). Log the host.
- **No new Python dependency without pyproject declaration.** Prefer the
  existing `httpx` client over adding Apprise as a dependency — one POST shape
  does not justify the library. If you conclude otherwise, stop and say why.
- Suites at branch start: backend 1242+, frontend 52+ (post-#34 numbers —
  verify at branch creation and record).

## What "run end" means in an event-driven service

Posterizarr had discrete runs; autoposter mostly does not. The honest v1
mapping, hooked where completion is already recorded:

| Event | Fires when | Source of truth |
|---|---|---|
| `scheduled_run_completed` | any named scheduler job finishes (collections, drift sweep, cleanup, arr sync) | `ScheduledRun.last_finished_at` write in `scheduler/core.py` |
| `full_pass_enqueued` | `POST /api/full-pass` succeeds | the endpoint (lands with PR #34) |

Deliberately NOT in v1: per-item events (volume), queue-drained detection (no
cheap boundary exists — design it when something needs it), Discord formatting
(unconfigured today; roadmap row 20 explicitly may slip). The dispatcher's
event-name + payload-builder split must make adding these later additive.

---

## Task 1: Config block and the payload contract

**Files:**
- Modify: `src/autoposter/config/schema.py`, `config/autoposter.example.yaml`
- Create: `src/autoposter/notify/__init__.py`, `src/autoposter/notify/payload.py`
- Test: `tests/test_notify_payload.py`

**Interfaces:**
- Produces: `NotificationsConfig` (enabled, url, mode: `"apprise-json"` |
  `"autoposter-v1"`, timeout_seconds, retry_count) on `Config`;
  `build_payload(mode, event: str, summary: str, detail: dict) -> dict`.

Two payload modes from day one: `apprise-json` (default — what any
Apprise-trained consumer or a reconnected `body.type` gate expects; field set
verified against Apprise) and `autoposter-v1` (the versioned native shape:
`{"schema": "autoposter/v1", "event", "at", "summary", "detail"}`). The
example config documents both.

- [ ] Step 1: verify Apprise's `json://` body fields against its source; record findings in `payload.py`'s docstring with the URL checked
- [ ] Steps 2-5: failing tests (both modes' exact shapes; unknown mode is a config validation error, not a runtime fallback) → red → implement → green
- [ ] Step 6: `test_example_config_matches_schema` still passes; commit

## Task 2: The dispatcher and sender

**Files:**
- Create: `src/autoposter/notify/dispatch.py`
- Test: `tests/test_notify_dispatch.py`

**Interfaces:**
- Produces: `Notifier` with `async def send(event: str, summary: str, detail: dict) -> bool`,
  constructed from config + the shared `app.state.http` client (the lifespan
  publishes it — pinned by `tests/test_app.py`); a disabled config yields a
  no-op notifier, never `None` (the `NullMDBListClient` precedent,
  `app.py:_build_mdblist`).

Retry: `retry_count` attempts with short backoff, all inside the configured
timeout discipline; total worst-case duration bounded and documented. Failure
after retries: one `logger.warning` (host only, not full URL) + an
`events_log` row so the UI's activity feed shows it. Success: debug log only.

- [ ] Failing tests first, including: the catcher fixture receives the exact
  documented payload; a 500-then-200 upstream succeeds via retry; exhausted
  retries return False, log once, write the events row, and **do not raise**;
  a mutation proof that the never-raises guard can fail (make `send` re-raise,
  show the caller-facing test red).
- [ ] Implement → green → commit

## Task 3: Hooks at the run boundaries

**Files:**
- Modify: `src/autoposter/scheduler/core.py`, `src/autoposter/app.py`,
  `src/autoposter/api/routes.py` (full-pass endpoint)
- Test: extend `tests/test_scheduler_core.py` (or its actual name — read the
  tree), `tests/test_api_full_pass.py`

**Interfaces:**
- Consumes: `Notifier` from Task 2, wired in `create_app`'s lifespan next to
  the other clients.

`scheduled_run_completed` fires where `last_finished_at`/`last_status` are
written — **after** the record is committed, so a notification can never
describe a run the database does not yet show. `full_pass_enqueued` fires from
the endpoint after its commit, carrying `{total, queued, skipped}`.

- [ ] Failing tests: a completed scheduler job notifies with the job's name
  and status; a failed job notifies with `type` reflecting failure; the
  full-pass response is unchanged (the notification is fire-and-forget — the
  request must not wait on the webhook); notification failure does not mark
  the run failed (mutation-prove it).
- [ ] Implement → green → commit

## Task 4: Rehearsal evidence and docs

**Files:**
- Modify: `README.md` (config reference), `docs/superpowers/specs/2026-08-20-autoposter-design.md` (6a entry)
- Report only: rehearsal transcript

The spec's acceptance line: *"the real n8n flow fires in a rehearsal."* From
this dev environment the cluster-internal n8n is unreachable — so the
rehearsal is: run a live `docker compose` stack with the notifier pointed at a
local catcher, trigger a real scheduled run and a real full pass, and capture
the actual POSTs; paste them in the report as the payloads the user can
compare against their flow's trigger node. The true n8n rehearsal is a
cutover-day step for the user — say so in the docs rather than claiming it
happened.

- [ ] Capture the rehearsal transcript; update docs; commit

---

Deliver as PR `feat/phase5a-notifications` → `main` via `tea`. Every guard
mutation-proven; suites green in the container; no AI attribution anywhere.
