# Phase 3g: Collection Posters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every collection this service manages a poster, matching what the tool being replaced does — a locally supplied image when one exists, otherwise the hosted default for that collection.

**Architecture:** Resolving a poster is two pure decisions (which URL, and whether a local file overrides it) and one side effect (fetch, then upload). A content hash on the `managed_collections` row makes an unchanged pass free, the same way `definition_hash` already does for the collection definition itself.

**Tech Stack:** Python 3.13, httpx, plexapi 4.18.2, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18.

## Why this is a gap rather than a deferral

Adopted collections keep whatever poster they already have, so nothing regressed at cutover — all 49 currently have one. But a collection this service *creates* has none, and the set is not static: `Oscars Winners <year>` gains a new member every ceremony as the five-year window slides. Without this, next year's collection is blank.

## Verified sources

Every URL below was fetched and returns 200. The pattern comes from `defaults/templates.yml:95` — `https://raw.githubusercontent.com/Kometa-Team/Default-Images/master/<<image>>.jpg` — with each default supplying `image`.

| Collection | `image` | Resolved path |
|---|---|---|
| Oscars Best Picture Winners | `award/oscars/best_picture_winner` | `award/oscars/best_picture_winner.jpg` |
| Oscars Best Director Winners | `award/oscars/best_director_winner` | `award/oscars/best_director_winner.jpg` |
| Oscars Winners `<year>` | `award/oscars/winner/<<key>>` | `award/oscars/winner/2026.jpg` |
| IMDb Popular / Top 250 / Lowest Rated | `chart/<<style>>/<<mapping_name_encoded>>` | `chart/color/IMDb%20Popular.jpg` |
| Age `<N>`+ … | `content_rating/cs/<<key_name>>` | `content_rating/cs/17.jpg` |
| Not Rated … | `content_rating/cs/NR` | `content_rating/cs/NR.jpg` |
| Ratings Collections | `separators/<<sep_style>>/<<separator>>` | `separators/orig/content_rating.jpg` |

`style` defaults to `color` for charts and `sep_style` to `orig` for separators. Chart names are URL-encoded, so the space in `IMDb Popular` becomes `%20`.

## Global Constraints

- **A local asset wins over the hosted image.** The configuration being replaced sets `prioritize_assets: true` with `asset_folders: true`, so a file at `<assets_root>/<library>/<collection title>/poster.{jpg,jpeg,png,webp}` takes precedence. This is how an operator overrides a poster, and silently preferring the download would overwrite their choice on every pass.
- **Only collections this service owns are touched** — the ownership label rule from phases 3a/3e is unchanged, and posters are set through the same reconcilers, so a conflicting or protected collection is never reached.
- **An unchanged pass uploads nothing.** Gate on a content hash stored on the row.
- **A failed fetch is not a failure.** A collection without a poster is a cosmetic gap; log it, leave the collection alone, and carry on with the rest. Never upload a partial or non-image body.
- **`apply_to_plex` gates every write**, as with everything else in the collections package.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it; `.reload()` is fine.
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- **No test may make a real outbound request** — an autouse fixture in `tests/conftest.py` enforces this. Use `httpx.MockTransport`.
- Baseline on branch start: 989 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.

## On the images' licensing

`Kometa-Team/Kometa` is MIT. `Kometa-Team/Default-Images` is a **separate repository with no LICENSE file** and no licence statement in its README, so the MIT grant does not literally extend to it — and in any case an MIT grant covers only what the licensor owns, which does not include the IMDb, AMPAS or Common Sense marks these images embed.

The operator's position, recorded deliberately: this is a private single-operator deployment, the images are **fetched at runtime rather than vendored into this repository**, and it replaces a tool that does exactly the same thing. That is a lighter posture than the badge assets, which *are* committed here. Record it in the module docstring so the reasoning survives; if this repository is ever published, revisit it alongside `assets/badges/PROVENANCE.md`.

---

## Task 1: Resolving which poster to use

**Files:**
- Create: `src/autoposter/collections/posters.py`
- Test: `tests/test_collection_posters.py`

**Interfaces:**
- Produces: `DEFAULT_IMAGES_BASE`; `hosted_poster_url(kind: str, key: str) -> str | None`; `local_poster_path(config, library: str, title: str) -> Path | None`.

**Notes for the implementer:**

- `kind` is one of `award_static`, `award_year`, `chart`, `content_rating`, `content_rating_other`, `separator`. `key` is the piece that varies — the image stem for `award_static`, the year for `award_year`, the collection title for `chart`, the bucket key for `content_rating`.
- URL-encode the key for charts only, with `urllib.parse.quote(key, safe="")`, so `IMDb Top 250` becomes `IMDb%20Top%20250`. Do not encode the others; they have no spaces and encoding a `/` would break `award/oscars/winner/2026`.
- An unknown `kind` returns `None` rather than guessing a URL. A wrong URL 404s and the collection silently keeps no poster, which is harder to notice than nothing happening.
- `local_poster_path` checks `<assets_root>/<library>/<title>/poster.<ext>` for `jpg`, `jpeg`, `png`, `webp` in that order, returning the first that exists. Honour `config.library_folders`: when false the layout is flat, so look for `<assets_root>/<title>.jpg` and friends instead.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_posters.py`:

```python
"""Choosing a collection's poster.

The URLs here were each fetched and confirmed to return 200; they are not
guesses. A wrong URL 404s and the collection quietly keeps no poster, which
is harder to spot than an error.
"""
import pytest

from autoposter.collections.posters import hosted_poster_url, local_poster_path

BASE = "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"


@pytest.mark.parametrize(
    "kind,key,expected",
    [
        ("award_static", "best_picture_winner", f"{BASE}/award/oscars/best_picture_winner.jpg"),
        ("award_static", "best_director_winner", f"{BASE}/award/oscars/best_director_winner.jpg"),
        ("award_year", "2026", f"{BASE}/award/oscars/winner/2026.jpg"),
        ("chart", "IMDb Popular", f"{BASE}/chart/color/IMDb%20Popular.jpg"),
        ("chart", "IMDb Top 250", f"{BASE}/chart/color/IMDb%20Top%20250.jpg"),
        ("chart", "IMDb Lowest Rated", f"{BASE}/chart/color/IMDb%20Lowest%20Rated.jpg"),
        ("content_rating", "17", f"{BASE}/content_rating/cs/17.jpg"),
        ("content_rating", "1", f"{BASE}/content_rating/cs/1.jpg"),
        ("content_rating_other", "", f"{BASE}/content_rating/cs/NR.jpg"),
        ("separator", "content_rating", f"{BASE}/separators/orig/content_rating.jpg"),
    ],
)
def test_hosted_urls_match_the_verified_paths(kind, key, expected):
    assert hosted_poster_url(kind, key) == expected


def test_only_chart_keys_are_url_encoded():
    """Encoding the award year would be harmless; encoding its slash would
    not, so the encoding is deliberately per-kind rather than blanket."""
    assert "%2F" not in hosted_poster_url("award_year", "2026")
    assert "/winner/2026.jpg" in hosted_poster_url("award_year", "2026")


def test_an_unknown_kind_yields_no_url():
    assert hosted_poster_url("something_else", "x") is None


def test_a_local_poster_is_found(tmp_path, config_factory):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / "Movies" / "IMDb Top 250"
    folder.mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(b"x")
    assert local_poster_path(config, "Movies", "IMDb Top 250") == folder / "poster.jpg"


def test_extensions_are_tried_in_order(tmp_path, config_factory):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / "Movies" / "IMDb Top 250"
    folder.mkdir(parents=True)
    (folder / "poster.png").write_bytes(b"x")
    (folder / "poster.webp").write_bytes(b"x")
    assert local_poster_path(config, "Movies", "IMDb Top 250").name == "poster.png"


def test_no_local_poster_returns_none(tmp_path, config_factory):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    assert local_poster_path(config, "Movies", "IMDb Top 250") is None


def test_the_flat_layout_is_honoured(tmp_path, config_factory):
    config = config_factory(assets_root=str(tmp_path), library_folders=False)
    (tmp_path / "IMDb Top 250.jpg").write_bytes(b"x")
    assert local_poster_path(config, "Movies", "IMDb Top 250").name == "IMDb Top 250.jpg"
```

`config_factory` is a small fixture the implementer adds to `tests/conftest.py`, returning a config with the given overrides applied to the example config — follow whatever pattern the existing config fixtures use rather than inventing a new one.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_posters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autoposter.collections.posters'`

- [ ] **Step 3: Implement, run, ruff and commit**

```bash
rtk proxy python -m pytest tests/test_collection_posters.py -v
rtk proxy ruff check src tests
git add src/autoposter/collections/posters.py tests/test_collection_posters.py tests/conftest.py
git commit --no-gpg-sign -m "Resolve which poster a collection should use"
```

---

## Task 2: Recording the poster we set

**Files:**
- Modify: `src/autoposter/db/models.py`
- Create: one Alembic migration
- Test: `tests/test_collection_poster_state.py`

**Interfaces:**
- Produces: `ManagedCollection.poster_sha256: str | None` (`String(64)`, nullable).

**Notes for the implementer:**

- Nullable with no server default. `NULL` means "we have never set this collection's poster", which is exactly right for every existing row: adopted collections keep the poster they already have, and we only replace it once we have fetched something to replace it with.
- `managed_collections` has rows on a deployed instance, so follow the clean-database migration sequence — `tests/conftest.py` calls `create_all` against the same database, so autogenerating after a test run yields an empty `pass` body that silently creates nothing. Reset (`docker compose down`, **without** `-v`), `alembic upgrade head`, autogenerate, then confirm the body contains a real `op.add_column`. `tests/test_migrations.py` guards this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_poster_state.py` covering: a new row has `poster_sha256` of `None`; the value round-trips; and two rows in different libraries with the same title keep independent values (the `(library, title)` uniqueness already allows this and it should stay true).

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_poster_state.py -v`
Expected: FAIL with `TypeError: 'poster_sha256' is an invalid keyword argument`

- [ ] **Step 3: Add the column, generate the migration, test and commit**

```bash
rtk proxy python -m pytest tests/test_collection_poster_state.py tests/test_migrations.py -v
rtk proxy ruff check src tests
git add src/autoposter/db/models.py alembic tests/test_collection_poster_state.py
git commit --no-gpg-sign -m "Record which poster each managed collection carries"
```

---

## Task 3: Fetching and applying

**Files:**
- Modify: `src/autoposter/collections/posters.py`
- Test: `tests/test_collection_poster_apply.py`

**Interfaces:**
- Consumes: `hosted_poster_url`, `local_poster_path`, `ManagedCollection`.
- Produces: `async fetch_poster(http, url) -> bytes | None`; `async apply_poster(session, http, config, collection, record, library, kind, key, dry_run=True) -> str | None` returning a description of what it did, or `None` when there was nothing to do.

**Notes for the implementer:**

- Resolution order: local file first, hosted URL second, nothing third. A local file is read directly rather than fetched.
- **Validate what came back before uploading it.** A 200 with an HTML error page is still a 200; check the bytes actually decode as an image (open with Pillow, which is already a dependency) and skip if not. Uploading a rendered error page as a collection poster is a worse outcome than no poster.
- Hash the bytes, compare with `record.poster_sha256`, and do nothing when they match. That is what makes a repeat pass free.
- `uploadPoster(filepath=...)` — plexapi takes a path, not bytes, so write to a `NamedTemporaryFile` and clean up on both the success and failure paths, exactly as `plex/artwork.py` already does. Do not use its `url=` form: that makes the Plex server fetch the image itself, which means we neither see nor hash what actually landed.
- Under `dry_run`, resolve and fetch and report, but neither upload nor record. Fetching under a dry run is deliberate — it is how the report can tell you the source is reachable.
- Any failure returns a message and leaves the collection untouched.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_poster_apply.py` covering: a hosted poster is fetched and uploaded and the hash recorded; a local file takes precedence and no HTTP request is made; an unchanged hash uploads nothing on a second pass; a changed local file re-uploads; a 404 leaves the collection untouched and reports it; a 200 whose body is not an image is rejected without uploading; dry run uploads and records nothing; and the temporary file is removed on both paths.

Use `httpx.MockTransport`, `tmp_path` for local files, and a small real JPEG built with Pillow for the "valid image" case so the validation is exercised against real bytes rather than a mock.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_poster_apply.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_poster'`

- [ ] **Step 3: Implement, run, ruff and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/collections/posters.py tests/test_collection_poster_apply.py
git commit --no-gpg-sign -m "Fetch and apply collection posters, skipping unchanged ones"
```

---

## Task 4: Wiring and configuration

**Files:**
- Modify: `src/autoposter/collections/reconcile.py`
- Modify: `src/autoposter/collections/lists.py`
- Modify: `src/autoposter/collections/sources.py`
- Modify: `src/autoposter/config/schema.py`
- Modify: `config/autoposter.example.yaml`
- Modify: `deploy/README.md`
- Test: `tests/test_collection_poster_wiring.py`

**Config to add**, under `collections:`:

```yaml
  posters: true # give managed collections a poster
```

**Notes for the implementer:**

- Each reconciler already knows which family it is handling, so it knows the `kind` and `key` to pass: the Common Sense reconciler knows the bucket key (and that the catch-all is `content_rating_other`), the separator path knows it is a `separator`, and `sources.py` knows whether a list collection is a chart or an award, and for the year collections what the year is.
- Call it **after** the collection exists and has been reconciled — a poster on a collection that failed to create is not a thing.
- A conflicting or protected collection must never reach this, which falls out of calling it after `resolve_collision` rather than before. There should be a test asserting that.
- Respect `collections.posters` and the existing `apply_to_plex`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_poster_wiring.py` covering: a smart collection gets its bucket's poster; the catch-all gets `NR`; a chart collection gets its chart poster; an Oscars year collection gets that year's; the separator gets the separator image; **a protected or unlabelled collection never has a poster applied**; and `posters: false` disables all of it.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_poster_wiring.py -v`
Expected: FAIL — the reconcilers do not yet apply posters

- [ ] **Step 3: Implement and run the full suite**

Run: `rtk proxy python -m pytest tests/ -v`
Expected: PASS — 989 baseline plus roughly 40 new tests, 5 skipped

- [ ] **Step 4: Document it**

In `deploy/README.md`: that managed collections get a poster; that a file at `<assets_root>/<library>/<collection title>/poster.jpg` overrides the hosted default and is how to supply your own; that posters are fetched at runtime from Kometa's Default-Images repository and never vendored here; and that a failed fetch leaves the collection alone rather than failing the pass.

- [ ] **Step 5: Run ruff and commit**

```bash
rtk proxy ruff check src tests
git add src/autoposter config/autoposter.example.yaml deploy/README.md tests
git commit --no-gpg-sign -m "Apply posters to the collections this service manages"
```

---

## Deferred

- **Orphaned Plex uploads.** Not reapable: the Plex API exposes no way to delete a specific uploaded poster, and the select-delete-reselect workaround was tested and does not remove the file. See the spec's §6a.
- **Backgrounds for collections.** The tool being replaced sets only posters for these defaults; nothing to match.
