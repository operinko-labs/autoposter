# Overlay Families — sub-phase C1 (the selection seam + the first families) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** give the overlay engine the *selection half* it has never had — a per-definition `condition:` evaluated per item through `collections/filters.py`'s oracle-proven parser and evaluator over a NEW overlay-side item view — and ship the first row-100 families (`direct_play` and the six content-rating regionals) on top of it, with `badge_fingerprint` extended to cover the per-item match outcome without moving the gate-off digest by one bit.

**Architecture:** three moving parts. (1) `OverlayDefinition` gains one field, `condition:`, holding the raw filter mapping; it is parsed and refused **at config load** by `overlays/selection.py::parse_condition`, which delegates to `collections.filters.parse_filters` (no second dialect — adjudication A3) and then narrows the vocabulary to what the overlay view can actually supply. (2) `overlays/selection.py::OverlayItemView` implements `filters.ItemView` over the `(MediaInfo, ItemFacts, plex_item)` triple `render/pipeline.py::apply_badges` already holds — deliberately **not** `collections/filter_values.py::PlexItemView`, whose accessors are bound by a "never cost a Plex request" rule the badge path has already paid past (`media_info_from_plex` reloads and walks `part.streams` before this code runs). (3) `badges/compose.py::badge_fingerprint` gains an `outcomes` argument folded in **beside** the definitions digest under the same non-empty guard, so the pinned empty-case literal is untouched, a config of unconditioned definitions is bit-identical to what it produced before this phase, and an item whose *aspect changed* re-renders because its match outcomes moved.

**Tech Stack:** Python 3.13, Pydantic v2, `collections/filters.py` (imported, never edited), Pillow, pytest, Docker Compose for the suite.

---

## Branch and cut point

- Branch name: **`feat/overlay-families`**.
- **Cut from `origin/main` after a fetch, verified with CONTENT probes, never a sha probe.** Re-verify the tip at execution time; do not trust any sha recorded in this document. (`origin/main` was `603340d` when this plan was written; #138 is open and touches `render/pipeline.py::process_item`, **not** `apply_badges` — adjudication A13 says branch now and re-verify the merge tree at PR time.)

```bash
git fetch origin
git cat-file -e origin/main:src/autoposter/overlays/schema.py
git cat-file -e origin/main:src/autoposter/overlays/sources.py
git cat-file -e origin/main:src/autoposter/badges/compose.py
git cat-file -e origin/main:src/autoposter/badges/values.py
git cat-file -e origin/main:src/autoposter/render/pipeline.py
git cat-file -e origin/main:src/autoposter/collections/filters.py
git cat-file -e origin/main:src/autoposter/config/schema.py
git cat-file -e origin/main:assets/badges/MANIFEST.sha256
git cat-file -e origin/main:assets/badges/PROVENANCE.md
git cat-file -e origin/main:tests/test_overlay_entrypoint.py
git cat-file -e origin/main:tests/test_overlay_engine_golden.py
git show origin/main:src/autoposter/overlays/schema.py | grep -q 'model_config = {"extra": "forbid", "frozen": True}' && echo SCHEMA-FORBID-PRESENT
git show origin/main:src/autoposter/overlays/schema.py | grep -qv 'condition' && echo CONDITION-FIELD-ABSENT
git show origin/main:src/autoposter/badges/compose.py | grep -q 'def badge_fingerprint(' && echo FINGERPRINT-PRESENT
git show origin/main:src/autoposter/badges/compose.py | grep -q 'def _definitions_digest(' && echo DEFINITIONS-DIGEST-PRESENT
git show origin/main:src/autoposter/render/pipeline.py | grep -q 'async def apply_badges' && echo APPLY-BADGES-PRESENT
git show origin/main:src/autoposter/collections/filters.py | grep -q 'def parse_filters(' && echo PARSE-FILTERS-PRESENT
git show origin/main:src/autoposter/collections/filters.py | grep -q 'FILTERABLE_ATTRIBUTES: tuple\[str, \.\.\.\]' && echo FILTERABLE-SET-PRESENT
git show origin/main:src/autoposter/collections/filters.py | grep -q '"content_rating", "tag", _BOTH, "listing"' && echo CONTENT-RATING-ROW-PRESENT
git show origin/main:src/autoposter/collections/filters.py | grep -q '"resolution", "tag", ("movie",), "listing"' && echo RESOLUTION-ROW-PRESENT
git show origin/main:tests/test_overlay_entrypoint.py | grep -q '576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd' && echo FINGERPRINT-STORM-PIN-PRESENT
git show origin/main:tests/test_overlay_engine_golden.py | grep -q 'POSTER_PIXELS_SHA = "fc8793' && echo GOLDEN-GATE-PRESENT
git checkout -b feat/overlay-families origin/main
git rev-parse HEAD
```

All eleven `cat-file` probes must succeed and all ten markers must print. If any probe fails, **STOP and report** — do not cut from a stale ref, and do not "fix" a marker by editing the file it probes.

---

## Global Constraints

Every task's requirements implicitly include this section.

1. **`src/autoposter/collections/filters.py` is NEVER edited.** It is reused **by import only** (`parse_filters`, `evaluate`, `predicates`, `FilterGroup`, `FilterPredicate`, `FilterAttribute`, `BY_NAME`, `FILTERABLE_ATTRIBUTES`). If a task appears to need a new row or a new operator class in that table, that is an **adjudication**, not an edit: **STOP and report it** (see "Adjudication A14 — `versions`" below, which is exactly this case and is already raised).
2. **`src/autoposter/render/compositor.py` and `src/autoposter/badges/spec.py` are never touched.** Badges are Pillow, not ImageMagick argv; nothing in this sub-phase reaches the base composite.
3. **The five parity pin files are never edited:** `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_compose.py`, `tests/test_badge_parity.py`. Changing an assertion in any of them is a phase failure; **STOP and report** rather than editing one.
4. **The golden gate is never edited.** `tests/test_overlay_engine_golden.py`'s `POSTER_PIXELS_SHA` and `TITLE_CARD_PIXELS_SHA` must keep passing UNMODIFIED. If either drifts, **STOP and report** — do not re-record it.
5. **The storm pin EXTENDS, never weakens.** `tests/test_overlay_entrypoint.py::test_the_gate_off_fingerprint_is_pinned_byte_identical` pins the literal `576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd`; that test and that literal stay **UNMODIFIED**. Two new pins are ADDED beside it (T1): the **unchanged-item-unchanged-digest** pin (same item, same definitions, same outcomes ⇒ identical digest, and identical to the pre-seam 5-argument call) and the **changed-match-re-renders** pin (one item attribute moves ⇒ the digest moves). A third guard rides with T2: vendoring art must not move `manifest_sha()` — see Constraint 12.
6. **Gate-off stays byte-identical through the REAL entry point, and now means three things.** Proven through `render.pipeline.apply_badges`, not through `badges/compose.py::compose` alone: **gate-off** (no definitions), **gate-on-but-unmatched** (a definition with a `condition:` this item does not satisfy) and **gate-on-and-matched** must produce, for the first two, the same drawn PIXELS. "Byte-identical" is measured as the entry-point suite already measures it — `_sha()` over the decoded RGB array, not over the WebP file, because the fingerprint legitimately lands in EXIF provenance and a differently-configured item legitimately stamps a different one.
7. **An unmatched definition costs no I/O.** Selection runs before the per-definition image resolution loop, so a definition this item does not match is never fetched, never downloaded and never decoded. Pinned.
8. **The regionals source PLEX's `contentRating`, never `item_facts.content_rating` (adjudication A5).** The latter is MDBList's Common Sense age rating (`facts/mdblist.py::parse_content_rating`); Kometa's alias buckets are Plex agent spellings (`gb/U`, `no/A`, `TV-Y`) Common Sense never emits, and `collections/filters.py`'s own `content_rating` row says so verbatim ("This is Plex's own certification and deliberately NOT `ItemFacts.content_rating`"). The divergence is pinned by a test against an item carrying BOTH values, with different answers.
9. **Agreement with the collections oracle wherever attributes overlap.** For every attribute the overlay view and `collections/filter_values.py::PlexItemView` both supply, a test asserts the two views answer *identically* on the same item — including a multi-version item. A divergence here would be the "same name, different filter" class `filters.py`'s own module docstring rules out.
10. **Refuse loudly at config load, never match-nothing at render time.** An unknown attribute, an operator its type does not carry, an attribute the overlay view cannot supply, an empty `condition:`, or an unknown family name is a **`ValueError` at config load** naming the offending key and listing what IS available. `overlays/schema.py`'s own docstring law ("an operator who writes `queue:` has to be told it does nothing") extends here: a family that silently matches nothing is indistinguishable from a family that is off.
11. **RED before GREEN on every behavioural step.** Write the failing test, run it, confirm it fails for the stated reason, then implement. Where a property is inherited from an already-tested generic mechanism and cannot RED, prove it **falsifiable by deliberate mutation** — write it, confirm PASS, temporarily break the mechanism, confirm FAIL, revert, confirm PASS — and record the mutation in the task report.
12. **Vendored art must not move `manifest_sha()`.** `badges/compose.py::manifest_sha()` hashes `assets/badges/MANIFEST.sha256` and that hash is in EVERY item's fingerprint. Adding 99 lines to that file would re-fingerprint every already-badged item in the library — the exact mass re-render this project already paid for once. The C1 art therefore gets its **own** manifest, `assets/badges/OVERLAY-MANIFEST.sha256`, which nothing folds into a fingerprint, and a test pins that `MANIFEST.sha256` still lists exactly its 513 built-in entries and names no `cr/` path.
13. **T1 MEASURES the baseline.** The full-suite pass count, the golden gate, the storm-pin literal and the pre-seam 5-argument fingerprint literal are all captured on the FRESHLY CUT branch, before any code change — not assumed from a prior phase's report and not predicted by this document.
14. **Container discipline.** One compose project per task (**`povc1`, `povc2`, `povc3`**), always with the `.superpowers/isolated-db.yml` overlay. **`--rm` is never used anywhere** — every run is started detached (`run -d --name`), waited on with a **foreground** `docker wait`, read back with `docker cp` into `.superpowers/`, then `docker rm`. Teardown is `docker compose -p <project> down` — **never** `down -v`.
15. **No network in tests.** `tests/conftest.py`'s `no_outbound_network` autouse fixture is not bypassed. The vendoring step's `docker cp` out of the pinned Kometa image is a one-off developer action outside the suite, not a test.
16. **Commits** are conventional, staged **by name** (never `git add -A`), and carry **no AI attribution** of any kind — no `Co-Authored-By`, no tool mention. Same for the PR body. Add `--no-gpg-sign` if signing would otherwise block or time out.
17. **Read-only outside the named files.** At the end of each task, `git diff --stat <task-start-sha> -- src/ tests/ assets/ docs/` must list only files named in that task's **Files** block.
18. **Artifacts** (logs, captured literals, transcription notes) go to `.superpowers/p-overlay-c1-*` and are not committed unless a step says so.

---

## Adjudication A14 — `versions` cannot ship in C1 without editing `collections/filters.py`

**Raised by this plan; NOT resolved by it. Read this before executing T2.**

`p-overlay-c-facts.md` C1.1 scopes C1 to `direct_play` + `versions` + the six content-rating regionals. The code says `versions` is not expressible under Constraint 1:

- Kometa selects the family with `plex_search: {all: {duplicate: true}}` (`p-overlay-datasources-probe.md` §4.8).
- `collections/filters.py`'s `duplicate` row is `("duplicate", "bool", ("movie",), "search-only", ..., filterable=False)`. `_split_key` (`filters.py:1789-1795`) **refuses** a non-`filterable` attribute in a `filters:`-dialect parse, by name, with a message pointing at `plex_search`.
- There is no `versions` row at all. `filters.py`'s own comment at `:1776-1781` names `versions` in the list of "the first filter-only attribute (`aspect`, `height`, `versions`, `summary`, ... — 44 of them, roadmap row 96's residue)" that "will arrive under a table row".

So the honest fix is **one new row in `FILTER_ATTRIBUTES`** — and that row is an edit to `collections/filters.py`, which Global Constraint 1 forbids and instructs be raised instead. The ready-to-rule recipe, for whoever adjudicates:

```python
    FilterAttribute(
        "versions", "int", _BOTH, "listing",
        "How many `<Media>` versions the item carries -- Kometa's `versions` "
        "filter counts media items (one of the 44 filter names with no Plex "
        "search field; the SEARCH-side spelling is the separate `duplicate` "
        "boolean row above). `listing`: the section listing carries `<Media>` "
        "in full and un-truncated (9a's probe measured the per-item histogram "
        "{1: 1905, 2: 46, 3: 4}), which is the same read the `resolution` row "
        "already relies on.",
        search_field=None, show_search_field=None,
        search_kinds=(), filterable=True,
    ),
```

It would also be the first row to exercise `_split_key`'s deliberately-written-but-unreachable `searching and not attribute.searchable` branch (`filters.py:1782-1788`), and it moves three column totals asserted in `tests/test_collection_filters.py` (int 3→4, listing 12→13, both-kinds 20→21 with `search_kinds` unchanged at 24/8/1 minus one row that is now unsearchable — the implementer re-derives the exact numbers from the table, never from this paragraph).

**Until that is ruled on, T2 ships TWO families, not three.** `versions` is not half-built: no `MediaInfo.version_count` field, no `versions.png`, no definition. Building the data half with no way to select on it would be dead code. T3 records the fence.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `src/autoposter/overlays/selection.py` | **NEW.** The overlay-side `ItemView`, the vocabulary narrowing, `parse_condition`, the compiled-condition cache, and `select()` | T1 |
| `src/autoposter/overlays/schema.py` | `OverlayDefinition.condition` field + its load-time refusal | T1 |
| `src/autoposter/badges/compose.py` | `badge_fingerprint(..., outcomes=None)` + `_outcomes_digest` | T1 |
| `src/autoposter/render/pipeline.py` | `apply_badges` builds the view, selects, folds outcomes into the fingerprint, resolves images for matched definitions only | T1 |
| `tests/test_overlay_selection.py` | **NEW.** The view, the vocabulary, the refusals, the oracle-agreement pins | T1 |
| `tests/test_overlay_schema.py` | `condition:` accept/refuse pins | T1 |
| `tests/test_overlay_entrypoint.py` | the extended storm pins; the three-way gate law through the real `apply_badges`; the no-I/O-for-unmatched pin | T1, T2 |
| `assets/badges/images/cr/*.png` | **NEW.** 98 vendored PNGs from the pinned Kometa image | T2 |
| `assets/badges/images/Direct-Play.png` | **NEW.** 1 vendored PNG | T2 |
| `assets/badges/OVERLAY-MANIFEST.sha256` | **NEW.** Checksums for the 99 files above, deliberately NOT folded into `manifest_sha()` | T2 |
| `assets/badges/PROVENANCE.md` | the C1 vendoring recorded, and the "deliberately not taken" line corrected | T2 |
| `src/autoposter/overlays/families.py` | **NEW.** `direct_play` and the six `content_rating_*` regionals as flat `OverlayDefinition` lists | T2 |
| `src/autoposter/config/schema.py` | `BadgesConfig.families` + `all_definitions()` + the unknown-family refusal | T2 |
| `tests/test_overlay_families.py` | **NEW.** per-family fires/silent pins, the Plex-vs-Common-Sense divergence pin, the asset-existence pin, the manifest-unmoved pin | T2 |
| `tests/test_ci_path_filters.py` | `OVERLAY-MANIFEST.sha256` added to `VERIFIED_NON_SOURCE` | T2 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | row 100's sub-phase ledger + the TVDb→TMDb correction | T3 |
| `.superpowers/sdd/progress.md` | the sub-phase wrap entry | T3 |

**Never modified by any task:** `src/autoposter/collections/filters.py`, `src/autoposter/collections/filter_values.py`, `src/autoposter/render/compositor.py`, `src/autoposter/badges/spec.py`, `src/autoposter/badges/draw.py`, `src/autoposter/badges/geometry.py`, `src/autoposter/overlays/render.py`, `src/autoposter/overlays/builtin.py`, `assets/badges/MANIFEST.sha256`, `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_compose.py`, `tests/test_badge_parity.py`, `tests/test_overlay_engine_golden.py`, `tests/test_collection_filters.py`, `tests/test_collection_filter_values.py`, `tests/test_collection_filter_oracle.py`.

---

## Interfaces produced by this phase

```python
# src/autoposter/overlays/selection.py
OVERLAY_ATTRIBUTES: tuple[str, ...]           # ("content_rating", "resolution")

class AttributeNotOnItem(LookupError): ...

class OverlayItemView:
    def __init__(self, media, facts=None, plex_item=None) -> None: ...
    def get(self, attribute: str, /) -> object | None: ...

def parse_condition(raw: object, *, field: str = "condition") -> FilterGroup: ...
def compiled_condition(definition) -> FilterGroup | None: ...
def select(definitions, view) -> tuple[list, list[tuple[str, bool]]]: ...
```

```python
# src/autoposter/overlays/schema.py
class OverlayDefinition(BaseModel):
    condition: dict[str, object] | None = None
```

```python
# src/autoposter/badges/compose.py
def _outcomes_digest(outcomes: list[tuple[str, bool]]) -> str: ...
def badge_fingerprint(
    base_fingerprint: str,
    art_kind: str,
    values: dict[str, str],
    asset_manifest_sha: str,
    definitions: list[OverlayDefinition] | None = None,
    outcomes: list[tuple[str, bool]] | None = None,
) -> str: ...
```

```python
# src/autoposter/overlays/families.py
FAMILIES: dict[str, list[OverlayDefinition]]   # keys: "direct_play",
    # "content_rating_au", "content_rating_de", "content_rating_nz",
    # "content_rating_uk", "content_rating_us_movie", "content_rating_us_show"
```

```python
# src/autoposter/config/schema.py
class BadgesConfig(BaseModel):
    families: list[str] = []
    def all_definitions(self) -> list[OverlayDefinition]: ...
```

**Naming note, recorded rather than silently taken.** `p-overlay-c-recon.md` §2.1 drafted this field as `filters:`. It ships as **`condition:`** on the controller's instruction, and the reason is sound: the grammar is identical but the *vocabulary* is a strict subset (two attributes in C1, against the collections block's 25), so reusing the collections spelling would promise an operator every `filters:` attribute works here. The refusal message names the difference explicitly.

---

## Task 1: The selection seam — the condition field, the overlay view, the fingerprint extension

**Files:**
- Create: `src/autoposter/overlays/selection.py`
- Create: `tests/test_overlay_selection.py`
- Modify: `src/autoposter/overlays/schema.py`
- Modify: `src/autoposter/badges/compose.py`
- Modify: `src/autoposter/render/pipeline.py`
- Modify: `tests/test_overlay_schema.py`
- Modify: `tests/test_overlay_entrypoint.py`

**Interfaces:**
- Consumes (all pre-existing, unchanged signatures — verified against `origin/main`):
  - `collections.filters.parse_filters(raw, *, field="filters", searching=False, base="all") -> FilterGroup`
  - `collections.filters.evaluate(node, view, *, now=None) -> bool`
  - `collections.filters.predicates(node) -> Iterator[FilterPredicate]`
  - `collections.filters.FilterGroup`, `.FilterPredicate`, `.FilterAttribute`, `.BY_NAME`, `.FILTERABLE_ATTRIBUTES`
  - `collections.filter_values.PlexItemView(item, tags=None)` — read in tests only, for the agreement pins
  - `badges.values.MediaInfo`, `badges.values.media_info_from_plex(item) -> MediaInfo`
  - `badges.compose.badge_fingerprint(base_fingerprint, art_kind, values, asset_manifest_sha, definitions=None)`
  - `render.pipeline.apply_badges(session, config, render, item, plex_item, facts, probe=None, *, http=None)`
- Produces: everything in the "Interfaces produced by this phase" block above except `families.py` and `BadgesConfig.families`/`all_definitions` (T2).

---

- [ ] **Step 1: Measure the full-suite baseline on the freshly cut branch**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-baseline test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-baseline.log'
docker wait povc1-baseline
docker cp povc1-baseline:/app/.superpowers/p-overlay-c1-t1-baseline.log .superpowers/p-overlay-c1-t1-baseline.log
docker rm povc1-baseline
tail -5 .superpowers/p-overlay-c1-t1-baseline.log
```

Expected: all green. **Record the exact pass count in the task report.** Every later step reconciles against this measured number, never against a number this plan predicts (Global Constraint 13).

- [ ] **Step 2: Confirm the three pins this task must not move, before touching anything**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-pins test sh -c 'pytest -q tests/test_overlay_engine_golden.py tests/test_overlay_entrypoint.py tests/test_badge_parity.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-pins.log'
docker wait povc1-pins
docker cp povc1-pins:/app/.superpowers/p-overlay-c1-t1-pins.log .superpowers/p-overlay-c1-t1-pins.log
docker rm povc1-pins
grep -E "passed|failed" .superpowers/p-overlay-c1-t1-pins.log
```

Expected: all green, zero failures. If not, **STOP and report** — the branch is not clean and nothing below is meaningful.

- [ ] **Step 3: Capture the PRE-SEAM 5-argument fingerprint literal**

This is the pin that makes "a config of unconditioned definitions does not move" checkable against a *recorded* value rather than against a re-derivation of the function under test. It MUST be captured now, on the unmodified code.

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-literal test python -c "
from autoposter.badges.compose import badge_fingerprint
from autoposter.overlays.schema import OverlayDefinition
print(badge_fingerprint(
    'base-fp', 'poster', {'critic': '8.6'}, 'manifest-sha',
    [OverlayDefinition(name='text(HELLO)')],
))
"
docker wait povc1-literal
docker logs povc1-literal | tee .superpowers/p-overlay-c1-t1-preseam-literal.txt
docker rm povc1-literal
```

Expected: one 64-character lowercase hex line. **Copy it verbatim** — it is pasted into the test in Step 20 as `PRE_SEAM_ONE_DEFINITION_FINGERPRINT`. Do not re-derive it later; do not compute it by hand.

- [ ] **Step 4: Write the failing tests for the overlay item view**

Create `tests/test_overlay_selection.py`:

```python
"""The overlay-side selection seam: the item view, the vocabulary, the refusals.

The view is deliberately NOT `collections/filter_values.py::PlexItemView`.
That one is bound by "reading a filter value never costs a Plex request",
because the collections engine walks the library once with `section.all()`
and holds PARTIAL objects. The badge pipeline holds a richer context: by the
time `apply_badges` builds this view, `media_info_from_plex` has already
called `reload()` and walked `part.streams`, and `facts` and `plex_item` are
handed in directly. So the four attributes the collections table defers or
scopes for that reason (`audio_language`, `resolution`'s movie-only kind,
`duplicate`, `network`) do not bind here -- the recon's own table, adjudication
A3b answered by construction rather than by relaxing a column.

What DOES bind, and is pinned below: wherever the two views both answer, they
must answer identically. A disagreement would be the same-name-different-filter
class `collections/filters.py`'s module docstring rules out.
"""
import pytest

from autoposter.badges.values import MediaInfo, media_info_from_plex
from autoposter.collections.filter_values import PlexItemView
from autoposter.overlays.selection import (
    OVERLAY_ATTRIBUTES,
    AttributeNotOnItem,
    OverlayItemView,
)
from autoposter.overlays.schema import OverlayDefinition

# `compiled_condition`, `parse_condition` and `select` are imported below, in
# Step 9 -- Step 6 (next) writes only the view half of this module, so
# importing all six names here would make Step 7's "7 passed" an
# `ImportError` at collection instead. Splitting the import keeps each RED
# step failing for the one reason it names.


class _Media:
    def __init__(self, resolution):
        self.videoResolution = resolution
        self.audioCodec = "eac3"
        self.audioChannels = 6
        self.parts = []


class _Item:
    """A plexapi-shaped stand-in. Plain attributes, so
    `object.__getattribute__` (which PlexItemView uses) reaches them."""

    def __init__(self, resolutions=("1080",), content_rating="PG-13"):
        self.media = [_Media(r) for r in resolutions]
        self.contentRating = content_rating
        self.duration = 4845912
        self.seasonNumber = None
        self.episodeNumber = None

    def reload(self):
        """No-op. `badges/values.py::media_info_from_plex` calls this
        unconditionally when `.media` is empty -- matching the real function,
        which does not gate the call on the attribute's presence -- so a fake
        with no `.media` needs somewhere for that call to land."""


class _Facts:
    """`item_facts` -- whose `content_rating` is MDBList's COMMON SENSE age
    rating, a different value space from Plex's certification (A5)."""

    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = "3"


def _view(item, facts=None):
    return OverlayItemView(media_info_from_plex(item), facts=facts, plex_item=item)


def test_the_vocabulary_is_exactly_what_this_slice_supplies():
    assert OVERLAY_ATTRIBUTES == ("content_rating", "resolution")


def test_content_rating_comes_from_plex_not_from_item_facts():
    """Adjudication A5. The item carries BOTH values and they differ; the
    view must answer Plex's certification, not Common Sense's age."""
    item = _Item(content_rating="PG-13")
    assert _view(item, _Facts()).get("content_rating") == "PG-13"


def test_resolution_answers_every_version_not_just_the_first():
    """`MediaInfo` keeps `media[0]` only, so the view reads the item's own
    `media` list -- which `media_info_from_plex` has already reloaded, so
    this costs no request. Reading MediaInfo instead would answer ("4k",)
    for an item PlexItemView answers ("4k", "1080") for."""
    item = _Item(resolutions=("4k", "1080"))
    assert _view(item).get("resolution") == ("4k", "1080")


def test_an_item_with_no_media_has_no_resolution():
    """A show or a season carries no `media`. That is an ANSWER -- None,
    which the tag missing-value rule turns into "a positive filter excludes
    it" -- not an error."""
    item = _Item()
    item.media = []
    assert _view(item).get("resolution") is None


def test_an_attribute_outside_the_vocabulary_raises_rather_than_answering_none():
    """None means "this item has no value", which the table turns into a
    defined match result. Answering None for something we simply cannot read
    would be indistinguishable from a real answer -- the same discipline
    `filter_values.AttributeNotInListing` holds."""
    with pytest.raises(AttributeNotOnItem) as caught:
        _view(_Item()).get("genre")
    assert "content_rating" in str(caught.value)


def test_the_two_views_agree_on_content_rating():
    """Global Constraint 9."""
    item = _Item(content_rating="TV-MA")
    assert _view(item).get("content_rating") == PlexItemView(item).get("content_rating")


def test_the_two_views_agree_on_resolution_including_a_multi_version_item():
    """Global Constraint 9, on the attribute where a naive MediaInfo-backed
    accessor would have diverged."""
    for resolutions in (("1080",), ("4k", "1080"), ()):
        item = _Item(resolutions=resolutions)
        assert _view(item).get("resolution") == PlexItemView(item).get("resolution")
```

- [ ] **Step 5: Run the view tests to verify they fail**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-red1 test sh -c 'pytest -q tests/test_overlay_selection.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-red1.log'
docker wait povc1-red1
docker cp povc1-red1:/app/.superpowers/p-overlay-c1-t1-red1.log .superpowers/p-overlay-c1-t1-red1.log
docker rm povc1-red1
tail -20 .superpowers/p-overlay-c1-t1-red1.log
```

Expected: collection error — `ModuleNotFoundError: No module named 'autoposter.overlays.selection'`.

- [ ] **Step 6: Write `overlays/selection.py` — the view half**

Create `src/autoposter/overlays/selection.py`:

```python
"""The overlay engine's SELECTION half: which definitions apply to this item.

Until this module, `badges/compose.py::_draw_definitions` drew EVERY
configured definition on EVERY badged item -- `OverlayDefinition` had no
condition, filter or applicability field at all. Every family roadmap row 100
names is a *conditional* overlay (Kometa expresses each as a `plex_search:`
or `filters:` block), so none of them was expressible. That is the gap this
module closes, and it closes it by REUSING a filter engine rather than
writing one.

**The grammar is `collections/filters.py`'s, imported, not re-implemented**
(adjudication A3). That module's parse/evaluate pair was proved against
Kometa 2.4.8's own filter code -- 120 items, member list against member list,
the `SETTLED-BY-ORACLE` verdicts on its rows -- and a second filter dialect
would be exactly the "same name, different filter" class its own docstring
rules out. `parse_condition` below is a thin narrowing in front of
`parse_filters`, not a parser.

**The VIEW is ours, and deliberately not `filter_values.PlexItemView`.** That
view is bound by one hard rule -- "reading a filter value never costs a Plex
request" -- because the collections engine walks the library once with
`section.all()` and holds PARTIAL plexapi objects, whose attribute access
silently reloads. The badge path is a richer per-item context: by the time
this view is built, `render/pipeline.py::apply_badges` has already called
`badges/values.py::media_info_from_plex`, which reloads the item and walks
`part.streams`, and it is handed `facts` and `plex_item` directly. So the
collections table's per-attribute source tiers and `kinds` restrictions are
LISTING constraints, and they do not bind here (the recon's adjudication
A3b): a show simply has no `media`, which the tag missing-value rule already
answers correctly, rather than needing a `kinds` column to forbid it.
`plex_item` is still very likely PARTIAL, though -- the badge path never
copies it -- so this view's own accessors read it the same load-bearing way
`filter_values.py`'s do (`object.__getattribute__`, imported directly rather
than re-implemented), even though the CLASS itself is not `PlexItemView`.

**The vocabulary is narrower than the collections block's, on purpose.**
`OVERLAY_ATTRIBUTES` is what THIS view can actually supply. An attribute that
parses as a collections filter but has no accessor here is refused at config
load naming what is available -- never answered as None, because None means
"this item has no value" and the table turns that into a defined match
result. A family that silently matches nothing is indistinguishable from a
family that is off.
"""
import json
from functools import lru_cache

from autoposter.collections.filter_values import _listing_value, _resolutions
from autoposter.collections.filters import (
    FilterGroup,
    evaluate,
    parse_filters,
    predicates,
)

__all__ = [
    "OVERLAY_ATTRIBUTES",
    "AttributeNotOnItem",
    "OverlayItemView",
    "compiled_condition",
    "parse_condition",
    "select",
]

# The attributes this view supplies, in `collections/filters.py`'s own
# spelling -- the key `evaluate` reads. Sub-phase C1's two:
#
# - `content_rating`: PLEX's certification string (adjudication A5), which is
#   what the six content-rating regionals match. Deliberately NOT
#   `ItemFacts.content_rating`, which is MDBList's Common Sense AGE rating --
#   a different value space. Kometa's alias buckets carry Plex agent
#   spellings (`gb/U`, `no/A`, `TV-Y`) Common Sense never emits, and the
#   `content_rating` row in `collections/filters.py` says exactly this.
# - `resolution`: every version's `videoResolution`, which is what
#   `direct_play` regexes.
#
# Later slices append; each addition owes a source note here and an agreement
# pin against `filter_values.PlexItemView` if that view supplies it too.
OVERLAY_ATTRIBUTES: tuple[str, ...] = ("content_rating", "resolution")


class AttributeNotOnItem(LookupError):
    """Asked for a value this view cannot supply.

    Raised, never returned as missing -- `parse_condition` refuses these at
    config load, so reaching this at render time means a condition got past
    that check.
    """


class OverlayItemView:
    """`filters.ItemView` over the badge pipeline's own per-item context.

    `media` is a `badges.values.MediaInfo`, `facts` an `ItemFacts`-shaped
    object (or None), `plex_item` the live plexapi item `apply_badges` was
    handed. All three are already in hand at the call site; this view makes
    no request of its own -- and reads `plex_item` the same load-bearing way
    `filter_values.py` does (see `get()` below) so that stays true even for
    an unrated movie whose `.media` is populated but whose `.contentRating`
    is unset.
    """

    def __init__(self, media, facts=None, plex_item=None) -> None:
        self._media = media
        self._facts = facts
        self._plex_item = plex_item

    def get(self, attribute: str, /) -> object | None:
        """Reads `plex_item` through `filter_values.py`'s own accessors, not
        a plain `getattr`, and that is load-bearing, not a style choice.

        `plex_item` is very likely a `PlexPartialObject`: `apply_badges`
        hands this view the SAME live item it uploads to, not a copy.
        `media_info_from_plex` reloading it beforehand only guarantees
        `.media` and its streams are populated -- it does nothing for
        `.contentRating`, and a plain `getattr` on an UNSET attribute of a
        partial object trips `PlexPartialObject.__getattribute__`'s reload
        branch: one synchronous blocking `requests` GET, inline, on the
        event loop (`select()` is called directly in `apply_badges`, not
        through `asyncio.to_thread`). `filter_values._listing_value` and
        `filter_values._resolutions` exist to read a partial object WITHOUT
        that risk (`object.__getattribute__`, which is exactly what
        `PlexPartialObject.__getattribute__` itself calls first, minus the
        reload branch that follows it) -- reusing them here closes that
        hazard and, for `resolution`, closes the duplication a straight
        `filter_values` import would otherwise leave (R2): one accessor,
        not two copies that can drift.
        """
        if attribute == "content_rating":
            return _listing_value(self._plex_item, "contentRating") or None
        if attribute == "resolution":
            return _resolutions(self._plex_item)
        raise AttributeNotOnItem(
            f"{attribute!r} is not an attribute an overlay condition can "
            "read on this service. Available: " + ", ".join(OVERLAY_ATTRIBUTES)
        )
```

- [ ] **Step 7: Run the view tests to verify they pass**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-green1 test sh -c 'pytest -q tests/test_overlay_selection.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-green1.log'
docker wait povc1-green1
docker cp povc1-green1:/app/.superpowers/p-overlay-c1-t1-green1.log .superpowers/p-overlay-c1-t1-green1.log
docker rm povc1-green1
tail -5 .superpowers/p-overlay-c1-t1-green1.log
```

Expected: 7 passed. (`test_a_view_...` names collect as written; the count is measured, not predicted — if it differs, reconcile before moving on.)

- [ ] **Step 8: Commit the view**

```bash
git add src/autoposter/overlays/selection.py tests/test_overlay_selection.py
git commit --no-gpg-sign -m "feat(overlays): the overlay-side item view for condition evaluation"
```

- [ ] **Step 9: Write the failing tests for `parse_condition` and `select`**

First, extend the import at the top of `tests/test_overlay_selection.py` (the
one Step 4 wrote) to add the three names this step's tests need and Step 6
did not define:

```python
from autoposter.overlays.selection import (
    OVERLAY_ATTRIBUTES,
    AttributeNotOnItem,
    OverlayItemView,
    compiled_condition,
    parse_condition,
    select,
)
```

Then append to `tests/test_overlay_selection.py`:

```python
# --- the parse half: one narrowing in front of collections/filters.py -------


def test_a_condition_parses_through_the_shared_grammar():
    group = parse_condition({"resolution.regex": "(?i)2160|4k"})
    written = list(predicates_of(group))
    assert len(written) == 1
    assert written[0].attribute.name == "resolution"
    assert written[0].operator == "regex"


def predicates_of(group):
    from autoposter.collections.filters import predicates as _predicates

    return _predicates(group)


def test_an_attribute_outside_the_overlay_vocabulary_is_refused_at_parse():
    """Global Constraint 10. `genre` is a perfectly good COLLECTIONS filter
    attribute; this view cannot supply it, so an operator gets told, at load,
    rather than silently getting a family that matches nothing."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"genre": "Horror"})
    message = str(caught.value)
    assert "genre" in message
    assert "content_rating" in message and "resolution" in message


def test_an_attribute_no_filter_vocabulary_has_is_refused_by_the_shared_parser():
    """The refusal comes from `collections/filters.py` unchanged -- proof the
    narrowing sits IN FRONT of the shared parser rather than replacing it."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"nonsense": "x"})
    assert "unknown filter attribute" in str(caught.value)


def test_an_operator_the_type_does_not_carry_is_refused():
    """`content_rating` is a `tag`; `tag` takes eq/not/regex and nothing else."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"content_rating.contains": "PG"})
    assert "content_rating" in str(caught.value)


def test_an_empty_condition_is_refused_rather_than_matching_everything():
    with pytest.raises(ValueError):
        parse_condition({})


def test_compiled_conditions_are_cached_by_value_not_by_definition_identity():
    """Two definitions writing the same condition compile once. The cache key
    is the condition's canonical JSON, so it cannot be fooled by key order."""
    a = OverlayDefinition(name="a", condition={"content_rating": "PG", "resolution": "4k"})
    b = OverlayDefinition(name="b", condition={"resolution": "4k", "content_rating": "PG"})
    assert compiled_condition(a) is compiled_condition(b)


def test_a_definition_with_no_condition_compiles_to_none():
    assert compiled_condition(OverlayDefinition(name="a")) is None


# --- select(): the matched subset AND the outcomes the fingerprint folds ----


def test_an_unconditioned_definition_matches_every_item_and_reports_no_outcome():
    """The pre-seam behaviour, preserved exactly: a definition with no
    condition draws on every badged item. It contributes NO outcome, which is
    what keeps an existing row-97 config's fingerprint from moving."""
    plain = OverlayDefinition(name="plain")
    matched, outcomes = select([plain], _view(_Item()))
    assert matched == [plain]
    assert outcomes == []


def test_a_matching_condition_selects_the_definition_and_records_a_true():
    fires = OverlayDefinition(name="dp", condition={"resolution.regex": "(?i)2160|4k"})
    matched, outcomes = select([fires], _view(_Item(resolutions=("4k",))))
    assert matched == [fires]
    assert outcomes == [("dp", True)]


def test_a_failing_condition_drops_the_definition_and_records_a_false():
    fires = OverlayDefinition(name="dp", condition={"resolution.regex": "(?i)2160|4k"})
    matched, outcomes = select([fires], _view(_Item(resolutions=("1080",))))
    assert matched == []
    assert outcomes == [("dp", False)]


def test_outcomes_keep_the_configured_order_not_a_sorted_one():
    """Definition ORDER is already significant to `_definitions_digest`
    (it decides which member of a group wins a tie), so the outcomes list
    carries the same order rather than a sorted one -- and two definitions
    sharing a name cannot collide the way a dict keyed on name would."""
    first = OverlayDefinition(name="z", condition={"resolution": "4k"})
    second = OverlayDefinition(name="a", condition={"resolution": "1080"})
    _, outcomes = select([first, second], _view(_Item(resolutions=("1080",))))
    assert outcomes == [("z", False), ("a", True)]
```

- [ ] **Step 10: Run to verify they fail**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-red2 test sh -c 'pytest -q tests/test_overlay_selection.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-red2.log'
docker wait povc1-red2
docker cp povc1-red2:/app/.superpowers/p-overlay-c1-t1-red2.log .superpowers/p-overlay-c1-t1-red2.log
docker rm povc1-red2
tail -20 .superpowers/p-overlay-c1-t1-red2.log
```

Expected: `ImportError: cannot import name 'parse_condition'` (and `compiled_condition`, `select`) — the module exists but these three do not.

- [ ] **Step 11: Add the parse and select halves to `overlays/selection.py`**

Append to `src/autoposter/overlays/selection.py`:

```python
def parse_condition(raw: object, *, field: str = "condition") -> FilterGroup:
    """Parse one definition's `condition:` block, or refuse naming the key.

    Two layers, in this order, because the messages are different and both
    are useful:

    1. `collections.filters.parse_filters` -- the shared grammar. It refuses
       an attribute no filter vocabulary has, an operator the attribute's
       type does not carry, an empty block, a `.and` suffix, a plex_search-
       only modifier, and a value that will not coerce. Those refusals are
       already written, already tested and already cite Kometa; none of them
       is re-implemented here.
    2. the overlay NARROWING -- an attribute that is a legitimate collections
       filter but that `OverlayItemView` cannot supply. That refusal has to
       be ours, because `filters.py` has no idea what this view reads.

    `field` is the dotted path the refusal names, so an operator with twenty
    definitions can find the one that is wrong.
    """
    group = parse_filters(raw, field=field)
    for predicate in predicates(group):
        if predicate.attribute.name not in OVERLAY_ATTRIBUTES:
            raise ValueError(
                f"{predicate.field}: {predicate.attribute.name!r} is a filter "
                "attribute this service can evaluate for a COLLECTION but not "
                "for an overlay -- an overlay condition is answered from what "
                "the badge pass already holds for the item, and there is no "
                "accessor for it there. An overlay condition can name: "
                + ", ".join(OVERLAY_ATTRIBUTES)
            )
    return group


@lru_cache(maxsize=512)
def _compiled(payload: str) -> FilterGroup:
    """Parse once per distinct condition, not once per item.

    Keyed on the condition's canonical JSON rather than on the definition,
    so two definitions writing the same condition share one tree and a
    reordered mapping is the same key. Bounded: an operator with more than
    512 distinct conditions pays a re-parse for the tail, which is a small
    cost with a hard ceiling instead of an unbounded one.

    Calls `parse_condition` with its default `field="condition"` -- a
    refusal raised THROUGH this cache (rather than at config load, where
    `OverlayDefinition._validate` already calls `parse_condition` with the
    same default and would have caught it first) therefore always names the
    literal field `"condition"`, never a per-overlay dotted path. Reaching
    this refusal at all means a condition got past load-time validation,
    which should not happen; it is not the path `parse_condition`'s `field`
    parameter is documented for.
    """
    return parse_condition(json.loads(payload))


def compiled_condition(definition) -> FilterGroup | None:
    """This definition's parsed condition, or None when it has none.

    `definition` is an `overlays.schema.OverlayDefinition`; typed loosely so
    this module does not import the schema that imports it back at validation
    time.
    """
    if definition.condition is None:
        return None
    return _compiled(json.dumps(definition.condition, sort_keys=True))


def select(definitions, view) -> tuple[list, list[tuple[str, bool]]]:
    """Which definitions apply to this item, and the outcomes to fingerprint.

    Returns `(matched, outcomes)`:

    - `matched` is the subset handed on to image resolution and `compose` --
      in configured order, so suppression and group/weight resolution
      downstream see exactly what an operator wrote, minus what this item
      does not match. A definition with no condition is in it always, which
      is the pre-seam behaviour preserved exactly.
    - `outcomes` is `(name, matched)` for every definition that CARRIES a
      condition, and nothing else. That asymmetry is the whole storm guard:
      a config of unconditioned definitions produces an EMPTY outcomes list,
      which `badge_fingerprint` folds in under a non-empty guard, so every
      already-badged item under an existing row-97 config keeps its digest to
      the bit. It is the same shape, and the same reasoning, as the
      `if definitions:` guard that already sits beside it.

    Order is configured order, not sorted: definition order already decides
    group tie-breaks and draw order, and keying on the name alone would
    collide for two definitions sharing one.
    """
    matched: list = []
    outcomes: list[tuple[str, bool]] = []
    for definition in definitions:
        condition = compiled_condition(definition)
        if condition is None:
            matched.append(definition)
            continue
        passed = evaluate(condition, view)
        outcomes.append((definition.name, passed))
        if passed:
            matched.append(definition)
    return matched, outcomes
```

- [ ] **Step 12: Run to verify they pass**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-green2 test sh -c 'pytest -q tests/test_overlay_selection.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-green2.log'
docker wait povc1-green2
docker cp povc1-green2:/app/.superpowers/p-overlay-c1-t1-green2.log .superpowers/p-overlay-c1-t1-green2.log
docker rm povc1-green2
tail -5 .superpowers/p-overlay-c1-t1-green2.log
```

Expected: all green. Note the `OverlayDefinition(name=..., condition=...)` constructions in these tests still fail — `condition` is not a field yet and `extra="forbid"` refuses it. That is the intended RED for Step 13; if those specific tests pass here, the schema already has the field and something is wrong.

**Expected failures at this step, and only these:** `test_compiled_conditions_are_cached_by_value_not_by_definition_identity` and the four `select` tests fail with pydantic's "Extra inputs are not permitted" for `condition` -- each constructs an `OverlayDefinition(..., condition=...)` and the field does not exist yet. `test_a_definition_with_no_condition_compiles_to_none` fails too, but not for that reason: it constructs `OverlayDefinition(name="a")` with no `condition` kwarg at all, so pydantic never objects to anything -- it fails inside `compiled_condition` itself with `AttributeError: 'OverlayDefinition' object has no attribute 'condition'`, because the attribute genuinely does not exist on the class yet. Six failures, two distinct reasons.

- [ ] **Step 13: Write the failing schema tests for `condition:`**

Append to `tests/test_overlay_schema.py`:

```python
# --- the selection seam's one new field (overlay era, sub-phase C1) ---------


def test_a_definition_may_carry_a_condition():
    definition = OverlayDefinition(
        name="Direct-Play", condition={"resolution.regex": "(?i)2160|4k"}
    )
    assert definition.condition == {"resolution.regex": "(?i)2160|4k"}


def test_no_condition_is_the_default_and_means_draw_on_every_item():
    assert OverlayDefinition(name="plain").condition is None


def test_a_condition_naming_an_attribute_this_view_cannot_read_is_refused_at_load():
    """Global Constraint 10: refused at config load, naming the key and
    listing what is available -- never accepted and silently matching
    nothing at render time."""
    with pytest.raises(ValidationError) as caught:
        OverlayDefinition(name="x", condition={"genre": "Horror"})
    message = str(caught.value)
    assert "genre" in message
    assert "content_rating" in message


def test_a_condition_with_a_bad_operator_is_refused_at_load():
    with pytest.raises(ValidationError) as caught:
        OverlayDefinition(name="x", condition={"content_rating.contains": "PG"})
    assert "content_rating" in str(caught.value)


def test_an_empty_condition_is_refused_at_load():
    with pytest.raises(ValidationError):
        OverlayDefinition(name="x", condition={})


def test_the_refusal_names_the_overlay_it_came_from():
    """An operator with twenty definitions needs both halves to fix one."""
    with pytest.raises(ValidationError) as caught:
        OverlayDefinition(name="my-overlay", condition={"genre": "Horror"})
    assert "my-overlay" in str(caught.value)
```

If `pytest` and `ValidationError` are not already imported at the top of `tests/test_overlay_schema.py`, add them (`import pytest` and `from pydantic import ValidationError`) rather than duplicating an import that is already there.

- [ ] **Step 14: Run to verify they fail**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-red3 test sh -c 'pytest -q tests/test_overlay_schema.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-red3.log'
docker wait povc1-red3
docker cp povc1-red3:/app/.superpowers/p-overlay-c1-t1-red3.log .superpowers/p-overlay-c1-t1-red3.log
docker rm povc1-red3
tail -20 .superpowers/p-overlay-c1-t1-red3.log
```

Expected: the six new tests fail with pydantic's `Extra inputs are not permitted` for `condition`. Every pre-existing test in the file still passes.

- [ ] **Step 15: Add the `condition` field to `overlays/schema.py`, and teach `badges/compose.py::_definitions_digest` to exclude it while unset**

Insert the field immediately after the `queue` field (i.e. after the block ending at the current `:66`, before the `horizontal_offset` block):

```python
    # The SELECTION half (overlay era, sub-phase C1). Every family roadmap
    # row 100 names is a CONDITIONAL overlay -- Kometa expresses each as a
    # `plex_search:` or `filters:` block -- and until this field existed the
    # engine drew every configured definition on every badged item, so none
    # of them was expressible: eight `aspect` definitions with no condition
    # all draw on every poster, and group/weight cannot save that, because
    # with no conditions the same highest-weight member wins on every item.
    #
    # The grammar is `collections/filters.py`'s, imported rather than
    # re-implemented (adjudication A3) -- that parser and evaluator were
    # proved against Kometa 2.4.8's own filter code. The VOCABULARY is
    # narrower: only what `overlays/selection.py::OverlayItemView` can
    # actually supply, which is why the key is `condition:` and not
    # `filters:`. Using the collections spelling would promise an operator
    # every collections attribute works here.
    #
    # Typed as the raw mapping rather than as a parsed `FilterGroup`: it has
    # to round-trip through `model_dump(mode="json")` for
    # `badges/compose.py::_definitions_digest`, so that editing a condition
    # moves the fingerprint for free.
    condition: dict[str, object] | None = Field(
        default=None,
        description=(
            "This overlay's applicability test, in the same filter grammar a "
            "collection's 'filters:' block uses, evaluated per item against "
            "what the badge pass already holds. A definition with no "
            "condition draws on every badged item."
        ),
    )
```

Then, inside `_validate`, immediately after the `text` refusal block and before the `if self.group and self.weight is None:` check, add:

```python
        # Refused at config load, not at render time, and with the overlay's
        # own name in the message: a condition that silently matches nothing
        # is indistinguishable from an overlay that is switched off. The
        # import is call-time for the same reason `_as_rgba`'s is --
        # `config/schema.py` types `BadgesConfig.definitions` with this class
        # and `actions/flags.py` imports `config.schema` on every Action
        # Center request, so a module-level import of the filter engine here
        # would put it on that path for every one of them.
        if self.condition is not None:
            from autoposter.overlays.selection import parse_condition

            try:
                parse_condition(self.condition, field="condition")
            except ValueError as exc:
                raise ValueError(f"overlay {name!r}: {exc}") from exc
```

**Now the digest law, in the same step, because it has to land before anything hashes a definition that carries this field.** `OverlayDefinition.model_config` is `{"extra": "forbid", "frozen": True}` -- no `exclude_none`, no `exclude_defaults` -- so `model_dump(mode="json")` includes every defaulted `None` field, `condition` included. Left alone, that MOVES `_definitions_digest` for every already-configured definition the moment this field exists, whether or not any operator ever sets it: measured on this tree, the exact one-definition call T1 Step 3 captures moves from
`973b2cf3010950d3980be8644f92f1ad5243936691ce100f89eb44bdf94f0923` (30 keys, pre-`condition`) to
`929e63d394ab6d333b4abeb80cdd2443b095b54478c7522381966205409db1ce` (31 keys, `"condition": null` included) -- a mass re-fingerprint, on the first pass after this phase, of every already-badged item in a library that configures ANY definition at all. That is exactly the storm Global Constraint 5 exists to prevent, and it is not the `outcomes` guard's job to prevent it -- `_definitions_digest` is a different function.

In `src/autoposter/badges/compose.py`, change `_definitions_digest`'s body:

```python
def _definitions_digest(definitions: list[OverlayDefinition]) -> str:
    """A stable digest of the definitions list's own content.

    ``model_dump(mode="json")`` plus ``sort_keys=True`` rather than
    ``repr()``: a definition's field order is a class-declaration detail, not
    part of what an operator configured, so the digest must not depend on it.
    The list's own ORDER is kept significant, though, and definitions are
    joined in the order given rather than sorted -- unlike each definition's
    fields, list order changes which member of a group wins ties (draw
    order), which is operator-visible.

    **LAW: every field added to ``OverlayDefinition`` after this comment is
    excluded from the dump here, by name, for as long as it sits at its own
    unset default.** ``model_dump`` carries no blanket ``exclude_none`` --
    that would also swallow a field an operator deliberately set back to
    ``None`` over a non-``None`` default, which this digest DOES need to
    notice. So each additive field earns one explicit line instead: popped
    when the dump's value for it equals the field's unset default, left in
    otherwise. Skipping this for ``condition`` (overlay era sub-phase C1)
    would have moved this digest for every definition that never touches the
    field at all -- see this function's call site's Step for the measured
    before/after hashes. Every future additive ``OverlayDefinition`` field
    owes the same one-line exclusion, or existing digests move on schema
    growth alone.
    """
    dumps = []
    for d in definitions:
        dump = d.model_dump(mode="json")
        if dump.get("condition") is None:
            dump.pop("condition", None)
        dumps.append(json.dumps(dump, sort_keys=True))
    return hashlib.sha256("\x1e".join(dumps).encode("utf-8")).hexdigest()
```

- [ ] **Step 16: Run the schema, selection and compose suites to verify they pass**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-green3 test sh -c 'pytest -q tests/test_overlay_schema.py tests/test_overlay_selection.py tests/test_overlay_builtin.py tests/test_badge_compose.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-green3.log'
docker wait povc1-green3
docker cp povc1-green3:/app/.superpowers/p-overlay-c1-t1-green3.log .superpowers/p-overlay-c1-t1-green3.log
docker rm povc1-green3
tail -5 .superpowers/p-overlay-c1-t1-green3.log
```

Expected: all green, including the six `select`/`compiled_condition` tests that were RED at Step 12. `tests/test_badge_compose.py` is a parity pin file (Global Constraint 3, never edited) -- it is run here, unmodified, specifically because it is the earliest point any suite constructs a real `OverlayDefinition` and hashes it through `_definitions_digest`, and the digest-exclusion fix above is what keeps its existing assertions passing unmodified now that the schema has one more field.

- [ ] **Step 17: Commit the field**

```bash
git add src/autoposter/overlays/selection.py src/autoposter/overlays/schema.py src/autoposter/badges/compose.py tests/test_overlay_selection.py tests/test_overlay_schema.py
git commit --no-gpg-sign -m "feat(overlays): a per-definition condition, parsed and refused at config load"
```

- [ ] **Step 18: Confirm the storm pin has not moved (nothing has touched the fingerprint yet)**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-storm1 test sh -c 'pytest -q tests/test_overlay_entrypoint.py tests/test_overlay_engine_golden.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-storm1.log'
docker wait povc1-storm1
docker cp povc1-storm1:/app/.superpowers/p-overlay-c1-t1-storm1.log .superpowers/p-overlay-c1-t1-storm1.log
docker rm povc1-storm1
grep -E "passed|failed" .superpowers/p-overlay-c1-t1-storm1.log
```

Expected: all green. If the golden gate or the storm pin moved here, adding a defaulted field changed a dump somewhere — **STOP and report**.

- [ ] **Step 19: Write the failing fingerprint tests (the extended storm pins)**

Append to `tests/test_overlay_entrypoint.py`, immediately after
`test_removing_a_definition_reverts_the_fingerprint`:

```python
# --- A4: the fingerprint must cover the per-item MATCH OUTCOME, and the
# storm pin must EXTEND rather than weaken. Three properties, three pins:
# the empty case stays byte-identical (the literal above, untouched); an
# unchanged item with unchanged matches keeps its digest, and specifically
# keeps the digest the PRE-SEAM five-argument call produced; and an item
# whose matched set moved re-renders. ------------------------------------

# Captured on the freshly cut branch BEFORE `outcomes` existed (T1 Step 3),
# by calling badge_fingerprint with exactly five arguments. Pinned as a
# literal rather than re-derived, for the same reason the gate-off literal
# above is: a re-derivation passes even when the formula and the pin move
# together.
PRE_SEAM_ONE_DEFINITION_FINGERPRINT = "<paste the 64-hex line from Step 3 here>"


def test_a_config_of_unconditioned_definitions_does_not_move_the_fingerprint():
    """The storm guard, extended to the case this phase creates. An operator
    who already configures row-97 definitions and writes no `condition:` on
    any of them must see ZERO fingerprint movement -- `select` reports no
    outcomes for an unconditioned definition, so the new digest part is never
    appended."""
    stamp = OverlayDefinition(name="text(HELLO)")
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp],
    ) == PRE_SEAM_ONE_DEFINITION_FINGERPRINT
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], [],
    ) == PRE_SEAM_ONE_DEFINITION_FINGERPRINT


def test_a_condition_carrying_definition_moves_the_fingerprint_from_the_pre_seam_value():
    """The discriminating half of the same guard. `_definitions_digest`
    excludes `condition` from a definition's dump only while it is unset
    (None) -- a definition that DOES carry one must therefore differ from
    the pre-seam literal. If it did not, the exclusion would be swallowing
    more than the unset default, and an operator's own `condition:` edits
    would stop moving the fingerprint too."""
    conditioned = OverlayDefinition(
        name="text(HELLO)", condition={"resolution": "4k"}
    )
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [conditioned],
    ) != PRE_SEAM_ONE_DEFINITION_FINGERPRINT


def test_an_empty_outcomes_list_is_indistinguishable_from_no_outcomes_at_all():
    """Same guard, at the gate-off end: `outcomes=[]` and `outcomes=None`
    must both leave `parts` untouched, so the pinned literal above stays
    reachable from every call shape."""
    assert badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6", "audience": "63%"}, "manifest-sha",
        None, [],
    ) == "576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd"


def test_an_unchanged_item_with_unchanged_matches_keeps_its_digest():
    """A4's first half, tied to the real selection mechanism rather than to
    two calls of `badge_fingerprint` with a hand-typed, byte-identical
    `outcomes` literal -- that only proves hashlib is deterministic (it
    cannot fail under any implementation of `_outcomes_digest`, and would
    survive a bug where two evaluations of the SAME item disagree: a set
    walked in insertion-unstable order, an `lru_cache` keyed on something
    other than the condition's own content). `outcomes` is produced by
    RUNNING `select()` against a real matching item, twice -- a genuinely
    unchanged second pass over the first pass's own inputs -- not typed as a
    literal."""
    from autoposter.overlays.selection import OverlayItemView, select

    stamp = OverlayDefinition(name="dp", condition={"resolution.regex": "(?i)2160|4k"})
    plex_item = _FakePlexItem()
    plex_item.media[0].videoResolution = "4k"
    view = OverlayItemView(None, plex_item=plex_item)

    _, first_outcomes = select([stamp], view)
    _, second_outcomes = select([stamp], view)
    first = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], first_outcomes,
    )
    second = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], second_outcomes,
    )
    assert first == second


def test_a_changed_match_outcome_moves_the_digest():
    """A4's second half, and the reason A4 was ruled yes: an item whose
    resolution changed from 1080 to 4k keeps its old badge forever if the
    fingerprint covers only the config, because the CONFIG did not move."""
    stamp = OverlayDefinition(name="dp", condition={"resolution.regex": "(?i)2160|4k"})
    before = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], [("dp", False)],
    )
    after = badge_fingerprint(
        "base-fp", "poster", {"critic": "8.6"}, "manifest-sha", [stamp], [("dp", True)],
    )
    assert before != after


def test_the_outcome_digest_is_order_significant_and_name_carrying():
    """Two definitions that swap outcomes are a different item state, and a
    name is part of what an outcome means -- otherwise `[True, False]` and
    `[False, True]` would collide."""
    a = OverlayDefinition(name="a", condition={"resolution": "4k"})
    b = OverlayDefinition(name="b", condition={"resolution": "1080"})
    one = badge_fingerprint(
        "base-fp", "poster", {}, "manifest-sha", [a, b], [("a", True), ("b", False)],
    )
    two = badge_fingerprint(
        "base-fp", "poster", {}, "manifest-sha", [a, b], [("a", False), ("b", True)],
    )
    assert one != two
```

- [ ] **Step 20: Paste the captured literal**

Replace `"<paste the 64-hex line from Step 3 here>"` with the exact hex string recorded in `.superpowers/p-overlay-c1-t1-preseam-literal.txt`. If that file is missing or does not hold a single 64-character hex line, **STOP** — re-run Step 3 against `origin/main` in a clean worktree; do not compute the value from the modified code.

- [ ] **Step 21: Run to verify they fail**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-red4 test sh -c 'pytest -q tests/test_overlay_entrypoint.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-red4.log'
docker wait povc1-red4
docker cp povc1-red4:/app/.superpowers/p-overlay-c1-t1-red4.log .superpowers/p-overlay-c1-t1-red4.log
docker rm povc1-red4
tail -25 .superpowers/p-overlay-c1-t1-red4.log
```

Expected: five of the six tests in this block fail, everywhere they pass a sixth argument, with `TypeError: badge_fingerprint() takes 4 to 5 positional arguments but 6 were given`: `test_an_empty_outcomes_list_is_indistinguishable_from_no_outcomes_at_all`, `test_an_unchanged_item_with_unchanged_matches_keeps_its_digest`, `test_a_changed_match_outcome_moves_the_digest`, `test_the_outcome_digest_is_order_significant_and_name_carrying`, and `test_a_config_of_unconditioned_definitions_does_not_move_the_fingerprint` (whose FIRST assertion already passes — it is the pre-seam five-argument call — and whose SECOND fails on that same `TypeError`; that split is the point, the pin is anchored to a value that exists before the change). The sixth, `test_a_condition_carrying_definition_moves_the_fingerprint_from_the_pre_seam_value`, passes outright here: it never passes a sixth argument, and the digest-exclusion law Step 15 already added makes its assertion true against the current code.

- [ ] **Step 22: Extend `badge_fingerprint`**

In `src/autoposter/badges/compose.py`, add `_outcomes_digest` immediately after `_definitions_digest`:

```python
def _outcomes_digest(outcomes: list[tuple[str, bool]]) -> str:
    """A stable digest of one item's per-definition match outcomes.

    `outcomes` is `(overlay name, matched)` in CONFIGURED order, produced by
    `overlays/selection.py::select`, and it carries only definitions that
    actually have a `condition:`. Joined in the order given rather than
    sorted, for the same reason `_definitions_digest` keeps list order: order
    is operator-visible (it decides group tie-breaks and draw order), and a
    dict keyed on the name alone would collide for two definitions sharing
    one.
    """
    parts = ["%s=%d" % (name, 1 if matched else 0) for name, matched in outcomes]
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()
```

Then change `badge_fingerprint`'s signature and body. The new signature:

```python
def badge_fingerprint(
    base_fingerprint: str,
    art_kind: str,
    values: dict[str, str],
    asset_manifest_sha: str,
    definitions: list[OverlayDefinition] | None = None,
    outcomes: list[tuple[str, bool]] | None = None,
) -> str:
```

Append to its docstring, after the existing `definitions` paragraph:

```
    ``outcomes`` folds in this ITEM's own match results (adjudication A4,
    overlay era sub-phase C1). Without it the gate is config-only, so an item
    whose resolution or aspect changed keeps its old badge forever -- the
    config did not move, and the fingerprint could not tell. It is guarded
    exactly the way ``definitions`` is, and the guard is what makes the
    extension safe: ``overlays/selection.py::select`` reports an outcome only
    for a definition that CARRIES a condition, so an empty or absent list
    leaves ``parts`` untouched and every already-badged item under an empty
    or unconditioned config keeps its digest to the bit. The cost is real and
    is disclosed rather than hidden: filter evaluation now runs BEFORE the
    fingerprint gate, so an unchanged item pays one ``json.dumps`` of the
    condition (``overlays/selection.py::compiled_condition``, run BEFORE its
    cache lookup) plus a ``FilterGroup`` tree walk, per conditioned
    definition -- not the single dict read this docstring described in an
    earlier draft. That is no Plex request (this view's own law) but it is
    not free, and it is the first work added to the unchanged-item path
    since row 97.
```

And the body's tail:

```python
    parts = [base_fingerprint, art_kind, asset_manifest_sha]
    parts += ["%s=%s" % (k, values[k]) for k in sorted(values)]
    if definitions:
        parts.append(_definitions_digest(definitions))
    if outcomes:
        parts.append(_outcomes_digest(outcomes))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
```

- [ ] **Step 23: Run to verify they pass, and that the pinned literals did not move**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-green4 test sh -c 'pytest -q tests/test_overlay_entrypoint.py tests/test_overlay_engine_golden.py tests/test_badge_compose.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-green4.log'
docker wait povc1-green4
docker cp povc1-green4:/app/.superpowers/p-overlay-c1-t1-green4.log .superpowers/p-overlay-c1-t1-green4.log
docker rm povc1-green4
grep -E "passed|failed" .superpowers/p-overlay-c1-t1-green4.log
```

Expected: all green, zero failures. `test_the_gate_off_fingerprint_is_pinned_byte_identical` must still pass **unmodified** (Global Constraint 5).

- [ ] **Step 24: Commit the fingerprint extension**

```bash
git add src/autoposter/badges/compose.py tests/test_overlay_entrypoint.py
git commit --no-gpg-sign -m "feat(badges): fold the per-item match outcome into badge_fingerprint"
```

- [ ] **Step 25: Write the failing entry-point tests — the three-way gate law**

Append to `tests/test_overlay_entrypoint.py`:

```python
# --- the seam through the REAL entry point: gate-off / gate-on-but-unmatched
# / gate-on-and-matched. Global Constraint 6. `_sha` is a PIXEL hash, not a
# file hash: a differently-configured item legitimately stamps different EXIF
# provenance, and what must be identical is what got DRAWN. --------------


class _FakePlexItem4k(_FakePlexItem):
    """The same fake, one attribute moved. `videoResolution` is what
    `direct_play` selects on."""

    def __init__(self):
        super().__init__()
        self.media[0].videoResolution = "4k"
        self.contentRating = "PG-13"


async def test_a_definition_whose_condition_fails_draws_exactly_the_gate_off_pixels(
    session, config_with_badges, tmp_path
):
    """The middle arm of the three-way law, and the one that did not exist
    before this phase: gate ON, condition NOT satisfied, and the drawn pixels
    must equal the no-definitions baseline exactly."""
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(stamp, format="PNG")
    config_with_badges.overlays_root = tmp_path

    config_with_badges.badges.definitions = []
    base_item, base_render = await _render(session, rating_key="cond-baseline")
    base_plex = _FakePlexItem()
    await apply_badges(session, config_with_badges, base_render, base_item, base_plex, _Facts())

    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", file="stamp.png",
            condition={"resolution.regex": "(?i)2160|4k"},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]
    item, render = await _render(session, rating_key="cond-unmatched")
    plex_item = _FakePlexItem()  # 1080 -- does not satisfy the condition
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())

    assert _sha(plex_item.last_bytes) == _sha(base_plex.last_bytes)


async def test_a_definition_whose_condition_holds_is_actually_drawn(
    session, config_with_badges, tmp_path
):
    """The third arm. Same config, an item that DOES satisfy it."""
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(stamp, format="PNG")
    config_with_badges.overlays_root = tmp_path

    config_with_badges.badges.definitions = []
    base_item, base_render = await _render(session, rating_key="cond-baseline-4k")
    base_plex = _FakePlexItem4k()
    await apply_badges(session, config_with_badges, base_render, base_item, base_plex, _Facts())

    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", file="stamp.png",
            condition={"resolution.regex": "(?i)2160|4k"},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]
    item, render = await _render(session, rating_key="cond-matched")
    plex_item = _FakePlexItem4k()
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())

    assert _sha(plex_item.last_bytes) != _sha(base_plex.last_bytes)


async def test_an_item_whose_match_outcome_changes_re_badges(
    session, config_with_badges, tmp_path
):
    """A4 through the real entry point, which is where it matters: the same
    render row, the same config, one item attribute moved -- and moved on an
    attribute `badge_values` never reads, so nothing but the match OUTCOME
    could be what moves the fingerprint. `contentRating` is read by the
    overlay view (it is what the six regionals select on) but by no badge:
    `BadgeInputs.content_rating` comes from `facts`, not from `plex_item`
    (`pipeline.py:1321`) -- unlike `videoResolution`, which also drives
    `resolution_image` and would move the fingerprint through `values` on
    its own, discriminating nothing. Without the outcome in the fingerprint,
    `apply_badges`'s gate sees an unchanged digest and
    `upload_status == 'uploaded'` and returns early -- the item keeps the
    wrong badge forever."""
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(stamp, format="PNG")
    config_with_badges.overlays_root = tmp_path
    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", file="stamp.png",
            condition={"content_rating": "PG-13"},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]

    item, render = await _render(session, rating_key="outcome-moves")
    plex_item = _FakePlexItem()  # no contentRating: does not match
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 1
    unmatched_fingerprint = render.badge_fingerprint

    plex_item.contentRating = "PG-13"  # the item changed
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 2, "a changed match outcome must re-badge"
    assert render.badge_fingerprint != unmatched_fingerprint


async def test_an_unmatched_definition_never_resolves_its_image(
    session, config_with_badges, tmp_path, monkeypatch
):
    """Global Constraint 7. Selection runs BEFORE the per-definition
    resolution loop, so an overlay this item does not match costs no stat, no
    download and no decode. Proven with a `url:` source, where the I/O is
    observable: zero requests must be made."""
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )
    calls = []

    def handler(request):
        calls.append(request)
        buffer = io.BytesIO()
        Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(buffer, format="PNG")
        return httpx.Response(
            200, content=buffer.getvalue(), headers={"content-type": "image/png"},
        )

    config_with_badges.overlays_root = tmp_path
    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="mystamp", url="https://example.com/a.png",
            condition={"resolution.regex": "(?i)2160|4k"},
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
        )
    ]
    item, render = await _render(session, rating_key="unmatched-url")
    plex_item = _FakePlexItem()  # 1080
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await apply_badges(
            session, config_with_badges, render, item, plex_item, _Facts(), http=http
        )
    assert calls == [], "an unmatched definition must not resolve its image"
```

- [ ] **Step 26: Run to verify they fail**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-red5 test sh -c 'pytest -q tests/test_overlay_entrypoint.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-red5.log'
docker wait povc1-red5
docker cp povc1-red5:/app/.superpowers/p-overlay-c1-t1-red5.log .superpowers/p-overlay-c1-t1-red5.log
docker rm povc1-red5
tail -30 .superpowers/p-overlay-c1-t1-red5.log
```

Expected: three of the four new tests fail — `apply_badges` does not evaluate conditions yet, so the condition is ignored: `test_a_definition_whose_condition_fails_draws_exactly_the_gate_off_pixels` (the stamp is drawn on the unmatched item), `test_an_item_whose_match_outcome_changes_re_badges` (the digest does not move, since neither `values` nor an evaluated outcome changes between the two calls, so the gate short-circuits the second upload) and `test_an_unmatched_definition_never_resolves_its_image` (the URL is fetched). `test_a_definition_whose_condition_holds_is_actually_drawn` is NOT among them: with every configured definition still drawn unconditionally today, the stamp already appears on a 4k item before any of this phase's code exists — write it here to keep holding once selection exists, not to prove a gap.

- [ ] **Step 27: Wire the seam into `render/pipeline.py::apply_badges`**

Add to the imports at the top of `src/autoposter/render/pipeline.py`, beside the existing `from autoposter.overlays.sources import ...` line:

```python
from autoposter.overlays.selection import OverlayItemView, select
```

Then replace the block from `values = badge_values(render.art_kind, inputs)` through the `for definition in definitions:` loop header with:

```python
    values = badge_values(render.art_kind, inputs)

    # The SELECTION half (overlay era sub-phase C1). Evaluated BEFORE the
    # fingerprint gate on purpose: `badge_fingerprint` folds this item's own
    # match outcomes in beside the definitions digest, so an item whose
    # resolution (or, in a later slice, aspect or language count) changed
    # re-renders. If the outcomes were computed after the gate, the gate
    # could never see them and a changed item would keep its old badge
    # forever -- adjudication A4. The cost is one `json.dumps` of the
    # condition (ahead of `compiled_condition`'s cache lookup) plus a filter
    # tree walk, per CONDITIONED definition, on every unchanged item; the
    # view itself makes no Plex request of its own -- `media_info_from_plex`
    # above already reloaded the item for `.media`, and the view's own
    # accessors read `plex_item` the same reload-free way `filter_values.py`
    # does for everything else.
    definitions = config.badges.definitions
    view = OverlayItemView(media, facts=facts, plex_item=plex_item)
    matched_definitions, outcomes = select(definitions, view)

    # render.fingerprint, not render.base_sha256: the badged image is composed
    # from the *base we rendered*, so the gate has to track what went into that
    # base -- see badge_fingerprint's docstring. `definitions` is the WHOLE
    # configured list, not the matched subset: editing or removing a
    # definition this item never matched must still move the digest, or a
    # config change goes unnoticed for every item it does not currently
    # apply to.
    fingerprint = badge_fingerprint(
        render.fingerprint or "", render.art_kind, values, manifest_sha(),
        definitions, outcomes,
    )
    if fingerprint == render.badge_fingerprint and render.upload_status == "uploaded":
        return

    if await _already_in_plex(config, probe, plex_item, render, fingerprint):
        # The image Plex is serving stamped this exact fingerprint, so it is
        # byte-for-byte what compose() would produce. Record what is already
        # true and skip both the composite and the upload.
        render.badge_fingerprint = fingerprint
        render.upload_status = "uploaded"
        render.uploaded_at = func.now()
        await session.commit()
        return

    resolved_images: dict[str, Path] = {}
    usable_definitions = []
    for definition in matched_definitions:
```

The rest of the loop body, the `compose_badges` call and everything after it are unchanged. Note two consequences, both intended: an unmatched definition never reaches `resolve_image_path` (no stat, no download, no decode — Global Constraint 7), and `compose_badges` receives only matched definitions, so `_resolve_definitions`'s suppression-then-group order finally runs over the MATCHED list, which is what makes group and weight mean anything for the first time.

- [ ] **Step 28: Run to verify they pass**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-green5 test sh -c 'pytest -q tests/test_overlay_entrypoint.py tests/test_badge_pipeline.py tests/test_overlay_engine_golden.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-green5.log'
docker wait povc1-green5
docker cp povc1-green5:/app/.superpowers/p-overlay-c1-t1-green5.log .superpowers/p-overlay-c1-t1-green5.log
docker rm povc1-green5
grep -E "passed|failed" .superpowers/p-overlay-c1-t1-green5.log
```

Expected: all green, zero failures. `test_badge_pipeline.py`'s ~30 positional `apply_badges` calls must all still pass — the signature did not change.

- [ ] **Step 29: Falsify the group-resolution-over-matched claim by deliberate mutation**

The claim "suppression and group weight now arbitrate over the MATCHED list" is inherited from `_resolve_definitions`, not new code, so it cannot RED (Global Constraint 11). Prove it falsifiable instead:

1. Temporarily change Step 27's loop header back to `for definition in definitions:` and pass `definitions=usable_definitions` unchanged.
2. Re-run `pytest -q tests/test_overlay_entrypoint.py` through the container recipe above.
3. Confirm `test_a_definition_whose_condition_fails_draws_exactly_the_gate_off_pixels` and `test_an_unmatched_definition_never_resolves_its_image` FAIL.
4. Revert the mutation. Re-run. Confirm both PASS.
5. Record the mutation and both outcomes in the task report.

- [ ] **Step 30: Run the full suite and reconcile against the Step 1 baseline**

```bash
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc1-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/p-overlay-c1-t1-full.log'
docker wait povc1-full
docker cp povc1-full:/app/.superpowers/p-overlay-c1-t1-full.log .superpowers/p-overlay-c1-t1-full.log
docker rm povc1-full
tail -5 .superpowers/p-overlay-c1-t1-full.log
```

Expected: zero failures, and a pass count equal to the Step 1 baseline **plus** exactly the tests this task added. If any number is unexplained, **STOP and report** — do not proceed to T2 on an unreconciled suite.

- [ ] **Step 31: Confirm the diff touched only this task's files**

```bash
git diff --stat origin/main -- src/ tests/ assets/ docs/
```

Expected: exactly `src/autoposter/overlays/selection.py`, `src/autoposter/overlays/schema.py`, `src/autoposter/badges/compose.py`, `src/autoposter/render/pipeline.py`, `tests/test_overlay_selection.py`, `tests/test_overlay_schema.py`, `tests/test_overlay_entrypoint.py`. Anything else is a Global Constraint 17 failure.

- [ ] **Step 32: Commit and tear down**

```bash
git add src/autoposter/render/pipeline.py tests/test_overlay_entrypoint.py
git commit --no-gpg-sign -m "feat(overlays): evaluate per-definition conditions in apply_badges"
docker compose -p povc1 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

**T1 gate: this task gets an opus review before T2 starts** (`p-overlay-c-facts.md` C4). The review's brief is the A4 hashing design and the three storm pins.

---

## Task 2: The families — vendored art, shipped definitions, fires/silent pins

**Files:**
- Create: `assets/badges/images/cr/` (98 PNGs)
- Create: `assets/badges/images/Direct-Play.png`
- Create: `assets/badges/OVERLAY-MANIFEST.sha256`
- Create: `src/autoposter/overlays/families.py`
- Create: `tests/test_overlay_families.py`
- Modify: `assets/badges/PROVENANCE.md`
- Modify: `src/autoposter/config/schema.py`
- Modify: `src/autoposter/render/pipeline.py`
- Modify: `tests/test_ci_path_filters.py`

**Interfaces:**
- Consumes: `OverlayDefinition` with `condition` (T1), `overlays.selection.select` (T1), `overlays.assets.IMAGES` (`= asset_path("badges") / "images"`), `overlays.sources.resolve_image_path` (which **refuses** a `builtin:` naming a missing file — so a missing asset is a hard per-definition failure, not a silent skip), `badges.compose.manifest_sha()`.
- Produces: `overlays.families.FAMILIES: dict[str, list[OverlayDefinition]]`; `config.schema.BadgesConfig.families: list[str]`; `config.schema.BadgesConfig.all_definitions() -> list[OverlayDefinition]`.

**Scope note:** two families, not three. `versions` is fenced by **Adjudication A14** above — it cannot be selected on without a new row in `collections/filters.py`, which Global Constraint 1 forbids. Do not vendor `versions.png`, do not add `MediaInfo.version_count`, do not write a `versions` definition. T3 records the fence.

---

- [ ] **Step 1: Vendor the art out of the pinned Kometa image**

The same image `assets/badges/PROVENANCE.md` already pins — the digest the `kometa` CronJob in the `media` namespace runs, not a floating tag. Run from the repo root:

```bash
IMG="docker.io/kometateam/kometa@sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a"
CID=$(docker create --entrypoint sh "$IMG")
mkdir -p .superpowers/p-overlay-c1-vendor
docker cp "$CID:/defaults/overlays/images/cr" .superpowers/p-overlay-c1-vendor/cr
docker cp "$CID:/defaults/overlays/images/Direct-Play.png" .superpowers/p-overlay-c1-vendor/Direct-Play.png
docker cp "$CID:/defaults/overlays/content_rating_au.yml" .superpowers/p-overlay-c1-vendor/
docker cp "$CID:/defaults/overlays/content_rating_de.yml" .superpowers/p-overlay-c1-vendor/
docker cp "$CID:/defaults/overlays/content_rating_nz.yml" .superpowers/p-overlay-c1-vendor/
docker cp "$CID:/defaults/overlays/content_rating_uk.yml" .superpowers/p-overlay-c1-vendor/
docker cp "$CID:/defaults/overlays/content_rating_us_movie.yml" .superpowers/p-overlay-c1-vendor/
docker cp "$CID:/defaults/overlays/content_rating_us_show.yml" .superpowers/p-overlay-c1-vendor/
docker cp "$CID:/defaults/overlays/direct_play.yml" .superpowers/p-overlay-c1-vendor/
docker rm -f "$CID"
ls .superpowers/p-overlay-c1-vendor/cr | wc -l
ls .superpowers/p-overlay-c1-vendor/cr | sed 's/[0-9].*//' | sort | uniq -c
```

Expected: **98** files in `cr/`. Record the per-prefix counts printed by the last command in the task report.

**Known discrepancy to resolve here, not later.** `p-overlay-datasources-probe.md` §4.3 enumerates the prefixes as `au_*` (14), `de*` (18), `nz_*` (24), `uk*` (16), `us*` (22), plus `cs.png` and `mal.png` — which sums to **96**, not the 98 the same paragraph states as the directory total. The listing above is the authority; record what it actually shows, note the two files the probe's enumeration missed by name, and carry the corrected figures into `PROVENANCE.md` in Step 3. Do not paper over the gap by trusting either number.

**This step's `ls | wc -l` output is `CR_COUNT`, the one authoritative constant every later reference to "98" or "99" in this task derives from — Step 2's manifest line count, Step 3's `PROVENANCE.md` row, and Step 5's two test pins.** `98` is this document's best current estimate, not a second source of truth: if your measured `CR_COUNT` differs from 98, substitute your measured number everywhere `98` and `99` (`= CR_COUNT + 1`, for `Direct-Play.png`) appear below — do not let the two disagree, and do not treat this document's `98` as correct over what `ls` actually printed.

- [ ] **Step 2: Move the art into the asset tree and build its own manifest**

```bash
mkdir -p assets/badges/images/cr
cp .superpowers/p-overlay-c1-vendor/cr/*.png assets/badges/images/cr/
cp .superpowers/p-overlay-c1-vendor/Direct-Play.png assets/badges/images/Direct-Play.png
cd assets/badges && \
  find images/cr images/Direct-Play.png -type f | LC_ALL=C sort | xargs sha256sum > OVERLAY-MANIFEST.sha256 && \
  cd ../..
wc -l assets/badges/OVERLAY-MANIFEST.sha256
git diff --stat -- assets/badges/MANIFEST.sha256
```

Expected: `OVERLAY-MANIFEST.sha256` has `CR_COUNT + 1` lines (Step 1's measured `CR_COUNT` plus `Direct-Play.png` — **99** if `CR_COUNT` was 98), and `git diff --stat` on `MANIFEST.sha256` is **EMPTY**.

**Why a second manifest** (Global Constraint 12): `badges/compose.py::manifest_sha()` hashes `assets/badges/MANIFEST.sha256`, and that hash is a part of EVERY item's `badge_fingerprint`. Appending 99 lines there would re-fingerprint every one of the ~16k already-badged items in a library that configures no family at all — the exact mass re-render this project already paid for once. The residual is disclosed rather than hidden: replacing a `cr/` PNG's bytes without editing the config does not move any fingerprint, the same shape as the `url:`-definition residual row 97 already disclosed.

- [ ] **Step 3: Record the provenance**

In `assets/badges/PROVENANCE.md`, add these rows to the "What was taken" table, with `98` replaced by Step 1's actual measured `CR_COUNT` if it differed:

```
| `/defaults/overlays/images/cr` | `images/cr` | 98 | the six content-rating regional overlay families |
| `/defaults/overlays/images/Direct-Play.png` | `images/Direct-Play.png` | 1 | the direct_play overlay family |
```

Replace the "Deliberately **not** taken" paragraph with:

```markdown
Deliberately **not** taken: `edition/`, `network/`, `ribbon/`, `streaming/`, `studio/` — the
overlay families this deployment does not yet enable (roadmap row 100, sub-phases C2 and C3).
`cr/` and `Direct-Play.png` WERE deliberately not taken until the overlay era's sub-phase C1
shipped the families that draw them.
```

And append this section:

```markdown
## `OVERLAY-MANIFEST.sha256` — a second manifest, on purpose

`MANIFEST.sha256` covers the 513 files the nine BUILT-IN badges draw, and
`badges/compose.py::manifest_sha()` folds its hash into every item's
`badge_fingerprint` so that replacing any of them re-badges the library.

The overlay-family art (`images/cr/`, `images/Direct-Play.png`) is checksummed
separately and is **not** in that hash. Adding it there would have moved
`manifest_sha()` and re-fingerprinted every already-badged item in a library
that enables no family at all — a ~16,000-item re-render for a file nothing
draws. The trade is stated rather than hidden: replacing one of these PNGs
without editing the config moves no fingerprint, which is the same residual
`url:`-sourced overlay definitions already carry (roadmap row 97).

Regenerate with, from `assets/badges/`:

    find images/cr images/Direct-Play.png -type f | LC_ALL=C sort | xargs sha256sum > OVERLAY-MANIFEST.sha256
```

Then add `"assets/badges/OVERLAY-MANIFEST.sha256",` to `VERIFIED_NON_SOURCE` in `tests/test_ci_path_filters.py`, on the line immediately after the existing `"assets/badges/MANIFEST.sha256",` entry — the suite reads it (Step 5), so a change to it must run CI.

- [ ] **Step 4: Commit the art**

```bash
git add assets/badges/images/cr assets/badges/images/Direct-Play.png assets/badges/OVERLAY-MANIFEST.sha256 assets/badges/PROVENANCE.md tests/test_ci_path_filters.py
git commit --no-gpg-sign -m "chore(assets): vendor the cr/ and Direct-Play overlay art from the pinned Kometa image"
```

- [ ] **Step 5: Write the failing asset and manifest pins**

Create `tests/test_overlay_families.py`:

```python
"""The shipped overlay families (roadmap row 100, sub-phase C1).

Two families ship here: `direct_play` (one definition) and the six
content-rating regionals. `versions` is fenced by adjudication A14 -- its
selection needs a `versions` row in `collections/filters.py`, which this
phase may not edit.

Every number in `families.py` is transcribed from the pinned Kometa tree
(v2.4.8, the image digest `assets/badges/PROVENANCE.md` records). These tests
are the transcription's checksum: what the art is, what each definition
selects on, that every named image actually exists, and that adding the art
moved no fingerprint.
"""
import hashlib
from pathlib import Path

import pytest

from autoposter.badges.compose import manifest_sha
from autoposter.overlays.assets import ASSETS, IMAGES
from autoposter.overlays.families import FAMILIES

OVERLAY_MANIFEST = ASSETS / "OVERLAY-MANIFEST.sha256"
BUILTIN_MANIFEST = ASSETS / "MANIFEST.sha256"

# The ONE authoritative constant every count pin below derives from, set once
# from Task 2 Step 1's measured `ls .superpowers/p-overlay-c1-vendor/cr | wc -l`
# -- never restated as an independent literal. `98` is this document's
# current best measurement; if Step 1 measured differently, this is the only
# line that needs to change; every pin that depends on it moves with it.
CR_COUNT = 98


def test_the_builtin_manifest_did_not_grow_when_the_family_art_landed():
    """Global Constraint 12 and the storm guard's third arm. `manifest_sha()`
    is in every item's fingerprint; adding `CR_COUNT + 1` entries here would
    re-badge every already-uploaded item in a library that enables no
    family."""
    lines = BUILTIN_MANIFEST.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 513
    assert not [line for line in lines if "images/cr/" in line]
    assert not [line for line in lines if "Direct-Play" in line]


def test_the_family_art_has_its_own_manifest_and_it_is_accurate():
    """Not just present -- correct. Every listed checksum is recomputed."""
    lines = OVERLAY_MANIFEST.read_text(encoding="utf-8").splitlines()
    assert len(lines) == CR_COUNT + 1  # + Direct-Play.png
    for line in lines:
        digest, _, relative = line.partition("  ")
        path = ASSETS / relative
        assert path.exists(), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative


def test_the_cr_directory_holds_the_pinned_regional_count():
    assert len(list((IMAGES / "cr").glob("*.png"))) == CR_COUNT


def test_every_family_definition_names_an_image_that_exists():
    """`overlays/sources.py::resolve_image_path` REFUSES a `builtin:` naming a
    missing file, so a mistyped filename is a hard per-definition failure at
    render time. This is the transcription's real checksum: it catches every
    wrong region prefix, wrong bucket spelling and wrong colour suffix at
    once."""
    missing = []
    for family, definitions in FAMILIES.items():
        for definition in definitions:
            assert definition.builtin, f"{family}/{definition.name} names no image"
            path = IMAGES / (definition.builtin + ".png")
            if not path.exists():
                missing.append(f"{family}/{definition.name} -> {definition.builtin}")
    assert missing == []


def test_direct_play_is_one_definition_with_the_measured_box():
    """Probe section 4.5 / grammar probe section 5.1: box 305x170 -- the one
    family in row 100 with a non-105 height -- centred, bottom, offset 30."""
    definitions = FAMILIES["direct_play"]
    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.name == "Direct-Play"
    assert definition.builtin == "Direct-Play"
    assert definition.condition == {"resolution.regex": "(?i)2160|4k"}
    assert (definition.back_width, definition.back_height) == (305, 170)
    assert definition.back_color == "#00000099"
    assert definition.back_radius == 30
    assert (definition.horizontal_align, definition.horizontal_offset) == ("center", 0)
    assert (definition.vertical_align, definition.vertical_offset) == ("bottom", 30)


def test_every_regional_definition_shares_the_measured_regional_box():
    """Probe section 4.3: all six regions hard-code 305x105 r30 at
    left/15, bottom/270."""
    for family, definitions in FAMILIES.items():
        if not family.startswith("content_rating_"):
            continue
        assert definitions, family
        for definition in definitions:
            assert (definition.back_width, definition.back_height) == (305, 105), definition.name
            assert definition.back_radius == 30, definition.name
            assert definition.back_color == "#00000099", definition.name
            assert (definition.horizontal_align, definition.horizontal_offset) == ("left", 15)
            assert (definition.vertical_align, definition.vertical_offset) == ("bottom", 270)


def test_the_us_movie_g_bucket_carries_kometas_own_alias_list_verbatim():
    """The one bucket the probe banked verbatim
    (`content_rating_us_movie.yml:51`). Pinned as data, because the alias
    tables are the whole of the regional logic and a dropped alias is a
    silently-wrong badge, not a crash. Note the `gb/` and `no/` prefixes:
    those are PLEX agent spellings, and no Common Sense value in
    `item_facts` ever looks like one -- adjudication A5's evidence."""
    definitions = {d.name: d for d in FAMILIES["content_rating_us_movie"]}
    assert definitions["us_movie_g"].condition == {
        "content_rating": [
            "1", "01", "2", "02", "3", "03", "4", "04", "5", "05", "6", "06",
            "G", "G - All Ages", "U", "gb/U", "gb/0+", "E", "gb/E", "A",
            "no/A", "TV-Y", "TV-G",
        ]
    }


# --- R1: alias-list completeness beyond the one hand-pinned `us_movie_g`
# bucket above. `test_every_family_definition_names_an_image_that_exists`
# and the box pins catch a wrong `builtin:` path or a wrong position; neither
# catches a DROPPED bucket or a truncated alias list -- the failure mode the
# plan itself names as the one that matters ("a dropped alias is not a
# crash; it is a silently missing badge"). Two nets: bucket NAMES the probe
# already gives for UK and US movie (section 4.3), pinned outright below; and
# a per-family (bucket count, total alias count) pair for every family,
# counted directly off the vendored YAML while Step 7 transcribes it -- never
# estimated, never copied from this document -- so a dropped bucket or a
# short alias list fails a count instead of passing silently.

FAMILY_ALIAS_COUNTS = {
    # <family>: (bucket_count, total_alias_count) -- filled in at Task 2
    # Step 7, counted directly off the vendored
    # `content_rating_<region>.yml` while reading it out. Every entry below
    # is `None` until Step 7 replaces it; a family still `None` when Step 8
    # runs means Step 7 is not actually done for it.
    "content_rating_us_movie": None,
    "content_rating_us_show": None,
    "content_rating_uk": None,
    "content_rating_au": None,
    "content_rating_de": None,
    "content_rating_nz": None,
}


def test_every_family_transcribes_every_bucket_and_every_alias():
    """The completeness net Step 7's worked example alone cannot provide:
    every regional family's bucket count and total alias count, both counted
    directly off its own vendored YAML, not estimated from `us_movie_g`'s
    shape or guessed from this document."""
    for family, expected in FAMILY_ALIAS_COUNTS.items():
        assert expected is not None, f"{family}: record its (buckets, aliases) count"
        bucket_count, alias_count = expected
        definitions = FAMILIES[family]
        assert len(definitions) == bucket_count, family
        assert (
            sum(len(d.condition["content_rating"]) for d in definitions)
            == alias_count
        ), family


def test_the_uk_and_us_movie_bucket_names_are_all_present():
    """A spot-check beyond counts, for the two families whose bucket names
    the probe already gives by name (section 4.3): `u, pg, 12, 15, 18, r18,
    nr` for UK, `g, pg, pg-13, r, nc-17, nr` for US movie. A missing bucket
    here is a dropped CONDITION, not a typo -- an item with that
    certification draws no regional badge at all, silently."""
    uk_names = {d.name for d in FAMILIES["content_rating_uk"]}
    assert uk_names == {f"uk_{b}" for b in ("u", "pg", "12", "15", "18", "r18", "nr")}
    us_movie_names = {d.name for d in FAMILIES["content_rating_us_movie"]}
    assert us_movie_names == {
        f"us_movie_{b}" for b in ("g", "pg", "pg-13", "r", "nc-17", "nr")
    }


def test_the_six_regions_ship_and_no_more():
    assert sorted(k for k in FAMILIES if k.startswith("content_rating_")) == [
        "content_rating_au",
        "content_rating_de",
        "content_rating_nz",
        "content_rating_uk",
        "content_rating_us_movie",
        "content_rating_us_show",
    ]


def test_versions_is_not_shipped_and_the_reason_is_recorded():
    """Adjudication A14: the family needs a `versions` row in
    `collections/filters.py`, which this phase may not edit. Pinned so that
    shipping it later is a deliberate act with a ruling behind it, not a
    quiet fill-in."""
    assert "versions" not in FAMILIES
```

- [ ] **Step 6: Run to verify they fail**

```bash
docker compose -p povc2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc2-red1 test sh -c 'pytest -q tests/test_overlay_families.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t2-red1.log'
docker wait povc2-red1
docker cp povc2-red1:/app/.superpowers/p-overlay-c1-t2-red1.log .superpowers/p-overlay-c1-t2-red1.log
docker rm povc2-red1
tail -20 .superpowers/p-overlay-c1-t2-red1.log
```

Expected: collection error — `ModuleNotFoundError: No module named 'autoposter.overlays.families'`. The two manifest tests would pass on their own; they are RED only through the import.

- [ ] **Step 7: Transcribe the family definitions into `overlays/families.py`**

This is a **data-entry step with a named source and a pinned verification**, not a design step. The source files were copied to `.superpowers/p-overlay-c1-vendor/` in Step 1. Read them; do not write an alias list from memory, and do not invent a bucket the file does not have. If a file's shape differs from what is described here, **STOP and report the difference** rather than smoothing it.

Per regional file, read out and transcribe:

- the region prefix and the image `value:` template (`content_rating_us_movie.yml:37` is `cr/<region><<overlay_name>><<inside_color>>`, where `inside_color` is `"c"` by default), which yields each definition's `builtin:` — e.g. bucket `g` in `us_movie` is `cr/usgc`;
- every bucket name and its comma-separated alias list, split on `", "` into a Python list of strings, **verbatim**, including the numeric aliases and the `gb/`/`no/` prefixed ones;
- whether the file sets `group:`/`weight:` on its overlays. Transcribe them if present; **do not invent a group if the file has none** — with disjoint alias buckets only one can match anyway, and inventing one would change upstream behaviour for an operator who enables two regions.

Create `src/autoposter/overlays/families.py`:

```python
"""The shipped overlay families (roadmap row 100, sub-phase C1).

Flat `OverlayDefinition` lists, not templates. Kometa expresses each family
as a templated YAML file whose `<<key>>` resolver this service does not
implement (and does not need: the recon's cut emits flat definitions), so
every number here is the RESOLVED value, transcribed from the pinned
v2.4.8 tree -- the same image digest `assets/badges/PROVENANCE.md` records,
copied out in this phase's Task 2 Step 1.

Two families ship. `versions` does not: its selection is Plex's `duplicate`
smart-search field, whose row in `collections/filters.py` is `search-only`
and `filterable=False`, and there is no `versions` row at all (it is one of
the 44 filter names with no Plex search field, named in that module's own
comment). Shipping it needs a new table row, which is an adjudication rather
than an edit -- see the C1 plan's "Adjudication A14".

Each family is opt-in through `config.badges.families`, and a family that is
not named costs nothing: no definition, no fingerprint movement, no asset
read.
"""
from autoposter.overlays.schema import OverlayDefinition

# Probe section 4.5 and grammar probe section 5.1 (`direct_play.yml`,
# verbatim in full there). One overlay. The box is 305x170 -- the one family
# in row 100 with a non-105 height -- and the image key defaults to the
# overlay's OWN NAME, `Direct-Play`, which is why the definition's `builtin:`
# and `name` are the same string. `resolution.regex` is Kometa's own
# selection, and `tag` carries `.regex` in this service's operator set too.
DIRECT_PLAY: list[OverlayDefinition] = [
    OverlayDefinition(
        name="Direct-Play",
        builtin="Direct-Play",
        condition={"resolution.regex": "(?i)2160|4k"},
        horizontal_align="center", horizontal_offset=0,
        vertical_align="bottom", vertical_offset=30,
        back_width=305, back_height=170,
        back_color="#00000099", back_radius=30,
    ),
]

# The six content-rating regionals. Probe section 4.3: all six share one
# shape -- a per-bucket comma-separated ALIAS LIST matched against
# `item.contentRating`, the Plex string, and nothing else. No API, no fact
# lookup.
#
# ADJUDICATION A5, and it is the whole reason these lists look the way they
# do: this matches PLEX's own certification, NOT `item_facts.content_rating`,
# which is MDBList's Common Sense AGE rating. Kometa's buckets carry Plex
# agent spellings -- `gb/U`, `gb/0+`, `no/A` -- that Common Sense never
# emits, and `collections/filters.py`'s own `content_rating` row says so in
# as many words. `overlays/selection.py::OverlayItemView` reads
# `plex_item.contentRating` for exactly this reason.
#
# The alias lists below are transcribed VERBATIM from the pinned files,
# including their numeric aliases. A dropped alias is not a crash; it is a
# silently missing badge on the items that carried it.
CONTENT_RATING_US_MOVIE: list[OverlayDefinition] = [
    OverlayDefinition(
        name="us_movie_g",
        builtin="cr/usgc",
        condition={"content_rating": [
            "1", "01", "2", "02", "3", "03", "4", "04", "5", "05", "6", "06",
            "G", "G - All Ages", "U", "gb/U", "gb/0+", "E", "gb/E", "A",
            "no/A", "TV-Y", "TV-G",
        ]},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
    # ... one entry per remaining bucket in `content_rating_us_movie.yml`
    # (`pg`, `pg-13`, `r`, `nc-17`, `nr` per probe section 4.3), each built
    # exactly like the one above: `name` is `us_movie_<bucket>`, `builtin` is
    # the file's own resolved `cr/<region><bucket>c`, `condition` is that
    # bucket's alias list split on ", ", and the six box/position values are
    # identical.
]

CONTENT_RATING_US_SHOW: list[OverlayDefinition] = [
    # One entry per bucket in `content_rating_us_show.yml`, same shape;
    # `name` is `us_show_<bucket>`. A SEPARATE file from us_movie upstream,
    # with its own board (TV-Y/TV-G/TV-PG/TV-14/TV-MA and friends).
]

CONTENT_RATING_UK: list[OverlayDefinition] = [
    # One entry per bucket in `content_rating_uk.yml` -- `u`, `pg`, `12`,
    # `15`, `18`, `r18`, `nr` per probe section 4.3 -- `name` is
    # `uk_<bucket>`.
]

CONTENT_RATING_AU: list[OverlayDefinition] = [
    # One entry per bucket in `content_rating_au.yml`; `name` is
    # `au_<bucket>`.
]

CONTENT_RATING_DE: list[OverlayDefinition] = [
    # One entry per bucket in `content_rating_de.yml`; `name` is
    # `de_<bucket>`.
]

CONTENT_RATING_NZ: list[OverlayDefinition] = [
    # One entry per bucket in `content_rating_nz.yml`; `name` is
    # `nz_<bucket>`.
]

FAMILIES: dict[str, list[OverlayDefinition]] = {
    "direct_play": DIRECT_PLAY,
    "content_rating_au": CONTENT_RATING_AU,
    "content_rating_de": CONTENT_RATING_DE,
    "content_rating_nz": CONTENT_RATING_NZ,
    "content_rating_uk": CONTENT_RATING_UK,
    "content_rating_us_movie": CONTENT_RATING_US_MOVIE,
    "content_rating_us_show": CONTENT_RATING_US_SHOW,
}

__all__ = ["FAMILIES"]
```

**The six `# ...` comment bodies above are the transcription's work, not a licence to guess.** Each expands into complete `OverlayDefinition(...)` calls in exactly the shape of `us_movie_g`, with the bucket names, alias strings, `builtin:` paths and (if present) `group`/`weight` read out of the vendored YAML. `test_every_family_definition_names_an_image_that_exists` and the box pins in Step 5 fail loudly on a wrong path or a wrong position, but neither one catches a dropped bucket or a truncated alias list — **while reading each region's YAML, also count its buckets and total aliases and fill in `FAMILY_ALIAS_COUNTS` in `tests/test_overlay_families.py`** (Step 5) with the real `(bucket_count, alias_count)` pair, counted directly off that file, never estimated. Before moving on, delete every `# ...` placeholder comment: a shipped file must not carry one, and no entry in `FAMILY_ALIAS_COUNTS` may still be `None`.

- [ ] **Step 8: Run the family pins to verify they pass**

```bash
docker compose -p povc2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc2-green1 test sh -c 'pytest -q tests/test_overlay_families.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t2-green1.log'
docker wait povc2-green1
docker cp povc2-green1:/app/.superpowers/p-overlay-c1-t2-green1.log .superpowers/p-overlay-c1-t2-green1.log
docker rm povc2-green1
tail -20 .superpowers/p-overlay-c1-t2-green1.log
```

Expected: all green. In particular `test_every_family_definition_names_an_image_that_exists` must pass with an empty `missing` list — if it names entries, the transcription's `builtin:` paths are wrong and must be re-read from the YAML, not adjusted until the test agrees. Same discipline for `test_every_family_transcribes_every_bucket_and_every_alias`: if a count disagrees, re-read that region's YAML and fix `families.py` or `FAMILY_ALIAS_COUNTS` — whichever one is wrong — never adjust the count to make the assertion pass without re-checking the source.

- [ ] **Step 9: Commit the definitions**

```bash
git add src/autoposter/overlays/families.py tests/test_overlay_families.py
git commit --no-gpg-sign -m "feat(overlays): ship the direct_play and content-rating regional families"
```

- [ ] **Step 10: Write the failing config-surface tests**

Append to `tests/test_overlay_families.py`:

```python
# --- the operator surface: `badges.families` -------------------------------

from pydantic import ValidationError

from autoposter.config.schema import BadgesConfig
from autoposter.overlays.schema import OverlayDefinition


def test_no_families_is_the_default_and_changes_nothing():
    """Gate-off. `all_definitions()` must be exactly `definitions` -- the
    same list every existing caller already reads."""
    config = BadgesConfig()
    assert config.families == []
    assert config.all_definitions() == []


def test_naming_a_family_expands_it_in_front_of_the_operators_own_definitions():
    """Family first, operator second, so an operator's own definition can
    suppress a family member with `suppress_overlays`."""
    own = OverlayDefinition(name="mine")
    config = BadgesConfig(families=["direct_play"], definitions=[own])
    expanded = config.all_definitions()
    assert expanded[-1] is own
    assert [d.name for d in expanded[:-1]] == ["Direct-Play"]


def test_expansion_is_not_stored_so_a_config_round_trip_cannot_double_it():
    """`definitions` itself is never mutated: the config editor round-trips
    config -> YAML -> config, and an expansion written back into
    `definitions` would be re-expanded on the next load."""
    config = BadgesConfig(families=["direct_play"])
    config.all_definitions()
    assert config.definitions == []


def test_an_unknown_family_is_refused_at_load_naming_what_exists():
    with pytest.raises(ValidationError) as caught:
        BadgesConfig(families=["ribbon"])
    message = str(caught.value)
    assert "ribbon" in message
    assert "direct_play" in message
    assert "content_rating_uk" in message


def test_naming_the_same_family_twice_is_refused():
    """Two copies of every definition would draw each one twice and double
    every group's members."""
    with pytest.raises(ValidationError):
        BadgesConfig(families=["direct_play", "direct_play"])
```

- [ ] **Step 11: Run to verify they fail**

```bash
docker compose -p povc2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc2-red2 test sh -c 'pytest -q tests/test_overlay_families.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t2-red2.log'
docker wait povc2-red2
docker cp povc2-red2:/app/.superpowers/p-overlay-c1-t2-red2.log .superpowers/p-overlay-c1-t2-red2.log
docker rm povc2-red2
tail -25 .superpowers/p-overlay-c1-t2-red2.log
```

Expected: the five new tests fail — `BadgesConfig` has no `families` field (`Extra inputs are not permitted`) and no `all_definitions` attribute.

- [ ] **Step 12: Add the config surface**

In `src/autoposter/config/schema.py`, add immediately after the `definitions` field (which currently ends at `:932`):

```python
    # Roadmap row 100, sub-phase C1. A family is a bundle of definitions this
    # service ships, transcribed from the pinned Kometa tree
    # (`overlays/families.py`) -- ~40 content-rating definitions is not
    # something an operator writes by hand, so naming the family is the
    # surface. Empty by default, which is byte-identical to no feature at
    # all: `all_definitions()` returns `definitions` unchanged.
    families: list[str] = Field(
        default_factory=list,
        description=(
            "Bundled overlay families to draw, by name -- each expands into "
            "this service's own transcription of Kometa's definitions for it, "
            "drawn before the operator's own definitions so those can suppress "
            "a family member."
        ),
    )
```

Add this validator to `BadgesConfig` (beside its other validators; if the class has none, add it after the last field):

```python
    @model_validator(mode="after")
    def _check_families(self) -> "BadgesConfig":
        """Refuse an unknown or repeated family name at config load.

        Naming what exists rather than saying "unknown": the list is closed
        and short, and an operator who typed `ribbon` needs to be told it is
        roadmap row 8a's rather than left to guess at a spelling.

        The empty-`families` return is above the import, not just above the
        loop, and that ordering is load-bearing: this validator runs on
        EVERY `BadgesConfig` construction, including the ones `families`
        never touches, and `actions/flags.py` imports `config.schema` on
        every Action Center queue/summary request. Importing
        `overlays/families.py` constructs ~40 `OverlayDefinition`s at module
        scope, and each one's own `_validate` call-imports
        `overlays.selection`, which imports `collections.filters`, which
        imports `langcodes` -- exactly the transitive weight
        `overlays/schema.py::_as_rgba`'s docstring already keeps off this
        path. An operator who never names a family must not pay for one.
        """
        if not self.families:
            return self

        from autoposter.overlays.families import FAMILIES

        for name in self.families:
            if name not in FAMILIES:
                raise ValueError(
                    f"{name!r} is not a bundled overlay family. Available: "
                    + ", ".join(sorted(FAMILIES))
                )
        duplicates = sorted({n for n in self.families if self.families.count(n) > 1})
        if duplicates:
            raise ValueError(
                "each overlay family may be named once; repeated: "
                + ", ".join(duplicates)
            )
        return self

    def all_definitions(self) -> list["OverlayDefinition"]:
        """Every definition this config draws: the named families, then the
        operator's own.

        Computed rather than folded into ``definitions`` at load, and that is
        deliberate: the config editor round-trips config -> YAML -> config,
        so an expansion written back into ``definitions`` would be expanded
        again on the next load and every family member would draw twice.

        Families come FIRST so an operator's own definition can name a family
        member in ``suppress_overlays`` -- suppression is resolved before
        group weight (``badges/compose.py::_resolve_definitions``), so it
        wins regardless of order, but reading order matching drawing order is
        what an operator expects.
        """
        from autoposter.overlays.families import FAMILIES

        expanded: list[OverlayDefinition] = []
        for name in self.families:
            expanded.extend(FAMILIES[name])
        return expanded + list(self.definitions)
```

If `model_validator` is not already imported in `config/schema.py`, add it to the existing `from pydantic import ...` line rather than adding a second import line.

- [ ] **Step 13: Point `apply_badges` at `all_definitions()`**

In `src/autoposter/render/pipeline.py::apply_badges`, change the single line

```python
    definitions = config.badges.definitions
```

(the one T1 Step 27 placed above the `view = ...` line) to:

```python
    # `all_definitions()`, not `.definitions`: a named family's definitions
    # are drawn too, and they must be in the list the fingerprint hashes as
    # well as in the list that gets selected over -- enabling a family has to
    # re-badge exactly like adding a definition by hand does.
    definitions = config.badges.all_definitions()
```

There is no other read of `config.badges.definitions` in this function after T1 (the fingerprint call and the selection call both use this local).

- [ ] **Step 14: Run to verify the config tests pass**

```bash
docker compose -p povc2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc2-green2 test sh -c 'pytest -q tests/test_overlay_families.py tests/test_config_safety.py tests/test_api_config_editor.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t2-green2.log'
docker wait povc2-green2
docker cp povc2-green2:/app/.superpowers/p-overlay-c1-t2-green2.log .superpowers/p-overlay-c1-t2-green2.log
docker rm povc2-green2
grep -E "passed|failed" .superpowers/p-overlay-c1-t2-green2.log
```

Expected: all green. The config-editor suite is run here because `BadgesConfig` gained a field and that page enumerates them.

- [ ] **Step 15: Commit the config surface**

```bash
git add src/autoposter/config/schema.py src/autoposter/render/pipeline.py tests/test_overlay_families.py
git commit --no-gpg-sign -m "feat(config): opt into bundled overlay families by name"
```

- [ ] **Step 16: Write the failing fires/silent pins per family, through the real entry point**

Append to `tests/test_overlay_entrypoint.py`:

```python
# --- per-family fires/silent, through the REAL apply_badges. One shaped item
# and one healthy item per family, plus the A5 divergence test. ------------


class _FakePlexItemRated(_FakePlexItem):
    """1080p, with a Plex certification. `contentRating` is what the six
    regionals select on."""

    def __init__(self, content_rating="PG-13"):
        super().__init__()
        self.contentRating = content_rating


class _CommonSenseFacts:
    """`item_facts` as `facts/mdblist.py::parse_content_rating` writes it:
    `content_rating` is a Common Sense AGE, not a certification.
    `"G - All Ages"` is IN Kometa's us_movie `g` bucket verbatim (the same
    bucket `"3"` is in), which is precisely why this makes a sharp
    divergence test -- and, unlike `"3"`, it is not itself a digit, so
    `badges/values.py::commonsense_text` answers `None` for it (any
    non-numeric string that is not `"NR"` does) and it draws no commonsense
    badge of its own. That matters here ONLY because the commonsense badge
    is a SEPARATE badge this same `facts.content_rating` field also feeds
    (`pipeline.py:1321`) -- if it drew, its own text would move between
    `divergent` and `plex_only` below for a reason that has nothing to do
    with which regional badge sourced correctly, and the `_sha` equality
    this test needs would fail for the wrong reason."""

    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = "G - All Ages"


class _FactsNoContentRating:
    """`item_facts` with no Common Sense rating at all -- what "Plex alone"
    genuinely looks like, as opposed to "Plex plus some OTHER Common Sense
    value" (a numeric one would draw its own commonsense badge and break the
    same equality `_CommonSenseFacts` above is built to avoid breaking)."""

    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = None


async def _badged(session, config, plex_item, rating_key, facts=None):
    item, render = await _render(session, rating_key=rating_key)
    await apply_badges(
        session, config, render, item, plex_item, facts or _Facts()
    )
    return plex_item.last_bytes


async def test_direct_play_fires_on_a_4k_item_and_is_silent_on_a_1080_one(
    session, config_with_badges
):
    config_with_badges.badges.families = []
    baseline = await _badged(session, config_with_badges, _FakePlexItem(), "dp-base")

    config_with_badges.badges.families = ["direct_play"]
    silent = await _badged(session, config_with_badges, _FakePlexItem(), "dp-1080")
    fires = await _badged(session, config_with_badges, _FakePlexItem4k(), "dp-4k")

    assert _sha(silent) == _sha(baseline), "1080p must draw the gate-off pixels"
    assert _sha(fires) != _sha(baseline), "4k must actually draw Direct-Play"


async def test_a_regional_fires_on_its_bucket_and_is_silent_off_it(
    session, config_with_badges
):
    config_with_badges.badges.families = []
    baseline = await _badged(
        session, config_with_badges, _FakePlexItemRated("PG-13"), "cr-base"
    )

    config_with_badges.badges.families = ["content_rating_us_movie"]
    fires = await _badged(
        session, config_with_badges, _FakePlexItemRated("PG-13"), "cr-pg13"
    )
    silent = await _badged(
        session, config_with_badges, _FakePlexItemRated("Unrated Nonsense"), "cr-none"
    )

    assert _sha(fires) != _sha(baseline), "PG-13 must draw its regional badge"
    assert _sha(silent) == _sha(baseline), "a certification in no bucket draws nothing"


async def test_the_regionals_read_plexs_certification_not_item_facts_common_sense(
    session, config_with_badges
):
    """Adjudication A5's divergence test, and it is sharp on purpose: the
    item carries Plex `PG-13` AND an item_facts Common Sense age of
    `"G - All Ages"`, which is IN Kometa's us_movie `g` bucket. If this view
    read `item_facts.content_rating`, the `g` badge would draw. It must draw
    the `pg-13` one.

    `divergent` and `plex_only` are built to draw the SAME commonsense badge
    as each other (neither's `facts.content_rating` is a digit, so neither
    draws one at all -- see `_CommonSenseFacts` and `_FactsNoContentRating`
    above) precisely so that `_sha` equality below isolates the REGIONAL
    badge's sourcing and nothing else."""
    config_with_badges.badges.families = ["content_rating_us_movie"]
    divergent = await _badged(
        session, config_with_badges, _FakePlexItemRated("PG-13"),
        "cr-divergent", facts=_CommonSenseFacts(),
    )

    # The same item as Plex sees it, with no Common Sense value at all.
    plex_only = await _badged(
        session, config_with_badges, _FakePlexItemRated("PG-13"),
        "cr-plex-only", facts=_FactsNoContentRating(),
    )
    # And the item as item_facts alone would have described it -- "3" here,
    # not `_CommonSenseFacts`'s own value, is fine: this call only needs SOME
    # `g`-bucket certification to demonstrate the bucket draws differently,
    # and it does not participate in the `_sha` equality above.
    as_common_sense = await _badged(
        session, config_with_badges, _FakePlexItemRated("3"), "cr-as-cs"
    )

    assert _sha(divergent) == _sha(plex_only), (
        "the Common Sense value must not change what is drawn"
    )
    assert _sha(divergent) != _sha(as_common_sense), (
        "reading item_facts.content_rating would have drawn the g badge"
    )


async def test_enabling_a_family_re_badges_an_already_uploaded_item(
    session, config_with_badges
):
    """The same law Finding 1 established for a hand-written definition: a
    family named on an already-uploaded render must reach Plex."""
    config_with_badges.badges.families = []
    item, render = await _render(session, rating_key="family-rebadge")
    plex_item = _FakePlexItem4k()
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 1
    before = render.badge_fingerprint

    config_with_badges.badges.families = ["direct_play"]
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 2
    assert render.badge_fingerprint != before


async def test_enabling_no_family_moves_no_fingerprint(
    session, config_with_badges
):
    """The storm guard at the family surface: a second pass with the same
    empty `families` must not re-upload."""
    config_with_badges.badges.families = []
    item, render = await _render(session, rating_key="family-storm")
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 1
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 1, "an unchanged config must not re-badge"
```

- [ ] **Step 17: Run to verify they fail, then pass**

```bash
docker compose -p povc2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc2-fam test sh -c 'pytest -q tests/test_overlay_entrypoint.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t2-fam.log'
docker wait povc2-fam
docker cp povc2-fam:/app/.superpowers/p-overlay-c1-t2-fam.log .superpowers/p-overlay-c1-t2-fam.log
docker rm povc2-fam
tail -25 .superpowers/p-overlay-c1-t2-fam.log
```

The T1 seam plus Steps 7–13 already make these pass; run them as the GREEN confirmation. If any FAILS, the transcription is wrong (most likely a `builtin:` path or an alias missing from the bucket) — fix `families.py` against the vendored YAML, never the test's expectation.

- [ ] **Step 18: Falsify each family by transient mutation**

Row 97 falsified each of its nine badges before trusting the pin; row 50 did the same. Do it here, one family at a time, recording each in the task report:

1. In `families.py`, change `DIRECT_PLAY`'s condition to `{"resolution.regex": "(?i)720"}`. Re-run `tests/test_overlay_entrypoint.py`. Confirm `test_direct_play_fires_on_a_4k_item_and_is_silent_on_a_1080_one` FAILS. Revert; confirm PASS.
2. In `families.py`, drop `"PG-13"` (or whichever alias the vendored file actually spells) from the `us_movie` `pg-13` bucket. Re-run. Confirm `test_a_regional_fires_on_its_bucket_and_is_silent_off_it` FAILS. Revert; confirm PASS.
3. In `overlays/selection.py`, change `get("content_rating")` to return `getattr(self._facts, "content_rating", None)`. Re-run. Confirm `test_the_regionals_read_plexs_certification_not_item_facts_common_sense` FAILS. Revert; confirm PASS. **This is the A5 mutation and it is the most important of the three** — it is the exact bug the adjudication exists to prevent.

- [ ] **Step 19: Run the full suite and reconcile**

```bash
docker compose -p povc2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc2-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/p-overlay-c1-t2-full.log'
docker wait povc2-full
docker cp povc2-full:/app/.superpowers/p-overlay-c1-t2-full.log .superpowers/p-overlay-c1-t2-full.log
docker rm povc2-full
tail -5 .superpowers/p-overlay-c1-t2-full.log
```

Expected: zero failures; pass count equals T1's measured total plus this task's additions. The golden gate, the five badge pins and the storm-pin literal must all still be green and **unmodified** — confirm with:

```bash
git diff --stat origin/main -- tests/test_badge_spec.py tests/test_badge_geometry.py tests/test_badge_draw.py tests/test_badge_compose.py tests/test_badge_parity.py tests/test_overlay_engine_golden.py assets/badges/MANIFEST.sha256 src/autoposter/collections/filters.py
```

Expected: **empty output**. Any line here is a Global Constraint 1/3/4/12 failure — **STOP and report**.

- [ ] **Step 20: Commit and tear down**

```bash
git add tests/test_overlay_entrypoint.py
git commit --no-gpg-sign -m "test(overlays): per-family fires/silent pins and the A5 divergence proof"
docker compose -p povc2 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## Task 3: Wrap — the ledger, the correction, the fence, the PR

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (row 100 only)
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:** none produced; this task ships no code.

---

- [ ] **Step 1: Correct row 100's cell and give it a sub-phase ledger**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, row 100's second cell currently reads:

```
aspect, language_count, content-rating regionals, direct_play, network/studio, ribbon, status (TVDb), streaming (TMDB watch providers), versions
```

Replace that cell's text with (keeping the row's other four cells exactly as they are, and keeping the row status **OPEN** — C1 is the first of several sub-phases):

```
aspect, language_count, content-rating regionals, direct_play, network/studio, ribbon, status (**TMDb**, not TVDb — see the correction below), streaming (TMDB watch providers), versions. **The row decomposes into sub-phases (`p-overlay-c-recon.md`, ratified in `p-overlay-c-facts.md` C1); C1 has shipped and the rest are named.** **The structural finding that reordered the phase:** the engine had no SELECTION half at all — `OverlayDefinition` carried no condition, filter or applicability field, and `badges/compose.py::_draw_definitions` drew every configured definition on every badged item, so no family in this row was expressible until a per-definition selection mechanism existed. **C1 — SHIPPED:** the selection seam (`OverlayDefinition.condition`, parsed and refused at config load by `overlays/selection.py::parse_condition`, which reuses `collections/filters.py`'s oracle-proven `parse_filters`/`evaluate` rather than growing a second dialect — adjudication A3) over a NEW overlay-side `OverlayItemView` on the `(MediaInfo, ItemFacts, plex_item)` triple `apply_badges` already holds, deliberately not `collections/filter_values.py::PlexItemView` (whose "never cost a Plex request" rule scopes four attributes the badge path has already paid past — adjudication A3b, answered by construction: a show simply has no `media`, which the tag missing-value rule already handles, so no `kinds` column needed relaxing); `badge_fingerprint` extended to fold the PER-ITEM match outcome in beside the definitions digest (adjudication A4 — without it an item whose resolution or aspect changed keeps its old badge forever, because the CONFIG did not move), under the same non-empty guard that keeps the gate-off literal and every unconditioned config bit-identical; and two families as shipped, opt-in `badges.families` bundles — `direct_play` and the six content-rating regionals (`au`, `de`, `nz`, `uk`, `us_movie`, `us_show` — **six files ship upstream, not four**), with 99 PNGs vendored from the pinned v2.4.8 image into their own `assets/badges/OVERLAY-MANIFEST.sha256` precisely so that `manifest_sha()` did not move and no already-badged item re-rendered for art it does not draw. The regionals match **Plex's own `contentRating`**, never `item_facts.content_rating` (adjudication A5: the latter is MDBList's Common Sense AGE rating, and Kometa's alias buckets carry Plex agent spellings like `gb/U` and `no/A` that Common Sense never emits) — pinned by a divergence test against an item carrying both values, and falsified by deliberate mutation. **C1's own fence, and it is an adjudication rather than a gap:** `versions` was scoped into C1 and did NOT ship. Its selection is Plex's `duplicate` field, whose `collections/filters.py` row is `search-only`/`filterable=False`, and there is no `versions` row at all — it is one of the 44 filter-only names that module's own comment already anticipates arriving "under a table row". Shipping it needs that row, which is a change to a Kometa-pinned shared vocabulary that phase 9b's search translation would also answer for, so it was raised rather than taken. **Still open, and named:** C2a the MDBList ratings parse (the highest value-per-line item in the row — the `ratings` array already arrives in a response the client already fetches, so eleven rating variables cost zero new HTTP) plus the four `plex_*` and the imdb/tmdb/user aliases; C2b `aspect` (a new `float` filter attribute — adjudication A11) and `language_count` (as derived int attributes rather than a new tag operator class — adjudication A8); C2c `status`; C3 `network`/`studio` (data held, cost is ~2700 lines of transcription and two large asset trees — adjudication A10 recommends operator-supplied first) and `streaming`, DEFERRED pending its own design because it is a per-library MEMBERSHIP SET and not a per-item predicate, which the badge pipeline has no concept of (adjudication A9). **FENCED pending one operator decision** (adjudication A2): 12 rating sources across 6 services, put to the operator once as four separable asks — Trakt (OAuth2, the best value-per-cost in the bucket, since `trakt_user_rating` is one whole-library fetch), OMDb (a query-parameter key, free, with a hard 1000/day cap that makes it near-useless at this library's 16k size — say so when asking), the AniDB+MAL pair (only if anime ratings matter, and then the `Convert.ids_to_anidb` mapping layer must be probed and sized first), and Serializd+Floppy (whose upstream modules were never fetched, so there is no honest question to ask yet). `ribbon` routes to row 8a. **Nothing in C1–C3 depends on any of it**, and no shipped Kometa default requires it either — `ratings.yml`'s own defaults are `rating1/2/3: none`. **Two upstream defects deliberately NOT ported:** `anidb_rating` is dead upstream (`plex.py:2570` tests a string `rating_sources` never produces, so it always raises) — the NAME ships in this service's vocabulary because dropping it would diverge from the pin, but no fetch will ever be written for it and the practical behaviour, a warned per-item skip, is already correct; and `omdb_rating`/`omdb_imdb_rating` are aliases upstream that both fall to the same `else` — if OMDb is ever un-fenced, they must resolve to genuinely distinct fields or the alias must be stated in a comment rather than arrived at by accident. **The TVDb→TMDb correction (A12):** this row said `status (TVDb)`. It is **TMDb** — `defaults/overlays/status.yml:66` filters `tmdb_status`, resolved from `TMDbShow.status` against `discover_status`; TVDb is never consulted. The same citation-honesty discipline row 50's cell already applied
```

- [ ] **Step 2: Verify the row edit touched nothing else**

```bash
git diff --stat -- docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git diff -- docs/superpowers/specs/2026-08-22-full-parity-roadmap.md | grep -c '^[+-]'
```

Expected: one file, and exactly two changed lines (one `-`, one `+`) plus the diff header. If more lines moved, an adjacent row was reflowed — revert and redo the edit surgically.

- [ ] **Step 3: Add the progress entry**

Append to `.superpowers/sdd/progress.md`, in the file's existing entry format:

```markdown
## 2026-09-03 — overlay era, sub-phase C1 (roadmap row 100, first slice)

The overlay engine got its selection half. `OverlayDefinition.condition`
reuses `collections/filters.py`'s parse/evaluate (adjudication A3 — no second
dialect) over a new overlay-side `OverlayItemView` on the
`(MediaInfo, ItemFacts, plex_item)` triple `apply_badges` already holds;
`badge_fingerprint` folds the per-item match outcome in beside the
definitions digest (A4), guarded so the pinned gate-off literal and every
unconditioned config stay bit-identical. Two families ship as opt-in
`badges.families` bundles: `direct_play` and the six content-rating
regionals, on 99 PNGs vendored into their own manifest so `manifest_sha()`
did not move.

The regionals match Plex's `contentRating`, never `item_facts.content_rating`
(A5) — proven by a divergence test on an item carrying a Common Sense value
in Kometa's `g` bucket alongside a Plex `PG-13`, and falsified by mutating
the accessor.

**Raised, not taken: adjudication A14.** `versions` was scoped into C1 and
did not ship. Its selection needs a `versions` row in
`collections/filters.py` (the `duplicate` row is `search-only` /
`filterable=False`, and `versions` is one of the 44 filter-only names that
module's own comment anticipates). That is a change to a Kometa-pinned
shared vocabulary phase 9b would also answer for, so it goes to the
controller with a ready-to-rule one-row recipe rather than being decided
here.

**Awaiting the operator, once:** the fenced new-integration bucket (A2) —
Trakt / OMDb-with-its-1000-a-day-cap / AniDB+MAL-as-a-pair /
Serializd+Floppy-unprobed. Nothing in C1–C3 depends on any of it.
```

- [ ] **Step 4: Run the docs-adjacent suite**

```bash
docker compose -p povc3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc3-docs test sh -c 'pytest -q tests/test_ci_path_filters.py tests/test_routes.py 2>&1 | tee /app/.superpowers/p-overlay-c1-t3-docs.log'
docker wait povc3-docs
docker cp povc3-docs:/app/.superpowers/p-overlay-c1-t3-docs.log .superpowers/p-overlay-c1-t3-docs.log
docker rm povc3-docs
grep -E "passed|failed" .superpowers/p-overlay-c1-t3-docs.log
```

Expected: all green.

- [ ] **Step 5: Run the full suite one last time (the whole-branch gate)**

```bash
docker compose -p povc3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povc3-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/p-overlay-c1-t3-full.log'
docker wait povc3-full
docker cp povc3-full:/app/.superpowers/p-overlay-c1-t3-full.log .superpowers/p-overlay-c1-t3-full.log
docker rm povc3-full
tail -5 .superpowers/p-overlay-c1-t3-full.log
docker compose -p povc3 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

Expected: zero failures, and a pass count reconciled against T1 Step 1's measured baseline plus everything C1 added. **This is the whole-branch gate; it gets an opus review** (`p-overlay-c-facts.md` C4).

- [ ] **Step 6: Re-verify the merge tree against `origin/main` (adjudication A13)**

#138 was open when this plan was written, touching `render/pipeline.py::process_item` — a different function from `apply_badges`. A textual collision in that file is possible; a semantic one is not.

```bash
git fetch origin
git merge-tree $(git merge-base HEAD origin/main) HEAD origin/main | grep -c '^<<<<<<<' || echo "NO CONFLICTS"
```

If conflicts are reported in `render/pipeline.py`, rebase onto `origin/main`, resolve them (both hunks keep their own function), re-run Step 5's full suite, and record the resolution in the PR body.

- [ ] **Step 7: Commit the wrap**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md .superpowers/sdd/progress.md
git commit --no-gpg-sign -m "docs(roadmap): row 100 gets a sub-phase ledger; status is TMDb, not TVDb"
```

- [ ] **Step 8: Prepare the PR body**

Push the branch and open the PR with exactly this body (no AI attribution anywhere — Global Constraint 16):

```bash
git push -u origin feat/overlay-families
```

```markdown
## Overlay families — sub-phase C1: the selection seam, direct_play, the six content-rating regionals

Roadmap row 100's first slice. The row's structural finding is that the
overlay engine had no **selection** half at all: `OverlayDefinition` carried
no condition of any kind and `_draw_definitions` drew every configured
definition on every badged item, so not one family in the row was
expressible. Eight `aspect` definitions with no condition all draw on every
poster, and group/weight cannot save that — with no conditions the same
highest-weight member wins on every item.

### What ships

- **`OverlayDefinition.condition`** — the same filter grammar a collection's
  `filters:` block uses, **imported** from `collections/filters.py` rather
  than re-implemented. That parser and evaluator were proved against Kometa
  2.4.8's own filter code, member list against member list; a second dialect
  would be the "same name, different filter" class that module's docstring
  already rules out. `collections/filters.py` itself is untouched.
- **`overlays/selection.py::OverlayItemView`** — an item view over the
  `(MediaInfo, ItemFacts, plex_item)` triple `apply_badges` already holds.
  Deliberately not `collections/filter_values.py::PlexItemView`: that view
  is bound by "reading a filter value never costs a Plex request" because
  the collections engine holds partial objects, and the badge path has
  already paid past that — `media_info_from_plex` reloads the item and walks
  `part.streams` before this runs. Its vocabulary is narrower than the
  collections block's on purpose, and an attribute outside it is refused at
  config load naming what is available, never answered as `None`.
- **`badge_fingerprint(..., outcomes=)`** — this item's own match results,
  folded in beside the definitions digest. Without it an item whose
  resolution changed keeps its old badge forever, because the *config* did
  not move.
- **Two families**, opt-in by name through `badges.families`: `direct_play`
  and the six content-rating regionals (`au`, `de`, `nz`, `uk`, `us_movie`,
  `us_show` — six files ship upstream, not four), on 99 PNGs vendored from
  the pinned Kometa v2.4.8 image.

### The regionals read Plex, not the fact map

`item_facts.content_rating` is MDBList's **Common Sense age rating**;
Kometa's alias buckets are Plex's own agent spellings (`gb/U`, `gb/0+`,
`no/A`, `TV-Y`). They are different value spaces, and `collections/filters.py`'s
own `content_rating` row says so in as many words. The divergence test uses
an item carrying a Plex `PG-13` **and** a Common Sense value in the `g`
bucket (`"G - All Ages"`), so reading the wrong one draws the wrong badge
rather than no badge. Falsified by mutating the accessor.

### No re-render storm, three ways

1. The pinned gate-off literal is **unmodified**, and so are the two golden
   pixel hashes and all five badge parity pin files.
2. `select` reports an outcome only for a definition that *carries* a
   condition, so a config of unconditioned definitions produces an empty
   outcomes list and its digest is bit-identical to the pre-seam five-argument
   value — pinned against a literal captured on `origin/main` before any code
   changed, not re-derived.
3. The 99 vendored PNGs went into their **own** `OVERLAY-MANIFEST.sha256`.
   `manifest_sha()` hashes `MANIFEST.sha256` and that hash is in every item's
   fingerprint; appending there would have re-badged ~16,000 items for art
   nothing draws. The residual is disclosed in `PROVENANCE.md` rather than
   hidden: replacing one of these PNGs without a config edit moves no
   fingerprint, the same shape as the `url:`-definition residual row 97
   already carries.

Gate-off is proven three ways through the **real** entry point,
`render/pipeline.py::apply_badges`: no definitions, a definition whose
condition fails, and a definition whose condition holds. The middle case
draws exactly the gate-off pixels and costs no I/O — an unmatched definition
never resolves, downloads or decodes its image.

### One thing scoped in that did not ship, and why

`versions` needs a `versions` row in `collections/filters.py`: its selection
is Plex's `duplicate` field, whose row there is `search-only` /
`filterable=False`, and `versions` itself is one of the 44 filter-only names
that module's own comment anticipates arriving "under a table row". That is
a change to a Kometa-pinned shared vocabulary phase 9b's search translation
would also have to answer for, so it is raised as an adjudication with a
ready-to-rule one-row recipe rather than taken here. The family is not
half-built: no dead `version_count` field, no unused PNG, no unreachable
definition.

### Verification

Full suite green, reconciled against a baseline measured on the freshly cut
branch. Each family falsified by transient mutation before its pin was
trusted. `git diff` against `origin/main` is empty for
`collections/filters.py`, `badges/spec.py`, `render/compositor.py`,
`assets/badges/MANIFEST.sha256`, the five badge parity pin files and the
golden gate.
```

---

## Self-review

**1. Spec coverage.** Every binding item in `p-overlay-c-facts.md`:

| Fact | Task |
|---|---|
| C1.1 — the selection seam, reusing `filters.py`, NOT `PlexItemView` | T1 Steps 4–17, 27 |
| C1.1 — `direct_play` | T2 Steps 1–9, 16–18 |
| C1.1 — `versions` | **NOT SHIPPED — Adjudication A14**, raised in the plan header, pinned by `test_versions_is_not_shipped_and_the_reason_is_recorded`, recorded in T3 |
| C1.1 — the six content-rating regionals | T2 Steps 1–9, 16–18 |
| C1.1 — vendored art, per-slice (A6) | T2 Steps 1–4 |
| C1.2 / A4 — fingerprint covers the per-item match, storm pin extends | T1 Steps 3, 19–24 |
| C1.3 / A5 — regionals match Plex `contentRating` | T2 Steps 7, 16 (divergence test), 18.3 (mutation) |
| C1.3 / A8, A7, A11 | C2's, named in T3's ledger; no C1 work |
| C1.4 — later slices named | T3 Step 1 |
| C1.5 / A2 — bucket (c) fenced, the operator's one-time ask | T3 Steps 1, 3 |
| C1.6 / A13 — branch from `origin/main`, re-verify merge tree at PR time | Branch section; T3 Step 6 |
| C2 — parity with the collections oracle, agreement pins | T1 Step 4 (two agreement tests) |
| C2 — gate-off byte-identical through the real `apply_badges` | T1 Steps 25–28 |
| C2 — per-family fires/silent on shaped/healthy items | T2 Steps 16–17 |
| C2 — RED-first; suites from a measured baseline | Global Constraints 11, 13; T1 Steps 1–2 |
| C2 — five parity pins + golden gate untouched | Global Constraints 3, 4; T2 Step 19's empty-diff check |
| C3 — row 100 sub-phase ledger, A12's TVDb→TMDb, the fenced bucket, upstream defects not ported | T3 Step 1 |
| C4 — 3 tasks, `p-overlay-c1-*` artifacts, `povc*` projects, opus T1 review + whole-branch gate | Task structure; Global Constraint 18; T1 Step 32; T3 Step 5 |

**2. Placeholder scan.** The six `# ...` comment bodies in `families.py` (T2 Step 7) are the only under-specified content, and they are deliberate: they are transcription targets with a named source file (copied out in Step 1), a fully worked exemplar (`us_movie_g`, whose alias list is the one the probe banked verbatim), an explicit prohibition on writing them from memory, and six tests that fail on any slip — `test_every_family_definition_names_an_image_that_exists` (a wrong `builtin:` path), the two box-shape pins, `test_the_us_movie_g_bucket_carries_kometas_own_alias_list_verbatim` (the one hand-pinned bucket), and two completeness nets added against R1: `test_every_family_transcribes_every_bucket_and_every_alias` (a per-family bucket count and total alias count, both counted off the vendored YAML at Step 7, not estimated) and `test_the_uk_and_us_movie_bucket_names_are_all_present` (the bucket NAMES the probe already gives for those two families). The step ends by requiring the `# ...` comments' deletion and every `FAMILY_ALIAS_COUNTS` entry filled in. No "TBD", no "add appropriate error handling", no "similar to Task N", no reference to an undefined type. Every code step carries complete code.

**3. Name consistency.** `condition` (field), `parse_condition`, `compiled_condition`, `select`, `OverlayItemView`, `AttributeNotOnItem`, `OVERLAY_ATTRIBUTES`, `_outcomes_digest`, `outcomes` (parameter), `FAMILIES`, `families`, `all_definitions`, `OVERLAY-MANIFEST.sha256` — each spelled identically in the Interfaces block, in every task that defines it and in every task that consumes it. `matched_definitions` is the local in `apply_badges`; `matched` is `select`'s first return element; the two never cross. Every consumed signature (`parse_filters`, `evaluate`, `predicates`, `apply_badges`, `badge_fingerprint`, `media_info_from_plex`, `PlexItemView`, `resolve_image_path`) was read off `origin/main` and is quoted as it stands there.
