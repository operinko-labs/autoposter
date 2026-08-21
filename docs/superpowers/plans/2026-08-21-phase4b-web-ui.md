# Phase 4b: Web UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React SPA, styled after the *arr applications, served by the same
container, covering every view the phase 4a API can already answer — plus the
provider attribution the spec makes an acceptance criterion for this phase.

**Architecture:** Vite + React + TypeScript building to static files that
FastAPI serves at `/`, with a catch-all fallback so client-side routes survive
a page reload. No server-side rendering and no separate web server: one image,
one process, same deployment story as phases 1-3. The SPA holds its session
token in memory plus `sessionStorage`, sends it as a bearer token, and treats
any 401 as "log in again".

**Tech Stack:** React 19, TypeScript, Vite, React Router, Vitest + Testing
Library. No component framework and no CSS-in-JS — the *arr look is a small
set of CSS custom properties and plain stylesheets, and a UI this size does
not earn a dependency with a styling runtime.

## Global Constraints

Carried forward from phase 4a; all still binding.

- **`/healthz` and `/metrics` stay unauthenticated**, and must not be
  shadowed by the SPA's catch-all route. Neither may `/api/*`: a request for
  a missing API path must still return its JSON 404, never the SPA's HTML.
- **No endpoint returns a provider API key**, a Plex token, or the password
  hash — and the UI must not reintroduce one by echoing a value back.
- **The session token never goes into `localStorage`** and never into a URL.
  `sessionStorage` scopes it to the tab and drops it when the tab closes.
- **Nothing blocks the event loop.** Serving static files is the only new
  server work here.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it.
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare
  `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- **No test may make a real outbound request** — an autouse fixture in
  `tests/conftest.py` enforces this. This binds the frontend too: Vitest must
  not reach the network, and the build must not fetch a font or an icon set
  at runtime.
- **A new Python dependency must be declared in `pyproject.toml`.**
  `tests/test_declared_dependencies.py` walks every `src/` import against that
  list.
- **CI path filters must not skip a file the suite reads.**
  `tests/test_ci_path_filters.py` enforces this, and `frontend/` sources are
  now such files.
- Baseline on branch start: 1074 passed, 5 skipped, `ruff check src tests`
  clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between
  transactions, so time-sensitive tests flake at roughly 5%. Re-run before
  concluding a failure is real.

## Scope: what this phase builds, and what it cannot

Spec section 6 lists five views. Three are fully answerable by the endpoints
phase 4a shipped; two are not, and the gap is in the API rather than the UI:

| Spec view | Endpoint it needs | In 4b? |
|---|---|---|
| Dashboard | `GET /api/status`, `GET /api/events` | Yes |
| Failures | `GET /api/jobs/parked`, retry, dismiss | Yes |
| API settings + attribution | `GET /api/config` | Yes |
| Library browser, item detail | needs an **image-serving endpoint** — `GET /api/items/{id}` returns fingerprints and upload status but no artwork URL, so "base vs. badged side-by-side" cannot be drawn | **No — 4c** |
| Config editor | needs a **config write endpoint** with schema validation and hot-reload; `GET /api/config` is read-only and redacted | **No — 4c** |

Collections are a partial case: `GET /api/collections` returns member counts,
so a read-only list is buildable, but "last diff result" and "diff now" have
no endpoint. This phase ships the read-only list and leaves the actions to 4c.

Deferring these is a scope call, not an omission — building either view now
would mean designing its API endpoint inside a frontend task, which is how
half-specified endpoints get shipped. **Phase 4c is: image serving, item
detail, library browser, config write + hot-reload, collection actions, and
the WebSocket that replaces this phase's polling.**

## Decisions taken here

**Accent colour: violet `#8b5cf6`.** The *arr suite already claims blue
(Sonarr), gold (Radarr) and orange (Prowlarr); violet is distinct at tab-strip
size, which is the whole point of their per-app accents. It is defined once as
`--accent` in `theme.css` and used nowhere as a literal, so changing it is a
one-line edit. **Flagged for the user** — this was asked and not answered, and
is trivially reversible.

**Node 26.7.0, pinned across all three environments.** This box had 24.8.0,
which is neither the 24 LTS nor current, so the frontend targets the latest
release instead. `engines` requires `>=26.0.0`, the Dockerfile and CI both use
`node:26-alpine`, and — since local Node is older — every frontend build and
test in this phase is run inside that container rather than on the host, so
what is verified is what ships.

**Polling, not WebSocket.** The spec wants live updates over a WebSocket. No
such endpoint exists yet, and adding one is a server task that belongs with
the other 4c endpoints. The dashboard polls every 5 seconds; the fetch layer
is written so swapping in a socket later touches one module.

**System font stack, no webfont.** The *arr apps use Roboto. Fetching it from
Google Fonts would put a runtime network dependency into a cluster-internal
deployment, and vendoring it adds a licence file and ~150 KB for a visual
detail. The stack leads with the platform UI font.

**TheTVDB attribution is rendered as text plus a direct link, not their
image.** Their terms require "attribution with a direct link to TheTVDB.com",
which text satisfies. The spec prefers their brand asset, but that asset is
not vendored and pulling a third-party brand image from the network is not
something to do unattended. **Flagged for the user** — swapping in the
official image later is a drop-in replacement.

**TMDB attribution uses the already-vendored logo.**
`assets/badges/images/rating/TMDb.png` (144x75, RGBA) is TMDB's logo, already
in the repo under the Kometa MIT carry-over. It is displayed smaller than the
application's own wordmark, as their terms require, alongside the exact
notice text.

---

## Task 1: Frontend scaffold, theme, and the build wiring

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`,
  `frontend/vite.config.ts`, `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/theme.css`
- Modify: `.gitignore`

**Interfaces:**
- Produces: an `npm run build` in `frontend/` emitting to `frontend/dist/`,
  and the CSS custom properties every later task styles against.

The theme is the *arr look reduced to tokens. Exact values, used as
`var(--name)` and never as literals:

```css
:root {
  --bg: #1a1a1a;
  --bg-elevated: #212121;
  --surface: #262626;
  --surface-hover: #2f2f2f;
  --border: #333333;
  --text: #cccccc;
  --text-strong: #ffffff;
  --text-muted: #888888;
  --accent: #8b5cf6;
  --accent-hover: #a78bfa;
  --ok: #27c24c;
  --warn: #ddbf42;
  --error: #f05050;
  --radius: 4px;
  --sidebar-width: 210px;
}
```

- [ ] **Step 1: Scaffold and pin**

Vite's React-TS template, then pin every dependency to an exact version — no
`^`. CI resolving a newer minor than the lockfile is exactly the skew that
broke this repo three times (ruff, Pillow, fastapi); the frontend starts with
`package-lock.json` committed and exact versions.

- [ ] **Step 2: Write `theme.css` with the tokens above**

- [ ] **Step 3: Verify the build emits**

Run: `cd frontend && npm ci && npm run build`
Expected: `frontend/dist/index.html` and a hashed asset bundle exist.

- [ ] **Step 4: Ignore build output, commit the lockfile**

`frontend/dist/` and `frontend/node_modules/` are ignored;
`frontend/package-lock.json` is committed.

- [ ] **Step 5: Commit**

## Task 2: Serving the SPA from FastAPI

**Files:**
- Create: `src/autoposter/api/spa.py`
- Modify: `src/autoposter/app.py`
- Test: `tests/test_spa_serving.py`

**Interfaces:**
- Consumes: `create_app(config, session_factory, secrets)` from
  `src/autoposter/app.py`.
- Produces: `mount_spa(app, dist: Path | None) -> None`.

The ordering rule is the whole risk in this task: the catch-all must be
mounted **after** `/api`, `/healthz` and `/metrics`, and must decline any path
under those prefixes. A catch-all that swallows `/api/nonexistent` turns a
JSON 404 into an HTML page and every API client's error handling breaks.

When `dist` does not exist — a dev checkout with no `npm run build` — mounting
is skipped entirely rather than erroring, so the Python test suite and a bare
`uvicorn` run need no Node toolchain.

- [ ] **Step 1: Write the failing tests**

```python
async def test_api_404_stays_json_not_spa_html(client_with_spa):
    response = await client_with_spa.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


async def test_healthz_is_not_shadowed_by_the_spa(client_with_spa):
    response = await client_with_spa.get("/healthz")
    assert response.status_code == 200


async def test_a_client_route_returns_the_index(client_with_spa):
    response = await client_with_spa.get("/failures")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


async def test_missing_dist_is_not_an_error(session_factory):
    app = create_app(load_config(EXAMPLE), session_factory, _secrets())
    mount_spa(app, Path("does/not/exist"))
    # No exception, and the API still answers.
```

- [ ] **Step 2: Run them and watch them fail**

Run: `rtk proxy python -m pytest tests/test_spa_serving.py -v`
Expected: FAIL — `mount_spa` does not exist.

- [ ] **Step 3: Implement `mount_spa`**

- [ ] **Step 4: Run them and watch them pass**

- [ ] **Step 5: Commit**

## Task 3: The fetch layer and authentication

**Files:**
- Create: `frontend/src/api/client.ts`, `frontend/src/api/types.ts`
- Create: `frontend/src/auth/SessionContext.tsx`
- Create: `frontend/src/pages/Login.tsx`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Produces: `apiFetch<T>(path, init?): Promise<T>` which attaches the bearer
  token and throws a typed `ApiError` carrying the status; `useSession()`
  exposing `{token, login, logout, status}`.

Every 401 from any call clears the session and routes to the login page —
centrally, in `apiFetch`, so no page has to remember to handle it.

- [ ] **Step 1: Write the failing tests** (token attached; 401 clears the
  session; the token never reaches `localStorage`)
- [ ] **Step 2: Run them and watch them fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run them and watch them pass**
- [ ] **Step 5: Commit**

## Task 4: The application shell

**Files:**
- Create: `frontend/src/App.tsx`, `frontend/src/shell/Sidebar.tsx`,
  `frontend/src/shell/shell.css`

A fixed-width left sidebar over a dark body, active item marked with an
`--accent` left border — the *arr layout. Nav: Dashboard, Collections,
Failures, Settings. (Library is deliberately absent; it arrives in 4c with
the endpoint that can fill it.)

- [ ] **Step 1: Implement the shell and routes**
- [ ] **Step 2: Verify the build still emits**
- [ ] **Step 3: Commit**

## Task 5: Dashboard

**Files:**
- Create: `frontend/src/pages/Dashboard.tsx`, `frontend/src/pages/dashboard.css`
- Test: `frontend/src/pages/Dashboard.test.tsx`

Job counts by state as stat cards, worker count, processed-in-24h, the
scheduled-run table with last status, and the activity feed from
`/api/events`. Polls every 5 seconds; the interval is cleared on unmount.

- [ ] **Step 1: Write the failing test** (renders counts from a mocked
  payload; clears its interval on unmount)
- [ ] **Step 2: Run it and watch it fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run it and watch it pass**
- [ ] **Step 5: Commit**

## Task 6: Failures and Collections

**Files:**
- Create: `frontend/src/pages/Failures.tsx`, `frontend/src/pages/Collections.tsx`
- Test: `frontend/src/pages/Failures.test.tsx`

Parked jobs with their reason, and per-row Retry and Dismiss posting to the
phase 4a endpoints, with the list refreshed from the server rather than
mutated optimistically. Collections is the read-only list.

- [ ] **Step 1: Write the failing test** (retry calls the endpoint and the row
  leaves the parked list)
- [ ] **Step 2: Run it and watch it fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run it and watch it pass**
- [ ] **Step 5: Commit**

## Task 7: Settings and provider attribution

**Files:**
- Create: `frontend/src/pages/Settings.tsx`, `frontend/src/pages/settings.css`
- Create: `frontend/public/tmdb-logo.png` (copied from
  `assets/badges/images/rating/TMDb.png`)
- Test: `frontend/src/pages/Settings.test.tsx`
- Test: `tests/test_attribution_present.py`

This is the phase's acceptance criterion, so it is tested from both sides: a
component test that the notices render, and a Python test asserting the built
bundle contains them — which catches the case where the component exists but
the route was never wired up.

Required, exactly:

- TMDB: *"This product uses TMDB and the TMDB APIs but is not endorsed,
  certified, or otherwise approved by TMDB."* plus the logo, sized smaller
  than the application's own wordmark.
- TheTVDB: attribution text with a direct anchor to `https://thetvdb.com`.

The page also renders the redacted config from `/api/config`, and must render
the redaction marker rather than any value that looks like a key.

- [ ] **Step 1: Write the failing tests**
- [ ] **Step 2: Run them and watch them fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run them and watch them pass**
- [ ] **Step 5: Commit**

## Task 8: Container build and CI

**Files:**
- Modify: `Dockerfile`
- Modify: `.forgejo/workflows/ci.yml`
- Modify: `tests/test_ci_path_filters.py`

A `node:26-alpine` stage runs `npm ci && npm run build`; the Python stage
copies `frontend/dist` to `/app/frontend/dist`, and `AUTOPOSTER_SPA_DIST`
points at it. `npm ci`, not `npm install` — it fails on a lockfile mismatch
instead of silently resolving something else.

CI gains a frontend lint/test/build step. `frontend/**` must **not** be in
`paths-ignore`, and `tests/test_ci_path_filters.py` gains that assertion, so
a frontend-only change cannot skip CI.

The image job gains a check in the same spirit as the existing
entrypoint-import one: a built image whose SPA is missing would serve a 404 at
`/` and look healthy to every probe.

```bash
docker run --rm autoposter:ci python -c "
from pathlib import Path
import os
dist = Path(os.environ['AUTOPOSTER_SPA_DIST'])
assert (dist / 'index.html').is_file(), dist
print('SPA present at', dist)
"
```

- [ ] **Step 1: Add the Node build stage**
- [ ] **Step 2: Build the image and verify the SPA is inside it**
- [ ] **Step 3: Add the CI steps and the path-filter assertion**
- [ ] **Step 4: Commit**

## Task 9: Documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-08-20-autoposter-design.md` (section 6a)
- Modify: `README.md`

Record what shipped, the two flagged decisions (accent colour, TheTVDB text
attribution), and that phase 4c owns image serving, item detail, the library
browser, config write plus hot-reload, collection actions and the WebSocket.

- [ ] **Step 1: Update the spec and README**
- [ ] **Step 2: Commit**
