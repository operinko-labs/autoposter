# Overlay Engine (row 97, overlay era Phase A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** replace `badges/spec.py`'s nine hardcoded `BadgeSpec` rows with a declarative overlay schema an operator can write, and prove the swap lossless by re-expressing every one of them through the new schema against the existing production-parity pins, unchanged.

**Architecture:** three layers, sequenced. (1) A new `src/autoposter/overlays/` package holds the schema (`OverlayDefinition`, a Pydantic model carrying the attribute set `p-overlay-grammar-probe.md` §1.1 banks) and the `<<variable>>` text grammar (§2), both pure and fixture-pinned — no Pillow, no config, no I/O. (2) The render path generalises: `overlays/render.py` draws one definition onto one layer with the addon/backdrop/text layout driven by attributes instead of by `compose.py`'s per-badge `if name ==` branches, and `badges/spec.py`'s `BADGES` becomes *derived* from nine `OverlayDefinition` instances rather than authored — which is what makes every existing badge pin an unchanged oracle for the swap. (3) The operator path adds `badges.definitions`, the image-source ladder (ours, stated), and the entry-point proof at `render.pipeline.apply_badges`.

**Tech Stack:** Python 3.13, Pydantic v2, Pillow (the badge renderer is Pillow, **not** ImageMagick — see Global Constraint 3), pytest, numpy (already a test dependency of `test_badge_parity.py`).

---

## Branch and cut point

- Branch name: **`feat/overlay-engine`**.
- **Cut from `origin/main` after a fetch, verified with a CONTENT probe, never a sha probe.** Cut from CURRENT `origin/main` — re-verify the tip at execution time; do not trust any sha recorded in this document. `feat/dynamic-packs` is a DIFFERENT, unrelated branch and is **not** the cut point.

```bash
git fetch origin
git cat-file -e origin/main:src/autoposter/badges/spec.py
git cat-file -e origin/main:src/autoposter/config/descriptions.py
git cat-file -e origin/main:tests/test_config_descriptions.py
git cat-file -e origin/main:src/autoposter/actions/flags.py
git cat-file -e origin/main:src/autoposter/api/action_center.py
git show origin/main:src/autoposter/badges/spec.py | grep -q "class BadgeSpec" && echo SPEC-PRESENT
git show origin/main:src/autoposter/badges/spec.py | grep -q '"episode_info"' && echo NINE-BADGES-PRESENT
git show origin/main:src/autoposter/badges/geometry.py | grep -q "def get_cord" && echo GETCORD-PRESENT
git show origin/main:src/autoposter/badges/compose.py | grep -q "ADDON_OFFSET" && echo ADDON-PRESENT
git show origin/main:src/autoposter/net/guard.py | grep -q "async def guarded_download" && echo GUARD-PRESENT
git show origin/main:src/autoposter/render/pipeline.py | grep -q "async def apply_badges" && echo SEAM-PRESENT
git show origin/main:tests/test_badge_parity.py | grep -q "MAX_RESIDUAL" && echo PARITY-PRESENT
git show origin/main:src/autoposter/render/pipeline.py | grep -q "quality_scored_at" && echo POST129-PRESENT
git checkout -b feat/overlay-engine origin/main
git rev-parse HEAD
```

All five `cat-file` probes must succeed and all eight markers must print.
`descriptions.py` + `test_config_descriptions.py` are the settings-clarity guard (row 217) — without them T3's new keys ship undescribed and pass. `actions/flags.py` + `api/action_center.py` are Action Center's own files (PR #128/#129); their presence is what actually discriminates today's `origin/main` from this plan's original, now-stale cut point — a marker inside `collections/engine.py` stays true long after #127 and cannot tell #127 apart from #129, which is why it is replaced by `POST129-PRESENT` (`render/pipeline.py`'s `quality_scored_at`, added by #129). `GUARD-PRESENT` proves `net/guard.py` is available: it is the seam T3's `url:` source uses, and without it that source would need an unguarded downloader written from scratch. `PARITY-PRESENT` proves the oracle this whole phase swings on is at the cut point. At the time of this preflight refresh (2026-09-02) `origin/main` is `6fff43c` (PR #129, Action Center) and all thirteen checks pass — **re-verify at execution time rather than assuming that sha is still current.** If any probe fails, **STOP and report**; do not cut from an older ref and do not stack on `feat/dynamic-packs`. Record the resolved `git rev-parse HEAD` in the Task 1 report.

---

## Global Constraints

Every task's requirements implicitly include this section.

1. **THE PARITY LAW is the spine.** The nine badges are re-expressed through the new schema LOSSLESSLY. The existing pins are the oracle and are **never edited**: `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_compose.py`, `tests/test_badge_parity.py`. If a re-expression cannot pass one of them unchanged, the re-expression is wrong — **not** the pin. Changing an assertion in any of those five files is a phase failure; if one genuinely must change, **STOP and report** instead.
2. **Falsifiability, per badge, every time.** A re-expression that passes proves nothing on its own — the pin might not be reading the attribute at all. Every badge's step group therefore ends with a **transient deliberate mutation** of exactly one attribute, a run that must go RED naming that badge, and a revert. A mutation that leaves the pin GREEN means the pin does not cover that attribute: **STOP and report**, do not paper over it with a new assertion.
3. **The badge path is Pillow, not argv.** `p-overlay-a-facts.md` C1.2 says "byte-identical argv". That is factually wrong about this codebase and the plan does not follow it: `render/compositor.py`'s argv builders composite the *base* artwork (resize/extent/caption/logo) and are pinned by `tests/test_production_parity.py`; badges are composited by `badges/compose.py` through Pillow and never reach `magick`. The parity law is re-homed onto the mechanism that actually exists — see Adjudication A1. **Nothing in this phase touches `render/compositor.py`, `render/textfit.py` or `tests/test_production_parity.py`.**
4. **Byte-identity is gated, not asserted by hand.** T1 records the sha256 of the composed pixel bytes for both oracle bases into `tests/test_overlay_engine_golden.py`. Every subsequent task re-runs that gate. The two hashes are **measured at T1, never predicted** — the plan does not name a value for them.
5. **The probe is cited by SECTION, never by line.** Every comment or docstring referencing `p-overlay-grammar-probe.md` names a section (`§1.1`, `§2.3`, `§3.3`, `§4.1`, `§6`), because line numbers in that document move. Kometa *source* citations inside the probe's own excerpts may be repeated verbatim (`overlay.py:530-552`) since they are pinned to tag `v2.4.8` / `498b3af`.
6. **Never invent an attribute semantics the probe did not bank.** If an attribute's behaviour is needed and §1–§4 do not state it, it is **deferred**, not guessed. The deferral list in "Banked but deliberately deferred" below is closed: adding to it mid-phase requires a STOP-and-report.
7. **The queue is NOT built** (facts C1.5). No `queue`, no `queues:`, no `dynamic_position`, no `overlay_limit`, no `queue_names`. `languages`' 61px stack stays the bespoke loop it is today (T2 Step 12) and is explicitly *not* re-expressed as a queue. T4 files the queue forward on row 97's close.
8. **The gated-feature entry-point law.** `badges.definitions` is proven through the REAL entry point, `render.pipeline.apply_badges`, not through `overlays/render.py` alone. With `definitions: []` (the default) the entry point must produce **byte-identical output to the pre-phase baseline** — asserted against T1's recorded hashes, not against a re-derivation.
9. **Config-field descriptions say WHAT, never WHEN.** `tests/test_config_descriptions.py` enforces a non-empty `Field(description=...)` on every field with no "after the next run"-shaped text. No `dict[str, SomeModel]` field is added; `definitions` is a `list[OverlayDefinition]`, matching `CollectionsConfig.definitions`.
10. **Suites stated-then-measured.** T1 Step 2 MEASURES the current baseline. **≈4692 backend passed is a hypothesis, not yet measured this run** (facts C5); the plan's earlier paired figure, `383`, was that same backend run's SKIP count, not a separate suite — there is a further 446-test frontend suite, but it is untouched by this phase (no file in the File Structure table is under `frontend/`) and this plan never runs it. Every later task states its expected count BEFORE running and reconciles any difference in its report.
11. **Container discipline.** Unique compose project per task (**`pov1` … `pov4`**), always with the `.superpowers/isolated-db.yml` overlay. Tee output to a path under `/app/.superpowers/` inside the container — never rely on streamed stdout (`rtk proxy` filters streamed logs and `--rm` deletes the container). A long run uses **no `--rm`**, is started detached, waited on with a foreground `docker wait`, and read back with `docker cp`. Teardown is `docker compose -p <project> down` — **never** `down -v`.
12. **Never assert wall-clock ordering across sleeps in a container test.** The dev machine's Docker clock steps ~2.7s backwards every ~27s. (No test in this plan sleeps; the constraint stands so a debugging detour does not add one.)
13. **No network in tests.** `tests/conftest.py`'s `no_outbound_network` autouse fixture is not bypassed. T3's `url:` source is exercised through an `httpx.MockTransport`, and a "no request was made" assertion is made against a handler that records every request.
14. **Commits** are conventional, `--no-gpg-sign`, staged **by name** (never `git add -A`), and carry **no AI attribution** of any kind — no `Co-Authored-By`, no tool name, nothing. Same for the PR body.
15. **Read-only outside the named files.** At the end of every task, `git diff --stat <task-start-sha> -- src/ tests/` must list only files named in that task's **Files** block.
16. **RED before GREEN on every behavioural step:** write the failing test, run it, watch it fail for the RIGHT reason, then implement.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `src/autoposter/overlays/__init__.py` | **new** — empty | T1 |
| `src/autoposter/overlays/assets.py` | **new** — the canvas/colour/font/image path constants moved out of `badges/spec.py` so `builtin.py` can read them without a cycle | T1 |
| `src/autoposter/overlays/schema.py` | **new** — `OverlayDefinition`, its literals and validators | T1 |
| `src/autoposter/overlays/variables.py` | **new** — the `<<variable>>` vocabulary, modifier table and substitution | T1 |
| `pyproject.toml` | add `num2words` as a declared dependency (the `W`/`WU`/`WL` modifiers import it) | T1 |
| `src/autoposter/overlays/render.py` | **new** — `draw_overlay`, the generalised per-definition draw | T2 |
| `src/autoposter/overlays/builtin.py` | **new** — the nine re-expressed definitions, `BUILTIN_OVERLAYS` | T2 |
| `src/autoposter/badges/spec.py` | `BADGES` becomes derived from `BUILTIN_OVERLAYS`; constants re-exported from `overlays/assets.py` | T2 |
| `src/autoposter/badges/compose.py` | `IMAGE_BADGES`/`BADGE_ICONS`/the addon `if name ==` branches replaced by `draw_overlay`; operator definitions drawn after the builtins | T2, T3 |
| `src/autoposter/overlays/sources.py` | **new** — the image-source ladder (`file` / `builtin` / `url` / name fallback) | T3 |
| `src/autoposter/config/schema.py` | `BadgesConfig.definitions: list[OverlayDefinition]`; `BadgesConfig.definition_image_max_bytes` | T3 |
| `src/autoposter/render/pipeline.py` | pass `config.badges.definitions` and the http client into `compose_badges` | T3 |
| `tests/test_overlay_schema.py` | **new** — T1's schema pins | T1 |
| `tests/test_overlay_variables.py` | **new** — T1's grammar pins | T1 |
| `tests/test_overlay_engine_golden.py` | **new** — T1's byte-identity gate; re-run by T2–T4 | T1 |
| `tests/test_overlay_builtin.py` | **new** — T2's per-badge re-expression pins | T2 |
| `tests/test_overlay_render.py` | **new** — T2's generalised-draw pins | T2 |
| `tests/test_overlay_sources.py` | **new** — T3's ladder pins | T3 |
| `tests/test_overlay_entrypoint.py` | **new** — T3's entry-point law | T3 |
| `config/autoposter.example.yaml` | document `badges.definitions` and `definition_image_max_bytes` | T3 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | close row 97; file the queue forward | T4 |
| `.superpowers/sdd/progress.md` | the era's first wrap | T4 |

**Never modified by any task:** `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_compose.py`, `tests/test_badge_parity.py`, `src/autoposter/badges/geometry.py`, `src/autoposter/badges/draw.py`, `src/autoposter/badges/values.py`, `src/autoposter/render/compositor.py`, `src/autoposter/render/textfit.py`, `tests/test_production_parity.py`.

---

## Interfaces produced by this phase

```python
# src/autoposter/overlays/schema.py
class OverlayDefinition(BaseModel):
    name: str
    group: str | None
    weight: int | None
    suppress_overlays: list[str]
    horizontal_offset: int | str | None
    horizontal_align: Literal["left", "center", "right"] | None
    vertical_offset: int | str | None
    vertical_align: Literal["top", "center", "bottom"] | None
    scale_width: int | None
    scale_height: int | None
    back_color: str | None
    back_line_color: str | None
    back_line_width: int | None
    back_radius: int
    back_padding: int
    back_align: Literal["left", "right", "center", "top", "bottom"]
    back_width: int
    back_height: int
    file: str | None
    builtin: str | None
    url: str | None
    text: str | None
    font: str | None
    font_size: int
    font_color: str
    stroke_width: int
    stroke_color: str | None
    addon_offset: int
    addon_position: Literal["left", "right", "top", "bottom"]
    @property
    def has_back(self) -> bool: ...
    def rgba(self, field: str) -> tuple[int, int, int, int] | None: ...

# src/autoposter/overlays/variables.py
STRING_VARS: frozenset[str]
INT_VARS: frozenset[str]
FLOAT_VARS: frozenset[str]
DATE_VAR: str
RATING_SOURCES: tuple[str, ...]
VAR_MODS: dict[str, tuple[str, ...]]
SINGLE_MODS: frozenset[str]
DOUBLE_MODS: frozenset[str]
class UnresolvedVariable(Exception): ...
def literal_of(name: str) -> str | None: ...
def tokens_in(literal: str) -> list[tuple[str, str]]: ...
def format_value(var: str, mod: str, value: object) -> str: ...
def render_text(literal: str, values: Mapping[str, object]) -> str: ...

# src/autoposter/overlays/render.py
def draw_overlay(
    layer: Image.Image,
    definition: OverlayDefinition,
    canvas: tuple[int, int],
    *,
    image: Image.Image | None = None,
    text: str | None = None,
    font: ImageFont.FreeTypeFont | None = None,
) -> tuple[int, int, int, int]: ...

# src/autoposter/overlays/builtin.py
BUILTIN_OVERLAYS: dict[str, OverlayDefinition]

# src/autoposter/overlays/sources.py
class OverlaySourceError(Exception): ...
async def resolve_image_path(
    definition: OverlayDefinition, *, overlays_root: Path, http, max_bytes: int
) -> Path | None: ...
```

---

## The image-source ladder — ours, stated honestly

Kometa's precedence (probe §1.2) is `file` → `default`/`pmm`/`PMM/`-prefixed `git` → `git` → `repo` → `url` → name-keyed fallback. Mapped onto our conventions, **which of those exist for us and which do not**:

| Kometa | Ours | Status | Why |
|---|---|---|---|
| `file:` (literal local path) | `file:` resolved under `Config.overlays_root` | **SHIPS** | `overlays_root` is the existing operator mount (`config/schema.py`, `Config.overlays_root`). Resolved under it, not literally, so a definition cannot name `/etc/passwd`. |
| `default:` / `pmm:` (Kometa's bundled `defaults/overlays/images/` tree) | `builtin:` resolved under `assets/badges/images/` | **SHIPS, RENAMED** | Per probe §6's correction this tree is Kometa's own, **not** the `Default-Images` repo. Our `assets/badges/images/` already vendors the subset the nine badges use (`audio_codec/`, `flag/`, `rating/`, `resolution/`, `Commonsense.png`). Named `builtin:` because `default:` reads as "the fallback" in a config file and `pmm:` names a project this codebase has no relationship with. |
| `git:` (`{configs_url}{git}.png`, the Kometa community Configs repo) | — | **DOES NOT EXIST** | We mirror no Configs repo and have no `GitHub.configs_url`. An operator wanting a community image gives its raw URL through `url:`. |
| `repo:` (`{config.custom_repo}{repo}.png`) | — | **DOES NOT EXIST** | There is no `custom_repo` setting in `Config` and this phase does not add one. Same answer as `git:`: use `url:`. |
| `url:` (direct download, `Content-Type == image/png` enforced) | `url:` through `net/guard.py::guarded_download` | **SHIPS** | `guarded_download` is this codebase's operator-typed-URL downloader: SSRF target validation on the first request *and every redirect hop*, a streamed byte cap, and a `content_types` allowlist that carries Kometa's own `image/png` check exactly. See Adjudication A6 — this is **not** `providers/fetch.py`, which cannot carry bytes. |
| name-keyed fallback (`library.overlay_folder/<name>.png`) | `overlays_root/<name>.png` | **SHIPS** | Same shape, our mount. |
| `get_and_save_image`'s TTL re-use against `cache.expiration` | a content-addressed disk cache at `overlays_root/.cache/<sha256(url)>.png` | **SHIPS, SIMPLIFIED** | Content-addressed rather than TTL'd: the key IS the URL, so a changed URL is a new file and an unchanged one is never re-fetched. No expiry sweep is added — see Adjudication A7. |

---

## Banked but deliberately deferred

Each is in the probe and each is **not** built here, with the reason. Closed list (Global Constraint 6).

| Banked | Probe | Deferred because |
|---|---|---|
| `queue` / `queues:` / `dynamic_position` / `overlay_limit` / `queue_names` | §4.2, §4.3, §5.4 | Facts C1.5: no shipped Kometa default uses it (the probe's own §5.4 honest gap — the only worked example is from docs). Filed forward on row 97 by T4. |
| `font_style` (named variation on a variable font) | §1.1 | Our two vendored faces (`Inter-Bold.ttf`, `Inter-Medium.ttf`) are static; `get_variation_names()` returns nothing to validate against. Adding the field without the validation would be an attribute that silently does nothing. |
| `blur(NN)` special name, incl. its degrade-to-`blur(50)` behaviour | §1.1 | No blur exists anywhere in our render path; building one is a new visual capability, not an engine generalisation. |
| `backdrop` special name and its full-canvas `-1` semantics | §1.1, §3.5 | None of the nine is a full-canvas backdrop. The `-1` sentinel itself **does** ship (T1 Step 6) with the "size to content" arm only; the "size to canvas" arm is the `backdrop`-name branch and is refused, not silently taken. |
| `git:` / `repo:` sources | §1.2 #3, #4 | See the ladder table. |
| `old_special_text` alias table (`audience_rating0`, `critic_rating%`, …) | §1.1 | Legacy Kometa bare names for backwards compatibility with pre-`<<variable>>` configs. We have no legacy configs; nothing would ever exercise it. |
| The 27 `rating_sources` external-API **fetches** | §2.2, §2.4, §7.2 | The 27 NAMES ship in `variables.py`'s vocabulary (they are part of the grammar); the per-source API plumbing is row 100's data half, which the probe's own §7.2 leaves unprobed. A definition naming one resolves only if the value is already in the item's value map, and is skipped-with-a-warning otherwise (probe §2.4's own `unresolved` behaviour). |
| `external_templates` / `templates` / `variables` / conditional substitution | §5.5, §7.3 | The config-load-time `<<key>>` pass, explicitly a separate and larger surface the probe did not bank (§7.3). Our schema is written directly; there is no template indirection to resolve. |
| `suppress_overlays`' Kometa-side config wiring | §7.4 | The *field* and its *resolution semantics* ship (§4.1 banks those fully); only Kometa's own `CollectionBuilder`/`OverlayFile` plumbing is unbanked, and we write our own. See Adjudication A5. |

---

## Adjudications this plan makes beyond the facts

Each is a design choice the facts did not settle, or settled against the code. Flagged so a reviewer can reject one without rejecting the task.

- **A1 (the parity law is re-homed from argv onto pixels).** Facts C1.2 says the re-expression "must render BYTE-IDENTICAL argv for every pinned case". There is no badge argv. `render/compositor.py`'s four builders and their pins in `tests/test_production_parity.py` cover the *base* composite — resize, extent, caption, logo — and `badges/compose.py` never calls any of them; it composites nine RGBA layers with Pillow and encodes WebP. The law's *content* (an engine swap with the strongest possible proof, falsifiable by a one-attribute mutation) is preserved and arguably strengthened: the gate is **byte-identity of the composed pixels** (T1's recorded hashes), which is a finer instrument than argv equality because it also catches a rendering change argv could never express. *Rejectable alternative:* re-scope the phase to generalise `compositor.py` instead. Not taken — row 97's text is "generalising what `badges/` hardcodes", and `badges/` is Pillow.
- **A2 (nine badges, not eight).** `BADGES` has **nine** keys. The facts, and `docs/research/kometa-overlays.md` before them, say eight. The discrepancy is real and mechanical: `compose()` iterates eight badges and `continue`s past `languages`, which `_draw_languages` handles separately with its own 61px stack. The plan re-expresses all nine and treats `languages` as the ninth, last, and most special — T2 Steps 11–12 — rather than quietly leaving one badge hardcoded. T4 corrects the count on row 97's close.
- **A3 (`BADGES` is derived, not deleted).** `badges/spec.py` keeps exporting `BadgeSpec` and `BADGES`, now built from `BUILTIN_OVERLAYS`. This is what lets all five existing pin files run **unchanged**, which is the entire proof. It is one adapter, in one place, and T4 files its eventual removal (once nothing reads `BadgeSpec`) as a follow-up rather than doing it here.
- **A4 (constants move to `overlays/assets.py`, re-exported).** `overlays/builtin.py` needs `IMAGES`, `INTER_BOLD`, `INTER_MEDIUM`; `badges/spec.py` needs `BUILTIN_OVERLAYS`. Importing both ways is a cycle. The constants move down into `overlays/assets.py` and `badges/spec.py` re-exports them, so `from autoposter.badges.spec import INTER_MEDIUM` (which `tests/test_badge_draw.py` does) keeps working byte-for-byte.
- **A5 (`suppress_overlays` ships).** Facts C1 does not list it; row 97's own roadmap text does ("general cross-overlay suppression"), and probe §4.1 banks its resolution completely — suppression is resolved *before* groups, so a suppressed overlay never reaches weight arbitration. It is a pure list operation over already-resolved names, costs one function, and is exactly the kind of thing that is far cheaper to build with the group resolver than bolted on later.
- **A6 (the `url:` source uses `net/guard.py`, not `providers/fetch.py`).** Facts C1.4 says "a `url:` source through the provider-cache/fetch core". That core cannot carry this: `providers/fetch.py::fetch_json` decodes to a dict and `providers/cache.py` persists it into a Postgres JSON column — a PNG has no representation there. `net/guard.py::guarded_download` is the seam that already exists for exactly this shape (an operator-typed URL fetched to a file) and it carries the SSRF guard, which a definition-supplied URL absolutely needs. The Content-Type check probe §1.2 banks maps onto its `content_types` parameter verbatim.
- **A7 (the URL cache is content-addressed, not TTL'd).** Kometa re-uses a downloaded overlay image until `cache.expiration` (§1.2). We key the cache file by `sha256(url)` instead, so the same URL is fetched once ever and a changed URL is a different file. Consequence, stated: an operator who replaces the image *behind* a stable URL must clear `overlays_root/.cache/`. That is the right trade for artwork stamps, which are versioned by name in practice, and it removes an expiry sweep this phase would otherwise have to schedule and test.
- **A8 (`back_radius` defaults to Kometa's 0, not our 30).** `BadgeSpec.radius` defaults to 30, but that 30 comes from `templates.yml`'s `back_radius: 30` (§5.5) — a *Kometa defaults-file* choice, not the attribute's default, which is 0 (§1.1). The schema default is 0 and all nine builtin definitions state their radius explicitly. This is exactly the kind of default that would otherwise silently apply to an operator's first definition.
- **A9 (`has_back` is derived, per Kometa).** `BadgeSpec.has_back` is an authored boolean; §1.1 (`overlay.py:214`) makes it `bool(back_color or back_line_color)`. The schema follows Kometa: `languages` is re-expressed with `back_color=None` and `has_back` falls out. This makes T2 Step 12's mutation — set `back_color` — a genuine falsification rather than a tautology.
- **A10 (`definitions` lives on `BadgesConfig`, not a new top-level key).** These are badges; they belong under the gate (`badges.enabled`) and the posture (`badges.upload_to_plex`) that already govern badge rendering. `list[OverlayDefinition]` mirrors `CollectionsConfig.definitions` and satisfies Global Constraint 9's no-`dict[str, Model]` rule.
- **A11 (an operator text overlay resolves against values we already have).** `<<variable>>` resolution takes a `Mapping[str, object]` the caller supplies. `compose.py` supplies the subset `BadgeInputs`/`MediaInfo` already carries. A definition naming anything else raises `UnresolvedVariable`, which `compose.py` catches into a per-item skip and a warning — probe §2.4's own behaviour (`unresolved`, a per-item overlay skip, not a run abort). No new data source is fetched anywhere in this phase.

---

## Task 1: The schema and the variable grammar

Pure Python. No Pillow, no config, no I/O. Everything here is fixture-pinned against the probe's cited sections.

**Files:**
- Create: `src/autoposter/overlays/__init__.py`
- Create: `src/autoposter/overlays/assets.py`
- Create: `src/autoposter/overlays/schema.py`
- Create: `src/autoposter/overlays/variables.py`
- Modify: `pyproject.toml`
- Create: `tests/test_overlay_schema.py`
- Create: `tests/test_overlay_variables.py`
- Create: `tests/test_overlay_engine_golden.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `OverlayDefinition` (every field above), `has_back`, `rgba()`; `render_text`, `tokens_in`, `format_value`, `literal_of`, `UnresolvedVariable`, `VAR_MODS`, `SINGLE_MODS`, `DOUBLE_MODS`, `RATING_SOURCES`, `STRING_VARS`, `INT_VARS`, `FLOAT_VARS`, `DATE_VAR`; the two recorded hash constants `POSTER_PIXELS_SHA` / `TITLE_CARD_PIXELS_SHA` in `tests/test_overlay_engine_golden.py`.

---

- [ ] **Step 1: Cut the branch**

Run the whole "Branch and cut point" block above. Paste the three `cat-file` results, the eight markers and `git rev-parse HEAD` into the task report. If any probe fails, STOP.

Then commit this plan, which is currently uncommitted:

```bash
git add docs/superpowers/plans/2026-09-03-overlay-engine.md
git commit --no-gpg-sign -m "docs(plans): the overlay engine plan, row 97 Phase A"
```

- [ ] **Step 2: MEASURE the baseline**

Do not assume the number.

```bash
docker compose -p pov1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov1-base test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-pov-t1-baseline.log'
docker wait pov1-base
docker cp pov1-base:/app/.superpowers/run-pov-t1-baseline.log .superpowers/run-pov-t1-baseline.log
docker rm pov1-base
tail -5 .superpowers/run-pov-t1-baseline.log
```

Expected: a green summary line. **≈4692 passed is a hypothesis, not the fact** (facts C5) — record the measured numbers verbatim in the task report as `BASELINE=<passed>/<skipped>`. The skip count is unmeasured; do not assume `383` — that figure was a skip count from a different, older run. This is the backend suite only; the separate 446-test frontend suite is out of scope for this phase. Every later task states its expected count against this and reconciles any gap.

- [ ] **Step 3: Write the byte-identity gate with a deliberately wrong hash**

This is the phase's central instrument, so it is built RED-first like anything else: the file is written with a placeholder hash that CANNOT be right, run so it fails, and only then filled with the measured value.

Create `tests/test_overlay_engine_golden.py`:

```python
"""The byte-identity gate for the row-97 engine swap.

`badges/compose.py` renders with Pillow, not ImageMagick, so this phase's
parity oracle is the composed image itself rather than an argv list (see the
plan's adjudication A1). Both hashes below were MEASURED on the pre-swap
code at Task 1 and are never recomputed by a later task: re-deriving the
expected value from the code under test is how a byte-identity gate becomes
a tautology.

The hash is taken over the DECODED RGB pixels, not over the encoded WebP
bytes, so a libwebp container-level difference (metadata ordering, say)
cannot fail a run that renders identical pixels.

If this fails after a Pillow or libwebp bump rather than after a code
change, STOP and report -- do not re-record the constants. A font
rasterisation change is a real rendering change and the operator has to
know about it.
"""
import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image

from autoposter.badges.compose import BadgeInputs, compose
from autoposter.badges.values import MediaInfo

ORACLE = Path("tests/fixtures/oracle")

# The same two input sets tests/test_badge_parity.py uses, repeated here
# rather than imported: this file must keep measuring what it measured at
# Task 1 even if that file's fixtures are ever re-tuned.
ALL_SOULS = BadgeInputs(
    media=MediaInfo("1080", "eac3", 6, 4845912, ("en",), frozenset(), None, None),
    critic_rating=4.9, audience_rating=6.3, content_rating="17", video_format="WEB",
)
EPISODE = BadgeInputs(
    media=MediaInfo("480", "aac", 2, 1380000, ("en",), frozenset(), 1, 1),
    critic_rating=None, audience_rating=10.0, content_rating=None, video_format="SDTV",
)

# MEASURED at Task 1 Step 4 on the pre-swap code. Do not recompute.
POSTER_PIXELS_SHA = "0" * 64
TITLE_CARD_PIXELS_SHA = "0" * 64


def pixels_sha(base: Path, art_kind: str, inputs: BadgeInputs) -> str:
    data = compose(base, art_kind, inputs)
    array = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.uint8)
    return hashlib.sha256(array.tobytes()).hexdigest()


def test_the_poster_composite_is_byte_identical_to_the_pre_swap_baseline():
    assert pixels_sha(
        ORACLE / "All_Souls_base_no_overlay.jpg", "poster", ALL_SOULS
    ) == POSTER_PIXELS_SHA


def test_the_title_card_composite_is_byte_identical_to_the_pre_swap_baseline():
    assert pixels_sha(
        ORACLE / "8OO10C_S01E01_base_no_overlay.jpg", "title_card", EPISODE
    ) == TITLE_CARD_PIXELS_SHA
```

- [ ] **Step 4: Run the gate, watch it fail, and record the two measured hashes**

```bash
docker compose -p pov1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov1-golden test sh -c 'pytest -q tests/test_overlay_engine_golden.py 2>&1 | tee /app/.superpowers/run-pov-t1-golden-red.log'
docker wait pov1-golden
docker cp pov1-golden:/app/.superpowers/run-pov-t1-golden-red.log .superpowers/run-pov-t1-golden-red.log
docker rm pov1-golden
grep -o "assert '[0-9a-f]\{64\}' == '0\{64\}'" .superpowers/run-pov-t1-golden-red.log
```

Expected: 2 failed, and the grep prints two lines each carrying a real 64-hex-character sha. Those are the measured values. Edit `POSTER_PIXELS_SHA` and `TITLE_CARD_PIXELS_SHA` to the sha from the corresponding test's failure (poster first, title card second — check the test name above each assertion in the log, do not assume grep order).

Re-run the same command into `run-pov-t1-golden-green.log`. Expected: **2 passed**. Paste both hashes verbatim into the task report; they are the number every later task quotes.

- [ ] **Step 5: Commit the gate**

```bash
git add tests/test_overlay_engine_golden.py
git commit --no-gpg-sign -m "test(overlays): record the pre-swap composite byte-identity gate"
```

- [ ] **Step 6: Write the failing schema tests**

Create `tests/test_overlay_schema.py`:

```python
"""The overlay definition schema.

Every assertion cites the section of `.superpowers/sdd/p-overlay-grammar-probe.md`
that banks the behaviour. Sections, never line numbers -- the probe's lines move.
"""
import pytest
from pydantic import ValidationError

from autoposter.overlays.schema import OverlayDefinition


def _d(**over):
    base = {"name": "example"}
    base.update(over)
    return OverlayDefinition(**base)


def test_a_name_is_required_and_cannot_be_blank():
    """Probe section 1.1, identity: Kometa raises OverlayError on a missing
    or blank name."""
    with pytest.raises(ValidationError):
        OverlayDefinition()
    with pytest.raises(ValidationError):
        OverlayDefinition(name="   ")


def test_a_pipe_in_the_name_is_rejected():
    """Probe section 1.1: `|` is a reserved separator elsewhere in Kometa's
    pipeline and a name containing it is always refused."""
    with pytest.raises(ValidationError):
        OverlayDefinition(name="a|b")


def test_group_requires_a_weight():
    """Probe section 1.1, identity: `group` requires `weight`."""
    with pytest.raises(ValidationError):
        _d(group="ribbon")
    assert _d(group="ribbon", weight=190).weight == 190


def test_weight_cannot_be_negative():
    """Probe section 1.1: minimum 0."""
    with pytest.raises(ValidationError):
        _d(group="ribbon", weight=-1)


def test_the_offset_pair_is_all_or_nothing():
    """Probe section 1.1, positioning: giving only one of the two offsets
    raises. Align may still be omitted (probe section 3.2)."""
    with pytest.raises(ValidationError):
        _d(horizontal_offset=15)
    with pytest.raises(ValidationError):
        _d(vertical_offset=15)
    both = _d(horizontal_offset=15, vertical_offset=15)
    assert both.horizontal_align is None and both.vertical_align is None


def test_percentage_offsets_survive_as_strings():
    """Probe section 3.2: the `%` suffix survives into the stored offset and
    is re-parsed at draw time."""
    assert _d(horizontal_offset="10%", vertical_offset="10%").horizontal_offset == "10%"


def test_a_signed_offset_is_only_legal_under_center_alignment():
    """Probe section 3.2: a non-center alignment requires offset >= 0; center
    allows a signed offset, which is the only way an overlay lands left or
    above true centre."""
    assert _d(
        horizontal_offset=30, horizontal_align="right",
        vertical_offset=-105, vertical_align="center",
    ).vertical_offset == -105
    with pytest.raises(ValidationError):
        _d(horizontal_offset=-30, horizontal_align="right",
           vertical_offset=0, vertical_align="center")


def test_a_center_percentage_offset_is_bounded_to_plus_or_minus_fifty():
    """Probe section 3.2: -50% to 50% for the percent form under center."""
    assert _d(horizontal_offset="-50%", horizontal_align="center",
              vertical_offset=0, vertical_align="center").horizontal_offset == "-50%"
    with pytest.raises(ValidationError):
        _d(horizontal_offset="-51%", horizontal_align="center",
           vertical_offset=0, vertical_align="center")


def test_a_non_center_percentage_offset_is_bounded_to_zero_to_hundred():
    """Probe section 3.2."""
    with pytest.raises(ValidationError):
        _d(horizontal_offset="101%", horizontal_align="left",
           vertical_offset=0, vertical_align="top")


def test_back_width_and_height_default_to_the_minus_one_sentinel():
    """Probe section 3.5: -1 means "size to content" for every overlay that
    is not the special `backdrop` name."""
    d = _d()
    assert d.back_width == -1 and d.back_height == -1


def test_back_line_width_defaults_to_one_only_when_a_line_colour_is_set():
    """Probe section 1.1, backdrop."""
    assert _d().back_line_width is None
    assert _d(back_line_color="#FFFFFF").back_line_width == 1
    assert _d(back_line_color="#FFFFFF", back_line_width=4).back_line_width == 4


def test_back_radius_defaults_to_kometas_zero_not_our_thirty():
    """Probe section 1.1 gives the attribute default as 0. The 30 every badge
    uses comes from Kometa's own templates.yml (probe section 5.5), which is a
    defaults-FILE choice, not the attribute's default."""
    assert _d().back_radius == 0


def test_back_align_defaults_to_center():
    """Probe section 1.1, backdrop."""
    assert _d().back_align == "center"


def test_has_back_is_derived_from_the_two_colours():
    """Probe section 1.1: has_back = bool(back_color or back_line_color)."""
    assert _d().has_back is False
    assert _d(back_color="#00000099").has_back is True
    assert _d(back_line_color="#FFFFFF").has_back is True


def test_a_backdrop_without_coordinates_is_refused():
    """Probe section 1.1: a non-backdrop, non-queued overlay with a backdrop
    but no coordinates raises."""
    with pytest.raises(ValidationError):
        _d(back_color="#00000099")


def test_colours_resolve_to_rgba_and_an_invalid_one_is_refused():
    """Probe section 1.1: every colour goes through ImageColor.getcolor(.., 'RGBA')
    and an unparseable value raises."""
    d = _d(back_color="#00000099", horizontal_offset=0, vertical_offset=0)
    assert d.rgba("back_color") == (0, 0, 0, 153)
    assert _d().rgba("back_color") is None
    with pytest.raises(ValidationError):
        _d(back_color="not-a-colour", horizontal_offset=0, vertical_offset=0)


def test_font_defaults_match_kometas_attribute_defaults():
    """Probe section 1.1, text-only: font_size 36, stroke_width 0."""
    d = _d()
    assert d.font_size == 36
    assert d.stroke_width == 0
    assert d.font_color == "#FFFFFF"


def test_addon_defaults_match_kometas():
    """Probe section 1.1, text-only: addon_offset 0, addon_position left."""
    d = _d()
    assert d.addon_offset == 0
    assert d.addon_position == "left"


def test_only_one_image_source_may_be_named():
    """Kometa's ladder (probe section 1.2) silently picks a winner when two are
    set. We refuse instead: an operator who set both meant one of them, and a
    silent precedence is the harder bug."""
    with pytest.raises(ValidationError):
        _d(file="a.png", url="https://example.invalid/a.png")


def test_the_deferred_special_names_are_refused_not_silently_accepted():
    """`blur(NN)` and `backdrop` are banked (probe sections 1.1, 3.5) and
    deliberately NOT built this phase. Accepting the name and ignoring its
    semantics would be worse than refusing it."""
    with pytest.raises(ValidationError):
        _d(name="blur(50)")
    with pytest.raises(ValidationError):
        _d(name="backdrop")


def test_the_deferred_queue_attribute_is_refused_by_name():
    """Facts C1.5: the queue is filed forward, not built. An operator who
    writes `queue:` must be told so, not have it dropped."""
    with pytest.raises(ValidationError):
        OverlayDefinition(name="example", queue="custom_queue_name")
```

- [ ] **Step 7: Run the schema tests to verify they fail**

```bash
docker compose -p pov1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov1-r1 test sh -c 'pytest -q tests/test_overlay_schema.py 2>&1 | tee /app/.superpowers/run-pov-t1-red1.log'
docker wait pov1-r1
docker cp pov1-r1:/app/.superpowers/run-pov-t1-red1.log .superpowers/run-pov-t1-red1.log
docker rm pov1-r1
tail -20 .superpowers/run-pov-t1-red1.log
```

Expected: a collection error — `ModuleNotFoundError: No module named 'autoposter.overlays'`. That is the right reason.

- [ ] **Step 8: Write `overlays/assets.py`**

Create `src/autoposter/overlays/__init__.py` (empty file) and `src/autoposter/overlays/assets.py`:

```python
"""Where the overlay engine's bundled assets and canvas constants live.

These were `badges/spec.py`'s module-level constants. They moved down here
so `overlays/builtin.py` can read them while `badges/spec.py` reads
`overlays/builtin.py`'s definitions -- the two directions would otherwise be
a cycle. `badges/spec.py` re-exports every name, so existing imports of
`INTER_MEDIUM`, `BACK_COLOR`, `POSTER_CANVAS` and friends are unchanged.
"""
from autoposter.assets import asset_path

ASSETS = asset_path("badges")
FONTS = ASSETS / "fonts"
IMAGES = ASSETS / "images"

POSTER_CANVAS = (1000, 1500)
EPISODE_CANVAS = (1920, 1080)

# "#00000099" -- 60% opacity black.
BACK_COLOR = (0, 0, 0, 153)
FONT_COLOR = (255, 255, 255, 255)

INTER_BOLD = FONTS / "Inter-Bold.ttf"
INTER_MEDIUM = FONTS / "Inter-Medium.ttf"
```

- [ ] **Step 9: Write `overlays/schema.py`**

Create `src/autoposter/overlays/schema.py`:

```python
"""The declarative overlay definition -- row 97's engine, replacing
`badges/spec.py`'s hardcoded table.

The attribute set is Kometa's, banked in `.superpowers/sdd/p-overlay-grammar-probe.md`
section 1.1 (independently corroborated by that document's section 5.5, which
is Kometa's own author-list of every key the `overlay:` block accepts). Every
validator below cites the probe SECTION that banks it. Attributes the probe
banks and this phase deliberately does not build are listed in the plan's
"Banked but deliberately deferred" table and are REFUSED here rather than
accepted-and-ignored: an operator who writes `queue:` has to be told it does
nothing, because a silently dropped attribute looks exactly like a working one.
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Probe section 1.1, special overlay-name forms. Both are banked, neither is
# built this phase.
DEFERRED_NAMES = ("backdrop",)
DEFERRED_NAME_PREFIXES = ("blur",)

_COLOR_FIELDS = ("back_color", "back_line_color", "font_color", "stroke_color")


def _as_rgba(value: str) -> tuple[int, int, int, int]:
    """Probe section 1.1: every colour goes through Pillow's own parser.

    Imported here rather than at module scope. T3 Step 8 makes
    `config/schema.py` import `OverlayDefinition` to type
    `BadgesConfig.definitions`, and `actions/flags.py` imports
    `config.schema` on every Action Center queue/summary request --
    `flags.py`'s own module docstring exists precisely to keep heavy imports
    (plexapi, httpx, the badge compositor) off that path. A module-level
    `from PIL import ImageColor` here would undo that: Pillow would become a
    transitive import of every Action Center request. Deferred to call time,
    it is only paid when a colour is actually validated or rendered.
    """
    from PIL import ImageColor

    return ImageColor.getcolor(value, "RGBA")


class OverlayDefinition(BaseModel):
    """One overlay, as an operator writes it."""

    model_config = {"extra": "forbid", "frozen": True}

    name: str = Field(
        description="This overlay's identity, used for grouping, suppression and the name-keyed image fallback.",
    )
    group: str | None = Field(
        default=None,
        description="Overlays sharing a group name compete per item; only the highest weight is drawn.",
    )
    weight: int | None = Field(
        default=None, ge=0,
        description="Rank within this overlay's group; higher wins. Required whenever group is set.",
    )
    suppress_overlays: list[str] = Field(
        default_factory=list,
        description="Names of other overlays dropped from any item this overlay also matches.",
    )

    horizontal_offset: int | str | None = Field(
        default=None,
        description="Horizontal distance from the edge horizontal_align names, in pixels or as a percentage of the canvas width.",
    )
    horizontal_align: Literal["left", "center", "right"] | None = Field(
        default=None,
        description="Which horizontal edge horizontal_offset is measured from; center measures from the centreline.",
    )
    vertical_offset: int | str | None = Field(
        default=None,
        description="Vertical distance from the edge vertical_align names, in pixels or as a percentage of the canvas height.",
    )
    vertical_align: Literal["top", "center", "bottom"] | None = Field(
        default=None,
        description="Which vertical edge vertical_offset is measured from; center measures from the centreline.",
    )

    scale_width: int | None = Field(
        default=None,
        description="Resize this overlay's image to this width in pixels before it is drawn.",
    )
    scale_height: int | None = Field(
        default=None,
        description="Resize this overlay's image to this height in pixels before it is drawn.",
    )

    back_color: str | None = Field(
        default=None,
        description="Fill colour of the backdrop drawn behind this overlay's content, as a CSS colour with optional alpha.",
    )
    back_line_color: str | None = Field(
        default=None,
        description="Outline colour of the backdrop, as a CSS colour with optional alpha.",
    )
    back_line_width: int | None = Field(
        default=None, ge=0,
        description="Outline thickness of the backdrop in pixels; defaults to 1 whenever back_line_color is set.",
    )
    back_radius: int = Field(
        default=0, ge=0,
        description="Corner radius of the backdrop in pixels; 0 draws square corners.",
    )
    back_padding: int = Field(
        default=0, ge=0,
        description="Pixels the backdrop extends beyond its box on all four sides.",
    )
    back_align: Literal["left", "right", "center", "top", "bottom"] = Field(
        default="center",
        description="Where this overlay's content sits inside its backdrop box.",
    )
    back_width: int = Field(
        default=-1,
        description="Backdrop width in pixels; -1 sizes the backdrop to its own content.",
    )
    back_height: int = Field(
        default=-1,
        description="Backdrop height in pixels; -1 sizes the backdrop to its own content.",
    )

    file: str | None = Field(
        default=None,
        description="A path beneath overlays_root naming this overlay's image.",
    )
    builtin: str | None = Field(
        default=None,
        description="A path beneath this service's bundled overlay image tree, without the .png suffix.",
    )
    url: str | None = Field(
        default=None,
        description="An http or https address this overlay's PNG is downloaded from and cached by URL.",
    )

    text: str | None = Field(
        default=None,
        description="The literal this overlay draws, in which <<variable>> tokens are replaced by the item's own values.",
    )
    font: str | None = Field(
        default=None,
        description="A path beneath fonts_root naming the TrueType face this overlay's text is drawn in.",
    )
    font_size: int = Field(
        default=36, gt=0,
        description="Point size this overlay's text is drawn at.",
    )
    font_color: str = Field(
        default="#FFFFFF",
        description="Fill colour of this overlay's text, as a CSS colour with optional alpha.",
    )
    stroke_width: int = Field(
        default=0, ge=0,
        description="Outline thickness in pixels drawn around each glyph of this overlay's text.",
    )
    stroke_color: str | None = Field(
        default=None,
        description="Outline colour drawn around each glyph, as a CSS colour with optional alpha.",
    )
    addon_offset: int = Field(
        default=0, ge=0,
        description="Gap in pixels between this overlay's image and its text when it carries both.",
    )
    addon_position: Literal["left", "right", "top", "bottom"] = Field(
        default="left",
        description="Which side of this overlay's text its image sits on when it carries both.",
    )

    @property
    def has_back(self) -> bool:
        """Probe section 1.1: `has_back = bool(back_color or back_line_color)`.

        Derived rather than authored, so an overlay cannot claim a backdrop it
        gave no colour for -- or lose one it did.
        """
        return bool(self.back_color or self.back_line_color)

    def rgba(self, field: str) -> tuple[int, int, int, int] | None:
        """One colour field as RGBA, or None when it was not set."""
        value = getattr(self, field)
        return _as_rgba(value) if value else None

    @model_validator(mode="after")
    def _validate(self) -> "OverlayDefinition":
        name = self.name.strip()
        if not name:
            raise ValueError("an overlay must have a non-blank 'name'")
        if "|" in name:
            raise ValueError("'|' is a reserved separator and cannot appear in an overlay name")
        # Probe section 1.1, special name forms. Deferred, so refused loudly.
        if name in DEFERRED_NAMES or name.startswith(DEFERRED_NAME_PREFIXES):
            raise ValueError(
                f"the {name!r} overlay form is not supported by this service; "
                "see roadmap row 97"
            )

        if self.group and self.weight is None:
            raise ValueError("an overlay with a 'group' must also have a 'weight'")

        # Probe section 1.1, positioning: the offset pair is all-or-nothing.
        offsets = (self.horizontal_offset, self.vertical_offset)
        if (offsets[0] is None) != (offsets[1] is None):
            raise ValueError(
                "'horizontal_offset' and 'vertical_offset' must be given together"
            )

        for axis, offset, align in (
            ("horizontal", self.horizontal_offset, self.horizontal_align),
            ("vertical", self.vertical_offset, self.vertical_align),
        ):
            _validate_offset(axis, offset, align)

        for field in _COLOR_FIELDS:
            value = getattr(self, field)
            if value:
                try:
                    _as_rgba(value)
                except ValueError as exc:
                    raise ValueError(f"{field} {value!r} is not a valid colour") from exc

        # Probe section 1.1: a backdrop with no coordinates is refused.
        if self.has_back and self.horizontal_offset is None:
            raise ValueError(
                "an overlay with a backdrop must also have coordinates"
            )

        # Probe section 1.1: back_line_width defaults to 1 when a line colour
        # is set and no width was given.
        if self.back_line_color and self.back_line_width is None:
            object.__setattr__(self, "back_line_width", 1)

        named = [f for f in ("file", "builtin", "url") if getattr(self, f)]
        if len(named) > 1:
            raise ValueError(
                "name at most one image source: " + ", ".join(named) + " were all given"
            )
        return self


def _validate_offset(axis: str, offset: int | str | None, align: str | None) -> None:
    """Probe section 3.2's range rules.

    A non-center alignment requires a non-negative offset; center allows a
    signed one, bounded to -50%..50% for the percentage form. There is no
    stated pixel bound under center, and none is invented here.
    """
    if offset is None:
        return
    if isinstance(offset, str):
        if not offset.endswith("%"):
            raise ValueError(f"{axis}_offset {offset!r} is neither an integer nor a percentage")
        try:
            percent = int(offset[:-1])
        except ValueError as exc:
            raise ValueError(f"{axis}_offset {offset!r} is not a valid percentage") from exc
        low, high = (-50, 50) if align == "center" else (0, 100)
        if not low <= percent <= high:
            raise ValueError(
                f"{axis}_offset {offset!r} is outside {low}% to {high}% for "
                f"{align or 'left/top'} alignment"
            )
        return
    if align != "center" and offset < 0:
        raise ValueError(
            f"{axis}_offset {offset} must be >= 0 for {align or 'left/top'} alignment"
        )
```

- [ ] **Step 10: Run the schema tests to verify they pass**

```bash
docker compose -p pov1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov1-g1 test sh -c 'pytest -q tests/test_overlay_schema.py 2>&1 | tee /app/.superpowers/run-pov-t1-green1.log'
docker wait pov1-g1
docker cp pov1-g1:/app/.superpowers/run-pov-t1-green1.log .superpowers/run-pov-t1-green1.log
docker rm pov1-g1
tail -3 .superpowers/run-pov-t1-green1.log
```

Expected: **21 passed**.

- [ ] **Step 11: Commit the schema**

```bash
git add src/autoposter/overlays/__init__.py src/autoposter/overlays/assets.py \
        src/autoposter/overlays/schema.py tests/test_overlay_schema.py
git commit --no-gpg-sign -m "feat(overlays): the declarative overlay definition schema"
```

- [ ] **Step 12: Write the failing variable-grammar tests**

Create `tests/test_overlay_variables.py`:

```python
"""The <<variable>> text grammar.

Banked in `.superpowers/sdd/p-overlay-grammar-probe.md` sections 2.1 to 2.4.
Cited by section throughout.
"""
import pytest

from autoposter.overlays.variables import (
    DOUBLE_MODS,
    RATING_SOURCES,
    SINGLE_MODS,
    VAR_MODS,
    UnresolvedVariable,
    format_value,
    literal_of,
    render_text,
    tokens_in,
)


def test_the_text_form_is_unwrapped_and_anything_else_is_not_text():
    """Probe section 2.1: a text overlay's name is `text(LITERAL)`."""
    assert literal_of("text(Runtime: <<runtimeH>>h)") == "Runtime: <<runtimeH>>h"
    assert literal_of("resolution") is None


def test_a_bare_token_is_found_with_an_empty_modifier():
    """Probe section 2.1."""
    assert tokens_in("<<title>>") == [("title", "")]


def test_a_two_character_modifier_wins_over_a_one_character_one():
    """Probe section 2.3: double_mods are tried first, which is why W and WU
    do not collide."""
    assert tokens_in("<<season_number>>WU") == [("season_number", "WU")]
    assert tokens_in("<<season_number>>W") == [("season_number", "W")]


def test_a_modifier_illegal_for_its_variable_is_not_a_token():
    """Probe section 2.3: the modifier table is per variable class, not global.
    `U` uppercases a string; it is not in runtime's set, so `<<runtime>>U` is
    the bare runtime token followed by a literal U."""
    assert tokens_in("<<runtime>>U") == [("runtime", "")]


def test_literal_text_outside_the_tokens_is_left_alone():
    """Probe section 2.1: everything not inside << >> passes through."""
    assert tokens_in("Runtime: <<runtimeH>>h <<runtimeM>>m") == [
        ("runtime", "H"), ("runtime", "M"),
    ]


def test_the_date_bracket_form_is_its_own_token():
    """Probe section 2.3: originally_available takes `[FORMAT]`."""
    assert tokens_in("<<originally_available[%Y]>>") == [
        ("originally_available", "[%Y]"),
    ]


@pytest.mark.parametrize(
    "var,mod,value,expected",
    [
        # Probe section 2.3, runtime row: total minutes / hours part / minutes part.
        ("runtime", "", 80, "80"),
        ("runtime", "H", 80, "1"),
        ("runtime", "M", 80, "20"),
        ("total_runtime", "H", 80, "1"),
        # Probe section 2.3, float row: /10 as given, x10 as int, /10 without a
        # trailing .0, /2 to one decimal.
        ("audience_rating", "", 6.3, "6.3"),
        ("audience_rating", "%", 6.3, "63"),
        ("audience_rating", "#", 8.0, "8"),
        ("audience_rating", "#", 8.6, "8.6"),
        ("audience_rating", "/", 6.4, "3.2"),
        # Probe section 2.3, string row.
        ("title", "", "The Ark", "The Ark"),
        ("title", "U", "The Ark", "THE ARK"),
        ("title", "L", "The Ark", "the ark"),
        ("title", "P", "the ark", "The Ark"),
        # Probe section 2.3, integer row: zero-padded to 2 and to 3.
        ("season_number", "", 1, "1"),
        ("season_number", "0", 1, "01"),
        ("season_number", "00", 1, "001"),
        ("episode_number", "0", 12, "12"),
    ],
)
def test_format_value_matches_the_banked_modifier_table(var, mod, value, expected):
    assert format_value(var, mod, value) == expected


def test_the_words_modifiers_spell_a_number_out():
    """Probe section 2.3, integer row: W via num2words, WU uppercased, WL
    lowercased."""
    assert format_value("season_number", "W", 3) == "three"
    assert format_value("season_number", "WU", 3) == "THREE"
    assert format_value("season_number", "WL", 3) == "three"


def test_the_percent_modifier_truncates_rather_than_rounds():
    """Probe section 2.3 gives `%` as x10-as-int. int() truncates, and
    `badges/values.py`'s own docstring records that production really does
    truncate here."""
    assert format_value("audience_rating", "%", 6.39) == "63"


def test_the_date_format_is_applied_through_strftime():
    """Probe section 2.3, originally_available row."""
    import datetime

    assert format_value(
        "originally_available", "[%Y-%m]", datetime.date(2023, 4, 5)
    ) == "2023-04"
    assert format_value(
        "originally_available", "", datetime.date(2023, 4, 5)
    ) == "2023-04-05"


def test_render_text_substitutes_every_token_and_keeps_the_literal():
    """Probe section 2.4 step 4."""
    assert render_text(
        "Runtime: <<runtimeH>>h <<runtimeM>>m", {"runtime": 80}
    ) == "Runtime: 1h 20m"


def test_render_text_substitutes_the_date_bracket_form():
    """Probe section 2.4 step 4: the bracket form goes through re.sub because
    the format string may contain regex metacharacters."""
    import datetime

    assert render_text(
        "(<<originally_available[%Y]>>)",
        {"originally_available": datetime.date(2023, 4, 5)},
    ) == "(2023)"


def test_a_variable_with_no_value_raises_rather_than_rendering_a_hole():
    """Probe section 2.4: an unresolved variable adds the overlay to the
    `unresolved` set -- the item is skipped, it is not drawn with a gap."""
    with pytest.raises(UnresolvedVariable):
        render_text("<<critic_rating>>", {})


def test_the_rating_vocabulary_is_the_banked_twenty_seven():
    """Probe section 2.2: 27 external rating sources. The NAMES are grammar and
    ship here; the per-source API fetches are row 100's data half and do not."""
    assert len(RATING_SOURCES) == 27
    assert "imdb_rating" in RATING_SOURCES
    assert "trakt_user_rating" in RATING_SOURCES
    assert "anidb_average_rating" in RATING_SOURCES
    # The three Plex-native ratings are float vars but NOT rating sources --
    # they read off the item rather than through an external call.
    for native in ("audience_rating", "critic_rating", "user_rating"):
        assert native not in RATING_SOURCES
        assert native in VAR_MODS


def test_the_modifier_sets_are_the_deduplicated_union():
    """Probe section 2.3: single_mods and double_mods across every variable."""
    assert SINGLE_MODS == {"H", "L", "M", "%", "#", "/", "U", "P", "W", "0", "["}
    assert DOUBLE_MODS == {"WU", "WL", "00"}
```

- [ ] **Step 13: Run the grammar tests to verify they fail**

```bash
docker compose -p pov1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov1-r2 test sh -c 'pytest -q tests/test_overlay_variables.py 2>&1 | tee /app/.superpowers/run-pov-t1-red2.log'
docker wait pov1-r2
docker cp pov1-r2:/app/.superpowers/run-pov-t1-red2.log .superpowers/run-pov-t1-red2.log
docker rm pov1-r2
tail -20 .superpowers/run-pov-t1-red2.log
```

Expected: a collection error — `ModuleNotFoundError: No module named 'autoposter.overlays.variables'`.

- [ ] **Step 14: Write `overlays/variables.py`**

Create `src/autoposter/overlays/variables.py`:

```python
"""Kometa's `<<variable>>` text grammar.

Banked in `.superpowers/sdd/p-overlay-grammar-probe.md` sections 2.1 to 2.4:
the four variable families, the per-family modifier table, and the
substitution order. Transcribed rather than reinvented, including the parts
that look like bugs -- `%` truncates because `int()` truncates, and
`badges/values.py` already records that production output really does.

This module is pure: it formats values it is HANDED. It never reads a Plex
item and never calls a rating API. The 27 external rating-source names are
part of the grammar and appear in the vocabulary; fetching them is row 100's
data half, which the probe's own section 7.2 leaves unprobed.
"""
import re
from collections.abc import Mapping

from num2words import num2words

# Probe section 2.2: 27 names, one per external rating API Kometa integrates.
RATING_SOURCES = (
    "anidb_average_rating", "anidb_rating", "anidb_score",
    "imdb_rating", "letterboxd_rating",
    "mal_rating", "mdb_average_rating", "mdb_letterboxd_rating",
    "mdb_metacritic_rating", "mdb_metacriticuser_rating", "mdb_myanimelist_rating",
    "mdb_rating", "mdb_score", "mdb_tmdb_rating", "mdb_tomatoes_rating",
    "mdb_tomatoesaudience_rating", "mdb_trakt_rating",
    "omdb_metacritic_rating", "omdb_rating", "omdb_tomatoes_rating",
    "serializd_rating",
    "tmdb_rating", "tvdb_rating",
    "trakt_rating", "trakt_user_rating",
    "anidb_temp_rating", "mal_score",
)

# Probe section 2.2. The three Plex-native ratings are float vars but not
# rating sources: they read off the item object rather than through a fetch.
PLEX_NATIVE_RATINGS = ("audience_rating", "critic_rating", "user_rating")
FLOAT_VARS = frozenset(RATING_SOURCES) | frozenset(PLEX_NATIVE_RATINGS)

STRING_VARS = frozenset({
    "title", "content_rating", "original_title", "edition",
    "show_title", "season_title",
})
INT_VARS = frozenset({
    "runtime", "total_runtime", "season_number", "episode_number",
    "episode_count", "versions",
})
DATE_VAR = "originally_available"

# Probe section 2.3, the modifier table -- one row per variable class.
_RUNTIME_MODS = ("", "H", "M")
_COUNT_MODS = ("", "W", "WU", "WL", "0", "00")
_FLOAT_MODS = ("", "%", "#", "/")
_STRING_MODS = ("", "U", "L", "P")

VAR_MODS: dict[str, tuple[str, ...]] = {
    "bitrate": ("", "H", "L"),
    DATE_VAR: ("", "["),
    **{v: _RUNTIME_MODS for v in ("runtime", "total_runtime")},
    **{v: _COUNT_MODS for v in
       ("season_number", "episode_number", "episode_count", "versions")},
    **{v: _FLOAT_MODS for v in sorted(FLOAT_VARS)},
    **{v: _STRING_MODS for v in sorted(STRING_VARS)},
}

# Probe section 2.3: the deduplicated 1- and 2-character sets, used by the
# matcher to decide how many trailing characters are the modifier. Two-char
# modifiers are tried first, which is why W and WU never collide.
SINGLE_MODS = frozenset(m for mods in VAR_MODS.values() for m in mods if len(m) == 1)
DOUBLE_MODS = frozenset(m for mods in VAR_MODS.values() for m in mods if len(m) == 2)

_TEXT_FORM = re.compile(r"^text\((.*)\)$", re.DOTALL)
_DATE_BRACKET = re.compile(r"<<" + DATE_VAR + r"\[(.+?)\]>>")


class UnresolvedVariable(Exception):
    """A token named a variable the caller supplied no value for.

    Probe section 2.4: Kometa adds the overlay to an `unresolved` set and
    skips it for that item with a warning rather than aborting the run.
    Callers are expected to catch this per item, not per pass.
    """


def literal_of(name: str) -> str | None:
    """The literal inside `text(...)`, or None when the name is not a text
    overlay. Probe section 2.1."""
    match = _TEXT_FORM.match(name)
    return match.group(1) if match else None


def tokens_in(literal: str) -> list[tuple[str, str]]:
    """Every `(variable, modifier)` pair the literal carries, in the order the
    tokens appear. Probe sections 2.1 and 2.3.

    Two-character modifiers are checked before one-character ones, and a
    modifier is only recognised when it is legal for that variable -- the
    table is per variable class, not global, so `<<runtime>>U` is the bare
    runtime token followed by a literal `U`.
    """
    found: list[tuple[str, str]] = []
    for match in re.finditer(r"<<([a-z_]+)(\[.+?\])?>>", literal):
        var = match.group(1)
        if var not in VAR_MODS:
            continue
        bracket = match.group(2)
        if bracket is not None:
            if "[" in VAR_MODS[var]:
                found.append((var, bracket))
            continue
        legal = VAR_MODS[var]
        tail = literal[match.end():]
        mod = ""
        if len(tail) >= 2 and tail[:2] in DOUBLE_MODS and tail[:2] in legal:
            mod = tail[:2]
        elif tail[:1] in SINGLE_MODS and tail[:1] in legal:
            mod = tail[:1]
        found.append((var, mod))
    return found


def format_value(var: str, mod: str, value: object) -> str:
    """One resolved value, formatted per its variable class's modifier.

    Probe section 2.3's table, one branch per row.
    """
    if var == DATE_VAR:
        return value.strftime(mod[1:-1]) if mod.startswith("[") else value.isoformat()
    if var in ("runtime", "total_runtime"):
        minutes = int(value)
        if mod == "H":
            return str(minutes // 60)
        if mod == "M":
            return str(minutes % 60)
        return str(minutes)
    if var in FLOAT_VARS:
        number = float(value)
        if mod == "%":
            # Truncation, not rounding -- probe section 2.3 gives x10-as-int,
            # and badges/values.py records that production truncates.
            return str(int(number * 10))
        if mod == "#":
            text = f"{number:g}"
            return text[:-2] if text.endswith(".0") else text
        if mod == "/":
            return f"{number / 2:.1f}"
        return f"{number:g}"
    if var in INT_VARS or var == "bitrate":
        number = int(value)
        if mod == "W":
            return num2words(number)
        if mod == "WU":
            return num2words(number).upper()
        if mod == "WL":
            return num2words(number).lower()
        if mod == "0":
            return "%02d" % number
        if mod == "00":
            return "%03d" % number
        return str(number)
    text = str(value)
    if mod == "U":
        return text.upper()
    if mod == "L":
        return text.lower()
    if mod == "P":
        return text.title()
    return text


def render_text(literal: str, values: Mapping[str, object]) -> str:
    """Substitute every token in `literal` from `values`.

    Probe section 2.4 step 4: the date-bracket form goes through `re.sub`
    (its format string may contain regex metacharacters), every other token
    through a plain `str.replace`.
    """
    full = literal
    for var, mod in tokens_in(literal):
        if var not in values or values[var] is None:
            raise UnresolvedVariable(var)
        rendered = format_value(var, mod, values[var])
        if mod.startswith("["):
            full = _DATE_BRACKET.sub(rendered, full, count=1)
        else:
            full = full.replace(f"<<{var}>>{mod}", rendered)
    return full
```

`num2words` is not yet a dependency of this project (`pyproject.toml` carries `pillow>=11.0` and `numpy>=2.0`, but no `num2words`) — the import above would fail without the next edit. This is not a contingency; it is certain to be needed, since every container run from here on imports `variables.py`.

In `pyproject.toml`, add to `dependencies` (after `"langcodes>=3.5",`):

```toml
    # The W/WU/WL modifiers in the <<variable>> grammar (probe section 2.3)
    # spell an integer out as words. Pure python, no C extension.
    "num2words>=0.5",
```

Rebuild the container image so the new dependency is actually installed before the first test run that imports it:

```bash
docker compose -f docker-compose.yml -f .superpowers/isolated-db.yml build test
```

- [ ] **Step 15: Run the grammar tests to verify they pass**

```bash
docker compose -p pov1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov1-g2 test sh -c 'pytest -q tests/test_overlay_variables.py 2>&1 | tee /app/.superpowers/run-pov-t1-green2.log'
docker wait pov1-g2
docker cp pov1-g2:/app/.superpowers/run-pov-t1-green2.log .superpowers/run-pov-t1-green2.log
docker rm pov1-g2
tail -3 .superpowers/run-pov-t1-green2.log
```

Expected: **all passed** — `num2words` was declared and the image rebuilt above, so the import in `variables.py` resolves.

- [ ] **Step 16: Run the full suite and the byte-identity gate**

```bash
docker compose -p pov1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov1-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-pov-t1-full.log'
docker wait pov1-full
docker cp pov1-full:/app/.superpowers/run-pov-t1-full.log .superpowers/run-pov-t1-full.log
docker rm pov1-full
tail -5 .superpowers/run-pov-t1-full.log
```

Expected: `BASELINE + <the count of new tests>` passed, skipped count matching whatever Step 2 measured. State the expected number in the report BEFORE quoting the result, and reconcile any gap. `tests/test_overlay_engine_golden.py` must be among the passes — nothing in T1 touches the render path, so the two hashes must be unchanged.

- [ ] **Step 17: Lint and commit**

```bash
docker compose -p pov1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test ruff check src tests
git add src/autoposter/overlays/variables.py tests/test_overlay_variables.py pyproject.toml
git commit --no-gpg-sign -m "feat(overlays): the <<variable>> text grammar and modifier table"
docker compose -p pov1 down
```

---

## Task 2: The render path — the generalisation and the parity re-expression

**This is the heart of the phase.** Nine badges, each re-expressed as an `OverlayDefinition`, each proven against its own existing pin unchanged, each falsified by a transient one-attribute mutation.

**Files:**
- Create: `src/autoposter/overlays/render.py`
- Create: `src/autoposter/overlays/builtin.py`
- Create: `tests/test_overlay_render.py`
- Create: `tests/test_overlay_builtin.py`
- Modify: `src/autoposter/badges/spec.py`
- Modify: `src/autoposter/badges/compose.py`

**Interfaces:**
- Consumes: `OverlayDefinition` and every constant/function T1 produced.
- Produces: `draw_overlay(...)`; `BUILTIN_OVERLAYS: dict[str, OverlayDefinition]`; `badges.spec.BADGES` unchanged in shape and value, now derived.

**Before starting:** `docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml up -d db` is not needed — every test in this task is pure Pillow. Use `run --rm test pytest ...` for the short cycles and the detached pattern only for full-suite runs.

**The pin selector used throughout this task.** For a badge `<b>`:

```bash
pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k <b>
```

`test_badge_spec.py::test_specs_produce_the_measured_boxes` is parametrized **by badge name**, so `-k <b>` selects that badge's measured-box row (and, for `runtimes`, both its rows — poster and episode). `test_badge_geometry.py` is parametrized by *value tuples*, so its ids carry no badge names and `-k <b>` selects nothing from it — it is in the command so that a badge whose name happens to appear in a geometry test's function name is caught too, and so the file is collected rather than forgotten. `test_badge_parity.py` is **not** in the selector: it calls `compose()`, which needs all nine badges present, so it is deferred to Step 46 where the whole set runs at once.

Every one of these files is **unedited** throughout (Global Constraint 1).

---

- [ ] **Step 1: Write the failing generalised-draw tests**

Create `tests/test_overlay_render.py`:

```python
"""The generalised per-definition draw.

`badges/compose.py` today decides an overlay's layout with `if name ==`
branches. This module's job is to make that decision from ATTRIBUTES, so an
operator's definition gets the same treatment the nine builtins do.

Assertions are on pixels, following tests/test_badge_draw.py: the failure
that matters is "drawn in the wrong place", which mocking cannot catch.
"""
from PIL import Image, ImageFont

from autoposter.overlays.assets import INTER_MEDIUM, POSTER_CANVAS
from autoposter.overlays.render import draw_overlay
from autoposter.overlays.schema import OverlayDefinition


def _layer():
    return Image.new("RGBA", POSTER_CANVAS, (0, 0, 0, 0))


def test_the_box_matches_the_coordinate_formula_with_padding():
    """Probe sections 3.3 and 1.1: back_padding expands the box on all four
    sides. Same numbers tests/test_badge_geometry.py measured for `critic`."""
    layer = _layer()
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="c", horizontal_offset=30, horizontal_align="right",
            vertical_offset=-105, vertical_align="center",
            back_width=160, back_height=160, back_padding=15,
            back_color="#00000099", back_radius=30,
        ),
        POSTER_CANVAS,
    )
    assert box == (795, 550, 985, 740)


def test_no_backdrop_is_drawn_when_no_colour_was_given():
    """Probe section 1.1: has_back is derived. The box still comes back --
    callers place content against it either way."""
    layer = _layer()
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="l", horizontal_offset=15, horizontal_align="left",
            vertical_offset=223, vertical_align="top",
            back_width=190, back_height=105,
        ),
        POSTER_CANVAS,
    )
    assert box == (15, 223, 205, 328)
    assert layer.getpixel((100, 260))[3] == 0


def test_a_backdrop_is_drawn_when_a_colour_was_given():
    layer = _layer()
    draw_overlay(
        layer,
        OverlayDefinition(
            name="r", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=305, back_height=105,
            back_color="#00000099", back_radius=30,
        ),
        POSTER_CANVAS,
    )
    assert layer.getpixel((160, 67))[3] == 153
    # Rounded, not square.
    assert layer.getpixel((16, 16))[3] == 0
    assert layer.getpixel((500, 500))[3] == 0


def test_an_addon_on_the_left_lays_image_then_gap_then_text():
    """Probe section 1.1: addon_position left, addon_offset the gap. This is
    the layout `commonsense` uses -- icon beside the text."""
    layer = _layer()
    icon = Image.new("RGBA", (60, 60), (255, 0, 0, 255))
    font = ImageFont.truetype(str(INTER_MEDIUM), 55)
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="text(x)", horizontal_offset=15, horizontal_align="left",
            vertical_offset=1125, vertical_align="top",
            back_width=305, back_height=105, back_color="#00000099",
            back_radius=30, addon_offset=15, addon_position="left",
        ),
        POSTER_CANVAS,
        image=icon, text="17+", font=font,
    )
    assert box == (15, 1125, 320, 1230)
    # The icon is left of the box centre, the ink right of it.
    assert layer.crop((15, 1125, 167, 1230)).getbbox() is not None
    assert layer.crop((175, 1125, 320, 1230)).getbbox() is not None


def test_an_addon_on_top_lays_image_above_the_text():
    """Probe section 1.1: addon_position top. This is the layout the two
    rating badges use -- logo above the number."""
    layer = _layer()
    icon = Image.new("RGBA", (60, 60), (0, 255, 0, 255))
    font = ImageFont.truetype(str(INTER_MEDIUM), 55)
    draw_overlay(
        layer,
        OverlayDefinition(
            name="text(y)", horizontal_offset=30, horizontal_align="right",
            vertical_offset=-105, vertical_align="center",
            back_width=160, back_height=160, back_padding=15,
            back_color="#00000099", back_radius=30,
            addon_offset=15, addon_position="top",
        ),
        POSTER_CANVAS,
        image=icon, text="4.9", font=font,
    )
    # Green icon pixels in the upper half of the padded box, none in the lower.
    upper = layer.crop((795, 550, 985, 645))
    assert any(p[1] == 255 and p[3] == 255 for p in upper.getdata())
    lower = layer.crop((795, 700, 985, 740))
    assert not any(p[1] == 255 and p[3] == 255 for p in lower.getdata())


def test_an_image_with_no_text_is_centred_and_never_resized():
    """`badges/draw.py`'s rule, carried forward: two audio-codec images are
    135px against a 105px box and Kometa lets them overflow."""
    layer = _layer()
    tall = Image.new("RGBA", (200, 135), (0, 0, 255, 255))
    draw_overlay(
        layer,
        OverlayDefinition(
            name="a", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=305, back_height=105,
        ),
        POSTER_CANVAS,
        image=tall,
    )
    assert layer.getpixel((167, 10))[3] == 255
    assert layer.getpixel((167, 124))[3] == 255


def test_scale_width_and_height_resize_the_image_before_it_is_drawn():
    """Probe section 1.1, scale. None of the nine builtins uses it; an
    operator definition can."""
    layer = _layer()
    big = Image.new("RGBA", (400, 400), (255, 255, 0, 255))
    draw_overlay(
        layer,
        OverlayDefinition(
            name="s", horizontal_offset=15, horizontal_align="left",
            vertical_offset=15, vertical_align="top",
            back_width=305, back_height=105,
            scale_width=40, scale_height=40,
        ),
        POSTER_CANVAS,
        image=big,
    )
    # 40x40 centred on (15,15,320,120): x 147..187, y 47..87.
    assert layer.getpixel((167, 67))[3] == 255
    assert layer.getpixel((120, 67))[3] == 0
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_render.py 2>&1 | tail -20
```

Expected: `ModuleNotFoundError: No module named 'autoposter.overlays.render'`.

- [ ] **Step 3: Write `overlays/render.py`**

Create `src/autoposter/overlays/render.py`:

```python
"""Draw one overlay definition onto one full-canvas RGBA layer.

This is `badges/compose.py`'s per-badge layout logic with the `if name ==`
branches replaced by attribute reads -- which is the whole point of row 97:
an operator's definition takes the same path the nine builtins do.

The Pillow primitives stay in `badges/draw.py` and the coordinate maths stays
in `badges/geometry.py`; both are pinned by tests this phase does not touch
and are on the never-modify list (Global Constraint 15's file table), so
this module IMPORTS `paste_centered` and `draw_text_centered` rather than
re-implementing them -- a second copy of either is exactly what would drift.
The one place this module adds real logic beyond those two primitives is the
stroke branch of `_draw_text_centered` below: `draw_text_centered` has no
stroke parameters, none of the nine builtins sets a stroke, and `draw.py`
cannot be extended to add them without violating the never-modify list.
"""
from PIL import Image, ImageDraw, ImageFont

from autoposter.badges.draw import draw_text_centered, paste_centered
from autoposter.badges.geometry import backdrop_box
from autoposter.overlays.schema import OverlayDefinition


def _content_size(
    layer: Image.Image,
    definition: OverlayDefinition,
    image: Image.Image | None,
    text: str | None,
    font: ImageFont.FreeTypeFont | None,
) -> tuple[int, int]:
    """The overlay's own box, for the `-1` sentinel and for `get_cord`.

    Probe section 3.5: an un-set back_width/back_height shrinks to fit the
    overlay's own content. (The other arm of that sentinel -- stretching to
    the canvas -- belongs to the `backdrop` name, which the schema refuses.)
    """
    width = height = 0
    if image is not None:
        width, height = image.size
    if text is not None and font is not None:
        left, top, right, bottom = ImageDraw.Draw(layer).textbbox(
            (0, 0), text, font=font, anchor="lt"
        )
        text_width, text_height = right - left, bottom - top
        if image is None:
            width, height = text_width, text_height
        elif definition.addon_position in ("left", "right"):
            width += definition.addon_offset + text_width
            height = max(height, text_height)
        else:
            height += definition.addon_offset + text_height
            width = max(width, text_width)
    return width, height


def _scaled(definition: OverlayDefinition, image: Image.Image) -> Image.Image:
    """Probe section 1.1: scale_width/scale_height resize before the draw.

    Neither set means the image is used at its native size and may overflow
    its box -- `badges/draw.py`'s documented rule, and what two of the
    audio-codec images actually do.
    """
    if definition.scale_width is None and definition.scale_height is None:
        return image
    width = definition.scale_width or image.width
    height = definition.scale_height or image.height
    return image.resize((width, height), Image.Resampling.LANCZOS)


def draw_overlay(
    layer: Image.Image,
    definition: OverlayDefinition,
    canvas: tuple[int, int],
    *,
    image: Image.Image | None = None,
    text: str | None = None,
    font: ImageFont.FreeTypeFont | None = None,
) -> tuple[int, int, int, int]:
    """Draw one overlay and return its backdrop box as `(x0, y0, x1, y1)`.

    The box comes back whether or not a backdrop was drawn, because a caller
    that composites more onto the same layer needs it either way -- the same
    contract `badges/draw.py::draw_backdrop` already has.
    """
    if image is not None:
        image = _scaled(definition, image)

    content = _content_size(layer, definition, image, text, font)
    box_width = definition.back_width if definition.back_width != -1 else content[0]
    box_height = definition.back_height if definition.back_height != -1 else content[1]

    box = backdrop_box(
        canvas, (box_width, box_height),
        definition.horizontal_align, definition.horizontal_offset or 0,
        definition.vertical_align, definition.vertical_offset or 0,
        definition.back_padding,
    )

    if definition.has_back:
        ImageDraw.Draw(layer).rounded_rectangle(
            box,
            radius=definition.back_radius,
            fill=definition.rgba("back_color"),
            outline=definition.rgba("back_line_color"),
            width=definition.back_line_width or 1,
        )

    if image is not None and text is not None and font is not None:
        _draw_addon_group(layer, definition, box, image, text, font, content)
    elif image is not None:
        paste_centered(layer, image, box)
    elif text is not None and font is not None:
        _draw_text_centered(layer, definition, text, font, box)
    return box


def _draw_addon_group(
    layer: Image.Image,
    definition: OverlayDefinition,
    box: tuple[int, int, int, int],
    image: Image.Image,
    text: str,
    font: ImageFont.FreeTypeFont,
    content: tuple[int, int],
) -> None:
    """Lay the image and the text out as one group and centre the group.

    Probe section 1.1: `addon_position` picks the side, `addon_offset` the
    gap. Kometa centres the GROUP in the backdrop box rather than centring
    each piece, which is why the text's own size is measured first.
    """
    if definition.addon_position in ("left", "right"):
        start = box[0] + (box[2] - box[0] - content[0]) // 2
        if definition.addon_position == "left":
            image_box = (start, box[1], start + image.width, box[3])
            text_box = (image_box[2] + definition.addon_offset, box[1],
                        start + content[0], box[3])
        else:
            text_box = (start, box[1], start + content[0] - image.width
                        - definition.addon_offset, box[3])
            image_box = (text_box[2] + definition.addon_offset, box[1],
                         start + content[0], box[3])
    else:
        start = box[1] + (box[3] - box[1] - content[1]) // 2
        if definition.addon_position == "top":
            image_box = (box[0], start, box[2], start + image.height)
            text_box = (box[0], image_box[3] + definition.addon_offset, box[2],
                        start + content[1])
        else:
            text_box = (box[0], start, box[2], start + content[1] - image.height
                        - definition.addon_offset)
            image_box = (box[0], text_box[3] + definition.addon_offset, box[2],
                         start + content[1])
    paste_centered(layer, image, image_box)
    _draw_text_centered(layer, definition, text, font, text_box)


def _draw_text_centered(
    layer: Image.Image,
    definition: OverlayDefinition,
    text: str,
    font: ImageFont.FreeTypeFont,
    box: tuple[int, int, int, int],
) -> None:
    """Centre text on a box, delegating to `badges/draw.py::draw_text_centered`
    for placement and fill.

    That primitive already does the `lt`-anchored bbox math and accepts a
    `color`, so the common case -- no stroke, which is every one of the nine
    builtins -- is a direct call and reuses it byte-for-byte. `draw.py` has no
    stroke parameters and is never modified by this phase, so the stroke case
    is the one genuine extension: it reproduces the same bbox math because
    there is no way to bolt a stroke onto an already-drawn call. This branch
    is unreachable from any of the nine builtins, none of which sets a
    stroke; it exists for an operator-authored definition that does.
    """
    color = definition.rgba("font_color")
    if definition.stroke_width == 0 and definition.stroke_color is None:
        draw_text_centered(layer, text, font, box, color=color)
        return
    drawing = ImageDraw.Draw(layer)
    left, top, right, bottom = drawing.textbbox((0, 0), text, font=font, anchor="lt")
    x = (box[0] + box[2]) // 2 - (right - left) // 2 - left
    y = (box[1] + box[3]) // 2 - (bottom - top) // 2 - top
    drawing.text(
        (x, y), text, font=font, fill=color, anchor="lt",
        stroke_width=definition.stroke_width,
        stroke_fill=definition.rgba("stroke_color"),
    )
```

- [ ] **Step 4: Run the generalised-draw tests to verify they pass**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_render.py 2>&1 | tail -5
```

Expected: **7 passed**.

- [ ] **Step 5: Commit the renderer**

```bash
git add src/autoposter/overlays/render.py tests/test_overlay_render.py
git commit --no-gpg-sign -m "feat(overlays): draw one definition, layout driven by attributes"
```

- [ ] **Step 6: Create the builtin module and the per-badge test file, empty of badges**

Create `src/autoposter/overlays/builtin.py`:

```python
"""The nine badges `badges/spec.py` used to hardcode, re-expressed as overlay
definitions.

Every number here came from `badges/spec.py` unchanged; the CHANGE is that
each is now an attribute of the general schema rather than a field of a
bespoke dataclass. `badges/spec.py`'s `BADGES` is derived from this dict, so
the five existing badge pin files are the oracle for the swap and none of
them is edited (see the plan's parity law).

Values come from Kometa v2.4.8's overlay defaults, cross-checked against
measured pixels from two production oracle images -- see
docs/research/kometa-overlays.md. Attribute semantics are banked in
`.superpowers/sdd/p-overlay-grammar-probe.md`, cited by section.
"""
from autoposter.overlays.schema import OverlayDefinition

BUILTIN_OVERLAYS: dict[str, OverlayDefinition] = {}
```

Create `tests/test_overlay_builtin.py`:

```python
"""The nine builtin overlays, re-expressed.

The parity proof lives in the EXISTING badge pins, which this phase never
edits. These assertions cover what those pins cannot see: that the definition
carries the attribute in the schema's own vocabulary rather than in a
transcription that happens to produce the same box.
"""
from autoposter.overlays.builtin import BUILTIN_OVERLAYS


def test_every_badge_is_defined_as_an_overlay():
    assert set(BUILTIN_OVERLAYS) == {
        "resolution", "audio_codec", "critic", "audience", "commonsense",
        "video_format", "runtimes", "episode_info", "languages",
    }


def test_the_image_only_badges_keep_badgespecs_default_font_size():
    """A3 claims `BADGES` (derived from these definitions -- T2 Step 7) is
    unchanged in shape AND value. `resolution` and `audio_codec` name no
    font_size (they draw no text), so without stating it explicitly here the
    schema's own default (36) would silently replace `BadgeSpec.font_size`'s
    default (55) once `_as_spec` forwards it -- a divergence no existing pin
    reads, since `test_badge_spec.py` and `test_badge_parity.py` never
    assert `font`/`font_size`."""
    assert BUILTIN_OVERLAYS["resolution"].font_size == 55
    assert BUILTIN_OVERLAYS["audio_codec"].font_size == 55
```

Run it — it must fail, both badges absent:

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_builtin.py 2>&1 | tail -10
```

Expected: **2 failed** — the empty-set mismatch and a `KeyError: 'resolution'` from the font_size pin, both correct with `BUILTIN_OVERLAYS` still empty. Neither test is run again until Step 46's gate, once all nine badges exist.

- [ ] **Step 7: Derive `BADGES` from `BUILTIN_OVERLAYS`**

Replace the whole body of `src/autoposter/badges/spec.py` with:

```python
"""Badge definitions.

The nine badges are now `OverlayDefinition` rows in
`autoposter.overlays.builtin` (roadmap row 97) rather than hardcoded
`BadgeSpec` rows here. This module keeps `BadgeSpec` and `BADGES` and DERIVES
the latter from those definitions, so every existing badge pin -- the measured
boxes, the coordinate formula, the Pillow primitives, the composition and the
production-residual comparison -- reads exactly what it read before the swap
and is the oracle proving the swap lossless.

Every parity-critical constant now lives in `autoposter.overlays.assets` and
is re-exported below so existing imports of `INTER_MEDIUM`, `BACK_COLOR`,
`POSTER_CANVAS` and friends are unchanged.
"""
from dataclasses import dataclass
from pathlib import Path

from autoposter.overlays.assets import (
    ASSETS,
    BACK_COLOR,
    EPISODE_CANVAS,
    FONT_COLOR,
    FONTS,
    IMAGES,
    INTER_BOLD,
    INTER_MEDIUM,
    POSTER_CANVAS,
)
from autoposter.overlays.builtin import BUILTIN_OVERLAYS

__all__ = [
    "ASSETS", "FONTS", "IMAGES", "POSTER_CANVAS", "EPISODE_CANVAS",
    "BACK_COLOR", "FONT_COLOR", "INTER_BOLD", "INTER_MEDIUM",
    "BadgeSpec", "BADGES", "canvas_for",
]


@dataclass(frozen=True)
class BadgeSpec:
    name: str
    h_align: str
    h_offset: int
    v_align: str
    v_offset: int
    box: tuple[int, int]
    padding: int = 0
    radius: int = 30
    font: Path | None = None
    font_size: int = 55
    has_back: bool = True


def _as_spec(name: str) -> BadgeSpec:
    """One overlay definition, in the shape the existing pins read.

    A pure projection: every value is read off the definition, nothing is
    defaulted here. That is what makes a mutation of any definition attribute
    show up as a failing pin.
    """
    definition = BUILTIN_OVERLAYS[name]
    return BadgeSpec(
        name=name,
        h_align=definition.horizontal_align,
        h_offset=definition.horizontal_offset,
        v_align=definition.vertical_align,
        v_offset=definition.vertical_offset,
        box=(definition.back_width, definition.back_height),
        padding=definition.back_padding,
        radius=definition.back_radius,
        font=Path(definition.font) if definition.font else None,
        font_size=definition.font_size,
        has_back=definition.has_back,
    )


BADGES: dict[str, BadgeSpec] = {name: _as_spec(name) for name in BUILTIN_OVERLAYS}


def canvas_for(art_kind: str) -> tuple[int, int]:
    """Kometa sizes by item type: episodes are landscape, everything else portrait."""
    return EPISODE_CANVAS if art_kind in ("title_card", "background") else POSTER_CANVAS
```

Do **not** run the badge suite yet — `BUILTIN_OVERLAYS` is empty, so `BADGES` is empty and every badge pin fails. That is the RED the next nine steps turn green one badge at a time.

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py 2>&1 | tail -5
```

Expected: failures naming `KeyError` / an empty `set(BADGES)`. Record the count.

---

### The nine re-expressions

Each is four steps: express → run the badge's own pin unchanged (must PASS) → mutate one attribute transiently (the pin must FAIL) → revert and re-run (must PASS).

**Badge 1 of 9 — `resolution`.** The simplest shape: a plain image overlay, left/top, no padding, no font. It is first because it exercises the `else` branch of `get_cord` (probe §3.3) and nothing else.

- [ ] **Step 8: Express `resolution`**

In `src/autoposter/overlays/builtin.py`, replace `BUILTIN_OVERLAYS: dict[str, OverlayDefinition] = {}` with:

```python
BUILTIN_OVERLAYS: dict[str, OverlayDefinition] = {
    # A plain image overlay: the left/top alignment is get_cord's `else`
    # branch, where the offset is the raw distance from the origin edge
    # (probe section 3.3). No `builtin:` value: `resolution.png`/
    # `resolution/` is a directory, and `resolve_image_path` (T3) refuses a
    # `builtin:` that resolves to a missing file. This badge's own value
    # names the file inside `IMAGE_BADGE_DIRS["resolution"]` at render time
    # (T2 Step 45) -- a value question, not a layout one, so the definition
    # names no specific image. `font_size=55` is stated explicitly because
    # the schema's own default is 36, `BadgeSpec.font_size` defaults to 55,
    # and this badge draws no text either way -- the parity law is shape AND
    # value, not shape only (`test_specs_produce_the_measured_boxes` never
    # asserts `font_size`, so a silent 55 -> 36 drift would pass unnoticed).
    "resolution": OverlayDefinition(
        name="resolution",
        horizontal_align="left", horizontal_offset=15,
        vertical_align="top", vertical_offset=15,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font_size=55,
    ),
}
```

- [ ] **Step 9: Run `resolution`'s pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k resolution 2>&1 | tail -5
```

Expected: **all passed**. (`test_badge_parity.py -k resolution` is deferred to Step 44, where every badge is present and `compose()` can run.)

- [ ] **Step 10: Mutate `resolution` and watch the pin fail**

Change `horizontal_offset=15` to `horizontal_offset=16` in the `resolution` definition and re-run the same command.

Expected: **FAIL**, with `assert (16, 15, 321, 120) == (15, 15, 320, 120)`. If it passes, the pin does not read this attribute — **STOP and report**.

- [ ] **Step 11: Revert and re-run**

Change `16` back to `15`. Re-run the Step 9 command. Expected: **all passed**.

---

**Badge 2 of 9 — `video_format`.** The simplest *text* overlay: Inter-Medium at 55, no icon, no padding, bottom-aligned. It is second because bottom alignment is `get_cord`'s inward-from-the-far-edge branch (probe §3.3) and because it introduces `font`/`font_size`/`text` with nothing else in the way.

- [ ] **Step 12: Express `video_format`**

Add to `BUILTIN_OVERLAYS`, after `resolution`:

```python
    # A plain text overlay. `bottom` is get_cord's inward-from-the-far-edge
    # branch: canvas - overlay - offset (probe section 3.3). The literal is
    # the value itself, so `text` carries no <<variable>> token -- the value
    # arrives from badges/values.py, which this phase does not touch.
    "video_format": OverlayDefinition(
        name="text(video_format)",
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=30,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font=str(INTER_MEDIUM), font_size=55,
    ),
```

and add `from autoposter.overlays.assets import INTER_BOLD, INTER_MEDIUM` to the module's imports.

- [ ] **Step 13: Run `video_format`'s pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k video_format 2>&1 | tail -5
```

Expected: **all passed**.

- [ ] **Step 14: Mutate `video_format` and watch the pin fail**

Change `back_width=305` to `back_width=300`. Re-run.

Expected: **FAIL**, with `assert (15, 1365, 315, 1470) == (15, 1365, 320, 1470)`. If it passes, **STOP and report**.

- [ ] **Step 15: Revert and re-run**

Restore `back_width=305`. Re-run the Step 13 command. Expected: **all passed**.

---

**Badge 3 of 9 — `runtimes`.** Wider box (600), right/bottom. This is the first badge whose displayed value is genuinely a `<<variable>>` expression in Kometa (`runtimes.yml`'s `format: "<<runtimeH>>h <<runtimeM>>m"`, probe §5.2), so its `text` records the real literal even though `badges/values.py::runtime_text` still produces the string today.

- [ ] **Step 16: Express `runtimes`**

Add to `BUILTIN_OVERLAYS`:

```python
    # The literal is Kometa's own, from runtimes.yml (probe section 5.2):
    # `text: "Runtime: "` + `format: "<<runtimeH>>h <<runtimeM>>m"`, joined
    # by that file's `final_name`. `badges/values.py::runtime_text` still
    # produces this string today and is untouched by this phase; recording
    # the literal here is what lets an operator copy the shape.
    "runtimes": OverlayDefinition(
        name="text(Runtime: <<runtimeH>>h <<runtimeM>>m)",
        horizontal_align="right", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=30,
        back_width=600, back_height=105,
        back_color="#00000099", back_radius=30,
        font=str(INTER_MEDIUM), font_size=55,
    ),
```

- [ ] **Step 17: Run `runtimes`' pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k runtimes 2>&1 | tail -5
```

Expected: **all passed** — including both `runtimes` rows in `test_badge_spec.py::test_specs_produce_the_measured_boxes`, one poster (`(385, 1365, 985, 1470)`) and one episode (`(1305, 945, 1905, 1050)`). This is the only badge with two measured rows, which is why its mutation below must fail twice.

- [ ] **Step 18: Mutate `runtimes` and watch the pin fail**

Change `back_width=600` to `back_width=305`. Re-run.

Expected: **FAIL** on both canvases — `assert (680, 1365, 985, 1470) == (385, 1365, 985, 1470)` and `assert (1600, 945, 1905, 1050) == (1305, 945, 1905, 1050)`. If either passes, **STOP and report**.

- [ ] **Step 19: Revert and re-run**

Restore `back_width=600`. Re-run the Step 17 command. Expected: **all passed**.

---

**Badge 4 of 9 — `episode_info`.** Title-card only, and carries one of the two surprising offsets: 150, not 30. `test_badge_spec.py::test_commonsense_and_episode_info_keep_their_surprising_offsets` exists precisely to stop a tidy-up, and it is now a pin on the *definition*.

- [ ] **Step 20: Express `episode_info`**

Add to `BUILTIN_OVERLAYS`:

```python
    # 150, not 30: Kometa's `vertical_align.exists: false` branch fires here
    # even though the file sets `vertical_align: bottom`. Measured at y=825 on
    # a 1080-high canvas, which is exactly 1080 - 105 - 150. A tidy-up to 30
    # is a regression and test_badge_spec.py pins it.
    "episode_info": OverlayDefinition(
        name="text(S<<season_number0>>E<<episode_number0>>)",
        horizontal_align="right", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=150,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font=str(INTER_MEDIUM), font_size=55,
    ),
```

- [ ] **Step 21: Run `episode_info`'s pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k "episode_info or surprising" 2>&1 | tail -5
```

Expected: **all passed**. The `-k` adds `test_commonsense_and_episode_info_keep_their_surprising_offsets`, which will still fail on the `commonsense` half until Step 28 — if only that one fails, with `KeyError: 'commonsense'`, that is expected and correct. Record it; do not "fix" it.

- [ ] **Step 22: Mutate `episode_info` and watch the pin fail**

Change `vertical_offset=150` to `vertical_offset=30`. Re-run.

Expected: **FAIL** with `assert (1600, 945, 1905, 1050) == (1600, 825, 1905, 930)`. If it passes, **STOP and report**.

- [ ] **Step 23: Revert and re-run**

Restore `vertical_offset=150`. Re-run the Step 21 command. Expected: as Step 21.

---

**Badge 5 of 9 — `audio_codec`.** The first `center` alignment, which is `get_cord`'s third and only signed formula (probe §3.3): `canvas/2 - overlay/2 + offset`. It is also the badge whose images overflow their box (135px art in a 105px box), which `overlays/render.py` inherits from `badges/draw.py`'s never-resize rule.

- [ ] **Step 24: Express `audio_codec`**

Add to `BUILTIN_OVERLAYS`:

```python
    # `center` is get_cord's third formula: canvas/2 - overlay/2 + offset
    # (probe section 3.3), the only one where the offset is a nudge rather
    # than a distance from an edge. `compact` is Kometa's audio_codec file
    # default and what production uses -- see docs/research/kometa-overlays.md
    # section 3.2. Two of these images are 135px tall against a 105px box and
    # are deliberately NOT scaled: Kometa lets them overflow. No `builtin:`
    # value, for the same reason as `resolution`: `audio_codec/compact` is a
    # directory, not a file, and this badge's own value names the file inside
    # `IMAGE_BADGE_DIRS["audio_codec"]` (T2 Step 45). `font_size=55` stated
    # explicitly for the same shape-and-value reason as `resolution`.
    "audio_codec": OverlayDefinition(
        name="audio_codec",
        horizontal_align="center", horizontal_offset=0,
        vertical_align="top", vertical_offset=15,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font_size=55,
    ),
```

- [ ] **Step 25: Run `audio_codec`'s pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k audio_codec 2>&1 | tail -5
```

Expected: **all passed** — the measured episode box is `(808, 15, 1113, 120)`, and 808 is `int(1920/2) - int(305/2) + 0`.

- [ ] **Step 26: Mutate `audio_codec` and watch the pin fail**

Change `horizontal_align="center"` to `horizontal_align="left"`. Re-run.

Expected: **FAIL** with `assert (0, 15, 305, 120) == (808, 15, 1113, 120)`. If it passes, **STOP and report**.

- [ ] **Step 27: Revert and re-run**

Restore `horizontal_align="center"`. Re-run the Step 25 command. Expected: **all passed**.

---

**Badge 6 of 9 — `commonsense`.** The first addon: an icon beside the text, `addon_position: left`, `addon_offset: 15`. It also carries the second surprising offset, 270.

- [ ] **Step 28: Express `commonsense`**

Add to `BUILTIN_OVERLAYS`:

```python
    # 270 for the same reason as episode_info's 150: Kometa's
    # `vertical_align.exists: false` branch fires even though the file sets
    # `vertical_align: bottom`. Confirmed in pixels.
    #
    # The first addon overlay: icon beside the text. `addon_position: left`
    # and `addon_offset: 15` (probe section 1.1) are what
    # badges/compose.py used to express as `if name == "commonsense"`.
    "commonsense": OverlayDefinition(
        name="text(<<content_rating>>)",
        builtin="Commonsense",
        horizontal_align="left", horizontal_offset=15,
        vertical_align="bottom", vertical_offset=270,
        back_width=305, back_height=105,
        back_color="#00000099", back_radius=30,
        font=str(INTER_MEDIUM), font_size=55,
        addon_offset=15, addon_position="left",
    ),
```

- [ ] **Step 29: Run `commonsense`'s pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k "commonsense or surprising" 2>&1 | tail -5
```

Expected: **all passed**, including `test_commonsense_and_episode_info_keep_their_surprising_offsets` in full now that both badges exist.

- [ ] **Step 30: Mutate `commonsense` and watch the pin fail**

Change `vertical_offset=270` to `vertical_offset=30`. Re-run.

Expected: **FAIL twice** — the measured box (`assert (15, 1365, 320, 1470) == (15, 1125, 320, 1230)`) and the surprising-offset pin (`assert 30 == 270`). If either passes, **STOP and report**.

- [ ] **Step 31: Revert and re-run**

Restore `vertical_offset=270`. Re-run the Step 29 command. Expected: **all passed**.

---

**Badge 7 of 9 — `critic`.** The first `back_padding`, the first `addon_position: top`, the first Inter-Bold, and a *negative* centre offset — the one alignment where a signed offset is legal (probe §3.2).

- [ ] **Step 32: Express `critic`**

Add to `BUILTIN_OVERLAYS`:

```python
    # `back_padding: 15` turns the 160x160 content box into a 190x190
    # backdrop, expanding on all four sides (probe section 1.1). The -105
    # vertical offset is legal only because the alignment is `center` --
    # probe section 3.2 -- and it moves the badge ABOVE true centre, which is
    # what puts it over `audience`.
    "critic": OverlayDefinition(
        name="text(<<critic_rating#>>)",
        builtin="rating/IMDb",
        horizontal_align="right", horizontal_offset=30,
        vertical_align="center", vertical_offset=-105,
        back_width=160, back_height=160, back_padding=15,
        back_color="#00000099", back_radius=30,
        font=str(INTER_BOLD), font_size=63,
        addon_offset=15, addon_position="top",
    ),
```

- [ ] **Step 33: Run `critic`'s pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k "critic or padding" 2>&1 | tail -5
```

Expected: **all passed**. The `-k` adds `test_badge_geometry.py::test_padding_expands_the_box_on_every_side`, which compares a padded and an unpadded box directly at `critic`'s own numbers — it does not read `BADGES`, so it passes independently, but running it here is what makes the next step's mutation legible.

- [ ] **Step 34: Mutate `critic` and watch the pin fail**

Change `back_padding=15` to `back_padding=0`. Re-run.

Expected: **FAIL** with `assert (810, 565, 970, 725) == (795, 550, 985, 740)`. If it passes, **STOP and report**.

- [ ] **Step 35: Revert and re-run**

Restore `back_padding=15`. Re-run the Step 33 command. Expected: **all passed**.

---

**Badge 8 of 9 — `audience`.** `critic`'s twin below the centreline: the same shape with a *positive* centre offset, and the `%` modifier family from probe §2.3 (`badges/values.py::audience_text` already truncates, matching the banked table).

- [ ] **Step 36: Express `audience`**

Add to `BUILTIN_OVERLAYS`:

```python
    # critic's twin below the centreline: +105 where critic is -105. The
    # literal uses the `%` modifier (probe section 2.3): x10-as-int, which
    # truncates rather than rounds -- badges/values.py::audience_text already
    # records that production output really does.
    "audience": OverlayDefinition(
        name="text(<<audience_rating%>>%)",
        builtin="rating/TMDb",
        horizontal_align="right", horizontal_offset=30,
        vertical_align="center", vertical_offset=105,
        back_width=160, back_height=160, back_padding=15,
        back_color="#00000099", back_radius=30,
        font=str(INTER_BOLD), font_size=63,
        addon_offset=15, addon_position="top",
    ),
```

- [ ] **Step 37: Run `audience`'s pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py -k audience 2>&1 | tail -5
```

Expected: **all passed** — the measured box is `(795, 760, 985, 950)`, 210px below `critic`'s.

- [ ] **Step 38: Mutate `audience` and watch the pin fail**

Change `vertical_offset=105` to `vertical_offset=-105`. Re-run.

Expected: **FAIL** with `assert (795, 550, 985, 740) == (795, 760, 985, 950)` — the badge lands exactly on top of `critic`, which is the failure mode a sign error would actually produce. If it passes, **STOP and report**.

- [ ] **Step 39: Revert and re-run**

Restore `vertical_offset=105`. Re-run the Step 37 command. Expected: **all passed**.

---

**Badge 9 of 9 — `languages`.** Last, and the most special: the only badge with no backdrop, the only one drawn by its own loop, and the one that most looks like it wants a queue and deliberately does not get one (Global Constraint 7). `has_back` is derived here rather than authored (Adjudication A9), which is what makes the mutation meaningful.

- [ ] **Step 40: Express `languages`**

Add to `BUILTIN_OVERLAYS`:

```python
    # The only badge without a backdrop: the production config applies the
    # languages overlay twice, the second time fully transparent, and neither
    # oracle image shows a backdrop behind the flag. `has_back` is DERIVED
    # from the colours (probe section 1.1), so omitting back_color is what
    # switches it off -- there is no separate flag to get wrong.
    #
    # This is the badge that looks most like a Kometa queue: one flag per
    # audio language, stacked 61px apart. It is deliberately NOT expressed as
    # one -- the queue is filed forward on row 97 (facts C1.5), and
    # badges/compose.py::_draw_languages keeps its bespoke loop.
    "languages": OverlayDefinition(
        name="text(<<audio_language>>)",
        horizontal_align="left", horizontal_offset=15,
        vertical_align="top", vertical_offset=223,
        back_width=190, back_height=105,
        back_radius=26,
        font=str(INTER_BOLD), font_size=50,
    ),
```

- [ ] **Step 41: Run `languages`' pins, UNCHANGED**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_badge_spec.py tests/test_badge_geometry.py tests/test_badge_draw.py 2>&1 | tail -5
```

Every badge now exists, so this runs the three files in full rather than a `-k` slice.

Expected: **all passed**, including `test_every_expected_badge_is_defined` (the nine-key set) and `test_languages_has_no_backdrop` (which asserts `languages` is false AND every other badge true).

- [ ] **Step 42: Mutate `languages` and watch the pin fail**

Add `back_color="#00000099",` to the `languages` definition. Re-run the Step 41 command.

Expected: **FAIL** on `test_languages_has_no_backdrop` with `assert True is False`. If it passes, the derivation is not wired — **STOP and report**.

- [ ] **Step 43: Revert and re-run**

Remove the `back_color` line. Re-run the Step 41 command. Expected: **all passed**.

- [ ] **Step 44: Commit the nine definitions**

```bash
git add src/autoposter/overlays/builtin.py src/autoposter/badges/spec.py \
        tests/test_overlay_builtin.py
git commit --no-gpg-sign -m "feat(overlays): the nine badges re-expressed as overlay definitions"
```

---

- [ ] **Step 45: Switch `compose.py` onto the generalised draw**

The nine definitions now carry the layout that `compose.py`'s `IMAGE_BADGES`, `BADGE_ICONS` and `if name == "commonsense"` branches used to. Replace those with `draw_overlay`.

In `src/autoposter/badges/compose.py`:

Replace the import block

```python
from autoposter.badges.draw import (
    composite,
    draw_backdrop,
    draw_text_centered,
    new_layer,
    paste_centered,
)
from autoposter.badges.spec import ASSETS, BADGES, IMAGES, canvas_for
```

with

```python
from autoposter.badges.draw import composite, draw_text_centered, new_layer
from autoposter.badges.spec import ASSETS, BADGES, IMAGES, canvas_for
from autoposter.overlays.builtin import BUILTIN_OVERLAYS
from autoposter.overlays.render import draw_overlay
```

Replace the `IMAGE_BADGES` / `BADGE_ICONS` / `ADDON_OFFSET` block with:

```python
# Where each image-valued badge's art lives, keyed by badge. The value is a
# directory: the badge's own value names the file inside it. Kept here rather
# than on the definition because the definition names ONE image and these
# badges pick one of many at render time from the item's own media -- which is
# a value question (badges/values.py), not a layout one.
IMAGE_BADGE_DIRS = {
    "resolution": IMAGES / "resolution",
    "audio_codec": IMAGES / "audio_codec" / "compact",
}
# Badges that pair a fixed logo with their text. The definition carries the
# POSITION and the GAP (addon_position / addon_offset); this carries the file.
BADGE_ICONS = {
    "critic": IMAGES / "rating" / "IMDb.png",
    "audience": IMAGES / "rating" / "TMDb.png",
    "commonsense": IMAGES / "Commonsense.png",
}
```

Replace the body of `compose`'s per-badge loop — everything from `spec = BADGES[name]` down to `composite(poster, layer)` — with:

```python
        definition = BUILTIN_OVERLAYS[name]
        layer = new_layer(canvas)

        if name in IMAGE_BADGE_DIRS:
            image_path = IMAGE_BADGE_DIRS[name] / ("%s.png" % value)
            if not image_path.exists():
                continue
            draw_overlay(layer, definition, canvas, image=_load(image_path))
        else:
            font = ImageFont.truetype(definition.font, definition.font_size)
            icon_path = BADGE_ICONS.get(name)
            icon = _load(icon_path) if icon_path is not None and icon_path.exists() else None
            draw_overlay(layer, definition, canvas, image=icon, text=value, font=font)
        composite(poster, layer)
```

Delete the now-orphaned `_text_size` helper, the `ImageDraw` import from the `from PIL import Image, ImageDraw, ImageFont` line (`_text_size` was its only user in this module), and the `paste_centered` / `draw_backdrop` imports (Global Constraint: remove only what *these* changes orphaned).

In `_draw_languages`, replace `spec = BADGES["languages"]` and its two uses with the definition:

```python
    definition = BUILTIN_OVERLAYS["languages"]
    font = ImageFont.truetype(definition.font, definition.font_size)
    for index, (country, label) in enumerate(language_slots(inputs.media)):
        flag_path = IMAGES / "flag" / "round" / ("%s.png" % country)
        if not flag_path.exists():
            continue
        flag = _load(flag_path)
        top = definition.vertical_offset + index * 61
        layer = new_layer(canvas)
        layer.paste(flag, (definition.horizontal_offset, top), flag)
        draw_text_centered(
            layer, label, font,
            (definition.horizontal_offset + flag.width + 14, top,
             definition.horizontal_offset + flag.width + 90, top + flag.height),
        )
        composite(poster, layer)
```

- [ ] **Step 46: Run the byte-identity gate and every badge pin**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov2-gate test sh -c 'pytest -q tests/test_overlay_engine_golden.py tests/test_badge_spec.py tests/test_badge_geometry.py tests/test_badge_draw.py tests/test_badge_compose.py tests/test_badge_parity.py tests/test_overlay_builtin.py tests/test_overlay_render.py 2>&1 | tee /app/.superpowers/run-pov-t2-gate.log'
docker wait pov2-gate
docker cp pov2-gate:/app/.superpowers/run-pov-t2-gate.log .superpowers/run-pov-t2-gate.log
docker rm pov2-gate
tail -10 .superpowers/run-pov-t2-gate.log
```

Expected: **all passed**. The two hashes from T1 Step 4 must match — that is the byte-identical proof for the whole swap. If a hash differs, the swap changed a pixel: **STOP and report**, do not re-record.

- [ ] **Step 47: The whole-engine mutation — prove the gate itself is live**

The per-badge mutations proved each *pin* reads its attribute. This proves the *gate* reads the render.

Change `commonsense`'s `addon_position="left"` to `addon_position="top"` and re-run the Step 46 command.

Expected: `test_the_poster_composite_is_byte_identical_to_the_pre_swap_baseline` **FAILS** with two different hashes, and `test_badge_parity.py -k commonsense` likely fails too. `test_badge_spec.py` and `test_badge_geometry.py` **pass** — the box is unchanged, only the content inside it moved, which is exactly the class of regression the box pins cannot see and the gate can. Record both facts in the report; they are the argument for why the gate exists.

Revert to `addon_position="left"` and re-run. Expected: **all passed**.

- [ ] **Step 48: Run the full suite**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov2-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-pov-t2-full.log'
docker wait pov2-full
docker cp pov2-full:/app/.superpowers/run-pov-t2-full.log .superpowers/run-pov-t2-full.log
docker rm pov2-full
tail -5 .superpowers/run-pov-t2-full.log
```

State the expected count against T1's measured total before quoting the result; reconcile any gap.

- [ ] **Step 49: Lint, verify the diff is confined, and commit**

```bash
docker compose -p pov2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test ruff check src tests
git diff --stat HEAD~3 -- src/ tests/
git add src/autoposter/badges/compose.py
git commit --no-gpg-sign -m "refactor(badges): compose through the generalised overlay draw"
docker compose -p pov2 down
```

`git diff --stat` must list only files in this task's Files block. **`tests/test_badge_*.py` must not appear at all** — if one does, the parity law was broken; revert it and re-run.

---

## Task 3: The operator path — config, the source ladder, the entry point

**Files:**
- Create: `src/autoposter/overlays/sources.py`
- Create: `tests/test_overlay_sources.py`
- Create: `tests/test_overlay_entrypoint.py`
- Modify: `src/autoposter/config/schema.py`
- Modify: `src/autoposter/badges/compose.py`
- Modify: `src/autoposter/render/pipeline.py`
- Modify: `config/autoposter.example.yaml`

**Interfaces:**
- Consumes: `OverlayDefinition`, `draw_overlay`, `render_text`, `UnresolvedVariable`, `BUILTIN_OVERLAYS`.
- Produces: `resolve_image_path(...)`, `OverlaySourceError`, `BadgesConfig.definitions`, `BadgesConfig.definition_image_max_bytes`, `compose(..., definitions=..., http=...)`.

---

- [ ] **Step 1: Write the failing source-ladder tests**

Create `tests/test_overlay_sources.py`:

```python
"""The image-source ladder -- ours, per the plan's ladder table.

Kometa's precedence (probe section 1.2) is file > default/pmm > git > repo >
url > name fallback. `git` and `repo` do not exist for us; `default`/`pmm` is
our `builtin`, resolving against the tree probe section 6 corrects to
Kometa's OWN `defaults/overlays/images/` rather than the Default-Images repo.
"""
from pathlib import Path

import httpx
import pytest

from autoposter.overlays.schema import OverlayDefinition
from autoposter.overlays.sources import OverlaySourceError, resolve_image_path


@pytest.fixture
def overlays_root(tmp_path):
    root = tmp_path / "overlays"
    root.mkdir()
    return root


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_file_source_resolves_under_overlays_root(overlays_root):
    (overlays_root / "mine.png").write_bytes(b"x")
    path = await resolve_image_path(
        OverlayDefinition(name="o", file="mine.png"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    )
    assert path == overlays_root / "mine.png"


async def test_a_file_source_cannot_escape_overlays_root(overlays_root):
    """An operator-typed path is not a licence to read the filesystem."""
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", file="../../etc/passwd"),
            overlays_root=overlays_root, http=None, max_bytes=1000,
        )


async def test_a_builtin_source_resolves_against_the_bundled_tree(overlays_root):
    """Probe section 6: this tree is Kometa's own defaults/overlays/images/,
    NOT the Default-Images repo. `.png` is appended when missing (probe
    section 1.2)."""
    path = await resolve_image_path(
        OverlayDefinition(name="o", builtin="Commonsense"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    )
    assert path is not None and path.name == "Commonsense.png"
    assert path.exists()


async def test_a_builtin_source_cannot_escape_the_bundled_tree(overlays_root):
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", builtin="../../../etc/passwd"),
            overlays_root=overlays_root, http=None, max_bytes=1000,
        )


async def test_a_missing_builtin_is_an_error_not_a_silent_skip(overlays_root):
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", builtin="no-such-stamp"),
            overlays_root=overlays_root, http=None, max_bytes=1000,
        )


async def test_a_url_source_is_downloaded_and_cached_by_url(overlays_root, monkeypatch):
    """Adjudication A7: content-addressed, so a second call makes no request.

    `guarded_download` resolves the host for real before any request
    (`net/guard.py::validate_target` -> `resolve_host`), and `no_outbound_network`
    (conftest.py) only patches `httpx.AsyncHTTPTransport.handle_async_request`,
    not `getaddrinfo` -- so `example.com` would otherwise hit real DNS. Patched
    per `tests/test_fetch_guard.py`'s own precedent (its `resolve_host` patches).
    """
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, content=b"\x89PNG\r\n\x1a\n" + b"0" * 64,
            headers={"content-type": "image/png"},
        )

    definition = OverlayDefinition(name="o", url="https://example.com/a.png")
    async with _client(handler) as http:
        first = await resolve_image_path(
            definition, overlays_root=overlays_root, http=http, max_bytes=1000
        )
        second = await resolve_image_path(
            definition, overlays_root=overlays_root, http=http, max_bytes=1000
        )
    assert first == second
    assert first.parent == overlays_root / ".cache"
    assert len(calls) == 1, "the second resolve must not re-request"


async def test_a_url_answering_with_the_wrong_content_type_is_refused(overlays_root, monkeypatch):
    """Probe section 1.2: Kometa validates Content-Type == image/png and
    rejects anything else. net/guard.py's content_types allowlist carries it.

    Same DNS concern and same fix as the test above: `example.com` is
    resolved for real by `validate_target` before the request, so
    `guard.resolve_host` is patched per `tests/test_fetch_guard.py`'s
    precedent.
    """
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )

    def handler(request):
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    async with _client(handler) as http:
        with pytest.raises(OverlaySourceError):
            await resolve_image_path(
                OverlayDefinition(name="o", url="https://example.com/a.png"),
                overlays_root=overlays_root, http=http, max_bytes=1000,
            )


async def test_a_url_pointing_at_a_private_address_is_never_requested(overlays_root):
    """net/guard.py's SSRF guard. A definition is operator-typed config, so
    this is the same threat model api/candidates.py already has."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x", headers={"content-type": "image/png"})

    async with _client(handler) as http:
        with pytest.raises(OverlaySourceError):
            await resolve_image_path(
                OverlayDefinition(name="o", url="http://169.254.169.254/latest/meta-data"),
                overlays_root=overlays_root, http=http, max_bytes=1000,
            )
    assert calls == [], "the guard must refuse before any request is made"


async def test_no_source_falls_back_to_the_name_keyed_file(overlays_root):
    """Probe section 1.2 step 6."""
    (overlays_root / "mystamp.png").write_bytes(b"x")
    path = await resolve_image_path(
        OverlayDefinition(name="mystamp"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    )
    assert path == overlays_root / "mystamp.png"


async def test_a_text_overlay_with_no_source_and_no_file_resolves_to_nothing(overlays_root):
    """A text overlay need not carry an image at all."""
    assert await resolve_image_path(
        OverlayDefinition(name="text(hello)"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    ) is None
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p pov3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_sources.py 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'autoposter.overlays.sources'`.

- [ ] **Step 3: Write `overlays/sources.py`**

Create `src/autoposter/overlays/sources.py`:

```python
"""Where an overlay's image comes from.

Kometa's precedence is banked in `.superpowers/sdd/p-overlay-grammar-probe.md`
section 1.2: `file` > `default`/`pmm` > `git` > `repo` > `url` > a name-keyed
fallback. Two of those rungs do not exist for this service and are not
invented -- there is no Kometa Configs-repo mirror (`git`) and no custom-repo
setting (`repo`). The plan's ladder table states each rung and why.

The `builtin` rung resolves against THIS service's bundled overlay-stamp tree,
which is the same shape as Kometa's own `defaults/overlays/images/` -- probe
section 6 specifically corrects the earlier premise that these assets come
from the `Kometa-Team/Default-Images` repo. They do not; that is a different
repo with a different (poster-sized) naming scheme.

Both path rungs are confined to their root. A definition is operator-typed
config, and an operator-typed path that can leave its mount is a file-read
primitive, not a convenience.
"""
import hashlib
from pathlib import Path

from autoposter.net.guard import FetchRefused, guarded_download
from autoposter.overlays.assets import IMAGES
from autoposter.overlays.schema import OverlayDefinition

# Probe section 1.2: Kometa validates `Content-Type == "image/png"` on every
# downloaded overlay image and rejects anything else. Carried verbatim into
# net/guard.py's allowlist parameter.
OVERLAY_CONTENT_TYPES = frozenset({"image/png"})


class OverlaySourceError(Exception):
    """This overlay's image could not be resolved.

    Carries the overlay's name and the reason, never a URL or a host -- the
    same rule net/guard.py holds itself to, for the same reason: this message
    reaches a log line an operator reads.
    """


def _confined(root: Path, value: str, name: str) -> Path:
    """A path beneath `root`, or a refusal."""
    candidate = (root / value).resolve()
    root = root.resolve()
    if root not in candidate.parents and candidate != root:
        raise OverlaySourceError(
            f"overlay {name!r}: the configured path leaves its root directory"
        )
    return candidate


async def resolve_image_path(
    definition: OverlayDefinition,
    *,
    overlays_root: Path,
    http,
    max_bytes: int,
) -> Path | None:
    """This overlay's image on disk, or None when it has no image.

    Returning None is not a failure: a text overlay carrying no addon has no
    image, and the caller draws text alone.
    """
    name = definition.name

    if definition.file:
        path = _confined(overlays_root, definition.file, name)
        if not path.exists():
            raise OverlaySourceError(f"overlay {name!r}: the configured file does not exist")
        return path

    if definition.builtin:
        # Probe section 1.2: a leading `overlays/images/` prefix is stripped
        # and `.png` appended when missing.
        value = definition.builtin.removeprefix("overlays/images/")
        if not value.endswith(".png"):
            value += ".png"
        path = _confined(IMAGES, value, name)
        if not path.exists():
            raise OverlaySourceError(
                f"overlay {name!r}: no bundled overlay image by that name"
            )
        return path

    if definition.url:
        return await _download(definition, overlays_root, http, max_bytes)

    # Probe section 1.2 step 6: the operator's own folder, keyed by the
    # overlay's own name.
    fallback = _confined(overlays_root, f"{name}.png", name)
    return fallback if fallback.exists() else None


async def _download(
    definition: OverlayDefinition, overlays_root: Path, http, max_bytes: int
) -> Path:
    """Fetch through the SSRF guard, cached by URL.

    Content-addressed rather than TTL'd (the plan's adjudication A7): the key
    IS the URL, so a changed URL is a new file and an unchanged one is never
    re-fetched. An operator who replaces the image behind a stable URL clears
    `<overlays_root>/.cache/`.
    """
    cache = overlays_root / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / (
        hashlib.sha256(definition.url.encode("utf-8")).hexdigest() + ".png"
    )
    if destination.exists():
        return destination
    try:
        await guarded_download(
            http, definition.url, destination,
            max_bytes=max_bytes, content_types=OVERLAY_CONTENT_TYPES,
        )
    except FetchRefused as exc:
        destination.unlink(missing_ok=True)
        raise OverlaySourceError(
            f"overlay {definition.name!r}: image download refused ({exc})"
        ) from exc
    return destination
```

- [ ] **Step 4: Run the source-ladder tests to verify they pass**

```bash
docker compose -p pov3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_sources.py 2>&1 | tail -5
```

Expected: **10 passed**.

- [ ] **Step 5: Commit the ladder**

```bash
git add src/autoposter/overlays/sources.py tests/test_overlay_sources.py
git commit --no-gpg-sign -m "feat(overlays): the image-source ladder, guarded url fetch included"
```

- [ ] **Step 6: Write the failing entry-point tests**

Create `tests/test_overlay_entrypoint.py`:

```python
"""The gated-feature entry-point law for operator-defined overlays.

The proof is made through the REAL entry point -- badges/compose.py::compose,
which render/pipeline.py::apply_badges calls -- not through
overlays/render.py alone. With no definitions configured, the output must be
byte-identical to the recorded pre-swap baseline.
"""
import hashlib
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autoposter.badges.compose import BadgeInputs, compose
from autoposter.badges.values import MediaInfo
from autoposter.config.schema import BadgesConfig
from autoposter.overlays.schema import OverlayDefinition
from tests.test_overlay_engine_golden import (
    ALL_SOULS,
    POSTER_PIXELS_SHA,
    pixels_sha,
)

ORACLE = Path("tests/fixtures/oracle")
BASE = ORACLE / "All_Souls_base_no_overlay.jpg"


def _sha(data: bytes) -> str:
    array = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.uint8)
    return hashlib.sha256(array.tobytes()).hexdigest()


def test_the_default_config_configures_no_definitions():
    """The gate-off value is the empty list, which is what makes 'no change
    for a config that does not set the key' checkable rather than asserted."""
    assert BadgesConfig().definitions == []


def test_no_definitions_is_byte_identical_to_the_pre_swap_baseline():
    """Global Constraint 8. Compared against the RECORDED hash, not against a
    second call to the same function -- a re-derivation would pass even if
    both sides changed together."""
    assert _sha(compose(BASE, "poster", ALL_SOULS, definitions=[])) == POSTER_PIXELS_SHA


def test_passing_no_definitions_argument_at_all_is_also_byte_identical():
    """Every existing caller omits the argument; none of them may move."""
    assert pixels_sha(BASE, "poster", ALL_SOULS) == POSTER_PIXELS_SHA


def test_one_definition_changes_the_output():
    """The other half of the gate: on must differ from off, or the gate is
    proving nothing."""
    stamp = OverlayDefinition(
        name="text(HELLO)",
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100,
        back_color="#FF0000FF", back_radius=10,
        font_size=55,
    )
    data = compose(BASE, "poster", ALL_SOULS, definitions=[stamp])
    assert _sha(data) != POSTER_PIXELS_SHA


def test_a_definition_naming_an_unresolvable_variable_is_skipped_not_fatal():
    """Probe section 2.4: an unresolved variable is a per-item overlay skip
    with a warning, not a run abort. The other overlays still draw, so the
    output equals the no-definitions baseline exactly."""
    stamp = OverlayDefinition(
        name="text(<<trakt_user_rating>>)",
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100,
        back_color="#FF0000FF", back_radius=10,
        font_size=55,
    )
    assert _sha(compose(BASE, "poster", ALL_SOULS, definitions=[stamp])) == POSTER_PIXELS_SHA


def test_a_group_keeps_only_the_highest_weight_member():
    """Probe section 4.1: group resolution is winner-take-highest, per item."""
    def _stamp(name, weight, colour):
        return OverlayDefinition(
            name=name, group="ribbon", weight=weight,
            horizontal_align="center", horizontal_offset=0,
            vertical_align="center", vertical_offset=0,
            back_width=300, back_height=100,
            back_color=colour, back_radius=10, font_size=55,
        )

    both = compose(BASE, "poster", ALL_SOULS, definitions=[
        _stamp("text(LOW)", 10, "#00FF00FF"), _stamp("text(HIGH)", 190, "#FF0000FF"),
    ])
    winner_only = compose(BASE, "poster", ALL_SOULS, definitions=[
        _stamp("text(HIGH)", 190, "#FF0000FF"),
    ])
    assert _sha(both) == _sha(winner_only)


def test_suppression_is_resolved_before_group_weight():
    """Probe section 4.1: if A suppresses B and both match, B is dropped
    outright -- group weight never arbitrates that pair."""
    suppressor = OverlayDefinition(
        name="text(A)", group="g", weight=10, suppress_overlays=["text(B)"],
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100, back_color="#FF0000FF",
        back_radius=10, font_size=55,
    )
    suppressed = OverlayDefinition(
        name="text(B)", group="g", weight=190,
        horizontal_align="center", horizontal_offset=0,
        vertical_align="center", vertical_offset=0,
        back_width=300, back_height=100, back_color="#00FF00FF",
        back_radius=10, font_size=55,
    )
    alone = compose(BASE, "poster", ALL_SOULS, definitions=[suppressor])
    together = compose(BASE, "poster", ALL_SOULS, definitions=[suppressor, suppressed])
    # B has the higher weight; without suppression it would win. It is dropped.
    assert _sha(together) == _sha(alone)
```

- [ ] **Step 7: Run them to verify they fail**

```bash
docker compose -p pov3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_entrypoint.py 2>&1 | tail -10
```

Expected: `AttributeError`/`TypeError` — `BadgesConfig` has no `definitions`, and `compose()` takes no `definitions` argument. That is the right reason.

- [ ] **Step 8: Add the config fields**

In `src/autoposter/config/schema.py`, add to the imports:

```python
from autoposter.overlays.schema import OverlayDefinition
```

and to `BadgesConfig`, after `adopt_from_plex`:

```python
    # Roadmap row 97. Operator-defined overlays, drawn after the nine built-in
    # badges. Empty by default, which is byte-identical to no feature at all
    # (tests/test_overlay_entrypoint.py pins that against a recorded hash).
    definitions: list[OverlayDefinition] = Field(
        default_factory=list,
        description=(
            "Operator-defined overlays composited on top of the built-in "
            "badges, each naming its own image or text, position, backdrop "
            "and optional group."
        ),
    )
    definition_image_max_bytes: int = Field(
        default=5 * 1024 * 1024, gt=0,
        description=(
            "Largest image an overlay definition's url source may download; "
            "a larger body is refused mid-stream."
        ),
    )
```

`Config.overlays_root`'s own description is now under-stated: it says only that `overlay_file` (the existing Posterizarr-style per-artwork overlay) reads from this mount, but T3's `file:`/`builtin:`-fallback and `url:` sources (`overlays/sources.py`) also resolve under it, and the `url:` source adds a `.cache/` subdirectory inside it. Widen it:

```python
    overlays_root: Path = Field(
        description=(
            "Where the overlay images referenced by overlay_file are read "
            "from; also the mount badges.definitions' file and name-keyed "
            "sources resolve under, and where a definition's url source is "
            "cached, in a .cache/ subdirectory."
        ),
    )
```

- [ ] **Step 9: Thread definitions through `compose`**

In `src/autoposter/badges/compose.py`, add the imports:

```python
from autoposter.overlays.schema import OverlayDefinition
from autoposter.overlays.variables import UnresolvedVariable, literal_of, render_text
```

and a module logger if one is not already present:

```python
import logging

logger = logging.getLogger(__name__)
```

Change `compose`'s signature to:

```python
def compose(
    base_path: Path,
    art_kind: str,
    inputs: BadgeInputs,
    fingerprint: str | None = None,
    definitions: list[OverlayDefinition] | None = None,
    resolved_images: dict[str, Path] | None = None,
) -> bytes:
```

and extend its docstring with:

```
    ``definitions`` are operator-defined overlays (roadmap row 97), drawn
    after every built-in badge and after the language stack. ``None`` and
    ``[]`` are the same thing and are byte-identical to the pre-row-97
    output.

    ``resolved_images`` maps a definition's name to an image already fetched
    for it. Resolution is async and this function is not: the caller does the
    I/O (``overlays.sources.resolve_image_path``) and hands the results in,
    which also keeps ``compose`` a pure function of its arguments.
```

Immediately before the `exif = Image.Exif()` line — i.e. after `_draw_languages` — insert:

```python
    _draw_definitions(poster, canvas, inputs, definitions or [], resolved_images or {})
```

and add, after `_draw_languages`:

```python
def _variable_values(art_kind: str, inputs: BadgeInputs) -> dict[str, object]:
    """The item values an operator's <<variable>> tokens can resolve against.

    Deliberately only what this service already gathers. Probe section 2.2's
    27 external rating sources are part of the GRAMMAR and are absent here on
    purpose: fetching them is row 100's data half, and a definition naming one
    is skipped rather than silently rendered wrong.
    """
    media = inputs.media
    values: dict[str, object] = {
        "content_rating": inputs.content_rating,
        "critic_rating": inputs.critic_rating,
        "audience_rating": inputs.audience_rating,
        "season_number": media.season_number,
        "episode_number": media.episode_number,
    }
    if media.duration_ms:
        values["runtime"] = media.duration_ms // 60000
        values["total_runtime"] = values["runtime"]
    return {k: v for k, v in values.items() if v is not None}


def _resolve_definitions(
    definitions: list[OverlayDefinition],
) -> list[OverlayDefinition]:
    """Apply suppression, then group weight. Probe section 4.1's order.

    Suppression runs FIRST: if A names B in suppress_overlays and both match,
    B is dropped outright and group weight never arbitrates that pair.
    """
    suppressed = {n for d in definitions for n in d.suppress_overlays}
    surviving = [d for d in definitions if d.name not in suppressed]

    winners: dict[str, OverlayDefinition] = {}
    result: list[OverlayDefinition] = []
    for definition in surviving:
        if not definition.group:
            result.append(definition)
            continue
        current = winners.get(definition.group)
        if current is None or definition.weight > current.weight:
            winners[definition.group] = definition
    return result + list(winners.values())


def _draw_definitions(
    poster: Image.Image,
    canvas: tuple[int, int],
    inputs: BadgeInputs,
    definitions: list[OverlayDefinition],
    resolved_images: dict[str, Path],
) -> None:
    """Draw the operator's own overlays, after every built-in one."""
    if not definitions:
        return
    values = _variable_values("", inputs)
    for definition in _resolve_definitions(definitions):
        literal = literal_of(definition.name)
        text = None
        if literal is not None:
            try:
                text = render_text(literal, values)
            except UnresolvedVariable as exc:
                # Probe section 2.4: a per-item skip with a warning, never a
                # run abort.
                logger.warning(
                    "overlay %r skipped: no value for <<%s>>", definition.name, exc
                )
                continue
        image_path = resolved_images.get(definition.name)
        image = _load(image_path) if image_path is not None else None
        font = (
            ImageFont.truetype(definition.font, definition.font_size)
            if text is not None and definition.font
            else ImageFont.load_default(definition.font_size)
            if text is not None
            else None
        )
        layer = new_layer(canvas)
        draw_overlay(layer, definition, canvas, image=image, text=text, font=font)
        composite(poster, layer)
```

- [ ] **Step 10: Run the entry-point tests to verify they pass**

```bash
docker compose -p pov3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_entrypoint.py tests/test_overlay_engine_golden.py 2>&1 | tail -5
```

Expected: **9 passed**. The two golden hashes must be unchanged — that is the gate-off half of the entry-point law.

- [ ] **Step 11: Mutate the gate and watch it fail**

Change `_draw_definitions`'s first two lines from

```python
    if not definitions:
        return
```

to

```python
    if definitions is None:
        return
```

and re-run the Step 10 command.

Expected: the tests still pass (an empty list draws nothing either way). That is a **negative** result and it is informative: it says the gate's safety comes from the empty loop, not the early return. Record it. Now make the mutation that matters: change `_resolve_definitions`'s `if definition.name not in suppressed` to `if True`, and re-run.

Expected: `test_suppression_is_resolved_before_group_weight` **FAILS**. Revert both edits and re-run; expected **9 passed**.

- [ ] **Step 12: Wire the pipeline**

Today, `apply_badges` has no `http` client in scope (`pipeline.py:1121`):

```python
async def apply_badges(session, config, render, item, plex_item, facts, probe=None) -> None:
```

Its only caller, `process_item`, does hold one (`http: httpx.AsyncClient`). But `tests/test_badge_pipeline.py` calls `apply_badges` **positionally at ~30 sites**, and that file is outside this task's Files block and outside the never-modify list — it must stay untouched. Adding `http` as a positional parameter would break every one of those call sites and either force an out-of-scope edit (violating Global Constraint 15) or leave the suite red. Adding it **keyword-only with a `None` default** avoids both: every existing positional call keeps working unchanged, and `process_item` is free to pass its own client by keyword.

In `src/autoposter/render/pipeline.py`, change `apply_badges`'s signature to:

```python
async def apply_badges(
    session, config, render, item, plex_item, facts, probe=None, *, http=None
) -> None:
```

Then, inside `apply_badges`, resolve each definition's image before the `compose_badges` call and pass both through. Locate the existing call:

```python
        compose_badges, Path(render.asset_path), render.art_kind, inputs, fingerprint
```

and replace it with a resolution step plus the extended call:

```python
    definitions = config.badges.definitions
    resolved_images: dict[str, Path] = {}
    for definition in definitions:
        try:
            path = await resolve_image_path(
                definition,
                overlays_root=config.overlays_root,
                http=http,
                max_bytes=config.badges.definition_image_max_bytes,
            )
        except OverlaySourceError as exc:
            # Class name and a fixed sentence: the message may carry an
            # operator-typed path.
            logger.warning(
                "overlay %r has no usable image (%s); skipping it",
                definition.name, type(exc).__name__,
            )
            continue
        if path is not None:
            resolved_images[definition.name] = path
```

then

```python
        compose_badges, Path(render.asset_path), render.art_kind, inputs, fingerprint,
        definitions, resolved_images,
```

Add the imports at the top of the module:

```python
from autoposter.overlays.sources import OverlaySourceError, resolve_image_path
```

Finally, in `process_item`, thread its own `http` client into the call it already makes:

```python
                await apply_badges(
                    session, config, render, media_item, plex_item, facts,
                    probe=artwork_probe, http=http,
                )
```

Every other caller of `apply_badges` — including all ~30 positional calls in `tests/test_badge_pipeline.py` — keeps the default `http=None`: `resolve_image_path` then raises `OverlaySourceError` for any `url:` source, which the `except` above already turns into a skip-with-a-warning, so nothing there breaks either.

- [ ] **Step 13: Document the new keys**

In `config/autoposter.example.yaml`, under the `badges:` block, add:

```yaml
  # Roadmap row 97. Operator-defined overlays, drawn on top of the nine
  # built-in badges. Empty means no change at all.
  #
  # Each entry takes the attributes Kometa's own `overlay:` block does:
  # name, group/weight, suppress_overlays, horizontal_/vertical_offset and
  # _align, scale_width/scale_height, back_color/back_line_color/
  # back_line_width/back_radius/back_padding/back_align/back_width/
  # back_height, one image source (file, builtin or url), and for a
  # `text(...)` name the font, font_size, font_color, stroke_width,
  # stroke_color, addon_offset and addon_position.
  #
  # `file:` is a path under overlays_root. `builtin:` is a path in this
  # service's bundled overlay-stamp tree (no .png needed). `url:` is
  # downloaded once and cached under overlays_root/.cache/ by URL.
  #
  # definitions:
  #   - name: "text(4K)"
  #     horizontal_align: right
  #     horizontal_offset: 15
  #     vertical_align: top
  #     vertical_offset: 15
  #     back_width: 200
  #     back_height: 105
  #     back_color: "#00000099"
  #     back_radius: 30
  #     font_size: 55
  definitions: []
  # Largest image a definition's `url:` source may download, in bytes.
  definition_image_max_bytes: 5242880
```

- [ ] **Step 14: Run the full suite**

```bash
docker compose -p pov3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov3-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-pov-t3-full.log'
docker wait pov3-full
docker cp pov3-full:/app/.superpowers/run-pov-t3-full.log .superpowers/run-pov-t3-full.log
docker rm pov3-full
tail -5 .superpowers/run-pov-t3-full.log
```

State the expected count first; reconcile any gap. `tests/test_config_descriptions.py` must pass — both new fields have descriptions saying WHAT they are.

- [ ] **Step 15: Lint, verify the diff, and commit**

```bash
docker compose -p pov3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test ruff check src tests
git diff --stat HEAD~2 -- src/ tests/
git add src/autoposter/config/schema.py src/autoposter/badges/compose.py \
        src/autoposter/render/pipeline.py config/autoposter.example.yaml \
        tests/test_overlay_entrypoint.py
git commit --no-gpg-sign -m "feat(overlays): operator-defined overlays, gated off by default"
docker compose -p pov3 down
```

`tests/test_badge_*.py` must not appear in the diff.

---

## Task 4: Wrap

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`
- Modify: `.superpowers/sdd/progress.md`

- [ ] **Step 1: Run the whole suite one more time, from a clean container**

```bash
docker compose -p pov4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pov4-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-pov-t4-full.log'
docker wait pov4-full
docker cp pov4-full:/app/.superpowers/run-pov-t4-full.log .superpowers/run-pov-t4-full.log
docker rm pov4-full
tail -5 .superpowers/run-pov-t4-full.log
```

Expected: green, matching T3's count. Quote the number against T1's `BASELINE`.

- [ ] **Step 2: Verify the parity law held across the whole branch**

```bash
git diff --stat origin/main -- tests/test_badge_spec.py tests/test_badge_geometry.py \
    tests/test_badge_draw.py tests/test_badge_compose.py tests/test_badge_parity.py \
    src/autoposter/badges/geometry.py src/autoposter/badges/draw.py \
    src/autoposter/badges/values.py src/autoposter/render/compositor.py \
    src/autoposter/render/textfit.py tests/test_production_parity.py
```

Expected: **empty output**. Any line here is a parity-law violation — STOP and report rather than closing the row. Paste the (empty) result into the task report; a wrap that does not show this is not a wrap.

- [ ] **Step 3: Close row 97 on the roadmap**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, extend row 97's Notes cell with an **answered** paragraph. Write it from the task reports, not from this plan — the numbers must be the measured ones. It must state, at minimum:

- The engine: `OverlayDefinition` in `src/autoposter/overlays/schema.py`, the `<<variable>>` grammar in `overlays/variables.py`, the generalised draw in `overlays/render.py`, the ladder in `overlays/sources.py`.
- **The parity proof, with its real shape:** all NINE badges (not eight — see the correction below) re-expressed, every existing badge pin passing UNCHANGED, plus a recorded pixel-hash gate; each badge falsified by a transient one-attribute mutation, listed.
- **The count correction:** the phase's own facts sheet (`p-overlay-a-facts.md` C1.2/C2) said "eight hardcoded badges" — the roadmap row itself carries no count, and neither does `docs/research/kometa-overlays.md`. `badges/spec.py`'s `BADGES` has nine — `compose()` iterates eight and `_draw_languages` handles the ninth. All nine are re-expressed.
- **The argv correction:** the phase's own facts sheet said the parity oracle was "byte-identical argv". Badges are Pillow; `render/compositor.py`'s argv pins cover the base composite and were never in scope. The gate is byte-identical pixels instead. State it plainly — a later reader will otherwise look for an argv proof that does not exist.
- **Filed forward, not built:** the queue (`queue`/`queues:`/`dynamic_position`/`overlay_limit`) — no shipped Kometa default uses it, per the probe's own §5.4 honest gap; `font_style`; `blur(NN)`; the `backdrop` name's size-to-canvas arm; `git:`/`repo:` sources; the `old_special_text` alias table; the 27 rating sources' FETCHES (row 100's data half, probe §7.2). Each with its one-line reason.
- **The `BadgeSpec` adapter** in `badges/spec.py` and its eventual removal, as a follow-up.

- [ ] **Step 4: Write the era's first wrap**

Append to `.superpowers/sdd/progress.md` a wrap entry for the overlay era's Phase A. It references the two pending operator checkboxes — **Arr tags** and **MDBList** — as open residuals **by reference only**; do not restate their content (facts C3).

- [ ] **Step 5: Commit and open the PR**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md .superpowers/sdd/progress.md
git commit --no-gpg-sign -m "docs(roadmap): row 97 closes; the queue filed forward, two corrections on record"
git push -u origin feat/overlay-engine
```

Open the PR with a plain body (facts C3): what shipped, the parity proof and its gate, the two corrections (nine badges, pixels-not-argv), and the deferral list. **No AI attribution of any kind**, in the commits or the body.

- [ ] **Step 6: Tear down**

```bash
docker compose -p pov4 down
```

---

## Self-review

**Spec coverage.** Facts C1.1 → T1 (schema) + T2 (the nine re-expressed). C1.2 → T2's per-badge cycles + T1's recorded gate, re-homed from argv onto pixels with the divergence flagged (A1, Global Constraint 3, and a roadmap correction in T4). C1.3 → T1's `variables.py` (four families + modifier sets), `geometry.py`'s three `get_cord` formulas exercised by badges 1/2/5 and pinned unchanged, the three canvases in `overlays/assets.py`, the `-1` sentinel in the schema and in `render.py::_content_size` with the size-to-canvas arm explicitly deferred. C1.4 → the ladder table plus T3's `sources.py`; `git`/`repo` enumerated as not-ours, `url` through `net/guard.py` with A6 flagging the divergence from the facts' "provider-cache/fetch core". C1.5 → group/weight ship (T3's `_resolve_definitions` + its entry-point pin); queue refused by the schema, filed forward by T4. C1.6 → nothing in this plan touches row 100's families, row 50, rows 98/99 or any UI. C2 → all five clauses covered: RED-by-construction per badge, every schema attribute pinned against a cited probe section, the entry-point law at Global Constraint 8 and T3 Steps 6–11, `Field(description=...)` on both new fields, and T1 Step 2 measuring the baseline. C3 → T4 Steps 3–5. C4 → four tasks in the mandated order, `pov1`–`pov4`, branch `feat/overlay-engine` from current `origin/main` (re-verified at execution, not "post-#127").

**Placeholder scan.** No TBD, no "add error handling", no "similar to Task N" — each of the nine badge definitions is written out in full, and each mutation names the exact edit and the exact expected assertion diff. One step deliberately has an *uncertain* expected result and says so rather than guessing: T3 Step 11's first mutation (recorded as a negative result, with the real mutation following). T3 Step 12's `http` client is no longer open-ended — the preflight resolved it: `apply_badges` grows a keyword-only `http=None` parameter, and `process_item` threads the client it already holds.

**Type consistency.** `OverlayDefinition` field names are identical in the schema, all nine builtins, `render.py`, `sources.py`, the config field and the example YAML. `draw_overlay`'s signature is the same in `render.py`, `test_overlay_render.py` and `compose.py`. `resolve_image_path`'s keyword-only `overlays_root`/`http`/`max_bytes` match between `sources.py`, its tests and `pipeline.py`. `BadgeSpec`'s field names (`h_align`, `h_offset`, `box`, `padding`, `radius`, `has_back`) are unchanged, which is what keeps the five pin files unedited. `pixels_sha` and the two hash constants are defined once, in `test_overlay_engine_golden.py`, and imported by `test_overlay_entrypoint.py` rather than re-derived.
