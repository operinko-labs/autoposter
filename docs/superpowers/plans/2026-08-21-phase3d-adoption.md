# Phase 3d: Adoption Run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the service take over an existing library without re-rendering any of it — walk Plex and `/assets`, record what already exists, and make the first real pass a no-op for everything unchanged.

**Architecture:** Adoption walks each Plex library, creates a `media_items` row per item and a `renders` row per artifact already on disk, hashing the file rather than resolving anything from the artwork providers. Those rows are marked `adopted`, and the render pipeline learns to short-circuit an adopted render *before* it resolves any provider — comparing the same fingerprint the pipeline normally computes, minus the one input adoption cannot know.

**Tech Stack:** Python 3.13, plexapi 4.18.2, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18, Alembic.

## Global Constraints

- **The specification is `docs/superpowers/specs/2026-08-20-autoposter-design.md` §5, "One-time adoption (first boot)".** It requires: walk Plex and `/assets`, build `media_items`/`renders`, hash existing bases, compute fingerprints, and produce zero re-renders. It also states plainly that **adoption resolves nothing from the artwork providers** — it hashes what is already on disk.
- **Adoption never writes to Plex and never writes an image.** It only reads Plex, reads the asset tree, and writes database rows. Nothing in this phase uploads, renders, moves or deletes anything.
- **Dry run is the default.** `--dry-run` produces the adoption report described in the spec, for review before cutover.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it; `.reload()` is fine.
- **Nothing blocks the event loop.** Hashing thousands of files and walking Plex are both blocking; they go through `asyncio.to_thread`.
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- **No test may make a real outbound request** — an autouse fixture in `tests/conftest.py` enforces this. Use `tmp_path` for asset trees.
- Baseline on branch start: 748 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.

## The problem this phase has to solve

`compute_fingerprint` takes `source_url` among its inputs, and `render_artifact` skips work only when the stored fingerprint equals a freshly computed one. On a normal pass that `source_url` comes from the provider ladder. **Adoption cannot know it** — the spec forbids resolving providers, and the file on disk carries no record of where its source came from.

So a naively adopted render would mismatch on the very first pass and re-render all ~16,000 artifacts, which is precisely the outcome adoption exists to avoid.

The fix is to compare the same fingerprint minus the one unknown input. An adopted render stores `compute_fingerprint(version, art_kind, **None**, base_sha, text_inputs, asset_hashes)`, and `render_artifact` computes the identical value — before touching any provider — whenever `render.adopted` is set. If they match and the file is still on disk, the pass is a no-op costing one hash and zero requests. If anything else changed (the title, the config version, the overlay or font file), they differ, adoption stops applying, and a normal render happens.

**The two computations must not be able to drift apart.** Extract the input-gathering into one function that both adoption and `render_artifact` call. Two implementations of "what goes into this fingerprint" would silently stop agreeing, and the failure mode is a full re-render of the library.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/autoposter/adopt/walk.py` | Walk Plex and the asset tree, build the rows, produce the report. |
| `src/autoposter/adopt/__main__.py` | `python -m autoposter.adopt` entry point. |
| `src/autoposter/render/pipeline.py` | Shared fingerprint-input gathering, and the adopted short-circuit. |
| `src/autoposter/db/models.py` | `Render.adopted`. |
| `src/autoposter/config/schema.py` | The `adopt` config section. |

---

## Task 1: The adopted flag

**Files:**
- Modify: `src/autoposter/db/models.py`
- Create: one Alembic migration
- Test: `tests/test_adopt_model.py`

**Interfaces:**
- Produces: `Render.adopted: bool` (default `False`, `server_default` false).

**Notes for the implementer:**

- `renders` has rows on any deployed instance, so this needs a `server_default` — `ADD COLUMN ... NOT NULL` without one fails outright against a non-empty table. `tests/test_migrations.py` already contains a regression test for exactly this, added when it happened before; make sure it still passes.
- Follow the clean-database migration sequence: `tests/conftest.py` calls `create_all` against the same database, so autogenerating after a test run yields an empty `pass` body that silently creates nothing on deploy. Reset (`docker compose down`, **without** `-v`), `alembic upgrade head`, then autogenerate, then confirm the body contains a real `op.add_column`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_adopt_model.py`:

```python
"""The adopted marker on renders."""
from sqlalchemy import select

from autoposter.db.models import MediaItem, Render


async def _render(session, **kw):
    item = MediaItem(kind="movie", rating_key="1", title="X", library="Movies")
    session.add(item)
    await session.flush()
    render = Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg", **kw)
    session.add(render)
    await session.flush()
    return render


async def test_renders_are_not_adopted_by_default(session):
    render = await _render(session)
    assert render.adopted is False


async def test_the_adopted_marker_round_trips(session):
    render = await _render(session, adopted=True)
    loaded = (
        await session.execute(select(Render).where(Render.id == render.id))
    ).scalar_one()
    assert loaded.adopted is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_adopt_model.py -v`
Expected: FAIL with `TypeError: 'adopted' is an invalid keyword argument for Render`

- [ ] **Step 3: Add the column**

In `src/autoposter/db/models.py`, inside `class Render`:

```python
    # Set by the adoption run for artwork that already existed on disk when
    # this service took over. Such a render cannot know the source_url its
    # base came from, so the pipeline compares its fingerprint without that
    # input -- see render_artifact. Cleared as soon as anything genuinely
    # changes and a real render happens.
    adopted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
```

Import `text` from `sqlalchemy` if it is not already imported. The `server_default` is not optional: without it this migration fails against the populated `renders` table a deployed instance already has.

- [ ] **Step 4: Generate the migration against a clean database, apply and commit**

```bash
docker compose down && docker compose up -d postgres && sleep 8
export AUTOPOSTER_DATABASE_URL=postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter
rtk proxy python -m alembic upgrade head
rtk proxy python -m alembic revision --autogenerate -m "adopted renders"
rtk proxy python -m alembic upgrade head
rtk proxy python -m pytest tests/test_adopt_model.py tests/test_migrations.py -v
rtk proxy ruff check src tests
git add src/autoposter/db/models.py alembic tests/test_adopt_model.py
git commit --no-gpg-sign -m "Mark renders adopted from an existing library"
```

---

## Task 2: Shared fingerprint inputs and the adopted short-circuit

Done before the walk, so the walk has one correct function to call.

**Files:**
- Modify: `src/autoposter/render/pipeline.py`
- Test: `tests/test_adopt_fingerprint.py`

**Interfaces:**
- Produces: `async gather_fingerprint_inputs(config, item, art_kind, base_sha256: str | None) -> tuple[list[str], list[str]]` returning `(text_inputs, asset_hashes)`; and `def adopted_fingerprint(config, art_kind, base_sha256, text_inputs, asset_hashes) -> str`, which is `compute_fingerprint` with `source_url=None`.

**Notes for the implementer:**

- `render_artifact` currently builds `text_inputs` and `asset_hashes` inline before calling `compute_fingerprint`. Extract exactly that logic — do not reimplement it — and have `render_artifact` call the extracted function. The extraction must be behaviour-preserving: every existing render test must still pass unchanged, which is the evidence that it is.
- Add the short-circuit to `render_artifact`, **before any provider resolution**, so an adopted item costs zero requests:

  ```
  if render.adopted and render.base_sha256:
      text_inputs, asset_hashes = await gather_fingerprint_inputs(...)
      candidate = adopted_fingerprint(config, art_kind, render.base_sha256, text_inputs, asset_hashes)
      if candidate == render.fingerprint and await asyncio.to_thread(target.exists):
          render.status = "rendered"
          render.detail = "adopted"
          await session.commit()
          return render
  ```
- When the comparison fails, fall through to the normal path and set `render.adopted = False` once a real render happens — the adoption no longer describes reality.
- The short-circuit must not fire when the file is gone: an adopted row whose asset was deleted has to be re-rendered.

- [ ] **Step 1: Write the failing test**

Create `tests/test_adopt_fingerprint.py` covering:
- `adopted_fingerprint` equals `compute_fingerprint` with `source_url=None` for the same other inputs;
- it differs when `base_sha256` differs, when a text input differs, and when an asset hash differs;
- an adopted render whose stored fingerprint matches is skipped by `render_artifact` **without any provider being consulted** — assert on a provider double that records calls and expect zero;
- an adopted render whose asset file is missing is *not* skipped;
- an adopted render whose text inputs changed is not skipped, and `adopted` is cleared after the real render;
- a non-adopted render is unaffected.

Follow the fake/provider style already used in `tests/test_pipeline.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_adopt_fingerprint.py -v`
Expected: FAIL with `ImportError: cannot import name 'adopted_fingerprint'`

- [ ] **Step 3: Extract, implement, and confirm nothing regressed**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — the full existing suite plus the new tests. Any pre-existing render test that changes behaviour means the extraction was not faithful; fix the extraction rather than the test.

- [ ] **Step 4: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter/render/pipeline.py tests/test_adopt_fingerprint.py
git commit --no-gpg-sign -m "Skip adopted renders without consulting any provider"
```

---

## Task 3: The adoption walk

**Files:**
- Create: `src/autoposter/adopt/__init__.py` (empty)
- Create: `src/autoposter/adopt/walk.py`
- Test: `tests/test_adopt_walk.py`

**Interfaces:**
- Consumes: `gather_fingerprint_inputs`, `adopted_fingerprint` from `pipeline`; `asset_path` from `render.naming`; `ART_KINDS_FOR` from `pipeline`.
- Produces: the frozen dataclass `AdoptionReport(items: int, renders: int, missing_assets: int, skipped: int, by_kind: dict[str, int])`; `async adopt_library(session, config, section, dry_run: bool = True) -> AdoptionReport`.

**Notes for the implementer:**

- Walk with `section.all()`, which returns items with their guids already populated in one call — measured at 1,954 movies in 2.8 seconds against the production server. For shows, also walk seasons and episodes, since they have their own artifacts.
- For each item, upsert a `media_items` row (there may already be one from a webhook; do not duplicate it — `rating_key` is unique).
- For each art kind the item implies, compute the expected asset path and check whether a file is there. **If no file exists, record it in `missing_assets` and create no render row** — a render row pointing at a nonexistent file would claim the artwork is fine when it is not.
- Hash existing files with `hashlib.sha256`, off the event loop. This is the expensive part of the run — around 18,000 files — so hash inside `asyncio.to_thread` and report progress periodically.
- **Never overwrite an existing render row that is not adopted.** If a render already exists with a real fingerprint, that item has been processed properly and adoption must leave it alone. Count it in `skipped`.
- Set `status="rendered"`, `adopted=True`, `base_sha256`, `asset_path`, and `fingerprint = adopted_fingerprint(...)`.
- `dry_run=True` computes and reports everything, and writes no rows.

- [ ] **Step 1: Write the failing test**

Create `tests/test_adopt_walk.py` covering:
- a movie whose poster exists on disk gets a `media_items` row and an adopted render with the file's real hash;
- a movie whose asset file is absent gets no render row and increments `missing_assets`;
- an item that already has a non-adopted render is left untouched and counted in `skipped`;
- running twice is idempotent — the second run creates nothing new;
- `dry_run=True` writes nothing at all but returns the same counts;
- the report's `by_kind` counts are right;
- hashing happens off the event loop.

Use `tmp_path` as `assets_root` and write real small files so the hashes are real. Use a fake section in the style of `tests/test_collection_resolve.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_adopt_walk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.adopt'`

- [ ] **Step 3: Implement, test and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/adopt tests/test_adopt_walk.py
git commit --no-gpg-sign -m "Adopt an existing library without re-rendering it"
```

---

## Task 4: Entry point, configuration and documentation

**Files:**
- Modify: `src/autoposter/config/schema.py`
- Modify: `config/autoposter.example.yaml`
- Create: `src/autoposter/adopt/__main__.py`
- Modify: `deploy/README.md`
- Test: `tests/test_adopt_config.py`

**Config to add:**

```yaml
adopt:
  apply: false # dry run by default: produce the report, write nothing
  libraries: # Plex library names to adopt
    - Movies
    - TV Shows
```

**Notes for the implementer:**

- `python -m autoposter.adopt` prints the report per library and a total. With `apply: false` it writes nothing, which is the mode the spec calls for before cutover.
- Follow `src/autoposter/collections/__main__.py` for how to build the config, engine, session factory and Plex server — reuse those helpers rather than inventing new ones.
- Connecting to Plex is blocking; keep it off the event loop.
- There is a genuine decision to document rather than bury, in `deploy/README.md`:

  **Adoption deliberately does not mark existing Plex artwork as already badged.** The spec's sketch suggested it, and it would avoid an upload for every item — but the overlays currently on the server were produced by the tool being replaced, not by this one. Leaving them marked as current would mean Plex keeps those overlays until each item happens to change for some other reason, which could be months. Instead, adopted items keep their expensive *base* artwork (that is what adoption is for) and re-badge on their next pass, which is comparatively cheap and produces artwork this service actually owns.

  Say that plainly in the docs, and note that the drift sweep's `drift_batch_size` throttles how fast that re-badging works through the library.

- [ ] **Step 1: Write the failing test**

Create `tests/test_adopt_config.py` asserting `adopt.apply` defaults to `False` and `adopt.libraries` defaults to `["Movies", "TV Shows"]`, asserting against the schema defaults directly (`AdoptConfig()`), not values loaded from the example YAML — a default changed to match the YAML would otherwise still pass.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_adopt_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'adopt'`

- [ ] **Step 3: Add the config and entry point, then run the suite**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — 748 baseline plus roughly 25 new tests, 5 skipped

`tests/test_example_config_matches_schema.py` asserts every key in the example config exists in the schema; the new section must satisfy it.

- [ ] **Step 4: Document the cutover**

In `deploy/README.md`, add a cutover section: run the adoption report first and read it, check `missing_assets` is what you expect, then set `adopt.apply: true` and run again, then repoint the Radarr/Sonarr webhooks and stop the old tools. Include the badging decision above.

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter config/autoposter.example.yaml deploy/README.md tests
git commit --no-gpg-sign -m "Add the adoption entry point and cutover documentation"
```

---

## Deferred

- **Arr sync.** Still blocked: it needs outbound Radarr and Sonarr access, and the service has only inbound webhook intake — no Arr client, and no `radarr`/`sonarr` entry in the config schema. It needs base URLs and API keys from the operator.
- **Adopting the existing Kometa collections.** All 51 carry the `Kometa` label rather than ours. Relabelling them in place versus creating ours alongside touches 51 live collections and is the operator's decision.
