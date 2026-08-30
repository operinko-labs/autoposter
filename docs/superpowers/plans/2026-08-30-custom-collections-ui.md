# Custom-Collections UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator can see the config-defined collection definitions (row 137) and create one from a pasted list URL — title, library scope, source URL — with an override-only remove as create's undo (row 202), all through the proven config-overrides write contract.

**Architecture:** T1 ships row 137's listing: `GET /api/collections/definitions` serves the RUNNING config's `collections.definitions` plus per-entry provenance (`file` | `override`) and the configured library names — no Plex, no engine run. T2 ships the server-side parser: `POST /api/collections/parse-source` over a new pure module `collections/source_urls.py`, table-driven, every accepted parse validated through the shipped builders' own params models. T3 builds the **Custom collections** panel on the Collections page: the listing, a create-from-URL form (Check via `POST /api/config/preview`, Create via `PUT /api/config/overrides`), and Remove on override-provenance rows only — with the freezing hazard pinned by a named test. T4 is the wrap: the DefinitionsPanel wart/docstring updates, the deploy README section, roadmap rows 137 and 202 closed, the PR body, full suites.

**Tech Stack:** Python 3.14 + FastAPI + pydantic v2 + pytest (compose `test` service), React 19 + vitest (compose `web` service), the config-overrides live-swap contract.

**Binding adjudications:** `.superpowers/sdd/p-ccui-facts.md` C1–C6. They are settled; nothing in this plan re-litigates them, and neither does any implementer. 137 first; the parser is server-side; create + override-only remove; edit is OUT; NO trakt; NO dynamic-in-UI; the freezing hazard is THE structural constraint.

## Global Constraints

Every task's requirements implicitly include this section. These are law.

- **The freezing hazard is the structural constraint.** The overrides document is a *delta* (`frontend/src/api/overrides.ts:16-18`): a file-defined definition is NEVER written into it — not on create, not on remove, not by seeding. T3 carries a test named for exactly this ("a file-defined definition never enters the overrides write"), and T1's provenance field exists to make the rule enforceable.
- **RED-first.** Every behaviour lands test-first: write the failing test, run it, read the failure, then implement. A test that was never seen failing proves nothing.
- **Branch `feat/custom-collections-ui`, per C6's merge-order rule.** Check `gh pr view 105 --json state,mergeCommit` first: if #105 is MERGED, cut from `main`; if still open, cut from `feat/sweep-2`'s tip (`7daf4f9`, which IS #105's content) and expect a clean rebase onto main once it lands. The user gates the PR — **this plan never pushes and never opens a PR.**
- **The standing container recipe for backend tests** — `sh -c`, because the image has no bash. Every pytest run goes through the compose `test` service with the isolated database overlay, `set -o pipefail`, and output teed to a log the host can read:
  ```bash
  docker compose -p <proj> -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --name <proj>-<label> test sh -c \
    "set -o pipefail; pytest <targets> -q 2>&1 | tee /app/.superpowers/run-pccui-<label>.log"
  ```
  No `--rm` — the container is named, the log file is **read** (`.superpowers/run-pccui-<label>.log` in the main tree; never trust the exit code or `docker wait` alone — roadmap row 193's finding), then `docker rm <proj>-<label>`. Long runs (the full suite) start detached (`run -d --name …`) with `docker wait <name>` in the foreground, then the log is read. Teardown per task: `docker compose -p <proj> down` — **never** `down -v`.
- **Frontend via the web-service host-redirect.** `npm test` is vitest, run in the compose `web` service with output redirected host-side:
  ```bash
  docker compose -p <proj> run --name <proj>-<label> web npm test > .superpowers/run-pccui-<label>.log 2>&1
  ```
  Read the log, then `docker rm <proj>-<label>`. Type-checking is `npx tsc --noEmit` through the same service.
- **Compose projects are `pccui*`** (`pccuit1`…`pccuit4`, one per task); **artifacts are `p-ccui-` prefixed** and live in `.superpowers/` / `.superpowers/sdd/` (gitignored). The PR body at `.superpowers/sdd/p-ccui-pr-body.md` is **never committed**.
- **Conventional commits, `--no-gpg-sign`, no attribution trailers of any kind** (no `Co-Authored-By`, no "Generated with", nothing). **Stage files by name** — never `git add -A`, `-u`, or `.`.
- **This plan file is committed by Task 1, Step 1.** It is an untracked working-tree file until then.

## Design resolutions (from the facts, made concrete)

Recorded here so no implementer re-derives them differently:

- **Provenance is per-entry in shape, uniform in value.** `merge_overrides` replaces every list WHOLESALE (`src/autoposter/config/overrides.py:69-93`), so the layer supplying the effective `collections.definitions` is single-valued: when the stored overrides document carries the key, every effective entry came through it → all `"override"`; when it does not, the mounted file supplies the whole list → all `"file"`. The API still stamps each entry (the frontend reasons per row), and T1 pins both states.
- **The wholesale replace's consequence is disclosed, not papered over.** A deployment whose mounted file has non-empty `definitions:` will have those rows STOP BUILDING the moment the panel stores an overrides list (the override replaces the file's list). The panel shows a warning note exactly while file-provenance rows exist, and the README says the two layers do not merge. Copying file rows into the override to "preserve" them is the freezing hazard and is forbidden.
- **A bare integer parses as `tmdb_list`.** C2 accepts bare ints; TMDb, TVDb and MDBList all take numeric list ids, so the parser takes the flagship `/list/<id>` reading and the `display_note` discloses it ("a bare number is read as a TMDb list id; paste the full URL for an MDBList or TVDb list"). The refusal hint teaches the alternatives.
- **The panel is a new component, not a rework of `DefinitionsPanel`.** `DefinitionsPanel` stays the Plex-touching dry-run surface; the new `CustomCollectionsPanel` is the config surface (listing + create + remove). T4 retires the "no listing endpoint exists" wart notes in `Collections.tsx` honestly.
- **Remove maps a row to its stored index by counting override rows** (`overrideOrdinal`), not by raw listing index — equal under today's uniform provenance, and correct even if a future server ever interleaves.
- **All library boxes checked = the `libraries` key omitted** (the definition's own `None` default, "every configured library" — `config/schema.py:313-316`). An explicit full list would freeze today's library names into the definition.

---

## File Structure

**Created:**

- `src/autoposter/collections/source_urls.py` — the pure URL→(builder, params) parser, table-driven, beside the params models it feeds.
- `tests/test_collection_definitions_api.py` — the two endpoints' API tests (T1 creates it, T2 appends).
- `tests/test_source_urls.py` — the parser's pure accept/refuse tables.
- `frontend/src/pages/CustomCollectionsPanel.tsx` — the panel: listing + provenance, create form, override-only remove, Check/Create.
- `frontend/src/pages/CustomCollectionsPanel.test.tsx` — its vitest suite, including the named freezing-hazard test.
- `frontend/src/pages/custom-collections.css` — the panel's styles (its own file, the `catalog.css`/`groups.css` precedent).
- `.superpowers/sdd/p-ccui-pr-body.md` — the PR body (T4; gitignored, uncommitted).

**Modified:**

- `src/autoposter/api/collections_builders.py` — the listing endpoint (T1) and the parse endpoint (T2) join the router after `collections_catalog` (line 168).
- `frontend/src/api/types.ts` — `DefinitionSummary`, `DefinitionsListingResponse`, `ParsedSourceResponse` (after `CollectionPreviewResponse`, ~line 405).
- `frontend/src/pages/Collections.tsx` — mounts the panel (T3); the `PREVIEW_ALL_KEY` note (:175-181) and the `DefinitionsPanel` docstring (:264-270) updated honestly (T4).
- `frontend/src/pages/Collections.test.tsx` — the page stub gains `/api/collections/definitions`; one mount assertion.
- `deploy/README.md` — a "Creating custom collections from the web UI" section before `## Collection posters` (line 522).
- `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` — row 137 (line 239) and row 202 (line 298) closed.

**Explicitly NOT touched:** `src/autoposter/config/schema.py` and `config/overrides.py` (the validators and merge are the contract this phase builds ON), every builder module (their params models are imported, never edited), `frontend/src/api/overrides.ts`, `frontend/src/pages/Settings.tsx`, `CatalogPanel.tsx`, `GroupsPanel.tsx`, `frontend/package.json` (no new dependency).

---

### Task 1: `GET /api/collections/definitions` — row 137's listing

**Files:**
- Modify: `src/autoposter/api/collections_builders.py` (import block ~line 60; new handler after line 168)
- Test: `tests/test_collection_definitions_api.py` (create)

**Interfaces:**
- Consumes: `request.app.state.config_holder.current` (the live config), `request.app.state.session_factory`, `load_overrides_document(session)` from `autoposter.config.overrides` (`overrides.py:187`), `require_session`.
- Produces: `GET /api/collections/definitions` → `{"libraries": [str], "definitions": [{"title", "builder", "params", "libraries", "sort", "sync_mode", "provenance"}]}` with `provenance ∈ {"file", "override"}` and `libraries: list[str] | None` per entry. T3's `DefinitionsListingResponse` mirrors this shape exactly.

- [ ] **Step 1: Create the branch and commit this plan**

```bash
gh pr view 105 --json state --jq .state
# MERGED  -> git checkout main && git pull && git checkout -b feat/custom-collections-ui
# OPEN    -> git checkout -b feat/custom-collections-ui feat/sweep-2
git add docs/superpowers/plans/2026-08-30-custom-collections-ui.md
git commit --no-gpg-sign -m "docs(plans): the custom-collections UI phase plan"
```

- [ ] **Step 2: Write the failing endpoint tests**

Create `tests/test_collection_definitions_api.py`:

```python
"""``GET /api/collections/definitions`` -- row 137's listing -- and, from the
same phase's T2, ``POST /api/collections/parse-source``.

The listing answers without touching anything: no Plex, no engine run, no
database read beyond the one stored-overrides lookup that decides provenance.
Provenance is the load-bearing field: "file" rows belong to the mounted YAML
and the panel must never copy them into the overrides document (the freezing
hazard ``frontend/src/api/overrides.ts`` opens with); "override" rows are the
stored document's own and are the only ones the panel may rewrite. The value
is uniform per state by construction -- the overrides layer replaces a list
WHOLESALE (``config/overrides.py``) -- and both states are pinned here.
"""
import pathlib

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.overrides import OVERRIDES_ROW_ID
from autoposter.config.schema import CollectionDefinition, Secrets
from autoposter.db.models import ConfigOverride

EXAMPLE = pathlib.Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

# One definition, written the sparse way an operator (or the panel) would.
A_DEFINITION = {
    "title": "Star Wars",
    "builder": "tmdb_collection",
    "params": {"id": 10},
    "libraries": ["Movies"],
}


@pytest_asyncio.fixture
async def app(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    return create_app(load_config(EXAMPLE), session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": "Bearer %s" % response.json()["token"]}


def _swap_definitions(app, entries) -> None:
    """The same live swap the overrides API performs, narrowed to definitions."""
    config = app.state.config_holder.current
    app.state.config_holder.swap(config.model_copy(
        update={
            "collections": config.collections.model_copy(
                update={"definitions": [
                    CollectionDefinition.model_validate(entry) for entry in entries
                ]}
            )
        }
    ))


async def test_the_definitions_listing_requires_a_session(client):
    assert (await client.get("/api/collections/definitions")).status_code == 401


async def test_the_listing_answers_without_plex_and_serves_the_library_names(
    client, app, auth_headers
):
    """Config-only, the catalog's replica argument: ``plex_server_factory`` is
    None here, the state every Plex-touching endpoint answers 503 from. The
    served ``libraries`` are the create form's scope checkboxes -- names, not
    a type enum, because a definition's scope IS names."""
    assert getattr(app.state, "plex_server_factory", None) is None

    response = await client.get("/api/collections/definitions", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["libraries"] == ["Movies", "TV Shows"]
    assert body["definitions"] == []


async def test_the_listing_reports_file_provenance_when_no_override_is_stored(
    client, app, auth_headers
):
    """No ``collections.definitions`` key in the stored overrides document
    means the mounted file supplies the whole effective list -- rows the panel
    renders WITHOUT a remove control and never writes anywhere."""
    _swap_definitions(app, [A_DEFINITION])

    body = (
        await client.get("/api/collections/definitions", headers=auth_headers)
    ).json()

    assert body["definitions"] == [{
        "title": "Star Wars",
        "builder": "tmdb_collection",
        "params": {"id": 10},
        "libraries": ["Movies"],
        "sort": "custom",
        "sync_mode": "sync",
        "provenance": "file",
    }]


async def test_the_listing_reports_override_provenance_when_the_document_carries_the_list(
    client, app, auth_headers, session_factory
):
    """With the key present in the stored document, every effective entry came
    through it (the wholesale replace), so every row is the panel's to
    rewrite -- the other half of the provenance split."""
    async with session_factory() as session:
        session.add(ConfigOverride(
            id=OVERRIDES_ROW_ID,
            document={"collections": {"definitions": [dict(A_DEFINITION)]}},
        ))
        await session.commit()
    _swap_definitions(app, [A_DEFINITION])

    body = (
        await client.get("/api/collections/definitions", headers=auth_headers)
    ).json()

    assert [entry["provenance"] for entry in body["definitions"]] == ["override"]
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
docker compose -p pccuit1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pccuit1-red1 test sh -c \
  "set -o pipefail; pytest tests/test_collection_definitions_api.py -q 2>&1 | tee /app/.superpowers/run-pccui-t1-red1.log"
```

Read `D:\Sites\autoposter\.superpowers\run-pccui-t1-red1.log`. Expected: **4 failed** — every test hits a 404 (`assert 404 == 401`, `assert 404 == 200`, and two `KeyError`/assert failures on the JSON body) because the route does not exist. Then `docker rm pccuit1-red1`.

- [ ] **Step 4: Implement the endpoint**

In `src/autoposter/api/collections_builders.py`, add one import after the `from autoposter.collections.service import (...)` block (~line 64):

```python
from autoposter.config.overrides import load_overrides_document
```

Insert the handler after `collections_catalog` (after line 168, before `class PreviewRequest`):

```python
@router.get("/collections/definitions")
async def collections_definitions(
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """The operator-configured collection definitions, without running anything.

    Row 137's listing: ``collections.definitions`` off the RUNNING config --
    the same list ``service.library_definitions`` appends after the built-in
    defaults on every pass -- with no Plex read and no engine run. The
    Definitions panel's preview stays the only dry run; this is the plain
    listing it never had.

    ``provenance`` is per entry in shape and uniform in value by construction:
    the overrides layer replaces a list WHOLESALE (``merge_overrides``), so
    when the stored overrides document carries ``collections.definitions``
    every effective entry came through it ("override"), and when it does not,
    every entry is the mounted file's ("file"). The distinction is what makes
    the Custom collections panel's writes safe: "override" rows are the
    panel's to rewrite, and "file" rows must never be copied into the stored
    document -- copying them would freeze today's file values against every
    future edit of the YAML (the delta rule ``frontend/src/api/overrides.ts``
    opens with).

    ``libraries`` is ``collections.libraries`` -- the names the create form
    offers as scope checkboxes. There is no library-type enum anywhere in a
    definition; the config speaks names, so the form does too.

    Not behind ``_enabled``, for the catalog's reason: reading config changes
    nothing, and the panel must render on a replica with no Plex connection.
    """
    config = request.app.state.config_holder.current
    async with request.app.state.session_factory() as session:
        stored = await load_overrides_document(session)
    section = stored.get("collections")
    overridden = isinstance(section, dict) and "definitions" in section
    provenance = "override" if overridden else "file"
    return {
        "libraries": list(config.collections.libraries),
        "definitions": [
            {
                "title": definition.title,
                "builder": definition.builder,
                "params": definition.params,
                "libraries": definition.libraries,
                "sort": definition.sort,
                "sync_mode": definition.sync_mode,
                "provenance": provenance,
            }
            for definition in config.collections.definitions
        ],
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker compose -p pccuit1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pccuit1-green1 test sh -c \
  "set -o pipefail; pytest tests/test_collection_definitions_api.py tests/test_collection_catalog.py -q 2>&1 | tee /app/.superpowers/run-pccui-t1-green1.log"
```

Read `D:\Sites\autoposter\.superpowers\run-pccui-t1-green1.log`. Expected: **all passed, 0 failed** (`test_collection_catalog.py` rides along as the router's nearest existing consumer). Then `docker rm pccuit1-green1`.

- [ ] **Step 6: Ruff**

```bash
docker compose -p pccuit1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pccuit1-ruff test sh -c \
  "set -o pipefail; ruff check src/autoposter/api/collections_builders.py tests/test_collection_definitions_api.py 2>&1 | tee /app/.superpowers/run-pccui-t1-ruff.log"
```

Read the log. Expected: `All checks passed!`. Then `docker rm pccuit1-ruff`.

- [ ] **Step 7: Commit and tear down**

```bash
git add src/autoposter/api/collections_builders.py tests/test_collection_definitions_api.py
git commit --no-gpg-sign -m "feat(api): GET /api/collections/definitions lists definitions with provenance (row 137)"
docker compose -p pccuit1 down
```

(`down` without `-v`, always.)

---

### Task 2: The source-URL parser and `POST /api/collections/parse-source`

**Files:**
- Create: `src/autoposter/collections/source_urls.py`, `tests/test_source_urls.py`
- Modify: `src/autoposter/api/collections_builders.py` (import + handler after Task 1's listing)
- Test: `tests/test_collection_definitions_api.py` (append two endpoint tests)

**Interfaces:**
- Consumes: the shipped params models — `ImdbListParams`/`ImdbWatchlistParams` (`builders/imdb_lists.py:110-147`), `MdblistListParams` (`builders/mdblist.py:85-126`), `TmdbEntityParams` (`builders/tmdb.py:143-158`), `TvdbListParams` (`builders/tvdb.py:67-109`). Their `extra="forbid"` and their own error strings ARE the net.
- Produces: `parse_source(text: str) -> ParsedSource` (`ParsedSource(builder: str, params: dict, display_note: str)`), raising `SourceUrlRefused(ValueError)` with an operator-facing message; `POST /api/collections/parse-source` `{"url": str}` → `{"builder", "params", "display_note"}` or a 422 whose `detail` is that message. T3 posts to this endpoint and renders both outcomes.

- [ ] **Step 1: Write the failing pure tests**

Create `tests/test_source_urls.py`:

```python
"""``collections/source_urls.py``: every accepted shape and every refusal.

Pure -- no app, no network. The parse is shape-only by design (facts C2):
nothing here mocks a provider because nothing is fetched; a list that does
not exist is the first pass's discovery, and sync semantics leave the
collection untouched when it fails.

The refusal table's fragments are the load-bearing half: a recognised shape
with a bad value must surface the params model's OWN error string (not a
paraphrase), and a trakt paste must be refused BY NAME -- row 202's fence.
"""
import re

import pytest

from autoposter.collections.source_urls import SourceUrlRefused, parse_source

ACCEPTED = [
    # imdb.com: one host, two builders -- the path decides (the dispatch the
    # recon flagged).
    ("https://www.imdb.com/list/ls055350410/", "imdb_list", {"list": "ls055350410"}),
    ("imdb.com/list/ls055350410?ref_=hm", "imdb_list", {"list": "ls055350410"}),
    (
        "https://m.imdb.com/user/ur00000001/watchlist",
        "imdb_watchlist", {"user": "ur00000001"},
    ),
    (
        "https://mdblist.com/lists/linaspurinis/top-watched-movies-of-the-week",
        "mdblist_list", {"list": "linaspurinis/top-watched-movies-of-the-week"},
    ),
    # The TMDb entity family rides free on one params model (facts C2). A
    # slugged segment's id is its leading digits.
    ("https://www.themoviedb.org/list/8136243", "tmdb_list", {"id": 8136243}),
    (
        "https://www.themoviedb.org/collection/10-star-wars-collection",
        "tmdb_collection", {"id": 10},
    ),
    ("https://www.themoviedb.org/company/2", "tmdb_company", {"id": 2}),
    ("https://www.themoviedb.org/network/213-netflix", "tmdb_network", {"id": 213}),
    (
        "https://www.themoviedb.org/keyword/9715-superhero/movie",
        "tmdb_keyword", {"id": 9715},
    ),
    (
        "https://www.thetvdb.com/lists/marvel-cinematic-universe",
        "tvdb_list", {"slug": "marvel-cinematic-universe"},
    ),
    # Bare shapes (facts C2): accepted where unambiguous.
    ("ls055350410", "imdb_list", {"list": "ls055350410"}),
    ("ur00000001", "imdb_watchlist", {"user": "ur00000001"}),
    (
        "linaspurinis/top-watched-movies-of-the-week",
        "mdblist_list", {"list": "linaspurinis/top-watched-movies-of-the-week"},
    ),
    ("8136243", "tmdb_list", {"id": 8136243}),
]


@pytest.mark.parametrize("text,builder,params", ACCEPTED)
def test_accepted_shapes_resolve_to_the_shipped_builder(text, builder, params):
    parsed = parse_source(text)

    assert (parsed.builder, parsed.params) == (builder, params)


def test_a_bare_number_discloses_the_tmdb_reading():
    """int is the one bare shape more than one builder could claim (TMDb,
    TVDb and MDBList all take a numeric list id); the note says which reading
    was taken and how to get the others."""
    parsed = parse_source("8136243")

    assert "read as a TMDb list id" in parsed.display_note


REFUSED = [
    # trakt: refused BY NAME -- row 202's fence, verbatim in the message.
    ("https://trakt.tv/users/someone/lists/best-of", "no trakt builder is shipped"),
    # An unknown host with a scheme is a refusal naming what IS supported...
    ("https://letterboxd.com/someone/list/slasher-flicks/", "is not a supported source"),
    # ...and schemeless it falls through the bare shapes to the same teaching.
    ("letterboxd.com/someone/list/slasher-flicks", "not a URL or a bare id"),
    # A known host off its list path.
    ("https://www.imdb.com/title/tt0111161/", "not a list or a user page"),
    # A recognised shape with a bad value reuses the params model's OWN error
    # string -- including the ls/ur cross-hint the imdb models teach with.
    ("https://www.imdb.com/list/ur00000001", "use the `imdb_watchlist` builder"),
    ("ls12x4", "is not an IMDb list id"),
    # TmdbEntityParams' gt=0: pydantic's own message, not a paraphrase.
    ("0", "greater than 0"),
    ("", "paste a list URL"),
    ("just words", "not a URL or a bare id"),
]


@pytest.mark.parametrize("text,fragment", REFUSED)
def test_refused_shapes_name_what_is_wrong(text, fragment):
    with pytest.raises(SourceUrlRefused, match=re.escape(fragment)):
        parse_source(text)
```

- [ ] **Step 2: Write the failing endpoint tests**

Append to `tests/test_collection_definitions_api.py`:

```python
# --- the parse endpoint (T2) ------------------------------------------------


async def test_parse_source_requires_a_session(client):
    response = await client.post(
        "/api/collections/parse-source", json={"url": "ls055350410"}
    )
    assert response.status_code == 401


async def test_parse_source_resolves_and_refuses_through_the_pure_parser(
    client, auth_headers
):
    """The endpoint is a thin shell: one accepted parse proving the shape of
    the 200, one trakt paste proving the 422 carries the refusal verbatim.
    The full accept/refuse tables are ``tests/test_source_urls.py``'s."""
    good = await client.post(
        "/api/collections/parse-source",
        json={"url": "https://www.imdb.com/list/ls055350410/"},
        headers=auth_headers,
    )
    assert good.status_code == 200
    body = good.json()
    assert body["builder"] == "imdb_list"
    assert body["params"] == {"list": "ls055350410"}
    assert body["display_note"]

    refused = await client.post(
        "/api/collections/parse-source",
        json={"url": "https://trakt.tv/users/someone/lists/best-of"},
        headers=auth_headers,
    )
    assert refused.status_code == 422
    assert "no trakt builder is shipped" in refused.json()["detail"]
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
docker compose -p pccuit2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pccuit2-red1 test sh -c \
  "set -o pipefail; pytest tests/test_source_urls.py tests/test_collection_definitions_api.py -q 2>&1 | tee /app/.superpowers/run-pccui-t2-red1.log"
```

Read `D:\Sites\autoposter\.superpowers\run-pccui-t2-red1.log`. Expected: `tests/test_source_urls.py` **errors at collection** (`ModuleNotFoundError: No module named 'autoposter.collections.source_urls'`), the two new endpoint tests **fail** (404), Task 1's four still pass. Then `docker rm pccuit2-red1`.

- [ ] **Step 4: Implement the parser module**

Create `src/autoposter/collections/source_urls.py`:

```python
"""Where a pasted list URL becomes a ``(builder, params)`` pair.

Server-side deliberately (custom-collections UI phase, facts C2): every URL
shape lives beside the params model it feeds, so what counts as "a valid IMDb
list id" exists once, in Python -- a TypeScript transcription would drift the
first time a builder's pattern moved. The endpoint wrapping this
(``api/collections_builders.py``) does nothing but call ``parse_source`` and
shape the refusal.

Shape-only, on purpose. Nothing here asks a provider whether the list exists:
validation at parse time matches validation at config load (the same params
models), and a list that does not exist is discovered the way it always has
been -- the first pass's builder raises, and sync semantics leave the
collection untouched. The form says so in as many words.

trakt is refused BY NAME rather than falling through the generic refusal:
row 202's fence says a pasted trakt URL has nothing to parse to because no
trakt builder is shipped, and an operator pasting one deserves that answer
rather than a list of hosts theirs is not among.
"""
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from autoposter.collections.builders.imdb_lists import (
    ImdbListParams,
    ImdbWatchlistParams,
)
from autoposter.collections.builders.mdblist import MdblistListParams
from autoposter.collections.builders.tmdb import TmdbEntityParams
from autoposter.collections.builders.tvdb import TvdbListParams

__all__ = ["ParsedSource", "SourceUrlRefused", "parse_source"]


class SourceUrlRefused(ValueError):
    """A paste this parser cannot turn into a shipped builder.

    The message is the whole point: operator-facing, and it either reuses the
    params model's own error string (a recognised shape with a bad value) or
    names what IS supported (an unrecognised shape).
    """


@dataclass(frozen=True)
class ParsedSource:
    """One recognised paste: the registry key, the validated params, and the
    sentence the form shows beside the resolved builder."""

    builder: str
    params: dict
    display_note: str


# What the generic refusals teach. One string, so every refusal names the same
# set and a builder added later is added here once.
SUPPORTED = (
    "imdb.com/list/ls… (imdb_list), imdb.com/user/ur… (imdb_watchlist), "
    "mdblist.com/lists/<user>/<slug> (mdblist_list), "
    "themoviedb.org's list/collection/company/network/keyword pages (tmdb_…), "
    "thetvdb.com/lists/<slug> (tvdb_list)"
)

# The TMDb entity family rides free (facts C2): one host, five path kinds, one
# params model. Disclosed per kind in the display note, because a company or
# network paste builds something broader than "the list at this URL".
_TMDB_KINDS: dict[str, tuple[str, str]] = {
    "list": ("tmdb_list", "a TMDb list, in list order"),
    "collection": (
        "tmdb_collection",
        "a TMDb franchise collection's films (Movie libraries only)",
    ),
    "company": ("tmdb_company", "everything TMDb credits to this production company"),
    "network": ("tmdb_network", "everything this TV network airs (Show libraries only)"),
    "keyword": ("tmdb_keyword", "everything TMDb tags with this keyword"),
}

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_INTEGER = re.compile(r"^\d+$")
# A TMDb path segment's leading id: themoviedb.org writes
# /collection/10-star-wars-collection, and the digits before the first dash
# are the id.
_LEADING_ID = re.compile(r"^(\d+)(?:-|$)")


def _validated(
    builder: str, model: type[BaseModel], params: dict, display_note: str
) -> ParsedSource:
    """The params, held to the builder's own model -- or refused with the
    model's own error string, never a paraphrase.

    ``exclude_none`` so optional fields the parser never sets (mdblist's
    ``sort``/``order``, tvdb_list's unused ``id``) do not appear as explicit
    nulls in the definition the form goes on to write.
    """
    try:
        valid = model.model_validate(params)
    except ValidationError as error:
        raise SourceUrlRefused(
            "; ".join(item["msg"] for item in error.errors())
        ) from error
    return ParsedSource(builder, valid.model_dump(exclude_none=True), display_note)


def _parse_imdb(segments: list[str]) -> ParsedSource:
    if len(segments) >= 2 and segments[0] == "list":
        return _validated(
            "imdb_list", ImdbListParams, {"list": segments[1]},
            "an IMDb list, in list order",
        )
    if len(segments) >= 2 and segments[0] == "user":
        return _validated(
            "imdb_watchlist", ImdbWatchlistParams, {"user": segments[1]},
            "an IMDb user's public watchlist",
        )
    raise SourceUrlRefused(
        "that imdb.com page is not a list or a user page -- supported: " + SUPPORTED
    )


def _parse_mdblist(segments: list[str]) -> ParsedSource:
    if len(segments) >= 3 and segments[0] == "lists":
        return _validated(
            "mdblist_list", MdblistListParams,
            {"list": f"{segments[1]}/{segments[2]}"},
            "an MDBList list",
        )
    raise SourceUrlRefused(
        "that mdblist.com page is not a list -- supported: " + SUPPORTED
    )


def _parse_tmdb(segments: list[str]) -> ParsedSource:
    if len(segments) >= 2 and segments[0] in _TMDB_KINDS:
        builder, note = _TMDB_KINDS[segments[0]]
        match = _LEADING_ID.match(segments[1])
        identifier = match.group(1) if match else segments[1]
        return _validated(builder, TmdbEntityParams, {"id": identifier}, note)
    raise SourceUrlRefused(
        "that themoviedb.org page is not a list, collection, company, network "
        "or keyword -- supported: " + SUPPORTED
    )


def _parse_tvdb(segments: list[str]) -> ParsedSource:
    if len(segments) >= 2 and segments[0] == "lists":
        return _validated(
            "tvdb_list", TvdbListParams, {"slug": segments[1]}, "a TVDb list"
        )
    raise SourceUrlRefused(
        "that thetvdb.com page is not a list -- supported: " + SUPPORTED
    )


def _refuse_trakt(segments: list[str]) -> ParsedSource:
    raise SourceUrlRefused(
        "trakt.tv is not a supported source: no trakt builder is shipped, so "
        "a pasted trakt URL has nothing to parse to -- trakt support is its "
        "own roadmap gap, not this form's. Supported: " + SUPPORTED
    )


_HOSTS = {
    "imdb.com": _parse_imdb,
    "m.imdb.com": _parse_imdb,
    "mdblist.com": _parse_mdblist,
    "themoviedb.org": _parse_tmdb,
    "thetvdb.com": _parse_tvdb,
    "trakt.tv": _refuse_trakt,
}


def _parse_bare(value: str) -> ParsedSource:
    """The four bare shapes (facts C2), accepted where unambiguous."""
    lowered = value.lower()
    if lowered.startswith("ls"):
        return _validated(
            "imdb_list", ImdbListParams, {"list": value}, "an IMDb list, in list order"
        )
    if lowered.startswith("ur"):
        return _validated(
            "imdb_watchlist", ImdbWatchlistParams, {"user": value},
            "an IMDb user's public watchlist",
        )
    if _INTEGER.match(value):
        # The one bare shape more than one builder could claim (TMDb, TVDb
        # and MDBList all take a numeric list id). Read as TMDb -- the
        # flagship /list/<id> URL among the sources -- and the note says so,
        # so an operator who meant another service knows to paste its URL.
        return _validated(
            "tmdb_list", TmdbEntityParams, {"id": value},
            "a TMDb list, in list order -- a bare number is read as a TMDb "
            "list id; paste the full URL for an MDBList or TVDb list",
        )
    head, _, _tail = value.partition("/")
    if "/" in value and "." not in head:
        # user/slug, MDBList's own two-part reference. A dotted first segment
        # reads as a host this parser does not know, not as a user name.
        return _validated(
            "mdblist_list", MdblistListParams, {"list": value}, "an MDBList list"
        )
    raise SourceUrlRefused(
        f"{value!r} is not a URL or a bare id this form recognises. Supported "
        "URLs: " + SUPPORTED + ". Bare values accepted: an IMDb ls…/ur… id, "
        "an MDBList <user>/<slug>, or a TMDb numeric list id."
    )


def parse_source(text: str) -> ParsedSource:
    """The ``(builder, params)`` a pasted source resolves to, or a refusal.

    Dispatch is by host for anything URL-shaped, then by bare shape. Every
    accepted parse goes through the builder's own params model, so what this
    returns is exactly what config validation will accept -- and every value
    refusal is that model's own error string.
    """
    value = text.strip()
    if not value:
        raise SourceUrlRefused("paste a list URL -- supported: " + SUPPORTED)
    candidate = value if _SCHEME.match(value) else "https://" + value
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower().removeprefix("www.")
    handler = _HOSTS.get(host)
    if handler is not None:
        return handler([segment for segment in parts.path.split("/") if segment])
    if "." in host and _SCHEME.match(value):
        # A real URL to a host this parser does not know. The bare fallbacks
        # below exist for ids, not for other services' pages.
        raise SourceUrlRefused(
            f"{host!r} is not a supported source -- supported: " + SUPPORTED
        )
    return _parse_bare(value)
```

- [ ] **Step 5: Implement the endpoint**

In `src/autoposter/api/collections_builders.py`, add the import after the `from autoposter.collections.service import (...)` block (beside Task 1's `load_overrides_document` import):

```python
from autoposter.collections.source_urls import SourceUrlRefused, parse_source
```

Insert after Task 1's `collections_definitions` handler (before `class PreviewRequest`):

```python
class ParseSourceRequest(BaseModel):
    """One pasted source URL (or bare id) to resolve to a builder."""

    url: str


@router.post("/collections/parse-source")
async def parse_collection_source(
    body: ParseSourceRequest,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Resolve a pasted list URL to ``(builder, params)`` -- shape-only.

    A thin shell over ``collections/source_urls.py``, which is where the
    shapes live (beside the params models they feed; see that module's
    docstring for why the parsing is server-side at all). Touches nothing:
    no Plex, no database, no outbound request -- a list's existence is the
    first pass's business, not this endpoint's. Not behind ``_enabled``, for
    the catalog's reason: parsing changes nothing, and the form must work on
    a replica with no Plex connection.

    The refusal is a 422 whose ``detail`` is one operator-facing sentence:
    the params model's own error string, or the supported-shapes list -- and
    for trakt, the row-202 fence by name.
    """
    try:
        parsed = parse_source(body.url)
    except SourceUrlRefused as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {
        "builder": parsed.builder,
        "params": parsed.params,
        "display_note": parsed.display_note,
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
docker compose -p pccuit2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pccuit2-green1 test sh -c \
  "set -o pipefail; pytest tests/test_source_urls.py tests/test_collection_definitions_api.py tests/test_collection_catalog.py -q 2>&1 | tee /app/.superpowers/run-pccui-t2-green1.log"
```

Read `D:\Sites\autoposter\.superpowers\run-pccui-t2-green1.log`. Expected: **all passed, 0 failed** (the 14 accept cases, the note test, the 9 refusals, the 6 API tests, plus the catalog ride-along). Then `docker rm pccuit2-green1`.

- [ ] **Step 7: Ruff**

```bash
docker compose -p pccuit2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pccuit2-ruff test sh -c \
  "set -o pipefail; ruff check src/autoposter/collections/source_urls.py src/autoposter/api/collections_builders.py tests/test_source_urls.py tests/test_collection_definitions_api.py 2>&1 | tee /app/.superpowers/run-pccui-t2-ruff.log"
```

Read the log. Expected: `All checks passed!`. Then `docker rm pccuit2-ruff`.

- [ ] **Step 8: Commit and tear down**

```bash
git add src/autoposter/collections/source_urls.py src/autoposter/api/collections_builders.py tests/test_source_urls.py tests/test_collection_definitions_api.py
git commit --no-gpg-sign -m "feat(collections): server-side source-URL parser and POST /api/collections/parse-source"
docker compose -p pccuit2 down
```

---

### Task 3: The Custom collections panel

**Files:**
- Modify: `frontend/src/api/types.ts` (insert after `CollectionPreviewResponse`, ~line 405)
- Create: `frontend/src/pages/CustomCollectionsPanel.tsx`, `frontend/src/pages/CustomCollectionsPanel.test.tsx`, `frontend/src/pages/custom-collections.css`
- Modify: `frontend/src/pages/Collections.tsx` (import + mount after `<DefinitionsPanel />`, line 587), `frontend/src/pages/Collections.test.tsx` (stub route + one assertion)

**Interfaces:**
- Consumes: T1's `GET /api/collections/definitions`, T2's `POST /api/collections/parse-source`, `POST /api/config/preview` and `PUT /api/config/overrides` (`routes.py:1482-1527` — both validate through `_validated_generation`, the full 422 catalogue of facts C4), and `documentFromConfig` / `readPath` / `withPath` / `withoutPath` / `fieldErrors` from `frontend/src/api/overrides.ts`; `ApiError` / `apiFetch` from `frontend/src/api/client.ts` (a 422 with a string `detail` arrives as the error's `message`).
- Produces: `DefinitionSummary`, `DefinitionsListingResponse`, `ParsedSourceResponse` (types.ts); `CustomCollectionsPanel` (named export), mounted on the Collections page. T4 references the panel by name in the README and roadmap rows.

- [ ] **Step 1: Add the types**

In `frontend/src/api/types.ts`, insert after `CollectionPreviewResponse` (~line 405):

```typescript
/** One operator-configured definition as GET /api/collections/definitions
 * serves it: the config-side facts only — no counts, no Plex state (that is
 * the preview's job). `libraries: null` means "every configured library".
 * `provenance` says which layer supplies the entry: "file" rows belong to
 * the mounted YAML and are never written into the overrides document (the
 * freezing hazard `api/overrides.ts` opens with); "override" rows are the
 * stored overrides list's own and are the only removable ones. */
export interface DefinitionSummary {
  title: string;
  builder: string;
  params: Record<string, unknown>;
  libraries: string[] | null;
  sort: string;
  sync_mode: string;
  provenance: "file" | "override";
}

/** GET /api/collections/definitions — row 137's listing. `libraries` is
 * `collections.libraries`, the names the create form offers as scope. */
export interface DefinitionsListingResponse {
  libraries: string[];
  definitions: DefinitionSummary[];
}

/** POST /api/collections/parse-source — a pasted URL resolved to the builder
 * and params the definition will carry. Shape-checked only: existence is the
 * first pass's business. A refusal is a 422 whose detail is one sentence. */
export interface ParsedSourceResponse {
  builder: string;
  params: Record<string, unknown>;
  display_note: string;
}
```

- [ ] **Step 2: Write the failing test suite**

Create `frontend/src/pages/CustomCollectionsPanel.test.tsx`:

```tsx
/** The Custom collections panel: the fourth writer of the overrides document,
 * and the one whose whole value is what it REFUSES to write.
 *
 * The hardest assertion is the named freezing-hazard test: a file-defined
 * definition NEVER enters the overrides write. The stored document is a
 * delta; copying a file row in would freeze today's YAML values against
 * every future edit of the file (api/overrides.ts:16-18). Everything else is
 * the sibling panels' contract: whole-list writes, key-removal as the
 * revert, unrelated overrides preserved, 422s rendered against the path they
 * name, re-read after save.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { CustomCollectionsPanel } from "./CustomCollectionsPanel";

const LIBRARIES = ["Movies", "TV Shows"];

const FILE_ROW = {
  title: "Hand Picked",
  builder: "plex_id",
  params: { ids: ["12345"] },
  libraries: ["Movies"],
  sort: "custom",
  sync_mode: "sync",
  provenance: "file",
};

const OVERRIDE_ROWS = [
  {
    title: "Star Wars",
    builder: "tmdb_collection",
    params: { id: 10 },
    libraries: null,
    sort: "custom",
    sync_mode: "sync",
    provenance: "override",
  },
  {
    title: "Weekly Watched",
    builder: "mdblist_list",
    params: { list: "someone/weekly" },
    libraries: ["Movies"],
    sort: "custom",
    sync_mode: "sync",
    provenance: "override",
  },
];

/** The stored entries behind OVERRIDE_ROWS, as the served config carries
 * them: `overridden_paths` names the list-as-leaf path and the served value
 * IS the stored one (an override wins the merge), so `documentFromConfig`
 * seeds exactly this array. */
const STORED_ENTRIES = [
  { title: "Star Wars", builder: "tmdb_collection", params: { id: 10 } },
  {
    title: "Weekly Watched",
    builder: "mdblist_list",
    params: { list: "someone/weekly" },
    libraries: ["Movies"],
  },
];

function listing(definitions: unknown[] = []) {
  return { libraries: LIBRARIES, definitions };
}

/** The config GET, whose only job here is seeding the document the panel
 * writes. The unrelated `plex.url` override is the point of the fixture: a
 * save about definitions must not drop it. */
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

/** A config already storing a definitions override, so Remove has stored
 * entries to subtract from. */
function overriddenConfig(entries: unknown[] = STORED_ENTRIES) {
  return config({
    collections: { enabled: true, definitions: entries },
    overridden_paths: ["plex.url", "collections.definitions"],
  });
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface StubOptions {
  definitions?: unknown;
  config?: unknown;
  parse?: (init?: RequestInit) => Response;
  preview?: (init?: RequestInit) => Response;
  save?: (init?: RequestInit) => Response;
}

function stubFetch(options: StubOptions = {}) {
  const puts: RequestInit[] = [];
  const previews: RequestInit[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/collections/definitions") {
      return json(options.definitions ?? listing());
    }
    if (path === "/api/config") return json(options.config ?? config());
    if (path === "/api/collections/parse-source") {
      if (options.parse) return options.parse(init);
      return json({
        builder: "imdb_list",
        params: { list: "ls055350410" },
        display_note: "an IMDb list, in list order",
      });
    }
    if (path === "/api/config/preview") {
      previews.push(init ?? {});
      if (options.preview) return options.preview(init);
      return json({
        version_before: "cfg-1",
        version_after: "cfg-2",
        restart_required: [],
        inert: [],
        impact: null,
      });
    }
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
  return { fetchMock, puts, previews };
}

/** The document a PUT (or preview POST) carried, parsed. */
function sentDocument(requests: RequestInit[], index = 0): Record<string, any> {
  const body = requests[index]?.body;
  expect(typeof body).toBe("string");
  return JSON.parse(body as string).document;
}

async function renderPanel(options: StubOptions = {}) {
  const stub = stubFetch(options);
  render(<CustomCollectionsPanel />);
  await screen.findByText("Create from a list URL");
  return stub;
}

/** Fill the form and resolve the URL through the (stubbed) parse endpoint. */
async function fillAndParse(
  title = "Space Operas",
  url = "https://www.imdb.com/list/ls055350410/",
) {
  fireEvent.change(screen.getByLabelText("Collection title"), {
    target: { value: title },
  });
  fireEvent.change(screen.getByLabelText("Source URL"), { target: { value: url } });
  fireEvent.click(screen.getByRole("button", { name: "Read URL" }));
  await screen.findByText("imdb_list");
}

beforeEach(() => {
  setToken(null);
});

describe("the custom collections panel", () => {
  it("lists definitions with provenance, offering Remove only on override rows", async () => {
    await renderPanel({
      definitions: listing([FILE_ROW, ...OVERRIDE_ROWS]),
      config: overriddenConfig(),
    });

    const fileRow = within(screen.getByText("Hand Picked").closest("tr")!);
    expect(fileRow.getByText("config file")).toBeInTheDocument();
    expect(fileRow.queryByRole("button", { name: /Remove/ })).toBeNull();

    expect(screen.getByRole("button", { name: "Remove Star Wars" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Remove Weekly Watched" }),
    ).toBeEnabled();
    expect(screen.getAllByText("override")).toHaveLength(2);
  });

  it("shows the wholesale-replace warning while file rows exist", async () => {
    await renderPanel({ definitions: listing([FILE_ROW]) });

    expect(screen.getByText(/do not merge/)).toBeInTheDocument();
  });

  it("hides the warning when every row is an override", async () => {
    await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    expect(screen.queryByText(/do not merge/)).toBeNull();
  });

  it("a file-defined definition never enters the overrides write", async () => {
    // THE freezing-hazard test (facts C1/C3). The file's "Hand Picked" is in
    // the effective listing but not in the stored overrides; a create must
    // write ONLY the new entry, or today's YAML values are frozen forever.
    const { puts } = await renderPanel({ definitions: listing([FILE_ROW]) });

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    // All library boxes are checked, so the `libraries` key is omitted -- the
    // definition's own "every configured library" default.
    expect(document.collections.definitions).toEqual([
      { title: "Space Operas", builder: "imdb_list", params: { list: "ls055350410" } },
    ]);
    expect(JSON.stringify(document)).not.toContain("Hand Picked");
    // The unrelated override the config reported is still in the document.
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("narrows the entry to the checked libraries", async () => {
    const { puts } = await renderPanel();

    await fillAndParse();
    fireEvent.click(screen.getByRole("checkbox", { name: "TV Shows" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(sentDocument(puts).collections.definitions).toEqual([
      {
        title: "Space Operas",
        builder: "imdb_list",
        params: { list: "ls055350410" },
        libraries: ["Movies"],
      },
    ]);
  });

  it("appends to the stored override list on create", async () => {
    const { puts } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(sentDocument(puts).collections.definitions).toEqual([
      ...STORED_ENTRIES,
      { title: "Space Operas", builder: "imdb_list", params: { list: "ls055350410" } },
    ]);
  });

  it("removes one override row by its stored position", async () => {
    const { puts } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Star Wars" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(sentDocument(puts).collections.definitions).toEqual([STORED_ENTRIES[1]]);
  });

  it("removing the last override drops the key — the overrides revert", async () => {
    const { puts } = await renderPanel({
      definitions: listing([OVERRIDE_ROWS[0]]),
      config: overriddenConfig([STORED_ENTRIES[0]]),
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Star Wars" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    // The revert IS the absence: `[]` would keep shadowing whatever the file
    // lists; the key going away hands the decision back to the file.
    expect(document.collections).toBeUndefined();
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("Check posts to the preview endpoint and stores nothing", async () => {
    const { puts, previews } = await renderPanel();

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Check" }));

    await screen.findByText(/Nothing was stored/);
    expect(previews).toHaveLength(1);
    expect(puts).toHaveLength(0);
    expect(sentDocument(previews).collections.definitions).toEqual([
      { title: "Space Operas", builder: "imdb_list", params: { list: "ls055350410" } },
    ]);
  });

  it("renders the parse refusal under the URL field", async () => {
    await renderPanel({
      parse: () =>
        json(
          {
            detail:
              "trakt.tv is not a supported source: no trakt builder is shipped",
          },
          422,
        ),
    });

    fireEvent.change(screen.getByLabelText("Source URL"), {
      target: { value: "https://trakt.tv/users/x/lists/y" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Read URL" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("no trakt builder is shipped");
  });

  it("renders a save 422 against the path it names", async () => {
    await renderPanel({
      save: () =>
        json(
          {
            detail: [
              {
                path: "collections",
                message:
                  "collection definition 'Space Operas' has the same title as a " +
                  "built-in collection this service already builds",
              },
            ],
          },
          422,
        ),
    });

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText(/has the same title/)).toBeInTheDocument();
    expect(screen.getByText("collections")).toBeInTheDocument();
  });

  it("clears the form and re-reads after a create, reporting versions", async () => {
    const { fetchMock, puts } = await renderPanel();

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(puts).toHaveLength(1));

    // What is stored is the server's answer, not this component's memory.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([path]) => path === "/api/collections/definitions"),
      ).toHaveLength(2),
    );
    expect(screen.getByLabelText("Collection title")).toHaveValue("");
    expect(screen.getByLabelText("Source URL")).toHaveValue("");
    const saved = await screen.findByText(/Saved\./);
    expect(saved).toHaveTextContent("cfg-1");
    expect(saved).toHaveTextContent("cfg-2");
  });

  it("disables Check and Create until parsed, titled, and scoped to at least one library", async () => {
    await renderPanel();

    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();

    await fillAndParse();
    expect(screen.getByRole("button", { name: "Create" })).toBeEnabled();

    // Zero libraries would write `libraries: []` -- "no library at all",
    // which the schema allows and no operator means.
    fireEvent.click(screen.getByRole("checkbox", { name: "Movies" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "TV Shows" }));
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });
});
```

- [ ] **Step 3: Run the suite to verify it fails**

```bash
docker compose -p pccuit3 run --name pccuit3-red1 web npm test > .superpowers/run-pccui-t3-red1.log 2>&1
```

Read `D:\Sites\autoposter\.superpowers\run-pccui-t3-red1.log`. Expected: `CustomCollectionsPanel.test.tsx` **errors** (`Failed to resolve import "./CustomCollectionsPanel"`); every other test file passes. Then `docker rm pccuit3-red1`.

- [ ] **Step 4: Write the styles**

Create `frontend/src/pages/custom-collections.css`:

```css
/* The Custom collections panel: the definitions listing with provenance, and
 * the create-from-URL form. Its own stylesheet, catalog.css's precedent: the
 * rules a later form panel would copy should be findable by name. */

.custom-note {
  margin: 0 0 8px;
  font-size: 13px;
  white-space: normal;
}

.custom-file-note {
  color: var(--warn);
}

.custom-params {
  font-size: 12px;
  word-break: break-all;
}

.custom-badge {
  display: inline-block;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
}

.custom-file {
  color: var(--warn);
  border-color: var(--warn);
}

.custom-form-title {
  margin: 16px 0 4px;
}

.custom-form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px;
  margin: 8px 0;
}

.custom-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.custom-field input {
  min-width: 16rem;
}

.custom-url {
  flex: 1 1 24rem;
}

.custom-url input {
  width: 100%;
}

.custom-parse-error {
  margin: 4px 0;
  font-size: 13px;
  white-space: normal;
  color: var(--error);
}

.custom-parsed {
  margin: 4px 0;
  font-size: 13px;
  white-space: normal;
}

.custom-libraries {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin: 8px 0;
  padding: 8px 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.custom-library {
  display: flex;
  align-items: center;
  gap: 6px;
}

.custom-actions {
  display: flex;
  gap: 8px;
  margin: 8px 0;
}

.custom-errors {
  margin: 8px 0 0;
  padding-left: 1.1rem;
  font-size: 13px;
}

.custom-error-message {
  color: var(--error);
}

.custom-checked {
  font-size: 13px;
  white-space: normal;
}

.custom-saved {
  margin-top: 12px;
  padding: 10px 14px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.custom-saved p {
  margin: 0 0 4px;
  white-space: normal;
}

.custom-saved p:last-child {
  margin-bottom: 0;
}

/* "Saved, but the re-read failed" -- a warning inside the success panel, not
 * a page error beside it, because the save itself did succeed. */
.custom-stale {
  color: var(--warn);
  font-size: 13px;
}
```

- [ ] **Step 5: Write the panel**

Create `frontend/src/pages/CustomCollectionsPanel.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
// The overrides document and its helpers are shared with the settings page
// and the sibling panels -- see `api/overrides.ts` for why a second copy of
// the seeding rule would be a bug rather than a duplication.
import {
  documentFromConfig,
  fieldErrors,
  readPath,
  withPath,
  withoutPath,
} from "../api/overrides";
import type {
  ConfigPreviewResponse,
  ConfigResponse,
  ConfigSaveResponse,
  DefinitionsListingResponse,
  DefinitionSummary,
  OverridesDocument,
  ParsedSourceResponse,
} from "../api/types";
import "./custom-collections.css";

/** Where every write here lands. The overrides layer replaces a list
 * WHOLESALE, so this panel always writes the complete override list -- and
 * builds it only from `overrideList` below, never from the whole config and
 * never from the server's file-provenance rows (the freezing hazard
 * `api/overrides.ts` opens with: a copied file entry would pin today's YAML
 * values against every future edit of the file). */
const DEFINITIONS_PATH = "collections.definitions";

/** Facts C2: what Read URL, Check and Create actually verify -- shape,
 * never existence. */
const SHAPE_ONLY_NOTE =
  "The URL is checked for shape only — whether the list exists is discovered " +
  "when the reconcile pass next runs it, and a source that fails leaves its " +
  "collection untouched rather than emptying it.";

/** Facts C3: the orphan story on remove, one honest sentence -- plus why
 * there is no edit control. */
const REMOVE_NOTE =
  "Removing a definition only stops the pass building it — the collection " +
  "already in Plex follows collections.delete_unconfigured: reported as an " +
  "orphan by default, deleted only when that setting says so. There is no " +
  "edit: change a definition by removing and re-creating it.";

/** The wholesale-replace disclosure, shown only while file-provenance rows
 * exist. It is the one thing an operator who writes definitions in the
 * mounted file must know before creating one here. */
const FILE_ROWS_NOTE =
  "Definitions in the mounted config file do not merge with definitions " +
  "created here: the first one saved here stores an overrides list that " +
  "replaces the file's for as long as it exists. Rows marked config file " +
  "are removable only by editing the file.";

/** CatalogPanel's precedent, for a save from this panel. */
const APPLIES_NOTE =
  "The change is live in the running process, and the collection itself " +
  "appears at the next reconcile — use Diff now above to run one immediately.";

/** The stored override list, as seeded from the served config (the served
 * value IS the stored one -- an override wins the merge). Absent exactly
 * when the mounted file's list is in force, which is when nothing may be
 * copied in. */
function overrideList(stored: OverridesDocument): unknown[] {
  const value = readPath(stored, DEFINITIONS_PATH);
  return Array.isArray(value) ? value : [];
}

/** The stored document plus one created entry, list written whole. */
function documentForCreate(
  stored: OverridesDocument,
  entry: Record<string, unknown>,
): OverridesDocument {
  return withPath(stored, DEFINITIONS_PATH, [...overrideList(stored), entry]);
}

/** The stored document minus the override entry at `ordinal`.
 *
 * Removing the last entry drops the key instead of writing `[]`: the key
 * going away is the overrides contract's revert, and it hands the decision
 * back to whatever the mounted file lists. */
function documentForRemove(
  stored: OverridesDocument,
  ordinal: number,
): OverridesDocument {
  const remaining = overrideList(stored).filter((_, at) => at !== ordinal);
  return remaining.length === 0
    ? withoutPath(stored, DEFINITIONS_PATH)
    : withPath(stored, DEFINITIONS_PATH, remaining);
}

/** A listing row's position within the stored override list: its index among
 * the override-provenance rows. Provenance is uniform today (the wholesale
 * replace makes the supplying layer single-valued), so this equals the raw
 * index -- counting keeps the mapping right regardless. */
function overrideOrdinal(definitions: DefinitionSummary[], index: number): number {
  return (
    definitions.slice(0, index + 1).filter((row) => row.provenance === "override")
      .length - 1
  );
}

/** The config-defined collection definitions, and the form that creates one
 * from a pasted list URL.
 *
 * The config surface, deliberately apart from the DefinitionsPanel above it:
 * that panel runs a real, Plex-touching dry run over these same definitions,
 * while everything here is config reads and config writes -- so this panel
 * renders on a replica where the Plex-touching panels report 503, the same
 * posture as the catalog and groups panels.
 *
 * Remove exists only for override-provenance rows -- create's undo (facts
 * C3). A file row renders with a badge and no control: this panel never
 * writes a file-defined entry anywhere, which is the freezing guard made
 * visible.
 */
export function CustomCollectionsPanel() {
  const [listing, setListing] = useState<DefinitionsListingResponse | null>(null);
  // The overrides the server already holds. Kept whole rather than reduced
  // to the one path: a save here must not drop an override another page
  // stored.
  const [stored, setStored] = useState<OverridesDocument>({});
  const [loadError, setLoadError] = useState<string | null>(null);

  // The create form.
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [parsed, setParsed] = useState<ParsedSourceResponse | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});

  // One busy key for whichever request is in flight ("parsing", "checking",
  // "saving", or "removing-<n>"), so only one write races nothing.
  const [busy, setBusy] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Separate from `saveError` on purpose: a re-read that fails after the
  // store succeeded did not unsave anything, and reporting it as a save
  // failure would put a red error beside "Saved."
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [checked, setChecked] = useState<ConfigPreviewResponse | null>(null);
  const [result, setResult] = useState<ConfigSaveResponse | null>(null);

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

  const adopt = useCallback(
    (definitions: DefinitionsListingResponse, config: ConfigResponse) => {
      setListing(definitions);
      setStored(documentFromConfig(config));
      // Scope starts as "every library": all boxes checked, which the entry
      // builder writes as NO `libraries` key at all.
      setChosen(
        Object.fromEntries(definitions.libraries.map((name) => [name, true])),
      );
    },
    [],
  );

  const reload = useCallback(async () => {
    const [definitions, config] = await Promise.all([
      apiFetch<DefinitionsListingResponse>("/api/collections/definitions"),
      apiFetch<ConfigResponse>("/api/config"),
    ]);
    if (live.current) adopt(definitions, config);
  }, [adopt]);

  useEffect(() => {
    reload().catch((caught: Error) => {
      if (live.current) setLoadError(caught.message);
    });
  }, [reload]);

  if (loadError !== null) {
    return (
      <section className="panel custom-collections-panel">
        <h2>Custom collections</h2>
        <p className="page-error">{loadError}</p>
      </section>
    );
  }

  if (listing === null) {
    return (
      <section className="panel custom-collections-panel">
        <h2>Custom collections</h2>
        <p className="muted">Loading…</p>
      </section>
    );
  }

  const fileRows = listing.definitions.some((row) => row.provenance === "file");
  const chosenNames = listing.libraries.filter((name) => chosen[name]);
  const allChosen = chosenNames.length === listing.libraries.length;
  const ready =
    parsed !== null && title.trim() !== "" && chosenNames.length > 0;

  /** Any edit invalidates what a previous Check or save reported (the
   * CatalogPanel posture: a 422 pinned to a definition nobody is proposing
   * any more is a refusal of nothing). */
  function touch() {
    setResult(null);
    setChecked(null);
    setReloadError(null);
    setErrors({});
    setSaveError(null);
  }

  function entry(): Record<string, unknown> {
    return {
      title: title.trim(),
      builder: parsed!.builder,
      params: parsed!.params,
      // All boxes checked = the key omitted = the definition's own "every
      // configured library" default (`None`). An explicit full list would
      // freeze today's library names into the definition.
      ...(allChosen ? {} : { libraries: chosenNames }),
    };
  }

  async function parse() {
    setBusy("parsing");
    setParsed(null);
    setParseError(null);
    touch();
    try {
      const response = await apiFetch<ParsedSourceResponse>(
        "/api/collections/parse-source",
        { method: "POST", body: JSON.stringify({ url }) },
      );
      if (live.current) setParsed(response);
    } catch (caught) {
      // A 422's string detail IS the message (api/client.ts errorBody), so
      // the refusal -- the params model's own error string, or the trakt
      // fence -- lands here verbatim.
      if (live.current) setParseError((caught as Error).message);
    } finally {
      if (live.current) setBusy(null);
    }
  }

  async function check() {
    setBusy("checking");
    touch();
    try {
      const response = await apiFetch<ConfigPreviewResponse>(
        "/api/config/preview",
        {
          method: "POST",
          body: JSON.stringify({ document: documentForCreate(stored, entry()) }),
        },
      );
      if (live.current) setChecked(response);
    } catch (caught) {
      if (live.current) {
        if (caught instanceof ApiError && caught.status === 422) {
          setErrors(fieldErrors(caught.detail));
          setSaveError("The server rejected this definition.");
        } else {
          setSaveError((caught as Error).message);
        }
      }
    } finally {
      if (live.current) setBusy(null);
    }
  }

  /** One PUT for create and remove -- the sibling panels' idiom verbatim:
   * store, then re-read OUTSIDE the try, because a failed re-read after a
   * successful store is staleness, not an unsaved change. */
  async function put(document: OverridesDocument, key: string) {
    setBusy(key);
    touch();
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
          setSaveError("The server rejected this change.");
        } else {
          setSaveError((caught as Error).message);
        }
      }
    }
    if (saved) {
      if (live.current && key === "saving") {
        // The created definition is stored; a form still holding it would
        // invite a duplicate-title 422 on the very next click.
        setTitle("");
        setUrl("");
        setParsed(null);
        setParseError(null);
      }
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
    if (live.current) setBusy(null);
  }

  return (
    <section className="panel custom-collections-panel">
      <h2>Custom collections</h2>

      <p className="muted custom-note">
        The config's own <span className="mono">collections.definitions</span> —
        what the pass builds beyond the built-ins and the catalog's presets.
        Create one from a list URL below; it is stored as a config override,
        like the settings page's edits.
      </p>
      <p className="muted custom-note">{REMOVE_NOTE}</p>
      {fileRows && (
        <p className="muted custom-note custom-file-note">{FILE_ROWS_NOTE}</p>
      )}

      {listing.definitions.length === 0 ? (
        <p className="empty">No definitions are configured — create one below.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Builder</th>
                <th>Params</th>
                <th>Libraries</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {listing.definitions.map((row, index) => (
                <tr key={`${index}-${row.title}`}>
                  <td className="cell-title">{row.title}</td>
                  <td className="mono">{row.builder}</td>
                  <td className="mono custom-params">{JSON.stringify(row.params)}</td>
                  <td className="muted">
                    {row.libraries === null ? "all" : row.libraries.join(", ")}
                  </td>
                  <td>
                    <span className={`custom-badge custom-${row.provenance}`}>
                      {row.provenance === "file" ? "config file" : "override"}
                    </span>
                  </td>
                  <td>
                    {/* Remove is create's undo, so it exists only for rows
                        the overrides document supplies. A file row's missing
                        control IS the freezing guard made visible: this
                        panel never writes file entries anywhere. */}
                    {row.provenance === "override" && (
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
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3 className="custom-form-title">Create from a list URL</h3>
      <p className="muted custom-note">{SHAPE_ONLY_NOTE}</p>

      <div className="custom-form">
        <label className="custom-field">
          <span>Title</span>
          <input
            type="text"
            value={title}
            aria-label="Collection title"
            onChange={(event) => {
              touch();
              setTitle(event.target.value);
            }}
          />
        </label>
        <label className="custom-field custom-url">
          <span>Source URL</span>
          <input
            type="text"
            value={url}
            placeholder="https://www.imdb.com/list/ls… — or an MDBList, TMDb or TVDb list URL"
            aria-label="Source URL"
            onChange={(event) => {
              touch();
              setParsed(null);
              setParseError(null);
              setUrl(event.target.value);
            }}
          />
        </label>
        <button
          type="button"
          disabled={busy !== null || url.trim() === ""}
          onClick={() => void parse()}
        >
          {busy === "parsing" ? "Reading…" : "Read URL"}
        </button>
      </div>

      {parseError !== null && (
        <p className="custom-parse-error" role="alert">
          {parseError}
        </p>
      )}
      {parsed !== null && (
        <p className="custom-parsed" role="status">
          Builder: <span className="mono">{parsed.builder}</span>
          {" — "}
          {parsed.display_note}
        </p>
      )}

      <fieldset className="custom-libraries">
        <legend className="muted">
          Libraries — all checked applies it to every configured library
        </legend>
        {listing.libraries.map((name) => (
          <label key={name} className="custom-library">
            <input
              type="checkbox"
              checked={chosen[name] ?? false}
              onChange={(event) => {
                touch();
                setChosen((previous) => ({
                  ...previous,
                  [name]: event.target.checked,
                }));
              }}
            />
            <span>{name}</span>
          </label>
        ))}
      </fieldset>

      <div className="custom-actions">
        <button
          type="button"
          disabled={busy !== null || !ready}
          onClick={() => void check()}
        >
          {busy === "checking" ? "Checking…" : "Check"}
        </button>
        <button
          type="button"
          disabled={busy !== null || !ready}
          onClick={() => void put(documentForCreate(stored, entry()), "saving")}
        >
          {busy === "saving" ? "Creating…" : "Create"}
        </button>
      </div>

      {saveError !== null && <p className="page-error">{saveError}</p>}
      {Object.entries(errors).length > 0 && (
        <ul className="custom-errors">
          {Object.entries(errors).map(([path, message]) => (
            <li key={path}>
              <span className="mono">{path}</span>{" "}
              <span className="custom-error-message">{message}</span>
            </li>
          ))}
        </ul>
      )}

      {checked !== null && (
        <p className="custom-checked" role="status">
          {`Checks out — the server would accept this definition (config ` +
            `${checked.version_before} → ${checked.version_after}). Nothing was stored.`}
        </p>
      )}

      {result !== null && (
        <div className="custom-saved" role="status">
          <p>{`Saved. Config ${result.version_before} → ${result.version_after}.`}</p>
          <p className="muted">{APPLIES_NOTE}</p>
          {reloadError !== null && <p className="custom-stale">{reloadError}</p>}
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
docker compose -p pccuit3 run --name pccuit3-green1 web npm test > .superpowers/run-pccui-t3-green1.log 2>&1
```

Read `D:\Sites\autoposter\.superpowers\run-pccui-t3-green1.log`. Expected: every test file passes, 0 failures — including all thirteen `CustomCollectionsPanel.test.tsx` tests. Then `docker rm pccuit3-green1`.

- [ ] **Step 7: Mount the panel on the Collections page, and pin the mount**

In `frontend/src/pages/Collections.tsx`: add the import beside the catalog panel's (line 16):

```tsx
import { CustomCollectionsPanel } from "./CustomCollectionsPanel";
```

and mount it directly after `<DefinitionsPanel />` (line 587):

```tsx
      <DefinitionsPanel />

      {/* The config surface for those same definitions: the row-137 listing,
          create-from-URL, and override-only remove. Config reads and config
          writes only, so it renders on a replica where the Plex-touching
          panels report 503. */}
      <CustomCollectionsPanel />
```

In `frontend/src/pages/Collections.test.tsx`: the page now mounts a consumer of the definitions listing, so `stubFetch` gains a route — insert directly after the `/api/collections/catalog` line (line 147):

```tsx
    // The custom-collections panel mounted on this page fetches its own
    // listing; its behaviour is covered in CustomCollectionsPanel.test.tsx.
    if (path === "/api/collections/definitions") {
      return json({ libraries: ["Movies"], definitions: [] });
    }
```

and add one page-level assertion at the end of the `describe("Collections", …)` block (the same posture the file takes for the catalog panel — behaviour lives in the panel's own suite; the page owes it a well-formed answer and a mount):

```tsx
  it("mounts the custom collections panel", async () => {
    stubFetch();

    render(<Collections />);

    expect(await screen.findByText("Create from a list URL")).toBeInTheDocument();
  });
```

- [ ] **Step 8: Run the full frontend suite and the type check**

```bash
docker compose -p pccuit3 run --name pccuit3-green2 web npm test > .superpowers/run-pccui-t3-green2.log 2>&1
docker compose -p pccuit3 run --name pccuit3-tsc web npx tsc --noEmit > .superpowers/run-pccui-t3-tsc.log 2>&1
```

Read both logs. Expected: all vitest files pass, 0 failures; `tsc` produces no output (empty log means clean). Then `docker rm pccuit3-green2 pccuit3-tsc`.

- [ ] **Step 9: Commit and tear down**

```bash
git add frontend/src/api/types.ts frontend/src/pages/CustomCollectionsPanel.tsx frontend/src/pages/CustomCollectionsPanel.test.tsx frontend/src/pages/custom-collections.css frontend/src/pages/Collections.tsx frontend/src/pages/Collections.test.tsx
git commit --no-gpg-sign -m "feat(frontend): the Custom collections panel creates and removes definitions from list URLs"
docker compose -p pccuit3 down
```

---

### Task 4: Wrap — wart retirement, README, rows 137 + 202, PR body, full suites

**Files:**
- Modify: `frontend/src/pages/Collections.tsx:175-181` (the `PREVIEW_ALL_KEY` note) and `:264-270` (the `DefinitionsPanel` docstring), `deploy/README.md` (new section before `## Collection posters`, line 522), `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (row 137 at line 239, row 202 at line 298)
- Create: `.superpowers/sdd/p-ccui-pr-body.md` (gitignored; NEVER committed)

**Interfaces:**
- Consumes: everything T1–T3 shipped (named in the rows and the PR body).
- Produces: the phase's paperwork. Nothing consumes it downstream. **No push, no PR — the user gates.**

- [ ] **Step 1: Retire the "no listing endpoint" warts honestly**

In `frontend/src/pages/Collections.tsx`, replace the comment above `PREVIEW_ALL_KEY` (lines 175-180):

```tsx
/** No endpoint lists the config-defined collection definitions on their own --
 * only `POST /api/collections/preview` (a real dry run against Plex) reports
 * what they are. Adding one was out of scope for this touch (frontend-only),
 * so "Preview all" -- a full, unfiltered preview -- doubles as the listing:
 * it is also the only preview that reports the delete sweep, since the
 * server runs the sweep only when nothing was filtered out. */
```

with:

```tsx
/** `GET /api/collections/definitions` (row 137) now lists the config-defined
 * definitions -- the Custom collections panel below renders it. What this key
 * still marks is the PREVIEW: a real, Plex-touching dry run, which is why it
 * stays a deliberate click rather than a mount-time fetch -- and "Preview
 * all" is still the only view that reports the delete sweep, since the
 * server runs the sweep only when nothing was filtered out. */
```

Replace the `DefinitionsPanel` docstring (lines 264-270):

```tsx
/** The config-defined collections and what the last preview said about each.
 *
 * There is no "list definitions" endpoint (see the module-level note on
 * `PREVIEW_ALL_KEY`), so the panel starts empty and "Preview all" is both the
 * listing and the first preview -- a real, Plex-touching dry run, so it is a
 * deliberate click rather than something this page fires on mount, the same
 * posture the Modes page takes towards every one of its dry runs. */
```

with:

```tsx
/** The config-defined collections and what the last preview said about each.
 *
 * The plain listing lives in the Custom collections panel below (row 137's
 * endpoint); this panel is the DRY RUN over those same definitions. It still
 * starts empty: a preview is a real, Plex-touching run, so it is a deliberate
 * click rather than something this page fires on mount, the same posture the
 * Modes page takes towards every one of its dry runs. */
```

- [ ] **Step 2: The README section**

In `deploy/README.md`, insert after line 520 (`See \`config/autoposter.example.yaml\` for the full block.`) and before `## Collection posters` (line 522):

```markdown

## Creating custom collections from the web UI

The Collections page's **Custom collections** panel (roadmap rows 137 + 202)
creates `definitions:` entries without editing the file above. Paste a list
URL, name the collection, pick which of `collections.libraries` it applies to
(all boxes checked = every library), Check, Create:

- **Supported pastes:** `imdb.com/list/ls…`, `imdb.com/user/ur…` (a
  watchlist), `mdblist.com/lists/<user>/<slug>`, themoviedb.org's
  list/collection/company/network/keyword pages, `thetvdb.com/lists/<slug>` —
  plus bare `ls…`/`ur…` ids, an MDBList `<user>/<slug>`, and a bare number
  (read as a TMDb list id; the form discloses that reading). The server
  resolves the paste to the builder and shows which one.
- **Shape-checked only.** Nothing asks the provider whether the list exists;
  the first pass discovers that, and a failing source leaves its collection
  untouched (the sync-semantics guarantee documented above).
- **trakt is refused by name** — no trakt builder is shipped (its own roadmap
  gap), so a trakt URL has nothing to parse to.
- **Stored as config overrides**, like the Settings page's edits: the panel
  writes `collections.definitions` through `PUT /api/config/overrides`, and
  the definitions it creates are removable from the same panel. Definitions
  written in the mounted file render with a *config file* badge and no remove
  control — and the two layers do not merge: an overrides list REPLACES the
  file's `definitions:` for as long as it exists, so a deployment that
  hand-writes file definitions should keep creating them there.
- **Removing** a definition only stops the pass building it; the collection
  in Plex follows `delete_unconfigured` (reported as an orphan by default).
  There is no edit control: edit = remove + create.
```

- [ ] **Step 3: Close roadmap rows 137 and 202**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, replace row 137's line (line 239) with:

```markdown
| 137 | Collection definitions listing endpoint (CLOSED — the custom-collections UI phase) | 8a Task 6 finding: the Definitions panel had nothing to show until an operator clicked Preview-all, because only `POST /api/collections/preview` existed. **What shipped.** `GET /api/collections/definitions` (`src/autoposter/api/collections_builders.py`): the RUNNING config's `collections.definitions` — title, builder, params, libraries, sort, sync_mode per entry — plus `collections.libraries` (the create form's scope names) and per-entry `provenance` (`file`/`override`). Provenance is decided by whether the stored overrides document carries the `collections.definitions` key: the overrides layer replaces a list WHOLESALE (`config/overrides.py`), so the supplying layer is single-valued per state — uniform in value, per-entry in shape — and the label is what lets the Custom collections panel (row 202) rewrite override rows while never copying file rows into the stored document (the freezing hazard `frontend/src/api/overrides.ts:16-18` names). No Plex read, no engine run, not behind the `_enabled` gate — the catalog's replica argument. The Definitions panel's "Preview-all is the listing" wart notes are retired by the same phase; the preview stays the only dry run. Adjudication C1 in `.superpowers/sdd/p-ccui-facts.md` | S — one config-read endpoint | yes — the Collections page lists definitions without running a preview | 95 (closed) |
```

Replace row 202's line (line 298) with:

```markdown
| 202 | A UI for operator-defined managed collections (title, type, source URL) (CLOSED — the custom-collections UI phase) | Filed by the dc-split phase's wrap on the user's directive; closed by the phase that also closed row 137. **What shipped.** (1) `POST /api/collections/parse-source` over the new pure `collections/source_urls.py`: a table-driven, server-side URL→(builder, params) parser — imdb.com/list/ls… → `imdb_list` and imdb.com/user/ur… → `imdb_watchlist` (one host, two builders), mdblist.com/lists/<user>/<slug> → `mdblist_list` (the paste `builders/mdblist.py:88-92` deliberately refuses in params now means something one layer up, exactly as filed), themoviedb.org's list/collection/company/network/keyword pages → the TMDb entity family (rides free on one params model, disclosed per kind in the display note), thetvdb.com/lists/<slug> → `tvdb_list`. Bare `ls…`/`ur…`/`<user>/<slug>` are accepted; a bare integer is read as a TMDb list id with the reading disclosed. Every accepted parse is validated through the builder's own params model, every value refusal reuses that model's own error string, and parsing is shape-only — existence stays the first pass's business, and the form says so. (2) The **Custom collections** panel on the Collections page: title + source URL + library-scope CHECKBOXES over the served `collections.libraries` names (all checked = the key omitted = every library; there is no type enum anywhere in a definition, so the filing's "Movies / Shows / Both" became names, which is strictly more expressive), Check via `POST /api/config/preview` (nothing persisted; the full 422 catalogue, including the duplicate-title guard and the operator-group separator reservation `config/schema.py:899`), Create via `PUT /api/config/overrides` with the shared `api/overrides.ts` helpers — the stored list always written whole from the override layer only, with a named test that a file-defined definition never enters the write. **One widening over the filing, disclosed:** REMOVE ships, for override-provenance rows only — adjudicated as create's undo (a typo'd UI creation must not require config surgery); file rows render with a config-file badge and no control. EDIT stays out (remove + create). **Both fences held:** a pasted trakt URL is refused BY NAME (no trakt builder is shipped — its own gap), and dynamic collections in the UI were not attempted. The panel states the orphan story (remove stops the pass; the Plex collection follows `delete_unconfigured`) and the wholesale-replace hazard for deployments that hand-write file definitions (an overrides list REPLACES the file's while it exists). Adjudications C1–C6 in `.superpowers/sdd/p-ccui-facts.md` | M — one parser module + two endpoints, one panel, the proven overrides write | yes — create a collection from a pasted list URL | 137 (closed by the same phase — the listing the panel renders), 200 (closed — the write contract), 201 (closed — the URL-shape precedent) |
```

- [ ] **Step 4: The PR body**

Create `.superpowers/sdd/p-ccui-pr-body.md` (gitignored — used when the user opens the PR, never committed):

```markdown
## Custom collections from the web UI (roadmap rows 137 + 202)

The Collections page could preview the config-defined definitions but never
list them (row 137), and creating one meant editing YAML (row 202). This
branch ships both halves, per the adjudications in
`.superpowers/sdd/p-ccui-facts.md` (C1–C6).

### What changed

- **`GET /api/collections/definitions`** (row 137): the running config's
  `collections.definitions` with per-entry provenance (`file`/`override`,
  decided by whether the stored overrides document carries the key — the
  overrides layer replaces lists wholesale) plus `collections.libraries`.
  No Plex read, no engine run; renders on a replica.
- **`POST /api/collections/parse-source`** over the new pure
  `collections/source_urls.py`: table-driven URL→(builder, params) for the
  shipped list sources (IMDb list/watchlist, MDBList, the TMDb entity
  family, TVDb), bare ids accepted where unambiguous (a bare integer is
  read as a TMDb list id, disclosed in the display note). Every parse is
  validated through the builder's own params model and every refusal reuses
  its error string. trakt is refused by name — no trakt builder is shipped.
  Shape-only: existence is the first pass's business, and the form says so.
- **The Custom collections panel** (`CustomCollectionsPanel.tsx`): the
  listing with provenance badges; create = title + source URL + library
  checkboxes (all checked omits the key), Check via `POST /api/config/preview`
  (nothing persisted), Create via `PUT /api/config/overrides` with the shared
  overrides helpers; Remove on override rows only — create's undo, the one
  disclosed widening over row 202's filing. Removing the last override drops
  the key (the contract's revert). 422s render against the path they name.
- **The freezing hazard is a named test**: "a file-defined definition never
  enters the overrides write" — file rows are never copied into the stored
  document, which would freeze today's YAML against future edits. The
  wholesale-replace consequence (an overrides list shadows the file's) is
  disclosed in the panel and the README.
- **Docs**: the DefinitionsPanel's "Preview-all is the listing" wart notes
  retired; `deploy/README.md` gains the create-from-URL section; rows 137
  and 202 closed.

### What deliberately did not change

- No edit control (edit = remove + create), no sync_mode/sort in the form
  (defaults `sync`/`custom`), no trakt, no dynamic-collections UI — the
  row's fences.
- No new frontend dependency; `schema.py`, `overrides.py`, the builders and
  the sibling panels untouched.

### Operator acceptance

Open Collections → Custom collections. Paste an IMDb/MDBList/TMDb/TVDb list
URL, Read URL (the resolved builder appears), name it, scope it, Check, then
Create. The panel re-reads and shows the row as an override; Diff now runs
the pass that builds it. Remove undoes it; removing the last UI-created
definition drops the override entirely.

### Testing

- `pytest tests/test_collection_definitions_api.py tests/test_source_urls.py`
  — the listing (both provenance states), the parse tables (14 accepts, 9
  refusals), the endpoints.
- `npm test` (vitest) — the panel's thirteen tests, including the named
  freezing-hazard test, whole-list writes, key-removal revert, and 422
  rendering; plus the page-level mount.
- Full backend suite and `tsc --noEmit`, green (logs under `.superpowers/`).
```

- [ ] **Step 5: Full backend suite (detached), frontend suite, type check, ruff**

```bash
docker compose -p pccuit4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pccuit4-full test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pccui-full.log"
docker wait pccuit4-full
```

**Read `D:\Sites\autoposter\.superpowers\run-pccui-full.log` — never trust the wait code alone** (roadmap row 193; with `set -o pipefail` the code is pytest's, but the log is the evidence). Expected: the tail reports **0 failed** (the suite's prior total plus this branch's 30 new backend tests; several minutes). A `tests/test_worker.py` / `tests/test_pipeline.py` one-off is rows 193/195's known flake: re-run the file alone and the full suite once before treating it as this branch's.

```bash
docker rm pccuit4-full
docker compose -p pccuit4 run --name pccuit4-vitest web npm test > .superpowers/run-pccui-t4-vitest.log 2>&1
docker compose -p pccuit4 run --name pccuit4-tsc web npx tsc --noEmit > .superpowers/run-pccui-t4-tsc.log 2>&1
docker compose -p pccuit4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pccuit4-ruff test sh -c \
  "set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-pccui-t4-ruff.log"
```

Read all three logs. Expected: vitest all green; tsc silent; `All checks passed!`. Then `docker rm pccuit4-vitest pccuit4-tsc pccuit4-ruff`.

- [ ] **Step 6: Commit the docs and tear down — and stop**

```bash
git add frontend/src/pages/Collections.tsx deploy/README.md docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(collections): create-from-URL README section; rows 137 and 202 closed"
docker compose -p pccuit4 down
git status
```

`git status` must show a clean tree apart from `.superpowers/` (gitignored — the PR body stays uncommitted). **Do not push. Do not open a PR.** The user gates both; hand them `.superpowers/sdd/p-ccui-pr-body.md` when they ask.

---

## Self-review (performed while writing)

- **Spec coverage:** C1 → T1 (effective list, provenance, libraries, no engine run; both provenance states pytested; row 137 closes in T4). C2 → T2 (server-side, table-driven; the imdb two-builder host, the TMDb family disclosed, bare-id acceptance incl. the int disclosure, the trakt refusal naming the fence, params-model error strings reused via `_validated`; shape-only with the form's `SHAPE_ONLY_NOTE` saying so). C3 → T3 (title + URL + library-name checkboxes with all-checked = omitted key; sync_mode/sort not exposed; create + override-only remove with file rows control-free; the whole-list write from the override layer only, the named freezing test, the orphan sentence, the sibling-panel idioms — live ref, put + fieldErrors + re-read-outside-try). C4 → T3's Check = `POST /api/config/preview`, Save = PUT, both surfacing fieldErrors (the duplicate-title guard and the `:899` separator reservation arrive as path `collections` entries and render in the errors list verbatim). C5 → T4 (both rows closed, both wart notes rewritten, the README section). C6 → four tasks in this order, branch/merge-order rule, container + host-redirect recipes, `pccui*` projects, `p-ccui-` artifacts, user-gated PR.
- **Placeholder scan:** every code step carries complete code; every run step carries the exact command and its expected outcome; no TBDs, no "similar to Task N".
- **Type consistency:** the T1 response shape ↔ `DefinitionSummary`/`DefinitionsListingResponse` ↔ the panel's fixtures; `ParsedSource(builder, params, display_note)` ↔ the endpoint's JSON ↔ `ParsedSourceResponse`; `DEFINITIONS_PATH = "collections.definitions"` everywhere, matching `config/schema.py:739`; `parse_source`/`SourceUrlRefused` named identically in module, endpoint and both test files; the vitest fixtures' `STORED_ENTRIES` match what `documentFromConfig` seeds from `overridden_paths` + the served list (list-as-leaf, `overrides.py:146-160`).
- **Known accepted risk:** the wholesale-replace shadowing (file definitions stop building while an override list exists) is inherent to the config system, not created here; it is disclosed in the panel note, the README, and row 202's closure rather than papered over — and the alternative (copying file rows into the override) is the freezing hazard the facts forbid.
