# Phase D — collection-poster title composite (roadmap row 105) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Composite a styled collection title, plus a fixed "COLLECTION" line,
onto the posters this service fetches for the collections it MANAGES, before
they are uploaded to Plex — off by default, and byte-identical to today while
it is off.

**Architecture:** A new sibling config sub-model `collections.poster_title`
(never under `artwork`) and a new **standalone** module
`collections/poster_title.py` that turns poster bytes into poster bytes with
Pillow, reusing `render/textfit.py`'s `prepare_text` and `FitResult` and
`overlays/sources.py`'s two-rung font finder. `collections/posters.py`'s
`apply_poster` calls it in one place — after every poster source rung, before
the sha256 that decides whether anything is uploaded. `render/pipeline.py`'s
`compose_styled`/`_CANVAS`/`art_config_for` are not widened, and no render
fingerprint can move.

**Tech Stack:** Python 3.12+, pydantic v2, Pillow, async SQLAlchemy 2.0 +
asyncpg, httpx, pytest + pytest-asyncio, React 19 + vitest, Docker Compose.
**No new dependency, no Alembic migration, no ImageMagick, no new provider
call.**

**Spec:** `.superpowers/sdd/p-phase-d-facts.md` (the controller's binding
rulings **C1**, adjudications A-1…A-10) and `.superpowers/sdd/p-phase-d-recon.md`
(the evidence those rulings argue from). Executors read both; where the recon
and C1 differ, **C1 wins** — see "Deviations from the recon", below.

---

## Read this first — what governs this plan

1. **Every `file:line` below was read via `git show origin/main:<path>`, not
   from a working tree** — `origin/main` at `26a90e2`. Do not read a mechanism
   site from whatever branch you happen to be standing on. Seven pull requests
   are open against this repository and three of them rewrite
   `render/pipeline.py`.
2. **There is no oracle, and the plan never pretends otherwise.** Kometa does
   not composite collection posters at all, and no captured Posterizarr output
   exists — not on the live Plex server, not in `tests/fixtures/`. The
   operator's Posterizarr *config* is the INPUT side and is transcribed as the
   shipped defaults; it is not a parity proof. Every place this feature is
   described in prose says **"styled after Posterizarr's part, not
   byte-matched"** (adjudication A-5, standing since the era recon's
   adjudication 4 and set as precedent by Phase B).
3. **The single finding that decides the phase (A-1).** `render_version`
   (`config/loader.py:48-55`) hashes `config.artwork.model_dump(mode="json")`
   **wholesale**, plus `library_folders` and the four roots — and its own
   docstring (`:37-40`) names `collections` among the sections it excludes. A
   key added under `artwork` moves `config.version` **at its default value**
   and strands roughly 16,000 stored fingerprints. A key added under
   `collections` structurally cannot. This section goes under `collections`.
4. **What C2b (#154) ships that this plan consumes.** #154 widened
   `overlays/sources.py::resolve_font_path` to `(fonts_root: Path | None,
   value: str, name: str)` and added a second rung, `BUNDLED_FONTS =
   {"Inter-Bold.ttf": …, "Inter-Medium.ttf": …}`, resolving against
   `assets/badges/fonts/`. **This plan requires that rung, and #154 has already
   merged** — `origin/main` at `26a90e2` carries `BUNDLED_FONTS`
   (`overlays/sources.py:90`) — so the branch cuts straight from `origin/main`
   and this plan is a standalone branch with no merge-order dependency.
   Copying that rung's body into `collections/poster_title.py` would still be
   a plan violation: reuse it as an import, never reimplement it.
5. **The one-time cost, stated up front (A-9) — the storm guard.** With the
   gate off nothing changes: every source rung produces the same bytes, the
   sha256 matches `ManagedCollection.poster_sha256`, no collection is uploaded
   — and every managed collection's `definition_hash` is byte-identical to the
   one already stored, so no pass is woken to look in the first place.

   **The rule that bounds the cost, in TWO digests.** One decides whether
   anything is uploaded; the other decides whether the pass gets that far. The
   settings are an input to both, and the second one is what makes the roll-out
   **instant rather than lazy**.

   **(1) `poster_sha256` — what gets uploaded.** The `poster_title` settings
   are PART of the bytes hashed into it, because that digest
   (`collections/posters.py:481-483`) is the compare that decides whether
   anything is uploaded and the compose step sits ABOVE it, so the composited
   bytes are what the digest remembers. The compare is over content, not over a
   config hash, which is what makes it self-correcting rather than per-pass.

   **(2) `definition_hash` — whether `apply_poster` is reached at all.** Every
   caller short-circuits on `definition_current and not (posters_on and
   record.poster_sha256 is None)` — `reconcile.py:1124`, `reconcile.py:869-871`,
   `smart.py:375`, `lists.py:305`; the base suite documents it in
   `tests/test_collection_poster_wiring.py`'s
   `test_a_third_pass_over_an_unchanged_collection_uploads_nothing`. So the
   settings are ALSO an input to the three definition hashes that the managed,
   COMPOSITED collections short-circuit on — `reconcile.definition_hash`
   (`:91-119`), `smart.smart_definition_hash` (`:216-239`) and
   `lists._members_hash` (`:71-93`) — each folding
   `poster_title.poster_title_parts(config)` as a **suffix** term, in the same
   payload position and by the same idiom `lists._settings_parts` already
   occupies in all three. Task 2 Step 3 states the exact code.

   `reconcile.separator_hash` (`:60-88`) is deliberately **not** given the
   term: a divider is never captioned (A-3), so the settings cannot move its
   bytes, and folding them into its hash would buy a summary-and-sort-title
   re-write that changes nothing.

   **The three properties that gives — this is the whole of A-9.**
   - **Gate ON ⇒ every managed collection's `definition_hash` moves**, so the
     very NEXT pass reaches `apply_poster` for each of them, composites, finds
     new bytes, uploads **once**, and stores both digests. The pass after that
     short-circuits again. One pass — not "whenever something else happens to
     move the definition".
   - **Gate OFF ⇒ the hash is byte-identical to today's.**
     `poster_title_parts` returns `[]` on an explicit `if not settings.enabled:`,
     so the settings' dump never enters the payload and every hash already
     stored on every live server still matches. This is structural, not an
     arithmetic accident about what the defaults happen to dump to: an operator
     may retune every box with the gate off and re-reconcile nothing.
   - **A text knob changed while the gate is on ⇒ the dump moves ⇒ the same
     single re-upload, once**, on the next pass.

   **What the gate-on pass costs beyond the upload, stated rather than
   hidden.** A moved `definition_hash` makes `definition_current` false, so
   that one pass also re-PUTs the collection's filter (or re-confirms its
   membership) and re-writes its summary, sort title and ride-along settings.
   Every one of those is an idempotent write of the value already there, and it
   is the same one-pass cost row 49's `poster_key` term and phase 10a-2's `url`
   term each already charged for the same reason — both said so in
   `separator_hash`'s and `definition_hash`'s own docstrings. It happens once,
   on the pass after the operator saves, and never again.

   Task 2 Step 1 pins all three. Two run through the real reconciler —
   `test_the_gate_on_re_uploads_each_managed_poster_exactly_once` (with
   **nothing nulled**: the gate flip alone is what reaches `apply_poster`, and
   dropping the suffix term turns it red) and
   `test_a_changed_text_knob_re_uploads_each_managed_poster_exactly_once`. The
   third, `test_the_gate_off_leaves_every_definition_hash_byte_identical`,
   calls the three hash functions directly, because they ARE the mechanism and
   the property is that they do not move.
   Disclosed in the row cell, in the field description, in the example config
   and in the PR body.

---

## Branch, cut point and merge order

- Worktree: **`D:\Sites\autoposter-phased`**, branch
  **`feat/phase-d-collection-title`**, cut from **`origin/main`**.
- **The cut ref is `origin/main`.** Measured this session: `origin/main` =
  `26a90e2` and carries `BUNDLED_FONTS` (`overlays/sources.py:90`) — #154
  merged at `0260188`, before this session started, so this branch cuts
  straight from `origin/main`. Task 1 Step 1 re-verifies this at execution
  time before doing anything else.
- **Standalone branch off `main`; no merge-order dependency.** This PR's body
  says so as its first line — there is no earlier PR this one stacks on.
- **The one real code contention is `config/schema.py` with #152**
  (`feat/playlists-98b`), and it is textually distant: #152 adds
  `PlaylistsConfig` around `schema.py:2321`; this plan adds a model at the END
  of `TitleCardConfig` (which spans `:368-414` on `origin/main`, with
  `class ArtworkConfig` next at `:417`) and one field inside `CollectionsConfig`
  at `:1809-1812`. `frontend/src/api/types.ts` is contended by several open
  branches; the edit here is one optional field appended to one interface.
- **This plan file is committed by Task 1, Step 3.** It is an untracked
  working-tree file until then.

---

## Global Constraints

Every task's requirements implicitly include this section. These are law.

1. **Nothing under `render/pipeline.py`.** `compose_styled`, `_CANVAS` and
   `art_config_for` are not read into this feature and not edited
   (adjudication A-8a). Also untouched: `overlays/` (except as an IMPORT of
   `resolve_font_path`/`OverlaySourceError`), `badges/`, `scheduler/merge.py`,
   `api/action_center.py`. If a step would edit one of those files, stop.
2. **No migration.** The composited bytes are hashed into the existing
   `ManagedCollection.poster_sha256` (`db/models.py:469`) by the same
   `hashlib.sha256(data)` at `collections/posters.py:481` — which is *why* the
   compose step must sit above that line. No column is added, no Alembic
   revision is written.
3. **No render fingerprint moves.** `config/loader.py` and
   `render/pipeline.py::compute_fingerprint` are not edited, and Task 3 Step 1
   proves it with `git diff --stat`. Task 1 pins it behaviourally as well:
   `render_version(config)` is identical with the gate off, with the gate on,
   and with every knob in the new section changed.
4. **Managed collections only, and only the posters this service fetches or
   generates** (A-2). `apply_local_posters_to_unmanaged`
   (`collections/posters.py:523-600`) is not edited. An operator's own file
   under `assets_root` — which is also where `api/manual.py`'s poster endpoint
   writes — passes through untouched.
5. **A divider is never captioned twice** (A-3). Generated separator art
   (`collections/separator_art.py`) bakes the divider's title into the image,
   and upstream's `separators/<style>/<stem>.jpg` carries the same word. Both
   are excluded, by a NAMED condition rather than as an accident of ordering.
6. **Clamp, do not refuse** (A-6). A title that will not fit above
   `min_point_size` is drawn at the floor with a WARNING, not abandoned. The
   ONE refusal is an empty title. This is a deliberate divergence from
   `separator_art._render`, argued in the new module's docstring.
7. **Served strings are class-name-only.** Roadmap row 213: an action string
   that reaches the run report never carries
   `f"{type(exc).__name__}: {exc}"` for an unmarked exception. This module's
   own `CollectionTitleRefused` is written by us, carries only the collection
   title and the configured font NAME, and may be interpolated — exactly as
   `PosterPathRefused` already is at `posters.py:401-407`. Anything else is
   caught by a bare `except Exception`, logged with `logger.exception`, and
   reported as a fixed sentence with no exception text in it.
8. **A compose failure is cosmetic and never fails the pass.** It uploads
   nothing and leaves `poster_sha256` exactly where it was, so the next pass
   retries — the same posture `apply_poster`'s docstring (`:393-395`) and
   `ensure_separator_art` (`:202-209`) already hold.
9. **Every gated feature gets at least one test through the REAL entry
   point.** For the composite that is `reconcile_content_ratings` and
   `engine._separators` (never `compose_collection_title` alone); for the
   impact row it is `POST /api/config/preview`. This repository has twice
   shipped a gated feature whose helper tests passed while the wired path
   differed.
10. **Pillow only. No test in this plan invokes `magick`, so no test in this
    plan carries `@pytest.mark.imagemagick`.** If a step ever does reach
    `render/compositor.py` or `textfit.fit_point_size`, that test MUST carry
    the marker — CI's main run is `-m "not imagemagick and not deep"` on a
    runner with no `magick` binary.
11. **RED-first.** Every behaviour lands test-first: write the failing test,
    run it, read the failure, then implement. A test never seen failing proves
    nothing.
12. **Container discipline.** One compose project (**`pphd`**), every backend
    command with **both** `-f` files. The overlay `.superpowers/isolated-db.yml`
    is gitignored and is copied into the worktree **by absolute path** from the
    main checkout. **Never `--rm`**: name the container, read the teed log
    file, then `docker rm`. Teardown is `docker compose -p pphd down` —
    **never `down -v`**.
13. **Test selection is by node id**, never by `-k` substring.
14. **The count chain.** `B` is the baseline PASSED count measured in Task 1
    Step 2. This plan adds **27** pytest tests and **2** vitest tests, summed
    from its own code blocks: Task 1 writes **15** in
    `tests/test_collection_poster_title.py` (3 in Step 4 + 12 in Step 9), Task 2
    writes **8** pins in `tests/test_collection_poster_wiring.py` (6 through the
    real reconcilers, 1 more through them for the changed-knob property, and 1
    over the three definition-hash functions) + **3** impact pins + **1**
    endpoint pin + the 2 vitest, Task 3 writes none. So: Task 1 ends at
    **`B + 15`**. Task 2 ends at **`B + 27`**. Task 3 ends at **`B + 27`** and 2
    new vitest tests. Any other number means a test elsewhere moved — find it
    before committing; never adjust the expectation.
15. **Commits** are conventional, staged **by name** (never `git add -A`,
    `-u`, or `.`), and carry **no AI attribution of any kind** — no
    `Co-Authored-By`, no "Generated with", no tool mention. Same for the PR
    body. If signing hangs, retry the same command with `--no-gpg-sign`
    appended rather than skipping the commit.

---

## Established facts

Every one of these was read this session from `origin/main` at `26a90e2`
unless another ref is named.

**The insertion point.**
`collections/posters.py::apply_poster:350-496` resolves bytes through four
rungs — the operator's local override `:400-417`, a file some source already
produced `:418-451` (the caller's generated separator art, or a cached
`Default-Images` family poster), the source `kind` names `:452-479` (a hosted
default or a TMDb profile photo), nothing fourth — then hashes at **`:481`**,
compares against `record.poster_sha256` at `:482-483`, reports a dry run at
`:485-486` and uploads at `:488-492`. **The compose step goes between `:479`
and `:481`.**

**The storm guard.** `config/loader.py::render_version:48-55` hashes
`{"artwork": config.artwork.model_dump(mode="json"), "library_folders": …,
"assets_root": …, "manual_assets_root": …, "fonts_root": …, "overlays_root":
…}`. Its docstring at `:37-40` lists `collections` among the excluded
sections. `render/pipeline.py::compute_fingerprint` takes `config_version` as
its first part. Nothing in this plan touches either file.

**The precedent module.** `collections/separator_art.py` is the shipped
one-block version of this: a `TextStyle` transcribed from upstream at `:96-106`,
`_render` at `:147-178` (copy base → `prepare_text` → `fit_point_size` →
refuse if truncated → `build_text_argv` → `-strip -quality 100`), and
`ensure_separator_art` at `:181-210` whose every failure path is
`logger.exception(...)` + `return None`. It uses **ImageMagick**; this plan
uses **Pillow** (Global Constraint 10 and the new module's docstring).

**The text machinery this plan reuses.** `render/textfit.py:26-61`
`prepare_text(text, style)` — quote normalisation, `newline_words`, then
`newline_on_symbols`, then `all_caps`; pure, no subprocess.
`render/textfit.py:20-23` `FitResult(point_size: int, truncated: bool)`,
frozen. `render/textfit.py:125-143` `fit_point_size` shells out to `magick`
and is **not** used here.

**The font rung (shipped on `origin/main` by #154).**
`overlays/sources.py:90-93` `BUNDLED_FONTS: dict[str, Path]` maps the exact
strings `"Inter-Bold.ttf"` and `"Inter-Medium.ttf"`;
`overlays/sources.py:96-133` `resolve_font_path(fonts_root: Path | None,
value: str, name: str) -> Path` tries `_confined(fonts_root, value, name)`
first and the bundle second, raising `OverlaySourceError` when neither
answers. Both font files (`assets/badges/fonts/Inter-Bold.ttf`,
`assets/badges/fonts/Inter-Medium.ttf`) are already committed on
`origin/main`.

**The config shapes.** `config/schema.py:171-258` `TextStyle` — `font`,
`all_caps`, `font_color`, `min_point_size`, `max_point_size`, `max_width`,
`max_height`, `text_offset` (a signed string, enforced by `_must_carry_sign`
at `:250-258`), `gravity`, `line_spacing`, `add_text`, `add_stroke`,
`stroke_color`, `stroke_width`, `newline_on_symbols`, `newline_words`. Five of
those have no default and must be supplied. `config/schema.py:261-365`
`ArtKindConfig` — **not** subclassed here (A-8b): `language_order`,
`min_width`, `min_height`, `skip_local_text_add`, `disable_online_asset_fetch`
and `skip_add_text_when_with_text` mean nothing for a collection poster and
would be advertised in the served field map for nothing.
`config/schema.py:368-414` `TitleCardConfig(ArtKindConfig)` is the existing
two-text-block shape and is the model to sit beside. **It ends at `:414`, not
at `:390`** — `:386-393` is the `skip_words` field, `:397-404` is
`skip_cjk_titles` and `:407-414` is `season_name_overrides`, whose closing `)`
is the class's last line. The next class is `class ArtworkConfig(BaseModel)` at
`:417`, and **that is the anchor the insertion uses**: an offset counted from
`:390` splits `skip_words`' `Field(...)` call and re-parents two fields onto the
new model.
`config/schema.py:1667-1855` `CollectionsConfig`; `posters: bool` is at
`:1809-1812`.

**The Posterizarr values, transcribed** (`posterizarr-config.txt`, gitignored
at repo root per `.gitignore:2`; re-read this session at `:274-289` and
`:310-328`, agreeing with phase 5b's transcription):

| Block | Values, verbatim |
|---|---|
| `CollectionPosterOverlayPart` (`:310-328`) | `fontAllCaps true`, `AddBorder true`, `AddText true`, `AddTextStroke false`, `strokecolor black`, `strokewidth 6`, `AddOverlay true`, `fontcolor white`, `bordercolor black`, `minPointSize 100`, `maxPointSize 250`, `borderwidth 30`, `MaxWidth 1900`, `MaxHeight 500`, `text_offset "+300"`, `lineSpacing 0`, `TextGravity south` |
| `CollectionTitlePosterPart` (`:274-289`) | `fontAllCaps true`, `fontcolor white`, `minPointSize 83`, `maxPointSize 250`, `strokecolor black`, `strokewidth 6`, `lineSpacing 0`, `AddCollectionTitle true`, `CollectionTitle "COLLECTION"`, `AddTextStroke false`, `MaxWidth 1200`, `MaxHeight 485`, `text_offset "+300"`, `TextGravity south` |
| Fonts (`:92`) | `collectionfont "Colus-Regular.ttf"` — **not** in `assets/fonts/`, which holds `Comfortaa-Medium.ttf`, `OFL.txt`, `PROVENANCE.md`, `.keep` |

**Three values in the shipped defaults are OURS, not transcribed, and each is
disclosed where it appears.** (a) The default `font` is `Inter-Medium.ttf`,
not `Colus-Regular.ttf`: Colus is not vendored (A-4 — licence unverified, and
with no oracle the exact bytes buy nothing verifiable), so shipping its name
as the default would make the feature refuse itself on every deployment.
(b) and (c) The `collection_line`'s `text_offset` (`+120`, not `+300`) and its
box/point-size range (`1200x150`, 40–90pt, not `1200x485`, 83–250pt): the
operator's file gives BOTH parts `text_offset "+300"` because they are two
different Posterizarr poster types, not two blocks on one image — shipping
both at `+300` would draw the fixed line straight through the title. There is
no oracle that says how upstream stacks them, so the shipped default is chosen
to be legible and is named as ours.

**Not implemented, and deliberately without a key** (so nothing advertises a
setting that does nothing): `AddBorder`/`bordercolor`/`borderwidth` and
`AddOverlay`/`collectionoverlayfile` from `CollectionPosterOverlayPart`. Named
in the row cell as a disclosed gap.

**The three poster surfaces, and which one this touches.** `apply_poster`
(managed collections, `posters.py:350`) — **the only one wired**.
`apply_local_posters_to_unmanaged` (`:523-600`, whose own docstring says "an
unmanaged collection's poster field is not ours to claim") — untouched.
`api/manual.py::install_collection_poster` — untouched; it writes the
operator's file to `poster_override_target`, which `apply_poster` reads as its
LOCAL rung, and the local rung is excluded.

**The separator kind.** `hosted_poster_url:145-155` handles
`kind == "separator"` with a style-scoped `"<style>:<stem>"` key, returning
`None` for a stem starting with `@` (generated art). Both the fetched
`separators/<style>/<stem>.jpg` and the generated `@base` composite carry the
group's name already.

**The impact preview's silence (A-7).** `config/impact.py:50`
`_ART_KINDS = frozenset({"poster", "season_poster", "background",
"title_card"})` and `_walk:162-185` selects from `Render` joined to
`MediaItem`. There is no `renders` row for a collection, so a
`collections.poster_title` edit previews as `impact: null` (via
`api/routes.py::_render_affecting:1588-1604`) while up to 325 collection
posters would be re-composited. `api/routes.py::preview_config_overrides:1890-1923`
is where the additive row goes.

**Test beds that already exist.** `tests/test_collection_poster_wiring.py`
drives `reconcile_content_ratings` and `engine._separators` against a
`RatingSection`/`RatingCollection` pair whose `uploadPoster` records the
uploaded bytes (`:149-155`) — Global Constraint 9's real entry point.
`tests/test_config_impact.py` carries `base_document`/`config`/`variant`
fixtures over the example config (`:46-79`). `tests/test_api_config_editor.py`
is in `tests/conftest.py`'s `DEEP_SUITES` (`:55`), so it is deselected on the
pull-request lane and runs in the container's plain `pytest -q`.
`tests/conftest.py:382-391` `config_factory(**overrides)` builds a `Config`
from the example file.

---

## File Structure

| File | Change |
|---|---|
| `src/autoposter/config/schema.py` | **modify** — new `CollectionPosterTitleConfig` + `_COLLECTION_GRAVITIES` beside `TitleCardConfig`, inserted **before `class ArtworkConfig` (`:417`)** and never at a counted offset; new `poster_title` field in `CollectionsConfig` after `posters` (`:1812`) |
| `src/autoposter/collections/poster_title.py` | **new leaf module** — `CollectionTitleRefused`, `REFERENCE_WIDTH`, `compose_collection_title(settings, fonts_root, data, title) -> bytes`, `poster_title_parts(config) -> list[str]` (Task 2), and the private `_font`/`_wrap`/`_break_word`/`_block_height`/`_fit`/`_draw`/`_scaled`/`_scaled_offset` |
| `src/autoposter/collections/posters.py` | **modify** — `SEPARATOR_KIND` constant beside `TMDB_PROFILE_KIND` (`:49`), used at `:145`; a `composable` flag set on each source rung; the compose step between `:479` and `:481` |
| `src/autoposter/collections/reconcile.py` | **modify** — one import; `definition_hash` (`:91-119`) gains a trailing `config=None` and folds `poster_title_parts(config)` as a suffix; its call site at `:1114` passes `config`. `separator_hash` is **not** touched |
| `src/autoposter/collections/smart.py` | **modify** — one import; `smart_definition_hash` (`:216-239`) gains the same trailing `config=None` and the same suffix; its call site at `:366` passes `config` |
| `src/autoposter/collections/lists.py` | **modify** — one import; `_members_hash` (`:71-93`) gains the same trailing `config=None` and the same suffix; its call site at `:279` passes `config`. Its second production call site, `collections/playlists.py:572`, is **untouched** — a playlist has no composited poster, the added parameter defaults to `None` there, and `poster_title_parts(None)` is `[]`, so that digest is unchanged |
| `src/autoposter/config/impact.py` | **modify** — `count_collection_posters(session, before, after) -> int` appended; two imports widened |
| `src/autoposter/api/routes.py` | **modify** — `preview_config_overrides` gains a `collection_posters` key; one import widened at `:48` |
| `frontend/src/api/types.ts` | **modify** — optional `collection_posters` on `ConfigPreviewResponse` (`:752-754`; anchor on the type name) |
| `frontend/src/pages/Settings.tsx` | **modify** — `ImpactReport` takes `collectionPosters` and renders one sentence in both arms (`:510-555`, call site `:809`) |
| `config/autoposter.example.yaml` | **modify** — a commented `poster_title:` block under `collections:` after `posters: true`, at `:167` on `origin/main` |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **modify** — row 105's cell (`:207`) files and closes |
| `tests/test_collection_poster_title.py` | **new** — 15 unit pins (3 config + 12 module) |
| `tests/test_collection_poster_wiring.py` | **modify** — 6 pins appended, through the real reconcilers |
| `tests/test_config_impact.py` | **modify** — 3 pins appended |
| `tests/test_api_config_editor.py` | **modify** — 1 pin appended, through `POST /api/config/preview` |
| `frontend/src/pages/Settings.test.tsx` | **modify** — 2 pins appended |

---

## Task 1: the config section and the compose module, no wiring

**Files:**
- Modify: `src/autoposter/config/schema.py` — before `class ArtworkConfig`
  (`:417`, found by grep, never by a counted offset) and after the `posters`
  field in `CollectionsConfig` (`:1812`)
- Create: `src/autoposter/collections/poster_title.py`
- Test: `tests/test_collection_poster_title.py`

**Interfaces:**
- Produces: `config.schema.CollectionPosterTitleConfig` with fields
  `enabled: bool`, `title: TextStyle`, `collection_line: TextStyle`,
  `collection_line_text: str`; reachable as
  `config.collections.poster_title`.
- Produces: `collections.poster_title.compose_collection_title(settings:
  CollectionPosterTitleConfig, fonts_root: Path | None, data: bytes, title:
  str) -> bytes` — blocking; returns JPEG bytes.
- Produces: `collections.poster_title.CollectionTitleRefused(Exception)` and
  `collections.poster_title.REFERENCE_WIDTH = 2000`.
- Consumes: `overlays.sources.resolve_font_path(fonts_root: Path | None,
  value: str, name: str) -> Path` and `overlays.sources.OverlaySourceError`,
  both as widened by #154 and therefore present on `origin/main`;
  `render.textfit.prepare_text` and `render.textfit.FitResult`, which #154
  does not touch.

---

- [ ] **Step 1: Verify the cut point and the C2b font rung**

**The cut ref is `origin/main`.** Verify every anchor against it before doing
anything else — a plan that reads its mechanism sites from a ref it is not
cutting from is the failure mode this whole step exists to prevent:

```bash
git fetch origin
git rev-parse origin/main
git show origin/main:src/autoposter/collections/posters.py | grep -q "digest = hashlib.sha256(data).hexdigest()" && echo INSERTION-POINT-PRESENT
git show origin/main:src/autoposter/config/loader.py | grep -q '"artwork": config.artwork.model_dump' && echo RENDER-VERSION-UNCHANGED
git show origin/main:src/autoposter/config/schema.py | grep -n "^class TitleCardConfig\|^class ArtworkConfig"
git show origin/main:src/autoposter/config/schema.py | grep -q "poster_title" && echo ALREADY-DONE-STOP
git show origin/main:src/autoposter/overlays/sources.py | grep -q "BUNDLED_FONTS" && echo CUT-REF-RUNG-PRESENT
git show origin/main:config/autoposter.example.yaml | grep -n "posters: true"
```

**Writing a second copy of the two-rung font finder inside
`collections/poster_title.py` is a plan violation.** There is one answer in
this codebase to "where does a configured font come from"; do not make it two
— import `overlays.sources.resolve_font_path`.

Expected: `INSERTION-POINT-PRESENT`, `RENDER-VERSION-UNCHANGED` and
`CUT-REF-RUNG-PRESENT` all print; `ALREADY-DONE-STOP` does **not**; the class
grep prints `368:class TitleCardConfig(ArtKindConfig)` and
`417:class ArtworkConfig(BaseModel)`; and the example-config grep prints
`167:  posters: true …`.

- **If `ALREADY-DONE-STOP` prints, STOP** — somebody has already landed this
  section and the plan is stale.
- **If `CUT-REF-RUNG-PRESENT` does not print, STOP** — `origin/main` no longer
  carries the bundled-font rung this plan requires; re-verify #154 actually
  merged before going any further, since every font-resolution assumption in
  Task 1 depends on it.
- **If the two class line numbers differ from `368`/`417`, do not adjust an
  offset — Step 6 anchors on `class ArtworkConfig` by grep and needs no line
  number at all.** Record the new numbers and carry on.

- [ ] **Step 2: Create the worktree, bootstrap `.superpowers/`, and measure the baseline**

Cut from `origin/main`, the ref Step 1 verified:

```bash
git worktree add ../autoposter-phased -b feat/phase-d-collection-title origin/main
cd ../autoposter-phased
git rev-parse HEAD
```

Record the resolved `HEAD` sha in the task report — it must be `26a90e2…`.
**Do not `git checkout` in the main checkout** — it holds other work.

**Shell state does not survive between tool calls in this harness; only the
working directory does.** A `MAIN=$(pwd)` set here would be empty by the next
command, and `cp "$MAIN/…" …` would then silently read from `/…`. So the main
checkout is written out as the absolute path **`D:\Sites\autoposter`** at every
single dereference below — spelled `D:/Sites/autoposter` in the commands, the
same absolute path with forward slashes so it runs verbatim under both the Bash
and the PowerShell tool. There is no `$MAIN` in this plan.

`.superpowers/` is gitignored, so the fresh worktree has none of it — no
compose overlay, and no destination for the teed logs. Copy the overlay in
**by absolute path**:

```bash
mkdir -p .superpowers
cp "D:/Sites/autoposter/.superpowers/isolated-db.yml" .superpowers/isolated-db.yml
cat .superpowers/isolated-db.yml
```

Expected: the small overlay prints (`services: postgres: ports: !reset null`
— what stops this database fighting the main checkout's for port 5432).
**If the copy fails, STOP** — every later container command depends on it.

Now measure the baseline. Do not assume the number:

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphd-baseline test sh -c \
  'set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pphd-baseline.log'
docker wait pphd-baseline
docker cp pphd-baseline:/app/.superpowers/run-pphd-baseline.log .superpowers/run-pphd-baseline.log
docker rm pphd-baseline
tail -5 .superpowers/run-pphd-baseline.log
```

Expected: a green summary line. **Read the log file — never trust `docker
wait`'s exit code alone.** Record `B=<passed>` and the skip count in the task
report. Every later suite run is compared against `B` plus the tests added so
far (Global Constraint 14).

- [ ] **Step 3: Commit this plan file**

```bash
mkdir -p docs/superpowers/plans
cp "D:/Sites/autoposter/docs/superpowers/plans/2026-09-05-phase-d-collection-title.md" \
   docs/superpowers/plans/2026-09-05-phase-d-collection-title.md
git add docs/superpowers/plans/2026-09-05-phase-d-collection-title.md
git commit -m "docs(collections): the phase D plan for the collection-poster title composite"
```

- [ ] **Step 4: Write the failing config tests**

Create `tests/test_collection_poster_title.py` with the header and the first
three tests:

```python
"""``collections.poster_title`` and ``collections/poster_title.py`` (row 105).

Pillow only. Nothing in this file invokes ``magick``, so nothing in it carries
``@pytest.mark.imagemagick`` -- CI's main run deselects that marker on a runner
with no binary, and a composite test that silently never ran would be worse
than no composite test.

The first test in the file is the storm guard, and it is the reason the section
lives under ``collections`` rather than under ``artwork``: ``render_version``
(``config/loader.py:48-55``) hashes ``config.artwork.model_dump()`` WHOLESALE,
so a key added under ``artwork`` moves ``config.version`` at its own default
value and strands every stored render fingerprint in the library.
"""
import io
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from autoposter.assets import asset_path
from autoposter.collections.poster_title import (
    CollectionTitleRefused,
    REFERENCE_WIDTH,
    compose_collection_title,
)
from autoposter.config.loader import render_version
from autoposter.config.schema import CollectionPosterTitleConfig

# A face that is committed to this repository and is reachable with no
# ``fonts_root`` at all, so these tests never depend on an operator mount.
BUNDLED = "Inter-Medium.ttf"


def _poster(width: int = 2000, height: int = 3000, colour: str = "navy") -> bytes:
    """A plain JPEG poster of a given size. Plain rather than noisy so that
    "some pixels changed" is unambiguously the text and not the encoder."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="JPEG", quality=100)
    return buffer.getvalue()


def _settings(**overrides) -> CollectionPosterTitleConfig:
    return CollectionPosterTitleConfig(**overrides)


def test_the_section_moves_no_render_version(config_factory):
    """The whole storm argument, in one assertion.

    Not "the default happens to be off": ``collections`` is excluded from
    ``render_version``'s hashed dict by construction (its docstring names the
    section at ``loader.py:37-40``), so turning the gate ON and changing every
    knob in it must still leave the value digit-for-digit identical.
    """
    config = config_factory()
    before = render_version(config)

    config.collections.poster_title.enabled = True
    config.collections.poster_title.collection_line_text = "SET"
    config.collections.poster_title.title.font = "Other-Font.ttf"
    config.collections.poster_title.title.min_point_size = 11
    config.collections.poster_title.title.max_point_size = 12
    config.collections.poster_title.title.max_width = 13
    config.collections.poster_title.title.max_height = 14
    config.collections.poster_title.title.text_offset = "-15"
    config.collections.poster_title.title.gravity = "north"
    config.collections.poster_title.collection_line.max_width = 16

    assert render_version(config) == before


def test_the_default_is_off_and_carries_the_transcribed_values(config_factory):
    """The shipped defaults, pinned against the operator's own Posterizarr file.

    ``posterizarr-config.txt:310-328`` (CollectionPosterOverlayPart) for the
    title block; ``:274-289`` (CollectionTitlePosterPart) for the fixed line's
    colour, stroke, caps and lineSpacing and for the word itself. The three
    values that are OURS rather than transcribed -- the font name, and the
    fixed line's offset and box -- are asserted in the second half, so a future
    edit cannot quietly promote one of them to "upstream's".
    """
    settings = config_factory().collections.poster_title

    assert settings.enabled is False
    assert settings.collection_line_text == "COLLECTION"
    # Transcribed, CollectionPosterOverlayPart.
    assert settings.title.all_caps is True
    assert settings.title.font_color == "white"
    assert settings.title.min_point_size == 100
    assert settings.title.max_point_size == 250
    assert settings.title.max_width == 1900
    assert settings.title.max_height == 500
    assert settings.title.text_offset == "+300"
    assert settings.title.gravity == "south"
    assert settings.title.line_spacing == 0
    assert settings.title.add_stroke is False
    assert settings.title.stroke_color == "black"
    assert settings.title.stroke_width == 6
    # Ours, and named as ours: Colus-Regular.ttf is not vendored, and both
    # Posterizarr parts carry "+300" because they are two poster TYPES rather
    # than two blocks on one image.
    assert settings.title.font == BUNDLED
    assert settings.collection_line.font == BUNDLED
    assert settings.collection_line.text_offset == "+120"
    assert settings.collection_line.max_width == 1200
    assert settings.collection_line.max_height == 150
    assert settings.collection_line.min_point_size == 40
    assert settings.collection_line.max_point_size == 90


def test_a_gravity_this_module_cannot_anchor_is_refused_by_name():
    """``TextStyle.gravity`` is an ImageMagick vocabulary and this module draws
    with Pillow, which anchors vertically only. A value it cannot honour is a
    loud refusal at config-validation time rather than a block drawn somewhere
    the operator did not ask for."""
    with pytest.raises(ValueError) as excinfo:
        CollectionPosterTitleConfig(
            collection_line={
                "font": BUNDLED, "min_point_size": 40, "max_point_size": 90,
                "max_width": 1200, "max_height": 150, "text_offset": "+120",
                "gravity": "southeast",
            },
        )
    assert "collections.poster_title.collection_line.gravity" in str(excinfo.value)
    assert "'southeast'" in str(excinfo.value)
```

- [ ] **Step 5: Run the config tests to verify they fail**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t1red1 test sh -c \
  'set -o pipefail; pytest tests/test_collection_poster_title.py -q 2>&1 | tee /app/.superpowers/run-pphd-t1red1.log'
docker cp pphd-t1red1:/app/.superpowers/run-pphd-t1red1.log .superpowers/run-pphd-t1red1.log
docker rm pphd-t1red1
tail -20 .superpowers/run-pphd-t1red1.log
```

Expected: a **collection error** — `ImportError: cannot import name
'CollectionPosterTitleConfig' from 'autoposter.config.schema'`. That is the
right RED for this step: neither the model nor the module exists yet.

- [ ] **Step 6: Add the config model**

**Anchor this insertion semantically, never by a line number.** `TitleCardConfig`
does not end at `:390` — `:390` is inside its `skip_words` field, and an
insertion there splits a `Field(...)` call and re-parents `skip_cjk_titles` and
`season_name_overrides` onto the new model. Find the END of the class by finding
the START of the next one:

```bash
grep -n "^class ArtworkConfig" src/autoposter/config/schema.py
```

Expected: `417:class ArtworkConfig(BaseModel):`. **Insert immediately BEFORE
that line** (leaving the two blank lines that separate top-level definitions),
whatever number it prints — the last line of `TitleCardConfig` is the closing
`)` of `season_name_overrides` just above it. Insert:

```python
# The three vertical anchors ``collections/poster_title.py`` can honour.
# ``TextStyle.gravity`` is an ImageMagick vocabulary shared with the render
# pipeline, which composites through ``magick`` and takes all nine; the
# collection-poster composite draws with Pillow and centres horizontally, so
# it anchors vertically only. Named here rather than in the drawing module so
# the refusal happens when the operator SAVES, not when a pass runs.
_COLLECTION_GRAVITIES = frozenset({"north", "center", "south"})


class CollectionPosterTitleConfig(BaseModel):
    """A styled title, plus a fixed second line, drawn onto a managed
    collection's poster before it is uploaded (roadmap row 105).

    **Where this lives is the whole safety argument.** ``config/loader.py``'s
    ``render_version`` hashes ``config.artwork.model_dump(mode="json")``
    wholesale and its docstring names ``collections`` among the sections it
    excludes, so a key here cannot move ``config.version`` and cannot strand a
    single stored render fingerprint -- while the same key under ``artwork``
    would strand roughly 16,000 of them at its own default value.

    **A sibling of ``ArtKindConfig``, not a subclass.** ``language_order``,
    ``min_width``/``min_height``, ``skip_local_text_add``,
    ``disable_online_asset_fetch`` and ``skip_add_text_when_with_text`` mean
    nothing for a collection poster, and this model is walked by
    ``config/descriptions.py`` into the map the Settings page renders -- so
    subclassing would advertise six settings that do nothing.

    **Styled after Posterizarr's parts, not byte-matched to them.** The
    defaults are the operator's own Posterizarr values
    (``CollectionPosterOverlayPart`` for the title block,
    ``CollectionTitlePosterPart`` for the fixed line's colour, stroke, caps and
    wording), written against a 2000-pixel-wide poster and scaled at draw time
    to whatever poster is actually fetched. No captured Posterizarr output
    exists anywhere, so there is no oracle to prove parity against and none is
    claimed. Three defaults are OURS and are called out on their fields: the
    font name, and the fixed line's offset and box.

    ``AddBorder`` and ``AddOverlay`` from ``CollectionPosterOverlayPart`` are
    not implemented and deliberately have no key here.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Draw the collection's title, plus the fixed collection_line_text, "
            "onto every managed collection poster this service fetches, before "
            "uploading it. Off is byte-identical to not having this feature. "
            "Turning it on re-uploads each affected collection's poster once, "
            "on the next pass, and then settles; a poster you placed yourself, "
            "a divider's art and any collection this service does not manage "
            "are never touched."
        ),
    )
    title: TextStyle = Field(
        default_factory=lambda: TextStyle(
            font="Inter-Medium.ttf",
            all_caps=True,
            font_color="white",
            min_point_size=100,
            max_point_size=250,
            max_width=1900,
            max_height=500,
            text_offset="+300",
            gravity="south",
            line_spacing=0,
            add_text=True,
            add_stroke=False,
            stroke_color="black",
            stroke_width=6,
        ),
        description=(
            "The collection's own title block. Its sizes and offsets are in "
            "pixels against a 2000-pixel-wide poster and are scaled to the "
            "poster actually fetched. The default font is a bundled face, not "
            "Posterizarr's Colus-Regular.ttf, which this service does not ship "
            "-- put your own copy under fonts_root and name it here to use it."
        ),
    )
    collection_line: TextStyle = Field(
        default_factory=lambda: TextStyle(
            font="Inter-Medium.ttf",
            all_caps=True,
            font_color="white",
            min_point_size=40,
            max_point_size=90,
            max_width=1200,
            max_height=150,
            text_offset="+120",
            gravity="south",
            line_spacing=0,
            add_text=True,
            add_stroke=False,
            stroke_color="black",
            stroke_width=6,
        ),
        description=(
            "The fixed second line's own block. Its colour, stroke, caps and "
            "line spacing are Posterizarr's; its offset and box are ours, "
            "because Posterizarr gives both of its parts the same offset -- "
            "they are two poster types there, not two blocks on one image, so "
            "copying both would draw this line through the title. Set add_text "
            "false to draw the title alone."
        ),
    )
    collection_line_text: str = Field(
        default="COLLECTION",
        description=(
            "The fixed second line printed with every collection title, "
            "Posterizarr's CollectionTitle. Empty draws no second line at all."
        ),
    )

    @model_validator(mode="after")
    def _gravities_must_be_drawable(self) -> "CollectionPosterTitleConfig":
        """Refuse a gravity the Pillow composite cannot anchor.

        A saved config with ``southeast`` here would otherwise draw every
        collection's title somewhere the operator did not ask for, on every
        managed collection, and be discovered by looking at Plex.
        """
        for name, style in (
            ("title", self.title),
            ("collection_line", self.collection_line),
        ):
            if style.gravity not in _COLLECTION_GRAVITIES:
                raise ValueError(
                    f"collections.poster_title.{name}.gravity {style.gravity!r} is "
                    "not one of 'north', 'center', 'south': the collection-poster "
                    "composite draws with Pillow and anchors its blocks "
                    "vertically only"
                )
        return self
```

Then, in `CollectionsConfig`, immediately **after** the `posters` field (which
ends at `:1812`), insert:

```python
    # Roadmap row 105. A sibling sub-model, and deliberately here rather than
    # under `artwork`: render_version hashes the artwork section wholesale
    # (config/loader.py:48-55) and excludes `collections`, so nothing in here
    # can move a stored render fingerprint. Off by default; see the model.
    poster_title: CollectionPosterTitleConfig = Field(
        default_factory=CollectionPosterTitleConfig,
        description=(
            "Draw a styled collection title, plus a fixed second line, onto "
            "every managed collection poster this service fetches, before "
            "uploading it. Off by default."
        ),
    )
```

- [ ] **Step 7: Run the config tests to verify the first three pass**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t1green1 test sh -c \
  'set -o pipefail; pytest tests/test_collection_poster_title.py -q 2>&1 | tee /app/.superpowers/run-pphd-t1green1.log'
docker cp pphd-t1green1:/app/.superpowers/run-pphd-t1green1.log .superpowers/run-pphd-t1green1.log
docker rm pphd-t1green1
tail -20 .superpowers/run-pphd-t1green1.log
```

Expected: still a **collection error**, but now
`ModuleNotFoundError: No module named 'autoposter.collections.poster_title'` —
the schema half is done and the module half is not. To see the three config
tests actually pass before the module exists, comment out the
`from autoposter.collections.poster_title import …` block, re-run, and confirm
**3 passed**; then restore the import verbatim before moving on.

- [ ] **Step 8: Commit the config section**

```bash
git add src/autoposter/config/schema.py
git commit -m "feat(collections): a poster_title section under collections, off by default

Row 105's config home. Under collections and never under artwork:
render_version hashes the artwork section wholesale, so the same key
there would move config.version at its default value and strand every
stored render fingerprint. A sibling model reusing TextStyle rather
than an ArtKindConfig subclass, so the served field map does not
advertise six settings a collection poster ignores."
```

- [ ] **Step 9: Write the failing module tests**

Append to `tests/test_collection_poster_title.py`:

```python
# --- the composite itself -----------------------------------------------------
#
# Every test below draws for real, with Pillow, against a plain-colour poster.
# "Plain" is load-bearing: it makes "these pixels changed" mean the text and
# nothing else, and it makes the byte-identity assertion below meaningful.


def _changed_rows(before: bytes, after: bytes) -> set[int]:
    """Which pixel rows the composite touched.

    Compared row-by-row rather than by a whole-image diff so the positional
    assertions can say WHERE the text landed, which is what the gravity and
    the scaling are about.
    """
    original = Image.open(io.BytesIO(before)).convert("RGB")
    composited = Image.open(io.BytesIO(after)).convert("RGB")
    assert original.size == composited.size
    rows = set()
    for y in range(original.height):
        for x in range(0, original.width, 7):  # a stride, not every column
            if original.getpixel((x, y)) != composited.getpixel((x, y)):
                rows.add(y)
                break
    return rows


def test_an_empty_title_is_refused_by_name():
    """The ONE refusal. Everything else clamps (adjudication A-6)."""
    with pytest.raises(CollectionTitleRefused) as excinfo:
        compose_collection_title(_settings(), None, _poster(), "   ")
    assert "empty" in str(excinfo.value)


def test_a_font_that_resolves_nowhere_is_refused_by_name(tmp_path):
    """Named by the FONT, which is the string the operator wrote and the one
    they can act on -- and by nothing else: no path, and no other exception's
    text (roadmap row 213)."""
    settings = _settings()
    settings.title.font = "NoSuchFace.ttf"
    with pytest.raises(CollectionTitleRefused) as excinfo:
        compose_collection_title(settings, tmp_path, _poster(), "A Collection")
    message = str(excinfo.value)
    assert "'NoSuchFace.ttf'" in message
    assert str(tmp_path) not in message


def test_bytes_that_do_not_decode_are_refused_by_name():
    """A poster source can hand back an HTML error page saved as .jpg. The
    refusal carries the exception's CLASS NAME and never its text."""
    with pytest.raises(CollectionTitleRefused) as excinfo:
        compose_collection_title(_settings(), None, b"<html>not an image</html>", "A Collection")
    assert "did not decode" in str(excinfo.value)


def test_a_composite_draws_into_the_lower_band_and_stays_a_jpeg():
    """The happy path, through the bundled face with no fonts_root at all."""
    source = _poster()
    out = compose_collection_title(_settings(), None, source, "A Collection")

    assert out != source
    image = Image.open(io.BytesIO(out))
    assert image.format == "JPEG"
    assert image.size == (2000, 3000)

    rows = _changed_rows(source, out)
    assert rows, "the composite drew nothing at all"
    # gravity south with a +300 title offset and a +120 line offset: nothing
    # may land in the top half, and nothing below the poster's own edge.
    assert min(rows) > 1500
    assert max(rows) < 3000


def test_the_same_inputs_give_byte_identical_output():
    """What apply_poster's sha-compare turns into 'an unchanged pass uploads
    nothing'. Without this the gate-on cost would be one re-upload PER PASS
    rather than one in total."""
    source = _poster()
    first = compose_collection_title(_settings(), None, source, "A Collection")
    second = compose_collection_title(_settings(), None, source, "A Collection")
    assert first == second


def test_a_long_title_is_clamped_rather_than_refused(caplog):
    """Adjudication A-6, and the deliberate divergence from separator_art.

    ``separator_art._render`` abandons a render whose text will not fit above
    the floor. That argument does not transfer: a divider's label is one of ten
    short words this service chooses, while a collection title is whatever the
    operator named their collection. Posterizarr clamps and writes; so do we,
    with a WARNING naming the title.
    """
    source = _poster()
    title = "The Extraordinarily Long Collection Of Films " * 6
    with caplog.at_level("WARNING"):
        out = compose_collection_title(_settings(), None, source, title)

    assert out != source
    assert any("clamped" in record.message for record in caplog.records)


def test_the_operators_own_font_beats_the_bundled_face(tmp_path):
    """Rung one is the operator's mount, rung two is the bundle -- the bundle
    is a fallback, never an override. Proven by putting a DIFFERENT face under
    fonts_root under the bundled name and getting different pixels."""
    (tmp_path / "Inter-Medium.ttf").write_bytes(
        (asset_path("fonts") / "Comfortaa-Medium.ttf").read_bytes()
    )
    source = _poster()
    theirs = compose_collection_title(_settings(), tmp_path, source, "A Collection")
    bundled = compose_collection_title(_settings(), None, source, "A Collection")
    assert theirs != bundled


def test_the_boxes_scale_with_the_poster_actually_fetched():
    """The transcribed values are written against a 2000-pixel-wide canvas
    (``render/compositor.POSTER_SIZE`` is "2000x3000"), and the hosted defaults
    this service fetches are not all that size. Unscaled, a 1900-pixel text box
    on a 1000-pixel poster would run the title off both edges.

    The assertion is proportional, not absolute: on a half-size poster the
    text must land in the same FRACTION of the image as on a full-size one.
    """
    full = _poster(2000, 3000)
    half = _poster(1000, 1500)
    full_rows = _changed_rows(full, compose_collection_title(_settings(), None, full, "A Collection"))
    half_rows = _changed_rows(half, compose_collection_title(_settings(), None, half, "A Collection"))

    assert full_rows and half_rows
    assert abs(min(full_rows) / 3000 - min(half_rows) / 1500) < 0.05
    assert abs(max(full_rows) / 3000 - max(half_rows) / 1500) < 0.05


def test_the_fixed_second_line_can_be_switched_off():
    """Posterizarr's AddCollectionTitle, as this schema spells it."""
    source = _poster()
    both = compose_collection_title(_settings(), None, source, "A Collection")
    settings = _settings()
    settings.collection_line.add_text = False
    title_only = compose_collection_title(settings, None, source, "A Collection")

    assert both != title_only
    assert len(_changed_rows(source, title_only)) < len(_changed_rows(source, both))


def test_an_empty_fixed_line_draws_the_title_alone():
    """The same outcome by the other spelling -- an operator who empties the
    word rather than switching the block off."""
    source = _poster()
    settings = _settings()
    settings.collection_line.add_text = False
    off = compose_collection_title(settings, None, source, "A Collection")

    emptied = _settings()
    emptied.collection_line_text = ""
    assert compose_collection_title(emptied, None, source, "A Collection") == off


def test_the_reference_width_is_the_canvas_the_values_were_written_against():
    """Pinned as a constant rather than a literal 2000 in three places: it is
    the same number ``render/compositor.POSTER_SIZE`` carries, and the whole
    scaling argument rests on it."""
    assert REFERENCE_WIDTH == 2000


def test_a_north_gravity_draws_at_the_top():
    """The gravity branch, proven positionally rather than by reading it."""
    source = _poster()
    settings = _settings()
    settings.title.gravity = "north"
    settings.title.text_offset = "+100"
    settings.collection_line.add_text = False

    rows = _changed_rows(source, compose_collection_title(settings, None, source, "A Collection"))
    assert rows
    assert min(rows) >= 100
    assert max(rows) < 1500
```

- [ ] **Step 10: Run the module tests to verify they fail**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t1red2 test sh -c \
  'set -o pipefail; pytest tests/test_collection_poster_title.py -q 2>&1 | tee /app/.superpowers/run-pphd-t1red2.log'
docker cp pphd-t1red2:/app/.superpowers/run-pphd-t1red2.log .superpowers/run-pphd-t1red2.log
docker rm pphd-t1red2
tail -20 .superpowers/run-pphd-t1red2.log
```

Expected: a collection error,
`ModuleNotFoundError: No module named 'autoposter.collections.poster_title'`.

- [ ] **Step 11: Write the module**

Create `src/autoposter/collections/poster_title.py`:

```python
"""A styled collection title, drawn onto a managed collection's poster.

Roadmap row 105. Posterizarr can print a collection's title, plus a fixed
"COLLECTION" line, onto the poster it writes for a collection
(``CollectionPosterOverlayPart`` / ``CollectionTitlePosterPart``). This module
is that capability, and it is **styled after those parts, not byte-matched to
them.** No captured Posterizarr output exists anywhere -- not on the live Plex
server, not under ``tests/fixtures/`` -- and Kometa has no equivalent at all
(it sets a collection's poster from a URL or a file and never composites), so
there is no oracle to render against. What IS transcribed is the INPUT side:
the operator's own Posterizarr config carries both parts with values
(``posterizarr-config.txt:274-289`` and ``:310-328``, gitignored, transcribed
by phase 5b), and those are the shipped defaults of
``config.schema.CollectionPosterTitleConfig``. A config is not a proof; the
row cell and the pull request say so in as many words.

**Standalone, like ``collections/separator_art.py``, and for the same
reason.** ``render/pipeline.py``'s ``compose_styled``/``_CANVAS``/
``art_config_for`` are NOT widened to carry a "collection" art kind: a
collection poster has no clearlogo branch, no language order and no title-card
second line, so the shared function would gain three dead branches -- and
widening that file would collide head-on with two open branches that rewrite
it. This module reaches for the same primitives ``separator_art`` does and
draws its own image.

**Pillow, not ImageMagick, and that IS a divergence from ``separator_art``.**
``textfit.fit_point_size`` measures by shelling out to ``magick``; every test
that composited for real would then need ``@pytest.mark.imagemagick``, and
CI's main run deselects that marker on a runner with no binary -- a composite
test that silently never ran would be worse than no composite test. So the FIT
is measured with Pillow's own metrics here, while ``prepare_text`` (quote
normalisation, forced breaks, all-caps) and ``FitResult`` (the truncation
vocabulary) are reused verbatim from ``render/textfit.py`` so the
normalisation and the contract live in one place. Pillow is already this
package's image decoder -- ``collections/posters._is_image`` opens every byte
string that reaches an upload with it.

**Clamp, do not refuse.** ``separator_art._render`` abandons a render whose
text will not fit above ``min_point_size``, on the argument that illegible
artwork is worse than none because it still gets hashed and so is never
retried. That argument does not transfer. A divider's label is one of ten
short words this service chooses; a collection title is whatever the operator
named their collection, and the title box here is narrower than the divider's.
So a title that will not fit is drawn at the floor with a WARNING naming it --
which is what Posterizarr does. The ONE refusal is an empty title, which has
nothing to draw.

**Determinism.** Same poster bytes + same font file + same settings + same
Pillow build => the same output bytes, which ``apply_poster``'s sha-compare
turns into "an unchanged pass uploads nothing". Without it the opt-in cost
would be one re-upload per pass rather than one in total. Across Pillow BUILDS
the bytes may drift and every affected collection re-uploads once, which is
the same bounded, self-settling cost turning the gate on carries anyway.

**Failure posture.** There are exactly three refusals and each is a
``CollectionTitleRefused`` carrying a written reason and nothing an operator
cannot act on -- never a filesystem path, and never another exception's message
(roadmap row 213). Only the font one has a name to carry, and it carries the
configured font NAME, which is the string the operator wrote: "the configured
font %r resolves neither under fonts_root nor to a bundled face". The other two
carry no name at all, because there is none that would help: "the collection
title is empty, so there is nothing to draw", and "the poster did not decode as
an image (%s)" with the underlying exception's CLASS NAME and never its text.
The caller reports whichever fired and uploads nothing, leaving
``poster_sha256`` where it was so a later pass retries: a missing poster is
cosmetic and must never fail the surrounding pass.
"""
import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from autoposter.config.schema import CollectionPosterTitleConfig, TextStyle
from autoposter.overlays.sources import OverlaySourceError, resolve_font_path
from autoposter.render.textfit import FitResult, prepare_text

logger = logging.getLogger(__name__)

# The canvas Posterizarr's transcribed box sizes, offsets and point sizes are
# written against. It is the same number ``render/compositor.POSTER_SIZE``
# carries ("2000x3000"). Every pixel value in the config is scaled by the
# fetched poster's own width over this, so a 1000x1500 hosted default gets the
# same composition as a 2000x3000 one instead of a 1900-pixel-wide text box on
# a 1000-pixel-wide image.
REFERENCE_WIDTH = 2000

# Upstream's ``-quality 100%``, the value ``separator_art._render`` writes at.
_QUALITY = 100


class CollectionTitleRefused(Exception):
    """This collection's poster could not have a title drawn onto it.

    Carries the configured font NAME and a written reason, never a filesystem
    path and never another exception's text: the caller puts this message into
    a run-report action an operator reads (roadmap row 213). ``PosterPathRefused``
    in ``collections/posters.py`` is the same shape, one module along.
    """


def _scaled(value: int, scale: float) -> int:
    """One configured pixel value at the fetched poster's scale, never below 1.

    A floor of 1 rather than 0: a zero point size or a zero-width box would
    make Pillow raise from inside the draw, which the caller would then have to
    report as an unnamed failure.
    """
    return max(1, round(value * scale))


def _scaled_offset(value: int, scale: float) -> int:
    """A SIGNED offset at the fetched poster's scale.

    Unlike ``_scaled`` this may be zero or negative: ``TextStyle.text_offset``
    carries a deliberate sign (its own ``_must_carry_sign`` validator insists on
    one), and a negative offset is how the render pipeline's title card hides a
    block off-canvas.
    """
    return round(value * scale)


def _font(fonts_root: Path | None, value: str) -> Path:
    """The configured face as a real file, through the shared two-rung finder.

    ``overlays/sources.resolve_font_path`` is that finder: the operator's own
    ``fonts_root`` first (confined, so a written value cannot leave the mount),
    this service's bundled faces second. Reused rather than re-implemented so
    there is ONE answer in this codebase to "where does a configured font come
    from" -- and so an operator who drops Posterizarr's own
    ``Colus-Regular.ttf`` into their ``fonts_root`` and names it here gets
    exactly that file.

    Its refusal names the overlay vocabulary, which is the wrong noun on a
    collection poster, so it is re-raised as this module's own with the font's
    NAME in it. The original is chained for the log, never interpolated into
    the message.
    """
    try:
        return resolve_font_path(fonts_root, value, "collection poster title")
    except OverlaySourceError as exc:
        raise CollectionTitleRefused(
            "the configured font %r resolves neither under fonts_root nor to a "
            "bundled face" % value
        ) from exc


def _break_word(font: ImageFont.FreeTypeFont, word: str, width: int) -> list[str]:
    """``word`` split between characters into pieces that each fit ``width``.

    Only ever reached at the point-size floor, for a single word wider than the
    whole box. At least one character goes on every piece, so a box narrower
    than one glyph terminates instead of looping forever.
    """
    pieces: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and font.getlength(candidate) > width:
            pieces.append(current)
            current = char
        else:
            current = candidate
    pieces.append(current)
    return pieces


def _wrap(
    font: ImageFont.FreeTypeFont, text: str, width: int, *, hard: bool
) -> list[str] | None:
    """``text`` broken into lines that each fit ``width``, or ``None``.

    The explicit newlines ``prepare_text`` inserted are honoured first, then
    each of those paragraphs is greedily word-wrapped -- the same shape
    ImageMagick's ``caption:`` produces, which is what the transcribed box
    sizes were measured against.

    ``None`` reports a single WORD wider than the box: no smaller line break
    exists, so the caller tries a smaller point size. At the floor the caller
    asks again with ``hard=True``, which breaks that word between characters
    rather than returning nothing -- a clamped render draws something.
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if font.getlength(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if font.getlength(word) <= width:
                current = word
                continue
            if not hard:
                return None
            pieces = _break_word(font, word, width)
            lines.extend(pieces[:-1])
            current = pieces[-1]
        lines.append(current)
    return lines


def _block_height(font: ImageFont.FreeTypeFont, lines: list[str], line_spacing: int) -> int:
    """The drawn height of a wrapped block, from the face's own metrics.

    ``getmetrics()`` rather than a per-line ``getbbox``: every line in a block
    is laid out on the same baseline pitch, so measuring one line's ink and
    another's would make a block of "AAA" shorter than a block of "ggg" and
    move the anchor for no reason an operator would recognise.
    """
    ascent, descent = font.getmetrics()
    return len(lines) * (ascent + descent) + line_spacing * max(0, len(lines) - 1)


def _fit(
    font_path: Path,
    text: str,
    box: tuple[int, int],
    line_spacing: int,
    floor: int,
    ceiling: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], FitResult]:
    """The largest point size in ``[floor, ceiling]`` whose block fits ``box``.

    A binary search rather than a scan: both the wrapped line count and each
    line's height rise monotonically with the point size, so "fits" is a
    monotone predicate and the search finds exactly what a 150-step descending
    scan would, with eight ``truetype`` loads instead of 150.

    Returns the fitted face, its lines, and a ``FitResult`` -- the same
    ``truncated`` vocabulary ``render/textfit.fit_point_size`` uses, so a
    reader of either is reading one contract. Unlike that function's callers
    this module DRAWS a truncated fit rather than abandoning it; ``truncated``
    is what the caller's WARNING is keyed off.
    """
    width, height = box
    best: tuple[int, ImageFont.FreeTypeFont, list[str]] | None = None
    low, high = floor, ceiling
    while low <= high:
        mid = (low + high) // 2
        font = ImageFont.truetype(str(font_path), mid)
        lines = _wrap(font, text, width, hard=False)
        if lines is not None and _block_height(font, lines, line_spacing) <= height:
            best = (mid, font, lines)
            low = mid + 1
        else:
            high = mid - 1
    if best is not None:
        size, font, lines = best
        return font, lines, FitResult(point_size=size, truncated=False)
    font = ImageFont.truetype(str(font_path), floor)
    return (
        font,
        _wrap(font, text, width, hard=True) or [text],
        FitResult(point_size=floor, truncated=True),
    )


def _draw(
    image: Image.Image,
    style: TextStyle,
    font_path: Path,
    text: str,
    scale: float,
    block_name: str,
    title: str,
) -> None:
    """Draw one wrapped, auto-fitted text block onto ``image``, in place.

    Horizontally centred always -- both Posterizarr parts are, and the schema
    admits only the three vertical gravities this honours. Vertically the block
    is anchored from the configured edge by the configured offset, both scaled
    to the poster actually fetched.
    """
    box = (_scaled(style.max_width, scale), _scaled(style.max_height, scale))
    line_spacing = _scaled(style.line_spacing, scale) if style.line_spacing else 0
    font, lines, fit = _fit(
        font_path,
        text,
        box,
        line_spacing,
        _scaled(style.min_point_size, scale),
        _scaled(style.max_point_size, scale),
    )
    if fit.truncated:
        logger.warning(
            "%s for %r does not fit its %dx%d box above %dpt; drawing it clamped "
            "at the floor",
            block_name, title, box[0], box[1], fit.point_size,
        )

    block = _block_height(font, lines, line_spacing)
    offset = _scaled_offset(int(style.text_offset), scale)
    if style.gravity == "north":
        top = offset
    elif style.gravity == "center":
        top = (image.height - block) // 2 + offset
    else:
        # "south" -- the config validator (_gravities_must_be_drawable) admits
        # only these three, so there is no fourth case to guess at.
        top = image.height - offset - block

    ascent, descent = font.getmetrics()
    pitch = ascent + descent + line_spacing
    stroke = _scaled(style.stroke_width, scale) if style.add_stroke else 0
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text(
            ((image.width - font.getlength(line)) / 2, top + index * pitch),
            line,
            font=font,
            fill=style.font_color,
            stroke_width=stroke,
            stroke_fill=style.stroke_color,
        )


def compose_collection_title(
    settings: CollectionPosterTitleConfig,
    fonts_root: Path | None,
    data: bytes,
    title: str,
) -> bytes:
    """``data`` with ``title`` and the fixed second line drawn on it, as JPEG.

    Blocking -- Pillow decodes, draws and re-encodes a whole poster -- so
    ``apply_poster`` calls it through ``asyncio.to_thread``, the same treatment
    that path already gives ``uploadPoster``.

    The fonts are resolved BEFORE the image is decoded, so a misconfigured face
    costs no decode and refuses in the same breath whichever poster arrived.

    Raises ``CollectionTitleRefused`` for an empty title, for a font that
    resolves nowhere, and for bytes that do not decode as an image. Each is a
    skip the caller REPORTS by name; none uploads anything, so
    ``poster_sha256`` stays where it was and the next pass tries again.
    """
    if not title.strip():
        raise CollectionTitleRefused(
            "the collection title is empty, so there is nothing to draw"
        )

    blocks: list[tuple[TextStyle, str, str]] = []
    if settings.title.add_text:
        blocks.append((settings.title, prepare_text(title, settings.title), "the title block"))
    line_text = prepare_text(settings.collection_line_text, settings.collection_line)
    if settings.collection_line.add_text and line_text.strip():
        blocks.append((settings.collection_line, line_text, "the collection line"))
    fonts = [_font(fonts_root, style.font) for style, _, _ in blocks]

    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = opened.convert("RGB")
    except Exception as exc:
        raise CollectionTitleRefused(
            "the poster did not decode as an image (%s)" % type(exc).__name__
        ) from exc

    scale = image.width / REFERENCE_WIDTH
    for font_path, (style, text, block_name) in zip(fonts, blocks):
        _draw(image, style, font_path, text, scale, block_name, title)

    buffer = io.BytesIO()
    # No EXIF and no timestamp is what Pillow writes by default here, which is
    # what makes equal inputs give equal bytes -- separator_art needs an
    # explicit ``-strip`` for the same guarantee because ImageMagick embeds
    # ``date:create``/``date:modify`` on every run.
    image.save(buffer, format="JPEG", quality=_QUALITY, subsampling=0)
    return buffer.getvalue()
```

- [ ] **Step 12: Run the module tests to verify they pass**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t1green2 test sh -c \
  'set -o pipefail; pytest tests/test_collection_poster_title.py -q 2>&1 | tee /app/.superpowers/run-pphd-t1green2.log'
docker cp pphd-t1green2:/app/.superpowers/run-pphd-t1green2.log .superpowers/run-pphd-t1green2.log
docker rm pphd-t1green2
tail -20 .superpowers/run-pphd-t1green2.log
```

Expected: **15 passed** — the 3 config pins from Step 4 plus the 12 module pins
from Step 9. If
`test_the_boxes_scale_with_the_poster_actually_fetched` fails on the 0.05
tolerance, do not widen the tolerance — read the two row sets and find which
of `_scaled`, `_scaled_offset` or the anchor arithmetic dropped the scale.

- [ ] **Step 13: Run the whole suite and ruff**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t1lint test sh -c \
  'set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-pphd-t1lint.log'
docker cp pphd-t1lint:/app/.superpowers/run-pphd-t1lint.log .superpowers/run-pphd-t1lint.log
docker rm pphd-t1lint
cat .superpowers/run-pphd-t1lint.log

docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphd-t1full test sh -c \
  'set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pphd-t1full.log'
docker wait pphd-t1full
docker cp pphd-t1full:/app/.superpowers/run-pphd-t1full.log .superpowers/run-pphd-t1full.log
docker rm pphd-t1full
tail -10 .superpowers/run-pphd-t1full.log
```

Expected: `ruff` clean, and **exactly `B + 15` passed** with the same skip
count as the baseline. In particular `tests/test_config_descriptions.py` and
`tests/test_example_config_matches_schema.py` must still be green — a new
field with no `description=` reds the first, and the second's reverse guard is
section-level so the untouched example config reds nothing.

- [ ] **Step 14: Commit the module**

```bash
git add src/autoposter/collections/poster_title.py tests/test_collection_poster_title.py
git commit -m "feat(collections): draw a collection title onto a poster, with Pillow

Row 105's compose step, as a standalone module beside separator_art
rather than a fifth art kind in render/pipeline.py: a collection poster
has no logo branch, no language order and no title-card second line.
Pillow rather than ImageMagick so every composite test runs on CI's main
lane, with prepare_text and FitResult reused so the normalisation and the
truncation contract stay in one place. A title that will not fit is
clamped and drawn with a warning, the way Posterizarr does; the one
refusal is an empty title. Styled after Posterizarr's parts, not
byte-matched to them -- no captured output exists to match against.

Nothing is wired to this yet."
```

---

## Task 2: the upload-path wiring, the impact row, and the storm guard

**Files:**
- Modify: `src/autoposter/collections/posters.py:49`, `:145`, `:397-398`, `:439-451`, `:475-481`
- Modify: `src/autoposter/collections/poster_title.py` — append `poster_title_parts`
- Modify: `src/autoposter/collections/reconcile.py:24-31`, `:91-119`, `:1114`
- Modify: `src/autoposter/collections/smart.py:53-63`, `:216-239`, `:366`
- Modify: `src/autoposter/collections/lists.py:19-28`, `:71-93`, `:279`
- Modify: `src/autoposter/config/impact.py:28-45`, end of file
- Modify: `src/autoposter/api/routes.py:48`, `:1911-1923`
- Modify: `frontend/src/api/types.ts:752-754` (`ConfigPreviewResponse`,
  anchored by type name)
- Modify: `frontend/src/pages/Settings.tsx:510-555`, `:809`
- Test: `tests/test_collection_poster_wiring.py`, `tests/test_config_impact.py`,
  `tests/test_api_config_editor.py`, `frontend/src/pages/Settings.test.tsx`

**Interfaces:**
- Consumes: `collections.poster_title.compose_collection_title(settings,
  fonts_root, data, title) -> bytes` and
  `collections.poster_title.CollectionTitleRefused` (Task 1).
- Consumes: `config.schema.CollectionPosterTitleConfig` at
  `config.collections.poster_title` (Task 1).
- Produces: `collections.posters.SEPARATOR_KIND = "separator"`.
- Produces: `collections.poster_title.poster_title_parts(config) -> list[str]`
  — the section's suffix term for a definition hash, `[]` while the gate is off.
- Widens: `reconcile.definition_hash(bucket, settings=None, url="", config=None)`,
  `smart.smart_definition_hash(url, summary, settings=None, config=None)` and
  `lists._members_hash(items, summary, sync_mode="sync", settings=None,
  config=None)` — a trailing keyword-defaulted parameter on each, so every
  existing caller and every existing test keeps its current call and its
  current digest.
- Produces: `config.impact.count_collection_posters(session: AsyncSession,
  before: Config, after: Config) -> int`.
- Produces: `POST /api/config/preview` gains a top-level integer key
  `"collection_posters"`, and `ConfigPreviewResponse` an optional
  `collection_posters?: number`.

---

- [ ] **Step 1: Write the failing wiring tests**

Append to `tests/test_collection_poster_wiring.py`:

```python
# --- the collection-title composite (roadmap row 105) -------------------------
#
# Every test here drives a REAL reconciler -- ``reconcile_content_ratings`` or
# ``engine._separators`` through ``_separator_pass`` -- and never
# ``apply_poster`` or ``compose_collection_title`` in isolation. A helper test
# would prove the composite can be computed and say nothing about whether the
# shipped path uses it, which is exactly how a gated feature has twice passed
# its own tests in this tree.


def _enable_title(config):
    """Turn row 105's gate on, leaving every other knob at its shipped default."""
    config.collections.poster_title.enabled = True
    return config


def _poster_bytes(colour: str = "navy") -> bytes:
    """A 200x300 poster -- big enough that the composite really draws glyphs.

    This file's own ``_jpeg_bytes`` (:52-55) is **4x4**, and 4/2000 scales every
    box, offset and point size in this section to the module's 1px floor: the
    title box becomes 4x1 at 1pt, every test here would emit the clamp WARNING,
    and ``uploaded[0] != data`` would go green because the module re-encodes at
    ``quality=100, subsampling=0`` while the fixture was saved at Pillow's
    default 75 -- not because anything was drawn. At 200x300 the title block
    gets a 190x50 box at 10-25pt and the fixed line a 120x15 box at 4-9pt, which
    is real text and needs no clamping. Plain colour rather than noise, so "these
    pixels moved" is unambiguously the glyphs.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (200, 300), colour).save(buffer, format="JPEG", quality=100)
    return buffer.getvalue()


def _moved_pixels(before: bytes, after: bytes, top: int, bottom: int) -> int:
    """How many pixels between rows ``top`` and ``bottom`` moved.

    A count rather than a boolean so a test can say "the text band moved a great
    deal more than an untouched band did", which holds whether or not a flat
    region round-trips a JPEG re-encode exactly.
    """
    original = Image.open(io.BytesIO(before)).convert("RGB")
    composited = Image.open(io.BytesIO(after)).convert("RGB")
    assert original.size == composited.size
    return sum(
        original.getpixel((x, y)) != composited.getpixel((x, y))
        for y in range(top, bottom)
        for x in range(original.width)
    )


async def test_the_gate_off_uploads_the_fetched_bytes_untouched(
    session, config_factory, tmp_path
):
    """The default, and the whole no-storm argument: off is byte-identical."""
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    assert config.collections.poster_title.enabled is False
    data = _poster_bytes()

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert section._existing["Age 17+ Movies"].uploaded_bytes == [data]


async def test_the_gate_on_composites_the_title_onto_a_managed_poster(
    session, config_factory, tmp_path
):
    """Through the real reconciler, so the wiring is what is proven.

    And proven by PIXELS inside the text band rather than by
    ``uploaded[0] != data``: the module re-encodes at ``quality=100,
    subsampling=0``, so any call at all changes the bytes of a fixture saved at
    Pillow's default quality. "The bytes differ" would stay green with the
    drawing removed. What is asserted instead is that the bottom third moved a
    great deal and the top half did not.
    """
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    data = _poster_bytes()

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    uploaded = section._existing["Age 17+ Movies"].uploaded_bytes
    assert len(uploaded) == 1
    composited = Image.open(io.BytesIO(uploaded[0]))
    assert composited.format == "JPEG"
    assert composited.size == (200, 300)

    # gravity south, a +300 title offset and a +120 line offset, all at the
    # 200/2000 scale: the glyphs land in the bottom third and nowhere else.
    drawn = _moved_pixels(data, uploaded[0], 200, 300)
    untouched = _moved_pixels(data, uploaded[0], 0, 150)
    assert drawn > 100, "the composite drew nothing into the title band"
    assert drawn > 20 * untouched

    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 == hashlib.sha256(uploaded[0]).hexdigest()


async def test_the_gate_on_re_uploads_each_managed_poster_exactly_once(
    session, config_factory, tmp_path
):
    """Adjudication A-9's arithmetic, proven rather than asserted in prose.

    THE RULE, in two digests. The poster_title settings are PART of the bytes
    hashed into ``poster_sha256``, because the compose step sits ABOVE that
    digest (``posters.py:481``) -- so the gate-on pass uploads a poster whose
    bytes differ from the pre-gate upload. And they are PART of
    ``definition_hash`` too, as ``poster_title_parts``' suffix term -- so that
    pass is the very NEXT one, not whichever later pass happens to move the
    definition for some other reason. A-9 promised "gate-on re-uploads each
    managed poster once", instantly; this is the whole of it.

    NOTHING IS NULLED HERE, AND THAT IS THE TEST. An earlier revision of this
    plan reached ``apply_poster`` by setting ``row.poster_sha256 = None``
    between the passes. That would go green while proving only the caller's
    documented NULL-sha fall-through -- every caller short-circuits on
    ``definition_current and not (posters_on and record.poster_sha256 is
    None)`` (``reconcile.py:1124``, ``smart.py:375``, ``lists.py:305``), which
    this file's own
    ``test_a_third_pass_over_an_unchanged_collection_uploads_nothing`` (:389)
    already documents -- and would say nothing at all about the roll-out an
    operator actually gets. Pass 2 below reaches ``apply_poster`` because the
    gate flip moved ``definition_hash``, which IS the production roll-out. Drop
    the suffix term and this test goes red at ``len(uploaded) == 2``.

    Pass 3 settles at the CALLER's short-circuit, on both digests at once. The
    in-process determinism that makes the CONTENT compare settle too is pinned
    separately, by Task 1's ``test_the_same_inputs_give_byte_identical_output``;
    this test does not claim to prove it.
    """
    section = RatingSection({"17"})
    config = config_factory(assets_root=str(tmp_path))
    config.collections.apply_to_plex = True
    data = _poster_bytes()

    # Pass 1, gate off: the fetched bytes go up untouched, and both digests
    # land -- the content one and the definition one.
    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    uploaded = section._existing["Age 17+ Movies"].uploaded_bytes
    assert uploaded == [data]
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    gate_off_definition = row.definition_hash
    assert gate_off_definition

    # The operator turns the gate on, and NOTHING else is touched: no sha is
    # nulled, no definition is edited, no row is deleted.
    _enable_title(config)

    # Pass 2, gate on: the definition hash moved, so this pass -- the very next
    # one -- reaches apply_poster. Exactly one more upload, and its pixels
    # differ.
    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2, "the gate flip did not move definition_hash"
    assert row.definition_hash != gate_off_definition
    assert uploaded[1] != uploaded[0]
    assert _moved_pixels(uploaded[0], uploaded[1], 200, 300) > 100
    assert row.poster_sha256 == hashlib.sha256(uploaded[1]).hexdigest()

    # Pass 3, gate still on and both digests stored: nothing at all.
    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2


async def test_a_changed_text_knob_re_uploads_each_managed_poster_exactly_once(
    session, config_factory, tmp_path
):
    """A-9's third property: an edit made while the gate is ALREADY on.

    Same two digests, same arithmetic. The knob is inside the model dump that
    ``poster_title_parts`` folds into ``definition_hash``, so the next pass
    reaches ``apply_poster``; and it changes the glyphs, so ``poster_sha256``
    moves and exactly one upload follows. The pass after that short-circuits.

    ``collection_line_text`` rather than a box size, so the assertion does not
    depend on a 200x300 fixture rendering two point sizes distinguishably: a
    different word is different glyphs at any size.
    """
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    data = _poster_bytes()

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    uploaded = section._existing["Age 17+ Movies"].uploaded_bytes
    assert len(uploaded) == 1

    config.collections.poster_title.collection_line_text = "SERIES"

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2, "the knob did not move definition_hash"
    assert uploaded[1] != uploaded[0]

    async with _client(_serving_handler(data, [])) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )
    assert len(uploaded) == 2


def test_the_gate_off_leaves_every_definition_hash_byte_identical(config_factory):
    """A-9's second property, on all three hash functions at once.

    ``config=None`` is not a contrivance: it is literally the signature every
    caller in the base suite still uses, so "equal to the ``config=None``
    payload" IS "equal to the digest already stored on the live server". What
    makes it byte-identical rather than merely equal-at-the-defaults is that
    ``poster_title_parts`` refuses on ``settings.enabled`` before it dumps
    anything -- so the boxes are retuned below with the gate off and the three
    digests do not move.

    ``separator_hash`` is absent on purpose: it never gets the term, because a
    divider is never captioned (A-3).

    Not driven through a reconciler because there is nothing wired to drive --
    these three functions ARE the mechanism, and Global Constraint 9's real
    entry point is covered by the two tests above, which reach them through
    ``reconcile_content_ratings``.
    """
    config = config_factory()
    assert config.collections.poster_title.enabled is False
    config.collections.poster_title.title.max_width = 111
    config.collections.poster_title.collection_line_text = "SET"

    bucket = Bucket(key="17", title="Age 17+ Movies", summary="s", values=("17",))
    assert definition_hash(bucket, None, "u", config) == definition_hash(bucket, None, "u")
    assert smart_definition_hash("u", "s", None, config) == smart_definition_hash("u", "s")
    assert _members_hash([], "s", "sync", None, config) == _members_hash([], "s", "sync")

    # And the gate ON moves all three, which is what makes the roll-out instant
    # rather than lazy.
    config.collections.poster_title.enabled = True
    assert definition_hash(bucket, None, "u", config) != definition_hash(bucket, None, "u")
    assert smart_definition_hash("u", "s", None, config) != smart_definition_hash("u", "s")
    assert _members_hash([], "s", "sync", None, config) != _members_hash([], "s", "sync")


async def test_an_operators_own_poster_file_passes_through_untouched(
    session, config_factory, tmp_path
):
    """Adjudication A-2. The local rung is also where api/manual.py's poster
    endpoint writes, so this covers the manual surface too: a file the operator
    supplied is theirs, and restyling it is a stronger claim than this service
    makes anywhere else."""
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    theirs = _poster_bytes("green")
    folder = tmp_path / "Movies" / "Age 17+ Movies"
    folder.mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(theirs)

    async with _client(_refusing_handler()) as http:
        await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert section._existing["Age 17+ Movies"].uploaded_bytes == [theirs]


async def test_a_divider_is_never_captioned_twice(session, config_factory, tmp_path):
    """Adjudication A-3, on the kind rather than on the ordering.

    A divider's art already carries the group's name -- baked in by
    separator_art._render for the generated ones, and printed by upstream on
    the fetched ``separators/<style>/<stem>.jpg``. Compositing on top would
    print the title twice.
    """
    section = RatingSection(())
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    data = _poster_bytes()

    async with _client(_serving_handler(data, [])) as http:
        await _separator_pass(session, section, http, config)

    assert section._existing[SEPARATOR_TITLE].uploaded_bytes == [data]


async def test_a_font_that_resolves_nowhere_reports_a_skip_and_uploads_nothing(
    session, config_factory, tmp_path
):
    """Adjudication A-4's tail. The action names the FONT -- the string the
    operator wrote -- and nothing else; poster_sha256 is left NULL so the next
    pass retries, exactly as an unfetchable hosted default is."""
    section = RatingSection({"17"})
    config = _enable_title(config_factory(assets_root=str(tmp_path)))
    config.collections.apply_to_plex = True
    config.fonts_root = str(tmp_path / "fonts")
    config.collections.poster_title.title.font = "NoSuchFace.ttf"

    async with _client(_serving_handler(_poster_bytes(), [])) as http:
        actions = await reconcile_content_ratings(
            session, section, "Movies", "Movie", LABEL, dry_run=False,
            http=http, config=config,
        )

    assert section._existing["Age 17+ Movies"].uploaded_bytes == []
    assert any("NoSuchFace.ttf" in action for action in actions)
    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert row.poster_sha256 is None
```

Add the imports these need, beside the file's existing ones — `io` (`:9`),
`Image` (`:14`), `select` (`:16`), `reconcile_content_ratings` (`:21`) and
`ManagedCollection` (`:23`) are all imported on the cut ref already; `hashlib`
and the three hash functions the last test calls directly are not:

```python
import hashlib
```

and, in the first-party block — **two of these widen an import line the file
already has** (`:19` and `:21`) rather than adding a second import of the same
module:

```python
from autoposter.collections.buckets import Bucket
from autoposter.collections.lists import _members_hash, reconcile_list_collection
from autoposter.collections.reconcile import definition_hash, reconcile_content_ratings
from autoposter.collections.smart import smart_definition_hash
```

`_members_hash` is private and is imported anyway, deliberately: the
alternative is asserting the list path's byte-identity through a whole
`reconcile_list_collection` pass, which would prove the same thing at ten times
the setup and would go green for the wrong reason if that pass short-circuited
somewhere earlier. Two suites on the cut ref already import it exactly this way
— `tests/test_builder_settings.py:31` and `tests/test_collection_adoption.py:9`.

- [ ] **Step 2: Run the wiring tests to verify they fail**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t2red1 test sh -c \
  'set -o pipefail; pytest tests/test_collection_poster_wiring.py -q 2>&1 | tee /app/.superpowers/run-pphd-t2red1.log'
docker cp pphd-t2red1:/app/.superpowers/run-pphd-t2red1.log .superpowers/run-pphd-t2red1.log
docker rm pphd-t2red1
tail -30 .superpowers/run-pphd-t2red1.log
```

Expected: the file's existing tests pass; the eight new ones split **3 passing
/ 5 failing** —
`test_the_gate_off_uploads_the_fetched_bytes_untouched`,
`test_an_operators_own_poster_file_passes_through_untouched` and
`test_a_divider_is_never_captioned_twice` PASS already (nothing composites
yet, so "untouched" is trivially true), and the other five FAIL, each at a
named line:

- `test_the_gate_on_composites_the_title_onto_a_managed_poster` at
  `assert drawn > 100, "the composite drew nothing into the title band"` — with nothing
  wired, the uploaded bytes ARE the fetched bytes and `drawn` is `0`.
- `test_the_gate_on_re_uploads_each_managed_poster_exactly_once` at
  `assert len(uploaded) == 2, "the gate flip did not move definition_hash"` —
  and **that is the RED that matters most in this file**. With no suffix term
  in `definition_hash`, flipping the gate moves nothing the reconciler looks
  at, so pass 2 short-circuits on `definition_current` and `uploaded` still has
  one entry. Nothing is nulled to rescue it, which is exactly the point: the
  earlier revision of this plan nulled `poster_sha256` here and got a green
  test that proved only the caller's fall-through.
- `test_a_changed_text_knob_re_uploads_each_managed_poster_exactly_once` at
  `assert len(uploaded) == 2, "the knob did not move definition_hash"`, for the
  same reason.
- `test_the_gate_off_leaves_every_definition_hash_byte_identical` with a
  `TypeError: definition_hash() takes from 1 to 3 positional arguments but 4
  were given` — the fourth parameter does not exist yet. A TypeError, not an
  assertion: the signature is part of what this test pins.
- `test_a_font_that_resolves_nowhere_reports_a_skip_and_uploads_nothing` at the
  missing `NoSuchFace.ttf` action.

**That split is the correct RED** — the three passing ones are the storm guard,
and they must be green before AND after.

- [ ] **Step 3: Wire the compose step into `apply_poster`**

In `src/autoposter/collections/posters.py`, add the import beside the other
first-party imports (after `from autoposter.collections.groups import
SEPARATOR_STYLES` at `:37`):

```python
from autoposter.collections.poster_title import (
    CollectionTitleRefused,
    compose_collection_title,
)
```

Add the constant immediately after `TMDB_PROFILE_KIND` (`:49`), following that
constant's own precedent:

```python
# The one kind whose art already carries its collection's name, whether this
# service generated it (``separator_art.py`` bakes the divider's title in) or
# fetched upstream's captioned ``separators/<style>/<stem>.jpg``. Named here
# so the producer below and the row-105 composite cannot drift into two
# spellings of one string.
SEPARATOR_KIND = "separator"
```

and use it in `hosted_poster_url` at `:145` — replace

```python
    if kind == "separator":
```

with

```python
    if kind == SEPARATOR_KIND:
```

In `apply_poster`, replace the two-line initialisation at `:397-398`

```python
    data: bytes | None = None
    source = ""
```

with

```python
    data: bytes | None = None
    source = ""
    # Whether row 105's composite may draw on whatever these rungs produce.
    # Set on each rung rather than inferred afterwards: "an operator's own file
    # is theirs" and "a divider is already captioned" are decisions, and a
    # decision that falls out of the ordering is one a later edit can undo
    # without noticing (adjudications A-2, A-3).
    composable = False
```

In the second rung, after `source = label` (`:446`), add:

```python
                # ``generated`` is the caller's separator art, which already
                # carries the divider's title (separator_art._render). A cached
                # Default-Images family poster is a plain fetched image and may
                # be drawn on.
                composable = cached is not generated
```

In the third rung, after the `source = (...)` assignment that ends at `:479`,
add:

```python
        composable = True
```

Then, immediately **before** `digest = hashlib.sha256(data).hexdigest()`
(`:481`), insert the compose step:

```python
    # Roadmap row 105, and this is the only line it costs: above the digest, so
    # the composited bytes are what ``poster_sha256`` remembers and an
    # unchanged pass still uploads nothing; below every source rung, so one
    # call serves all of them.
    #
    # Two exclusions, both named above rather than incidental: an operator's
    # own file is never restyled (``composable`` is False on the local rung,
    # and api/manual.py's poster endpoint writes THROUGH that rung), and a
    # divider is never captioned twice.
    #
    # A failure here uploads nothing and leaves ``poster_sha256`` where it was,
    # so the next pass retries -- a missing poster is cosmetic and must never
    # fail the surrounding pass.
    styling = config.collections.poster_title
    if styling.enabled and composable and kind != SEPARATOR_KIND:
        try:
            data = await asyncio.to_thread(
                compose_collection_title,
                styling, Path(config.fonts_root), data, record.title,
            )
        except CollectionTitleRefused as exc:
            logger.warning(
                "did not draw a title onto the poster for %r: %s", record.title, exc
            )
            return "did not draw a title onto the poster for %r: %s" % (
                record.title, exc,
            )
        except Exception:
            # Row 213: an unmarked exception's text never reaches a served
            # action string. The log carries the traceback; the report carries
            # a sentence.
            logger.exception("failed to draw a title onto the poster for %r", record.title)
            return "failed to draw a title onto the poster for %r" % record.title
        source = "%s, with the collection title drawn on" % source
```

**Then the second half of the wiring, and it is the half that makes the
roll-out instant.** Everything above only decides what gets uploaded *if*
`apply_poster` is reached. What decides whether it is reached is
`definition_hash`, and the settings have to be in it — otherwise a stable
managed collection short-circuits on `definition_current` forever and its
poster is never re-composited after the operator flips the gate. A-9 promised
the re-upload happens once, on the next pass; this is what delivers it.

**First, one new public function in `src/autoposter/collections/poster_title.py`.**
Add `json` to that module's imports (`import io` / `import json` /
`import logging`), and put the function immediately after
`CollectionTitleRefused`, above the private `_scaled` block — it is the
module's second public entry point and the `_`-prefixed block below reads as
one unit:

```python
def poster_title_parts(config) -> list[str]:
    """This section's contribution to a managed collection's definition hash.

    It has to be there, for the reason ``lists._settings_parts`` gives about
    the ride-along settings: a definition hash is what a reconcile pass
    short-circuits on, so a collection that is already current would never be
    revisited and the operator's edit would never be drawn -- a setting that
    reads as saved and silently is not. Here that is the whole roll-out:
    without this term, flipping the gate on leaves every stable managed
    collection short-circuiting on ``definition_current`` and its poster is
    re-composited only if something ELSE happens to move its definition. With
    it, the very next pass re-composites each of them exactly once.

    Folded as a SUFFIX by ``reconcile.definition_hash``,
    ``smart.smart_definition_hash`` and ``lists._members_hash`` -- the last
    element of the payload, in the same position ``_settings_parts`` already
    occupies in all three.

    ``reconcile.separator_hash`` deliberately does NOT fold it. A divider is
    never captioned -- its art already carries the group's name -- so these
    settings cannot move its bytes, and putting them in its hash would buy a
    summary-and-sort-title re-write that changes nothing.

    ``playlists.py:572``'s own call to ``lists._members_hash`` is left on the
    new ``config=None`` default rather than widened to pass ``config``: a
    playlist has no composited poster, so there is nothing for this term to
    move, and ``poster_title_parts(None)`` is ``[]`` -- hash-safe by
    construction, not merely unedited.

    **Nothing is contributed while the gate is off**, and that is an explicit
    ``if not settings.enabled`` rather than a hope about what the defaults dump
    to. Every hash already stored on every live server has to keep matching,
    byte for byte: otherwise merely SHIPPING this section, switched off, would
    re-reconcile every managed collection in the library to write nothing. It
    is the same guarantee ``_settings_parts``' defaults and ``_members_hash``'s
    ``sync`` mode give, made structural instead of arithmetic.

    Takes the whole ``Config`` rather than the sub-model, mirroring
    ``posters.posters_enabled(config, http)``: all three call sites already
    hold ``config`` for exactly that call, and one place knowing the attribute
    path is one place to change if it ever moves. ``None`` is accepted for the
    same reason ``posters_enabled`` accepts it -- a pass with no config
    composites nothing.

    ``sort_keys`` because a hash input must not move when pydantic field
    ordering does, and ``mode="json"`` because a ``TextStyle`` holds only JSON
    scalars and it is the dump ``config/loader.py::render_version`` already
    takes of ``artwork``. The WHOLE sub-model is dumped, ``enabled`` included:
    every knob in it changes the glyphs, so every knob has to reach the pass.
    """
    settings = getattr(getattr(config, "collections", None), "poster_title", None)
    if settings is None or not settings.enabled:
        return []
    return [
        "poster_title=%s"
        % json.dumps(settings.model_dump(mode="json"), sort_keys=True)
    ]
```

**Then the same three-line change in each of the three hash functions.** They
already share `_settings_parts`; they now share this too. In every one of them
the import is **module-level, not local** — unlike `_settings_parts`, which is
imported inside the function body because `lists.py` imports `reconcile.py` at
load time and a module-level import back would be a cycle. `poster_title.py` is
a leaf: it imports `config.schema`, `render.textfit`, `overlays.sources` and
Pillow, and nothing under `collections/` — and `posters.py`, which all three of
these modules already import at load time, imports it too. Verify before
editing, and if this prints anything, use a local import instead and say so in
the task report:

```bash
git show origin/main:src/autoposter/collections/poster_title.py 2>/dev/null | grep -n "autoposter.collections" || true
grep -rn "from autoposter.collections" src/autoposter/collections/poster_title.py || echo NO-BACK-EDGE
```

**`src/autoposter/collections/reconcile.py`.** Add the import immediately
**before** the existing `from autoposter.collections.posters import ...` line
(`:24`) — `poster_title` sorts before `posters`, `_` being below `s`:

```python
from autoposter.collections.poster_title import poster_title_parts
```

Change `definition_hash`'s signature (`:91`):

```python
def definition_hash(bucket: Bucket, settings=None, url: str = "", config=None) -> str:
```

Append this paragraph to its docstring, after the `settings` one and before the
closing `"""` (`:113`):

```
    ``config`` folds ``collections.poster_title`` in the same way and one term
    further along -- see ``poster_title.poster_title_parts``, which is where
    the reason and the gate-off byte-identity live. It is last in the payload
    and contributes NOTHING while the gate is off, so every hash already stored
    still matches. Defaulted, like ``settings`` and ``url``, so every existing
    caller and every existing test keeps its current call and its current
    digest.
```

and its payload (`:116-119`) — note the local `from autoposter.collections.lists
import _settings_parts` at `:114` stays exactly as it is:

```python
    payload = "\x1f".join([
        bucket.title, bucket.summary, *bucket.values, url,
        *_settings_parts(settings),
        *poster_title_parts(config),
    ])
```

Then the call site at `:1114` — `config` is already in scope one line below, at
`posters_enabled(config, http)`:

```python
        wanted = definition_hash(bucket, bucket_settings, url, config)
```

**`src/autoposter/collections/smart.py`.** The same import, before its own
`from autoposter.collections.posters import ...` (`:54`). Then `:216`:

```python
def smart_definition_hash(url: str, summary: str | None, settings=None, config=None) -> str:
```

with the same docstring paragraph and the payload at `:238`:

```python
    payload = "\x1f".join([url, summary or "", *_settings_parts(settings), *poster_title_parts(config)])
```

and the call site at `:366`:

```python
    wanted = smart_definition_hash(url, summary, settings, config)
```

**`src/autoposter/collections/lists.py`.** The same import, before its own
`from autoposter.collections.posters import ...` (`:20`). Then `:71-72`:

```python
def _members_hash(
    items: list, summary: str | None, sync_mode: str = "sync", settings=None, config=None
) -> str:
```

with the same docstring paragraph and the payload at `:87-92`:

```python
    payload = "\x1f".join([
        summary or "",
        *[str(i.ratingKey) for i in items],
        *([] if sync_mode == "sync" else [sync_mode]),
        *_settings_parts(settings),
        *poster_title_parts(config),
    ])
```

and the call site at `:279`:

```python
    wanted = _members_hash(items, summary, sync_mode, settings, config)
```

`_members_hash` has a second production call site,
`collections/playlists.py:572` (`wanted = _members_hash(items,
definition.summary, definition.sync_mode, None)`). **Leave it exactly as it
is.** A playlist has no composited poster, so the new `config` parameter
defaults to `None` there and `poster_title_parts(None)` is `[]` — hash-safe by
construction. Do not widen that call.

**`reconcile.separator_hash` gets none of this**, and that is a decision rather
than an omission: see `poster_title_parts`' docstring, and
`tests/test_collection_group_separators.py`'s `SHIPPED_HASH`, which pins that
digest as a literal and would go red if the term were added there.

- [ ] **Step 4: Run the wiring tests to verify they pass**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t2green1 test sh -c \
  'set -o pipefail; pytest tests/test_collection_poster_wiring.py tests/test_collection_poster_apply.py tests/test_collection_posters.py tests/test_separator_art.py -q 2>&1 | tee /app/.superpowers/run-pphd-t2green1.log'
docker cp pphd-t2green1:/app/.superpowers/run-pphd-t2green1.log .superpowers/run-pphd-t2green1.log
docker rm pphd-t2green1
tail -20 .superpowers/run-pphd-t2green1.log
```

Expected: all four suites green, **eight** more tests than before in
`test_collection_poster_wiring.py`.

**And one thing more, because this step is where a hash term could quietly
break a neighbour.** The three digests just gained a parameter, so run the
suites that pin them — they must be unchanged, since every one of them still
calls with no `config` and `poster_title_parts(None)` is `[]`:

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t2hash test sh -c \
  'set -o pipefail; pytest tests/test_collection_group_separators.py tests/test_collection_lists.py tests/test_collection_smart.py tests/test_builder_settings.py tests/test_collection_adoption.py -q 2>&1 | tee /app/.superpowers/run-pphd-t2hash.log'
docker cp pphd-t2hash:/app/.superpowers/run-pphd-t2hash.log .superpowers/run-pphd-t2hash.log
docker rm pphd-t2hash
tail -10 .superpowers/run-pphd-t2hash.log
```

Expected: all green, **no change in count**, and in particular
`test_collection_group_separators.py`'s `SHIPPED_HASH` assertions still pass —
that literal digest is the byte-history of `separator_hash`, and it is the
loudest possible alarm for the term having been folded into the one hash that
must not have it. **If any of these go red, STOP**: the gate-off contribution
is not `[]`, and shipping that would re-reconcile every managed collection in
the deployment.

- [ ] **Step 5: Commit the wiring**

```bash
git add src/autoposter/collections/posters.py src/autoposter/collections/poster_title.py \
  src/autoposter/collections/reconcile.py src/autoposter/collections/smart.py \
  src/autoposter/collections/lists.py tests/test_collection_poster_wiring.py
git commit -m "feat(collections): draw the title onto managed posters before upload

Row 105's compose step goes above the sha256 and below every source rung:
above, so the composited bytes are what poster_sha256 remembers and an
unchanged pass still uploads nothing; below, so one call serves the
cached family poster, the hosted default and the TMDb profile photo
alike.

Two exclusions, both named on the rung rather than left to the ordering:
an operator's own file under assets_root passes through untouched (which
is also where the manual poster endpoint writes), and a divider is never
captioned twice -- its art already carries the group's name. Unmanaged
collections are not reached at all.

The settings are also a suffix term in the three definition hashes those
collections short-circuit on -- definition_hash, smart_definition_hash and
_members_hash, by the same idiom _settings_parts already occupies in all
three -- so flipping the gate re-composites each managed poster on the
NEXT pass rather than whenever something else moves its definition. The
term is empty while the gate is off, on an explicit enabled check, so
every hash already stored still matches byte for byte and shipping this
section switched off re-reconciles nothing. separator_hash is left alone:
a divider is never captioned, so its bytes cannot move.

Off by default. A compose failure reports by name, uploads nothing, and
leaves poster_sha256 where it was so the next pass retries."
```

- [ ] **Step 6: Write the failing impact tests**

Append to `tests/test_config_impact.py`:

```python
# --- collection posters (roadmap row 105, adjudication A-7) -------------------
#
# The honesty row this preview owed and did not have. ``count_affected`` walks
# ``renders``, and there is no renders row for a collection -- so a
# ``collections.poster_title`` edit previewed as "no re-renders" while every
# managed collection's poster was about to be re-composited and re-uploaded.


async def _seed_collections(session, count: int) -> None:
    for index in range(count):
        session.add(
            ManagedCollection(
                library="Movies", title=f"Collection {index}", kind="smart",
                definition_hash="d" * 64,
            )
        )
    await session.flush()


async def test_a_poster_title_edit_counts_every_managed_collection(
    session, config, variant
):
    await _seed_collections(session, 3)
    after = variant({"collections": {"poster_title": {"enabled": True}}})
    assert await count_collection_posters(session, config, after) == 3


async def test_turning_knobs_while_the_gate_is_off_counts_nothing(
    session, config, variant
):
    """Off means the composite is skipped entirely, so no byte moves however
    the boxes are retuned. Reporting a cost here would teach an operator to
    ignore the number."""
    await _seed_collections(session, 3)
    after = variant({"collections": {"poster_title": {"collection_line_text": "SET"}}})
    assert await count_collection_posters(session, config, after) == 0


async def test_an_unrelated_edit_counts_no_collection_posters(session, config, variant):
    await _seed_collections(session, 3)
    after = variant({"workers": 9})
    assert await count_collection_posters(session, config, after) == 0
```

Widen that file's two import lines:

```python
from autoposter.config.impact import affected_items, count_affected, count_collection_posters
from autoposter.db.models import Job, ManagedCollection, MediaItem, Render
```

- [ ] **Step 7: Run the impact tests to verify they fail**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t2red2 test sh -c \
  'set -o pipefail; pytest tests/test_config_impact.py -q 2>&1 | tee /app/.superpowers/run-pphd-t2red2.log'
docker cp pphd-t2red2:/app/.superpowers/run-pphd-t2red2.log .superpowers/run-pphd-t2red2.log
docker rm pphd-t2red2
tail -20 .superpowers/run-pphd-t2red2.log
```

Expected: a collection error, `ImportError: cannot import name
'count_collection_posters' from 'autoposter.config.impact'`.

- [ ] **Step 8: Add the impact arm and serve it**

In `src/autoposter/config/impact.py`, widen the two imports:

```python
from sqlalchemy import func, select
```

```python
from autoposter.db.models import ManagedCollection, MediaItem, Render
```

and append at the end of the file:

```python
async def count_collection_posters(
    session: AsyncSession, before: Config, after: Config
) -> int:
    """How many managed collection posters this edit would re-composite.

    The honesty row the preview owed and did not have (roadmap row 105).
    ``count_affected`` above walks the ``renders`` table and ``_ART_KINDS``
    holds the four ITEM kinds, so a ``collections.poster_title`` edit previewed
    as "no re-renders" while up to every managed collection's poster was about
    to be re-composited and re-uploaded on the next pass. Not wrong -- out of
    scope -- but the page's promise is "see the cost before committing", and
    this is the first setting with a real cost that walk cannot see.

    Zero unless the block actually changed AND at least one side of the edit
    has it switched on: with the gate off the composite is skipped entirely, so
    retuning the boxes moves no bytes and reporting a cost would teach an
    operator to ignore the number.

    An OVER-estimate, in the same direction and for the same reason
    ``count_affected`` is one: a managed collection whose poster comes from the
    operator's own file under ``assets_root``, or a divider whose art is
    already captioned, passes through untouched -- and this count cannot know
    which those are without reading the filesystem. Render it with a "~".

    One ``SELECT COUNT`` and no walk: unlike the render preview there is
    nothing per-row to recompute, because a collection poster carries no
    fingerprint. Read-only, like everything else in this module.
    """
    if after.collections.poster_title == before.collections.poster_title:
        return 0
    if not (
        before.collections.poster_title.enabled or after.collections.poster_title.enabled
    ):
        return 0
    total = await session.execute(select(func.count()).select_from(ManagedCollection))
    return int(total.scalar_one())
```

In `src/autoposter/api/routes.py`, widen the import at `:48`:

```python
from autoposter.config.impact import affected_items, count_affected, count_collection_posters
```

and replace the body of `preview_config_overrides` from `impact = None`
through the `return` with:

```python
    impact = None
    collection_posters = 0
    render_affecting = _render_affecting(before, after)
    # A second reason to open a session, and the same "do not pay for a
    # scheduler tweak" posture: a collections.poster_title edit moves no render
    # fingerprint at all (config/loader.py's render_version excludes the whole
    # section) and so reports a null impact, while genuinely re-uploading every
    # managed collection's poster once. Both numbers are true at once.
    poster_change = after.collections.poster_title != before.collections.poster_title
    if render_affecting or poster_change:
        async with request.app.state.session_factory() as session:
            if render_affecting:
                impact = asdict(await count_affected(session, after))
            collection_posters = await count_collection_posters(session, before, after)
    return {
        "version_before": before.version,
        "version_after": after.version,
        "restart_required": _restart_required(before, after),
        "inert": _inert_changes(before, after),
        "impact": impact,
        "collection_posters": collection_posters,
    }
```

and add this paragraph to that function's docstring, after the `impact` one:

```
    ``collection_posters`` is a separate count and deliberately not part of
    ``impact``: ``config/impact.py`` walks the ``renders`` table, which has no
    row for a collection, so a ``collections.poster_title`` edit reports a null
    impact beside a real collection-poster count. Also an over-estimate --
    render it with a "~".
```

- [ ] **Step 9: Write the failing endpoint test, then run both**

Append to `tests/test_api_config_editor.py`, in the `--- preview ---` section:

```python
async def test_a_preview_counts_the_collection_posters_a_row_105_edit_moves(
    client, auth_headers, session, app
):
    """Row 105's honesty row, through the endpoint that serves it.

    A collections edit moves no render fingerprint -- ``render_version``
    excludes the whole section -- so ``impact`` is null here, which is exactly
    the case the count exists for: null impact beside a real cost.
    """
    session.add(
        ManagedCollection(
            library="Movies", title="A Collection", kind="smart",
            definition_hash="d" * 64,
        )
    )
    await session.commit()

    response = await client.post(
        "/api/config/preview", headers=auth_headers,
        json={"document": {"collections": {"poster_title": {"enabled": True}}}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["impact"] is None
    assert body["collection_posters"] == 1
```

Widen that file's `autoposter.db.models` import to include
`ManagedCollection`.

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t2red3 test sh -c \
  'set -o pipefail; pytest tests/test_api_config_editor.py::test_a_preview_counts_the_collection_posters_a_row_105_edit_moves -q 2>&1 | tee /app/.superpowers/run-pphd-t2red3.log'
docker cp pphd-t2red3:/app/.superpowers/run-pphd-t2red3.log .superpowers/run-pphd-t2red3.log
docker rm pphd-t2red3
tail -20 .superpowers/run-pphd-t2red3.log
```

Expected: **FAIL** — `KeyError: 'collection_posters'` if the routes edit has
not been made yet, or a clean PASS if Step 8 is already in. If it passes on
the first run, revert the routes hunk, watch it fail, and restore it: a test
never seen failing proves nothing.

Now run both suites green:

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t2green2 test sh -c \
  'set -o pipefail; pytest tests/test_config_impact.py tests/test_api_config_editor.py -q 2>&1 | tee /app/.superpowers/run-pphd-t2green2.log'
docker cp pphd-t2green2:/app/.superpowers/run-pphd-t2green2.log .superpowers/run-pphd-t2green2.log
docker rm pphd-t2green2
tail -20 .superpowers/run-pphd-t2green2.log
```

Expected: both green, four more tests than before across the two files.

- [ ] **Step 10: Commit the impact row**

```bash
git add src/autoposter/config/impact.py src/autoposter/api/routes.py \
        tests/test_config_impact.py tests/test_api_config_editor.py
git commit -m "feat(config): preview the collection posters a poster_title edit moves

The impact walk reads the renders table and there is no renders row for
a collection, so a collections.poster_title edit previewed as 'no
re-renders' while every managed collection poster was about to be
re-composited and re-uploaded once. The preview now carries that count
beside the null impact -- one SELECT COUNT, zero unless the block
changed and one side of the edit has it switched on, and an over-estimate
in the same direction the render count is (a poster the operator placed
themselves passes through untouched)."
```

- [ ] **Step 11: Write the failing frontend tests**

Append to `frontend/src/pages/Settings.test.tsx`, inside the
`describe("Settings preview", …)` block:

```tsx
  it("counts the collection posters an edit would re-composite, beside a null impact", async () => {
    // The row-105 case exactly: `collections` is excluded from render_version,
    // so the server reports no re-renders AND a real collection-poster cost.
    // Both sentences are true and both have to be on the page.
    stubEditor({
      responses: {
        "/api/config/preview": json({
          version_before: "abc123",
          version_after: "abc123",
          restart_required: [],
          impact: null,
          collection_posters: 41,
        }),
      },
    });
    await renderSettings();

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview");

    const panel = pendingPanel();
    expect(
      within(panel).getByText(/No re-renders/),
    ).toBeInTheDocument();
    // "~", never "41": a poster the operator placed themselves passes through
    // untouched and the server cannot know which those are.
    expect(
      within(panel).getByText(/~41 managed collection posters/),
    ).toBeInTheDocument();
  });

  it("says nothing about collection posters when there are none to re-composite", async () => {
    stubEditor({
      responses: { "/api/config/preview": previewBody(null, "abc123") },
    });
    await renderSettings();

    fireEvent.change(screen.getByLabelText("artwork.poster.text.min_point_size"), {
      target: { value: "24" },
    });
    await click("Preview");

    expect(
      within(pendingPanel()).queryByText(/managed collection posters/),
    ).not.toBeInTheDocument();
  });
```

- [ ] **Step 12: Run the frontend tests to verify they fail**

```bash
docker compose -p pphd run --name pphd-t2fered web npm test \
  > .superpowers/run-pphd-t2fered.log 2>&1
docker rm pphd-t2fered
tail -40 .superpowers/run-pphd-t2fered.log
```

Expected: the first new test FAILS (`Unable to find an element with the text:
/~41 managed collection posters/`); the second PASSES already, because nothing
renders that string yet. That split is the correct RED.

- [ ] **Step 13: Serve the count on the page**

In `frontend/src/api/types.ts`, replace the `ConfigPreviewResponse` interface
— anchor on the type name (`grep -n "interface ConfigPreviewResponse"` to
confirm before editing; it is at `:752-754` on `origin/main`, not a fixed
offset worth trusting) — with:

```ts
export interface ConfigPreviewResponse extends ConfigSaveResponse {
  impact: ConfigImpact | null;
  /** How many managed collection posters a `collections.poster_title` edit
   * would re-composite and re-upload, once. Separate from `impact` because
   * `config/impact.py` walks the `renders` table and a collection has no row
   * there -- so this number is real precisely when `impact` is null. Optional
   * because an older server does not send it, and an OVER-estimate when it
   * does (a poster the operator placed themselves passes through untouched),
   * so render it with a "~". */
  collection_posters?: number;
}
```

In `frontend/src/pages/Settings.tsx`, replace `ImpactReport` (`:510-555`)
with:

```tsx
/** What the preview said this document would cost.
 *
 * `impact: null` is not "zero items": it is the server saying the edit cannot
 * change a rendered image at all, so the walk was never run (routes.py's
 * `_render_affecting`). Rendering it as a count of zero would invite the
 * operator to compare it against a real one.
 *
 * Collection posters are counted separately and are NOT part of `impact`:
 * `config/impact.py` walks the `renders` table, which has no row for a
 * collection, so a `collections.poster_title` edit shows "no re-renders"
 * beside a real collection-poster cost. Both sentences are true at once. */
function ImpactReport({
  impact,
  collectionPosters,
}: {
  impact: ConfigPreviewResponse["impact"];
  collectionPosters: number;
}) {
  const posters =
    collectionPosters > 0 ? (
      <p className="muted config-impact-note">
        {`~${collectionPosters} managed collection posters would be re-composited and re-uploaded, once.`}
      </p>
    ) : null;

  if (impact === null) {
    return (
      <>
        <p className="config-impact none">
          No re-renders — this change does not affect rendered artwork.
        </p>
        {posters}
      </>
    );
  }

  // Every examined row affected is what an artwork edit always looks like, so
  // the copy says that outright rather than presenting the number as though
  // the edit had picked rows out of the library.
  const wholeLibrary = impact.of_total > 0 && impact.affected === impact.of_total;
  const renders = `~${impact.affected} of ${impact.of_total} artwork renders`;
  const kinds = Object.entries(impact.by_art_kind);

  return (
    <div className="config-impact">
      <p className="config-impact-count" title={IMPACT_CAVEAT}>
        {wholeLibrary
          ? `Any artwork change re-renders the whole library — ${renders}.`
          : `${renders} are out of date.`}
      </p>
      {kinds.length > 0 && (
        <>
          <ul className="config-impact-kinds">
            {kinds.map(([kind, affected]) => (
              <li key={kind}>{`${kind}: ${affected}`}</li>
            ))}
          </ul>
          <p className="muted config-impact-note">
            {wholeLibrary
              ? `${IMPACT_BREAKDOWN_WHOLE_LIBRARY_NOTE} ${IMPACT_BREAKDOWN_GATE_NOTE}`
              : IMPACT_BREAKDOWN_GATE_NOTE}
          </p>
        </>
      )}
      {posters}
    </div>
  );
}
```

and at the call site (`:809`) replace

```tsx
              <ImpactReport impact={preview.impact} />
```

with

```tsx
              <ImpactReport
                impact={preview.impact}
                collectionPosters={preview.collection_posters ?? 0}
              />
```

`className="muted config-impact-note"` is reused deliberately rather than a
new `.config-impact-collections`: both classes already have real declarations
in the stylesheet, and roadmap row 108 is this repository's record of what an
undeclared class costs.

- [ ] **Step 14: Run the frontend tests and the type-check**

```bash
docker compose -p pphd run --name pphd-t2fegreen web npm test \
  > .superpowers/run-pphd-t2fegreen.log 2>&1
docker rm pphd-t2fegreen
tail -40 .superpowers/run-pphd-t2fegreen.log

docker compose -p pphd run --name pphd-t2tsc web npx tsc --noEmit \
  > .superpowers/run-pphd-t2tsc.log 2>&1
docker rm pphd-t2tsc
cat .superpowers/run-pphd-t2tsc.log
```

Expected: all vitest files pass with 0 failures and two more tests in
`Settings.test.tsx`; `tsc` produces no output at all.

- [ ] **Step 15: Run the whole backend suite and ruff**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t2lint test sh -c \
  'set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-pphd-t2lint.log'
docker cp pphd-t2lint:/app/.superpowers/run-pphd-t2lint.log .superpowers/run-pphd-t2lint.log
docker rm pphd-t2lint
cat .superpowers/run-pphd-t2lint.log

docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphd-t2full test sh -c \
  'set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pphd-t2full.log'
docker wait pphd-t2full
docker cp pphd-t2full:/app/.superpowers/run-pphd-t2full.log .superpowers/run-pphd-t2full.log
docker rm pphd-t2full
tail -10 .superpowers/run-pphd-t2full.log
```

Expected: `ruff` clean, and **exactly `B + 27` passed** with the same skip
count as the baseline (`B + 15` from Task 1, plus 8 wiring + 3 impact + 1
endpoint here).

- [ ] **Step 16: Commit the frontend**

```bash
git add frontend/src/api/types.ts frontend/src/pages/Settings.tsx \
        frontend/src/pages/Settings.test.tsx
git commit -m "feat(settings): show the collection-poster cost the render walk cannot see

A collections.poster_title edit reports a null impact -- the section is
excluded from render_version, so no fingerprint moves -- while genuinely
re-uploading every managed collection's poster once. The preview now
renders both sentences, with a '~' because the count is an over-estimate:
a poster the operator placed themselves passes through untouched and the
server cannot know which those are."
```

```bash
docker compose -p pphd down
```

---

## Task 3: the example config, the roadmap cell, and the wrap

**Files:**
- Modify: `config/autoposter.example.yaml` — after `posters: true`, at `:167`
  on `origin/main`
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:207` (row
  105)
- Test: the whole suite, `ruff`, `npm test`, `npx tsc --noEmit`

**Interfaces:**
- Consumes: everything Tasks 1 and 2 produced. Produces no new symbol.

---

- [ ] **Step 1: Prove the laws with the diff, before writing any prose**

**The base of this diff is `origin/main`, the ref this branch was cut from.**
Diff against it directly:

```bash
git diff --stat origin/main...HEAD
git diff --name-only origin/main...HEAD | sort
```

Expected — **exactly sixteen** paths at this step, and nothing else. The list
below enumerates **eighteen** because two of them are written later in this task
and are annotated with the step that adds them; re-run the same command after
Step 5 and all eighteen must be present:

```
docs/superpowers/plans/2026-09-05-phase-d-collection-title.md
frontend/src/api/types.ts
frontend/src/pages/Settings.test.tsx
frontend/src/pages/Settings.tsx
src/autoposter/api/routes.py
src/autoposter/collections/lists.py
src/autoposter/collections/poster_title.py
src/autoposter/collections/posters.py
src/autoposter/collections/reconcile.py
src/autoposter/collections/smart.py
src/autoposter/config/impact.py
src/autoposter/config/schema.py
tests/test_api_config_editor.py
tests/test_collection_poster_title.py
tests/test_collection_poster_wiring.py
tests/test_config_impact.py
config/autoposter.example.yaml                                (added by Step 2)
docs/superpowers/specs/2026-08-22-full-parity-roadmap.md       (added by Step 5)
```

The three reconciler files are the definition-hash suffix term and nothing
else: one import line, one defaulted parameter, one payload element and one
call site each. If `git diff origin/main...HEAD --
src/autoposter/collections/reconcile.py src/autoposter/collections/smart.py
src/autoposter/collections/lists.py` shows anything beyond those twelve
hunks — a changed short-circuit condition, a touched `separator_hash`, a
reordered payload — **STOP**: the blast radius has grown past what A-9 bought.

**If `src/autoposter/render/pipeline.py`, `src/autoposter/config/loader.py`,
anything under `src/autoposter/overlays/` or `src/autoposter/badges/`,
`src/autoposter/scheduler/merge.py`, `src/autoposter/api/action_center.py`, or
any file under `alembic/` appears in that list, STOP** — a law has been broken
and the commit that broke it must be found and reverted before anything else
happens.

Then prove the storm guard from the other direction, against the real config:

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t3ver test sh -c \
  'python -c "
from autoposter.config.loader import load_config, render_version
c = load_config(\"config/autoposter.example.yaml\")
before = render_version(c)
c.collections.poster_title.enabled = True
c.collections.poster_title.title.max_width = 111
print(before, render_version(c), before == render_version(c))
"'
docker rm pphd-t3ver
```

Expected: the two digests are identical and the line ends `True`. Record both
in the task report. **If they differ, STOP** — the section has somehow reached
`render_version`'s hashed dict and every stored fingerprint in the deployment
is at risk.

- [ ] **Step 2: Write the example-config block**

In `config/autoposter.example.yaml`, immediately after the `posters: true` line
— `:167` on `origin/main`, the line Task 1 Step 1's last grep printed. Anchor
on the text, not the number:

```bash
grep -n "posters: true" config/autoposter.example.yaml
```

Insert after it:

```yaml
  # Row 105. Draw a styled collection title, plus the fixed second line below,
  # onto every managed collection poster this service FETCHES, before it is
  # uploaded. Off by default, and off is byte-identical to not having it.
  #
  # Turning it on moves poster_sha256 for every affected collection ONCE, so
  # each of their posters is re-uploaded once and then settles -- the compare
  # is over content, not over a config hash. It happens on the NEXT pass, not
  # eventually: these settings are also part of the definition hash a pass
  # short-circuits on, so that pass re-writes each affected collection's own
  # summary and sort title too, once, with the values already there. Settings'
  # preview counts them before you commit. Nothing in this section can
  # re-render a single library item: render_version excludes the whole
  # collections section, and with this off nothing here reaches any hash at
  # all -- retune every box below and no collection is revisited.
  #
  # NEVER drawn on: a poster you placed yourself under assets_root (which is
  # also where the manual poster endpoint writes), a divider's art (it already
  # carries the group's name), and any collection this service does not manage.
  #
  # STYLED AFTER Posterizarr's CollectionPosterOverlayPart and
  # CollectionTitlePosterPart, NOT byte-matched to them: no captured
  # Posterizarr output exists anywhere to render against, so the values below
  # are transcribed from the INPUT side and nothing here is a parity proof.
  # Three of them are ours rather than upstream's, and are marked. AddBorder
  # and AddOverlay from that part are not implemented and have no key here.
  #
  # Sizes and offsets are pixels against a 2000-pixel-wide poster and are
  # scaled to whatever poster is actually fetched. text_offset must carry an
  # explicit sign; gravity is north, center or south.
  #
  # font: any file under fonts_root, or one of the bundled faces
  # (Inter-Medium.ttf, Inter-Bold.ttf). Posterizarr's own Colus-Regular.ttf is
  # not shipped -- drop your copy into fonts_root and name it here to use it.
  # poster_title:
  #   enabled: false
  #   collection_line_text: COLLECTION # the fixed second line; empty draws none
  #   title: # CollectionPosterOverlayPart, transcribed (except font)
  #     font: Inter-Medium.ttf # ours: Colus-Regular.ttf is not vendored
  #     min_point_size: 100
  #     max_point_size: 250
  #     max_width: 1900
  #     max_height: 500
  #     text_offset: "+300"
  #     gravity: south
  #   collection_line: # colour/stroke/caps from CollectionTitlePosterPart
  #     font: Inter-Medium.ttf # ours, as above
  #     min_point_size: 40 # ours: upstream's 83-250 is for a title, not a word
  #     max_point_size: 90 # ours
  #     max_width: 1200
  #     max_height: 150 # ours
  #     text_offset: "+120" # ours: upstream gives both parts +300 because they
  #                         # are two poster TYPES there, not two blocks on one
  #                         # image -- copying it would draw this through the title
  #     gravity: south
```

The whole block is commented out deliberately. The forward guard in
`tests/test_example_config_matches_schema.py` checks that every key the example
*writes* exists in the schema, and the reverse guard is section-level and says
so ("Section level only, deliberately") — `collections:` already exists, so
this file is free to document the section without committing to a value.

- [ ] **Step 3: Run the config suites**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t3cfg test sh -c \
  'set -o pipefail; pytest tests/test_example_config_matches_schema.py tests/test_config_descriptions.py tests/test_config_impact.py -q 2>&1 | tee /app/.superpowers/run-pphd-t3cfg.log'
docker cp pphd-t3cfg:/app/.superpowers/run-pphd-t3cfg.log .superpowers/run-pphd-t3cfg.log
docker rm pphd-t3cfg
tail -10 .superpowers/run-pphd-t3cfg.log
```

Expected: all three green, with no change in count from Task 2.

- [ ] **Step 4: Commit the example config**

```bash
git add config/autoposter.example.yaml
git commit -m "docs(config): document the collections.poster_title section

Commented out, like group_order and separator_style above it: the
section-level guard means the example need not commit to a value, and a
commented block can carry the disclosures a key cannot -- what is
transcribed from Posterizarr and what is ours, what is never drawn on,
and the one-time re-upload turning the gate on costs."
```

- [ ] **Step 5: Write the roadmap row**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, row 105 is one
line at `:207`. Replace its **third** cell (the description, between the
second and third `|`) with the existing text plus this appended paragraph, and
its **fourth** cell (`M — reuse compositor + textfit; new config, font, tests;
no oracle`) with `M — DELIVERED (phase D)`:

```
**answered phase D (row CLOSED):** delivered — `collections.poster_title`
(`config/schema.py::CollectionPosterTitleConfig`) plus a standalone
`src/autoposter/collections/poster_title.py`, wired into
`collections/posters.py::apply_poster` above the sha256 that decides whether
anything is uploaded. **Styled after Posterizarr's
`CollectionPosterOverlayPart`/`CollectionTitlePosterPart`, NOT byte-matched to
them** — Kometa has no equivalent, no captured Posterizarr output exists on the
live server or in `tests/fixtures/`, and the operator's Posterizarr *config* is
the input side rather than an oracle. This is parity-only-by-source-reading and
the era recon's adjudication 4 is why the row says so instead of letting
"parity-only" read as "parity-proven". Three shipped defaults are OURS and are
marked as such in the schema and the example config: the font (Posterizarr's
`Colus-Regular.ttf` is not vendored — licence unverified, and with no oracle
the exact bytes buy nothing verifiable, so the default is the bundled
`Inter-Medium.ttf` from row 100's C2b rung and an operator's own Colus under
`fonts_root` wins), and the fixed line's offset and box (upstream gives both
parts `text_offset "+300"` because they are two poster TYPES there, not two
blocks on one image). **Divergences, each deliberate:** the composite draws
with **Pillow, not ImageMagick** — `textfit.fit_point_size` shells out to
`magick` and every composite test would then be deselected from CI's main lane,
so `prepare_text` and `FitResult` are reused and the fit is measured with
Pillow's own metrics, binary-searched over `[min_point_size, max_point_size]`;
a title that will not fit **clamps and draws with a warning** rather than being
refused the way `separator_art._render` refuses, because a divider's label is
one of ten short words this service chooses while a collection title is
whatever the operator named their collection (the one refusal is an empty
title); and `AddBorder`/`AddOverlay` from `CollectionPosterOverlayPart` are
**not implemented and deliberately have no key**, so nothing advertises a
setting that does nothing. **Scope, narrowly:** managed collections only, and
only the posters this service fetches or caches — a poster the operator placed
under `assets_root` (which is also where `api/manual.py`'s poster endpoint
writes) passes through untouched, a divider is never captioned twice whether
its art was generated here or fetched already-captioned, and
`apply_local_posters_to_unmanaged` is not reached at all. **The storm answer:**
the section lives under `collections` and never under `artwork` —
`render_version` (`config/loader.py:48-55`) hashes `config.artwork.model_dump()`
wholesale and excludes `collections`, so **no stored render fingerprint can
move and no library item re-renders**, proven both by
`tests/test_collection_poster_title.py::test_the_section_moves_no_render_version`
and by a digest comparison over the real example config. **The one-time cost,
disclosed rather than hidden:** turning the gate on moves `poster_sha256` once
for every managed collection whose poster this service fetches, so each is
re-uploaded once and then settles — bounded, opt-in and self-correcting,
because the compare is over content rather than over a config hash. And it
happens on the NEXT pass rather than eventually, because the section is also a
suffix term in the three definition hashes those collections short-circuit on
(`reconcile.definition_hash`, `smart.smart_definition_hash`,
`lists._members_hash`, each via `poster_title.poster_title_parts`, by the same
idiom `lists._settings_parts` already occupies in all three); without it a
stable managed collection would keep short-circuiting on `definition_current`
and would be re-composited only when something unrelated moved its definition.
That one pass also re-writes each affected collection's filter, summary and
sort title with the values already there — the same one-pass idempotent cost
row 49's `poster_key` term and phase 10a-2's `url` term each charged for the
same reason. With the gate OFF the term is empty on an explicit `enabled`
check, so every hash already stored still matches byte for byte and shipping
the section switched off re-reconciles nothing; `separator_hash` never gets the
term at all, because a divider is never captioned and its bytes cannot move.
The
settings preview now says so: `POST /api/config/preview` carries a
`collection_posters` count beside its (null) `impact`, because
`config/impact.py` walks the `renders` table and a collection has no row there
— the first setting with a real cost that walk could not see. That count is an
over-estimate in the same direction the render one is, and is rendered with a
"~". A compose failure — an unresolvable font named by name, an undecodable
poster, an empty title — reports, uploads nothing and leaves `poster_sha256`
where it was so the next pass retries; it never fails the surrounding pass, and
never puts an unmarked exception's text into a served action (row 213)
```

- [ ] **Step 6: Commit the roadmap**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit -m "docs(roadmap): file and close row 105

The collection-poster title composite ships with its gap stated rather
than smoothed over: no oracle exists for it in either upstream, three
defaults are ours and are marked, two Posterizarr keys are deliberately
unimplemented, and turning the gate on re-uploads each managed
collection's poster once."
```

- [ ] **Step 7: The final full run — backend, lint, frontend, types**

```bash
docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pphd-t3lint test sh -c \
  'set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-pphd-t3lint.log'
docker cp pphd-t3lint:/app/.superpowers/run-pphd-t3lint.log .superpowers/run-pphd-t3lint.log
docker rm pphd-t3lint
cat .superpowers/run-pphd-t3lint.log

docker compose -p pphd -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphd-t3full test sh -c \
  'set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pphd-t3full.log'
docker wait pphd-t3full
docker cp pphd-t3full:/app/.superpowers/run-pphd-t3full.log .superpowers/run-pphd-t3full.log
docker rm pphd-t3full
tail -10 .superpowers/run-pphd-t3full.log

docker compose -p pphd run --name pphd-t3fe web npm test \
  > .superpowers/run-pphd-t3fe.log 2>&1
docker rm pphd-t3fe
tail -20 .superpowers/run-pphd-t3fe.log

docker compose -p pphd run --name pphd-t3tsc web npx tsc --noEmit \
  > .superpowers/run-pphd-t3tsc.log 2>&1
docker rm pphd-t3tsc
cat .superpowers/run-pphd-t3tsc.log
```

Read all four logs. Expected: `ruff` clean; **exactly `B + 27` passed** with
the baseline's skip count (Task 3 adds no test, so this is Task 2's number
unchanged); vitest all green with two more tests than the baseline in
`Settings.test.tsx`; `tsc` silent.

```bash
docker compose -p pphd down
cp .superpowers/run-pphd-*.log "D:/Sites/autoposter/.superpowers/"
```

The log copy is not optional: `.superpowers/` is gitignored, so every run
record in this worktree is lost the moment the worktree is pruned.

- [ ] **Step 8: Push and open the PR**

```bash
git push -u origin feat/phase-d-collection-title
```

Open the PR against `main`. Body, **first line verbatim**:

```
Standalone branch off main; no merge-order dependency.
```

Then, in this order:

1. **What ships** — `collections.poster_title` and
   `collections/poster_title.py`, wired into `apply_poster` between the last
   source rung and the sha256.
2. **The no-oracle disclosure, in full** — "styled after Posterizarr's
   `CollectionPosterOverlayPart`/`CollectionTitlePosterPart`, not byte-matched
   to them. Kometa has no equivalent and no captured Posterizarr output exists
   to render against, so this is parity-only-by-source-reading. The operator's
   Posterizarr config supplied the values; a config is not a proof."
3. **The three defaults that are ours**, each with its reason.
4. **The two Posterizarr keys deliberately not implemented** (`AddBorder`,
   `AddOverlay`) and why they have no key at all.
5. **The storm answer** — `collections`, never `artwork`; the two digests from
   Task 3 Step 1, quoted.
6. **The one-time re-upload disclosure** — "the `poster_title` settings are
   part of the bytes hashed into `poster_sha256`, so turning the gate on moves
   that digest once for every managed collection whose poster this service
   fetches: each is re-uploaded once and then settles. It happens on the NEXT
   pass, because the settings are also a suffix term in the three definition
   hashes those collections short-circuit on — `definition_hash`,
   `smart_definition_hash` and `_members_hash`, via `poster_title_parts`, by
   the same idiom `_settings_parts` already occupies in all three. That one
   pass also re-writes each affected collection's filter, summary and sort
   title with the values already there, which is the same idempotent one-pass
   cost row 49's `poster_key` term and phase 10a-2's `url` term each charged.
   With the gate off the term is empty on an explicit `enabled` check, so every
   hash already stored still matches byte for byte and shipping the section
   switched off re-reconciles nothing; `separator_hash` is deliberately left
   alone, because a divider is never captioned. The Settings preview counts the
   posters before you commit."
7. **What is never touched** — an operator's own poster file, the manual
   endpoint's write, dividers, unmanaged collections, `render/pipeline.py`, and
   every stored fingerprint.
8. **The test list**, by count: **15** unit pins (3 config + 12 module), **8**
   in `tests/test_collection_poster_wiring.py` (7 through the real reconcilers
   — including the gate-on roll-out with nothing nulled and the changed-knob
   property — and 1 over the three definition-hash functions, pinning that the
   gate-off payload is byte-identical), 3 impact pins, 1 through
   `POST /api/config/preview`, 2 vitest — **27 new pytest and 2 new vitest**.

**No AI attribution anywhere in the body.**

---

## Self-Review

**Spec coverage — C1's ten adjudications, each to a task.**
A-1 (the config home) → Task 1 Steps 4/6 and its `test_the_section_moves_no_render_version`, plus Task 3 Step 1's digest comparison.
A-2 (managed only; operator posters untouched) → Task 2 Steps 1/3 (`composable` on the local rung) and `test_an_operators_own_poster_file_passes_through_untouched`; `apply_local_posters_to_unmanaged` is named in Global Constraint 4 as untouched and Task 3 Step 1 proves it.
A-3 (separator art excluded) → Task 2 Step 3's `composable = cached is not generated` **and** the `kind != SEPARATOR_KIND` condition, pinned by `test_a_divider_is_never_captioned_twice`.
A-4 (the font) → Task 1 Step 1's C2b gate, `_font`'s two rungs, `test_the_operators_own_font_beats_the_bundled_face` and `test_a_font_that_resolves_nowhere_is_refused_by_name`, plus Task 2's
`test_a_font_that_resolves_nowhere_reports_a_skip_and_uploads_nothing`.
A-5 (the disclosure) → the module docstring, the model docstring, the example-config block, the roadmap cell (Task 3 Step 5) and PR body point 2.
A-6 (clamp, refuse only empty) → `_fit`'s truncated arm, `_wrap(hard=True)`, `test_a_long_title_is_clamped_rather_than_refused`, `test_an_empty_title_is_refused_by_name`.
A-7 (the impact row) → Task 2 Steps 6-14, backend and page both.
A-8 (standalone module, sibling model) → the new module exists and `render/pipeline.py` is proven untouched (Task 3 Step 1); `CollectionPosterTitleConfig(BaseModel)`, not `ArtKindConfig`.
A-9 (the one-time cost, and its promise that the roll-out is INSTANT) → the section is an input to both digests, not just to `poster_sha256`: `poster_title.poster_title_parts(config)` is a suffix term in `reconcile.definition_hash`, `smart.smart_definition_hash` and `lists._members_hash` (Task 2 Step 3), so the pass after the operator saves is the one that re-composites. Three pins, all in Task 2 Step 1: `test_the_gate_on_re_uploads_each_managed_poster_exactly_once` reaches `apply_poster` for real across three passes (gate off → gate on, one upload whose pixels differ → gate on again, no upload) with **nothing nulled**, so the gate flip alone is what defeats the short-circuit and dropping the term turns it red; `test_the_gate_off_leaves_every_definition_hash_byte_identical` pins the other direction over all three functions, on an explicit `if not settings.enabled` rather than on what the defaults dump to; `test_a_changed_text_knob_re_uploads_each_managed_poster_exactly_once` pins the third property. `separator_hash` is excluded by the same A-3 reasoning that excludes dividers from the composite, and `tests/test_collection_group_separators.py`'s literal `SHIPPED_HASH` is the alarm if that is ever forgotten. The in-process determinism the content compare rests on is pinned separately by Task 1's `test_the_same_inputs_give_byte_identical_output`; plus the example-config comment, the field description, the roadmap cell and PR body point 6.
A-10 (sequencing) → "Branch, cut point and merge order": a standalone cut from `origin/main` with no merge-order dependency, verified fresh by Task 1 Step 1.
The SCOPE's three tasks map one-to-one onto Tasks 1, 2 and 3.

**Placeholder scan.** None. Every code step carries the code verbatim; every
run step carries the command and the expected output, including the two steps
whose correct RED is a *partial* failure (Task 2 Steps 2 and 12), which are
called out as such so a partial pass is not mistaken for a broken test.

**Type consistency.** `compose_collection_title(settings, fonts_root, data,
title) -> bytes` is declared once (Task 1 *Interfaces*), defined once (Task 1
Step 11) and called once (Task 2 Step 3), with `settings` =
`config.collections.poster_title` and `fonts_root` = `Path(config.fonts_root)`
matching the `Path | None` annotation. `CollectionTitleRefused` is raised in
three places in one module and caught in exactly one. `count_collection_posters(session,
before, after) -> int` is declared, defined and called with the same three
arguments in the same order. `CollectionPosterTitleConfig`'s four field names
(`enabled`, `title`, `collection_line`, `collection_line_text`) are spelled
identically in the schema, the module, the tests, the example config and the
endpoint test. `collection_posters` is spelled the same in the endpoint, the
TypeScript interface, the component prop's source (`preview.collection_posters
?? 0`) and both vitest fixtures. `REFERENCE_WIDTH` is defined once and asserted
once. `poster_title_parts(config) -> list[str]` is declared once (Task 2
*Interfaces*), defined once (Task 2 Step 3) and called from exactly three
places, each spelled `*poster_title_parts(config)` as the last element of the
payload list; the three hash functions it feeds each take the new argument as a
trailing `config=None`, and each of their three call sites passes the same
in-scope `config` those functions' own callers already hand to
`posters_enabled(config, http)` a line or two away.

**Known gaps, deliberately not closed.**
(a) An upstream chart or award poster that already carries its own baked title
will now carry two, with the gate on. That is the direct consequence of A-2's
"composite everything this service fetches", it is visible the moment an
operator turns the gate on, and inventing per-kind exclusions beyond the
divider would be guessing at which of six Default-Images families carry text.
Named in the row cell's scope sentence, not coded around.
(b) `count_collection_posters` counts every managed row, including the ones
whose poster is an operator file or a divider. Over-estimate, stated in the
docstring, on the page as a "~", and in the PR body — the alternative is
reading the filesystem from inside a config preview.
(c) Nothing in this plan vendors `Colus-Regular.ttf`. A-4 leaves that to the
operator's `fonts_root`, and with no oracle the exact bytes prove nothing that
`Inter-Medium.ttf` does not.

**A gap this revision CLOSED rather than filed.** An earlier draft filed "the
gate-on roll-out is lazy, not instant" as a fourth known gap: `poster_title`
was not part of `definition_hash`, so a stable managed collection kept
short-circuiting on `definition_current` and its poster was re-composited only
on whatever later pass moved its definition for some other reason — while A-9
promised the re-upload happened once and immediately. That was not a gap to
disclose; it was the feature not working as adjudicated, and the A-9 pin was
reaching `apply_poster` by nulling `poster_sha256`, which proved only the
caller's fall-through. The section is now a suffix term in the three definition
hashes (Task 2 Step 3), the pin nulls nothing, and the cost the old gap cited
as prohibitive is stated instead of avoided: three files gain one import, one
defaulted parameter, one payload element and one call site each, and the
gate-on pass re-writes each affected collection's filter, summary and sort
title once with the values already there — the same idempotent one-pass charge
row 49's `poster_key` term and phase 10a-2's `url` term each already made, and
a smaller one than a feature that silently does not roll out.
`count_collection_posters` is still an over-estimate and still rendered with a
"~", for reason (b) alone.
