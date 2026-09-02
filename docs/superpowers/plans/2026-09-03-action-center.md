# Action Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Action Center — a curation queue over the artwork assets this service chose, where each row is one `renders` row, each reason it is imperfect is a SQL predicate derived from the live config, and every operator action is an enqueue.

**Architecture:** The render pipeline already computes every fact roadmap row 103 names and then throws it away. This phase **stores the facts** on `renders` (nine new columns, written at the existing write-back so the fingerprint short-circuit cannot blank them) and **derives the judgements** at query time from the live config, in a flag registry of SQL predicates (`src/autoposter/actions/flags.py`). One definition of a flag serves both the `WHERE` clause and the per-row labels. Dismissals live in `action_dismissals`, keyed by a SHA-256 evidence hash computed **in SQL** over the fact columns, so the join is a plain equality and paging and totals stay server-side — and so a fact changing returns the row on its own with no sweep and no invalidation job. Every action (single re-search, bulk re-search, backfill) goes through `_enqueue_reprocess` and the `uq_jobs_pending_dedupe` partial index; nothing calls a provider inline.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / Alembic / PostgreSQL 18 · React 19 + react-router-dom 7 + Vite 8 + Vitest 4 + @testing-library/react · pytest + pytest-asyncio in the `test` compose service.

---

## Global Constraints

Every task's requirements implicitly include this section. Read it before the first step of any task.

1. **Branch.** `feat/action-center` cuts from **`fix/config-safety`'s FINAL tip** — the phase stacks. Do **not** branch from `main`. That tip is the branch's final tip (re-verify at execution) — run `git log --oneline -1 fix/config-safety` before cutting and use whatever the tip actually is.
2. **Migration.** The one new alembic migration has `down_revision = "c7e1b93a4d20"` (the config-safety head). There must be **exactly one alembic head** after it. `tests/test_migrations.py` proves both the single head and model/migration agreement; it must be green.
3. **Files that stay untouched.** No edit of any kind to `src/autoposter/config/schema.py`, to anything under `src/autoposter/badges/`, or to anything under `src/autoposter/overlays/`. The queued overlay era owns those files and this phase must leave its run clear. Reading from `config.schema` (importing `Config`) is fine; editing it is not. This is achievable because the design adds **no new config settings** — every judgement is derived from config fields that already exist.
4. **Facts stored, judgements derived.** The new `renders` columns hold **facts** and carry factual, non-judgemental names (11a's own risk line is the naming law: "selected language = en, preferred = xx", never "bad"). Flags are SQL predicates in a registry, evaluated at query time against the **live** config (`request.app.state.config_holder.current`). **No stored verdicts.** Editing `language_order` or `providers.order` must change what the queue shows with **zero row writes**.
5. **The write-back site.** All nine new columns are assigned at the existing pipeline write-back block (`src/autoposter/render/pipeline.py`, the `render.provider = provider_name` … `render.rendered_at = func.now()` run, currently lines 858–882), following the `source_mode` precedent whose own comment says why: *"Provenance the 'unchanged' short-circuit above cannot blank out, unlike detail."* `quality_scored_at` timestamps fact coverage.
6. **Dismissals.** The `action_dismissals` table is keyed by an evidence hash over the fact columns. Facts change → hash changes → the item returns to the queue. No sweep, no invalidation job.
7. **Every action is an enqueue.** Single re-search, bulk re-search and the backfill all go through `_enqueue_reprocess` (`src/autoposter/api/routes.py:643`) and the `uq_jobs_pending_dedupe` partial unique index. **Never** an inline provider call.
8. **The flags.** Ten flag codes plus one coverage code. Six live instantly on rows that already exist: `missing`, `skipped`, `truncated`, `show_fallback`, `upload_failed`, `unknown_provenance`. Four populate only as rows re-render (or as T4's backfill drives them): `language_miss`, `provider_downgrade`, `textless_miss`, `logo_fallback`. `unknown_provenance` is the `adopted` flag and its filter is **default-off**. `unscored` is the eleventh code, also default-off, and exists to make coverage visible. **This split is the honest limitation and it goes in the PR body verbatim.**
9. **Out of scope. Do not build any of these.** T5 files each as a named roadmap row: the **delete** action; an **inline candidate picker** inside the queue (deep-link to `/items/{id}` instead); **resolution-floor enforcement** (capture width/height/point-size facts, enforce nothing — row 219 keeps the decision); the **provider-threw vs provider-returned-empty** distinction; the **near-miss text-fit** metric; the **jobs / ops-inbox tab** (row 130); any **non-asset signal** (failures, ID mismatches, sweep refusals, drift warnings).
10. **TDD, RED-first.** Write the failing test, run it, watch it fail for the right reason, then implement. In particular:
    - Each flag predicate is pinned **both ways**: it fires on a shaped row and is silent on a healthy row.
    - The **derived-not-stored** property is pinned: editing the config flips the flag with **no row write**.
    - The write-back is pinned through the **real pipeline entry point** (`render_artifact`): a render lands its facts, and an unchanged fingerprint does **not** wipe them.
    - The dismissal's **return-on-change** is pinned.
    - The **enqueue-dedupe** is pinned.
    - Frontend page tests are vitest, in the repo's page-test conventions.
11. **Naming.** "Action Center" everywhere — in the nav label, the page heading, the docstrings, the roadmap and the PR body. Never "Actions Center".
12. **Attribution.** No `Co-Authored-By` trailers. No mention of AI, Claude, or any assistant in any commit message, PR body, code comment or document.
13. **The container test command.** The suite runs in the container, never on the host. Use this exact shape, with `<N>` = the task number:

    ```bash
    docker compose -p pac<N> -f docker-compose.yml -f .superpowers/isolated-db.yml \
      run --name pac<N>-run test sh -c \
      "set -o pipefail; pytest <paths> -q 2>&1 | tee /app/.superpowers/run-pac<N>.log"
    ```

    No `--rm` — the container's output must survive so the log can be read back. If the command times out, wait on it in the foreground with `docker wait pac<N>-run`. Tear down with `docker compose -p pac<N> -f docker-compose.yml -f .superpowers/isolated-db.yml down` — **never** `down -v`.
14. **The frontend test command.** `npm run test -- --run`, run from `frontend/`. Type-check with `npx tsc --noEmit` from `frontend/`.
15. **Suite baselines are measured, never inherited.** The last recorded totals on the stacked tip were **4603 backend / 400 vitest** (config-safety T3). Measure the real baseline at the start of T1 and report deltas against what you measured.
16. **A new `tests/test_api_*.py` file must be declared.** `tests/test_ci_path_filters.py` asserts that every `test_api_*.py` file appears in either `DEEP_SUITES` or `FAST_API_SUITES` in `tests/conftest.py`. T2 adds `test_api_action_center.py` and **must** add it to `DEEP_SUITES` in the same commit, or CI goes red.
17. **House code style.** Comments explain *why*, in prose, citing the concrete failure they prevent. Double-hyphen `--` for em dashes in Python and TypeScript comments. Python lines wrap at 100 columns (`ruff check .` from the repo root must be clean). TypeScript pages are named exports, never default exports.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/autoposter/actions/__init__.py` | Package marker for the flag registry. |
| `src/autoposter/actions/flags.py` | The flag registry: one `Flag` per code, each carrying a SQL predicate built from the live `Config`, plus the SQL evidence expression the dismissal join uses. The single definition of "what counts as imperfect". |
| `src/autoposter/db/models.py` | Nine new `Render` columns; the new `ActionDismissal` model; the `skipped` vocabulary fix on `Render.status`'s comment. |
| `alembic/versions/a9d4e70c3b15_action_center_facts.py` | The one migration: nine `ALTER TABLE renders ADD COLUMN`s and `CREATE TABLE action_dismissals`. |
| `src/autoposter/render/pipeline.py` | The bookkeeping: bind the language order and the two fallback booleans, carry the fitted point size out of `compose_styled`, and assign the nine columns at the existing write-back. |
| `src/autoposter/providers/ladder.py` | One additive public helper, `normalise_language`, so the stored language code is canonical. No behaviour change. |
| `src/autoposter/api/action_center.py` | The whole API surface: list, summary, dismiss, undismiss, single re-search, bulk re-search, and the quality backfill. |
| `src/autoposter/api/routes.py` | One `include_router(action_center_router)` line with its justifying comment; the `skipped` docstring fix. |
| `frontend/src/pages/ActionCenter.tsx` | The page: count chips, filter bar, table, pager, per-row actions, the two-step bulk arm, the backfill control. |
| `frontend/src/pages/action-center.css` | Only what is specific to this page. |
| `frontend/src/api/types.ts` | The response interfaces, appended at the end of the file. |
| `frontend/src/App.tsx` | One import and one `<Route>` before the catch-all. |
| `frontend/src/shell/Sidebar.tsx` | One `ICONS` entry and one `NAV` entry with its position comment. |

---

## Task Summary

| # | Task | Size |
|---|---|---|
| 1 | Facts on `renders`: the migration, the model, the pipeline bookkeeping, the flag registry | M |
| 2 | The API: `src/autoposter/api/action_center.py` | M |
| 3 | The page: `frontend/src/pages/ActionCenter.tsx` | M–L |
| 4 | The batched quality backfill | S |
| 5 | Wrap: docs, roadmap rows, progress entry, PR body | S |

---

### Task 1: Facts on `renders` — the migration, the model, the pipeline bookkeeping, the flag registry

**Files:**
- Create: `src/autoposter/actions/__init__.py`
- Create: `src/autoposter/actions/flags.py`
- Create: `alembic/versions/a9d4e70c3b15_action_center_facts.py`
- Modify: `src/autoposter/db/models.py` (add `SmallInteger` to the imports; nine columns on `Render`; the `skipped` comment fix; a new `ActionDismissal` model appended at the end of the file)
- Modify: `src/autoposter/providers/ladder.py` (rename `_normalise` to `normalise_language`)
- Modify: `src/autoposter/render/pipeline.py` (the import line; `ComposeResult.point_size`; `compose_styled`'s capture; `_provider_rank`; six bookkeeping bindings and the nine write-back assignments in `render_artifact`)
- Modify: `src/autoposter/api/routes.py` (the `item_filters` docstring's status list)
- Test: `tests/test_action_flags.py` (new)
- Test: `tests/test_pipeline_quality.py` (new)

**Interfaces:**

- **Consumes:** `Render` and `MediaItem` from `autoposter.db.models`; `Config` from `autoposter.config.schema`; `language_rank(candidate, language_order) -> int` and `UNRANKED = 99` from `autoposter.providers.ladder`.
- **Produces**, all consumed verbatim by Tasks 2 and 4:

  ```python
  # autoposter/db/models.py -- new Render columns
  Render.selected_language:  str | None      # String(16)
  Render.language_rank:      int | None      # SmallInteger
  Render.provider_rank:      int | None      # SmallInteger
  Render.textless_fallback:  bool            # Boolean, server_default false
  Render.logo_text_fallback: bool            # Boolean, server_default false
  Render.base_width:         int | None      # SmallInteger
  Render.base_height:        int | None      # SmallInteger
  Render.text_point_size:    int | None      # SmallInteger
  Render.quality_scored_at:  datetime | None # DateTime(timezone=True)

  # autoposter/db/models.py -- new model
  class ActionDismissal(Base):
      __tablename__ = "action_dismissals"
      id: int; item_id: int; art_kind: str; flag: str | None
      evidence: str; dismissed_at: datetime; note: str | None
      # UniqueConstraint("item_id", "art_kind", name="uq_action_dismissal_item_kind")

  # autoposter/actions/flags.py
  ART_KINDS: tuple[str, ...]                 # ("poster","season_poster","background","title_card")

  @dataclass(frozen=True)
  class Flag:
      code: str
      label: str
      description: str
      default_on: bool
      instant: bool
      predicate: Callable[[Config], ColumnElement[bool]]
      detail: Callable[[Render], str]

  FLAGS: dict[str, Flag]                              # insertion-ordered, the chip order
  def predicate_for(code: str, config: Config) -> ColumnElement[bool]     # KeyError on unknown
  def default_predicate(config: Config) -> ColumnElement[bool]
  def detail_for(code: str, render: Render) -> str
  def evidence_expression() -> ColumnElement[str]     # SQL sha256 hex over the fact columns

  # autoposter/providers/ladder.py
  def normalise_language(language: str | None) -> str | None

  # autoposter/render/pipeline.py
  @dataclass class ComposeResult:  output: Path; truncated: bool; detail: str | None; point_size: int | None
  def _provider_rank(providers: list, provider_name: str | None) -> int | None
  ```

---

- [ ] **Step 1: Cut the branch from the config-safety tip and measure the baseline**

The phase stacks on `fix/config-safety`; branching from `main` would chain the migration wrong and lose the `config_holder` seam this design reads.

```bash
cd /d/Sites/autoposter
git fetch --all
git log --oneline -1 fix/config-safety
git checkout fix/config-safety
git checkout -b feat/action-center
git log --oneline -1
```

Expected: the new branch's tip is identical to `fix/config-safety`'s tip.

Confirm the migration head you are about to chain onto:

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac1-heads test sh -c "alembic heads"
```

Expected: exactly one line, ending `c7e1b93a4d20 (head)`. If more than one line appears, stop and report it — the phase cannot proceed on a split chain.

Measure the real baseline (do not inherit a number from any document):

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac1-base test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pac1-base.log"
```

Expected: a `N passed` line with zero failures. Record `N` — every later delta in this task is measured against it.

- [ ] **Step 2: Write the failing flag-registry test**

Create `tests/test_action_flags.py`:

```python
"""The Action Center's flag registry: what counts as an imperfect asset.

Every predicate is pinned BOTH ways -- it fires on a row shaped to trip it and
is silent on a healthy one -- because a predicate that fires on everything and
a predicate that fires on nothing both pass a one-directional test.

The two derived-not-stored tests are the point of the whole design: editing
the config must change what the queue shows with no row write at all. A stored
verdict would pass every other test in this file and fail those two.
"""
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from autoposter.actions import flags
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem, Render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


async def _seed(session, *, library="Movies", art_kind="poster", **render_fields) -> Render:
    """One media item and its render row, with the render's columns overridable.

    A fresh rating key per call because the column is unique and these tests
    seed several rows into one shared database.
    """
    item = MediaItem(
        rating_key=f"rk-{uuid4().hex[:12]}",
        library=library,
        kind="movie",
        title="Dune: Part Two",
    )
    session.add(item)
    await session.flush()
    fields = {"status": "rendered", "asset_path": "/assets/Movies/Dune/poster.jpg"}
    fields.update(render_fields)
    render = Render(item_id=item.id, art_kind=art_kind, **fields)
    session.add(render)
    await session.commit()
    return render


async def _fires_on(session, config, code: str) -> set[int]:
    """The render ids this flag's SQL selects, evaluated by the database.

    Joined to media_items because some predicates read MediaItem.library --
    the library language override is part of what "first choice" means.
    """
    rows = await session.execute(
        select(Render.id)
        .join(MediaItem, Render.item_id == MediaItem.id)
        .where(flags.predicate_for(code, config))
    )
    return set(rows.scalars().all())


# --- the six that light up on rows that already exist ------------------------


async def test_missing_fires_on_a_no_art_row_and_is_silent_on_a_rendered_one(session, config):
    flagged = await _seed(session, status="no_art")
    healthy = await _seed(session, status="rendered")

    fired = await _fires_on(session, config, "missing")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_skipped_fires_on_a_skipped_row_and_is_silent_on_a_rendered_one(session, config):
    flagged = await _seed(session, status="skipped")
    healthy = await _seed(session, status="rendered")

    fired = await _fires_on(session, config, "skipped")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_truncated_fires_on_a_truncated_row_and_is_silent_on_a_rendered_one(
    session, config
):
    flagged = await _seed(session, status="truncated")
    healthy = await _seed(session, status="rendered")

    fired = await _fires_on(session, config, "truncated")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_show_fallback_fires_on_the_source_mode_row_two_seven_shipped(session, config):
    """Row 132 shipped `source_mode = 'show_fallback'` and gave it no operator
    surface. This is where already-persisted behaviour finally gets a name."""
    flagged = await _seed(session, art_kind="season_poster", source_mode="show_fallback")
    healthy = await _seed(session, art_kind="season_poster", source_mode="generate")

    fired = await _fires_on(session, config, "show_fallback")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_upload_failed_fires_independently_of_the_render_status(session, config):
    """`upload_status` is orthogonal to `status`: a row can be rendered
    perfectly and never have reached Plex."""
    flagged = await _seed(session, status="rendered", upload_status="failed")
    healthy = await _seed(session, status="rendered", upload_status="uploaded")

    fired = await _fires_on(session, config, "upload_failed")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_unknown_provenance_fires_on_an_adopted_row(session, config):
    """An adopted row is art this service found on disk and took over: no
    provider, no source URL, no idea what it is. Honestly unknown quality."""
    flagged = await _seed(session, adopted=True)
    healthy = await _seed(session, adopted=False)

    fired = await _fires_on(session, config, "unknown_provenance")

    assert flagged.id in fired
    assert healthy.id not in fired


# --- the four that need the new bookkeeping ----------------------------------


async def test_language_miss_fires_on_an_en_row_under_an_xx_leading_order(session, config):
    """Roadmap 11a's own acceptance, at the predicate level: the shipped
    example config leads every art kind with `xx`, so English is rank 1."""
    assert config.artwork.poster.language_order[0] == "xx"
    flagged = await _seed(session, selected_language="en", language_rank=1)
    healthy = await _seed(session, selected_language=None, language_rank=0)

    fired = await _fires_on(session, config, "language_miss")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_language_miss_is_silent_on_a_row_the_ladder_never_ranked(session, config):
    """`language_rank IS NULL` is every row written before this taxonomy, plus
    every manual override. Its `selected_language` is NULL, which compares
    unequal to every preference -- without the gate the whole pre-existing
    library would read as a language miss the pipeline never made."""
    unscored = await _seed(session, selected_language=None, language_rank=None)

    fired = await _fires_on(session, config, "language_miss")

    assert unscored.id not in fired


async def test_language_miss_follows_the_library_language_override(session, config):
    """Roadmap row 38: a library can re-point its posters. A queue judging that
    library against the global order would flag every row it re-pointed."""
    config.artwork.library_language_overrides = {"Anime": {"poster": ["en", "xx"]}}
    honoured = await _seed(session, library="Anime", selected_language="en", language_rank=0)
    missed = await _seed(session, library="Movies", selected_language="en", language_rank=1)

    fired = await _fires_on(session, config, "language_miss")

    assert honoured.id not in fired
    assert missed.id in fired


async def test_provider_downgrade_fires_on_a_second_choice_provider(session, config):
    assert config.providers.order[0] == "TMDB"
    flagged = await _seed(session, provider="TVDB", provider_rank=1)
    healthy = await _seed(session, provider="TMDB", provider_rank=0)

    fired = await _fires_on(session, config, "provider_downgrade")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_provider_downgrade_is_silent_on_a_manual_override(session, config):
    """`provider = 'manual'` describes a file an operator placed by hand. No
    ladder position describes it, so `provider_rank` is NULL and the flag must
    not read the operator's own choice as a downgrade."""
    manual = await _seed(session, provider="manual", provider_rank=None)

    fired = await _fires_on(session, config, "provider_downgrade")

    assert manual.id not in fired


async def test_textless_miss_fires_when_the_ladder_took_a_text_bearing_image(session, config):
    flagged = await _seed(session, textless_fallback=True)
    healthy = await _seed(session, textless_fallback=False)

    fired = await _fires_on(session, config, "textless_miss")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_logo_fallback_fires_when_a_poster_wore_text_in_a_logos_place(session, config):
    flagged = await _seed(session, logo_text_fallback=True)
    healthy = await _seed(session, logo_text_fallback=False)

    fired = await _fires_on(session, config, "logo_fallback")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_unscored_fires_on_a_rendered_row_with_no_quality_stamp(session, config):
    from datetime import datetime, timezone

    flagged = await _seed(session, status="rendered", quality_scored_at=None)
    healthy = await _seed(
        session, status="rendered", quality_scored_at=datetime.now(timezone.utc)
    )
    # A no_art row is already named by `missing`; calling it unscored as well
    # would double-count the same row under two chips.
    not_renderable = await _seed(session, status="no_art", quality_scored_at=None)

    fired = await _fires_on(session, config, "unscored")

    assert flagged.id in fired
    assert healthy.id not in fired
    assert not_renderable.id not in fired


# --- the derived-not-stored property, which is the whole design --------------


async def test_editing_the_language_order_flips_the_flag_with_no_row_write(session, config):
    """The reason no verdict is stored. An operator who re-points
    `language_order` must see the queue change on the next request, without a
    backfill, and the row's own facts must be exactly as the render left them.
    """
    render = await _seed(session, selected_language="en", language_rank=1)

    assert render.id in await _fires_on(session, config, "language_miss")

    config.artwork.poster.language_order = ["en", "fi"]

    assert render.id not in await _fires_on(session, config, "language_miss")

    # Nothing was written. Re-read from the database rather than trusting the
    # identity map: a predicate that "worked" by quietly updating the row
    # would pass the two assertions above and fail here.
    session.expire_all()
    reread = (
        await session.execute(select(Render).where(Render.id == render.id))
    ).scalar_one()
    assert reread.selected_language == "en"
    assert reread.language_rank == 1


async def test_editing_the_provider_order_flips_the_flag_with_no_row_write(session, config):
    render = await _seed(session, provider="TVDB", provider_rank=1)

    assert render.id in await _fires_on(session, config, "provider_downgrade")

    config.providers.order = ["TVDB", "TMDB", "Fanart"]

    assert render.id not in await _fires_on(session, config, "provider_downgrade")

    session.expire_all()
    reread = (
        await session.execute(select(Render).where(Render.id == render.id))
    ).scalar_one()
    assert reread.provider == "TVDB"
    assert reread.provider_rank == 1


# --- the default population and the evidence hash ----------------------------


async def test_the_default_population_leaves_adopted_rows_out(session, config):
    """A5: an adopted library may be tens of thousands of rows, and a queue
    that opens showing ten thousand rows is not a queue. The operator opts in
    by picking the chip."""
    adopted = await _seed(session, adopted=True, status="rendered")
    broken = await _seed(session, status="no_art")

    rows = await session.execute(
        select(Render.id)
        .join(MediaItem, Render.item_id == MediaItem.id)
        .where(flags.default_predicate(config))
    )
    default_population = set(rows.scalars().all())

    assert broken.id in default_population
    assert adopted.id not in default_population


async def test_the_evidence_hash_moves_when_a_fact_changes(session, config):
    """11b's "dismissal that sticks until the underlying facts change",
    reduced to its mechanism: a re-render finding Finnish where it found
    English must move the hash, so the dismissal join stops matching and the
    row returns with no sweep and no invalidation job."""
    render = await _seed(session, selected_language="en", language_rank=1)

    async def evidence() -> str:
        return (
            await session.execute(
                select(flags.evidence_expression()).where(Render.id == render.id)
            )
        ).scalar_one()

    before = await evidence()
    assert len(before) == 64

    render.selected_language = "fi"
    await session.commit()

    assert await evidence() != before


async def test_the_evidence_hash_ignores_a_column_no_flag_rests_on(session, config):
    """`detail` is free text the pipeline resets on every successful render.
    Folding it into the evidence would resurrect every dismissal on every
    pass -- exactly the bug 11b names."""
    render = await _seed(session, selected_language="en", language_rank=1, detail="unchanged")

    async def evidence() -> str:
        return (
            await session.execute(
                select(flags.evidence_expression()).where(Render.id == render.id)
            )
        ).scalar_one()

    before = await evidence()
    render.detail = None
    await session.commit()

    assert await evidence() == before


# --- the registry's agreement with the pipeline ------------------------------


def test_the_registry_reads_the_same_language_order_the_pipeline_renders_with(config):
    """`flags._language_order` restates `pipeline.language_order_for` rather
    than importing it (importing the pipeline pulls plexapi and the badge
    compositor into an API request). Two statements of one rule drift, so the
    agreement is pinned instead of assumed."""
    from autoposter.render.pipeline import language_order_for

    config.artwork.library_language_overrides = {"Anime": {"poster": ["en", "xx"]}}

    for library in ("Movies", "Anime"):
        for art_kind in flags.ART_KINDS:
            assert flags._language_order(config, library, art_kind) == language_order_for(
                config, library, art_kind
            )


def test_the_registry_lists_the_same_art_kinds_the_pipeline_renders():
    """`flags.ART_KINDS` restates the union of `pipeline.ART_KINDS_FOR`'s
    values rather than importing it (importing the pipeline pulls plexapi,
    httpx and the badge compositor into every request that counts a flag).
    Pinned by a test rather than assumed, for the same reason as
    `_language_order`."""
    from autoposter.render.pipeline import ART_KINDS_FOR

    rendered = {art_kind for kinds in ART_KINDS_FOR.values() for art_kind in kinds}
    assert set(flags.ART_KINDS) == rendered


def test_every_flag_declares_a_label_a_description_and_a_detail(config):
    """The page renders all three; a flag that shipped with an empty label is
    a chip an operator cannot identify."""
    assert set(flags.FLAGS) == {
        "missing", "skipped", "truncated", "show_fallback", "upload_failed",
        "unknown_provenance", "language_miss", "provider_downgrade",
        "textless_miss", "logo_fallback", "unscored",
    }
    for code, flag in flags.FLAGS.items():
        assert flag.code == code
        assert flag.label
        assert flag.description
        assert callable(flag.detail)
    assert flags.FLAGS["unknown_provenance"].default_on is False
    assert flags.FLAGS["unscored"].default_on is False
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac1-red1 test sh -c \
  "set -o pipefail; pytest tests/test_action_flags.py -q 2>&1 | tee /app/.superpowers/run-pac1-red1.log"
```

Expected: a collection error, `ModuleNotFoundError: No module named 'autoposter.actions'`. That is the right RED — the module does not exist yet.

- [ ] **Step 4: Add the nine columns and the dismissal model to `src/autoposter/db/models.py`**

First, add `SmallInteger` to the SQLAlchemy import block (it is alphabetical between `String` and… no — between `Index` and `Integer` alphabetically it is `Index, Integer, SmallInteger, String`). Replace:

```python
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
```

with:

```python
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
```

Then fix the `status` vocabulary defect on the way past. Replace:

```python
    # pending | rendered | truncated | no_art | failed
    status: Mapped[str] = mapped_column(String(24), default="pending")
```

with:

```python
    # pending | rendered | truncated | no_art | failed | skipped
    #
    # `skipped` was missing from this list while render/pipeline.py wrote it
    # in four places (a disabled art kind, a skip-word title, an unnumbered
    # item, online fetch disabled). No CHECK constraint governs the column in
    # the model or in any migration, so nothing caught it, and the
    # /items/filters dropdown was right only because it reads DISTINCT from
    # the table. An Action Center flag registry written from this comment
    # would have silently dropped a whole status.
    status: Mapped[str] = mapped_column(String(24), default="pending")
```

Then add the nine columns immediately after the `adopted` column (the last field on `Render`):

```python
    # --- Action Center quality facts (roadmap 11a) --------------------------
    #
    # Facts, never verdicts. Which language the ladder took, which provider,
    # which fallbacks it made -- whether any of that is worth an operator's
    # attention is a SQL predicate in actions/flags.py, evaluated against the
    # config that is live when the queue is read. A stored `language_miss`
    # boolean would be a lie the moment an operator re-pointed
    # artwork.poster.language_order, and re-backfilling the library on every
    # config save is worse than a predicate. 11a's own risk note is the
    # naming law: factual ("selected language = en"), never judgemental.
    #
    # All nine are written at render/pipeline.py's write-back block, beside
    # source_mode and for source_mode's own stated reason: the "unchanged"
    # fingerprint short-circuit returns before that block, so a fact recorded
    # there survives a pass that changes nothing -- unlike `detail`, which is
    # unconditionally reset on every successful render.

    # The candidate's own language tag, normalised to the two-letter form the
    # config speaks (providers/ladder.py's normalise_language, so a TVDB
    # "eng" does not read as a miss against every "en"-preferring order).
    # NULL when the provider tagged it with nothing, which is what textless
    # art looks like.
    selected_language: Mapped[str | None] = mapped_column(String(16))
    # Where that language sat in the order in force AT RENDER TIME, from
    # ladder.language_rank; 99 is the ladder's UNRANKED. History, and
    # deliberately not what the flag reads: it is the evidence a row shows an
    # operator, while the judgement is recomputed live. NULL means no ladder
    # ever ranked this row.
    language_rank: Mapped[int | None] = mapped_column(SmallInteger)
    # Where the winning provider sat in the RUNTIME ladder -- the list the
    # pipeline was handed, not config.providers.order. app.py's
    # _build_providers warns and drops a configured provider with no
    # implementation, so a config-index rank can claim a position that never
    # existed on this deployment. NULL for a manual override, which no ladder
    # produced.
    provider_rank: Mapped[int | None] = mapped_column(SmallInteger)
    # The order preferred textless art, no provider had any, and the ladder
    # took a text-bearing image rather than nothing. This is
    # ladder.Selection.is_fallback, which was returned by the ladder from the
    # day it was written and read by nobody until now.
    textless_fallback: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # A poster that wanted a clearlogo, found none on any provider, and drew
    # its title text instead because artwork.logo_text_fallback allowed it.
    logo_text_fallback: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # The base image's pixel dimensions as the provider reported them.
    # CAPTURED, ENFORCED NOWHERE: artwork.min_width/min_height stay inert and
    # the resolution floor stays roadmap row 219's open decision. Capturing
    # them now is what makes 219 a small later change instead of a re-backfill
    # of the whole library -- which is 11a's own named risk.
    base_width: Mapped[int | None] = mapped_column(SmallInteger)
    base_height: Mapped[int | None] = mapped_column(SmallInteger)
    # The point size the primary title block finally fitted at, from
    # textfit.FitResult. NULL when no text was drawn -- a logo poster, a
    # verbatim source, a suppressed title. Capture-only for the same reason as
    # the dimensions: the near-miss fit metric is filed, not built.
    text_point_size: Mapped[int | None] = mapped_column(SmallInteger)
    # When the eight facts above were last written. NULL means "never scored
    # under this taxonomy", which is every row that existed before this
    # migration. It is what makes the coverage gap visible instead of letting
    # an unscored row read as a clean one.
    quality_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Then append the new model at the **end** of `src/autoposter/db/models.py`:

```python
class ActionDismissal(Base):
    """One render row an operator told the Action Center to stop showing.

    Keyed by ``evidence`` -- a SHA-256 over the fact columns the queue judges,
    computed IN SQL (``actions/flags.evidence_expression``) so the queue's
    join is a plain equality and paging and totals stay server-side. The
    dismissal holds only while that hash still matches: a re-render that finds
    Finnish where it found English moves the hash, the join stops matching,
    and the row comes back on its own. That is roadmap 11b's "dismissal that
    sticks until the underlying facts change" and its "dismissals need a
    fingerprint-style identity so a re-render doesn't resurrect dismissed
    rows" -- both, with no sweep and no invalidation job between them.

    The unit is the render row, not the flag. The queue's Dismiss control is
    per row, and hiding a row under one flag while it still showed under
    another would be a button that visibly does nothing. ``flag`` records
    which flag the operator was looking at, for the audit; it does not narrow
    what the dismissal covers.

    ``ON DELETE CASCADE`` like every other child of ``media_items``:
    scheduler/prune.py hard-deletes item rows, and a dismissal must not
    outlive the item it is about.
    """

    __tablename__ = "action_dismissals"
    __table_args__ = (
        UniqueConstraint("item_id", "art_kind", name="uq_action_dismissal_item_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    art_kind: Mapped[str] = mapped_column(String(24))
    flag: Mapped[str | None] = mapped_column(String(32))
    evidence: Mapped[str] = mapped_column(String(64))
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 5: Write the migration**

Create `alembic/versions/a9d4e70c3b15_action_center_facts.py`:

```python
"""action center facts

Revision ID: a9d4e70c3b15
Revises: c7e1b93a4d20
Create Date: 2026-09-03 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9d4e70c3b15'
down_revision: Union[str, Sequence[str], None] = 'c7e1b93a4d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('renders', sa.Column('selected_language', sa.String(length=16), nullable=True))
    op.add_column('renders', sa.Column('language_rank', sa.SmallInteger(), nullable=True))
    op.add_column('renders', sa.Column('provider_rank', sa.SmallInteger(), nullable=True))
    # server_default, not just default: `default` is Python-side only, so
    # ADD COLUMN NOT NULL would fail against the populated renders table a
    # deployed instance already has. renders.adopted and renders.upload_status
    # already carry the same reasoning.
    op.add_column(
        'renders',
        sa.Column(
            'textless_fallback', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
    )
    op.add_column(
        'renders',
        sa.Column(
            'logo_text_fallback', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
    )
    op.add_column('renders', sa.Column('base_width', sa.SmallInteger(), nullable=True))
    op.add_column('renders', sa.Column('base_height', sa.SmallInteger(), nullable=True))
    op.add_column('renders', sa.Column('text_point_size', sa.SmallInteger(), nullable=True))
    op.add_column(
        'renders', sa.Column('quality_scored_at', sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        'action_dismissals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('art_kind', sa.String(length=24), nullable=False),
        sa.Column('flag', sa.String(length=32), nullable=True),
        sa.Column('evidence', sa.String(length=64), nullable=False),
        sa.Column(
            'dismissed_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['item_id'], ['media_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'art_kind', name='uq_action_dismissal_item_kind'),
    )
    op.create_index(
        op.f('ix_action_dismissals_item_id'), 'action_dismissals', ['item_id'], unique=False
    )
    # No index on any of the nine new renders columns, and none added to
    # status or adopted either. The library is ~16,000 items and renders is
    # per (item, art_kind), so tens of thousands of rows: a sequential scan is
    # the right answer at this size, and a speculative index would cost every
    # render write for a page an operator opens occasionally. The absence is
    # deliberate; revisit if it ever measures slow.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_action_dismissals_item_id'), table_name='action_dismissals')
    op.drop_table('action_dismissals')
    op.drop_column('renders', 'quality_scored_at')
    op.drop_column('renders', 'text_point_size')
    op.drop_column('renders', 'base_height')
    op.drop_column('renders', 'base_width')
    op.drop_column('renders', 'logo_text_fallback')
    op.drop_column('renders', 'textless_fallback')
    op.drop_column('renders', 'provider_rank')
    op.drop_column('renders', 'language_rank')
    op.drop_column('renders', 'selected_language')
```

- [ ] **Step 6: Prove the migration matches the models and leaves one head**

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac1-mig test sh -c \
  "set -o pipefail; pytest tests/test_migrations.py -q 2>&1 | tee /app/.superpowers/run-pac1-mig.log"
```

Expected: `3 passed`. This suite is the repository's only model/migration drift guard — an empty autogenerate that runs clean once shipped an image that could not boot. If it reports more than one head, or an autogenerate diff, fix the migration before going further; do not proceed on a red result.

- [ ] **Step 7: Create the actions package**

Create `src/autoposter/actions/__init__.py` with exactly one line:

```python
"""The Action Center's judgement layer: facts live on renders, flags live here."""
```

- [ ] **Step 8: Write the flag registry**

Create `src/autoposter/actions/flags.py`:

```python
"""What counts as an imperfect asset, expressed as SQL over the stored facts.

The Action Center stores FACTS on ``renders`` (written at
``render/pipeline.py``'s write-back) and derives JUDGEMENTS here, at query
time, against the LIVE config. Roadmap 11a's own risk note is the rule --
"Keep flags factual ('selected language = en, preferred = xx'), not
judgemental ('bad') -- the queue decides what's worth reviewing, data stays
stable". A stored ``language_miss`` boolean would be a lie the moment an
operator re-pointed ``language_order``, and re-backfilling fifteen thousand
rows on every config save is worse than a predicate.

Every predicate is a SQL expression rather than a Python function over fetched
rows, for two reasons that both matter:

 * filtering, counting and paging stay server-side, so the summary endpoint is
   one grouped query rather than a library walk; and
 * the one definition of a flag serves both the ``WHERE`` clause and the
   per-row labels -- the list endpoint selects each predicate a second time as
   a labelled boolean column, so what a row is *shown* as cannot drift from
   what it was *filtered* by.

Predicates may reference ``MediaItem`` as well as ``Render``: every caller
joins the two, and ``artwork.library_language_overrides`` (roadmap row 38)
makes the language preference a property of the library as well as the art
kind.

Nothing here writes anything. That is the property
``tests/test_action_flags.py`` pins twice over.
"""
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Text, and_, cast, func, literal, or_
from sqlalchemy.dialects.postgresql import BYTEA

from autoposter.config.schema import Config
from autoposter.db.models import MediaItem, Render
from autoposter.providers.ladder import UNRANKED

# The four values `renders.art_kind` holds, and the four
# `artwork.library_language_overrides` accepts. Spelled out rather than
# imported from render/pipeline.py's ART_KINDS_FOR: importing that module
# pulls plexapi, httpx and the badge compositor into every request that counts
# a flag. The agreement is pinned in tests/test_action_flags.py rather than
# assumed.
ART_KINDS = ("poster", "season_poster", "background", "title_card")


@dataclass(frozen=True)
class Flag:
    """One reason a render row might want an operator's attention."""

    code: str
    label: str
    #: One sentence the page shows beside the chip. Says what the fact is, not
    #: how bad it is.
    description: str
    #: Whether this flag is part of the queue's DEFAULT population. Off means
    #: the operator has to ask for it by name -- see `unknown_provenance`,
    #: whose population may be the whole adopted library.
    default_on: bool
    #: Whether this flag can fire on rows written before the Action Center
    #: shipped. False for the four that need the new bookkeeping, and the
    #: reason `unscored` exists at all.
    instant: bool
    predicate: Callable[[Config], ColumnElement[bool]]
    #: The factual sentence shown in the row's own detail cell. Reads only the
    #: row's stored columns, so it needs no config and no second query.
    detail: Callable[[Render], str]


def _language_order(config: Config, library: str | None, art_kind: str) -> list[str]:
    """The preference list this artifact is ranked against, live.

    The same rule as ``render/pipeline.py::language_order_for``, restated here
    rather than imported for the reason ART_KINDS is restated. Two statements
    of one rule drift, so the agreement is pinned by a test rather than left
    to good intentions.
    """
    if library is not None:
        override = config.artwork.library_language_overrides.get(library, {}).get(art_kind)
        if override:
            return override
    return getattr(config.artwork, art_kind).language_order


# --- the predicates ----------------------------------------------------------


def _missing(config: Config) -> ColumnElement[bool]:
    return Render.status == "no_art"


def _skipped(config: Config) -> ColumnElement[bool]:
    return Render.status == "skipped"


def _truncated(config: Config) -> ColumnElement[bool]:
    return Render.status == "truncated"


def _show_fallback(config: Config) -> ColumnElement[bool]:
    return Render.source_mode == "show_fallback"


def _upload_failed(config: Config) -> ColumnElement[bool]:
    return Render.upload_status == "failed"


def _unknown_provenance(config: Config) -> ColumnElement[bool]:
    return Render.adopted.is_(True)


def _language_miss(config: Config) -> ColumnElement[bool]:
    """The ladder did not achieve this art kind's first-choice language.

    Derived, never stored. ``language_rank`` records the position achieved
    under the order in force when the row rendered; that is history. An
    operator who re-points ``language_order`` today must see the queue change
    today, with no backfill, so the comparison is made here against
    ``order[0]`` as it stands this request.

    ``language_rank IS NOT NULL`` is the "a ladder actually ran and ranked
    something" gate. It is NULL on a manual override, on a row that produced
    no art, and on every row written before this taxonomy existed -- all of
    which also have a NULL ``selected_language``, which compares unequal to
    every preference. Without the gate the whole pre-existing library would
    read as a language miss the pipeline never made.

    Textless art carries no language tag and the ladder ranks it at ``xx``'s
    position, so a NULL is compared as ``xx`` here. That is what makes an
    ``xx``-leading order silent on a textless row.
    """
    achieved = func.coalesce(Render.selected_language, literal("xx"))
    terms: list[ColumnElement[bool]] = []
    for art_kind in ART_KINDS:
        overridden = {
            library: by_kind[art_kind]
            for library, by_kind in config.artwork.library_language_overrides.items()
            if by_kind.get(art_kind)
        }
        for library, order in overridden.items():
            terms.append(
                and_(
                    Render.art_kind == art_kind,
                    MediaItem.library == library,
                    achieved != literal(order[0]),
                )
            )
        base = getattr(config.artwork, art_kind).language_order
        if base:
            clauses = [Render.art_kind == art_kind, achieved != literal(base[0])]
            if overridden:
                # The libraries above answer for themselves; this clause is
                # every other library, or a row whose library the override
                # does not name would be judged twice under two orders.
                clauses.append(MediaItem.library.notin_(sorted(overridden)))
            terms.append(and_(*clauses))
    if not terms:
        return literal(False)
    return and_(Render.language_rank.isnot(None), or_(*terms))


def _provider_downgrade(config: Config) -> ColumnElement[bool]:
    """The winning provider is not the operator's first-choice provider.

    Live, like the language flag: ``provider_rank`` is the position in the
    runtime ladder at render time and is the evidence a row shows; the
    judgement is the comparison against ``config.providers.order[0]`` as it
    stands now, so re-ordering the providers re-shapes the queue with no row
    write.

    ``provider_rank IS NOT NULL`` gates it to rows a provider ladder actually
    chose. It is NULL for a manual override -- ``provider = 'manual'``, which
    no ladder position describes -- and for every pre-taxonomy row.
    """
    order = config.providers.order
    if not order:
        return literal(False)
    return and_(Render.provider_rank.isnot(None), Render.provider != literal(order[0]))


def _textless_miss(config: Config) -> ColumnElement[bool]:
    return Render.textless_fallback.is_(True)


def _logo_fallback(config: Config) -> ColumnElement[bool]:
    return Render.logo_text_fallback.is_(True)


def _unscored(config: Config) -> ColumnElement[bool]:
    """A rendered row this taxonomy has never scored.

    Scoped to ``rendered`` on purpose: a ``no_art`` row is already named by
    `missing`, and calling it unscored as well would count the same row under
    two chips and would make the backfill's population one it can never
    finish -- a row that produces no art never reaches the write-back that
    stamps ``quality_scored_at``.
    """
    return and_(Render.status == "rendered", Render.quality_scored_at.is_(None))


# --- the row details ---------------------------------------------------------


def _plain(text_value: str) -> Callable[[Render], str]:
    return lambda render: text_value


def _missing_detail(render: Render) -> str:
    return render.detail or "no art on any provider"


def _skipped_detail(render: Render) -> str:
    return render.detail or "skipped"


def _truncated_detail(render: Render) -> str:
    return render.detail or "the title does not fit at the minimum point size"


def _upload_detail(render: Render) -> str:
    return f"render {render.status}, upload {render.upload_status}"


def _language_detail(render: Render) -> str:
    language = render.selected_language or "untagged"
    if render.language_rank == UNRANKED:
        return f"selected {language}; not in the preference list at all"
    return f"selected {language}; rank {render.language_rank} in the order that rendered it"


def _provider_detail(render: Render) -> str:
    return f"selected {render.provider}; rank {render.provider_rank} in the ladder that ran"


# --- the registry ------------------------------------------------------------

_REGISTRY: tuple[Flag, ...] = (
    Flag(
        code="missing",
        label="No art found",
        description="No provider had artwork of this kind for this item.",
        default_on=True,
        instant=True,
        predicate=_missing,
        detail=_missing_detail,
    ),
    Flag(
        code="skipped",
        label="Skipped",
        description=(
            "The pipeline declined to render this artifact -- the art kind is "
            "disabled, the title matched a skip word, the item cannot be named, "
            "or online fetch is off and there is no local asset."
        ),
        default_on=True,
        instant=True,
        predicate=_skipped,
        detail=_skipped_detail,
    ),
    Flag(
        code="truncated",
        label="Text does not fit",
        description=(
            "The title would not fit its box at the minimum point size, so no "
            "file was written at all."
        ),
        default_on=True,
        instant=True,
        predicate=_truncated,
        detail=_truncated_detail,
    ),
    Flag(
        code="show_fallback",
        label="Styled from the show poster",
        description=(
            "No provider had art for this season, so the show's own poster was "
            "styled with the season text instead."
        ),
        default_on=True,
        instant=True,
        predicate=_show_fallback,
        detail=_plain("season art came from the show's poster"),
    ),
    Flag(
        code="upload_failed",
        label="Upload failed",
        description="The artwork was rendered but never reached Plex.",
        default_on=True,
        instant=True,
        predicate=_upload_failed,
        detail=_upload_detail,
    ),
    Flag(
        code="language_miss",
        label="Not the preferred language",
        description=(
            "The language the ladder achieved is not this art kind's first "
            "choice in the configuration as it stands now."
        ),
        default_on=True,
        instant=False,
        predicate=_language_miss,
        detail=_language_detail,
    ),
    Flag(
        code="provider_downgrade",
        label="Not the preferred provider",
        description=(
            "The provider that won is not the first in the configured order as "
            "it stands now."
        ),
        default_on=True,
        instant=False,
        predicate=_provider_downgrade,
        detail=_provider_detail,
    ),
    Flag(
        code="textless_miss",
        label="Text-bearing art taken",
        description=(
            "Textless art was preferred, no provider had any, and a "
            "text-bearing image was taken rather than nothing."
        ),
        default_on=True,
        instant=False,
        predicate=_textless_miss,
        detail=_plain("no textless art on any provider; a text-bearing image was used"),
    ),
    Flag(
        code="logo_fallback",
        label="Title text instead of a logo",
        description=(
            "This poster wanted a clearlogo, no provider had one, and the title "
            "text was drawn in its place."
        ),
        default_on=True,
        instant=False,
        predicate=_logo_fallback,
        detail=_plain("no clearlogo on any provider; the title text was drawn instead"),
    ),
    Flag(
        code="unknown_provenance",
        label="Adopted, provenance unknown",
        description=(
            "Artwork that was already on disk when this service took over. No "
            "provider, no source URL, no way to say what it is. Off by default: "
            "on a library that was adopted wholesale this is every row."
        ),
        default_on=False,
        instant=True,
        predicate=_unknown_provenance,
        detail=_plain("found on disk at adoption; no provider and no source URL"),
    ),
    Flag(
        code="unscored",
        label="Not yet scored",
        description=(
            "Rendered before the quality facts existed, so four of the flags "
            "cannot be evaluated for it until it re-renders."
        ),
        default_on=False,
        instant=True,
        predicate=_unscored,
        detail=_plain("rendered before the quality taxonomy; re-render to score it"),
    ),
)

#: Insertion-ordered, and that order is the order the page's chips appear in.
FLAGS: dict[str, Flag] = {flag.code: flag for flag in _REGISTRY}


def predicate_for(code: str, config: Config) -> ColumnElement[bool]:
    """The SQL this flag is, against the live config.

    Raises ``KeyError`` for an unknown code; the API turns that into a 400
    naming the codes it does have, rather than silently answering with an
    unfiltered queue.
    """
    return FLAGS[code].predicate(config)


def default_predicate(config: Config) -> ColumnElement[bool]:
    """The queue's default population: any default-on flag firing."""
    return or_(*[flag.predicate(config) for flag in FLAGS.values() if flag.default_on])


def detail_for(code: str, render: Render) -> str:
    """The factual sentence behind this flag, for this row."""
    return FLAGS[code].detail(render)


# The columns a dismissal is a dismissal *of*. `detail` is deliberately absent:
# it is free text the pipeline resets to None on every successful render, so
# folding it in would move the hash on every pass and resurrect every
# dismissal -- exactly the bug 11b names as its own risk.
#
# `quality_scored_at` itself is deliberately absent too, for the opposite
# reason: `unscored` rests on it, so leaving it out entirely would let a
# dismissal made while a row was unscored survive the very re-render that
# scores it. But the raw timestamp is not what is hashed -- it moves on
# every re-render even when scored-ness does not, which would resurrect that
# same dismissal on every later pass. What is hashed is scored-ness itself.
_EVIDENCE_COLUMNS = (
    Render.status,
    Render.source_mode,
    Render.upload_status,
    Render.adopted,
    Render.provider,
    Render.selected_language,
    Render.textless,
    Render.language_rank,
    Render.provider_rank,
    Render.textless_fallback,
    Render.logo_text_fallback,
    Render.quality_scored_at.isnot(None),
)


def evidence_expression() -> ColumnElement[str]:
    """This row's facts, hashed, as a SQL expression over ``renders``.

    In SQL rather than in Python so the dismissal join is a plain equality:
    that is what keeps filtering, paging and `total` server-side. Computing the
    hash in Python would mean fetching every candidate row to decide which ones
    to hide, which makes `total` a lie and pages arbitrarily short.

    ``sha256`` is a PostgreSQL built-in (11+) and needs no extension.
    ``coalesce`` to a sentinel rather than relying on ``concat_ws``, which
    skips NULLs -- without it ``(NULL, 'en')`` and ``('en', NULL)`` would hash
    identically and a fact moving between two columns would leave a dismissal
    standing.
    """
    parts = [
        func.coalesce(cast(column, Text), literal("~")) for column in _EVIDENCE_COLUMNS
    ]
    joined = func.concat_ws(literal("|"), *parts)
    return func.encode(func.sha256(cast(joined, BYTEA)), literal("hex"))
```

- [ ] **Step 9: Run the flag tests and watch them pass**

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac1-green1 test sh -c \
  "set -o pipefail; pytest tests/test_action_flags.py -q 2>&1 | tee /app/.superpowers/run-pac1-green1.log"
```

Expected: `22 passed`. If `test_the_registry_reads_the_same_language_order_the_pipeline_renders_with` fails, the two statements of the rule have already drifted — fix `_language_order`, not the test.

- [ ] **Step 10: Commit the facts columns and the registry**

```bash
git add src/autoposter/actions/ src/autoposter/db/models.py \
        alembic/versions/a9d4e70c3b15_action_center_facts.py \
        tests/test_action_flags.py
git commit -m "feat(actions): quality facts on renders, and the flag registry that judges them

Nine columns on renders holding what the ladder actually did, and a
registry of SQL predicates that decides -- against the config live at
request time -- which of them is worth an operator's attention. No
verdict is stored: re-pointing language_order or providers.order
re-shapes the queue with no row write, which is what the two
derived-not-stored tests pin.

Dismissals get their own table, keyed by a SHA-256 over the fact columns
computed in SQL, so the queue's join is an equality and paging and
totals stay server-side -- and so a fact changing returns the row with
no sweep behind it.

Carries the renders.status vocabulary fix: the model documented five
values while the pipeline wrote a sixth, and a registry written from
that comment would have dropped 'skipped' silently."
```

- [ ] **Step 11: Write the failing pipeline test**

Create `tests/test_pipeline_quality.py`:

```python
"""The Action Center's quality facts, through the real pipeline entry point.

Every test here drives ``render_artifact``. A helper-level test would prove
the values can be computed and say nothing about whether the write-back
records them -- and the write-back's placement is the whole design: it sits
below the "unchanged" fingerprint short-circuit, so a fact recorded there
survives a pass that changes nothing, unlike `detail`, which is blanked.
"""
from pathlib import Path

import httpx
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import Render
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import render_artifact
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _config(tmp_path):
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    return config


def _item():
    return ResolvedItem(
        rating_key="1", library="Movies", kind="movie", title="Dune: Part Two", year=2024,
        season_number=None, episode_number=None, root_folder="Dune (2024)",
        file_path="/mnt/Media/Movies/Dune (2024)/x.mkv", art_url=None,
        tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )


class _Provider:
    """One poster candidate, and optionally one logo candidate.

    ``name`` is a class-level attribute on the real clients
    (providers/tmdb.py's ``name = "TMDB"``), so it is one here too -- the
    runtime provider rank is read off exactly this attribute.
    """

    def __init__(self, name="TMDB", *, language=None, art=True, logo_url=None):
        self.name = name
        self._language = language
        self._art = art
        self._logo_url = logo_url
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind == "poster" and self._art:
            return [
                ArtCandidate(self.name, "https://img/poster.jpg", self._language, 2000, 3000, 5.0)
            ]
        if request.art_kind == "logo" and self._logo_url is not None:
            return [ArtCandidate(self.name, self._logo_url, "en", 800, 300, 5.0)]
        return []


def _fake_http():
    async def handler(request):
        return httpx.Response(200, content=b"fake-image-bytes")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_out_imagemagick(monkeypatch):
    """Record every argv passed to compositor.run without invoking ImageMagick.

    ``fit_point_size`` is the only other function on this path that shells out,
    so it is stubbed too -- and its stubbed 120 is exactly what the point-size
    capture test asserts arrives in the column.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: calls.append(argv))
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    return calls


async def test_a_render_records_the_language_it_achieved_and_its_rank(
    session, tmp_path, monkeypatch
):
    """Roadmap 11a's own stated acceptance: a forced en-only render on an
    xx-preferring config produces the language fact."""
    config = _config(tmp_path)
    assert config.artwork.poster.language_order == ["xx", "en", "fi"]
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    assert render.status == "rendered"
    assert render.selected_language == "en"
    assert render.language_rank == 1
    assert render.quality_scored_at is not None


async def test_an_unchanged_pass_does_not_wipe_the_quality_facts(
    session, tmp_path, monkeypatch
):
    """The entry-point law for this phase. The fingerprint short-circuit
    returns above the write-back, so a second identical pass must leave every
    fact exactly where the first one put it -- the failure mode `detail` has
    and `source_mode` was written to avoid."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        first = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )
        assert first.selected_language == "en"

        # A sentinel the second pass has no way to reproduce. If the
        # short-circuit path touched these columns at all -- blanking them, or
        # rewriting them from a selection it never made -- this is what
        # catches it.
        first.selected_language = "fi"
        first.language_rank = 2
        await session.commit()

        second = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    assert second.detail == "unchanged"
    assert second.selected_language == "fi"
    assert second.language_rank == 2
    assert second.quality_scored_at is not None


async def test_the_textless_fallback_is_recorded_when_a_text_bearing_image_is_taken(
    session, tmp_path, monkeypatch
):
    """`Selection.is_fallback` has been returned by the ladder since it was
    written and read by nobody. This is the line that reads it."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    assert render.textless_fallback is True


async def test_a_textless_pick_records_no_fallback(session, tmp_path, monkeypatch):
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language=None)],
        )

    assert render.textless_fallback is False
    assert render.selected_language is None
    assert render.language_rank == 0


async def test_the_logo_to_text_fallback_is_recorded_when_the_fallback_is_on(
    session, tmp_path, monkeypatch
):
    """The "taken" half of the logo decision, which until now materialised no
    variable at all -- only its suppressed sibling did."""
    config = _config(tmp_path)
    config.artwork.logo_text_fallback = True
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster",
            [_Provider(language="en", logo_url=None)],
        )

    assert render.logo_text_fallback is True


async def test_no_logo_with_the_fallback_off_records_no_logo_fallback(
    session, tmp_path, monkeypatch
):
    """With the fallback off the poster gets neither logo nor text, which is a
    different thing from wearing text in a logo's place."""
    config = _config(tmp_path)
    assert config.artwork.logo_text_fallback is False
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster",
            [_Provider(language="en", logo_url=None)],
        )

    assert render.logo_text_fallback is False


async def test_the_provider_rank_is_taken_against_the_runtime_ladder(
    session, tmp_path, monkeypatch
):
    """Against the list the pipeline was handed, never config.providers.order:
    app.py drops a configured provider with no implementation, so a
    config-index rank can name a position that never existed."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster",
            [_Provider("TVDB", art=False), _Provider("TMDB", language="en")],
        )

    assert render.provider == "TMDB"
    assert render.provider_rank == 1


async def test_a_manual_override_records_no_provider_rank_and_no_language(
    session, tmp_path, monkeypatch
):
    """A hand-placed file has no ladder position at all. Recording 0 would
    read as "the operator's first-choice provider"; recording anything else
    would read as a downgrade of a choice no ladder made."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _item()
    override = Path(config.manual_assets_root) / "Movies" / "Dune (2024)" / "poster.jpg"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_bytes(b"the operator's own poster")

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, resolved, "poster", [_Provider(language="en")],
        )

    assert render.provider == "manual"
    assert render.provider_rank is None
    assert render.selected_language is None
    assert render.language_rank is None


async def test_the_base_dimensions_and_the_fitted_point_size_are_captured(
    session, tmp_path, monkeypatch
):
    """Captured, enforced nowhere. Row 219 keeps the resolution-floor
    decision; what this buys is that answering it later is a small change
    instead of a re-backfill of the whole library."""
    config = _config(tmp_path)
    # use_logo off so the title text is actually drawn and a point size exists
    # to capture -- a logo poster suppresses the text entirely.
    config.artwork.use_logo = False
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    assert render.base_width == 2000
    assert render.base_height == 3000
    assert render.text_point_size == 120


async def test_the_facts_are_committed_not_merely_set_on_the_instance(
    session, tmp_path, monkeypatch
):
    """A page reads the row back from the database, not from the pipeline's
    identity map."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    session.expire_all()
    reread = (
        await session.execute(select(Render).where(Render.id == render.id))
    ).scalar_one()
    assert reread.selected_language == "en"
    assert reread.provider_rank == 0
    assert reread.quality_scored_at is not None
```

- [ ] **Step 12: Run the pipeline test to verify it fails**

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac1-red2 test sh -c \
  "set -o pipefail; pytest tests/test_pipeline_quality.py -q 2>&1 | tee /app/.superpowers/run-pac1-red2.log"
```

Expected: failures asserting `None == "en"` and `None == 1` — the columns exist (Step 4) but nothing writes them yet. That is the right RED. If instead you see a collection error, fix that first.

- [ ] **Step 13: Make the ladder's language normalisation public**

In `src/autoposter/providers/ladder.py`, replace:

```python
def _normalise(language: str | None) -> str | None:
    if language is None:
        return None
    return _THREE_TO_TWO.get(language, language)
```

with:

```python
def normalise_language(language: str | None) -> str | None:
    """The two-letter form of a provider's language tag.

    TVDB reports ISO-639-2/T three-letter codes and the config speaks
    two-letter ones, so ``eng`` and ``en`` are the same preference and must
    compare equal. Public because render/pipeline.py stores the normalised
    code as a quality fact: an ``eng`` sitting in that column would read as a
    language miss against every ``en``-preferring order.
    """
    if language is None:
        return None
    return _THREE_TO_TWO.get(language, language)
```

and update its single call site inside `language_rank`, replacing:

```python
    code = _normalise(candidate.language)
```

with:

```python
    code = normalise_language(candidate.language)
```

- [ ] **Step 14: Carry the fitted point size out of `compose_styled`**

In `src/autoposter/render/pipeline.py`, replace the `ComposeResult` field block:

```python
    output: Path
    truncated: bool
    detail: str | None = None
```

with:

```python
    output: Path
    truncated: bool
    detail: str | None = None
    # The point size the PRIMARY title block finally fitted at (textfit's
    # FitResult). Carried out because render_artifact records it as a quality
    # fact and the value is otherwise computed inside the loop below and
    # dropped. None when no title was drawn at all. Optional with a default so
    # every other caller of compose_styled -- api/testing.py, api/manual.py,
    # the artwork modes -- is untouched by its arrival.
    point_size: int | None = None
```

Then, in `compose_styled`, replace:

```python
    blocks = []
    if draw_text:
        blocks.append((settings.text, primary_text))
    if art_kind == "title_card":
        blocks.append((settings.episode_text, secondary_text))
    for style, text in blocks:
        if style is None or not text:
            continue
        prepared = prepare_text(text, style)
        font_path = str(Path(config.fonts_root) / style.font)
        fit = await asyncio.to_thread(
            fit_point_size, config.magick_binary, font_path, style, prepared
        )
```

with:

```python
    blocks = []
    if draw_text:
        blocks.append((settings.text, primary_text))
    if art_kind == "title_card":
        blocks.append((settings.episode_text, secondary_text))
    primary_point_size: int | None = None
    for style, text in blocks:
        if style is None or not text:
            continue
        prepared = prepare_text(text, style)
        font_path = str(Path(config.fonts_root) / style.font)
        fit = await asyncio.to_thread(
            fit_point_size, config.magick_binary, font_path, style, prepared
        )
        # Identity against settings.text rather than the loop position: on a
        # title card with draw_text off, the first block IS the episode text,
        # and recording its size as the title's would be a fact about the
        # wrong string.
        if style is settings.text:
            primary_point_size = fit.point_size
```

and replace the function's final line:

```python
    return ComposeResult(output=working, truncated=False)
```

with:

```python
    return ComposeResult(output=working, truncated=False, point_size=primary_point_size)
```

- [ ] **Step 15: Add the runtime provider rank helper**

In `src/autoposter/render/pipeline.py`, add this function immediately **above** `async def render_artifact(`:

```python
def _provider_rank(providers: list, provider_name: str | None) -> int | None:
    """Where the winning provider sat in the ladder this pass actually walked.

    Against the RUNTIME list, never ``config.providers.order``: app.py's
    ``_build_providers`` warns and drops a configured provider that has no
    implementation, so a config-index rank can claim a position that never
    existed on this deployment. A name the runtime list does not hold -- the
    ``"manual"`` a hand-placed override stamps -- has no ladder position at
    all and records None, rather than a number that would read as a downgrade
    of a choice no ladder made.
    """
    names = [getattr(provider, "name", None) for provider in providers]
    if provider_name is None or provider_name not in names:
        return None
    return names.index(provider_name)
```

- [ ] **Step 16: Widen the pipeline's ladder import**

Replace:

```python
from autoposter.providers.ladder import select_artwork
```

with:

```python
from autoposter.providers.ladder import language_rank, normalise_language, select_artwork
```

- [ ] **Step 17: Bind the six facts the selection path decides and discards**

In `render_artifact`, replace:

```python
        override = await asyncio.to_thread(manual_override_path, config, item, art_kind)
        show_fallback = False
        local_source = False
        chosen_candidate = None
```

with:

```python
        override = await asyncio.to_thread(manual_override_path, config, item, art_kind)
        show_fallback = False
        local_source = False
        chosen_candidate = None
        # Action Center quality facts (roadmap 11a). Each of these is already
        # decided somewhere below and, until this phase, thrown away: the
        # ladder returns is_fallback and nobody reads it, the logo branch
        # materialises only its suppressed half, and the language order is an
        # inline expression at the select_artwork call. They are bound here so
        # the write-back at the end of this function can record them whichever
        # branch ran.
        selection_order: list[str] = []
        textless_fallback = False
        logo_text_fallback_taken = False
        text_point_size: int | None = None
```

Next, replace:

```python
            selection = await select_artwork(
                providers,
                language_order_for(config, item.library, art_kind),
                art.ArtRequest(
                    art_kind=art_kind,
```

with:

```python
            selection_order = language_order_for(config, item.library, art_kind)
            selection = await select_artwork(
                providers,
                selection_order,
                art.ArtRequest(
                    art_kind=art_kind,
```

Next, in the season-poster fallback, replace:

```python
                selection = await select_artwork(
                    providers,
                    language_order_for(config, item.library, "poster"),
                    art.ArtRequest(
                        art_kind=art.POSTER,
```

with:

```python
                # The rank recorded below must be taken against the list this
                # request was actually ranked by. The show-poster request uses
                # the poster order, so re-binding it here is the difference
                # between a true fact and one computed against a list that did
                # not choose this image.
                selection_order = language_order_for(config, item.library, "poster")
                selection = await select_artwork(
                    providers,
                    selection_order,
                    art.ArtRequest(
                        art_kind=art.POSTER,
```

Next, replace:

```python
            candidate = selection.candidate
            chosen_candidate = candidate
            base_sha = await _download(http, candidate.url, working)
            source_url = candidate.url
            provider_name = candidate.provider
            textless = candidate.is_textless
```

with:

```python
            candidate = selection.candidate
            chosen_candidate = candidate
            base_sha = await _download(http, candidate.url, working)
            source_url = candidate.url
            provider_name = candidate.provider
            textless = candidate.is_textless
            # True exactly when the order preferred textless art, no provider
            # had any, and the ladder took a text-bearing image rather than
            # nothing. The ladder has returned this since it was written and
            # nothing has ever read it.
            textless_fallback = selection.is_fallback
```

Next, replace:

```python
                elif not config.artwork.logo_text_fallback:
                    suppress_text = True
```

with:

```python
                elif not config.artwork.logo_text_fallback:
                    suppress_text = True
                else:
                    # The other half of the same decision, which until now had
                    # no variable at all: no logo on any provider AND
                    # logo_text_fallback on, so this poster is wearing its
                    # title text in a logo's place. That is the fact roadmap
                    # 103 calls "logo-to-text fallback taken".
                    logo_text_fallback_taken = True
```

Next, replace:

```python
            await asyncio.to_thread(
                _publish, styled.output, target, config.backup_root, config.assets_root
            )
```

with:

```python
            text_point_size = styled.point_size
            await asyncio.to_thread(
                _publish, styled.output, target, config.backup_root, config.assets_root
            )
```

- [ ] **Step 18: Write the nine facts at the existing write-back**

In `render_artifact`, replace:

```python
    render.provider = provider_name
    render.source_url = source_url
    render.textless = textless
    render.base_sha256 = base_sha
```

with:

```python
    render.provider = provider_name
    render.source_url = source_url
    render.textless = textless
    # The Action Center's quality facts (roadmap 11a), here rather than
    # anywhere earlier for source_mode's own stated reason two blocks below:
    # the "unchanged" fingerprint short-circuit returns above this point, so a
    # fact recorded here survives a pass that changes nothing, while anything
    # written like `detail` would be blanked on the next visit.
    #
    # Facts only. Whether "en under an xx-preferring order" is worth an
    # operator's attention is decided by a SQL predicate in actions/flags.py,
    # against the config that is live when the queue is read -- never by a
    # verdict frozen here, which would go stale the moment the order changed.
    render.selected_language = (
        normalise_language(chosen_candidate.language) if chosen_candidate is not None else None
    )
    render.language_rank = (
        language_rank(chosen_candidate, selection_order)
        if chosen_candidate is not None and selection_order
        else None
    )
    render.provider_rank = _provider_rank(providers, provider_name)
    render.textless_fallback = textless_fallback
    render.logo_text_fallback = logo_text_fallback_taken
    render.base_width = chosen_candidate.width if chosen_candidate is not None else None
    render.base_height = chosen_candidate.height if chosen_candidate is not None else None
    render.text_point_size = text_point_size
    # Database clock, per the global constraint, exactly like rendered_at
    # below: the app and database clocks drift.
    render.quality_scored_at = func.now()
    render.base_sha256 = base_sha
```

- [ ] **Step 19: Run the pipeline test and watch it pass**

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac1-green2 test sh -c \
  "set -o pipefail; pytest tests/test_pipeline_quality.py tests/test_pipeline.py tests/test_ladder.py -q 2>&1 | tee /app/.superpowers/run-pac1-green2.log"
```

Expected: all pass. `tests/test_pipeline.py` and `tests/test_ladder.py` are run alongside because Steps 13–18 edited the modules they cover; a green new file beside a red old one is not a pass.

- [ ] **Step 20: Fix the `skipped` omission in the API docstring**

In `src/autoposter/api/routes.py`, in `item_filters`, replace:

```python
    "statuses" reports `Render.status` (pending|rendered|truncated|no_art|
    failed), not `Render.upload_status`. That is what list_items()'s own
```

with:

```python
    "statuses" reports `Render.status` (pending|rendered|truncated|no_art|
    failed|skipped), not `Render.upload_status`. That is what list_items()'s own
```

- [ ] **Step 21: Run the full suite and lint**

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac1-full test sh -c \
  "set -o pipefail; ruff check . && pytest -q 2>&1 | tee /app/.superpowers/run-pac1-full.log"
```

Expected: `ruff` clean, and a passing total equal to the Step 1 baseline plus the new tests (22 from `test_action_flags.py` + 10 from `test_pipeline_quality.py` = **+32**). Report the measured number, not the computed one, and explain any difference.

- [ ] **Step 22: Commit the pipeline bookkeeping**

```bash
git add src/autoposter/render/pipeline.py src/autoposter/providers/ladder.py \
        src/autoposter/api/routes.py tests/test_pipeline_quality.py
git commit -m "feat(render): record what the ladder actually did, at the write-back

Six facts the selection path already decides and discards -- the
achieved language and its rank, the runtime provider rank, the textless
fallback the ladder has always returned and nobody read, the logo-to-text
fallback that had no variable at all, and the base dimensions and fitted
point size -- now land on the render row.

At the write-back beside source_mode, not anywhere earlier, for the
reason source_mode's own comment gives: the unchanged-fingerprint
short-circuit returns above that block, so a fact written there survives
a pass that changes nothing. That property is pinned through
render_artifact itself, with a sentinel the second pass cannot reproduce.

The dimensions and the point size are captured and enforced nowhere:
roadmap row 219 keeps the resolution-floor decision, and capturing now is
what makes answering it later a small change rather than a re-backfill."
```

- [ ] **Step 23: Tear down the task's containers**

```bash
docker compose -p pac1 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

Never `down -v`: the volume is not this task's to destroy.

---

### Task 2: The API — `src/autoposter/api/action_center.py`

**Files:**
- Create: `src/autoposter/api/action_center.py`
- Modify: `src/autoposter/api/routes.py` (one import line, one `include_router` line with its comment)
- Modify: `tests/conftest.py` (add `test_api_action_center.py` to `DEEP_SUITES`)
- Test: `tests/test_api_action_center.py` (new)

**Interfaces:**

- **Consumes** (all produced by Task 1): `autoposter.actions.flags` — `FLAGS`, `Flag`, `predicate_for(code, config)`, `default_predicate(config)`, `detail_for(code, render)`, `evidence_expression()`; `autoposter.db.models.ActionDismissal`; the nine `Render` columns. Also the pre-existing `_enqueue_reprocess(session, item) -> int | None` from `autoposter.api.routes`, imported inside the handler (see Step 3's comment for why).
- **Produces**, consumed by Task 3 (the page) and extended by Task 4 (the backfill):

  ```python
  # autoposter/api/action_center.py
  router: APIRouter          # no prefix; mounted under routes.py's /api prefix
  DEFAULT_ACTIONS_LIMIT = 50
  MAX_ACTIONS_LIMIT = 200

  GET  /api/actions          ?flag&library&art_kind&include_dismissed&limit&offset
       -> {"total": int, "limit": int, "offset": int, "items": [ActionRow]}
       ActionRow = {"item_id", "art_kind", "title", "library", "kind", "status",
                    "upload_status", "provider", "flags": [str], "details": [str],
                    "dismissed": bool, "evidence": str,
                    "quality_scored_at": str|null, "updated_at": str}
  GET  /api/actions/summary  ?library&art_kind&include_dismissed
       -> {"total": int,
           "flags": [{"code","label","description","default_on","instant","count"}]}
  POST /api/actions/dismiss   {"item_id","art_kind","flag"?,"note"?}
       -> {"dismissed": true, "evidence": str}
  POST /api/actions/undismiss {"item_id","art_kind"} -> {"dismissed": false}
  POST /api/actions/rerender  {"item_id"} -> {"queued": bool, "job_id": int|null}
  POST /api/actions/bulk/rerender
       {"flag"?,"library"?,"art_kind"?,"include_dismissed"?,"apply": bool}
       -> {"status": "dry run"|"enqueued"|"complete",
           "matched": int, "items": int, "enqueued": int, "detail": str}

  # module-private, reused by Task 4
  def _flag_predicate(config, flag: str | None) -> ColumnElement[bool]   # 400 on unknown
  def _dismissal_join() -> ColumnElement[bool]
  ```

---

- [ ] **Step 1: Declare the new suite in the CI lane, and write the failing API test**

`tests/test_ci_path_filters.py` asserts that every `test_api_*.py` file is named in `DEEP_SUITES` or `FAST_API_SUITES`. A new API suite that is in neither turns CI red on its own commit. Add it first.

In `tests/conftest.py`, inside `DEEP_SUITES`, replace:

```python
        "test_api_actions.py",
        "test_api_artwork.py",
```

with:

```python
        "test_api_action_center.py",
        "test_api_actions.py",
        "test_api_artwork.py",
```

Then create `tests/test_api_action_center.py`:

```python
"""GET/POST /api/actions -- the Action Center's queue, counts and actions.

Two of these tests are the phase's load-bearing ones and are worth naming:
`test_editing_the_provider_order_changes_the_queue_with_no_row_write` proves
the judgement is derived rather than stored, through the real endpoint and the
real config holder; and
`test_a_dismissed_row_returns_once_its_facts_change` proves 11b's dismissal
identity, which is the risk that row names for itself.
"""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ActionDismissal, EventLog, Job, MediaItem, Render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
ADMIN_PASSWORD_HASH = hash_password(PASSWORD)


@pytest_asyncio.fixture
async def app(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x", fanart_apikey="x",
        webhook_secret="x", admin_password_hash=ADMIN_PASSWORD_HASH,
    )
    return create_app(load_config(EXAMPLE), session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _seed(session, *, rating_key, library="Movies", art_kind="poster", **render_fields):
    item = MediaItem(rating_key=rating_key, library=library, kind="movie", title=f"T{rating_key}")
    session.add(item)
    await session.flush()
    fields = {"status": "rendered", "asset_path": f"/assets/{rating_key}.jpg"}
    fields.update(render_fields)
    render = Render(item_id=item.id, art_kind=art_kind, **fields)
    session.add(render)
    await session.commit()
    return item, render


# --- GET /api/actions --------------------------------------------------------


async def test_the_queue_requires_a_session(client):
    assert (await client.get("/api/actions")).status_code == 401


async def test_a_flagged_row_is_listed_with_its_flag_and_its_factual_detail(
    client, auth_headers, session
):
    item, _ = await _seed(session, rating_key="1", status="no_art", detail="no poster art anywhere")
    await _seed(session, rating_key="2", status="rendered")

    body = (await client.get("/api/actions", headers=auth_headers)).json()

    assert body["total"] == 1
    row = body["items"][0]
    assert row["item_id"] == item.id
    assert row["art_kind"] == "poster"
    assert row["library"] == "Movies"
    assert row["flags"] == ["missing"]
    # The factual sentence, from the registry -- the page never restates a
    # fact of its own.
    assert row["details"] == ["no poster art anywhere"]
    assert row["dismissed"] is False
    assert len(row["evidence"]) == 64


async def test_an_unknown_flag_is_a_400_naming_the_flags_this_build_has(client, auth_headers):
    """Not a silently unfiltered queue: a typo in a saved URL would otherwise
    answer with the whole library and look like the filter working."""
    response = await client.get("/api/actions?flag=lanugage_miss", headers=auth_headers)

    assert response.status_code == 400
    assert "language_miss" in response.json()["detail"]


async def test_the_flag_filter_narrows_the_queue(client, auth_headers, session):
    missing, _ = await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="rendered", upload_status="failed")

    body = (await client.get("/api/actions?flag=missing", headers=auth_headers)).json()

    assert body["total"] == 1
    assert body["items"][0]["item_id"] == missing.id


async def test_the_library_and_art_kind_filters_narrow_the_queue(client, auth_headers, session):
    await _seed(session, rating_key="1", library="Movies", art_kind="poster", status="no_art")
    wanted, _ = await _seed(
        session, rating_key="2", library="Shows", art_kind="background", status="no_art"
    )

    body = (
        await client.get(
            "/api/actions?library=Shows&art_kind=background", headers=auth_headers
        )
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["item_id"] == wanted.id


async def test_paging_reports_a_total_and_never_re_serves_a_row(client, auth_headers, session):
    """`updated_at` alone is not a total order -- a full pass stamps thousands
    of rows inside one transaction timestamp, and page 2 would hand back rows
    from page 1. The id tiebreak is what makes the pager honest."""
    for n in range(5):
        await _seed(session, rating_key=str(n), status="no_art")

    first = (await client.get("/api/actions?limit=2&offset=0", headers=auth_headers)).json()
    second = (await client.get("/api/actions?limit=2&offset=2", headers=auth_headers)).json()

    assert first["total"] == 5
    assert first["limit"] == 2
    assert second["offset"] == 2
    seen = {row["item_id"] for row in first["items"]}
    assert seen.isdisjoint({row["item_id"] for row in second["items"]})


async def test_the_limit_is_clamped_to_the_endpoints_ceiling(client, auth_headers, session):
    await _seed(session, rating_key="1", status="no_art")

    body = (await client.get("/api/actions?limit=9999", headers=auth_headers)).json()

    assert body["limit"] == 200


# --- GET /api/actions/summary ------------------------------------------------


async def test_the_summary_counts_every_flag_and_totals_the_default_population(
    client, auth_headers, session
):
    await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="rendered", upload_status="failed")
    # Adopted is a flag with a count, and is NOT in the default total: on a
    # wholesale-adopted library it would be every row (A5).
    await _seed(session, rating_key="3", status="rendered", adopted=True)

    body = (await client.get("/api/actions/summary", headers=auth_headers)).json()
    counts = {entry["code"]: entry["count"] for entry in body["flags"]}

    assert counts["missing"] == 1
    assert counts["upload_failed"] == 1
    assert counts["unknown_provenance"] == 1
    assert body["total"] == 2
    labels = {entry["code"]: entry["label"] for entry in body["flags"]}
    assert labels["missing"]
    assert next(e for e in body["flags"] if e["code"] == "unknown_provenance")["default_on"] is False


# --- the derived-not-stored property, through the real endpoint --------------


async def test_editing_the_provider_order_changes_the_queue_with_no_row_write(
    app, client, auth_headers, session
):
    """The whole design in one test. The config holder is swapped exactly as a
    real config save swaps it, and the queue answers differently on the next
    request -- with the row's own columns untouched."""
    item, render = await _seed(session, rating_key="1", provider="TVDB", provider_rank=1)

    before = (
        await client.get("/api/actions?flag=provider_downgrade", headers=auth_headers)
    ).json()
    assert before["total"] == 1

    edited = load_config(EXAMPLE)
    edited.providers.order = ["TVDB", "TMDB", "Fanart"]
    app.state.config_holder.swap(edited)

    after = (
        await client.get("/api/actions?flag=provider_downgrade", headers=auth_headers)
    ).json()
    assert after["total"] == 0

    session.expire_all()
    reread = (await session.execute(select(Render).where(Render.id == render.id))).scalar_one()
    assert reread.provider == "TVDB"
    assert reread.provider_rank == 1


# --- dismissal ---------------------------------------------------------------


async def test_dismissing_a_row_takes_it_out_of_the_queue_and_the_counts(
    client, auth_headers, session
):
    item, _ = await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "missing"},
    )

    assert response.status_code == 200
    assert response.json()["dismissed"] is True
    assert len(response.json()["evidence"]) == 64
    assert (await client.get("/api/actions", headers=auth_headers)).json()["total"] == 0
    summary = (await client.get("/api/actions/summary", headers=auth_headers)).json()
    assert {e["code"]: e["count"] for e in summary["flags"]}["missing"] == 0


async def test_a_dismissed_row_returns_once_its_facts_change(client, auth_headers, session):
    """11b's own risk, answered by the mechanism rather than by a sweep: the
    dismissal holds an evidence hash, the queue recomputes that hash in SQL on
    every read, and a fact moving makes the join stop matching."""
    from datetime import datetime, timezone

    item, render = await _seed(session, rating_key="1", status="no_art")
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "missing"},
    )
    assert (await client.get("/api/actions", headers=auth_headers)).json()["total"] == 0

    # What a re-render that found art would do to this row -- including the
    # quality stamp, so this row does not also read as `unscored` and the
    # assertion below stays isolated to the one fact that actually moved.
    render.status = "rendered"
    render.upload_status = "failed"
    render.quality_scored_at = datetime.now(timezone.utc)
    await session.commit()

    body = (await client.get("/api/actions", headers=auth_headers)).json()
    assert body["total"] == 1
    assert body["items"][0]["flags"] == ["upload_failed"]
    assert body["items"][0]["dismissed"] is False


async def test_a_dismissed_row_returns_once_it_is_scored(client, auth_headers, session):
    """`unscored` rests on `quality_scored_at IS NULL`; the evidence hash must
    track scored-ness rather than the raw timestamp, or a re-render that
    finally scores this row could never resurrect a dismissal made while it
    was unscored."""
    from datetime import datetime, timezone

    item, render = await _seed(
        session, rating_key="1", status="rendered", upload_status="failed", quality_scored_at=None
    )
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "unscored"},
    )
    assert (await client.get("/api/actions", headers=auth_headers)).json()["total"] == 0

    # What the render that finally scores this row would do to it.
    render.quality_scored_at = datetime.now(timezone.utc)
    await session.commit()

    body = (await client.get("/api/actions", headers=auth_headers)).json()
    assert body["total"] == 1
    assert body["items"][0]["flags"] == ["upload_failed"]
    assert body["items"][0]["dismissed"] is False


async def test_include_dismissed_shows_the_row_and_marks_it(client, auth_headers, session):
    item, _ = await _seed(session, rating_key="1", status="no_art")
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "missing"},
    )

    body = (
        await client.get("/api/actions?include_dismissed=true", headers=auth_headers)
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["dismissed"] is True


async def test_undismissing_brings_the_row_back(client, auth_headers, session):
    item, _ = await _seed(session, rating_key="1", status="no_art")
    await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster", "flag": "missing"},
    )

    response = await client.post(
        "/api/actions/undismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "poster"},
    )

    assert response.status_code == 200
    assert response.json()["dismissed"] is False
    assert (await client.get("/api/actions", headers=auth_headers)).json()["total"] == 1
    assert (await session.execute(select(ActionDismissal))).scalars().all() == []


async def test_dismissing_twice_updates_the_evidence_rather_than_erroring(
    client, auth_headers, session
):
    """UNIQUE(item_id, art_kind) makes the second press a conflict; an
    operator pressing Dismiss on a row whose facts have moved must re-dismiss
    it, not see a 500."""
    item, render = await _seed(session, rating_key="1", status="no_art")
    payload = {"item_id": item.id, "art_kind": "poster", "flag": "missing"}
    first = (await client.post("/api/actions/dismiss", headers=auth_headers, json=payload)).json()

    render.status = "skipped"
    await session.commit()
    second = (await client.post("/api/actions/dismiss", headers=auth_headers, json=payload)).json()

    assert second["evidence"] != first["evidence"]
    rows = (await session.execute(select(ActionDismissal))).scalars().all()
    assert len(rows) == 1


async def test_dismissing_a_render_that_does_not_exist_is_a_404(client, auth_headers, session):
    item, _ = await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/dismiss",
        headers=auth_headers,
        json={"item_id": item.id, "art_kind": "title_card"},
    )

    assert response.status_code == 404


# --- the actions, which are enqueues -----------------------------------------


async def test_a_re_search_queues_one_process_item_job(client, auth_headers, session):
    item, _ = await _seed(session, rating_key="1", status="no_art")

    response = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": item.id}
    )

    assert response.status_code == 200
    assert response.json()["queued"] is True
    jobs = (await session.execute(select(Job))).scalars().all()
    assert [job.kind for job in jobs] == ["process_item"]


async def test_asking_twice_while_the_first_is_pending_queues_nothing_the_second_time(
    client, auth_headers, session
):
    """`uq_jobs_pending_dedupe` is what makes a bulk press safe, so it is
    pinned here rather than assumed. A second queue is an answer, not an
    error: 200 with `queued: false`, exactly as /items/{id}/reprocess."""
    item, _ = await _seed(session, rating_key="1", status="no_art")

    first = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": item.id}
    )
    second = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": item.id}
    )

    assert first.json()["queued"] is True
    assert second.status_code == 200
    assert second.json() == {"queued": False, "job_id": None}
    assert len((await session.execute(select(Job))).scalars().all()) == 1


async def test_a_re_search_for_an_unknown_item_is_a_404(client, auth_headers):
    response = await client.post(
        "/api/actions/rerender", headers=auth_headers, json={"item_id": 999999}
    )
    assert response.status_code == 404


async def test_the_bulk_dry_run_counts_and_writes_nothing(client, auth_headers, session):
    await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="no_art")

    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": False}
        )
    ).json()

    assert body["status"] == "dry run"
    assert body["matched"] == 2
    assert body["items"] == 2
    assert body["enqueued"] == 0
    assert (await session.execute(select(Job))).scalars().all() == []
    assert (await session.execute(select(EventLog))).scalars().all() == []


async def test_the_bulk_apply_enqueues_and_records_one_event(client, auth_headers, session):
    await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="no_art")

    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": True}
        )
    ).json()

    assert body["status"] == "enqueued"
    assert body["enqueued"] == 2
    assert len((await session.execute(select(Job))).scalars().all()) == 2
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [event.event_type for event in events] == ["action_center_bulk_rerender"]


async def test_the_bulk_apply_collapses_two_flagged_kinds_of_one_item_into_one_job(
    client, auth_headers, session
):
    """11b's stated risk is a burst. The unit of the queue is a render row and
    the unit of the queue's work is an ITEM, so 400 flagged rows across 200
    items are 200 jobs, not 400 -- and the pending dedupe collapses the rest."""
    item = MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune")
    session.add(item)
    await session.flush()
    session.add_all([
        Render(item_id=item.id, art_kind="poster", status="no_art", asset_path="/a.jpg"),
        Render(item_id=item.id, art_kind="background", status="no_art", asset_path="/b.jpg"),
    ])
    await session.commit()

    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": True}
        )
    ).json()

    assert body["matched"] == 2
    assert body["items"] == 1
    assert body["enqueued"] == 1
    assert len((await session.execute(select(Job))).scalars().all()) == 1


async def test_the_bulk_apply_honours_the_flag_filter(client, auth_headers, session):
    wanted, _ = await _seed(session, rating_key="1", status="no_art")
    await _seed(session, rating_key="2", status="rendered", upload_status="failed")

    body = (
        await client.post(
            "/api/actions/bulk/rerender",
            headers=auth_headers,
            json={"apply": True, "flag": "missing"},
        )
    ).json()

    assert body["matched"] == 1
    assert body["enqueued"] == 1


async def test_the_bulk_apply_over_an_empty_match_reports_complete(client, auth_headers):
    body = (
        await client.post(
            "/api/actions/bulk/rerender", headers=auth_headers, json={"apply": True}
        )
    ).json()

    assert body["status"] == "complete"
    assert body["enqueued"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker compose -p pac2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac2-red test sh -c \
  "set -o pipefail; pytest tests/test_api_action_center.py -q 2>&1 | tee /app/.superpowers/run-pac2-red.log"
```

Expected: every test fails with a 404 from the ASGI app — the routes do not exist yet.

- [ ] **Step 3: Write the module**

Create `src/autoposter/api/action_center.py`:

```python
"""The Action Center: the curation queue over the artwork this service chose.

Roadmap rows 11a/11b. The unit is one ``renders`` row -- one chosen artwork
asset, which is exactly what ``UNIQUE(item_id, art_kind)`` already keys -- and
every reason a row is in the queue is a SQL predicate from
``actions/flags.py``, built against the config that is live for THIS request.
Nothing here stores a verdict: re-pointing ``language_order`` or
``providers.order`` re-shapes every list and every count with no row write.

Its own module rather than more of ``routes.py`` because the queries here are
a different kind. Every other listing selects columns; these select predicates
-- once into the WHERE clause and again as labelled boolean columns, so what a
row is *shown* as cannot drift from what it was *filtered* by.

Every action is an ENQUEUE. ``_enqueue_reprocess`` plus the
``uq_jobs_pending_dedupe`` partial index is what answers 11b's own stated risk
-- "one 're-search all 400 flagged items' click is a burst": the bulk press
collapses to distinct items, asking twice while the first is pending queues
nothing the second time, and the worker pool paces the provider calls exactly
as an ordinary pass does. There is no inline provider call anywhere in this
file.

Two honest caveats that the page's copy repeats, and that no amount of UI can
remove:

 * A re-search is a re-*search*, not a guarantee. An already-rendered row
   re-runs provider selection against the 24h ``provider_cache`` and may
   legitimately find the same art and stay flagged.
 * The work is queued per ITEM, because ``process_item`` is the only unit the
   queue has. Re-searching a flagged poster re-renders that item's background
   too.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.dialects.postgresql import insert

from autoposter.actions import flags
from autoposter.api.auth import require_session
from autoposter.db.models import ActionDismissal, EventLog, MediaItem, Render
from autoposter.db.models import Session as SessionModel

router = APIRouter()

DEFAULT_ACTIONS_LIMIT = 50
MAX_ACTIONS_LIMIT = 200


def _flag_predicate(config, flag: str | None):
    """The WHERE clause one chip means, or the default population for no chip.

    An unknown code is a 400 naming what this build has, not a silently
    unfiltered queue: a typo in a bookmarked URL would otherwise answer with
    the whole library and read as the filter working.
    """
    if flag is None:
        return flags.default_predicate(config)
    try:
        return flags.predicate_for(flag, config)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown flag {flag!r}; this build has: " + ", ".join(flags.FLAGS)
            ),
        ) from None


def _dismissal_join():
    """The LEFT JOIN condition that hides a dismissed row -- and only while its
    facts hold.

    The evidence equality is the whole mechanism. ``evidence_expression()``
    recomputes the hash from the row's current columns on every read, so a
    re-render that changes a fact makes this condition stop matching and the
    row returns on its own: no sweep, no invalidation job, no resurrection
    bug. ``UNIQUE(item_id, art_kind)`` on the dismissals table means this join
    can never multiply a row, which is what keeps ``total`` honest.
    """
    return and_(
        ActionDismissal.item_id == Render.item_id,
        ActionDismissal.art_kind == Render.art_kind,
        ActionDismissal.evidence == flags.evidence_expression(),
    )


def _scope(library: str | None, art_kind: str | None, include_dismissed: bool) -> list:
    conditions = []
    if library is not None:
        conditions.append(MediaItem.library == library)
    if art_kind is not None:
        conditions.append(Render.art_kind == art_kind)
    if not include_dismissed:
        conditions.append(ActionDismissal.id.is_(None))
    return conditions


@router.get("/actions")
async def list_actions(
    request: Request,
    flag: str | None = None,
    library: str | None = None,
    art_kind: str | None = None,
    include_dismissed: bool = False,
    limit: int = DEFAULT_ACTIONS_LIMIT,
    offset: int = 0,
    _: SessionModel = Depends(require_session),
) -> dict:
    """One page of the queue, with the whole match's `total`.

    Each registered flag is selected a second time as a labelled boolean, so
    the row's own ``flags`` list is computed by the same SQL that filtered it.
    Deriving the labels in Python instead would be a second definition of each
    flag, and the two would eventually disagree about the same row.

    The order is total -- ``updated_at DESC, id DESC``. ``updated_at`` alone is
    not: a full pass stamps thousands of rows inside one transaction
    timestamp, and page 2 would hand back rows page 1 already showed.
    """
    config = request.app.state.config_holder.current
    capped_limit = min(max(limit, 1), MAX_ACTIONS_LIMIT)
    capped_offset = max(offset, 0)
    conditions = [_flag_predicate(config, flag)]
    conditions.extend(_scope(library, art_kind, include_dismissed))

    labelled = [
        entry.predicate(config).label(f"is_{code}") for code, entry in flags.FLAGS.items()
    ]

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # A separate COUNT(*), not len() of a fetched result: renders runs to
        # tens of thousands of rows.
        total = (
            await session.execute(
                select(func.count())
                .select_from(Render)
                .join(MediaItem, Render.item_id == MediaItem.id)
                .outerjoin(ActionDismissal, _dismissal_join())
                .where(*conditions)
            )
        ).scalar_one()

        rows = (
            await session.execute(
                select(
                    Render,
                    MediaItem.title,
                    MediaItem.library,
                    MediaItem.kind,
                    flags.evidence_expression().label("evidence"),
                    ActionDismissal.id.isnot(None).label("dismissed"),
                    *labelled,
                )
                .join(MediaItem, Render.item_id == MediaItem.id)
                .outerjoin(ActionDismissal, _dismissal_join())
                .where(*conditions)
                .order_by(Render.updated_at.desc(), Render.id.desc())
                .limit(capped_limit)
                .offset(capped_offset)
            )
        ).all()

    items = []
    for row in rows:
        render = row[0]
        fired = [code for code in flags.FLAGS if getattr(row, f"is_{code}")]
        items.append(
            {
                "item_id": render.item_id,
                "art_kind": render.art_kind,
                "title": row.title,
                "library": row.library,
                "kind": row.kind,
                "status": render.status,
                "upload_status": render.upload_status,
                "provider": render.provider,
                "flags": fired,
                # The factual sentence behind each flag, in the same order, so
                # the page renders the server's own words rather than
                # restating a fact it would have to keep in step.
                "details": [flags.detail_for(code, render) for code in fired],
                "dismissed": bool(row.dismissed),
                "evidence": row.evidence,
                "quality_scored_at": render.quality_scored_at,
                "updated_at": render.updated_at,
            }
        )

    return {"total": total, "limit": capped_limit, "offset": capped_offset, "items": items}


@router.get("/actions/summary")
async def actions_summary(
    request: Request,
    library: str | None = None,
    art_kind: str | None = None,
    include_dismissed: bool = False,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Every flag's count, and the default population's total, in one query.

    One grouped pass with a conditional sum per flag rather than a query per
    chip: eleven round trips for a header row would be eleven sequential scans
    of the same table.

    The registry's label, description and both switches ride along, so the
    page renders the chips the server actually has rather than a list of its
    own that a new flag would silently fall out of.
    """
    config = request.app.state.config_holder.current
    conditions = _scope(library, art_kind, include_dismissed)

    counters = [
        func.sum(case((entry.predicate(config), 1), else_=0)).label(f"n_{code}")
        for code, entry in flags.FLAGS.items()
    ]
    counters.append(
        func.sum(case((flags.default_predicate(config), 1), else_=0)).label("n_default")
    )

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        row = (
            await session.execute(
                select(*counters)
                .select_from(Render)
                .join(MediaItem, Render.item_id == MediaItem.id)
                .outerjoin(ActionDismissal, _dismissal_join())
                .where(*conditions)
            )
        ).one()

    # SUM over no rows is NULL, not 0 -- an empty library must read as zero
    # counts rather than as eleven nulls the page would render as blanks.
    def count(name: str) -> int:
        return int(getattr(row, name) or 0)

    return {
        "total": count("n_default"),
        "flags": [
            {
                "code": code,
                "label": entry.label,
                "description": entry.description,
                "default_on": entry.default_on,
                "instant": entry.instant,
                "count": count(f"n_{code}"),
            }
            for code, entry in flags.FLAGS.items()
        ],
    }


class DismissBody(BaseModel):
    item_id: int
    art_kind: str
    #: Which chip the operator was looking at. Recorded for the audit; it does
    #: not narrow what the dismissal covers -- see ActionDismissal's docstring.
    flag: str | None = None
    note: str | None = Field(default=None, max_length=500)


@router.post("/actions/dismiss")
async def dismiss_action(
    body: DismissBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Stop showing this render row while its facts stay as they are.

    The evidence is computed by the database, from the same expression the
    queue's join recomputes on every read. Computing it here in Python instead
    would be a second definition of "these facts", and the day the two
    disagreed every dismissal would either stick forever or never stick at
    all.

    Upserted rather than inserted: ``UNIQUE(item_id, art_kind)`` makes a
    second press a conflict, and an operator re-dismissing a row whose facts
    have moved must get a fresh dismissal, not a 500.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        evidence = (
            await session.execute(
                select(flags.evidence_expression()).where(
                    Render.item_id == body.item_id, Render.art_kind == body.art_kind
                )
            )
        ).scalar_one_or_none()
        if evidence is None:
            raise HTTPException(
                status_code=404, detail="no render row for that item and art kind"
            )

        await session.execute(
            insert(ActionDismissal)
            .values(
                item_id=body.item_id,
                art_kind=body.art_kind,
                flag=body.flag,
                evidence=evidence,
                note=body.note,
            )
            .on_conflict_do_update(
                constraint="uq_action_dismissal_item_kind",
                set_={
                    "evidence": evidence,
                    "flag": body.flag,
                    "note": body.note,
                    "dismissed_at": func.now(),
                },
            )
        )
        await session.commit()

    return {"dismissed": True, "evidence": evidence}


class UndismissBody(BaseModel):
    item_id: int
    art_kind: str


@router.post("/actions/undismiss")
async def undismiss_action(
    body: UndismissBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Put the row back in the queue.

    Deleting the row rather than flagging it: a dismissal is a hiding rule and
    nothing else, it carries no history worth keeping, and a soft-deleted one
    would have to be excluded from the join that is the whole mechanism.
    Undismissing something that was never dismissed is a no-op answering 200,
    because that is what the operator asked for and it is now true.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await session.execute(
            delete(ActionDismissal).where(
                ActionDismissal.item_id == body.item_id,
                ActionDismissal.art_kind == body.art_kind,
            )
        )
        await session.commit()
    return {"dismissed": False}


class RerenderBody(BaseModel):
    item_id: int


@router.post("/actions/rerender")
async def rerender_action(
    body: RerenderBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Queue one item for another pass.

    A 200 with ``queued: false`` when the pending dedupe swallows it, not a
    409: the work IS queued, which is what the operator wanted, and styling
    that as a failure would teach them to press again. This mirrors
    ``/items/{id}/reprocess`` exactly, which is the point -- two enqueue
    surfaces that answered differently would be two contracts.
    """
    # Deferred to here rather than imported at module scope: api/routes.py
    # imports this module's router, so the other direction is a cycle. Shared
    # rather than hand-built so the dedupe key cannot drift from the one every
    # other intake path uses.
    from autoposter.api.routes import _enqueue_reprocess

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = (
            await session.execute(select(MediaItem).where(MediaItem.id == body.item_id))
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        job_id = await _enqueue_reprocess(session, item)
    return {"queued": job_id is not None, "job_id": job_id}


class BulkRerenderBody(BaseModel):
    flag: str | None = None
    library: str | None = None
    art_kind: str | None = None
    include_dismissed: bool = False
    #: False is the unguarded offer -- it writes nothing and answers with the
    #: numbers. True is what the page's two-step arm sends.
    apply: bool = False


@router.post("/actions/bulk/rerender")
async def bulk_rerender_action(
    body: BulkRerenderBody, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Queue one batch of the current filter, or count what one would queue.

    Always a 200 with a ``status`` field. ``dry run``, ``enqueued`` and
    ``complete`` are all expected answers to an honest question, not errors
    for the client to style as failures -- the ``facts_backfill`` precedent.

    One batch of ``scheduler.drift_batch_size`` per press, not the whole
    match: the response has to stay bounded, and the operator pressing again
    is the pacing. ``matched`` counts render ROWS and ``items`` counts the
    distinct items in this batch, because the queue's unit is a row and the
    work's unit is an item -- 400 flagged rows across 200 items are 200 jobs.
    ``enqueued`` is jobs actually created, so a batch the pending dedupe
    swallowed reports 0 rather than claiming work it did not queue.

    One ``events_log`` row per applied press, the ops rule: a write nobody can
    find afterwards is not an operator action, it is a mystery. The dry run
    writes none, because it changed nothing.
    """
    from autoposter.api.routes import _enqueue_reprocess

    config = request.app.state.config_holder.current
    conditions = [_flag_predicate(config, body.flag)]
    conditions.extend(_scope(body.library, body.art_kind, body.include_dismissed))
    batch_size = config.scheduler.drift_batch_size

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        matched = (
            await session.execute(
                select(func.count())
                .select_from(Render)
                .join(MediaItem, Render.item_id == MediaItem.id)
                .outerjoin(ActionDismissal, _dismissal_join())
                .where(*conditions)
            )
        ).scalar_one()

        item_ids = (
            (
                await session.execute(
                    select(Render.item_id)
                    .join(MediaItem, Render.item_id == MediaItem.id)
                    .outerjoin(ActionDismissal, _dismissal_join())
                    .where(*conditions)
                    .group_by(Render.item_id)
                    .order_by(Render.item_id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

        if not body.apply:
            return {
                "status": "dry run",
                "matched": matched,
                "items": len(item_ids),
                "enqueued": 0,
                "detail": (
                    f"{matched} flagged row(s) across {len(item_ids)} item(s) in this "
                    "batch. Nothing was queued."
                ),
            }

        items = (
            (await session.execute(select(MediaItem).where(MediaItem.id.in_(item_ids))))
            .scalars()
            .all()
        ) if item_ids else []

        enqueued = 0
        for item in items:
            if await _enqueue_reprocess(session, item) is not None:
                enqueued += 1

        status = "complete" if not item_ids else "enqueued"
        detail = (
            "complete: nothing matches this filter"
            if not item_ids
            else (
                f"queued {enqueued} of {len(item_ids)} item(s) carrying {matched} "
                "flagged row(s); a re-search may legitimately find the same art"
            )
        )
        session.add(
            EventLog(
                source="actions",
                event_type="action_center_bulk_rerender",
                payload={
                    "flag": body.flag,
                    "library": body.library,
                    "art_kind": body.art_kind,
                    "matched": matched,
                    "items": len(item_ids),
                    "enqueued": enqueued,
                },
                outcome="action center bulk re-search: " + detail,
            )
        )
        await session.commit()

    return {
        "status": status,
        "matched": matched,
        "items": len(item_ids),
        "enqueued": enqueued,
        "detail": detail,
    }
```

- [ ] **Step 4: Mount the router**

In `src/autoposter/api/routes.py`, add the import alongside the other router imports — it sorts first alphabetically in that block:

```python
from autoposter.api.artwork import router as artwork_router
```

becomes

```python
from autoposter.api.action_center import router as action_center_router
from autoposter.api.artwork import router as artwork_router
```

Then add the mount at the end of the `include_router` block, after `version_router`:

```python
# The Action Center (roadmap rows 11a/11b): the curation queue over the
# artwork this service chose and rendered. Its own module because the queries
# there are a different kind from every other listing here -- they select
# predicates built from the LIVE config, once into the WHERE clause and again
# as labelled booleans, so that "why is this row here" and "which rows are
# here" are one definition. Folding that into this module would put a second,
# subtler kind of query beside the plain column listings.
router.include_router(action_center_router)
```

- [ ] **Step 5: Run the API test and watch it pass**

```bash
docker compose -p pac2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac2-green test sh -c \
  "set -o pipefail; pytest tests/test_api_action_center.py tests/test_ci_path_filters.py -q 2>&1 | tee /app/.superpowers/run-pac2-green.log"
```

Expected: all pass. `test_ci_path_filters.py` runs alongside because Step 1 edited the lane lists it reads; a new API suite that is green while the lane guard is red has not shipped.

- [ ] **Step 6: Run the full suite and lint**

```bash
docker compose -p pac2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac2-full test sh -c \
  "set -o pipefail; ruff check . && pytest -q 2>&1 | tee /app/.superpowers/run-pac2-full.log"
```

Expected: `ruff` clean, and the Task 1 total plus **+24** (the new API suite). Report the measured number.

- [ ] **Step 7: Commit**

```bash
git add src/autoposter/api/action_center.py src/autoposter/api/routes.py \
        tests/conftest.py tests/test_api_action_center.py
git commit -m "feat(api): the Action Center's queue, counts and actions

List, summary, dismiss, undismiss, re-search and bulk re-search. Every
query builds its predicates from the config live for that request, so
re-pointing providers.order changes the queue on the next read with no
row write -- pinned through the real endpoint and the real config holder.

Each flag is selected twice: once into the WHERE clause and again as a
labelled boolean, so a row's own flag list is computed by the same SQL
that filtered it and the two cannot drift.

Dismissals join on a SHA-256 the database recomputes from the row's
current facts, which is how a dismissal stops holding the moment a
re-render moves one -- no sweep, no invalidation job. Both halves are
pinned.

Every action is an enqueue through the shared _enqueue_reprocess and the
pending-dedupe index: two flagged kinds of one item collapse to one job,
and asking twice while the first is pending queues nothing.

The new suite is declared in DEEP_SUITES in the same commit, per the
lane guard's own contract."
```

- [ ] **Step 8: Tear down**

```bash
docker compose -p pac2 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

### Task 3: The page — `frontend/src/pages/ActionCenter.tsx`

**Files:**
- Modify: `frontend/src/api/types.ts` (four interfaces appended at the end of the file)
- Create: `frontend/src/pages/ActionCenter.tsx`
- Create: `frontend/src/pages/action-center.css`
- Modify: `frontend/src/App.tsx` (one import, one `<Route>` before the catch-all)
- Modify: `frontend/src/shell/Sidebar.tsx` (one `ICONS` entry, one `NAV` entry)
- Test: `frontend/src/shell/Sidebar.test.tsx` (one added `it`)
- Test: `frontend/src/pages/ActionCenter.test.tsx` (new)

**Interfaces:**

- **Consumes** (all produced by Task 2): `GET /api/actions`, `GET /api/actions/summary`, `POST /api/actions/dismiss`, `POST /api/actions/undismiss`, `POST /api/actions/rerender`, `POST /api/actions/bulk/rerender`; plus the pre-existing `GET /api/items/filters` (`ItemFiltersResponse`), `apiFetch<T>(path, init?)` from `../api/client`, and `formatTime` from `../format`.
- **Produces**, extended by Task 4:

  ```ts
  // frontend/src/api/types.ts
  export interface ActionFlagSummary {
    code: string; label: string; description: string;
    default_on: boolean; instant: boolean; count: number;
  }
  export interface ActionsSummaryResponse { total: number; flags: ActionFlagSummary[]; }
  export interface ActionRow {
    item_id: number; art_kind: string; title: string; library: string; kind: string;
    status: string; upload_status: string; provider: string | null;
    flags: string[]; details: string[]; dismissed: boolean; evidence: string;
    quality_scored_at: string | null; updated_at: string;
  }
  export interface ActionsResponse {
    total: number; limit: number; offset: number; items: ActionRow[];
  }
  export interface BulkRerenderResponse {
    status: "dry run" | "enqueued" | "complete";
    matched: number; items: number; enqueued: number; detail: string;
  }

  // frontend/src/pages/ActionCenter.tsx
  export function ActionCenter(): JSX.Element     // named export, route "/actions"
  ```

---

- [ ] **Step 1: Write the failing page test**

Create `frontend/src/pages/ActionCenter.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { ActionCenter } from "./ActionCenter";

const FILTERS = {
  libraries: ["Movies", "Shows"],
  kinds: ["movie", "show"],
  statuses: ["rendered", "no_art"],
};

const SUMMARY = {
  total: 2,
  flags: [
    {
      code: "missing",
      label: "No art found",
      description: "No provider had artwork of this kind for this item.",
      default_on: true,
      instant: true,
      count: 1,
    },
    {
      code: "language_miss",
      label: "Not the preferred language",
      description: "The language the ladder achieved is not this art kind's first choice.",
      default_on: true,
      instant: false,
      count: 1,
    },
    {
      code: "unknown_provenance",
      label: "Adopted, provenance unknown",
      description: "Artwork that was already on disk when this service took over.",
      default_on: false,
      instant: true,
      count: 9,
    },
  ],
};

const ROWS = {
  total: 2,
  limit: 50,
  offset: 0,
  items: [
    {
      item_id: 7,
      art_kind: "poster",
      title: "Dune: Part Two",
      library: "Movies",
      kind: "movie",
      status: "no_art",
      upload_status: "pending",
      provider: null,
      flags: ["missing"],
      details: ["no poster art on any provider"],
      dismissed: false,
      evidence: "a".repeat(64),
      quality_scored_at: null,
      updated_at: "2026-01-02T03:04:05Z",
    },
    {
      item_id: 8,
      art_kind: "background",
      title: "Heat",
      library: "Movies",
      kind: "movie",
      status: "rendered",
      upload_status: "uploaded",
      provider: "TVDB",
      flags: ["language_miss"],
      details: ["selected en; rank 1 in the order that rendered it"],
      dismissed: false,
      evidence: "b".repeat(64),
      quality_scored_at: "2026-01-02T03:04:05Z",
      updated_at: "2026-01-02T03:04:05Z",
    },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Answers whatever the page asks for, and throws on anything it should not
 * ask for -- an unexpected path is a defect, not an empty table. */
function stubFetch(overrides: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (path: string) => {
    if (path === "/api/items/filters") return json(FILTERS);
    if (path.startsWith("/api/actions/summary")) return json(overrides.summary ?? SUMMARY);
    if (path.startsWith("/api/actions/bulk/rerender")) {
      return json(overrides.bulk ?? { status: "dry run", matched: 2, items: 2, enqueued: 0, detail: "2 flagged row(s) across 2 item(s) in this batch. Nothing was queued." });
    }
    if (path.startsWith("/api/actions/rerender")) {
      return json(overrides.rerender ?? { queued: true, job_id: 12 });
    }
    if (path.startsWith("/api/actions/dismiss")) {
      return json({ dismissed: true, evidence: "a".repeat(64) });
    }
    if (path.startsWith("/api/actions/undismiss")) return json({ dismissed: false });
    if (path.startsWith("/api/actions")) return json(overrides.rows ?? ROWS);
    throw new Error(`the page requested an unexpected path: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ActionCenter />
    </MemoryRouter>,
  );
}

/** Every path this page fetched, in order. */
function paths(fetchMock: ReturnType<typeof stubFetch>): string[] {
  return fetchMock.mock.calls.map((call) => call[0] as string);
}

beforeEach(() => {
  setToken(null);
});

describe("ActionCenter", () => {
  it("lists a flagged asset with its flag and the fact behind it", async () => {
    stubFetch();

    renderPage();

    expect(await screen.findByText("Dune: Part Two")).toBeInTheDocument();
    expect(screen.getByText("no poster art on any provider")).toBeInTheDocument();
    expect(
      screen.getByText("selected en; rank 1 in the order that rendered it"),
    ).toBeInTheDocument();
  });

  it("renders one count chip per flag the server knows about", async () => {
    stubFetch();

    renderPage();

    const missing = await screen.findByRole("button", { name: /No art found/ });
    expect(within(missing).getByText("1")).toBeInTheDocument();
    // Off by default and still offered: the operator opts into the adopted
    // population rather than being handed it.
    expect(
      within(screen.getByRole("button", { name: /Adopted, provenance unknown/ })).getByText("9"),
    ).toBeInTheDocument();
  });

  it("says which flags cannot fire until a row re-renders", async () => {
    // The honest limitation, on the page and not only in the PR body: four
    // flags need bookkeeping that only a re-render writes.
    stubFetch();

    renderPage();

    const lazy = await screen.findByRole("button", { name: /Not the preferred language/ });
    expect(lazy).toHaveAttribute("title", expect.stringContaining("re-render"));
  });

  it("filters the queue by the chip that was clicked", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getByRole("button", { name: /No art found/ }));

    await waitFor(() =>
      expect(paths(fetchMock).some((path) => path.includes("flag=missing"))).toBe(true),
    );
  });

  it("resets the offset when a filter changes", async () => {
    // A search narrowing the queue to two rows, read at offset 50, answers
    // with an empty list rather than an error -- which reads as "nothing is
    // flagged" when the truth is the opposite.
    // `total: 60` (rather than ROWS' own 2) so Next is genuinely enabled --
    // the pager disables it once `offset + rows.length >= total`, and with
    // only two rows total that would be true at offset 0 already.
    const fetchMock = stubFetch({ rows: { ...ROWS, total: 60 } });
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(paths(fetchMock).some((path) => path.includes("offset=50"))).toBe(true),
    );

    fireEvent.change(screen.getByLabelText("Library"), { target: { value: "Shows" } });

    await waitFor(() => {
      const last = paths(fetchMock).filter((path) => path.startsWith("/api/actions?")).at(-1);
      expect(last).toContain("library=Shows");
      expect(last).toContain("offset=0");
    });
  });

  it("shows the pager range against the server's own total", async () => {
    stubFetch();

    renderPage();

    expect(await screen.findByText("1–2 of 2")).toBeInTheDocument();
  });

  it("re-searches one row and re-reads the queue from the server", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getAllByRole("button", { name: "Re-search" })[0]);

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before + 1));
    const after = paths(fetchMock).slice(before);
    expect(after[0]).toBe("/api/actions/rerender");
    expect(fetchMock.mock.calls[before][1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[before][1].body)).toEqual({ item_id: 7 });
    // Re-read rather than mutated locally: the server is the authority on
    // what is still flagged, and a re-search may legitimately change nothing.
    expect(after.some((path) => path.startsWith("/api/actions?"))).toBe(true);
  });

  it("reports a de-duplicated re-search without pretending it queued", async () => {
    const fetchMock = stubFetch({ rerender: { queued: false, job_id: null } });
    renderPage();
    await screen.findByText("Dune: Part Two");

    fireEvent.click(screen.getAllByRole("button", { name: "Re-search" })[0]);

    expect(await screen.findByText(/already queued/i)).toBeInTheDocument();
    expect(paths(fetchMock).filter((path) => path === "/api/actions/rerender")).toHaveLength(1);
  });

  it("dismisses a row through the dismiss endpoint and re-reads", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before + 1));
    expect(paths(fetchMock)[before]).toBe("/api/actions/dismiss");
    expect(JSON.parse(fetchMock.mock.calls[before][1].body)).toEqual({
      item_id: 7,
      art_kind: "poster",
      flag: "missing",
    });
  });

  it("restores a dismissed row through the undismiss endpoint", async () => {
    const dismissed = {
      ...ROWS,
      total: 1,
      items: [{ ...ROWS.items[0], dismissed: true }],
    };
    const fetchMock = stubFetch({ rows: dismissed });
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));

    await waitFor(() => expect(paths(fetchMock)[before]).toBe("/api/actions/undismiss"));
    expect(JSON.parse(fetchMock.mock.calls[before][1].body)).toEqual({
      item_id: 7,
      art_kind: "poster",
    });
  });

  it("links each row to the item page, where the picker lives", async () => {
    // Replace is a deep link, not an inline picker: the picker is row 73's
    // and lives on the item page. A second copy of it here would be a second
    // thing to keep in step.
    stubFetch();

    renderPage();

    expect(await screen.findByRole("link", { name: "Dune: Part Two" })).toHaveAttribute(
      "href",
      "/items/7",
    );
  });

  it("counts a bulk re-search without queuing anything", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Count what this would queue" }));

    expect(
      await screen.findByText(/Nothing was queued/),
    ).toBeInTheDocument();
    expect(JSON.parse(fetchMock.mock.calls[before][1].body).apply).toBe(false);
  });

  it("arms the bulk apply without posting anything", async () => {
    // The mutation proof: arming is a state change and nothing else. A button
    // that armed AND posted would pass every other test in this file.
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));

    expect(await screen.findByRole("button", { name: "Yes, queue them" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(before);
  });

  it("queues the batch once the arm is confirmed, then re-reads", async () => {
    const fetchMock = stubFetch({
      bulk: {
        status: "enqueued",
        matched: 2,
        items: 2,
        enqueued: 2,
        detail: "queued 2 of 2 item(s) carrying 2 flagged row(s)",
      },
    });
    renderPage();
    await screen.findByText("Dune: Part Two");
    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));
    const before = fetchMock.mock.calls.length;

    fireEvent.click(await screen.findByRole("button", { name: "Yes, queue them" }));

    await waitFor(() => expect(screen.getByText(/queued 2 of 2/)).toBeInTheDocument());
    expect(JSON.parse(fetchMock.mock.calls[before][1].body).apply).toBe(true);
    expect(paths(fetchMock).slice(before).some((path) => path.startsWith("/api/actions?"))).toBe(
      true,
    );
  });

  it("disarms the bulk apply when a filter changes", async () => {
    // The grant was for the request the filters described. Changing them
    // changes the request, so the grant does not survive it.
    stubFetch();
    renderPage();
    await screen.findByText("Dune: Part Two");
    fireEvent.click(screen.getByRole("button", { name: "Re-search everything matching" }));
    await screen.findByRole("button", { name: "Yes, queue them" });

    fireEvent.change(screen.getByLabelText("Library"), { target: { value: "Shows" } });

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Yes, queue them" })).not.toBeInTheDocument(),
    );
  });

  it("renders an empty state when nothing is flagged", async () => {
    stubFetch({ rows: { total: 0, limit: 50, offset: 0, items: [] } });

    renderPage();

    expect(await screen.findByText(/Nothing needs attention/)).toBeInTheDocument();
  });

  it("reports a failed load rather than rendering an empty queue", async () => {
    const fetchMock = vi.fn(async (path: string) => {
      if (path === "/api/items/filters") return json(FILTERS);
      if (path.startsWith("/api/actions/summary")) return json(SUMMARY);
      return json({ detail: "the database is unreachable" }, 503);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByText("the database is unreachable")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing needs attention/)).not.toBeInTheDocument();
  });

  it("keeps the table scrollable and the row actions grouped", async () => {
    // jsdom computes no layout, so what is assertable is that the hooks the
    // shared CSS hangs off are on the right nodes. The widths themselves were
    // checked in a browser, exactly as the Failures sweep did.
    stubFetch();

    renderPage();
    const title = await screen.findByText("Dune: Part Two");

    expect(title.closest("table")?.parentElement).toHaveClass("table-scroll");
    const actions = screen.getAllByRole("button", { name: "Re-search" })[0].parentElement;
    expect(actions).toHaveClass("row-actions");
  });
});
```

- [ ] **Step 2: Run the page test to verify it fails**

```bash
cd frontend && npm run test -- --run src/pages/ActionCenter.test.tsx
```

Expected: `Failed to resolve import "./ActionCenter"` — the page does not exist yet.

- [ ] **Step 3: Append the response types**

Append to the **end** of `frontend/src/api/types.ts`:

```ts
/** `GET /api/actions/summary` -- one entry per flag the server has, with its
 * count under the current library/art-kind scope.
 *
 * The label and description come from the server's own registry
 * (src/autoposter/actions/flags.py), not from a list here: a flag added there
 * would silently be missing from a chip row this file enumerated.
 *
 * `default_on` false means the flag is offered but is not part of the queue's
 * default population -- `unknown_provenance` is every row of a
 * wholesale-adopted library, and a queue that opens showing ten thousand rows
 * is not a queue. `instant` false means the flag cannot fire until a row
 * re-renders, because the fact it rests on is written at the render
 * write-back; the page says so on the chip.
 */
export interface ActionFlagSummary {
  code: string;
  label: string;
  description: string;
  default_on: boolean;
  instant: boolean;
  count: number;
}

export interface ActionsSummaryResponse {
  /** How many rows the default population holds -- not the sum of the counts
   * above, which double-count a row carrying two flags. */
  total: number;
  flags: ActionFlagSummary[];
}

/** One row of `GET /api/actions`: one chosen artwork asset, which is one
 * `renders` row.
 *
 * `flags` and `details` are parallel arrays in the same order -- the server
 * evaluates each flag's predicate and hands back the factual sentence behind
 * it, so the page renders the server's own words rather than restating a fact
 * it would have to keep in step.
 *
 * `evidence` is the SHA-256 the dismissal is keyed by. The page does not
 * compute or compare it; it is here because it is what makes a dismissal stop
 * holding when a fact moves, and a debugging operator should be able to see
 * it.
 */
export interface ActionRow {
  item_id: number;
  art_kind: string;
  title: string;
  library: string;
  kind: string;
  status: string;
  upload_status: string;
  provider: string | null;
  flags: string[];
  details: string[];
  dismissed: boolean;
  evidence: string;
  /** null means this row predates the quality taxonomy, so four of the flags
   * cannot be evaluated for it until it re-renders. */
  quality_scored_at: string | null;
  updated_at: string;
}

export interface ActionsResponse {
  total: number;
  limit: number;
  offset: number;
  items: ActionRow[];
}

/** `POST /api/actions/bulk/rerender` -- one batch's outcome, or a count of
 * what one would be.
 *
 * All three statuses arrive as a 200 with real numbers: "dry run" changed
 * nothing on purpose, and "complete" means nothing matches the filter. Only a
 * non-2xx is an error. `matched` counts flagged ROWS, `items` counts the
 * distinct items in this batch, and `enqueued` counts jobs actually created --
 * a batch the pending dedupe swallowed reports 0 rather than claiming work it
 * did not queue.
 */
export interface BulkRerenderResponse {
  status: "dry run" | "enqueued" | "complete";
  matched: number;
  items: number;
  enqueued: number;
  detail: string;
}
```

- [ ] **Step 4: Write the page**

Create `frontend/src/pages/ActionCenter.tsx`:

```tsx
/** The Action Center: every artwork asset this service chose that is imperfect.
 *
 * Roadmap rows 11a/11b. One row is one chosen asset -- one `renders` row --
 * and every reason it is here is computed by the server from the config live
 * at that moment. Nothing on this page decides what counts as imperfect, and
 * nothing here restates a fact: the flags, their labels, and the factual
 * sentence behind each one all arrive from the server's registry, so a flag
 * added there appears here without an edit.
 *
 * Three conventions shape the rest, borrowed from the pages that established
 * them:
 *
 *  - **The server's numbers, never a restatement.** The chips, the pager range
 *    and the bulk result all render counts the server sent. A refusal or a
 *    de-duplicated queue is an answer with its numbers, not an error (Modes).
 *  - **Dry run is the offer, apply is the exception.** Counting what a bulk
 *    re-search would queue is one unguarded click, because it queues nothing.
 *    Applying is a two-step gate, and a filter change withdraws the grant --
 *    the grant was for the request those filters described (Modes).
 *  - **Re-read after every action.** An optimistic removal would show a row
 *    gone when the server had not moved it, and a re-search can legitimately
 *    change nothing at all (Failures).
 */
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";

import { apiFetch } from "../api/client";
import type {
  ActionRow,
  ActionsResponse,
  ActionsSummaryResponse,
  BulkRerenderResponse,
  ItemFiltersResponse,
} from "../api/types";
import { formatTime } from "../format";
// The pill and the row-error paragraph are dashboard.css's, exactly as
// Modes.tsx and Library.tsx borrow them. Imported explicitly rather than
// relied on: which stylesheets are in the bundle depends on which pages the
// router has loaded, and this page is reachable without the Dashboard.
import "./dashboard.css";
import "./action-center.css";

/** One screen of rows. The endpoint caps `limit` at 200
 * (MAX_ACTIONS_LIMIT in src/autoposter/api/action_center.py). */
const PAGE_SIZE = 50;

/** Mirrors `flags.ART_KINDS` in src/autoposter/actions/flags.py. Hardcoded
 * rather than fetched: /api/items/filters reports render *statuses*, not art
 * kinds, and the four kinds are a fixed part of the schema, not library data. */
const ART_KINDS = ["poster", "season_poster", "background", "title_card"];

/** A stable identity for one row: the queue's unit is (item, art kind), which
 * is exactly what the renders table keys uniquely. */
function rowKey(row: ActionRow): string {
  return `${row.item_id}:${row.art_kind}`;
}

export function ActionCenter() {
  const [filters, setFilters] = useState<ItemFiltersResponse | null>(null);
  /** Held apart from `error` for the Library page's reason: the dropdowns
   * being unavailable is not the queue's failure, and neither should erase
   * the other. */
  const [filtersError, setFiltersError] = useState<string | null>(null);

  const [flag, setFlag] = useState("");
  const [library, setLibrary] = useState("");
  const [artKind, setArtKind] = useState("");
  const [includeDismissed, setIncludeDismissed] = useState(false);
  const [offset, setOffset] = useState(0);

  const [summary, setSummary] = useState<ActionsSummaryResponse | null>(null);
  const [page, setPage] = useState<ActionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const [armed, setArmed] = useState(false);
  const [bulk, setBulk] = useState<BulkRerenderResponse | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  // `load` is awaited from an effect and again from every click handler, so a
  // response can land after the page has gone. A ref rather than a per-effect
  // local, because the same guard has to cover both callers -- the pattern
  // Failures.tsx established.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiFetch<ItemFiltersResponse>("/api/items/filters")
      .then((response) => {
        if (!cancelled) setFilters(response);
      })
      .catch((caught: Error) => {
        if (!cancelled) setFiltersError(caught.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** The scope both requests share: everything except the chip and the page. */
  const scopeQuery = useCallback(() => {
    const query = new URLSearchParams();
    // Only when non-empty: `library=` with nothing after it is still a filter
    // the endpoint would evaluate, and it matches a library called "".
    if (library !== "") query.set("library", library);
    if (artKind !== "") query.set("art_kind", artKind);
    if (includeDismissed) query.set("include_dismissed", "true");
    return query;
  }, [library, artKind, includeDismissed]);

  const load = useCallback(async () => {
    try {
      const summaryResponse = await apiFetch<ActionsSummaryResponse>(
        `/api/actions/summary?${scopeQuery().toString()}`,
      );
      if (!live.current) return;
      setSummary(summaryResponse);

      const query = scopeQuery();
      query.set("limit", String(PAGE_SIZE));
      query.set("offset", String(offset));
      if (flag !== "") query.set("flag", flag);
      const rows = await apiFetch<ActionsResponse>(`/api/actions?${query.toString()}`);
      if (!live.current) return;
      setPage(rows);
      setError(null);
    } catch (caught) {
      if (!live.current) return;
      setError((caught as Error).message);
    }
  }, [scopeQuery, offset, flag]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Every filter change resets the page and withdraws the bulk grant.
   *
   * The offset, because landing on page 9 of a filtered result that has one
   * is answered with an empty list rather than an error -- which reads as
   * "nothing is flagged" when the truth is the opposite. The grant, because
   * it was given for the request those filters described. */
  function refocus(apply: () => void) {
    apply();
    setOffset(0);
    setArmed(false);
    setBulk(null);
    setNotice(null);
  }

  function chooseFilter(set: (value: string) => void) {
    return (event: ChangeEvent<HTMLSelectElement>) => refocus(() => set(event.target.value));
  }

  async function act(row: ActionRow, run: () => Promise<string | null>) {
    setBusyKey(rowKey(row));
    setError(null);
    setNotice(null);
    try {
      const message = await run();
      if (live.current && message !== null) setNotice(message);
      // Re-read rather than removing the row locally. A re-search may
      // legitimately find the same art and leave the row exactly where it
      // was, and the server is the authority on what is still flagged.
      await load();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBusyKey(null);
    }
  }

  function reSearch(row: ActionRow) {
    return act(row, async () => {
      const response = await apiFetch<{ queued: boolean; job_id: number | null }>(
        "/api/actions/rerender",
        { method: "POST", body: JSON.stringify({ item_id: row.item_id }) },
      );
      return response.queued
        ? `${row.title}: queued.`
        : `${row.title}: a pass for this item is already queued; nothing was added.`;
    });
  }

  function dismiss(row: ActionRow) {
    return act(row, async () => {
      await apiFetch("/api/actions/dismiss", {
        method: "POST",
        body: JSON.stringify({
          item_id: row.item_id,
          art_kind: row.art_kind,
          flag: row.flags[0] ?? null,
        }),
      });
      return null;
    });
  }

  function restore(row: ActionRow) {
    return act(row, async () => {
      await apiFetch("/api/actions/undismiss", {
        method: "POST",
        body: JSON.stringify({ item_id: row.item_id, art_kind: row.art_kind }),
      });
      return null;
    });
  }

  async function runBulk(apply: boolean) {
    setArmed(false);
    setBulkBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { apply };
      if (flag !== "") body.flag = flag;
      if (library !== "") body.library = library;
      if (artKind !== "") body.art_kind = artKind;
      if (includeDismissed) body.include_dismissed = true;
      const response = await apiFetch<BulkRerenderResponse>("/api/actions/bulk/rerender", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (live.current) setBulk(response);
      if (apply) await load();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBulkBusy(false);
    }
  }

  const rows: ActionRow[] = page?.items ?? [];
  const total = page?.total ?? 0;
  const labels = new Map((summary?.flags ?? []).map((entry) => [entry.code, entry.label]));

  return (
    <>
      <div className="page-header">
        <h1>Action Center</h1>
        {summary !== null && (
          <span className="muted">
            {summary.total} asset{summary.total === 1 ? "" : "s"} need attention
          </span>
        )}
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      <div className="action-chips">
        {(summary?.flags ?? []).map((entry) => (
          <button
            type="button"
            key={entry.code}
            className={
              flag === entry.code
                ? "action-chip active"
                : entry.count === 0
                  ? "action-chip action-chip-zero"
                  : "action-chip"
            }
            aria-pressed={flag === entry.code}
            title={
              entry.instant
                ? entry.description
                : `${entry.description} This flag cannot fire until the row re-renders.`
            }
            onClick={() => refocus(() => setFlag(flag === entry.code ? "" : entry.code))}
          >
            <span className="action-chip-label">{entry.label}</span>
            <span className="action-chip-count">{entry.count}</span>
          </button>
        ))}
      </div>

      <div className="action-filters">
        <label>
          Library
          <select value={library} onChange={chooseFilter(setLibrary)}>
            <option value="">All libraries</option>
            {filters?.libraries.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Art kind
          <select value={artKind} onChange={chooseFilter(setArtKind)}>
            <option value="">All art kinds</option>
            {ART_KINDS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="action-toggle">
          <input
            type="checkbox"
            checked={includeDismissed}
            onChange={(event) => refocus(() => setIncludeDismissed(event.target.checked))}
          />
          Show dismissed
        </label>
        {filtersError !== null && (
          <span className="page-error">Filters are unavailable: {filtersError}</span>
        )}
      </div>

      <div className="panel action-bulk">
        <div className="row-actions">
          <button type="button" disabled={bulkBusy} onClick={() => void runBulk(false)}>
            Count what this would queue
          </button>
          {armed ? (
            <>
              {/* `alert`, so a screen reader is told the question appeared:
                  the button label alone does not carry what is about to
                  happen. */}
              <span className="action-confirm" role="alert">
                Queue another pass for every item matching these filters?
              </span>
              <button
                type="button"
                className="primary"
                disabled={bulkBusy}
                autoFocus
                onClick={() => void runBulk(true)}
              >
                Yes, queue them
              </button>
              <button type="button" onClick={() => setArmed(false)}>
                Cancel
              </button>
            </>
          ) : (
            /* Arms the gate. It must never post -- see the mutation proof in
               ActionCenter.test.tsx. */
            <button type="button" disabled={bulkBusy} onClick={() => setArmed(true)}>
              Re-search everything matching
            </button>
          )}
        </div>
        <p className="muted action-caveat">
          A re-search runs provider selection again for the whole item, against the
          24-hour provider cache. It may legitimately find the same artwork and leave
          the row flagged.
        </p>
        {bulk !== null && (
          /* `status` rather than `alert`: this is the outcome of something the
             operator asked for, including "nothing matched", and it must not
             be announced as an error. */
          <div className="action-bulk-result" role="status">
            <span className={`pill ${bulk.enqueued > 0 ? "pill-ok" : "pill-skipped"}`}>
              {bulk.status}
            </span>
            <span>{bulk.detail}</span>
          </div>
        )}
      </div>

      {notice !== null && <p className="muted action-notice">{notice}</p>}

      <div className="panel">
        {page === null ? (
          error === null ? (
            <p className="muted">Loading…</p>
          ) : null
        ) : rows.length === 0 ? (
          <p className="empty">Nothing needs attention under these filters.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Library</th>
                  <th>Art kind</th>
                  <th>Why</th>
                  <th>Scored</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={rowKey(row)}>
                    <td>
                      <Link to={`/items/${row.item_id}`}>{row.title}</Link>
                    </td>
                    <td className="muted">{row.library}</td>
                    <td className="mono">{row.art_kind}</td>
                    <td className="cell-wrap">
                      {row.flags.map((code, index) => (
                        <div className="action-reason" key={code}>
                          <span className="action-flag">{labels.get(code) ?? code}</span>
                          <span className="action-detail">{row.details[index]}</span>
                        </div>
                      ))}
                    </td>
                    <td className="muted cell-time">
                      {row.quality_scored_at === null
                        ? "not yet"
                        : formatTime(row.quality_scored_at)}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          type="button"
                          disabled={busyKey === rowKey(row)}
                          onClick={() => void reSearch(row)}
                        >
                          Re-search
                        </button>
                        {/* The picker is row 73's and lives on the item page.
                            A second copy of it inside the queue would be a
                            second thing to keep in step. */}
                        <Link className="action-replace" to={`/items/${row.item_id}`}>
                          Replace
                        </Link>
                        {row.dismissed ? (
                          <button
                            type="button"
                            disabled={busyKey === rowKey(row)}
                            onClick={() => void restore(row)}
                          >
                            Restore
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busyKey === rowKey(row)}
                            onClick={() => void dismiss(row)}
                          >
                            Dismiss
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="action-pager">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}
        >
          Previous
        </button>
        <span className="muted">
          {rows.length === 0 ? "Nothing to show" : `${offset + 1}–${offset + rows.length} of ${total}`}
        </span>
        <button
          type="button"
          disabled={offset + rows.length >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </button>
      </div>
    </>
  );
}
```

- [ ] **Step 5: Write the stylesheet**

Create `frontend/src/pages/action-center.css`:

```css
/* The Action Center. The shared page primitives (.page-header, .page-error,
 * .empty, .table-scroll, .cell-time, .cell-wrap, .row-actions) come from
 * shell/shell.css, .panel/.muted/.mono from theme.css, and .pill* from
 * dashboard.css -- imported explicitly by the page, as Modes.tsx does. The
 * pager is `.action-pager` rather than a second import of `.library-pager`:
 * which stylesheets are in the bundle depends on which pages the router has
 * loaded (see the import comment above `ActionCenter.tsx`'s own imports),
 * and this page is reachable without the Dashboard or Library ever loading
 * `library.css`. The rule below is copied from `.library-pager` rather than
 * imported, so the two would need to drift on purpose, not by accident of
 * navigation order. */

.action-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 24px;
}

.action-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

/* A chip is a filter toggle that also carries its own count, so the header
 * row answers "what is wrong" and "how much of it" at once. */
.action-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: inherit;
  font-size: 13px;
  color: var(--text);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 6px 10px;
  cursor: pointer;
}

.action-chip.active {
  border-color: var(--accent);
  color: var(--accent);
}

/* A flag with no matches stays on screen rather than disappearing: a chip
 * that vanishes when its count reaches zero takes with it the only evidence
 * that the count IS zero, and the operator cannot tell "none" from "not
 * offered". */
.action-chip-zero {
  opacity: 0.55;
}

.action-chip-count {
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}

.action-chip.active .action-chip-count {
  color: var(--accent);
}

.action-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}

.action-filters label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.action-filters select {
  font-family: inherit;
  font-size: 14px;
  text-transform: none;
  letter-spacing: normal;
  color: var(--text);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 7px 10px;
}

.action-filters select:focus {
  outline: none;
  border-color: var(--accent);
}

.action-toggle {
  text-transform: none;
  letter-spacing: normal;
  font-size: 13px;
}

.action-bulk {
  margin-bottom: 20px;
}

/* The sentence beside the confirm button, and the standing caveat under the
 * bar. Both are always rendered rather than put behind a disclosure: an
 * operator who has to open something to learn that a re-search may change
 * nothing will learn it afterwards instead. */
.action-confirm {
  color: var(--warn);
  font-size: 13px;
}

.action-caveat {
  margin: 10px 0 0;
  font-size: 12px;
}

.action-bulk-result {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  font-size: 13px;
}

.action-notice {
  margin: 0 0 12px;
  font-size: 13px;
}

/* One flag and the fact behind it, stacked. A row can carry several, and
 * putting the label and its evidence on one line made the reason column the
 * widest thing on the page at every viewport. */
.action-reason {
  display: block;
  margin-bottom: 6px;
}

.action-reason:last-child {
  margin-bottom: 0;
}

.action-flag {
  display: block;
  color: var(--text-strong);
  font-size: 13px;
}

.action-detail {
  display: block;
  color: var(--text-muted);
  font-size: 12px;
}

/* Replace is a link, not a button, because it navigates. Styled to sit level
 * with the buttons beside it inside .row-actions rather than reading as body
 * text that happens to be blue. */
.action-replace {
  font-size: 13px;
}
```

- [ ] **Step 6: Route the page and put a link into it**

In `frontend/src/App.tsx`, add the import alphabetically (it sorts before `Collections`):

```tsx
import { SessionProvider, useSession } from "./auth/SessionContext";
import { Collections } from "./pages/Collections";
```

becomes

```tsx
import { SessionProvider, useSession } from "./auth/SessionContext";
import { ActionCenter } from "./pages/ActionCenter";
import { Collections } from "./pages/Collections";
```

and add the route immediately after the Dashboard's, matching the nav order:

```tsx
          <Route path="/" element={<Dashboard />} />
          <Route path="/library" element={<Library />} />
```

becomes

```tsx
          <Route path="/" element={<Dashboard />} />
          <Route path="/actions" element={<ActionCenter />} />
          <Route path="/library" element={<Library />} />
```

In `frontend/src/shell/Sidebar.tsx`, add the icon to `ICONS` (a checklist-with-a-tick, matching the flat single-path style of the rest):

```tsx
  dashboard: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
```

becomes

```tsx
  actions:
    "M19 3h-4.18C14.4 1.84 13.3 1 12 1s-2.4.84-2.82 2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm-7 0a1 1 0 1 1 0 2 1 1 0 0 1 0-2zm-1.2 14L7 13.2l1.4-1.4 2.4 2.4 5.8-5.8L18 9.8 10.8 17z",
  dashboard: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
```

and add the `NAV` entry immediately after Dashboard, with its position comment in the house voice:

```tsx
const NAV = [
  { to: "/", label: "Dashboard", icon: "dashboard", end: true },
  { to: "/library", label: "Library", icon: "library", end: false },
```

becomes

```tsx
const NAV = [
  { to: "/", label: "Dashboard", icon: "dashboard", end: true },
  // Directly after Dashboard: "what needs me" is the question an operator
  // asks immediately after "what is happening", and before browsing anything.
  // Distinct from Failures below, which is where hard failures live -- this
  // is the other surface, for artwork that succeeded and is suboptimal.
  { to: "/actions", label: "Action Center", icon: "actions", end: false },
  { to: "/library", label: "Library", icon: "library", end: false },
```

- [ ] **Step 7: Pin the sidebar link**

In `frontend/src/shell/Sidebar.test.tsx`, add this test beside the other per-page link assertions (immediately before `it("reaches the id-mismatch view", …)`):

```tsx
  it("reaches the Action Center", () => {
    stubMatchMedia(false);

    renderSidebar();

    // The queue is only reachable from here; a route with no link into it is
    // a page that ships and is never found.
    const link = screen.getByRole("link", { name: "Action Center" });
    expect(link).toHaveAttribute("href", "/actions");
    expect(link).toHaveAttribute("title", "Action Center");
  });
```

- [ ] **Step 8: Run the frontend suite and the type check**

```bash
cd frontend && npm run test -- --run
```

Expected: all pass, including the 18 new `ActionCenter` tests and the added sidebar assertion.

```bash
cd frontend && npx tsc --noEmit
```

Expected: no output.

- [ ] **Step 9: Run the backend suite too**

Nothing in this task touched Python, but `tests/test_attribution_present.py` reads `frontend/dist` and other repo-reading suites exist; prove the tree is still green before committing.

```bash
docker compose -p pac3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac3-full test sh -c \
  "set -o pipefail; ruff check . && pytest -q 2>&1 | tee /app/.superpowers/run-pac3-full.log"
```

Expected: the same total as Task 2's, unchanged.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/pages/ActionCenter.tsx frontend/src/pages/ActionCenter.test.tsx \
        frontend/src/pages/action-center.css frontend/src/api/types.ts \
        frontend/src/App.tsx frontend/src/shell/Sidebar.tsx frontend/src/shell/Sidebar.test.tsx
git commit -m "feat(ui): the Action Center page

Count chips, filters, the queue, per-row re-search / replace / dismiss,
and a two-step bulk arm. Nothing here decides what counts as imperfect
and nothing restates a fact: the flags, their labels and the factual
sentence behind each one all arrive from the server's registry, so a flag
added there appears here with no edit.

Replace deep-links to the item page rather than embedding the picker: the
picker is row 73's, and a second copy of it in the queue would be a
second thing to keep in step.

The bulk gate follows the run-modes convention -- counting is one
unguarded click because it queues nothing, applying is armed, and a
filter change withdraws the grant, since the grant was for the request
those filters described. The mutation proof that arming never posts is
pinned.

The sidebar link is pinned in the same commit: a route with no link into
it is a page that ships and is never found."
```

- [ ] **Step 11: Tear down**

```bash
docker compose -p pac3 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

### Task 4: The batched quality backfill

**Files:**
- Modify: `src/autoposter/api/action_center.py` (two handlers and one helper appended)
- Modify: `tests/test_api_action_center.py` (a new section of tests appended)
- Modify: `frontend/src/api/types.ts` (two interfaces appended)
- Modify: `frontend/src/pages/ActionCenter.tsx` (the progress panel and its effect)
- Modify: `frontend/src/pages/action-center.css` (the panel's classes)
- Modify: `frontend/src/pages/ActionCenter.test.tsx` (the stub gains the new path; three new tests)

**Interfaces:**

- **Consumes:** everything Tasks 1–3 produced — `Render.quality_scored_at`, `_flag_predicate`, `_dismissal_join`, `_enqueue_reprocess`, `apiFetch`, the page's `live` ref and `load()`.
- **Produces:**

  ```python
  # autoposter/api/action_center.py
  async def _backfill_progress(session) -> tuple[int, int]   # (done, total)
  GET  /api/actions/backfill  -> {"status": "not_started"|"in_progress"|"complete",
                                  "done": int, "total": int}
  POST /api/actions/backfill  -> {"status": "enqueued"|"complete", "selected": int,
                                  "enqueued": int, "done": int, "total": int,
                                  "detail": str}
  ```

  ```ts
  // frontend/src/api/types.ts
  export interface QualityBackfillStatus {
    status: "not_started" | "in_progress" | "complete"; done: number; total: number;
  }
  export interface QualityBackfillTrigger {
    status: "enqueued" | "complete";
    selected: number; enqueued: number; done: number; total: number; detail: string;
  }
  ```

---

- [ ] **Step 1: Write the failing backfill tests**

Append to `tests/test_api_action_center.py`:

```python
# --- the quality backfill ----------------------------------------------------
#
# 11a's "backfill job scoring the existing library". It cannot invent a
# language: no column holds one on a pre-existing row, so the achieved rank of
# an already-rendered asset is genuinely unrecoverable without re-selecting.
# So the backfill re-selects, in operator-paced batches -- which is both the
# backfill and a demonstration of the bulk path.


async def test_the_backfill_status_reports_complete_on_an_empty_library(client, auth_headers):
    """Completion is derived first, and by the POST's own rule -- `done >=
    total` <=> nothing unscored is left <=> the trigger selects 0. Testing
    "has it started" first would let an empty library read `not_started`
    forever here while every POST answered `complete`: two endpoints
    disagreeing about the one state a disabled button keys off."""
    body = (await client.get("/api/actions/backfill", headers=auth_headers)).json()

    assert body == {"status": "complete", "done": 0, "total": 0}


async def test_the_backfill_status_counts_only_rows_that_can_be_scored(
    client, auth_headers, session
):
    """A no_art row never reaches the write-back that stamps
    quality_scored_at, so counting it in the population would make a backfill
    that can never finish."""
    from datetime import datetime, timezone

    await _seed(session, rating_key="1", status="rendered", quality_scored_at=None)
    await _seed(
        session, rating_key="2", status="rendered",
        quality_scored_at=datetime.now(timezone.utc),
    )
    await _seed(session, rating_key="3", status="no_art")

    body = (await client.get("/api/actions/backfill", headers=auth_headers)).json()

    assert body == {"status": "in_progress", "done": 1, "total": 2}


async def test_the_backfill_clears_the_fingerprint_of_every_row_it_enqueues(
    client, auth_headers, session
):
    """The crux, and the reason a plain enqueue is not enough. The pipeline's
    unchanged-fingerprint short-circuit returns ABOVE the write-back, so a
    re-processed row whose inputs have not changed reports "unchanged" and
    scores nothing -- the backfill would run forever and finish never.
    Clearing the fingerprint is what makes the next pass a real render, and it
    is the same move /items/{id}/renders/{kind}/clear-override already makes
    for the same reason."""
    _, render = await _seed(
        session, rating_key="1", status="rendered", quality_scored_at=None,
        fingerprint="deadbeef" * 8,
    )

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["status"] == "enqueued"
    assert body["selected"] == 1
    assert body["enqueued"] == 1
    session.expire_all()
    reread = (await session.execute(select(Render).where(Render.id == render.id))).scalar_one()
    assert reread.fingerprint is None


async def test_the_backfill_leaves_an_already_scored_rows_fingerprint_alone(
    client, auth_headers, session
):
    """The discriminating half: a backfill that cleared every fingerprint
    would pass the test above and force a full re-render of the whole library
    on the next pass."""
    from datetime import datetime, timezone

    _, scored = await _seed(
        session, rating_key="1", status="rendered",
        quality_scored_at=datetime.now(timezone.utc), fingerprint="cafebabe" * 8,
    )
    await _seed(session, rating_key="2", status="rendered", quality_scored_at=None)

    await client.post("/api/actions/backfill", headers=auth_headers)

    session.expire_all()
    reread = (await session.execute(select(Render).where(Render.id == scored.id))).scalar_one()
    assert reread.fingerprint == "cafebabe" * 8


async def test_the_backfill_queues_one_job_per_item_and_records_one_event(
    client, auth_headers, session
):
    item = MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune")
    session.add(item)
    await session.flush()
    session.add_all([
        Render(item_id=item.id, art_kind="poster", status="rendered", asset_path="/a.jpg"),
        Render(item_id=item.id, art_kind="background", status="rendered", asset_path="/b.jpg"),
    ])
    await session.commit()

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["selected"] == 2
    assert body["enqueued"] == 1
    assert len((await session.execute(select(Job))).scalars().all()) == 1
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [event.event_type for event in events] == ["action_center_quality_backfill"]


async def test_the_backfill_honours_the_configured_batch_size(
    app, client, auth_headers, session
):
    """One batch per press, not the whole library: the sweep's own reasoning --
    the worker pool and every provider budget. The operator pressing again is
    the pacing."""
    for n in range(3):
        await _seed(session, rating_key=str(n), status="rendered", fingerprint=f"{n}" * 64)

    edited = load_config(EXAMPLE)
    edited.scheduler.drift_batch_size = 1
    app.state.config_holder.swap(edited)

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["selected"] == 1
    cleared = (
        await session.execute(select(Render).where(Render.fingerprint.is_(None)))
    ).scalars().all()
    assert len(cleared) == 1


async def test_the_backfill_reports_completion_idempotently(client, auth_headers, session):
    """`complete` is an answer, not an error: a 200 with real counts, so the
    button can render it rather than styling it as a failure."""
    from datetime import datetime, timezone

    await _seed(
        session, rating_key="1", status="rendered",
        quality_scored_at=datetime.now(timezone.utc),
    )

    body = (await client.post("/api/actions/backfill", headers=auth_headers)).json()

    assert body["status"] == "complete"
    assert body["selected"] == 0
    assert body["enqueued"] == 0
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_the_backfill_requires_a_session(client):
    assert (await client.get("/api/actions/backfill")).status_code == 401
    assert (await client.post("/api/actions/backfill")).status_code == 401
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p pac4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac4-red test sh -c \
  "set -o pipefail; pytest tests/test_api_action_center.py -q -k backfill 2>&1 | tee /app/.superpowers/run-pac4-red.log"
```

Expected: eight failures, each a 404 from the ASGI app.

- [ ] **Step 3: Write the two handlers**

Append to `src/autoposter/api/action_center.py`:

```python
# --- the quality backfill ----------------------------------------------------
#
# Roadmap 11a's "backfill job scoring the existing library", in the
# api/facts_backfill.py shape: one batch per press, an honest `status` always
# returned as a 200, one events_log row per trigger, and a GET that answers
# standing progress so the button renders correctly on page load.
#
# It differs from that precedent in two ways, both deliberate.
#
# It keeps NO cursor row. The facts backfill needs one because it stamps
# `fetched_at` on everything it walks whether or not the walk filled anything,
# so "already visited" is not derivable from the data. This population is
# self-consuming: a row leaves it exactly when a render stamps
# `quality_scored_at`, so "what is left" is a WHERE clause and a second table
# would be a second source of truth to keep in step.
#
# And it does NOT park while TMDb's 429 window is open. The facts backfill
# parks because a batch gathered with TMDb skipped stamps `fetched_at` anyway
# and silently loses the columns it exists to fill. Nothing here stamps
# anything: `quality_scored_at` is written by the render's own write-back, and
# only when a render actually happened. A batch enqueued into a backoff window
# is simply paced by the workers, which is what D4 says the queue is for.


async def _backfill_progress(session) -> tuple[int, int]:
    """``(done, total)`` over the rows this backfill can actually score.

    Scoped to ``status = 'rendered'``. A ``no_art``, ``skipped`` or
    ``truncated`` row returns from ``render_artifact`` long before the
    write-back that stamps ``quality_scored_at``, so counting one here would
    make a population the backfill can never finish -- a progress bar that
    stops at 94% forever. Those rows are already named by their own flags.
    """
    total = (
        await session.execute(
            select(func.count()).select_from(Render).where(Render.status == "rendered")
        )
    ).scalar_one()
    done = (
        await session.execute(
            select(func.count())
            .select_from(Render)
            .where(Render.status == "rendered", Render.quality_scored_at.isnot(None))
        )
    ).scalar_one()
    return done, total


@router.get("/actions/backfill")
async def backfill_status(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """How much of the scorable population has been scored. Reads only."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        done, total = await _backfill_progress(session)

    # Completion is derived FIRST, and by the POST's own rule: `done >= total`
    # <=> no unscored rendered row is left <=> the trigger selects 0. Testing
    # "has it started" first would let an empty library answer `not_started`
    # here forever while every POST answered `complete` -- the two endpoints
    # disagreeing about the one state a disabled button keys off.
    if done >= total:
        status = "complete"
    elif done == 0:
        status = "not_started"
    else:
        status = "in_progress"
    return {"status": status, "done": done, "total": total}


@router.post("/actions/backfill")
async def backfill_trigger(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """One batch: clear, enqueue, report -- or report completion idempotently.

    Clearing the fingerprint is the substance of this endpoint and is not
    optional. An enqueue on its own would not score anything: the pipeline's
    unchanged-fingerprint short-circuit returns ABOVE the write-back, so a
    re-processed row whose inputs have not moved reports "unchanged" and
    stamps nothing, and the backfill would run forever and finish never.
    ``/items/{id}/renders/{kind}/clear-override`` already makes exactly this
    move for exactly this reason.

    That is also the honest cost, and the page says so: this is a real
    re-render of every row it touches -- a provider selection against the 24h
    cache, a composite, a publish and an upload. It is the only way to recover
    an achieved language, which no column on a pre-existing row holds.

    Always a 200 with a ``status`` field. ``complete`` is an expected answer
    to an honest question, not an error for the client to style as a failure.
    """
    from autoposter.api.routes import _enqueue_reprocess

    config = request.app.state.config_holder.current
    batch_size = config.scheduler.drift_batch_size

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        batch = (
            (
                await session.execute(
                    select(Render)
                    .where(Render.status == "rendered", Render.quality_scored_at.is_(None))
                    .order_by(Render.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

        item_ids = []
        for render in batch:
            render.fingerprint = None
            if render.item_id not in item_ids:
                item_ids.append(render.item_id)
        if batch:
            await session.commit()

        items = (
            (
                (await session.execute(select(MediaItem).where(MediaItem.id.in_(item_ids))))
                .scalars()
                .all()
            )
            if item_ids
            else []
        )
        enqueued = 0
        for item in items:
            if await _enqueue_reprocess(session, item) is not None:
                enqueued += 1

        done, total = await _backfill_progress(session)
        if not batch:
            detail = f"complete: all {total} rendered asset(s) have been scored"
        else:
            detail = (
                f"queued {enqueued} item(s) covering {len(batch)} unscored asset(s); "
                f"{done} of {total} scored so far"
            )
        # The ops rule: a write nobody can find afterwards is not an operator
        # action, it is a mystery. One row per press, the completes included --
        # an operator reading the table should see the walk.
        session.add(
            EventLog(
                source="actions",
                event_type="action_center_quality_backfill",
                payload={
                    "selected": len(batch),
                    "items": len(item_ids),
                    "enqueued": enqueued,
                    "done": done,
                    "total": total,
                },
                outcome="action center quality backfill: " + detail,
            )
        )
        await session.commit()

    return {
        "status": "complete" if not batch else "enqueued",
        "selected": len(batch),
        "enqueued": enqueued,
        "done": done,
        "total": total,
        "detail": detail,
    }
```

- [ ] **Step 4: Run the backfill tests and watch them pass**

```bash
docker compose -p pac4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac4-green test sh -c \
  "set -o pipefail; pytest tests/test_api_action_center.py -q 2>&1 | tee /app/.superpowers/run-pac4-green.log"
```

Expected: `32 passed` (Task 2's 24 plus these 8).

- [ ] **Step 5: Append the frontend types**

Append to the **end** of `frontend/src/api/types.ts`:

```ts
/** `GET /api/actions/backfill` -- the quality backfill's standing progress.
 *
 * Measured over rows that CAN be scored (`status = 'rendered'`) rather than
 * over every render row: an asset that produced no art never reaches the
 * write-back that stamps the quality facts, so counting it would make a
 * progress bar that stops short forever. Those rows already have their own
 * flags.
 *
 * `complete` is derived server-side by the POST's own rule (`done >= total`),
 * so an empty library reads `complete` on both endpoints rather than
 * `not_started` here and `complete` there.
 */
export interface QualityBackfillStatus {
  status: "not_started" | "in_progress" | "complete";
  done: number;
  total: number;
}

/** `POST /api/actions/backfill` -- one triggered batch's outcome.
 *
 * `complete` is an answer, not a failure: it arrives as a 200 with real
 * counts. `selected` is how many unscored assets this batch took;
 * `enqueued` is how many items it actually queued, which is smaller whenever
 * one item carries several unscored assets or a pass for it was already
 * pending.
 */
export interface QualityBackfillTrigger {
  status: "enqueued" | "complete";
  selected: number;
  enqueued: number;
  done: number;
  total: number;
  detail: string;
}
```

- [ ] **Step 6: Update the page test's stub and add the three new tests**

In `frontend/src/pages/ActionCenter.test.tsx`, add the backfill path to the dispatcher **above** the `/api/actions` catch-all (the order matters — `/api/actions/backfill` starts with `/api/actions`):

```tsx
    if (path.startsWith("/api/actions/undismiss")) return json({ dismissed: false });
    if (path.startsWith("/api/actions")) return json(overrides.rows ?? ROWS);
```

becomes

```tsx
    if (path.startsWith("/api/actions/undismiss")) return json({ dismissed: false });
    if (path.startsWith("/api/actions/backfill")) {
      return json(overrides.backfill ?? { status: "in_progress", done: 120, total: 300 });
    }
    if (path.startsWith("/api/actions")) return json(overrides.rows ?? ROWS);
```

Then append these tests inside the `describe("ActionCenter", …)` block:

```tsx
  it("shows how much of the library has been scored", async () => {
    stubFetch();

    renderPage();

    expect(await screen.findByText("120 of 300 assets scored")).toBeInTheDocument();
  });

  it("triggers one backfill batch and re-reads the progress", async () => {
    const fetchMock = stubFetch();
    renderPage();
    await screen.findByText("120 of 300 assets scored");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Score the next batch" }));

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before + 1));
    expect(paths(fetchMock)[before]).toBe("/api/actions/backfill");
    expect(fetchMock.mock.calls[before][1].method).toBe("POST");
    // Re-read: the progress is the server's, and the batch it queued has not
    // run yet, so the page must not compute a number of its own.
    expect(paths(fetchMock).slice(before + 1)).toContain("/api/actions/backfill");
  });

  it("offers nothing to press once every asset is scored", async () => {
    stubFetch({ backfill: { status: "complete", done: 300, total: 300 } });

    renderPage();

    expect(
      await screen.findByRole("button", { name: "Score the next batch" }),
    ).toBeDisabled();
  });
```

- [ ] **Step 7: Add the panel to the page**

In `frontend/src/pages/ActionCenter.tsx`, extend the type import:

```tsx
import type {
  ActionRow,
  ActionsResponse,
  ActionsSummaryResponse,
  BulkRerenderResponse,
  ItemFiltersResponse,
} from "../api/types";
```

becomes

```tsx
import type {
  ActionRow,
  ActionsResponse,
  ActionsSummaryResponse,
  BulkRerenderResponse,
  ItemFiltersResponse,
  QualityBackfillStatus,
  QualityBackfillTrigger,
} from "../api/types";
```

Add the state, beside the bulk state:

```tsx
  const [armed, setArmed] = useState(false);
  const [bulk, setBulk] = useState<BulkRerenderResponse | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
```

becomes

```tsx
  const [armed, setArmed] = useState(false);
  const [bulk, setBulk] = useState<BulkRerenderResponse | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const [coverage, setCoverage] = useState<QualityBackfillStatus | null>(null);
  const [coverageDetail, setCoverageDetail] = useState<string | null>(null);
  const [coverageBusy, setCoverageBusy] = useState(false);
```

Add the loader and its effect, immediately **after** the `useEffect(() => { void load(); }, [load]);` block:

```tsx
  /** The coverage panel reads its own endpoint, on its own effect.
   *
   * Not folded into `load()`: coverage does not depend on the filters, so
   * re-reading it on every chip click would be a query per click for a number
   * that cannot have changed. */
  const loadCoverage = useCallback(async () => {
    try {
      const response = await apiFetch<QualityBackfillStatus>("/api/actions/backfill");
      if (live.current) setCoverage(response);
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    }
  }, []);

  useEffect(() => {
    void loadCoverage();
  }, [loadCoverage]);

  async function runBackfill() {
    setCoverageBusy(true);
    setError(null);
    try {
      const response = await apiFetch<QualityBackfillTrigger>("/api/actions/backfill", {
        method: "POST",
      });
      if (live.current) setCoverageDetail(response.detail);
      // Re-read rather than trusting the trigger's own numbers: the batch it
      // queued has not run yet, so the coverage it reported is the coverage
      // BEFORE the work, and the page must not present it as after.
      await loadCoverage();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setCoverageBusy(false);
    }
  }
```

Render the panel immediately **above** the bulk panel (`<div className="panel action-bulk">`):

```tsx
      {coverage !== null && (
        <div className="panel action-coverage">
          <div className="row-actions">
            <span>
              {coverage.done} of {coverage.total} assets scored
            </span>
            <button
              type="button"
              disabled={coverageBusy || coverage.status === "complete"}
              onClick={() => void runBackfill()}
            >
              Score the next batch
            </button>
          </div>
          <p className="muted action-caveat">
            Four of the flags rest on facts written when an asset renders, so an
            asset rendered before this page existed cannot show them yet. Scoring
            a batch queues a genuine re-render for those assets — the only way to
            recover which language the ladder achieved, which no column holds for
            an older row. One batch per press.
          </p>
          {coverageDetail !== null && (
            <p className="muted action-notice" role="status">
              {coverageDetail}
            </p>
          )}
        </div>
      )}
```

- [ ] **Step 8: Add the panel's one class**

Append to `frontend/src/pages/action-center.css`:

```css
/* The coverage panel. Above the bulk bar on purpose: "can this page even see
 * everything yet" is the question that qualifies every count above it, and an
 * operator who reads the chips without it will read an unscored library as a
 * clean one. */
.action-coverage {
  margin-bottom: 20px;
}
```

- [ ] **Step 9: Run both suites and the type check**

```bash
cd frontend && npm run test -- --run && npx tsc --noEmit
```

Expected: all pass, `tsc` silent.

```bash
docker compose -p pac4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac4-full test sh -c \
  "set -o pipefail; ruff check . && pytest -q 2>&1 | tee /app/.superpowers/run-pac4-full.log"
```

Expected: `ruff` clean, and Task 3's total plus **+8**.

- [ ] **Step 10: Commit**

```bash
git add src/autoposter/api/action_center.py tests/test_api_action_center.py \
        frontend/src/api/types.ts frontend/src/pages/ActionCenter.tsx \
        frontend/src/pages/ActionCenter.test.tsx frontend/src/pages/action-center.css
git commit -m "feat(actions): the batched quality backfill, and the coverage it reports

11a's sweep job, in the facts-backfill shape: one batch per press, an
honest status always returned as a 200, one events_log row per trigger,
and a GET that answers standing progress so the button renders on load.

It clears each selected row's fingerprint before enqueuing, and that is
the substance of it rather than an optimisation: the pipeline's
unchanged-fingerprint short-circuit returns above the write-back, so a
plain enqueue would report 'unchanged' and score nothing, and the
backfill would run forever and finish never. Both halves are pinned --
the selected row's fingerprint is cleared, an already-scored row's is
not.

No cursor row and no TMDb park, unlike the facts backfill, because
neither applies: this population is self-consuming, and nothing here
stamps a column a skipped provider could silently empty. Both departures
are argued in the module.

The page says the honest limitation out loud rather than leaving it to
the PR body: four flags cannot fire until an asset re-renders, and
scoring a batch is a real re-render."
```

- [ ] **Step 11: Tear down**

```bash
docker compose -p pac4 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

### Task 5: Wrap — the roadmap, the shipping log, the PR body

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (rows 103, 130, 132, 219 annotated; rows 231–236 filed; the 11a and 11b sub-phase headings marked)
- Modify: `.superpowers/sdd/progress.md` (one ship entry appended)

This task writes documents only. No source file, no test file, no migration.

**Interfaces:** none produced. It consumes the measured suite totals from Tasks 1–4 and the branch's own commit range.

---

- [ ] **Step 1: Verify the branch state and re-measure both suites**

The wrap states numbers, so it measures them. Do not carry a total forward from an earlier task's log.

```bash
cd /d/Sites/autoposter
git status --short
git log --oneline fix/config-safety..feat/action-center
git rev-list --left-right --count fix/config-safety...feat/action-center
```

Expected: a clean tree, four feature commits, and `0` behind.

```bash
docker compose -p pac5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pac5-full test sh -c \
  "set -o pipefail; ruff check . && pytest -q 2>&1 | tee /app/.superpowers/run-pac5-full.log"
cd frontend && npm run test -- --run && npx tsc --noEmit
```

Record both totals. They go in the ship entry and the PR body verbatim.

- [ ] **Step 2: Close 11a and close 11b in part**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, replace:

```markdown
#### 11a — Asset-quality flags and backfill
```

with:

```markdown
#### 11a — Asset-quality flags and backfill (CLOSED — the Action Center phase)
```

and replace:

```markdown
#### 11b — Action Center review queue and bulk actions
```

with:

```markdown
#### 11b — Action Center review queue and bulk actions (CLOSED IN PART — the Action Center phase)
```

Then, immediately after 11a's `**Testable when shipped:**` paragraph, add:

```markdown
**Closed by the Action Center phase.** Nine facts on `renders` written at the
existing pipeline write-back — the achieved language and its rank, the runtime
provider rank, the textless fallback the ladder had always returned and nobody
read, the logo-to-text fallback that had no variable at all, the base
dimensions, the fitted point size, and `quality_scored_at` — plus the batched
backfill (`GET`/`POST /api/actions/backfill`) whose counts are visible via the
API, which is this sub-phase's own stated acceptance. The other half of that
acceptance is pinned literally: a forced `en` render on the shipped
`xx`-preferring order produces the language flag, through `render_artifact`
itself.

The taxonomy is stored as facts and judged in code, which is the direct answer
to this row's own risk line. The flags are SQL predicates in
`src/autoposter/actions/flags.py`, evaluated against the config live at request
time, so re-pointing `language_order` or `providers.order` re-shapes the queue
with no row write and no re-backfill. Two tests exist for nothing else.

**Honest limitation, stated here and in the PR body rather than discovered
later:** six flags light up on rows that already exist (`missing`, `skipped`,
`truncated`, `show_fallback`, `upload_failed`, `unknown_provenance`), and four
cannot fire until a row re-renders (`language_miss`, `provider_downgrade`,
`textless_miss`, `logo_fallback`) because the facts they rest on are written at
the write-back. `quality_scored_at` is what makes that gap visible instead of
letting an unscored row read as a clean one, and the backfill is what closes it
at the operator's pace. The dimensions and the point size are CAPTURED and
enforced nowhere — see row 219.
```

and immediately after 11b's `**Testable when shipped:**` paragraph, add:

```markdown
**Closed in part by the Action Center phase.** Shipped: the filterable queue
(`/actions`) with per-flag count chips, library and art-kind filters and a
dismissed-visible toggle; per-row re-search and dismiss/restore; replace as a
deep link to the item page's picker; the two-step bulk re-search; and dismissal
that sticks until the underlying facts change. That last one is this row's own
stated risk and is answered by the mechanism rather than by a sweep: the
dismissal holds a SHA-256 over the row's fact columns, the queue recomputes
that hash in SQL on every read, and a re-render moving a fact makes the join
stop matching so the row returns on its own.

The other stated risk — "one 're-search all 400 flagged items' click is a
burst" — is answered by the queue: every action is an enqueue through the
shared `_enqueue_reprocess` and the `uq_jobs_pending_dedupe` partial index, so
400 flagged rows across 200 items are 200 jobs, asking twice while the first is
pending queues nothing, and the worker pool paces the provider calls exactly as
an ordinary pass does. There is no inline provider call anywhere in the
feature.

**Not shipped, and filed rather than half-built:** the inline candidate picker
inside the queue (row 231), the delete action (row 232). Row 103 therefore
stays open — see its own cell.
```

- [ ] **Step 3: Annotate row 103 as open, with the remainder named**

Replace the whole of row 103's line:

```markdown
| 103 | Action Center + asset-quality tracking | Track *why* each chosen asset is imperfect (language rank, provider rank, truncated text, text-fallback, missing), a review queue with resolve/replace/delete and bulk ops | **XL — the largest net-new subsystem**; nothing models "succeeded but suboptimal" | parity-only (but the biggest Posterizarr UI feature) | 73 |
```

with:

```markdown
| 103 | Action Center + asset-quality tracking (OPEN — 11a closed, 11b closed in part) | Track *why* each chosen asset is imperfect (language rank, provider rank, truncated text, text-fallback, missing), a review queue with resolve/replace/delete and bulk ops. **The Action Center phase delivered the asset-curation queue this row specifies, working end to end, and did not deliver the whole row.** Shipped: nine quality facts on `renders` written at the pipeline write-back (surviving the fingerprint short-circuit, following the `source_mode` precedent); a flag registry of SQL predicates evaluated against the LIVE config, so no verdict is stored and re-pointing `language_order` or `providers.order` re-shapes the queue with no row write; `action_dismissals` keyed by a SHA-256 the database recomputes from the row's own facts, so a dismissal stops holding the moment a re-render moves one; the `/actions` page with count chips, filters, a pager, per-row re-search / replace-by-deep-link / dismiss, a two-step bulk re-search and a coverage panel; and the batched backfill. Every action is an enqueue through the shared `_enqueue_reprocess` and the pending-dedupe index — never an inline provider call, which is 11b's own stated burst risk answered by the queue. Ten flags plus a coverage code, six of which light up on rows that already exist and four of which need a re-render (see 11a's close for the split, which is stated plainly rather than smoothed over). **Still open, and what keeps this row open:** the **delete** action (row 232 — this project deletes nothing anywhere else, and inventing a destructive verb for a render row had no honest semantics to ship); the **inline candidate picker** inside the queue (row 231 — replace is a deep link to row 73's picker on the item page); the **tabbed "what needs me" shell** folding row 130's Failures view and the ID-mismatch scan (row 231); and the **non-asset signals** — sweep refusals, arr-sync misassignments, award drift, budget ceilings — which are a second phase-sized subsystem needing a persistence layer at three chokepoints, not an extra tab (row 231). Resolution floors were never this row's: see 219 | **XL — the largest net-new subsystem**; nothing models "succeeded but suboptimal". The asset queue is delivered; the remainder is rows 231/232 | parity-only (but the biggest Posterizarr UI feature) | 73 |
```

- [ ] **Step 4: Annotate rows 130, 132 and 219**

Row 130 — append to the end of its "What it is" cell, before the closing ` | M | — | — |`:

```markdown
. **Not folded into the Action Center phase, deliberately:** its unit is a `jobs` row, not a render row — a different question about a different set of states, which is exactly why `api/jobs.py` and the parked endpoints in `routes.py` are already split. It folds in only if the Action Center later becomes a tabbed shell (Assets | Failures | Mismatches), which is row 231's decision; building that shell in one night would have diluted the asset queue and delivered two half-pages
```

Row 132 — append to the end of its "What it is" cell, before its closing size column:

```markdown
. **Retro-labelled by the Action Center phase:** `source_mode = "show_fallback"` was persisted and had no operator surface anywhere; it is now a first-class flag with a count and a filter, so shipped behaviour is finally visible to the person running it
```

Row 219 — append to the end of its "What it is" cell, before its closing size column:

```markdown
. **Half-unblocked by the Action Center phase:** `renders.base_width` and `renders.base_height` now record the provider's reported dimensions on every render, and `renders.text_point_size` records the fitted title size. Nothing reads them and nothing is enforced — this row keeps the wire-or-remove decision entirely. What changed is the cost of answering it: the minimum-dimension half is now a small later change against data already on file, rather than a re-backfill of the whole library, which was 11a's own named risk
```

- [ ] **Step 5: File the fenced-out work as named rows**

Insert these rows immediately after row 230's line in the same table:

```markdown
| 231 | Action Center phase 2: the "what needs me" shell, the inline picker, and the non-asset signals (FILED — the Action Center phase) | Three things the Action Center phase fenced out with the reasons written down, kept in one row because they are one decision about what that surface becomes. (a) **The tabbed shell.** Fold row **130** (Failures with job payload diagnostics) and the ID-mismatch scan into one surface, Assets \| Failures \| Mismatches. The phase deliberately did not: a `jobs` row and a `renders` row are different questions about different states, and building the shell overnight would have delivered two half-pages instead of one working queue. (b) **The inline candidate picker.** Replace currently deep-links to `/items/{id}` and row 73's picker; embedding it would be a second copy of the picker to keep in step. (c) **The non-asset signals** — parked jobs, arr-sync misassignments, `max_deletes` and `protect_labels` refusals, families refused past `max_collections`, contested-title skips, collision adoptions, orphaned asset paths, award-category drift, the TMDb 429 backoff window (a durable row with no reader at all), MDBList/Tracearr ceilings. Almost none of these is persisted, so folding them in is not "read an existing table", it is building a persistence layer for each. The clean design needs exactly three chokepoints, not twenty write sites: `collections/service.py::reconcile_libraries` (where every refusal, skip and collision is already folded into `run.actions` inside the session, one line above its commit), `scheduler/core.py`'s scheduled-run failure path (session and commit in hand), and `render/pipeline.py`'s render statuses — which the Action Center already reads. The mismatch sweep stays outside it: `api/mismatches.py` is stateless by explicit design and folding it in would overrule a documented architectural decision | **L–XL** — (a) is M, (b) is M, (c) is a second phase-sized subsystem | parity-only | 103; 130; 73 |
| 232 | The Action Center's delete action, once its semantics are settled (FILED — the Action Center phase) | Posterizarr's delete removes the asset so it regenerates. This project's nearest primitive is clear-override (row 24), which applies only to *manual* overrides, and the codebase states its posture out loud — `dismiss_job`'s docstring: "this project deletes nothing anywhere else either". So the question this row has to answer first is what delete MEANS for a render row: remove the published asset from the tree and clear the fingerprint so the next pass rebuilds it? Clear the row's provenance so the ladder starts clean? Ask Plex to drop the uploaded artwork, which the reset mode already warns it cannot reclaim? The Action Center phase shipped re-search / replace / dismiss and filed this rather than inventing a destructive verb with no stated meaning | S once the semantics are decided; the decision is the work | parity-only | 103; 24 |
| 233 | The ladder cannot distinguish "the provider had nothing" from "the provider threw" (FILED — the Action Center phase) | `providers/ladder.py`'s walk swallows a provider exception, logs a warning and continues, so `Selection` carries no trace of it and the caller cannot tell an empty answer from a failed one. The Action Center's `missing` flag therefore says "no art on any provider" for both cases, which is true of the outcome and misleading about the cause: a TMDb outage during a full pass reads identically to genuinely absent artwork. The fix is new plumbing on `Selection` (which providers were asked, which answered, which failed) and one more fact column; it was fenced out of the Action Center phase because it changes the ladder, which that phase deliberately did not touch | S–M — a field on `Selection`, one column, and the flag's own copy | parity-only | 103 |
| 234 | Near-miss text fit: how close a title came to not fitting (FILED — the Action Center phase) | `textfit.FitResult` reports a binary `truncated`, so only the floor breach survives today and the Action Center can only flag the assets where no file was written at all. A title that fitted only by dropping to the minimum point size is a real quality signal and is invisible. `renders.text_point_size` now records the size each title fitted at (Action Center phase), so the data exists; what this row adds is the judgement — a predicate comparing the fitted size against the style's own range, and whatever operator threshold that needs | S — one predicate over a column that already exists | parity-only | 103; 219 |
| 235 | Notify on newly actionable assets (FILED — the Action Center phase) | The Action Center is a pull surface: an operator sees a flagged asset when they open the page. Nothing tells them a pass has just produced fifty of them. A notification riding row 19's taxonomy would, but it needs a definition of "newly" that a derived-flag design does not have for free — the flags are recomputed on every read, so "new since when" has no column today, and the honest options (a high-water mark per flag, or a stamp on the render row) are the substance of this row | S–M | parity-only | 103; 19 |
```

- [ ] **Step 6: File the PROVENANCE_KEYS guard row — but only if the config-safety wrap has not already**

Adjudication C3: this carry lands in whichever wrap fires first. **Verify, do not duplicate.**

```bash
grep -n "PROVENANCE_KEYS" docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
```

- If that prints a row, the config-safety wrap already filed it. Do nothing, and say so in the ship entry.
- If it prints nothing, add row 236 immediately after row 235:

```markdown
| 236 | `PROVENANCE_KEYS` has no agreement guard (FILED — carried from config-safety T2's review) | The config editor decides which response keys are provenance rather than editable settings by consulting a hand-maintained `PROVENANCE_KEYS` set. Nothing ties that set to the keys the response actually carries, so the next provenance key added to the payload renders as an editable field with the whole suite green — the same silent-drift shape `SCHEDULED_JOB_NAMES` had before row 107's guard, and fixed the same way: a test that demands exact two-way agreement between the set and the keys the handler emits, failing in both directions | S — one test | — | 107 as the pattern |
```

- [ ] **Step 7: Append the ship entry to the shipping log**

Append to `.superpowers/sdd/progress.md`, replacing `<N>`/`<V>` with the totals measured in Step 1 and `<sha>` with the branch head:

```markdown
- ACTION CENTER SHIPPED (rows 11a/11b; 103 stays open): the asset-curation queue, working end to end. Nine quality facts on `renders` written at the pipeline write-back beside `source_mode`, so the unchanged-fingerprint short-circuit cannot blank them — pinned through `render_artifact` with a sentinel the second pass cannot reproduce. Judgement is DERIVED, never stored: ten flags plus a coverage code as SQL predicates over the LIVE config (`actions/flags.py`), pinned both ways each and pinned twice for the property that matters — editing `language_order` or `providers.order` flips the flag with no row write, once at the predicate and once through the real endpoint and the real config holder. Dismissals join on a SHA-256 the DATABASE recomputes from the row's current facts, which is what makes paging and `total` server-side and what returns a row the moment a re-render moves a fact (both halves pinned). Every action is an enqueue through the shared `_enqueue_reprocess` + `uq_jobs_pending_dedupe`: two flagged kinds of one item collapse to one job, asking twice queues once. The backfill clears each selected row's fingerprint before enqueuing — without that the short-circuit reports "unchanged" and the sweep would never finish; the discriminating negative (an already-scored row's fingerprint is untouched) is pinned too. HONEST LIMITATION, in the PR body and on the page itself: six flags light on existing rows, four need a re-render, and `quality_scored_at` plus the backfill are what make that visible and closable rather than hidden. Suites <N>/<V> measured. Head <sha>. Stacks on fix/config-safety — BOTH PRs gate in order. Rows 231-235 filed (phase-2 shell + picker + non-asset signals; delete; provider-threw-vs-empty; near-miss fit; notifications); 130/132/219 annotated. GATE: the config-safety PR first, then this one.
```

- [ ] **Step 8: Verify the wrap changed only documents**

```bash
git status --short
git diff --stat
```

Expected: exactly two paths, both `.md`. If any file under `src/`, `tests/`, `frontend/` or `alembic/` appears, something was edited that this task does not own — revert it and report.

- [ ] **Step 9: Commit the wrap**

`ruff` and the suites are unchanged by a documents-only commit, so they are not re-run here; Step 1 already measured them on this exact tree.

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md .superpowers/sdd/progress.md
git commit -m "docs(roadmap): 11a closes, 11b closes in part, 103 stays open

Row 103 is XL and this phase delivered its asset-curation queue, not all
of it. The row's cell now says which parts shipped and which are rows
231/232, so the close cannot be read as more than it is.

Rows 231-235 file what was fenced out with the reasoning attached rather
than half-built: the tabbed 'what needs me' shell folding row 130 and the
non-asset signals' persistence layer at three chokepoints, the inline
picker, the delete action and the semantics question it has to answer
first, the ladder's provider-threw-vs-empty blindness, the near-miss fit
metric, and notifications.

130, 132 and 219 are annotated where this phase changed their standing:
130 says why it was not folded in, 132 finally has an operator surface
for behaviour it shipped without one, and 219's minimum-dimension half is
now a small change against captured data instead of a re-backfill."
```

- [ ] **Step 10: Prepare the PR body**

The PR is opened after the whole-branch review, not here. Write the body now so the honest limitation cannot be lost between the two. Save it as the branch's PR description when the time comes; it is not committed to the repository.

```markdown
## Action Center (roadmap 11a / 11b; row 103 stays open)

An operator surface for artwork that succeeded and is suboptimal — the
question the Failures page does not answer, because that one covers hard
failures only.

**This branch stacks on `fix/config-safety` and must merge after it.** Its
migration chains onto `c7e1b93a4d20`, and the design reads the live config
through the holder that branch's work depends on.

### What ships

- **Nine quality facts on `renders`**, written at the existing pipeline
  write-back beside `source_mode`, and there for `source_mode`'s own stated
  reason: the unchanged-fingerprint short-circuit returns above that block, so
  a fact recorded there survives a pass that changes nothing.
- **Judgement derived, never stored.** Ten flags plus a coverage code are SQL
  predicates in `src/autoposter/actions/flags.py`, built from the config live
  at request time. Re-pointing `language_order` or `providers.order` re-shapes
  the queue on the next read with **no row write and no re-backfill** — the
  direct answer to 11a's own risk line, and pinned twice.
- **Dismissal by evidence hash.** `action_dismissals` holds a SHA-256 over the
  row's fact columns, computed and recomputed **in SQL**, so the queue's join
  is a plain equality (paging and `total` stay server-side) and a re-render
  that moves a fact returns the row on its own — no sweep, no invalidation
  job. That is 11b's "dismissals need a fingerprint-style identity", literally.
- **Every action is an enqueue** through the shared `_enqueue_reprocess` and
  the `uq_jobs_pending_dedupe` partial index. 400 flagged rows across 200 items
  are 200 jobs; asking twice while the first is pending queues nothing. There
  is no inline provider call anywhere in the feature — 11b's stated burst risk,
  answered by the queue rather than by a rate limiter of its own.
- **The page** at `/actions`: per-flag count chips, library and art-kind
  filters, a dismissed-visible toggle, a pager, per-row re-search /
  replace-by-deep-link / dismiss, a two-step bulk arm, and a coverage panel
  with the batched backfill.

### The honest limitation

Six flags light up on rows that already exist — `missing`, `skipped`,
`truncated`, `show_fallback`, `upload_failed`, `unknown_provenance` — so the
queue has real contents on first load with no re-render and no backfill.

**Four cannot fire until a row re-renders**: `language_miss`,
`provider_downgrade`, `textless_miss`, `logo_fallback`. The facts they rest on
are written at the write-back, and no column on a pre-existing row holds the
achieved language, so it is genuinely unrecoverable without re-selecting.
`quality_scored_at` is what makes that gap visible instead of letting an
unscored row read as clean, and the backfill closes it at the operator's pace
— one batch per press, clearing each row's fingerprint so the next pass is a
real render rather than an "unchanged" that scores nothing.

Two further caveats the page repeats in its own copy: a re-search is a
re-*search*, not a guarantee (an already-rendered row re-runs selection against
the 24h provider cache and may find the same art and stay flagged); and work is
queued per item, so re-searching a flagged poster re-renders that item's
background too.

### What does not ship, and why

Filed as rows 231–235 with the reasoning attached, not half-built: the tabbed
"what needs me" shell folding row 130 and the non-asset signals' persistence
layer (a second phase-sized subsystem needing three chokepoints, not an extra
tab); the inline picker inside the queue; the **delete** action, which had no
honest semantics to ship for a render row in a project whose own code says "this
project deletes nothing anywhere else either"; the ladder's
provider-threw-vs-returned-empty blindness; the near-miss fit metric; and
notifications on newly actionable assets.

Resolution floors were never this row's. `base_width`, `base_height` and
`text_point_size` are now **captured and enforced nowhere** — row 219 keeps the
wire-or-remove decision, and what changed is that answering it later is a small
change against data already on file rather than a re-backfill.

**Row 103 therefore stays open.** 11a closes; 11b closes in part. A "fully
working Action Center" is true of what ships and is said that way: working end
to end, not complete.

### Suites

Backend `<N>` passed, frontend `<V>` passed, `ruff` and `tsc` clean, one alembic
head.
```

- [ ] **Step 11: Tear down**

```bash
docker compose -p pac5 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## Self-Review Record

Run at the end of plan writing; recorded here so an executor can see what was checked.

**Spec coverage** — every adjudication in `.superpowers/sdd/p-actioncenter-facts.md`:

| Fact | Where it lands |
|---|---|
| C1.1 narrow asset-queue scope (unit = a `renders` row) | T2's list endpoint keys on `(item_id, art_kind)`; T5 files the ops inbox as row 231 |
| C1.2 facts stored on `renders`, judgements derived at query time | T1 Steps 4 and 8; the two derived-not-stored tests in T1 Step 2 and the endpoint-level one in T2 Step 1 |
| C1.3 dismissal = evidence hash table | T1's `ActionDismissal` + `evidence_expression`; T2's `_dismissal_join` and the return-on-change test |
| C1.4 every action is an enqueue | T2's `rerender`/`bulk`, T4's backfill; the dedupe and collapse tests |
| C1.5 six instant / four lazy, `quality_scored_at` makes it visible | The registry's `instant` field, the `unscored` flag, T4's coverage panel, 11a's close text, the PR body |
| C1.6 A2 delete filed; A3 capture and do not enforce; A5 `adopted` default-off | Row 232; T1's three capture columns + row 219's annotation; `default_on=False` and its test |
| C1.7 11a closes, 11b in part, 103 open | T5 Steps 2–3 |
| C1.8 "Action Center" naming | Nav label, page heading, docstrings, roadmap, PR body |
| C1.9 stacks on config-safety, `down_revision = c7e1b93a4d20`, no schema.py/badges/overlays | Global Constraints 1–3; T1 Steps 1 and 5 |
| C2 the entry-point law and RED-first | T1 Step 11's `render_artifact` tests, T2 Step 1's endpoint tests, T3's vitest page tests; every task RED-runs before implementing |
| C3 verify-don't-duplicate the PROVENANCE_KEYS carry | T5 Step 6 |
| C4 five tasks in the stated order and sizes | The task summary table |

**Placeholder scan:** no "TBD", no "add validation", no "similar to Task N", no step that describes a code change without showing the code.

**Type and name consistency:** `predicate_for`, `default_predicate`, `detail_for`, `evidence_expression`, `FLAGS`, `ART_KINDS`, `Flag.instant`, `Flag.default_on`, `normalise_language`, `_provider_rank`, `ComposeResult.point_size`, `ActionDismissal`, `_flag_predicate`, `_dismissal_join`, `_backfill_progress`, `ActionRow`, `ActionsResponse`, `ActionsSummaryResponse`, `ActionFlagSummary`, `BulkRerenderResponse`, `QualityBackfillStatus`, `QualityBackfillTrigger`, `ActionCenter` — each spelled identically wherever it appears across Tasks 1–4. The nine column names in T1's Interfaces block match the migration, the write-back, the evidence tuple and the predicates.

