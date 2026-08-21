# Phase 4c: Library Browser and Item Detail — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The two views spec section 6 asks for and phase 4b could not build — an
art-grid library browser with filters, and an item detail page showing the base
artwork beside what Plex is actually serving, with the facts and fingerprints
that produced it.

**Architecture:** One new artwork endpoint serving bytes, three small read
endpoints, and the React pages that consume them. The artwork endpoint is the
only genuinely new server capability; everything else the item and render
tables already hold.

**Tech Stack:** As phase 4b — FastAPI, async SQLAlchemy, React 19, TypeScript,
Vite, Vitest. Python 3.14.7, Node 26.7.0, PostgreSQL 18.1, all pinned exactly.

## Global Constraints

- **Nothing blocks the event loop.** Image reads, resizes and any badge
  recomposition go through `asyncio.to_thread`. `assets_root` can be an NFS
  mount — a bare `open()` in a handler stalls every other request.
- **`/healthz` and `/metrics` stay unauthenticated**; everything under `/api`
  except login requires a session. A missing `/api` path returns its JSON 404,
  never the SPA shell.
- **No endpoint returns a provider API key**, a Plex token, or the password
  hash.
- **Never call `.refresh()` on a Plex object** — a guard test forbids it.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Read endpoints must not fall over on a large library**: ~1,953 movies, 283
  shows, ~13,000 episodes. Every list endpoint paginates in SQL.
- **The artwork endpoint must not become a file-read primitive.** It resolves a
  path from the database, never from the URL. Path traversal via `art_kind` or
  an item id is the risk; `tests/test_spa_serving.py`'s raw-ASGI harness is the
  precedent for testing it properly — httpx normalises dot segments and will
  silently prove nothing.
- **Run the suite in the container**: `docker compose run --rm test pytest`.
  A bare host `pytest` now fails at collection by design — `tests/conftest.py`
  requires `AUTOPOSTER_TEST_DATABASE_URL` with no fallback.
- **Never run two pytest sessions against one PostgreSQL** — they deadlock on
  `DROP TABLE jobs`. Use the compose stack, which brings its own.
- **Never `docker compose down -v`.**
- **Commit with `git commit --no-gpg-sign`**; no AI attribution in messages.
- **A new Python dependency must be declared in `pyproject.toml`** —
  `tests/test_declared_dependencies.py` walks every `src/` import against it.
- **Guard tests must be proven falsifiable.** Break the thing the test guards,
  show it red, restore, show it green, and paste both outputs. This repository
  has shipped tests that could not fail six times; the mutation proof is the
  only thing that has reliably caught it.
- Baseline on branch start: container, Python 3.14.7, `1183 passed, 0 skipped`.

## What already exists, and what does not

Verified against `src/autoposter/api/routes.py` and `src/autoposter/db/models.py`:

| Needed | Status |
|---|---|
| Item list with pagination, library/kind, render status | **`GET /api/items`** — returns `total`, `items[{id,title,library,kind,rating_key,render_status}]` |
| Item detail with facts and render history | **`GET /api/items/{id}`** — returns `facts{...}` and `renders[{art_kind,status,fingerprint,badge_fingerprint,upload_status,adopted,rendered_at,uploaded_at}]` |
| Re-run an item | **`POST /api/items/{id}/reprocess`** |
| Base artwork bytes | **Missing.** `Render.asset_path` (`models.py:88`) holds the on-disk path under `assets_root`; nothing serves it |
| Badged artwork bytes | **Missing, and it is not on disk at all.** `badges/compose.py:144` `compose(base_path, art_kind, inputs, fingerprint) -> bytes` builds it in memory and it is uploaded straight to Plex |
| Distinct filter values (libraries, kinds, statuses) | **Missing** — the UI needs them to populate filters without scanning every row |

## The one real design decision

**What "badged" means in the side-by-side view.** The badged image is never
persisted. Two options were considered:

1. **Recompose on demand** via `compose()`. Faithful to what the pipeline
   *would* produce, but costs CPU per request and requires reassembling
   `BadgeInputs` from persisted facts.
2. **Proxy what Plex is currently serving.** Shows what a user actually sees in
   Plex, needs no recomposition, and reuses the existing provenance machinery
   in `src/autoposter/plex/artwork.py`.

**Decision: option 2.** The question this view answers is "is the right image
live?", not "what would we generate?" — and the render row already records the
generated state via `badge_fingerprint` and `upload_status`, so a mismatch is
visible without re-rendering. Option 1 would also make the detail page's cost
scale with badge complexity, on the event loop's thread pool, for a view that
is opened casually.

The consequence to surface in the UI: when Plex has nothing, or is unreachable,
the badged pane must say so plainly rather than showing a broken image.

---

## Task 1: Serve base artwork

**Files:**
- Create: `src/autoposter/api/artwork.py`
- Modify: `src/autoposter/api/routes.py`
- Test: `tests/test_api_artwork.py`

**Interfaces:**
- Produces: `GET /api/items/{item_id}/artwork/{art_kind}` → the base image
  bytes with the correct content type, `404` when the item, the render or the
  file is absent.

The path comes from `Render.asset_path` for that item and art kind — **never
from the URL**. `art_kind` selects a row; it must not be concatenated into a
filesystem path.

- [ ] **Step 1: Write the failing tests**

```python
async def test_serves_the_base_image_for_a_render(client, session, tmp_path):
    # a Render row whose asset_path points at a real file
    response = await client.get("/api/items/1/artwork/poster")
    assert response.status_code == 200
    assert response.headers["content-type"] in ("image/jpeg", "image/webp", "image/png")
    assert response.content == expected_bytes


async def test_unknown_art_kind_is_404_not_a_file_read(client):
    response = await client.get("/api/items/1/artwork/../../etc/passwd")
    assert response.status_code == 404


async def test_a_render_row_pointing_at_a_missing_file_is_404(client, session):
    ...


async def test_artwork_requires_a_session(client_without_token):
    assert (await client_without_token.get("/api/items/1/artwork/poster")).status_code == 401
```

Drive the traversal case through the raw-ASGI harness in
`tests/test_spa_serving.py` (`_raw_asgi_get`), not `AsyncClient` — httpx
resolves `..` before the request leaves the client, so an `AsyncClient` version
of that test passes against a vulnerable implementation. Reuse the helper
rather than duplicating it.

- [ ] **Step 2: Run them and watch them fail**

Run: `docker compose run --rm test pytest tests/test_api_artwork.py -q`

- [ ] **Step 3: Implement**

The file read goes through `asyncio.to_thread`. Content type from the file's
suffix, not from a client-supplied value.

- [ ] **Step 4: Run them and watch them pass**

- [ ] **Step 5: Prove the traversal test falsifiable**

Change the implementation to build the path from `art_kind`, show the test red,
restore, show it green. Paste both outputs.

- [ ] **Step 6: Commit**

## Task 2: Serve what Plex is currently showing

**Files:**
- Modify: `src/autoposter/api/artwork.py`
- Test: `tests/test_api_artwork.py`

**Interfaces:**
- Produces: `GET /api/items/{item_id}/artwork/{art_kind}/live` → the bytes Plex
  is serving for that item, or `404` with a JSON reason when Plex has none.

Reuse `src/autoposter/plex/artwork.py`'s existing fetch path. **Do not call
`.refresh()`.** Plex being unreachable is a `503` with a reason, not a 500 and
not a hang — the request must carry a timeout.

- [ ] **Step 1: Write the failing tests** (serves bytes; unreachable Plex gives
  503 with a reason; no artwork gives 404; requires a session)
- [ ] **Step 2: Run them and watch them fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run them and watch them pass**
- [ ] **Step 5: Commit**

## Task 3: Filter values endpoint

**Files:**
- Modify: `src/autoposter/api/routes.py`
- Test: `tests/test_api_filters.py`

**Interfaces:**
- Produces: `GET /api/items/filters` → `{libraries: [...], kinds: [...],
  statuses: [...]}`, each distinct and sorted.

Distinct values in SQL (`select(distinct(...))`), not by fetching rows and
reducing in Python — the items table has ~15,000 rows.

**Route ordering matters:** `/api/items/filters` must be registered *before*
`/api/items/{item_id}`, or FastAPI matches `filters` as an item id. Add a test
that asserts `GET /api/items/filters` returns the filter payload and not a 404
or a validation error.

- [ ] **Step 1: Write the failing tests**, including the ordering one
- [ ] **Step 2: Run them and watch them fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run them and watch them pass**
- [ ] **Step 5: Commit**

## Task 4: Library browser page

**Files:**
- Create: `frontend/src/pages/Library.tsx`, `frontend/src/pages/library.css`
- Modify: `frontend/src/App.tsx`, `frontend/src/shell/Sidebar.tsx`,
  `frontend/src/api/types.ts`
- Test: `frontend/src/pages/Library.test.tsx`

An art grid of posters from Task 1's endpoint, with filters from Task 3, and
pagination against `GET /api/items`'s `total`. A "Library" nav item joins the
sidebar — phase 4b deliberately left it out because nothing could fill it.

Images are lazy (`loading="lazy"`) and each tile degrades to a title card when
the artwork 404s, since an item with no render yet is normal rather than an
error.

- [ ] **Step 1: Write the failing test** (renders tiles from a mocked payload;
  a filter change refetches with the right query; a 404 tile shows its title)
- [ ] **Step 2: Run it and watch it fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run it and watch it pass**
- [ ] **Step 5: Commit**

## Task 5: Item detail page

**Files:**
- Create: `frontend/src/pages/ItemDetail.tsx`, `frontend/src/pages/item.css`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/pages/ItemDetail.test.tsx`

Base and live artwork side by side; the facts feeding the badges; render
history with fingerprint, badge fingerprint, upload status and timestamps; and
a re-run button posting to `POST /api/items/{id}/reprocess`.

The live pane states plainly when Plex has no artwork or is unreachable —
**never a broken image**. Fingerprint values are shown truncated with the full
value available on hover.

- [ ] **Step 1: Write the failing test** (renders both panes; the live pane's
  unreachable state renders as text; re-run posts and reflects the response)
- [ ] **Step 2: Run it and watch it fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run it and watch it pass**
- [ ] **Step 5: Commit**

## Task 6: Documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-08-20-autoposter-design.md` (section 6a)
- Modify: `README.md`

Record the endpoints added, the badged-means-live decision and its reasoning,
and what section 6 still owes: the config editor, collection member counts and
actions, the provider-candidate picker, clear-manual-override, and the
WebSocket that would replace the dashboard's polling.

**Check section 6a's existing claims against the code while you are there** —
one of its Phase 4b entries was already found describing a pinning scheme the
branch had replaced.

- [ ] **Step 1: Update the spec and README**
- [ ] **Step 2: Commit**
