# Overlay Ratings — sub-phase C2a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** wire the eighteen cheapest of the data-source probe's 29 external rating sources into the `<<variable>>` grammar (the eleven `mdb_*` MDBList ratings, the four `plex_*` Plex-embedded ratings, and the `imdb_rating`/`tmdb_rating`/`user_rating` aliases) and rule adjudication A14 by adding the one permitted `collections/filters.py` row a `versions` overlay family needs, then ship that family.

**Architecture:** four independent seams, all additive to C1's shipped selection engine. (1) `facts/mdblist.py::parse_ratings` reads the SAME response `MDBListClient.content_rating` already fetches — no new HTTP call — and applies the probe's per-field rescaling table; `MDBListClient.ratings`/`NullMDBListClient.ratings` expose it, degrading (not raising) on a transient MDBList failure, the same discipline `facts/gather.py::gather_facts` already uses for `content_rating`. (2) `badges/values.py::plex_native_ratings` reads `plex_item.ratings` and `plex_item.userRating` through the same reload-free access `overlays/selection.py::OverlayItemView` and `collections/filter_values.py` already use. (3) `BadgeInputs` gains one `ratings: dict[str, float | None]` field that `render/pipeline.py::apply_badges` populates from (1), (2) and the `critic_rating`/`audience_rating` aliases already in hand, and `badges/compose.py::_variable_values` merges it in — so every `<<mdb_average_rating>>`, `<<plex_imdb_rating>>`, `<<imdb_rating>>`, … token an operator writes now resolves. (4) `collections/filters.py` gains **one** additive `FilterAttribute` row, `versions` (adjudication A14, raised by C1's plan and ruled here), with a matching `collections/filter_values.py` accessor shared verbatim by `overlays/selection.py::OverlayItemView` — so the `versions` overlay family (T2) selects on `condition: {"versions.gt": 1}` instead of Plex's unfilterable `duplicate` search field, and the two T1-review LOW findings C2a was named for (L-2, L-4) are met where the code they describe actually changes.

**Tech Stack:** Python 3.13, Pydantic v2, `collections/filters.py` (one additive row, otherwise imported only), httpx, pytest, Docker Compose for the suite.

**Task cut, and why.** T1 is "the plumbing": every rating source this slice wires (MDBList parse + client method, Plex-native reads, alias wiring into `BadgeInputs`/`_variable_values`, the `versions` filters.py row + accessor + `OverlayItemView` vocabulary growth + the two LOW rewords) — one coherent unit, because every piece of it is invisible to an operator until a family or a hand-written `<<variable>>` token exercises it, and every piece shares the same "read something already in hand, degrade gracefully on a miss" shape. T2 is the one thing T1's plumbing unlocks that has its own vendored asset and its own fires/silent proof: the `versions` family. T3 is wrap (roadmap ledger, `progress.md`, full-branch verification) — kept separate because it touches no `src/` or `tests/` file any other task touches, so it can run standalone even if handed to a different reviewer.

## Global Constraints

Every task's requirements implicitly include this section.

1. **Branch `feat/overlay-ratings` from POST-#139 `origin/main`.** `feat/overlay-families` (PR #139, sub-phase C1) is **not merged as of this writing** — verify it has merged before cutting (Step 1 below is the content-probe that proves it), and re-verify the tip at execution time; do not trust any sha recorded in this document.
2. **`src/autoposter/collections/filters.py` gets exactly ONE edit: the additive `versions` row (T1).** No other row is touched, renamed or reordered, and no existing row's `note` text is edited even where it now reads slightly stale (`duplicate`'s note still correctly describes Kometa's own vocabulary). If a step appears to need a second edit to this file, that is a new adjudication — **STOP and report it**, do not take it.
3. **`src/autoposter/collections/filter_values.py`, `src/autoposter/render/compositor.py` and `src/autoposter/badges/spec.py` are never touched.**
4. **The five parity pin files are never edited:** `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_compose.py` (existing tests in it — new tests may be ADDED), `tests/test_badge_parity.py`.
5. **The golden gate is never edited.** `tests/test_overlay_engine_golden.py`'s `POSTER_PIXELS_SHA` and `TITLE_CARD_PIXELS_SHA` must keep passing UNMODIFIED.
6. **The storm pin holds, and the digest-evolution LAW now covers rating VALUES too, guarded the same way `outcomes` is.** `tests/test_overlay_entrypoint.py::test_the_gate_off_fingerprint_is_pinned_byte_identical`'s literal `576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd` is never edited. `BadgeInputs.ratings` is a NEW dataclass field with a `default_factory=dict` default — an EMPTY dict, same as no field at all — so `badge_values`'s displayed-badge set and every existing `_variable_values` caller's output are unchanged for a caller that sets it to nothing, and `badge_fingerprint`'s existing `values` argument (which `_variable_values` never feeds) still never reads `inputs.ratings`. Two things move a fingerprint once a definition names a rating token: (1) the existing `definitions` digest, unchanged — adding, editing or removing the `text(...)` literal itself is a config edit like any other; and (2) a NEW, separately-guarded `ratings` digest (Task 1, Concern D: `_rating_values_digest`, folded into `badge_fingerprint` via a new `ratings` parameter) that closes the staleness gap a config-only digest leaves open — an item's real MDBList/Plex rating changing between passes, with the config itself untouched, must still re-badge. It is guarded exactly the way `outcomes` is: computed per definition from `overlays.variables.tokens_in(literal_of(definition.name))` against `inputs.ratings`, contributing nothing for a definition naming no (currently-resolved) rating token — including every definition and every config predating this phase — and moving only for the specific definition(s) whose referenced value itself changed. **A rating-token definition's digest therefore moves IFF a value it actually uses moves** — an unrelated definition's fingerprint does not move, and a config naming no rating token at all does not move at all.
7. **MDBList ratings are resolved per-pass, never persisted.** No new `item_facts` column, no migration (adjudication A7). `MDBListClient.ratings` is a second, independent parse of the SAME HTTP response `content_rating` already caches (same URL, same params, same cache key) — not a second network call within a pass.
8. **The rescaling is transcribed exactly per the probe's own table** (`.superpowers/sdd/p-overlay-datasources-probe.md` §2.3.3), including the letterboxd `*2` outlier and the "falsy source value is `None`, never `0.0`" rule. Where the probe's table names a Kometa ATTRIBUTE but this service's own MDBList client shape (a raw JSON `ratings: [{source, value, score, votes}]` array plus two top-level scalars) requires a value-vs-score sourcing decision the probe does not cover — because the probe is a Kometa-source-only document and never fetched a raw MDBList response — that decision is recorded as this module's own reasoning, in a code comment, not presented as a transcribed fact.
9. **The dead-upstream defects are NOT ported.** No `anidb_rating` wiring of any kind (it is dead upstream — probe §2.3.4 — and out of this slice's scope regardless). `omdb_rating`/`omdb_imdb_rating`'s upstream aliasing is not reproduced because OMDb is not implemented here at all (probe bucket (c), fenced pending adjudication A2) — nothing in this phase names either token.
10. **Refuse loudly at config load, never match-nothing at render time** — C1's own law, unchanged. A `condition:` naming `versions` with a bad operator, or naming a search-only attribute, is a `ValueError` at load, never a silent non-match.
11. **RED before GREEN on every behavioural step.**
12. **Container discipline.** One compose project per task (**`povr1`, `povr2`, `povr3`**), always with the `.superpowers/isolated-db.yml` overlay. **`--rm` is never used** — every run is started detached (`run -d --name`), waited on with a **foreground** `docker wait`, read back with `docker cp` into `.superpowers/`, then `docker rm`. Teardown is `docker compose -p <project> down` — **never** `down -v`.
13. **No network in tests.** `tests/conftest.py`'s `no_outbound_network` autouse fixture is not bypassed. T2's vendoring `docker cp` out of the pinned Kometa image is a one-off developer action outside the suite, not a test.
14. **Commits** are conventional, staged **by name** (never `git add -A`), and carry **no AI attribution** of any kind — no `Co-Authored-By`, no tool mention. Add `--no-gpg-sign` if signing would otherwise block or time out.
15. **Read-only outside the named files.** At the end of each task, `git diff --stat <task-start-sha> -- src/ tests/ assets/ docs/` must list only files named in that task's **Files** block.
16. **Artifacts** (logs, captured literals, vendored source) go to `.superpowers/p-overlay-c2a-*` and are not committed unless a step says so.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `src/autoposter/facts/mdblist.py` | `parse_ratings`, `MDBListClient.ratings`, `NullMDBListClient.ratings` | T1 |
| `src/autoposter/badges/values.py` | `plex_native_ratings` | T1 |
| `src/autoposter/badges/compose.py` | `BadgeInputs.ratings` field; `_variable_values` merges it; `badge_fingerprint` gains a guarded `ratings` parameter + `_rating_values_digest` | T1 |
| `src/autoposter/render/pipeline.py` | `apply_badges` gains `mdblist=None`; builds and passes `ratings` | T1 |
| `src/autoposter/collections/filters.py` | **THE one additive row**: `versions` | T1 |
| `src/autoposter/collections/filter_values.py` | `_versions` accessor, added to `_ACCESSORS` | T1 |
| `src/autoposter/overlays/selection.py` | `OVERLAY_ATTRIBUTES` gains `"versions"`; `get()` gains a branch; the L-2 docstring reword; the L-4 `parse_condition` reword | T1 |
| `tests/fixtures/facts/mdblist_full_ratings.json` | **NEW.** All nine `ratings[]` sources plus both top-level scalars | T1 |
| `tests/test_mdblist.py` | `parse_ratings`/`MDBListClient.ratings`/`NullMDBListClient.ratings` pins | T1 |
| `tests/test_badge_values.py` | `plex_native_ratings` pins | T1 |
| `tests/test_badge_compose.py` | `BadgeInputs.ratings` + `_variable_values` merge pins; `badge_fingerprint`'s `ratings`-fold pins | T1 |
| `tests/test_overlay_entrypoint.py` | ratings resolve through the real `apply_badges`; MDBList degrade-on-limit pin | T1 |
| `tests/test_collection_filters.py` | the `versions` row's column-total moves; the both-dialect pins | T1 |
| `tests/test_collection_filter_values.py` | `_versions` accessor pins; the shipped-families tuple grows | T1 |
| `tests/test_overlay_selection.py` | `OVERLAY_ATTRIBUTES` grows; `versions` condition + agreement pins; L-4's reworded message | T1 |
| `assets/badges/images/versions.png` | **NEW.** Vendored from the pinned Kometa image | T2 |
| `assets/badges/OVERLAY-MANIFEST.sha256` | Regenerated (+1 line) | T2 |
| `assets/badges/PROVENANCE.md` | `versions.png` recorded | T2 |
| `src/autoposter/overlays/families.py` | `VERSIONS` list; `FAMILIES["versions"]` | T2 |
| `tests/test_overlay_families.py` | the "not shipped" pin flips to a shipped one; manifest count +1 | T2 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | row 100's C2a entry, shipped; L-2/L-4 marked met | T3 |
| `.superpowers/sdd/progress.md` | the sub-phase wrap entry | T3 |

**Never modified by any task:** `src/autoposter/collections/filter_values.py` (beyond T1's one function addition — see the table row above; the module itself is not restructured), `src/autoposter/render/compositor.py`, `src/autoposter/badges/spec.py`, `src/autoposter/badges/draw.py`, `src/autoposter/badges/geometry.py`, `src/autoposter/overlays/render.py`, `src/autoposter/overlays/builtin.py`, `src/autoposter/overlays/schema.py`, `assets/badges/MANIFEST.sha256`, `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_parity.py`, `tests/test_overlay_engine_golden.py`, `tests/test_collection_filter_oracle.py`, `tests/test_ci_path_filters.py` (`OVERLAY-MANIFEST.sha256` is already in `VERIFIED_NON_SOURCE` from C1).

---

## Interfaces produced by this phase

```python
# src/autoposter/facts/mdblist.py
def parse_ratings(payload: dict) -> dict[str, float | None]: ...
    # keys: mdb_average_rating, mdb_imdb_rating, mdb_letterboxd_rating,
    # mdb_metacritic_rating, mdb_metacriticuser_rating, mdb_myanimelist_rating,
    # mdb_rating, mdb_tmdb_rating, mdb_tomatoes_rating,
    # mdb_tomatoesaudience_rating, mdb_trakt_rating -- always all eleven keys.

class MDBListClient:
    async def ratings(
        self, tmdb_id: int | None = None, tvdb_id: int | None = None,
        is_movie: bool = True,
    ) -> dict[str, float | None]: ...

class NullMDBListClient:
    async def ratings(
        self, tmdb_id: int | None = None, tvdb_id: int | None = None,
        is_movie: bool = True,
    ) -> dict[str, float | None]: ...
```

```python
# src/autoposter/badges/values.py
def plex_native_ratings(item) -> dict[str, float | None]: ...
    # keys: user_rating, plex_imdb_rating, plex_tmdb_rating,
    # plex_tomatoes_rating, plex_tomatoesaudience_rating
```

```python
# src/autoposter/badges/compose.py
@dataclass(frozen=True)
class BadgeInputs:
    media: MediaInfo
    critic_rating: float | None = None
    audience_rating: float | None = None
    content_rating: str | None = None
    video_format: str | None = None
    ratings: dict[str, float | None] = field(default_factory=dict)   # NEW
```

```python
# src/autoposter/badges/compose.py
def badge_fingerprint(
    base_fingerprint: str,
    art_kind: str,
    values: dict[str, str],
    asset_manifest_sha: str,
    definitions: list[OverlayDefinition] | None = None,
    outcomes: list[tuple[str, bool]] | None = None,
    ratings: dict[str, float | None] | None = None,               # NEW
) -> str: ...
    # `ratings` folds each definition's USED rating-token values in, guarded
    # the same way `outcomes` is: absent/empty, or referenced by no
    # definition, leaves the fingerprint byte-identical to before this field
    # existed (Global Constraint 6).
```

```python
# src/autoposter/render/pipeline.py
async def apply_badges(
    session, config, render, item, plex_item, facts, probe=None, *,
    http=None, mdblist=None,                                          # mdblist NEW
) -> None: ...
```

```python
# src/autoposter/collections/filters.py
# FILTER_ATTRIBUTES grows by ONE row, appended at the end (34 total):
#   FilterAttribute("versions", "int", _BOTH, "listing", ..., search_field=None,
#                    show_search_field=None, search_kinds=(), filterable=True)
```

```python
# src/autoposter/collections/filter_values.py
def _versions(item: object) -> int | None: ...
# _ACCESSORS["versions"] = _versions
```

```python
# src/autoposter/overlays/selection.py
OVERLAY_ATTRIBUTES: tuple[str, ...]   # ("content_rating", "resolution", "versions")
```

```python
# src/autoposter/overlays/families.py
FAMILIES: dict[str, list[OverlayDefinition]]   # gains key "versions"
```

---

## Branch and cut point

- Branch name: **`feat/overlay-ratings`**.
- **Cut from `origin/main` after a fetch, verified with CONTENT probes, never a sha probe.** These probes prove #139 (C1) has landed — this plan cannot be executed against a pre-C1 `main`.

```bash
git fetch origin
git cat-file -e origin/main:src/autoposter/overlays/selection.py
git cat-file -e origin/main:src/autoposter/overlays/families.py
git cat-file -e origin/main:src/autoposter/facts/mdblist.py
git cat-file -e origin/main:src/autoposter/badges/values.py
git cat-file -e origin/main:src/autoposter/badges/compose.py
git cat-file -e origin/main:src/autoposter/render/pipeline.py
git cat-file -e origin/main:src/autoposter/collections/filters.py
git cat-file -e origin/main:src/autoposter/collections/filter_values.py
git cat-file -e origin/main:assets/badges/OVERLAY-MANIFEST.sha256
git cat-file -e origin/main:assets/badges/PROVENANCE.md
git cat-file -e origin/main:tests/test_overlay_families.py
git cat-file -e origin/main:tests/test_overlay_selection.py
git cat-file -e origin/main:tests/test_overlay_entrypoint.py
git show origin/main:src/autoposter/overlays/selection.py | grep -q 'OVERLAY_ATTRIBUTES: tuple\[str, \.\.\.\] = ("content_rating", "resolution")' && echo C1-VOCABULARY-PRESENT
git show origin/main:src/autoposter/overlays/families.py | grep -q '"versions" not in FAMILIES\|CONTENT_RATING_US_SHOW' && echo C1-FAMILIES-PRESENT
git show origin/main:src/autoposter/collections/filters.py | grep -q '"duplicate", "bool", ("movie",), "search-only"' && echo DUPLICATE-ROW-PRESENT
git show origin/main:src/autoposter/collections/filters.py | grep -qv '"versions", "int"' && echo VERSIONS-ROW-ABSENT
git show origin/main:src/autoposter/facts/mdblist.py | grep -q 'def content_rating(' && echo MDBLIST-CONTENT-RATING-PRESENT
git show origin/main:src/autoposter/badges/compose.py | grep -q 'class BadgeInputs' && echo BADGE-INPUTS-PRESENT
git show origin/main:src/autoposter/render/pipeline.py | grep -q 'async def apply_badges' && echo APPLY-BADGES-PRESENT
git show origin/main:tests/test_overlay_families.py | grep -q 'test_versions_is_not_shipped_and_the_reason_is_recorded' && echo A14-FENCE-PIN-PRESENT
git show origin/main:tests/test_overlay_entrypoint.py | grep -q '576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd' && echo FINGERPRINT-STORM-PIN-PRESENT
git show origin/main:tests/test_overlay_engine_golden.py | grep -q 'POSTER_PIXELS_SHA = "fc8793' && echo GOLDEN-GATE-PRESENT
git checkout -b feat/overlay-ratings origin/main
git rev-parse HEAD
```

All fourteen `cat-file` probes must succeed and all nine markers must print (including `VERSIONS-ROW-ABSENT`, which proves this plan's one permitted row does not already exist). If any probe fails — most likely because #139 has not merged yet — **STOP and report**; do not cut from a stale ref, and do not "fix" a marker by editing the file it probes.

---

## Task 1: The plumbing — MDBList ratings, Plex-native ratings, the alias wiring, and the `versions` row

**Files:**
- Create: `tests/fixtures/facts/mdblist_full_ratings.json`
- Modify: `src/autoposter/facts/mdblist.py`, `src/autoposter/badges/values.py`, `src/autoposter/badges/compose.py`, `src/autoposter/render/pipeline.py`, `src/autoposter/collections/filters.py`, `src/autoposter/collections/filter_values.py`, `src/autoposter/overlays/selection.py`
- Test: `tests/test_mdblist.py`, `tests/test_badge_values.py`, `tests/test_badge_compose.py`, `tests/test_overlay_entrypoint.py`, `tests/test_collection_filters.py`, `tests/test_collection_filter_values.py`, `tests/test_overlay_selection.py`

**Interfaces:**
- Consumes (from C1): `overlays.selection.{OVERLAY_ATTRIBUTES, OverlayItemView, parse_condition}`, `collections.filter_values.{_listing_value, _resolutions, PlexItemView}`, `collections.filters.{FilterAttribute, FILTER_ATTRIBUTES, BY_NAME, parse_filters, evaluate, predicates, _BOTH}`, `badges.compose.{BadgeInputs, badge_fingerprint, badge_values, _variable_values}` (`badge_fingerprint` itself gains a new `ratings` parameter here — Concern D), `badges.values.{MediaInfo, media_info_from_plex}`, `render.pipeline.apply_badges`, `facts.mdblist.{MDBListClient, NullMDBListClient, MDBListLimitReached, parse_content_rating}`.
- Produces: everything in "Interfaces produced by this phase" above, all consumed by T2's `versions` family.

- [ ] **Step 1: Verify the branch (the checklist above) and confirm the working tree is clean**

```bash
git status --short
```
Expected: empty (a freshly cut branch).

### Concern A — `parse_ratings`: the MDBList rescaling table

- [ ] **Step 2: Write the fixture and the failing tests**

`tests/fixtures/facts/mdblist_full_ratings.json` (new file):

```json
{
  "title": "All Souls",
  "year": 2023,
  "imdbid": "tt14316486",
  "tmdbid": 940143,
  "type": "movie",
  "certification": "R",
  "commonsense": 1,
  "age_rating": 17,
  "score": 55,
  "average": 58,
  "ratings": [
    {"source": "imdb", "value": 4.9, "score": 49, "votes": 981},
    {"source": "tmdb", "value": 6.3, "score": 63, "votes": 41},
    {"source": "trakt", "value": 72, "score": 72, "votes": 120},
    {"source": "letterboxd", "value": 3.1, "score": 62, "votes": 88},
    {"source": "tomatoes", "value": 67, "score": 67, "votes": 44},
    {"source": "tomatoesaudience", "value": 71, "score": 71, "votes": 210},
    {"source": "metacritic", "value": 58, "score": 58, "votes": 22},
    {"source": "metacriticuser", "value": 6.8, "score": 68, "votes": 30},
    {"source": "myanimelist", "value": null, "score": null, "votes": 0}
  ]
}
```

Append to `tests/test_mdblist.py`, after the `parse_content_rating` tests (after line 56):

```python
from autoposter.facts.mdblist import parse_ratings  # add to the existing import block


def test_parse_ratings_applies_the_probes_per_field_rescaling():
    """Probe section 2.3.3's table, field by field. `letterboxd` is the *2
    outlier (a 5-point scale to Kometa's 10-point one); `imdb`,
    `metacriticuser` and `myanimelist` are untransformed because their native
    scale is already 0-10; everything else reads MDBList's own 0-100 `score`
    and divides by 10."""
    payload = load("mdblist_full_ratings.json")
    assert parse_ratings(payload) == {
        "mdb_average_rating": 5.8,
        "mdb_imdb_rating": 4.9,
        "mdb_letterboxd_rating": 6.2,
        "mdb_metacritic_rating": 5.8,
        "mdb_metacriticuser_rating": 6.8,
        "mdb_myanimelist_rating": None,
        "mdb_rating": 5.5,
        "mdb_tmdb_rating": 6.3,
        "mdb_tomatoes_rating": 6.7,
        "mdb_tomatoesaudience_rating": 7.1,
        "mdb_trakt_rating": 7.2,
    }


def test_parse_ratings_treats_a_falsy_source_value_as_none_not_zero():
    """Probe section 2.3.3: every branch in the pinned Kometa source is
    `X / 10 if X else None` -- a legitimate 0 is not a legitimate rating."""
    payload = load("mdblist_full_ratings.json")
    payload = payload | {
        "ratings": [
            e | {"value": 0, "score": 0} if e["source"] == "imdb" else e
            for e in payload["ratings"]
        ]
    }
    assert parse_ratings(payload)["mdb_imdb_rating"] is None


def test_parse_ratings_is_total_over_a_payload_with_no_ratings_array():
    assert parse_ratings({}) == {
        "mdb_average_rating": None,
        "mdb_imdb_rating": None,
        "mdb_letterboxd_rating": None,
        "mdb_metacritic_rating": None,
        "mdb_metacriticuser_rating": None,
        "mdb_myanimelist_rating": None,
        "mdb_rating": None,
        "mdb_tmdb_rating": None,
        "mdb_tomatoes_rating": None,
        "mdb_tomatoesaudience_rating": None,
        "mdb_trakt_rating": None,
    }


def test_parse_ratings_degrades_a_malformed_entry_to_none_and_warns(caplog):
    """MEDIUM finding, preflight review: a present-but-non-numeric field (a
    stray string MDBList might emit for an errored source) must degrade that
    ONE key to `None`, never raise `ValueError` out of `parse_ratings` -- the
    same 'missing/unknown degrades, never errors' discipline the falsy-value
    branch above already has, extended to the malformed-value case. A raised
    `ValueError` here would fail the WHOLE badge stage for the item (only
    `MDBListLimitReached`/`httpx.HTTPError` are caught around the call site,
    Concern E) -- exactly the gap this pins shut."""
    payload = load("mdblist_full_ratings.json")
    payload = payload | {
        "ratings": [
            e | {"value": "not-a-number", "score": "also-not-a-number"}
            if e["source"] == "imdb" else e
            for e in payload["ratings"]
        ],
    }
    with caplog.at_level("WARNING"):
        result = parse_ratings(payload)
    assert result["mdb_imdb_rating"] is None, "the malformed source alone degrades"
    assert result["mdb_tmdb_rating"] == 6.3, "an unrelated source must not be taken down with it"
    assert len(caplog.records) == 1, "one warning for the one malformed source"
```

- [ ] **Step 3: Run and confirm RED**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-red1 test sh -c 'pytest -q tests/test_mdblist.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-red1.log'
docker wait povr1-red1
docker cp povr1-red1:/app/.superpowers/p-overlay-c2a-t1-red1.log .superpowers/p-overlay-c2a-t1-red1.log
docker rm povr1-red1
```
Expected: collection error or `ImportError: cannot import name 'parse_ratings'`.

- [ ] **Step 4: Implement `parse_ratings`**

In `src/autoposter/facts/mdblist.py`, after `parse_content_rating`:

```python
# The `ratings[]` entry's `source` value, and which sub-field to read, for
# each MDBList-sourced overlay rating this service exposes. Probe section
# 2.3.3 (`.superpowers/sdd/p-overlay-datasources-probe.md`) names Kometa's own
# attribute per field and its transform; the RAW HTTP JSON shape below
# (`ratings: [{source, value, score, votes}, ...]` plus two top-level
# scalars) is THIS CLIENT's own, not something the probe -- a Kometa-source-
# only document -- ever fetched, so the value-vs-score sourcing here is this
# module's own reasoning, not a transcribed fact:
#   - the three "none"-transform fields (imdb_rating, metacriticuser_rating,
#     myanimelist_rating) read the array entry's `value` -- these sources'
#     NATIVE scale is already 0-10 (IMDb; Metacritic's own 0-10 USER score,
#     distinct from its 0-100 Metascore; MyAnimeList), so no rescale applies;
#   - letterboxd reads `value` too (its native scale is 0-5 stars) and
#     doubles it, per the probe;
#   - the remaining five array-sourced fields (metacritic, trakt, tomatoes,
#     tomatoesaudience, tmdb) read the array entry's `score` -- MDBList's own
#     0-100 cross-source normalisation -- and divide by 10; their native
#     scales are NOT uniformly 0-10 (Rotten Tomatoes and Metacritic's
#     Metascore are 0-100, Trakt is 0-100), so the normalised field is the
#     one that produces a sane 0-10 number for all five with one rule;
#   - the two TOP-LEVEL scalars (`average`, `score`) are MDBList's own
#     composite fields and both divide by 10, per the probe's `mdb_average_
#     rating`/`mdb_rating` rows.
_ARRAY_VALUE_FIELDS = {
    "mdb_imdb_rating": "imdb",
    "mdb_metacriticuser_rating": "metacriticuser",
    "mdb_myanimelist_rating": "myanimelist",
}
_ARRAY_VALUE_DOUBLED_FIELDS = {"mdb_letterboxd_rating": "letterboxd"}
_ARRAY_SCORE_FIELDS = {
    "mdb_metacritic_rating": "metacritic",
    "mdb_trakt_rating": "trakt",
    "mdb_tomatoes_rating": "tomatoes",
    "mdb_tomatoesaudience_rating": "tomatoesaudience",
    "mdb_tmdb_rating": "tmdb",
}


def parse_ratings(payload: dict) -> dict[str, float | None]:
    """The eleven `mdb_*` overlay rating sources (`overlays/variables.py::
    RATING_SOURCES`), per probe section 2.3.3's per-field rescaling table --
    transcribed exactly, letterboxd's `*2` included. See the module comment
    above for the value-vs-score sourcing this client's own shape requires
    that the probe does not cover.

    `MDBListClient.content_rating` already fetches this same response for the
    Common Sense age rating; this is a second, independent parse of it -- no
    new HTTP call. Always returns all eleven keys; an unresolved field is
    `None`, never a missing key, so a caller can `dict.update` it in without
    a stale value surviving from a previous pass.
    """
    by_source = {
        entry.get("source"): entry
        for entry in payload.get("ratings") or []
        if isinstance(entry, dict)
    }

    def coerce(field: str, raw: object) -> float | None:
        # A present-but-non-numeric value (a stray string MDBList might emit
        # for an errored source) must degrade to None for THIS key alone --
        # never raise. `parse_ratings` has no item context of its own, so the
        # warning names the field; the caller (Concern E) still degrades the
        # whole `ratings()` call on a transport failure, but a malformed
        # value inside an otherwise-good response must not take the other
        # ten keys down with it.
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning("mdblist: non-numeric %s value %r; treating as absent", field, raw)
            return None

    def array_value(field: str, *, doubled: bool = False) -> float | None:
        source = _ARRAY_VALUE_DOUBLED_FIELDS[field] if doubled else _ARRAY_VALUE_FIELDS[field]
        raw = (by_source.get(source) or {}).get("value")
        if not raw:
            return None
        value = coerce(field, raw)
        return value * 2 if (doubled and value is not None) else value

    def array_score(field: str) -> float | None:
        raw = (by_source.get(_ARRAY_SCORE_FIELDS[field]) or {}).get("score")
        if not raw:
            return None
        value = coerce(field, raw)
        return value / 10 if value is not None else None

    result: dict[str, float | None] = {}
    for field in _ARRAY_VALUE_FIELDS:
        result[field] = array_value(field)
    for field in _ARRAY_VALUE_DOUBLED_FIELDS:
        result[field] = array_value(field, doubled=True)
    for field in _ARRAY_SCORE_FIELDS:
        result[field] = array_score(field)
    average = payload.get("average")
    average_value = coerce("mdb_average_rating", average) if average else None
    result["mdb_average_rating"] = average_value / 10 if average_value is not None else None
    composite = payload.get("score")
    composite_value = coerce("mdb_rating", composite) if composite else None
    result["mdb_rating"] = composite_value / 10 if composite_value is not None else None
    return result
```

(`logger` is already module-level in `mdblist.py`; do not re-import.)

- [ ] **Step 5: Run and confirm GREEN**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-green1 test sh -c 'pytest -q tests/test_mdblist.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-green1.log'
docker wait povr1-green1
docker cp povr1-green1:/app/.superpowers/p-overlay-c2a-t1-green1.log .superpowers/p-overlay-c2a-t1-green1.log
docker rm povr1-green1
```
Expected: all pass.

- [ ] **Step 6: Live-verification gate — check `parse_ratings`' field map against one real cached MDBList payload**

MEDIUM finding, preflight review: the value-vs-score sourcing split above (Global Constraint 8) is this module's own reasoning from the probe plus the one real fixture this repo already had (`tests/fixtures/facts/mdblist_movie.json`, which only exercises `imdb`/`tmdb` — insufficient to distinguish the two readings) — never checked against a fuller real response. Before committing, check whether this environment's own cache already holds one: the `provider_cache` table (`src/autoposter/db/models.py::ProviderCache` — `key`/`value`/`expires_at`; `value` stores `{"found", "payload"}`, `src/autoposter/providers/cache.py`) that every provider client, MDBList's included, already reads and writes through. This is an inspection, not a test run — no `docker wait`/`docker cp` choreography (Global Constraint 12 scopes that discipline to running the suite): the same "one-off developer action outside the suite, not a test" framing Global Constraint 13 already gives T2's vendoring `docker cp`:

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  exec -T postgres psql -U autoposter -d autoposter -t -c \
  "select value->'payload' from provider_cache where value->'payload' ? 'ratings' and value->'payload' ? 'commonsense' limit 1;" \
  | tee .superpowers/p-overlay-c2a-t1-cache-check.log
cat .superpowers/p-overlay-c2a-t1-cache-check.log
```

Two outcomes:
- **A row comes back:** compare its `ratings[]` entries' `source`/`value`/`score` shape, and its top-level `average`/`score` fields, against `_ARRAY_VALUE_FIELDS`/`_ARRAY_VALUE_DOUBLED_FIELDS`/`_ARRAY_SCORE_FIELDS` (Step 4). **If the live shape contradicts the field map — a source name that does not appear, a sub-field that is not `value`/`score`, a nesting this parse does not expect — STOP and report it** before Step 7's commit, the same discipline T2 Step 1 uses for `versions.yml`.
- **The query returns nothing** (the expected case for a freshly isolated compose stack that has never called MDBList for a real item): there is nothing here to check against. This does NOT resolve the self-review's open question below — record that it is still open and deferred to the operator checkbox that recommendation already is, and proceed.

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/facts/mdblist_full_ratings.json tests/test_mdblist.py src/autoposter/facts/mdblist.py
git commit -m "feat(facts): parse MDBList's eleven mdb_* overlay ratings"
```

### Concern B — `MDBListClient.ratings` / `NullMDBListClient.ratings`

- [ ] **Step 8: Write the failing tests**

Append to `tests/test_mdblist.py`:

```python
async def test_ratings_reuses_the_content_rating_endpoint_and_cache(session):
    """Roadmap row 100 C2a: 'the ratings array already arrives in a response
    the client already fetches' -- proved by counting transport calls, not
    asserted."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("mdblist_full_ratings.json"))

    factory = session_factory_for(session)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        cache = ProviderCache(factory)
        client = MDBListClient("KEY", http, cache=cache)
        await client.content_rating(tmdb_id=940143, is_movie=True)
        result = await client.ratings(tmdb_id=940143, is_movie=True)

    assert len(calls) == 1, "the second read must be served from the cache"
    assert result["mdb_imdb_rating"] == 4.9


async def test_ratings_with_no_identifier_makes_no_request_and_answers_all_none():
    def handler(request):
        raise AssertionError("should not have been called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await MDBListClient("KEY", http).ratings(is_movie=True)

    assert result == {k: None for k in result}
    assert len(result) == 11


async def test_ratings_quota_exhaustion_raises_the_same_error_content_rating_does():
    from autoposter.facts.mdblist import MDBListLimitReached

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": "API Limit Reached!"})
        )
    ) as http:
        with pytest.raises(MDBListLimitReached):
            await MDBListClient("KEY", http).ratings(tmdb_id=1, is_movie=True)


async def test_null_client_ratings_answers_all_none_and_makes_no_request():
    result = await NullMDBListClient().ratings(tmdb_id=1, is_movie=True)
    assert result == {k: None for k in result}
    assert len(result) == 11
```

- [ ] **Step 9: Run and confirm RED**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-red2 test sh -c 'pytest -q tests/test_mdblist.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-red2.log'
docker wait povr1-red2
docker cp povr1-red2:/app/.superpowers/p-overlay-c2a-t1-red2.log .superpowers/p-overlay-c2a-t1-red2.log
docker rm povr1-red2
```
Expected: `AttributeError: 'MDBListClient' object has no attribute 'ratings'` (4 failures).

- [ ] **Step 10: Implement**

In `src/autoposter/facts/mdblist.py`, add to `NullMDBListClient`:

```python
    async def ratings(
        self,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        is_movie: bool = True,
    ) -> dict[str, float | None]:
        return parse_ratings({})
```

And to `MDBListClient`, after `content_rating`:

```python
    async def ratings(
        self,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        is_movie: bool = True,
    ) -> dict[str, float | None]:
        """The eleven `mdb_*` overlay rating sources for one item.

        Hits the exact same URL and params `content_rating` does, so within
        this response's cache TTL calling both on one item costs one HTTP
        request, not two (`build_cache_key` keys on URL+params, apikey
        stripped).
        """
        identifier = tmdb_id if is_movie else tvdb_id
        empty = parse_ratings({})
        if identifier is None:
            return empty
        provider = "tmdb" if is_movie else "tvdb"
        media_type = "movie" if is_movie else "show"
        url = f"{BASE_URL}/{provider}/{media_type}/{identifier}/"
        params = {"apikey": self._apikey}
        payload = await fetch_json(
            method="GET",
            url=url,
            params=params,
            request=lambda: self._client.get(
                url, params=params, headers={"User-Agent": "autoposter"}
            ),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
            cacheable=_not_a_limit_body,
        )
        if payload is None:
            return empty
        error = payload.get("error") if isinstance(payload, dict) else None
        if error in _LIMIT_ERRORS:
            raise MDBListLimitReached(error)
        if error:
            logger.warning("mdblist error for %s %s: %s", provider, identifier, error)
            return empty
        return parse_ratings(payload)
```

- [ ] **Step 11: Run and confirm GREEN**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-green2 test sh -c 'pytest -q tests/test_mdblist.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-green2.log'
docker wait povr1-green2
docker cp povr1-green2:/app/.superpowers/p-overlay-c2a-t1-green2.log .superpowers/p-overlay-c2a-t1-green2.log
docker rm povr1-green2
```
Expected: all pass.

- [ ] **Step 12: Commit**

```bash
git add tests/test_mdblist.py src/autoposter/facts/mdblist.py
git commit -m "feat(facts): MDBListClient.ratings, sharing content_rating's cached fetch"
```

### Concern C — `plex_native_ratings`: the four `plex_*` sources plus `user_rating`

- [ ] **Step 13: Write the failing tests**

New file `tests/test_badge_values.py` may already exist — check first with `git show HEAD:tests/test_badge_values.py > /dev/null` before assuming; if it exists, append; if not, create it importing what the existing `badges/values.py` test coverage needs. Append (or create with) this section:

```python
from autoposter.badges.values import plex_native_ratings


class _Rating:
    def __init__(self, image, value):
        self.image = image
        self.value = value


class _RatedItem:
    def __init__(self, user_rating=None, ratings=()):
        if user_rating is not None:
            self.userRating = user_rating
        self.ratings = ratings


def test_plex_native_ratings_reads_all_four_by_image_prefix():
    """Probe section 2.3.6, transcribed verbatim: the rottentomatoes://
    discrimination is by URL SUFFIX (ripe/rotten = critic, anything else =
    audience), not by a separate field."""
    item = _RatedItem(
        user_rating=8.0,
        ratings=(
            _Rating("imdb://image.rating", 7.7),
            _Rating("themoviedb://image.rating", 8.4),
            _Rating("rottentomatoes://image.rating.ripe", 9.0),
            _Rating("rottentomatoes://image.rating.upright", 8.8),
        ),
    )
    assert plex_native_ratings(item) == {
        "user_rating": 8.0,
        "plex_imdb_rating": 7.7,
        "plex_tmdb_rating": 8.4,
        "plex_tomatoes_rating": 9.0,
        "plex_tomatoesaudience_rating": 8.8,
    }


def test_plex_native_ratings_reads_the_rotten_suffix_as_critic_too():
    item = _RatedItem(ratings=(_Rating("rottentomatoes://image.rating.rotten", 3.0),))
    assert plex_native_ratings(item)["plex_tomatoes_rating"] == 3.0


def test_plex_native_ratings_is_total_over_an_unrated_item():
    item = _RatedItem()
    assert plex_native_ratings(item) == {
        "user_rating": None,
        "plex_imdb_rating": None,
        "plex_tmdb_rating": None,
        "plex_tomatoes_rating": None,
        "plex_tomatoesaudience_rating": None,
    }


def test_plex_native_ratings_does_not_trigger_a_partial_object_reload():
    """The reload hazard C1's T1 review flagged (finding C3) for
    OverlayItemView applies identically here: `item` may be the same PARTIAL
    plexapi object apply_badges uploads to, and a plain `getattr` on an
    UNSET attribute trips PlexPartialObject.__getattribute__'s reload
    branch -- one blocking `requests` GET, inline. A fake with no `reload`
    method proves the accessor never calls it."""
    class _PartialNoReload:
        ratings = ()

    assert plex_native_ratings(_PartialNoReload()) == {
        "user_rating": None,
        "plex_imdb_rating": None,
        "plex_tmdb_rating": None,
        "plex_tomatoes_rating": None,
        "plex_tomatoesaudience_rating": None,
    }
```

- [ ] **Step 14: Run and confirm RED**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-red3 test sh -c 'pytest -q tests/test_badge_values.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-red3.log'
docker wait povr1-red3
docker cp povr1-red3:/app/.superpowers/p-overlay-c2a-t1-red3.log .superpowers/p-overlay-c2a-t1-red3.log
docker rm povr1-red3
```
Expected: `ImportError: cannot import name 'plex_native_ratings'` (or, if the file is new, a collection error naming the same).

- [ ] **Step 15: Implement**

In `src/autoposter/badges/values.py`, after `media_info_from_plex`:

```python
def plex_native_ratings(item) -> dict[str, float | None]:
    """The overlay grammar's `user_rating` plus the four `plex_*` sources --
    the one rating family needing no external call at all (probe section
    2.3.6, transcribed verbatim below, including the `rottentomatoes://`
    URL-suffix discrimination -- ripe/rotten is the critic score, anything
    else is the audience one).

    Reads through `object.__getattribute__`, not a plain `getattr`: `item` is
    very likely the same PARTIAL plexapi object `apply_badges` uploads to,
    and a plain read of an unset attribute (an unrated item's `.userRating`,
    or one with no MDBList-sourced Plex ratings) trips
    `PlexPartialObject.__getattribute__`'s reload branch -- one synchronous
    blocking `requests` GET, inline, on the event loop. Same discipline
    `collections/filter_values.py::_listing_value` and
    `overlays/selection.py::OverlayItemView.get` already use; duplicated
    (three lines) rather than imported, because `badges/` has no other
    dependency on `collections/` and this is the only thing that would
    create one.
    """

    def read(name):
        try:
            return object.__getattribute__(item, name)
        except AttributeError:
            return None

    result: dict[str, float | None] = {
        "user_rating": read("userRating"),
        "plex_imdb_rating": None,
        "plex_tmdb_rating": None,
        "plex_tomatoes_rating": None,
        "plex_tomatoesaudience_rating": None,
    }
    for rating in read("ratings") or ():
        image = getattr(rating, "image", "") or ""
        if image.startswith("imdb://"):
            result["plex_imdb_rating"] = rating.value
        elif image.startswith("themoviedb://"):
            result["plex_tmdb_rating"] = rating.value
        elif image.startswith("rottentomatoes://"):
            if image.endswith("ripe") or image.endswith("rotten"):
                result["plex_tomatoes_rating"] = rating.value
            else:
                result["plex_tomatoesaudience_rating"] = rating.value
    return result
```

- [ ] **Step 16: Run and confirm GREEN**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-green3 test sh -c 'pytest -q tests/test_badge_values.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-green3.log'
docker wait povr1-green3
docker cp povr1-green3:/app/.superpowers/p-overlay-c2a-t1-green3.log .superpowers/p-overlay-c2a-t1-green3.log
docker rm povr1-green3
```
Expected: all pass.

- [ ] **Step 17: Commit**

```bash
git add tests/test_badge_values.py src/autoposter/badges/values.py
git commit -m "feat(badges): read the four plex_* ratings and user_rating, reload-free"
```

### Concern D — `BadgeInputs.ratings` and `_variable_values`

- [ ] **Step 18: Write the failing tests**

Append to `tests/test_badge_compose.py`:

```python
from autoposter.badges.compose import _variable_values  # add to the existing import line
from autoposter.overlays.schema import OverlayDefinition  # add to the existing import line


def test_variable_values_merges_ratings_in():
    inputs = _inputs(ratings={"mdb_average_rating": 6.5, "plex_imdb_rating": 7.7})
    values = _variable_values("", inputs)
    assert values["mdb_average_rating"] == 6.5
    assert values["plex_imdb_rating"] == 7.7


def test_variable_values_drops_a_none_valued_rating():
    inputs = _inputs(ratings={"mdb_rating": None, "user_rating": 8.0})
    values = _variable_values("", inputs)
    assert "mdb_rating" not in values
    assert values["user_rating"] == 8.0


def test_an_empty_ratings_dict_changes_nothing():
    """Global Constraint 6: the default is an empty dict, and an empty dict
    merged in is the pre-C2a output exactly."""
    with_default = _variable_values("", _inputs())
    with_empty = _variable_values("", _inputs(ratings={}))
    assert with_default == with_empty


def test_unchanged_rating_values_do_not_move_the_fingerprint():
    """MEDIUM finding, preflight review, ruled for correctness: `ratings`
    folds each definition's USED rating-token VALUE into the fingerprint too
    -- guarded the same way `outcomes` is (Global Constraint 6). The same
    resolved values passed twice must produce the same digest."""
    stamp = OverlayDefinition(name="text(<<mdb_rating>>)")
    ratings = {"mdb_rating": 6.5}
    a = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m", [stamp], ratings=ratings)
    b = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m", [stamp], ratings=dict(ratings))
    assert a == b


def test_a_changed_rating_value_used_by_a_definition_moves_the_fingerprint():
    """The staleness gap this closes: a definition that NAMES a rating token
    must re-badge when that token's resolved value moves, even though the
    config (`definitions`) itself did not change at all -- otherwise an
    already-uploaded item's rating text goes stale forever."""
    stamp = OverlayDefinition(name="text(<<mdb_rating>>)")
    before = badge_fingerprint(
        "abc", "poster", {"critic": "8.6"}, "m", [stamp], ratings={"mdb_rating": 6.5},
    )
    after = badge_fingerprint(
        "abc", "poster", {"critic": "8.6"}, "m", [stamp], ratings={"mdb_rating": 7.0},
    )
    assert before != after


def test_a_definition_naming_no_rating_token_is_byte_identical_regardless_of_ratings():
    """The non-empty guard, the storm pin's own discipline extended: a
    config whose definitions name no rating token -- every config predating
    this phase included -- must not move at all, no matter what `ratings`
    carries."""
    stamp = OverlayDefinition(name="text(HELLO)")
    without_ratings = badge_fingerprint("abc", "poster", {"critic": "8.6"}, "m", [stamp])
    with_ratings = badge_fingerprint(
        "abc", "poster", {"critic": "8.6"}, "m", [stamp],
        ratings={"mdb_rating": 6.5, "user_rating": 8.0},
    )
    assert without_ratings == with_ratings
```

- [ ] **Step 19: Run and confirm RED**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-red4 test sh -c 'pytest -q tests/test_badge_compose.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-red4.log'
docker wait povr1-red4
docker cp povr1-red4:/app/.superpowers/p-overlay-c2a-t1-red4.log .superpowers/p-overlay-c2a-t1-red4.log
docker rm povr1-red4
```
Expected: `TypeError: BadgeInputs.__init__() got an unexpected keyword argument 'ratings'` (3 failures) and `TypeError: badge_fingerprint() got an unexpected keyword argument 'ratings'` (the 3 new fingerprint-fold pins) -- 6 failures total.

- [ ] **Step 20: Implement**

In `src/autoposter/badges/compose.py`, change the import line:

```python
from dataclasses import dataclass, field
```

Add the field to `BadgeInputs` (after `video_format`):

```python
    # Roadmap row 100, sub-phase C2a: the eleven mdb_*, four plex_*, and
    # user_rating overlay rating sources, keyed by their <<variable>> name
    # (`overlays/variables.py::RATING_SOURCES`/`PLEX_NATIVE_RATINGS`). This
    # dataclass does not know imdb_rating/tmdb_rating are aliases of
    # critic_rating/audience_rating above -- it carries whatever the caller
    # resolved. Default is an empty dict: no field at all, for a caller that
    # sets nothing (Global Constraint 6).
    ratings: dict[str, float | None] = field(default_factory=dict)
```

Replace `_variable_values`'s docstring and body:

```python
def _variable_values(art_kind: str, inputs: BadgeInputs) -> dict[str, object]:
    """The item values an operator's <<variable>> tokens can resolve against.

    Roadmap row 100, sub-phase C2a: eighteen of the data-source probe's 29
    external rating sources now resolve here -- the eleven mdb_*, the four
    plex_*, and the imdb_rating/tmdb_rating/user_rating aliases, all carried
    in `inputs.ratings` and merged in below. The remaining eleven (four
    omdb_*, three anidb_*, mal_rating, two trakt_*, serializd_rating,
    floppy_rating) each need a new external integration this service does not
    have (probe bucket (c), fenced pending an operator decision --
    adjudication A2) and are still absent on purpose: a definition naming one
    is skipped rather than silently rendered wrong.
    """
    media = inputs.media
    values: dict[str, object] = {
        "content_rating": inputs.content_rating,
        "critic_rating": inputs.critic_rating,
        "audience_rating": inputs.audience_rating,
        "season_number": media.season_number,
        "episode_number": media.episode_number,
        **inputs.ratings,
    }
    if media.duration_ms:
        values["runtime"] = media.duration_ms // 60000
        values["total_runtime"] = values["runtime"]
    return {k: v for k, v in values.items() if v is not None}
```

MEDIUM finding, preflight review, ruled for correctness: fold resolved rating VALUES into `badge_fingerprint` too, guarded the same way `outcomes` is. Widen the `overlays.variables` import line:

```python
from autoposter.overlays.variables import UnresolvedVariable, literal_of, render_text, tokens_in
```

Add `_rating_values_digest`, after `_outcomes_digest` and before `badge_fingerprint`:

```python
def _rating_values_digest(
    definitions: list[OverlayDefinition], ratings: dict[str, float | None]
) -> str | None:
    """A stable digest of the RESOLVED rating values each definition's own
    `<<variable>>` literal actually names (roadmap row 100, sub-phase C2a).

    `badge_fingerprint`'s `values` argument never carries `inputs.ratings` --
    it is consumed only by `_variable_values`, for `<<variable>>` TEXT
    resolution (Global Constraint 6) -- so without this, a later pass where
    an item's real MDBList/Plex rating changes would never move the
    fingerprint at all, leaving stale rating text on already-uploaded
    artwork indefinitely. This closes that gap the same way
    `_outcomes_digest` closes the parallel one for CONDITION evaluation:
    guarded so a definition naming no (currently-resolved) rating token
    contributes nothing, and the digest for one that does moves IFF a
    referenced value itself moves.

    Returns `None`, not an empty string, when no definition ends up naming
    any resolved rating token -- so the caller's own guard (matching
    `definitions`/`outcomes`) leaves an unrelated or rating-free config's
    fingerprint completely untouched.
    """
    parts = []
    for d in definitions:
        literal = literal_of(d.name)
        if literal is None:
            continue
        for var, _mod in tokens_in(literal):
            if var in ratings:
                parts.append("%s:%s=%s" % (d.name, var, ratings[var]))
    if not parts:
        return None
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()
```

Widen `badge_fingerprint`'s signature, docstring and body:

```python
def badge_fingerprint(
    base_fingerprint: str,
    art_kind: str,
    values: dict[str, str],
    asset_manifest_sha: str,
    definitions: list[OverlayDefinition] | None = None,
    outcomes: list[tuple[str, bool]] | None = None,
    ratings: dict[str, float | None] | None = None,
) -> str:
```

Add one paragraph to the docstring, after the `outcomes` paragraph:

```
    ``ratings`` folds in the RESOLVED VALUE of every rating token a
    definition's own ``<<variable>>`` literal actually names (roadmap row
    100, sub-phase C2a) -- guarded the same way ``outcomes`` is: a
    definition naming no rating token contributes nothing, and a later pass
    where an item's real MDBList/Plex rating changes moves the digest only
    for the definition(s) that actually reference it, closing the staleness
    gap a config-only digest would otherwise leave open.
```

Widen the body:

```python
    parts = [base_fingerprint, art_kind, asset_manifest_sha]
    parts += ["%s=%s" % (k, values[k]) for k in sorted(values)]
    if definitions:
        parts.append(_definitions_digest(definitions))
    if outcomes:
        parts.append(_outcomes_digest(outcomes))
    if definitions and ratings:
        rating_digest = _rating_values_digest(definitions, ratings)
        if rating_digest is not None:
            parts.append(rating_digest)
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
```

- [ ] **Step 21: Run and confirm GREEN**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-green4 test sh -c 'pytest -q tests/test_badge_compose.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-green4.log'
docker wait povr1-green4
docker cp povr1-green4:/app/.superpowers/p-overlay-c2a-t1-green4.log .superpowers/p-overlay-c2a-t1-green4.log
docker rm povr1-green4
```
Expected: all pass.

- [ ] **Step 22: Commit**

```bash
git add tests/test_badge_compose.py src/autoposter/badges/compose.py
git commit -m "feat(badges): BadgeInputs.ratings merges into the <<variable>> value map and the fingerprint"
```

### Concern E — wiring it all up in `apply_badges`, through the real entry point

- [ ] **Step 23: Write the failing tests**

Append to `tests/test_overlay_entrypoint.py`, in the per-family fires/silent section (near the other `_Facts`/`_FakePlexItem*` fixtures):

```python
class _FakePlexItemWithRatings(_FakePlexItem):
    """Carries `.userRating` and a `.ratings` collection -- the plex_* four,
    read by `badges.values.plex_native_ratings`."""

    def __init__(self, user_rating=8.0):
        super().__init__()
        self.userRating = user_rating
        self.ratings = [
            type("R", (), {"image": "imdb://image.rating", "value": 7.7})(),
            type("R", (), {"image": "themoviedb://image.rating", "value": 8.4})(),
        ]


class _RaisingMDBList:
    """A stand-in `mdblist` client that always raises, to prove `apply_badges`
    degrades rather than propagating."""

    def __init__(self, exc):
        self._exc = exc

    async def ratings(self, tmdb_id=None, tvdb_id=None, is_movie=True):
        raise self._exc


class _RecordingMDBList:
    def __init__(self, values):
        self._values = values
        self.calls = []

    async def ratings(self, tmdb_id=None, tvdb_id=None, is_movie=True):
        self.calls.append((tmdb_id, tvdb_id, is_movie))
        return self._values


async def test_imdb_and_tmdb_rating_tokens_resolve_from_the_existing_facts(
    session, config_with_badges
):
    """The alias half of C2a: no new fetch -- item_facts.critic_rating IS
    imdb_rating and item_facts.audience_rating IS tmdb_rating (probe bucket
    (a)), and now the <<imdb_rating>>/<<tmdb_rating>> SPELLINGS resolve too,
    not only <<critic_rating>>/<<audience_rating>>.

    HIGH finding, preflight review: this item's own gate-off baseline (no
    definitions at all) vs the same item with the alias definition
    configured -- the pixel-hash-vs-baseline pattern
    `test_plex_native_ratings_resolve_through_the_real_entry_point` below
    already uses, not `Path(render.asset_path).exists()` (that path is a
    committed fixture `apply_badges` never writes to; it exists before this
    test runs and would keep existing whether or not the tokens ever
    resolved). If the aliases did NOT resolve, `UnresolvedVariable` would be
    caught and skipped per definition (probe section 2.4), and `fires` would
    come back pixel-identical to `baseline` -- that is what must actually
    fail at RED."""
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = []
    baseline = await _badged(
        session, config_with_badges, _FakePlexItem(), "ratings-aliases-base"
    )

    config_with_badges.badges.definitions = [
        OverlayDefinition(
            name="text(<<imdb_rating>> / <<tmdb_rating>>)",
            horizontal_align="center", horizontal_offset=0,
            vertical_align="top", vertical_offset=0,
        ),
    ]
    fires = await _badged(session, config_with_badges, _FakePlexItem(), "ratings-aliases")

    assert _sha(fires) != _sha(baseline), "the imdb_rating/tmdb_rating aliases must actually draw"


async def test_plex_native_ratings_resolve_through_the_real_entry_point(
    session, config_with_badges
):
    """The plex_* four and user_rating, end to end: fires differently on an
    item that carries them vs one that does not, proving the values actually
    reach the fingerprint (and therefore the render), not just that
    apply_badges runs without error."""
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = [
        OverlayDefinition(name="text(<<plex_imdb_rating>>)"),
    ]
    unrated = await _badged(session, config_with_badges, _FakePlexItem(), "pnr-unrated")
    rated = await _badged(
        session, config_with_badges, _FakePlexItemWithRatings(), "pnr-rated"
    )
    assert _sha(unrated) != _sha(rated)


async def test_mdblist_ratings_reach_the_variable_map(session, config_with_badges):
    config_with_badges.badges.families = []
    config_with_badges.badges.definitions = [
        OverlayDefinition(name="text(<<mdb_average_rating>>)"),
    ]
    item, render = await _render(session, rating_key="mdb-fires")
    plex_item = _FakePlexItem()
    mdblist = _RecordingMDBList({"mdb_average_rating": 6.5})
    await apply_badges(
        session, config_with_badges, render, item, plex_item, _Facts(), mdblist=mdblist
    )
    assert mdblist.calls == [(item.tmdb_id, item.tvdb_id, item.kind == "movie")]


async def test_mdblist_quota_exhaustion_degrades_ratings_only_not_the_whole_badge_stage(
    session, config_with_badges
):
    from autoposter.facts.mdblist import MDBListLimitReached

    item, render = await _render(session, rating_key="mdb-limit")
    plex_item = _FakePlexItem()
    await apply_badges(
        session, config_with_badges, render, item, plex_item, _Facts(),
        mdblist=_RaisingMDBList(MDBListLimitReached("API Limit Reached!")),
    )
    assert render.badge_fingerprint is not None  # the badge stage completed


async def test_mdblist_transport_failure_degrades_ratings_only(session, config_with_badges):
    import httpx as httpx_module

    item, render = await _render(session, rating_key="mdb-httperror")
    plex_item = _FakePlexItem()
    await apply_badges(
        session, config_with_badges, render, item, plex_item, _Facts(),
        mdblist=_RaisingMDBList(httpx_module.ConnectError("boom")),
    )
    assert render.badge_fingerprint is not None


async def test_no_mdblist_client_is_a_no_op_not_an_error(session, config_with_badges):
    """`mdblist=None` is what every test predating this phase passes -- the
    same shape `http=None` already has."""
    item, render = await _render(session, rating_key="mdb-none")
    await apply_badges(session, config_with_badges, render, item, _FakePlexItem(), _Facts())
    assert render.badge_fingerprint is not None
```

- [ ] **Step 24: Run and confirm RED**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-red5 test sh -c 'pytest -q tests/test_overlay_entrypoint.py -k ratings 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-red5.log'
docker wait povr1-red5
docker cp povr1-red5:/app/.superpowers/p-overlay-c2a-t1-red5.log .superpowers/p-overlay-c2a-t1-red5.log
docker rm povr1-red5
```
Expected: `TypeError: apply_badges() got an unexpected keyword argument 'mdblist'` where passed; the alias test (`test_imdb_and_tmdb_rating_tokens_resolve_from_the_existing_facts`) and the plex-native test both fail because the tokens do not resolve (`UnresolvedVariable`, caught and skipped per definition, probe section 2.4) -- both are now pixel-hash-vs-baseline comparisons (HIGH finding, preflight review), so each fails because `fires`/`rated` comes back byte-identical to its own gate-off `baseline`/`unrated` (no badge was actually drawn), not because anything raised. Confirm each fails for that specific reason (an `AssertionError` on the `!=`, not a collection error or an unrelated failure), not a fixture bug.

- [ ] **Step 25: Implement**

In `src/autoposter/render/pipeline.py`, add to the existing import from `autoposter.badges.values`:

```python
from autoposter.badges.values import (
    MediaInfo,
    media_info_from_plex,
    plex_native_ratings,
    video_format_text,
)
```

(Adjust to match whatever the existing import list already contains — add `plex_native_ratings` to it, do not duplicate the import statement.)

Add:

```python
from autoposter.facts.mdblist import MDBListLimitReached
```

Change `apply_badges`'s signature:

```python
async def apply_badges(
    session, config, render, item, plex_item, facts, probe=None, *, http=None, mdblist=None
) -> None:
```

Add one sentence to the docstring, after the `http` paragraph:

```
    ``mdblist`` is roadmap row 100 sub-phase C2a's rating source, optional
    with a ``None`` default for the same reason ``http`` is: every existing
    caller keeps working unchanged. With no client, the eleven ``mdb_*``
    tokens simply do not resolve -- ``UnresolvedVariable`` is caught per
    definition, same as any other unresolved token.
```

Replace the `inputs = BadgeInputs(...)` block:

```python
    media = await asyncio.to_thread(media_info_from_plex, plex_item)
    critic_rating = getattr(facts, "critic_rating", None)
    audience_rating = getattr(facts, "audience_rating", None)
    ratings: dict[str, float | None] = dict(plex_native_ratings(plex_item))
    # The imdb_rating/tmdb_rating aliases (roadmap row 100, sub-phase C2a):
    # this service's IMDb/TMDb facts ARE Kometa's imdb_rating/tmdb_rating
    # rating-source values (probe bucket (a)) -- no new fetch, just making
    # the <<imdb_rating>>/<<tmdb_rating>> SPELLINGS resolve too, alongside
    # the pre-existing <<critic_rating>>/<<audience_rating>> ones.
    if critic_rating is not None:
        ratings["imdb_rating"] = critic_rating
    if audience_rating is not None:
        ratings["tmdb_rating"] = audience_rating
    # MDBList's eleven mdb_* ratings, per-pass, never persisted (adjudication
    # A7). `item.kind in ("movie", "show")` mirrors `facts/gather.py::
    # gather_facts`'s own gate for `mdblist.content_rating` exactly -- season
    # and episode badges do not carry mdb_* ratings in this slice. A
    # transient MDBList failure must degrade this one set of values, not the
    # whole badge stage: same two `except` clauses `gather_facts` already
    # uses for the same client.
    if mdblist is not None and item.kind in ("movie", "show"):
        try:
            ratings.update(await mdblist.ratings(
                tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id,
                is_movie=item.kind == "movie",
            ))
        except MDBListLimitReached:
            logger.warning("mdblist daily limit reached; skipping mdb_* overlay ratings")
        except httpx.HTTPError as exc:
            logger.warning("mdblist request failed; skipping mdb_* overlay ratings: %s", exc)
    inputs = BadgeInputs(
        media=media,
        critic_rating=critic_rating,
        audience_rating=audience_rating,
        content_rating=getattr(facts, "content_rating", None),
        video_format=video_format_text(media),
        ratings=ratings,
    )
```

(`httpx` and `logger` are already module-level in `pipeline.py`; do not re-import.)

Further down the same function, the existing `badge_fingerprint(...)` call already folds `definitions` and `outcomes` in; extend it with the `ratings` dict built above (Concern D's new fold, Global Constraint 6) -- this is what actually threads the resolved values to the fingerprint gate, not just to `_variable_values`:

```python
    fingerprint = badge_fingerprint(
        render.fingerprint or "", render.art_kind, values, manifest_sha(),
        definitions, outcomes, ratings=ratings,
    )
```

Finally, update the one production call site (near line 1522, inside `process_item`):

```python
                await apply_badges(
                    session, config, render, media_item, plex_item, facts,
                    probe=artwork_probe, http=http, mdblist=mdblist,
                )
```

- [ ] **Step 26: Run and confirm GREEN**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-green5 test sh -c 'pytest -q tests/test_overlay_entrypoint.py tests/test_badge_pipeline.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-green5.log'
docker wait povr1-green5
docker cp povr1-green5:/app/.superpowers/p-overlay-c2a-t1-green5.log .superpowers/p-overlay-c2a-t1-green5.log
docker rm povr1-green5
```
Expected: all pass. `test_badge_pipeline.py` is included because it calls `apply_badges` positionally at ~30 sites — the new `mdblist=None` keyword-only argument must not break any of them.

- [ ] **Step 27: Commit**

```bash
git add tests/test_overlay_entrypoint.py src/autoposter/render/pipeline.py
git commit -m "feat(pipeline): wire imdb/tmdb/user aliases and mdb_* ratings into apply_badges"
```

### Concern F — the `versions` row: adjudication A14, ruled

- [ ] **Step 28: Write the failing tests**

Append to `tests/test_collection_filters.py`, near the other attribute-specific tests:

```python
def test_versions_is_filterable_with_the_int_operators():
    """Dialect 1 of A14's both-dialect pin: `versions.gt` in a `filters:`
    block, the way the versions overlay family (T2) will select on it."""
    assert evaluate(parse_filters({"versions.gt": 1}), _view("versions", 2)) is True
    assert evaluate(parse_filters({"versions.gt": 1}), _view("versions", 1)) is False
    assert evaluate(parse_filters({"versions.gt": 1}), _view("versions", None)) is False


def test_versions_is_not_searchable_and_the_refusal_says_where_it_lives():
    """Dialect 2 of A14's both-dialect pin: `versions` is filterable but has
    no Plex search field, so a `plex_search:` use of it is the FIRST REAL
    (non-synthetic) row to reach `_split_key`'s `searching and not
    attribute.searchable` branch -- written and proven with a synthetic row
    since search-tails-1
    (`test_a_search_refuses_a_filter_only_attribute_naming_the_other_block`
    above); reachable for real now."""
    with pytest.raises(ValueError) as error:
        parse_filters({"versions.gt": 1}, searching=True)
    message = str(error.value)
    assert "versions" in message
    assert "no search field" in message
    assert "filters:" in message
```

Then update the four EXISTING literal-count assertions the new row moves (measured, not guessed, at Step 31 — the values below are already computed against this tree by running the table's own arithmetic and are recorded here so the RED step below fails for exactly these reasons):

`test_the_table_holds_exactly_the_tier_one_rows` (append to the ordered list, after `"unmatched"`):
```python
        "unmatched",
        "versions",
    ]
```

`test_the_column_totals_are_the_transcriptions_checksum`:
```python
    assert {k: len(v) for k, v in by_type.items()} == {
        "tag": 13,
        "str": 3,
        "int": 4,
        "float": 3,
        "date": 3,
        "duration": 1,
        "bool": 7,
    }
    assert by_source["listing"] == [
        "year",
        "resolution",
        "audience_rating",
        "critic_rating",
        "content_rating",
        "added",
        "release",
        "duration",
        "studio",
        "plays",
        "last_played",
        "user_rating",
        "versions",
    ]
```

`test_item_kinds_are_movie_show_or_both`:
```python
    assert len([r for r in FILTER_ATTRIBUTES if r.kinds == ("movie", "show")]) == 21
```

`test_the_search_kinds_column_is_its_own_and_differs_from_kinds`:
```python
    assert Counter(row.search_kinds for row in FILTER_ATTRIBUTES) == {
        ("movie", "show"): 24, ("movie",): 8, ("show",): 1, (): 1,
    }
```

`test_every_row_is_searchable_and_twentyfive_are_filterable` (rename to `test_every_row_but_versions_is_searchable_and_twentysix_are_filterable`):
```python
def test_every_row_but_versions_is_searchable_and_twentysix_are_filterable():
    """C2a's `versions` row (adjudication A14) is the table's first
    filterable-but-not-searchable row: it has no Plex search field at all
    (Kometa's own `versions` filter has none -- the search-side spelling is
    the separate, unfilterable `duplicate` row). Every other row remains
    both, unchanged."""
    from autoposter.collections.filters import (
        FILTERABLE_ATTRIBUTES,
        FILTER_ATTRIBUTES,
        SEARCHABLE_ATTRIBUTES,
    )

    assert all(row.searchable for row in FILTER_ATTRIBUTES if row.name != "versions")
    assert BY_NAME["versions"].searchable is False
    assert len(SEARCHABLE_ATTRIBUTES) == 33
    assert len(FILTERABLE_ATTRIBUTES) == 26
    assert set(SEARCHABLE_ATTRIBUTES) - set(FILTERABLE_ATTRIBUTES) == {
        "unplayed", "progress", "decade",
        "hdr", "dovi", "trash", "duplicate", "unmatched",
    }
    assert set(FILTERABLE_ATTRIBUTES) - set(SEARCHABLE_ATTRIBUTES) == {"versions"}
```

- [ ] **Step 29: Run and confirm RED**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-red6 test sh -c 'pytest -q tests/test_collection_filters.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-red6.log'
docker wait povr1-red6
docker cp povr1-red6:/app/.superpowers/p-overlay-c2a-t1-red6.log .superpowers/p-overlay-c2a-t1-red6.log
docker rm povr1-red6
```
Expected: the two new tests fail with `KeyError`/`ValueError: unknown filter attribute 'versions'`; the five updated count assertions fail against the CURRENT (pre-row) table.

- [ ] **Step 30: Add the row — the ONE permitted edit to `filters.py`**

At the end of `FILTER_ATTRIBUTES` (immediately after the `"unmatched"` row, before the tuple's closing paren):

```python
    FilterAttribute(
        "versions", "int", _BOTH, "listing",
        "How many `<Media>` versions the item carries -- Kometa's `versions` "
        "filter counts media items (one of the 44 filter names with no Plex "
        "search field; the SEARCH-side spelling is the separate `duplicate` "
        "row above, movie-only and unfilterable). `listing`: the section "
        "listing carries `<Media>` in full and un-truncated (9a's probe "
        "measured the per-item histogram {1: 1905, 2: 46, 3: 4}, recorded on "
        "the `resolution` row above), which is the same read that row "
        "already relies on. Adjudication A14: raised by C1's plan (the "
        "`versions` overlay family cannot select on `duplicate`, which is "
        "search-only), ruled here by sub-phase C2a -- the one row this "
        "sub-phase's own Global Constraint 2 permits, additive-only.",
        search_field=None, show_search_field=None,
        search_kinds=(), filterable=True,
    ),
```

- [ ] **Step 31: Run and confirm GREEN**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-green6 test sh -c 'pytest -q tests/test_collection_filters.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-green6.log'
docker wait povr1-green6
docker cp povr1-green6:/app/.superpowers/p-overlay-c2a-t1-green6.log .superpowers/p-overlay-c2a-t1-green6.log
docker rm povr1-green6
```
Expected: all pass. If any of Step 28's literal counts do NOT match (they were computed against this exact tree beforehand, so a mismatch means the tree drifted since this plan was written) — **STOP and report the actual numbers** rather than editing the assertion to whatever makes it pass.

- [ ] **Step 32: Commit**

```bash
git add tests/test_collection_filters.py src/autoposter/collections/filters.py
git commit -m "feat(collections): rule adjudication A14 -- the versions filter row"
```

### Concern G — the `versions` accessor, shared by both views

- [ ] **Step 33: Write the failing tests**

Append to `tests/test_collection_filter_values.py`:

```python
def test_versions_reads_the_media_count():
    """The same `resolution` fixture (`test_resolution_reads_every_version_
    of_a_multi_version_movie` above), counted rather than valued."""
    xml = MOVIE_XML.replace(
        '<Media id="9" videoResolution="1080" width="1920"><Part id="1" file="/m.mkv"/></Media>',
        '<Media id="9" videoResolution="1080"><Part id="1" file="/a.mkv"/></Media>'
        '<Media id="10" videoResolution="4k"><Part id="2" file="/b.mkv"/></Media>',
    )
    assert PlexItemView(a_movie(xml)).get("versions") == 2
    assert PlexItemView(a_movie()).get("versions") == 1


def test_versions_is_missing_not_zero_for_an_item_with_no_media():
    assert PlexItemView(a_show()).get("versions") is None
```

And rename+extend `test_the_twelve_shipped_families_are_named`:

```python
def test_the_thirteen_shipped_families_are_named():
    assert SHIPPED_ATTRIBUTES == (
        "year",
        "resolution",
        "audience_rating",
        "critic_rating",
        "content_rating",
        "added",
        "release",
        "duration",
        "studio",
        "plays",
        "last_played",
        "user_rating",
        "versions",
    )
    assert BATCHED_ATTRIBUTES == (
        "genre",
        "audio_language",
        "subtitle_language",
        "label",
        "collection",
    )
    assert DEFERRED_ATTRIBUTES == ("network",)
```

- [ ] **Step 34: Run and confirm RED**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-red7 test sh -c 'pytest -q tests/test_collection_filter_values.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-red7.log'
docker wait povr1-red7
docker cp povr1-red7:/app/.superpowers/p-overlay-c2a-t1-red7.log .superpowers/p-overlay-c2a-t1-red7.log
docker rm povr1-red7
```
Expected: `test_an_accessor_exists_for_exactly_the_listing_rows` and `test_the_runtime_accessor_map_matches_the_listing_rows` already RED from Step 30/32 (the table now has a `versions` row with no accessor yet); the two new tests fail with `AttributeNotInListing`; the renamed test fails with `NameError`/collection error until the old name is gone.

- [ ] **Step 35: Implement**

In `src/autoposter/collections/filter_values.py`, after `_resolutions`:

```python
def _versions(item: object) -> int | None:
    """How many `<Media>` versions this item carries -- Kometa's `versions`
    filter. The same reload-free listing read `_resolutions` uses, counted
    rather than valued. Answers None (missing), not 0, for an item with no
    `<Media>` at all -- exactly as `_resolutions` does; `filters._is_missing`
    treats both the same regardless of the attribute's type."""
    media = _listing_value(item, "media")
    if not media:
        return None
    return len(media)
```

And register it:

```python
_ACCESSORS = {name: _passthrough(attrib) for name, attrib in _LISTING_ATTRIBS.items()}
_ACCESSORS["duration"] = _duration_minutes
_ACCESSORS["resolution"] = _resolutions
_ACCESSORS["versions"] = _versions
```

- [ ] **Step 36: Run and confirm GREEN**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-green7 test sh -c 'pytest -q tests/test_collection_filter_values.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-green7.log'
docker wait povr1-green7
docker cp povr1-green7:/app/.superpowers/p-overlay-c2a-t1-green7.log .superpowers/p-overlay-c2a-t1-green7.log
docker rm povr1-green7
```
Expected: all pass.

- [ ] **Step 37: Commit**

```bash
git add tests/test_collection_filter_values.py src/autoposter/collections/filter_values.py
git commit -m "feat(collections): the versions accessor -- a listing Media count"
```

### Concern H — `OverlayItemView` grows `versions`, and the two LOW rewords (L-2, L-4)

- [ ] **Step 38: Write the failing tests**

Update the existing vocabulary pin in `tests/test_overlay_selection.py`:

```python
def test_the_vocabulary_is_exactly_what_this_slice_supplies():
    assert OVERLAY_ATTRIBUTES == ("content_rating", "resolution", "versions")
```

Append:

```python
def test_versions_answers_the_media_count():
    item = _Item(resolutions=("4k", "1080"))
    assert _view(item).get("versions") == 2


def test_an_item_with_no_media_has_no_versions():
    item = _Item()
    item.media = []
    assert _view(item).get("versions") is None


def test_the_two_views_agree_on_versions():
    """Global Constraint 9 -- structural here, not just tested: both views
    import the SAME `_versions` function object from `filter_values`, the
    same way `resolution` already does."""
    for resolutions in (("1080",), ("4k", "1080"), ()):
        item = _Item(resolutions=resolutions)
        assert _view(item).get("versions") == PlexItemView(item).get("versions")


def test_a_condition_can_now_name_versions():
    group = parse_condition({"versions.gt": 1})
    [written] = predicates_of(group)
    assert written.attribute.name == "versions"
    assert written.operator == "gt"


def test_a_search_only_attribute_is_refused_with_an_overlay_appropriate_message():
    """L-4, met here: filters.py's own message for a search-only attribute
    (`duplicate` -- still search-only after this phase; only `versions` is
    new) tells a COLLECTION operator to 'move it into the plex_search
    builder's params'. An overlay `condition:` has no plex_search builder to
    move it into, so `parse_condition` re-words the remedy rather than
    repeating Kometa's advice verbatim; the attribute is refused either way."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"duplicate": True})
    message = str(caught.value)
    assert "duplicate" in message
    assert "plex_search builder" not in message
    assert "condition" in message
```

Also add the import needed for the agreement pin (if not already present from C1):

```python
from autoposter.collections.filter_values import PlexItemView
```

- [ ] **Step 39: Run and confirm RED**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-red8 test sh -c 'pytest -q tests/test_overlay_selection.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-red8.log'
docker wait povr1-red8
docker cp povr1-red8:/app/.superpowers/p-overlay-c2a-t1-red8.log .superpowers/p-overlay-c2a-t1-red8.log
docker rm povr1-red8
```
Expected: the vocabulary pin fails (`("content_rating", "resolution") != ("content_rating", "resolution", "versions")`); the four `versions`-condition tests fail with `AttributeNotOnItem`/parse refusal; the L-4 test currently PASSES for the wrong reason or fails on `"plex_search builder" not in message` (filters.py's raw message is what `parse_condition` returns today) — confirm it fails on that specific assertion, not on `"duplicate" in message`.

- [ ] **Step 40: Implement**

In `src/autoposter/overlays/selection.py`, widen the import:

```python
from autoposter.collections.filter_values import _listing_value, _resolutions, _versions
```

Grow the vocabulary and its comment:

```python
# The attributes this view supplies, in `collections/filters.py`'s own
# spelling -- the key `evaluate` reads.
#
# - `content_rating`: PLEX's certification string (adjudication A5)...
# - `resolution`: every version's `videoResolution`...
# - `versions` (sub-phase C2a): how many `<Media>` versions the item
#   carries -- adjudication A14's row, shared verbatim with
#   `filter_values.PlexItemView` via the same `_versions` function object,
#   so Global Constraint 9's agreement is structural rather than merely
#   tested.
#
# Later slices append; each addition owes a source note here and an
# agreement pin against `filter_values.PlexItemView` if that view supplies
# it too.
OVERLAY_ATTRIBUTES: tuple[str, ...] = ("content_rating", "resolution", "versions")
```

Find and replace this paragraph in the module docstring (the one immediately following the `**The VIEW is ours...**` paragraph, ending "...rather than needing a `kinds` column to forbid it."):

```
So the collections table's per-attribute source tiers and `kinds`
restrictions are LISTING constraints, and they do not bind here (the
recon's adjudication A3b): a show simply has no `media`, which the tag
missing-value rule already answers correctly, rather than needing a
`kinds` column to forbid it. **T1 review finding L-2, met here (sub-phase
C2a):** that "do not bind" claim is about the `kinds` COLUMN specifically
-- `resolution`'s `kinds=("movie",)` is the one restriction this view has
ever genuinely relaxed, because `parse_filters` never consults `kinds` at
all (verified by reading the whole parse path). It says nothing about
which attributes have an ACCESSOR: `audio_language`, `duplicate` and
`network` have none on this view and are refused by `OVERLAY_ATTRIBUTES`
membership, a wholly different mechanism, and stay refused regardless of
`kinds`. `versions` is the first attribute genuinely newly unlocked since
C1 -- a real `filters.py` row, a real accessor, both dialects proven.
```

Add the branch to `get()`:

```python
        if attribute == "content_rating":
            return _listing_value(self._plex_item, "contentRating") or None
        if attribute == "resolution":
            return _resolutions(self._plex_item)
        if attribute == "versions":
            return _versions(self._plex_item)
        raise AttributeNotOnItem(
            f"{attribute!r} is not an attribute an overlay condition can "
            "read on this service. Available: " + ", ".join(OVERLAY_ATTRIBUTES)
        )
```

Rework `parse_condition` for L-4:

```python
_SEARCH_ONLY_MARKER = "Move it into the plex_search builder's"


def parse_condition(raw: object, *, field: str = "condition") -> FilterGroup:
    """Parse one definition's `condition:` block, or refuse naming the key.

    Two layers, in this order, because the messages are different and both
    are useful:

    1. `collections.filters.parse_filters` -- the shared grammar...
    2. the overlay NARROWING -- an attribute that is a legitimate collections
       filter but that `OverlayItemView` cannot supply...

    A third, narrower case rides inside layer 1 (T1 review finding L-4, met
    here): a SEARCH-ONLY attribute (`duplicate`, `unplayed`, ...) is refused
    by `filters.py` with "Move it into the plex_search builder's `params`" --
    correct advice for a COLLECTION, meaningless for an overlay `condition:`
    block, which has no plex_search builder to move anything into. The
    attribute is unavailable to an overlay either way; only the remedy
    sentence is wrong-audience, and `filters.py` may not be edited to fix it
    (Global Constraint 2), so the reword happens here, by catching that one
    specific message rather than re-implementing the check.

    `field` is the dotted path the refusal names, so an operator with twenty
    definitions can find the one that is wrong.
    """
    try:
        group = parse_filters(raw, field=field)
    except ValueError as exc:
        message = str(exc)
        if _SEARCH_ONLY_MARKER in message:
            name = message.split("'")[1] if "'" in message else "?"
            raise ValueError(
                f"{field}: {name!r} is a Plex smart-search-only attribute "
                "(Kometa has no `filters:` equivalent for it either), and an "
                "overlay condition cannot use it at all -- there is no "
                "plex_search builder here to move it into. Available: "
                + ", ".join(OVERLAY_ATTRIBUTES)
            ) from None
        raise
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
```

Finally, replace the matching paragraph in `tests/test_overlay_selection.py`'s own module docstring (L-2's twin, per the T1 review's own note that it appears there too). LOW finding, preflight review: the current text (verified against the working tree, not paraphrased) reads:

```
So the four attributes the collections table defers or scopes for that reason
(`audio_language`, `resolution`'s movie-only kind, `duplicate`, `network`) do
not bind here -- the recon's own table, adjudication A3b answered by
construction rather than by relaxing a column.
```

Replace it with:

```
So the four attributes the collections table defers or scopes for that reason
(`audio_language`, `resolution`'s movie-only kind, `duplicate`, `network`) do
not bind here -- the recon's own table, adjudication A3b answered by
construction rather than by relaxing a column. That is a `kinds`-column fact
only, not an accessor one (T1 review finding L-2): `audio_language`,
`duplicate` and `network` still have no accessor on this view and are refused
the same way they always were. `versions` (sub-phase C2a) is the first
attribute this view has genuinely gained since C1.
```

- [ ] **Step 41: Run and confirm GREEN**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-green8 test sh -c 'pytest -q tests/test_overlay_selection.py tests/test_overlay_schema.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-green8.log'
docker wait povr1-green8
docker cp povr1-green8:/app/.superpowers/p-overlay-c2a-t1-green8.log .superpowers/p-overlay-c2a-t1-green8.log
docker rm povr1-green8
```
Expected: all pass.

- [ ] **Step 42: Commit**

```bash
git add tests/test_overlay_selection.py src/autoposter/overlays/selection.py
git commit -m "feat(overlays): versions in the overlay vocabulary; the L-2/L-4 rewords"
```

- [ ] **Step 43: Full-suite check for Task 1**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-full.log'
docker wait povr1-full
docker cp povr1-full:/app/.superpowers/p-overlay-c2a-t1-full.log .superpowers/p-overlay-c2a-t1-full.log
docker rm povr1-full
tail -n 5 .superpowers/p-overlay-c2a-t1-full.log
```
Expected: `0 failed`. Record the pass count for T3's ledger.

- [ ] **Step 44: Ruff and scope check**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr1-ruff test sh -c 'ruff check . 2>&1 | tee /app/.superpowers/p-overlay-c2a-t1-ruff.log'
docker wait povr1-ruff
docker cp povr1-ruff:/app/.superpowers/p-overlay-c2a-t1-ruff.log .superpowers/p-overlay-c2a-t1-ruff.log
docker rm povr1-ruff
git diff --stat <task-1-start-sha> -- src/ tests/ assets/
```
Expected: ruff clean; the diff lists only files in this task's **Files** block (substitute `<task-1-start-sha>` for the commit hash recorded at Step 1).

- [ ] **Step 45: Tear down**

```bash
docker compose -p povr1 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## Task 2: The `versions` family — vendored art, definition, fires/silent pin

**Files:**
- Create: `assets/badges/images/versions.png`
- Modify: `assets/badges/OVERLAY-MANIFEST.sha256`, `assets/badges/PROVENANCE.md`, `src/autoposter/overlays/families.py`
- Test: `tests/test_overlay_families.py`, `tests/test_overlay_entrypoint.py`

**Interfaces:**
- Consumes: T1's `OVERLAY_ATTRIBUTES` (now including `"versions"`), `overlays.schema.OverlayDefinition`, `overlays.assets.{ASSETS, IMAGES}`.
- Produces: `FAMILIES["versions"]`, consumed by `config.schema.BadgesConfig.all_definitions()` (already generic over `FAMILIES`'s keys — no `config/schema.py` edit needed).

- [ ] **Step 1: Vendor `versions.png` and `versions.yml` from the pinned Kometa image**

```bash
mkdir -p .superpowers/p-overlay-c2a-vendor
IMG="docker.io/kometateam/kometa@sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a"
CID=$(docker create --entrypoint sh "$IMG")
docker cp "$CID:/defaults/overlays/images/versions.png" .superpowers/p-overlay-c2a-vendor/versions.png
docker cp "$CID:/defaults/overlays/versions.yml" .superpowers/p-overlay-c2a-vendor/versions.yml
docker rm -f "$CID"
cp .superpowers/p-overlay-c2a-vendor/versions.png assets/badges/images/versions.png
cat .superpowers/p-overlay-c2a-vendor/versions.yml
```

Read the vendored `versions.yml`. Probe section 4.8 already gives the box (105×105) and the non-episode position (`horizontal_offset: 15`, `vertical_align: bottom`, `vertical_offset: 335`); confirm both against the file. **If either differs from the probe's stated numbers, STOP and report the difference** before Step 2 — the same discipline C1's Task 2 used for the UK 8-bucket discovery (`.superpowers/sdd/2026-09-03-overlay-families-c1/task-2-report.md`, "Deviation 2"). `back_color`/`back_radius` are not stated by the probe for this specific family; if the vendored file sets them explicitly, use those values; if it inherits from the un-vendored `templates.yml` the way `direct_play.yml` did in C1 (no `back_color`/`back_radius` key in the file body), use `back_color="#00000099"`, `back_radius=30` — the same "standard" template default every other C1/C2a family uses, and say so in the code comment (matching `families.py`'s existing `DIRECT_PLAY` comment's own reasoning for the identical situation).

- [ ] **Step 2: Regenerate `OVERLAY-MANIFEST.sha256` inside the Linux container, not the host shell**

C1's Task 2 found that running `find | xargs sha256sum` from this Windows/git-bash host produces binary-mode lines (`<hash> *<path>`) instead of the GNU coreutils text-mode (`<hash>  <path>`, two spaces) the existing manifest and its parser (`line.partition("  ")`) require. Regenerate inside the container:

```bash
docker compose -p povr2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr2-manifest test sh -c \
  'cd assets/badges && find images/cr images/Direct-Play.png images/versions.png -type f | LC_ALL=C sort | xargs sha256sum > OVERLAY-MANIFEST.sha256 && cat OVERLAY-MANIFEST.sha256'
docker wait povr2-manifest
docker cp povr2-manifest:/app/assets/badges/OVERLAY-MANIFEST.sha256 assets/badges/OVERLAY-MANIFEST.sha256
docker rm povr2-manifest
wc -l assets/badges/OVERLAY-MANIFEST.sha256
```
Expected: one more line than before T2 (100, given C1 shipped 99 — reconcile against the actual pre-T2 count from `git show HEAD:assets/badges/OVERLAY-MANIFEST.sha256 | wc -l` if it differs).

- [ ] **Step 3: Update `PROVENANCE.md`**

Add one row to the "What was taken" table, after the `Direct-Play.png` row:

```
| `/defaults/overlays/images/versions.png` | `images/versions.png` | 1 | the versions overlay family |
```

And update the "Deliberately not taken" paragraph — remove `versions` from what remains untaken (it names no `versions` line today; if the exact wording needs adjustment, confirm the sentence still reads correctly with `versions.png` now present under "What was taken").

- [ ] **Step 4: Write the failing tests**

In `tests/test_overlay_families.py`, replace `test_versions_is_not_shipped_and_the_reason_is_recorded`:

```python
def test_versions_is_shipped_now_that_adjudication_a14_is_ruled():
    """Adjudication A14 (raised by C1's plan, ruled by C2a's T1): the
    `versions` row in `collections/filters.py` now exists, so the family
    that was fenced can ship. One definition: `versions.gt: 1` -- more than
    one `<Media>` entry, the same threshold Kometa's `duplicate` smart
    search meant, expressed on the new filterable int attribute instead
    (`duplicate` itself stays search-only and unusable in a `condition:`
    block; see the L-4 fix)."""
    definitions = FAMILIES["versions"]
    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.name == "versions"
    assert definition.builtin == "versions"
    assert definition.condition == {"versions.gt": 1}
    assert (definition.back_width, definition.back_height) == (105, 105)
```

Update `test_the_family_art_has_its_own_manifest_and_it_is_accurate`:

```python
    assert len(lines) == CR_COUNT + 2  # + Direct-Play.png + versions.png
```

- [ ] **Step 5: Run and confirm RED**

```bash
docker compose -p povr2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr2-red1 test sh -c 'pytest -q tests/test_overlay_families.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t2-red1.log'
docker wait povr2-red1
docker cp povr2-red1:/app/.superpowers/p-overlay-c2a-t2-red1.log .superpowers/p-overlay-c2a-t2-red1.log
docker rm povr2-red1
```
Expected: `KeyError: 'versions'` from the new test; the manifest-count test already passes or fails depending on Step 2's ordering — if it fails here, that is expected (implementation is Step 6).

- [ ] **Step 6: Implement the family**

In `src/autoposter/overlays/families.py`, add before the `FAMILIES` dict (fill in `back_color`/`back_radius`/position from Step 1's actual vendored values if they differed from the probe's stated numbers):

```python
# Probe section 4.8: a single static badge, 105x105. Selection is Plex's
# `duplicate`/`episode_duplicate` smart search upstream; this service selects
# on the NEW `versions` int filter attribute instead (adjudication A14),
# because `duplicate` itself is search-only and unfilterable, so an overlay
# `condition:` block cannot name it at all. `versions.gt: 1` means "more than
# one `<Media>` entry" -- the same threshold. Position is the non-episode
# default (`horizontal_offset: 15`, `vertical_offset: 335`); the
# episode-specific position (235/270) is the one positioning conditional in
# the whole row-100 set that branches on `builder_level`, and this module
# ships flat, resolved definitions rather than a template engine (the same
# simplification C1 already made for every other family), so only the
# non-episode position is reproduced.
VERSIONS: list[OverlayDefinition] = [
    OverlayDefinition(
        name="versions",
        builtin="versions",
        condition={"versions.gt": 1},
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=335,
        back_width=105, back_height=105,
        back_color="#00000099", back_radius=30,
    ),
]
```

Update the module docstring's opening ("Two families ship...") to say three, and drop the paragraph explaining why `versions` does not ship (adjudication A14 is now ruled, not pending):

```python
"""The shipped overlay families (roadmap row 100, sub-phases C1 and C2a).

Flat `OverlayDefinition` lists, not templates. Kometa expresses each family
as a templated YAML file whose `<<key>>` resolver this service does not
implement (and does not need: the recon's cut emits flat definitions), so
every number here is the RESOLVED value, transcribed from the pinned
v2.4.8 tree -- the same image digest `assets/badges/PROVENANCE.md` records.

Three families ship: `direct_play`, the six content-rating regionals (C1),
and `versions` (C2a, adjudication A14 ruled -- the `collections/filters.py`
row it needed now exists).

Each family is opt-in through `config.badges.families`, and a family that is
not named costs nothing: no definition, no fingerprint movement, no asset
read.
"""
```

Add to `FAMILIES`:

```python
FAMILIES: dict[str, list[OverlayDefinition]] = {
    "direct_play": DIRECT_PLAY,
    "content_rating_au": CONTENT_RATING_AU,
    "content_rating_de": CONTENT_RATING_DE,
    "content_rating_nz": CONTENT_RATING_NZ,
    "content_rating_uk": CONTENT_RATING_UK,
    "content_rating_us_movie": CONTENT_RATING_US_MOVIE,
    "content_rating_us_show": CONTENT_RATING_US_SHOW,
    "versions": VERSIONS,
}
```

- [ ] **Step 7: Run and confirm GREEN**

```bash
docker compose -p povr2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr2-green1 test sh -c 'pytest -q tests/test_overlay_families.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t2-green1.log'
docker wait povr2-green1
docker cp povr2-green1:/app/.superpowers/p-overlay-c2a-t2-green1.log .superpowers/p-overlay-c2a-t2-green1.log
docker rm povr2-green1
```
Expected: all pass.

- [ ] **Step 8: Commit the vendored art and the family**

```bash
git add assets/badges/images/versions.png assets/badges/OVERLAY-MANIFEST.sha256 assets/badges/PROVENANCE.md src/autoposter/overlays/families.py tests/test_overlay_families.py
git commit -m "feat(overlays): ship the versions family (adjudication A14 ruled)"
```

### Concern — the fires/silent pin through the real entry point

- [ ] **Step 9: Write the failing test**

Append to `tests/test_overlay_entrypoint.py`, beside `test_direct_play_fires_on_a_4k_item_and_is_silent_on_a_1080_one`:

```python
class _FakePlexItemMultiVersion(_FakePlexItem):
    """Two `<Media>` entries -- what `versions.gt: 1` selects on."""

    def __init__(self):
        super().__init__()
        self.media = self.media + [type("M", (), {
            "parts": [type("P", (), {"file": None})()],
            "videoResolution": "4k", "audioCodec": "eac3", "audioChannels": 6,
        })()]


async def test_versions_fires_on_a_multi_version_item_and_is_silent_on_a_single_one(
    session, config_with_badges
):
    config_with_badges.badges.families = []
    base_single = await _badged(session, config_with_badges, _FakePlexItem(), "v-base-1")
    base_multi = await _badged(
        session, config_with_badges, _FakePlexItemMultiVersion(), "v-base-2"
    )

    config_with_badges.badges.families = ["versions"]
    silent = await _badged(session, config_with_badges, _FakePlexItem(), "v-1")
    fires = await _badged(session, config_with_badges, _FakePlexItemMultiVersion(), "v-2")

    assert _sha(silent) == _sha(base_single), "a single version must draw the gate-off pixels"
    assert _sha(fires) != _sha(base_multi), "a multi-version item must actually draw the badge"
```

- [ ] **Step 10: Run and confirm RED**

```bash
docker compose -p povr2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr2-red2 test sh -c 'pytest -q tests/test_overlay_entrypoint.py -k versions 2>&1 | tee /app/.superpowers/p-overlay-c2a-t2-red2.log'
docker wait povr2-red2
docker cp povr2-red2:/app/.superpowers/p-overlay-c2a-t2-red2.log .superpowers/p-overlay-c2a-t2-red2.log
docker rm povr2-red2
```
Expected: this test should already be GREEN once Step 6 lands (Task 1's plumbing plus Task 2's Step 6 family are both prerequisites already satisfied by this point in a linear execution) — if it is unexpectedly RED, that is a real defect: STOP and diagnose before proceeding, per **systematic-debugging**, rather than editing the test to match wrong behaviour.

- [ ] **Step 11: Confirm GREEN and commit**

```bash
docker compose -p povr2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr2-green2 test sh -c 'pytest -q tests/test_overlay_entrypoint.py 2>&1 | tee /app/.superpowers/p-overlay-c2a-t2-green2.log'
docker wait povr2-green2
docker cp povr2-green2:/app/.superpowers/p-overlay-c2a-t2-green2.log .superpowers/p-overlay-c2a-t2-green2.log
docker rm povr2-green2
```
Expected: all pass.

```bash
git add tests/test_overlay_entrypoint.py
git commit -m "test(overlays): the versions family fires/silent pin, through apply_badges"
```

- [ ] **Step 12: Full-suite check for Task 2**

```bash
docker compose -p povr2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr2-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/p-overlay-c2a-t2-full.log'
docker wait povr2-full
docker cp povr2-full:/app/.superpowers/p-overlay-c2a-t2-full.log .superpowers/p-overlay-c2a-t2-full.log
docker rm povr2-full
tail -n 5 .superpowers/p-overlay-c2a-t2-full.log
```
Expected: `0 failed`.

- [ ] **Step 13: Scope check and teardown**

```bash
git diff --stat <task-2-start-sha> -- src/ tests/ assets/
docker compose -p povr2 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```
Expected: only files in this task's **Files** block.

---

## Task 3: Wrap — the roadmap ledger, `progress.md`, whole-branch verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, `.superpowers/sdd/progress.md`

**Interfaces:** none — this task reads the branch's own history and the two prior tasks' artifacts; it produces no code.

- [ ] **Step 1: Update row 100's cell**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, row 100's cell currently reads (in full, this is the exact text to locate):

Find the substring beginning `**Still open, and named:** C2a the MDBList ratings parse (the highest value-per-line item in the row` and ending `...advice — \`parse_condition\` needs its own re-word once a \`condition:\` block can legitimately name one); C2b \`aspect\`` (the text runs on into C2b without a paragraph break — locate the exact span in the live file before editing; it is one continuous sentence run inside the row's single table cell).

Replace that span with:

```
**C2a — SHIPPED:** the eleven `mdb_*` MDBList ratings (`facts/mdblist.py::parse_ratings`, the same response `content_rating` already fetches — zero new HTTP), the four `plex_*` Plex-embedded ratings and `user_rating` (`badges/values.py::plex_native_ratings`, reload-free), the `imdb_rating`/`tmdb_rating` aliases onto the facts this service already gathers, and `versions`' one-row `filters.py` adjudication (A14) — the `versions` int attribute (`listing`, filterable, NOT searchable: Kometa's own `versions` filter has no Plex search field), both dialects pinned (a `filters:` block matches; a `plex_search:` use is refused naming the `filters:` block instead) — plus the `versions` overlay family itself, one definition (`versions.gt: 1`), now that the row it needed exists. **Two of the T1 review's LOW findings, met, not merely deferred:** L-2 (`selection.py`'s docstring no longer overclaims which attributes "do not bind" — the `kinds`-column relaxation and the accessor question are now stated as the separate things they are, and `versions` is named as the first attribute genuinely gained since C1) and L-4 (`parse_condition` catches `filters.py`'s search-only refusal and re-words its remedy for an overlay caller, which has no plex_search builder to move a key into). **Still open, and named:** C2b `aspect`
```

- [ ] **Step 2: Run and confirm the edit is syntactically sound**

```bash
grep -c '| 100 |' docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
```
Expected: `1` — the row is still exactly one table row (a stray unescaped `|` in the new text would split it).

- [ ] **Step 3: Commit the roadmap edit**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit -m "docs(roadmap): C2a closes -- ratings wired, A14 ruled, L-2/L-4 met"
```

- [ ] **Step 4: Append the sub-phase wrap entry**

Read the tail of `.superpowers/sdd/progress.md` to match its existing entry format (each sub-phase's own entries follow the same shape as C1's), then append an entry for C2a naming: the branch, the three tasks, the full-suite pass count from Task 1 Step 43 and Task 2 Step 12, and a one-line pointer to this plan's path.

- [ ] **Step 5: Commit**

```bash
git add .superpowers/sdd/progress.md
git commit -m "docs(progress): overlay ratings sub-phase C2a wrap"
```

- [ ] **Step 6: Whole-branch verification**

```bash
docker compose -p povr3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr3-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/p-overlay-c2a-t3-full.log'
docker wait povr3-full
docker cp povr3-full:/app/.superpowers/p-overlay-c2a-t3-full.log .superpowers/p-overlay-c2a-t3-full.log
docker rm povr3-full
tail -n 5 .superpowers/p-overlay-c2a-t3-full.log
docker compose -p povr3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povr3-ruff test sh -c 'ruff check . 2>&1 | tee /app/.superpowers/p-overlay-c2a-t3-ruff.log'
docker wait povr3-ruff
docker cp povr3-ruff:/app/.superpowers/p-overlay-c2a-t3-ruff.log .superpowers/p-overlay-c2a-t3-ruff.log
docker rm povr3-ruff
git diff --stat origin/main -- src/ tests/ assets/ docs/
docker compose -p povr3 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```
Expected: `0 failed`, ruff clean, and the whole-branch diff lists only files this plan's three **Files** blocks named.

- [ ] **Step 7: Hand off**

Use **superpowers:finishing-a-development-branch** to decide push / PR / further review. Do not push or open a PR without that skill's checklist.

---

## Self-review

**Coverage vs the facts (`p-overlay-c-facts.md` C1.4, C5) and the probe:**
- MDBList ratings parse — Task 1, Concerns A–B. ✓
- The four `plex_*` and imdb/tmdb/user aliases — Task 1, Concerns C–E. ✓
- `versions`' one-row `filters.py` adjudication (A14), both-dialect pins — Task 1, Concern F. ✓
- The `versions` family shipped — Task 2. ✓
- L-2 and L-4 met — Task 1, Concern H. ✓
- A7 (per-pass, no migration) — `BadgeInputs.ratings` is request-scoped, never written to `item_facts`; stated in Global Constraint 7 and Task 1 Step 25's code comment.
- The rescaling table transcribed exactly, letterboxd's `*2` included — Task 1 Step 4, with the value-vs-score sourcing decision recorded as reasoning rather than presented as a transcribed fact (Global Constraint 8), a malformed field degrading to `None` with one warning rather than raising (`test_parse_ratings_degrades_a_malformed_entry_to_none_and_warns`, Step 2, implemented Step 4), and a live-verification STOP-and-report gate against a real cached `provider_cache` payload before it ships (Step 6).
- Dead-upstream defects not ported — Global Constraint 9; nothing in any task names `anidb_rating`, `omdb_rating` or `omdb_imdb_rating`.
- Storm pin + the digest-evolution law, both halves — Global Constraint 6: the empty/no-rating-token case is byte-identical (Task 1 Concern D's `test_an_empty_ratings_dict_changes_nothing` and `test_a_definition_naming_no_rating_token_is_byte_identical_regardless_of_ratings`), and a rating VALUE a definition actually uses now folds into `badge_fingerprint` too, guarded the same way `outcomes` is (`test_unchanged_rating_values_do_not_move_the_fingerprint`, `test_a_changed_rating_value_used_by_a_definition_moves_the_fingerprint`) — closing the staleness gap the preflight review flagged.
- Container discipline, no AI attribution, `--no-gpg-sign` — Global Constraints 12–14, present in every task's command blocks.

**Placeholder scan:** every code block in every step is complete and runnable — no `TODO`, no "implement later", no "add error handling", no test asserting only a type with no value. The one place a genuinely unresolvable-at-plan-time value appears (T2 Step 1's `back_color`/`back_radius` for `versions`, since the vendored YAML has not been read yet) is handled the way C1's plan handled the identical situation for `direct_play` — a stated, precedented default with an explicit instruction to verify against the real vendored file and STOP-and-report on a divergence, not a blank left for the implementer to invent.

**Name consistency, checked across every task boundary:** `parse_ratings` (T1 Concern A → B's `MDBListClient.ratings` → E's `apply_badges`), `plex_native_ratings` (T1 Concern C → E), `BadgeInputs.ratings` / `_variable_values` (T1 Concern D → E), `_versions` (T1 Concern G, one function object → Concern H's `OverlayItemView`, imported not re-implemented → T2's `condition={"versions.gt": 1}`), `OVERLAY_ATTRIBUTES` (T1 Concern H → T2 Step 6's family), `FAMILIES["versions"]` (T2 Step 6 → Step 9's fires/silent pin, and T3's roadmap text). One spelling each throughout.

**L-2/L-4 carries, present:** both rewords are Task 1 Concern H, Step 40, with their own new tests (Step 38) and the roadmap's past-tense "met" language (T3 Step 1) rather than the C1-era future-tense "gets met."

**Code-vs-probe contradictions found while researching this plan (report to the reader, not silently resolved):**
1. **The probe never fetched a raw MDBList API response.** Its §2.3.3 table names Kometa's own parsed-object ATTRIBUTE names and their transform, not this service's own client's raw JSON field names. This plan's `value`-vs-`score` sourcing split (documented in Task 1 Step 4's code comment) is this plan's own reasoning from the one real fixture (`tests/fixtures/facts/mdblist_movie.json`, which shows only `imdb` and `tmdb` entries) plus general knowledge of MDBList's API shape — not a transcribed fact. It is internally self-consistent (9 array-sourced + 2 top-level = 11, matching the probe's count) and the existing `imdb`/`tmdb` fixture values are consistent with either reading (`value` and `score/10` coincide for both), so the ambiguity could not be resolved from evidence already in this repository. Task 1 Step 6 now gives this an actual STOP-and-report gate — read one real cached MDBList payload out of `provider_cache` and check it against the field map — rather than leaving it a prose recommendation with no corresponding step; a freshly isolated compose stack with no MDBList history yet is the expected case and defers the check to that same recommendation, still open, rather than resolving it by fiat.
2. **The `versions` family's exact box/backdrop values are inferred from precedent, not the probe.** The probe (§4.8) gives box 105×105 and the non-episode position (15, 335) but says nothing about `back_color`/`back_radius` for this specific family. This plan infers the same "standard" template default (`#00000099`, radius 30) every other C1/C2a family uses — the same inference C1's own plan made for `direct_play.yml`'s `back_radius`, and for the same stated reason. Task 2 Step 1 vendors the real `versions.yml` and instructs a STOP-and-report if it disagrees.
3. **The roadmap's row 100 cell is a single very long table-cell paragraph.** T3 Step 1 replaces a substring inside it rather than rewriting the cell; the exact span must be re-located in the live file at execution time (it may have drifted if another sub-phase lands first), not assumed byte-identical to what this plan quotes.
