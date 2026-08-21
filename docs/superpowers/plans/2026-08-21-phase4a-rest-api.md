# Phase 4a: REST API and Authentication — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything the Web UI needs from the server — authentication and the read and action endpoints — with no frontend toolchain involved.

**Architecture:** A single `/api` router behind one authentication dependency, reading the tables phases 1–3 already fill. Sessions are opaque tokens in the database rather than signed cookies, so they can be revoked and need no signing library. The SPA that consumes this is phase 4b; splitting there keeps this half fully testable in the existing pytest harness and defers the Node decision to where it belongs.

**Tech Stack:** Python 3.13, FastAPI, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18, bcrypt.

## Global Constraints

- **`/healthz` and `/metrics` stay unauthenticated.** Kubernetes probes and Prometheus scrape them, and breaking either takes the pod down or blinds monitoring. Everything under `/api` except login requires a session.
- **The admin password is never stored or configured in plaintext.** The deployment supplies a bcrypt *hash* via `AUTOPOSTER_ADMIN_PASSWORD_HASH`, following the existing `Secrets` pattern for every other credential. It must never appear in `config/autoposter.example.yaml`, in a log line, or in an API response.
- **No endpoint returns a provider API key**, a Plex token, or the password hash. The config endpoint must redact them.
- **Read endpoints must not fall over on a large library.** ~1,953 movies, 283 shows and ~13,000 episodes: every list endpoint paginates, with the limit applied in SQL rather than after fetching.
- **Nothing blocks the event loop.** No Plex calls, no filesystem walks, no image work in a request handler.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it.
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- **No test may make a real outbound request** — an autouse fixture in `tests/conftest.py` enforces this.
- **A new dependency must be declared in `pyproject.toml`.** `tests/test_declared_dependencies.py` walks every `src/` import against that list and will fail otherwise — this is the guard that caught a missing Pillow after it had already broken the built image.
- Baseline on branch start: 1038 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.

## What the data already supports

Phases 1–3 fill everything this exposes, so no new collection work is needed: `media_items`, `renders` (base and badge fingerprints, upload status), `item_facts`, `jobs` (including parked ones), `events_log`, `managed_collections`, `scheduled_runs`.

---

## Task 1: Authentication

**Files:**
- Create: `src/autoposter/api/__init__.py` (empty)
- Create: `src/autoposter/api/auth.py`
- Modify: `src/autoposter/db/models.py`
- Modify: `src/autoposter/config/schema.py` (the `Secrets` class)
- Modify: `pyproject.toml`
- Create: one Alembic migration
- Test: `tests/test_api_auth.py`

**Interfaces:**
- Produces: `Session` model (`id`, `token_hash` `String(64)` unique, `created_at`, `expires_at`, `last_seen_at`); `hash_password(plain: str) -> str`; `verify_password(plain: str, hashed: str) -> bool`; `async create_session(session, ttl_hours: float) -> str` returning the plaintext token; `async session_for_token(session, token: str) -> Session | None`; `async revoke(session, token: str) -> None`; and a FastAPI dependency `require_session`.

**Notes for the implementer:**

- Add `bcrypt>=4.2` to `pyproject.toml` dependencies.
- **Store only a hash of the session token**, exactly as for the password. A database dump should not hand someone a working session. SHA-256 is right here — the token is high-entropy random, so it needs no key stretching, unlike the password.
- Generate tokens with `secrets.token_urlsafe(32)`.
- Compare tokens with `hmac.compare_digest`, not `==`.
- An expired session must not authenticate. Compare against `func.now()`, never the host clock.
- `require_session` reads the `Authorization: Bearer <token>` header and raises 401 when absent, unknown or expired. It must not distinguish those cases in the response — one 401 for all three.
- The login endpoint must take the same time whether or not the account exists; with a single fixed admin there is only one path, but do not short-circuit before the bcrypt check when the hash is unset — verify against a dummy hash instead, or the response time reveals whether the deployment is configured.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_auth.py`:

```python
"""Authentication for the Web UI's API."""
import asyncio

import pytest
from sqlalchemy import select, text

from autoposter.api.auth import (
    create_session,
    hash_password,
    revoke,
    session_for_token,
    verify_password,
)
from autoposter.db.models import Session


def test_a_password_verifies_against_its_hash():
    hashed = hash_password("correct horse")
    assert verify_password("correct horse", hashed) is True
    assert verify_password("Correct horse", hashed) is False


def test_hashing_is_salted():
    """Two hashes of the same password must differ, or the hash file leaks
    which accounts share a password."""
    assert hash_password("same") != hash_password("same")


def test_a_malformed_hash_does_not_raise():
    """A truncated or hand-edited AUTOPOSTER_ADMIN_PASSWORD_HASH must fail
    the login, not 500 the endpoint."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False


async def test_a_session_token_is_returned_in_plaintext_once(session):
    token = await create_session(session, ttl_hours=1)
    assert token and len(token) >= 32


async def test_only_the_hash_of_the_token_is_stored(session):
    """A database dump must not hand someone a working session."""
    token = await create_session(session, ttl_hours=1)
    row = (await session.execute(select(Session))).scalar_one()
    assert row.token_hash != token
    assert token not in row.token_hash


async def test_a_valid_token_resolves(session):
    token = await create_session(session, ttl_hours=1)
    assert await session_for_token(session, token) is not None


async def test_an_unknown_token_does_not_resolve(session):
    await create_session(session, ttl_hours=1)
    assert await session_for_token(session, "nope") is None


async def test_an_expired_token_does_not_resolve(session):
    token = await create_session(session, ttl_hours=1)
    await session.execute(text("UPDATE sessions SET expires_at = now() - interval '1 hour'"))
    assert await session_for_token(session, token) is None


async def test_a_revoked_token_does_not_resolve(session):
    token = await create_session(session, ttl_hours=1)
    await revoke(session, token)
    assert await session_for_token(session, token) is None


async def test_two_sessions_are_independent(session):
    first = await create_session(session, ttl_hours=1)
    second = await create_session(session, ttl_hours=1)
    await revoke(session, first)
    assert await session_for_token(session, first) is None
    assert await session_for_token(session, second) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_api_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.api'`

- [ ] **Step 3: Implement, generate the migration against a clean database, and commit**

The migration adds `sessions`. Follow the established sequence — `tests/conftest.py` calls `create_all` against the same database, so autogenerating after a test run yields an empty `pass` body that silently creates nothing on deploy. Reset (`docker compose down`, **without** `-v`), `alembic upgrade head`, autogenerate, confirm real DDL. `tests/test_migrations.py` guards it.

```bash
rtk proxy python -m pytest tests/test_api_auth.py tests/test_migrations.py tests/test_declared_dependencies.py -v
rtk proxy ruff check src tests
git add src/autoposter/api src/autoposter/db/models.py src/autoposter/config/schema.py pyproject.toml alembic tests/test_api_auth.py
git commit --no-gpg-sign -m "Add password and session authentication for the API"
```

---

## Task 2: The login endpoint and the router

**Files:**
- Create: `src/autoposter/api/routes.py`
- Modify: `src/autoposter/app.py`
- Test: `tests/test_api_login.py`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces: `router` with `POST /api/login`, `POST /api/logout`, `GET /api/me`; mounted in `app.py`.

**Notes for the implementer:**

- `POST /api/login` takes `{"password": "..."}` and returns `{"token": "...", "expires_at": "..."}`, or 401. It is the only `/api` route without the session dependency.
- **`/healthz` and `/metrics` must remain reachable without a token.** There is a test for this; k8s probes and Prometheus depend on it.
- When `AUTOPOSTER_ADMIN_PASSWORD_HASH` is unset, every login attempt fails with 401 and a log line saying the deployment has no admin password configured. It must not fall open.
- Use FastAPI's `Depends` for the session, so every later route gets it by declaration rather than by remembering.
- Follow `tests/test_routes.py` for how the app is built in tests (`ASGITransport`); note the lifespan does not run there.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_login.py` covering: a correct password returns a token; a wrong one returns 401 with no token; an unset hash means every attempt is 401; `/api/me` requires a token and returns 401 without one, with a malformed one, and with an expired one; a valid token reaches it; `/api/logout` revokes so the same token then fails; **and `/healthz` and `/metrics` are reachable with no token at all**.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_api_login.py -v`
Expected: FAIL — the routes do not exist

- [ ] **Step 3: Implement, run the suite, ruff and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/api src/autoposter/app.py tests/test_api_login.py
git commit --no-gpg-sign -m "Add the login endpoint and mount the API router"
```

---

## Task 3: Dashboard endpoints

**Files:**
- Modify: `src/autoposter/api/routes.py`
- Test: `tests/test_api_dashboard.py`

**Interfaces:**
- Produces: `GET /api/status`, `GET /api/events`.

**What they return:**

`/api/status` — queue depth by state, worker count, items processed in the last 24 hours, failure and parked counts, and each scheduled job's last run and status from `scheduled_runs`.

`/api/events` — the most recent `events_log` rows, newest first, `limit` capped (default 50, maximum 200), each with `source`, `event_type`, `outcome`, `received_at`.

**Notes for the implementer:**

- Aggregate in SQL with `GROUP BY`, not by fetching rows and counting in Python. The `jobs` table is the hot one.
- "Processed in the last 24 hours" is relative to `func.now()`.
- `events_log.payload` holds whole webhook bodies. **Do not return it** — it is large and may carry tokens from the sending service. Return the summary fields only.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_dashboard.py` covering: an empty database returns zeroed counts rather than erroring; queued, running and parked jobs are counted separately; the 24-hour window excludes older rows; scheduled job status is reported; events come back newest first; the limit is respected and capped above its maximum; **the event payload is never included in the response**; and all of it requires a session.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_api_dashboard.py -v`
Expected: FAIL — 404, the routes do not exist

- [ ] **Step 3: Implement, run the suite, ruff and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/api/routes.py tests/test_api_dashboard.py
git commit --no-gpg-sign -m "Add dashboard status and activity endpoints"
```

---

## Task 4: Library and collection endpoints

**Files:**
- Modify: `src/autoposter/api/routes.py`
- Test: `tests/test_api_library.py`

**Interfaces:**
- Produces: `GET /api/items`, `GET /api/items/{item_id}`, `GET /api/collections`.

**What they return:**

`/api/items` — paginated (`limit` default 50, maximum 200; `offset`), filterable by `library`, `kind` and render `status`, searchable by title. Each row: id, title, library, kind, rating key, and a per-item summary of render status.

`/api/items/{item_id}` — the item, its `item_facts`, and every `renders` row with `art_kind`, `status`, `fingerprint`, `badge_fingerprint`, `upload_status`, `adopted` and timestamps. 404 when unknown.

`/api/collections` — managed collections with library, title, kind, and whether a poster hash is recorded.

**Notes for the implementer:**

- Apply `limit`/`offset` in SQL. A naive `.all()` here fetches 16,000 rows per request.
- Search must not be vulnerable to a pattern injected through `%` or `_`; escape them, or use a parameterised `ILIKE` with the wildcards added server-side.
- Return a total count alongside the page so the UI can paginate, using a separate `COUNT(*)` rather than `len()` of everything.
- These are read-only. Nothing here writes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_library.py` covering: pagination returns the right slice and total; the limit is capped; filtering by library, kind and status each work and combine; search matches a substring case-insensitively; **a search containing `%` matches literally rather than as a wildcard**; item detail includes facts and renders; an unknown id is 404; collections list what is managed; and everything requires a session.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_api_library.py -v`
Expected: FAIL — 404, the routes do not exist

- [ ] **Step 3: Implement, run the suite, ruff and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/api/routes.py tests/test_api_library.py
git commit --no-gpg-sign -m "Add library browse and collection endpoints"
```

---

## Task 5: Failures, actions and the redacted config

**Files:**
- Modify: `src/autoposter/api/routes.py`
- Test: `tests/test_api_actions.py`

**Interfaces:**
- Produces: `GET /api/jobs/parked`, `POST /api/jobs/{job_id}/retry`, `POST /api/jobs/{job_id}/dismiss`, `POST /api/items/{item_id}/reprocess`, `GET /api/config`.

**Notes for the implementer:**

- Retry resets a parked job to pending and clears its attempt count, so the worker pool picks it up. Dismiss marks it dismissed; **it does not delete the row** — the history of what failed is worth keeping, and this project deletes nothing anywhere else either.
- `reprocess` enqueues a job for one item using the existing `enqueue` and its `dedupe_key` convention, so asking twice does not queue twice.
- **`GET /api/config` must redact every secret.** Return the config with provider keys, the Plex token and the admin hash replaced by a marker — never the values, not even partially. There is a test that asserts no known secret value appears anywhere in the response body.
- Acting on an unknown or already-dismissed job is a 404 or a no-op, never a 500.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_actions.py` covering: parked jobs are listed with their reasons; retry makes a parked job claimable again; dismiss marks without deleting; retrying an unknown job is 404; reprocess enqueues once and twice is still once; the config endpoint returns structure; **the config response contains no secret value** (seed a recognisable one and assert its absence); and all of it requires a session.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_api_actions.py -v`
Expected: FAIL — 404, the routes do not exist

- [ ] **Step 3: Implement and run the full suite**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — 1038 baseline plus roughly 60 new tests, 5 skipped

- [ ] **Step 4: Document it**

In `deploy/README.md`: how to generate the bcrypt hash and set `AUTOPOSTER_ADMIN_PASSWORD_HASH`, that an unset hash means no one can log in rather than everyone can, that `/healthz` and `/metrics` stay open for probes and scraping, and the session lifetime.

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter deploy/README.md tests
git commit --no-gpg-sign -m "Add failure handling, item actions and a redacted config endpoint"
```

---

## Deferred to phase 4b

- **The SPA itself** — React, the Node build stage in the Dockerfile, and the dark theme matching the *arr family. The palette is already captured from Radarr's own `Styles/Themes/dark.js` rather than eyeballed.
- **The WebSocket** for live updates. `/api/events` polling is enough to build the dashboard against; the socket is an optimisation once there is a UI to feel it.
- **Provider attribution.** TheTVDB and TMDB notices belong on the API settings page, which is a UI surface. Recorded in spec §6 as an acceptance criterion for the UI phase.
