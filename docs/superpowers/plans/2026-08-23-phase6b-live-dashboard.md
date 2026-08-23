# Phase 6b: Live Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dashboard stops polling: an authenticated NDJSON stream pushes
status + recent events to the page, which renders updates as they happen.
(The other half of roadmap 6b — the live log tail — already shipped in the
logs-page PR.)

**Architecture:** A per-process `StatusBroadcaster` polls the database
server-side — but only while at least one subscriber is connected — and fans
a full snapshot (`status` + last-25 `events`) to subscriber queues whenever
the snapshot *changes*. One `GET /api/dashboard/stream` endpoint serves it as
NDJSON (current snapshot first, then changes, `{"heartbeat": true}` on 15s
idle), copying `api/logs.py`'s proven shape. The Dashboard swaps its 5s
double-fetch loop for `apiFetchNdjson` + the Logs page's reconnect pattern.

**Transport decision (deviation from the roadmap's title, deliberate):** the
roadmap calls 6b "WebSocket" and names header-auth as the risk. That risk
only exists for a browser `WebSocket` (cannot set headers); the shipped
NDJSON pattern (`api/logs.py` + `apiFetchNdjson`) already streams under
`Authorization: Bearer` with a plain `Depends(require_session)`. No
WebSocket machinery exists anywhere in the repo (no route, no vite `ws:
true`, no ingress config in-tree) — introducing it would add infrastructure
to deliver the same capability the existing pattern delivers. The PR states
this.

**Change-detection decision:** DB-backed server-side polling, NOT in-process
event fanout and NOT LISTEN/NOTIFY. The three `events_log` write sites are
independent session commits with no shared hook, and the codebase is written
to tolerate ≥2 replicas (jobs claimed `FOR UPDATE SKIP LOCKED`; the repo
does not pin the replica count) — an in-process bus would silently drop
events written by another replica. LISTEN/NOTIFY would be correct but needs
a pool-external lifespan-managed connection plus write-site changes. Reading
the shared database is replica-correct by construction, and net-cheaper than
today: one poll per process while subscribed (vs two queries per 5s per open
tab), zero when nobody is watching.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds throughout.
- Container-only testing from `D:\Sites\autoposter` (PowerShell): backend
  `docker compose run --rm test pytest …`, one session at a time; frontend
  `docker compose run --rm web npm test` + `npm run build`. Zero skips.
- Baselines on this branch's parent (main 294fa89): backend **1314 passed**
  under `-m "not imagemagick"`, frontend **94 passed**. Verify at start.
- **ASGITransport buffers** — endpoint-level tests of the unending stream
  hang. Copy `tests/test_api_logs.py`'s exact idioms (documented there at
  :145-152): endpoint tests only for what terminates (401; headers via a
  `SimpleNamespace(app=app)` fake request + `body_iterator.aclose()`);
  stream behaviour via the module-level generator with `anext()` +
  `asyncio.wait_for(…, 5)` in `try/finally: aclose()`; interval/heartbeat
  shrunk via `monkeypatch.setattr`.
- The structural auth sweep walks `app.openapi()`: the new endpoint carries
  the inline `_: SessionModel = Depends(require_session)` form
  (`api/logs.py:184` is the precedent).
- Mutation proofs for every guard (mutate → red → copy+cmp restore → green),
  transcripts in reports.
- `/api/events` deliberately never selects `payload` (may carry webhook
  tokens, `routes.py:267-268`) — the stream reuses the same 4-column shape
  and MUST NOT widen it.
- Stage by name; `--no-gpg-sign`; no AI attribution; no new dependencies.

## Verified facts (file:line checked 2026-08-23; hand to every implementer)

- Dashboard today: `Dashboard.tsx:17` `POLL_MS = 5000`; effect :83-112 does
  `Promise.all` of `/api/status` + `/api/events?limit=25` per tick; stale
  comment :15-16 defers "the socket" to phase 4c. Renders: workers,
  processed_last_24h, jobs_by_state cards, scheduled_jobs table (incl.
  interval_seconds), events table.
- `/api/status` handler `routes.py:201-255`: two `jobs` aggregates + one
  `ScheduledRun` select + in-memory `scheduler_intervals` merge; exact
  response shape at :240-254. `/api/events` `routes.py:258-288`: 4 columns,
  `ORDER BY received_at DESC`, limit capped 200, index-served.
- NDJSON template: `api/logs.py` — `LogBuffer` (:41-130) with
  subscribe-returns-backlog+queue-atomically, `ndjson_lines(buffer)`
  module-level generator (:148-179, heartbeat via `wait_for` timeout,
  unsubscribe in `finally`), endpoint (:182-205) with
  `media_type="application/x-ndjson"`, `Cache-Control: no-store`,
  `X-Accel-Buffering: no`.
- Frontend template: `apiFetchNdjson(path, onValue, signal)`
  (`client.ts:132-177`, abort resolves); reconnect pattern
  `Logs.tsx:37-76` (`RECONNECT_MS = 3000`, clear-state-on-connect because
  the server resends from scratch, aborted-check on both exits, timer
  cleanup).
- Collections' diff-now watch polls `/api/status` conditionally
  (`Collections.tsx:114-157`) — OUT of 6b's scope, leave it.
- Frontend test idioms: `Dashboard.test.tsx` `json()` helper (fresh
  Response per call — bodies are single-read, gotcha at :253-254),
  `stubFetch` path router, `jobRow(name)` row scoping, fake timers with
  `shouldAdvanceTime`; the polling test :116-137 is the one this phase
  rewrites. `Logs.test.tsx` is the streaming-stub template (never-closing
  `ReadableStream` honouring the abort signal). `vite.config.ts` sets
  `unstubGlobals: true`.
- Stale comments to update in this phase: `Dashboard.tsx:15-16`,
  `client.ts:12-13` (both promise a WebSocket that is now not the design);
  soften the "because the dashboard polls" clause of the uvicorn.access
  rationale in `api/logs.py:53-55` and `tests/test_api_logs.py:77-79`
  (the exclusion itself stays — access-line noise is reason enough).

---

## Task 1: Backend — StatusBroadcaster + /api/dashboard/stream

**Files:**
- Create: `src/autoposter/api/dashboard_stream.py`
- Modify: `src/autoposter/api/routes.py` (include router; factor the
  status/events snapshot queries so the endpoint and broadcaster share one
  implementation — no copy-pasted SQL), `src/autoposter/app.py` (state
  wiring), `api/logs.py` + `tests/test_api_logs.py` (comment softening only)
- Test: `tests/test_api_dashboard_stream.py`

**Interfaces:**
- Produces: `StatusBroadcaster(session_factory, interval_seconds=2.0,
  events_limit=25)` with `subscribe() -> tuple[dict | None, asyncio.Queue]`
  (latest snapshot + queue, atomically; `None` snapshot only before the
  first poll completes), `unsubscribe(queue)`; it runs its poll loop ONLY
  while `_subscribers` is non-empty (first subscriber starts an
  `asyncio.create_task` loop; last unsubscribe stops it — no background
  work with zero viewers). Each poll builds
  `{"status": <exact /api/status shape>, "events": <exact /api/events
  shape's list>}` via the shared snapshot helpers and fans out ONLY when
  the snapshot differs from the previous one (compare the built dicts;
  `json.dumps(..., sort_keys=True, default=str)` equality is acceptable).
  Queue size 16, drop-on-full (a stalled reader misses intermediate
  snapshots and gets the next changed one — never an unbounded queue).
- Produces: `GET /api/dashboard/stream` (inline `Depends(require_session)`)
  → NDJSON: the current snapshot first (if one exists), then changed
  snapshots, `{"heartbeat": true}` per 15s idle; same headers as the logs
  stream; module-level `ndjson_snapshots(broadcaster)` generator factored
  exactly like `ndjson_lines`, unsubscribe in `finally`.
- Consumes: the poll interval needs no config knob (YAGNI — constant 2.0s;
  a comment says why: half the old client tick, one per process).
- `app.py`: `app.state.dashboard_broadcaster` created unconditionally in
  `create_app` (the logs `log_buffer` precedent); no lifespan work needed —
  the loop is subscriber-driven, and its task must be created lazily from
  `subscribe()` (called on the running loop) rather than in `create_app`
  (no loop there). Broadcaster errors: one failed poll logs a warning and
  retries next tick; it must never kill the loop while subscribers remain.

- [ ] Failing tests first, per the logs idioms: snapshot shape matches the
      REST endpoints exactly (build both from the same fixture data and
      compare); no-change ⇒ no fanout (poll twice on identical data, queue
      stays empty); change ⇒ exactly one snapshot on the queue; loop stops
      when the last subscriber leaves (assert the task finishes) and
      restarts for a new subscriber; generator yields
      initial-snapshot-then-change-then-heartbeat; unsubscribe-on-aclose;
      endpoint 401; endpoint headers via the fake-request idiom; a poll
      that raises keeps the loop alive (next tick still fans out).
- [ ] Mutation proofs: (1) drop the change-comparison (fan out every poll)
      — the no-change test reds; (2) drop the `finally` unsubscribe — the
      leak test reds.
- [ ] Full backend suite ≥1314+new, 0 skips; ruff clean. Commit.

## Task 2: Frontend — Dashboard consumes the stream

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx`, `frontend/src/api/types.ts`
  (a `DashboardSnapshot` interface + `isDashboardSnapshot` guard mirroring
  `isLogLine`), `frontend/src/api/client.ts` (comment :12-13 only)
- Test: `frontend/src/pages/Dashboard.test.tsx` (rewrite the polling test
  as stream tests; keep every other test passing unchanged)

**Interfaces:**
- Consumes: `GET /api/dashboard/stream` NDJSON
  (`{status, events} | {heartbeat: true}`); `apiFetchNdjson`; the
  `Logs.tsx:37-76` reconnect pattern verbatim (3s, clear-nothing needed —
  each snapshot fully replaces state, so on reconnect simply keep the last
  rendered snapshot until the new connect's first snapshot arrives; show a
  reconnecting indicator like the Logs page's status element).
- The `setInterval` loop, `POLL_MS`, and the two per-tick fetches are
  deleted; the initial paint comes from the stream's first line (the
  broadcaster sends the current snapshot on connect). The full-pass and
  run-now click handlers keep their one-shot fetches — action feedback must
  not wait up to 2s for the stream (their optimistic UI stays; the next
  snapshot reconciles).
- Stale comment `Dashboard.tsx:15-16` replaced by the real design note.

- [ ] Failing tests first (stream stub per `Logs.test.tsx`'s
      abort-honouring `ReadableStream` helper — read it before writing):
      first snapshot renders counts/jobs/events; a second snapshot updates
      a count in place with NO additional `/api/status`//`/api/events`
      fetches (assert fetch call paths); heartbeats ignored; stream
      failure shows the reconnecting state and reconnects after 3s (fake
      timers), and the last data stays rendered meanwhile; unmount aborts
      (no post-unmount activity); run-now/full-pass handlers still work
      against the stream stub (their tests keep passing).
- [ ] Mutation proofs: (1) break `isDashboardSnapshot` to accept
      heartbeats — heartbeat test reds; (2) drop the reconnect timer — the
      reconnect test reds.
- [ ] Frontend suite ≥94+new−1 (the deleted polling test), 0 skips; build
      clean. Commit.

## Task 3: Review, roadmap, PR

- Roadmap: row 72 and the 6b phase entry get `answered 6b:` /
  **delivered** notes stating both the capability and the transport
  deviation (NDJSON over WebSocket, with the one-line auth rationale).
- Whole-branch review (most capable model), one batched fix round if
  findings; rebase onto main if it moved; both suites after any rebase.
- PR `feat/phase6b-live-dashboard` → `main` via `tea`: the design
  decisions (transport, DB-backed change detection, replica-correctness),
  the load math (per-tab 5s double-fetch → per-process 2s single poll,
  zero idle), suite deltas. No AI attribution.
