# Group-Order UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator can reorder the ten collection groups from the web UI — a Groups panel on the Collections page, server-enumerated, writing `collections.group_order` through the existing config-overrides contract.

**Architecture:** T1 puts a `groups` array on the catalog read the Collections page already fetches (`GET /api/collections/catalog`), derived server-side from `collections/groups.py` so the frontend never transcribes the group keys. T2 builds the Groups panel beside the catalog picker: per-row up/down buttons (no dnd dependency), a save that writes the complete permutation via `PUT /api/config/overrides` using the shared `api/overrides.ts` helpers, and a reset that removes the key (`withoutPath`) — the overrides contract's revert. T3 is the wrap: one README line, roadmap row 200 filed and closed, the PR body artifact.

**Tech Stack:** Python 3.14 + FastAPI + pytest (compose `test` service), React 19 + vitest 4 (compose `web` service), pydantic v2 config with live swap.

**Binding adjudications:** `.superpowers/sdd/p-groupui-facts.md` C1–C7. They are settled; nothing in this plan re-litigates them, and neither does any implementer.

## Global Constraints

Every task's requirements implicitly include this section. These are law.

- **No dnd dependency — no new frontend dependency at all.** `frontend/package.json` is untouched. The affordance is per-row up/down buttons, top/bottom disabled at the edges (facts C4).
- **The ten group keys are never hardcoded in TypeScript.** No production frontend file contains `"charts"`, `"operator"` or any other group key; everything the panel renders comes from the server's `groups` array (facts C2). Test *fixtures* may carry keys — they are stub server data — but the component must work identically for a fixture of four invented groups, and the tests prove it by using four.
- **`frontend/src/pages/Settings.tsx` is untouched.** The generic editor stays the generic surface; its null→read-only fallthrough for `group_order` is shared generic behaviour, not a bug this phase fixes (facts C1).
- **`src/autoposter/collections/groups.py` stays module-scope-import-free from its own package.** `tests/test_collection_groups.py` pins this by reading the file's AST. The new `group_listing` uses only names already defined in that module.
- **Conventional commits, `--no-gpg-sign`, no attribution trailers of any kind** (no `Co-Authored-By`, no "Generated with", nothing). **Stage files by name** — never `git add -A`, `-u`, or `.`.
- **The standing container recipe for backend tests.** Every pytest run goes through the compose `test` service with the isolated database overlay, `set -o pipefail`, and output teed to a log the host can read:
  ```bash
  docker compose -p <proj> -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --name <proj>-<label> test sh -c \
    "set -o pipefail; pytest <targets> -q 2>&1 | tee /app/.superpowers/run-<label>.log"
  ```
  No `--rm` — the container is named, the log file is **read** (`.superpowers/run-<label>.log` in the main tree; never trust the exit code or `docker wait` alone — roadmap row 193's finding), then `docker rm <proj>-<label>`. Long runs (the full suite) start detached (`run -d --name …`) with `docker wait <name>` in the foreground, then the log is read. Teardown per task: `docker compose -p <proj> down` — **never** `down -v`.
- **Frontend tests via the frontend's own runner.** `npm test` is `vitest run` (`frontend/package.json:14`), executed in the compose `web` service with output redirected to a host-side log:
  ```bash
  docker compose -p <proj> run --name <proj>-<label> web npm test > .superpowers/run-<label>.log 2>&1
  ```
  Read the log, then `docker rm <proj>-<label>`. Type-checking is `npx tsc --noEmit` through the same service.
- **Artifacts are `p-groupui-` prefixed** and live in `.superpowers/sdd/` (gitignored — `.gitignore:23` covers `.superpowers/`). The PR body at `.superpowers/sdd/p-groupui-pr-body.md` is **never committed**.
- **Branch `feat/group-order-ui`, cut from `main`.** Worktrees optional — no parallel phase is running, main-tree execution is fine (facts C7).
- **This plan file is committed by Task 1, Step 1.** It is an untracked working-tree file until then.

---

## File Structure

**Created:**

- `frontend/src/pages/GroupsPanel.tsx` — the Groups panel: fetch, reorder state, save/reset, the C5 honesty notes.
- `frontend/src/pages/GroupsPanel.test.tsx` — its vitest suite.
- `frontend/src/pages/groups.css` — the panel's styles (its own file, the `catalog.css` precedent: rules a later panel would copy should be findable by name).
- `.superpowers/sdd/p-groupui-pr-body.md` — the PR body (Task 3; gitignored, uncommitted).

**Modified:**

- `src/autoposter/collections/groups.py` — one new pure function, `group_listing(config)`.
- `src/autoposter/api/collections_builders.py:134-157` — the catalog handler returns `groups` beside `categories`.
- `tests/test_collection_groups.py` — two pure tests for `group_listing`.
- `tests/test_collection_catalog.py` — two endpoint tests for the `groups` array.
- `frontend/src/api/types.ts` — `CatalogGroup`, and `groups` on `CollectionsCatalogResponse`.
- `frontend/src/pages/Collections.tsx` — mounts `<GroupsPanel />`.
- `frontend/src/pages/Collections.test.tsx` — the page fixture gains a `groups` array; one mount assertion.
- `deploy/README.md:388-403` — the `group_order` entry gains the one line pointing at the panel.
- `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` — row 200, filed and closed.

**Explicitly NOT touched:** `frontend/src/pages/Settings.tsx`, `frontend/package.json`, `frontend/src/pages/CatalogPanel.tsx`, `frontend/src/pages/CatalogPanel.test.tsx` (its fixtures lack `groups`, and that is fine — the picker never reads the field and the `json()` stubs are untyped), anything in `src/autoposter/config/`, any reconciler.

---

### Task 1: The `groups` array on the catalog read

**Files:**
- Modify: `src/autoposter/collections/groups.py` (append after `separator_titles`, line 496)
- Modify: `src/autoposter/api/collections_builders.py:134-157`
- Test: `tests/test_collection_groups.py` (append), `tests/test_collection_catalog.py` (append after `test_the_catalog_endpoint_reports_which_presets_are_active`, line 2211)

**Interfaces:**
- Consumes: `effective_order(config)`, `section_number(group, order)`, `separator_title(group)` — all already in `groups.py`.
- Produces: `group_listing(config) -> list[dict]` — every group as `{"key": str, "title": str, "section": str, "position": int}`, **served in the running config's effective order**, so `position` equals the array index. The endpoint response becomes `{"categories": [...], "groups": [...]}`. Task 2's TypeScript types mirror this shape exactly.

- [ ] **Step 1: Create the branch and commit this plan**

```bash
git checkout -b feat/group-order-ui main
git add docs/superpowers/plans/2026-08-30-group-order-ui.md
git commit --no-gpg-sign -m "docs(plans): the group-order UI phase plan"
```

- [ ] **Step 2: Write the failing pure tests**

Append to `tests/test_collection_groups.py` (the module's existing `config(**kwargs)` helper and `groups` import are already at the top of the file):

```python
def test_group_listing_serves_every_group_in_effective_order():
    """The catalog endpoint's `groups` array, one layer down: the whole
    enumeration, effective order, position == index — so the frontend never
    holds a copy of the keys (the row-156 instinct, applied to TypeScript)."""
    listing = groups.group_listing(config())

    assert [entry["key"] for entry in listing] == list(groups.CANONICAL_ORDER)
    assert listing[0] == {
        "key": "charts", "title": "Chart Collections",
        "section": "010", "position": 0,
    }
    assert listing[-1] == {
        "key": "operator", "title": "Collections",
        "section": "100", "position": 9,
    }


def test_group_listing_reorders_and_renumbers_under_group_order():
    """`position` and `section` are both EFFECTIVE, not canonical: a partial
    `group_order` moves the named group to the front and renumbers everything,
    exactly as `effective_order`/`section_number` decide for the pass itself."""
    listing = groups.group_listing(config(group_order=["operator"]))

    assert listing[0] == {
        "key": "operator", "title": "Collections",
        "section": "010", "position": 0,
    }
    assert [entry["key"] for entry in listing][1:4] == [
        "charts", "awards", "content_ratings",
    ]
    assert listing[1]["section"] == "020"
    assert [entry["position"] for entry in listing] == list(range(10))
```

- [ ] **Step 3: Write the failing endpoint tests**

Append to `tests/test_collection_catalog.py`, directly after `test_the_catalog_endpoint_reports_which_presets_are_active` (line 2211). The `client`, `app` and `auth_headers` fixtures already exist in the file:

```python
async def test_the_catalog_endpoint_serves_the_groups_in_effective_order(
    client, auth_headers
):
    """The Groups panel's enumeration source (group-order UI phase, C2): the
    server serves the groups so the frontend never transcribes the ten keys.
    The example config leaves `group_order` unset, so this is canonical."""
    body = (await client.get("/api/collections/catalog", headers=auth_headers)).json()

    assert [entry["key"] for entry in body["groups"]] == [
        "charts", "awards", "content_ratings", "content", "location",
        "media", "people", "production", "time", "operator",
    ]
    assert body["groups"][0] == {
        "key": "charts", "title": "Chart Collections",
        "section": "010", "position": 0,
    }
    assert body["groups"][-1] == {
        "key": "operator", "title": "Collections",
        "section": "100", "position": 9,
    }


async def test_the_catalog_endpoint_reports_the_live_group_order(
    client, app, auth_headers
):
    """The same live-config read the active-presets test above proves: a
    swapped `group_order` reorders and renumbers the served array without a
    restart, which is what makes the panel's re-read after save truthful."""
    config = app.state.config_holder.current
    app.state.config_holder.swap(config.model_copy(
        update={
            "collections": config.collections.model_copy(
                update={"group_order": ["operator"]}
            )
        }
    ))

    body = (await client.get("/api/collections/catalog", headers=auth_headers)).json()

    assert body["groups"][0]["key"] == "operator"
    assert body["groups"][0]["section"] == "010"
    assert body["groups"][1]["key"] == "charts"
    assert [entry["position"] for entry in body["groups"]] == list(range(10))
```

- [ ] **Step 4: Run the tests to verify they fail**

```bash
docker compose -p pguit1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pguit1-red1 test sh -c \
  "set -o pipefail; pytest tests/test_collection_groups.py tests/test_collection_catalog.py -q 2>&1 | tee /app/.superpowers/run-pgui-t1-red1.log"
```

Read `D:\Sites\autoposter\.superpowers\run-pgui-t1-red1.log`. Expected: **4 failed** (the two pure tests with `AttributeError: module 'autoposter.collections.groups' has no attribute 'group_listing'`, the two endpoint tests with `KeyError: 'groups'`), everything else in the two files passed. Then `docker rm pguit1-red1`.

- [ ] **Step 5: Implement `group_listing`**

Append to `src/autoposter/collections/groups.py`, after `separator_titles` (line 496). It uses only this module's own names — the AST import-free pin holds:

```python
def group_listing(config) -> list[dict]:
    """Every group as the catalog endpoint serves it, in effective order.

    ``{key, title, section, position}`` per group: the config-legal name, the
    divider's display title, the ``!NNN`` section number, and the group's index
    under the RUNNING config's ``group_order`` — so ``position`` equals the
    array index, and a UI that renders the array in order is rendering the tab.

    This is the group-order panel's enumeration source, and the reason it
    exists is the reason ``CANONICAL_ORDER`` is written out above: the keys
    live in exactly one place. A frontend holding its own copy of the ten
    names would be the drift this module's other tables refuse.
    """
    order = effective_order(config)
    return [
        {
            "key": group,
            "title": separator_title(group),
            "section": section_number(group, order),
            "position": order.index(group),
        }
        for group in order
    ]
```

- [ ] **Step 6: Serve it from the catalog handler**

In `src/autoposter/api/collections_builders.py`, add the import (after the `catalog_listing` import at line 49 — `groups` has no module-scope package imports, so this closes no cycle):

```python
from autoposter.collections.groups import group_listing
```

Replace the return of `collections_catalog` (lines 155-157):

```python
    config = request.app.state.config_holder.current
    return {
        "categories": catalog_listing(config),
        # The group-order panel's enumeration (group-order UI phase, C2):
        # served rather than transcribed, so the ten keys exist in exactly
        # one language. Effective order, not canonical -- the panel shows
        # the tab as the running config orders it.
        "groups": group_listing(config),
    }
```

And extend the handler docstring's first paragraph (line 135) with one sentence at its end:

```
    Also carries ``groups``: every collection group as ``{key, title, section,
    position}`` in the running config's effective order, which is the Groups
    panel's enumeration source — served so the frontend never holds a copy of
    the group keys.
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
docker compose -p pguit1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pguit1-green1 test sh -c \
  "set -o pipefail; pytest tests/test_collection_groups.py tests/test_collection_catalog.py tests/test_collection_separator.py -q 2>&1 | tee /app/.superpowers/run-pgui-t1-green1.log"
```

Read `D:\Sites\autoposter\.superpowers\run-pgui-t1-green1.log`. Expected: **all passed, 0 failed** (the separator file rides along as the nearest consumer of `groups.py`). Then `docker rm pguit1-green1`.

- [ ] **Step 8: Ruff**

```bash
docker compose -p pguit1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pguit1-ruff test sh -c \
  "set -o pipefail; ruff check src/autoposter/collections/groups.py src/autoposter/api/collections_builders.py tests/test_collection_groups.py tests/test_collection_catalog.py 2>&1 | tee /app/.superpowers/run-pgui-t1-ruff.log"
```

Read the log. Expected: `All checks passed!`. Then `docker rm pguit1-ruff`.

- [ ] **Step 9: Commit and tear down**

```bash
git add src/autoposter/collections/groups.py src/autoposter/api/collections_builders.py tests/test_collection_groups.py tests/test_collection_catalog.py
git commit --no-gpg-sign -m "feat(api): the catalog read serves the collection groups in effective order"
docker compose -p pguit1 down
```

(`down` without `-v`, always.)

---

### Task 2: The Groups panel

**Files:**
- Modify: `frontend/src/api/types.ts` (after `CollectionsCatalogResponse`, line 459-461)
- Create: `frontend/src/pages/GroupsPanel.tsx`, `frontend/src/pages/GroupsPanel.test.tsx`, `frontend/src/pages/groups.css`
- Modify: `frontend/src/pages/Collections.tsx` (import + mount), `frontend/src/pages/Collections.test.tsx` (fixture + one assertion)

**Interfaces:**
- Consumes: Task 1's `groups` array on `GET /api/collections/catalog` (`{key, title, section, position}`, effective order); `GET /api/config` / `PUT /api/config/overrides` and the helpers `documentFromConfig`, `withPath`, `withoutPath`, `hasPath`, `fieldErrors` from `frontend/src/api/overrides.ts`; `ApiError`, `apiFetch` from `frontend/src/api/client.ts`.
- Produces: `CatalogGroup` (types.ts), `GroupsPanel` (named export), mounted on the Collections page. Nothing later consumes these beyond the page itself.

- [ ] **Step 1: Add the types**

In `frontend/src/api/types.ts`, insert before `CollectionsCatalogResponse` (line 457) and extend it:

```typescript
/** One collection group as the catalog endpoint enumerates it: the
 * config-legal key, the divider's display title, the `!NNN` section number
 * the RUNNING config gives it, and its index under that config's
 * `collections.group_order`. Served in effective order — `position` equals
 * the array index — so the Groups panel renders the array and never holds
 * its own copy of the keys. */
export interface CatalogGroup {
  key: string;
  title: string;
  section: string;
  position: number;
}
```

and change the response interface to:

```typescript
/** GET /api/collections/catalog. Touches neither Plex nor the database -- it
 * is a pure table plus which keys the live config has switched on, and the
 * group enumeration in the running config's effective order. */
export interface CollectionsCatalogResponse {
  categories: CatalogCategory[];
  groups: CatalogGroup[];
}
```

- [ ] **Step 2: Write the failing test suite**

Create `frontend/src/pages/GroupsPanel.test.tsx`:

```tsx
/** The Groups panel: the third writer of the overrides document, and the one
 * whose whole value is WHAT it writes.
 *
 * Two things are asserted hardest. The PUT body: a save is always the COMPLETE
 * permutation of every key the server enumerated (partial-list semantics exist
 * server-side, but an explicit full list is what an operator should find
 * persisted), and Reset is the key going AWAY — `withoutPath`, the overrides
 * contract's revert — never a write of any list. And the enumeration source:
 * the fixture serves FOUR groups, not ten, and every assertion still holds —
 * which is the proof that no group key lives in the component.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { GroupsPanel } from "./GroupsPanel";

/** Four groups, deliberately not ten: the panel must render whatever the
 * server enumerates. Internally consistent as a four-group answer — sections
 * are position × 10, positions are the indexes. */
const GROUPS = [
  { key: "charts", title: "Chart Collections", section: "010", position: 0 },
  { key: "awards", title: "Award Collections", section: "020", position: 1 },
  { key: "content_ratings", title: "Ratings Collections", section: "030", position: 2 },
  { key: "operator", title: "Collections", section: "040", position: 3 },
];

function catalog(groups: unknown[] = GROUPS) {
  // The panel reads only `groups`; the categories belong to the picker.
  return { categories: [], groups };
}

/** The config GET, whose only job here is seeding the document the panel
 * edits. The unrelated `plex.url` override is the point of the fixture: a
 * save about group order must not drop it. */
function config(overrides: Record<string, unknown> = {}) {
  return {
    version: "cfg-1",
    plex: { url: "http://plex:32400" },
    collections: { enabled: true },
    overridden_paths: ["plex.url"],
    frozen_paths: {},
    redacted_paths: [],
    keep_sentinel: "***KEEP***",
    ...overrides,
  };
}

/** A config that already stores a group_order override, so Reset has
 * something to remove. The served groups order matches it, as the server's
 * effective order would. */
function overriddenConfig() {
  return config({
    collections: {
      enabled: true,
      group_order: ["operator", "charts", "awards", "content_ratings"],
    },
    overridden_paths: ["plex.url", "collections.group_order"],
  });
}

const OVERRIDDEN_GROUPS = [
  { key: "operator", title: "Collections", section: "010", position: 0 },
  { key: "charts", title: "Chart Collections", section: "020", position: 1 },
  { key: "awards", title: "Award Collections", section: "030", position: 2 },
  { key: "content_ratings", title: "Ratings Collections", section: "040", position: 3 },
];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface StubOptions {
  catalog?: unknown;
  config?: unknown;
  save?: (init?: RequestInit) => Response;
}

function stubFetch(options: StubOptions = {}) {
  const puts: RequestInit[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/collections/catalog") return json(options.catalog ?? catalog());
    if (path === "/api/config") return json(options.config ?? config());
    if (path === "/api/config/overrides") {
      puts.push(init ?? {});
      if (options.save) return options.save(init);
      return json({
        version_before: "cfg-1",
        version_after: "cfg-2",
        restart_required: [],
      });
    }
    throw new Error(`unexpected fetch: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, puts };
}

/** The document the panel sent, parsed. */
function sentDocument(puts: RequestInit[], index = 0): Record<string, any> {
  const body = puts[index]?.body;
  expect(typeof body).toBe("string");
  return JSON.parse(body as string).document;
}

async function renderPanel(options: StubOptions = {}) {
  const stub = stubFetch(options);
  render(<GroupsPanel />);
  await screen.findByRole("list", { name: "Collection group order" });
  return stub;
}

function rowTitles() {
  const list = screen.getByRole("list", { name: "Collection group order" });
  return within(list)
    .getAllByRole("listitem")
    .map((row) => within(row).getByText(/./, { selector: ".groups-title" }).textContent);
}

async function save() {
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
}

beforeEach(() => {
  setToken(null);
});

describe("the groups panel", () => {
  it("renders the server's groups in the served order, with keys and live section numbers", async () => {
    await renderPanel();

    expect(rowTitles()).toEqual([
      "Chart Collections",
      "Award Collections",
      "Ratings Collections",
      "Collections",
    ]);
    const first = screen.getAllByRole("listitem")[0];
    // The key an operator would write in YAML, and the section the RUNNING
    // config gives the group — both straight off the response.
    expect(within(first).getByText("charts")).toBeInTheDocument();
    expect(within(first).getByText("!010")).toBeInTheDocument();
  });

  it("disables Up on the first row and Down on the last", async () => {
    await renderPanel();

    expect(screen.getByRole("button", { name: "Move Chart Collections up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Collections down" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Award Collections up" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Move Award Collections down" })).toBeEnabled();
  });

  it("moves a row and marks the order unsaved, keeping the served section beside it", async () => {
    await renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));

    expect(rowTitles()).toEqual([
      "Award Collections",
      "Chart Collections",
      "Ratings Collections",
      "Collections",
    ]);
    // The badge is the RUNNING config's number — what the sort titles in Plex
    // carry right now. Renumbering happens on save, and the churn note says
    // so; recomputing it here would be a second copy of the server's
    // arithmetic.
    const first = screen.getAllByRole("listitem")[0];
    expect(within(first).getByText("!020")).toBeInTheDocument();
    expect(screen.getByText(/unsaved/i)).toBeInTheDocument();
  });

  it("saves the complete permutation, keeping unrelated overrides", async () => {
    const { puts } = await renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    await save();

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    // The WHOLE list, not the moved key: what the operator sees is exactly
    // what persists.
    expect(document.collections.group_order).toEqual([
      "awards", "charts", "content_ratings", "operator",
    ]);
    // The unrelated override the config reported is still in the document.
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("disables Save when the order matches the server, including after a move undone", async () => {
    const { puts } = await renderPanel();

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections down" }));

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(puts).toHaveLength(0);
  });

  it("resets by removing the key, never by writing an order", async () => {
    const { puts } = await renderPanel({
      catalog: catalog(OVERRIDDEN_GROUPS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Reset to config file" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    // The revert IS the absence: `null` is a value the server refuses, and a
    // written canonical list would be an override pretending to be none.
    expect(document.collections).toBeUndefined();
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("disables Reset when no override is stored", async () => {
    await renderPanel();

    expect(screen.getByRole("button", { name: "Reset to config file" })).toBeDisabled();
  });

  it("hands focus to the row's other button when a move lands it at an edge", async () => {
    await renderPanel();

    // Award moves to the top; its Up button becomes disabled, and a disabled
    // element drops keyboard focus on the floor — so the panel hands focus to
    // the same row's Down button.
    const up = screen.getByRole("button", { name: "Move Award Collections up" });
    up.focus();
    fireEvent.click(up);

    expect(screen.getByRole("button", { name: "Move Award Collections down" })).toHaveFocus();
  });

  it("states the live-swap and churn sentences", async () => {
    await renderPanel();

    // C5: the change is live with no restart and lands at the next pass…
    expect(screen.getByText(/no restart/i)).toBeInTheDocument();
    // …and the first pass that sees it rewrites the moved groups' sort
    // titles, one write each, once.
    expect(screen.getByText(/one write per collection, once/i)).toBeInTheDocument();
  });

  it("renders a 422 against the path it names", async () => {
    await renderPanel({
      save: () =>
        json(
          {
            detail: [
              {
                path: "collections.group_order",
                message: "unknown collection group 'chartz'",
              },
            ],
          },
          422,
        ),
    });

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    await save();

    expect(await screen.findByText("unknown collection group 'chartz'")).toBeInTheDocument();
    expect(screen.getByText("collections.group_order")).toBeInTheDocument();
  });

  it("re-reads the catalog and config after a save, and reports the versions", async () => {
    const { fetchMock, puts } = await renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    await save();
    await waitFor(() => expect(puts).toHaveLength(1));

    // Which order is stored is the server's answer, not this component's.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([path]) => path === "/api/collections/catalog"),
      ).toHaveLength(2),
    );
    const saved = await screen.findByRole("status");
    expect(saved).toHaveTextContent("cfg-1");
    expect(saved).toHaveTextContent("cfg-2");
  });
});
```

- [ ] **Step 3: Run the suite to verify it fails**

```bash
docker compose -p pguit2 run --name pguit2-red1 web npm test > .superpowers/run-pgui-t2-red1.log 2>&1
```

Read `D:\Sites\autoposter\.superpowers\run-pgui-t2-red1.log`. Expected: the `GroupsPanel.test.tsx` file **errors** (`Failed to resolve import "./GroupsPanel"`); every other test file passes. Then `docker rm pguit2-red1`.

- [ ] **Step 4: Write the styles**

Create `frontend/src/pages/groups.css`:

```css
/* The Groups panel. Its own stylesheet, catalog.css's precedent: the rules a
 * later reorder surface would copy should be findable in a file named after
 * the thing. */

.groups-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.groups-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.groups-note {
  margin: 0 0 8px;
  font-size: 13px;
  white-space: normal;
}

.groups-unsaved {
  display: inline-block;
  margin-bottom: 8px;
  padding: 2px 8px;
  border: 1px solid var(--warn);
  border-radius: 999px;
  font-size: 12px;
  color: var(--warn);
  white-space: nowrap;
}

.groups-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.groups-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-top: 1px solid var(--border);
}

.groups-row:first-child {
  border-top: none;
}

.groups-position {
  flex: 0 0 1.6rem;
  text-align: right;
}

.groups-title {
  font-weight: 600;
  flex: 0 1 auto;
}

.groups-key,
.groups-section {
  font-size: 12px;
}

/* The move buttons at the row's end, past a gap that soaks up the width — so
 * the two targets sit in a stable column the pointer does not have to chase
 * as titles change length. */
.groups-moves {
  display: flex;
  gap: 6px;
  margin-left: auto;
}

/* Arrow glyphs alone are small targets; give them button-sized boxes. The
 * accessible name is the aria-label, not the glyph. */
.groups-moves button {
  min-width: 2.2rem;
  min-height: 2rem;
}

.groups-errors {
  margin: 8px 0 0;
  padding-left: 1.1rem;
  font-size: 13px;
}

.groups-error-message {
  color: var(--error);
}

.groups-saved {
  margin-top: 12px;
  padding: 10px 14px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.groups-saved p {
  margin: 0 0 4px;
  white-space: normal;
}

.groups-saved p:last-child {
  margin-bottom: 0;
}

/* "Saved, but the re-read failed" — a warning inside the success panel, not a
 * page error beside it, because the save itself did succeed. */
.groups-stale {
  color: var(--warn);
  font-size: 13px;
}
```

- [ ] **Step 5: Write the panel**

Create `frontend/src/pages/GroupsPanel.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
// The overrides document and its helpers are shared with the settings page
// and the catalog picker -- see `api/overrides.ts` for why a copy of the
// seeding rule would be a bug rather than a duplication.
import {
  documentFromConfig,
  fieldErrors,
  hasPath,
  withPath,
  withoutPath,
} from "../api/overrides";
import type {
  CatalogGroup,
  CollectionsCatalogResponse,
  ConfigResponse,
  ConfigSaveResponse,
  OverridesDocument,
} from "../api/types";
import "./groups.css";

/** Where the panel's one setting is written. A save is always the COMPLETE
 * permutation of every key the server enumerated: partial-list semantics
 * exist server-side, but an explicit full list is what an operator should
 * find persisted in their overrides. Reset is the key going away entirely --
 * the overrides contract's revert -- never a write of any list. */
const GROUP_ORDER_PATH = "collections.group_order";

/** When a saved order is seen. The collections section is live (config live
 * swap; only `collections.enabled` is frozen), so the save lands in the
 * running process at once -- and the tab itself changes when the reconcile
 * pass next runs, which Diff now on this page makes immediate. */
const LIVE_NOTE =
  "A saved order is live in the running process at once — no restart. The " +
  "collections tab itself changes when the reconcile pass next runs; Diff " +
  "now above runs one immediately.";

/** What that pass does. Section numbers derive from position, so reordering
 * renumbers -- a one-off sort-title write per collection in the groups that
 * moved, the row-49 disclosure made operator-facing. */
const CHURN_NOTE =
  "Section numbers derive from position, so the pass that first sees a new " +
  "order re-writes the sort title of every collection in the groups that " +
  "moved — one write per collection, once — and then settles.";

/** What Reset actually reverts to. Removing the key hands the decision back
 * to the mounted config file, which is not always the built-in order -- the
 * file may name its own -- so the copy says which, rather than promising
 * "the default". */
const RESET_NOTE =
  "Reset removes the override: the order goes back to whatever the mounted " +
  "config file says, which is the built-in order when the file says nothing.";

/** The group blocks in Plex's collections tab, in the order the running
 * config shows them, and the buttons that reorder them.
 *
 * Everything rendered here comes off the server's `groups` array -- keys,
 * titles, section numbers. The component holds no group name of its own, so
 * a group the server grows (or renames) appears here without this file being
 * touched: the same rule the catalog picker's tab strip follows for
 * categories, applied to the config-legal keys themselves.
 */
export function GroupsPanel() {
  const [groups, setGroups] = useState<CatalogGroup[] | null>(null);
  // The pending order, as keys. Seeded from the response (which arrives in
  // effective order) and re-seeded after every save: what is stored is the
  // server's answer, not this component's memory of what was clicked.
  const [order, setOrder] = useState<string[]>([]);
  // The overrides the server already holds. Kept whole rather than reduced
  // to the one path: a save here must not drop an override another page
  // stored.
  const [stored, setStored] = useState<OverridesDocument>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Separate from `saveError` on purpose: a re-read that fails after the
  // store succeeded did not unsave anything, and reporting it as a save
  // failure would put a red error beside "Saved."
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ConfigSaveResponse | null>(null);
  const [saving, setSaving] = useState(false);

  // A ref rather than an effect-local `cancelled`: the save handler's
  // continuation lands outside any effect, the same reasoning the
  // neighbouring panels use.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  // A row moved to an edge disables the button that took it there, and a
  // disabled element drops keyboard focus on the floor -- so the click
  // handler queues a hand-off to the row's other button, applied after the
  // re-render. The explicit-focus posture is CatalogPanel's roving-tabindex
  // precedent, applied to the one place this panel needs it.
  const buttonRefs = useRef<
    Record<string, { up: HTMLButtonElement | null; down: HTMLButtonElement | null }>
  >({});
  const pendingFocus = useRef<{ key: string; which: "up" | "down" } | null>(null);
  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    pendingFocus.current = null;
    buttonRefs.current[target.key]?.[target.which]?.focus();
  });

  const adopt = useCallback(
    (catalog: CollectionsCatalogResponse, config: ConfigResponse) => {
      setGroups(catalog.groups);
      setOrder(catalog.groups.map((group) => group.key));
      setStored(documentFromConfig(config));
    },
    [],
  );

  const reload = useCallback(async () => {
    const [catalog, config] = await Promise.all([
      apiFetch<CollectionsCatalogResponse>("/api/collections/catalog"),
      apiFetch<ConfigResponse>("/api/config"),
    ]);
    if (live.current) adopt(catalog, config);
  }, [adopt]);

  useEffect(() => {
    reload().catch((caught: Error) => {
      if (live.current) setLoadError(caught.message);
    });
  }, [reload]);

  if (loadError !== null) {
    return (
      <section className="panel groups-panel">
        <h2>Groups</h2>
        <p className="page-error">{loadError}</p>
      </section>
    );
  }

  if (groups === null) {
    return (
      <section className="panel groups-panel">
        <h2>Groups</h2>
        <p className="muted">Loading…</p>
      </section>
    );
  }

  const byKey = new Map(groups.map((group) => [group.key, group]));
  const dirty = order.some((key, index) => key !== groups[index]?.key);
  const overridden = hasPath(stored, GROUP_ORDER_PATH);

  function move(key: string, delta: -1 | 1) {
    // A stale save panel beside a changed order would read as though that
    // save had accounted for the change; a 422 pinned to an order nobody is
    // proposing any more is worse.
    setResult(null);
    setReloadError(null);
    setErrors({});
    setSaveError(null);
    const index = order.indexOf(key);
    const target = index + delta;
    if (index === -1 || target < 0 || target >= order.length) return;
    if (target === 0) pendingFocus.current = { key, which: "down" };
    else if (target === order.length - 1) pendingFocus.current = { key, which: "up" };
    else pendingFocus.current = null;
    const next = [...order];
    [next[index], next[target]] = [next[target], next[index]];
    setOrder(next);
  }

  /** One PUT for both buttons: Save sends the full permutation at the path,
   * Reset sends the document without it. Everything else -- the 422 handling,
   * the re-read, the stale note -- is identical, so it lives once. */
  async function put(document: OverridesDocument) {
    setSaving(true);
    setErrors({});
    setSaveError(null);
    setReloadError(null);
    setResult(null);
    let saved = false;
    try {
      const response = await apiFetch<ConfigSaveResponse>("/api/config/overrides", {
        method: "PUT",
        body: JSON.stringify({ document }),
      });
      if (live.current) setResult(response);
      saved = true;
    } catch (caught) {
      if (live.current) {
        if (caught instanceof ApiError && caught.status === 422) {
          setErrors(fieldErrors(caught.detail));
          setSaveError("The server rejected this order.");
        } else {
          setSaveError((caught as Error).message);
        }
      }
    }
    // Re-read rather than assume: the stored order and the section numbers
    // are the config's answer to what was just written. Outside the save's
    // own try on purpose -- the store already succeeded, so a failure here
    // means the panel is stale, not that nothing was saved.
    if (saved) {
      try {
        await reload();
      } catch (caught) {
        if (live.current) {
          setReloadError(
            `Saved, but the panel could not be re-read (${(caught as Error).message}). ` +
              "What is shown may be stale — reload the page.",
          );
        }
      }
    }
    if (live.current) setSaving(false);
  }

  return (
    <section className="panel groups-panel">
      <div className="groups-header">
        <h2>Groups</h2>
        <div className="groups-actions">
          <button
            type="button"
            disabled={!overridden || saving}
            title={
              overridden
                ? undefined
                : "No override is stored; the mounted config file's order is already in effect."
            }
            onClick={() => void put(withoutPath(stored, GROUP_ORDER_PATH))}
          >
            Reset to config file
          </button>
          <button
            type="button"
            disabled={!dirty || saving}
            onClick={() => void put(withPath(stored, GROUP_ORDER_PATH, order))}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <p className="muted groups-note">
        The order of the collection blocks in Plex's collections tab. Move a
        group and Save — the whole list is stored, so what you see here is
        exactly what persists as <span className="mono">{GROUP_ORDER_PATH}</span>.
      </p>
      <p className="muted groups-note">{LIVE_NOTE}</p>
      <p className="muted groups-note">{CHURN_NOTE}</p>
      <p className="muted groups-note">{RESET_NOTE}</p>

      {dirty && <span className="groups-unsaved">unsaved — Save to store</span>}

      <ol className="groups-rows" aria-label="Collection group order">
        {order.map((key, index) => {
          const group = byKey.get(key);
          if (group === undefined) return null;
          return (
            <li key={key} className="groups-row">
              <span className="groups-position muted">{index + 1}.</span>
              <span className="groups-title">{group.title}</span>
              <span className="mono muted groups-key">{key}</span>
              {/* The section number the RUNNING config gives this group --
                  what its sort titles carry in Plex right now. Deliberately
                  not recomputed for a pending move: renumbering happens on
                  save (the churn note says so), and a client-side copy of
                  the position-times-ten arithmetic would be a second source
                  of truth waiting to drift. */}
              <span className="mono muted groups-section">{`!${group.section}`}</span>
              <span className="groups-moves">
                <button
                  type="button"
                  aria-label={`Move ${group.title} up`}
                  disabled={saving || index === 0}
                  ref={(element) => {
                    (buttonRefs.current[key] ??= { up: null, down: null }).up = element;
                  }}
                  onClick={() => move(key, -1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  aria-label={`Move ${group.title} down`}
                  disabled={saving || index === order.length - 1}
                  ref={(element) => {
                    (buttonRefs.current[key] ??= { up: null, down: null }).down = element;
                  }}
                  onClick={() => move(key, 1)}
                >
                  ↓
                </button>
              </span>
            </li>
          );
        })}
      </ol>

      {saveError !== null && <p className="page-error">{saveError}</p>}
      {Object.entries(errors).length > 0 && (
        <ul className="groups-errors">
          {Object.entries(errors).map(([path, message]) => (
            <li key={path}>
              <span className="mono">{path}</span>{" "}
              <span className="groups-error-message">{message}</span>
            </li>
          ))}
        </ul>
      )}

      {result !== null && (
        <div className="groups-saved" role="status">
          <p>{`Saved. Config ${result.version_before} → ${result.version_after}.`}</p>
          {reloadError !== null && <p className="groups-stale">{reloadError}</p>}
          {result.restart_required.length > 0 && (
            <p className="muted">
              {`Needs a restart: ${result.restart_required.join(", ")}.`}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 6: Run the suite to verify the new file passes**

```bash
docker compose -p pguit2 run --name pguit2-green1 web npm test > .superpowers/run-pgui-t2-green1.log 2>&1
```

Read `D:\Sites\autoposter\.superpowers\run-pgui-t2-green1.log`. Expected: every test file passes, 0 failures — including all eleven `GroupsPanel.test.tsx` tests. Then `docker rm pguit2-green1`.

- [ ] **Step 7: Mount the panel on the Collections page, and pin the mount**

In `frontend/src/pages/Collections.tsx`: add the import beside the catalog panel's (line 16):

```tsx
import { GroupsPanel } from "./GroupsPanel";
```

and mount it after `<CatalogPanel />` (line 591), replacing the closing of the fragment:

```tsx
      <CatalogPanel />

      {/* The group order below the catalog that fills the groups: an operator
          picks what to build, then arranges the blocks it lands in. Like the
          catalog it touches neither Plex nor the database, so it renders on a
          replica where the top panels report 503. */}
      <GroupsPanel />
    </>
```

In `frontend/src/pages/Collections.test.tsx`: the page now mounts a second consumer of the catalog fixture, so `CATALOG` (line 94) gains a `groups` array — append the property after `categories`' closing bracket (line 117):

```tsx
  groups: [
    { key: "awards", title: "Award Collections", section: "010", position: 0 },
    { key: "charts", title: "Chart Collections", section: "020", position: 1 },
  ],
```

and add one page-level assertion at the end of the `describe("Collections", …)` block (the same posture the file's comment at line 90 takes for the catalog panel — the panel's own behaviour is covered in `GroupsPanel.test.tsx`; what the page owes it is a well-formed answer and a mount):

```tsx
  it("mounts the groups panel below the catalog", async () => {
    stubFetch();

    render(<Collections />);

    expect(
      await screen.findByRole("list", { name: "Collection group order" }),
    ).toBeInTheDocument();
  });
```

- [ ] **Step 8: Run the full frontend suite and the type check**

```bash
docker compose -p pguit2 run --name pguit2-green2 web npm test > .superpowers/run-pgui-t2-green2.log 2>&1
docker compose -p pguit2 run --name pguit2-tsc web npx tsc --noEmit > .superpowers/run-pgui-t2-tsc.log 2>&1
```

Read both logs. Expected: all vitest files pass, 0 failures; `tsc` produces no output (empty log means clean). Then `docker rm pguit2-green2 pguit2-tsc`.

- [ ] **Step 9: Commit and tear down**

```bash
git add frontend/src/api/types.ts frontend/src/pages/GroupsPanel.tsx frontend/src/pages/GroupsPanel.test.tsx frontend/src/pages/groups.css frontend/src/pages/Collections.tsx frontend/src/pages/Collections.test.tsx
git commit --no-gpg-sign -m "feat(frontend): the Groups panel reorders collection groups from the Collections page"
docker compose -p pguit2 down
```

---

### Task 3: Wrap — README line, row 200, PR body, full suites

**Files:**
- Modify: `deploy/README.md:399-403` (the `group_order` entry), `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:295` (append row 200 after row 199)
- Create: `.superpowers/sdd/p-groupui-pr-body.md` (gitignored; NEVER committed)

**Interfaces:**
- Consumes: everything T1/T2 shipped (named in the row and the PR body).
- Produces: the phase's paperwork. Nothing consumes it downstream.

- [ ] **Step 1: The README line**

In `deploy/README.md`, the `group_order` entry's naming paragraph ends at line 403:

```
  like. Neither is accepted as a reordering that silently did nothing.
```

Append a new paragraph directly after it (before the `**The first pass after this feature ships…**` paragraph), same two-space indent as the entry's other paragraphs:

```markdown

  The **Groups** panel on the Collections page is this setting's UI: per-group
  up/down moves, saved as the complete ten-key list through the settings
  overrides, and its Reset removes the override so the file above (or the
  built-in order) applies again.
```

- [ ] **Step 2: Roadmap row 200**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, append after row 199 (line 295), following the existing row format (`| # | title | prose | size | operator-visible | deps |`):

```markdown
| 200 | Reorder the collection groups from the web UI (CLOSED — the group-order UI phase) | Filed and closed by the same phase, on the user's directive that reordering the collection groupings through the UI is a must-have — which is why it jumped ahead of row 197's phase. Row 49 shipped `collections.group_order` as YAML only; the Settings page rendered it read-only (a null default falls through the generic editor's widget chooser), and nothing in the frontend knew the groups existed. **What shipped.** `GET /api/collections/catalog` gains a `groups` array — `{key, title, section, position}` per group, derived from `collections/groups.py`'s one pure new function (`group_listing`) and served in the RUNNING config's effective order, so the ten keys are never transcribed into TypeScript (the row-156 instinct, applied to the frontend; a four-group test fixture proves the panel assumes nothing about the count either). The **Groups** panel on the Collections page renders that array with per-row up/down buttons — no dnd dependency, edges disabled, focus handed off when a move disables the button that made it — and writes `collections.group_order` as the COMPLETE permutation through `PUT /api/config/overrides` with the shared `api/overrides.ts` helpers, so a save here cannot drop another page's override. Reset removes the key (`withoutPath`) — the overrides contract's revert, honestly labelled as reverting to the mounted file's value rather than promising "the default". The panel states row 49's churn disclosure operator-facing: a saved order is live with no restart, lands at the next reconcile pass, and that pass re-writes one sort title per collection in the groups that moved, once. No preview-of-churn machinery was built — `POST /api/config/preview` answers `impact: null` for a collections edit correctly, since no artwork re-renders. Settings.tsx untouched: once `group_order` holds a value the generic editor renders it as an editable string list whose invalid input the 422/fieldErrors path already refuses, and the panel is the reorder surface. `deploy/README.md`'s `group_order` entry points at the panel. Adjudications in `.superpowers/sdd/p-groupui-facts.md` (C1–C7) | S — one endpoint field, one panel, no new dependencies | yes — a Groups panel on the Collections page | 49 (closed — the scheme it reorders), 102 (closed) |
```

- [ ] **Step 3: The PR body**

Create `.superpowers/sdd/p-groupui-pr-body.md` (gitignored — this file is used when the PR is opened, never committed):

```markdown
## Reorder the collection groups from the web UI (roadmap row 200)

Row 49 shipped `collections.group_order` as a YAML-only setting; the Settings
page renders it read-only and nothing in the frontend knew the groups existed.
This branch gives it a UI, per the adjudications in
`.superpowers/sdd/p-groupui-facts.md` (C1–C7).

### What changed

- **`GET /api/collections/catalog` gains `groups`** — `{key, title, section,
  position}` per group, from the new pure `group_listing` in
  `collections/groups.py`, served in the running config's effective order.
  The frontend never transcribes the group keys; the server is the single
  source (C2).
- **A Groups panel on the Collections page** (`GroupsPanel.tsx`): per-row
  up/down buttons (no dnd dependency, C4), full-permutation writes to
  `collections.group_order` through `PUT /api/config/overrides` with the
  shared overrides helpers, Reset via key removal — the contract's revert
  (C3). 422s render against the path they name.
- **The churn disclosure is operator-facing** (C5): the panel states that a
  saved order is live with no restart, lands at the next reconcile pass, and
  that the first pass to see it re-writes one sort title per collection in
  the moved groups, once. No preview-of-churn machinery — `POST
  /api/config/preview` correctly answers `impact: null` for collections
  edits, since no artwork re-renders.
- **`deploy/README.md`**: the `group_order` entry points at the panel.
  **Roadmap row 200** filed and closed.

### What deliberately did not change

- `Settings.tsx` — the generic editor stays the generic surface (C1). Once
  `group_order` holds a value it renders there as an editable string list;
  invalid input is refused by the existing 422/fieldErrors path.
- No new frontend dependency; `package.json` untouched.

### Operator acceptance

Open Collections → Groups. Move a group; Save. The panel re-reads and shows
the stored order with its new section numbers. Run Diff now (or wait for the
scheduled pass): the pass re-writes the sort titles of the moved groups'
collections — one write each, once — and the collections tab shows the new
block order. Reset removes the override; the mounted file's order (or the
built-in one) applies again from the next pass.

### Testing

- `pytest tests/test_collection_groups.py tests/test_collection_catalog.py`
  — the pure listing and the endpoint, canonical and reordered.
- `npm test` (vitest) — the panel's eleven tests (permutation writes,
  reset-as-removal, edge/focus behaviour, the C5 sentences, 422 rendering,
  post-save re-read) plus the page-level mount.
- Full backend suite and `tsc --noEmit`, green (logs under `.superpowers/`).
```

- [ ] **Step 4: Full backend suite (detached), frontend suite, ruff**

```bash
docker compose -p pguit3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pguit3-full test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pgui-full.log"
docker wait pguit3-full
```

**Read `D:\Sites\autoposter\.superpowers\run-pgui-full.log` — never trust the wait code alone** (roadmap row 193: a green `docker wait` has masked a failing suite before the pipefail discipline; with `set -o pipefail` the code is pytest's, but the log is still the evidence). Expected: the tail reports **0 failed** (the passed count is the suite's current total plus this branch's four new backend tests; ~3770s of tests, several minutes). A `tests/test_worker.py` or `tests/test_pipeline.py` one-off is rows 193/195's known flake: re-run the file alone and the full suite once before treating it as this branch's.

```bash
docker rm pguit3-full
docker compose -p pguit3 run --name pguit3-vitest web npm test > .superpowers/run-pgui-t3-vitest.log 2>&1
docker compose -p pguit3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pguit3-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-pgui-t3-ruff.log"
```

Read both logs. Expected: vitest all green; `All checks passed!`. Then `docker rm pguit3-vitest pguit3-ruff`.

- [ ] **Step 5: Commit the docs and tear down**

```bash
git add deploy/README.md docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(groups): README points at the Groups panel; roadmap row 200 filed and closed"
docker compose -p pguit3 down
```

(The PR body stays uncommitted — `.superpowers/` is gitignored, and `git status` after this commit must show a clean tree apart from that directory.)

---

## Self-review (performed while writing)

- **Spec coverage:** C1 → Task 2 scopes the panel to the Collections page and touches Settings.tsx nowhere (Global Constraints forbid it). C2 → Task 1 (server enumeration, effective order) + the no-keys-in-TS constraint and the four-group fixture proof. C3 → Task 2's save/reset tests pin the full permutation and `withoutPath`. C4 → up/down buttons, edges disabled, focus hand-off; no dependency added. C5 → `LIVE_NOTE`/`CHURN_NOTE`, asserted verbatim-ish by test; no preview machinery built. C6 → Task 3's README line and row 200. C7 → three tasks, this order, container/frontend-runner discipline in Global Constraints.
- **Placeholder scan:** every code step carries the complete code; every run step carries the exact command and expected output.
- **Type consistency:** `group_listing` → `{key, title, section, position}` → `CatalogGroup` → the panel's `byKey`/`order` — one shape end to end; `GROUP_ORDER_PATH` = `collections.group_order` everywhere, matching `config/schema.py:675`.
