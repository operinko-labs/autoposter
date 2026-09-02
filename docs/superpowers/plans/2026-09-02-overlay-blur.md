# Overlay Blur/Backdrop Primitives (row 50, overlay era Phase B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** un-refuse the two overlay-name primitives roadmap row 50 asks for — `backdrop` (a full-canvas solid-colour layer) and `blur(NN)` (a whole-poster Gaussian blur) — inside the overlay engine Phase A already shipped, with no change anywhere outside `overlays/schema.py`, `overlays/render.py` and `badges/compose.py`.

**Architecture:** both primitives are Pillow-side, not ImageMagick argv (the era recon's own reversal, ratified in `p-overlay-b-facts.md` C1.1 — `render/compositor.py` is untouched). `backdrop` is the smaller of the two: `overlays/schema.py` stops refusing the name and stops requiring coordinates for it, and `overlays/render.py::draw_overlay` gains the other arm of its existing `-1` sentinel (full canvas instead of shrink-to-content) — no new code path. `blur(NN)` is architecturally different: it is not a per-definition draw call at all, but a *per-item pre-pass* — `badges/compose.py::compose` scans the operator's already-resolved (suppressed, grouped) definitions for every `blur(NN)` name, takes the MAXIMUM `NN` across all matches, and applies one `ImageFilter.GaussianBlur` to the whole base canvas before any badge or overlay — built-in or operator-defined — is composited on top. A malformed `blur(NN)` name is refused at validation, diverging from Kometa's own silent degrade-to-`blur(50)` on purpose (adjudication A3).

**Tech Stack:** Python 3.13, Pydantic v2, Pillow (`ImageFilter.GaussianBlur` — already-vendored stdlib-adjacent, no new dependency), pytest.

---

## Branch and cut point

- Branch name: **`feat/overlay-blur`**.
- **Cut from `origin/main` after a fetch, verified with CONTENT probes, never a sha probe.** Re-verify the tip at execution time; do not trust any sha recorded in this document.

```bash
git fetch origin
git cat-file -e origin/main:src/autoposter/overlays/schema.py
git cat-file -e origin/main:src/autoposter/overlays/render.py
git cat-file -e origin/main:src/autoposter/badges/compose.py
git cat-file -e origin/main:tests/test_overlay_engine_golden.py
git cat-file -e origin/main:tests/test_overlay_entrypoint.py
git show origin/main:src/autoposter/overlays/schema.py | grep -q 'DEFERRED_NAMES = ("backdrop",)' && echo BACKDROP-DEFERRED-PRESENT
git show origin/main:src/autoposter/overlays/schema.py | grep -q 'DEFERRED_NAME_PREFIXES = ("blur",)' && echo BLUR-DEFERRED-PRESENT
git show origin/main:src/autoposter/overlays/render.py | grep -q 'the backdrop name, which the schema refuses' && echo BACKDROP-DOCSTRING-PRESENT
git show origin/main:src/autoposter/badges/compose.py | grep -q 'def _resolve_definitions' && echo RESOLVE-DEFINITIONS-PRESENT
git show origin/main:src/autoposter/badges/compose.py | grep -q 'def _draw_definitions' && echo DRAW-DEFINITIONS-PRESENT
git show origin/main:src/autoposter/render/pipeline.py | grep -q 'async def apply_badges' && echo APPLY-BADGES-PRESENT
git show origin/main:tests/test_overlay_engine_golden.py | grep -q 'POSTER_PIXELS_SHA = "fc8793' && echo GOLDEN-GATE-PRESENT
git show origin/main:tests/test_overlay_entrypoint.py | grep -q '576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd' && echo FINGERPRINT-STORM-PIN-PRESENT
git checkout -b feat/overlay-blur origin/main
git rev-parse HEAD
```

All seven `cat-file` probes must succeed and all eight markers must print. `BACKDROP-DEFERRED-PRESENT`/`BLUR-DEFERRED-PRESENT`/`BACKDROP-DOCSTRING-PRESENT` prove this is the pre-fix code this plan's steps assume; `FINGERPRINT-STORM-PIN-PRESENT` is the literal this plan must leave byte-identical (the storm pin, re-asserted in `p-overlay-b-facts.md` C2). If any probe fails, **STOP and report** — do not cut from a stale ref.

---

## Global Constraints

Every task's requirements implicitly include this section.

1. **Scope is Pillow-side only.** `src/autoposter/render/compositor.py`, `src/autoposter/render/textfit.py`, `tests/test_production_parity.py` and `src/autoposter/badges/spec.py` are **never touched**. This phase's seams are entirely inside `overlays/schema.py`, `overlays/render.py` and `badges/compose.py`.
2. **The five parity pin files are never edited:** `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_compose.py`, `tests/test_badge_parity.py`. Changing an assertion in any of them is a phase failure; **STOP and report** rather than editing one.
3. **The golden gate is never edited.** `tests/test_overlay_engine_golden.py`'s two recorded hashes (`POSTER_PIXELS_SHA`, `TITLE_CARD_PIXELS_SHA`) are Phase A's own byte-identity baseline. Neither this phase's code changes the codepath that produces them (an empty `definitions` list touches nothing new), so both must keep passing UNMODIFIED. If either drifts, **STOP and report** — do not re-record it.
4. **The storm pin is never edited.** `tests/test_overlay_entrypoint.py::test_the_gate_off_fingerprint_is_pinned_byte_identical` pins the literal `576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd`. Neither primitive touches `badge_fingerprint` or `_definitions_digest` — the existing `if definitions:` non-empty guard already covers them, the same way it covers every other definition. This test must keep passing UNMODIFIED.
5. **Refuse loudly on a bad `blur(NN)`, diverging from Kometa on purpose (adjudication A3).** Kometa silently substitutes `blur(50)` on a malformed `blur(NN)` name rather than raising (`overlay.py:223-231`, the one attribute-parse path in its whole class that degrades instead of failing). This schema refuses instead, matching every other validator in `overlays/schema.py` (its own docstring, "REFUSED here rather than accepted-and-ignored"). The divergence is recorded as a comment beside the check, citing Kometa's own behaviour.
6. **Operator mistakes land in the existing warn-and-skip net, unchanged.** Neither primitive introduces a new failure mode: an unresolvable `file:`/`builtin:`/`url:` image, a bad font, or a download failure already warn-and-skip per-definition in `render/pipeline.py::apply_badges`'s existing loop and `badges/compose.py::_draw_definitions`'s existing per-definition try/except. No new code is needed there, and none is added.
7. **The gated-feature entry-point law, both ways.** Both primitives are proven through the REAL entry point, `render.pipeline.apply_badges`, not through `overlays/render.py` or `badges/compose.py::compose` alone. With no `blur`/`backdrop` definition configured (the default and every existing operator config), output stays byte-identical to Phase A's own recorded baseline — this is what Constraints 3 and 4 already gate. With one configured, `apply_badges` must actually draw it.
8. **RED before GREEN on every behavioural step.** Write the failing test, run it, confirm it fails for the stated reason, then implement. A test that cannot RED against the pre-fix code because the property it proves is *inherited* from an already-tested generic mechanism (suppression-before-blur-scan, inherited from `_resolve_definitions`) is instead proven **falsifiable by deliberate mutation** — write it, confirm it passes, temporarily break the mechanism, confirm it fails, revert, confirm it passes again.
9. **No config schema change.** `config.badges.definitions` already exists (Phase A); an operator writes `blur(NN)`/`backdrop` entries into the exact same list. No new field, no new `BadgesConfig` key.
10. **Container discipline.** Unique compose project per task (**`povb1`, `povb2`**), always with the `.superpowers/isolated-db.yml` overlay. A short targeted run uses `run --rm test pytest ...`. A full-suite run uses **no `--rm`**: started detached (`-d --name`), waited on with a foreground `docker wait`, read back with `docker cp` to a path under `.superpowers/`, then `docker rm`. Teardown is `docker compose -p <project> down` — **never** `down -v`.
11. **No network in tests.** `tests/conftest.py`'s `no_outbound_network` autouse fixture is not bypassed; nothing in this phase needs it.
12. **Commits** are conventional, `--no-gpg-sign` (fall back to `--no-gpg-sign` explicitly if signing would otherwise block), staged **by name** (never `git add -A`), and carry **no AI attribution** of any kind. Same for the PR body.
13. **Read-only outside the named files.** At the end of each task, `git diff --stat <task-start-sha> -- src/ tests/` must list only files named in that task's **Files** block.
14. **T1 measures before it cites.** The full-suite baseline, the golden gate and the storm-pin literal are all confirmed passing on the FRESHLY CUT branch, before any code change — not assumed from a prior phase's report.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `src/autoposter/overlays/schema.py` | un-refuse `backdrop` (with a coordinate exemption) and `blur(NN)` (with range-validated parsing via a new `blur_amount` property) | T1 |
| `src/autoposter/overlays/render.py` | `draw_overlay`'s `-1` sentinel gains the `backdrop` name's full-canvas arm | T1 |
| `src/autoposter/badges/compose.py` | new `_blur_amount` pre-pass in `compose()`, applied before any badge or overlay composites; blur-only definitions skipped in `_draw_definitions` | T1 |
| `tests/test_overlay_schema.py` | backdrop un-refusal + coordinate exemption pins; `blur(NN)` parsing + bad-NN refusal pins | T1 |
| `tests/test_overlay_render.py` | backdrop full-canvas sentinel pins (both arms) | T1 |
| `tests/test_overlay_entrypoint.py` | blur max-across-matches + suppression-before-max pins (compose-level); blur and backdrop drawn through the real `apply_badges` | T1 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | close row 50; record the era-recon correction | T2 |
| `.superpowers/sdd/progress.md` | the phase's wrap entry | T2 |

**Never modified by any task:** `src/autoposter/render/compositor.py`, `src/autoposter/render/textfit.py`, `src/autoposter/badges/spec.py`, `src/autoposter/config/schema.py`, `src/autoposter/render/pipeline.py`, `src/autoposter/overlays/sources.py`, `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_compose.py`, `tests/test_badge_parity.py`, `tests/test_overlay_engine_golden.py`, `tests/test_production_parity.py`.

`overlays/sources.py` is deliberately left alone (adjudication A5): a `blur(NN)`/`backdrop` definition with no `file`/`builtin`/`url` falls through to the name-keyed local fallback, which does one harmless no-op stat call and returns `None` — a cosmetic divergence from Kometa's "skipped entirely," not a correctness bug, and not worth the extra surface for this phase.

---

## Interfaces produced by this phase

```python
# src/autoposter/overlays/schema.py
class OverlayDefinition(BaseModel):
    ...
    @property
    def blur_amount(self) -> int | None:
        """The NN in a `blur(NN)` overlay name, or None for any other name."""
```

```python
# src/autoposter/badges/compose.py
def _blur_amount(definitions: list[OverlayDefinition]) -> int:
    """The maximum blur_amount across an already-resolved definitions list, or 0."""
```

`draw_overlay`'s signature (`overlays/render.py`) is unchanged — the backdrop arm is internal to its existing sentinel resolution.

---

## Task 1: Implementation — schema, sentinel, pre-pass, full test law

**Files:**
- Modify: `src/autoposter/overlays/schema.py`
- Modify: `src/autoposter/overlays/render.py`
- Modify: `src/autoposter/badges/compose.py`
- Modify: `tests/test_overlay_schema.py`
- Modify: `tests/test_overlay_render.py`
- Modify: `tests/test_overlay_entrypoint.py`

**Interfaces:**
- Consumes: `OverlayDefinition` (`overlays/schema.py`), `draw_overlay(layer, definition, canvas, *, image=None, text=None, font=None)` (`overlays/render.py`), `compose(base_path, art_kind, inputs, fingerprint=None, definitions=None, resolved_images=None, fonts_root=None)`, `_resolve_definitions(definitions)`, `_draw_definitions(poster, canvas, inputs, definitions, resolved_images, fonts_root)` (`badges/compose.py`), `apply_badges(session, config, render, item, plex_item, facts, probe=None, *, http=None)` (`render/pipeline.py`) — all pre-existing, unchanged signatures.
- Produces: `OverlayDefinition.blur_amount -> int | None`; `_blur_amount(definitions: list[OverlayDefinition]) -> int`.

- [ ] **Step 1: Measure the full-suite baseline on the freshly cut branch**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povb1-baseline test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-povb-t1-baseline.log'
docker wait povb1-baseline
docker cp povb1-baseline:/app/.superpowers/run-povb-t1-baseline.log .superpowers/run-povb-t1-baseline.log
docker rm povb1-baseline
tail -5 .superpowers/run-povb-t1-baseline.log
```

Expected: all green. Record the exact pass count — this is the baseline every later step reconciles against, not a number predicted by this plan (Global Constraint 14). Also confirm the two pins this phase must not move:

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_engine_golden.py tests/test_overlay_entrypoint.py::test_the_gate_off_fingerprint_is_pinned_byte_identical -v
```

Expected: both PASS on the unmodified pre-fix code. Record their result in the task report before any implementation step below.

- [ ] **Step 2: RED — schema tests for backdrop's un-refusal and coordinate exemption**

In `tests/test_overlay_schema.py`, replace `test_the_deferred_special_names_are_refused_not_silently_accepted` (the test that currently asserts both names are refused) with:

```python
def test_the_backdrop_name_is_no_longer_refused():
    """Roadmap row 50: `backdrop` un-refused. Its whole point is a
    full-canvas layer, so unlike every other overlay with a backdrop, it
    needs no coordinates (probe section 1.1, overlay.py:325-330: offsets
    default to 0 for this name rather than being required)."""
    d = _d(name="backdrop", back_color="#00000099")
    assert d.has_back is True
    assert d.horizontal_offset is None


def test_a_non_backdrop_overlay_with_a_backdrop_still_needs_coordinates():
    """The exemption above is specific to the literal name 'backdrop', not
    to every name that merely contains the word -- unchanged from before
    this phase."""
    with pytest.raises(ValidationError):
        _d(name="backdrop_ribbon", back_color="#00000099")


def test_blur_nn_is_no_longer_refused_and_is_parsed_from_the_name():
    """Roadmap row 50: `blur(NN)` un-refused. Parsed on demand via
    `blur_amount` rather than stored as a separate field -- `badges/
    compose.py`'s pre-pass reads it directly off the definition."""
    assert _d(name="blur(30)").blur_amount == 30
    assert _d(name="blur(100)").blur_amount == 100
    assert _d().blur_amount is None


def test_a_malformed_blur_nn_is_refused_not_silently_degraded_to_fifty():
    """Adjudication A3: Kometa itself silently substitutes blur(50) on a
    malformed blur(NN) name (probe section 1.1, overlay.py:223-231) -- the
    one attribute-parse path in its whole class that degrades instead of
    raising. This schema refuses instead, matching every other validator in
    this file (its own docstring, lines 8-11) rather than letting a typo
    silently change blur strength."""
    with pytest.raises(ValidationError):
        _d(name="blur(0)")
    with pytest.raises(ValidationError):
        _d(name="blur(101)")
    with pytest.raises(ValidationError):
        _d(name="blur(abc)")
    with pytest.raises(ValidationError):
        _d(name="blur")
```

- [ ] **Step 3: Run the new schema tests, confirm they fail for the right reason**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_schema.py -k "backdrop_name_is_no_longer_refused or blur_nn_is_no_longer_refused" -v
```

Expected: both FAIL — `test_the_backdrop_name_is_no_longer_refused` and `test_blur_nn_is_no_longer_refused_and_is_parsed_from_the_name` both raise `ValidationError` inside `_d(...)`, because the pre-fix code still refuses both names outright. `test_a_non_backdrop_overlay_with_a_backdrop_still_needs_coordinates` and `test_a_malformed_blur_nn_is_refused_not_silently_degraded_to_fifty` already pass (they assert refusal, which the pre-fix blanket refusal already produces) — note this in the report; it is expected, not a defect.

- [ ] **Step 4: GREEN — implement the schema changes**

In `src/autoposter/overlays/schema.py`, replace the imports and the deferred-name constants (currently lines 13-20 — the replacement REMOVES `DEFERRED_NAMES` and `DEFERRED_NAME_PREFIXES` entirely; this phase builds both banked forms, so the constants and their two-line comment must not survive as orphaned dead code):

```python
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Probe section 1.1, special overlay-name forms. Both are banked, neither is
# built this phase.
DEFERRED_NAMES = ("backdrop",)
DEFERRED_NAME_PREFIXES = ("blur",)
```

with:

```python
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Probe section 1.1: `blur(NN)` requires 0 < NN <= 100, parsed from the name
# itself, not a separate field (roadmap row 50).
_BLUR_FORM = re.compile(r"^blur\((\d+)\)$")
```

Then remove the `_validate` clauses that consumed the two deleted constants (grep the file for `DEFERRED_NAMES` and `DEFERRED_NAME_PREFIXES` — zero references must remain; the refusal logic they powered is superseded by the blur/backdrop acceptance below).

Add the `blur_amount` property immediately after the existing `has_back` property (which ends at `return bool(self.back_color or self.back_line_color)`), before `def rgba`:

```python
    @property
    def blur_amount(self) -> int | None:
        """The NN in a `blur(NN)` overlay name, or None for any other name.

        Roadmap row 50. Re-derived from `name` on every access rather than
        cached at construction: `_validate` already proved a `blur`-prefixed
        name parses, so this cannot diverge from what passed validation, and
        a second stored copy is exactly what would drift.
        """
        match = _BLUR_FORM.match(self.name)
        return int(match.group(1)) if match else None
```

Replace the deferred-name refusal block inside `_validate` (currently):

```python
        # Probe section 1.1, special name forms. Deferred, so refused loudly.
        if name in DEFERRED_NAMES or name.startswith(DEFERRED_NAME_PREFIXES):
            raise ValueError(
                f"the {name!r} overlay form is not supported by this service; "
                "see roadmap row 97"
            )
```

with:

```python
        # Probe section 1.1: `blur(NN)` requires 0 < NN <= 100. Kometa's own
        # parser SILENTLY substitutes blur(50) on any parse failure rather
        # than raising (overlay.py:223-231) -- the one attribute-parse path
        # in the whole class that degrades instead of failing the run. This
        # schema diverges on purpose: every other validator here refuses
        # rather than accepts-and-ignores (this module's own docstring), and
        # a typo silently changing blur strength from "faint" to "50" is
        # exactly the silent-wrong-art class this project refuses to ship
        # (roadmap row 50).
        if name.startswith("blur"):
            match = _BLUR_FORM.match(name)
            if match is None or not (0 < int(match.group(1)) <= 100):
                raise ValueError(
                    f"{name!r} is not a valid blur(NN) overlay name; NN must "
                    "satisfy 0 < NN <= 100"
                )
```

Replace the backdrop-coordinate check (currently):

```python
        # Probe section 1.1: a backdrop with no coordinates is refused.
        if self.has_back and self.horizontal_offset is None:
            raise ValueError(
                "an overlay with a backdrop must also have coordinates"
            )
```

with:

```python
        # Probe section 1.1: a backdrop with no coordinates is refused --
        # except for the special "backdrop" name itself, whose offsets
        # default to 0 rather than being required (overlay.py:325-330,
        # roadmap row 50): its whole point is a FULL-CANVAS layer, which
        # needs no position at all.
        if self.has_back and self.horizontal_offset is None and name != "backdrop":
            raise ValueError(
                "an overlay with a backdrop must also have coordinates"
            )
```

Finally, in the module docstring, remove the now-stale sentence that names `backdrop`/`blur` as examples of what this phase refuses — the docstring's general "REFUSED here rather than accepted-and-ignored" framing stays true for `queue`/`text`, just not for these two any more; no other docstring edit is needed since neither name was named explicitly in the module docstring's prose (only in the two removed constants' own comment, already replaced above).

- [ ] **Step 5: Run the schema tests, confirm GREEN**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_schema.py -v
```

Expected: every test in the file passes, including all four new ones and every pre-existing one (in particular `test_a_backdrop_without_coordinates_is_refused`, which uses the default `name="example"` and must still refuse — the exemption is name-specific).

- [ ] **Step 6: Commit the schema change**

```bash
git add src/autoposter/overlays/schema.py tests/test_overlay_schema.py
git commit --no-gpg-sign -m "feat(overlays): un-refuse blur(NN) and backdrop in the schema"
```

- [ ] **Step 7: RED — render.py backdrop sentinel tests, and the entry-point proof**

In `tests/test_overlay_render.py`, add after the existing `test_back_width_and_height_default_to_shrink_wrap_the_content` test:

```python
def test_the_backdrop_name_stretches_the_minus_one_sentinel_to_the_full_canvas():
    """Probe section 3.5's other arm: for the special 'backdrop' name, an
    unset back_width/back_height stretches to the FULL CANVAS instead of
    shrinking to content -- the arm every other overlay name never reaches
    (roadmap row 50)."""
    layer = _layer()
    box = draw_overlay(
        layer,
        OverlayDefinition(name="backdrop", back_color="#00000099"),
        POSTER_CANVAS,
    )
    assert box == (0, 0, POSTER_CANVAS[0], POSTER_CANVAS[1])
    assert layer.getpixel((10, 10))[3] == 153


def test_the_backdrop_name_with_an_explicit_box_is_not_stretched():
    """The stretch arm only fires on the -1 sentinel; an explicit
    back_width/back_height on a 'backdrop'-named overlay is honoured exactly
    like any other overlay's box."""
    layer = _layer()
    box = draw_overlay(
        layer,
        OverlayDefinition(
            name="backdrop", back_color="#00000099",
            horizontal_offset=0, horizontal_align="left",
            vertical_offset=0, vertical_align="top",
            back_width=200, back_height=100,
        ),
        POSTER_CANVAS,
    )
    assert box == (0, 0, 200, 100)
```

In `tests/test_overlay_entrypoint.py`, add after `test_a_definition_whose_image_fails_to_resolve_does_not_stamp_an_empty_backdrop`:

```python
async def test_apply_badges_draws_a_backdrop_definition_through_the_real_entry_point(
    session, config_with_badges
):
    """The entry-point law (Global Constraint 7): the backdrop sentinel's
    full-canvas arm is exercised through the real apply_badges, not just
    draw_overlay in isolation."""
    config_with_badges.badges.definitions = []
    item, render = await _render(session, rating_key="backdrop-entrypoint-item")
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    baseline_bytes = plex_item.last_bytes

    config_with_badges.badges.definitions = [
        OverlayDefinition(name="backdrop", back_color="#00000099"),
    ]
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 2
    assert plex_item.last_bytes != baseline_bytes, "the full-canvas backdrop must actually be drawn"
```

- [ ] **Step 8: Run the new render/entrypoint tests, confirm they fail for the right reason**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_render.py -k backdrop_name_stretches -v
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_entrypoint.py::test_apply_badges_draws_a_backdrop_definition_through_the_real_entry_point -v
```

Expected: `test_the_backdrop_name_stretches_the_minus_one_sentinel_to_the_full_canvas` FAILS — pre-fix, `box_width`/`box_height` resolve via `content[0]`/`content[1]` (both 0, since a bare `backdrop` definition has no image and no text), so the box degenerates to a near-zero rectangle instead of `(0, 0, 1000, 1500)`. `test_apply_badges_draws_a_backdrop_definition_through_the_real_entry_point` FAILS on the last assertion — the degenerate box draws nothing perceptible, so `plex_item.last_bytes == baseline_bytes`. (`test_the_backdrop_name_with_an_explicit_box_is_not_stretched` is a regression pin, not a RED-first test — run it too and confirm it already PASSES: an explicit non-`-1` `back_width`/`back_height` never reaches the sentinel branch either before or after this phase.)

- [ ] **Step 9: GREEN — implement the render.py sentinel**

In `src/autoposter/overlays/render.py`, update `_content_size`'s docstring (currently ending "...belongs to the `backdrop` name, which the schema refuses.)"):

```python
    Probe section 3.5: an un-set back_width/back_height shrinks to fit the
    overlay's own content. (The other arm of that sentinel -- stretching to
    the canvas -- belongs to the `backdrop` name, which the schema refuses.)
    """
```

to:

```python
    Probe section 3.5: an un-set back_width/back_height shrinks to fit the
    overlay's own content. (The other arm of that sentinel -- stretching to
    the canvas -- belongs to the `backdrop` name, handled in `draw_overlay`
    below, roadmap row 50.)
    """
```

In `draw_overlay`, replace the sentinel resolution (currently):

```python
    content = _content_size(layer, definition, image, text, font)
    box_width = definition.back_width if definition.back_width != -1 else content[0]
    box_height = definition.back_height if definition.back_height != -1 else content[1]
```

with:

```python
    content = _content_size(layer, definition, image, text, font)
    if definition.name == "backdrop":
        # Probe section 3.5's other arm: for the special "backdrop" name, an
        # unset back_width/back_height stretches to the FULL CANVAS rather
        # than shrinking to content (overlay.py:449-452) -- every other
        # overlay uses the content-sized arm below.
        box_width = definition.back_width if definition.back_width != -1 else canvas[0]
        box_height = definition.back_height if definition.back_height != -1 else canvas[1]
    else:
        box_width = definition.back_width if definition.back_width != -1 else content[0]
        box_height = definition.back_height if definition.back_height != -1 else content[1]
```

- [ ] **Step 10: Run the render/entrypoint tests, confirm GREEN**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_render.py -v
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_entrypoint.py::test_apply_badges_draws_a_backdrop_definition_through_the_real_entry_point -v
```

Expected: every test in `test_overlay_render.py` passes (the two new ones plus every pre-existing one — none of the nine builtins is named `backdrop`, so this branch is unreachable from any of them and the untouched `else` arm is exactly the pre-existing code, byte for byte). The entrypoint test passes.

- [ ] **Step 11: Commit the render.py change**

```bash
git add src/autoposter/overlays/render.py tests/test_overlay_render.py tests/test_overlay_entrypoint.py
git commit --no-gpg-sign -m "feat(overlays): backdrop's full-canvas sentinel arm"
```

- [ ] **Step 12: RED — the blur pre-pass tests**

In `tests/test_overlay_entrypoint.py`, add after `test_removing_a_definition_reverts_the_fingerprint` (before the "the real entry point" section comment) — these are compose-level, matching this file's existing `test_a_group_keeps_only_the_highest_weight_member` / `test_suppression_is_resolved_before_group_weight` pattern:

```python
def test_blur_takes_the_maximum_across_every_matched_definition():
    """The per-item pre-pass semantics (p-overlay-b-recon.md, a fresh fetch
    of modules/overlays.py::run_overlays): max NN across every blur(NN)
    match, not sum, not last-wins, not first-wins."""
    low_only = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[
        OverlayDefinition(name="blur(10)"),
    ]))
    both = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[
        OverlayDefinition(name="blur(10)"), OverlayDefinition(name="blur(80)"),
    ]))
    high_only = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[
        OverlayDefinition(name="blur(80)"),
    ]))
    assert both != low_only, "the higher blur must win, not the first-listed one"
    assert both == high_only, "two matches at (10, 80) must equal a single 80 alone"


async def test_apply_badges_draws_a_blur_definition_through_the_real_entry_point(
    session, config_with_badges
):
    """The entry-point law (Global Constraint 7): the blur pre-pass is a NEW
    top-level mechanism in compose(), not an extension of an existing
    per-definition draw call -- it needs its own proof through the real
    apply_badges, the same lesson Phase A's own review drew about
    definitions never reaching compose() through anything but a direct
    call."""
    config_with_badges.badges.definitions = []
    item, render = await _render(session, rating_key="blur-entrypoint-item")
    plex_item = _FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    baseline_bytes = plex_item.last_bytes

    config_with_badges.badges.definitions = [OverlayDefinition(name="blur(30)")]
    await apply_badges(session, config_with_badges, render, item, plex_item, _Facts())
    assert plex_item.uploads == 2
    assert plex_item.last_bytes != baseline_bytes, "the blur must actually be applied"
```

- [ ] **Step 13: Run the new blur tests, confirm they fail for the right reason**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_entrypoint.py -k "blur_takes_the_maximum or blur_definition_through_the_real_entry_point" -v
```

Expected: both FAIL. `test_blur_takes_the_maximum_across_every_matched_definition` fails at `assert both != low_only` — pre-fix, no code path draws anything for a `blur(NN)` name (no image, no text, no backdrop colour), so `low_only`, `both` and `high_only` are all byte-identical to each other and to the un-badged baseline. `test_apply_badges_draws_a_blur_definition_through_the_real_entry_point` fails at the final assertion for the same reason.

- [ ] **Step 14: GREEN — implement the blur pre-pass**

In `src/autoposter/badges/compose.py`, change the Pillow import (currently `from PIL import Image, ImageFont`):

```python
from PIL import Image, ImageFont
```

to:

```python
from PIL import Image, ImageFilter, ImageFont
```

Add a `_blur_amount` helper immediately after `_resolve_definitions` (which ends `return result + list(winners.values())`), before `_draw_definitions`:

```python
def _blur_amount(definitions: list[OverlayDefinition]) -> int:
    """The per-item blur pre-pass amount: the MAXIMUM NN across every
    blur(NN) definition in the already-resolved (suppressed, grouped) list --
    not sum, not last-wins, not first-wins. 0 means "no blur configured",
    the same case this phase must not perturb for any existing config
    (roadmap row 50)."""
    return max((d.blur_amount for d in definitions if d.blur_amount is not None), default=0)
```

In `compose()`, insert the pre-pass right after `poster` is created and resized, before `values = badge_values(...)` (currently):

```python
    canvas = canvas_for(art_kind)
    poster = Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS)

    values = badge_values(art_kind, inputs)
```

becomes:

```python
    canvas = canvas_for(art_kind)
    poster = Image.open(base_path).convert("RGB").resize(canvas, Image.Resampling.LANCZOS)

    resolved_definitions = _resolve_definitions(definitions or [])
    blur = _blur_amount(resolved_definitions)
    if blur > 0:
        # Roadmap row 50: one GaussianBlur on the whole base canvas, before
        # any badge or overlay is composited -- Kometa's own per-item
        # pre-pass semantics (recon p-overlay-b-recon.md), not a
        # per-definition draw call. Badges therefore stay sharp on a
        # blurred background.
        poster = poster.filter(ImageFilter.GaussianBlur(blur))

    values = badge_values(art_kind, inputs)
```

Also extend `compose()`'s docstring — after the existing `` ``definitions`` are operator-defined overlays... `` paragraph, add:

```python
    ``definitions`` may also include one or more ``blur(NN)`` names (roadmap
    row 50): the MAXIMUM NN across every one that survives suppression and
    group resolution is applied ONCE, to the whole base canvas, before any
    badge or overlay -- built-in or operator-defined -- is composited on
    top.
```

Finally, in `_draw_definitions`, skip blur-only definitions inside the per-definition loop (currently starts `for definition in _resolve_definitions(definitions): literal = literal_of(definition.name)`):

```python
    for definition in _resolve_definitions(definitions):
        literal = literal_of(definition.name)
```

becomes:

```python
    for definition in _resolve_definitions(definitions):
        if definition.blur_amount is not None:
            # The blur pre-pass already ran in compose(), before any badge
            # was drawn. This definition carries no image, no text and no
            # backdrop colour of its own -- routing it through draw_overlay
            # would be a harmless no-op call that composites an empty
            # transparent layer, correct by accident rather than by design.
            continue
        literal = literal_of(definition.name)
```

- [ ] **Step 15: Run the blur tests, confirm GREEN**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_entrypoint.py -v
```

Expected: every test in the file passes, including the two new ones.

- [ ] **Step 16: Add and falsify the suppression-before-max pin**

The suppression-before-scan property is *inherited* from `_resolve_definitions` (already suppresses before this phase's blur scan ever runs) rather than new code, so it cannot RED against the pre-Step-14 code the way Steps 12-13 did (both write no visible pixels either way pre-fix). Per Global Constraint 8, prove it falsifiable by deliberate mutation instead. Add to `tests/test_overlay_entrypoint.py`, beside the two tests from Step 12:

```python
def test_blur_suppression_is_resolved_before_the_max_scan():
    """Probe/recon: compile_overlays (suppress+group) runs before the
    per-item blur scan -- a suppressed blur(NN) must not count toward the
    max. Inherited from _resolve_definitions rather than new code; proven
    falsifiable by deliberate mutation in this task's own report, not by a
    RED-before-GREEN cycle."""
    suppressor = OverlayDefinition(name="blur(10)", suppress_overlays=["blur(80)"])
    suppressed = OverlayDefinition(name="blur(80)")
    together = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[suppressor, suppressed]))
    low_only = _sha(compose(BASE, "poster", ALL_SOULS, definitions=[
        OverlayDefinition(name="blur(10)"),
    ]))
    assert together == low_only, "the suppressed blur(80) must not raise the max to 80"
```

Run it and confirm it PASSES immediately:

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test pytest -q tests/test_overlay_entrypoint.py::test_blur_suppression_is_resolved_before_the_max_scan -v
```

Now falsify it. In `src/autoposter/badges/compose.py`, temporarily change the pre-pass in `compose()` to scan the RAW list instead of the resolved one:

```python
    resolved_definitions = _resolve_definitions(definitions or [])
    blur = _blur_amount(resolved_definitions)
```

to (temporarily):

```python
    resolved_definitions = _resolve_definitions(definitions or [])
    blur = _blur_amount(definitions or [])
```

Re-run the same command. Expected: **FAILS** — with suppression bypassed, `blur(80)` survives the (temporarily unused) scan and `together`'s blur becomes 80, diverging from `low_only`'s 10. This proves the test actually depends on suppression running first. Revert the temporary edit back to `blur = _blur_amount(resolved_definitions)` and re-run once more to confirm **PASS** again.

- [ ] **Step 17: Commit the blur pre-pass**

```bash
git add src/autoposter/badges/compose.py tests/test_overlay_entrypoint.py
git commit --no-gpg-sign -m "feat(overlays): the blur(NN) max-NN pre-pass in compose()"
```

- [ ] **Step 18: Run the whole suite, reconcile against the Step 1 baseline**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povb1-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-povb-t1-full.log'
docker wait povb1-full
docker cp povb1-full:/app/.superpowers/run-povb-t1-full.log .superpowers/run-povb-t1-full.log
docker rm povb1-full
tail -5 .superpowers/run-povb-t1-full.log
```

Expected: green, count equal to Step 1's baseline plus the NET tests added in this task (the schema step replaces 1 existing refusal test with 4, so net +3 there; 2 render + 1 backdrop-entrypoint + 2 compose-level blur/suppression + 1 blur-entrypoint = net +9 total; the MEASURED Step 1 baseline is the arbiter — state the reconciliation explicitly in the task report against it, not against this parenthetical).

- [ ] **Step 19: Verify the never-touch files are untouched**

```bash
git diff --stat <task-1-start-sha> -- src/autoposter/render/compositor.py src/autoposter/render/textfit.py \
    src/autoposter/badges/spec.py src/autoposter/config/schema.py src/autoposter/render/pipeline.py \
    src/autoposter/overlays/sources.py tests/test_badge_spec.py tests/test_badge_geometry.py \
    tests/test_badge_draw.py tests/test_badge_compose.py tests/test_badge_parity.py \
    tests/test_overlay_engine_golden.py tests/test_production_parity.py
```

Expected: **empty output**. Paste the (empty) result into the task report. Also confirm the diff against the task's own start point touches only the six files in this task's **Files** block:

```bash
git diff --stat <task-1-start-sha> -- src/ tests/
```

- [ ] **Step 20: Lint**

```bash
docker compose -p povb1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test ruff check src tests
```

Expected: clean. Fix and re-run if not, then amend the affected commit's contents into a new commit (never `--amend`) with a `fix(overlays):` message before proceeding.

- [ ] **Step 21: Tear down**

```bash
docker compose -p povb1 down
```

---

## Task 2: Wrap

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`
- Modify: `.superpowers/sdd/progress.md`

- [ ] **Step 1: Run the whole suite one more time, from a clean container**

```bash
docker compose -p povb2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name povb2-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-povb-t2-full.log'
docker wait povb2-full
docker cp povb2-full:/app/.superpowers/run-povb-t2-full.log .superpowers/run-povb-t2-full.log
docker rm povb2-full
tail -5 .superpowers/run-povb-t2-full.log
```

Expected: green, matching Task 1's Step 18 count exactly (no code changes happen in this task before this point).

- [ ] **Step 2: Verify the parity law held across the whole branch**

```bash
git diff --stat origin/main -- src/autoposter/render/compositor.py src/autoposter/render/textfit.py \
    src/autoposter/badges/spec.py src/autoposter/config/schema.py src/autoposter/render/pipeline.py \
    src/autoposter/overlays/sources.py tests/test_badge_spec.py tests/test_badge_geometry.py \
    tests/test_badge_draw.py tests/test_badge_compose.py tests/test_badge_parity.py \
    tests/test_overlay_engine_golden.py tests/test_production_parity.py
```

Expected: **empty output**. Any line here is a phase-law violation — STOP and report rather than closing the row. Paste the (empty) result into the task report.

- [ ] **Step 3: Close row 50 on the roadmap**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, extend row 50's cell (currently `| 50 | \`blur(##)\` / \`backdrop\` overlays | Whole-poster blur and solid-colour layer primitives | S — two Pillow ops | parity-only | — |`) with an **answered** paragraph, appended to the description cell, before the closing `|`. Write it from the task's own measured results (commit shas, suite count), not from this plan, and it must state at minimum:

- What shipped: `backdrop` un-refused with its own coordinate exemption (`overlays/schema.py`), the `-1` sentinel's full-canvas arm (`overlays/render.py::draw_overlay`), and `blur(NN)`'s new max-across-matches pre-pass in `badges/compose.py::compose` (`_blur_amount`, run over `_resolve_definitions`'s already-suppressed/grouped list, applied once via `ImageFilter.GaussianBlur` before any badge or overlay composites).
- **The A3 divergence, stated plainly:** Kometa silently substitutes `blur(50)` on a malformed `blur(NN)` name; this schema refuses instead (the comment lives beside the check in `schema.py`), matching every other validator in the file rather than letting a typo silently change blur strength.
- **The era-recon correction, restored:** `p-overlay-era-recon.md`'s own adjudication A1 (written before Phase A shipped) proposed correcting this row's cell FROM "two Pillow ops" TO "two `magick` argv ops," reasoning the render pipeline was ImageMagick-only. Phase A's own landed code disproves that — badges are 100% Pillow (`badges/`, `overlays/`), architecturally disjoint from `render/compositor.py`'s argv builders, which this phase never touches either. The roadmap's ORIGINAL "S — two Pillow ops" text was correct the first time and stands unchanged.
- **No re-render storm, by construction:** neither primitive touches `render.fingerprint`; `badge_fingerprint`'s existing non-empty-`definitions` guard already covers `blur`/`backdrop` the same way it covers every other definition — zero fingerprint movement, zero re-renders, for the ~16k already-badged items in any library that configures neither name.
- **Cosmetic divergence accepted, not built (A5):** `overlays/sources.py`'s name-keyed image-fallback rung still runs one harmless no-op stat call for a `blur(NN)`/`backdrop` name rather than being skipped outright the way Kometa skips it entirely — no functional bug, filed as a nice-to-have rather than built.

- [ ] **Step 4: Write the phase's wrap entry**

Append one bullet to `.superpowers/sdd/progress.md`, in the file's own established one-line-per-phase style (see the most recent `PHASE B` entries at the end of the file for the exact voice). It must name: the task's commit shas, the schema un-refusal with A3's divergence, the render.py sentinel arm, the compose.py pre-pass with its falsifiability proof (Step 16), the storm-guard re-verification (no `badge_fingerprint` change needed), the five parity pins + golden gate diff being empty across the whole branch, the era-recon's A1 correction reversed back to the roadmap's original text, and the measured final suite count.

- [ ] **Step 5: Commit and open the PR**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md .superpowers/sdd/progress.md
git commit --no-gpg-sign -m "docs(roadmap): row 50 closes; the era-recon correction restored"
git push -u origin feat/overlay-blur
```

Open the PR with a plain body (Global Constraint 12, facts C3): what shipped (both primitives, one line each), the A3 divergence from Kometa stated plainly, and — leading, not buried — **this is opt-in and changes nothing for any existing config**: no operator config today writes a `blur(NN)` or `backdrop` overlay, so every already-badged item's fingerprint, pixels and upload status are untouched until an operator deliberately adds one. State the empty parity-diff result from Step 2. No AI attribution of any kind, in the commit or the body.

- [ ] **Step 6: Tear down**

```bash
docker compose -p povb2 down
```

---

## Self-review

**Spec coverage.** `p-overlay-b-facts.md` C1.1 (the reversal, Pillow-side only) → Task 1's three source files, `render/compositor.py` left untouched, checked at Task 1 Step 19 and Task 2 Step 2. C1.2 (blur's max-NN, post-suppress/group pre-pass) → Task 1 Steps 12-17, including the falsifiability proof for the inherited suppression-ordering property. C1.3 (backdrop's un-refusal + sentinel) → Task 1 Steps 2-11. C1.4 (A3 ruled: refuse loudly, divergence comment) → Task 1 Step 4's schema edit and its comment, Step 12-13's tests. C1.5 (A2: sequenced now) → this plan exists. C2 (the whole law: entry-point both ways, max-NN pinned, suppression-before-max pinned, both sentinel arms, refusal arms, the five pins + golden gate untouched, RED-first, warn-and-skip net unchanged) → covered end to end across Task 1's steps, cross-referenced in Global Constraints 1-8. C3 (row closes, era-recon correction recorded, PR body plain about opt-in-ness) → Task 2 Steps 3 and 5. C4 (2 tasks, `feat/overlay-blur` from current `origin/main`, `povb*` projects) → the branch/cut-point section and both tasks' container commands.

**Placeholder scan.** No TBD, no "add error handling," no "similar to Task N" — every test is written out in full with its exact assertions, every code edit shows the exact before/after text taken from the real file, and every RED step states the specific reason the pre-fix code fails (traced through `_content_size`'s content-size-zero degenerate box for backdrop, and the "nothing draws for a blur(NN) name yet" reasoning for blur) rather than a generic "should fail." Step 16's falsifiability proof spells out the exact temporary code change and its exact expected failure.

**Type consistency.** `OverlayDefinition.blur_amount` is defined once (Step 4) and consumed identically in `badges/compose.py::_blur_amount` (Step 14), `_draw_definitions`'s skip (Step 14) and every new test (Steps 2, 12, 16) — always `int | None`, never re-typed. `draw_overlay`'s signature is unchanged everywhere it is called (Step 9's edit is internal to the function body). `_blur_amount(definitions: list[OverlayDefinition]) -> int` matches its one call site in `compose()` and its one falsification edit in Step 16, both passing a `list[OverlayDefinition]` (never `None` — `definitions or []` is resolved before either call).
